"""Script generation. Produces a structured script the renderer can consume directly.

Design goals (these are what keep the channel on the right side of YouTube's
'inauthentic content' policy): a unique worked example with real numbers in every
video, rotating formats, a visible on-screen data chart, a consistent brand voice.
"""
import json
import re

from .llm import ask_json

SYSTEM = """You are the head writer for a faceless YouTube channel about personal finance.
You write tight, spoken-word scripts for a neural text-to-speech voice: short sentences,
contractions, no bullet symbols, no markdown, no stage directions, numbers written the way
a person says them where ambiguity exists (say 'seven percent', but keep '$1,200' style for money).
Never give personalised advice; explain mechanisms and math. Be concrete: every video contains
at least one fully worked numeric example the viewer can reproduce. Avoid clichés like
'in today's video' or 'without further ado'. Open on the payoff, not a greeting.

Retention rules (these decide whether the algorithm recommends the video):
- HOOK: the first sentence states the surprising number or claim; the title's main keywords are SPOKEN
  within the first two sentences (YouTube indexes the transcript).
- MICRO-HOOKS: every section except 'close' ends with a one-sentence forward tease that opens a
  curiosity gap ("and the second mistake costs even more", "the twist is in year three"). Never a summary.
- TAIL: the 'close' section never says "in summary", "to recap", "that's all" or fades out. It delivers the
  action rule in two or three sentences, then ONE generic forward tease, then the sign-off.
  The tease must NOT name a topic, product, number or event (next week's subject is chosen from the news):
  good: "Next week I'm pulling apart a money habit that looks smart and quietly isn't."
        "There's a mechanic almost nobody checks until it's cost them — that's next."
        "Next week's one is the kind of thing you'll wish someone had told you at twenty-five."
  bad: anything mentioning mortgages, taxes, the Fed, a company, a percentage or a dollar figure.
  Write a FRESH line in your own words every time — never reuse the example sentences above.
- Any named person keeps the same name, gender and pronouns throughout; state gender implicitly via pronouns.
Return ONLY valid JSON matching the schema requested."""

SCHEMA = """{
  "title": "<= 60 chars, curiosity + specific number or contrast, no clickbait lies",
  "alt_titles": ["2 alternative titles"],
  "thumbnail": {
    "hero": "THE number the viewer will search for, or the cost delta, <= 9 chars, e.g. '+$200K' | '7%' | '$1,348/mo'. Must appear in the title or be its direct consequence; NEVER a derived difference like '2%' when the title says 7%",
    "hero_label": "2-4 words, all caps, e.g. 'MORE INTEREST' | 'MORTGAGE RATES'",
    "hero_is_cost": true,
    "compare": {"left": {"label": "5% RATE", "value": "$373K"}, "right": {"label": "7% RATE", "value": "$558K"}},
    "icon": "one Lucide icon for the topic: home | car | piggy-bank | credit-card | briefcase | receipt | landmark | graduation-cap | heart-pulse | shopping-cart | chart-line | wallet",
    "query": "3-5 word stock-photo search for ONE PERSON with an expression matching the title's emotion, e.g. 'worried man glasses portrait' | 'shocked woman laptop' | 'serious businesswoman office'"
  },
  "description_hook": "1-2 sentences, <= 150 characters TOTAL, starting with the primary keyword phrase, written as a curiosity hook that extends the title (never 'In this video we...'). Numerals and symbols ($400,000, 7%), never spelled-out numbers.",
  "description_body": "2-3 short paragraphs, 120-200 words, plain prose: the problem, what the viewer will be able to do after watching, and the related terms a searcher would use (secondary keywords woven into sentences, NOT a list). Numerals only. No disclaimer, no subscribe line, no timestamps, no links, no hashtags.",
  "key_facts": ["3-5 items of <= 8 words each with the video's concrete numbers, e.g. '$400,000 loan · 5% vs 7%', '+$200,000 lifetime interest'"],
  "hashtags": ["3-5 specific CamelCase hashtags without generic ones like #viral, e.g. '#MortgageRates', '#Amortization', '#PersonalFinance'"],
  "tags": ["12-18 lowercase tags"],
  "sections": [
    {
      "id": "hook",
      "heading": "3-6 word on-screen heading that also works as a chapter title in search: concrete and keyword-bearing ('The 7% Payment Shock', 'Amortization: Year 1 vs Year 10'), never generic ('Introduction', 'Step 1')",
      "narration": "60-90 words. State the surprising claim and the number that proves it.",
      "visual_query": "2-4 word stock-footage search (e.g. 'calculator desk')",
      "stat": {"label": "on-screen big number label", "value": "$180,000"} ,
      "short_worthy": true
    },
    {"id": "s1", "heading": "...", "narration": "150-220 words", "visual_query": "...", "stat": null, "short_worthy": false},
    {"id": "s2", "...": "..."},
    {"id": "s3", "...": "..."},
    {"id": "s4", "...": "..."},
    {"id": "s5", "...": "..."},
    {"id": "close", "heading": "...", "narration": "50-80 words: the action rule (no recap), one GENERIC forward tease that names no topic, then EXACTLY this sign-off text: {signoff}", "visual_query": "...", "stat": null, "short_worthy": false}
  ],
  "chart": {
    "type": "line | bar",
    "title": "chart title",
    "x_label": "e.g. Years",
    "series": [{"name": "Series A", "points": [[0, 0], [10, 1200]]}, {"name": "Series B", "points": [[0, 0], [10, 900]]}],
    "y_prefix": "$",
    "y_suffix": ""
  },
  "shorts": [
    {"hook_title": "<= 40 chars on-screen title", "narration": "110-150 words (about 45-55 seconds), self-contained, ends with 'Full breakdown on the channel.'", "visual_query": "..."},
    {"hook_title": "...", "narration": "...", "visual_query": "..."},
    {"hook_title": "...", "narration": "...", "visual_query": "..."}
  ]
}"""


def generate_script(cfg: dict, pick: dict) -> dict:
    from .state import load_performance
    ch = cfg["channel"]
    target_words = int(cfg["video"]["target_minutes"] * 150)  # ~150 wpm spoken
    hints = load_performance().get("script_hints") or []
    hint_block = ("\nLESSONS FROM THIS CHANNEL'S RETENTION DATA (apply them):\n- " + "\n- ".join(hints) + "\n") if hints else ""
    news_block = ""
    if pick.get("news_hook"):
        news_block = (f"\nNEWS HOOK (this week's event; use it in the hook and title so the video rides the search wave, "
                      f"but explain the underlying mechanism so the video stays useful for years): {pick['news_hook']}\n")
    user = f"""Channel: {ch['name']} — {ch['tagline']}{hint_block}
Niche: {ch['niche']}
Audience: {ch['audience']}
Sign-off (must appear verbatim at the end of the 'close' section): {ch['signoff']}

TOPIC: {pick['topic']}
CATEGORY: {pick['category']}
FORMAT TO FOLLOW: {pick['format']}{news_block}

Total narration length across all sections: about {target_words} words (±10%).
Use 7 sections total: hook, s1..s5, close. Mark exactly 1-2 sections as short_worthy.
The 'chart' must visualise the video's core worked example with 1-2 series and 4-12 points each; make the numbers consistent with the narration.
Both chart series MUST be in the same unit and a similar magnitude (e.g. two dollar balances), never a price next to a total value — otherwise one line is flat.
THUMBNAIL: 'hero' is the single number a scroller must see — the headline figure from the title or the cost it causes
(e.g. title 'Why a 7% mortgage costs $200K more' -> hero '+$200K', label 'MORE INTEREST'; compare 5% vs 7% totals).
'compare' is only for videos with two directly comparable figures in the same unit; otherwise set it to null.
'hero_is_cost' is true when the hero is money lost / extra paid (shown in red), false when it is a gain or a rate (gold).
'query' must describe a PERSON (face visible) whose expression matches the title's emotion — faces lift click-through.
The three Shorts must each be a different angle on the topic (the number, the mistake, the rule) and must NOT repeat the long video's sentences.

Return JSON exactly matching this schema:
{SCHEMA.replace('{signoff}', ch['signoff'])}"""
    data = ask_json(cfg, SYSTEM, user)
    _validate(data, cfg)
    return data


_THUMB_ICONS = {"home", "car", "piggy-bank", "credit-card", "briefcase", "receipt", "landmark", "graduation-cap",
                "heart-pulse", "shopping-cart", "chart-line", "wallet"}


# sentence-level: remove just the disclaimer sentence, not the paragraph it sits in
_DISCLAIMER_RE = re.compile(r"[^.!?\n]*(?:financial advice|investment advi[cs]e|educational purposes only)[^.!?\n]*[.!?]?\s*", re.I)
# timestamp lines like "[00:00] Hook" / "1:20 - Step 1"; clock times ("9:30 am") are left alone
_TIMESTAMP_RE = re.compile(r"^[ \t]*\[?\d{1,2}:\d{2}(?::\d{2})?\]?(?![ \t]*(?:am|pm|a\.m\.|p\.m\.)\b).*$", re.I | re.M)


def _clean_prose(text: str) -> str:
    """Drop disclaimer sentences, timestamp lines, hashtags and URLs the model added despite instructions;
    the description builder appends the canonical versions itself (so nothing appears twice)."""
    text = _DISCLAIMER_RE.sub("", str(text or ""))
    text = _TIMESTAMP_RE.sub("", text)
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"(?m)^\s*(#\w+\s*)+$", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _validate(d: dict, cfg: dict) -> None:
    for k in ("title", "tags", "sections", "shorts"):
        if k not in d:
            raise ValueError(f"Script missing key: {k}")
    # description parts (v3.2.1). Older single-field replies are split: first sentence -> hook, rest -> body.
    hook = _clean_prose(d.get("description_hook") or "")
    body = _clean_prose(d.get("description_body") or "")
    if not hook and not body:
        legacy = _clean_prose(d.get("description") or "") or d["title"]
        parts = re.split(r"(?<=[.!?])\s+", legacy, 1)
        hook = parts[0] if parts[0][-1:] in ".!?" else parts[0] + "."
        body = parts[1] if len(parts) > 1 else ""
    if len(hook) > 160:  # keep the search snippet intact
        cut = hook[:157]
        hook = cut[: cut.rfind(" ")].rstrip(",;:") + "…"
    facts = [str(x).strip(" -•·") for x in (d.get("key_facts") or []) if str(x).strip()][:5]
    tags_ = []
    for h in (d.get("hashtags") or []):
        h = "#" + re.sub(r"[^A-Za-z0-9]", "", str(h))
        if len(h) > 2 and h.lower() not in ("#viral", "#shorts", "#fyp", "#trending", "#youtube") and h not in tags_:
            tags_.append(h)
    d["description_hook"], d["description_body"], d["key_facts"], d["hashtags"] = hook, body, facts, tags_[:5]
    d["description"] = (hook + "\n\n" + body).strip()   # kept for anything that still reads the old field
    # thumbnail spec: normalise, and accept the pre-v3.2.1 flat keys (thumbnail_text / thumbnail_query) as a fallback
    th = d.get("thumbnail") if isinstance(d.get("thumbnail"), dict) else {}
    hero = str(th.get("hero") or d.get("thumbnail_text") or d["title"]).strip()[:12]
    cmp_ = th.get("compare") if isinstance(th.get("compare"), dict) else None
    if cmp_ and not all(isinstance(cmp_.get(s), dict) and cmp_[s].get("label") and cmp_[s].get("value") for s in ("left", "right")):
        cmp_ = None
    hic = th.get("hero_is_cost")
    if isinstance(hic, str):                      # LLMs sometimes emit "false" as a string
        hic = hic.strip().lower() in ("true", "yes", "1")
    elif hic is None:
        hic = hero.startswith(("+", "-")) or "cost" in d["title"].lower()
    icon = th.get("icon")
    d["thumbnail"] = {
        "hero": hero,
        "hero_label": str(th.get("hero_label") or "").strip()[:28].upper(),
        "hero_is_cost": bool(hic),
        "compare": cmp_ and {s: {"label": str(cmp_[s]["label"])[:14].upper(), "value": str(cmp_[s]["value"])[:10]} for s in ("left", "right")},
        "icon": icon if isinstance(icon, str) and icon in _THUMB_ICONS else None,
        "query": str(th.get("query") or d.get("thumbnail_query") or "worried person portrait").strip()[:60],
    }
    alts = d.get("alt_titles")
    d["alt_titles"] = [str(a)[:100] for a in alts if str(a).strip()] if isinstance(alts, list) else []
    d.setdefault("thumbnail_text", hero)   # older code paths / selftest still read this
    if len(d["sections"]) < 4:
        raise ValueError("Script has too few sections")
    d["title"] = d["title"][:100]
    d["tags"] = [t[:30] for t in d["tags"]][:25]
    d["shorts"] = d["shorts"][: cfg["shorts"]["count"]]
    for s in d["sections"]:
        s.setdefault("stat", None)
        s.setdefault("short_worthy", False)
        s.setdefault("visual_query", "finance")
    d.setdefault("chart", None)


def word_count(script: dict) -> int:
    return sum(len(s["narration"].split()) for s in script["sections"])


if __name__ == "__main__":  # quick manual test: python -m pipeline.script
    from .config import load_config
    from .topics import pick_topic
    cfg = load_config()
    pick = pick_topic()
    s = generate_script(cfg, pick)
    print(json.dumps(s, indent=2)[:3000])
    print("words:", word_count(s))
