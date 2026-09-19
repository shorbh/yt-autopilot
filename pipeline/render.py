"""Beat-engine renderer.

Section narration -> TTS (word timings) -> storyboard beats (1-2 sentences each, with a visual)
-> one animated clip per beat, timed to the exact word the voice is on -> concat -> voice
(+ optional music bed) -> karaoke captions burned in.

Everything is plain ffmpeg + PIL frame sequences; no extra dependencies.
"""
from __future__ import annotations

import glob
import os
import random
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import assets_remote, motion, visuals
from .config import ROOT, hex_to_rgb
from .storyboard import storyboard
from .tts import concat_audio, ffprobe_duration, synthesize, synthesize_sections

GAP = 0.35            # silence between sections (audio) — mirrored in video timing
CHAPTER_SECS = 1.3    # chapter title card length (audio is delayed by the same amount)
OUTRO_SECS = 6.0      # end screen (subscribe CTA); audio is padded with silence to match
FADE = 0.25           # fade-in on every cut
MIN_BEAT = 1.4        # beats shorter than this are merged into the previous one

# Performance: intermediates are re-encoded in the final pass, so encode them as fast as possible
# at high quality; the final pass uses veryfast (YouTube re-encodes anyway - medium buys nothing).
WORKERS = max(2, min(4, os.cpu_count() or 2))          # parallel beat clips (GitHub runners: 4 vCPU)
INTER = ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "16", "-pix_fmt", "yuv420p", "-threads", "2"]
FINAL = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p"]


def _run(cmd: list[str], cwd: Path | None = None) -> None:
    res = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError("ffmpeg failed:\n" + " ".join(cmd) + "\n" + res.stderr[-2500:])


# ------------------------------------------------------------------ timing

def _estimate_words(narration: str, duration: float) -> list[dict]:
    toks = narration.split()
    if not toks:
        return []
    weights = [len(t) + 1 for t in toks]
    total = float(sum(weights))
    t, out = 0.0, []
    for tok, wgt in zip(toks, weights):
        d = duration * wgt / total
        out.append({"start": t, "end": t + d, "text": tok})
        t += d
    return out


def _align_beats(section: dict, beats: list[dict]) -> list[dict]:
    """Give each beat start/dur (seconds, relative to the section audio) from TTS word timings.
    Token index -> word-boundary index is mapped proportionally, which is robust to the TTS
    engine tokenising '$500' or 'day-three' differently from str.split()."""
    words = section.get("words") or _estimate_words(section["narration"], section["duration"])
    toks_total = max(1, len(section["narration"].split()))
    dur = section["duration"]

    def t_at(tok_idx: int) -> float:
        if not words:
            return dur * tok_idx / toks_total
        i = min(len(words) - 1, int(round(tok_idx * len(words) / toks_total)))
        return max(0.0, min(dur, words[i]["start"]))

    tok_cursor, timed = 0, []
    for b in beats:
        start = t_at(tok_cursor) if tok_cursor else 0.0
        tok_cursor += len(b["text"].split())
        timed.append({**b, "start": start})
    for i, b in enumerate(timed):
        nxt = timed[i + 1]["start"] if i + 1 < len(timed) else dur + GAP
        b["dur"] = max(0.1, nxt - b["start"])
    # merge too-short beats into the previous one
    merged: list[dict] = []
    for b in timed:
        if merged and b["dur"] < MIN_BEAT:
            merged[-1]["dur"] += b["dur"]
            merged[-1]["text"] += " " + b["text"]
        else:
            merged.append(b)
    if len(merged) > 1 and merged[0]["dur"] < MIN_BEAT:  # first one too short: fold forward
        merged[1]["start"] = merged[0]["start"]
        merged[1]["dur"] += merged[0]["dur"]
        merged.pop(0)
    return merged


# ------------------------------------------------------------------ clip builders

def _vf_common(w: int, h: int, fps: int) -> str:
    return f"fps={fps},setsar=1,fade=t=in:d={FADE}"


def _nframes(dur: float, fps: int) -> int:
    """Exact frame count for a clip. Using -frames:v instead of -t avoids the 0..1-frame overshoot
    per clip that -t produces, which would accumulate into visible A/V drift across ~60 beats."""
    return max(1, int(round(dur * fps)))


def _clip_from_frames(frames_dir: Path, n: int, dur: float, w: int, h: int, fps: int, overlay: Path | None, out: Path) -> None:
    anim = n / motion.ANIM_FPS
    hold = max(0.0, dur - anim + 0.5)
    vf = f"[0:v]fps={fps},tpad=stop_mode=clone:stop_duration={hold:.3f},setsar=1,fade=t=in:d={FADE}[bg]"
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(motion.ANIM_FPS), "-i", str(frames_dir / "%04d.png")]
    if overlay:
        cmd += ["-i", str(overlay)]
        vf += ";[bg][1:v]overlay=0:0:format=auto[v]"
    else:
        vf += ";[bg]null[v]"
    cmd += ["-filter_complex", vf, "-map", "[v]", "-frames:v", str(_nframes(dur, fps)), "-an", *INTER, str(out)]
    _run(cmd)


def _clip_from_video(src: Path, dur: float, w: int, h: int, fps: int, overlay: Path | None, out: Path) -> None:
    vf = (f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
          f"eq=brightness=-0.12:saturation=0.9,{_vf_common(w, h, fps)}[bg]")
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-stream_loop", "-1", "-i", str(src)]
    if overlay:
        cmd += ["-i", str(overlay)]
        vf += ";[bg][1:v]overlay=0:0:format=auto[v]"
    else:
        vf += ";[bg]null[v]"
    cmd += ["-filter_complex", vf, "-map", "[v]", "-frames:v", str(_nframes(dur, fps)), "-an", *INTER, str(out)]
    _run(cmd)


def _clip_from_image(src: Path, dur: float, w: int, h: int, fps: int, overlay: Path | None, out: Path) -> None:
    frames = _nframes(dur, fps)
    zoom = f"zoompan=z='min(zoom+0.0006,1.14)':d={frames}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={w}x{h}:fps={fps}"
    vf = (f"[0:v]scale={int(w * 1.3)}:{int(h * 1.3)}:force_original_aspect_ratio=increase,crop={int(w * 1.3)}:{int(h * 1.3)},"
          f"{zoom},eq=brightness=-0.1,setsar=1,fade=t=in:d={FADE}[bg]")
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src)]
    if overlay:
        cmd += ["-i", str(overlay)]
        vf += ";[bg][1:v]overlay=0:0:format=auto[v]"
    else:
        vf += ";[bg]null[v]"
    cmd += ["-filter_complex", vf, "-map", "[v]", "-frames:v", str(frames), "-an", *INTER, str(out)]
    _run(cmd)


class _BrollCache:
    """One Pexels download per query per video (saves API calls and time). Thread-safe."""

    def __init__(self, workdir: Path, portrait: bool):
        self.dir = workdir / "broll"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.portrait = portrait
        self.cache: dict[str, tuple[str, Path] | None] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    def get(self, query: str, dur: float) -> tuple[str, Path] | None:
        # Key on the file slug (not the raw query) so two queries that map to the same file name
        # ("finance desk" / "finance-desk") share one lock and never write the same file concurrently.
        slug = "".join(c if c.isalnum() else "_" for c in query.lower().strip())[:40]
        with self._guard:
            lock = self._locks.setdefault(slug, threading.Lock())
        with lock:  # two beats with the same query wait for one download instead of racing
            return self._get(slug, query, dur)

    def _get(self, slug: str, query: str, dur: float) -> tuple[str, Path] | None:
        if slug in self.cache:
            return self.cache[slug]
        v = visuals.pexels_video(query, dur, self.dir / f"{slug}.mp4", portrait=self.portrait)
        res = ("video", v) if v else None
        if res is None:
            p = visuals.pexels_photo(query, self.dir / f"{slug}.jpg", portrait=self.portrait)
            res = ("image", p) if p else None
        self.cache[slug] = res
        return res

    def photo(self, query: str) -> Path | None:
        """Photo only (for photo_text cards). Cached separately from video b-roll."""
        slug = "ph_" + "".join(c if c.isalnum() else "_" for c in query.lower().strip())[:40]
        with self._guard:
            lock = self._locks.setdefault(slug, threading.Lock())
        with lock:
            if slug in self.cache:
                return self.cache[slug][1] if self.cache[slug] else None
            p = visuals.pexels_photo(query, self.dir / f"{slug}.jpg", portrait=self.portrait)
            self.cache[slug] = ("image", p) if p else None
            return p


def _beat_clip(cfg: dict, beat: dict, idx: int, w: int, h: int, fps: int, workdir: Path,
               overlay: Path | None, chart: dict | None, broll: _BrollCache, heading: str) -> Path:
    out = workdir / f"beat_{idx:03d}.mp4"
    vis = beat["visual"]
    vtype = vis.get("type")
    fdir = workdir / f"frames_{idx:03d}"

    def _callout(text: str | None = None) -> dict:
        return {"type": "callout", "text": (text or beat["text"])[:90]}

    try:
        if vtype == "chart" and not chart:
            vtype, vis = "callout", _callout()
        if vtype == "chart" and chart:
            r = motion.chart_progressive(cfg, chart, w, h, fdir)
            if r:
                _clip_from_frames(r[0], r[1], beat["dur"], w, h, fps, overlay, out)
                return out
            vtype, vis = "callout", _callout()
        if vtype == "broll" and cfg["video"].get("b_roll", "auto") != "off":
            got = broll.get(vis.get("query", "finance desk"), beat["dur"])
            if got:
                kind, src = got
                (_clip_from_video if kind == "video" else _clip_from_image)(src, beat["dur"], w, h, fps, overlay, out)
                return out
            vtype, vis = "callout", _callout()
        elif vtype == "broll":
            vtype, vis = "callout", _callout()
        if vtype == "photo_text":
            photo = broll.photo(vis.get("query", "finance desk")) if cfg["video"].get("b_roll", "auto") != "off" else None
            r = motion.photo_text(cfg, vis, w, h, fdir, photo, variant=idx)
            if r:
                _clip_from_frames(r[0], r[1], beat["dur"], w, h, fps, overlay, out)
                return out
            vtype, vis = "callout", _callout(vis.get("text"))
        if vtype == "character":
            r = motion.character(cfg, vis, w, h, fdir, variant=idx)
            if r:
                _clip_from_frames(r[0], r[1], beat["dur"], w, h, fps, overlay, out)
                return out
            vtype, vis = "callout", _callout(vis.get("text") or None)
        if vtype in motion.RENDERERS:
            fd, n = motion.RENDERERS[vtype](cfg, vis, w, h, fdir, variant=idx)
            _clip_from_frames(fd, n, beat["dur"], w, h, fps, overlay, out)
            return out
    except Exception as e:  # noqa: BLE001 - a single bad beat must not kill the video
        print(f"[warn] beat {idx} ({vtype}) failed: {str(e)[-300:]} — using slide")

    slide = visuals.slide(cfg, heading, w, h, workdir / f"slide_{idx:03d}.png", subtitle=beat["text"][:80])
    _clip_from_image(slide, beat["dur"], w, h, fps, overlay, out)
    return out


# ------------------------------------------------------------------ captions (ASS karaoke)

def _ass_colour(hex_rgb: str, alpha: int = 0) -> str:
    r, g, b = hex_to_rgb(hex_rgb)
    return f"&H{alpha:02X}{b:02X}{g:02X}{r:02X}"


def _ass_time(t: float) -> str:
    cs = max(0, int(round(t * 100)))  # integer centiseconds: avoids '60.00' from float rounding
    h, rem = divmod(cs, 360000)
    m, rem = divmod(rem, 6000)
    s, c = divmod(rem, 100)
    return f"{h:d}:{m:02d}:{s:02d}.{c:02d}"


def build_ass(cfg: dict, sections: list[dict], offsets: list[float], w: int, h: int, out: Path,
              portrait: bool, max_words: int = 3, max_span: float = 1.6) -> Path:
    """Word-highlight ('karaoke') captions: words turn accent-coloured as they are spoken."""
    st = cfg["style"]
    size = int(h * (0.052 if not portrait else 0.038))
    margin_v = int(h * (0.07 if not portrait else 0.30))
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,DejaVu Sans,{size},{_ass_colour(st['accent'])},{_ass_colour('#FFFFFF')},{_ass_colour('#101010')},{_ass_colour('#000000', 128)},-1,0,0,0,100,100,1,0,1,{max(3, size // 14)},1,2,40,40,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = []
    for s, off in zip(sections, offsets):
        words = s.get("words") or _estimate_words(s["narration"], s["duration"])
        # ensure each word has an end no later than the next word's start (karaoke needs contiguous k-times)
        for i, wd in enumerate(words):
            nxt = words[i + 1]["start"] if i + 1 < len(words) else min(s["duration"], wd["end"] + 0.4)
            wd["_end"] = max(wd["start"] + 0.05, min(wd["end"] + 0.15, nxt))
        chunk: list[dict] = []

        def flush():
            if not chunk:
                return
            st_t = chunk[0]["start"] + off
            en_t = chunk[-1]["_end"] + off
            parts = []
            for i, wd in enumerate(chunk):
                seg_end = chunk[i + 1]["start"] if i + 1 < len(chunk) else chunk[-1]["_end"]
                k = max(1, int(round((seg_end - wd["start"]) * 100)))
                safe = str(wd["text"]).replace("{", "(").replace("}", ")")  # braces would open an ASS override block
                parts.append(f"{{\\k{k}}}{safe}")
            lines.append(f"Dialogue: 0,{_ass_time(st_t)},{_ass_time(en_t)},Cap,,0,0,0,,{' '.join(parts)}")
            chunk.clear()

        for wd in words:
            if chunk and (len(chunk) >= max_words or wd["_end"] - chunk[0]["start"] > max_span):
                flush()
            chunk.append(wd)
        flush()
    out.write_text(header + "\n".join(lines) + "\n", encoding="utf-8")
    return out


# ------------------------------------------------------------------ assembly

def _music_track(cfg: dict) -> Path | None:
    if not cfg["video"].get("music", False):
        return None
    files = glob.glob(str(ROOT / "assets" / "music" / "*.mp3")) + glob.glob(str(ROOT / "assets" / "music" / "*.m4a"))
    return Path(random.choice(files)) if files else None


def _assemble(cfg: dict, sections: list[dict], w: int, h: int, workdir: Path, out_mp4: Path,
              portrait: bool, chart: dict | None, chapters: bool,
              beats_by_id: dict | None = None) -> tuple[Path, list[float]]:
    """Returns (video path, per-section start offsets in seconds)."""
    fps = cfg["video"]["fps"]
    if beats_by_id is None:
        beats_by_id = storyboard(cfg, sections, chart)
    broll = _BrollCache(workdir, portrait)
    outro = chapters and cfg["video"].get("outro", True)
    # 1) Plan every clip (cheap, sequential) ...
    jobs: list = []          # callables producing a clip path, in playback order
    offsets: list[float] = []
    lead_in: list[float] = []
    all_beats: list[dict] = []
    t = 0.0
    idx = 0
    for si, s in enumerate(sections):
        card = chapters and 0 < si < len(sections) - 1
        lead_in.append(CHAPTER_SECS if card else 0.0)
        if card:
            def _chapter(si=si, s=s):
                fd, n = motion.chapter_card(cfg, s["heading"], si, w, h, workdir / f"frames_ch{si}")
                c = workdir / f"chapter_{si:02d}.mp4"
                _clip_from_frames(fd, n, CHAPTER_SECS, w, h, fps, None, c)
                return c
            jobs.append(_chapter)
            t += CHAPTER_SECS
        offsets.append(t)
        overlay = (visuals.lower_third(cfg, s["heading"], w, h, workdir / f"lt_{si:02d}.png", center=portrait)
                   if s.get("heading") else None)
        beats = _align_beats(s, beats_by_id.get(s["id"], []))
        for b in beats:
            def _beat(b=b, idx=idx, overlay=overlay, heading=s["heading"]):
                return _beat_clip(cfg, b, idx, w, h, fps, workdir, overlay, chart, broll, heading)
            jobs.append(_beat)
            all_beats.append(b)
            idx += 1
        t += s["duration"] + GAP
    if outro:
        def _outro():
            fd, n = motion.outro_card(cfg, w, h, workdir / "frames_outro")
            c = workdir / "outro.mp4"
            _clip_from_frames(fd, n, OUTRO_SECS, w, h, fps, None, c)
            return c
        jobs.append(_outro)

    types = [b["visual"].get("type") for b in all_beats]
    mix = ", ".join(f"{types.count(x)} {x}" for x in sorted(set(types), key=types.index))
    print(f"      storyboard: {len(all_beats)} beats across {len(sections)} sections "
          f"(avg {sum(s['duration'] for s in sections) / max(1, len(all_beats)):.1f}s per visual): {mix}; "
          f"rendering with {WORKERS} workers")

    # Warm the remote-asset caches once, concurrently, so beat workers never wait on the network
    # for the same icon/character twice.
    warm = set()
    for b in all_beats:
        v = b["visual"]
        sides = [v.get(s) for s in ("left", "right") if isinstance(v.get(s), dict)]
        for ic in [v.get("icon")] + list(v.get("icons") or []) + [sd.get("icon") for sd in sides]:
            if isinstance(ic, str) and ic:
                warm.add(("icon", ic))
        for ch in [v.get("character")] + [sd.get("character") for sd in sides]:
            if isinstance(ch, dict) and ch.get("name"):
                warm.add(("peep", str(ch.get("name")), str(ch.get("gender") or "neutral"), str(ch.get("mood") or "neutral")))
    # Warm at the exact base sizes the renderers use, so the first beat that needs an asset hits the
    # rasterised cache too (not only the SVG cache).
    accent = hex_to_rgb(cfg["style"]["accent"])
    with ThreadPoolExecutor(max_workers=6) as pool:
        for item in warm:
            if item[0] == "icon":
                pool.submit(assets_remote.icon, item[1], motion.ICON_BASE, accent)
            else:
                pool.submit(assets_remote.peep, item[1], item[2], item[3], motion.PEEP_BASE)

    # 2) ... then render them in parallel (PIL and ffmpeg both release the GIL), keeping order.
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        clips: list[Path] = list(pool.map(lambda job: job(), jobs))

    lst = workdir / "concat.txt"
    lst.write_text("".join(f"file '{c.name}'\n" for c in clips), encoding="utf-8")
    silent = workdir / "silent.mp4"
    _run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", "concat.txt", "-c", "copy", silent.name], cwd=workdir)

    audio = workdir / "voice.m4a"
    concat_audio([Path(s["audio"]) for s in sections], audio, GAP, lead_in=lead_in, tail_pad=OUTRO_SECS if outro else 0.0)

    music = _music_track(cfg)
    base = ["ffmpeg", "-y", "-loglevel", "error", "-i", silent.name, "-i", audio.name]
    afilter: list[str] = []
    if music:
        base += ["-stream_loop", "-1", "-i", str(music)]
        # music bed with real sidechain ducking: the voice compresses the music while speaking, music
        # breathes back up in pauses (-20 dB base level; ducks a further ~12 dB under speech)
        afilter = ["-filter_complex",
                   "[1:a]asplit=2[v][sc];[2:a]volume=0.12[m];[m][sc]sidechaincompress=threshold=0.02:ratio=8:attack=40:release=500[md];"
                   "[v][md]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[a]",
                   "-map", "0:v", "-map", "[a]"]
    tail = ["-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", str(out_mp4)]

    # Shorts: thin progress bar along the bottom (standard retention device on vertical video).
    # drawbox cannot animate (its expressions are evaluated once; `t` there means thickness), so a
    # full-width colour strip slides in from the left via overlay, whose x expression is per-frame.
    bar = ""
    if portrait:
        total = sum(s["duration"] + GAP for s in sections)
        r, g, b = hex_to_rgb(cfg["style"]["accent"])
        bar = (f"[v];color=c=0x{r:02X}{g:02X}{b:02X}@0.9:s={w}x14:r={fps}[bar];"
               f"[v][bar]overlay=x='-w+w*min(1\\,t/{total:.2f})':y=H-h:format=auto")

    if cfg["video"].get("captions", True):
        ass = build_ass(cfg, sections, offsets, w, h, workdir / "captions.ass", portrait)
        n_words = sum(len(s.get("words") or []) for s in sections)
        print(f"      captions: {ass.stat().st_size} bytes, {n_words} timed words from TTS" + ("" if n_words else " (estimated timings)"))
        for vf in dict.fromkeys((f"ass={ass.name}" + bar, f"ass={ass.name}")):  # de-duplicated, ordered
            try:
                _run(base + ["-vf", vf, *FINAL] + afilter + tail, cwd=workdir)
                return out_mp4, offsets
            except RuntimeError as e:
                print(f"[warn] final pass failed with filters {vf}: {str(e)[-300:]}")

    _run(base + ["-c:v", "copy"] + afilter + tail, cwd=workdir)
    return out_mp4, offsets


# ------------------------------------------------------------------ public API

def build_long_video(cfg: dict, script: dict, workdir: Path) -> dict:
    workdir.mkdir(parents=True, exist_ok=True)
    w, h = cfg["video"]["width"], cfg["video"]["height"]
    sections = synthesize_sections(cfg, script["sections"], workdir)
    out, offsets = _assemble(cfg, sections, w, h, workdir, workdir / "long.mp4", portrait=False,
                             chart=script.get("chart"), chapters=True)
    total = ffprobe_duration(out)
    stamps = [f"{int(o // 60):02d}:{int(o % 60):02d} {s['heading']}" for s, o in zip(sections, offsets)]
    if stamps:
        stamps[0] = "00:00 " + sections[0]["heading"]  # YouTube requires the first chapter at 00:00
    return {"path": str(out), "duration": total, "timestamps": stamps, "sections": sections}


def build_shorts(cfg: dict, script: dict, workdir: Path) -> list[dict]:
    w, h = cfg["shorts"]["width"], cfg["shorts"]["height"]
    max_s = cfg["shorts"]["max_seconds"]
    shorts = script.get("shorts", [])[: cfg["shorts"]["count"]]

    def _voice(k_sh):
        k, sh = k_sh
        wd = workdir / f"short_{k}"
        wd.mkdir(parents=True, exist_ok=True)
        mp3 = wd / "sec_00.mp3"
        info = synthesize(cfg, sh["narration"], mp3)
        if info["duration"] > max_s - 1:  # too long -> speak faster once
            fast = {**cfg, "voice": {**cfg["voice"], "rate": "+14%"}}
            info = synthesize(fast, sh["narration"], mp3)
        return {"id": f"short_{k}", "heading": sh.get("hook_title", ""), "narration": sh["narration"],
                "audio": str(mp3), "duration": info["duration"], "words": info["words"], "_wd": wd}

    # TTS for all Shorts concurrently, then ONE storyboard call for all of them (saves 2 LLM calls).
    with ThreadPoolExecutor(max_workers=3) as pool:
        secs = list(pool.map(_voice, enumerate(shorts)))
    beats_all = storyboard(cfg, secs, None)

    results = []
    for sec, sh in zip(secs, shorts):
        wd = sec.pop("_wd")
        out, _ = _assemble(cfg, [sec], w, h, wd, wd / "short.mp4", portrait=True, chart=None, chapters=False,
                           beats_by_id={sec["id"]: beats_all.get(sec["id"], [])})
        results.append({"path": str(out), "duration": ffprobe_duration(out), "title": sh.get("hook_title", script["title"])[:90]})
    return results


def thumbnail(cfg: dict, script: dict, workdir: Path) -> Path:
    """Primary thumbnail (A) + two variants (B: different expression, C: alt title text, no character)
    for YouTube Studio's Test & Compare, which the API cannot drive. Returns the path of A."""
    a = _thumbnail(cfg, script, workdir, workdir / "thumbnail.jpg", mood=None, text=None, character=True)
    try:
        alt_mood = "worried" if str(script.get("thumbnail_mood") or "shocked") != "worried" else "excited"
        _thumbnail(cfg, script, workdir, workdir / "thumbnail_B.jpg", mood=alt_mood, text=None, character=True)
        alt_text = (script.get("alt_titles") or [None])[0]
        _thumbnail(cfg, script, workdir, workdir / "thumbnail_C.jpg", mood=None, text=alt_text, character=False)
    except Exception as e:  # noqa: BLE001 - variants are a bonus, never fatal
        print(f"[warn] thumbnail variants failed: {str(e)[:120]}")
    return a


def _thumbnail(cfg: dict, script: dict, workdir: Path, out: Path, mood: str | None, text: str | None, character: bool) -> Path:
    """Split layout: bold text panel on the left, dimmed photo fading in on the right."""
    from PIL import Image, ImageDraw, ImageEnhance

    W, H = 1280, 720
    st = cfg["style"]
    bg = visuals.gradient(W, H, st["bg_dark"], st["bg_dark2"])
    q = script.get("thumbnail_query") or script["sections"][0].get("visual_query", "finance")
    photo_path = workdir / "thumb_bg.jpg"
    if not photo_path.exists():
        photo_path = visuals.pexels_photo(q, photo_path)
    if photo_path:
        ph = Image.open(photo_path).convert("RGB")
        ph = ph.resize((max(W, int(ph.width * H / ph.height)), H))
        ph = ph.crop((max(0, (ph.width - W) // 2), 0, max(0, (ph.width - W) // 2) + W, H))
        ph = ImageEnhance.Brightness(ph).enhance(0.7)
        # alpha ramp: transparent on the left 45%, opaque on the right
        mask = Image.linear_gradient("L").rotate(90, expand=True).resize((W, H))  # dark->light left->right
        mask = mask.point(lambda v: max(0, min(255, int((v - 90) * 2.2))))
        bg = Image.composite(ph, bg, mask)
    # Emotional face (the strongest CTR lever): a hand-drawn character with a surprised/worried expression
    # on the right third, in front of the faded photo.
    has_char = False
    if character and cfg["video"].get("thumbnail_character", True):
        mood = str(mood or script.get("thumbnail_mood") or "shocked")
        who = script.get("thumbnail_character")
        if not isinstance(who, dict):
            who = {"name": script.get("title", "viewer"), "gender": "neutral"}
        pp = assets_remote.peep(str(who.get("name") or "viewer"), str(who.get("gender") or "neutral"), mood, motion.PEEP_BASE)
        if pp is not None:
            pp = pp.resize((620, 620), Image.LANCZOS)
            bg.paste(pp, (W - 560, H - 560), pp)  # overflows right/bottom edges; PIL clips (Peeps are half-body)
            has_char = True
    d = ImageDraw.Draw(bg)
    text = str(text or script.get("thumbnail_text") or script["title"])[:30].upper()
    words = text.split()
    line1, line2 = (" ".join(words[: max(1, len(words) // 2)]), " ".join(words[max(1, len(words) // 2):])) if len(words) > 2 else (text, "")
    size = 150
    f = visuals.font(cfg, size)
    while max(d.textlength(line1, font=f), d.textlength(line2, font=f)) > W * 0.62 and size > 64:
        size -= 6
        f = visuals.font(cfg, size)
    x, y = 60, H / 2 - (size * 1.15 if line2 else size * 0.5)
    d.text((x, y), line1, font=f, fill=hex_to_rgb(st["accent2"]), stroke_width=8, stroke_fill=(0, 0, 0))
    if line2:
        d.text((x, y + size * 1.15), line2, font=f, fill=(255, 255, 255), stroke_width=8, stroke_fill=(0, 0, 0))
    d.rectangle([0, H - 22, W, H], fill=hex_to_rgb(st["accent"]))
    # channel tag: bottom-right normally; bottom-left when the character occupies the right third
    tag_xy, tag_anchor = ((28, H - 46), "lm") if has_char else ((W - 28, H - 46), "rm")
    d.text(tag_xy, cfg["channel"]["name"].upper(), font=visuals.font(cfg, 28), fill=(255, 255, 255), anchor=tag_anchor,
           stroke_width=3, stroke_fill=(0, 0, 0))
    bg.save(out, "JPEG", quality=88, optimize=True)
    return out
