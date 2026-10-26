"""Resident worker heartbeat resilience tests.

The heartbeat lives on its own thread so a slow or failing job scan can
no longer starve liveness updates, and each write retries transient
Drive failures. All tests use the in-memory FakeDriveTransport.
"""
from __future__ import annotations

import time

import pytest

from hermes_hybrid import resident_worker
from hermes_hybrid.colab_dispatch import RESIDENT_WORKER_FILE
from hermes_hybrid.drive_vault import DriveUnavailable, DriveVault, FakeDriveTransport


@pytest.fixture
def vault():
    transport = FakeDriveTransport()
    transport.seed_root()
    return DriveVault(transport, sleeper=lambda _: None), transport


def test_heartbeat_with_retry_recovers_from_transient_failure(vault):
    v, transport = vault
    transport.fail_next(1, DriveUnavailable)
    assert resident_worker.heartbeat_with_retry(v, "resident-t", "running") is True
    data = v.read_json(RESIDENT_WORKER_FILE)
    assert data["worker_id"] == "resident-t"
    assert data["state"] == "running"


def test_heartbeat_with_retry_reports_persistent_failure(vault):
    v, transport = vault
    transport.fail_next(10, DriveUnavailable)
    assert resident_worker.heartbeat_with_retry(
        v, "resident-t", "running", attempts=2) is False


def test_heartbeat_continues_while_scan_is_failing(vault, monkeypatch):
    v, _transport = vault

    def failing_scan(vault_, max_jobs=5):
        raise DriveUnavailable("simulated outage")

    monkeypatch.setattr(resident_worker, "scan_once", failing_scan)
    stamps = []
    real_hb = resident_worker.heartbeat

    def recording_hb(vault_, worker_id, state, **kw):
        stamps.append(time.monotonic())
        return real_hb(vault_, worker_id, state, **kw)

    monkeypatch.setattr(resident_worker, "heartbeat", recording_hb)
    deadline = time.monotonic() + 1.4
    rc = resident_worker.run(v, interval=0.2, heartbeat_interval=0.3,
                             stop=lambda: time.monotonic() > deadline)
    assert rc == 0
    # Initial write + at least two thread writes + final stopped write,
    # all while every scan raised.
    assert len(stamps) >= 4
    data = v.read_json(RESIDENT_WORKER_FILE)
    assert data["state"] == "stopped"


def test_heartbeat_thread_publishes_without_scan(vault):
    v, _transport = vault
    cycles = {"cycles": 7}
    beat = resident_worker._HeartbeatThread(
        v, "resident-th", started_at=resident_worker.now(),
        restart_count=1, cycle_counter=cycles, interval=0.2)
    beat.start()
    try:
        time.sleep(0.55)
    finally:
        beat.request_stop()
        beat.join(timeout=5)
    data = v.read_json(RESIDENT_WORKER_FILE)
    assert data["worker_id"] == "resident-th"
    assert data["state"] == "running"
    assert data["cycle_count"] == 7
