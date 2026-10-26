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


def test_hermes_discord_toolset_includes_colab_tools_but_schema_gated(monkeypatch):
    from toolsets import resolve_toolset
    from tools import colab_job_tools
    from tools.registry import registry, invalidate_check_fn_cache
    tools = resolve_toolset("hermes-discord")
    assert "colab_submit" in tools
    assert "colab_status" in tools
    monkeypatch.delenv("GOOGLE_DRIVE_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("HERMES_DRIVE_VAULT_ROOT_ID", raising=False)
    invalidate_check_fn_cache()
    names = {d["function"]["name"] for d in registry.get_definitions(set(tools), quiet=True)}
    assert "colab_submit" not in names
    monkeypatch.setenv("GOOGLE_DRIVE_ACCESS_TOKEN", "in-memory-test-only")
    monkeypatch.setenv("HERMES_DRIVE_VAULT_ROOT_ID", "test-vault")
    invalidate_check_fn_cache()
    names = {d["function"]["name"] for d in registry.get_definitions(set(tools), quiet=True)}
    assert "colab_submit" in names and "colab_status" in names
    invalidate_check_fn_cache()


def test_google_adc_provider_refreshes_on_demand(monkeypatch, tmp_path):
    """A protected ADC file can refresh Drive tokens without Colab or static secrets."""
    import sys
    from types import ModuleType
    from tools import colab_job_tools

    creds_file = tmp_path / "owner-authorized-adc.json"
    creds_file.write_text("{}", encoding="utf8")
    monkeypatch.delenv("GOOGLE_DRIVE_ACCESS_TOKEN", raising=False)
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(creds_file))
    monkeypatch.setenv("HERMES_DRIVE_VAULT_ROOT_ID", "canonical-vault-id")
    monkeypatch.setattr(colab_job_tools, "_CREDENTIALS", None)

    class FakeCredentials:
        valid = False
        token = None
        calls = 0

        def refresh(self, _request):
            self.calls += 1
            self.token = "test-token-not-secret"
            self.valid = True

    cred = FakeCredentials()
    auth_module = ModuleType("google.auth")
    auth_module.__path__ = []
    load_args = []
    def load(filename, scopes):
        load_args.append((filename, scopes))
        return cred, "dummy-project"
    auth_module.load_credentials_from_file = load
    request_module = ModuleType("google.auth.transport.requests")
    request_module.Request = lambda: object()
    transport_module = ModuleType("google.auth.transport")
    transport_module.__path__ = []
    transport_module.requests = request_module
    google_module = ModuleType("google")
    google_module.__path__ = []
    google_module.auth = auth_module
    auth_module.transport = transport_module

    for name, module in [
        ("google", google_module),
        ("google.auth", auth_module),
        ("google.auth.transport", transport_module),
        ("google.auth.transport.requests", request_module),
    ]:
        monkeypatch.setitem(sys.modules, name, module)

    assert colab_job_tools._enabled()
    vault = colab_job_tools._vault()
    assert vault._t._token_provider() == "test-token-not-secret"
    assert vault._t._token_provider() == "test-token-not-secret"
    assert cred.calls == 1
    assert load_args[0][0] == str(creds_file)
    assert load_args[0][1] == ["https://www.googleapis.com/auth/drive"]


def test_resident_worker_handles_only_explicitly_resident_jobs(vault):
    from hermes_hybrid.resident_worker import scan_once
    colab = submit_job(vault, {"type": "echo", "message": "colab"}, "job-colab-only")
    resident = submit_job(vault, {"type": "echo", "message": "linux",
                                  "executor": "resident"}, "job-resident-only")
    assert colab["executor"] == "colab"
    assert resident["executor"] == "resident"
    outcome = scan_once(vault, max_jobs=2)
    assert outcome["done"] == 1
    assert job_report(vault, "job-resident-only")["state"] == "done"
    assert job_report(vault, "job-colab-only")["state"] == "queued"
    assert colab_jobs.read_result(vault, "job-colab-only") is None


def test_resident_worker_recovers_a_stale_running_job(vault):
    from hermes_hybrid.resident_worker import scan_once
    submit_job(vault, {"type": "echo", "message": "retry",
                       "executor": "resident"}, "job-stale")
    folder = colab_jobs.job_dir("job-stale")
    vault.write_json(folder + "/status.json", {
        "job_id": "job-stale", "state": "running",
        "updated_at": "2025-01-01T00:00:00+00:00",
    })
    assert scan_once(vault)["done"] == 1
    assert job_report(vault, "job-stale")["state"] == "done"


def test_resident_recovers_crash_after_result_without_reexecuting(vault, monkeypatch):
    from hermes_hybrid import resident_worker
    submit_job(vault, {"type": "echo", "message": "preserved",
                       "executor": "resident"}, "job-partial")
    folder = colab_jobs.job_dir("job-partial")
    result = {"job_id": "job-partial", "ok": True,
              "result": {"type": "echo", "message": "original"},
              "finished_at": "2026-10-26T01:00:00+00:00"}
    vault.write_json(folder + "/result.json", result)
    vault.write_json(folder + "/status.json", {
        "job_id": "job-partial", "state": "running",
        "updated_at": "2025-01-01T00:00:00+00:00"
    })
    monkeypatch.setattr(colab_jobs, "run_job", lambda *_: pytest.fail("reexecuted"))
    assert resident_worker.scan_once(vault)["done"] == 1
    assert job_report(vault, "job-partial")["result"] == result


def test_completed_jobs_do_not_starve_new_resident_jobs(vault):
    from hermes_hybrid.resident_worker import scan_once
    for i in range(12):
        name = f"a-completed-{i:02d}"
        submit_job(vault, {"type": "echo", "message": "done",
                           "executor": "resident"}, name)
        colab_jobs.run_job(vault, name)
    submit_job(vault, {"type": "echo", "message": "new",
                       "executor": "resident"}, "z-new-job")
    outcome = scan_once(vault, max_jobs=1)
    assert outcome["done"] == 1
    assert job_report(vault, "z-new-job")["state"] == "done"


def test_colab_notebook_skips_resident_jobs(tmp_path):
    notebook = build_worker_notebook()
    source = "".join(notebook["cells"][2]["source"])
    expected = 'VAULT = Path("/content/drive/MyDrive") / VAULT_NAME'
    assert expected in source
    source = source.replace(expected, "VAULT = Path(" + repr(str(tmp_path)) + ")")
    source = source.replace(
        "deadline = time.monotonic() + min(max(WORKER_MINUTES, 1), 120) * 60",
        "deadline = time.monotonic() + 0.01")
    source = source.replace("time.sleep(max(POLL_SECONDS, 5))", "time.sleep(0.01)")
    folder = tmp_path / "06_Automation" / "Hermes" / "ColabJobs" / "resident-only"
    folder.mkdir(parents=True)
    (folder / "job.json").write_text(json.dumps({
        "job_id": "resident-only", "schema": 1,
        "spec": {"type": "echo", "message": "dont_run",
                 "executor": "resident"}}), encoding="utf8")
    (folder / "status.json").write_text(json.dumps({
        "job_id": "resident-only", "state": "queued"}), encoding="utf8")
    exec(compile(source, "<worker>", "exec"), {})
    assert json.loads((folder / "status.json").read_text())["state"] == "queued"
    assert not (folder / "result.json").exists()


def test_resident_health_is_independent_of_colab_heartbeat(vault):
    from hermes_hybrid.colab_dispatch import RESIDENT_WORKER_FILE, resident_health
    now = datetime.now(timezone.utc)
    vault.write_json(RESIDENT_WORKER_FILE, {
        "worker_id": "test", "state": "running", "last_seen_at": now.isoformat()
    })
    assert resident_health(vault, current_time=now)["online"]
    assert not worker_health(vault, current_time=now)["online"]


def test_submit_tool_can_route_to_resident(monkeypatch, vault):
    from tools import colab_job_tools
    monkeypatch.setenv("GOOGLE_DRIVE_ACCESS_TOKEN", "fake")
    monkeypatch.setenv("HERMES_DRIVE_VAULT_ROOT_ID", "fake-root")
    monkeypatch.setattr(colab_job_tools, "_vault", lambda: vault)
    reply = json.loads(colab_job_tools._submit({
        "type": "echo", "message": "linux", "executor": "resident",
        "job_id": "resident-tool-test"}))
    assert reply["success"]
    assert reply["executor"] == "resident"
    assert colab_jobs.read_job(vault, "resident-tool-test")["spec"]["executor"] == "resident"


def test_linux_worker_uptime_record_carries_across_restarts(vault):
    from hermes_hybrid.resident_worker import run
    from hermes_hybrid.colab_dispatch import RESIDENT_WORKER_FILE, resident_health
    assert run(vault, once=True) == 0
    first = vault.read_json(RESIDENT_WORKER_FILE)
    assert first["restart_count"] == 1
    assert first["state"] == "stopped"
    assert first["cycle_count"] == 1
    assert run(vault, once=True) == 0
    second = vault.read_json(RESIDENT_WORKER_FILE)
    assert second["restart_count"] == 2
    assert second["cycle_count"] == 1
    assert second["worker_id"] != first["worker_id"]
    assert not resident_health(vault)["online"]


def test_hermes_tool_reads_both_worker_heartbeats(monkeypatch, vault):
    from tools import colab_job_tools
    from hermes_hybrid.colab_dispatch import RESIDENT_WORKER_FILE, WORKER_FILE
    monkeypatch.setenv("GOOGLE_DRIVE_ACCESS_TOKEN", "fixture-token")
    monkeypatch.setenv("HERMES_DRIVE_VAULT_ROOT_ID", "fixture-vault")
    monkeypatch.setattr(colab_job_tools, "_vault", lambda: vault)
    current = datetime.now(timezone.utc).isoformat()
    vault.write_json(RESIDENT_WORKER_FILE, {
        "state": "running", "last_seen_at": current, "started_at": current,
        "restart_count": 3, "cycle_count": 100
    })
    health = json.loads(colab_job_tools._health_status({}))
    assert health["success"] is True
    assert health["resident"]["online"] is True
    assert health["resident"]["restart_count"] == 3
    assert 0 <= health["resident"]["current_process_uptime_seconds"] <= 30
    assert health["colab"]["online"] is False


def test_authorized_muse_hatch_cli_credential_provider(monkeypatch, tmp_path, vault):
    """No static Google token is required on a VM with an authorized hatch CLI."""
    from tools import colab_job_tools
    from hermes_hybrid import cli_drive_transport
    exe = tmp_path / "fake-hatch-gws-cli"
    exe.write_text("#!/bin/sh\nexit 0\n", encoding="utf8")
    exe.chmod(0o700)
    monkeypatch.delenv("GOOGLE_DRIVE_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    monkeypatch.setenv("HERMES_DRIVE_PROVIDER", "hatch")
    monkeypatch.setenv("HERMES_DRIVE_VAULT_ROOT_ID", "fixture-vault")
    monkeypatch.setenv("HERMES_GWS_CLI", str(exe))
    assert colab_job_tools._enabled()
    called = []
    monkeypatch.setattr(cli_drive_transport, "cli_vault",
                        lambda root_id: called.append(root_id) or vault)
    assert colab_job_tools._vault() is vault
    assert called == ["fixture-vault"]
    monkeypatch.setenv("HERMES_GWS_CLI", "/nonexistent/unauthorized")
    assert not colab_job_tools._enabled()
