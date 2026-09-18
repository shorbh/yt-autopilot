"""Smoke test WITHOUT any API keys: renders a ~40-second sample video + 1 Short from a canned
script using free edge-tts and generated visuals. Proves ffmpeg/fonts/TTS all work on this machine.
Without an LLM key the storyboard degrades to default (callout) beats, which still exercises the
beat engine, karaoke captions and chapter cards.

  python selftest.py
Output: build/selftest/long.mp4, build/selftest/short_0/short.mp4, build/selftest/thumbnail.jpg
"""
import shutil
import sys

from pipeline.config import BUILD, load_config
from pipeline.render import build_long_video, build_shorts, thumbnail

SCRIPT = {
    "title": "What a 1% Fee Really Costs You",
    "thumbnail_text": "1% = $100,000",
    "thumbnail_query": "stack of coins desk",
    "description": "A quick demo render.",
    "tags": ["finance", "index funds", "fees"],
    "chart": {
        "type": "line", "title": "Same $500/month, 30 years", "x_label": "Years", "y_prefix": "$",
        "series": [
            {"name": "0.1% fee", "points": [[0, 0], [5, 34000], [10, 79000], [15, 138000], [20, 216000], [25, 318000], [30, 452000]]},
            {"name": "1.1% fee", "points": [[0, 0], [5, 33000], [10, 74000], [15, 124000], [20, 185000], [25, 260000], [30, 352000]]},
        ],
    },
    "sections": [
        {"id": "hook", "heading": "The 1% lie", "visual_query": "calculator desk", "short_worthy": True, "stat": None,
         "narration": "A one percent fee doesn't cost you one percent. On a five hundred dollar monthly investment over thirty years, it quietly removes about one hundred thousand dollars. Here's the math, and the fix."},
        {"id": "s1", "heading": "Same money, two funds", "visual_query": "stock chart", "stat": None, "short_worthy": False,
         "narration": "Imagine two identical investors. Both put in five hundred dollars a month. Both earn seven percent before fees. One pays a tenth of a percent. The other pays one point one percent. After thirty years the gap between them is not one percent. It's over twenty percent of the final balance."},
        {"id": "close", "heading": "The rule", "visual_query": "sunrise city", "stat": None, "short_worthy": False,
         "narration": "The rule is simple: fees compound exactly like returns do, just against you. Check the expense ratio before you check anything else. If this saved you a mistake, subscribe."},
    ],
    "shorts": [
        {"hook_title": "1% fee = 20% less money", "visual_query": "coins falling",
         "narration": "Quick one. A one percent fund fee sounds tiny. It isn't. Five hundred dollars a month for thirty years at seven percent gives you about four hundred fifty thousand. Add a one percent fee and you end near three hundred fifty thousand. Same money, same market, one hundred thousand dollars gone. Fees compound against you. Check the expense ratio first. Full breakdown on the channel."}
    ],
}


def main() -> int:
    cfg = load_config()
    cfg["video"]["b_roll"] = "off"      # no Pexels key needed for the smoke test
    cfg["shorts"]["count"] = 1
    wd = BUILD / "selftest"
    shutil.rmtree(wd, ignore_errors=True)
    wd.mkdir(parents=True)
    print("Rendering sample long video (edge-tts + beat engine + ffmpeg)...")
    long = build_long_video(cfg, SCRIPT, wd)
    print(f"  OK  {long['path']}  ({long['duration']:.1f}s)")
    thumbnail(cfg, SCRIPT, wd)
    print("Rendering one Short...")
    shorts = build_shorts(cfg, SCRIPT, wd)
    print(f"  OK  {shorts[0]['path']}  ({shorts[0]['duration']:.1f}s)")
    print("\nSELFTEST PASSED. Open the files in build/selftest/ to check voice, captions and visuals.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # noqa: BLE001
        print("\nSELFTEST FAILED:", e)
        print("Common fixes: install ffmpeg and make sure `ffmpeg -version` works in this terminal; "
              "check internet access (edge-tts needs it).")
        sys.exit(1)
