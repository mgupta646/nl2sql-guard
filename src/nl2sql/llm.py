"""LLM backends. Live ones use only the standard library and read keys from env vars."""
from __future__ import annotations

import json
import os
import re
import urllib.request
from typing import Protocol


class LLM(Protocol):
    name: str

    def generate(self, system: str, user: str) -> str: ...


def extract_sql(text: str) -> str:
    """Pull SQL out of a model reply (```sql fences, or the whole reply)."""
    m = re.search(r"```(?:sql)?\s*(.*?)```", text, re.S | re.I)
    return (m.group(1) if m else text).strip()


def _post(url: str, headers: dict, body: dict) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=60) as r:  # noqa: S310
        return json.loads(r.read())


class OpenAICompatible:
    def __init__(self, name: str, base_url: str, model: str, key_env: str):
        self.name, self.url, self.model, self.key_env = name, base_url.rstrip("/") + "/chat/completions", model, key_env

    def generate(self, system: str, user: str) -> str:
        key = os.environ.get(self.key_env) or ""
        if not key:
            raise RuntimeError(f"set {self.key_env}")
        d = _post(self.url, {"Authorization": f"Bearer {key}"},
                  {"model": self.model, "temperature": 0, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]})
        return d["choices"][0]["message"]["content"]


class Anthropic:
    def __init__(self, model: str = "claude-sonnet-5-5"):
        self.name, self.model = f"anthropic:{model}", model

    def generate(self, system: str, user: str) -> str:
        key = os.environ.get("ANTHROPIC_API_KEY") or ""
        if not key:
            raise RuntimeError("set ANTHROPIC_API_KEY")
        d = _post("https://api.anthropic.com/v1/messages", {"x-api-key": key, "anthropic-version": "2023-06-01"},
                  {"model": self.model, "max_tokens": 800, "system": system, "messages": [{"role": "user", "content": user}]})
        return "".join(b.get("text", "") for b in d["content"])


class Recorded:
    """Replays recorded replies, keyed by question. Each question maps to a list of
    replies, one per attempt, so self-correction can be replayed too."""

    def __init__(self, replies: dict[str, list[str]], name: str = "recorded"):
        self.replies, self.name, self._turn = replies, name, {}

    def generate(self, system: str, user: str) -> str:
        q = user.split("QUESTION:", 1)[-1].split("\n", 1)[0].strip()
        if q not in self.replies:
            raise KeyError(f"no recorded reply for {q!r}")
        i = self._turn.get(q, 0)
        self._turn[q] = i + 1
        seq = self.replies[q]
        return seq[min(i, len(seq) - 1)]


def get_llm(provider: str, model: str | None = None) -> LLM:
    presets = {
        "groq": ("https://api.groq.com/openai/v1", "llama-3.3-70b-versatile", "GROQ_API_KEY"),
        "openai": ("https://api.openai.com/v1", "gpt-4o-mini", "OPENAI_API_KEY"),
    }
    if provider == "anthropic":
        return Anthropic(model or "claude-sonnet-5-5")
    if provider in presets:
        url, default, env = presets[provider]
        return OpenAICompatible(f"{provider}:{model or default}", url, model or default, env)
    raise ValueError(f"unknown provider {provider!r}")
