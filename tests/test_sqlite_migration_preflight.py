"""Fail-closed, non-destructive legacy SQLite -> Obsidian export tests."""

import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "migrate_sqlite_to_vault.py"


class SQLiteVaultMigrationPreflightTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / "home #?%"
        self.home.mkdir()
        self.vault = self.root / "vault"

    def queue(self, rows=((1, "[]"),)):
        db = self.home / "retaindb_queue.db"
        with sqlite3.connect(db) as conn:
            conn.execute(
                "CREATE TABLE pending (id INTEGER PRIMARY KEY, user_id TEXT, "
                "session_id TEXT, messages_json TEXT, created_at TEXT, last_error TEXT)"
            )
            conn.executemany(
                "INSERT INTO pending VALUES (?, 'u1', 's1', ?, '2026-01-01', NULL)",
                rows,
            )
        return db

    def run_cli(self, *flags):
        return subprocess.run(
            [
                sys.executable, str(SCRIPT), "--home", str(self.home),
                "--vault", str(self.vault), *flags,
            ],
            capture_output=True, text=True, check=False,
        )

    def test_unsafe_fact_id_rejected_before_any_export(self):
        source = self.queue()
        with sqlite3.connect(self.home / "memory_store.db") as conn:
            conn.execute("CREATE TABLE facts (fact_id TEXT, hrr_vector BLOB)")
            conn.execute("INSERT INTO facts VALUES ('../escape', NULL)")
        before = source.read_bytes()
        result = self.run_cli()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unsafe legacy record identifier", result.stderr)
        self.assertFalse((self.vault / "06_Automation/Hermes/RetainQueue/1.json").exists())
        self.assertEqual(source.read_bytes(), before)

    def test_existing_destination_blocks_entire_export(self):
        self.queue(((1, "[]"), (2, "[]")))
        out = self.vault / "06_Automation/Hermes/RetainQueue"
        out.mkdir(parents=True)
        (out / "2.json").write_text("KEEP", encoding="utf-8")
        result = self.run_cli()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("already exists", result.stderr)
        self.assertEqual((out / "2.json").read_text(encoding="utf-8"), "KEEP")
        self.assertFalse((out / "1.json").exists())

    def test_symlink_destination_cannot_escape_vault(self):
        self.queue()
        outside = self.root / "outside"
        outside.mkdir()
        parent = self.vault / "06_Automation/Hermes"
        parent.mkdir(parents=True)
        (parent / "RetainQueue").symlink_to(outside, target_is_directory=True)
        result = self.run_cli()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("symlinked Vault destination", result.stderr)
        self.assertEqual(list(outside.iterdir()), [])

    def test_corrupt_json_refused_without_partial_export(self):
        self.queue(((1, "[]"), (2, "{broken")))
        result = self.run_cli()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("JSONDecodeError", result.stderr)
        self.assertFalse((self.vault / "06_Automation/Hermes/RetainQueue/1.json").exists())

    def test_missing_required_table_fails_closed(self):
        self.queue()
        with sqlite3.connect(self.home / "memory_store.db") as conn:
            conn.execute("CREATE TABLE unrelated (id INTEGER)")
        result = self.run_cli()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no such table: facts", result.stderr)
        self.assertFalse((self.vault / "06_Automation/Hermes/RetainQueue/1.json").exists())

    def test_special_characters_in_home_and_readonly_source(self):
        source = self.queue()
        before = source.read_bytes()
        dry = self.run_cli("--dry-run")
        self.assertEqual(dry.returncode, 0, dry.stderr)
        self.assertEqual(json.loads(dry.stdout)["retaindb_queue"], 1)
        self.assertFalse(self.vault.exists())
        actual = self.run_cli()
        self.assertEqual(actual.returncode, 0, actual.stderr)
        out = self.vault / "06_Automation/Hermes/RetainQueue/1.json"
        self.assertEqual(json.loads(out.read_text(encoding="utf-8"))["user_id"], "u1")
        self.assertEqual(source.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
