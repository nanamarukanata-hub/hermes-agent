"""Muse VM outbound courier for the authenticated Muse ChatGPT Bridge.

This only transports *approved* work orders to a local inbox. It never
executes received instructions, git commands, model calls or shell commands.
All completion reports require a separate explicit action by the Muse agent.

Python 3.10+, standard library only. No Muse account cookies.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from urllib.parse import urlsplit

DEFAULT_BASE = "https://muse-chatgpt-bridge.onrender.com"
DEFAULT_HOME = Path.home() / ".local" / "share" / "muse-bridge-courier"
MAX_JSON = 128_000
TASK_ID = re.compile(r"^[0-9a-f]{16}$")


class CourierError(Exception):
    pass


def _base(value: str) -> str:
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username
            or parsed.password or parsed.query or parsed.fragment or
            parsed.path not in ("", "/")):
        raise CourierError("Bridge base must be an HTTPS origin, with no credentials or path")
    return value.rstrip("/")


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CourierError("local JSON file is missing or invalid") from exc
    if not isinstance(data, dict):
        raise CourierError("local JSON must contain an object")
    return data


def _private_dir(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.stat().st_mode & 0o077:
        raise CourierError("state directory has unsafe permissions")


def _save_private(path: Path, content: bytes) -> None:
    _private_dir(path.parent)
    fd, temp_name = tempfile.mkstemp(prefix=".pending-", dir=str(path.parent))
    tmp = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as f:
            os.fchmod(f.fileno(), 0o600)
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _load_token(root: Path) -> str:
    path = root / "agent.token"
    try:
        mode = path.stat().st_mode
        if not stat.S_ISREG(mode) or mode & 0o077:
            raise CourierError("agent.token must be a private regular file (0600)")
        token = path.read_text(encoding="ascii").strip()
    except OSError as exc:
        raise CourierError("agent.token missing; pair this VM first") from exc
    if not token.startswith("mrc1.") or len(token) > 1000:
        raise CourierError("invalid local agent token")
    return token


def _request(base: str, path: str, *, token: str | None = None,
             payload: dict | None = None, timeout: int = 38) -> tuple[int, Any]:
    headers = {"Accept": "application/json", "Cache-Control": "no-store"}
    if token:
        headers["Authorization"] = "Bearer " + token
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(base + path, headers=headers, data=data,
                      method="POST" if payload is not None else "GET")
    try:
        with urlopen(request, timeout=timeout) as response:
            status = response.status
            if status == 204:
                return status, None
            data = response.read(MAX_JSON + 1)
    except HTTPError as exc:
        if exc.code in (401, 403):
            raise CourierError("authentication rejected; verify pairing or rotate") from None
        raise CourierError("Bridge HTTP error " + str(exc.code)) from None
    except (URLError, TimeoutError, OSError):
        raise CourierError("Bridge network request failed") from None
    if len(data) > MAX_JSON:
        raise CourierError("Bridge response too large")
    try:
        body = json.loads(data)
    except (ValueError, UnicodeDecodeError):
        raise CourierError("Bridge did not return valid JSON") from None
    if not isinstance(body, dict):
        raise CourierError("unexpected Bridge response schema")
    return status, body


def pair(base: str, root: Path, code: str) -> None:
    code = code.strip()
    if not 25 <= len(code) <= 96:
        raise CourierError("invalid pairing code length")
    _, response = _request(base, "/agent/pair",
                           payload={"pair_code": code, "label": "muse-vm"})
    if response.get("status") != "paired":
        raise CourierError("Bridge did not confirm pairing")
    token = response.get("agent_token")
    if not isinstance(token, str) or not token.startswith("mrc1."):
        raise CourierError("Bridge returned no valid agent credential")
    _save_private(root / "agent.token", (token + "\n").encode("ascii"))
    print("Pairing succeeded. Credential saved privately; not displayed.")


def _save_inbox(root: Path, task: dict) -> bool:
    task_id = task.get("task_id")
    if not isinstance(task_id, str) or not TASK_ID.fullmatch(task_id):
        raise CourierError("Bridge sent invalid task id")
    if not isinstance(task.get("instruction"), str):
        raise CourierError("Bridge sent invalid instruction")
    inbox = root / "inbox"
    _private_dir(inbox)
    destination = inbox / (task_id + ".json")
    if destination.exists() or (root / "done" / (task_id + ".json")).exists():
        return False
    content = (json.dumps(task, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if len(content) > MAX_JSON:
        raise CourierError("task exceeds local size limit")
    # Link from a private temporary file to avoid replacing an existing task.
    fd, name = tempfile.mkstemp(prefix=".new-", dir=str(inbox))
    tmp = Path(name)
    try:
        with os.fdopen(fd, "wb") as f:
            os.fchmod(f.fileno(), 0o600)
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        try:
            os.link(tmp, destination)
        except FileExistsError:
            return False
        return True
    finally:
        tmp.unlink(missing_ok=True)


def poll(base: str, root: Path, once: bool) -> None:
    token = _load_token(root)
    failures = 0
    while True:
        try:
            status, response = _request(base, "/agent/next?wait=25", token=token)
            failures = 0
            if status == 204:
                if once:
                    print("Connected; no approved task pending.")
                    return
            elif isinstance(response, dict):
                added = _save_inbox(root, response)
                if added:
                    print("Received approved task " + response["task_id"] +
                          " in local inbox; NO execution was performed.")
                elif once:
                    print("Connected; task already in local inbox.")
                if once:
                    return
        except CourierError as exc:
            if "authentication rejected" in str(exc) or once:
                raise
            failures += 1
            print("Courier reconnect: " + str(exc), file=sys.stderr)
            time.sleep(min(60, 2 ** min(failures, 5)))


def complete(base: str, root: Path, task_id: str, result_file: Path) -> None:
    if not TASK_ID.fullmatch(task_id):
        raise CourierError("invalid task id")
    source = root / "inbox" / (task_id + ".json")
    if not source.is_file():
        raise CourierError("task not found in local inbox")
    result = _read_json(result_file)
    if not isinstance(result.get("result"), str) or not result["result"].strip():
        raise CourierError("result file requires a nonempty 'result' string")
    payload = {"task_id": task_id, "result": result["result"],
               "failed": result.get("failed", False)}
    if type(payload["failed"]) is not bool:
        raise CourierError("'failed' must be boolean")
    for key in ("diff", "test_results"):
        if key in result:
            if not isinstance(result[key], str):
                raise CourierError(key + " must be a string")
            payload[key] = result[key]
    _, response = _request(base, "/agent/complete",
                           token=_load_token(root), payload=payload)
    if response.get("task_id") != task_id or response.get("status") not in ("done", "failed"):
        raise CourierError("Bridge did not confirm task completion")
    done = root / "done"
    _private_dir(done)
    os.replace(source, done / (task_id + ".json"))
    print("Completion acknowledged for task " + task_id)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Muse VM outbound courier (manual approval only)")
    parser.add_argument("--base", default=DEFAULT_BASE)
    parser.add_argument("--home", type=Path, default=DEFAULT_HOME)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("pair", help="Pair once using a short-lived code")
    p.add_argument("--code-file", type=Path, help="Optional private file containing the code")
    q = sub.add_parser("poll", help="Long-poll approved work orders into a private inbox")
    q.add_argument("--once", action="store_true")
    c = sub.add_parser("complete", help="Report an agent-reviewed result; never execute a task")
    c.add_argument("task_id")
    c.add_argument("--result-file", type=Path, required=True)
    args = parser.parse_args(argv)
    os.umask(0o077)
    try:
        base = _base(args.base)
        root = args.home.expanduser()
        _private_dir(root)
        if args.command == "pair":
            if args.code_file:
                mode = args.code_file.stat().st_mode
                if mode & 0o077:
                    raise CourierError("code file must be private (0600)")
                code = args.code_file.read_text(encoding="ascii")
            else:
                code = getpass.getpass("Short-lived pairing code (hidden): ")
            pair(base, root, code)
        elif args.command == "poll":
            poll(base, root, args.once)
        else:
            complete(base, root, args.task_id, args.result_file)
        return 0
    except (CourierError, OSError, UnicodeError) as exc:
        print("Courier error: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
