"""Weekly analytics review: score topics, refill the topic bank, write a markdown report.

Usage:
  python run_weekly_review.py            # needs YT_* secrets
  python run_weekly_review.py --refill   # only refill topic bank (needs LLM key only)
"""
import argparse
import sys
from pathlib import Path

from pipeline.analytics import channel_totals, fetch_video_stats, refill_topics, score_and_save, weekly_report
from pipeline.config import ROOT, load_config
from pipeline.state import load_performance


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--refill", action="store_true")
    args = ap.parse_args()
    cfg = load_config()

    if args.refill:
        n = refill_topics(cfg, load_performance())
        print(f"Added {n} topics.")
        return 0

    stats = fetch_video_stats()
    perf = score_and_save(stats, cfg) or load_performance()
    totals = channel_totals()
    added = refill_topics(cfg, perf)
    report = weekly_report(cfg, stats, perf, totals, added)
    out = ROOT / "reports"
    out.mkdir(exist_ok=True)
    (out / "latest.md").write_text(report, encoding="utf-8")
    print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
