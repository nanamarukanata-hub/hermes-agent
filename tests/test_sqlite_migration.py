"""Tests for the SQLite -> Vault migration (runtime stores only).

Kanban is excluded: its runtime still uses SQLite (see
docs/HERMES-HYBRID.md).  Migration scripts are read-only and never
delete a legacy .db file.
"""

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

from hermes_hybrid.vault_kv import VaultDir


def test_vault_dir_roundtrip(tmp_path):
    store = VaultDir(tmp_path / "vault", "ApiResponses")
    store.write("resp_1", {"response_id": "resp_1", "data": {"a": 1}, "accessed_at": 1.0})
    assert store.read("resp_1")["data"] == {"a": 1}
    assert store.list_ids() == ["resp_1"]
    assert store.delete("resp_1") is True
    assert store.read("resp_1") is None
    assert store.delete("resp_1") is False


def test_kanban_export_is_readonly(tmp_path):
    db = tmp_path / "kanban.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE tasks (id TEXT PRIMARY KEY, title TEXT, status TEXT)")
    conn.execute("INSERT INTO tasks VALUES ('t1', 'Do thing', 'todo')")
    conn.execute("CREATE TABLE task_links (parent_id TEXT, child_id TEXT)")
    conn.commit()
    conn.close()
    before = db.read_bytes()
    vault = tmp_path / "vault"
    result = subprocess.run(
        [sys.executable, "scripts/migrate_kanban_to_vault.py", str(db), str(vault)],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    exported = json.loads((vault / "06_Automation" / "Hermes" / "Kanban" / "default" / "t1.json").read_text())
    assert exported["title"] == "Do thing"
    assert db.read_bytes() == before  # legacy DB untouched
    # Second run without --force refuses to overwrite.
    result2 = subprocess.run(
        [sys.executable, "scripts/migrate_kanban_to_vault.py", str(db), str(vault)],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True,
    )
    assert result2.returncode == 2


def test_sqlite_to_vault_dry_run(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    conn = sqlite3.connect(str(home / "retaindb_queue.db"))
    conn.execute("CREATE TABLE pending (id INTEGER PRIMARY KEY, user_id TEXT, session_id TEXT, messages_json TEXT, created_at TEXT, last_error TEXT)")
    conn.execute("INSERT INTO pending VALUES (1, 'u1', 's1', '[{\"role\": \"user\", \"content\": \"hi\"}]', '2026-01-01', NULL)")
    conn.commit()
    conn.close()
    vault = tmp_path / "vault"
    result = subprocess.run(
        [sys.executable, "scripts/migrate_sqlite_to_vault.py", "--vault", str(vault), "--home", str(home), "--dry-run"],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    counts = json.loads(result.stdout)
    assert counts["retaindb_queue"] == 1
    # Dry run writes nothing.
    assert not (vault / "06_Automation").exists()
