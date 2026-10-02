"""Demand-validated topics: what are people actually typing into YouTube this week?

1. YouTube's own search autocomplete (free, no key, `suggestqueries.google.com ... ds=yt`) for the seed
   phrases in config.yaml -> ~200 real queries.
2. Drop anything already covered (semantic match, see topics.similar) and anything not question/explainer-shaped.
3. The LLM ranks the rest for channel fit + evergreen-ness + "has a number in it" and returns the top few.
4. Competition check (YouTube Data API search.list, 100 quota units each, capped at 3): how many of the
   top-10 results come from channels under 50k subscribers? More = an opening a new channel can win.
Returns the best query verbatim — it becomes the title's wording, because that is how search finds it.
Any failure -> None, and the caller falls back to the evergreen bank.
"""
from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote_plus

import requests

from .llm import ask_json

_AC = "https://suggestqueries.google.com/complete/search?client=firefox&ds=yt&hl=en&gl=us&q={q}"
_UA = {"User-Agent": "Mozilla/5.0 (compatible; yt-autopilot/4.0)"}
_QUESTION = re.compile(r"^(how|why|what|when|should|is|can|do|does|which)\b|\b(vs|versus|explained|calculator|per month|a year|salary)\b", re.I)


def autocomplete(seed: str, limit: int = 10) -> list[str]:
    try:
        r = requests.get(_AC.format(q=quote_plus(seed)), headers=_UA, timeout=10)
        r.raise_for_status()
        data = json.loads(r.text)
        return [str(s) for s in data[1]][:limit] if isinstance(data, list) and len(data) > 1 else []
    except (requests.RequestException, ValueError, IndexError):
        return []


def harvest(cfg: dict, covered: list[str]) -> list[str]:
    """Real queries people type, minus covered topics, question-shaped first."""
    from .topics import similar
    seeds = list(cfg.get("topics", {}).get("demand_seeds") or [])
    if not seeds:
        return []
    with ThreadPoolExecutor(max_workers=8) as pool:
        batches = list(pool.map(autocomplete, seeds))
    seen, out = set(), []
    for batch in batches:
        for q in batch:
            k = q.lower().strip()
            if k in seen or len(k.split()) < 3 or any(similar(k, c) for c in covered):
                continue
            seen.add(k)
            out.append(q)
    out.sort(key=lambda q: 0 if _QUESTION.search(q) else 1)   # stable: question-shaped queries first
    return out[:220]


def competition(query: str) -> dict | None:
    """Share of top-10 search results from small channels (< 50k subs). None when the API is unavailable."""
    try:
        from .upload import youtube_client
        yt = youtube_client()
        res = yt.search().list(part="snippet", q=query, type="video", maxResults=10, relevanceLanguage="en", regionCode="US").execute()
        ch_ids = list({it["snippet"]["channelId"] for it in res.get("items", [])})
        if not ch_ids:
            return {"results": 0, "small_share": 1.0}
        chans = yt.channels().list(part="statistics", id=",".join(ch_ids)).execute()
        subs = {c["id"]: int(c.get("statistics", {}).get("subscriberCount", 0) or 0) for c in chans.get("items", [])}
        small = sum(1 for it in res.get("items", []) if subs.get(it["snippet"]["channelId"], 0) < 50_000)
        return {"results": len(res.get("items", [])), "small_share": round(small / max(1, len(res.get("items", []))), 2)}
    except Exception as e:  # noqa: BLE001 - no OAuth locally, quota, network
        print(f"[demand] competition check skipped ({str(e)[:80]})")
        return None


SYSTEM = """You are the programming editor of a YouTube channel that explains how money works with worked numeric examples.
You get real search queries people typed into YouTube. Pick the BEST video topics for this channel. A good pick:
(1) is a question or comparison the channel can answer with a concrete calculation; (2) stays useful for years;
(3) is specific (has or implies a number, a product, a decision), not vague ('how to be rich'); (4) is not already covered.
Return the query text VERBATIM — its wording is what search matches. Return ONLY JSON."""

SCHEMA = """{"picks": [{"query": "verbatim query", "category": "one of the given categories", "kind": "mechanic | story", "why": "<= 12 words"}]}"""


def pick(cfg: dict, categories: list[str], covered: list[str], n: int = 3) -> dict | None:
    queries = harvest(cfg, covered)
    if len(queries) < 10:
        print(f"[demand] only {len(queries)} candidate queries; skipping")
        return None
    user = (f"Channel: {cfg['channel']['name']} — {cfg['channel']['tagline']}\nCategories: {', '.join(categories)}\n"
            "kind = 'story' when the query is about a company, product, price or event ('why is rent so expensive', "
            "'how does venmo make money'); 'mechanic' when it is about a rule, calculation or personal decision.\n"
            f"Already covered (skip anything similar): {'; '.join(covered[-30:]) or 'nothing'}\n\n"
            "Queries:\n" + "\n".join(f"- {q}" for q in queries) + f"\n\nReturn the top {n} as JSON exactly like:\n{SCHEMA}")
    try:
        data = ask_json(cfg, SYSTEM, user, temperature=0.3)
        raw = data.get("picks", []) if isinstance(data, dict) else data   # some models return the bare list
        picks = [p for p in (raw or []) if isinstance(p, dict) and p.get("query")][:n]
    except Exception as e:  # noqa: BLE001
        print(f"[demand] LLM ranking failed ({str(e)[:100]})")
        return None
    if not picks:
        return None
    # competition: prefer queries where small channels already rank (capped API spend: n calls)
    for p in picks:
        p["competition"] = competition(str(p["query"]))
    picks.sort(key=lambda p: -(p["competition"]["small_share"] if p.get("competition") else 0.5))
    best = picks[0]
    cat = str(best.get("category") or "")
    comp = best.get("competition")
    print(f"[demand] '{best['query']}'  ({best.get('kind', 'mechanic')}; small-channel share in top 10: "
          f"{comp['small_share'] if comp else 'n/a'})")
    return {"query": str(best["query"]).strip(), "category": cat if cat in categories else categories[0],
            "kind": "story" if str(best.get("kind", "")).lower().startswith("s") else "mechanic",
            "competition": comp}
