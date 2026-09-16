# yt-autopilot — a zero-touch, zero-cost faceless YouTube channel

Every Monday, GitHub Actions (free) picks an un-used topic, writes a researched script with a
worked numeric example, voices it with a free neural voice, renders a captioned 6–10 minute video
plus three vertical Shorts, generates a thumbnail, uploads everything to YouTube and schedules the
Shorts across the week. Every Sunday it reads your analytics, learns which topics/formats your
audience rewards, and refills the topic bank accordingly. You do nothing after setup.

**Running cost: $0.** LLM (Gemini free tier), voice (edge-tts), b-roll (Pexels free), compute
(GitHub Actions free 2,000 min/month; a weekly run uses ~25–40 min), hosting (YouTube).

---

## One-time setup (~25 minutes)

You need a Google account for the channel and a GitHub account. Do these steps yourself — they
involve signing in and pasting secrets, which nobody else (including an AI) should do for you.

### 1. Create the YouTube channel (3 min)
1. youtube.com → profile → *Create a channel*. Name it (default config uses **Money Mechanics**; change `channel.name` in `config.yaml` if you pick something else).
2. YouTube Studio → Settings → Channel → *Feature eligibility* → verify your phone number (needed for custom thumbnails and videos over 15 min).

### 2. Get a free LLM key (2 min)
- Go to **aistudio.google.com** → *Get API key* → create. Copy it. (No card required.)
- Optional backup: **console.groq.com** → API key (also free).

### 3. Get a free Pexels key (1 min, optional but recommended)
- **pexels.com/api** → *Get started* → copy key. Without it the videos use clean generated slides instead of stock footage; both are fine.

### 4. Create the YouTube API credentials (8 min)
1. **console.cloud.google.com** → new project (any name).
2. *APIs & Services → Library*: enable **YouTube Data API v3** and **YouTube Analytics API**.
3. *OAuth consent screen*: External → fill app name + your email → add scopes `youtube.upload`, `youtube`, `yt-analytics.readonly` → add your Gmail as a test user → save.
4. *Credentials → Create credentials → OAuth client ID → Desktop app* → **Download JSON**, save as `client_secret.json` in this folder.
5. Back on the OAuth consent screen click **Publish app** (otherwise the refresh token dies after 7 days).

### 5. Authorise once on your PC (3 min)
```powershell
cd yt-autopilot
pip install -r requirements.txt
python setup_auth.py client_secret.json
```
A browser opens; sign in with the channel's Google account, allow access. The terminal prints
`YT_CLIENT_ID`, `YT_CLIENT_SECRET`, `YT_REFRESH_TOKEN`. Keep them for step 7. Then delete `client_secret.json`.

### 6. Local smoke test (5 min, needs ffmpeg)
```powershell
winget install Gyan.FFmpeg      # then reopen the terminal
python selftest.py              # renders a 40 s sample in build/selftest/ — no keys needed
```
Watch `build/selftest/long.mp4`. Like the voice? Browse alternatives with
`python -m edge_tts --list-voices | findstr en-` and set `voice.name` in `config.yaml`.

Full dry run with a real AI script (uses your Gemini key, uploads nothing):
```powershell
$env:GEMINI_API_KEY="..."; $env:PEXELS_API_KEY="..."
python run_produce.py --dry-run --keep-build
```

### 7. Put it on autopilot (3 min)
1. Create a **private** GitHub repo, push this folder to it.
2. Repo → *Settings → Secrets and variables → Actions → New repository secret* for each of:
   `GEMINI_API_KEY`, `PEXELS_API_KEY`, `YT_CLIENT_ID`, `YT_CLIENT_SECRET`, `YT_REFRESH_TOKEN`
   (optionally `GROQ_API_KEY` / `ANTHROPIC_API_KEY` as fallbacks).
3. Repo → *Actions* → enable workflows → run **Produce & schedule weekly videos** manually once with `dry_run = true`. Download the artifact and check it.
4. Run it once more with `dry_run = false`. Your first video is live-scheduled. From now on it runs every Monday by itself; the analytics review runs every Sunday and shows up under *Actions → job summary*.

That's it. Your ongoing effort is optional: glance at the Sunday report, occasionally reply to comments.

---

## What happens each week

```
Sunday 05:00 UTC   weekly_review.yml
  analytics ──► score categories/formats ──► refill topic bank (LLM) ──► reports/latest.md

Monday 06:00 UTC   produce.yml
  pick topic (never repeats, rotates 7 formats, weighted by scores)
  ──► script JSON (title, 7 sections, chart data, 3 Shorts, description, tags, thumbnail text)
  ──► edge-tts per section (word timings)         ──► captions.srt
  ──► visuals per section: stat card | chart | Pexels b-roll | branded slide
  ──► ffmpeg: clips → concat → voice → burned captions → long.mp4 (1080p)
  ──► 3 × short.mp4 (1080×1920, big captions)   ──► thumbnail.jpg
  ──► upload long (publishes 2 PM ET Mon) + Shorts (Tue/Thu/Sat noon)
  ──► commit data/published.json
```

## Files you might touch
| File | Why |
|---|---|
| `config.yaml` | channel name, voice, cadence hours, colours, video length |
| `data/topics.json` | add your own topic ideas; the bank auto-refills weekly |
| `.github/workflows/produce.yml` | change the cron to publish 2–3×/week (see STRATEGY.md) |

## Commands
```
python selftest.py                       # offline render test
python run_produce.py --dry-run          # full pipeline, no upload
python run_produce.py                    # produce + upload
python run_produce.py --topic "..."      # force a topic
python run_weekly_review.py              # analytics + refill (needs YT secrets)
python run_weekly_review.py --refill     # refill topics only
```

## Troubleshooting
- **ffmpeg not found** → install (`winget install Gyan.FFmpeg`) and reopen the terminal.
- **edge-tts 403 / no audio** → transient Microsoft block; the code retries. If it persists on GitHub runners, re-run the job.
- **Upload 401 / invalid_grant** → refresh token expired because the OAuth app is still in *Testing*. Publish the app and re-run `setup_auth.py`.
- **Thumbnail "forbidden"** → verify the channel's phone number in YouTube Studio.
- **LLM 429** → Gemini free quota hit; add `GROQ_API_KEY` as fallback or set `llm.provider: groq`.
- **Topic bank exhausted** → `python run_weekly_review.py --refill`.

## Policy compliance built in
- `containsSyntheticMedia: true` on every upload (YouTube's AI-disclosure requirement).
- Every video has a unique worked example, an on-screen data chart, rotating format, consistent brand voice/sign-off — the opposite of the "mass-produced, repetitive" pattern the inauthentic-content policy targets.
- Disclaimer in every description; category Education; not made for kids.
