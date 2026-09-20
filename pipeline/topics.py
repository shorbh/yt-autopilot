"""Topic selection: never repeat, rotate formats (anti-'sameness' for YouTube policy),
and weight toward categories/formats that analytics show are winning."""
import json
import random
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


def pick_topic(seed: int | None = None, cfg: dict | None = None, forced: str | None = None) -> dict:
    """Trend first (if something in this week's news fits the channel), evergreen bank otherwise.
    `forced` skips both (used by --topic / workflow_dispatch) and works even when the bank is exhausted.
    Returns {category, topic, format, source: 'trend'|'bank'|'forced', news_hook?}."""
    if seed is not None:
        random.seed(seed)
    bank = load_bank()
    published = load_published()
    used_titles = {p.get("topic") for p in published}
    perf = load_performance()

    recent_formats = [p.get("format") for p in published[-3:]]
    formats = [f for f in bank["formats"] if f not in recent_formats] or bank["formats"]
    fmt = _weighted_choice(formats, perf.get("format_scores", {}))

    if forced:
        cats = list(bank["categories"])
        return {"category": _weighted_choice(cats, perf.get("category_scores", {})), "topic": forced, "format": fmt, "source": "forced"}

    if cfg is not None and not _trend_recently(published, int(cfg.get("topics", {}).get("trend_max_per_days", 7))):
        from .trends import scout
        hit = scout(cfg, list(bank["categories"]), [str(t) for t in used_titles if t])
        if hit:
            return {"category": hit["category"], "topic": hit["topic"], "format": fmt, "source": "trend",
                    "news_hook": hit["news_hook"], "headline": hit["headline"]}

    categories = {c: [t for t in ts if t not in used_titles] for c, ts in bank["categories"].items()}
    categories = {c: ts for c, ts in categories.items() if ts}
    if not categories:
        raise RuntimeError("Topic bank exhausted. Run `python run_weekly_review.py --refill` to generate new topics.")

    category = _weighted_choice(list(categories), perf.get("category_scores", {}))
    topic = random.choice(categories[category])
    return {"category": category, "topic": topic, "format": fmt, "source": "bank"}


def add_topics(category: str, new_titles: list[str]) -> int:
    bank = load_bank()
    existing = {t for ts in bank["categories"].values() for t in ts}
    fresh = [t for t in new_titles if t not in existing]
    bank["categories"].setdefault(category, []).extend(fresh)
    with open(DATA / "topics.json", "w", encoding="utf-8") as f:
        json.dump(bank, f, indent=2, ensure_ascii=False)
    return len(fresh)
