"""Storyboard: turn each section's narration into *beats* — short sentence ranges, each with a
visual the renderer can draw deterministically. This is what makes the screen show what the
voice is saying and change every few seconds.

Robustness rules:
  * We split sentences ourselves and ask the LLM only for sentence RANGES + a visual spec, so
    text drift in the model's reply cannot break alignment.
  * Anything malformed degrades to a sensible default (callout of the sentence / b-roll), and
    if the whole call fails the caller falls back to one visual per section.
"""
from __future__ import annotations

import json
import re

from .llm import ask_json

VISUAL_TYPES = ("bignumber", "compare", "list", "formula", "callout", "chart", "timeline", "broll")

_SENT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'$])")


def split_sentences(text: str) -> list[str]:
    text = " ".join(text.split())
    parts = [p.strip() for p in _SENT_RE.split(text) if p.strip()]
    # merge very short fragments ("Here's why.") into the previous sentence for nicer beats
    out: list[str] = []
    for p in parts:
        if out and len(p.split()) <= 3:
            out[-1] = out[-1] + " " + p
        else:
            out.append(p)
    return out or [text]


SYSTEM = """You are a motion-graphics director for a faceless personal-finance YouTube channel.
You receive numbered sentences of a voice-over and decide, beat by beat, what is ON SCREEN while
each sentence is spoken. Rules:
- A beat covers 1-2 consecutive sentences (3 only for pure narrative). Aim for a visual change every 4-9 seconds.
- The visual must show exactly what the sentence says. Numbers spoken -> bignumber/formula/compare. Options -> compare.
  Steps or reasons -> list. A rule or punchline -> callout (short, <= 12 words). Chronology -> timeline.
  The core worked example -> chart. Use broll ONLY for scene-setting sentences with no data (max 25% of beats).
- All on-screen text must be short, concrete and copied/derived from the sentences (no new facts).
- On screen, ALWAYS write numbers as numerals with symbols ("$50,000", "40%", "10,000 shares"), even when
  the sentence spells them out ("fifty thousand dollars").
- Return ONLY valid JSON."""

SCHEMA = """{
  "sections": [
    {
      "id": "<section id as given>",
      "beats": [
        {"from": 1, "to": 2, "visual": {"type": "bignumber", "value": "$452,000", "label": "after 30 years at 7%"}},
        {"from": 3, "to": 3, "visual": {"type": "compare", "title": "Same $500/month", "left": {"label": "0.1% fee", "value": "$452,000", "note": "index fund"}, "right": {"label": "1.1% fee", "value": "$352,000", "note": "active fund"}}},
        {"from": 4, "to": 5, "visual": {"type": "list", "title": "Three things that vest", "items": ["25% after year one", "Monthly after that", "Nothing if you leave early"]}},
        {"from": 6, "to": 6, "visual": {"type": "formula", "lines": ["$500 × 12 × 30 = $180,000 invested", "at 7% → $452,000"]}},
        {"from": 7, "to": 7, "visual": {"type": "callout", "text": "Fees compound against you."}},
        {"from": 8, "to": 9, "visual": {"type": "chart"}},
        {"from": 10, "to": 10, "visual": {"type": "timeline", "items": [{"when": "Year 1", "what": "25% vests"}, {"when": "Year 2-4", "what": "monthly vesting"}]}},
        {"from": 11, "to": 11, "visual": {"type": "broll", "query": "city office window"}}
      ]
    }
  ]
}"""


def _default_visual(sentence: str) -> dict:
    words = sentence.split()
    if len(words) <= 14:
        return {"type": "callout", "text": sentence.rstrip(".")}
    return {"type": "broll", "query": "finance desk"}


def _clean_visual(v: dict, sentences: list[str]) -> dict:
    if not isinstance(v, dict) or v.get("type") not in VISUAL_TYPES:
        return _default_visual(sentences[0])
    t = v["type"]
    if t == "bignumber" and not v.get("value"):
        return _default_visual(sentences[0])
    if t == "compare" and not (isinstance(v.get("left"), dict) and isinstance(v.get("right"), dict)):
        return _default_visual(sentences[0])
    if t == "list":
        items = [str(i)[:60] for i in (v.get("items") or []) if str(i).strip()][:5]
        if len(items) < 2:
            return _default_visual(sentences[0])
        v["items"] = items
    if t == "formula":
        lines = [str(l)[:48] for l in (v.get("lines") or []) if str(l).strip()][:4]
        if not lines:
            return _default_visual(sentences[0])
        v["lines"] = lines
    if t == "callout":
        v["text"] = str(v.get("text") or sentences[0])[:90]
    if t == "timeline":
        items = [i for i in (v.get("items") or []) if isinstance(i, dict) and i.get("when") and i.get("what")][:6]
        if len(items) < 2:
            return _default_visual(sentences[0])
        v["items"] = items
    if t == "broll":
        v["query"] = str(v.get("query") or "finance desk")[:40]
    return v


def _normalise(section_id: str, sentences: list[str], raw_beats: list) -> list[dict]:
    """Turn LLM ranges into a complete, ordered, gap-free cover of the sentences."""
    n = len(sentences)
    beats: list[dict] = []
    cursor = 1
    for b in raw_beats or []:
        try:
            a, z = int(b.get("from", cursor)), int(b.get("to", cursor))
        except (TypeError, ValueError, AttributeError):
            continue
        a, z = max(a, cursor), min(max(z, a), n)
        if a > n:
            break
        if a > cursor:  # fill gap with defaults
            for k in range(cursor, a):
                beats.append({"from": k, "to": k, "visual": _default_visual(sentences[k - 1])})
        beats.append({"from": a, "to": z, "visual": _clean_visual(b.get("visual"), sentences[a - 1:z])})
        cursor = z + 1
    for k in range(cursor, n + 1):
        beats.append({"from": k, "to": k, "visual": _default_visual(sentences[k - 1])})
    for b in beats:
        b["text"] = " ".join(sentences[b["from"] - 1:b["to"]])
    return beats


def storyboard(cfg: dict, sections: list[dict], chart: dict | None) -> dict[str, list[dict]]:
    """Returns {section_id: [beat, ...]}. Each beat: {from, to, text, visual}."""
    numbered = []
    sent_map: dict[str, list[str]] = {}
    for s in sections:
        sents = split_sentences(s["narration"])
        sent_map[s["id"]] = sents
        numbered.append(f"## Section '{s['id']}' — heading: {s['heading']}\n" +
                        "\n".join(f"{i + 1}. {t}" for i, t in enumerate(sents)))
    chart_hint = f"\nThe video's chart shows: {chart.get('title')}" if chart else "\nThere is no chart; do not use type 'chart'."
    user = ("Storyboard these sections. Every sentence number must be covered exactly once, in order.\n"
            f"Channel: {cfg['channel']['name']}.{chart_hint}\n\n" + "\n\n".join(numbered) +
            f"\n\nReturn JSON exactly like:\n{SCHEMA}")
    try:
        data = ask_json(cfg, SYSTEM, user, temperature=0.5)
        by_id = {str(x.get("id")): x.get("beats", []) for x in data.get("sections", []) if isinstance(x, dict)}
    except Exception as e:  # noqa: BLE001 - never let the storyboard kill production
        print(f"[warn] storyboard LLM failed ({e}); using default beats")
        by_id = {}
    out = {}
    for s in sections:
        out[s["id"]] = _normalise(s["id"], sent_map[s["id"]], by_id.get(s["id"], []))
    return out


if __name__ == "__main__":
    print(json.dumps(split_sentences("A one percent fee doesn't cost one percent. Here's why. Over 30 years it removes $100,000!"), indent=1))
