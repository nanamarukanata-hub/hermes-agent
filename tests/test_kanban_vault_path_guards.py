"""Regression guards for Vault-only Kanban path resolution.

These tests use temporary directories and never access the real Vault.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hermes_cli import kanban_db as kb


class VaultKanbanPathTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.vault = self.root / "vault"
        self.vault.mkdir()
        env = patch.dict(os.environ, {
            "HERMES_OBSIDIAN_VAULT": str(self.vault),
            "HERMES_KANBAN_HOME": str(self.root / "legacy-home"),
            "HERMES_KANBAN_DB": str(self.root / "legacy-home" / "kanban.db"),
        })
        env.start()
        self.addCleanup(env.stop)
        # Avoid inheriting an unrelated board selection from the caller.
        self.board_env = patch.dict(os.environ, {"HERMES_KANBAN_BOARD": "default"})
        self.board_env.start()
        self.addCleanup(self.board_env.stop)

    def test_configured_vault_wins_over_legacy_environment_pin(self):
        store = kb.kanban_store_dir()
        self.assertEqual(
            store, self.vault / "06_Automation" / "Hermes" / "Kanban" / "default"
        )
        kb.init_db()
        self.assertTrue((store / "counters.json").exists())
        self.assertFalse((self.root / "legacy-home").exists())

    def test_explicit_db_path_cannot_bypass_vault(self):
        escaped = self.root / "outside" / "board.db"
        with self.assertRaisesRegex(ValueError, "db_path is forbidden"):
            kb.connect(db_path=escaped)
        self.assertFalse(escaped.parent.exists())

    def test_missing_vault_is_not_created_implicitly(self):
        missing = self.root / "missing"
        with patch.dict(os.environ, {"HERMES_OBSIDIAN_VAULT": str(missing)}):
            with self.assertRaisesRegex(ValueError, "existing directory"):
                kb.init_db()
        self.assertFalse(missing.exists())

    def test_intermediate_symlink_cannot_redirect_writes(self):
        outside = self.root / "outside"
        outside.mkdir()
        (self.vault / "06_Automation").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "escapes"):
            kb.init_db()
        self.assertEqual(list(outside.iterdir()), [])

    def test_board_slug_cannot_traverse_vault(self):
        with self.assertRaises(ValueError):
            kb.kanban_store_dir(board="../../outside")
        self.assertEqual(list(self.vault.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
