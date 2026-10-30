"""Review-gated edits for owner-owned agent instruction Markdown files.

This operates ONLY on explicitly named files in the invoking user's HOME.
It never edits service policy, platform-provided prompts, or YAML credentials.
A paired Courier brings a work order; a human/agent must explicitly run apply.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
import time
from typing import Any

from .vm_courier import CourierError, _private_dir, _save_private

TARGETS = {
    "hermes_soul": ".hermes/SOUL.md",
    "muse_soul": "SOUL.md",
    "muse_agents": "AGENTS.md",
    "muse_identity": "IDENTITY.md",
    "muse_user": "USER.md",
    "muse_memory": "MEMORY.md",
}
MAX_BYTES = 128 * 1024
_NAME = re.compile(r"^[A-Za-z0-9_-]{1,48}\.md$")
_TASK = re.compile(r"^[0-9a-f]{16}$")
_SHA = re.compile(r"^[0-9a-f]{64}$")


def _load_work_order(state: Path, task_id: str, target: str, *, require_edit: bool) -> dict:
    if not _TASK.fullmatch(task_id):
        raise CourierError("invalid task ID")
    record = state / "inbox" / (task_id + ".json")
    if record.is_symlink() or not record.is_file():
        raise CourierError("no local approved Courier work order")
    work = json.loads(record.read_text("utf-8"))
    if not isinstance(work, dict) or work.get("task_id") != task_id or work.get("repo") != "hermes-agent":
        raise CourierError("work order repository or ID mismatch")
    # Task creation and Bridge approval alone do not authorize ALL files.
    # Explicit owner instruction marker is required for this config operation.
    instruction = work.get("instruction")
    if require_edit and (not isinstance(instruction, str) or ("AGENT_CONFIG_EDIT[" + target + "]") not in instruction):
        raise CourierError("work order does not explicitly authorize this target")
    return work


def _target(home: Path, target: str) -> Path:
    if target not in TARGETS:
        raise CourierError("target is not on the owner-owned allowlist")
    root = home.expanduser().resolve(strict=True)
    if root.is_symlink() or not root.is_dir():
        raise CourierError("invalid home directory")
    path = root / TARGETS[target]
    # Explicitly refuse symlinks in both parent chain and target.
    current = root
    for component in Path(TARGETS[target]).parts:
        current = current / component
        if current.is_symlink():
            raise CourierError("symlink in target path")
    if not path.is_file():
        raise CourierError("target Markdown file does not exist")
    if path.stat().st_uid != os.getuid():
        raise CourierError("target is not owned by this VM user")
    return path


def _read_file(path: Path) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BYTES:
            raise CourierError("file is not regular or is too large")
        if info.st_uid != os.getuid():
            raise CourierError("file owner mismatch")
        data = os.read(fd, MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise CourierError("file exceeds size limit")
        return data
    finally:
        os.close(fd)


def _audit(state: Path, task_id: str, target: str, result: str) -> None:
    _private_dir(state)
    path = state / "config-audit.jsonl"
    if path.is_symlink() or (path.exists() and (path.stat().st_mode & 0o077)):
        raise CourierError("unsafe audit log")
    event = {"ts": int(time.time()), "task_id": task_id,
             "target": target, "result": result}
    line = (json.dumps(event, separators=(",", ":")) + "\n").encode()
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT |
                 getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        os.write(fd, line)
        os.fsync(fd)
    finally:
        os.close(fd)


def run(*, state: Path, home: Path, task_id: str, target: str,
        operation: str, source_name: str | None, expected_sha256: str | None,
        execute: bool) -> dict:
    _private_dir(state)
    audit_result = "denied"
    try:
        _load_work_order(state, task_id, target, require_edit=(operation == "apply"))
        path = _target(home, target)
        original = _read_file(path)
        current_sha = hashlib.sha256(original).hexdigest()
        if operation == "status":
            audit_result = "inspected"
            return {"target": target, "exists": True, "size": len(original),
                    "sha256": current_sha, "contents_exposed": False}
        if operation != "apply":
            raise CourierError("operation not supported")
        if not execute:
            raise CourierError("apply requires --execute after explicit owner review")
        if expected_sha256 is None or not _SHA.fullmatch(expected_sha256):
            raise CourierError("--expected-sha256 must be a 64-char digest from status")
        if current_sha != expected_sha256:
            raise CourierError("target changed since review; inspect again")
        if not source_name or not _NAME.fullmatch(source_name):
            raise CourierError("source name must be a private Markdown basename")
        staging = state / "staging"
        _private_dir(staging)
        source = staging / source_name
        if source.is_symlink() or not source.is_file() or source.stat().st_mode & 0o077:
            raise CourierError("staging source must be a private 0600 regular file")
        contents = _read_file(source)
        contents.decode("utf-8")  # only textual instructions, never binary
        backup_dir = state / "config-backups"
        _private_dir(backup_dir)
        # One approved operation per (task, target). A previous backup also
        # indicates a partial attempt: fail closed and require owner review.
        if any(backup_dir.glob(task_id + "-" + target + "-*.md")):
            raise CourierError("task already attempted this target; new approval required")
        backup_name = task_id + "-" + target + "-" + current_sha[:16] + ".md"
        backup = backup_dir / backup_name
        if backup.exists():
            raise CourierError("backup already exists; refuse duplicate operation")
        # Backups preserve original data privately, without printing it.
        fd = os.open(backup, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(original)
                f.flush()
                os.fsync(f.fileno())
        except Exception:
            backup.unlink(missing_ok=True)
            raise
        # Owner-reviewed content is applied atomically, never through a symlink.
        fd, tempname = tempfile.mkstemp(prefix=".owner-config-", dir=str(path.parent))
        temp = Path(tempname)
        try:
            with os.fdopen(fd, "wb") as f:
                os.fchmod(f.fileno(), 0o600)
                f.write(contents)
                f.flush()
                os.fsync(f.fileno())
            # Refuse if replaced between inspection and commit.
            if hashlib.sha256(_read_file(path)).hexdigest() != current_sha:
                raise CourierError("target changed while applying; backup retained")
            os.replace(temp, path)
        finally:
            temp.unlink(missing_ok=True)
        audit_result = "applied"
        return {"target": target, "applied": True, "bytes": len(contents),
                "sha256": hashlib.sha256(contents).hexdigest(),
                "backup": str(backup), "contents_exposed": False}
    except Exception:
        raise
    finally:
        _audit(state, task_id, target, audit_result)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Review-only owner-owned agent Markdown edits")
    p.add_argument("--state", type=Path,
                   default=Path.home() / ".local/share/muse-bridge-courier")
    p.add_argument("--home", type=Path, default=Path.home())
    p.add_argument("--task-id", required=True)
    p.add_argument("--target", choices=sorted(TARGETS), required=True)
    p.add_argument("--operation", choices=("status", "apply"), required=True)
    p.add_argument("--source-name")
    p.add_argument("--expected-sha256")
    p.add_argument("--execute", action="store_true")
    args = p.parse_args(argv)
    os.umask(0o077)
    try:
        print(json.dumps(run(state=args.state, home=args.home, task_id=args.task_id,
                             target=args.target, operation=args.operation,
                             source_name=args.source_name, expected_sha256=args.expected_sha256,
                             execute=args.execute), ensure_ascii=False))
        return 0
    except (CourierError, OSError, ValueError, UnicodeError) as exc:
        print("Config operator error: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
