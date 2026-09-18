"""Free neural TTS via Microsoft Edge (edge-tts). Returns audio + word timings for captions."""
import asyncio
import json
import subprocess
import time
from pathlib import Path

import edge_tts


def ffprobe_duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout
    return float(json.loads(out)["format"]["duration"])


async def _synth(text: str, voice: str, rate: str, pitch: str, out_mp3: Path) -> list[dict]:
    words: list[dict] = []
    try:  # edge-tts >= 7 lets you request word-level boundaries explicitly
        comm = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch, boundary="WordBoundary")
    except TypeError:  # older versions: word boundaries are the default
        comm = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)
    with open(out_mp3, "wb") as f:
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":  # sentence boundaries would duplicate/overlap word times
                words.append({
                    "start": chunk["offset"] / 1e7,
                    "end": (chunk["offset"] + chunk["duration"]) / 1e7,
                    "text": chunk["text"],
                })
    return words


def synthesize(cfg: dict, text: str, out_mp3: Path, retries: int = 3) -> dict:
    """Synthesize `text` to out_mp3. Returns {'duration': float, 'words': [...]}"""
    v = cfg["voice"]
    last = None
    for attempt in range(retries):
        try:
            words = asyncio.run(_synth(text, v["name"], v.get("rate", "+0%"), v.get("pitch", "+0Hz"), out_mp3))
            dur = ffprobe_duration(out_mp3)
            if dur < 0.5:
                raise RuntimeError("TTS produced empty audio")
            return {"duration": dur, "words": words}
        except Exception as e:  # noqa: BLE001 - network flakiness; retry
            last = e
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"edge-tts failed: {last}")


def synthesize_sections(cfg: dict, sections: list[dict], workdir: Path) -> list[dict]:
    """One mp3 per section so we know exact per-section durations. Adds 'audio','duration','words'."""
    from concurrent.futures import ThreadPoolExecutor

    def _one(i_s):
        i, s = i_s
        mp3 = workdir / f"sec_{i:02d}.mp3"
        info = synthesize(cfg, s["narration"], mp3)
        return {**s, "audio": str(mp3), "duration": info["duration"], "words": info["words"]}

    # 3 concurrent requests: fast, but gentle enough not to trip Microsoft's rate limiting.
    with ThreadPoolExecutor(max_workers=3) as pool:
        return list(pool.map(_one, enumerate(sections)))


def concat_audio(section_audio: list[Path], out_path: Path, gap_s: float = 0.35,
                 lead_in: list[float] | None = None, tail_pad: float = 0.0) -> float:
    """Concatenate with a short silence between sections. `lead_in[i]` seconds of silence are
    inserted BEFORE section i (chapter cards); `tail_pad` seconds after the last (end screen).
    Returns total duration."""
    inputs, filters = [], []
    lead_in = lead_in or [0.0] * len(section_audio)
    last = len(section_audio) - 1
    for i, p in enumerate(section_audio):
        inputs += ["-i", str(p)]
        pre = f"adelay={int(lead_in[i] * 1000)}:all=1," if lead_in[i] > 0 else ""
        pad = gap_s + (tail_pad if i == last else 0.0)
        filters.append(f"[{i}:a]{pre}apad=pad_dur={pad:.3f}[a{i}]")
    joined = "".join(f"[a{i}]" for i in range(len(section_audio)))
    # loudnorm internally upsamples to 192 kHz; resample back to 48 kHz so the AAC encoder gets a sane rate.
    fc = ";".join(filters) + f";{joined}concat=n={len(section_audio)}:v=0:a=1,loudnorm=I=-16:TP=-1.5:LRA=11,aresample=48000[out]"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", *inputs, "-filter_complex", fc, "-map", "[out]",
         "-c:a", "aac", "-b:a", "192k", str(out_path)],
        check=True,
    )
    return ffprobe_duration(out_path)
