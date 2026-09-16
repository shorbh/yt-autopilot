"""Topic selection: never repeat, rotate formats (anti-'sameness' for YouTube policy),
and weight toward categories/formats that analytics show are winning."""
import json
import random
from .config import DATA
from .state import load_published, load_performance


def load_bank() -> dict:
    with open(DATA / "topics.json", "r", encoding="utf-8") as f:
        return json.load(f)


def _weighted_choice(items: list[str], scores: dict, floor: float = 0.5) -> str:
    # score = relative performance (1.0 = average). Floor keeps exploration alive.
    weights = [max(floor, float(scores.get(i, 1.0))) for i in items]
    return random.choices(items, weights=weights, k=1)[0]


def pick_topic(seed: int | None = None) -> dict:
    if seed is not None:
        random.seed(seed)
    bank = load_bank()
    used_titles = {p.get("topic") for p in load_published()}
    perf = load_performance()

    categories = {c: [t for t in ts if t not in used_titles] for c, ts in bank["categories"].items()}
    categories = {c: ts for c, ts in categories.items() if ts}
    if not categories:
        raise RuntimeError("Topic bank exhausted. Run `python run_weekly_review.py --refill` to generate new topics.")

    category = _weighted_choice(list(categories), perf.get("category_scores", {}))
    topic = random.choice(categories[category])

    recent_formats = [p.get("format") for p in load_published()[-3:]]
    formats = [f for f in bank["formats"] if f not in recent_formats] or bank["formats"]
    fmt = _weighted_choice(formats, perf.get("format_scores", {}))

    return {"category": category, "topic": topic, "format": fmt}


def add_topics(category: str, new_titles: list[str]) -> int:
    bank = load_bank()
    existing = {t for ts in bank["categories"].values() for t in ts}
    fresh = [t for t in new_titles if t not in existing]
    bank["categories"].setdefault(category, []).extend(fresh)
    with open(DATA / "topics.json", "w", encoding="utf-8") as f:
        json.dump(bank, f, indent=2, ensure_ascii=False)
    return len(fresh)
