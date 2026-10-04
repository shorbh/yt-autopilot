"""Design the channel's narrator with Fish Audio Voice Design instead of borrowing a library voice.

Why: S2 takes its *style* from the reference voice. Library "Narration" voices were cloned from deliberately
flat reads (Fish's own cloning guide asks for "consistent tone and emotion"), so bracket cues can only nudge them.
A designed voice can be briefed for varied intonation from the start.

Two modes (run via .github/workflows/voice_design.yml):
  generate  -> POST /v1/voice-design (1¢ per request) with the brief + a real line from our scripts, n=4, fixed seed.
               Saves out/candidate_N.wav + candidates.json to the artifact. Listen, pick one.
  create    -> regenerates the SAME candidates (same seed) and POSTs the picked one to /model as a private reusable
               voice (fast train mode, instantly available). Prints the model id -> config.yaml voice.fish_reference_id.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
from pathlib import Path

import requests

API = "https://api.fish.audio/v1"
DEFAULT_BRIEF = (
    "Warm male explainer in his mid-thirties, American English, conversational and genuinely engaged — like a sharp friend "
    "explaining money across a kitchen table, not a radio announcer. Clearly varied intonation: picks up pace when "
    "building a point, slows down and drops his voice on the big number, lets a short sentence land with a beat of "
    "silence. Natural breaths, crisp consonants, no sales energy, no monotone, no theatrical drama."
)
DEFAULT_LINE = ("At 5%, that $400,000 loan costs you $373,000 in interest. At 7%? It's $558,000. "
                "Same house. Same thirty years. One number changed.")


def _hdr(key: str, model: str | None = None) -> dict:
    h = {"Authorization": f"Bearer {key}"}
    if model:
        h["model"] = model
    return h


def generate(key: str, brief: str, line: str, seed: int, out: Path, n: int = 4) -> list[dict]:
    r = requests.post(f"{API}/voice-design", headers={**_hdr(key, "voice-design-1"), "Content-Type": "application/json"},
                      json={"instruction": brief, "reference_text": line[:300], "language": "en", "n": n, "seed": seed},
                      timeout=180)
    if r.status_code != 200:
        sys.exit(f"voice-design {r.status_code}: {r.text[:300]}")
    cands = r.json().get("candidates", [])
    out.mkdir(parents=True, exist_ok=True)
    meta = []
    for c in cands:
        i = int(c.get("index", len(meta)))
        wav = out / f"candidate_{i}.wav"
        wav.write_bytes(base64.b64decode(c["audio_base64"]))
        meta.append({"index": i, "id": c.get("id"), "signature": c.get("signature"), "duration_ms": c.get("duration_ms"),
                     "file": wav.name})
        print(f"  candidate {i}: {c.get('duration_ms', '?')} ms -> {wav.name}")
    (out / "candidates.json").write_text(json.dumps({"brief": brief, "line": line, "seed": seed, "candidates": meta}, indent=2))
    return cands


def create_model(key: str, cand: dict, line: str, title: str) -> str:
    files = [("voices", (f"candidate_{cand.get('index', 0)}.wav", base64.b64decode(cand["audio_base64"]), "audio/wav"))]
    data = [("type", "tts"), ("train_mode", "fast"), ("title", title), ("visibility", "private"),
            ("texts", line[:300]), ("enhance_audio_quality", "true"),
            ("description", "Money Mechanics narrator — designed with Fish Voice Design")]
    if cand.get("signature"):
        data.append(("voice_design_signatures", cand["signature"]))
    r = requests.post(f"{API.rsplit('/v1', 1)[0]}/model", headers=_hdr(key), files=files, data=data, timeout=180)
    if r.status_code not in (200, 201):
        sys.exit(f"create model {r.status_code}: {r.text[:300]}")
    d = r.json()
    print(f"\nVOICE MODEL CREATED: id={d.get('_id')}  state={d.get('state')}  title={d.get('title')}")
    print("Put this id into config.yaml -> voice.fish_reference_id")
    return str(d.get("_id"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["generate", "create"], default="generate")
    ap.add_argument("--brief", default=DEFAULT_BRIEF)
    ap.add_argument("--line", default=DEFAULT_LINE)
    ap.add_argument("--seed", type=int, default=20261004)
    ap.add_argument("--pick", type=int, default=0, help="candidate index to turn into a model (create mode)")
    ap.add_argument("--title", default="Money Mechanics narrator")
    ap.add_argument("--out", default="voice_design_out")
    a = ap.parse_args()
    key = os.environ.get("FISH_API_KEY")
    if not key:
        sys.exit("FISH_API_KEY is not set")
    out = Path(a.out)
    print(f"brief: {a.brief[:90]}…\nline:  {a.line[:90]}…\nseed:  {a.seed}")
    cands = generate(key, a.brief, a.line, a.seed, out)
    if a.mode == "create":
        chosen = next((c for c in cands if int(c.get("index", -1)) == a.pick), None)
        if chosen is None:
            sys.exit(f"no candidate with index {a.pick}")
        create_model(key, chosen, a.line, a.title)
    return 0


if __name__ == "__main__":
    sys.exit(main())
