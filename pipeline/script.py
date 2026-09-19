"""Script generation. Produces a structured script the renderer can consume directly.

Design goals (these are what keep the channel on the right side of YouTube's
'inauthentic content' policy): a unique worked example with real numbers in every
video, rotating formats, a visible on-screen data chart, a consistent brand voice.
"""
import json
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
  "description": "150-250 words. First line is a hook. Include 3 timestamps placeholders like [00:00], a one-line disclaimer, and a call to subscribe. No links.",
  "tags": ["12-18 lowercase tags"],
  "sections": [
    {
      "id": "hook",
      "heading": "3-6 word on-screen heading",
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


def _validate(d: dict, cfg: dict) -> None:
    for k in ("title", "description", "tags", "sections", "shorts"):
        if k not in d:
            raise ValueError(f"Script missing key: {k}")
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
    # description must include disclaimer for policy + trust
    disc = cfg["channel"]["disclaimer"]
    if disc.lower() not in d["description"].lower():
        d["description"] += f"\n\n{disc}"


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
