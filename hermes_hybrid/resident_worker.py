"""A small, supervised Linux worker for Hermes Drive Vault jobs.

Does not start or keep Google Colab awake. Executes only explicitly routed
resident jobs (echo/text_stats/hash_file) using Hermes's existing Drive OAuth.
Uses flock for single-process local ownership. The supervisor (systemd or the
existing Muse Supervisor/Hook) is responsible for restarting the process.
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import logging
import os
from pathlib import Path
import signal
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone

from . import colab_jobs
from .colab_dispatch import RESIDENT_WORKER_FILE, validate_spec
from .drive_vault import DriveVault

LOG = logging.getLogger("hermes.colab.resident")
_BASE = colab_jobs._BASE
_LOCK = Path(tempfile.gettempdir()) / ("hermes-resident-worker-" + str(os.getuid()) + ".lock")
_STALE_SECONDS = 120


def now():
    return datetime.now(timezone.utc).isoformat()


def _stale(status, *, at=None):
    try:
        ts = datetime.fromisoformat(status["updated_at"].replace("Z", "+00:00"))
        if ts.tzinfo is None:
            return False
        age = ((at or datetime.now(timezone.utc)) - ts).total_seconds()
        return age >= _STALE_SECONDS
    except (KeyError, AttributeError, TypeError, ValueError):
        # Do not steal an unknown running job automatically.
        return False


def _process(vault: DriveVault, job_id: str) -> str | None:
    """Process one eligible job. Returns terminal state or None."""
    try:
        colab_jobs.job_dir(job_id)  # validate identifier
        job = colab_jobs.read_job(vault, job_id)
        if not isinstance(job, dict) or job.get("job_id") != job_id:
            return None
        spec = job.get("spec")
        if not isinstance(spec, dict) or spec.get("executor") != "resident":
            return None
        clean = validate_spec(spec)
        if clean != spec or job.get("schema") != 1:
            # No arbitrary or ambiguously configured job is run.
            return None
        status = colab_jobs.read_status(vault, job_id)
        if not isinstance(status, dict) or status.get("job_id") != job_id:
            return None
        state = status.get("state")
        if state in ("done", "failed"):
            return None
        previous_result = colab_jobs.read_result(vault, job_id)
        if previous_result is not None:
            # Crash after result write but before status update: finalize only.
            success = previous_result.get("ok") is True
            state = "done" if success else "failed"
            base = colab_jobs.job_dir(job_id)
            vault.write_json(base + "/checkpoint.json", {
                "job_id": job_id, "stage": state, "updated_at": now()
            })
            vault.write_json(base + "/status.json", {
                "job_id": job_id, "state": state, "updated_at": now()
            })
            return state
        if state != "queued" and not (state == "running" and _stale(status)):
            return None
        result = colab_jobs.run_job(vault, job_id)
        return "done" if result.get("ok") else "failed"
    except ValueError:
        return None


def scan_once(vault: DriveVault, max_jobs: int = 5) -> dict:
    scanned = 0
    done = 0
    failed = 0
    for name in vault.list_names(_BASE):
        if not name or name.startswith(".") or name in ("worker.json", "resident-worker.json"):
            continue
        if scanned >= max_jobs:
            break
        scanned += 1
        state = _process(vault, name)
        done += state == "done"
        failed += state == "failed"
    return {"scanned": scanned, "done": done, "failed": failed}


def heartbeat(vault: DriveVault, worker_id: str, state: str):
    vault.write_json(RESIDENT_WORKER_FILE, {
        "worker_id": worker_id, "state": state, "last_seen_at": now(),
    })


@contextlib.contextmanager
def single_instance():
    # This local lock is held for the entire process lifetime, not a Drive
    # cross-VM distributed lease. Deploy exactly one instance on one VM.
    with _LOCK.open("a+", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("resident-worker already active on this VM") from exc
        yield


def run(vault: DriveVault, *, interval: float = 30.0,
        max_jobs: int = 5, once: bool = False,
        stop=None) -> int:
    """Run Drive I/O loop; never schedule AI messages/tasks."""
    stopper = stop or (lambda: False)
    worker_id = "resident-" + uuid.uuid4().hex[:12]
    failures = 0
    heartbeat(vault, worker_id, "running")
    try:
        while not stopper():
            try:
                stats = scan_once(vault, max_jobs=max_jobs)
                heartbeat(vault, worker_id, "running")
                failures = 0
                if stats["done"] or stats["failed"]:
                    LOG.info("job cycle result: %s", json.dumps(stats))
            except Exception as exc:
                failures = min(failures + 1, 8)
                LOG.warning("Drive polling failed (%s); retry backoff", type(exc).__name__)
            if once:
                break
            delay = min(max(interval, 1.0) * (2 ** min(failures, 5)), 900.0)
            # Small sleep steps make systemd shutdown responsive.
            elapsed = 0.0
            while elapsed < delay and not stopper():
                step = min(1.0, delay - elapsed)
                time.sleep(step)
                elapsed += step
        return 0
    finally:
        try:
            heartbeat(vault, worker_id, "stopped")
        except Exception:
            LOG.warning("Unable to publish stop heartbeat (Drive unavailable)")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Hermes Drive resident worker")
    parser.add_argument("--once", action="store_true", help="One scan/heartbeat, then exit")
    parser.add_argument("--interval", type=float, default=30.0)
    args = parser.parse_args(argv)
    if not (5 <= args.interval <= 300):
        parser.error("--interval must be between 5 and 300 seconds")
    if not os.getenv("HERMES_DRIVE_VAULT_ROOT_ID"):
        parser.error("HERMES_DRIVE_VAULT_ROOT_ID must pin the canonical Drive folder")
    if not (os.getenv("GOOGLE_DRIVE_ACCESS_TOKEN") or
            os.getenv("GOOGLE_APPLICATION_CREDENTIALS")):
        parser.error("Google Drive credentials are required; no secrets in code")
    from tools.colab_job_tools import _vault
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    shutting_down = [False]

    def handle_signal(_sig, _frame):
        shutting_down[0] = True

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)
    try:
        with single_instance():
            return run(_vault(), interval=args.interval, once=args.once,
                       stop=lambda: shutting_down[0])
    except RuntimeError as exc:
        LOG.error("Not started: %s", exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())
