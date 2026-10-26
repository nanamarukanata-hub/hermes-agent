"""Hermes -> Colab Drive queue integration and notebook-worker tests."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from hermes_hybrid import colab_jobs
from hermes_hybrid.colab_dispatch import (
    WORKER_FILE, job_report, safe_input_path, submit_job, validate_spec, worker_health
)
from hermes_hybrid.colab_worker import build_worker_notebook, worker_notebook_bytes
from hermes_hybrid.drive_vault import DriveVault, FakeDriveTransport


@pytest.fixture
def vault():
    transport = FakeDriveTransport()
    transport.seed_root()
    return DriveVault(transport, sleeper=lambda _: None)


def test_submit_queues_job_without_running_it(vault):
    response = submit_job(vault, {"type": "echo", "message": "hello"}, "colab-probe-1")
    assert response["state"] == "queued"
    assert response["worker"]["online"] is False
    assert colab_jobs.read_job(vault, "colab-probe-1")["spec"]["message"] == "hello"
    assert job_report(vault, "colab-probe-1")["state"] == "queued"
    assert colab_jobs.read_result(vault, "colab-probe-1") is None


def test_idempotency_prevents_overwriting_finished_result(vault):
    submit_job(vault, {"type": "echo", "message": "hello"}, "colab-retry-1")
    result = colab_jobs.run_job(vault, "colab-retry-1")
    assert result["ok"] is True
    again = submit_job(vault, {"type": "echo", "message": "hello"}, "colab-retry-1")
    assert again["state"] == "already_exists"
    assert job_report(vault, "colab-retry-1")["result"] == result
    with pytest.raises(ValueError, match="different specification"):
        submit_job(vault, {"type": "echo", "message": "overwrite"}, "colab-retry-1")


@pytest.mark.parametrize("name", [
    "../.env", "/etc/passwd", "06_Automation/Hermes/../../../keys",
    "06_Automation/Hermes//secret", "06_Automation/Hermes/a\\b",
    "06_Automation/Hermes/./file.txt", "other_dir/item.md",
    "06_Automation/Hermes/a/../secret",
])
def test_input_restricted_to_canonical_hermes_subtree(name):
    with pytest.raises(ValueError):
        safe_input_path(name)


def test_valid_text_statistics_round_trip(vault):
    input_path = "06_Automation/Hermes/IntegrationTests/sample.md"
    vault.write_text(input_path, "foo bar\nbaz\n")
    submit_job(vault, {"type": "text_stats", "input": input_path}, "colab-stats-1")
    # Local reference execution mirrors the active Colab worker's contract.
    response = colab_jobs.run_job(vault, "colab-stats-1")
    assert response["ok"]
    assert job_report(vault, "colab-stats-1")["state"] == "done"
    assert response["result"]["words"] == 3


def test_worker_heartbeat_detects_stale_and_live(vault):
    now = datetime(2026, 10, 26, 10, 0, tzinfo=timezone.utc)
    assert worker_health(vault, current_time=now)["reason"] == "no_worker_heartbeat"
    vault.write_json(WORKER_FILE, {
        "state": "running", "last_seen_at": (now - timedelta(seconds=10)).isoformat()
    })
    assert worker_health(vault, current_time=now)["online"]
    assert not worker_health(vault, current_time=now + timedelta(minutes=5))["online"]
    vault.write_json(WORKER_FILE, {
        "state": "stopped", "last_seen_at": now.isoformat()
    })
    assert not worker_health(vault, current_time=now)["online"]


def test_generated_worker_notebook_is_self_contained_and_compiles():
    notebook = build_worker_notebook()
    assert len(notebook["cells"]) == 3
    auth_source = "".join(notebook["cells"][1]["source"])
    source = "".join(notebook["cells"][2]["source"])
    assert "auth.authenticate_user()" in auth_source
    assert 'drive.mount("/content/drive")' in auth_source
    assert "WORKER_MINUTES = 30" in source
    assert "MAX_JOBS = 50" in source
    compile(source, "<colab-worker>", "exec")
    assert "eval(" not in source and "exec(" not in source
    assert notebook == json.loads(worker_notebook_bytes())
    with pytest.raises(ValueError):
        build_worker_notebook("../untrusted")


def test_generated_worker_executes_queued_job_against_mounted_fixture(tmp_path):
    """Run the *actual generated notebook cell* with a fake mounted Drive."""
    notebook = build_worker_notebook()
    source = "".join(notebook["cells"][2]["source"])
    expected = 'VAULT = Path("/content/drive/MyDrive") / VAULT_NAME'
    assert expected in source
    source = source.replace(expected, "VAULT = Path(" + repr(str(tmp_path)) + ")")
    source = source.replace(
        "deadline = time.monotonic() + min(max(WORKER_MINUTES, 1), 120) * 60",
        "deadline = time.monotonic() + 0.01",
    )
    source = source.replace("time.sleep(max(POLL_SECONDS, 5))", "time.sleep(0.01)")
    folder = (tmp_path / "06_Automation" / "Hermes" / "ColabJobs" / "fixture-job-1")
    folder.mkdir(parents=True)
    (folder / "job.json").write_text(json.dumps({
        "job_id": "fixture-job-1", "schema": 1,
        "spec": {"type": "echo", "message": "connected"}}), encoding="utf8")
    (folder / "status.json").write_text(json.dumps({
        "job_id": "fixture-job-1", "state": "queued"}), encoding="utf8")
    exec(compile(source, "<mounted-colab-worker>", "exec"), {})
    assert json.loads((folder / "status.json").read_text())["state"] == "done"
    result = json.loads((folder / "result.json").read_text())
    assert result["ok"] is True and result["result"]["message"] == "connected"
    worker = json.loads((folder.parent / "worker.json").read_text())
    assert worker["state"] == "stopped"


def test_hermes_native_tool_handlers_use_drive_vault(monkeypatch, vault):
    from tools import colab_job_tools
    from tools.registry import registry
    monkeypatch.setenv("GOOGLE_DRIVE_ACCESS_TOKEN", "fake-for-inmemory-transport")
    monkeypatch.setenv("HERMES_DRIVE_VAULT_ROOT_ID", "fake-vault-root")
    monkeypatch.setattr(colab_job_tools, "_vault", lambda: vault)
    assert registry.get_entry("colab_submit") is not None
    assert registry.get_entry("colab_status") is not None
    queued = json.loads(colab_job_tools._submit({
        "type": "echo", "message": "hello", "job_id": "tool-check-1"
    }))
    assert queued["success"] is True and queued["state"] == "queued"
    status = json.loads(colab_job_tools._status({"job_id": "tool-check-1"}))
    assert status["success"] and status["state"] == "queued"
    monkeypatch.delenv("GOOGLE_DRIVE_ACCESS_TOKEN", raising=False)
    no_auth = json.loads(colab_job_tools._submit({
        "type": "echo", "message": "nope"
    }))
    assert no_auth.get("error")
