"""Task and session store for the Muse <-> ChatGPT bridge.

Files only, no database.  Layout under a state directory
(default ``<repo>/muse_bridge_state`` when the server is run from a
checkout, overridable with ``MUSE_BRIDGE_STATE``):

- ``tasks/<task_id>.json`` — one work order per instruction.
- ``sessions/<session_id>.json`` — session record: ordered task ids,
  repo, last activity, and a free-form ``context`` note that Muse
  updates so the next turn can continue from the previous one.

A task lifecycle: ``queued`` -> ``awaiting_muse`` (needs Muse
execution) or ``done`` / ``failed`` (bounded auto actions) or
``needs_approval`` (policy gate) -> ``approved`` -> back to
``awaiting_muse``.  Nothing is ever deleted by this module; a
completed task keeps its result, logs, diff and test output so
ChatGPT can re-read it later in the same session.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

STATUSES = ("queued", "awaiting_muse", "needs_approval", "approved", "done", "failed", "rejected")


def _now() -> float:
    return time.time()


def _atomic_write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _read_json(path: Path) -> dict | None:
    if not path.exists() or path.is_symlink():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


class TaskStore:
    def __init__(self, state_dir: str | Path):
        self.root = Path(state_dir).expanduser()
        self.tasks_dir = self.root / "tasks"
        self.sessions_dir = self.root / "sessions"
        self.tasks_dir.mkdir(parents=True, exist_ok=True)
        self.sessions_dir.mkdir(parents=True, exist_ok=True)

    # -- tasks ----------------------------------------------------------
    def create_task(self, repo: str, instruction: str, session_id: str | None = None) -> dict:
        task_id = uuid.uuid4().hex[:16]
        session_id = session_id or uuid.uuid4().hex[:16]
        task = {
            "task_id": task_id,
            "session_id": session_id,
            "repo": repo,
            "instruction": instruction,
            "status": "queued",
            "created_at": _now(),
            "updated_at": _now(),
            "result": None,
            "logs": [],
            "diff": None,
            "test_results": None,
            "approval": None,
        }
        _atomic_write(self.tasks_dir / f"{task_id}.json", task)
        session = self.get_session(session_id) or {
            "session_id": session_id, "repo": repo, "task_ids": [],
            "created_at": _now(), "updated_at": _now(), "context": "",
        }
        if task_id not in session["task_ids"]:
            session["task_ids"].append(task_id)
        session["updated_at"] = _now()
        _atomic_write(self.sessions_dir / f"{session_id}.json", session)
        return task

    def get_task(self, task_id: str) -> dict | None:
        return _read_json(self.tasks_dir / f"{task_id}.json")

    def update_task(self, task_id: str, **fields: Any) -> dict | None:
        task = self.get_task(task_id)
        if task is None:
            return None
        task.update(fields)
        task["updated_at"] = _now()
        _atomic_write(self.tasks_dir / f"{task_id}.json", task)
        return task

    def add_log(self, task_id: str, line: str) -> dict | None:
        task = self.get_task(task_id)
        if task is None:
            return None
        task.setdefault("logs", []).append({"at": _now(), "line": line})
        task["updated_at"] = _now()
        _atomic_write(self.tasks_dir / f"{task_id}.json", task)
        return task

    def list_tasks(self, session_id: str | None = None) -> list[dict]:
        tasks = []
        for path in sorted(self.tasks_dir.glob("*.json")):
            task = _read_json(path)
            if task is None:
                continue
            if session_id is not None and task.get("session_id") != session_id:
                continue
            tasks.append(task)
        tasks.sort(key=lambda t: t.get("created_at", 0))
        return tasks

    # -- sessions ---------------------------------------------------------
    def get_session(self, session_id: str) -> dict | None:
        return _read_json(self.sessions_dir / f"{session_id}.json")

    def list_sessions(self) -> list[dict]:
        sessions = []
        for path in sorted(self.sessions_dir.glob("*.json")):
            session = _read_json(path)
            if session is not None:
                sessions.append(session)
        sessions.sort(key=lambda s: s.get("updated_at", 0), reverse=True)
        return sessions

    def update_session_context(self, session_id: str, context: str) -> dict | None:
        session = self.get_session(session_id)
        if session is None:
            return None
        session["context"] = context
        session["updated_at"] = _now()
        _atomic_write(self.sessions_dir / f"{session_id}.json", session)
        return session
