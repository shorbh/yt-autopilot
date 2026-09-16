"""ffmpeg-based assembly: section clips -> concat -> audio + burned captions. Long-form and Shorts."""
from __future__ import annotations

import math
import subprocess
from pathlib import Path

from . import visuals
from .tts import concat_audio, ffprobe_duration, synthesize, synthesize_sections

GAP = 0.35  # silence between sections (must match tts.concat_audio)


def _run(cmd: list[str], cwd: Path | None = None) -> None:
    res = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError("ffmpeg failed:\n" + " ".join(cmd) + "\n" + res.stderr[-2500:])


def _srt_time(t: float) -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def build_srt(sections: list[dict], out: Path, max_words: int = 3, max_span: float = 1.6) -> Path:
    """Karaoke-style short caption chunks from edge-tts word boundaries."""
    idx, offset, lines = 1, 0.0, []
    for s in sections:
        words = s.get("words") or []
        chunk: list[dict] = []

        def flush():
            nonlocal idx
            if not chunk:
                return
            st = chunk[0]["start"] + offset
            en = max(chunk[-1]["end"] + offset, st + 0.3)
            txt = " ".join(w["text"] for w in chunk)
            lines.append(f"{idx}\n{_srt_time(st)} --> {_srt_time(en)}\n{txt}\n")
            idx += 1
            chunk.clear()

        for w in words:
            if chunk and (len(chunk) >= max_words or w["end"] - chunk[0]["start"] > max_span):
                flush()
            chunk.append(w)
        flush()
        offset += s["duration"] + GAP
    out.write_text("\n".join(lines), encoding="utf-8")
    return out


def _clip_from_video(src: Path, dur: float, w: int, h: int, fps: int, overlay: Path | None, out: Path) -> None:
    vf = f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1,fps={fps},eq=brightness=-0.06:saturation=1.05[bg]"
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-stream_loop", "-1", "-i", str(src)]
    if overlay:
        cmd += ["-i", str(overlay)]
        vf += ";[bg][1:v]overlay=0:0:format=auto[v]"
    else:
        vf += ";[bg]null[v]"
    cmd += ["-filter_complex", vf, "-map", "[v]", "-t", f"{dur:.3f}", "-an",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", str(out)]
    _run(cmd)


def _clip_from_image(src: Path, dur: float, w: int, h: int, fps: int, overlay: Path | None, out: Path) -> None:
    frames = max(1, int(math.ceil(dur * fps)))
    zoom = f"zoompan=z='min(zoom+0.0005,1.12)':d={frames}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={w}x{h}:fps={fps}"
    vf = f"[0:v]scale={int(w * 1.3)}:{int(h * 1.3)}:force_original_aspect_ratio=increase,crop={int(w * 1.3)}:{int(h * 1.3)},{zoom},setsar=1[bg]"
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src)]
    if overlay:
        cmd += ["-i", str(overlay)]
        vf += ";[bg][1:v]overlay=0:0:format=auto[v]"
    else:
        vf += ";[bg]null[v]"
    cmd += ["-filter_complex", vf, "-map", "[v]", "-frames:v", str(frames), "-an",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", str(out)]
    _run(cmd)


def _section_visual(cfg: dict, s: dict, i: int, w: int, h: int, workdir: Path, portrait: bool, chart_png: Path | None) -> tuple[str, Path]:
    """Decide what the viewer sees during a section. Returns (kind, path)."""
    st = workdir / f"vis_{i:02d}"
    if s.get("stat") and s["stat"].get("value"):
        return "image", visuals.stat_card(cfg, s["stat"].get("label", ""), str(s["stat"]["value"]), w, h, st.with_suffix(".png"))
    if chart_png is not None:
        return "image", chart_png
    if cfg["video"].get("b_roll", "auto") != "off":
        v = visuals.pexels_video(s.get("visual_query", "finance"), s["duration"], st.with_suffix(".mp4"), portrait=portrait)
        if v:
            return "video", v
        p = visuals.pexels_photo(s.get("visual_query", "finance"), st.with_suffix(".jpg"), portrait=portrait)
        if p:
            return "image", p
    return "image", visuals.slide(cfg, s["heading"], w, h, st.with_suffix(".png"))


def _assemble(cfg: dict, sections: list[dict], w: int, h: int, workdir: Path, out_mp4: Path, portrait: bool, chart: dict | None, caption_size: int) -> Path:
    fps = cfg["video"]["fps"]
    chart_png = None
    chart_section = None
    if chart:
        chart_png = visuals.chart_image(cfg, chart, w, h, workdir / "chart.png")
        if chart_png and len(sections) >= 3:
            chart_section = min(3, len(sections) - 2)  # show the chart in the middle of the video

    clips = []
    for i, s in enumerate(sections):
        dur = s["duration"] + GAP
        kind, src = _section_visual(cfg, s, i, w, h, workdir, portrait, chart_png if i == chart_section else None)
        overlay = visuals.lower_third(cfg, s["heading"], w, h, workdir / f"lt_{i:02d}.png") if s.get("heading") else None
        clip = workdir / f"clip_{i:02d}.mp4"
        (_clip_from_video if kind == "video" else _clip_from_image)(Path(src), dur, w, h, fps, overlay, clip)
        clips.append(clip)

    lst = workdir / "concat.txt"
    lst.write_text("".join(f"file '{c.name}'\n" for c in clips), encoding="utf-8")
    silent = workdir / "silent.mp4"
    _run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", "concat.txt", "-c", "copy", silent.name], cwd=workdir)

    audio = workdir / "voice.m4a"
    concat_audio([Path(s["audio"]) for s in sections], audio, GAP)

    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", silent.name, "-i", audio.name]
    if cfg["video"].get("captions", True):
        srt = build_srt(sections, workdir / "captions.srt")
        style = (f"FontName=DejaVu Sans,FontSize={caption_size},Bold=1,PrimaryColour=&H00FFFFFF,"
                 f"OutlineColour=&H00101010,BorderStyle=1,Outline=3,Shadow=1,Alignment=2,MarginV={110 if portrait else 40}")
        cmd += ["-vf", f"subtitles={srt.name}:force_style='{style}'"]
        cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", "19", "-pix_fmt", "yuv420p"]
    else:
        cmd += ["-c:v", "copy"]
    cmd += ["-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", str(out_mp4)]
    _run(cmd, cwd=workdir)
    return out_mp4


# ---------------------------------------------------------------- public API

def build_long_video(cfg: dict, script: dict, workdir: Path) -> dict:
    workdir.mkdir(parents=True, exist_ok=True)
    w, h = cfg["video"]["width"], cfg["video"]["height"]
    sections = synthesize_sections(cfg, script["sections"], workdir)
    out = _assemble(cfg, sections, w, h, workdir, workdir / "long.mp4", portrait=False, chart=script.get("chart"), caption_size=15)
    total = ffprobe_duration(out)
    # timestamps for the description
    t, stamps = 0.0, []
    for s in sections:
        stamps.append(f"{int(t // 60):02d}:{int(t % 60):02d} {s['heading']}")
        t += s["duration"] + GAP
    return {"path": str(out), "duration": total, "timestamps": stamps, "sections": sections}


def build_shorts(cfg: dict, script: dict, workdir: Path) -> list[dict]:
    w, h = cfg["shorts"]["width"], cfg["shorts"]["height"]
    max_s = cfg["shorts"]["max_seconds"]
    results = []
    for k, sh in enumerate(script.get("shorts", [])[: cfg["shorts"]["count"]]):
        wd = workdir / f"short_{k}"
        wd.mkdir(parents=True, exist_ok=True)
        mp3 = wd / "sec_00.mp3"
        info = synthesize(cfg, sh["narration"], mp3)
        if info["duration"] > max_s - 1:  # too long -> speak faster once
            fast = {**cfg, "voice": {**cfg["voice"], "rate": "+14%"}}
            info = synthesize(fast, sh["narration"], mp3)
        sec = {"heading": sh.get("hook_title", ""), "narration": sh["narration"], "visual_query": sh.get("visual_query", "finance"),
               "stat": None, "audio": str(mp3), "duration": info["duration"], "words": info["words"]}
        # libass scales FontSize by video_height/288 (SRT default PlayResY), so 17 ~= 113 px on a 1920-tall Short.
        out = _assemble(cfg, [sec], w, h, wd, wd / "short.mp4", portrait=True, chart=None, caption_size=17)
        results.append({"path": str(out), "duration": ffprobe_duration(out), "title": sh.get("hook_title", script["title"])[:90]})
    return results


def thumbnail(cfg: dict, script: dict, workdir: Path) -> Path:
    from PIL import Image, ImageDraw, ImageEnhance
    from .config import hex_to_rgb

    W, H = 1280, 720
    st = cfg["style"]
    bg_path = visuals.pexels_photo(script["sections"][0].get("visual_query", "finance"), workdir / "thumb_bg.jpg")
    if bg_path:
        bg = Image.open(bg_path).convert("RGB")
        bg = bg.resize((max(W, int(bg.width * H / bg.height)), H)).crop((0, 0, W, H))
        bg = ImageEnhance.Brightness(bg).enhance(0.45)
    else:
        bg = visuals.gradient(W, H, st["bg_dark2"], st["bg_dark"])
    d = ImageDraw.Draw(bg)
    text = script.get("thumbnail_text", script["title"])[:28].upper()
    size = 150
    f = visuals.font(cfg, size)
    while d.textlength(text, font=f) > W * 0.9 and size > 60:
        size -= 6
        f = visuals.font(cfg, size)
    d.text((W / 2, H / 2 - 20), text, font=f, fill=hex_to_rgb(st["accent2"]), anchor="mm", stroke_width=10, stroke_fill=(0, 0, 0))
    d.rectangle([0, H - 26, W, H], fill=hex_to_rgb(st["accent"]))
    d.text((W - 30, H - 50), cfg["channel"]["name"].upper(), font=visuals.font(cfg, 30), fill=(255, 255, 255), anchor="rm")
    out = workdir / "thumbnail.jpg"
    bg.save(out, "JPEG", quality=88, optimize=True)
    return out
