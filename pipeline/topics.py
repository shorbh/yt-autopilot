"""Topic selection: never repeat, rotate formats (anti-'sameness' for YouTube policy),
and weight toward categories/formats that analytics show are winning."""
import json
import random
import re
from datetime import datetime, timedelta, timezone

from .config import DATA
from .state import load_published, load_performance


def load_bank() -> dict:
    with open(DATA / "topics.json", "r", encoding="utf-8") as f:
        return json.load(f)


def _weighted_choice(items: list[str], scores: dict, floor: float = 0.5) -> str:
    # score = relative performance (1.0 = average). Floor keeps exploration alive.
    weights = [max(floor, float(scores.get(i, 1.0))) for i in items]
    return random.choices(items, weights=weights, k=1)[0]


def _trend_recently(published: list[dict], days: int) -> bool:
    """True if a trend-sourced long video was recorded within `days` — then this run stays evergreen, so at
    2 videos/week at most one rides the news."""
    if days <= 0:
        return False
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    for p in published:
        if p.get("kind") == "long" and p.get("source") == "trend" and p.get("recorded_at"):
            try:
                if datetime.fromisoformat(p["recorded_at"]) > cutoff:
                    print(f"[trends] a trend topic ran on {p['recorded_at'][:10]} (< {days}d ago); this run uses the evergreen bank")
                    return True
            except ValueError:
                continue
    return False


# ------------------------------------------------------------------ semantic de-duplication

_STOP = set("a an the of to in on for and or vs versus with your you how why what when is are does do it its this that "
            "than from by at as be can should will actually really explained guide math rule rules money".split())


def _terms(text: str) -> set[str]:
    toks = re.findall(r"[a-z0-9$%.]+", str(text).lower().replace(",", ""))
    return {t.rstrip(".") for t in toks if t not in _STOP and len(t) > 1}


def similar(a: str, b: str) -> bool:
    """True when two topics are about the same thing even if worded differently. Run #10 repeated the 7% mortgage
    video because the scout rephrased it as 'rent vs buy at 7%' and only exact matches were checked.
    Rule: Jaccard >= 0.4 on content terms, OR >= 3 shared content terms that include a number/percentage."""
    ta, tb = _terms(a), _terms(b)
    if not ta or not tb:
        return False
    shared = ta & tb
    jac = len(shared) / len(ta | tb)
    has_num = any(re.search(r"\d", t) for t in shared)
    return jac >= 0.4 or (len(shared) >= 3 and has_num)


def covered_topics(published: list[dict] | None = None) -> list[str]:
    pub = published if published is not None else load_published()
    out: list[str] = []
    for p in pub:
        for k in ("topic", "title", "headline", "query"):
            v = p.get(k)
            if v and v not in out:
                out.append(str(v))
    return out


def is_covered(candidate: str, covered: list[str]) -> bool:
    return any(similar(candidate, c) for c in covered)


# ------------------------------------------------------------------ picking

def _story_turn(cfg: dict, published: list[dict]) -> bool:
    """Should this run be a money STORY (broad, discovery) or a MECHANIC (narrow, deep)? Keeps the realised share of
    recent long videos near topics.story_share (default 0.6), using the weekly review's kind_scores when present."""
    share = float(cfg.get("topics", {}).get("story_share", 0.6))
    ks = load_performance().get("kind_scores") or {}
    if ks.get("story") and ks.get("mechanic"):   # data-driven drift toward what earns views per impression
        share = max(0.3, min(0.8, share * (ks["story"] / ks["mechanic"]) ** 0.5))
    recent = [p for p in published if p.get("kind") == "long"][-10:]
    if not recent:
        return random.random() < share
    done = sum(1 for p in recent if p.get("topic_kind") == "story") / len(recent)
    return done < share


def pick_topic(seed: int | None = None, cfg: dict | None = None, forced: str | None = None) -> dict:
    """Order: forced -> trend (capped per week) -> demand-validated search query -> evergreen bank.
    Returns {category, topic, format, source, kind ('story'|'mechanic'), query?, news_hook?, headline?, sources?}."""
    if seed is not None:
        random.seed(seed)
    bank = load_bank()
    published = load_published()
    perf = load_performance()
    covered = covered_topics(published)
    cats = list(bank["categories"])
    tcfg = (cfg or {}).get("topics", {})

    recent_formats = [p.get("format") for p in published[-3:]]
    formats = [f for f in bank["formats"] if f not in recent_formats] or bank["formats"]
    fmt = _weighted_choice(formats, perf.get("format_scores", {}))
    story_formats = list(tcfg.get("story_formats") or [])

    def _finish(d: dict) -> dict:
        kind = d.get("kind") or ("story" if cfg and _story_turn(cfg, published) and story_formats else "mechanic")
        d["kind"] = kind
        if kind == "story" and story_formats:
            d["format"] = random.choice([f for f in story_formats if f not in recent_formats] or story_formats)
        else:
            d.setdefault("format", fmt)
        if cfg is not None and kind == "story":
            from .ground import gather
            g = gather(d.get("query") or d["topic"])
            d["sources"] = g["sources"]
            d["source_block"] = g["block"]
            if not g["sources"]:
                d["kind"] = "mechanic"           # no facts to ground on -> do not tell a story
                d["format"] = fmt
        return d

    if forced:
        return _finish({"category": _weighted_choice(cats, perf.get("category_scores", {})), "topic": forced, "source": "forced"})

    if cfg is not None and not _trend_recently(published, int(tcfg.get("trend_max_per_days", 7))):
        from .trends import scout
        hit = scout(cfg, cats, covered)
        if hit and not is_covered(hit["topic"], covered) and not is_covered(hit.get("headline", ""), covered):
            return _finish({"category": hit["category"], "topic": hit["topic"], "source": "trend", "kind": "story",
                            "news_hook": hit["news_hook"], "headline": hit["headline"], "query": hit["topic"]})
        elif hit:
            print(f"[trends] '{hit['topic'][:70]}' is a re-phrasing of a covered topic; skipped")

    if cfg is not None and tcfg.get("demand", True):
        from .demand import pick as demand_pick
        hit = demand_pick(cfg, cats, covered)
        if hit and not is_covered(hit["query"], covered):
            return _finish({"category": hit["category"], "topic": hit["query"], "query": hit["query"], "source": "demand",
                            "kind": hit["kind"], "competition": hit.get("competition")})

    categories = {c: [t for t in ts if not is_covered(t, covered)] for c, ts in bank["categories"].items()}
    categories = {c: ts for c, ts in categories.items() if ts}
    if not categories:
        raise RuntimeError("Topic bank exhausted. Run `python run_weekly_review.py --refill` to generate new topics.")
    category = _weighted_choice(list(categories), perf.get("category_scores", {}))
    topic = random.choice(categories[category])
    return _finish({"category": category, "topic": topic, "source": "bank"})


def add_topics(category: str, new_titles: list[str]) -> int:
    """Append new topics, skipping anything semantically close to an existing topic in ANY category
    (the Sep-27 refill produced '48-hour rule' and '72-hour cart delay' in two categories)."""
    bank = load_bank()
    existing = [t for ts in bank["categories"].values() for t in ts] + covered_topics()
    fresh: list[str] = []
    for t in new_titles:
        if not is_covered(t, existing) and not is_covered(t, fresh):
            fresh.append(t)
    bank["categories"].setdefault(category, []).extend(fresh)
    with open(DATA / "topics.json", "w", encoding="utf-8") as f:
        json.dump(bank, f, indent=2, ensure_ascii=False)
    return len(fresh)
