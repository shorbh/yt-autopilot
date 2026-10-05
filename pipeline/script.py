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
  within the first two sentences (YouTube indexes the transcript). The hook's number must be one the video
  itself later explains or that appears in SOURCES — never an invented statistic ("creates 80% of all wealth").
  If no sourced number fits, hook with a concrete comparison or a cost the viewer can verify instead.
- OPEN LOOP: the hook's last sentence before the roadmap promises ONE specific payoff that arrives LATE in the video and
  says where ("…and at the end, the one rule that tells you where your first $100 goes" / "stick around for number six —
  it's the one almost everyone gets backwards"). The payoff must really be in that section. Around the MIDDLE section,
  one sentence reminds the viewer the payoff is still ahead ("the rule I promised comes right after these last two").
- Never use violent or self-harm metaphors ("financial suicide", "kill your savings"); say "a financial disaster" instead.
- MICRO-HOOKS: every section except 'close' ends with a one-sentence forward tease that opens a
  curiosity gap ("and the second mistake costs even more", "the twist is in year three"). Never a summary.
- TAIL: the 'close' section never says "in summary", "to recap", "that's all" or fades out. It delivers the
  action rule in two or three sentences, then ONE bridge sentence, then the sign-off. The bridge is either a
  by-name recommendation of the PREVIOUS video when one is given, or otherwise a generic forward tease that
  must NOT name a topic, product, number or event (next week's subject is chosen from the news):
  good: "Next week I'm pulling apart a money habit that looks smart and quietly isn't."
        "There's a mechanic almost nobody checks until it's cost them — that's next."
        "Next week's one is the kind of thing you'll wish someone had told you at twenty-five."
  bad: anything mentioning mortgages, taxes, the Fed, a company, a percentage or a dollar figure.
  Write a FRESH line in your own words every time — never reuse the example sentences above.
- Any named person keeps the same name, gender and pronouns throughout; state gender implicitly via pronouns.

Write for the EAR, not the page (a synthetic voice reads this; the words must carry the feeling):
- SIGNPOST. Every body section (s1, s2, …) OPENS with a 2-6 word spoken signpost sentence that tells the listener where we are
  ("Okay. Number two: bonds." / "Now the part most people skip." / "Here's where it gets interesting."). The hook
  ENDS with a one-line roadmap ("Seven types, safest first. Let's go."). The close opens with "So, the rule:" or similar.
- LAND the point. After the key number in each section, one short sentence that says what it means in plain words,
  then move on. Never run two ideas together in one sentence.
- Vary sentence length on purpose: after a long sentence, a short one. Four words. Then build again.
- Talk to "you". One rhetorical question per section, answered immediately. One dry aside per video, never more.
- Put the surprising number at the END of its sentence, where the voice lands on it.
- Concrete over abstract: "$514 more every month" beats "a significantly higher payment".
Return ONLY valid JSON matching the schema requested."""

SCHEMA = """{
  "title": "<= 60 chars, Headline Case with punctuation. Pick the pattern that fits the KIND: MAP -> 'Every Type of X Explained for Beginners' | 'A vs B vs C vs D Explained' | 'How to Go From X to Y (Step by Step)' | 'How Much You NEED to X to Y in N Years'; SEARCH QUERY given -> keep its words and order as a headline ('How Much House Can I Afford With a $75K Salary?'); otherwise a specific claim with a number ('A 7% Mortgage Costs $200,000 More'). Words that out-perform on small channels: Explained, for Beginners, Every Type, vs, Step by Step, NEED. Numbers ALWAYS as numerals/symbols (40%, $200,000 — never 'Forty Percent'). Plain words a 25-year-old uses; no jargon terms as the subject ('Authorized User Piggybacking'), no 'Colon: Subtitle' constructions, no clickbait lies",
  "alt_titles": ["2 alternative titles"],
  "thumbnail": {
    "hero": "THE number the viewer will search for, or the cost delta, <= 9 chars, e.g. '+$200K' | '7%' | '$1,348/mo'. Must appear in the title or be its direct consequence; NEVER a derived difference like '2%' when the title says 7%",
    "hero_label": "2-3 words MAX, all caps, readable on a TV across a room, e.g. 'MORE INTEREST' | 'PER MONTH'",
    "hero_is_cost": true,
    "compare": {"left": {"label": "5% RATE", "value": "$373K"}, "right": {"label": "7% RATE", "value": "$558K"}, "_rule": "LEFT = the BETTER outcome for the viewer (shown green), RIGHT = the worse/costlier one (shown red)"},
    "icon": "one Lucide icon for the topic: home | car | piggy-bank | credit-card | briefcase | receipt | landmark | graduation-cap | heart-pulse | shopping-cart | chart-line | wallet",
    "items": [{"label": "STOCKS", "icon": "chart-line"}, {"label": "BONDS", "icon": "landmark"}, {"label": "REAL ESTATE", "icon": "home"}, {"label": "CRYPTO", "icon": "coins"}],
    "map_title": "2-4 words for the bright map thumbnail, e.g. 'EVERY INVESTMENT' | 'EVERY TYPE OF FUND' | 'SAVING → INVESTING' (MAP kind only; else null)",
    "promise": "2-3 words, the benefit only this video gives, shown as a sticker on the map thumbnail: 'WHERE TO START' | 'RANKED BY RISK' | 'SAFEST FIRST' | 'FOR $100' | 'IN 8 MINUTES' (MAP kind only; else null)",
    "query": "3-5 word stock-photo search for ONE PERSON with an expression matching the title's emotion, e.g. 'worried man glasses portrait' | 'shocked woman laptop' | 'serious businesswoman office'"
  },
  "description_hook": "1-2 sentences, <= 150 characters TOTAL, starting with the primary keyword phrase, written as a curiosity hook that extends the title (never 'In this video we...'). Numerals and symbols ($400,000, 7%), never spelled-out numbers.",
  "description_body": "2-3 short paragraphs, 120-200 words, plain prose: the problem, what the viewer will be able to do after watching, and the related terms a searcher would use (secondary keywords woven into sentences, NOT a list). Numerals only. No disclaimer, no subscribe line, no timestamps, no links, no hashtags.",
  "key_facts": ["3-5 items of <= 8 words, EACH containing a number from the video, e.g. '$400,000 loan · 5% vs 7%', '+$200,000 lifetime interest', '$10,000 → $45,000 in 10 years'"],
  "hashtags": ["3-5 specific CamelCase hashtags without generic ones like #viral, e.g. '#MortgageRates', '#Amortization', '#PersonalFinance'"],
  "tags": ["12-18 lowercase tags"],
  "sections": [
    {
      "id": "hook",
      "heading": "3-6 word on-screen heading that also works as a chapter title in search: concrete and keyword-bearing ('The 7% Payment Shock', 'Amortization: Year 1 vs Year 10'), never generic ('Introduction', 'Step 1')",
      "narration": "60-90 words. State the surprising claim and the number that proves it.",
      "visual_query": "2-4 word stock-footage search (e.g. 'calculator desk')",
      "stat": {"label": "on-screen big number label", "value": "$180,000"} ,
      "short_worthy": true,
      "covers": ["MAP videos only: the thumbnail.items labels this section explains, in order, e.g. ['CASH', 'BONDS']; [] for hook/close"]
    },
    {"id": "s1", "heading": "...", "narration": "{body_words} words", "visual_query": "...", "stat": null, "short_worthy": false, "covers": []},
    {"id": "s2", "...": "..."},
    {"id": "...", "...": "... continue s3, s4, ... up to {last_body}"},
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
    {"hook_title": "<= 7 words: the claim itself in numerals, shown full-screen in the first second ('Your card charges interest DAILY', '$500 a month = $452,000')", "hook_face": "3-4 word stock-photo search for ONE person whose expression matches the claim ('shocked woman phone', 'worried man bills')", "narration": "110-150 words (about 45-55 seconds). FIRST SENTENCE <= 12 words and IS the claim — a loss, a contradiction or a number; no greeting, no 'did you know'. Self-contained, ends with 'Full breakdown on the channel.'", "visual_query": "..."}{more_shorts}
  ]
}"""


def _schema(cfg: dict, last_body: str = "s5", body_words: str = "150-220") -> str:
    """SCHEMA with the sign-off filled in, exactly shorts.count entries shown (the model copies the example count),
    and the body-section plan (s1..last_body, words per section) spelled out."""
    n = int(cfg["shorts"]["count"])
    more = "".join(',\n    {"hook_title": "...", "hook_face": "...", "narration": "...", "visual_query": "..."}' for _ in range(max(0, n - 1)))
    return (SCHEMA.replace("{signoff}", cfg["channel"]["signoff"]).replace("{more_shorts}", more)
            .replace("{body_words}", body_words).replace("{last_body}", last_body))


# Measured on the channel with Fish at speed 0.96, breath-group pauses, chapter cards and section gaps:
#   run 10: 920 words -> 5:40 (162 wpm, few cues)      run 20: 1,444 words -> 10:12 (142 wpm, 156 cues + 10 cards)
# With cues capped (v6.3.1) the truth sits between; 150 keeps an 8-minute target near 8-9 minutes. Models also undershoot
# the budget they are given, so the prompt asks for slightly more than the floor needs.
WORDS_PER_MIN = 150


def target_minutes(cfg: dict) -> float:
    """Adaptive length from the weekly review, clamped to config floor/cap; config value until data exists."""
    from .state import load_performance
    v = cfg["video"]
    lo, hi = float(v.get("min_minutes", 7)), float(v.get("max_minutes", 12))
    t = load_performance().get("target_minutes") or v["target_minutes"]
    return max(lo, min(hi, float(t)))


def _section_plan(cfg: dict, pick: dict, target_words: int) -> tuple[int, str, str, str]:
    """(body_sections, per-section word range, prose instruction, last body id). MAP videos get ONE section per item plus a
    decision-rule section, so each chapter card / map highlight is exactly one item (run 10 packed 7 items into 5
    sections and the progress map could not follow). Other kinds keep the classic 5 body sections."""
    n_body, last = 5, "s5"
    plan = "Use 7 sections total: hook, s1..s5, close."
    if pick.get("kind") == "map":
        n_body, last = 8, "s(N+1)"   # planning assumption: 7 items + decision rule; the model may pick 5-8 items
        plan = ("Pick N = 5-8 items. Use ONE body section PER ITEM, in coverage order, plus ONE decision-rule section: "
                "hook, s1..sN (each 'covers' exactly one label from thumbnail.items, which therefore has exactly N entries), "
                "s(N+1) = the decision rule ('start here if you are X', covers []), close. With 7 items that is 10 sections. "
                "Never put two items in one section — the on-screen progress map highlights one item per section. "
                "Fewer items means proportionally LONGER sections: the total word count is what must hold.")
    per = max(90, (target_words - 150) // n_body)   # hook ~80 + close ~70 come off the top
    return n_body, f"{per}-{int(per * 1.25)}", plan, last


def generate_script(cfg: dict, pick: dict, previous: dict | None = None) -> dict:
    """`previous` = the channel's most recent long video ({title, video_id}); the close recommends it by name
    (a verbal end-screen bridge) — the one forward link we CAN make without pre-committing next week's topic."""
    from .state import load_performance
    ch = cfg["channel"]
    minutes = target_minutes(cfg)
    target_words = int(minutes * WORDS_PER_MIN * 1.08)   # +8%: models undershoot; the floor below is what we actually enforce
    floor_words = int(float(cfg["video"].get("min_minutes", 7)) * WORDS_PER_MIN)
    _, body_words, plan, last_body = _section_plan(cfg, pick, target_words)
    hints = load_performance().get("script_hints") or []
    hint_block = ("\nLESSONS FROM THIS CHANNEL'S RETENTION DATA (apply them):\n- " + "\n- ".join(hints) + "\n") if hints else ""
    news_block = ""
    if pick.get("news_hook"):
        news_block = (f"\nNEWS HOOK (this week's event; use it in the hook and title so the video rides the search wave, "
                      f"but explain the underlying mechanism so the video stays useful for years): {pick['news_hook']}\n")
    query_block = ""
    if pick.get("query") and pick.get("source") == "demand":
        query_block = (f"\nSEARCH QUERY (people type exactly this into YouTube; the title must keep these words in this order, "
                       f"and the hook must speak them in the first two sentences): \"{pick['query']}\"\n")
    kind_block = ""
    if pick.get("kind") == "map":
        kind_block = ("\nKIND: MAP (beginner taxonomy — the format that out-performs 50-100x on small channels). Promise COMPLETE "
                      "coverage in the title and deliver it: 6-8 items (or 4 options, or N steps) in a sensible order, ONE "
                      "paragraph each with the ONE number that matters for it (cost, return, risk, limit, time), the same "
                      "criteria applied to every item so they are comparable, and a clear 'start here if you are X' decision at "
                      "the end. ONE item per body section; the hook names how many items and the single most surprising "
                      "number among them (a number the video itself then explains). Also return 'thumbnail.items' (IN THE ORDER THE "
                      "VIDEO COVERS THEM) and 'thumbnail.map_title', and set each section's 'covers' to the exact item label it "
                      "explains — the on-screen progress map highlights that item while the section plays, so they must match.\n")
    elif pick.get("kind") == "story":
        kind_block = ("\nKIND: MONEY STORY. Tell it as a narrative about the subject (the company, product, price or event): "
                      "what happened, the mechanism underneath, the numbers, and what it means for the viewer's own money. "
                      "Still include one worked calculation the viewer can reproduce.\n")
        if "name-hook" in str(pick.get("format", "")):
            kind_block += ("REAL PERSON RULES (non-negotiable): the named person is the SUBJECT of analysis. State only positions "
                           "that appear in SOURCES, attributed ('in his 2013 shareholder letter, Buffett wrote…'). Never invent or "
                           "paraphrase-as-quote; never imply they endorse this channel or any product; never speculate about their "
                           "private finances or motives. Our conclusions are ours: 'the math says…', not 'he says…'. Title pattern: "
                           "\"<Name>'s <Rule> — Checked Against the Math\" or \"Why <Name>'s <Rule> Works (and When It Doesn't)\".\n")
    source_block = pick.get("source_block") or ""
    from .outliers import patterns_block
    win_block = patterns_block(10)   # titles out-performing on small channels this quarter (empty until the first Sunday scout)
    prev_block = ""
    if previous and previous.get("title"):
        prev_block = (f"\nPREVIOUS VIDEO ON THE CHANNEL: \"{previous['title']}\" — in the 'close' section, INSTEAD of the generic "
                      f"tease, recommend this video by name in one natural sentence (e.g. \"If you haven't seen why ..., "
                      f"that one's on the channel now\"), then the sign-off.\n")
    user = f"""Channel: {ch['name']} — {ch['tagline']}{hint_block}
Niche: {ch['niche']}
Audience: {ch['audience']}
Sign-off (must appear verbatim at the end of the 'close' section): {ch['signoff']}

TOPIC: {pick['topic']}
CATEGORY: {pick['category']}
FORMAT TO FOLLOW: {pick['format']}{query_block}{kind_block}{news_block}{prev_block}{win_block}{source_block}

Total narration length across all sections: about {target_words} words (±10%) — this is a {minutes:.0f}-minute video; a draft
under {floor_words} words will be rejected. Each body section runs {body_words} words.
{plan} Mark exactly 1-2 sections as short_worthy.
Return exactly {cfg['shorts']['count']} Shorts in 'shorts'.
The 'chart' must visualise the video's core worked example with 1-2 series and 4-12 points each; make the numbers consistent with the narration.
Both chart series MUST be in the same unit and a similar magnitude (e.g. two dollar balances), never a price next to a total value — otherwise one line is flat.
THUMBNAIL: 'hero' is the single number a scroller must see — the headline figure from the title or the cost it causes
(e.g. title 'Why a 7% mortgage costs $200K more' -> hero '+$200K', label 'MORE INTEREST'; compare 5% vs 7% totals).
'compare' is only for videos with two directly comparable figures in the same unit; otherwise set it to null.
'hero_is_cost' is true when the hero is money lost / extra paid (shown in red), false when it is a gain or a rate (gold).
'query' must describe a PERSON (face visible) whose expression matches the title's emotion — faces lift click-through.
For MAP videos also return 'items': the 4-8 things the video covers, each with a 1-2 word UPPERCASE label and a Lucide icon from:
chart-line, landmark, home, building-2, coins, piggy-bank, wallet, credit-card, receipt, briefcase, shield, shield-check, percent,
trending-up, trending-down, calendar, clock, gift, graduation-cap, heart-pulse, car, globe, factory, gem, scale, lock, key, banknote,
hand-coins, users, layers, package, umbrella, target. The bright icon-grid thumbnail is built from these.
The Shorts must each be a different angle on the topic (the number, the mistake, the rule, the story) and must NOT repeat the long video's sentences.
SHORTS COLD OPEN: 4 of 5 viewers swiped away in the first second on this channel. Every Short's first sentence is the claim
itself (a loss, a contradiction or a number, <= 12 words) and 'hook_title' is that same claim in <= 7 words — it fills the screen
on frame one. No warm-up words.

Return JSON exactly matching this schema:
{_schema(cfg, last_body, body_words)}"""
    data = ask_json(cfg, SYSTEM, user)
    _validate(data, cfg)
    # Length guard: models routinely undershoot (run #14: 776 words for an 8-minute target -> a 5-minute video; run 10:
    # 920 words -> 5:40 against a 7-minute FLOOR). Up to two corrective passes with the shortfall spelled out per
    # section; keep whichever draft is closest to target. The floor is a hard channel rule (watch-hours), so we spend
    # the extra LLM calls (~$0.01) rather than ship a short video.
    wc = word_count(data)
    for attempt in range(2):
        if wc >= max(0.9 * target_words, floor_words):
            break
        print(f"      script is {wc} words vs {target_words} target (floor {floor_words}); asking for a fuller draft ({attempt + 1}/2)")
        short_secs = ", ".join(f"{s['id']} ({len(s['narration'].split())}w)" for s in data["sections"]
                               if s.get("id") not in ("hook", "close"))
        try:
            data2 = ask_json(cfg, SYSTEM, user + f"\n\nYOUR PREVIOUS DRAFT HAD ONLY {wc} WORDS OF NARRATION — about "
                             f"{wc / WORDS_PER_MIN:.1f} minutes, below the {floor_words}-word floor. Section lengths were: {short_secs}. "
                             f"Every body section must reach {body_words} words: add a second worked example with real numbers, "
                             f"the step-by-step of the calculation, or the common objection and its answer. Do NOT add sections "
                             f"or pad with filler. Same JSON shape.")
            _validate(data2, cfg)
            wc2 = word_count(data2)
            if abs(wc2 - target_words) < abs(wc - target_words):
                data, wc = data2, wc2
        except Exception as e:  # noqa: BLE001 - keep the better draft so far
            print(f"[warn] fuller draft failed ({str(e)[:100]}); keeping the previous one")
    if wc < floor_words:
        print(f"[warn] script still {wc} words (< {floor_words} floor) after retries; video will run ~{wc / WORDS_PER_MIN:.1f} min")
    _ensure_open_loop(cfg, data)
    data["sources"] = [{"title": s.get("title", ""), "url": s.get("url", "")} for s in (pick.get("sources") or []) if s.get("url")]
    data["real_people"] = pick.get("kind") == "story"   # grounded stories: no stock faces for real people (render/storyboard)
    data["kind"] = pick.get("kind", "mechanic")
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


_SMALL = {"a", "an", "the", "and", "or", "of", "on", "in", "at", "to", "for", "with", "vs", "vs.", "by", "from", "per"}
_ACRONYMS = {"ira", "iras", "etf", "etfs", "hsa", "apr", "apy", "hysa", "fdic", "irs", "cds", "fico", "rsu", "rsus", "pmi", "fha", "llc", "s&p", "usa", "gdp", "cpi"}
_QWORDS = ("how", "why", "what", "when", "should", "is", "can", "do", "does", "which", "are", "will")


_NUM_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
              "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90, "hundred": 100}


def numerals(text: str) -> str:
    """On-screen/metadata text uses symbols: '90 percent' -> '90%', 'versus' -> 'vs', 'forty percent' -> '40%',
    'two hundred dollars' -> '$200'. (The narration keeps spelled-out numbers for the voice; this is for titles,
    hook cards and key facts.)"""
    t = str(text)
    words = "|".join(_NUM_WORDS)
    tens = "twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety"
    t = re.sub(r"\b(a|an|" + words + r") hundred (thousand )?dollars\b",
               lambda m: f"${_NUM_WORDS.get(m.group(1).lower(), 1)}00{',000' if m.group(2) else ''}", t, flags=re.I)
    t = re.sub(r"\b(" + tens + r")-(one|two|three|four|five|six|seven|eight|nine) percent\b",
               lambda m: f"{_NUM_WORDS[m.group(1).lower()] + _NUM_WORDS[m.group(2).lower()]}%", t, flags=re.I)
    t = re.sub(r"(?<!-)\b(" + words + r") percent\b", lambda m: f"{_NUM_WORDS[m.group(1).lower()]}%", t, flags=re.I)
    t = re.sub(r"(\d)\s*percent\b", r"\1%", t, flags=re.I)
    t = re.sub(r"\bversus\b", "vs", t, flags=re.I)
    t = re.sub(r"\b(\d[\d,]*) dollars\b", r"$\1", t, flags=re.I)
    return t


def headline(title: str) -> str:
    """Search-query titles arrive as the raw lowercase query ('how much house can i afford with 75k salary').
    Make it a headline: Title Case (small words lower), 'i' -> 'I', 75k -> $75K, '?' on questions."""
    t = numerals(" ".join(str(title).split()))
    if not t:
        return t
    t = re.sub(r"\b(401|403|457)k\b", r"\1(k)", t, flags=re.I)             # retirement plans, not dollars
    t = re.sub(r"\b(?!(?:401|403|457)\()(\d{2,3})k\b", r"$\1K", t, flags=re.I)   # 75k -> $75K
    t = re.sub(r"\$\$", "$", t)
    words = t.split()
    out = []
    for i, w in enumerate(words):
        lw = w.lower().rstrip("?.,!")
        if lw == "i":
            out.append("I")
        elif lw in _ACRONYMS:
            out.append(w.upper())
        elif i not in (0, len(words) - 1) and lw in _SMALL:
            out.append(w.lower())
        elif w.isupper() and len(w) > 1 and not w[0].isdigit():            # keep acronyms (IRA, ETF, HSA)
            out.append(w)
        else:
            out.append(w[:1].upper() + w[1:])
    t = " ".join(out)
    if t.split()[0].lower() in _QWORDS and not t.rstrip().endswith(("?", "!", ".")):
        t += "?"
    return t[:100]


_TILE_MAX = 15   # chars that fit a 4x2 tile label at the floor font size (thumbnail and in-video grid)
_TILE_ALIASES = {"CERTIFICATES OF DEPOSIT": "CDS", "CERTIFICATES OF": "CDS", "CERTIFICATES": "CDS", "CERTIFICATE OF DEPOSIT": "CDS",
                 "INDIVIDUAL STOCKS": "STOCKS", "SINGLE STOCKS": "STOCKS", "HIGH YIELD SAVINGS": "HIGH YIELD", "HIGH-YIELD SAVINGS": "HIGH YIELD",
                 "SAVINGS ACCOUNTS": "SAVINGS", "TREASURY BILLS": "T-BILLS", "TREASURY BONDS": "TREASURIES", "CRYPTOCURRENCY": "CRYPTO",
                 "CRYPTOCURRENCIES": "CRYPTO", "EXCHANGE TRADED FUNDS": "ETFS", "EXCHANGE-TRADED FUNDS": "ETFS", "GOVERNMENT BONDS": "GOV BONDS",
                 "CORPORATE BONDS": "CORP BONDS", "MONEY MARKET FUNDS": "MONEY MARKET", "PRECIOUS METALS": "GOLD & METALS",
                 "RETIREMENT ACCOUNTS": "401(K) & IRA", "PEER TO PEER LENDING": "P2P LENDING", "PEER-TO-PEER LENDING": "P2P LENDING"}
_TILE_HEADS = {"STOCKS", "BONDS", "FUNDS", "ETFS", "CASH", "GOLD", "CRYPTO", "SAVINGS", "ESTATE", "ANNUITIES", "OPTIONS", "FUTURES", "LENDING", "ART", "LAND"}


def _tile_label(raw: str) -> str:
    """1-2 word tile label that fits without a mid-word cut (run 21: 'CERTIFICATES O', 'INDIVIDUAL STO'). Known long
    names get a short alias; otherwise keep two words if they fit, else the head noun, else the first word."""
    words = [w for w in re.sub(r"[^A-Z0-9&()\-/ ]", "", raw.upper()).split() if w]
    if not words:
        return "ITEM"
    full = " ".join(words)
    if full in _TILE_ALIASES:
        return _TILE_ALIASES[full]
    two = " ".join(words[:2])
    if two in _TILE_ALIASES:
        return _TILE_ALIASES[two]
    if len(two) <= _TILE_MAX:
        return two
    if len(words) >= 2 and words[1] in _TILE_HEADS and len(words[1]) <= _TILE_MAX:
        return words[1]
    first = words[0] if words else "ITEM"
    return first[:_TILE_MAX] if len(first) <= _TILE_MAX else first[:_TILE_MAX - 1] + "."


def _validate(d: dict, cfg: dict) -> None:
    for k in ("title", "tags", "sections", "shorts"):
        if k not in d:
            raise ValueError(f"Script missing key: {k}")
    d["sections"] = [s for s in (d["sections"] if isinstance(d["sections"], list) else [])
                     if isinstance(s, dict) and str(s.get("narration") or "").strip()]
    for i, s in enumerate(d["sections"]):   # ids are what the storyboard, map binding and open-loop pass key on
        s["id"] = str(s.get("id") or ("hook" if i == 0 else "close" if i == len(d["sections"]) - 1 else f"s{i}"))
        s.setdefault("heading", "")
    d["title"] = headline(str(d["title"]))
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
    # key facts are for skimmers and search: keep only items that carry a number; slogans add nothing
    facts = [numerals(str(x).strip(" -•·")) for x in (d.get("key_facts") or []) if str(x).strip() and re.search(r"\d", str(x))][:5]
    if len(facts) < 2:
        facts = []
    tags_ = []
    for h in (d.get("hashtags") or []):
        h = "#" + re.sub(r"[^A-Za-z0-9]", "", str(h))
        if len(h) > 2 and h.lower() not in ("#viral", "#shorts", "#fyp", "#trending", "#youtube") and h not in tags_:
            tags_.append(h)
    d["description_hook"], d["description_body"], d["key_facts"], d["hashtags"] = hook, body, facts, tags_[:5]
    d["description"] = (hook + "\n\n" + body).strip()   # kept for anything that still reads the old field
    # thumbnail spec: normalise, and accept the pre-v3.2.1 flat keys (thumbnail_text / thumbnail_query) as a fallback
    th = d.get("thumbnail") if isinstance(d.get("thumbnail"), dict) else {}
    hero = str(th.get("hero") or d.get("thumbnail_text") or d["title"]).strip()[:10]   # 10 chars fits the TV-size type
    cmp_ = th.get("compare") if isinstance(th.get("compare"), dict) else None
    if cmp_ and not all(isinstance(cmp_.get(s), dict) and cmp_[s].get("label") and cmp_[s].get("value") for s in ("left", "right")):
        cmp_ = None
    hic = th.get("hero_is_cost")
    if isinstance(hic, str):                      # LLMs sometimes emit "false" as a string
        hic = hic.strip().lower() in ("true", "yes", "1")
    elif hic is None:
        hic = hero.startswith(("+", "-")) or "cost" in d["title"].lower()
    icon = th.get("icon")
    items = []
    for it in (th.get("items") or [])[:8]:
        if isinstance(it, dict) and it.get("label"):
            items.append({"label": _tile_label(str(it["label"])),
                          "icon": str(it.get("icon") or "").strip().lower() or None})
    # MAP binding: each section's `covers` -> item labels; items re-ordered by first coverage so the grid, the thumbnail
    # and the narration all run in the same order. Sections without `covers` get a text match on heading + narration.
    labels = [it["label"] for it in items]
    if items:
        def _match(sec: dict) -> list[str]:
            cv = sec.get("covers")
            cv = [cv] if isinstance(cv, str) else (cv if isinstance(cv, list) else [])
            raw = [str(x).upper().strip() for x in cv if str(x).strip()]
            raw += [t for t in (_tile_label(x) for x in raw) if t and t != "ITEM"]   # 'CERTIFICATES OF DEPOSIT' must hit the 'CDS' tile
            # exact label first; substring only when nothing matched exactly ('I BONDS' must not also tag 'BONDS')
            got = [l for l in labels if l in raw] or [l for l in labels if any(l in r or r in l for r in raw)]
            if not got and "covers" not in sec:   # model omitted the field: label word in heading or opening narration
                text = (str(sec.get("heading", "")) + " " + str(sec.get("narration", ""))[:300]).upper()
                got = [l for l in labels if any(w for w in re.findall(r"[A-Z]{3,}", l) if w in text)]
            return got   # an explicit [] (e.g. the decision-rule section) stays [] -> grid shows everything ticked
        order: list[str] = []
        for sec in d["sections"]:
            sec["covers"] = _match(sec) if sec.get("id") not in ("hook", "close") else []
            for l in sec["covers"]:
                if l not in order:
                    order.append(l)
        items = sorted(items, key=lambda it: order.index(it["label"]) if it["label"] in order else 99)
    d["thumbnail"] = {
        "items": items,
        "map_title": " ".join(str(th.get("map_title") or "").upper().split()[:4])[:24],
        "promise": numerals(" ".join(str(th.get("promise") or "").upper().split()[:3]))[:18],
        "hero": hero,
        "hero_label": " ".join(str(th.get("hero_label") or "").split()[:3]).strip()[:24].upper(),
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
    d["tags"] = [str(t)[:30] for t in (d["tags"] if isinstance(d["tags"], list) else [])][:25]
    # run 22: the model returned the Shorts as bare strings; accept a string as the narration, drop anything else
    shorts: list[dict] = []
    for sh in (d["shorts"] if isinstance(d["shorts"], list) else []):
        if isinstance(sh, dict) and str(sh.get("narration") or "").strip():
            shorts.append(sh)
        elif isinstance(sh, str) and len(sh.split()) >= 20:
            shorts.append({"narration": sh.strip()})
    if not shorts:
        raise ValueError("Script has no usable Shorts")
    d["shorts"] = shorts[: cfg["shorts"]["count"]]
    for sh in d["shorts"]:
        if not sh.get("hook_title"):   # first sentence is the claim (schema rule) — use it when the model gave no title
            first = re.split(r"(?<=[.!?])\s+", sh["narration"].strip(), 1)[0]
            sh["hook_title"] = " ".join(first.split()[:7])
        sh.setdefault("visual_query", d["sections"][0].get("visual_query", "finance") if isinstance(d["sections"][0], dict) else "finance")
        sh["hook_title"] = numerals(" ".join(str(sh.get("hook_title") or d["title"]).split()[:8]))[:48]
        sh["hook_face"] = str(sh.get("hook_face") or "surprised person portrait")[:40]
    for s in d["sections"]:
        s.setdefault("stat", None)
        s.setdefault("short_worthy", False)
        s.setdefault("visual_query", "finance")
        s["narration"] = _soften(str(s.get("narration") or ""))
    for sh in d["shorts"]:
        sh["narration"] = _soften(str(sh.get("narration") or ""))
        sh["hook_title"] = _soften(sh["hook_title"])
    d["title"] = _soften(d["title"])
    d["description_body"] = _soften(d["description_body"])
    d.setdefault("chart", None)


# ------------------------------------------------------------------ retention: the open loop (enforced, not just prompted)

_LOOP_RE = re.compile(r"\b(at the end|by the end|stick around|stay (?:with me|to the end|until)|coming up|later in|"
                      r"last one|the final (?:one|step|rule|type)|i['’]?ll show you|wait (?:for|until)|number (?:six|seven|eight|nine|[6-9]))\b", re.I)
_REMIND_RE = re.compile(r"\b(promised|still (?:coming|ahead)|coming up|before the end|hang on|almost there|two more)\b", re.I)


def _ensure_open_loop(cfg: dict, d: dict) -> None:
    """Run 21: the prompt asked for an open loop in the hook and a mid-video reminder; the model wrote neither.
    Check for them and, when missing, make ONE small LLM call that inserts exactly one sentence in each place.
    Accepted only if the original sentences survive (sequence ratio), so nothing else in the hook changes."""
    import difflib
    secs = d.get("sections") or []
    body = [s for s in secs if s.get("id") not in ("hook", "close")]
    if len(body) < 3 or secs[0].get("id") != "hook":
        return
    hook, payoff, mid = secs[0], body[-1], body[len(body) // 2]
    need_hook = not _LOOP_RE.search(hook["narration"])
    need_mid = not _REMIND_RE.search(mid["narration"])
    if not (need_hook or need_mid):
        return
    user = (f"HOOK (current):\n{hook['narration']}\n\nPAYOFF SECTION — heading \"{payoff.get('heading')}\" (it is the LAST part "
            f"before the sign-off, {len(body)} parts in):\n{payoff['narration'][:600]}\n\nMIDDLE SECTION (current opening):\n"
            f"{mid['narration'][:300]}\n\nReturn JSON {{\"hook\": \"...\", \"reminder\": \"...\"}}.\n"
            f"- hook: the HOOK text unchanged, plus ONE new sentence (<= 22 words) inserted right before its final roadmap "
            f"sentence(s), promising the payoff and saying where it is, e.g. \"And at the end, the one rule that tells you where "
            f"your first $100 goes.\" / \"Stick around for number {len(body)} — it is the one almost everyone gets backwards.\" "
            f"Copy every other word exactly.\n"
            f"- reminder: ONE sentence (<= 16 words) to open the middle section, telling the viewer the promised payoff is still "
            f"ahead, e.g. \"Halfway there — the rule I promised comes right after these.\" Plain spoken English, no markdown.")
    try:
        out = ask_json(cfg, "You edit a spoken finance script for retention. Return ONLY the JSON asked for.", user, temperature=0.4)
    except Exception as e:  # noqa: BLE001 - a missing tease is not worth failing the run
        print(f"[warn] open-loop pass failed ({str(e)[:100]})")
        return
    done = []
    new_hook = " ".join(str(out.get("hook") or "").split())
    if need_hook and new_hook and _LOOP_RE.search(new_hook):
        ratio = difflib.SequenceMatcher(None, hook["narration"].split(), new_hook.split(), autojunk=False).ratio()
        if ratio >= 0.7 and len(new_hook.split()) <= len(hook["narration"].split()) + 30:
            hook["narration"] = _soften(new_hook)
            done.append("hook open loop")
    rem = " ".join(str(out.get("reminder") or "").split())
    if need_mid and 4 <= len(rem.split()) <= 22 and not _CUE_RE.search(rem):
        rem = rem.rstrip() if rem.rstrip()[-1:] in ".!?" else rem.rstrip() + "."
        mid["narration"] = _soften(rem + " " + mid["narration"])
        done.append(f"mid-video reminder ({mid.get('id')})")
    if done:
        print(f"      open loop added: {', '.join(done)}")
    elif need_hook:
        print("[warn] open loop: hook rewrite rejected (changed too much); keeping the original hook")


# Metaphors that read badly on a money channel and trip YouTube's wellbeing classifiers (run 20 Short: "financial suicide").
_SOFTEN = [(re.compile(r"\bfinancial suicide\b", re.I), "a financial disaster"),
           (re.compile(r"\bcommit(s|ting|ted)? suicide\b", re.I),
            lambda m: {"": "self-destruct", "s": "self-destructs", "ting": "self-destructing", "ted": "self-destructed"}[(m.group(1) or "").lower()]),
           (re.compile(r"\bsuicid\w*\b", re.I), "ruinous"),
           (re.compile(r"\bkill(s|ing|ed)? your (savings|portfolio|returns|wealth)\b", re.I),
            lambda m: {"": "wipe out", "s": "wipes out", "ing": "wiping out", "ed": "wiped out"}[(m.group(1) or "").lower()] + " your " + m.group(2)),
           (re.compile(r"\bblow your brains out\b", re.I), "lose your head")]


def _soften(text: str) -> str:
    for rx, rep in _SOFTEN:
        text = rx.sub(rep, text)
    return text


# ------------------------------------------------------------------ delivery cues (Fish Audio S2 bracket syntax)

_CUE_RE = re.compile(r"\[[^\]]{1,60}\]|\((?:break|long-break|breath)\)")

DELIVERY_SYSTEM = """You are a voice director marking up a finance explainer for the Fish Audio S2 text-to-speech model.
You add DELIVERY CUES in square brackets to the narration. The cues shape how a line is spoken; the words never change.
S2 reads bracket cues as natural-language stage directions, so be PHYSICAL and SPECIFIC — describe pace, volume, breath
and attitude, not just a mood word. Good cues:
  [leaning in, quieter and slower]   [picking up pace, energised]   [flat and matter-of-fact, then a beat of silence]
  [genuinely surprised, eyebrows up]  [dry, half-smiling]   [slow and heavy on every word]   [warm, reassuring]
  [almost whispering the number]     [brisk, like listing items]   [sceptical, drawn out]   [building, louder]
Weak cues (avoid): [calm] [confident] [neutral] [serious] — a flat reference voice ignores them.
Position: a cue goes at the START of the sentence it colours. [emphasis] goes IMMEDIATELY BEFORE the word or number to
stress. (break) is a short pause, [long-break] a longer one; put them right BEFORE a reveal, after a question, or after a
short punch sentence.
Density — {density_rules}
Always:
- Keep each cue SHORT (2-5 words). Never stack two cues on one sentence: over-direction makes the voice re-plan its
  prosody every sentence and the joins sound robotic.
- Every section opens with a cue that changes the energy from the previous section.
- (break) only right before a sentence that delivers a surprising number; [long-break] at most once per section.
- The sign-off sentence gets [warm, unhurried].
- Never use laughing/sighing/crying/shouting/screaming effects. No [narrator]. No cues inside a number or between a
  currency sign and its digits.
- Do NOT alter, reorder, add or remove any word or punctuation. Output must equal the input once cues are stripped.
Return ONLY JSON: {"sections": [{"id": "...", "spoken": "..."}], "shorts": [{"index": 0, "spoken": "..."}]}"""

_DENSITY = {
    "low": ("a cue on roughly 1 sentence in 4; [emphasis] on ONE number per section; let the voice carry the rest "
            "(use this when the reference voice is already expressive)."),
    "medium": ("a cue on roughly 1 sentence in 2-3, alternating energy (fast/slow, loud/quiet, warm/dry) so the read "
               "never settles into one gear; [emphasis] on the 1-2 numbers that matter most in each section."),
    "high": ("a cue on roughly every second sentence, alternating energy; [emphasis] before every key number. "
             "Use only for a very flat reference voice — this setting can sound over-directed."),
}


def _clean(text: str) -> str:
    return " ".join(_CUE_RE.sub(" ", str(text)).split())


# Sentences per cue the density setting means in practice. The prompt asks for this; the model ignores it (run 20:
# 156 cues on 14 parts at "medium" — more than one per sentence, the over-directed sound the user called robotic),
# so the budget is enforced here.
_SENT_PER_CUE = {"low": 4.0, "medium": 2.5, "high": 1.6}


def _thin_cues(spoken: str, density: str) -> str:
    """Cap the cues in one part to the density budget. Keeps, in priority order: the opening cue (sets the section's
    energy), up to two [emphasis], up to two pauses, then mood cues spread evenly through the part. Removing cues
    never changes the words, so the narration == stripped-spoken invariant holds."""
    plain = _clean(spoken)
    n_sent = max(1, len(re.findall(r"[.!?](?:\s|$)", plain)))
    budget = max(3, round(n_sent / _SENT_PER_CUE.get(density, 2.5)))
    cues = list(_CUE_RE.finditer(spoken))
    if len(cues) <= budget:
        return spoken
    low = [m.group(0).lower() for m in cues]
    keep = {0}
    keep.update([i for i, c in enumerate(low) if c.startswith("[emphasis")][:2])
    keep.update([i for i, c in enumerate(low) if c in ("(break)", "(long-break)", "(breath)", "[long-break]")][:2])
    rest = [i for i in range(len(cues)) if i not in keep]
    slots = budget - len(keep)
    if slots > 0 and rest:
        step = len(rest) / slots
        keep.update(rest[int(j * step)] for j in range(slots))
    out, last = [], 0
    for i, m in enumerate(cues):
        out.append(spoken[last:m.start()])
        out.append(m.group(0) if i in keep else " ")   # a space, so two words never fuse when a cue between them goes
        last = m.end()
    out.append(spoken[last:])
    return " ".join("".join(out).split())


def annotate_delivery(cfg: dict, script: dict) -> int:
    """One LLM call: add Fish delivery cues to every section and Short. Writes `spoken` next to `narration`.
    A section is accepted only if stripping the cues gives back the original narration exactly — so captions,
    beat alignment and on-screen text (which all use `narration`) can never drift. Returns how many were accepted."""
    secs = script.get("sections", [])
    shorts = script.get("shorts", [])
    user = ("Mark up these narrations.\n\nSECTIONS:\n" +
            "\n\n".join(f"[id={s['id']}]\n{s['narration']}" for s in secs) +
            "\n\nSHORTS:\n" + "\n\n".join(f"[index={i}]\n{sh['narration']}" for i, sh in enumerate(shorts)))
    density = str(cfg.get("voice", {}).get("cue_density", "medium")).lower()
    system = DELIVERY_SYSTEM.replace("{density_rules}", _DENSITY.get(density, _DENSITY["medium"]))
    try:
        data = ask_json(cfg, system, user, temperature=0.4)
    except Exception as e:  # noqa: BLE001 - cues are a bonus
        print(f"[warn] delivery pass failed ({str(e)[:100]}); narration will be read without cues")
        return 0
    accepted = 0
    by_id = {str(x.get("id")): str(x.get("spoken") or "") for x in data.get("sections", []) if isinstance(x, dict)}
    for s in secs:
        sp = by_id.get(str(s["id"]), "")
        if sp and _clean(sp) == " ".join(s["narration"].split()) and _CUE_RE.search(sp):
            s["spoken"] = _thin_cues(" ".join(sp.split()), density)
            accepted += 1
    by_ix = {int(x.get("index", -1)): str(x.get("spoken") or "") for x in data.get("shorts", []) if isinstance(x, dict)}
    for i, sh in enumerate(shorts):
        sp = by_ix.get(i, "")
        if sp and _clean(sp) == " ".join(sh["narration"].split()) and _CUE_RE.search(sp):
            sh["spoken"] = _thin_cues(" ".join(sp.split()), density)
            accepted += 1
    total = len(secs) + len(shorts)
    cues = sum(len(_CUE_RE.findall(x.get("spoken", ""))) for x in secs + shorts)
    raw = sum(len(_CUE_RE.findall(str(x.get("spoken") or ""))) for x in (data.get("sections") or []) + (data.get("shorts") or []) if isinstance(x, dict))
    print(f"      delivery cues ({density}): {accepted}/{total} parts annotated, {cues} cues kept of {raw} proposed" +
          ("" if accepted == total else " (rejected parts had altered words; they will be read plain)"))
    return accepted


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
