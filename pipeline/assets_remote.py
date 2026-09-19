"""Free remote visual assets, all cached per run and all failure-tolerant (return None -> caller falls back).

* Lucide icons (ISC licence): SVG from jsDelivr, rasterised with cairosvg. ~1,600 names.

People are NOT drawn here any more. v3.2 replaced the hand-drawn Open Peeps cartoons with real
Pexels portrait photos (see render._BrollCache.person) plus typographic initial avatars in
compare cards (motion._initial_circle). Both look premium next to the photo b-roll; cartoons did not.
"""
from __future__ import annotations

import io
import re
import threading

import requests
from PIL import Image

_ICON_CACHE: dict[tuple, Image.Image | None] = {}
_SVG_CACHE: dict[str, str | None] = {}   # one download per icon name
_LOCK = threading.Lock()
_LUCIDE_URL = "https://cdn.jsdelivr.net/npm/lucide-static@latest/icons/{name}.svg"

try:
    import cairosvg  # type: ignore
    HAVE_CAIRO = True
except Exception:  # noqa: BLE001 - missing native cairo lib on some machines
    cairosvg = None
    HAVE_CAIRO = False


def _fetch_svg(key: str, url: str) -> str | None:
    with _LOCK:
        if key in _SVG_CACHE:
            return _SVG_CACHE[key]
    svg = None
    try:
        r = requests.get(url, timeout=20)
        if r.status_code == 200 and b"<svg" in r.content:
            svg = r.content.decode("utf-8")
        else:
            print(f"[warn] asset HTTP {r.status_code}: {url[:90]}")
    except Exception as e:  # noqa: BLE001
        print(f"[warn] asset fetch failed: {str(e)[:120]}")
    with _LOCK:
        _SVG_CACHE[key] = svg
    return svg


def _rasterise(svg: str, w: int, h: int) -> Image.Image | None:
    if not HAVE_CAIRO:
        return None
    try:
        png = cairosvg.svg2png(bytestring=svg.encode("utf-8"), output_width=w, output_height=h)
        return Image.open(io.BytesIO(png)).convert("RGBA")
    except Exception as e:  # noqa: BLE001
        print(f"[warn] svg rasterise failed: {str(e)[:120]}")
        return None


# ------------------------------------------------------------------ icons

def icon(name: str, size: int, color: tuple[int, int, int]) -> Image.Image | None:
    """Return an RGBA Lucide icon of `size` px, or None if unavailable."""
    if not HAVE_CAIRO or not name:
        return None
    name = re.sub(r"[^a-z0-9-]", "", name.lower().replace("_", "-"))
    key = ("icon", name, size, color)
    with _LOCK:
        if key in _ICON_CACHE:
            return _ICON_CACHE[key]
    img = None
    svg = _fetch_svg("lucide:" + name, _LUCIDE_URL.format(name=name))
    if svg:
        hexcol = "#%02x%02x%02x" % color
        svg = svg.replace('stroke="currentColor"', f'stroke="{hexcol}"').replace('fill="currentColor"', f'fill="{hexcol}"')
        svg = svg.replace('stroke-width="2"', 'stroke-width="1.75"')
        img = _rasterise(svg, size, size)
    with _LOCK:
        _ICON_CACHE[key] = img
    return img


# ------------------------------------------------------------------ people -> stock-photo queries

_MOOD_WORDS = {
    "neutral": "portrait", "happy": "smiling", "excited": "celebrating", "proud": "confident",
    "worried": "worried", "sad": "sad", "stressed": "stressed", "shocked": "surprised", "surprised": "surprised",
    "scared": "anxious", "angry": "frustrated", "thinking": "thinking", "serious": "serious",
    "explaining": "talking", "confused": "confused", "tired": "tired", "laughing": "laughing",
}


def person_query(gender: str | None, mood: str | None) -> str:
    """'female' + 'worried' -> 'worried woman portrait' (a Pexels search that reliably returns one person)."""
    g = (gender or "neutral").lower()
    who = "woman" if g.startswith(("f", "w", "g")) else ("man" if g.startswith(("m", "b")) else "person")
    m = _MOOD_WORDS.get((mood or "neutral").lower(), "portrait")
    return f"{m} {who} portrait" if m != "portrait" else f"{who} portrait office"
