"""Thin wrapper around the Claude API. One place for the model name, retries and JSON parsing."""
from __future__ import annotations

import json
import os
import re
import time

MODELS = [m for m in [os.environ.get("RE7_MODEL", "")] if m] + ["claude-sonnet-5-5", "claude-haiku-5-5", "claude-opus-5-5"]


def _create_params() -> set:
    try:
        import inspect
        from anthropic.resources.messages import Messages
        return set(inspect.signature(Messages.create).parameters)
    except Exception:  # noqa: BLE001
        return set()


_CREATE_PARAMS = _create_params()


def _client():
    import anthropic
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")
    return anthropic.Anthropic(api_key=key)


WARNINGS: list[str] = []


def _writing_settings(model: str) -> dict:
    low = {"output_config": {"effort": "low"}}
    if "sonnet" in model:
        return {"thinking": {"type": "between_tools"}, **low}
    if "haiku" in model:
        return {"thinking": {"type": "disabled"}, **low}
    return low  # opus and anything else: cannot disable; keep effort low  # non-fatal problems from the last run, surfaced in the run log


def complete(system: str, user: str, max_tokens: int = 4000, temperature: float | None = None) -> str:
    """Return a complete reply. A reply that stops early (max_tokens, or a safety stop) is kept
    as a fallback but the next model is tried first, so a cut-off memo or draft list is rare."""
    client = _client()
    attempts: list[str] = []
    partial = ""
    for model in MODELS:
        for attempt in range(2):
            try:
                kwargs = dict(model=model, max_tokens=max(max_tokens, 12000), system=system,
                              messages=[{"role": "user", "content": user}])
                if temperature is not None and "temperature" in _CREATE_PARAMS:
                    kwargs["temperature"] = temperature
                # These are writing calls, not reasoning calls. Current models think by default and
                # on a long input the thinking can use the whole token budget, returning no text at all.
                # Each model family has its own way of being told not to (see docs: thinking):
                #   Sonnet 5.5 rejects "disabled" and takes "between_tools"; Haiku 5.5 takes "disabled";
                #   Opus 5.5 cannot turn thinking off, so it gets a low effort and a large budget.
                for k, v in _writing_settings(model).items():
                    if k in _CREATE_PARAMS:
                        kwargs[k] = v
                try:
                    msg = client.messages.create(**kwargs)
                except Exception as e:  # noqa: BLE001
                    err = str(e).lower()
                    if ("thinking" in err or "effort" in err or "output_config" in err) and (
                            "thinking" in kwargs or "output_config" in kwargs):
                        kwargs.pop("thinking", None)
                        kwargs.pop("output_config", None)
                        kwargs["max_tokens"] = max(max_tokens, 16000)
                        msg = client.messages.create(**kwargs)
                    else:
                        raise
                text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
                stop = getattr(msg, "stop_reason", "?")
                if text.strip() and stop in ("end_turn", "stop_sequence"):
                    return text
                if text.strip():
                    attempts.append(f"{model}: reply stopped early (stop_reason={stop}, {len(text)} chars)")
                    if len(text) > len(partial):
                        partial = text
                else:
                    attempts.append(f"{model}: empty reply (stop_reason={stop}, input_chars={len(system) + len(user)})")
                break  # same input will stop the same way; try the next model
            except Exception as e:  # noqa: BLE001
                attempts.append(f"{model}: {str(e)[:120]}")
                if "not_found" in str(e).lower() or "404" in str(e):
                    break  # try the next model name
                time.sleep(2 * (attempt + 1))
    if partial:
        WARNINGS.append("used a cut-off reply; " + " | ".join(attempts))
        return partial
    raise RuntimeError("Claude API call failed; attempts: " + " | ".join(attempts))


def _loads(t: str):
    return json.loads(t, strict=False)  # allow raw newlines/tabs inside strings


def _salvage_array(text: str):
    """A reply cut off mid-array: keep the complete objects and close the array."""
    a = text.find("[")
    if a < 0:
        return None
    depth, in_str, esc, last_obj_end = 0, False, False, -1
    for i, ch in enumerate(text[a:], start=a):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                last_obj_end = i
    if last_obj_end > a:
        try:
            return _loads(text[a:last_obj_end + 1] + "]")
        except json.JSONDecodeError:
            return None
    return None


def _parse_json(text: str):
    text = text.strip().lstrip("\ufeff")
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text).strip()
    try:
        return _loads(text)
    except json.JSONDecodeError:
        pass
    for open_c, close_c in (("[", "]"), ("{", "}")):
        a, b = text.find(open_c), text.rfind(close_c)
        if a >= 0 and b > a:
            try:
                return _loads(text[a:b + 1])
            except json.JSONDecodeError:
                continue
    salvaged = _salvage_array(text)
    if salvaged:
        return salvaged
    raise ValueError(f"model did not return JSON; reply began: {text[:160]!r}")


def complete_json(system: str, user: str, max_tokens: int = 4000):
    instr = "\nRespond with valid JSON only: start your reply with [ or { and end with the matching bracket. No prose, no code fences."
    text = complete(system + instr, user, max_tokens)
    try:
        return _parse_json(text)
    except ValueError:
        # one retry with the previous reply shown, asking for JSON only
        text2 = complete(system + instr, user + "\n\nYour previous reply was not valid JSON. Return only the JSON.", max_tokens)
        return _parse_json(text2)
