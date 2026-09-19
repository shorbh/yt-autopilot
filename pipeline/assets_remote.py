"""Free remote visual assets, all cached per run and all failure-tolerant (return None -> caller falls back).

* Lucide icons (ISC licence): SVG from jsDelivr, rasterised with cairosvg. ~1,600 names.
* Characters: DiceBear "Open Peeps" (Pablo Stanley, CC0) — hand-drawn half-body people via the free
  DiceBear HTTP API (SVG, no key). Hairstyle/facial-hair sets are chosen from the stated gender so a
  "Sarah" is never drawn as a man; expression is chosen from a mood word.
"""
from __future__ import annotations

import hashlib
import io
import re
import threading
import urllib.parse

import requests
from PIL import Image

_ICON_CACHE: dict[tuple, Image.Image | None] = {}
_SVG_CACHE: dict[str, str | None] = {}   # one download per icon name / peep spec
_LOCK = threading.Lock()
_LUCIDE_URL = "https://cdn.jsdelivr.net/npm/lucide-static@latest/icons/{name}.svg"
_PEEPS_URL = "https://api.dicebear.com/9.x/open-peeps/svg"

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


# ------------------------------------------------------------------ characters (Open Peeps)

_HEADS = {
    "female": ["long", "longBangs", "longCurly", "bun", "buns", "bangs", "mediumBangs", "mediumStraight", "longAfro", "twists", "hijab"],
    "male": ["short1", "short2", "short3", "short4", "short5", "flatTop", "pomp", "shaved1", "noHair1", "dreads1", "mohawk"],
    "neutral": ["short2", "medium1", "medium2", "bangs2", "twists2", "shaved3"],
}
_FACES = {
    "neutral": "calm", "happy": "smile", "excited": "smileBig", "proud": "smileBig",
    "worried": "concerned", "sad": "solemn", "stressed": "tired", "shocked": "awe", "surprised": "awe",
    "scared": "fear", "angry": "veryAngry", "thinking": "driven", "serious": "serious", "explaining": "explaining",
    "confused": "suspicious", "tired": "tired", "laughing": "smileLOL",
}
_SKINS = ["ffdbb4", "edb98a", "d08b5b", "ae5d29", "694d3d"]
_CLOTHES = ["3ddc97", "ffd166", "8fb4ff", "e879a5", "9be7d8"]


def _pick(seq: list[str], seed: str) -> str:
    h = int(hashlib.md5(seed.encode("utf-8")).hexdigest(), 16)
    return seq[h % len(seq)]


def peep(name: str, gender: str = "neutral", mood: str = "neutral", size: int = 600) -> Image.Image | None:
    """Hand-drawn character (Open Peeps). Same name -> same person every time within and across videos."""
    if not HAVE_CAIRO:
        return None
    gender = (gender or "neutral").lower()
    gender = "female" if gender.startswith(("f", "w", "g")) else ("male" if gender.startswith(("m", "b")) else "neutral")
    face = _FACES.get((mood or "neutral").lower(), "calm")
    seed = (name or "person").strip().lower()
    params = {
        "seed": seed,
        "head": _pick(_HEADS[gender], seed),
        "face": face,
        "skinColor": _pick(_SKINS, seed + "skin"),
        "clothingColor": _pick(_CLOTHES, seed + "cloth"),
        "facialHairProbability": 0 if gender != "male" else 35,
        "accessoriesProbability": 15,
        "maskProbability": 0,      # API default is 5% -> a medical mask would hide the mood expression
        # no `size` param and no backgroundColor: the SVG is fetched ONCE per person and rasterised
        # at any pixel size by cairosvg (keeps the network cache warm across animated size changes).
    }
    url = _PEEPS_URL + "?" + urllib.parse.urlencode(params)
    key = ("peep", url, size)
    with _LOCK:
        if key in _ICON_CACHE:
            return _ICON_CACHE[key]
    svg = _fetch_svg("peep:" + url, url)
    img = _rasterise(svg, size, size) if svg else None
    with _LOCK:
        _ICON_CACHE[key] = img
    return img
