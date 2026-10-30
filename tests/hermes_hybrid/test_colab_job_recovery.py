"""Recovery state machine tests for the persistent Colab/resident queue."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from hermes_hybrid import colab_dispatch, colab_jobs
from hermes_hybrid.colab_job_recovery import classify_job, reconcile
from hermes_hybrid.drive_vault import DriveVault, FakeDriveTransport

T0 = datetime(2026, 10, 26, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def vault():
    transport = FakeDriveTransport()
    transport.seed_root()
    return DriveVault(transport, sleeper=lambda _: None)


def _status(vault, job_id, payload):
    vault.write_json(f"{colab_jobs.job_dir(job_id)}/status.json", payload)


def _stamp(at: datetime) -> str:
    return at.isoformat()


def test_pending_and_completed_classification(vault):
    colab_dispatch.submit_job(vault, {"type": "echo", "message": "x"}, "job-pend-1")
    info = classify_job(vault, "job-pend-1", at=T0)
    assert info["state"] == "pending"
    colab_jobs.run_job(vault, "job-pend-1")
    info = classify_job(vault, "job-pend-1", at=T0)
    assert info["state"] == "completed"


def test_result_is_terminal_and_never_requeued(vault):
    colab_dispatch.submit_job(vault, {"type": "echo", "message": "x"}, "job-done-1")
    colab_jobs.run_job(vault, "job-done-1")
    report = reconcile(vault, at=T0 + timedelta(hours=5))
    assert report["actions"] == []
    assert classify_job(vault, "job-done-1", at=T0)["state"] == "completed"


def test_fresh_running_is_untouched_stale_running_is_interrupted(vault):
    colab_dispatch.submit_job(vault, {"type": "echo", "message": "x"}, "job-run-1")
    _status(vault, "job-run-1", {"job_id": "job-run-1", "state": "running",
                                 "updated_at": _stamp(T0)})
    assert classify_job(vault, "job-run-1", at=T0 + timedelta(seconds=60))["state"] == "running"
    stale_at = T0 + timedelta(seconds=181)
    assert classify_job(vault, "job-run-1", at=stale_at)["state"] == "interrupted"
    report = reconcile(vault, at=stale_at)
    assert [a["action"] for a in report["actions"]] == ["marked_retrying"]
    status = colab_jobs.read_status(vault, "job-run-1")
    assert status["state"] == "retrying"
    assert status["next_retry_at"] == _stamp(stale_at + timedelta(seconds=30))


def test_backoff_then_requeue_increments_attempts(vault):
    colab_dispatch.submit_job(vault, {"type": "echo", "message": "x"}, "job-retry-1")
    _status(vault, "job-retry-1", {"job_id": "job-retry-1", "state": "running",
                                   "attempts": 0, "updated_at": _stamp(T0)})
    t1 = T0 + timedelta(seconds=200)
    reconcile(vault, at=t1)
    # Backoff not yet elapsed: no requeue.
    report = reconcile(vault, at=t1 + timedelta(seconds=10))
    assert report["actions"] == []
    # Elapsed: requeue with attempts + 1.
    report = reconcile(vault, at=t1 + timedelta(seconds=31))
    assert [a["action"] for a in report["actions"]] == ["requeued"]
    status = colab_jobs.read_status(vault, "job-retry-1")
    assert status["state"] == "queued"
    assert status["attempts"] == 1


def test_attempts_exhausted_quarantines_job(vault):
    colab_dispatch.submit_job(vault, {"type": "echo", "message": "x"}, "job-dead-1")
    _status(vault, "job-dead-1", {"job_id": "job-dead-1", "state": "running",
                                  "attempts": 3, "updated_at": _stamp(T0)})
    report = reconcile(vault, at=T0 + timedelta(seconds=400), max_attempts=3)
    assert [a["action"] for a in report["actions"]] == ["quarantined"]
    assert colab_jobs.read_status(vault, "job-dead-1")["state"] == "failed"
    quarantine = vault.read_json(f"{colab_jobs.job_dir('job-dead-1')}/quarantine.json")
    assert quarantine["reason"] == "attempts_exhausted"
    # Quarantined jobs stay failed and are never requeued by later passes.
    report = reconcile(vault, at=T0 + timedelta(hours=3), max_attempts=3)
    assert report["actions"] == []


def test_terminal_status_without_result_recovers(vault):
    colab_dispatch.submit_job(vault, {"type": "echo", "message": "x"}, "job-inc-1")
    _status(vault, "job-inc-1", {"job_id": "job-inc-1", "state": "done",
                                 "updated_at": _stamp(T0)})
    assert classify_job(vault, "job-inc-1", at=T0)["state"] == "interrupted"
    report = reconcile(vault, at=T0)
    assert [a["action"] for a in report["actions"]] == ["marked_retrying"]


def test_recovery_events_are_recorded(vault):
    colab_dispatch.submit_job(vault, {"type": "echo", "message": "x"}, "job-ev-1")
    _status(vault, "job-ev-1", {"job_id": "job-ev-1", "state": "running",
                                "updated_at": _stamp(T0)})
    reconcile(vault, at=T0 + timedelta(seconds=300))
    log = vault.read_text("06_Automation/Hermes/ColabJobs/recovery-events.jsonl")
    assert "marked_retrying" in log
    assert "reconcile_run" in log


def test_dry_run_changes_nothing(vault):
    colab_dispatch.submit_job(vault, {"type": "echo", "message": "x"}, "job-dry-1")
    _status(vault, "job-dry-1", {"job_id": "job-dry-1", "state": "running",
                                 "updated_at": _stamp(T0)})
    report = reconcile(vault, at=T0 + timedelta(seconds=300), apply=False)
    assert [a["action"] for a in report["actions"]] == ["marked_retrying"]
    assert colab_jobs.read_status(vault, "job-dry-1")["state"] == "running"
