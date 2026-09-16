"""Tiny JSON state store (committed to the repo by the GitHub Action)."""
import json
from datetime import datetime, timezone
from .config import DATA

PUBLISHED = DATA / "published.json"
PERF = DATA / "performance.json"


def _load(path, default):
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return default


def _save(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)


def load_published() -> list[dict]:
    return _load(PUBLISHED, [])


def record_published(entry: dict) -> None:
    items = load_published()
    entry["recorded_at"] = datetime.now(timezone.utc).isoformat()
    items.append(entry)
    _save(PUBLISHED, items)


def load_performance() -> dict:
    return _load(PERF, {"category_scores": {}, "format_scores": {}, "updated_at": None})


def save_performance(perf: dict) -> None:
    perf["updated_at"] = datetime.now(timezone.utc).isoformat()
    _save(PERF, perf)
