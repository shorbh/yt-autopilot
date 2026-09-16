# Strategy: how this channel gets to money, and how fast

## The one date that matters: 1 February 2027
YouTube doubles the Partner Program thresholds for *new applicants* on that date
(4,000 → 8,000 watch hours, or 10M → 20M Shorts views). You have ~19 weeks from today
(17 Sep 2026). Getting accepted before then halves the work forever. That is why the
default cadence below is more aggressive than "1 video a week".

## Why finance explainers
Highest ad RPM of any evergreen niche ($8–20 per 1,000 monetised views vs $1–3 for
entertainment), evergreen (a video about compound interest earns for years), and the
"worked example with real numbers" format is both what viewers reward and what YouTube's
inauthentic-content policy explicitly considers legitimate editorial value.

## Monetisation math (be sceptical of anyone who promises more)
Assumptions: 8-min videos, 45% average view duration ≈ 3.6 min watched per view.

| Milestone | Needs | Realistic timeline |
|---|---|---|
| 4,000 watch hours | ~67,000 long-form views | 3–6 months at 2–3 videos/week |
| 1,000 subscribers | ~1 sub per 60–100 views → 60–100k views | usually lands around the same time |
| First $100/month | ~12k monetised long views/month at $8 RPM | month 5–8 |
| $500–1,000/month | 60–120k views/month, or a 50+ video back-catalogue compounding | month 9–15 |

Shorts are a discovery engine, not a revenue engine ($0.05–0.10 RPM). They exist here to
push viewers to the long videos, which is why every Short ends with a pointer to the channel.

## Recommended cadence changes (one-line edits)
- **Weeks 1–8 (before Feb 2027):** 3 long videos/week. In `produce.yml` change the cron to
  `"0 6 * * 1,3,5"`. Cost is still $0; runner time ≈ 90 min/week of the 2,000 free minutes.
  Gemini free tier handles ~4 LLM calls per video easily.
- **After acceptance into YPP:** drop to 2/week and let the analytics loop optimise topics.

## Levers that actually move the numbers (in order)
1. **Click-through rate (title + thumbnail).** The script generator produces 3 titles and a
   2–4 word thumbnail text. Once monetised, YouTube's built-in *Test & Compare* lets you A/B
   thumbnails — turn it on in Studio for every upload; zero effort.
2. **Retention in the first 30 seconds.** The "hook" section states the surprising number
   first. Watch the Sunday report's *Avg % watched*; anything under 35% means the format for
   that category is off — the weighting will down-rank it automatically.
3. **Back-catalogue.** Evergreen finance videos keep getting searched. 50 videos in the
   library is the tipping point most faceless finance channels report.
4. **Playlists / end screens.** Once you have 8+ videos, spend 10 minutes in Studio making
   one playlist per category (the category names in `topics.json`) — session watch time
   is the metric the algorithm rewards most.

## The 10-minutes-a-week you *should* still spend
Reply to comments the first day a video is live (algorithm signal + builds "identifiable
human creator" evidence for policy reviews), and skim `reports/latest.md`. Nothing else.

## Risks and how the system handles them
| Risk | Mitigation |
|---|---|
| YouTube "inauthentic content" flag | unique worked example every video, 7 rotating formats, on-screen chart, consistent brand, synthetic-media disclosure, Education category |
| Free LLM quota changes | provider auto-fallback: Gemini → Groq → Anthropic |
| edge-tts blocked from GitHub IPs | retries with backoff; fallback is to run `run_produce.py` from your PC via Task Scheduler |
| OAuth token expiry | publish the OAuth app (README step 4.5) |
| Topic bank runs dry | weekly LLM refill weighted toward winning categories |

## Where Claude fits from here (why this pays for itself)
The pipeline runs without Claude. Where a Claude subscription keeps earning its fee:
- Paste `reports/latest.md` into a chat every few weeks: "what should I change?" — it can
  rewrite the format list, retune the topic bank, or adjust the script prompt in `script.py`.
- "Add a second channel in Hindi" is a `config.yaml` copy plus a new topic bank — a
  20-minute job with Claude, days by hand.
- When YouTube changes policy or a free API changes shape (they will), Claude can patch
  the exact module in minutes rather than you re-learning the codebase.
- Optional upgrades it can build on request: royalty-free music bed, Anthropic-API
  scripts for higher quality, community-post automation, affiliate links in descriptions
  once you have an audience (the second revenue line, often larger than ads in finance).
