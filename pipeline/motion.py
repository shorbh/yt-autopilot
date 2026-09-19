"""Animated motion-graphics cards, rendered as short PNG frame sequences with PIL.

Every renderer returns (frames_dir, n_frames). The clip builder plays the frames at ANIM_FPS and
then holds the last frame for the rest of the beat (ffmpeg tpad), so a 2-second animation can
sit under a 9-second sentence without generating 270 images.

Design rules baked in here (what a motion designer would enforce):
  * every piece of text goes through fit_text_box(): wrap -> shrink -> ellipsis; nothing is clipped
  * one accent colour for "good/primary", one for "numbers/highlight"; text is white on navy
  * 3 rotating background variants so consecutive cards never look identical
  * icons (Lucide) and imagery (real photos) so the video is not text-only
  * nothing is drawn above TOP_SAFE: that strip belongs to the section lower-third overlay
All sizes are relative to the canvas so the same code serves 1920x1080 and 1080x1920.
"""
from __future__ import annotations

import math
import re
import threading
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

from . import assets_remote
from .config import hex_to_rgb
from .visuals import font, gradient

ANIM_FPS = 15
TOP_SAFE = 0.16   # fraction of the canvas height reserved for the lower-third heading overlay


def _ease(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 3  # ease-out cubic


def _mix(col, a: float, bg=(11, 16, 32)):
    """Fade a colour in from the background (alpha 0..1) without needing RGBA compositing."""
    return tuple(int(c * a + b * (1 - a)) for c, b in zip(col, bg))


# ------------------------------------------------------------------ backgrounds (cached)

_BASE_CACHE: dict[tuple, Image.Image] = {}
_BASE_LOCK = threading.Lock()
_GLOWS = [  # (ellipse box as fractions of w/h, colour) — three variants
    ((0.55, -0.4, 1.3, 0.6), (40, 60, 110)),
    ((-0.3, 0.5, 0.45, 1.4), (30, 80, 90)),
    ((0.3, 0.7, 1.1, 1.5), (60, 45, 110)),
]


def _base(cfg: dict, w: int, h: int, alt: bool = False, variant: int = 0) -> Image.Image:
    """Background + brand tag. Rendered once per (size, variant) and copied per frame."""
    st = cfg["style"]
    variant %= len(_GLOWS)
    key = (w, h, alt, variant, st["bg_dark"], st["bg_dark2"])
    with _BASE_LOCK:
        img = _BASE_CACHE.get(key)
        if img is None:
            c1, c2 = (st["bg_dark2"], st["bg_dark"]) if alt else (st["bg_dark"], st["bg_dark2"])
            img = gradient(w, h, c1, c2, noise=False)
            box, col = _GLOWS[variant]
            glow = Image.new("RGB", (w, h), (0, 0, 0))
            ImageDraw.Draw(glow).ellipse([box[0] * w, box[1] * h, box[2] * w, box[3] * h], fill=col)
            glow = glow.filter(ImageFilter.GaussianBlur(w // 8))
            img = Image.blend(img, Image.composite(glow, img, glow.convert("L")), 0.55)
            d = ImageDraw.Draw(img)
            d.text((w * 0.97, h * 0.965), cfg["channel"]["name"].upper(), font=font(cfg, int(min(w, h) * 0.022)),
                   fill=(150, 160, 190), anchor="rm")
            _BASE_CACHE[key] = img
    return img.copy()


def _save_frames(frames: list[Image.Image], out_dir: Path) -> tuple[Path, int]:
    out_dir.mkdir(parents=True, exist_ok=True)
    for i, fr in enumerate(frames):
        fr.save(out_dir / f"{i:04d}.png", "PNG", compress_level=1)
    return out_dir, len(frames)


# ------------------------------------------------------------------ text fitting (the clipping fix)

def _wrap(d, text: str, fnt, max_w: float) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for wd in words:
        trial = (cur + " " + wd).strip()
        if d.textlength(trial, font=fnt) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = wd
    if cur:
        lines.append(cur)
    return lines


def fit_text_box(cfg, d, text: str, box_w: float, box_h: float, start: int, floor: int = 22,
                 line_spacing: float = 1.15, max_lines: int | None = None):
    """Largest font (<= start) whose wrapped text fits in box_w x box_h. If even `floor` overflows,
    the text is truncated with an ellipsis. Returns (font, lines, line_height)."""
    text = " ".join(str(text).split())
    size = start
    while True:
        fnt = font(cfg, size)
        lines = _wrap(d, text, fnt, box_w)
        lh = fnt.size * line_spacing
        cap = max_lines or 99
        if (len(lines) * lh <= box_h and len(lines) <= cap) or size <= floor:
            break
        size -= max(2, size // 10)
    allowed = max(1, min(max_lines or 99, int(box_h // lh)))
    if len(lines) > allowed:
        lines = lines[:allowed]
        last = lines[-1]
        while last and d.textlength(last + "…", font=fnt) > box_w:
            last = last[:-1].rstrip()
        lines[-1] = (last.rstrip(" ,;:") + "…") if last else "…"
    return fnt, lines, lh


def draw_fit(cfg, d, text, box, start, fill, floor=22, align="left", valign="top", max_lines=None, stroke=0):
    """Draw text fitted inside box=(x0,y0,x1,y1). Returns the height used."""
    x0, y0, x1, y1 = box
    fnt, lines, lh = fit_text_box(cfg, d, text, x1 - x0, y1 - y0, start, floor, max_lines=max_lines)
    total = len(lines) * lh
    y = y0 if valign == "top" else (y0 + (y1 - y0 - total) / 2 if valign == "middle" else y1 - total)
    for i, line in enumerate(lines):
        tw = d.textlength(line, font=fnt)
        x = x0 if align == "left" else ((x0 + x1) / 2 - tw / 2 if align == "center" else x1 - tw)
        d.text((x, y + i * lh), line, font=fnt, fill=fill, stroke_width=stroke, stroke_fill=(0, 0, 0) if stroke else None)
    return total


def _fit_font(cfg, d, text, max_w, start, floor=28):
    size = start
    f = font(cfg, size)
    while d.textlength(text, font=f) > max_w and size > floor:
        size -= max(4, size // 12)
        f = font(cfg, size)
    return f


_NUM_RE = re.compile(r"[-+]?\d[\d,]*\.?\d*")


def _animate_number(value: str, t: float) -> str:
    """'$452,000' at t=0.5 -> '$226,000' (keeps prefix/suffix and formatting)."""
    m = _NUM_RE.search(value)
    if not m or t >= 1.0:
        return value
    raw = m.group(0)
    try:
        num = float(raw.replace(",", ""))
    except ValueError:
        return value
    cur = num * (0.1 + 0.9 * _ease(t))   # start at 10%, never a lonely "0" on the first frame
    if "." in raw:
        dec = len(raw.split(".")[1])
        s = f"{cur:,.{dec}f}" if "," in raw else f"{cur:.{dec}f}"
    else:
        s = f"{int(round(cur)):,}" if "," in raw or abs(num) >= 10000 else f"{int(round(cur))}"
    return value[: m.start()] + s + value[m.end():]


def _paste_rgba(img: Image.Image, layer: Image.Image, cx: float, cy: float, alpha: float = 1.0) -> None:
    if alpha < 1.0:
        a = layer.split()[3].point(lambda v: int(v * alpha))
        layer = layer.copy()
        layer.putalpha(a)
    img.paste(layer, (int(cx - layer.width / 2), int(cy - layer.height / 2)), layer)


ICON_BASE = 512


def _initial_circle(cfg, d: ImageDraw.ImageDraw, name: str, size: int, col, cx: float, cy: float, alpha: float = 1.0) -> None:
    """Typographic avatar: a filled circle with the person's initial (what apps show when there is no photo)."""
    r = size / 2
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=_mix(col, alpha), outline=_mix((255, 255, 255), alpha * 0.35), width=max(2, size // 40))
    ini = (name.strip()[:1] or "?").upper()
    d.text((cx, cy + size * 0.02), ini, font=font(cfg, int(size * 0.55)), fill=_mix((11, 16, 32), alpha, _mix(col, alpha)), anchor="mm")


def _cover(ph: Image.Image, w: int, h: int) -> Image.Image:
    """Cover-crop a photo to exactly w x h."""
    scale = max(w / ph.width, h / ph.height)
    ph = ph.resize((int(ph.width * scale) + 1, int(ph.height * scale) + 1))
    return ph.crop(((ph.width - w) // 2, (ph.height - h) // 2, (ph.width - w) // 2 + w, (ph.height - h) // 2 + h))


_PORTRAIT_CACHE: dict[tuple, Image.Image] = {}


def _portrait_tile(photo: Path, w: int, h: int, radius: int) -> Image.Image:
    """Rounded-corner RGBA photo tile, computed once per (photo, size) and reused across frames."""
    key = (str(photo), w, h)
    with _BASE_LOCK:
        tile = _PORTRAIT_CACHE.get(key)
    if tile is None:
        ph = _cover(Image.open(photo).convert("RGB"), w, h)
        ph = ImageEnhance.Color(ph).enhance(0.85)
        mask = Image.new("L", (w, h), 0)
        ImageDraw.Draw(mask).rounded_rectangle([0, 0, w - 1, h - 1], radius=radius, fill=255)
        tile = ph.convert("RGBA")
        tile.putalpha(mask)
        with _BASE_LOCK:
            _PORTRAIT_CACHE[key] = tile
    return tile


def _paste_icon(img: Image.Image, name: str | None, size: int, color, cx: float, cy: float, alpha: float = 1.0) -> bool:
    """Paste a Lucide icon centred at (cx, cy). Returns False if the icon is unavailable."""
    if not name:
        return False
    ic = assets_remote.icon(name, ICON_BASE, color)
    if ic is not None and size != ICON_BASE:
        ic = ic.resize((size, size), Image.LANCZOS)
    if ic is None:
        return False
    if alpha < 1.0:
        a = ic.split()[3].point(lambda v: int(v * alpha))
        ic = ic.copy()
        ic.putalpha(a)
    img.paste(ic, (int(cx - size / 2), int(cy - size / 2)), ic)
    return True


# ------------------------------------------------------------------ renderers

def bignumber(cfg, spec, w, h, out_dir, variant=0) -> tuple[Path, int]:
    st = cfg["style"]
    value, label = str(spec.get("value") or ""), str(spec.get("label") or "")
    n = int(ANIM_FPS * 1.6)
    frames = []
    for i in range(n + 1):
        t = i / n
        img = _base(cfg, w, h, variant=variant)
        d = ImageDraw.Draw(img)
        vf = _fit_font(cfg, d, value, w * 0.86, int(min(w, h) * (0.24 if w > h else 0.17)))
        scale = 0.85 + 0.15 * _ease(t)
        d.text((w / 2, h * 0.47), _animate_number(value, t), font=font(cfg, int(vf.size * scale)), fill=hex_to_rgb(st["accent2"]), anchor="mm")
        if t > 0.35:
            a = _ease((t - 0.35) / 0.65)
            draw_fit(cfg, d, label, (w * 0.1, h * 0.47 + vf.size * 0.65, w * 0.9, h * 0.9), int(vf.size * 0.26),
                     _mix(hex_to_rgb(st["text"]), a), align="center", max_lines=2)
        isz = int(min(w, h) * 0.11)
        _paste_icon(img, spec.get("icon"), isz, hex_to_rgb(st["accent"]), w / 2, h * TOP_SAFE + isz * 0.7, alpha=_ease(t))
        frames.append(img)
    return _save_frames(frames, out_dir)


def callout(cfg, spec, w, h, out_dir, variant=0) -> tuple[Path, int]:
    st = cfg["style"]
    text = str(spec.get("text") or "")
    n = int(ANIM_FPS * 0.7)
    frames = []
    for i in range(n + 1):
        t = _ease(i / n)
        img = _base(cfg, w, h, alt=True, variant=variant)
        d = ImageDraw.Draw(img)
        size = int(min(w, h) * (0.075 if w > h else 0.06))
        fnt, lines, lh = fit_text_box(cfg, d, text, w * 0.78, h * 0.5, size, max_lines=4)
        total = len(lines) * lh
        y0 = h / 2 - total / 2 + size * 0.1
        bar_w = int(w * 0.18 * t)
        d.rectangle([w / 2 - bar_w / 2, y0 - size * 0.6, w / 2 + bar_w / 2, y0 - size * 0.5], fill=hex_to_rgb(st["accent"]))
        col = _mix(hex_to_rgb(st["text"]), t, (20, 27, 55))
        for k, line in enumerate(lines):
            d.text((w / 2, y0 + k * lh + lh / 2), line, font=fnt, fill=col, anchor="mm")
        frames.append(img)
    return _save_frames(frames, out_dir)


def icon_text(cfg, spec, w, h, out_dir, variant=0) -> tuple[Path, int]:
    """Big Lucide icon + short phrase. Falls back to a callout look if the icon is unavailable."""
    st = cfg["style"]
    text = str(spec.get("text") or "")
    name = spec.get("icon")
    portrait = h > w
    if not name or assets_remote.icon(name, 64, (255, 255, 255)) is None:
        return callout(cfg, {"text": text}, w, h, out_dir, variant)
    n = int(ANIM_FPS * 0.9)
    frames = []
    for i in range(n + 1):
        t = _ease(i / n)
        img = _base(cfg, w, h, variant=variant)
        d = ImageDraw.Draw(img)
        isz = int(min(w, h) * (0.26 if not portrait else 0.22) * (0.8 + 0.2 * t))
        if portrait:
            _paste_icon(img, name, isz, hex_to_rgb(st["accent"]), w / 2, h * 0.36, alpha=t)
            draw_fit(cfg, d, text, (w * 0.1, h * 0.5, w * 0.9, h * 0.72), int(w * 0.075), _mix(hex_to_rgb(st["text"]), t), align="center", max_lines=4)
        else:
            _paste_icon(img, name, isz, hex_to_rgb(st["accent"]), w * 0.27, h * 0.5, alpha=t)
            draw_fit(cfg, d, text, (w * 0.46 - (1 - t) * w * 0.03, h * 0.28, w * 0.92, h * 0.72), int(h * 0.085), _mix(hex_to_rgb(st["text"]), t), valign="middle", max_lines=4)
        frames.append(img)
    return _save_frames(frames, out_dir)


def formula(cfg, spec, w, h, out_dir, variant=0) -> tuple[Path, int]:
    st = cfg["style"]
    lines = [str(l) for l in spec.get("lines", [])][:4]
    per = 0.55
    n = int(ANIM_FPS * (per * len(lines) + 0.3))
    frames = []
    for i in range(n + 1):
        tt = i / ANIM_FPS
        img = _base(cfg, w, h, variant=variant)
        d = ImageDraw.Draw(img)
        size = int(min(w, h) * (0.07 if w > h else 0.055))
        gap = size * 1.7
        y0 = h / 2 - gap * (len(lines) - 1) / 2
        for k, line in enumerate(lines):
            a = _ease((tt - k * per) / 0.4)
            if a <= 0:
                continue
            f = _fit_font(cfg, d, line, w * 0.86, size)
            colr = hex_to_rgb(st["accent2"] if k == len(lines) - 1 else st["text"])
            d.text((w / 2 - (1 - a) * w * 0.03, y0 + k * gap), line, font=f, fill=_mix(colr, a), anchor="mm")
        frames.append(img)
    return _save_frames(frames, out_dir)


def list_card(cfg, spec, w, h, out_dir, variant=0) -> tuple[Path, int]:
    st = cfg["style"]
    title = str(spec.get("title") or "")
    items = [str(x) for x in spec.get("items", [])][:5]
    icons = list(spec.get("icons") or []) + [None] * 5
    per = 0.55
    n = int(ANIM_FPS * (per * len(items) + 0.4))
    portrait = h > w
    frames = []
    for i in range(n + 1):
        tt = i / ANIM_FPS
        img = _base(cfg, w, h, variant=variant)
        d = ImageDraw.Draw(img)
        tsize = int(min(w, h) * (0.06 if not portrait else 0.05))
        x = w * 0.1
        top = h * (TOP_SAFE + 0.01)
        used = draw_fit(cfg, d, title, (x, top, w * 0.9, top + h * 0.16), tsize, hex_to_rgb(st["text"]), max_lines=2)
        y = top + used + tsize * 0.6
        avail = h * 0.86 - y
        row = min(tsize * 1.9, avail / max(1, len(items)))
        isize = int(row * 0.42)
        for k, item in enumerate(items):
            a = _ease((tt - 0.3 - k * per) / 0.4)
            if a <= 0:
                continue
            cy = y + k * row + row / 2
            cx = x + isize * 0.7 - (1 - a) * w * 0.04
            if not _paste_icon(img, icons[k], int(isize * 1.3), hex_to_rgb(st["accent"]), cx, cy, alpha=a):
                d.ellipse([cx - isize * 0.55, cy - isize * 0.55, cx + isize * 0.55, cy + isize * 0.55], fill=_mix(hex_to_rgb(st["accent"]), a))
                d.text((cx, cy), str(k + 1), font=font(cfg, int(isize * 0.8)), fill=hex_to_rgb(st["bg_dark"]), anchor="mm")
            draw_fit(cfg, d, item, (cx + isize * 1.1, cy - row * 0.42, w * 0.9, cy + row * 0.42), int(row * 0.42),
                     _mix(hex_to_rgb(st["text"]), a), valign="middle", max_lines=2)
        frames.append(img)
    return _save_frames(frames, out_dir)


def compare(cfg, spec, w, h, out_dir, variant=0) -> tuple[Path, int]:
    st = cfg["style"]
    title = str(spec.get("title") or "")
    L, R = spec.get("left") or {}, spec.get("right") or {}
    n = int(ANIM_FPS * 1.4)
    portrait = h > w
    frames = []
    for i in range(n + 1):
        tt = i / n
        img = _base(cfg, w, h, variant=variant)
        d = ImageDraw.Draw(img)
        tsize = int(min(w, h) * (0.05 if not portrait else 0.045))
        top = h * (TOP_SAFE + 0.01)
        if title:
            # title sits BELOW the lower-third strip (v3.1 drew it at 8% and collided with the heading)
            draw_fit(cfg, d, title, (w * 0.06, top, w * 0.94, top + h * 0.11), tsize, hex_to_rgb(st["text"]), align="center", max_lines=2)
        if portrait:
            boxes = [(w * 0.08, h * 0.3, w * 0.92, h * 0.54), (w * 0.08, h * 0.58, w * 0.92, h * 0.82)]
        else:
            boxes = [(w * 0.07, h * 0.31, w * 0.48, h * 0.88), (w * 0.52, h * 0.31, w * 0.93, h * 0.88)]
        for k, (side, box) in enumerate(zip((L, R), boxes)):
            a = _ease((tt - 0.25 * k) / 0.6)
            if a <= 0:
                continue
            dx = (1 - a) * w * 0.05 * (-1 if k == 0 else 1)
            x0, y0, x1, y1 = box[0] + dx, box[1], box[2] + dx, box[3]
            col = hex_to_rgb(st["accent"] if k == 0 else st["accent2"])
            d.rounded_rectangle([x0, y0, x1, y1], radius=28, fill=(20, 27, 55), outline=col, width=4)
            cx, bw, bh = (x0 + x1) / 2, x1 - x0, y1 - y0
            pad = bw * 0.06
            # a person gets an initial avatar (like a chat app); an option gets its icon
            ch = side.get("character")
            if isinstance(ch, dict) and ch.get("name"):
                _initial_circle(cfg, d, str(ch["name"]), int(bh * 0.2), col, cx, y0 + bh * 0.17, alpha=a)
                has_icon = True
            else:
                has_icon = _paste_icon(img, side.get("icon"), int(bh * 0.16), col, cx, y0 + bh * 0.16, alpha=a)
            label_y = y0 + bh * (0.32 if has_icon else 0.12)
            draw_fit(cfg, d, str(side.get("label") or ""), (x0 + pad, label_y, x1 - pad, label_y + bh * 0.16), int(tsize * 0.8), col, align="center", max_lines=1)
            val = str(side.get("value") or "")
            vf = _fit_font(cfg, d, val, bw * 0.86, int(min(w, h) * (0.12 if not portrait else 0.09)))
            d.text((cx, y0 + bh * (0.6 if has_icon else 0.54)), _animate_number(val, a), font=vf, fill=hex_to_rgb(st["text"]), anchor="mm")
            note = str(side.get("note") or "")
            if note:
                draw_fit(cfg, d, note, (x0 + pad, y0 + bh * (0.76 if has_icon else 0.7), x1 - pad, y1 - pad), int(tsize * 0.6), (190, 200, 225), align="center", max_lines=2)
        frames.append(img)
    return _save_frames(frames, out_dir)


def timeline(cfg, spec, w, h, out_dir, variant=0) -> tuple[Path, int]:
    st = cfg["style"]
    items = [i for i in spec.get("items", []) if isinstance(i, dict)][:6]
    n = int(ANIM_FPS * 1.6)
    portrait = h > w
    m = max(1, len(items))
    frames = []
    for i in range(n + 1):
        tt = i / n
        img = _base(cfg, w, h, variant=variant)
        d = ImageDraw.Draw(img)
        if portrait:
            x = w * 0.16
            ys = [h * 0.2 + k * (h * 0.6 / max(1, m - 1)) for k in range(m)] if m > 1 else [h * 0.5]
            d.line([(x, ys[0]), (x, ys[0] + (ys[-1] - ys[0]) * _ease(tt))], fill=hex_to_rgb(st["accent"]), width=6)
            for k, it in enumerate(items):
                a = _ease((tt - k / m) / 0.35)
                if a <= 0:
                    continue
                d.ellipse([x - 16, ys[k] - 16, x + 16, ys[k] + 16], fill=hex_to_rgb(st["accent2"]))
                draw_fit(cfg, d, str(it.get("when") or ""), (x + 40, ys[k] - w * 0.05, w * 0.92, ys[k]), int(w * 0.045), _mix(hex_to_rgb(st["accent2"]), a), max_lines=1, valign="bottom")
                draw_fit(cfg, d, str(it.get("what") or ""), (x + 40, ys[k] + w * 0.01, w * 0.92, ys[k] + h * 0.6 / m * 0.8), int(w * 0.04), _mix(hex_to_rgb(st["text"]), a), max_lines=2)
        else:
            y = h * 0.55
            # keep the first/last labels inside the canvas: shrink the line span and align edge items inward
            xs = [w * 0.14 + k * (w * 0.72 / max(1, m - 1)) for k in range(m)] if m > 1 else [w * 0.5]
            col_w = w * 0.72 / max(1, m - 1) if m > 1 else w * 0.6
            d.line([(xs[0], y), (xs[0] + (xs[-1] - xs[0]) * _ease(tt), y)], fill=hex_to_rgb(st["accent"]), width=8)
            for k, it in enumerate(items):
                a = _ease((tt - k / m) / 0.35)
                if a <= 0:
                    continue
                d.ellipse([xs[k] - 18, y - 18, xs[k] + 18, y + 18], fill=hex_to_rgb(st["accent2"]))
                half = min(col_w * 0.48, w * 0.2)
                bx0, bx1 = max(w * 0.02, xs[k] - half), min(w * 0.98, xs[k] + half)
                draw_fit(cfg, d, str(it.get("when") or ""), (bx0, y - h * 0.16, bx1, y - h * 0.04), int(h * 0.05), _mix(hex_to_rgb(st["accent2"]), a), align="center", valign="bottom", max_lines=1)
                draw_fit(cfg, d, str(it.get("what") or ""), (bx0, y + h * 0.05, bx1, y + h * 0.3), int(h * 0.036), _mix(hex_to_rgb(st["text"]), a), align="center", max_lines=3)
        frames.append(img)
    return _save_frames(frames, out_dir)


def photo_text(cfg, spec, w, h, out_dir, photo: Path | None, variant=0) -> tuple[Path, int] | None:
    """Photo with a gradient fade on one side, text on the other (landscape: right/left; portrait: top/bottom)."""
    if photo is None or not Path(photo).exists():
        return None
    st = cfg["style"]
    text = str(spec.get("text") or "")
    portrait = h > w
    ph = ImageEnhance.Brightness(_cover(Image.open(photo).convert("RGB"), w, h)).enhance(0.75)
    if portrait:
        mask = Image.linear_gradient("L").resize((w, h))  # black top -> white bottom
        mask = mask.point(lambda v: max(0, min(255, int((v - 60) * 1.8))))
    else:
        mask = Image.linear_gradient("L").rotate(90, expand=True).resize((w, h))  # black left -> white right
        mask = mask.point(lambda v: max(0, min(255, int((v - 100) * 2.4))))
    n = int(ANIM_FPS * 0.9)
    frames = []
    for i in range(n + 1):
        t = _ease(i / n)
        bg = _base(cfg, w, h, variant=variant)
        # photo slides in slightly and fades up
        m = mask.point(lambda v: int(v * t))
        img = Image.composite(ph, bg, m)
        d = ImageDraw.Draw(img)
        if portrait:
            box = (w * 0.08, h * (TOP_SAFE + 0.04), w * 0.92, h * 0.46)
            draw_fit(cfg, d, text, box, int(w * 0.07), _mix(hex_to_rgb(st["text"]), t), align="center", valign="middle", max_lines=4, stroke=3)
        else:
            box = (w * 0.06, h * 0.27, w * 0.5, h * 0.77)
            draw_fit(cfg, d, text, box, int(h * 0.075), _mix(hex_to_rgb(st["text"]), t), align="left", valign="middle", max_lines=4, stroke=3)
        bar_w = int(w * 0.12 * t)
        d.rectangle([w * 0.06, h * 0.24, w * 0.06 + bar_w, h * 0.24 + 6] if not portrait else
                    [w / 2 - bar_w / 2, h * (TOP_SAFE + 0.01), w / 2 + bar_w / 2, h * (TOP_SAFE + 0.01) + 6], fill=hex_to_rgb(st["accent"]))
        frames.append(img)
    return _save_frames(frames, out_dir)


def character(cfg, spec, w, h, out_dir, photo: Path | None = None, variant=0) -> tuple[Path, int]:
    """A named person: a real portrait photo (Pexels, chosen from gender + mood) in a rounded tile with a
    name chip, next to the sentence's key line and an optional big stat. Landscape: photo left, text right;
    portrait: photo top, text below. Without a photo the tile becomes a large initial avatar — never a cartoon."""
    st = cfg["style"]
    ch = spec.get("character") or {"name": spec.get("name"), "gender": spec.get("gender"), "mood": spec.get("mood")}
    name = str(ch.get("name") or "").strip()
    text = str(spec.get("text") or "")
    stat = str(spec.get("stat") or "")
    portrait = h > w
    accent = hex_to_rgb(st["accent"])
    if portrait:
        tw_, th_ = int(w * 0.6), int(w * 0.6)
        tx, ty = (w - tw_) / 2, h * (TOP_SAFE + 0.03)
        box = (w * 0.08, ty + th_ + h * 0.07, w * 0.92, h * 0.86)
    else:
        tw_, th_ = int(w * 0.3), int(h * 0.62)
        tx, ty = w * 0.08, h * (TOP_SAFE + 0.03)
        box = (w * 0.45, ty, w * 0.94, ty + th_)
    radius = int(min(w, h) * 0.035)
    tile = _portrait_tile(Path(photo), tw_, th_, radius) if photo and Path(photo).exists() else None
    n = int(ANIM_FPS * 0.9)
    frames = []
    for i in range(n + 1):
        t = _ease(i / n)
        img = _base(cfg, w, h, variant=variant)
        d = ImageDraw.Draw(img)
        dx = (1 - t) * w * 0.03 * (0 if portrait else -1)
        if tile is not None:
            _paste_rgba(img, tile, tx + tw_ / 2 + dx, ty + th_ / 2, alpha=t)
            d.rounded_rectangle([tx + dx, ty, tx + dx + tw_, ty + th_], radius=radius, outline=_mix(accent, t), width=4)
        else:
            d.rounded_rectangle([tx + dx, ty, tx + dx + tw_, ty + th_], radius=radius, fill=(20, 27, 55), outline=_mix(accent, t), width=4)
            _initial_circle(cfg, d, name or "?", int(min(tw_, th_) * 0.5), accent, tx + dx + tw_ / 2, ty + th_ / 2, alpha=t)
        # name chip overlapping the bottom edge of the tile
        if name:
            f = font(cfg, int(min(w, h) * 0.038))
            ntw = d.textlength(name, font=f)
            pad = int(min(w, h) * 0.014)
            cx, cy = tx + dx + tw_ / 2, ty + th_
            d.rounded_rectangle([cx - ntw / 2 - pad * 2, cy - f.size * 0.7 - pad, cx + ntw / 2 + pad * 2, cy + f.size * 0.7 + pad],
                                radius=(int(f.size * 1.4) + 2 * pad) // 2, fill=_mix(accent, t))
            d.text((cx, cy), name, font=f, fill=hex_to_rgb(st["bg_dark"]), anchor="mm")
        col = _mix(hex_to_rgb(st["text"]), t)
        tb = box
        if stat:
            sf = _fit_font(cfg, d, stat, (tb[2] - tb[0]), int(min(w, h) * (0.15 if not portrait else 0.12)))
            sx = (tb[0] + tb[2]) / 2 if portrait else tb[0]
            d.text((sx, tb[1] + sf.size * 0.55), _animate_number(stat, t), font=sf, fill=_mix(hex_to_rgb(st["accent2"]), t), anchor="mm" if portrait else "lm")
            tb = (tb[0], tb[1] + sf.size * 1.3, tb[2], tb[3])
        draw_fit(cfg, d, text, tb, int(min(w, h) * (0.07 if not portrait else 0.062)), col,
                 align="center" if portrait else "left", valign="top" if stat else "middle", max_lines=4)
        frames.append(img)
    return _save_frames(frames, out_dir)


def chart_progressive(cfg, chart: dict, w, h, out_dir) -> tuple[Path, int] | None:
    """Line chart that draws itself; bars grow. Renders partial data per frame with fixed axes."""
    from .visuals import chart_image
    import copy
    try:
        series = chart["series"]
        max_pts = max(len(s["points"]) for s in series)
    except (KeyError, TypeError, ValueError):
        return None
    n = int(ANIM_FPS * 2.0)
    out_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    for i in range(n + 1):
        t = _ease(i / n)
        partial = copy.deepcopy(chart)
        k = max(2, int(round(max_pts * t)))
        for s in partial["series"]:
            pts = s["points"]
            s["points"] = pts[:k] if len(pts) >= 2 else pts
        partial["_fixed_range"] = chart
        if chart_image(cfg, partial, w, h, out_dir / f"{i:04d}.png") is None:
            return None
        count += 1
    return out_dir, count


def chapter_card(cfg, heading: str, index: int, w, h, out_dir) -> tuple[Path, int]:
    st = cfg["style"]
    n = int(ANIM_FPS * 1.0)
    frames = []
    for i in range(n + 1):
        t = _ease(i / n)
        img = _base(cfg, w, h, alt=True, variant=index)
        d = ImageDraw.Draw(img)
        size = int(min(w, h) * (0.09 if w > h else 0.07))
        d.text((w / 2, h / 2 - size * 1.2), f"PART {index}", font=font(cfg, int(size * 0.35)), fill=hex_to_rgb(st["accent"]), anchor="mm")
        draw_fit(cfg, d, heading, (w * 0.1, h / 2 - size * 0.7, w * 0.9, h / 2 + size * 0.9), size, _mix(hex_to_rgb(st["text"]), t, (27, 36, 71)), align="center", valign="top", max_lines=2)
        lw = int(w * 0.25 * t)
        d.rectangle([w / 2 - lw / 2, h / 2 + size * 1.1, w / 2 + lw / 2, h / 2 + size * 1.1 + 6], fill=hex_to_rgb(st["accent"]))
        frames.append(img)
    return _save_frames(frames, out_dir)


def outro_card(cfg, w, h, out_dir) -> tuple[Path, int]:
    """End screen: CTA + channel name; leaves the right half free for YouTube end-screen elements."""
    st = cfg["style"]
    n = int(ANIM_FPS * 1.2)
    portrait = h > w
    frames = []
    for i in range(n + 1):
        t = _ease(i / n)
        img = _base(cfg, w, h, variant=1)
        d = ImageDraw.Draw(img)
        size = int(min(w, h) * (0.075 if not portrait else 0.06))
        box = (w * 0.07, h * 0.3, w * (0.48 if not portrait else 0.93), h * 0.6)
        draw_fit(cfg, d, "One new money mechanic every week.", box, size, _mix(hex_to_rgb(st["text"]), t), valign="middle", max_lines=3)
        pill_w, pill_h = int(w * (0.2 if not portrait else 0.5)), int(size * 1.1)
        px, py = w * 0.07, h * 0.66
        d.rounded_rectangle([px, py, px + pill_w * t, py + pill_h], radius=pill_h // 2, fill=hex_to_rgb(st["accent"]))
        if t > 0.6:
            d.text((px + pill_w / 2, py + pill_h / 2), "SUBSCRIBE", font=font(cfg, int(size * 0.5)), fill=hex_to_rgb(st["bg_dark"]), anchor="mm")
        d.text((w * 0.07, h * 0.22), cfg["channel"]["name"].upper(), font=font(cfg, int(size * 0.45)), fill=hex_to_rgb(st["accent2"]), anchor="lm")
        frames.append(img)
    return _save_frames(frames, out_dir)


RENDERERS = {   # character / photo_text / chart take extra args and are dispatched explicitly in render.py
    "bignumber": bignumber,
    "callout": callout,
    "icon_text": icon_text,
    "formula": formula,
    "list": list_card,
    "compare": compare,
    "timeline": timeline,
}
