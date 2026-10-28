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

    def test_rich_session_preview_truncation_boundary(self):
        for length in (60, 61, 100):
            with self.subTest(length=length):
                sid = f"preview_{length}"
                self.db.create_session(sid, "cli")
                self.db.append_message(sid, "user", "A" * length)
                reopened = SessionDB()
                sessions = reopened.list_sessions_rich(limit=20)
                preview = next(row["preview"] for row in sessions if row["id"] == sid)
                expected = "A" * 60 + ("..." if length > 60 else "")
                self.assertEqual(preview, expected)

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

    def test_branch_lineage_title_uses_hash_suffix_without_legacy_collisions(self):
        self.db.create_session("base", "cli")
        self.db.set_session_title("base", "My Coding Session")
        self.assertEqual(
            self.db.get_next_title_in_lineage("My Coding Session"),
            "My Coding Session #2",
        )

        self.db.create_session("old_branch", "cli")
        self.db.set_session_title("old_branch", "My Coding Session (2)")
        self.assertEqual(
            self.db.get_next_title_in_lineage("My Coding Session"),
            "My Coding Session #3",
        )

        self.db.create_session("new_branch", "cli")
        self.db.set_session_title("new_branch", "My Coding Session #3")
        self.assertEqual(
            self.db.get_next_title_in_lineage("My Coding Session"),
            "My Coding Session #4",
        )

    def test_resume_title_prefers_highest_numbered_lineage(self):
        self.db.create_session("root", "discord")
        self.db.set_session_title("root", "My Project")
        self.db.create_session("branch_2", "discord")
        self.db.set_session_title("branch_2", "My Project #2")
        self.db.create_session("branch_10", "discord")
        self.db.set_session_title("branch_10", "My Project #10")
        self.db.create_session("legacy_3", "discord")
        self.db.set_session_title("legacy_3", "My Project (3)")
        self.db.create_session("unrelated", "discord")
        self.db.set_session_title("unrelated", "My Project Extra #99")

        # Numeric lineage takes precedence over lexical and last-active order.
        self.assertEqual(self.db.resolve_session_by_title("My Project"), "branch_10")
        # Explicitly requesting a numbered session must not silently advance.
        self.assertEqual(self.db.resolve_session_by_title("My Project #2"), "branch_2")
        self.assertEqual(self.db.resolve_session_by_title("My Project (3)"), "legacy_3")
        self.assertEqual(self.db.resolve_session_by_title("My Project Extra"), "unrelated")
        self.assertIsNone(self.db.resolve_session_by_title("Missing"))

    def test_resume_title_can_find_lineage_without_original_root(self):
        self.db.create_session("branch_2", "discord")
        self.db.set_session_title("branch_2", "Archived Project #2")
        self.db.create_session("branch_3", "discord")
        self.db.set_session_title("branch_3", "Archived Project (3)")
        self.assertEqual(self.db.resolve_session_by_title("Archived Project"), "branch_3")

    def test_title_sanitization_preserves_whitespace_and_strips_controls(self):
        self.assertEqual(SessionDB.sanitize_title("hello\t\nworld"), "hello world")
        self.assertEqual(SessionDB.sanitize_title("hello\x00world"), "helloworld")
        self.assertEqual(SessionDB.sanitize_title("hello\u200bworld"), "helloworld")
        self.assertIsNone(SessionDB.sanitize_title("\x00\x01"))

    def test_oversized_title_rejected_without_mutating_vault(self):
        self.db.create_session("title_limit", "discord")
        self.db.set_session_title("title_limit", "Original")
        self.assertEqual(SessionDB.sanitize_title("A" * 100), "A" * 100)
        with self.assertRaisesRegex(ValueError, "too long"):
            SessionDB.sanitize_title("A" * 101)
        with self.assertRaisesRegex(ValueError, "too long"):
            self.db.set_session_title("title_limit", "B" * 150)
        reopened = SessionDB()
        self.assertEqual(reopened.get_session_title("title_limit"), "Original")

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

        # A byte-for-byte-equivalent retry is idempotent.
        retry = migrate(old_db, commit=True)
        self.assertEqual(retry["already_migrated"], 1)
        self.assertEqual(retry["written"], 0)

        # Matching messages and source do NOT prove the full session matches.
        # Preserve changes made after import instead of silently claiming
        # that a stale legacy record is already migrated.
        self.db._edit("legacy1", lambda item: item.update(model="newer-model"))
        with self.assertRaisesRegex(RuntimeError, "Conflicting Vault session"):
            migrate(old_db)
        with self.assertRaisesRegex(RuntimeError, "Conflicting Vault session"):
            migrate(old_db, commit=True)
        self.assertEqual(self.db.get_session("legacy1")["model"], "newer-model")
        self.assertTrue(old_db.exists())

    def test_gateway_session_routing_is_inside_vault(self):
        from gateway.config import GatewayConfig
        from gateway.session import SessionStore
        gateway = SessionStore(Path(self.temp.name) / "outside", GatewayConfig())
        self.assertTrue(gateway.sessions_dir.is_relative_to(Path(self.temp.name)))
        self.assertEqual(gateway._db.root, self.db.root)
        gateway._db.create_session("gw_a", "discord")
        self.assertTrue((self.db.root / "gw_a" / "metadata.json").exists())

    def test_discord_thread_tracker_is_inside_vault(self):
        # Import the helper source directly: gateway.platforms.__init__ eagerly
        # loads unrelated network adapters (e.g. Yuanbao/httpx). This storage
        # regression must not require every optional platform dependency.
        import importlib.util
        helper_file = Path(__file__).resolve().parents[1] / "gateway" / "platforms" / "helpers.py"
        spec = importlib.util.spec_from_file_location("_obsidian_test_gateway_helpers", helper_file)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        helpers = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helpers)
        ThreadParticipationTracker = helpers.ThreadParticipationTracker
        tracker = ThreadParticipationTracker("discord")
        tracker.mark("channel_123")
        target = Path(self.temp.name) / "06_Automation" / "Hermes" / "GatewayRouting" / "discord_threads.json"
        self.assertTrue(target.is_file())
        self.assertIn("channel_123", ThreadParticipationTracker("discord"))

    def test_thread_tracker_rejects_symlinked_json_file(self):
        # A valid GatewayRouting directory is insufficient if its state file
        # is a symlink to arbitrary content outside the Vault.
        import importlib.util
        helper_file = Path(__file__).resolve().parents[1] / "gateway" / "platforms" / "helpers.py"
        spec = importlib.util.spec_from_file_location("_thread_symlink_test_helpers", helper_file)
        helpers = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helpers)
        state = Path(self.temp.name) / "06_Automation" / "Hermes" / "GatewayRouting" / "discord_threads.json"
        state.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory() as outside:
            external = Path(outside) / "external.json"
            external.write_text('["private_thread"]', encoding="utf-8")
            state.symlink_to(external)
            with self.assertRaises(RuntimeError):
                helpers.ThreadParticipationTracker("discord")
            self.assertEqual(external.read_text(encoding="utf-8"), '["private_thread"]')
            external.unlink()
            # Broken symlinks must not be interpreted as an empty state file.
            with self.assertRaises(RuntimeError):
                helpers.ThreadParticipationTracker("discord")

    def test_thread_tracker_platform_cannot_escape_vault(self):
        import importlib.util
        helper_file = Path(__file__).resolve().parents[1] / "gateway" / "platforms" / "helpers.py"
        spec = importlib.util.spec_from_file_location("_thread_path_test_helpers", helper_file)
        helpers = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helpers)
        for name in ("../escape", "discord/../../escape", ".", "..", "", "bad\\\\name"):
            with self.subTest(platform=name), self.assertRaises(ValueError):
                helpers.ThreadParticipationTracker(name)

    def test_dot_segment_ids_cannot_write_or_delete_parent_directories(self):
        # Both names match the filename regex but must never resolve to
        # Sessions/ or its parent, especially in delete_session().
        sentinel = self.db.root.parent / "parent-sentinel.txt"
        sentinel.write_text("untouched", encoding="utf-8")
        for session_id in (".", ".."):
            with self.subTest(session_id=session_id):
                with self.assertRaises(ValueError):
                    self.db.create_session(session_id, "discord")
                with self.assertRaises(ValueError):
                    self.db.delete_session(session_id)
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "untouched")
        self.assertTrue(self.db.root.is_dir())

    def test_symlink_session_folder_cannot_read_write_or_delete_target(self):
        # A symlink inside the Vault must not redirect storage outside it.
        with tempfile.TemporaryDirectory() as outside:
            target = Path(outside)
            sentinel = target / "keep.txt"
            sentinel.write_text("untouched", encoding="utf-8")
            (target / "metadata.json").write_text(
                '{"id": "linked", "messages": []}', encoding="utf-8")
            (self.db.root / "linked").symlink_to(target, target_is_directory=True)
            with self.assertRaises(RuntimeError):
                self.db.get_session("linked")
            with self.assertRaises(RuntimeError):
                self.db.create_session("linked", "discord")
            with self.assertRaises(RuntimeError):
                self.db.delete_session("linked")
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "untouched")

    def test_symlinked_metadata_cannot_read_outside_vault(self):
        # Folder-level symlink checks are insufficient: metadata.json may
        # itself point to a file outside the Vault (including broken links).
        with tempfile.TemporaryDirectory() as outside:
            external = Path(outside) / "external.json"
            external.write_text(
                '{"id": "linked_meta", "source": "discord", "messages": []}',
                encoding="utf-8",
            )
            folder = self.db.root / "linked_meta"
            folder.mkdir()
            (folder / "metadata.json").symlink_to(external)
            with self.assertRaises(RuntimeError):
                self.db.get_session("linked_meta")
            with self.assertRaises(RuntimeError):
                self.db.create_session("linked_meta", "discord")
            with self.assertRaises(RuntimeError):
                self.db.delete_session("linked_meta")
            self.assertIn('"linked_meta"', external.read_text(encoding="utf-8"))

            # A dangling symlink must not be treated as a missing session.
            external.unlink()
            with self.assertRaises(RuntimeError):
                self.db.get_session("linked_meta")
            with self.assertRaises(RuntimeError):
                self.db.create_session("linked_meta", "discord")

    def test_metadata_directory_cannot_be_treated_as_file(self):
        folder = self.db.root / "bad_meta"
        (folder / "metadata.json").mkdir(parents=True)
        with self.assertRaises(RuntimeError):
            self.db.get_session("bad_meta")

    def test_state_meta_symlink_and_directory_cannot_escape_vault(self):
        with tempfile.TemporaryDirectory() as outside:
            external = Path(outside) / "state.json"
            original = '{"private": "outside"}'
            external.write_text(original, encoding="utf-8")
            meta = self.db.root / "state_meta.json"
            meta.symlink_to(external)
            with self.assertRaises(RuntimeError):
                self.db.get_meta("private")
            with self.assertRaises(RuntimeError):
                self.db.set_meta("new", "value")
            self.assertEqual(external.read_text(encoding="utf-8"), original)

            external.unlink()  # Dangling links must not become empty state.
            with self.assertRaises(RuntimeError):
                self.db.get_meta("private")
            with self.assertRaises(RuntimeError):
                self.db.set_meta("new", "value")
            meta.unlink()
            meta.mkdir()
            with self.assertRaises(RuntimeError):
                self.db.get_meta("private")
            with self.assertRaises(RuntimeError):
                self.db.set_meta("new", "value")

    def test_symlinked_vault_parent_does_not_create_outside_directories(self):
        # Validate before mkdir(parents=True): an attacker-controlled Vault
        # parent link must not cause even temporary writes outside the Vault.
        with tempfile.TemporaryDirectory() as vault, tempfile.TemporaryDirectory() as outside:
            (Path(vault) / "06_Automation").symlink_to(
                Path(outside), target_is_directory=True
            )
            with patch.dict(os.environ, {"HERMES_OBSIDIAN_VAULT": vault}):
                with self.assertRaises(RuntimeError):
                    SessionDB()
            self.assertFalse((Path(outside) / "Hermes").exists())

    def test_symlinked_writer_lockfile_is_rejected(self):
        with tempfile.TemporaryDirectory() as outside:
            external = Path(outside) / "lock"
            external.write_text("do not modify", encoding="utf-8")
            self.db._lockfile.symlink_to(external)
            with self.assertRaises(RuntimeError):
                self.db.create_session("blocked", "discord")
            self.assertEqual(external.read_text(encoding="utf-8"), "do not modify")
            self.assertFalse((self.db.root / "blocked").exists())

    def test_global_state_meta_normal_roundtrip(self):
        self.assertIsNone(self.db.get_meta("last_auto_prune"))
        self.db.set_meta("last_auto_prune", "123")
        self.assertEqual(SessionDB().get_meta("last_auto_prune"), "123")

    def test_sqlite_never_generated(self):
        self.db.create_session("test_s", "discord")
        self.db.append_message("test_s", "user", "persist")
        self.assertFalse(list(Path(self.temp.name).rglob("*.db")))
        self.assertFalse(list(Path(self.temp.name).rglob("*.sqlite*")))


    def test_finalize_orphaned_compression_preserves_messages_and_is_idempotent(self):
        import time

        self.db.create_session("parent", "cli")
        self.db.end_session("parent", "compression")
        self.db.create_session(
            "stale_child", "cli", parent_session_id="parent"
        )
        self.db.append_message("stale_child", "user", "Keep this conversation")
        self.db._edit(
            "stale_child",
            lambda item: item.update(started_at=time.time() - 8 * 86400),
        )
        original_messages = self.db.get_messages("stale_child")
        transcript = (
            self.db.root / "stale_child" / "Transcript.md"
        ).read_text(encoding="utf-8")

        self.assertEqual(self.db.finalize_orphaned_compression_sessions(), 1)
        ended = self.db.get_session("stale_child")
        self.assertEqual(ended["end_reason"], "orphaned_compression")
        self.assertIsInstance(ended["ended_at"], float)
        self.assertEqual(self.db.get_messages("stale_child"), original_messages)
        self.assertEqual(
            (self.db.root / "stale_child" / "Transcript.md").read_text(
                encoding="utf-8"
            ),
            transcript,
        )
        self.assertEqual(SessionDB().finalize_orphaned_compression_sessions(), 0)

    def test_finalize_orphaned_compression_excludes_nonmatching_sessions(self):
        import time

        self.db.create_session("compressed_parent", "cli")
        self.db.end_session("compressed_parent", "compression")
        self.db.create_session("normal_parent", "cli")
        self.db.end_session("normal_parent", "user_exit")
        self.db.create_session("open_parent", "cli")
        self.db._edit(
            "open_parent",
            lambda item: item.update(end_reason="compression"),
        )

        cases = (
            ("recent", "compressed_parent", True, False, False),
            ("active", "compressed_parent", True, True, True),
            ("empty", "compressed_parent", False, True, False),
            ("wrong_parent", "normal_parent", True, True, False),
            ("open_parent_child", "open_parent", True, True, False),
            ("already_ended", "compressed_parent", True, True, False),
        )
        for sid, parent, has_message, old, api_called in cases:
            self.db.create_session(sid, "cli", parent_session_id=parent)
            if has_message:
                self.db.append_message(sid, "user", sid)
            if old:
                self.db._edit(
                    sid, lambda item: item.update(
                        started_at=time.time() - 8 * 86400
                    )
                )
            if api_called:
                self.db.update_token_counts(sid, api_call_count=1)
        self.db.end_session("already_ended", "user_exit")

        self.assertEqual(self.db.finalize_orphaned_compression_sessions(), 0)
        for sid, _, _, _, _ in cases:
            session = self.db.get_session(sid)
            self.assertNotEqual(session["end_reason"], "orphaned_compression")


if __name__ == "__main__":
    unittest.main()
