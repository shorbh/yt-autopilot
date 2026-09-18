"""Animated motion-graphics cards, rendered as short PNG frame sequences with PIL.

Every renderer returns (frames_dir, n_frames). The clip builder plays the frames at ANIM_FPS and
then holds the last frame for the rest of the beat (ffmpeg tpad), so a 2-second animation can
sit under a 9-second sentence without generating 270 images.

All sizes are relative to the canvas so the same code serves 1920x1080 and 1080x1920.
"""
from __future__ import annotations

import math
import re
import threading
from pathlib import Path

from PIL import Image, ImageDraw

from .config import hex_to_rgb
from .visuals import _draw_text_block, font, gradient

ANIM_FPS = 15


def _ease(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 3  # ease-out cubic


_BASE_CACHE: dict[tuple, Image.Image] = {}
_BASE_LOCK = threading.Lock()


def _base(cfg: dict, w: int, h: int, alt: bool = False) -> Image.Image:
    """Background + brand tag. The gradient/blur is expensive (~0.3 s at 1080p) and identical for
    every frame, so it is rendered once per (size, variant) and copied."""
    key = (w, h, alt, cfg["style"]["bg_dark"], cfg["style"]["bg_dark2"])
    with _BASE_LOCK:
        img = _BASE_CACHE.get(key)
        if img is None:
            st = cfg["style"]
            img = gradient(w, h, st["bg_dark2" if alt else "bg_dark"], st["bg_dark" if alt else "bg_dark2"])
            d = ImageDraw.Draw(img)
            # tiny brand tag bottom-right; captions live bottom-centre, lower-third top-left
            tag = cfg["channel"]["name"].upper()
            f = font(cfg, int(min(w, h) * 0.022))
            d.text((w * 0.97, h * 0.965), tag, font=f, fill=(150, 160, 190), anchor="rm")
            _BASE_CACHE[key] = img
    return img.copy()


def _save_frames(frames: list[Image.Image], out_dir: Path) -> tuple[Path, int]:
    out_dir.mkdir(parents=True, exist_ok=True)
    for i, fr in enumerate(frames):
        fr.save(out_dir / f"{i:04d}.png", "PNG", compress_level=1)
    return out_dir, len(frames)


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
    cur = num * _ease(t)
    if "." in raw:
        dec = len(raw.split(".")[1])
        s = f"{cur:,.{dec}f}" if "," in raw else f"{cur:.{dec}f}"
    else:
        s = f"{int(round(cur)):,}" if "," in raw or abs(num) >= 10000 else f"{int(round(cur))}"
    return value[: m.start()] + s + value[m.end():]


# ------------------------------------------------------------------ renderers

def bignumber(cfg, spec, w, h, out_dir) -> tuple[Path, int]:
    st = cfg["style"]
    value, label = str(spec.get("value") or ""), str(spec.get("label") or "")
    n = int(ANIM_FPS * 1.6)
    frames = []
    for i in range(n + 1):
        t = i / n
        img = _base(cfg, w, h)
        d = ImageDraw.Draw(img)
        vf = _fit_font(cfg, d, value, w * 0.86, int(min(w, h) * (0.24 if w > h else 0.17)))
        scale = 0.85 + 0.15 * _ease(t)
        shown = _animate_number(value, t)
        d.text((w / 2, h * 0.44), shown, font=font(cfg, int(vf.size * scale)), fill=hex_to_rgb(st["accent2"]), anchor="mm")
        if t > 0.35:
            alpha = _ease((t - 0.35) / 0.65)
            col = tuple(int(c * alpha + 11 * (1 - alpha)) for c in hex_to_rgb(st["text"]))
            _draw_text_block(d, (w / 2, h * 0.44 + vf.size * 0.7), label, font(cfg, int(vf.size * 0.26)), col, w * 0.8, anchor_center=True)
        frames.append(img)
    return _save_frames(frames, out_dir)


def callout(cfg, spec, w, h, out_dir) -> tuple[Path, int]:
    st = cfg["style"]
    text = str(spec.get("text", ""))
    n = int(ANIM_FPS * 0.7)
    frames = []
    for i in range(n + 1):
        t = _ease(i / n)
        img = _base(cfg, w, h, alt=True)
        d = ImageDraw.Draw(img)
        size = int(min(w, h) * (0.075 if w > h else 0.06) * (0.9 + 0.1 * t))
        f = font(cfg, size)
        # measure wrapped height to centre vertically
        est_lines = max(1, math.ceil(d.textlength(text, font=f) / (w * 0.78)))
        y0 = h / 2 - est_lines * f.size * 1.15 / 2
        bar_w = int(w * 0.18 * t)
        d.rectangle([w / 2 - bar_w / 2, y0 - size * 0.55, w / 2 + bar_w / 2, y0 - size * 0.45], fill=hex_to_rgb(st["accent"]))
        col = tuple(int(c * t + 20 * (1 - t)) for c in hex_to_rgb(st["text"]))
        _draw_text_block(d, (w / 2, y0), text, f, col, w * 0.78, anchor_center=True)
        frames.append(img)
    return _save_frames(frames, out_dir)


def formula(cfg, spec, w, h, out_dir) -> tuple[Path, int]:
    st = cfg["style"]
    lines = [str(l) for l in spec.get("lines", [])][:4]
    per = 0.55
    n = int(ANIM_FPS * (per * len(lines) + 0.3))
    frames = []
    for i in range(n + 1):
        tt = i / ANIM_FPS
        img = _base(cfg, w, h)
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
            col = tuple(int(c * a + 11 * (1 - a)) for c in colr)
            d.text((w / 2 - (1 - a) * w * 0.03, y0 + k * gap), line, font=f, fill=col, anchor="mm")
        frames.append(img)
    return _save_frames(frames, out_dir)


def list_card(cfg, spec, w, h, out_dir) -> tuple[Path, int]:
    st = cfg["style"]
    title, items = str(spec.get("title", "")), [str(x) for x in spec.get("items", [])][:5]
    per = 0.55
    n = int(ANIM_FPS * (per * len(items) + 0.4))
    frames = []
    for i in range(n + 1):
        tt = i / ANIM_FPS
        img = _base(cfg, w, h)
        d = ImageDraw.Draw(img)
        tsize = int(min(w, h) * (0.06 if w > h else 0.05))
        isize = int(tsize * 0.75)
        x = w * 0.12
        y = h * 0.2
        _draw_text_block(d, (x, y), title, font(cfg, tsize), hex_to_rgb(st["text"]), w * 0.76)
        y += tsize * 1.9
        gap = isize * 2.1
        for k, item in enumerate(items):
            a = _ease((tt - 0.3 - k * per) / 0.4)
            if a <= 0:
                continue
            cx = x + isize * 0.55 - (1 - a) * w * 0.04
            d.ellipse([cx - isize * 0.35, y + k * gap + isize * 0.15, cx + isize * 0.35, y + k * gap + isize * 0.85], fill=hex_to_rgb(st["accent"]))
            d.text((cx, y + k * gap + isize * 0.5), str(k + 1), font=font(cfg, int(isize * 0.5)), fill=hex_to_rgb(st["bg_dark"]), anchor="mm")
            col = tuple(int(c * a + 11 * (1 - a)) for c in hex_to_rgb(st["text"]))
            _draw_text_block(d, (cx + isize * 0.7, y + k * gap), item, font(cfg, isize), col, w * 0.7)
        frames.append(img)
    return _save_frames(frames, out_dir)


def compare(cfg, spec, w, h, out_dir) -> tuple[Path, int]:
    st = cfg["style"]
    title = str(spec.get("title", ""))
    L, R = spec.get("left", {}), spec.get("right", {})
    n = int(ANIM_FPS * 1.4)
    portrait = h > w
    frames = []
    for i in range(n + 1):
        tt = i / n
        img = _base(cfg, w, h)
        d = ImageDraw.Draw(img)
        tsize = int(min(w, h) * (0.055 if not portrait else 0.045))
        if title:
            d.text((w / 2, h * (0.14 if not portrait else 0.12)), title[:60], font=_fit_font(cfg, d, title[:60], w * 0.9, tsize), fill=hex_to_rgb(st["text"]), anchor="mm")
        if portrait:
            boxes = [(w * 0.08, h * 0.22, w * 0.92, h * 0.5), (w * 0.08, h * 0.55, w * 0.92, h * 0.83)]
        else:
            boxes = [(w * 0.07, h * 0.26, w * 0.48, h * 0.86), (w * 0.52, h * 0.26, w * 0.93, h * 0.86)]
        for k, (side, box) in enumerate(zip((L, R), boxes)):
            a = _ease((tt - 0.25 * k) / 0.6)
            if a <= 0:
                continue
            dx = (1 - a) * w * 0.05 * (-1 if k == 0 else 1)
            x0, y0, x1, y1 = box[0] + dx, box[1], box[2] + dx, box[3]
            col = hex_to_rgb(st["accent"] if k == 0 else st["accent2"])
            d.rounded_rectangle([x0, y0, x1, y1], radius=28, fill=(20, 27, 55), outline=col, width=4)
            cx, bw = (x0 + x1) / 2, x1 - x0
            lf = font(cfg, int(tsize * 0.8))
            d.text((cx, y0 + (y1 - y0) * 0.18), str(side.get("label") or "")[:28], font=lf, fill=col, anchor="mm")
            val = str(side.get("value") or "")
            vf = _fit_font(cfg, d, val, bw * 0.86, int(min(w, h) * (0.12 if not portrait else 0.09)))
            d.text((cx, y0 + (y1 - y0) * 0.5), _animate_number(val, a), font=vf, fill=hex_to_rgb(st["text"]), anchor="mm")
            note = str(side.get("note") or "")[:60]
            if note:
                _draw_text_block(d, (cx, y0 + (y1 - y0) * 0.72), note, font(cfg, int(tsize * 0.6)), (190, 200, 225), bw * 0.85, anchor_center=True)
        frames.append(img)
    return _save_frames(frames, out_dir)


def timeline(cfg, spec, w, h, out_dir) -> tuple[Path, int]:
    st = cfg["style"]
    items = [i for i in spec.get("items", []) if isinstance(i, dict)][:6]
    n = int(ANIM_FPS * 1.6)
    portrait = h > w
    frames = []
    for i in range(n + 1):
        tt = i / n
        img = _base(cfg, w, h)
        d = ImageDraw.Draw(img)
        m = len(items)
        if portrait:
            x = w * 0.18
            ys = [h * 0.2 + k * (h * 0.6 / max(1, m - 1)) for k in range(m)] if m > 1 else [h * 0.5]
            d.line([(x, ys[0]), (x, ys[0] + (ys[-1] - ys[0]) * _ease(tt))], fill=hex_to_rgb(st["accent"]), width=6)
            for k, it in enumerate(items):
                a = _ease((tt - k / max(1, m)) / 0.35)
                if a <= 0:
                    continue
                d.ellipse([x - 16, ys[k] - 16, x + 16, ys[k] + 16], fill=hex_to_rgb(st["accent2"]))
                d.text((x + 40, ys[k] - 6), str(it.get("when", ""))[:22], font=font(cfg, int(w * 0.045)), fill=hex_to_rgb(st["accent2"]), anchor="lm")
                _draw_text_block(d, (x + 40, ys[k] + w * 0.03), str(it.get("what", ""))[:70], font(cfg, int(w * 0.04)), hex_to_rgb(st["text"]), w * 0.7)
        else:
            y = h * 0.55
            xs = [w * 0.1 + k * (w * 0.8 / max(1, m - 1)) for k in range(m)] if m > 1 else [w * 0.5]
            d.line([(xs[0], y), (xs[0] + (xs[-1] - xs[0]) * _ease(tt), y)], fill=hex_to_rgb(st["accent"]), width=8)
            for k, it in enumerate(items):
                a = _ease((tt - k / max(1, m)) / 0.35)
                if a <= 0:
                    continue
                d.ellipse([xs[k] - 18, y - 18, xs[k] + 18, y + 18], fill=hex_to_rgb(st["accent2"]))
                d.text((xs[k], y - h * 0.07), str(it.get("when", ""))[:18], font=font(cfg, int(h * 0.045)), fill=hex_to_rgb(st["accent2"]), anchor="mm")
                _draw_text_block(d, (xs[k], y + h * 0.06), str(it.get("what", ""))[:60], font(cfg, int(h * 0.034)), hex_to_rgb(st["text"]), w * 0.8 / max(1, m) * 0.95, anchor_center=True)
        frames.append(img)
    return _save_frames(frames, out_dir)


def chart_progressive(cfg, chart: dict, w, h, out_dir) -> tuple[Path, int] | None:
    """Line chart that draws itself; bars grow. Reuses chart_image for the final frame's layout
    by rendering partial data for each frame."""
    from .visuals import chart_image
    import copy
    try:
        series = chart["series"]
        max_pts = max(len(s["points"]) for s in series)
    except (KeyError, TypeError, ValueError):
        return None
    n = int(ANIM_FPS * 2.0)
    frames_dir = out_dir
    frames_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    for i in range(n + 1):
        t = _ease(i / n)
        partial = copy.deepcopy(chart)
        k = max(2, int(round(max_pts * t)))
        for s in partial["series"]:
            pts = s["points"]
            s["points"] = pts[:k] if len(pts) >= 2 else pts
        # keep axes stable: pass full-range hints
        partial["_fixed_range"] = chart
        p = chart_image(cfg, partial, w, h, frames_dir / f"{i:04d}.png")
        if p is None:
            return None
        count += 1
    return frames_dir, count


def chapter_card(cfg, heading: str, index: int, w, h, out_dir) -> tuple[Path, int]:
    st = cfg["style"]
    n = int(ANIM_FPS * 1.0)
    frames = []
    for i in range(n + 1):
        t = _ease(i / n)
        img = _base(cfg, w, h, alt=True)
        d = ImageDraw.Draw(img)
        size = int(min(w, h) * (0.09 if w > h else 0.07))
        f = _fit_font(cfg, d, heading, w * 0.8, size)
        col = tuple(int(c * t + 20 * (1 - t)) for c in hex_to_rgb(st["text"]))
        d.text((w / 2, h / 2), heading, font=f, fill=col, anchor="mm")
        d.text((w / 2, h / 2 - size * 1.1), f"PART {index}", font=font(cfg, int(size * 0.35)), fill=hex_to_rgb(st["accent"]), anchor="mm")
        lw = int(w * 0.25 * t)
        d.rectangle([w / 2 - lw / 2, h / 2 + size * 0.9, w / 2 + lw / 2, h / 2 + size * 0.9 + 6], fill=hex_to_rgb(st["accent"]))
        frames.append(img)
    return _save_frames(frames, out_dir)


RENDERERS = {
    "bignumber": bignumber,
    "callout": callout,
    "formula": formula,
    "list": list_card,
    "compare": compare,
    "timeline": timeline,
}
