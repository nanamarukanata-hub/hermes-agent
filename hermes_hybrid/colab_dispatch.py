"""Drive-backed, idempotent Hermes -> Colab job dispatch.

This module enqueues *data*, not executable code. Colab's notebook worker
executes only the built-in echo/text_stats/hash_file operations when a user
starts that worker. A queued job does NOT launch an idle Colab runtime.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from hermes_hybrid import colab_jobs
from hermes_hybrid.drive_vault import DriveVault
from hermes_hybrid.vault_kv import validate_id

INPUT_PREFIX = "06_Automation/Hermes/"
WORKER_FILE = colab_jobs._BASE + "/worker.json"
RESIDENT_WORKER_FILE = colab_jobs._BASE + "/resident-worker.json"
_HEARTBEAT_STALE_SECONDS = 120
_SEGMENT = re.compile(r"^[A-Za-z0-9_. -]+$")


def safe_input_path(value: str) -> str:
    """Restrict worker file access to the canonical Hermes Vault subtree."""
    if not isinstance(value, str) or len(value) > 500 or not value.startswith(INPUT_PREFIX):
        raise ValueError("input must be a path inside 06_Automation/Hermes/")
    parts = value.split("/")
    if not parts or any(part in ("", ".", "..") or not _SEGMENT.fullmatch(part) for part in parts):
        raise ValueError("invalid Vault-relative input path")
    if "\\" in value or value.startswith("/"):
        raise ValueError("invalid Vault-relative input path")
    return value


def validate_spec(spec: dict) -> dict:
    if not isinstance(spec, dict):
        raise ValueError("job spec must be an object")
    jtype = spec.get("type")
    executor = spec.get("executor", "colab")
    if executor not in ("colab", "resident"):
        raise ValueError("executor must be colab or resident")
    if jtype == "echo":
        msg = spec.get("message", "")
        if not isinstance(msg, str) or len(msg) > 2000:
            raise ValueError("echo message must be a string of at most 2000 chars")
        clean = {"type": "echo", "message": msg}
        if executor == "resident":
            clean["executor"] = executor
        return clean
    if jtype in ("text_stats", "hash_file"):
        clean = {"type": jtype, "input": safe_input_path(spec.get("input", ""))}
        if executor == "resident":
            clean["executor"] = executor
        return clean
    raise ValueError("supported Colab jobs: echo, text_stats, hash_file")


def submit_job(vault: DriveVault, spec: dict, job_id: Optional[str] = None) -> dict:
    """Create one queued job; exact replays return the same job without overwriting it.

    Important: concurrent Hermes writers should use distinct UUIDs. A multi-file
    Drive queue is not a transactional database; use one active Colab worker.
    """
    clean = validate_spec(spec)
    name = validate_id(job_id or ("job-" + uuid.uuid4().hex[:20]))
    existing = colab_jobs.read_job(vault, name)
    if existing is not None:
        if existing.get("spec") != clean:
            raise ValueError("job_id exists with a different specification")
        return {"job_id": name, "state": "already_exists", "job": existing}
    colab_jobs.write_job(vault, name, clean)
    return {"job_id": name, "state": "queued", "job": clean,
            "executor": clean.get("executor", "colab"),
            "worker": resident_health(vault) if clean.get("executor") == "resident"
                else worker_health(vault)}


def worker_health(vault: DriveVault, *, current_time: Optional[datetime] = None) -> dict:
    """Liveness of the user-started Colab notebook worker."""
    return _health(vault, WORKER_FILE, current_time=current_time)


def resident_health(vault: DriveVault, *, current_time: Optional[datetime] = None) -> dict:
    """Liveness of the separately supervised Linux resident worker."""
    return _health(vault, RESIDENT_WORKER_FILE, current_time=current_time)


def _health(vault: DriveVault, file_path: str, *, current_time: Optional[datetime]) -> dict:
    data = vault.read_json(file_path)
    if not isinstance(data, dict):
        return {"online": False, "reason": "no_worker_heartbeat"}
    stamp = data.get("last_seen_at")
    try:
        last_seen = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        if last_seen.tzinfo is None:
            raise ValueError("timezone required")
    except (AttributeError, ValueError, TypeError):
        return {"online": False, "reason": "invalid_worker_heartbeat"}
    now = current_time or datetime.now(timezone.utc)
    age = (now - last_seen).total_seconds()
    online = (-30 <= age <= _HEARTBEAT_STALE_SECONDS and data.get("state") == "running")
    info = {"online": bool(online),
            "reason": "active" if online else "worker_offline_or_stale",
            "last_seen_at": stamp}
    restart_count = data.get("restart_count")
    if type(restart_count) is int and restart_count >= 0:
        info["restart_count"] = restart_count
    started = data.get("started_at")
    if isinstance(started, str):
        try:
            start_dt = datetime.fromisoformat(started.replace("Z", "+00:00"))
            seconds = int((now - start_dt).total_seconds())
            if online and 0 <= seconds < 365 * 86400:
                info["current_process_uptime_seconds"] = seconds
                info["started_at"] = started
        except (ValueError, TypeError):
            pass
    return info


def job_report(vault: DriveVault, job_id: str) -> dict:
    name = validate_id(job_id)
    job = colab_jobs.read_job(vault, name)
    if job is None:
        return {"found": False, "job_id": name}
    status = colab_jobs.read_status(vault, name) or {"state": "unknown"}
    result = colab_jobs.read_result(vault, name)
    state = status.get("state", "unknown")
    if state in ("done", "failed") and result is None:
        state = "inconsistent_missing_result"
    executor = (job.get("spec") or {}).get("executor", "colab")
    return {"found": True, "job_id": name, "state": state,
            "executor": executor,
            "result": result, "worker": resident_health(vault) if executor == "resident"
                else worker_health(vault)}
