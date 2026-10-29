"""Non-destructive, full-record verification of legacy SQLite session migration."""

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hermes_obsidian_state import SessionDB
from scripts.migrate_sessions_to_obsidian import migrate


class SessionMigrationVerificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.vault = self.root / "vault"
        self.vault.mkdir()
        env = patch.dict(os.environ, {"HERMES_OBSIDIAN_VAULT": str(self.vault)})
        env.start()
        self.addCleanup(env.stop)
        self.source = self.root / "state #?%.db"
        conn = sqlite3.connect(self.source)
        try:
            conn.execute(
                "CREATE TABLE sessions (id TEXT, source TEXT, started_at REAL, model TEXT)"
            )
            conn.execute(
                "CREATE TABLE messages (id INTEGER, session_id TEXT, role TEXT, "
                "content TEXT, timestamp REAL, tool_calls TEXT)"
            )
            conn.execute(
                "INSERT INTO sessions VALUES (?, ?, ?, ?)",
                ("legacy1", "discord", 100, "original-model"),
            )
            content = "\x00json:" + json.dumps(
                [{"type": "text", "text": "日本語の会話"}], ensure_ascii=False
            )
            conn.execute(
                "INSERT INTO messages VALUES (?, ?, ?, ?, ?, ?)",
                (1, "legacy1", "assistant", content, 101,
                 json.dumps([{"id": "call-1", "function": {"name": "lookup"}}])),
            )
            conn.commit()
        finally:
            conn.close()

    def test_verify_only_checks_complete_records_and_never_writes(self):
        original = self.source.read_bytes()
        dry = migrate(self.source)
        self.assertEqual(dry["mode"], "dry-run")
        self.assertEqual(dry["messages"], 1)

        # Dry-run no longer initializes the Sessions directory. Create it
        # explicitly to exercise missing-record verification separately.
        (self.vault / "06_Automation/Hermes/Sessions").mkdir(parents=True)
        with self.assertRaisesRegex(RuntimeError, "Missing Vault sessions"):
            migrate(self.source, verify=True)
        self.assertFalse((self.vault / "06_Automation/Hermes/Sessions/legacy1").exists())

        committed = migrate(self.source, commit=True)
        self.assertEqual(committed["written"], 1)
        messages = SessionDB().get_messages("legacy1")
        self.assertEqual(messages[0]["content"][0]["text"], "日本語の会話")
        self.assertEqual(messages[0]["tool_calls"][0]["id"], "call-1")

        before_verify = (
            self.vault / "06_Automation/Hermes/Sessions/legacy1/metadata.json"
        ).read_bytes()
        verified = migrate(self.source, verify=True)
        self.assertEqual(verified["mode"], "verify")
        self.assertEqual(verified["verified"], 1)
        self.assertEqual(verified["written"], 0)
        self.assertEqual(
            (self.vault / "06_Automation/Hermes/Sessions/legacy1/metadata.json").read_bytes(),
            before_verify,
        )
        self.assertEqual(self.source.read_bytes(), original)

        SessionDB()._edit("legacy1", lambda row: row.update(model="changed"))
        with self.assertRaisesRegex(RuntimeError, "Conflicting Vault session"):
            migrate(self.source, verify=True)
        self.assertEqual(self.source.read_bytes(), original)

    def test_dry_run_never_initializes_missing_vault_directories(self):
        source_before = self.source.read_bytes()
        result = migrate(self.source)
        self.assertEqual(result["mode"], "dry-run")
        self.assertEqual(result["sessions"], 1)
        self.assertEqual(result["written"], 0)
        self.assertFalse((self.vault / "06_Automation").exists())
        self.assertEqual(self.source.read_bytes(), source_before)

    def test_verify_only_never_initializes_missing_vault_directories(self):
        with self.assertRaisesRegex(RuntimeError, "Existing Vault Sessions directory"):
            migrate(self.source, verify=True)
        self.assertFalse((self.vault / "06_Automation").exists())

    def test_duplicate_source_session_ids_fail_before_writing(self):
        # Even a malformed legacy database must never cause a partial import.
        # The first duplicate would otherwise be written before the second
        # triggers a destination conflict during the commit loop.
        with sqlite3.connect(self.source) as conn:
            conn.execute(
                "INSERT INTO sessions VALUES (?, ?, ?, ?)",
                ("legacy1", "discord", 200, "duplicate-model"),
            )
        original = self.source.read_bytes()
        with self.assertRaisesRegex(RuntimeError, "Duplicate SQLite session ID"):
            migrate(self.source, commit=True)
        self.assertFalse(
            (self.vault / "06_Automation/Hermes/Sessions/legacy1/metadata.json").exists()
        )
        self.assertEqual(self.source.read_bytes(), original)

    def test_postwrite_verification_detects_metadata_corruption(self):
        original_save = SessionDB._save

        def corrupt_model(instance, session_id, record):
            changed = dict(record)
            changed["model"] = "corrupted-during-save"
            return original_save(instance, session_id, changed)

        with patch.object(SessionDB, "_save", corrupt_model):
            with self.assertRaisesRegex(RuntimeError, "Post-write verification failed"):
                migrate(self.source, commit=True)
        self.assertTrue(self.source.exists())
        self.assertFalse(list(self.vault.rglob("*.db")))


if __name__ == "__main__":
    unittest.main()
