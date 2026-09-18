"""Free remote visual assets, all cached per run and all failure-tolerant (return None -> caller falls back).

* Lucide icons (ISC licence): SVG from jsDelivr, rasterised with cairosvg. ~1,600 names.
* Sketch illustrations: Pollinations.ai (Flux), keyless. Anonymous limit ~1 request / 15 s, so a
  single background thread fetches them sequentially while the rest of the video renders.
"""
from __future__ import annotations

import io
import re
import threading
import time
import urllib.parse
from pathlib import Path

import requests
from PIL import Image

from .config import env

_ICON_CACHE: dict[tuple, Image.Image | None] = {}
_SVG_CACHE: dict[str, str | None] = {}   # one download per icon name; sizes/colours rasterise from it
_ICON_LOCK = threading.Lock()
_LUCIDE_URL = "https://cdn.jsdelivr.net/npm/lucide-static@latest/icons/{name}.svg"

try:
    import cairosvg  # type: ignore
    HAVE_CAIRO = True
except Exception:  # noqa: BLE001 - missing native cairo lib on some machines
    cairosvg = None
    HAVE_CAIRO = False


def icon(name: str, size: int, color: tuple[int, int, int]) -> Image.Image | None:
    """Return an RGBA icon image of `size` px, or None if unavailable."""
    if not HAVE_CAIRO or not name:
        return None
    name = re.sub(r"[^a-z0-9-]", "", name.lower().replace("_", "-"))
    key = (name, size, color)
    with _ICON_LOCK:
        if key in _ICON_CACHE:
            return _ICON_CACHE[key]
    img = None
    try:
        with _ICON_LOCK:
            have_svg = name in _SVG_CACHE
            svg = _SVG_CACHE.get(name)
        if not have_svg:
            r = requests.get(_LUCIDE_URL.format(name=name), timeout=15)
            svg = r.content.decode("utf-8") if r.status_code == 200 and b"<svg" in r.content else None
            with _ICON_LOCK:
                _SVG_CACHE[name] = svg
        if svg:
            hexcol = "#%02x%02x%02x" % color
            svg = svg.replace('stroke="currentColor"', f'stroke="{hexcol}"').replace('fill="currentColor"', f'fill="{hexcol}"')
            svg = svg.replace('stroke-width="2"', 'stroke-width="1.75"')
            png = cairosvg.svg2png(bytestring=svg.encode("utf-8"), output_width=size, output_height=size)
            img = Image.open(io.BytesIO(png)).convert("RGBA")
    except Exception as e:  # noqa: BLE001
        print(f"[warn] icon '{name}' unavailable: {str(e)[:120]}")
    with _ICON_LOCK:
        _ICON_CACHE[key] = img
    return img


# ------------------------------------------------------------------ illustrations

_ILLUS_LOCK = threading.Lock()
_ILLUS_LAST = 0.0
_ILLUS_FAILS = 0           # consecutive failures; circuit breaker below
ILLUS_MAX_FAILS = 2        # after this many in a row the layer is switched off for the run
ILLUS_MIN_INTERVAL = 16.0  # seconds between anonymous requests

STYLE_SUFFIX = (", minimal single-line sketch illustration, thin white ink lines on a solid dark navy background "
                "(#0B1020), flat, clean, no shading, no text, no letters, no watermark, centered, generous margins")


def _pollinations_urls(prompt: str, w: int, h: int, seed: int) -> list[str]:
    q = urllib.parse.quote(prompt + STYLE_SUFFIX)
    params = f"width={w}&height={h}&nologo=true&seed={seed}&model=flux"
    return [
        f"https://image.pollinations.ai/prompt/{q}?{params}",
        f"https://gen.pollinations.ai/image/{q}?{params}",
    ]


def illustration(prompt: str, out: Path, w: int = 1024, h: int = 1024, timeout: int = 60) -> Path | None:
    """Fetch one sketch illustration. Serialised + rate-limited across threads. Returns path or None.
    Circuit breaker: after ILLUS_MAX_FAILS consecutive failures the layer is disabled for the run,
    so a down service costs at most ~2 minutes instead of 2 minutes per beat."""
    global _ILLUS_LAST, _ILLUS_FAILS
    if env("ILLUSTRATIONS", "1") in ("0", "false", "off"):
        return None
    if out.exists() and out.stat().st_size > 5000:
        return out
    with _ILLUS_LOCK:
        if _ILLUS_FAILS >= ILLUS_MAX_FAILS:
            return None
        wait = ILLUS_MIN_INTERVAL - (time.time() - _ILLUS_LAST)
        if wait > 0:
            time.sleep(wait)
        _ILLUS_LAST = time.time()
        seed = abs(hash(prompt)) % 100000
        for url in _pollinations_urls(prompt, w, h, seed):
            try:
                r = requests.get(url, timeout=timeout)
                if r.status_code == 200 and r.headers.get("content-type", "").startswith("image") and len(r.content) > 5000:
                    img = Image.open(io.BytesIO(r.content)).convert("RGB")
                    img.save(out, "PNG")
                    _ILLUS_FAILS = 0
                    return out
                print(f"[warn] illustration HTTP {r.status_code} from {url.split('/')[2]}")
            except Exception as e:  # noqa: BLE001
                print(f"[warn] illustration failed ({url.split('/')[2]}): {str(e)[:120]}")
        _ILLUS_FAILS += 1
        if _ILLUS_FAILS >= ILLUS_MAX_FAILS:
            print("[warn] illustration service unreliable — sketch layer disabled for this run")
        return None
