"""Tests for hermes_hybrid.drive_vault and hermes_hybrid.colab_jobs.

Everything runs against FakeDriveTransport: the Drive folder
``ChatGPT-Obsidian-Vault`` is the canonical store, writes are
revision-checked and verified, transient failures retry, and a raced
write surfaces as a conflict instead of silent data loss.
"""

import json

import pytest

from hermes_hybrid import colab_jobs
from hermes_hybrid.drive_vault import (
    DriveConflict,
    DriveNotFound,
    DriveUnavailable,
    DriveVault,
    DriveVaultDir,
    FakeDriveTransport,
)


@pytest.fixture()
def vault():
    transport = FakeDriveTransport()
    transport.seed_root()
    return DriveVault(transport, sleeper=lambda _s: None), transport


class TestDriveVaultBasics:
    def test_root_must_exist_and_is_never_created(self):
        transport = FakeDriveTransport()  # no root seeded
        v = DriveVault(transport, sleeper=lambda _s: None)
        with pytest.raises(DriveNotFound):
            v.write_text("06_Automation/Hermes/x.md", "hi")

    def test_write_read_update_roundtrip(self, vault):
        v, _ = vault
        meta1 = v.write_text("06_Automation/Hermes/Notes/a.md", "first")
        assert v.read_text("06_Automation/Hermes/Notes/a.md") == "first"
        meta2 = v.write_text("06_Automation/Hermes/Notes/a.md", "second",
                             expected_revision=meta1.revision)
        assert meta2.revision != meta1.revision
        assert v.read_text("06_Automation/Hermes/Notes/a.md") == "second"

    def test_json_roundtrip_and_listing(self, vault):
        v, _ = vault
        v.write_json("06_Automation/Hermes/Kanban/board/tasks.json", {"n": 1})
        assert v.read_json("06_Automation/Hermes/Kanban/board/tasks.json") == {"n": 1}
        assert v.list_names("06_Automation/Hermes/Kanban/board") == ["tasks.json"]
        assert v.exists("06_Automation/Hermes/Kanban/board/tasks.json")
        assert not v.exists("06_Automation/Hermes/Kanban/board/nope.json")

    def test_invalid_paths_rejected(self, vault):
        v, _ = vault
        for bad in ("../escape.md", "/abs.md", "a/../../b.md", ""):
            with pytest.raises(ValueError):
                v.write_text(bad, "x")


class TestConflictAndFailure:
    def test_stale_revision_is_refused_before_overwrite(self, vault):
        v, _ = vault
        meta = v.write_text("06_Automation/Hermes/state.json", "v1")
        # Another writer updates the file.
        v.write_text("06_Automation/Hermes/state.json", "v2",
                     expected_revision=meta.revision)
        with pytest.raises(DriveConflict):
            v.write_text("06_Automation/Hermes/state.json", "v3",
                         expected_revision=meta.revision)
        # The newer content survived; the stale write did not land.
        assert v.read_text("06_Automation/Hermes/state.json") == "v2"

    def test_transient_failure_retries_and_recovers(self, vault):
        v, transport = vault
        v.write_text("06_Automation/Hermes/log.md", "before")
        transport.fail_next(2, DriveUnavailable)
        v.write_text("06_Automation/Hermes/log.md", "after")
        assert v.read_text("06_Automation/Hermes/log.md") == "after"

    def test_persistent_failure_raises_and_old_content_intact(self, vault):
        v, transport = vault
        v.write_text("06_Automation/Hermes/log.md", "before")
        transport.fail_next(50, DriveUnavailable)
        with pytest.raises(DriveUnavailable):
            v.write_text("06_Automation/Hermes/log.md", "after")
        transport.fail_next(0)
        assert v.read_text("06_Automation/Hermes/log.md") == "before"


class TestDriveVaultDir:
    def test_record_store_semantics(self, vault):
        v, _ = vault
        d = DriveVaultDir(v, "06_Automation", "Hermes", "WorkerLogs")
        d.write("run-1", {"lines": 3})
        d.write("run-2", {"lines": 5})
        assert d.read("run-1") == {"lines": 3}
        assert d.list_ids() == ["run-1", "run-2"]
        assert d.delete("run-1") is True
        assert d.read("run-1") is None
        assert [r["lines"] for r in d.list_records()] == [5]

    def test_record_write_with_revision_guard(self, vault):
        v, _ = vault
        d = DriveVaultDir(v, "06_Automation", "Hermes", "DiscordThreads")
        d.write("thread-9", {"state": "open"})
        data, revision = d.read_with_revision("thread-9")
        assert data == {"state": "open"}
        d.write("thread-9", {"state": "closed"}, expected_revision=revision)
        with pytest.raises(DriveConflict):
            d.write("thread-9", {"state": "open"}, expected_revision=revision)


class TestColabJobs:
    def test_echo_job_lifecycle(self, vault):
        v, _ = vault
        colab_jobs.write_job(v, "job-echo", {"type": "echo", "message": "ping"})
        assert colab_jobs.read_status(v, "job-echo")["state"] == "queued"
        result = colab_jobs.run_job(v, "job-echo")
        assert result["ok"] is True
        assert result["result"] == {"type": "echo", "message": "ping"}
        assert colab_jobs.read_status(v, "job-echo")["state"] == "done"
        assert colab_jobs.read_result(v, "job-echo")["ok"] is True

    def test_text_stats_job(self, vault):
        v, _ = vault
        v.write_text("06_Automation/Hermes/Inputs/doc.md", "one two\nthree\n")
        colab_jobs.write_job(v, "job-stats",
                             {"type": "text_stats",
                              "input": "06_Automation/Hermes/Inputs/doc.md"})
        result = colab_jobs.run_job(v, "job-stats")
        assert result["ok"] is True
        stats = result["result"]
        assert stats["words"] == 3 and stats["lines"] == 2
        assert len(stats["sha256"]) == 64

    def test_missing_input_is_recorded_as_failure(self, vault):
        v, _ = vault
        colab_jobs.write_job(v, "job-bad",
                             {"type": "hash_file", "input": "nope/missing.bin"})
        result = colab_jobs.run_job(v, "job-bad")
        assert result["ok"] is False
        assert colab_jobs.read_status(v, "job-bad")["state"] == "failed"

    def test_invalid_spec_rejected(self, vault):
        v, _ = vault
        with pytest.raises(ValueError):
            colab_jobs.write_job(v, "job-x", {"type": "shell", "cmd": "rm -rf /"})

    def test_notebook_is_valid_and_targets_vault_job(self, vault):
        nb = colab_jobs.build_colab_notebook("job-echo")
        assert nb["nbformat"] == 4
        code = "".join(nb["cells"][1]["source"])
        assert "ChatGPT-Obsidian-Vault" in code
        assert "job-echo" in code
        assert "drive.mount" in code
        # The notebook is JSON-serialisable as an .ipynb file.
        json.dumps(nb)
