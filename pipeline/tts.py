"""Text-to-speech with word timings for captions.

Providers (voice.provider in config.yaml):
  * elevenlabs — premium neural voice (ELEVENLABS_API_KEY). Uses the /with-timestamps endpoint so we get
    character alignment -> word boundaries, same contract as edge-tts. Credits are checked BEFORE a video
    starts so the whole video gets one voice; when the monthly quota can't cover it, the whole video uses
    edge-tts. (Only if ElevenLabs errors mid-video after retries does a single section fall back to edge —
    a voice change beats a failed run.)
  * edge — free Microsoft Edge neural TTS (edge-tts). Always available; the fallback.
  * auto — elevenlabs when a key exists and credits suffice, else edge.
"""
from __future__ import annotations

import asyncio
import base64
import json
import subprocess
import threading
import time
from pathlib import Path

import edge_tts
import requests

from .config import env

_ELEVEN = "https://api.elevenlabs.io/v1"
_quota_lock = threading.Lock()
_quota: dict | None = None   # {"remaining": int, "ok": bool} cached per run


class _ElevenDisabled(RuntimeError):
    """ElevenLabs refused at the account level; don't retry this run."""


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


# ------------------------------------------------------------------ ElevenLabs

def _eleven_quota() -> dict:
    """Remaining characters this billing period (cached for the run). {'remaining': int, 'ok': bool}."""
    global _quota
    with _quota_lock:
        if _quota is not None:
            return _quota
        key = env("ELEVENLABS_API_KEY")
        if not key:
            _quota = {"remaining": 0, "ok": False}
            return _quota
        try:
            r = requests.get(f"{_ELEVEN}/user/subscription", headers={"xi-api-key": key}, timeout=20)
            r.raise_for_status()
            d = r.json()
            remaining = int(d.get("character_limit", 0)) - int(d.get("character_count", 0))
            _quota = {"remaining": max(0, remaining), "ok": True, "tier": d.get("tier", "?")}
            print(f"      ElevenLabs: {_quota['remaining']:,} characters left this month (tier: {_quota['tier']})")
            if str(_quota["tier"]).lower() == "free":
                # ElevenLabs blocks free-tier calls from datacenter IPs (GitHub Actions) with 401 detected_unusual_activity
                print("      ElevenLabs free tier is refused from CI runners; a paid plan (Starter) is needed for the premium voice")
        except Exception as e:  # noqa: BLE001
            # New ElevenLabs keys carry permission scopes; a key without "User -> Read" gets 401 here even though
            # text-to-speech would work. Don't give up: treat the balance as unknown and let the first TTS call decide.
            print(f"[warn] ElevenLabs balance check failed ({str(e)[:90]}). If this is 401, the API key lacks the "
                  f"'User: read' permission — regenerate it with Text-to-Speech + User(read). Trying TTS anyway.")
            _quota = {"remaining": 10 ** 9, "ok": True, "tier": "unknown", "unverified": True}
        return _quota


def provider_for(cfg: dict, text_chars: int) -> str:
    """Decide the voice for ONE video (all of its sections). Reserves the characters so a later video in the
    same run sees the reduced balance. Returns 'elevenlabs' or 'edge'."""
    want = str(cfg["voice"].get("provider", "auto")).lower()
    if want == "edge":
        return "edge"
    q = _eleven_quota()
    need = int(text_chars * 1.05) + 50
    with _quota_lock:
        if q["ok"] and q["remaining"] >= need:
            q["remaining"] -= need
            return "elevenlabs"
    if want == "elevenlabs":
        print(f"[warn] ElevenLabs requested but {q['remaining']:,} chars left < {need:,} needed; this video uses edge-tts")
    return "edge"


def _eleven_synth(cfg: dict, text: str, out_mp3: Path) -> list[dict]:
    v = cfg["voice"]
    key = env("ELEVENLABS_API_KEY")
    url = f"{_ELEVEN}/text-to-speech/{v.get('elevenlabs_voice_id')}/with-timestamps?output_format=mp3_44100_128"
    body = {
        "text": text,
        "model_id": v.get("elevenlabs_model", "eleven_turbo_v2_5"),
        "voice_settings": {"stability": float(v.get("elevenlabs_stability", 0.45)),
                           "similarity_boost": float(v.get("elevenlabs_similarity", 0.8)),
                           "style": float(v.get("elevenlabs_style", 0.2)),
                           "use_speaker_boost": True,
                           "speed": float(v.get("elevenlabs_speed", 1.05))},
    }
    r = requests.post(url, headers={"xi-api-key": key, "Content-Type": "application/json"}, json=body, timeout=180)
    if r.status_code in (401, 402, 429):
        # account-level refusals (free-tier datacenter block "detected_unusual_activity", quota_exceeded, bad key):
        # disable ElevenLabs for the REST of the run so later videos go straight to edge-tts without retrying
        with _quota_lock:
            if _quota is not None:
                _quota["ok"], _quota["remaining"] = False, 0
        raise _ElevenDisabled(f"ElevenLabs {r.status_code}: {r.text[:160]}")
    if r.status_code != 200:
        raise RuntimeError(f"ElevenLabs {r.status_code}: {r.text[:200]}")
    d = r.json()
    out_mp3.write_bytes(base64.b64decode(d["audio_base64"]))
    # character alignment -> word boundaries (split on whitespace), the same shape edge-tts gives us
    al = d.get("alignment") or d.get("normalized_alignment") or {}
    chars, starts, ends = al.get("characters", []), al.get("character_start_times_seconds", []), al.get("character_end_times_seconds", [])
    words, cur, w_start, w_end = [], "", None, None
    for ch, s, e in zip(chars, starts, ends):
        if ch.isspace():
            if cur:
                words.append({"start": w_start, "end": w_end, "text": cur})
            cur, w_start = "", None
        else:
            if w_start is None:
                w_start = s
            w_end = e
            cur += ch
    if cur:
        words.append({"start": w_start, "end": w_end, "text": cur})
    return words


# ------------------------------------------------------------------ public API

def synthesize(cfg: dict, text: str, out_mp3: Path, retries: int = 3, provider: str | None = None) -> dict:
    """Synthesize `text` to out_mp3. Returns {'duration': float, 'words': [...], 'provider': str}.
    `provider` should come from provider_for() so every section of a video uses the same voice."""
    v = cfg["voice"]
    provider = provider or provider_for(cfg, len(text))
    last = None
    with _quota_lock:   # account refused earlier in this run -> don't even try
        if provider == "elevenlabs" and _quota is not None and not _quota["ok"]:
            provider = "edge"
    if provider == "elevenlabs":
        for attempt in range(2):
            try:
                words = _eleven_synth(cfg, text, out_mp3)
                dur = ffprobe_duration(out_mp3)
                if dur < 0.5:
                    raise RuntimeError("ElevenLabs produced empty audio")
                return {"duration": dur, "words": words, "provider": "elevenlabs"}
            except _ElevenDisabled as e:
                last = e
                break                      # account refused: no point retrying
            except Exception as e:  # noqa: BLE001
                last = e
                time.sleep(3)
        print(f"[warn] ElevenLabs failed ({str(last)[:140]}); falling back to edge-tts")
    for attempt in range(retries):
        try:
            words = asyncio.run(_synth(text, v["name"], v.get("rate", "+0%"), v.get("pitch", "+0Hz"), out_mp3))
            dur = ffprobe_duration(out_mp3)
            if dur < 0.5:
                raise RuntimeError("TTS produced empty audio")
            return {"duration": dur, "words": words, "provider": "edge"}
        except Exception as e:  # noqa: BLE001 - network flakiness; retry
            last = e
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"edge-tts failed: {last}")


def synthesize_sections(cfg: dict, sections: list[dict], workdir: Path) -> list[dict]:
    """One mp3 per section so we know exact per-section durations. Adds 'audio','duration','words'.
    The provider is chosen ONCE for the whole video so the voice never changes between sections."""
    from concurrent.futures import ThreadPoolExecutor
    provider = provider_for(cfg, sum(len(s["narration"]) for s in sections))
    print(f"      voice: {provider}")

    def _one(i_s):
        i, s = i_s
        mp3 = workdir / f"sec_{i:02d}.mp3"
        info = synthesize(cfg, s["narration"], mp3, provider=provider)
        return {**s, "audio": str(mp3), "duration": info["duration"], "words": info["words"]}

    # 3 concurrent requests: fast, but gentle enough not to trip rate limiting on either provider.
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
