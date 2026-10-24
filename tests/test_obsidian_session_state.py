"""Regression tests for the SQLite-free Obsidian session backend."""
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hermes_obsidian_state import SessionDB


class ObsidianStateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.patch = patch.dict(os.environ, {"HERMES_OBSIDIAN_VAULT": self.temp.name})
        self.patch.start()
        self.db = SessionDB()

    def tearDown(self):
        self.patch.stop()
        self.temp.cleanup()

    def test_no_vault_environment_fails_closed(self):
        with patch.dict(os.environ, {"HERMES_OBSIDIAN_VAULT": ""}):
            with self.assertRaises(RuntimeError):
                SessionDB()

    def test_restart_and_multimodal_roundtrip(self):
        self.db.create_session("discord_demo", "discord", user_id="user")
        payload = [{"type": "text", "text": "こんにちは"}]
        self.db.append_message("discord_demo", "user", payload)
        self.db.append_message("discord_demo", "assistant", "返答", tool_calls=[{"id": "x"}])
        reopened = SessionDB()
        self.assertEqual(reopened.get_messages("discord_demo")[0]["content"], payload)
        self.assertEqual(reopened.get_messages_as_conversation("discord_demo")[1]["content"], "返答")
        self.assertEqual(reopened.get_session("discord_demo")["message_count"], 2)
        self.assertEqual(reopened.get_session("discord_demo")["tool_call_count"], 1)
        self.assertTrue((reopened.root / "discord_demo" / "Transcript.md").exists())

    def test_rewrite_title_search_and_cleanup(self):
        self.db.create_session("test_a", "discord")
        self.db.append_message("test_a", "user", "old")
        self.db.replace_messages("test_a", [{"role": "user", "content": "new", "timestamp": 123}])
        self.assertEqual(self.db.search_messages("old"), [])
        self.assertEqual(len(self.db.search_messages("new")), 1)
        self.assertTrue(self.db.set_session_title("test_a", "Welcome"))
        self.assertEqual(self.db.resolve_session_by_title("Welcome"), "test_a")
        self.db.end_session("test_a", "test")
        self.assertTrue(self.db.delete_session("test_a"))
        self.assertEqual(self.db.session_count(), 0)

    def test_offline_legacy_migration_dry_run_and_commit(self):
        from scripts.migrate_sessions_to_obsidian import migrate

        old_dir = Path(self.temp.name).parent / (Path(self.temp.name).name + ".legacy")
        old_dir.mkdir()
        old_db = old_dir / "state.db"
        self.addCleanup(lambda: __import__("shutil").rmtree(old_dir, ignore_errors=True))

        # Migration's only SQLite use is the explicitly supplied legacy file,
        # not the runtime or the Obsidian Vault.
        with sqlite3.connect(old_db) as conn:
            conn.execute("CREATE TABLE sessions (id TEXT, source TEXT, started_at REAL, model TEXT)")
            conn.execute("CREATE TABLE messages (id INTEGER, session_id TEXT, role TEXT, content TEXT, timestamp REAL, tool_calls TEXT)")
            conn.execute("INSERT INTO sessions VALUES (?, ?, ?, ?)", ("legacy1", "discord", 100, "test-model"))
            conn.execute("INSERT INTO messages VALUES (?, ?, ?, ?, ?, ?)",
                         (1, "legacy1", "user", "legacy content", 101, None))
        dry = migrate(old_db)
        self.assertEqual(dry["mode"], "dry-run")
        self.assertEqual(dry["written"], 0)
        self.assertIsNone(self.db.get_session("legacy1"))

        completed = migrate(old_db, commit=True)
        self.assertEqual(completed["written"], 1)
        self.assertEqual(self.db.get_messages("legacy1")[0]["content"], "legacy content")
        self.assertTrue(old_db.exists())
        self.assertFalse(list(Path(self.temp.name).rglob("*.db")))

    def test_gateway_session_routing_is_inside_vault(self):
        from gateway.config import GatewayConfig
        from gateway.session import SessionStore
        gateway = SessionStore(Path(self.temp.name) / "outside", GatewayConfig())
        self.assertTrue(gateway.sessions_dir.is_relative_to(Path(self.temp.name)))
        self.assertEqual(gateway._db.root, self.db.root)
        gateway._db.create_session("gw_a", "discord")
        self.assertTrue((self.db.root / "gw_a" / "metadata.json").exists())

    def test_discord_thread_tracker_is_inside_vault(self):
        from gateway.platforms.helpers import ThreadParticipationTracker
        tracker = ThreadParticipationTracker("discord")
        tracker.mark("channel_123")
        target = Path(self.temp.name) / "06_Automation" / "Hermes" / "GatewayRouting" / "discord_threads.json"
        self.assertTrue(target.is_file())
        self.assertIn("channel_123", ThreadParticipationTracker("discord"))

    def test_sqlite_never_generated(self):
        self.db.create_session("test_s", "discord")
        self.db.append_message("test_s", "user", "persist")
        self.assertFalse(list(Path(self.temp.name).rglob("*.db")))
        self.assertFalse(list(Path(self.temp.name).rglob("*.sqlite*")))


if __name__ == "__main__":
    unittest.main()
