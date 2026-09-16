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
Return ONLY valid JSON matching the schema requested."""

SCHEMA = """{
  "title": "<= 60 chars, curiosity + specific number or contrast, no clickbait lies",
  "alt_titles": ["2 alternative titles"],
  "thumbnail_text": "2-4 words, all caps, punchy (e.g. '1% = $180,000')",
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
    {"id": "close", "heading": "...", "narration": "60-90 words: one-sentence recap, the action rule, then EXACTLY this sign-off text: {signoff}", "visual_query": "...", "stat": null, "short_worthy": false}
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
    ch = cfg["channel"]
    target_words = int(cfg["video"]["target_minutes"] * 150)  # ~150 wpm spoken
    user = f"""Channel: {ch['name']} — {ch['tagline']}
Niche: {ch['niche']}
Audience: {ch['audience']}
Sign-off (must appear verbatim at the end of the 'close' section): {ch['signoff']}

TOPIC: {pick['topic']}
CATEGORY: {pick['category']}
FORMAT TO FOLLOW: {pick['format']}

Total narration length across all sections: about {target_words} words (±10%).
Use 7 sections total: hook, s1..s5, close. Mark exactly 1-2 sections as short_worthy.
The 'chart' must visualise the video's core worked example with 2 series and 6-12 points each; make the numbers consistent with the narration.
The three Shorts must each be a different angle on the topic (the number, the mistake, the rule) and must NOT repeat the long video's sentences.

Return JSON exactly matching this schema:
{SCHEMA.replace('{signoff}', ch['signoff'])}"""
    data = ask_json(cfg, SYSTEM, user)
    _validate(data, cfg)
    return data


def _validate(d: dict, cfg: dict) -> None:
    for k in ("title", "description", "tags", "sections", "shorts", "thumbnail_text"):
        if k not in d:
            raise ValueError(f"Script missing key: {k}")
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
