"""Muse VM -> Render resident courier (stdlib only; no Muse login cookies).

Run once inside a user-authorized Muse Secure VM:
  python3 agent_courier.py pair --code-file /home/hatch/private-pair-code
Then run persistently through the EXISTING runtime hook/supervisor:
  python3 agent_courier.py serve

The courier is NOT an LLM agent. It makes outbound HTTPS requests, saves
approved work orders in a local persistent inbox, and uploads completed
JSON results from the persistent outbox. A Muse-native event/agent trigger
must be configured separately by Muse: saving a JSON file does not wake
the Muse AI on its own. No scheduled AI tasks or Muse session cookies.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
from pathlib import Path
import re
import signal
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

DEFAULT_BASE = "https://muse-chatgpt-bridge.onrender.com"
DEFAULT_HOME = Path("/home/hatch/muse-bridge")
TASK_RE = re.compile(r"^[0-9a-f]{16}$")
MAX_BODY = 65000


def _base_url(value: str) -> str:
    parsed = urlparse(value)
    # This is a dedicated personal Render service. Forbid SSRF, localhost,
    # insecure HTTP, URL credentials and nonstandard paths.
    if (parsed.scheme != "https" or parsed.hostname !=
            "muse-chatgpt-bridge.onrender.com" or parsed.port not in (None, 443)
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path not in ("", "/")):
        raise ValueError("Only the pinned HTTPS Muse Bridge origin is allowed")
    return DEFAULT_BASE


def _private_write(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            output.write(value)
            output.flush()
            os.fsync(output.fileno())
        os.replace(tmp, path)
        os.chmod(path, 0o600)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def _request(url: str, *, token: str | None = None,
             body: dict | None = None, timeout: int = 35) -> tuple[int, dict | None]:
    headers = {"Accept": "application/json", "User-Agent": "muse-resident-courier/1"}
    if token:
        headers["Authorization"] = "Bearer " + token
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        if len(data) > MAX_BODY:
            raise ValueError("JSON payload too large")
    req = Request(url, data=data, headers=headers, method="POST" if body is not None else "GET")
    try:
        with urlopen(req, timeout=timeout) as response:
            status = response.status
            raw = response.read(MAX_BODY + 1)
            if len(raw) > MAX_BODY:
                raise ValueError("response too large")
    except HTTPError as err:
        # Never print server response, which could contain sensitive material.
        return err.code, None
    if status == 204:
        return status, None
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("server JSON was not an object")
    return status, payload


def pair(base: str, code: str, home: Path, label: str = "muse-vm") -> dict:
    if not code.strip():
        raise ValueError("empty pairing code")
    url = _base_url(base)
    status, payload = _request(url + "/agent/pair", body={
        "pair_code": code.strip(), "label": label,
    })
    if status != 200 or not isinstance(payload, dict):
        return {"ok": False, "error": f"pair failed (HTTP {status})"}
    token = payload.get("agent_token")
    if not isinstance(token, str) or not token.startswith("mrc1.") or len(token) > 1000:
        return {"ok": False, "error": "invalid server pairing reply"}
    _private_write(home / "agent-token", token + "\n")
    return {"ok": True, "status": "paired", "token_saved_to": str(home / "agent-token")}


def _token(home: Path) -> str:
    path = home / "agent-token"
    if path.is_symlink() or (path.stat().st_mode & 0o077):
        raise PermissionError("agent-token file must be private (mode 0600)")
    return path.read_text(encoding="utf-8").strip()


def _stage_order(home: Path, data: dict) -> bool:
    task_id = data.get("task_id")
    if not isinstance(task_id, str) or not TASK_RE.fullmatch(task_id):
        raise ValueError("malformed task_id")
    repo = data.get("repo")
    if repo not in ("hermes-agent", "apex-6-private"):
        raise ValueError("unexpected repository")
    instruction = data.get("instruction")
    if not isinstance(instruction, str) or not 0 < len(instruction) <= 6000:
        raise ValueError("invalid instruction")
    path = home / "inbox" / f"{task_id}.json"
    if path.exists():
        return False  # idempotent across redelivery or restart
    _private_write(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    return True


def _flush_outbox(base: str, token: str, home: Path) -> None:
    outbox = home / "outbox"
    outbox.mkdir(parents=True, exist_ok=True, mode=0o700)
    for path in sorted(outbox.glob("*.json"))[:25]:
        if not TASK_RE.fullmatch(path.stem) or path.is_symlink():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or payload.get("task_id") != path.stem:
                continue
            status, _response = _request(base + "/agent/complete", token=token, body=payload)
            if status == 200:
                sent = home / "sent" / path.name
                sent.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                os.replace(path, sent)
                print("completion delivered for " + path.stem, flush=True)
            elif status == 401:
                raise PermissionError("Muse resident token rejected; re-pair is needed")
        except (OSError, ValueError, json.JSONDecodeError) as err:
            print("outbox delivery deferred for " + path.stem + " (" + type(err).__name__ + ")",
                  file=sys.stderr, flush=True)


def serve(base: str, home: Path, *, max_cycles: int | None = None) -> None:
    url = _base_url(base)
    token = _token(home)
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    stop = False

    def _stop(_signum, _frame):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    retry = 1
    cycles = 0
    print("resident courier started (outbound HTTPS, no scheduled AI task)", flush=True)
    while not stop:
        if max_cycles is not None and cycles >= max_cycles:
            break
        cycles += 1
        try:
            _flush_outbox(url, token, home)
            status, payload = _request(url + "/agent/next?wait=20", token=token, timeout=40)
            if status == 401:
                raise PermissionError("Muse resident credential rejected; needs one-time re-pair")
            if status == 200 and payload is not None:
                is_new = _stage_order(home, payload)
                if is_new:
                    print("new approved order stored: " + payload["task_id"], flush=True)
                    # No arbitrary shell execution here. Muse must wire its
                    # native event trigger to this persistent inbox once.
            elif status != 204:
                raise RuntimeError("bridge returned unexpected HTTP response")
            retry = 1
        except PermissionError:
            raise  # do not silently keep trying rejected credentials
        except (OSError, URLError, RuntimeError, ValueError, json.JSONDecodeError):
            # Network outage or Render cold start: keep restoring connection.
            print(f"bridge temporarily unreachable; retry in {retry}s", file=sys.stderr, flush=True)
            time.sleep(retry)
            retry = min(retry * 2, 60)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Muse VM permanent outbound Render courier")
    p.add_argument("command", choices=["pair", "serve", "check"])
    p.add_argument("--base-url", default=DEFAULT_BASE)
    p.add_argument("--home", type=Path, default=DEFAULT_HOME)
    p.add_argument("--code-file", type=Path, help="Read one-time pairing code privately from this file")
    p.add_argument("--label", default="muse-vm")
    args = p.parse_args(argv)
    base = _base_url(args.base_url)
    if args.command == "pair":
        code = args.code_file.read_text(encoding="utf-8") if args.code_file else getpass.getpass(
            "Enter ChatGPT Muse Bridge pairing code: ")
        output = pair(base, code, args.home, label=args.label)
        print(json.dumps(output, ensure_ascii=False))
        return 0 if output.get("ok") else 1
    if args.command == "check":
        token = _token(args.home)
        status, payload = _request(base + "/agent/next?wait=0", token=token)
        print(json.dumps({"status": "authenticated" if status in (200, 204) else
                          "authentication_failed", "http_status": status}))
        return 0 if status in (200, 204) else 1
    serve(base, args.home)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
