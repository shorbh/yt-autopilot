"""Weekly feedback loop: pull per-video performance, score categories/formats, refill topic bank.

Score = normalised blend of views, average view percentage and subscribers gained,
so the topic picker leans into what the audience actually rewards. This is the
'human-like editorial decision' layer that makes the channel improve on its own.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

from .llm import ask_json
from .state import load_published, save_performance
from .topics import add_topics, load_bank
from .upload import analytics_client


def fetch_video_stats(days: int = 28) -> dict[str, dict]:
    ya = analytics_client()
    end = date.today() - timedelta(days=1)
    start = end - timedelta(days=days)
    try:
        resp = ya.reports().query(
            ids="channel==MINE",
            startDate=start.isoformat(),
            endDate=end.isoformat(),
            metrics="views,estimatedMinutesWatched,averageViewPercentage,subscribersGained,likes",
            dimensions="video",
            sort="-views",
            maxResults=200,
        ).execute()
    except Exception as e:  # noqa: BLE001 - HttpError, RefreshError (bad token), network; keep the review going
        print(f"[warn] analytics query failed: {e}")
        return {}
    cols = [c["name"] for c in resp.get("columnHeaders", [])]
    out = {}
    for row in resp.get("rows", []):
        rec = dict(zip(cols, row))
        out[rec["video"]] = rec
    return out


def channel_totals() -> dict:
    ya = analytics_client()
    end = date.today() - timedelta(days=1)
    start = end - timedelta(days=365)
    try:
        resp = ya.reports().query(ids="channel==MINE", startDate=start.isoformat(), endDate=end.isoformat(),
                                  metrics="views,estimatedMinutesWatched,subscribersGained,subscribersLost").execute()
        row = resp.get("rows", [[0, 0, 0, 0]])[0]
        return {"views_365d": row[0], "watch_hours_365d": round(row[1] / 60, 1), "subs_net_365d": row[2] - row[3]}
    except Exception as e:  # noqa: BLE001
        print(f"[warn] channel totals failed: {e}")
        return {}


def fetch_retention(video_ids: list[str], days: int = 28) -> dict[str, dict]:
    """Nose/body/tail retention per video from YouTube Analytics' audienceWatchRatio curve.
    Returns {video_id: {"nose": ratio still watching at 5% of the video, "mid": at 50%, "tail": at 90%}}."""
    ya = analytics_client()
    end = date.today() - timedelta(days=1)
    start = end - timedelta(days=days)
    out = {}
    for vid in video_ids[:12]:  # quota is cheap (1 unit each) but keep the review fast
        try:
            resp = ya.reports().query(
                ids="channel==MINE", startDate=start.isoformat(), endDate=end.isoformat(),
                metrics="audienceWatchRatio", dimensions="elapsedVideoTimeRatio", filters=f"video=={vid}",
            ).execute()
            rows = resp.get("rows", [])
            if not rows:
                continue
            curve = {float(r[0]): float(r[1]) for r in rows}

            def at(x):
                k = min(curve, key=lambda e: abs(e - x))
                return curve[k]
            out[vid] = {"nose": round(at(0.05), 3), "mid": round(at(0.5), 3), "tail": round(at(0.9), 3)}
        except Exception as e:  # noqa: BLE001
            print(f"[warn] retention curve for {vid} failed: {str(e)[:120]}")
    return out


def retention_hints(ret: dict[str, dict]) -> list[str]:
    """Turn retention diagnostics into concrete writing instructions for next week's script."""
    if not ret:
        return []
    n = len(ret)
    nose = sum(r["nose"] for r in ret.values()) / n
    mid = sum(r["mid"] for r in ret.values()) / n
    tail = sum(r["tail"] for r in ret.values()) / n
    hints = []
    if nose < 0.60:   # >40% gone in the first 5% of the video = "hook miss"
        hints.append(f"Recent videos lost {100 - nose * 100:.0f}% of viewers in the first seconds. Make the hook ONE short sentence "
                     "with the single most surprising number, then immediately show the stakes; no context first.")
    if mid < 0.35:
        hints.append(f"Only {mid * 100:.0f}% of viewers reach the midpoint. Move the worked example earlier (section s2 at the latest) "
                     "and add a stronger forward tease at the end of s1 and s2.")
    if tail < 0.20 and mid >= 0.35:
        hints.append(f"Only {tail * 100:.0f}% reach the last 10%. Make the final section shorter and end on the action rule, "
                     "a generic (topic-free) tease and the sign-off; no wind-down language.")
    return hints


def score_and_save(stats: dict[str, dict]) -> dict:
    pub = load_published()
    by_cat, by_fmt = defaultdict(list), defaultdict(list)
    scored = []
    for p in pub:
        st = stats.get(p.get("video_id", ""))
        if not st or p.get("kind") != "long":
            continue
        s = (st.get("views", 0) ** 0.5) * (0.5 + st.get("averageViewPercentage", 0) / 100) + 3 * st.get("subscribersGained", 0)
        scored.append(s)
        by_cat[p["category"]].append(s)
        by_fmt[p["format"]].append(s)
    if not scored:
        return {}
    mean = sum(scored) / len(scored) or 1.0
    long_ids = [p["video_id"] for p in pub if p.get("kind") == "long" and p.get("video_id") in stats][-8:]
    ret = fetch_retention(long_ids)
    perf = {
        "category_scores": {c: round((sum(v) / len(v)) / mean, 3) for c, v in by_cat.items()},
        "format_scores": {f: round((sum(v) / len(v)) / mean, 3) for f, v in by_fmt.items()},
        "videos_scored": len(scored),
        "retention": ret,
        "script_hints": retention_hints(ret),
    }
    save_performance(perf)
    return perf


def refill_topics(cfg: dict, perf: dict, per_category: int = 5) -> int:
    """Ask the LLM for fresh, non-duplicate topics — more for winning categories."""
    bank = load_bank()
    added = 0
    for cat, titles in bank["categories"].items():
        weight = perf.get("category_scores", {}).get(cat, 1.0)
        n = max(2, round(per_category * min(2.0, max(0.5, weight))))
        data = ask_json(
            cfg,
            "You are a YouTube strategist for a personal-finance explainer channel. Return JSON only.",
            f"Category: {cat}\nExisting topics (do not repeat or paraphrase):\n- " + "\n- ".join(titles[-40:]) +
            f"\n\nPropose {n} NEW video topics for this category. Each must contain a concrete mechanism, number, or comparison "
            f"and be evergreen for a global English audience. Format: {{\"topics\": [\"...\", ...]}}",
            temperature=0.9,
        )
        added += add_topics(cat, [t for t in data.get("topics", []) if isinstance(t, str) and 20 < len(t) < 120])
    return added


def weekly_report(cfg: dict, stats: dict, perf: dict, totals: dict, added: int) -> str:
    pub = load_published()
    lines = [f"# Weekly channel review — {date.today().isoformat()}", ""]
    if totals:
        need_hours = max(0, 4000 - totals.get("watch_hours_365d", 0))
        lines += [f"**Watch hours (365d):** {totals.get('watch_hours_365d')}  →  {need_hours:.0f} h to YPP threshold",
                  f"**Net subs (365d):** {totals.get('subs_net_365d')}   **Views:** {totals.get('views_365d')}", ""]
    lines += ["## Top videos (28d)", "", "| Video | Views | Avg % watched | Subs |", "|---|---|---|---|"]
    ranked = sorted(pub, key=lambda p: stats.get(p.get("video_id", ""), {}).get("views", 0), reverse=True)[:10]
    for p in ranked:
        s = stats.get(p.get("video_id", ""), {})
        lines.append(f"| {p['title'][:50]} | {s.get('views', 0)} | {s.get('averageViewPercentage', 0):.0f}% | {s.get('subscribersGained', 0)} |")
    if perf:
        lines += ["", "## What the audience rewards", ""]
        for c, v in sorted(perf["category_scores"].items(), key=lambda kv: -kv[1]):
            lines.append(f"- {c}: {v:.2f}x")
        if perf.get("retention"):
            lines += ["", "## Retention (share of viewers still watching)", "", "| Video | at 5% (nose) | at 50% | at 90% (tail) |", "|---|---|---|---|"]
            titles = {p.get("video_id"): p["title"] for p in pub}
            for vid, r in perf["retention"].items():
                lines.append(f"| {titles.get(vid, vid)[:45]} | {r['nose'] * 100:.0f}% | {r['mid'] * 100:.0f}% | {r['tail'] * 100:.0f}% |")
        if perf.get("script_hints"):
            lines += ["", "## Automatic writing adjustments for next week", ""] + [f"- {h}" for h in perf["script_hints"]]
    lines += ["", f"Topic bank refilled with **{added}** new topics.", ""]
    return "\n".join(lines)
