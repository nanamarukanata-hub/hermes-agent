"""GPT Responses API adapter for the authenticated personal Muse VM.

Only the Render server holds OPENAI_API_KEY. Muse authenticates to Render using
its existing one-time-paired resident bearer credential. This is OpenAI API
usage (separate from a ChatGPT subscription), not a ChatGPT session proxy.

No tools, secret logging, model selection by callers, or persistent input store.
"""
from __future__ import annotations

import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

API_URL = "https://api.openai.com/v1/responses"
DEFAULT_MODEL = "gpt-4.1-mini"
MAX_PROMPT = 6000
MAX_REPLY = 24000
MAX_OUTPUT_TOKENS = 1024


def ready() -> bool:
    """Non-secret readiness flag; does not prove upstream API access/billing."""
    return bool(os.environ.get("OPENAI_API_KEY", "").strip())


def ask(prompt: object) -> dict:
    """Send one bounded text-only request, returning text or a safe error code."""
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > MAX_PROMPT:
        return {"error": "INVALID_PROMPT", "max_chars": MAX_PROMPT}
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        return {"error": "OPENAI_API_KEY_NOT_CONFIGURED"}
    model = os.environ.get("MUSE_GPT_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL
    # Fixed model chosen by the operator, never by an unauthenticated caller.
    if len(model) > 100 or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_." for c in model):
        return {"error": "INVALID_MODEL_CONFIGURATION"}
    payload = {
        "model": model,
        "input": prompt,
        "store": False,
        "max_output_tokens": MAX_OUTPUT_TOKENS,
    }
    req = Request(
        API_URL,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": "Bearer " + key,
                 "Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urlopen(req, timeout=75) as response:
            data = response.read(180000 + 1)
    except HTTPError as exc:
        # Do not forward upstream errors: they may contain user input or hints
        # about private API credentials and their workspace configuration.
        if exc.code == 401:
            return {"error": "OPENAI_AUTH_FAILED", "http_status": 401}
        if exc.code == 429:
            return {"error": "OPENAI_LIMITED", "http_status": 429}
        return {"error": "OPENAI_UPSTREAM_ERROR", "http_status": exc.code}
    except (OSError, URLError, TimeoutError):
        return {"error": "OPENAI_NETWORK_ERROR"}
    if len(data) > 180000:
        return {"error": "OPENAI_RESPONSE_TOO_LARGE"}
    try:
        body = json.loads(data)
    except (ValueError, UnicodeDecodeError):
        return {"error": "OPENAI_BAD_RESPONSE"}
    if not isinstance(body, dict):
        return {"error": "OPENAI_BAD_RESPONSE"}
    parts = []
    for item in body.get("output", []):
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for block in item.get("content", []):
            if isinstance(block, dict) and block.get("type") == "output_text":
                value = block.get("text")
                if isinstance(value, str):
                    parts.append(value)
    answer = "\n".join(parts)
    if not answer:
        return {"error": "OPENAI_NO_TEXT", "response_status": body.get("status")}
    if len(answer) > MAX_REPLY:
        answer = answer[:MAX_REPLY]
    usage = body.get("usage") or {}
    if not isinstance(usage, dict):
        usage = {}
    return {
        "ok": True, "answer": answer,
        "model": model,
        "usage": {k: usage[k] for k in ("input_tokens", "output_tokens", "total_tokens")
                  if type(usage.get(k)) is int},
        "note": "OpenAI Responses API result; not a message from this ChatGPT conversation.",
    }
