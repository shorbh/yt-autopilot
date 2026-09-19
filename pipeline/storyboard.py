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

VISUAL_TYPES = ("bignumber", "compare", "list", "formula", "callout", "chart", "timeline", "broll",
                "icon_text", "photo_text", "character")
MAX_CALLOUT_SHARE = 0.25   # plain text cards may be at most a quarter of all beats

# Lucide icon names the storyboard may use (all verified to exist in lucide-static).
ICONS = (
    "wallet coins banknote piggy-bank landmark building-2 briefcase receipt calculator percent trending-up trending-down "
    "chart-line chart-bar chart-pie line-chart bar-chart-3 arrow-up-right arrow-down-right scale shield shield-check "
    "shield-alert alert-triangle circle-alert check-circle-2 x-circle lock unlock key clock timer hourglass calendar "
    "calendar-days repeat refresh-cw rotate-ccw target flag trophy award star gem crown gift hand-coins handshake "
    "users user user-check user-x baby heart heart-pulse home house car fuel plane graduation-cap book-open lightbulb "
    "brain zap flame droplet leaf sprout tree-pine mountain map map-pin compass route milestone anchor scissors "
    "shopping-cart shopping-bag credit-card smartphone laptop tv globe factory truck package box layers "
    "divide equal minus plus infinity sigma hash file-text files folder clipboard-list list-checks table "
    "pause play skip-forward fast-forward rewind door-open door-closed umbrella life-buoy bomb skull hourglass"
).split()

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
- A beat covers ONE sentence. Only pair two consecutive sentences when both are short (under ~10 words each).
  Never three. Target: a visual change every 4-8 seconds of speech.
- The visual must show exactly what the sentence says. Numbers spoken -> bignumber/formula/compare. Options -> compare.
  Steps or reasons -> list. Chronology -> timeline. The core worked example -> chart.
- Vary the LAYERS so the video is never text-only. Mix these across each section:
    icon_text    = a concept statement (<= 10 words) + one icon from the allowed list, e.g. "A grant is a promise" + "handshake".
    photo_text   = scene-setting or emotional sentence: short phrase + a 2-4 word stock-photo query (objects/places, no faces).
    character    = any sentence about a named or implied PERSON ("Sarah, 30, earns...", "imagine an investor who..."):
                   give character {name, gender, mood}; it is rendered as a real stock portrait photo chosen from gender
                   and mood. Gender MUST follow the script's name/pronouns (Sarah/she -> female, Mike/he -> male;
                   unknown -> neutral). mood in: neutral, happy, excited, worried, sad, stressed, shocked, scared, thinking,
                   serious, proud, confused. Add "text" (<= 12 words) and optional "stat" ("$39,000").
                   The same name must keep the same gender across the whole video.
    compare      = two people or two options; when the sides are people add "character" to each side instead of "icon"
                   (people are shown as initial avatars there).
    callout      = a rule or punchline, <= 12 words. Use SPARINGLY: at most 1 in 4 beats.
    broll        = ONLY for transitions with no data (max 15% of beats).
  Never put two visuals of the same type back to back; alternate text-heavy cards with image cards.
- The FIRST beat of the 'hook' section must be a visual card (bignumber if the sentence has a number, otherwise
  photo_text or character) — never a callout. The first 3 seconds decide whether the viewer stays.
- Add an "icon" (from the allowed list) to bignumber, compare sides, and list items ("icons": [...]) whenever one fits.
- All on-screen text must be short, concrete and copied/derived from the sentences (no new facts).
- On screen, ALWAYS write numbers as numerals with symbols ("$50,000", "40%", "10,000 shares"), even when
  the sentence spells them out ("fifty thousand dollars").
- Return ONLY valid JSON."""

SCHEMA = """{
  "sections": [
    {
      "id": "<section id as given>",
      "beats": [
        {"from": 1, "to": 1, "visual": {"type": "bignumber", "value": "$452,000", "label": "after 30 years at 7%", "icon": "piggy-bank"}},
        {"from": 2, "to": 2, "visual": {"type": "character", "character": {"name": "Sarah", "gender": "female", "mood": "worried"}, "text": "Saves $500 a month, pays 1.1% in fees", "stat": "$352,000"}},
        {"from": 3, "to": 3, "visual": {"type": "compare", "title": "Same $500/month", "left": {"label": "Sarah", "value": "$352,000", "note": "1.1% fee", "character": {"name": "Sarah", "gender": "female", "mood": "worried"}}, "right": {"label": "Mike", "value": "$452,000", "note": "0.1% fee", "character": {"name": "Mike", "gender": "male", "mood": "happy"}}}},
        {"from": 4, "to": 4, "visual": {"type": "icon_text", "text": "A grant is a promise, not a paycheck", "icon": "handshake"}},
        {"from": 5, "to": 6, "visual": {"type": "list", "title": "Three things that vest", "items": ["25% after year one", "Monthly after that", "Nothing if you leave early"], "icons": ["calendar", "repeat", "door-open"]}},
        {"from": 7, "to": 7, "visual": {"type": "formula", "lines": ["$500 × 12 × 30 = $180,000 invested", "at 7% → $452,000"]}},
        {"from": 8, "to": 8, "visual": {"type": "photo_text", "text": "The offer letter lands", "query": "signed contract desk"}},
        {"from": 9, "to": 9, "visual": {"type": "callout", "text": "Fees compound against you."}},
        {"from": 10, "to": 10, "visual": {"type": "chart"}},
        {"from": 11, "to": 11, "visual": {"type": "timeline", "items": [{"when": "Year 1", "what": "25% vests"}, {"when": "Year 2-4", "what": "monthly vesting"}]}},
        {"from": 12, "to": 12, "visual": {"type": "broll", "query": "city office window"}}
      ]
    }
  ]
}"""


MAX_BEAT_WORDS = 22   # ~9 s of speech; anything longer is split so the screen keeps changing


def _key_phrase(sentence: str, max_words: int = 12) -> str:
    """Short on-screen phrase for a long sentence: the first clause if it is short enough,
    otherwise the first few words with an ellipsis."""
    s = sentence.strip().rstrip(".!?")
    for sep in (", ", "; ", " — ", " - ", ": "):
        head = s.split(sep)[0]
        if 3 <= len(head.split()) <= max_words:
            return head
    w = s.split()
    return s if len(w) <= max_words else " ".join(w[:max_words - 2]) + "…"


def _default_visual(sentence: str) -> dict:
    return {"type": "callout", "text": _key_phrase(sentence)}


# keyword -> (icon, stock-photo query). Used to turn runs of plain text callouts into image cards.
_KEYWORDS = [
    (("retire", "retirement", "pension", "fire "), ("hourglass", "sunset beach chairs")),
    (("tax", "taxes", "irs"), ("receipt", "tax documents desk")),
    (("invest", "index fund", "stock", "market", "portfolio", "etf"), ("chart-line", "stock market screen")),
    (("save", "saving", "savings", "emergency fund"), ("piggy-bank", "glass jar coins")),
    (("debt", "loan", "credit card", "interest", "mortgage", "borrow"), ("credit-card", "credit card bills")),
    (("salary", "income", "paycheck", "raise", "job", "career", "employer"), ("briefcase", "office desk laptop")),
    (("house", "home", "rent", "landlord", "apartment"), ("home", "house keys door")),
    (("car", "lease", "vehicle"), ("car", "car dealership")),
    (("bank", "banks", "deposit", "account"), ("landmark", "bank building facade")),
    (("inflation", "price", "prices", "cost", "expensive"), ("trending-up", "grocery receipt")),
    (("time", "years", "decade", "month", "monthly"), ("calendar-days", "wall calendar")),
    (("risk", "lose", "loss", "crash", "recession"), ("alert-triangle", "storm clouds city")),
    (("compound", "growth", "grow", "double", "return"), ("sprout", "plant growing coins")),
    (("family", "kids", "children", "spouse", "partner"), ("users", "family walking park")),
    (("budget", "spend", "spending", "shopping", "groceries"), ("shopping-cart", "shopping cart aisle")),
    (("rule", "lesson", "mistake", "trap", "myth"), ("lightbulb", "notebook pen desk")),
    (("percent", "%", "fee", "fees", "rate"), ("percent", "calculator paperwork")),
]


def _keyword_visual(sentence: str, k: int) -> dict:
    """Pick an icon_text (even k) or photo_text (odd k) card whose icon/photo matches the sentence."""
    low = " " + sentence.lower() + " "
    icon, query = "lightbulb", "finance desk notebook"
    for keys, (ic, q) in _KEYWORDS:
        if any(kw in low for kw in keys):
            icon, query = ic, q
            break
    phrase = _key_phrase(sentence)
    if k % 2 == 0:
        return {"type": "icon_text", "text": phrase, "icon": icon}
    return {"type": "photo_text", "text": phrase, "query": query}


_NUM_IN_TEXT = re.compile(r"(\$\s?[\d,]+(?:\.\d+)?\s*(?:k|K|m|M|million|billion|thousand)?|[\d,]+(?:\.\d+)?\s*(?:%|percent|years?|months?|dollars?))")


def _diversify(beats: list[dict], first_is_hook: bool = False) -> list[dict]:
    """Pacing/variety guard applied after the LLM:
    1. no two consecutive plain callouts;
    2. plain callouts capped at MAX_CALLOUT_SHARE of all beats (excess become keyword image cards);
    3. the very first beat of the hook is a visual, never a callout;
    4. no three consecutive beats with the same card layout."""
    prev_callout, k = False, 0
    for b in beats:
        if b["visual"].get("type") == "callout":
            if prev_callout:
                b["visual"] = _keyword_visual(b["text"], k)
                k += 1
            prev_callout = b["visual"].get("type") == "callout"
        else:
            prev_callout = False
    allowed = max(1, int(len(beats) * MAX_CALLOUT_SHARE))
    callouts = [b for b in beats if b["visual"].get("type") == "callout"]
    for b in callouts[allowed:]:  # keep the earliest ones (punchlines tend to be planned), convert the rest
        b["visual"] = _keyword_visual(b["text"], k)
        k += 1
    # 3. the hook opens on a visual (before rule 4, so a triple created here is healed below)
    if first_is_hook and beats and beats[0]["visual"].get("type") in ("callout", "broll"):
        m = _NUM_IN_TEXT.search(beats[0]["text"])
        if m:
            beats[0]["visual"] = {"type": "bignumber", "value": m.group(1).strip(), "label": _key_phrase(beats[0]["text"], 8)}
        else:
            beats[0]["visual"] = _keyword_visual(beats[0]["text"], 1)  # photo card
    # 4. never three cards of the SAME layout in a row (run #7: five bignumbers back to back). The third
    #    becomes a keyword image card; chart/broll/character/compare are left alone (they carry their own imagery).
    for i in range(2, len(beats)):
        t0, t1, t2 = (beats[j]["visual"].get("type") for j in (i - 2, i - 1, i))
        if t0 == t1 == t2 and t2 in ("bignumber", "callout", "icon_text", "formula", "list", "photo_text"):
            # icon_text run -> photo card (odd k); photo_text run -> icon card (even k); anything else alternates
            want = 1 if t2 == "icon_text" else (0 if t2 == "photo_text" else k % 2)
            beats[i]["visual"] = _keyword_visual(beats[i]["text"], want)
            k += 1
    return beats


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
    if t == "icon_text":
        v["text"] = str(v.get("text") or _key_phrase(sentences[0]))[:70]
        if v.get("icon") not in ICONS:
            v["icon"] = None  # renderer degrades to a callout
    if t == "photo_text":
        v["text"] = str(v.get("text") or _key_phrase(sentences[0]))[:70]
        v["query"] = str(v.get("query") or "finance desk")[:40]
    if t == "character":
        ch = v.get("character") if isinstance(v.get("character"), dict) else {"name": v.get("name"), "gender": v.get("gender"), "mood": v.get("mood")}
        if not ch.get("name"):
            return _default_visual(sentences[0])
        v["character"] = {"name": str(ch.get("name"))[:24], "gender": str(ch.get("gender") or "neutral"), "mood": str(ch.get("mood") or "neutral")}
        v["text"] = str(v.get("text") or _key_phrase(sentences[0]))[:80]
        v["stat"] = str(v.get("stat") or "")[:20]
    # icon hygiene on the other types
    if v.get("icon") is not None and v.get("icon") not in ICONS:
        v["icon"] = None
    if t == "compare":
        for side in ("left", "right"):
            sd = v[side]
            if sd.get("icon") not in ICONS:
                sd["icon"] = None
            ch = sd.get("character")
            if isinstance(ch, dict) and ch.get("name"):
                sd["character"] = {"name": str(ch.get("name"))[:24], "gender": str(ch.get("gender") or "neutral"), "mood": str(ch.get("mood") or "neutral")}
            else:
                sd.pop("character", None)
    if t == "list" and v.get("icons"):
        v["icons"] = [i if i in ICONS else None for i in v["icons"]][:5]
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

    # Pacing guard: a multi-sentence beat longer than ~9 s of speech is split into one beat per
    # sentence. The first keeps the planned visual; the rest get a callout of their key phrase.
    paced: list[dict] = []
    for b in beats:
        span = sentences[b["from"] - 1:b["to"]]
        if len(span) > 1 and sum(len(s.split()) for s in span) > MAX_BEAT_WORDS:
            for j, k in enumerate(range(b["from"], b["to"] + 1)):
                paced.append({"from": k, "to": k, "visual": b["visual"] if j == 0 else _default_visual(sentences[k - 1])})
        else:
            paced.append(b)
    for b in paced:
        b["text"] = " ".join(sentences[b["from"] - 1:b["to"]])
    return _diversify(paced, first_is_hook=(section_id == "hook"))


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
            f"Channel: {cfg['channel']['name']}.{chart_hint}\n"
            f"Allowed icon names: {', '.join(ICONS)}\n\n" + "\n\n".join(numbered) +
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
