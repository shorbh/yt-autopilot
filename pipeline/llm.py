"""Pluggable LLM client. All providers have a free tier; the first key found wins.

Priority (provider=auto): GEMINI_API_KEY -> GROQ_API_KEY -> ANTHROPIC_API_KEY
Every call asks for strict JSON and retries once on parse failure.
"""
import json
import re
import time
import requests
from .config import env


class LLMError(RuntimeError):
    pass


def _pick_provider(cfg: dict) -> str:
    want = cfg["llm"].get("provider", "auto")
    if want != "auto":
        return want
    if env("GEMINI_API_KEY"):
        return "gemini"
    if env("GROQ_API_KEY"):
        return "groq"
    if env("ANTHROPIC_API_KEY"):
        return "anthropic"
    raise LLMError("No LLM key found. Set GEMINI_API_KEY (free at aistudio.google.com), GROQ_API_KEY, or ANTHROPIC_API_KEY.")


def _gemini(cfg, system, user, temperature):
    model = cfg["llm"]["gemini_model"]
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={env('GEMINI_API_KEY')}"
    body = {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": {"temperature": temperature, "response_mime_type": "application/json", "maxOutputTokens": 8192},
    }
    r = requests.post(url, json=body, timeout=180)
    if r.status_code != 200:
        raise LLMError(f"Gemini {r.status_code}: {r.text[:300]}")
    return r.json()["candidates"][0]["content"]["parts"][0]["text"]


def _groq(cfg, system, user, temperature):
    r = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {env('GROQ_API_KEY')}"},
        json={
            "model": cfg["llm"]["groq_model"],
            "temperature": temperature,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        },
        timeout=180,
    )
    if r.status_code != 200:
        raise LLMError(f"Groq {r.status_code}: {r.text[:300]}")
    return r.json()["choices"][0]["message"]["content"]


def _anthropic(cfg, system, user, temperature):
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": env("ANTHROPIC_API_KEY"), "anthropic-version": "2023-06-01"},
        json={
            "model": cfg["llm"]["anthropic_model"],
            "max_tokens": 8000,
            "temperature": temperature,
            "system": system + "\nRespond with a single JSON object and nothing else.",
            "messages": [{"role": "user", "content": user}],
        },
        timeout=180,
    )
    if r.status_code != 200:
        raise LLMError(f"Anthropic {r.status_code}: {r.text[:300]}")
    return "".join(b.get("text", "") for b in r.json()["content"])


_PROVIDERS = {"gemini": _gemini, "groq": _groq, "anthropic": _anthropic}


def _extract_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, flags=re.S)
        if not m:
            raise
        return json.loads(m.group(0))


def ask_json(cfg: dict, system: str, user: str, temperature: float | None = None, retries: int = 3) -> dict:
    provider = _pick_provider(cfg)
    fn = _PROVIDERS[provider]
    temp = cfg["llm"].get("temperature", 0.8) if temperature is None else temperature
    last = None
    for attempt in range(retries):
        try:
            raw = fn(cfg, system, user, temp)
            return _extract_json(raw)
        except (LLMError, json.JSONDecodeError, KeyError, requests.RequestException) as e:
            last = e
            time.sleep(4 * (attempt + 1))
    raise LLMError(f"LLM failed after {retries} attempts via {provider}: {last}")
