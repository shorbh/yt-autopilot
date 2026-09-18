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
| Cadence | 1 long (6–10 min) + 3 Shorts per week; Monday 06:00 UTC production, long publishes 14:00 ET Mon, Shorts Tue/Thu/Sat 12:00 ET |
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
      1. pipeline/topics.py    pick unused topic (weighted by performance), rotate 7 formats
      2. pipeline/script.py    LLM → strict JSON: title, 7 sections, chart data, 3 Shorts,
                               description, tags, thumbnail text   (pipeline/llm.py: provider chain)
      3. pipeline/tts.py       edge-tts per section → mp3 + word timings; concat + loudnorm
         pipeline/storyboard.py LLM splits each section into beats (1-2 sentences) + a visual spec
         pipeline/motion.py    animated PIL cards: bignumber | compare | list | formula | callout | icon_text |
                               photo_text | illustration | timeline | chart | chapter | outro  (text always fitted)
         pipeline/assets_remote.py  Lucide icons (jsDelivr + cairosvg), Pollinations sketch illustrations
         pipeline/visuals.py   static helpers, chart drawing, Pexels b-roll/photos, lower-third / Shorts title
         pipeline/render.py    beat timing from word boundaries → one clip per beat (fade) → concat
                               → voice (+ music bed) → karaoke ASS captions → long.mp4
                               3 × short.mp4 (1080×1920)   +  thumbnail.jpg (split layout)
      4. pipeline/upload.py    YouTube Data API v3 resumable upload, private + publishAt,
                               containsSyntheticMedia=true, thumbnail set
      5. pipeline/state.py     data/published.json → git commit (so topics never repeat)
```

**Files you will actually touch**

| File | Purpose |
|---|---|
| `config.yaml` | channel name/tagline/niche/sign-off, voice, video length, cadence hours, colours, LLM models |
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

**Manual production run:** Actions → *Produce & schedule weekly videos* → *Run workflow*. Tick *Render only* for a dry run (artifact appears on the run page). Optional *Force a topic*.

**Change cadence:** edit the `cron` in `produce.yml` (`0 6 * * 1,3,5` = Mon/Wed/Fri). Publish hours are in `config.yaml → publishing`.

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

## 10. Open items / next steps

- [ ] **Review the dry-run output** (artifact of run #3): voice, caption size, chart/stat-card look, thumbnail text.
- [ ] Decide: wait for the Monday cron, or trigger the first real upload now.
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
| 2026-09-19 | v3 performance hardening: illustration beats dispatched last so no render worker idles on a remote fetch; Pollinations circuit breaker (60 s timeout, layer disabled after 2 consecutive failures — worst case ~2 min instead of ~18). | Asked to confirm performance was preserved; two gaps found and closed. |
| 2026-09-19 | **v3 visuals — imagery layers.** Review of run #4 output: timeline text clipped at canvas edges (1:18), and video was text-only. Added `pipeline/assets_remote.py` (Lucide icons via jsDelivr + cairosvg; Pollinations.ai sketch illustrations, keyless, rate-limited, fetched in a background thread); `motion.py` rewritten with `fit_text_box`/`draw_fit` (wrap → shrink → ellipsis, nothing clips), 3 rotating background variants, icons in list/compare/bignumber, new cards `icon_text`, `photo_text` (Pexels photo + text), `illustration` (sketch + caption), `outro_card` (6 s end screen, audio padded); Shorts get a pinned top title and an animated progress bar (overlay-based — `drawbox` cannot animate). Storyboard prompt now demands layer variety and provides the allowed icon list. Workflow installs `libcairo2`; `cairosvg` in requirements (import guarded — icons simply skip if missing). | Text cards inform but don't *feel*; a mixed-layer edit (icons, photos, sketches, footage) is what retains viewers. |
| 2026-09-18 | Dry-run #4 (beat engine + parallel): **6m 02s total** (was 14m 51s) — long video 6.4 min in 237 s, 3 Shorts in 60 s. Found: b-roll cache `key`→`slug` NameError (all b-roll beats fell back to slides) — fixed; storyboard under-split (31 beats, 12 s avg) — added pacing guard in `storyboard._normalise` (multi-sentence beats > 22 words split per sentence, extras become key-phrase callouts) and tightened the prompt to one sentence per beat. | Measured results of the performance work; pacing target is 4–8 s per visual. |
| 2026-09-18 | **Render performance.** Background gradient/blur cached (was recomputed per frame — the single biggest waste); beat clips rendered on a 4-worker thread pool (`-threads 2` each); Shorts TTS concurrent + one storyboard call for all Shorts; long-video TTS 3 concurrent; intermediates `ultrafast` (re-encoded anyway), final `veryfast` (was `medium`); per-stage timings printed. | Asked whether render time had been optimised — it had not; pipeline was fully sequential. |
| 2026-09-18 | **Beat engine (v2 visuals).** New `pipeline/storyboard.py` (LLM assigns a visual to every 1–2 sentences), `pipeline/motion.py` (animated PIL cards: bignumber count-up, compare, list reveal, formula, callout, timeline, progressive chart, chapter card), `render.py` rewritten to cut per beat with timings from TTS word boundaries, 0.25 s fades, chapter cards (audio delayed to match), karaoke ASS captions (words turn accent colour as spoken), optional music bed (`assets/music/`), split-layout thumbnail. Prompt fixes: chart series must share a unit; numerals on screen; thumbnail text needs a number/contrast + `thumbnail_query`. Description placeholder-timestamp stripping fixed. `llm.py` maxOutputTokens 16k. | Review of dry-run #3: one visual per ~50 s and visuals unrelated to the spoken words — the main retention killer. Target: a matched visual change every 4–9 s. |
