"""Persistent-queue recovery reconciler for Hermes Colab/resident jobs.

The Drive queue (``06_Automation/Hermes/ColabJobs/<job_id>/``) is the
persistent store. Workers are disposable: a Colab runtime can vanish and
the Linux resident worker can be killed or the VM regenerated. This
module classifies every queued job into an explicit recovery state and
repairs the ones that were interrupted, without ever re-running a job
that already produced a result (side-effect prevention) and without
stealing a job from a worker that is demonstrably alive.

States: pending, running, checkpointed, interrupted, retrying,
completed, failed.

Retry policy: an interrupted job is first marked ``retrying`` with an
exponential-backoff ``next_retry_at``; a later reconcile pass requeues
it (status ``queued``, attempts + 1) once the backoff has elapsed. Jobs
that exhaust ``max_attempts`` are quarantined: status ``failed`` plus a
``quarantine.json`` record, and are never requeued automatically.

Every action is appended to ``recovery-events.jsonl`` in the queue root
so the recovery history survives on Drive. Run at most one reconciler
at a time (the Colab Manager tick is the intended caller); transitions
are written to converge even if two passes overlap.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Optional

from hermes_hybrid import colab_jobs
from hermes_hybrid.drive_vault import DriveVault
from hermes_hybrid.vault_kv import validate_id

EVENTS_FILE = colab_jobs._BASE + "/recovery-events.jsonl"
TERMINAL = ("completed", "failed")
DEFAULT_STALE_SECONDS = 180
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BASE_BACKOFF = 30
DEFAULT_MAX_BACKOFF = 900


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_stamp(value) -> Optional[datetime]:
    if not isinstance(value, str):
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        return None
    return stamp


def _age_seconds(status: Optional[dict], at: datetime) -> Optional[float]:
    if not isinstance(status, dict):
        return None
    stamp = _parse_stamp(status.get("updated_at"))
    if stamp is None:
        return None
    return (at - stamp).total_seconds()


def classify_job(vault: DriveVault, job_id: str, *, at: Optional[datetime] = None,
                 stale_seconds: int = DEFAULT_STALE_SECONDS) -> Optional[dict]:
    """Classify one job into the recovery state machine. None if unknown job."""
    name = validate_id(job_id)
    job = colab_jobs.read_job(vault, name)
    if job is None:
        return None
    at = at or _now()
    status = colab_jobs.read_status(vault, name)
    result = colab_jobs.read_result(vault, name)
    info = {"job_id": name,
            "executor": (job.get("spec") or {}).get("executor", "colab"),
            "attempts": 0}
    if isinstance(status, dict) and type(status.get("attempts")) is int:
        info["attempts"] = status["attempts"]
    if result is not None:
        # A produced result is terminal evidence: never rerun this job.
        info["state"] = "completed" if result.get("ok") else "failed"
        info["reason"] = "result_present"
        return info
    quarantine = vault.read_json(f"{colab_jobs.job_dir(name)}/quarantine.json")
    if isinstance(quarantine, dict):
        # Quarantined jobs are terminally failed until a human reviews them.
        info["state"] = "failed"
        info["reason"] = "quarantined"
        return info
    state = (status or {}).get("state")
    if state in ("done", "failed"):
        # Terminal status without its result file: inconsistent, recoverable.
        info["state"] = "interrupted"
        info["reason"] = "terminal_status_without_result"
        return info
    if state == "running":
        age = _age_seconds(status, at)
        if age is not None and age <= stale_seconds:
            info["state"] = "running"
            info["reason"] = "worker_active"
        else:
            info["state"] = "interrupted"
            info["reason"] = "running_status_stale"
        return info
    if state == "queued":
        info["state"] = "pending"
        info["reason"] = "awaiting_worker"
        return info
    if state == "retrying":
        info["state"] = "retrying"
        info["reason"] = "backoff_pending"
        info["next_retry_at"] = (status or {}).get("next_retry_at")
        return info
    if state == "interrupted":
        info["state"] = "interrupted"
        info["reason"] = "marked_interrupted"
        return info
    checkpoint = vault.read_json(f"{colab_jobs.job_dir(name)}/checkpoint.json")
    if isinstance(checkpoint, dict) and checkpoint.get("stage") not in (None, "done", "failed"):
        info["state"] = "checkpointed"
        info["reason"] = "checkpoint_without_status"
        return info
    info["state"] = "pending"
    info["reason"] = "no_status_yet"
    return info


def list_job_ids(vault: DriveVault) -> list[str]:
    """Job directory names in the queue root (files such as *.json excluded)."""
    names = []
    for name in vault.list_names(colab_jobs._BASE):
        if name.endswith((".json", ".jsonl", ".ipynb", ".md")):
            continue
        try:
            names.append(validate_id(name))
        except Exception:
            continue
    return sorted(names)


def classify_all(vault: DriveVault, *, at: Optional[datetime] = None,
                 stale_seconds: int = DEFAULT_STALE_SECONDS) -> dict:
    jobs = {}
    for job_id in list_job_ids(vault):
        info = classify_job(vault, job_id, at=at, stale_seconds=stale_seconds)
        if info is not None:
            jobs[job_id] = info
    counts: dict[str, int] = {}
    for info in jobs.values():
        counts[info["state"]] = counts.get(info["state"], 0) + 1
    return {"jobs": jobs, "counts": counts}


def _backoff_seconds(attempts: int, base: int, cap: int) -> int:
    return int(min(cap, base * (2 ** max(0, attempts))))


def _append_events(vault: DriveVault, events: list[dict]) -> None:
    if not events:
        return
    existing = vault.read_text(EVENTS_FILE) or ""
    lines = "".join(json.dumps(event, ensure_ascii=False) + "\n" for event in events)
    vault.write_text(EVENTS_FILE, existing + lines)


def reconcile(vault: DriveVault, *, at: Optional[datetime] = None,
              max_attempts: int = DEFAULT_MAX_ATTEMPTS,
              stale_seconds: int = DEFAULT_STALE_SECONDS,
              base_backoff: int = DEFAULT_BASE_BACKOFF,
              max_backoff: int = DEFAULT_MAX_BACKOFF,
              apply: bool = True) -> dict:
    """Repair interrupted jobs according to the retry policy.

    interrupted/checkpointed -> retrying (backoff scheduled), or failed +
    quarantine when attempts are exhausted. retrying -> queued once the
    backoff elapsed (attempts + 1), after re-checking that no result has
    appeared in the meantime. Live running jobs and terminal jobs are
    never touched.
    """
    at = at or _now()
    snapshot = classify_all(vault, at=at, stale_seconds=stale_seconds)
    actions: list[dict] = []
    events: list[dict] = []
    stamp = at.isoformat()

    def record(job_id: str, action: str, from_state: str, **extra) -> None:
        entry = {"job_id": job_id, "action": action, "from_state": from_state,
                 "at": stamp, **extra}
        actions.append(entry)
        events.append({"kind": "recovery_action", **entry})

    for job_id, info in sorted(snapshot["jobs"].items()):
        state = info["state"]
        attempts = info["attempts"]
        status_rel = f"{colab_jobs.job_dir(job_id)}/status.json"
        if state in ("interrupted", "checkpointed"):
            if attempts >= max_attempts:
                if apply:
                    vault.write_json(status_rel, {
                        "job_id": job_id, "state": "failed", "attempts": attempts,
                        "updated_at": stamp, "error": "attempts_exhausted"})
                    vault.write_json(f"{colab_jobs.job_dir(job_id)}/quarantine.json", {
                        "job_id": job_id, "quarantined_at": stamp,
                        "reason": "attempts_exhausted", "attempts": attempts,
                        "recovery": "manual_review_required"})
                record(job_id, "quarantined", state, attempts=attempts)
            else:
                delay = _backoff_seconds(attempts, base_backoff, max_backoff)
                next_at = (at + timedelta(seconds=delay)).isoformat()
                if apply:
                    vault.write_json(status_rel, {
                        "job_id": job_id, "state": "retrying", "attempts": attempts,
                        "updated_at": stamp, "next_retry_at": next_at,
                        "recovered_from": info["reason"]})
                record(job_id, "marked_retrying", state,
                       attempts=attempts, next_retry_at=next_at)
        elif state == "retrying":
            status = colab_jobs.read_status(vault, job_id) or {}
            due = _parse_stamp(status.get("next_retry_at"))
            if due is not None and due <= at:
                # Re-check for a result right before requeueing: a worker
                # may have finished the job after classification.
                if colab_jobs.read_result(vault, job_id) is not None:
                    record(job_id, "kept_terminal", state,
                           reason="result_appeared_before_requeue")
                    continue
                if attempts >= max_attempts:
                    if apply:
                        vault.write_json(status_rel, {
                            "job_id": job_id, "state": "failed",
                            "attempts": attempts, "updated_at": stamp,
                            "error": "attempts_exhausted"})
                        vault.write_json(
                            f"{colab_jobs.job_dir(job_id)}/quarantine.json", {
                                "job_id": job_id, "quarantined_at": stamp,
                                "reason": "attempts_exhausted",
                                "attempts": attempts,
                                "recovery": "manual_review_required"})
                    record(job_id, "quarantined", state, attempts=attempts)
                else:
                    if apply:
                        vault.write_json(status_rel, {
                            "job_id": job_id, "state": "queued",
                            "attempts": attempts + 1, "updated_at": stamp,
                            "recovered_from": "retry_backoff_elapsed"})
                    record(job_id, "requeued", state, attempts=attempts + 1)
    if apply:
        events.append({"kind": "reconcile_run", "at": stamp,
                       "counts": snapshot["counts"], "actions": len(actions)})
        _append_events(vault, events)
    return {"at": stamp, "counts": snapshot["counts"],
            "jobs": snapshot["jobs"], "actions": actions, "applied": apply}
