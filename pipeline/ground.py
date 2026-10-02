"""Grounding for money-story topics: the LLM may only use facts that appear in fetched sources.

Why: "How does Venmo make money" or "Why is rent so expensive" are the formats that grow faceless channels,
but an ungrounded LLM will invent revenue figures and dates. We fetch (free, no keys):
  * Wikipedia — search for the subject, take the top article's summary + first sections (REST API)
  * Google News RSS — 4 recent headlines + snippets for the subject
and hand the excerpts to the script prompt with a hard rule; the URLs go into the video description.
Everything is failure-tolerant: no sources -> the caller runs the topic as a normal mechanic (no story claims).
"""
from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from urllib.parse import quote

import requests

_UA = {"User-Agent": "yt-autopilot/4.0 (educational finance explainers; contact via GitHub shorbh/yt-autopilot)"}
_STOP = set("how why what is are the a an of to in on for do does did so much many make makes money cost costs expensive "
            "explained vs versus work works actually really still per year month".split())
_TAG = re.compile(r"<[^>]+>")


def subject_of(query: str) -> str:
    """'how does venmo make money' -> 'venmo'; 'why is rent so expensive' -> 'rent'."""
    words = [w for w in re.findall(r"[a-z0-9$%.'-]+", query.lower()) if w not in _STOP]
    return " ".join(words[:4]) or query


def wikipedia(subject: str) -> dict | None:
    try:
        r = requests.get("https://en.wikipedia.org/w/api.php", headers=_UA, timeout=12,
                         params={"action": "query", "list": "search", "srsearch": subject, "format": "json", "srlimit": 1})
        r.raise_for_status()
        hits = r.json().get("query", {}).get("search", [])
        if not hits:
            return None
        title = hits[0]["title"]
        s = requests.get(f"https://en.wikipedia.org/api/rest_v1/page/summary/{quote(title)}", headers=_UA, timeout=12)
        s.raise_for_status()
        summ = s.json()
        # a bit more than the lead: plain-text extract of the first ~2,500 chars of the article
        e = requests.get("https://en.wikipedia.org/w/api.php", headers=_UA, timeout=12,
                         params={"action": "query", "prop": "extracts", "explaintext": 1, "exchars": 1200,   # API max
                                 "titles": title, "format": "json"})
        pages = e.json().get("query", {}).get("pages", {}) if e.ok else {}
        body = next(iter(pages.values()), {}).get("extract", "") if pages else ""
        text = (summ.get("extract", "") + "\n" + body).strip()
        return {"title": title, "url": summ.get("content_urls", {}).get("desktop", {}).get("page", f"https://en.wikipedia.org/wiki/{quote(title)}"),
                "text": text[:3000]}
    except (requests.RequestException, ValueError, KeyError):
        return None


def news(subject: str, limit: int = 4) -> list[dict]:
    url = f"https://news.google.com/rss/search?q={quote(subject)}+when:30d&hl=en-US&gl=US&ceid=US:en"
    try:
        r = requests.get(url, headers=_UA, timeout=12)
        r.raise_for_status()
        root = ET.fromstring(r.content)
    except (requests.RequestException, ET.ParseError):
        return []
    out = []
    for item in root.iter("item"):
        title = html.unescape(_TAG.sub("", item.findtext("title") or "")).strip()
        desc = html.unescape(_TAG.sub(" ", item.findtext("description") or "")).strip()
        link = (item.findtext("link") or "").strip()
        if title:
            out.append({"title": title, "snippet": re.sub(r"\s+", " ", desc)[:300], "url": link})
        if len(out) >= limit:
            break
    return out


def gather(query: str) -> dict:
    """{'subject', 'sources': [{title,url,text}], 'block': str for the prompt}. 'sources' may be empty."""
    subj = subject_of(query)
    sources: list[dict] = []
    w = wikipedia(subj)
    if w:
        sources.append(w)
    for n in news(subj):
        sources.append({"title": n["title"], "url": n["url"], "text": n["snippet"]})
    block = ""
    if sources:
        parts = [f"[{i + 1}] {s['title']} — {s['url']}\n{s['text']}" for i, s in enumerate(sources)]
        block = ("\nSOURCES (the ONLY facts, names, dates and figures you may state; cite like [1]; where you calculate, "
                 "show the arithmetic from these numbers; if a figure you need is absent, say so or use a clearly labelled "
                 "round assumption):\n" + "\n\n".join(parts) + "\n")
    print(f"[ground] '{subj}': {len(sources)} sources" + ("" if sources else " — running as a plain mechanic topic"))
    return {"subject": subj, "sources": sources, "block": block}
