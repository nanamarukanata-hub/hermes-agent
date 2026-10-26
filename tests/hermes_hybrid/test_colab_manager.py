"""Colab Manager routing/availability tests (mock backends via Drive files)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from hermes_hybrid import colab_dispatch, colab_jobs
from hermes_hybrid.colab_manager import ColabManager, manager_state, probe_colab_enterprise
from hermes_hybrid.drive_vault import DriveVault, FakeDriveTransport

T0 = datetime(2026, 10, 26, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def vault():
    transport = FakeDriveTransport()
    transport.seed_root()
    return DriveVault(transport, sleeper=lambda _: None)


def _colab_heartbeat(vault, at: datetime, device=None):
    payload = {"worker_id": "colab-test", "state": "running",
               "last_seen_at": at.isoformat()}
    if device is not None:
        payload["device"] = device
    vault.write_json(colab_dispatch.WORKER_FILE, payload)


def _resident_heartbeat(vault, at: datetime):
    vault.write_json(colab_dispatch.RESIDENT_WORKER_FILE, {
        "worker_id": "resident-test", "state": "running",
        "last_seen_at": at.isoformat(), "restart_count": 1,
        "started_at": at.isoformat()})


GPU_DEVICE = {"cpu_count": 2, "gpu": {"available": True, "name": "Tesla T4", "cuda": True}}
CPU_DEVICE = {"cpu_count": 2, "gpu": {"available": False, "name": None, "cuda": False}}


def test_no_backend_online_job_still_queued(vault):
    manager = ColabManager(vault)
    out = manager.submit({"type": "echo", "message": "hi"}, "job-mgr-wait-1", at=T0)
    assert out["routing"]["decision"] == "wait"
    assert out["state"] == "queued"  # persisted, not lost
    assert colab_jobs.read_job(vault, "job-mgr-wait-1") is not None


def test_colab_online_routes_to_colab(vault):
    _colab_heartbeat(vault, T0, GPU_DEVICE)
    manager = ColabManager(vault)
    out = manager.submit({"type": "echo", "message": "hi"}, "job-mgr-colab-1", at=T0)
    assert out["routing"]["executor"] == "colab"


def test_gpu_job_falls_back_to_resident_cpu_when_colab_has_no_gpu(vault):
    _colab_heartbeat(vault, T0, CPU_DEVICE)
    _resident_heartbeat(vault, T0)
    manager = ColabManager(vault)
    out = manager.submit({"type": "echo", "message": "hi", "requires_gpu": True},
                         "job-mgr-fb-1", at=T0)
    assert out["routing"]["decision"] == "fallback"
    assert out["routing"]["reason"] == "gpu_unavailable_cpu_fallback"
    assert out["routing"]["executor"] == "resident"


def test_gpu_job_waits_when_fallback_disallowed(vault):
    _colab_heartbeat(vault, T0, CPU_DEVICE)
    _resident_heartbeat(vault, T0)
    manager = ColabManager(vault)
    decision = manager.route({"type": "echo", "message": "hi", "requires_gpu": True,
                              "allow_fallback": False}, at=T0)
    assert decision["decision"] == "wait"
    assert decision["executor"] == "colab"


def test_stale_colab_heartbeat_is_offline(vault):
    _colab_heartbeat(vault, T0 - timedelta(seconds=300), GPU_DEVICE)
    manager = ColabManager(vault)
    backends = manager.backends(at=T0)
    assert backends["colab"]["online"] is False


def test_enterprise_probe_never_claims_online(monkeypatch):
    monkeypatch.delenv("HERMES_COLAB_ENTERPRISE_PROJECT", raising=False)
    probe = probe_colab_enterprise()
    assert probe["online"] is False
    assert probe["reason"] == "not_configured"
    monkeypatch.setenv("HERMES_COLAB_ENTERPRISE_PROJECT", "demo-project")
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    probe = probe_colab_enterprise()
    assert probe["online"] is False
    assert probe["reason"] == "credentials_missing"


def test_device_info_job_roundtrip(vault):
    _resident_heartbeat(vault, T0)
    manager = ColabManager(vault)
    out = manager.submit({"type": "device_info", "executor": "resident"},
                         "job-mgr-dev-1", at=T0)
    assert out["state"] == "queued"
    result = colab_jobs.run_job(vault, "job-mgr-dev-1")
    assert result["ok"] is True
    assert result["result"]["type"] == "device_info"
    assert result["result"]["cpu_count"] >= 1


def test_tick_publishes_state_and_recovers_interrupted_job(vault):
    _resident_heartbeat(vault, T0)
    colab_dispatch.submit_job(vault, {"type": "echo", "message": "x"}, "job-mgr-tick-1")
    vault.write_json(f"{colab_jobs.job_dir('job-mgr-tick-1')}/status.json", {
        "job_id": "job-mgr-tick-1", "state": "running",
        "updated_at": (T0 - timedelta(seconds=600)).isoformat()})
    manager = ColabManager(vault)
    state = manager.tick(at=T0)
    assert state["queue"].get("retrying") == 1
    assert [a["action"] for a in state["recovery_actions"]] == ["marked_retrying"]
    saved = manager_state(vault)
    assert saved["backends"]["resident"]["online"] is True
    assert saved["monitor_interval_seconds"] == 60
