"""YouTube Data API v3 upload with OAuth refresh token (headless-safe for GitHub Actions).

Quota: upload = 1600 units, thumbnail = 50, list = 1. Daily free quota = 10,000.
One weekly run (1 long + 3 Shorts + 1 thumbnail) ~ 6,450 units. Fine.
"""
from __future__ import annotations

import json
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


def preflight() -> str:
    """Prove the refresh token still works BEFORE spending six minutes rendering (1 quota unit).
    Returns the channel title. Raises RuntimeError with the exact fix when the token is dead."""
    from google.auth.exceptions import RefreshError
    try:
        resp = youtube_client().channels().list(part="snippet", mine=True).execute()
        return resp["items"][0]["snippet"]["title"]
    except RefreshError as e:
        raise RuntimeError(
            "YouTube refresh token rejected (invalid_grant: expired or revoked). Fix: run `python setup_auth.py "
            "client_secret.json` locally (the OAuth app must be in Production, and pick the brand channel), paste the "
            "new value into the YT_REFRESH_TOKEN repository secret, then re-run this workflow. Nothing was uploaded."
        ) from e


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


# ------------------------------------------------------------------ playlists (session-time lever)

def _playlist_title(category: str) -> str:
    return category.replace("_", " ").strip().title()   # "big_life_money" -> "Big Life Money"


def add_to_category_playlist(cfg: dict, category: str, video_id: str) -> str | None:
    """Add a long video to the playlist named after its topic category, creating the playlist the first time.
    Playlist IDs are cached in data/playlists.json (committed with the other state). Never fatal — a playlist
    failure must not fail the upload. Costs: playlists.insert 50 units (once per category), playlistItems.insert 50."""
    from .config import DATA
    if not cfg.get("topics", {}).get("playlists", True):
        return None
    cache_path = DATA / "playlists.json"
    try:
        cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}
    except (OSError, ValueError):
        cache = {}
    yt = youtube_client()
    title = _playlist_title(category)
    pid = cache.get(category)
    try:
        if not pid:
            # look for an existing playlist with that title (e.g. created by hand) before making a new one
            token = None
            while not pid:
                resp = yt.playlists().list(part="snippet", mine=True, maxResults=50, pageToken=token).execute()
                for item in resp.get("items", []):
                    if item["snippet"]["title"].strip().lower() == title.lower():
                        pid = item["id"]
                        break
                token = resp.get("nextPageToken")
                if not token:
                    break
        if not pid:
            body = {"snippet": {"title": title,
                                "description": f"{cfg['channel']['name']} — every explainer on {title.lower()}. {cfg['channel']['tagline']}"},
                    "status": {"privacyStatus": "public"}}
            pid = yt.playlists().insert(part="snippet,status", body=body).execute()["id"]
            print(f"      created playlist '{title}' ({pid})")
        yt.playlistItems().insert(part="snippet", body={"snippet": {"playlistId": pid, "resourceId": {"kind": "youtube#video", "videoId": video_id}}}).execute()
        cache[category] = pid
        cache_path.write_text(json.dumps(cache, indent=2), encoding="utf-8")
        return pid
    except Exception as e:  # noqa: BLE001 - HttpError, RefreshError, network, disk: a playlist must never fail a run
        print(f"[warn] playlist '{title}' not updated: {str(e)[:160]}")
        return None
