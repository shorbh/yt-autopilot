"""Text-to-speech with word timings for captions.

Providers (voice.provider in config.yaml; `auto` walks voice.order, default fish -> elevenlabs -> edge):
  * fish — Fish Audio `s2.1-pro-free` (FISH_API_KEY): production-quality voice at $0, no CI/datacenter block.
    The TTS endpoint has no timestamps, so word timings come from Fish's own ASR (`/v1/asr`,
    ignore_timestamps=false, $0.36 per audio hour ≈ 15¢/week for this channel): phrase segments, with the
    words inside each segment spread by character length. If ASR fails the video still renders — captions use
    the estimated-timing path.
  * elevenlabs — premium neural voice (ELEVENLABS_API_KEY) via /with-timestamps (true word alignment).
    Credits are checked BEFORE a video starts; the free tier is refused from CI (401 detected_unusual_activity).
  * edge — free Microsoft Edge neural TTS (edge-tts) with word boundaries. Always available; the final fallback.
One provider per video: the decision is made once per video so the voice never changes between sections.
"""
from __future__ import annotations

import asyncio
import base64
import json
import re
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


_FISH = "https://api.fish.audio/v1"
_fish_state = {"ok": None, "asr": True}   # ok: None = untested, True/False after the first TTS call; asr: off after a 402/401
_TAG_RE = re.compile(r"\[[^\]]{1,60}\]|\((?:break|long-break|breath)\)")   # Fish emotion/pause cues, never captioned


def _fish_ok() -> bool:
    return bool(env("FISH_API_KEY")) and _fish_state["ok"] is not False


def _fish_synth(cfg: dict, text: str, out_mp3: Path) -> list[dict]:
    """Fish Audio TTS (free model) + ASR alignment -> word timings. Raises on TTS failure; ASR failure -> []."""
    v = cfg["voice"]
    key = env("FISH_API_KEY")
    body = {"text": text, "format": "mp3", "mp3_bitrate": 128, "normalize": True, "latency": "normal",
            "prosody": {"speed": float(v.get("fish_speed", 1.0)), "volume": 0},
            # sampling: higher temperature = more prosodic variation (default 0.7 reads flat on narration voices)
            "temperature": float(v.get("fish_temperature", 0.9)), "top_p": float(v.get("fish_top_p", 0.8))}
    if v.get("fish_reference_id"):
        body["reference_id"] = str(v["fish_reference_id"])
    r = requests.post(f"{_FISH}/tts", json=body, timeout=240,
                      headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                               "model": str(v.get("fish_model", "s2.1-pro-free"))})
    if r.status_code in (401, 402, 403):
        _fish_state["ok"] = False
        raise RuntimeError(f"Fish Audio {r.status_code}: {r.text[:160]} (key/permissions/balance) — disabled for this run")
    if r.status_code != 200 or len(r.content) < 1000:
        raise RuntimeError(f"Fish Audio {r.status_code}: {r.text[:160]}")
    out_mp3.write_bytes(r.content)
    _fish_state["ok"] = True
    return _fish_align(key, out_mp3, text)


def _fish_align(key: str, mp3: Path, text: str) -> list[dict]:
    """Word timings from Fish ASR segments. Words of the SPOKEN text are mapped onto the transcript's segments
    by proportional position (robust to the ASR hearing '$1,200' as 'twelve hundred dollars')."""
    if not _fish_state["asr"] or (env("GROQ_API_KEY") and _groq_state["ok"]):
        return []   # Groq Whisper (free) aligns the finished section instead — see _ensure_words
    try:
        with open(mp3, "rb") as fh:
            r = requests.post(f"{_FISH}/asr", headers={"Authorization": f"Bearer {key}", "model": "transcribe-1"},
                              files={"audio": ("a.mp3", fh, "audio/mpeg")}, data={"language": "en", "ignore_timestamps": "false"},
                              timeout=240)
        if r.status_code in (401, 402, 403):
            _fish_state["asr"] = False   # account-level: say it once, not once per section
            alt = ("captions use estimated timings. Fix: top up Fish *API* credit (separate from platform credit) or add "
                   "GROQ_API_KEY (free Whisper alignment)")
            print(f"[warn] Fish ASR {r.status_code}: {r.text[:110]} — {alt}")
            return []
        if r.status_code != 200:
            print(f"[warn] Fish ASR {r.status_code}: {r.text[:120]} — this section uses estimated timings")
            return []
        segs = sorted((s for s in r.json().get("segments", []) if s.get("text", "").strip() and s["end"] > s["start"]),
                      key=lambda s: float(s["start"]))
    except Exception as e:  # noqa: BLE001
        print(f"[warn] Fish ASR failed ({str(e)[:100]}) — captions will use estimated timings")
        return []
    return _distribute(segs, text)


_GROQ_AUDIO = "https://api.groq.com/openai/v1/audio/transcriptions"
_groq_state = {"ok": True}


def _groq_align(mp3: Path, text: str) -> list[dict]:
    """Word timings from Groq's hosted Whisper (free tier: 8 audio-hours/day, word timestamps) — the $0 alternative
    to Fish ASR, so captions stay in sync without any API credit. Each Whisper word is a segment; our spoken tokens
    are distributed over them by character share (robust to '$1,200' being heard as 'twelve hundred dollars')."""
    key = env("GROQ_API_KEY")
    if not key or not _groq_state["ok"]:
        return []
    def _post():
        with open(mp3, "rb") as fh:
            return requests.post(_GROQ_AUDIO, headers={"Authorization": f"Bearer {key}"},
                                 files={"file": (mp3.name, fh, "audio/mpeg")},
                                 data={"model": "whisper-large-v3-turbo", "response_format": "verbose_json",
                                       "timestamp_granularities[]": "word", "language": "en", "temperature": "0"},
                                 timeout=240)
    try:
        r = _post()
        if r.status_code == 429:
            time.sleep(8)   # free tier: 20 requests/min; one retry is enough at 3 workers
            r = _post()
        if r.status_code in (401, 402, 403):
            _groq_state["ok"] = False
            print(f"[warn] Groq Whisper {r.status_code}: {r.text[:120]} — alignment off for this run")
            return []
        if r.status_code != 200:
            print(f"[warn] Groq Whisper {r.status_code}: {r.text[:120]} — this part uses estimated timings")
            return []
        ws = r.json().get("words") or []
        segs = sorted(({"text": str(w.get("word", "")).strip(), "start": float(w["start"]), "end": float(w["end"])}
                       for w in ws if str(w.get("word", "")).strip() and float(w["end"]) > float(w["start"])),
                      key=lambda s: s["start"])
    except Exception as e:  # noqa: BLE001
        print(f"[warn] Groq Whisper failed ({str(e)[:100]}) — this part uses estimated timings")
        return []
    return _distribute(segs, text)


def _distribute(segs: list[dict], text: str) -> list[dict]:
    """Map the SPOKEN text's tokens onto timed transcript segments, in proportion to each segment's character share."""
    toks = _TAG_RE.sub("", text).split()
    if not segs or not toks:
        return []
    # distribute our tokens over segments in proportion to each segment's share of the transcript's characters
    seg_chars = [len(s["text"]) for s in segs]
    total_chars = float(sum(seg_chars)) or 1.0
    words, ti = [], 0
    for i, s in enumerate(segs):
        n = len(toks) - ti if i == len(segs) - 1 else max(1, round(len(toks) * seg_chars[i] / total_chars))
        chunk = toks[ti:ti + n]
        ti += n
        if not chunk:
            continue
        w_total = float(sum(len(t) + 1 for t in chunk))
        t0, span = float(s["start"]), float(s["end"]) - float(s["start"])
        acc = 0.0
        for t in chunk:
            d = span * (len(t) + 1) / w_total
            words.append({"start": t0 + acc, "end": t0 + acc + d, "text": t})
            acc += d
        if ti >= len(toks):
            break
    return words


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


def _eleven_available(text_chars: int) -> bool:
    if not env("ELEVENLABS_API_KEY"):
        return False
    q = _eleven_quota()
    need = int(text_chars * 1.05) + 50
    with _quota_lock:
        if q["ok"] and q["remaining"] >= need:
            q["remaining"] -= need      # reserve so a later video in this run sees the reduced balance
            return True
    return False


def provider_for(cfg: dict, text_chars: int) -> str:
    """Decide the voice for ONE video (all of its sections). Returns 'fish' | 'elevenlabs' | 'edge'.
    voice.provider = a provider name forces it (with edge as the safety net), or 'auto' walks voice.order."""
    v = cfg["voice"]
    want = str(v.get("provider", "auto")).lower()
    order = [want] if want != "auto" else [str(p).lower() for p in (v.get("order") or ["fish", "elevenlabs", "edge"])]
    for p in order:
        if p == "fish" and _fish_ok():
            return "fish"
        if p == "elevenlabs" and _eleven_available(text_chars):
            return "elevenlabs"
        if p == "edge":
            return "edge"
    if want not in ("auto", "edge"):
        print(f"[warn] voice provider '{want}' unavailable (key, credits or an earlier refusal); this video uses edge-tts")
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

def spoken_text(part: dict, provider: str) -> str:
    """What the voice reads: the cue-annotated `spoken` text for Fish (S2 understands [bracket] cues); the clean
    narration for everyone else — edge/ElevenLabs would read the brackets aloud."""
    if provider == "fish" and part.get("spoken"):
        return part["spoken"]
    return _TAG_RE.sub(" ", part["narration"]).strip()


def synthesize(cfg: dict, text: str, out_mp3: Path, retries: int = 3, provider: str | None = None) -> dict:
    """Synthesize `text` to out_mp3. Returns {'duration': float, 'words': [...], 'provider': str}.
    `provider` should come from provider_for() so every section of a video uses the same voice."""
    v = cfg["voice"]
    provider = provider or provider_for(cfg, len(text))
    last = None
    with _quota_lock:   # account refused earlier in this run -> don't even try
        if provider == "elevenlabs" and _quota is not None and not _quota["ok"]:
            provider = "edge"
    if provider == "fish" and not _fish_ok():
        provider = "edge"
    if provider == "fish":
        for attempt in range(2):
            try:
                words = _fish_synth(cfg, text, out_mp3)
                dur = ffprobe_duration(out_mp3)
                if dur < 0.5:
                    raise RuntimeError("Fish Audio produced empty audio")
                return {"duration": dur, "words": words, "provider": "fish"}
            except Exception as e:  # noqa: BLE001
                last = e
                if not _fish_ok():
                    break
                time.sleep(3)
        print(f"[warn] Fish Audio failed ({str(last)[:140]}); falling back to edge-tts")
        provider = "edge"
    text = _TAG_RE.sub(" ", text).strip() if provider != "fish" else text   # cues are Fish-only
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


_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=(?:\[[^\]]*\]\s*|\(break\)\s*|\(long-break\)\s*)*[A-Z0-9\"'$])")
PAUSE_S = 0.55          # silence between sentence groups inside a section — a person breathes; a reader doesn't
GROUP_WORDS = 38        # ~2-3 sentences per breath group


def _groups(text: str) -> list[str]:
    """Split narration into breath groups of 2-3 sentences (<= GROUP_WORDS words). Cues stay attached to their sentence."""
    sents = [s.strip() for s in _SENT_SPLIT.split(" ".join(str(text).split())) if s.strip()]
    groups, cur, n = [], [], 0
    for s in sents:
        wc = len(_TAG_RE.sub(" ", s).split())
        if cur and (n + wc > GROUP_WORDS or len(cur) >= 3):
            groups.append(" ".join(cur))
            cur, n = [], 0
        cur.append(s)
        n += wc
    if cur:
        groups.append(" ".join(cur))
    return groups or [str(text)]


def synthesize_paced(cfg: dict, text: str, out_mp3: Path, provider: str) -> dict:
    """Synthesize `text` as breath groups with PAUSE_S of silence between them, concatenated into one mp3.
    Word timings are shifted into the combined timeline. Falls back to a single synthesis on any failure."""
    groups = _groups(text)
    if len(groups) == 1:
        return _ensure_words(synthesize(cfg, text, out_mp3, provider=provider), out_mp3, text)
    parts, words, offset = [], [], 0.0
    try:
        for gi, g in enumerate(groups):
            p = out_mp3.with_name(f"{out_mp3.stem}_g{gi:02d}.mp3")
            info = synthesize(cfg, g, p, provider=provider)
            if info.get("provider") != provider:
                raise RuntimeError("provider changed mid-section")   # -> whole section re-synthesised in one consistent voice
            for w in info["words"]:
                words.append({**w, "start": w["start"] + offset, "end": w["end"] + offset})
            offset += info["duration"] + PAUSE_S
            parts.append(p)
        inputs, filters = [], []
        for i, p in enumerate(parts):
            inputs += ["-i", str(p)]
            pad = PAUSE_S if i < len(parts) - 1 else 0.0
            filters.append(f"[{i}:a]aresample=48000,aformat=channel_layouts=mono,apad=pad_dur={pad:.3f}[a{i}]")  # concat needs equal formats
        joined = "".join(f"[a{i}]" for i in range(len(parts)))
        fc = ";".join(filters) + f";{joined}concat=n={len(parts)}:v=0:a=1[out]"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *inputs, "-filter_complex", fc, "-map", "[out]",
                        "-c:a", "libmp3lame", "-q:a", "2", str(out_mp3)], check=True)
        return _ensure_words({"duration": ffprobe_duration(out_mp3), "words": words, "provider": provider}, out_mp3, text)
    except Exception as e:  # noqa: BLE001 - never lose a video over pacing
        print(f"[warn] paced synthesis failed ({str(e)[:100]}); synthesising the section in one piece")
        return _ensure_words(synthesize(cfg, text, out_mp3, provider=provider), out_mp3, text)


def _ensure_words(info: dict, mp3: Path, text: str) -> dict:
    """Fish TTS has no timestamps; when Fish ASR is unavailable (no API credit) the words list comes back empty and
    captions drift onto estimated timings (run 20: visibly out of sync). Align the finished part once with Groq
    Whisper instead — one request per section/Short, free."""
    if info.get("words") or info.get("provider") != "fish":
        return info
    words = _groq_align(mp3, text)
    if words:
        info = {**info, "words": words, "aligned": "groq"}
    return info


def synthesize_sections(cfg: dict, sections: list[dict], workdir: Path) -> list[dict]:
    """One mp3 per section so we know exact per-section durations. Adds 'audio','duration','words'.
    The provider is chosen ONCE for the whole video so the voice never changes between sections."""
    from concurrent.futures import ThreadPoolExecutor
    provider = provider_for(cfg, sum(len(s["narration"]) for s in sections))
    print(f"      voice: {provider}" + (" (with delivery cues)" if provider == "fish" and any(s.get("spoken") for s in sections) else "")
          + f", paced in breath groups ({PAUSE_S}s pauses)")

    def _one(i_s):
        i, s = i_s
        mp3 = workdir / f"sec_{i:02d}.mp3"
        info = synthesize_paced(cfg, spoken_text(s, provider), mp3, provider)
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
