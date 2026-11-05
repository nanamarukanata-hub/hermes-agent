"""Step 10 operational tests for the Drive Vault integration.

Simulates, against the in-memory Drive transport: process restart
(a brand-new DriveVault over the same remote state), concurrent
writers, failure recovery, duplicate prevention, and a full
Hermes -> Colab-job -> Hermes round trip. The remote (Drive) state
is the only thing that survives the "restart" -- exactly the
canonical-store property the integration is built on.
"""

import pytest

from hermes_hybrid import colab_jobs
from hermes_hybrid.drive_vault import (
    DriveConflict,
    DriveVault,
    DriveVaultDir,
    FakeDriveTransport,
)

TREE = "06_Automation/Hermes"


@pytest.fixture()
def transport():
    t = FakeDriveTransport()
    t.seed_root()
    return t


def _vault(t):
    return DriveVault(t, sleeper=lambda _s: None)


class TestRestartRecovery:
    def test_state_survives_client_restart(self, transport):
        v1 = _vault(transport)
        v1.write_json(f"{TREE}/Sessions/s1/metadata.json", {"id": "s1"})
        kanban = DriveVaultDir(v1, "06_Automation", "Hermes", "Kanban", "board")
        kanban.write("task-1", {"title": "t", "status": "open"})

        # "Restart": a completely new client instance, same Drive state.
        v2 = _vault(transport)
        assert v2.read_json(f"{TREE}/Sessions/s1/metadata.json") == {"id": "s1"}
        kanban2 = DriveVaultDir(v2, "06_Automation", "Hermes", "Kanban", "board")
        assert kanban2.read("task-1") == {"title": "t", "status": "open"}

    def test_colab_job_completes_across_restart(self, transport):
        v1 = _vault(transport)
        v1.write_text(f"{TREE}/Inputs/note.md", "alpha beta\ngamma\n")
        colab_jobs.write_job(v1, "job-rt", {"type": "text_stats",
                                            "input": f"{TREE}/Inputs/note.md"})
        # Colab side (fresh client, as a mounted notebook would be):
        v2 = _vault(transport)
        result = colab_jobs.run_job(v2, "job-rt")
        assert result["ok"] is True
        # Hermes side after another restart reads the result back:
        v3 = _vault(transport)
        stored = colab_jobs.read_result(v3, "job-rt")
        assert stored["ok"] is True
        assert stored["result"]["words"] == 3
        assert colab_jobs.read_status(v3, "job-rt")["state"] == "done"


class TestConcurrentWrites:
    def test_two_writers_same_record_exactly_one_wins(self, transport):
        v = _vault(transport)
        d = DriveVaultDir(v, "06_Automation", "Hermes", "DiscordThreads")
        d.write("th-1", {"count": 0})
        _, rev_a = d.read_with_revision("th-1")
        _, rev_b = d.read_with_revision("th-1")
        d.write("th-1", {"count": 1}, expected_revision=rev_a)
        with pytest.raises(DriveConflict):
            d.write("th-1", {"count": 99}, expected_revision=rev_b)
        # Loser's write never landed; no duplicated or torn record.
        assert d.read("th-1") == {"count": 1}
        assert d.list_ids() == ["th-1"]

    def test_duplicate_delivery_is_idempotent(self, transport):
        """Writing the same job result twice leaves a single result."""
        v = _vault(transport)
        colab_jobs.write_job(v, "job-idem", {"type": "echo", "message": "x"})
        r1 = colab_jobs.run_job(v, "job-idem")
        r2 = colab_jobs.run_job(v, "job-idem")
        assert r1["ok"] and r2["ok"]
        assert v.list_names(colab_jobs.job_dir("job-idem")) == [
            "checkpoint.json", "job.json", "result.json", "status.json",
        ]
