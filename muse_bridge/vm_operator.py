"""Owner-controlled Muse VM development operator for approved Courier inbox tasks.

Unlike a generic shell proxy, this is a constrained, explicitly invoked
capability adapter. Receiving a task never starts an operation. An authorized
Muse VM agent must select an action and explicitly execute it; write and test
actions require an additional --execute flag.

No shell=True, arbitrary commands, privilege escalation, remote URL fetches,
user-supplied subprocess arguments or secret dumping.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import time
from typing import Any

from .vm_courier import CourierError, _private_dir, TASK_ID

MAX_FILE = 256 * 1024
MAX_OUTPUT = 12000
TEST_PATTERN = re.compile(r"^test_[A-Za-z0-9_]+\.py$")
DENIED_PARTS = {".git", ".ssh", ".gnupg", ".aws", ".config", ".env",
                ".secrets", "secrets", "__pycache__"}
DENIED_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".kdbx"}
ACTIONS = {
    "read_file": {"writes": False, "requires": "--path"},
    "write_file": {"writes": True, "requires": "--path and --content-file"},
    "git_status": {"writes": False, "requires": "none"},
    "git_diff": {"writes": False, "requires": "none"},
    "run_tests": {"writes": True, "requires": "--execute and --test-pattern"},
}


def _task(root: Path, task_id: str) -> dict:
    if not TASK_ID.fullmatch(task_id):
        raise CourierError("task_id must be 16 lowercase hex characters")
    path = root / "inbox" / (task_id + ".json")
    if not path.is_file() or path.is_symlink():
        raise CourierError("task is not present in the local Courier inbox")
    info = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(info, dict) or info.get("task_id") != task_id:
        raise CourierError("invalid Courier inbox record")
    if info.get("repo") != "hermes-agent":
        raise CourierError("task is not authorized for this repository")
    return info


def _safe_workspace(workspace: Path) -> Path:
    resolved = workspace.expanduser().resolve(strict=True)
    if not resolved.is_dir():
        raise CourierError("workspace is not a directory")
    if not ((resolved / "muse_bridge" / "vm_courier.py").is_file()
            and (resolved / "tests").is_dir()):
        raise CourierError("workspace does not look like the expected Hermes checkout")
    # Do not expose a home-directory root as an unrestricted search area.
    if resolved in (Path.home().resolve(), Path("/")):
        raise CourierError("workspace is too broad")
    return resolved


def _safe_path(workspace: Path, relative: str, *, allow_missing: bool = False) -> Path:
    if not isinstance(relative, str) or not relative or relative.startswith(("/", "~", "\\")):
        raise CourierError("only workspace-relative paths are accepted")
    raw_parts = Path(relative).parts
    if (any(p in ("", ".", "..") or p in DENIED_PARTS for p in raw_parts)
            or any(p.lower().endswith(tuple(DENIED_SUFFIXES)) for p in raw_parts)
            or any("token" in p.lower() or "credential" in p.lower() for p in raw_parts)):
        raise CourierError("secret, traversal or reserved paths are not allowed")
    candidate = workspace / relative
    try:
        real = candidate.resolve(strict=not allow_missing)
        real.relative_to(workspace)
    except (OSError, ValueError):
        raise CourierError("path must resolve within workspace") from None
    if candidate.is_symlink():
        raise CourierError("symlink targets are disallowed")
    return candidate


def _audit(state: Path, event: dict[str, Any]) -> None:
    _private_dir(state)
    log = state / "operator-audit.jsonl"
    if log.exists() and (log.is_symlink() or log.stat().st_mode & 0o077):
        raise CourierError("audit log permissions are unsafe")
    line = (json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
    flags = os.O_CREAT | os.O_APPEND | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(log, flags, 0o600)
    try:
        os.write(fd, line)
        os.fsync(fd)
    finally:
        os.close(fd)


def _run(argv: list[str], workspace: Path, timeout: int) -> str:
    # The argv are hard-coded below; user text never becomes an executable or flag.
    env = {k: v for k, v in os.environ.items()
           if k in ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "SYSTEMROOT")}
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_OPTIONAL_LOCKS"] = "0"
    try:
        p = subprocess.run(argv, cwd=workspace, env=env, stdin=subprocess.DEVNULL,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise CourierError("local command failed or timed out") from None
    text = p.stdout[:MAX_OUTPUT + 1].decode("utf-8", "replace")
    if len(text) > MAX_OUTPUT:
        text = text[:MAX_OUTPUT] + "\n[output truncated]"
    return f"return_code={p.returncode}\n{text}"


def _write_file(target: Path, source: Path, allow_overwrite: bool) -> str:
    if not source.is_file() or source.is_symlink():
        raise CourierError("content source must be an existing regular file")
    if source.stat().st_size > MAX_FILE:
        raise CourierError("content exceeds size limit")
    if target.exists() and not allow_overwrite:
        raise CourierError("file exists; explicit --overwrite required")
    if target.is_symlink():
        raise CourierError("cannot write through a symlink")
    if not target.parent.is_dir():
        raise CourierError("parent directory must already exist")
    data = source.read_bytes()
    if len(data) > MAX_FILE:
        raise CourierError("content exceeds size limit")
    # Avoid accidental commits of invalid text; binary work is out of scope.
    data.decode("utf-8")
    fd, temp_name = tempfile.mkstemp(prefix=".operator-", dir=target.parent)
    tmp = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            os.fchmod(handle.fileno(), 0o600)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if target.exists() and not allow_overwrite:
            raise CourierError("target appeared while writing")
        os.replace(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)
    return "wrote %d bytes to %s" % (len(data), target.name)


def execute(args: argparse.Namespace) -> str:
    state = args.home.expanduser()
    _private_dir(state)
    event = {"timestamp": int(time.time()), "task_id": args.task_id,
             "action": args.action, "result": "denied"}
    phase = "preflight"
    try:
        _task(state, args.task_id)
        workspace = _safe_workspace(args.workspace)
        action = args.action
        if action not in ACTIONS:
            raise CourierError("action not in capability allowlist")
        if ACTIONS[action]["writes"] and not args.execute:
            raise CourierError("this action requires --execute after Muse review")
        if action == "read_file":
            target = _safe_path(workspace, args.path)
            if not target.is_file() or target.stat().st_size > MAX_FILE:
                raise CourierError("file not found or too large")
            phase = "execution"
            result = target.read_text(encoding="utf-8")[:MAX_OUTPUT]
        elif action == "write_file":
            target = _safe_path(workspace, args.path, allow_missing=True)
            if not args.content_file:
                raise CourierError("--content-file required for write_file")
            phase = "execution"
            result = _write_file(target, args.content_file, args.overwrite)
        elif action == "git_status":
            phase = "execution"
            result = _run(["git", "status", "--short"], workspace, timeout=15)
        elif action == "git_diff":
            phase = "execution"
            result = _run(["git", "diff", "--no-ext-diff", "--stat"], workspace, timeout=15)
        else:  # run_tests
            pattern = args.test_pattern
            if not TEST_PATTERN.fullmatch(pattern):
                raise CourierError("test pattern must be a specific test_*.py basename")
            phase = "execution"
            result = _run([sys.executable, "-m", "unittest", "discover", "-s", "tests",
                           "-p", pattern, "-v"], workspace, timeout=180)
        event["result"] = "executed"
        return result
    except Exception:
        event["result"] = "error" if phase == "execution" else "denied"
        raise
    finally:
        _audit(state, event)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Reviewed capabilities for authorized Muse inbox tasks")
    parser.add_argument("--home", type=Path, default=Path.home() / ".local/share/muse-bridge-courier")
    parser.add_argument("--workspace", type=Path,
                        default=Path.home() / "workspace/courier-intake/hermes-agent")
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--action", choices=sorted(ACTIONS), required=True)
    parser.add_argument("--path")
    parser.add_argument("--content-file", type=Path)
    parser.add_argument("--test-pattern", default="test_muse_vm_courier.py")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    os.umask(0o077)
    try:
        print(execute(args))
        return 0
    except (CourierError, OSError, UnicodeError, ValueError) as exc:
        print("Operator error: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
