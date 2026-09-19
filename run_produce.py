"""Produce one long video + Shorts, then upload (or dry-run).

Usage:
  python run_produce.py                 # full run: pick topic -> script -> render -> upload
  python run_produce.py --dry-run       # everything except upload (files land in build/<slug>/)
  python run_produce.py --topic "..."   # force a topic
  python run_produce.py --no-shorts
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

from pipeline.config import BUILD, load_config
from pipeline.describe import long_description, short_description
from pipeline.render import build_long_video, build_shorts, contact_sheet, thumbnail
from pipeline.script import generate_script, word_count
from pipeline.state import record_published
from pipeline.topics import pick_topic


def slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:60]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--topic")
    ap.add_argument("--no-shorts", action="store_true")
    ap.add_argument("--keep-build", action="store_true", help="don't delete build files after upload")
    args = ap.parse_args()

    cfg = load_config()
    t0 = time.time()

    # forced topic skips the trend scan (saves an LLM call); otherwise trend scout first, evergreen bank second
    pick = pick_topic(cfg=cfg, forced=args.topic or None)
    print(f"[1/5] Topic: {pick['topic']}  ({pick['category']} · {pick['format'].split(':')[0]} · {pick.get('source', 'bank')})")

    script = generate_script(cfg, pick)
    print(f"[2/5] Script: '{script['title']}'  ~{word_count(script)} words  ({time.time()-t0:.0f}s)")

    workdir = BUILD / f"{datetime.now():%Y%m%d}-{slugify(script['title'])}"
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / "script.json").write_text(json.dumps({"pick": pick, "script": script}, indent=2, ensure_ascii=False), encoding="utf-8")

    t1 = time.time()
    long = build_long_video(cfg, script, workdir)
    print(f"[3/5] Long video rendered: {long['duration']/60:.1f} min in {time.time()-t1:.0f}s -> {long['path']}")
    thumb = thumbnail(cfg, script, workdir)
    contact_sheet(Path(long["path"]), workdir / "contact_sheet.jpg")   # one frame per 6 s, tiled — for visual QA

    t2 = time.time()
    shorts = [] if args.no_shorts else build_shorts(cfg, script, workdir)
    print(f"[4/5] Shorts rendered: {len(shorts)} in {time.time()-t2:.0f}s")

    # description: hook + subscribe link above the fold, context body, key facts, real chapters, watch-next, hashtags
    desc = long_description(cfg, script, long["timestamps"], topic=pick["topic"])
    (workdir / "description.txt").write_text(desc, encoding="utf-8")

    if args.dry_run:
        print(f"[5/5] DRY RUN — nothing uploaded. Inspect: {workdir}")
        print(f"Done in {time.time()-t0:.0f}s")
        return 0

    from pipeline.upload import publish_at, upload_video

    pub = cfg["publishing"]
    long_id = upload_video(cfg, Path(long["path"]), script["title"], desc, script["tags"],
                           publish_at(cfg, 0, pub["long_publish_hour"]), thumbnail=thumb)
    record_published({"kind": "long", "video_id": long_id, "title": script["title"], "topic": pick["topic"],
                      "category": pick["category"], "format": pick["format"], "duration": long["duration"],
                      "source": pick.get("source", "bank"), "headline": pick.get("headline", "")})
    print(f"[5/5] Uploaded long video: https://youtu.be/{long_id}")

    for i, sh in enumerate(shorts):
        day = pub["shorts_offset_days"][i % len(pub["shorts_offset_days"])]
        sdesc = short_description(cfg, script, sh["title"], long_id)
        sid = upload_video(cfg, Path(sh["path"]), sh["title"], sdesc, script["tags"][:10],
                           publish_at(cfg, day, pub["shorts_publish_hour"]), is_short=True)
        record_published({"kind": "short", "video_id": sid, "title": sh["title"], "topic": pick["topic"],
                          "category": pick["category"], "format": pick["format"], "parent": long_id})
        print(f"      Short {i+1} scheduled (+{day}d): https://youtu.be/{sid}")

    if not args.keep_build:
        shutil.rmtree(workdir, ignore_errors=True)
    print(f"Done in {time.time()-t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
