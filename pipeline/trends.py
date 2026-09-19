"""Trend scout: is there something happening in the world THIS week that the channel should explain?

Sources (free, no key, plain RSS):
  * Google News search feeds for the finance queries in config.yaml (last 7 days, US edition)
  * Google Trends "trending now" feed for the US

The headlines go to the LLM with the channel brief and the list of topics already covered. It returns
ONE candidate topic with a 0-10 "fit" score. We only take it when fit >= topics.trend_min_fit and the
story can be told as an evergreen mechanism with a worked example (news hook, timeless body) — a
finance channel must never become a news channel. Anything failing (network, parse, LLM) -> None and
the evergreen bank is used, so this can never block a Monday run.
"""
from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote_plus

import requests

from .llm import ask_json

_NEWS = "https://news.google.com/rss/search?q={q}+when:7d&hl=en-US&gl=US&ceid=US:en"
_TRENDS = "https://trends.google.com/trending/rss?geo=US"
_UA = {"User-Agent": "Mozilla/5.0 (compatible; yt-autopilot/3.2)"}
_TAG = re.compile(r"<[^>]+>")


def _fetch_titles(url: str, limit: int) -> list[str]:
    try:
        r = requests.get(url, headers=_UA, timeout=15)
        r.raise_for_status()
        root = ET.fromstring(r.content)
    except (requests.RequestException, ET.ParseError):
        return []
    out = []
    for item in root.iter("item"):
        t = item.findtext("title") or ""
        t = html.unescape(_TAG.sub("", t)).strip()
        t = re.sub(r"\s+-\s+[^-]{2,40}$", "", t)   # strip the trailing " - Publisher" Google News adds
        if len(t) > 15:
            out.append(t)
        if len(out) >= limit:
            break
    return out


def headlines(cfg: dict) -> list[str]:
    """Up to ~60 de-duplicated headlines, fetched concurrently (all feeds in ~2 s)."""
    queries = list(cfg.get("topics", {}).get("trend_queries") or ["personal finance"])
    urls = [_NEWS.format(q=quote_plus(q)) for q in queries] + [_TRENDS]
    with ThreadPoolExecutor(max_workers=min(8, len(urls))) as pool:
        batches = list(pool.map(lambda u: _fetch_titles(u, 8), urls))
    seen, out = set(), []
    for batch in batches:
        for t in batch:
            k = t.lower()[:60]
            if k not in seen:
                seen.add(k)
                out.append(t)
    return out[:60]


SYSTEM = """You are the editor of a faceless YouTube channel that explains how money works with worked numeric examples.
You receive this week's headlines. Decide whether ONE of them is a genuinely good hook for the channel.
A good trend topic: (1) touches the audience's own money (rates, taxes, prices, jobs, housing, debt, investing, benefits);
(2) can be explained as a timeless MECHANISM with a reproducible calculation, so the video stays useful for years;
(3) is not political commentary, not a single company's stock tip, not celebrity/crime/sports news, not already covered.
Score 'fit' 0-10 honestly: 9-10 = everyone with a paycheck is asking about this right now; 7-8 = strong, clearly relevant;
<= 6 = weak or a stretch. Return ONLY JSON."""

SCHEMA = """{
  "fit": 0,
  "headline": "the headline you picked (verbatim) or empty",
  "topic": "video topic phrased as an evergreen explainer with a number, e.g. 'What a 0.25% Fed cut actually does to your $400,000 mortgage'",
  "category": "one of the given categories",
  "news_hook": "one sentence the script can open with that ties the timeless mechanism to this week's event",
  "why": "one sentence"
}"""


def scout(cfg: dict, categories: list[str], covered: list[str]) -> dict | None:
    """Returns {"topic","category","news_hook","headline","fit"} or None when nothing qualifies."""
    tcfg = cfg.get("topics", {})
    if not tcfg.get("trends", True):
        return None
    heads = headlines(cfg)
    if len(heads) < 5:
        print("[trends] too few headlines fetched; using the evergreen bank")
        return None
    ch = cfg["channel"]
    user = (f"Channel: {ch['name']} — {ch['tagline']}\nNiche: {ch['niche']}\nAudience: {ch['audience']}\n"
            f"Categories: {', '.join(categories)}\n"
            f"Already covered (do not repeat): {'; '.join(covered[-25:]) or 'nothing yet'}\n\n"
            "This week's headlines:\n" + "\n".join(f"- {h}" for h in heads) +
            f"\n\nReturn JSON exactly like:\n{SCHEMA}")
    try:
        data = ask_json(cfg, SYSTEM, user, temperature=0.3)
        fit = int(data.get("fit") or 0)
    except Exception as e:  # noqa: BLE001
        print(f"[trends] LLM failed ({str(e)[:100]}); using the evergreen bank")
        return None
    topic = str(data.get("topic") or "").strip()
    min_fit = int(tcfg.get("trend_min_fit", 7))
    if fit < min_fit or len(topic) < 12:
        print(f"[trends] best headline scored {fit}/10 (< {min_fit}); using the evergreen bank")
        return None
    cat = str(data.get("category") or "")
    if cat not in categories:
        cat = categories[0]
    print(f"[trends] using trend topic (fit {fit}/10): {topic}  <- {str(data.get('headline') or '')[:80]}")
    return {"topic": topic[:120], "category": cat, "news_hook": str(data.get("news_hook") or "")[:300],
            "headline": str(data.get("headline") or "")[:160], "fit": fit}
