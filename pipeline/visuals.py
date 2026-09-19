"""Visual assets: generated slides, stat cards, charts (PIL only) and optional free Pexels b-roll."""
from __future__ import annotations

import io
import math
import random
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .config import ROOT, env, hex_to_rgb

_FONT_CANDIDATES = [
    "assets/fonts/Inter-Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/segoeuib.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
]


def font(cfg: dict, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    cands = [cfg["style"].get("font_bold", "")] + _FONT_CANDIDATES
    for c in cands:
        p = Path(c) if Path(c).is_absolute() else ROOT / c
        if p.exists():
            try:
                return ImageFont.truetype(str(p), size)
            except OSError:
                continue
    try:
        return ImageFont.load_default(size=size)  # Pillow >= 10.1 returns a scalable FreeType font
    except TypeError:
        return ImageFont.load_default()  # older Pillow: bitmap font (no anchor support; text may look small)


def gradient(w: int, h: int, c1: str, c2: str, noise: bool = True) -> Image.Image:
    a, b = hex_to_rgb(c1), hex_to_rgb(c2)
    strip = Image.new("RGB", (1, h))
    px = strip.load()
    for y in range(h):
        t = y / max(1, h - 1)
        px[0, y] = tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))
    img = strip.resize((w, h))
    if noise:
        # subtle diagonal glow so slides don't look flat
        glow = Image.new("RGB", (w, h), (0, 0, 0))
        gd = ImageDraw.Draw(glow)
        gd.ellipse([w * 0.55, -h * 0.4, w * 1.3, h * 0.6], fill=(40, 60, 110))
        glow = glow.filter(ImageFilter.GaussianBlur(w // 8))
        img = Image.blend(img, Image.composite(glow, img, glow.convert("L")), 0.5)
    return img


def _draw_text_block(draw, xy, text, fnt, fill, max_width, line_spacing=1.15, stroke=0, stroke_fill=None, anchor_center=False):
    x, y = xy
    # wrap by pixel width
    words, lines, cur = text.split(), [], ""
    for wd in words:
        trial = (cur + " " + wd).strip()
        if draw.textlength(trial, font=fnt) <= max_width or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = wd
    if cur:
        lines.append(cur)
    lh = fnt.size * line_spacing
    for i, line in enumerate(lines):
        lx = x - draw.textlength(line, font=fnt) / 2 if anchor_center else x
        draw.text((lx, y + i * lh), line, font=fnt, fill=fill, stroke_width=stroke, stroke_fill=stroke_fill)
    return len(lines) * lh


def slide(cfg: dict, heading: str, w: int, h: int, out: Path, subtitle: str | None = None) -> Path:
    st = cfg["style"]
    img = gradient(w, h, st["bg_dark"], st["bg_dark2"])
    d = ImageDraw.Draw(img)
    accent = hex_to_rgb(st["accent"])
    d.rectangle([w * 0.08, h * 0.30, w * 0.08 + 14, h * 0.30 + h * 0.20], fill=accent)
    hsize = int(h * 0.085) if w > h else int(h * 0.055)
    used = _draw_text_block(d, (w * 0.11, h * 0.29), heading, font(cfg, hsize), hex_to_rgb(st["text"]), w * 0.8)
    if subtitle:
        _draw_text_block(d, (w * 0.11, h * 0.29 + used + 30), subtitle, font(cfg, int(hsize * 0.5)), accent, w * 0.8)
    img.save(out, "PNG")
    return out


def stat_card(cfg: dict, label: str, value: str, w: int, h: int, out: Path) -> Path:
    st = cfg["style"]
    img = gradient(w, h, st["bg_dark2"], st["bg_dark"])
    d = ImageDraw.Draw(img)
    vsize = int(min(w, h) * (0.22 if w > h else 0.16))
    vf = font(cfg, vsize)
    while d.textlength(value, font=vf) > w * 0.86 and vsize > 40:
        vsize -= 8
        vf = font(cfg, vsize)
    d.text((w / 2, h * 0.44), value, font=vf, fill=hex_to_rgb(st["accent2"]), anchor="mm")
    _draw_text_block(d, (w / 2, h * 0.44 + vsize * 0.7), label, font(cfg, int(vsize * 0.28)), hex_to_rgb(st["text"]), w * 0.8, anchor_center=True)
    img.save(out, "PNG")
    return out


def _nice_step(span: float, target_ticks: int) -> float:
    """Axis step that is 1, 2, 2.5 or 5 x 10^k and gives about `target_ticks` intervals."""
    if span <= 0:
        return 1.0
    raw = span / max(1, target_ticks)
    mag = 10 ** math.floor(math.log10(raw))
    for m in (1, 2, 2.5, 5, 10):
        if m * mag >= raw:
            return m * mag
    return 10 * mag


def _fmt_tick(v: float) -> str:
    return f"{v:,.0f}" if abs(v - round(v)) < 1e-9 else f"{v:,.1f}"


def _coerce_series(raw: list) -> list[dict]:
    series = [
        {**s, "points": [(float(str(p[0]).replace(",", "").lstrip("$")), float(str(p[1]).replace(",", "").lstrip("$"))) for p in s["points"]]}
        for s in raw
    ]
    return [s for s in series if s["points"]]


def chart_image(cfg: dict, chart: dict, w: int, h: int, out: Path) -> Path | None:
    """Simple, clean line/bar chart drawn with PIL (no matplotlib dependency).
    If chart['_fixed_range'] holds the full chart, axes are computed from it so that
    progressive (partial-data) frames keep a stable scale."""
    try:
        # coerce to floats: LLMs sometimes emit "1200" or "$1,200" instead of numbers
        series = _coerce_series(chart["series"])
        full = _coerce_series(chart["_fixed_range"]["series"]) if chart.get("_fixed_range") else series
        pts_all = [p for s in full for p in s["points"]]
        xs = [p[0] for p in pts_all]
        ys = [p[1] for p in pts_all]
        if not xs or max(xs) == min(xs) or not series:
            return None
    except (KeyError, TypeError, IndexError, ValueError, AttributeError):
        return None

    st = cfg["style"]
    img = gradient(w, h, st["bg_dark"], st["bg_dark2"], noise=False)
    d = ImageDraw.Draw(img)
    # top padding clears the lower-third heading strip (motion.TOP_SAFE); title + legend live in 0.17h..0.27h
    # bottom padding clears the burned-in caption band (~0.13h from the bottom) plus the x-axis labels
    pad_l, pad_r, pad_t, pad_b = int(w * 0.12), int(w * 0.06), int(h * 0.29), int(h * 0.24)
    x0, y0, x1, y1 = pad_l, pad_t, w - pad_r, h - pad_b
    ystep = _nice_step((max(ys) - min(0, min(ys))) or 1, 5)
    ymin = math.floor(min(0, min(ys)) / ystep) * ystep
    ymax = math.ceil(max(ys) * 1.04 / ystep) * ystep
    if ymax <= ymin:  # e.g. all-zero data; avoid a zero/negative range
        ymax = ymin + ystep
    xmin, xmax = min(xs), max(xs)

    def X(v):
        return x0 + (v - xmin) / (xmax - xmin) * (x1 - x0)

    def Y(v):
        return y1 - (v - ymin) / (ymax - ymin) * (y1 - y0)

    grid = (60, 70, 100)
    small = font(cfg, int(h * 0.028))
    y_prefix, y_suffix = str(chart.get("y_prefix") or ""), str(chart.get("y_suffix") or "")  # LLMs may emit null
    val = ymin
    while val <= ymax + ystep * 0.01:   # round-number ticks: 0, 20,000, 40,000 ... never "1,368"
        gy = Y(val)
        d.line([(x0, gy), (x1, gy)], fill=grid, width=2)
        d.text((x0 - 14, gy), f"{y_prefix}{_fmt_tick(val)}{y_suffix}", font=small, fill=(180, 190, 210), anchor="rm")
        val += ystep
    xstep = _nice_step(xmax - xmin, 8)
    xv = math.ceil(xmin / xstep) * xstep
    while xv <= xmax + xstep * 0.01:
        d.text((X(xv), y1 + 12), _fmt_tick(xv), font=small, fill=(180, 190, 210), anchor="mt")
        xv += xstep
    d.text(((x0 + x1) / 2, y1 + int(h * 0.075)), str(chart.get("x_label") or ""), font=small, fill=(200, 205, 220), anchor="mm")
    title = str(chart.get("title") or "")[:80]
    tf = font(cfg, int(h * 0.05))
    title_w = (x1 - int(w * 0.24)) - x0           # leave the right quarter for the legend
    while d.textlength(title, font=tf) > title_w and tf.size > int(h * 0.03):
        tf = font(cfg, tf.size - 2)
    while d.textlength(title, font=tf) > title_w and len(title) > 8:
        title = title[:-2].rstrip() + "…"
    d.text((x0, h * 0.19), title, font=tf, fill=hex_to_rgb(st["text"]), anchor="lm")

    colors = [hex_to_rgb(st["accent"]), hex_to_rgb(st["accent2"]), (120, 170, 255), (255, 120, 150)]
    ctype = chart.get("type", "line")
    n = len(series)
    for si, s in enumerate(series):
        col = colors[si % len(colors)]
        pts = sorted(s["points"])
        if ctype == "bar":
            bw = (x1 - x0) / (len(pts) * (n + 1))
            for k, (px, py) in enumerate(pts):
                bx = X(px) - bw * n / 2 + si * bw
                d.rectangle([bx, Y(py), bx + bw * 0.9, Y(0)], fill=col)
        else:
            line = [(X(px), Y(py)) for px, py in pts]
            if len(line) > 1:
                d.line(line, fill=col, width=int(h * 0.008), joint="curve")
            for lx, ly in line:
                d.ellipse([lx - 7, ly - 7, lx + 7, ly + 7], fill=col)
        # legend (top-right, same band as the title)
        ly = h * 0.17 + si * int(h * 0.045)
        d.rectangle([x1 - int(w * 0.22), ly - 10, x1 - int(w * 0.22) + 30, ly + 10], fill=col)
        d.text((x1 - int(w * 0.22) + 44, ly), str(s.get("name", ""))[:28], font=small, fill=(230, 235, 245), anchor="lm")
    img.save(out, "PNG")
    return out


def lower_third(cfg: dict, text: str, w: int, h: int, out: Path, center: bool = False) -> Path:
    """Transparent PNG overlay with the section heading: top-left pill (landscape) or a centred
    top title bar (Shorts, where the hook title should stay pinned)."""
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    f = font(cfg, int(min(w, h) * (0.045 if not center else 0.05)))
    text = text if len(text) <= 36 else text[:33].rstrip() + "..."
    tw = d.textlength(text, font=f)
    while tw > w * 0.86 and f.size > 20:
        f = font(cfg, f.size - 4)
        tw = d.textlength(text, font=f)
    pad = int(h * 0.016) if not center else int(w * 0.03)
    x, y = (int(w * 0.04), int(h * 0.05)) if not center else (int(w / 2 - tw / 2 - pad - 10), int(h * 0.08))
    d.rounded_rectangle([x, y, x + tw + pad * 2, y + f.size + pad * 2], radius=14, fill=(11, 16, 32, 200))
    d.rectangle([x, y, x + 8, y + f.size + pad * 2], fill=hex_to_rgb(cfg["style"]["accent"]) + (255,))
    d.text((x + pad + 10, y + pad), text, font=f, fill=(255, 255, 255, 255))
    img.save(out, "PNG")
    return out


def broll_overlay(cfg: dict, phrase: str, w: int, h: int, out: Path, base: Path | None) -> Path:
    """Overlay for b-roll beats: the section lower third (base) PLUS the sentence's key phrase on a soft
    dark gradient, so footage never plays as a wordless gap (v3.1 audit: 6 s of a car bonnet, no text)."""
    from .motion import TOP_SAFE, draw_fit
    img = Image.open(base).convert("RGBA") if base and Path(base).exists() else Image.new("RGBA", (w, h), (0, 0, 0, 0))
    portrait = h > w
    shade = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shade)
    if portrait:
        y0, y1 = int(h * TOP_SAFE), int(h * 0.5)
        for y in range(y0, y1):  # vertical ramp: dark under the text, clear below
            a = int(150 * (1 - (y - y0) / (y1 - y0)))
            sd.line([(0, y), (w, y)], fill=(11, 16, 32, a))
        box = (w * 0.08, h * (TOP_SAFE + 0.03), w * 0.92, h * 0.44)
    else:
        x1 = int(w * 0.6)
        for x in range(0, x1):    # horizontal ramp: dark on the left, clear on the right
            a = int(170 * (1 - x / x1))
            sd.line([(x, 0), (x, h)], fill=(11, 16, 32, a))
        box = (w * 0.06, h * 0.3, w * 0.52, h * 0.74)
    img = Image.alpha_composite(img, shade)
    d = ImageDraw.Draw(img)
    draw_fit(cfg, d, phrase, box, int(min(w, h) * (0.075 if not portrait else 0.068)), (255, 255, 255, 255),
             align="center" if portrait else "left", valign="middle", max_lines=4, stroke=3)
    accent = hex_to_rgb(cfg["style"]["accent"]) + (255,)
    if portrait:
        d.rectangle([w / 2 - w * 0.06, box[1] - 14, w / 2 + w * 0.06, box[1] - 8], fill=accent)
    else:
        d.rectangle([box[0], box[1] - 20, box[0] + w * 0.12, box[1] - 14], fill=accent)
    img.save(out, "PNG")
    return out


# ---------------- Pexels (free: 200 req/hour, 20k/month) ----------------

def pexels_video(query: str, min_seconds: float, out: Path, portrait: bool = False) -> Path | None:
    key = env("PEXELS_API_KEY")
    if not key:
        return None
    try:
        r = requests.get(
            "https://api.pexels.com/videos/search",
            headers={"Authorization": key},
            params={"query": query, "per_page": 15, "orientation": "portrait" if portrait else "landscape", "size": "medium"},
            timeout=30,
        )
        r.raise_for_status()
        vids = r.json().get("videos", [])
        random.shuffle(vids)
        for v in vids:
            files = [f for f in v.get("video_files", []) if f.get("width") and 1000 <= f["width"] <= 2600]
            if not files:
                continue
            files.sort(key=lambda f: -f["width"])
            url = files[0]["link"]
            with requests.get(url, stream=True, timeout=120) as dl:
                dl.raise_for_status()
                with open(out, "wb") as fh:
                    for chunk in dl.iter_content(1 << 20):
                        fh.write(chunk)
            return out
    except requests.RequestException:
        return None
    return None


def pexels_photos(query: str, outs: list[Path], portrait: bool = False) -> list[Path]:
    """Download up to len(outs) DIFFERENT photos for one query with a single search call.
    Returns the paths that succeeded (possibly fewer than requested, possibly empty)."""
    key = env("PEXELS_API_KEY")
    if not key or not outs:
        return []
    try:
        r = requests.get(
            "https://api.pexels.com/v1/search",
            headers={"Authorization": key},
            params={"query": query, "per_page": 15, "orientation": "portrait" if portrait else "landscape"},
            timeout=30,
        )
        r.raise_for_status()
        photos = r.json().get("photos", [])
    except requests.RequestException:
        return []
    if not photos:
        return []
    random.shuffle(photos)
    got: list[Path] = []
    for p, out in zip(photos, outs):
        try:
            dl = requests.get(p["src"]["large2x"], timeout=60)
            dl.raise_for_status()
            Image.open(io.BytesIO(dl.content)).convert("RGB").save(out, "JPEG", quality=90)
            got.append(out)
        except (requests.RequestException, OSError, KeyError):
            continue
    return got


def pexels_photo(query: str, out: Path, portrait: bool = False) -> Path | None:
    got = pexels_photos(query, [out], portrait)
    return got[0] if got else None
