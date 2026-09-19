"""Video descriptions, assembled in four zones (search snippet -> context -> chapters -> ecosystem).

  1. Above the fold: the LLM's <=150-char hook (primary keyword first) + a one-click subscribe link,
     so both are visible before "...more".
  2. Context body: 2-3 short paragraphs with secondary keywords (LLM), then an "In this video" line of
     the concrete numbers for skimmers and search.
  3. Chapters: keyword-rich section headings from the real render offsets, first one at 00:00.
  4. Ecosystem: "Watch next" links to the channel's most recent long videos (session time), sign-off,
     the single canonical disclaimer, and 3-5 specific hashtags (YouTube shows the first three above the title).
"""
from __future__ import annotations

from .state import load_published


def subscribe_link(cfg: dict) -> str:
    cid = str(cfg["channel"].get("id") or "").strip()
    return f"https://www.youtube.com/channel/{cid}?sub_confirmation=1" if cid else ""


def watch_next(exclude_topic: str | None = None, n: int = 2) -> list[dict]:
    """Most recent long videos (newest first), excluding the current topic."""
    longs = [p for p in load_published() if p.get("kind") == "long" and p.get("video_id")
             and (exclude_topic is None or p.get("topic") != exclude_topic)]
    return longs[-n:][::-1]


def long_description(cfg: dict, script: dict, stamps: list[str], topic: str | None = None) -> str:
    ch = cfg["channel"]
    sub = subscribe_link(cfg)
    parts: list[str] = []
    hook = script.get("description_hook") or script.get("title", "")
    parts.append(hook + (f"\nSubscribe: {sub}" if sub else ""))
    body = script.get("description_body") or ""
    if body:
        parts.append(body)
    facts = script.get("key_facts") or []
    if facts:
        parts.append("In this video: " + " · ".join(facts))
    if stamps:
        parts.append("Chapters:\n" + "\n".join(stamps))
    nxt = watch_next(exclude_topic=topic)
    if nxt:
        parts.append("Watch next:\n" + "\n".join(f"▶ {p.get('title') or ch['name']}: https://youtu.be/{p['video_id']}" for p in nxt))
    parts.append(ch["signoff"])
    parts.append(ch["disclaimer"])
    tags = script.get("hashtags") or []
    if tags:
        parts.append(" ".join(tags))
    parts = [p.strip() for p in parts if p and p.strip()]
    # YouTube's limit is 5,000 chars (upload.py cuts at 4,900). Never slice mid-URL: shorten the body first.
    while len("\n\n".join(parts)) > 4800 and len(parts) > 2:
        longest = max(range(len(parts)), key=lambda i: len(parts[i]))
        parts[longest] = parts[longest][: max(200, len(parts[longest]) - 400)].rsplit(" ", 1)[0] + "…"
    return "\n\n".join(parts)


def short_description(cfg: dict, script: dict, short_title: str, long_id: str) -> str:
    ch = cfg["channel"]
    tags = list(script.get("hashtags") or [])[:3]
    return "\n\n".join(x for x in (
        f"{short_title}\nFull breakdown: https://youtu.be/{long_id}",
        ch["disclaimer"],
        " ".join(tags + ["#Shorts"]),
    ) if x)
