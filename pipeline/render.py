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
from .storyboard import _key_phrase, storyboard
from .tts import _eleven_available, concat_audio, ffprobe_duration, provider_for, spoken_text, synthesize, synthesize_sections

GAP = 0.9             # silence between sections (audio) — mirrored in video timing. v6.1: a real threshold between topics
CHAPTER_SECS = 1.1    # chapter title card length (audio is delayed by the same amount): long enough to register as a new topic
MAP_CARD_SECS = 1.8   # progress-map card between sections of a MAP video (the grid needs a beat to be read)
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
        merged[1]["visual"] = merged[0]["visual"]   # the opening visual (e.g. a Shorts hook card) must survive the fold
        merged.pop(0)
    return merged


# ------------------------------------------------------------------ clip builders

FADE_COLOR = "0x0B1020"   # fade from the brand navy, not black: a black dip on a navy card read as a dropout
DRIFT = 0.05              # continuous slow zoom over each card's life (5%): nothing on screen ever fully stops


def _fade(fade: bool) -> str:
    """Edit rhythm (v5): HARD CUTS between beats; a short fade only where `fade` is requested (section changes).
    Fades everywhere read as slow — a cut lands on the first word."""
    return f",fade=t=in:d={FADE}:color={FADE_COLOR}" if fade else ""


def _drift(w: int, h: int, dur: float) -> str:
    """Per-frame slow push-in via `scale` with eval=frame (far cheaper than zoompan on video), then centre-crop.
    Even dimensions are forced so yuv420p stays happy."""
    if dur < 2.5:   # too short for a push-in to register; saves the per-frame rescale
        return ""
    k = f"(1+{DRIFT}*min(t/{dur:.3f}\\,1))"
    # crop freezes in_w/in_h at config time, so its default centring would be (W-W)/2 = 0 -> a top-left-anchored
    # zoom (content sliding toward the corner). Recompute the centre from the same t-formula instead.
    # fast_bilinear: run #16 showed bicubic per-frame rescaling doubled render time; at a 5% zoom the difference is invisible.
    return (f",scale=w='trunc(iw*{k}/2)*2':h='trunc(ih*{k}/2)*2':eval=frame:flags=fast_bilinear,"
            f"crop={w}:{h}:x='(trunc(in_w*{k}/2)*2-in_w)/2':y='(trunc(in_h*{k}/2)*2-in_h)/2',setsar=1")


def _nframes(dur: float, fps: int) -> int:
    """Exact frame count for a clip. Using -frames:v instead of -t avoids the 0..1-frame overshoot
    per clip that -t produces, which would accumulate into visible A/V drift across ~60 beats."""
    return max(1, int(round(dur * fps)))


def _clip_from_frames(frames_dir: Path, n: int, dur: float, w: int, h: int, fps: int, overlay: Path | None, out: Path,
                      fade: bool = False, drift: bool = True) -> None:
    anim = n / motion.ANIM_FPS
    hold = max(0.0, dur - anim + 0.5)
    vf = (f"[0:v]fps={fps},tpad=stop_mode=clone:stop_duration={hold:.3f},setsar=1"
          f"{_drift(w, h, dur) if drift else ''}{_fade(fade)}[bg]")
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(motion.ANIM_FPS), "-i", str(frames_dir / "%04d.png")]
    if overlay:
        cmd += ["-i", str(overlay)]
        vf += ";[bg][1:v]overlay=0:0:format=auto[v]"
    else:
        vf += ";[bg]null[v]"
    cmd += ["-filter_complex", vf, "-map", "[v]", "-frames:v", str(_nframes(dur, fps)), "-an", *INTER, str(out)]
    _run(cmd)


def _clip_card_over_video(frames_dir: Path, n: int, src: Path, dur: float, w: int, h: int, fps: int,
                          overlay: Path | None, out: Path, fade: bool = False) -> None:
    """A transparent card (RGBA frame sequence) composited over looping, dimmed footage, then the lower third.
    This is the 'card over footage' beat: the information stays on screen while something real is moving."""
    anim = n / motion.ANIM_FPS
    hold = max(0.0, dur - anim + 0.5)
    vf = (f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
          f"eq=brightness=-0.22:saturation=0.75,fps={fps},setsar=1[bg];"
          f"[1:v]fps={fps},tpad=stop_mode=clone:stop_duration={hold:.3f},format=rgba[card];"
          f"[bg][card]overlay=0:0:format=auto{_fade(fade)}[v1]")
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-stream_loop", "-1", "-i", str(src),
           "-framerate", str(motion.ANIM_FPS), "-i", str(frames_dir / "%04d.png")]
    if overlay:
        cmd += ["-i", str(overlay)]
        vf += ";[v1][2:v]overlay=0:0:format=auto[v]"
    else:
        vf += ";[v1]null[v]"
    cmd += ["-filter_complex", vf, "-map", "[v]", "-frames:v", str(_nframes(dur, fps)), "-an", *INTER, str(out)]
    _run(cmd)


def _clip_from_video(src: Path, dur: float, w: int, h: int, fps: int, overlay: Path | None, out: Path, fade: bool = False) -> None:
    vf = (f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
          f"eq=brightness=-0.12:saturation=0.9,fps={fps},setsar=1{_fade(fade)}[bg]")
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-stream_loop", "-1", "-i", str(src)]
    if overlay:
        cmd += ["-i", str(overlay)]
        vf += ";[bg][1:v]overlay=0:0:format=auto[v]"
    else:
        vf += ";[bg]null[v]"
    cmd += ["-filter_complex", vf, "-map", "[v]", "-frames:v", str(_nframes(dur, fps)), "-an", *INTER, str(out)]
    _run(cmd)


def _clip_from_image(src: Path, dur: float, w: int, h: int, fps: int, overlay: Path | None, out: Path, fade: bool = False) -> None:
    frames = _nframes(dur, fps)
    zoom = f"zoompan=z='min(zoom+0.0006,1.14)':d={frames}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={w}x{h}:fps={fps}"
    vf = (f"[0:v]scale={int(w * 1.3)}:{int(h * 1.3)}:force_original_aspect_ratio=increase,crop={int(w * 1.3)}:{int(h * 1.3)},"
          f"{zoom},eq=brightness=-0.1,setsar=1{_fade(fade)}[bg]")
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

    def photo(self, query: str, key: str | None = None) -> Path | None:
        """Photo only (for photo_text cards). Cached separately from video b-roll. `key` overrides the
        cache slug so different queries can be pinned to one file (same person -> same face)."""
        slug = "ph_" + "".join(c if c.isalnum() else "_" for c in (key or query).lower().strip())[:40]
        with self._guard:
            lock = self._locks.setdefault(slug, threading.Lock())
        with lock:
            if slug in self.cache:
                return self.cache[slug][1] if self.cache[slug] else None
            p = visuals.pexels_photo(query, self.dir / f"{slug}.jpg", portrait=self.portrait)
            self.cache[slug] = ("image", p) if p else None
            return p

    def person(self, ch: dict | None) -> Path | None:
        """Portrait photo for a named character. The FIRST mood a person appears with fixes their face for
        the whole video (one download per name), so 'Sarah' is the same woman in every beat."""
        if not isinstance(ch, dict) or not ch.get("name"):
            return None
        q = assets_remote.person_query(ch.get("gender"), ch.get("mood"))
        return self.photo(q, key="person_" + str(ch["name"]).strip().lower())


def _beat_clip(cfg: dict, beat: dict, idx: int, w: int, h: int, fps: int, workdir: Path,
               overlay: Path | None, chart: dict | None, broll: _BrollCache, heading: str, fade: bool = False) -> Path:
    out = workdir / f"beat_{idx:03d}.mp4"
    vis = beat["visual"]
    vtype = vis.get("type")
    fdir = workdir / f"frames_{idx:03d}"
    dur = beat["dur"]

    def _callout(text: str | None = None) -> dict:
        return {"type": "callout", "text": (text or beat["text"])[:90]}

    try:
        if vtype == "chart" and not chart:
            vtype, vis = "callout", _callout()
        if vtype == "chart" and chart:
            r = motion.chart_progressive(cfg, chart, w, h, fdir)
            if r:
                _clip_from_frames(r[0], r[1], dur, w, h, fps, overlay, out, fade=fade, drift=False)   # axes must not drift
                return out
            vtype, vis = "callout", _callout()
        if vtype == "broll" and cfg["video"].get("b_roll", "auto") != "off":
            got = broll.get(vis.get("query", "finance desk"), dur)
            if got:
                kind, src = got
                # footage always carries the sentence's key phrase (plus the section lower third)
                phrase = str(vis.get("text") or _key_phrase(beat["text"]))
                ov = visuals.broll_overlay(cfg, phrase, w, h, workdir / f"ov_{idx:03d}.png", overlay)
                (_clip_from_video if kind == "video" else _clip_from_image)(src, dur, w, h, fps, ov, out, fade=fade)
                return out
            vtype, vis = "callout", _callout()
        elif vtype == "broll":
            vtype, vis = "callout", _callout()
        if vtype == "photo_text":
            photo = broll.photo(vis.get("query", "finance desk")) if cfg["video"].get("b_roll", "auto") != "off" else None
            r = motion.photo_text(cfg, vis, w, h, fdir, photo, variant=idx)
            if r:
                _clip_from_frames(r[0], r[1], dur, w, h, fps, overlay, out, fade=fade)
                return out
            vtype, vis = "callout", _callout(vis.get("text"))
        if vtype == "footage_text" or (vtype in ("bignumber", "icon_text", "callout") and vis.get("footage")):
            # the beat the user singled out: real moving footage with the card's information over it
            got = broll.get(str(vis.get("footage") or vis.get("query") or "city street timelapse"), dur) \
                if cfg["video"].get("b_roll", "auto") != "off" else None
            if got and got[0] == "video":
                card_type = "callout" if vtype == "footage_text" else vtype
                spec = {**vis, "theme": "transparent"}
                if card_type == "callout":
                    spec["text"] = str(vis.get("text") or _key_phrase(beat["text"]))
                fd, n = motion.RENDERERS[card_type](cfg, spec, w, h, fdir, variant=idx)
                _clip_card_over_video(fd, n, got[1], dur, w, h, fps, overlay, out, fade=fade)
                return out
            if vtype == "footage_text":                       # no footage available -> photo card with the same text
                vis = {"type": "photo_text", "text": str(vis.get("text") or _key_phrase(beat["text"])), "query": vis.get("query", "finance desk")}
                photo = broll.photo(vis["query"]) if cfg["video"].get("b_roll", "auto") != "off" else None
                r = motion.photo_text(cfg, vis, w, h, fdir, photo, variant=idx)
                if r:
                    _clip_from_frames(r[0], r[1], dur, w, h, fps, overlay, out, fade=fade)
                    return out
                vtype, vis = "callout", _callout(vis.get("text"))
            else:
                vis = {k: v for k, v in vis.items() if k != "footage"}   # fall through to the plain card
        if vtype == "map_grid":
            fd, n = motion.map_grid(cfg, vis, w, h, fdir, variant=idx)
            _clip_from_frames(fd, n, dur, w, h, fps, overlay, out, fade=fade, drift=False)
            return out
        if vtype == "character":
            photo = broll.person(vis.get("character")) if cfg["video"].get("b_roll", "auto") != "off" else None
            fd, n = motion.character(cfg, vis, w, h, fdir, photo=photo, variant=idx)
            _clip_from_frames(fd, n, dur, w, h, fps, overlay, out, fade=fade)
            return out
        if vtype == "hook":   # Shorts cold open: face photo + claim, no title overlay
            photo = broll.photo(vis.get("query", "surprised person portrait")) if cfg["video"].get("b_roll", "auto") != "off" else None
            fd, n = motion.hook_card(cfg, vis, w, h, fdir, photo=photo, variant=idx)
            _clip_from_frames(fd, n, dur, w, h, fps, None, out, fade=False)   # frame one must be readable
            return out
        if vtype in motion.RENDERERS:
            fd, n = motion.RENDERERS[vtype](cfg, vis, w, h, fdir, variant=idx)
            _clip_from_frames(fd, n, dur, w, h, fps, overlay, out, fade=fade)
            return out
    except Exception as e:  # noqa: BLE001 - a single bad beat must not kill the video
        print(f"[warn] beat {idx} ({vtype}) failed: {str(e)[-300:]} — using slide")

    slide = visuals.slide(cfg, heading, w, h, workdir / f"slide_{idx:03d}.png", subtitle=beat["text"][:80])
    _clip_from_image(slide, dur, w, h, fps, overlay, out, fade=fade)
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

# ------------------------------------------------------------------ sound design (synthesised, no files, no licences)

_SFX_RECIPES = {
    # whoosh: pink-noise swell, band-limited, 0.5 s — section changes
    "whoosh": "anoisesrc=d=0.55:c=pink:a=0.7:r=48000,highpass=f=500,lowpass=f=5000,afade=t=in:d=0.18,afade=t=out:st=0.25:d=0.3",
    # tick: short bright click, 70 ms — a number lands
    "tick": "sine=f=1400:d=0.07:r=48000,afade=t=out:st=0.01:d=0.06,volume=0.8",
    # riser: low brown-noise swell over 1.1 s into a reveal
    "riser": "anoisesrc=d=1.1:c=brown:a=0.9:r=48000,lowpass=f=700,afade=t=in:d=1.0,afade=t=out:st=1.0:d=0.1",
}


def _sfx_bank(workdir: Path) -> dict[str, Path]:
    """Render the three sounds once per video (~50 ms each) into workdir/sfx_*.wav."""
    bank = {}
    for name, graph in _SFX_RECIPES.items():
        p = workdir / f"sfx_{name}.wav"
        if not p.exists():
            _run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", graph, "-c:a", "pcm_s16le", str(p)])
        bank[name] = p
    return bank


def _sfx_events(beats: list[dict], offsets: list[float], lead_in: list[float], chapters: bool) -> list[tuple[str, float]]:
    """(sound, time) pairs on the final timeline. Sparse by design: a whoosh at each section change, a tick
    when each big number finishes counting up (motion.bignumber animates 1.6 s), a riser into the first
    number reveal of a section. Anything more and the sound design becomes noise."""
    ev: list[tuple[str, float]] = []
    for si, off in enumerate(offsets):
        if si > 0 and chapters:
            ev.append(("whoosh", max(0.0, off - lead_in[si] - 0.1)))
    seen_riser: set[int] = set()
    sec_idx = -1
    for b in beats:
        if b.get("_section_first"):
            sec_idx += 1
        vt = b["visual"].get("type")
        t0 = float(b.get("_abs", 0.0))
        if vt in ("bignumber", "compare"):
            land = t0 + (1.6 if vt == "bignumber" else 1.45)   # = the renderers' animation lengths; sound after picture
            if land < t0 + b["dur"]:
                ev.append(("tick", land))
            if sec_idx not in seen_riser and t0 > 1.3:
                ev.append(("riser", t0 - 1.1))
                seen_riser.add(sec_idx)
    return sorted(ev, key=lambda e: e[1])


def _mix_sfx(cfg: dict, voice: Path, events: list[tuple[str, float]], workdir: Path) -> Path:
    """Mix the sound-design events under the voice track at a fixed low level. Returns the new track
    (or the untouched voice track if there is nothing to add or ffmpeg fails)."""
    if not events:
        return voice
    try:
        bank = _sfx_bank(workdir)
        level = float(cfg["video"].get("sfx_level", 0.18))
        out = workdir / "voice_sfx.m4a"
        inputs = ["-i", str(voice)]
        parts = []
        for i, (name, at) in enumerate(events[:80]):   # hard cap; a 10-min video has ~25
            inputs += ["-i", str(bank[name])]
            parts.append(f"[{i + 1}:a]adelay={int(at * 1000)}:all=1,volume={level:.2f}[s{i}]")
        n = min(len(events), 80)
        graph = ";".join(parts) + ";" + "".join(f"[s{i}]" for i in range(n)) + f"amix=inputs={n}:normalize=0:dropout_transition=0[sfx];" \
                f"[0:a][sfx]amix=inputs=2:duration=first:normalize=0:dropout_transition=0[a]"
        _run(["ffmpeg", "-y", "-loglevel", "error", *inputs, "-filter_complex", graph, "-map", "[a]",
              "-c:a", "aac", "-b:a", "192k", str(out)])
        print(f"      sound design: {n} cues ({', '.join(f'{k} x{sum(1 for e in events if e[0] == k)}' for k in _SFX_RECIPES if any(e[0] == k for e in events))})")
        return out
    except Exception as e:  # noqa: BLE001 - never fatal
        print(f"[warn] sound design skipped: {str(e)[-160:]}")
        return voice


def _music_track(cfg: dict) -> Path | None:
    if not cfg["video"].get("music", False):
        return None
    files = glob.glob(str(ROOT / "assets" / "music" / "*.mp3")) + glob.glob(str(ROOT / "assets" / "music" / "*.m4a"))
    return Path(random.choice(files)) if files else None


def _assemble(cfg: dict, sections: list[dict], w: int, h: int, workdir: Path, out_mp4: Path,
              portrait: bool, chart: dict | None, chapters: bool,
              beats_by_id: dict | None = None, real_people: bool = False,
              map_items: list[dict] | None = None, map_title: str = "") -> tuple[Path, list[float]]:
    """Returns (video path, per-section start offsets in seconds).
    map_items (MAP videos): the section cards become a progress map of these items instead of plain title cards."""
    fps = cfg["video"]["fps"]
    if beats_by_id is None:
        beats_by_id = storyboard(cfg, sections, chart, real_people=real_people)
    broll = _BrollCache(workdir, portrait)
    outro = chapters and cfg["video"].get("outro", True)
    use_map = bool(map_items) and len(map_items) >= 4 and chapters
    card_secs = MAP_CARD_SECS if use_map else CHAPTER_SECS
    body_sections = max(1, len(sections) - 2)          # s1..s5 carry the items; hook and close do not
    # 1) Plan every clip (cheap, sequential) ...
    jobs: list = []          # callables producing a clip path, in playback order
    offsets: list[float] = []
    lead_in: list[float] = []
    all_beats: list[dict] = []
    t = 0.0
    idx = 0
    for si, s in enumerate(sections):
        card = chapters and 0 < si < len(sections) - 1
        lead_in.append(card_secs if card else 0.0)
        if card:
            def _chapter(si=si, s=s):
                c = workdir / f"chapter_{si:02d}.mp4"
                if use_map:
                    cur = min(len(map_items) - 1, round((si - 1) * (len(map_items) - 1) / max(1, body_sections - 1)))
                    spec = {"items": map_items, "current": cur, "title": map_title or s["heading"]}
                    fd, n = motion.map_grid(cfg, spec, w, h, workdir / f"frames_ch{si}", variant=si)
                else:
                    fd, n = motion.chapter_card(cfg, s["heading"], si, w, h, workdir / f"frames_ch{si}")
                _clip_from_frames(fd, n, card_secs, w, h, fps, None, c, drift=False)
                return c
            jobs.append(_chapter)
            t += card_secs
        offsets.append(t)
        overlay = (visuals.lower_third(cfg, s["heading"], w, h, workdir / f"lt_{si:02d}.png", center=portrait)
                   if s.get("heading") else None)
        beats = _align_beats(s, beats_by_id.get(s["id"], []))
        # the section's reveal (its first big number) flips to the bright theme — the thumbnail's world, inside the video
        if si > 0:
            for b in beats:
                v = b["visual"]
                if v.get("type") == "bignumber" and not v.get("footage"):
                    v["theme"] = "bright"
                    break
        for bi, b in enumerate(beats):
            # v5 rhythm: hard cuts within a section; a short fade only on the section's first beat
            def _beat(b=b, idx=idx, overlay=overlay, heading=s["heading"], fade=(bi == 0 and si > 0)):
                return _beat_clip(cfg, b, idx, w, h, fps, workdir, overlay, chart, broll, heading, fade=fade)
            jobs.append(_beat)
            b["_abs"] = t + b["start"]          # absolute start on the final timeline (for sound design)
            b["_section_first"] = bi == 0
            all_beats.append(b)
            idx += 1
        t += s["duration"] + GAP
    if outro:
        def _outro():
            fd, n = motion.outro_card(cfg, w, h, workdir / "frames_outro")
            c = workdir / "outro.mp4"
            _clip_from_frames(fd, n, OUTRO_SECS, w, h, fps, None, c, fade=True, drift=False)
            return c
        jobs.append(_outro)

    types = [b["visual"].get("type") for b in all_beats]
    mix = ", ".join(f"{types.count(x)} {x}" for x in sorted(set(types), key=types.index))
    print(f"      storyboard: {len(all_beats)} beats across {len(sections)} sections "
          f"(avg {sum(s['duration'] for s in sections) / max(1, len(all_beats)):.1f}s per visual): {mix}; "
          f"rendering with {WORKERS} workers")

    # Warm the remote-asset caches once, concurrently, so beat workers never wait on the network
    # for the same icon / person photo twice. Icons are rasterised at the exact base size the
    # renderers use; people are fetched in first-appearance order so their face is fixed by the
    # first mood the script gives them.
    icons: set[str] = set()
    people: dict[str, dict] = {}
    for b in all_beats:
        v = b["visual"]
        sides = [v.get(s) for s in ("left", "right") if isinstance(v.get(s), dict)]
        for ic in [v.get("icon")] + list(v.get("icons") or []) + [sd.get("icon") for sd in sides]:
            if isinstance(ic, str) and ic:
                icons.add(ic)
        ch = v.get("character")
        if v.get("type") == "character" and isinstance(ch, dict) and ch.get("name"):
            people.setdefault(str(ch["name"]).strip().lower(), ch)
    accent = hex_to_rgb(cfg["style"]["accent"])
    with ThreadPoolExecutor(max_workers=6) as pool:
        for ic in icons:
            pool.submit(assets_remote.icon, ic, motion.ICON_BASE, accent)
        if cfg["video"].get("b_roll", "auto") != "off":
            for ch in people.values():
                pool.submit(broll.person, ch)

    # 2) ... then render them in parallel (PIL and ffmpeg both release the GIL), keeping order.
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        clips: list[Path] = list(pool.map(lambda job: job(), jobs))

    lst = workdir / "concat.txt"
    lst.write_text("".join(f"file '{c.name}'\n" for c in clips), encoding="utf-8")
    silent = workdir / "silent.mp4"
    _run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", "concat.txt", "-c", "copy", silent.name], cwd=workdir)

    audio = workdir / "voice.m4a"
    concat_audio([Path(s["audio"]) for s in sections], audio, GAP, lead_in=lead_in, tail_pad=OUTRO_SECS if outro else 0.0)
    # sound design (whoosh on section changes, tick when a number lands, riser into the first reveal of a
    # section) is mixed UNDER the voice here, so the music ducker below keys on voice+sfx together
    if cfg["video"].get("sfx", True):
        audio = _mix_sfx(cfg, audio, _sfx_events(all_beats, offsets, lead_in, chapters), workdir)

    music = _music_track(cfg)
    base = ["ffmpeg", "-y", "-loglevel", "error", "-i", silent.name, "-i", audio.name]
    afilter: list[str] = []
    if music:
        base += ["-stream_loop", "-1", "-i", str(music)]
        # music bed with real sidechain ducking: the voice compresses the music while speaking, music
        # breathes back up in pauses (base level from config, default -20 dB; ducks a further ~12 dB under speech)
        mvol = float(cfg["video"].get("music_level", 0.10))
        afilter = ["-filter_complex",
                   f"[1:a]asplit=2[v][sc];[2:a]volume={mvol:.3f}[m];[m][sc]sidechaincompress=threshold=0.02:ratio=8:attack=40:release=500[md];"
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
    th = script.get("thumbnail") if isinstance(script.get("thumbnail"), dict) else {}
    map_items = [i for i in (th.get("items") or []) if isinstance(i, dict) and i.get("label")] if script.get("kind") == "map" else None
    out, offsets = _assemble(cfg, sections, w, h, workdir, workdir / "long.mp4", portrait=False,
                             chart=script.get("chart"), chapters=True, real_people=bool(script.get("real_people")),
                             map_items=map_items, map_title=str(th.get("map_title") or ""))
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
        prov = provider_for(cfg, len(sh["narration"]))
        text = spoken_text(sh, prov)
        info = synthesize(cfg, text, mp3, provider=prov)
        if info["duration"] > max_s - 1:  # too long -> speak faster once (same provider, so the voice is consistent)
            fast = {**cfg, "voice": {**cfg["voice"], "rate": "+14%", "elevenlabs_speed": 1.18, "fish_speed": 1.15}}
            if prov == "elevenlabs":
                _eleven_available(len(sh["narration"]))   # debit the second pass from the credit reservation
            info = synthesize(fast, text, mp3, provider=prov)
        return {"id": f"short_{k}", "heading": sh.get("hook_title", ""), "narration": sh["narration"],
                "audio": str(mp3), "duration": info["duration"], "words": info["words"], "_wd": wd}

    # TTS for all Shorts concurrently, then ONE storyboard call for all of them (saves 2 LLM calls).
    with ThreadPoolExecutor(max_workers=3) as pool:
        secs = list(pool.map(_voice, enumerate(shorts)))
    beats_all = storyboard(cfg, secs, None, real_people=bool(script.get("real_people")))

    results = []
    for sec, sh in zip(secs, shorts):
        wd = sec.pop("_wd")
        beats = beats_all.get(sec["id"], [])
        if beats:  # cold open: the first beat is always the claim over a face, whatever the storyboard chose
            beats[0]["visual"] = {"type": "hook", "text": sh.get("hook_title") or sec["heading"],
                                  "query": sh.get("hook_face") or "surprised person portrait"}
        out, _ = _assemble(cfg, [sec], w, h, wd, wd / "short.mp4", portrait=True, chart=None, chapters=False,
                           beats_by_id={sec["id"]: beats})
        results.append({"path": str(out), "duration": ffprobe_duration(out), "title": sh.get("hook_title", script["title"])[:90]})
    return results


_TW, _TH = 1280, 720
_RED = (255, 90, 95)          # "cost" colour (money lost / extra paid)
_GREEN = (61, 220, 151)       # "good" side of a comparison


def _thumb_spec(script: dict) -> dict:
    """Thumbnail spec with fallbacks for scripts produced before v3.2.1 (or selftest)."""
    th = script.get("thumbnail") if isinstance(script.get("thumbnail"), dict) else {}
    hero = str(th.get("hero") or script.get("thumbnail_text") or script["title"])[:12]
    return {
        "hero": hero,
        "hero_label": str(th.get("hero_label") or "").upper(),
        "hero_is_cost": bool(th.get("hero_is_cost", hero.startswith("+"))),
        "compare": th.get("compare") if isinstance(th.get("compare"), dict) else None,
        "icon": th.get("icon"),
        "query": str(th.get("query") or script.get("thumbnail_query") or "worried person portrait"),
    }


def thumbnail(cfg: dict, script: dict, workdir: Path) -> Path:
    """A = hero number + face photo (primary). B = versus layout when the script compares two figures, else the hero
    on a second photo. C = the first alt title as text. All three go to the artifact for Studio's Test & Compare
    (the API cannot set variants). Patterned on what wins in the feed: a face, one huge number, red = cost,
    green vs red for comparisons, a topic icon so the subject reads at feed size. Returns the path of A."""
    spec = _thumb_spec(script)
    photos = visuals.pexels_photos(spec["query"], [workdir / "thumb_bg.jpg", workdir / "thumb_bg2.jpg"])
    pa = photos[0] if photos else None
    pb = photos[1] if len(photos) > 1 else pa
    th = script.get("thumbnail") if isinstance(script.get("thumbnail"), dict) else {}
    items = [i for i in (th.get("items") or []) if isinstance(i, dict) and i.get("label")]
    if script.get("kind") == "map" and len(items) >= 4:
        # v6 outlier pattern: bright, flat, labelled icon grid (the two >100x videos in the reference set).
        # A = bright map; B = the dark hero so Studio's Test & Compare can tell us which grammar wins for us.
        a = _thumb_map(cfg, th, items, workdir / "thumbnail.jpg")
        try:
            _thumb_hero(cfg, spec, workdir / "thumbnail_B.jpg", pa)
            alts = script.get("alt_titles")
            alt = (alts[0] if isinstance(alts, list) and alts else None) or script["title"]
            _thumb_hero(cfg, spec, workdir / "thumbnail_C.jpg", pb, title_text=alt)
        except Exception as e:  # noqa: BLE001
            print(f"[warn] thumbnail variants failed: {str(e)[:120]}")
        return a
    a = _thumb_hero(cfg, spec, workdir / "thumbnail.jpg", pa)
    try:
        if spec["compare"]:
            _thumb_versus(cfg, spec, workdir / "thumbnail_B.jpg", pa)
        else:
            _thumb_hero(cfg, spec, workdir / "thumbnail_B.jpg", pb)
        alts = script.get("alt_titles")
        alt = (alts[0] if isinstance(alts, list) and alts else None) or script["title"]
        _thumb_hero(cfg, spec, workdir / "thumbnail_C.jpg", pb, title_text=alt)
    except Exception as e:  # noqa: BLE001 - variants are a bonus, never fatal
        print(f"[warn] thumbnail variants failed: {str(e)[:120]}")
    return a


def _thumb_canvas(cfg: dict, photo: Path | None, photo_side: str = "right", brightness: float = 0.75):
    """Navy gradient with the photo cover-cropped and blended in on one side (or full-bleed when photo_side='full')."""
    from PIL import Image, ImageEnhance
    st = cfg["style"]
    bg = visuals.gradient(_TW, _TH, st["bg_dark"], st["bg_dark2"])
    if not (photo and Path(photo).exists()):
        return bg
    ph = ImageEnhance.Brightness(motion._cover(Image.open(photo).convert("RGB"), _TW, _TH)).enhance(brightness)
    if photo_side == "full":
        return ph
    # the photo is cover-cropped from its centre, so the subject's face is usually near the middle; shift the
    # visible window so the face lands in the photo half rather than under the text
    shift = int(_TW * 0.18)
    ph = ph.transform((_TW, _TH), Image.AFFINE, (1, 0, -shift if photo_side == "right" else shift, 0, 1, 0))
    mask = Image.linear_gradient("L").rotate(90 if photo_side == "right" else -90, expand=True).resize((_TW, _TH))
    mask = mask.point(lambda v: max(0, min(255, int((v - 95) * 2.4))))
    return Image.composite(ph, bg, mask)


def _thumb_chrome(cfg: dict, d, img, icon: str | None) -> None:
    """Brand strip + wordmark + topic icon badge (bottom-left, clear of all text boxes) shared by every layout."""
    st = cfg["style"]
    d.rectangle([0, _TH - 20, _TW, _TH], fill=hex_to_rgb(st["accent"]))
    d.text((_TW - 28, _TH - 44), cfg["channel"]["name"].upper(), font=visuals.font(cfg, 26), fill=(255, 255, 255), anchor="rm",
           stroke_width=3, stroke_fill=(0, 0, 0))
    ic = assets_remote.icon(icon, motion.ICON_BASE, (255, 255, 255)) if icon else None
    if ic is not None:
        ic = ic.resize((64, 64))
        bx, by, bs = 40, _TH - 20 - 28 - 100, 100
        d.rounded_rectangle([bx, by, bx + bs, by + bs], radius=22, fill=(20, 27, 55), outline=hex_to_rgb(st["accent"]), width=4)
        img.paste(ic, (bx + 18, by + 18), ic)


def _thumb_hero(cfg: dict, spec: dict, out: Path, photo: Path | None, title_text: str | None = None) -> Path:
    """Left: one huge number (red if it is a cost, gold otherwise) with a short label; right: the face photo.
    With title_text the number is replaced by the fitted title (variant C)."""
    from PIL import ImageDraw
    st = cfg["style"]
    img = _thumb_canvas(cfg, photo, "right")
    d = ImageDraw.Draw(img)
    box_w = _TW * 0.56
    if title_text:
        text = " ".join(str(title_text).upper().split())
        fnt, lines, lh = motion.fit_text_box(cfg, d, text, box_w, _TH * 0.6, 132, floor=56, line_spacing=1.04, max_lines=4)
        y = _TH * 0.42 - len(lines) * lh / 2   # sits above the icon badge (bottom-left) even at four lines
        for i, line in enumerate(lines):
            d.text((56, y + i * lh), line, font=fnt, fill=hex_to_rgb(st["accent2"]) if i == 0 else (255, 255, 255),
                   stroke_width=max(5, fnt.size // 18), stroke_fill=(0, 0, 0))
    else:
        # TV-first sizing: 55% of this channel's watch time is on TV screens, where a 1280x720 thumbnail is drawn
        # ~300 px wide — the hero must be legible at that size, so it fills the left half and the label is one line.
        hero = spec["hero"]
        col = _RED if spec["hero_is_cost"] else hex_to_rgb(st["accent2"])
        hf = motion._fit_font(cfg, d, hero, box_w, 310, floor=130)
        label = spec["hero_label"]
        total = hf.size * 1.05 + (96 if label else 0)
        y0 = max(40, _TH * 0.45 - total / 2)   # centred slightly above the middle; the icon badge lives bottom-left
        d.text((56, y0), hero, font=hf, fill=col, stroke_width=max(10, hf.size // 14), stroke_fill=(0, 0, 0))
        if label:
            motion.draw_fit(cfg, d, label, (60, y0 + hf.size * 1.1, 60 + box_w, y0 + hf.size * 1.1 + 100), 84,
                            (255, 255, 255), floor=52, max_lines=1, stroke=7)
    _thumb_chrome(cfg, d, img, spec.get("icon"))
    img.save(out, "JPEG", quality=90, optimize=True)
    return out


_MAP_BG = (255, 214, 102)          # warm yellow (brand accent2) — bright flat thumbnails are the outlier grammar
_MAP_TILES = [(255, 90, 95), (61, 220, 151), (80, 140, 255), (255, 150, 60), (170, 100, 255), (40, 200, 220), (255, 110, 170), (120, 200, 80)]


def _thumb_map(cfg: dict, th: dict, items: list[dict], out: Path) -> Path:
    """Bright labelled icon grid: 4 -> 2x2 big tiles, 5-6 -> 3x2, 7-8 -> 4x2. Title strip at the top (2-4 words).
    Flat colours, thick black labels — readable at 300 px on a TV, which is where most of our watch time is."""
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (_TW, _TH), _MAP_BG)
    d = ImageDraw.Draw(img)
    title = (th.get("map_title") or "").strip() or "EVERY TYPE EXPLAINED"
    tf = motion._fit_font(cfg, d, title, _TW * 0.9, 118, floor=70)
    d.text((_TW / 2, 86), title, font=tf, fill=(20, 20, 30), anchor="mm", stroke_width=3, stroke_fill=(255, 255, 255))
    n = min(8, max(4, len(items)))
    cols = 2 if n <= 4 else (3 if n <= 6 else 4)
    rows = -(-n // cols)
    top, bottom, side, gap = 170, _TH - 60, 50, 22
    tw = (_TW - 2 * side - (cols - 1) * gap) // cols
    thh = (bottom - top - (rows - 1) * gap) // rows
    for i, it in enumerate(items[:n]):
        r, c = divmod(i, cols)
        x0, y0 = side + c * (tw + gap), top + r * (thh + gap)
        col = _MAP_TILES[i % len(_MAP_TILES)]
        d.rounded_rectangle([x0, y0, x0 + tw, y0 + thh], radius=26, fill=col, outline=(20, 20, 30), width=5)
        isz = int(min(tw, thh) * 0.46)
        ic = assets_remote.icon(it.get("icon") or "", motion.ICON_BASE, (20, 20, 30)) if it.get("icon") else None
        if ic is not None:
            ic = ic.resize((isz, isz))
            img.paste(ic, (int(x0 + tw / 2 - isz / 2), int(y0 + thh * 0.42 - isz / 2)), ic)
        else:
            d.ellipse([x0 + tw / 2 - isz / 2, y0 + thh * 0.42 - isz / 2, x0 + tw / 2 + isz / 2, y0 + thh * 0.42 + isz / 2],
                      outline=(20, 20, 30), width=6)
        lf = motion._fit_font(cfg, d, str(it["label"]), tw * 0.9, int(thh * 0.2), floor=26)
        d.text((x0 + tw / 2, y0 + thh * 0.83), str(it["label"]), font=lf, fill=(255, 255, 255), anchor="mm",
               stroke_width=max(3, lf.size // 9), stroke_fill=(20, 20, 30))
    d.text((_TW - 26, _TH - 28), cfg["channel"]["name"].upper(), font=visuals.font(cfg, 26), fill=(20, 20, 30), anchor="rm")
    img.save(out, "JPEG", quality=90, optimize=True)
    return out


def _thumb_versus(cfg: dict, spec: dict, out: Path, photo: Path | None) -> Path:
    """Split screen: left half green-tinted (the cheaper / better figure), right half red-tinted (the costly one),
    the face photo dimmed behind, a white divider, label + value stacked in each half."""
    from PIL import Image, ImageDraw
    cmp_ = spec["compare"]
    img = _thumb_canvas(cfg, photo, "full", brightness=0.5)
    tint = Image.new("RGBA", (_TW, _TH), (0, 0, 0, 0))
    td = ImageDraw.Draw(tint)
    td.rectangle([0, 0, _TW // 2, _TH], fill=_GREEN + (70,))
    td.rectangle([_TW // 2, 0, _TW, _TH], fill=_RED + (80,))
    td.rectangle([0, 0, _TW, int(_TH * 0.42)], fill=(11, 16, 32, 120))   # darker band behind the text
    img = Image.alpha_composite(img.convert("RGBA"), tint).convert("RGB")
    d = ImageDraw.Draw(img)
    d.rectangle([_TW // 2 - 3, 0, _TW // 2 + 3, _TH], fill=(255, 255, 255))
    for k, (side, col) in enumerate(((cmp_["left"], _GREEN), (cmp_["right"], _RED))):
        x0 = 40 + k * (_TW // 2)
        x1 = x0 + _TW // 2 - 80
        motion.draw_fit(cfg, d, str(side["label"]).upper(), (x0, 44, x1, 150), 104, col, floor=56, max_lines=1, stroke=7)
        vf = motion._fit_font(cfg, d, str(side["value"]), x1 - x0, 150, floor=72)
        d.text((x0, 160), str(side["value"]), font=vf, fill=(255, 255, 255), stroke_width=max(6, vf.size // 16), stroke_fill=(0, 0, 0))
    _thumb_chrome(cfg, d, img, spec.get("icon"))
    img.save(out, "JPEG", quality=90, optimize=True)
    return out


def first_frame(video: Path, out: Path, at_s: float = 0.6) -> Path | None:
    """One JPEG from `at_s` seconds in — used to audit each Short's cold open from the artifact. Never fatal."""
    try:
        _run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{at_s:.2f}", "-i", str(video), "-frames:v", "1", "-q:v", "4", str(out)])
        return out
    except Exception as e:  # noqa: BLE001
        print(f"[warn] first frame failed: {str(e)[-120:]}")
        return None


def contact_sheet(video: Path, out: Path, every_s: float = 6.0, cols: int = 6, tile_w: int = 320) -> Path | None:
    """One ffmpeg call: a frame every `every_s` seconds tiled into a grid, for a 10-second visual audit of
    the whole video without downloading it. Never fatal."""
    try:
        dur = ffprobe_duration(video)
        n = max(1, int(dur / every_s) + 1)
        rows = max(1, -(-n // cols))
        _run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(video),
              "-vf", f"fps=1/{every_s},scale={tile_w}:-2,tile={cols}x{rows}:padding=4:margin=4:color=0x0B1020",
              "-frames:v", "1", "-q:v", "4", str(out)])
        return out
    except Exception as e:  # noqa: BLE001
        print(f"[warn] contact sheet failed: {str(e)[-200:]}")
        return None
