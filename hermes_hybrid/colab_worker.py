"""Self-contained Google Colab worker notebook for Hermes's Drive-backed queue.

Generated notebook contains no credentials, GitHub token, package installation,
or arbitrary Python execution from jobs. The owner mounts Drive via the
Colab-provided consent screen, then launches a bounded polling cell.
"""
from __future__ import annotations

import json

from hermes_hybrid.colab_jobs import _AUTH_CELL
from hermes_hybrid.drive_vault import VAULT_ROOT_NAME

_WORKER_CELL = '''\
# Step 2: Hermes Colab queue worker (user-started, bounded session).
# Only Drive I/O here, NO AI polling tasks. Stop this cell to stop the worker.
# The notebook will not wake up an idle/free Colab runtime from outside.
import hashlib
import json
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

VAULT_NAME = "__VAULT_NAME__"
WORKER_MINUTES = 30
POLL_SECONDS = 15
MAX_JOBS = 50
VAULT = Path("/content/drive/MyDrive") / VAULT_NAME
QUEUE = VAULT / "06_Automation" / "Hermes" / "ColabJobs"
INPUT_PREFIX = "06_Automation/Hermes/"
WORKER_ID = "colab-" + uuid.uuid4().hex[:12]
JOB_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
SEGMENT = re.compile(r"^[A-Za-z0-9_. -]+$")

def timestamp():
    return datetime.now(timezone.utc).isoformat()

def persist(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\\n", encoding="utf-8")

def health(state):
    persist(QUEUE / "worker.json", {
        "worker_id": WORKER_ID, "state": state, "last_seen_at": timestamp()
    })

def valid_input(path):
    if not isinstance(path, str) or len(path) > 500 or not path.startswith(INPUT_PREFIX):
        raise ValueError("not a Hermes Vault input")
    parts = path.split("/")
    if any(s in ("", ".", "..") or not SEGMENT.fullmatch(s) for s in parts):
        raise ValueError("invalid path")
    if "\\\\" in path:
        raise ValueError("invalid path")
    result = VAULT.joinpath(*parts).resolve()
    if not result.is_relative_to((VAULT / "06_Automation" / "Hermes").resolve()):
        raise ValueError("input escapes allowed Hermes subtree")
    return result

def execute(spec):
    if not isinstance(spec, dict):
        raise ValueError("invalid job definition")
    kind = spec.get("type")
    if kind == "echo":
        message = spec.get("message", "")
        if not isinstance(message, str) or len(message) > 2000:
            raise ValueError("invalid echo message")
        return {"type": "echo", "message": message}
    if kind in ("text_stats", "hash_file"):
        name = spec.get("input")
        blob = valid_input(name).read_bytes()
        if len(blob) > 50 * 1024 * 1024:
            raise ValueError("file exceeds Colab worker maximum")
        result = {"type": kind, "input": name, "size": len(blob),
                  "sha256": hashlib.sha256(blob).hexdigest()}
        if kind == "text_stats":
            text = blob.decode("utf-8")
            result.update({
                "lines": text.count("\\n") + (1 if text and not text.endswith("\\n") else 0),
                "words": len(text.split()), "chars": len(text)
            })
        return result
    raise ValueError("unsupported job type")

def process_one(folder):
    job_id = folder.name
    if not JOB_ID_PATTERN.fullmatch(job_id):
        return False
    status_file = folder / "status.json"
    job_file = folder / "job.json"
    if not job_file.is_file() or not status_file.is_file():
        return False
    try:
        status = json.loads(status_file.read_text(encoding="utf-8"))
        if status.get("state") != "queued" or status.get("job_id") != job_id:
            return False
        job = json.loads(job_file.read_text(encoding="utf-8"))
        if job.get("job_id") != job_id or job.get("schema") != 1:
            raise ValueError("job definition mismatch")
        # Resident jobs are owned by the supervised Linux worker, never Colab.
        if (job.get("spec") or {}).get("executor", "colab") != "colab":
            return False
    except (OSError, json.JSONDecodeError, KeyError, ValueError):
        # Incomplete/concurrent Drive sync: leave queued job for next cycle.
        return False
    persist(status_file, {"job_id": job_id, "state": "running", "updated_at": timestamp()})
    persist(folder / "checkpoint.json",
            {"job_id": job_id, "stage": "executing", "updated_at": timestamp()})
    try:
        payload = execute(job["spec"])
        result = {"job_id": job_id, "ok": True,
                  "result": payload, "finished_at": timestamp()}
        state = "done"
    except Exception as exc:
        # Exception type only; no secret-bearing stack traces in Drive.
        result = {"job_id": job_id, "ok": False,
                  "error": type(exc).__name__, "finished_at": timestamp()}
        state = "failed"
    persist(folder / "result.json", result)
    persist(folder / "checkpoint.json",
            {"job_id": job_id, "stage": state, "updated_at": timestamp()})
    persist(status_file, {"job_id": job_id, "state": state, "updated_at": timestamp()})
    print(json.dumps({"job_id": job_id, "state": state, "ok": result["ok"]},
                     ensure_ascii=False), flush=True)
    return True

QUEUE.mkdir(parents=True, exist_ok=True)
completed = 0
deadline = time.monotonic() + min(max(WORKER_MINUTES, 1), 120) * 60
health("running")
try:
    while time.monotonic() < deadline and completed < MAX_JOBS:
        for entry in sorted(QUEUE.iterdir()):
            if entry.is_dir() and process_one(entry):
                completed += 1
                if completed >= MAX_JOBS:
                    break
        health("running")
        if completed < MAX_JOBS:
            time.sleep(max(POLL_SECONDS, 5))
except KeyboardInterrupt:
    print("Stopped by user. Queued jobs remain in Drive.")
finally:
    health("stopped")
print("Worker stopped. Completed jobs:", completed)
'''


def build_worker_notebook(vault_name: str = VAULT_ROOT_NAME) -> dict:
    """Generate runnable Colab notebook; login credentials never embedded."""
    if not vault_name or "/" in vault_name or "\\" in vault_name or vault_name in (".", ".."):
        raise ValueError("invalid Vault folder name")
    source = _WORKER_CELL.replace("__VAULT_NAME__", vault_name)
    return {
        "nbformat": 4,
        "nbformat_minor": 0,
        "metadata": {
            "colab": {"name": "hermes-colab-queue-worker.ipynb", "provenance": []},
            "kernelspec": {"name": "python3", "display_name": "Python 3"},
            "language_info": {"name": "python"},
        },
        "cells": [
            {"cell_type": "markdown", "metadata": {}, "source": [
                "# Hermes → Colab queue worker\n",
                "Run cell 1 to approve Google's Drive access. Then run cell 2.\n",
                "Hermes writes job JSON to the canonical Drive Vault; this "
                "worker detects queued jobs, executes allowlisted actions and "
                "writes results. Leave the cell running to process new jobs.\n",
                "**This is not an always-on service**: free Colab can disconnect "
                "or terminate. Do not share secrets in chat or this notebook."
            ]},
            {"cell_type": "code", "metadata": {}, "execution_count": None,
             "outputs": [], "source": _AUTH_CELL.splitlines(keepends=True)},
            {"cell_type": "code", "metadata": {}, "execution_count": None,
             "outputs": [], "source": source.splitlines(keepends=True)},
        ],
    }


def worker_notebook_bytes(vault_name: str = VAULT_ROOT_NAME) -> bytes:
    return (json.dumps(build_worker_notebook(vault_name), ensure_ascii=False, indent=2)
            + "\n").encode("utf-8")
