"""RetainDB pending queue migration: source remains untouched."""
import json
import sqlite3

from scripts.migrate_retaindb_queue import migrate


def test_migration_dry_run_apply_idempotent_and_read_only(tmp_path):
    source = tmp_path / "legacy_queue.db"
    vault = tmp_path / "vault"
    vault.mkdir()
    with sqlite3.connect(source) as conn:
        conn.execute(
            "CREATE TABLE pending (id INTEGER PRIMARY KEY, user_id TEXT, "
            "session_id TEXT, messages_json TEXT, created_at TEXT, last_error TEXT)"
        )
        conn.execute(
            "INSERT INTO pending VALUES (1, ?, ?, ?, ?, ?)",
            ("user-a", "session-a", json.dumps([{"role": "user", "content": "hi"}]),
             "2026-10-25T00:00:00Z", None),
        )
        conn.execute(
            "INSERT INTO pending VALUES (2, ?, ?, ?, ?, ?)",
            ("user-b", "session-b", json.dumps([{"role": "user", "content": "hey"}]),
             "2026-10-25T00:00:00Z", "API unavailable"),
        )
        conn.execute(
            "INSERT INTO pending VALUES (3, ?, ?, ?, ?, ?)",
            ("user-c", "session-c", "invalid json", "2026-10-25T00:00:00Z", None),
        )

    queue_dir = vault / "06_Automation" / "Hermes" / "RetainQueue"
    dry = migrate(source, vault)
    assert dry == {"found": 3, "migrated": 0, "already_present": 0, "invalid": 1}
    assert not queue_dir.exists()

    applied = migrate(source, vault, apply=True)
    assert applied == {"found": 3, "migrated": 2, "already_present": 0, "invalid": 1}
    assert (queue_dir / "legacy-1.json").is_file()
    assert json.loads((queue_dir / "legacy-2.json").read_text())["last_error"] == "API unavailable"

    again = migrate(source, vault, apply=True)
    assert again == {"found": 3, "migrated": 0, "already_present": 2, "invalid": 1}
    with sqlite3.connect(source) as conn:
        assert conn.execute("SELECT count(*) FROM pending").fetchone()[0] == 3
