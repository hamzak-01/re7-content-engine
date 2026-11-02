"""Thin wrapper around the Claude API. One place for the model name, retries and JSON parsing."""
from __future__ import annotations

import json
import os
import re
import time

MODELS = [m for m in [os.environ.get("RE7_MODEL", "")] if m] + ["claude-sonnet-5-5", "claude-haiku-5-5", "claude-sonnet-4-5"]


def _client():
    import anthropic
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")
    return anthropic.Anthropic(api_key=key)


def complete(system: str, user: str, max_tokens: int = 4000, temperature: float = 0.2) -> str:
    client = _client()
    last = None
    for model in MODELS:
        for attempt in range(2):
            try:
                msg = client.messages.create(
                    model=model, max_tokens=max_tokens, temperature=temperature,
                    system=system, messages=[{"role": "user", "content": user}],
                )
                return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
            except Exception as e:  # noqa: BLE001
                last = e
                if "not_found" in str(e).lower() or "404" in str(e):
                    break  # try the next model name
                time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"Claude API call failed: {last}")


def complete_json(system: str, user: str, max_tokens: int = 4000):
    text = complete(system + "\nRespond with valid JSON only, no prose, no code fences.", user, max_tokens)
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    start = min([i for i in (text.find("{"), text.find("[")) if i >= 0] or [0])
    return json.loads(text[start:])
