"""Fail-closed guards for the read-only legacy Kanban -> Obsidian export."""

import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "migrate_kanban_to_vault.py"


class KanbanMigrationGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = self.root / "kanban.db"
        self.vault = self.root / "vault"
        with sqlite3.connect(self.db) as conn:
            conn.execute("CREATE TABLE tasks (id TEXT PRIMARY KEY, title TEXT)")
            conn.execute("INSERT INTO tasks VALUES ('t1', 'Task')")

    def run_cli(self, *extra):
        return subprocess.run(
            [sys.executable, str(SCRIPT), str(self.db), str(self.vault), *extra],
            capture_output=True, text=True, check=False,
        )

    def test_export_is_read_only_and_second_run_refused(self):
        before = self.db.read_bytes()
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        task_file = self.vault / "06_Automation/Hermes/Kanban/default/t1.json"
        self.assertEqual(json.loads(task_file.read_text())["title"], "Task")
        self.assertEqual(self.db.read_bytes(), before)
        self.assertEqual(self.run_cli().returncode, 2)

    def test_rejects_board_traversal_before_creating_vault(self):
        result = self.run_cli("--board", "../../outside")
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertFalse(self.vault.exists())

    def test_rejects_unsafe_id_without_partial_export(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute("INSERT INTO tasks VALUES ('../escape', 'Bad')")
        result = self.run_cli()
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertFalse(self.vault.exists())

    def test_rejects_task_id_colliding_with_metadata_file(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute("INSERT INTO tasks VALUES ('links', 'Bad')")
        result = self.run_cli()
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertFalse(self.vault.exists())

    def test_refuses_existing_unmarked_file_without_force(self):
        out = self.vault / "06_Automation/Hermes/Kanban/default"
        out.mkdir(parents=True)
        (out / "t1.json").write_text("KEEP")
        result = self.run_cli()
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual((out / "t1.json").read_text(), "KEEP")
        self.assertFalse((out / ".migrated").exists())

    def test_rejects_symlink_redirect_outside_vault(self):
        out = self.vault / "06_Automation/Hermes/Kanban"
        out.mkdir(parents=True)
        external = self.root / "external"
        external.mkdir()
        (out / "default").symlink_to(external, target_is_directory=True)
        result = self.run_cli()
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertFalse(list(external.iterdir()))


    def test_missing_required_tasks_table_fails_closed(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute("DROP TABLE tasks")
        result = self.run_cli()
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("no such table: tasks", result.stderr)
        self.assertFalse(self.vault.exists())

    def test_broken_optional_table_fails_instead_of_silent_data_loss(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute("CREATE VIEW task_links AS SELECT * FROM missing_source")
        result = self.run_cli()
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("no such table", result.stderr)
        self.assertFalse(self.vault.exists())

    def test_read_only_uri_handles_reserved_filename_characters(self):
        renamed = self.root / "kanban #?%.db"
        self.db.rename(renamed)
        self.db = renamed
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        task_file = self.vault / "06_Automation/Hermes/Kanban/default/t1.json"
        self.assertEqual(json.loads(task_file.read_text())["title"], "Task")


if __name__ == "__main__":
    unittest.main()
