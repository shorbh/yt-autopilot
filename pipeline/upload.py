"""YouTube Data API v3 upload with OAuth refresh token (headless-safe for GitHub Actions).

Quota: upload = 1600 units, thumbnail = 50, list = 1. Daily free quota = 10,000.
One weekly run (1 long + 3 Shorts + 1 thumbnail) ~ 6,450 units. Fine.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

from .config import env

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]


def youtube_client():
    cid, secret, refresh = env("YT_CLIENT_ID"), env("YT_CLIENT_SECRET"), env("YT_REFRESH_TOKEN")
    if not all((cid, secret, refresh)):
        raise RuntimeError("Missing YT_CLIENT_ID / YT_CLIENT_SECRET / YT_REFRESH_TOKEN. Run setup_auth.py once.")
    creds = Credentials(None, refresh_token=refresh, client_id=cid, client_secret=secret,
                        token_uri="https://oauth2.googleapis.com/token", scopes=SCOPES)
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def analytics_client():
    cid, secret, refresh = env("YT_CLIENT_ID"), env("YT_CLIENT_SECRET"), env("YT_REFRESH_TOKEN")
    creds = Credentials(None, refresh_token=refresh, client_id=cid, client_secret=secret,
                        token_uri="https://oauth2.googleapis.com/token", scopes=SCOPES)
    return build("youtubeAnalytics", "v2", credentials=creds, cache_discovery=False)


def publish_at(cfg: dict, days_from_now: int, hour: int) -> str:
    tz = ZoneInfo(cfg["publishing"]["timezone"])
    now = datetime.now(tz)
    when = (now + timedelta(days=days_from_now)).replace(hour=hour, minute=0, second=0, microsecond=0)
    if when <= now + timedelta(minutes=15):
        when += timedelta(days=1)
    return when.isoformat()


def upload_video(cfg: dict, path: Path, title: str, description: str, tags: list[str], publish_iso: str | None,
                 thumbnail: Path | None = None, is_short: bool = False) -> str:
    yt = youtube_client()
    pub = cfg["publishing"]
    if is_short and "#shorts" not in description.lower():
        description = description.rstrip() + "\n\n#Shorts"
    # YouTube limits the tag list to 500 chars in total (tags with spaces count their quotes too) -> invalidTags
    kept, total = [], 0
    for t in tags[:30]:
        t = str(t).strip()[:30]
        cost = len(t) + (2 if " " in t else 0) + 1
        if not t or total + cost > 450:
            continue
        kept.append(t)
        total += cost
    tags = kept
    body = {
        "snippet": {
            "title": title[:100],
            "description": description[:4900],
            "tags": tags,
            "categoryId": pub["category_id"],
            "defaultLanguage": cfg["channel"]["language"][:2],
            "defaultAudioLanguage": cfg["channel"]["language"],
        },
        "status": {
            "privacyStatus": pub["privacy"] if publish_iso else "public",
            "selfDeclaredMadeForKids": bool(pub["made_for_kids"]),
            "containsSyntheticMedia": bool(pub.get("synthetic_media_disclosure", True)),
        },
    }
    if publish_iso and pub["privacy"] == "private":
        body["status"]["publishAt"] = publish_iso

    media = MediaFileUpload(str(path), chunksize=8 * 1024 * 1024, resumable=True, mimetype="video/mp4")
    req = yt.videos().insert(part="snippet,status", body=body, media_body=media)
    video_id = None
    backoff = 5
    while video_id is None:
        try:
            status, resp = req.next_chunk()
            if resp is not None:
                video_id = resp["id"]
        except HttpError as e:
            if e.resp.status in (500, 502, 503, 504) and backoff < 300:
                time.sleep(backoff)
                backoff *= 2
            else:
                raise
    if thumbnail and thumbnail.exists() and not is_short:
        try:
            yt.thumbnails().set(videoId=video_id, media_body=MediaFileUpload(str(thumbnail))).execute()
        except HttpError as e:  # custom thumbnails need a verified phone number on the channel
            print(f"[warn] thumbnail not set: {e}")
    return video_id
