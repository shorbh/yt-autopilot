# yt-autopilot — Build Playbook & Decision Log

> **Purpose.** A complete, honest record of what was built, every decision and *why*, every
> mistake and how it was fixed, and a repeatable checklist for launching another channel from
> this codebase. This is the single document to update whenever the system changes.
>
> **Living document.** Add an entry to §11 *Change log* for every change. If a step in
> §8 *Launch checklist* turns out to be wrong or outdated, fix it there too.

---

## 0. Quick facts (current state — 18 Sep 2026)

| Item | Value |
|---|---|
| Channel | **Money Mechanics** — YouTube channel ID `UCTfIJ4MK2kj57qheFd0Y9vg` (brand channel under saurabhsinghal998@gmail.com; formerly the unused "Calmlance" brand channel) |
| Handle | still `@calmlance3783` — custom handle refused by YouTube pre-verification; retry now that phone is verified (§10 Open items) |
| Niche / audience | Personal-finance explainers, English, global/US audience |
| Cadence | **2 long (7–12 min, adaptive) + 6 Shorts per week** since v3.3; Mon + Thu 06:00 UTC production, long publishes 14:00 ET same day, Shorts on the 3 following days 12:00 ET |
| Code | github.com/shorbh/yt-autopilot (public) — `main` branch |
| Compute | GitHub Actions free tier (ubuntu-latest). A full run ≈ 15 min → ~60 min/month of the 2,000 free |
| Google Cloud project | `yt-autopilot` (ID `yt-autopilot-508819`) — YouTube Data API v3 + YouTube Analytics API enabled |
| OAuth app | External, **In production** (published 17 Sep 2026); Desktop client `yt-autopilot-desktop` |
| Public pages | https://shorbh.github.io/yt-autopilot/ (homepage) and `/privacy.html` — served from `docs/` via GitHub Pages |
| LLM | Gemini `gemini-3.5-flash-lite` (free tier) → Groq → Anthropic fallback chain |
| Voice | edge-tts `en-US-AndrewMultilingualNeural`, rate +4% |
| Stock footage | Pexels API (free) with generated-slide fallback |
| Secrets (GitHub → Settings → Secrets → Actions) | `GEMINI_API_KEY`, `PEXELS_API_KEY`, `YT_CLIENT_ID`, `YT_CLIENT_SECRET`, `YT_REFRESH_TOKEN` |
| Running cost | **$0** |
| Status | Dry-run #3 succeeded end-to-end (14m 51s). Not yet uploaded a real video. Monday cron is armed. |

---

## 1. The brief and how it was interpreted

**Original ask:** "Build the fastest profitable YouTube channel; minimal or no management effort; everything free or near-free."

**How that was translated into design constraints:**

1. **Zero-touch after setup.** Everything that recurs must run on a schedule without a human — including topic selection, scripting, voice, editing, thumbnails, upload *and* the editorial feedback loop. A "human in the loop each week" would violate the brief.
2. **$0 running cost.** Every component had to have a genuinely free tier that survives a weekly cadence: Gemini free API, Microsoft Edge neural TTS via `edge-tts`, Pexels API, GitHub Actions free minutes, GitHub Pages, YouTube itself.
3. **Profitability is bounded by YouTube's rules, not by our effort.** So the design is optimised for the actual gating metric — **4,000 public watch hours + 1,000 subscribers** (or 10M Shorts views) — and for *not getting demonetised*.
4. **Honesty over hype.** "Fastest profitable" cannot be guaranteed. The document STRATEGY.md gives realistic math: first $100/month around month 5–8; $500–1,000/month around month 9–15 if it compounds.

**Key external facts that shaped the design (verified 17 Sep 2026):**

- YouTube Partner Program threshold today: 1,000 subs + 4,000 watch hours (12 mo) *or* 10M Shorts views (90 d). **From 1 Feb 2027 these double for new applicants** (8,000 h / 20M). ⇒ urgency: aim to be accepted before Feb 2027; STRATEGY.md recommends a temporary 3-videos-per-week cadence.
- YouTube's **"inauthentic content" policy** (July 2025) demonetises mass-produced, repetitive AI content. What is *allowed*: AI-drafted/voiced content with real editorial value — unique research, worked examples, varied formats, an identifiable creator voice — plus the **"altered or synthetic content" disclosure**. Every part of the script generator is built around this.
- Finance explainers have the highest ad RPM of any evergreen niche ($8–20 per 1,000 monetised views vs $1–3 for entertainment) and are evergreen (a compound-interest video earns for years).

---

## 2. Decisions taken (with alternatives rejected)

| Decision | Chosen | Alternatives considered | Why |
|---|---|---|---|
| Niche | Finance/money explainers, English global | Tech/AI tutorials; history/mystery; Shorts-only facts | Highest RPM, evergreen, and the "worked example with numbers" format is both what viewers reward and what YouTube's policy explicitly considers legitimate. |
| Format mix | 1 long + 3 Shorts / week | 3 long/week; daily Shorts | Long form builds watch hours (the gating metric); Shorts are a discovery feeder (RPM ≈ $0.05–0.10, so they never carry revenue). Recommend 3 long/week until Feb 2027. |
| Runtime | GitHub Actions cron | Windows Task Scheduler on the PC | Runs with the PC off; free 2,000 min/month; secrets storage built in; logs + artifacts for debugging. |
| LLM | Gemini free tier, pluggable fallbacks | Anthropic API (paid), Groq only | Free; only ~4 LLM calls per video so even the 20/day cap on Flash models is fine. Provider is auto-selected by whichever key exists so a quota change is a one-secret fix. |
| Voice | `edge-tts` (Microsoft neural) | ElevenLabs, OpenAI TTS, gTTS | Only high-quality option that is truly free without limits; returns **word-level timings**, which power the karaoke-style captions. Known risk: Microsoft occasionally blocks datacenter IPs → retries with backoff. |
| Visuals | Pexels b-roll + generated stat cards + PIL-drawn chart + branded slides | Stable Diffusion images, stock video subscriptions | Free; the generated chart/stat cards give every video *original* visuals (policy-relevant) and never fail; Pexels adds motion when available and silently falls back when not. |
| Rendering | Raw `ffmpeg` via subprocess | MoviePy | Faster, fewer dependencies, no Python-side frame handling; captions burned with libass `subtitles=` filter. |
| Captions | Burned in, 3-word chunks, from TTS word boundaries | YouTube auto-captions | Burned captions raise retention on mobile/muted playback and are a visible "editing" signal. |
| Upload scheduling | Upload as `private` + `publishAt` | Publish immediately; separate runs per day | One run per week renders everything; YouTube itself releases the long video Monday 14:00 ET and Shorts Tue/Thu/Sat. |
| Feedback loop | Weekly YouTube Analytics pull → score categories/formats → weight topic picker → LLM refills topic bank | None; manual review | This is the "human-like editorial judgment" layer that makes the channel improve without a human. |
| Repo visibility | **Public** | Private | Needed free GitHub Pages to host the homepage + privacy policy Google requires before an OAuth app can be *published* (see §5). Code contains no secrets. |
| Channel | Reuse empty brand channel "Calmlance" → renamed **Money Mechanics** | Rename personal channel; create a new brand channel | Keeps the personal channel untouched (user's wish); creating a *new* channel now requires YouTube identity verification, whereas the existing empty brand channel needed none. |
| OAuth publishing status | **In production** | Leave in Testing | In Testing, refresh tokens expire every 7 days → weekly manual re-auth, violating "zero-touch". |

---

## 3. Architecture

```
GitHub Actions (cron)
│
├─ Sunday 05:00 UTC  weekly_review.yml  →  run_weekly_review.py
│     YouTube Analytics API ──► score categories/formats ──► data/performance.json
│     LLM refills data/topics.json (more topics for winning categories)
│     reports/latest.md (also shown in the Actions job summary) → git commit
│
└─ Monday 06:00 UTC  produce.yml  →  run_produce.py
      1. pipeline/trends.py    Google News + Google Trends RSS (free) → LLM "fit" score; a trend topic is used
                               only when fit ≥ 7 AND it can be told as an evergreen mechanism
         pipeline/topics.py    otherwise: pick unused bank topic (weighted by performance); rotate 7 formats
      2. pipeline/script.py    LLM → strict JSON: title, 7 sections, chart data, 3 Shorts,
                               description, tags, thumbnail text   (pipeline/llm.py: provider chain)
                               (close = action rule + GENERIC tease that names no topic + sign-off)
      3. pipeline/tts.py       edge-tts per section → mp3 + word timings; concat + loudnorm
         pipeline/storyboard.py LLM splits each section into beats (1-2 sentences) + a visual spec
         pipeline/motion.py    animated PIL cards: bignumber | compare | list | formula | callout | icon_text |
                               photo_text | character (real portrait photo) | timeline | chart | chapter | outro
                               (text always fitted; nothing drawn above TOP_SAFE = 16% — the lower-third strip)
         pipeline/assets_remote.py  Lucide icons (jsDelivr + cairosvg); person_query() gender+mood → Pexels search
         pipeline/visuals.py   chart drawing (nice ticks), Pexels b-roll/photos, lower-third, b-roll phrase overlay
         pipeline/render.py    beat timing from word boundaries → one clip per beat (fade from navy) → concat
                               → voice (+ music bed) → karaoke ASS captions → long.mp4
                               3 × short.mp4 (1080×1920) + thumbnail A/B/C (photo + fitted type) + contact_sheet.jpg
      4. pipeline/upload.py    YouTube Data API v3 resumable upload, private + publishAt,
                               containsSyntheticMedia=true, thumbnail set
      5. pipeline/state.py     data/published.json → git commit (so topics never repeat)
```

**Files you will actually touch**

| File | Purpose |
|---|---|
| `config.yaml` | channel name/**id**/tagline/niche/sign-off, voice, video length, cadence hours, colours, LLM models, `topics.trends` on/off + news queries + `trend_min_fit` |
| `data/topics.json` | 7 categories × ~7 seed topics + 7 rotating formats; auto-refilled weekly |
| `.github/workflows/produce.yml` | cron for production (change to `0 6 * * 1,3,5` for 3×/week) |
| `.github/workflows/weekly_review.yml` | cron for analytics review |
| `docs/index.html`, `docs/privacy.html` | public pages Google requires for a published OAuth app |
| `selftest.py` | offline render test (no API keys) |
| `setup_auth.py` | one-time OAuth flow → prints the three `YT_*` secrets |
| `STRATEGY.md` | monetisation math, cadence advice, levers |
| `README.md` | generic setup guide (kept in sync with this playbook) |

**Policy-compliance features baked into the code (do not remove):**

- `status.containsSyntheticMedia = true` on every upload (AI-disclosure requirement).
- Every script must contain a fully worked numeric example, an on-screen chart, and a stat card; formats rotate so the last 3 videos never share a format; a consistent brand sign-off closes every video.
- Disclaimer ("education only, not financial advice") appended to every description; category 27 Education; not made-for-kids.
- Topic never repeats (`data/published.json` is committed by the workflow).

---

## 4. Timeline — what was done, in order

### Phase A — Design & code (Cowork session, 17 Sep 2026)

1. Web research to verify current YPP thresholds, the Feb-2027 doubling, the inauthentic-content policy, Gemini free-tier limits, and that `edge-tts` still works.
2. Wrote the full pipeline (12 Python modules, 2 workflows, config, topic bank, docs).
3. **Local sandbox was unavailable** on the machine (VM failed to start), so the code could not be executed before delivery. Mitigation: a second-opinion static review pass was run, which fixed ~10 concrete bugs (Actions input injection, `loudnorm` sample rate, Pillow `load_default(size)` compatibility, chart data coercion, tag-length limit, `RefreshError` handling, Windows `tzdata`). `selftest.py` was added so the render path can be verified locally in 2 minutes.

### Phase B — Cloud & accounts setup (Claude in Chrome, 17 Sep)

Done by the assistant in the user's Chrome unless marked **(user)**.

4. **Google Cloud**: project `yt-autopilot` created (ID `yt-autopilot-508819`).
5. Enabled **YouTube Data API v3** and **YouTube Analytics API**. *(Gotcha: the console's "Enable" button silently moved when a promo banner appeared; verify on the API dashboard that both are listed.)*
6. **Google Auth Platform**: "Get started" → app name `yt-autopilot`, support email, **External** audience, contact email, accepted the Google API Services User Data Policy **(user approved)**.
7. **Data access (scopes)**: pasted manually — `youtube.upload`, `youtube`, `yt-analytics.readonly` → Update → Save.
8. **Audience → Test users**: added `saurabhsinghal998@gmail.com`. *(Gotcha: the first Save was lost when the Chrome extension disconnected; the user then hit "Error 403: access_denied — app not completed verification" on sign-in. Fix: re-add the test user and confirm the table shows 1 user.)*
9. **Clients**: created OAuth client, type **Desktop app**, name `yt-autopilot-desktop`. The client-secret dialog appears once; **(user)** downloaded `client_secret.json`. The assistant deliberately stops here — it does not read, copy, or paste secrets.
10. **(user)** `pip install -r requirements.txt` then `python setup_auth.py client_secret.json` → signed in → got `YT_CLIENT_ID`, `YT_CLIENT_SECRET`, `YT_REFRESH_TOKEN`.
11. **GitHub**: created **public** repo `shorbh/yt-autopilot`; **(user)** pushed the code with git.
12. Added `docs/index.html` + `docs/privacy.html`; enabled **GitHub Pages** from `main` / `docs`.
13. **Branding** in Google Auth Platform: homepage `https://shorbh.github.io/yt-autopilot/`, privacy `…/privacy.html`, authorised domain `shorbh.github.io` → Save. *(Why: the "Publish app" button is disabled until these exist.)*
14. **Audience → Publish app → Confirm** **(user approved earlier)**. Status is now **In production**. The "requires verification" banner is cosmetic for a single-user app; only the 100-user cap applies.
15. **(user)** Re-ran `setup_auth.py` **after** publishing and **choosing the Money Mechanics channel** at the account chooser, so the refresh token is (a) tied to the right channel and (b) not subject to the 7-day Testing-mode expiry. Deleted `client_secret.json`.

### Phase C — YouTube channel (17 Sep)

16. Initially started renaming the personal channel "Saurabh Singhal" → user asked to keep it intact → **all edits cancelled, nothing saved**.
17. "Create a channel" now demands YouTube identity verification, so instead the existing empty brand channel **Calmlance** (0 videos, 0 subs) was **renamed to Money Mechanics** with the channel description below. Handle change was refused ("not available" for every variant, only suffixed ones offered) — left unchanged.
18. **(user)** Phone-verified the channel (needed for custom thumbnails, >15-min uploads, likely handles).

Channel description in use:
> How money actually works, explained in plain English.
> Every week: one clear explainer on investing, debt, saving, or the psychology of money — always with a worked example you can reproduce. No hype, no hot tips, just the mechanics.
> Videos use an AI narrator and are for education only; nothing here is financial advice.

### Phase D — First runs & fixes (17–18 Sep)

19. **(user)** added the 5 repository secrets. Assistant verified the names on the Secrets page (values never shown).
20. **Run #1 (dry run) — failed in 1m 22s.** Gemini returned 404: `gemini-2.5-flash-lite` retired for new users. **Fix:** `config.yaml → llm.gemini_model: "gemini-3.5-flash-lite"` (edited directly on GitHub).
21. **Run #2 — failed in 7m 29s.** Script (936 words), TTS, all clips, concat and audio mix all worked; the final ffmpeg pass failed with `Unable to open captions.srt`. **Fixes** (commit `f604f24`):
    - `render.py`: if the caption burn-in fails, log a warning and render without captions instead of aborting the run; if TTS returns no word timings, estimate them from the narration so the SRT is never empty; print caption stats.
    - `tts.py`: request `boundary="WordBoundary"` explicitly (edge-tts ≥ 7) with a fallback for older versions.
    - `produce.yml`: `PYTHONUNBUFFERED=1` so progress lines stream live; upload the `build/` artifact even on failure (`if: always() && inputs.dry_run`) for debugging.
22. **Run #3 (dry run) — SUCCESS, 14m 51s.** Topic *"RSUs and Stock Options: The Real Math Behind Tech Wealth"*, 1,031 words, 6.1-min captioned 1080p video (1,030 timed words), thumbnail, 3 captioned Shorts. Artifact `rendered-video` (10 files) available on the run page for 3 days.

---

## 5. Things that were not obvious (gotchas) — read before the next channel

1. **Publishing the OAuth app now needs a homepage, privacy policy and authorised domain.** Free solution: GitHub Pages on a public repo. A private repo has no free Pages.
2. **Tokens minted while the app is in Testing expire in 7 days even after you publish.** Always re-run `setup_auth.py` *after* publishing.
3. **The refresh token is bound to whichever channel you pick in the Google account chooser.** For a brand channel, pick the brand channel, not the personal account.
4. **Test-user saves can be silently lost** if the browser hiccups. Confirm the table shows the user before trying to sign in.
5. **Gemini model names churn.** When a 404 says a model is "no longer available to new users", change `llm.gemini_model` — nothing else. Groq/Anthropic keys act as automatic fallbacks if present.
6. **Creating an additional YouTube channel now requires identity verification;** renaming an existing empty brand channel does not.
7. **Custom handles may be refused on a fresh/unverified channel** (only `name-xyz` suffixes offered). Phone-verify first, then retry.
8. **Google Cloud console buttons shift when promo banners appear** — click by finding the element, not by remembered position, and verify the result.
9. **edge-tts may be rate-limited from GitHub's datacenter IPs.** Retries are built in; if a run still fails at TTS, just re-run the workflow.
10. **Custom thumbnails need a phone-verified channel**, otherwise the API returns 403 and the code logs a warning and continues.
11. **Cartoon/illustration layers do not survive contact with real photo b-roll.** Two attempts (Pollinations AI sketches, then Open Peeps) both looked cheap next to Pexels footage. What works: real portrait photos for people, typographic initial avatars in compare cards, icons for concepts. Don't reintroduce a cartoon layer without an A/B on CTR.
12. **Every card renderer must respect `motion.TOP_SAFE`.** The section heading overlay occupies the top ~14% of the frame; any title drawn there collides (run #6: compare and chart titles). New renderers start content at `h * (TOP_SAFE + 0.01)`.
13. **`fade=t=in` fades from black by default.** On a navy card that reads as a dropout; always pass `color=0x0B1020` (brand bg).
15. **The thumbnail number must be the one people search for.** Ask the LLM for the title's headline figure or its consequence (7% → "+$200K"), never an intermediate ("2%"). A face with the right expression, red for cost, green-vs-red for comparisons — that is what YouTube's own AI thumbnails do, and they are the benchmark.
14. **Audit from the contact sheet, not the mp4.** `contact_sheet.jpg` (one frame per 6 s) is in every dry-run artifact; the whole video is reviewable in one image.

---

## 6. Where the assistant stopped and the user acted (by design)

These are hard boundaries, not laziness; they protect the account owner:

- Signing in to Google/GitHub/YouTube; passwords.
- Downloading `client_secret.json`; running `setup_auth.py`; reading or pasting any key/token/secret into GitHub Secrets.
- Clicking "Allow" on the OAuth consent screen.
- Phone / identity verification.
- Accepting terms (Google API user-data policy) and publishing the OAuth app — done only after explicit approval in chat.
- Deleting anything permanently (old videos would have been set to Private, not deleted).

---

## 7. Operating manual

**Normal operation:** nothing. Check `reports/latest.md` (or Actions → *Weekly analytics review* → job summary) on Sundays if curious. Reply to comments in the first 24 h of a video if you want the algorithm boost.

**The two-minute Studio checklist (Mon + Thu, after each run).** Two link surfaces have no API, so they are the only manual work:
1. Studio → Content → *Shorts* → open each of the run's 3 Shorts → **Related video** → pick the run's long video → Save. (This is the pinned button inside the Shorts player; it is where Short→long traffic comes from. Description links are hidden behind "…more".)
2. Studio → open the long video → Editor → **End screen** → add element *Video → Best for viewer* (or *Most recent upload*) + a *Subscribe* element → Save. The narrator already recommends the previous video by name in the close, so the card has something to land on.
Everything else — description Watch-next links, category playlists, the verbal bridge — is automatic.

**Manual production run:** Actions → *Produce & schedule weekly videos* → *Run workflow*. Tick *Render only* for a dry run (artifact appears on the run page). Optional *Force a topic*.

**Change cadence:** edit the `cron` in `produce.yml` (now `0 6 * * 1,4` = Mon/Thu; `0 6 * * 1,3,5` = Mon/Wed/Fri). Keep `publishing.shorts_offset_days` shorter than the gap between runs. Publish hours are in `config.yaml → publishing`.

**Video length:** the Sunday review adapts `target_minutes` from mid-video retention (≥45% → +1 min, <30% → −1 min) but never outside `video.min_minutes`…`video.max_minutes` (7–12). To pin the length, set min = max.

**Change voice:** `python -m edge_tts --list-voices | findstr en-` → set `voice.name`. Preview with `python selftest.py`.

**Add topics by hand:** append strings to a category in `data/topics.json`.

**Something failed:** open the run log. Order of likely causes: LLM model name/quota (§5.5) → edge-tts block (re-run) → YouTube 401 `invalid_grant` (token — re-run `setup_auth.py`, update the secret) → quota (10,000 units/day; a weekly run uses ~6,500).

**Local test without keys:** `python selftest.py` (needs ffmpeg: `winget install Gyan.FFmpeg`).

---

## 8. Launch checklist for a NEW channel from this codebase

Estimated 40 minutes; steps marked 🧑 must be done by the account owner.

**A. Decide** niche, language, channel name, cadence. Write the niche/audience/sign-off into a copy of `config.yaml`; write ~50 seed topics into `data/topics.json`; adjust the LLM `SYSTEM` prompt in `pipeline/script.py` if the niche is not finance.

**B. YouTube**
1. 🧑 Create the channel (or rename an empty brand channel). Set name + description. Phone-verify (Studio → Settings → Channel → Feature eligibility).
2. Set the handle (after verification).

**C. Google Cloud (one project per channel is cleanest; reusing this project also works — same OAuth client, different channel at sign-in)**
3. New project → enable YouTube Data API v3 + YouTube Analytics API.
4. Google Auth Platform → Get started → External → contact emails → accept policy.
5. Data access → paste the 3 scopes → Update → Save.
6. Audience → Add test user (owner's Gmail) → **confirm it shows in the table**.
7. Branding → homepage, privacy URL, authorised domain (from step D3) → Save.
8. Clients → Desktop app → 🧑 download `client_secret.json`.
9. Audience → Publish app → Confirm.

**D. GitHub**
1. New **public** repo; push the code.
2. Settings → Pages → branch `main`, folder `/docs` → Save. Note the URL.
3. Update `docs/index.html` / `privacy.html` links and channel name.
4. 🧑 Settings → Secrets → Actions: `GEMINI_API_KEY`, `PEXELS_API_KEY`, `YT_CLIENT_ID`, `YT_CLIENT_SECRET`, `YT_REFRESH_TOKEN` (optional `GROQ_API_KEY`).

**E. Keys** 🧑 aistudio.google.com → API key (pick the project). pexels.com/api → key.

**F. Auth** 🧑 `pip install -r requirements.txt` → `python setup_auth.py client_secret.json` → pick the **new channel** at the chooser → paste values into secrets → delete `client_secret.json`. Do this **after** step C9.

**G. Verify** Actions → Produce → Run workflow with *Render only* ticked → download artifact → watch. Then run without the tick (or wait for the cron).

**H. Record** Add the new channel to §0 and a line to §11.

---

## 9. Realistic expectations (from STRATEGY.md)

- Watch-hour math at 8-min videos and ~45% retention ≈ 3.6 min per view → **~67,000 long-form views for 4,000 hours**. At 2–3 videos/week that is typically 3–6 months.
- First $100/month ≈ month 5–8; $500–1,000/month ≈ month 9–15, driven by back-catalogue compounding (50+ evergreen videos is the usual tipping point).
- **1 Feb 2027 deadline:** thresholds double for new applicants. Recommendation: run `0 6 * * 1,3,5` (3 long/week) until accepted, then drop to 2/week.
- Shorts are discovery, not revenue. Affiliate links in descriptions become the second (often larger) income line once there is an audience — not yet implemented.

---

## 9b. What external research we adopted (and what we didn't)

Source: user's Gemini "Advanced Video Engineering" report (Sep 2026). Adopted: hook in first 3–7 s with a
visual pattern-interrupt (hook rule), no intro/logo, frequent cuts (≈6 s target for explainers; 2–3 s is for
talking-head), micro-hooks via chapter cards, karaoke captions for muted mobile viewing, don't summarise-and-fade
at the end (end screen stays visual + CTA), emotional face on thumbnails, QCR (thumbnail promise = content),
Test & Compare thumbnails once monetised, inauthentic-content & synthetic-media disclosure compliance.
Not adopted: Kokoro TTS / Whisper / ComfyUI / local LLaMA — they presume a local GPU box; our runner is a free
4-vCPU cloud VM, edge-tts already yields word timestamps, and unverified image generators produced poor output.
Several statistics in the report are unsourced; treat them as directional.

## 10. Open items / next steps

- [x] Run #7 dry run (v3.2) audited; run #8 went live 19 Sep (see change log).
- [x] v3.2.1 fixes + thumbnail rework (see change log) — push, then the Monday run uses them. A dry run first is optional; the changes are prompt + PIL only.
- [ ] Video 1 (`_arItix7pNQ`): swap the thumbnail in Studio to YouTube's AI suggestion #3 ("+$200K cash drain") — ours said "2%".
- [ ] Every thumbnail YouTube's AI suggests is a free benchmark: compare against ours in the artifact and steal what works.
- [ ] Watch the Monday run's `[trends]` line for a few weeks: if a news topic qualifies every single week, raise `trend_min_fit` to 8 so the evergreen bank still gets used.
- [ ] After the first Sunday review: read `reports/latest.md` and Studio → Audience → "When your viewers are on YouTube"; adjust `long_publish_hour` if the peak is elsewhere.
- [ ] Retry the custom handle (`@MoneyMechanics…`) now that the channel is phone-verified.
- [ ] Consider switching the cron to 3×/week before Feb 2027 (STRATEGY.md).
- [ ] Upload a channel banner + profile picture (PIL-generated assets could be added to the repo).
- [ ] After 8+ videos: create one playlist per topic category in Studio (session watch-time lever).
- [ ] Later: royalty-free music bed; affiliate-link block in descriptions; second channel (Hindi) via §8.

---

## 11. Change log

| Date | Change | Why |
|---|---|---|
| 2026-09-17 | Initial pipeline, workflows, docs written (Cowork session) | Project start |
| 2026-09-17 | Static review fixes: Actions input injection, loudnorm resample, Pillow compat, chart coercion, tag length cap, RefreshError handling, tzdata | Sandbox unavailable; pre-empt runtime bugs |
| 2026-09-17 | Google Cloud project, APIs, OAuth consent, scopes, test user, Desktop client | YouTube API access |
| 2026-09-17 | Repo `shorbh/yt-autopilot` created (public); GitHub Pages from `docs/` | Hosting + Google's homepage/privacy requirement |
| 2026-09-17 | OAuth app published to production | Avoid 7-day token expiry |
| 2026-09-17 | Brand channel "Calmlance" renamed to **Money Mechanics** | Separate channel without identity verification |
| 2026-09-17 | `config.yaml`: `gemini-2.5-flash-lite` → `gemini-3.5-flash-lite` | Model retired (run #1 404) |
| 2026-09-17 | `render.py` caption fallback + stats; `tts.py` explicit WordBoundary; `produce.yml` unbuffered logs + always-upload artifact | Run #2 caption burn-in failure |
| 2026-09-18 | Dry-run #3 succeeded end-to-end (14m 51s) | First verified full render |
| 2026-09-18 | Added this PLAYBOOK.md | Documentation for maintenance and cloning |
| 2026-09-24 | **Thursday run failed at upload: `invalid_grant: Token has been expired or revoked`** — exactly 7 days after the refresh token was minted on 17 Sep while the OAuth app was still in *Testing* (gotcha #2; the token kept its 7-day life even after the app was published). Worked for runs on 19, 20 (review) and 21 Sep, died on the 24th. Nothing was uploaded (failed on the first chunk), state untouched. Fix: re-run `setup_auth.py` (app now in Production → non-expiring token), update `YT_REFRESH_TOKEN`, re-run the workflow. Code: added `upload.preflight()` — a 1-unit `channels.list` at step 0 of every live run, so a dead token fails in 2 seconds with the fix printed instead of after a 6-minute render. | First unattended failure; make the failure mode cheap and self-explanatory. |
| 2026-09-21 | **v3.3 — watch-hours acceleration.** Reviewed a pre-monetization strategy text (verified: 4,000 h = views × minutes watched, Shorts-feed time doesn't count, Related-video button + verbal end-screen bridge; discounted: "Shorts subscribers poison long-form" (largely fixed by YouTube since 2023), live streams (not viable faceless), 20-min targets (retention risk)). Built: (1) **verbal bridge to the previous video** — `generate_script(previous=…)` gets the last long video's title and the close recommends it by name instead of the generic tease; description Watch-next + the manual end-screen card land on the same video. (2) **Auto-playlists** — `upload.add_to_category_playlist` creates one public playlist per topic category (reuses a hand-made one with the same title) and adds every long video; IDs cached in `data/playlists.json`; existing `youtube` scope suffices. (3) **Adaptive length** — `analytics.adaptive_length` moves `performance.target_minutes` ±1 from mid-video retention (≥45% / <30%, needs ≥2 videos of data), clamped to new `video.min_minutes`/`max_minutes` (7/12); `script.target_minutes()` reads it. (4) **Cadence 2×/week** — cron `0 6 * * 1,4`, Shorts offsets [1,2,3]. (5) **Trend cap** — `topics._trend_recently`: at most one trend-sourced video per `trend_max_per_days` (7), so Mon can ride the news and Thu is evergreen. (6) Studio two-minute checklist in §7 (Related video on Shorts, end screen on the long video — no API for either). | User request; the user asked that adaptive length never drop below a floor — hence `min_minutes`. |
| 2026-09-20 | **v3.2.1 — description engine** (`pipeline/describe.py`). Video 1's description had the disclaimer twice (LLM wrote one, validator appended ours), numbers spelled out ("four hundred thousand dollar" — the TTS rule leaked into the description), no hashtags, no links. New four-zone layout: (1) ≤150-char keyword-first hook + `?sub_confirmation=1` link (needs `channel.id` in config) above the fold; (2) 2–3 paragraph body with secondary keywords + "In this video: $400,000 loan · 5% vs 7% · +$200,000" key facts; (3) real chapters from render offsets, headings now prompted to be keyword-bearing mini-titles; (4) "Watch next" links to the last two long videos from `published.json` (session time), sign-off, ONE disclaimer, 3–5 specific hashtags (first three show above the title). Script schema: `description_hook`, `description_body`, `key_facts`, `hashtags`; `_clean_prose` strips any disclaimer / timestamp / URL / hashtag lines the model adds anyway. Shorts descriptions get the hashtags + `#Shorts`. `description.txt` saved to the artifact. | Review of the live description + a description-SEO guide the user shared (validated: snippet, sub link, chapters at 00:00, specific hashtags; toned down: description is a search/classification signal, not the recommender's "feed"). |
| 2026-09-19 | **v3.2.1 — thumbnail rework + audit fixes.** Live thumbnail read "2% = $200,000" (the LLM used the 7%−5% gap, not the searched number) on a cardboard-box photo; YouTube's own AI suggestions (face, red-vs-green comparison, red cost delta, house icon) were clearly stronger. Script schema now returns a `thumbnail` object: `hero` (the title's number or its cost, never a derived delta), `hero_label`, `hero_is_cost` (red vs gold), optional `compare` {left,right}, topic `icon`, and a face-first photo `query`; `_validate` normalises it (string booleans, unhashable icons, missing sides, `alt_titles` type). `render.thumbnail`: A = hero + face photo (photo shifted so the face sits in the photo half), B = green/red **versus** split when `compare` exists (else hero on the 2nd photo), C = alt title; all carry a Lucide topic badge bottom-left. Also: chart bottom padding 24% (caption no longer covers the x-axis); `_diversify` rule 4 — never 3 same-layout cards in a row (hook rule moved before it); tease prompt "write a fresh line, never reuse the examples". | Run #7 audit + comparison with YouTube's AI thumbnails. YouTube's suggestions are Studio-only (no API), so the pattern was rebuilt in PIL. |
| 2026-09-19 | **GO LIVE — run #8, first real upload** (6m 15s, no warnings). Trend scout picked "What 7% Rates Could Mean for Home Buyers" (fit 10/10) → *Why A Seven Percent Mortgage Costs Two Hundred Grand More* (6.4 min, 58 beats: 20 icon_text, 10 photo_text, 11 callout, 6 character, 5 bignumber, 3 compare, 2 list, chart) → `youtu.be/_arItix7pNQ`, scheduled Sat 2 PM ET; Shorts `lM23hv42OoA` (+1d), `PIs2aloEVh0` (+3d), `SsUE_svp1k8` (+5d). `published.json` committed by the workflow. Monday cron is now the second video. | Dry-run #7 (v3.2) audit passed: no title collisions, b-roll carries text, real portraits, contact sheet in artifact. Decision: publish while the 7%-rates story is current. |
| 2026-09-19 | Dry-run #7 (v3.2): 6m 02s, zero warnings; 47 beats @ 6.8 s. Audit findings for v3.2.1 (not yet done): caption overlaps chart x-axis (raise chart bottom padding to ~22%); runs of 5 consecutive bignumber cards (add "no 3 same-type in a row" rule to `_diversify`); generic tease copied the prompt example verbatim (add "write a fresh line, never reuse the examples"); thumbnail B photo low-contrast. | Contact-sheet review. |
| 2026-09-19 | **v3.2 — photos not cartoons, layout safe zone, trend scout, contact sheet.** Frame-by-frame audit of run #6 (68 frames) found: compare-card and chart titles overlapping the section heading; a 6 s wordless b-roll shot; a black dip + lonely "0" at the start of a count-up; odd chart ticks ("$1,368"); Open Peeps judged cartoonish by the user; thumbnail C cut at 30 chars. Changes: (1) **Open Peeps removed** — `character` beats now show a real Pexels portrait (query built from gender + mood by `assets_remote.person_query`; first appearance pins the face for the whole video via `_BrollCache.person`); `compare` people become initial-circle avatars; thumbnails are photo + fitted type (A primary, B second photo, C alt title — full text, 1–3 lines via `fit_text_box`). (2) **`motion.TOP_SAFE` = 16%**: every card, the chart title/legend and photo cards start below the heading strip. (3) B-roll beats always carry the sentence's key phrase (`visuals.broll_overlay`, merged into the lower-third overlay — no extra ffmpeg pass). (4) Fades from brand navy (`FADE_COLOR`), count-ups start at 10%. (5) Chart axes use nice steps (1/2/2.5/5 × 10ⁿ), title width-limited so it never runs under the legend. (6) **Trend scout** (`pipeline/trends.py`): Google News RSS for the finance queries in `config.yaml` + Google Trends US RSS, fetched concurrently (~2 s), LLM scores one candidate 0–10; used when fit ≥ `trend_min_fit` (7) and phrased as an evergreen mechanism; the script gets a `NEWS HOOK` line; `published.json` records `source` and `headline`. Any failure → bank. (7) **Generic next-video tease**: the close keeps a one-line curiosity gap ("a money habit that looks smart and quietly isn't") but must not name a topic, number or event, so Monday's slot stays free for the news (user decision); retention hint text updated to match. (8) `contact_sheet.jpg` (1 frame / 6 s, 6-wide grid, one ffmpeg call) in every dry-run artifact. `--topic` now works even with an exhausted bank. | User review of run #6 + request to react to real-world events instead of pre-committing topics. |
| 2026-09-19 | Dry-run #6 (v3.1): **313 s total** (long 5.7 min in 222 s; 3 Shorts in 79 s), zero warnings. Mix: 51 beats @ 6.4 s — 18 icon_text, 8 photo_text, 8 callout (16%), 7 bignumber, 3 character, 3 compare, chart, list, 2 broll. Artifact includes thumbnail A/B/C. | Callout cap and character layer verified in production. |
| 2026-09-19 | Perf: characters/icons rasterised once at a base size (1024 / 512 px) and resized with PIL per animation frame instead of re-running cairo for every size; warm-up now pre-rasterises at those exact sizes. | Animated size changes were triggering ~14 cairo renders per character beat. |
| 2026-09-19 | **Research gaps closed.** Script prompt: hook speaks the title keywords in the first two sentences; every section ends with a one-sentence forward tease (micro-hook); close = action rule → next-topic tease → sign-off, never "in summary". Thumbnails: A (primary) + B (different expression) + C (alt title, no character) saved to the artifact for Studio's *Test & Compare* (API cannot set variants). Music: real sidechain ducking (music dips under speech). **Retention diagnostics**: weekly review pulls the `audienceWatchRatio` curve per recent video (nose 5% / mid 50% / tail 90%), prints a table, and converts weak spots into `script_hints` in `performance.json`, which `generate_script` injects into the next script prompt — the channel now corrects its own hooks and endings from data. | Point-by-point audit of the external research (§9b). |
| 2026-09-19 | **v3.1 — characters instead of AI sketches.** Review of run #5: Pollinations sketches were off-topic, washed out and watermarked; "Sarah" drawn as a man; still ~50% text cards. Removed Pollinations entirely. Added `assets_remote.peep()` — DiceBear **Open Peeps** (Pablo Stanley, CC0, free HTTP API, SVG → cairosvg): hand-drawn half-body people, hairstyle/facial-hair chosen from the script's stated gender, expression from a mood word, same name → same person. New `character` card (avatar + name tag + line + optional stat); `compare` sides accept `character`. Storyboard: `character` type replaces `illustration`; hard cap of 25% plain callouts; first hook beat forced visual (bignumber if a number is spoken, else photo). Thumbnail gets a Peep with a shocked/worried face (the "emotional face" CTR lever from the research). Asset caches warmed concurrently before rendering. | User feedback + external research (retention nose/body/tail, QCR, emotional thumbnails). |
| 2026-09-19 | Dry-run #5 (v3): **6m 45s total**; long 6.5 min in 281 s with 64 beats (5.8 s/visual), icons + 4 sketches fetched without warnings. Mix was 50% callouts → added `storyboard._diversify`: no two consecutive callouts; later ones become keyword-matched `icon_text` / `photo_text` cards. | Text-only runs still too long; diversify automatically. |
| 2026-09-19 | v3 performance hardening: illustration beats dispatched last so no render worker idles on a remote fetch; Pollinations circuit breaker (60 s timeout, layer disabled after 2 consecutive failures — worst case ~2 min instead of ~18). | Asked to confirm performance was preserved; two gaps found and closed. |
| 2026-09-19 | **v3 visuals — imagery layers.** Review of run #4 output: timeline text clipped at canvas edges (1:18), and video was text-only. Added `pipeline/assets_remote.py` (Lucide icons via jsDelivr + cairosvg; Pollinations.ai sketch illustrations, keyless, rate-limited, fetched in a background thread); `motion.py` rewritten with `fit_text_box`/`draw_fit` (wrap → shrink → ellipsis, nothing clips), 3 rotating background variants, icons in list/compare/bignumber, new cards `icon_text`, `photo_text` (Pexels photo + text), `illustration` (sketch + caption), `outro_card` (6 s end screen, audio padded); Shorts get a pinned top title and an animated progress bar (overlay-based — `drawbox` cannot animate). Storyboard prompt now demands layer variety and provides the allowed icon list. Workflow installs `libcairo2`; `cairosvg` in requirements (import guarded — icons simply skip if missing). | Text cards inform but don't *feel*; a mixed-layer edit (icons, photos, sketches, footage) is what retains viewers. |
| 2026-09-18 | Dry-run #4 (beat engine + parallel): **6m 02s total** (was 14m 51s) — long video 6.4 min in 237 s, 3 Shorts in 60 s. Found: b-roll cache `key`→`slug` NameError (all b-roll beats fell back to slides) — fixed; storyboard under-split (31 beats, 12 s avg) — added pacing guard in `storyboard._normalise` (multi-sentence beats > 22 words split per sentence, extras become key-phrase callouts) and tightened the prompt to one sentence per beat. | Measured results of the performance work; pacing target is 4–8 s per visual. |
| 2026-09-18 | **Render performance.** Background gradient/blur cached (was recomputed per frame — the single biggest waste); beat clips rendered on a 4-worker thread pool (`-threads 2` each); Shorts TTS concurrent + one storyboard call for all Shorts; long-video TTS 3 concurrent; intermediates `ultrafast` (re-encoded anyway), final `veryfast` (was `medium`); per-stage timings printed. | Asked whether render time had been optimised — it had not; pipeline was fully sequential. |
| 2026-09-18 | **Beat engine (v2 visuals).** New `pipeline/storyboard.py` (LLM assigns a visual to every 1–2 sentences), `pipeline/motion.py` (animated PIL cards: bignumber count-up, compare, list reveal, formula, callout, timeline, progressive chart, chapter card), `render.py` rewritten to cut per beat with timings from TTS word boundaries, 0.25 s fades, chapter cards (audio delayed to match), karaoke ASS captions (words turn accent colour as spoken), optional music bed (`assets/music/`), split-layout thumbnail. Prompt fixes: chart series must share a unit; numerals on screen; thumbnail text needs a number/contrast + `thumbnail_query`. Description placeholder-timestamp stripping fixed. `llm.py` maxOutputTokens 16k. | Review of dry-run #3: one visual per ~50 s and visuals unrelated to the spoken words — the main retention killer. Target: a matched visual change every 4–9 s. |
