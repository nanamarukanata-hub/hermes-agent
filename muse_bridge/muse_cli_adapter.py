"""Opt-in adapter for personal muse.ai via nikships/muse-cli.

This is not Meta Muse Code (`muse`). No automatic VM wake or AI keepalive.
Cookies must be configured outside Git, mode 0600, in the CLI's home.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess

MAX_INSTRUCTION = 6000
MAX_REPLY = 12000


def _available(binary: str = "muse-cli") -> bool:
    return shutil.which(binary) is not None


def _cookie_ready(home: str | None = None) -> bool:
    path = Path(home or str(Path.home())) / ".config" / "muse-cli" / "cookies.txt"
    try:
        mode = path.stat().st_mode
        return path.is_file() and (mode & 0o077) == 0 and path.stat().st_size > 0
    except OSError:
        return False


def configure_cookie_from_environment() -> bool:
    """Provision personal Muse cookies on an ephemeral host at boot.

    MUSE_BRIDGE_MUSE_COOKIES is a Render secret, never a Git file or
    ChatGPT message. This is separate from the Bridge OAuth approval key.
    """
    raw = os.environ.pop("MUSE_BRIDGE_MUSE_COOKIES", "")
    if not raw:
        return False
    if "hatch_sess" not in raw or len(raw) > 65536:
        return False
    path = Path.home() / ".config" / "muse-cli" / "cookies.txt"
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_text(raw.strip() + "\n", encoding="utf-8")
    path.chmod(0o600)
    return True


def _execute(args: list[str], *, binary: str = "muse-cli", timeout: int = 30) -> dict:
    """Run an argv-only CLI command; never return stderr or raw credentials."""
    if not _available(binary):
        return {"ok": False, "error": "MUSE_CLI_NOT_INSTALLED"}
    if not _cookie_ready():
        return {"ok": False, "error": "MUSE_AUTH_NOT_CONFIGURED"}
    env = {**os.environ, "MUSE_NO_UPDATE_CHECK": "1"}
    try:
        p = subprocess.run([binary, *args], capture_output=True, text=True,
                           timeout=timeout, check=False, env=env)
    except subprocess.TimeoutExpired:
        # A Muse send can reach the agent even if the reply times out.
        return {"ok": False, "error": "MUSE_COMMAND_TIMEOUT_DELIVERY_UNKNOWN"}
    except OSError:
        return {"ok": False, "error": "MUSE_CLI_EXECUTION_FAILED"}
    if p.returncode != 0:
        return {"ok": False, "error": "MUSE_CLI_COMMAND_FAILED"}
    try:
        data = json.loads(p.stdout)
    except (TypeError, ValueError):
        return {"ok": False, "error": "MUSE_CLI_INVALID_JSON"}
    if not isinstance(data, dict):
        return {"ok": False, "error": "MUSE_CLI_INVALID_JSON"}
    return {"ok": True, "data": data}


def status() -> dict:
    out = _execute(["status"], timeout=20)
    if not out["ok"]:
        return out
    data = out["data"]
    summary = {k: data[k] for k in ("reachable", "healthy", "unread", "chat_count", "session_count")
               if type(data.get(k)) in (bool, int)}
    return {"ok": True, "muse": summary,
            "note": "Authenticated CLI status returned; Muse VM state may change."}


def send(instruction: str, *, repo: str, task_id: str, wait_seconds: int = 15) -> dict:
    if not isinstance(instruction, str) or not 0 < len(instruction.strip()) <= MAX_INSTRUCTION:
        return {"ok": False, "error": "INVALID_INSTRUCTION"}
    if repo not in ("hermes-agent", "apex-6-private"):
        return {"ok": False, "error": "INVALID_REPO"}
    if type(wait_seconds) is not int or not 0 <= wait_seconds <= 20:
        return {"ok": False, "error": "INVALID_WAIT"}
    message = (f"ChatGPT Muse Bridge work order {task_id} for {repo}:\n"
               f"{instruction.strip()}\n"
               "Report what was actually executed, and any blockers. "
               "Do not claim unexecuted work succeeded.")
    out = _execute(["send", message, "--wait", str(wait_seconds)],
                   timeout=max(30, wait_seconds + 15))
    if not out["ok"]:
        return out
    data = out["data"]
    if data.get("sent") is not True:
        return {"ok": False, "error": "MUSE_SEND_UNCONFIRMED"}
    reply = data.get("reply")
    reply_text = reply.get("text") if isinstance(reply, dict) else None
    return {"ok": True, "sent": True,
            "reply_received": isinstance(reply_text, str) and bool(reply_text),
            "reply": reply_text[:MAX_REPLY] if isinstance(reply_text, str) else None,
            "note": "Muse's reply alone does not prove code was modified."}
