"""Outlier scout: which videos in our niche are doing far better than their channel's size predicts?

The user's hand-built "outlier" playlist (Oct 2026) showed the pattern — beginner maps, bright icon-grid thumbnails,
"Explained / for Beginners / vs / Step by Step" titles — at 50-100x channel baseline. This automates the discovery
weekly so the prompts keep learning from what is winning NOW:

  search.list (100 units) for each niche query, last 90 days, relevance order
  -> videos.list statistics (1 unit)  -> channels.list statistics (1 unit)
  -> ratio = views / subscribers; keep ratio >= 10 and views >= 20k; store in data/outliers.json (rolling 150)
  -> the script prompt gets the top titles as "patterns that win"; the refill gets the topics.

Quota: ~12 searches a week ≈ 1,250 units of the 10,000/day. Everything is failure-tolerant.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta, timezone

from .config import DATA

OUTLIERS = DATA / "outliers.json"


def load_outliers() -> list[dict]:
    try:
        return json.loads(OUTLIERS.read_text(encoding="utf-8")) if OUTLIERS.exists() else []
    except (OSError, ValueError):
        return []


def _iso_days_ago(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_duration(iso: str) -> int:
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso or "")
    if not m:
        return 0
    h, mi, s = (int(x) if x else 0 for x in m.groups())
    return h * 3600 + mi * 60 + s


def scout(cfg: dict, max_queries: int = 12) -> list[dict]:
    """Run the searches, score, merge into data/outliers.json. Returns the new outliers found this run."""
    from .upload import youtube_client
    queries = list(cfg.get("topics", {}).get("outlier_queries") or [])[:max_queries]
    if not queries:
        return []
    try:
        yt = youtube_client()
    except Exception as e:  # noqa: BLE001
        print(f"[outliers] no YouTube client ({str(e)[:80]}); skipped")
        return []
    vids: dict[str, dict] = {}
    for q in queries:
        try:
            res = yt.search().list(part="snippet", q=q, type="video", maxResults=25, order="relevance",
                                   publishedAfter=_iso_days_ago(90), relevanceLanguage="en", regionCode="US",
                                   videoDuration="medium").execute()   # medium = 4-20 min, our format
            for it in res.get("items", []):
                vid = it["id"]["videoId"]
                vids.setdefault(vid, {"video_id": vid, "title": it["snippet"]["title"], "channel_id": it["snippet"]["channelId"],
                                      "channel": it["snippet"]["channelTitle"], "published": it["snippet"]["publishedAt"][:10],
                                      "query": q})
        except Exception as e:  # noqa: BLE001 - quota, network
            print(f"[outliers] search '{q}' failed: {str(e)[:100]}")
    if not vids:
        return []
    ids = list(vids)
    try:
        for i in range(0, len(ids), 50):
            stats = yt.videos().list(part="statistics,contentDetails", id=",".join(ids[i:i + 50])).execute()
            for v in stats.get("items", []):
                d = vids.get(v["id"])
                if d is not None:
                    d["views"] = int(v.get("statistics", {}).get("viewCount", 0) or 0)
                    d["duration_s"] = _parse_duration(v.get("contentDetails", {}).get("duration", ""))
        ch_ids = list({d["channel_id"] for d in vids.values()})
        subs: dict[str, int] = {}
        for i in range(0, len(ch_ids), 50):
            chans = yt.channels().list(part="statistics", id=",".join(ch_ids[i:i + 50])).execute()
            for c in chans.get("items", []):
                subs[c["id"]] = int(c.get("statistics", {}).get("subscriberCount", 0) or 0)
    except Exception as e:  # noqa: BLE001
        print(f"[outliers] stats failed: {str(e)[:100]}")
        return []
    found: list[dict] = []
    for d in vids.values():
        s = subs.get(d["channel_id"], 0)
        v = d.get("views", 0)
        if v < 20_000 or s <= 0:
            continue
        ratio = v / s
        if ratio >= 10:
            d["subs"] = s
            d["ratio"] = round(ratio, 1)
            found.append(d)
    found.sort(key=lambda d: -d["ratio"])
    # merge, rolling window of 150 by ratio
    existing = {o["video_id"]: o for o in load_outliers()}
    for d in found:
        existing[d["video_id"]] = {**existing.get(d["video_id"], {}), **d, "seen": date.today().isoformat()}
    merged = sorted(existing.values(), key=lambda o: -o.get("ratio", 0))[:150]
    OUTLIERS.write_text(json.dumps(merged, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[outliers] {len(found)} outliers this week (ratio >= 10x, >= 20k views); {len(merged)} tracked")
    return found


def patterns_block(limit: int = 12) -> str:
    """Prompt block for the script writer: the highest-ratio titles tracked, so it sees what wins NOW."""
    items = sorted(load_outliers(), key=lambda o: -o.get("ratio", 0))[:limit]
    if not items:
        return ""
    lines = [f"- \"{o['title']}\" ({o.get('ratio', 0):.0f}x its channel's size, {o.get('views', 0):,} views, "
             f"{o.get('duration_s', 0) // 60} min)" for o in items]
    return ("\nTITLES THAT OUT-PERFORM ON SMALL CHANNELS IN THIS NICHE RIGHT NOW (match their promise and shape, never copy them):\n"
            + "\n".join(lines) + "\n")


def topic_candidates(limit: int = 10) -> list[str]:
    """Outlier titles re-usable as topic seeds (de-branded)."""
    out = []
    for o in sorted(load_outliers(), key=lambda o: -o.get("ratio", 0)):
        t = re.sub(r"[|•#].*$", "", o["title"]).strip()
        t = re.sub(r"\s*\(.*?\)\s*$", "", t).strip()
        if 20 < len(t) < 110:
            out.append(t)
        if len(out) >= limit:
            break
    return out
