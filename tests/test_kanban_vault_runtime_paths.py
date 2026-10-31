"""Kanban worker logs and scratch workspaces must never persist outside Vault."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hermes_cli import kanban_db as kb


class KanbanVaultRuntimePathsTests(unittest.TestCase):
    def test_default_and_named_board_paths_are_vault_only(self):
        with tempfile.TemporaryDirectory() as root:
            vault = Path(root) / "vault"
            legacy = Path(root) / "legacy"
            vault.mkdir()
            with patch.dict(os.environ, {
                "HERMES_OBSIDIAN_VAULT": str(vault),
                "HERMES_KANBAN_HOME": str(legacy),
            }, clear=True):
                base = vault / "06_Automation" / "Hermes" / "Kanban"
                for board in ("default", "rp-bot"):
                    with self.subTest(board=board):
                        self.assertEqual(kb.workspaces_root(board), base / board / "workspaces")
                        self.assertEqual(kb.worker_logs_dir(board), base / board / "logs")
                self.assertFalse(legacy.exists())

    def test_missing_vault_rejects_paths_even_with_legacy_home(self):
        with tempfile.TemporaryDirectory() as root:
            with patch.dict(os.environ, {"HERMES_KANBAN_HOME": root}, clear=True):
                for resolver in (kb.workspaces_root, kb.worker_logs_dir):
                    with self.subTest(resolver=resolver.__name__):
                        with self.assertRaisesRegex(ValueError, "HERMES_OBSIDIAN_VAULT is required"):
                            resolver("default")

    def test_external_workspace_override_is_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            vault = Path(root) / "vault"
            vault.mkdir()
            outside = Path(root) / "outside-workspaces"
            with patch.dict(os.environ, {
                "HERMES_OBSIDIAN_VAULT": str(vault),
                "HERMES_KANBAN_WORKSPACES_ROOT": str(outside),
            }, clear=True):
                with self.assertRaisesRegex(ValueError, "escapes HERMES_OBSIDIAN_VAULT"):
                    kb.workspaces_root("default")
                self.assertFalse(outside.exists())

    def test_internal_workspace_override_is_allowed(self):
        with tempfile.TemporaryDirectory() as root:
            vault = Path(root) / "vault"
            vault.mkdir()
            override = vault / "scratch"
            with patch.dict(os.environ, {
                "HERMES_OBSIDIAN_VAULT": str(vault),
                "HERMES_KANBAN_WORKSPACES_ROOT": str(override),
            }, clear=True):
                self.assertEqual(kb.workspaces_root("default"), override)

    def test_workspace_and_log_symlink_escape_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            vault = Path(root) / "vault"
            vault.mkdir()
            outside = Path(root) / "outside"
            with patch.dict(os.environ, {"HERMES_OBSIDIAN_VAULT": str(vault)}, clear=True):
                board = kb.kanban_store_dir(board="default")
                board.mkdir(parents=True)
                for name, resolver in (("workspaces", kb.workspaces_root), ("logs", kb.worker_logs_dir)):
                    link = board / name
                    link.symlink_to(outside, target_is_directory=True)
                    with self.subTest(name=name):
                        with self.assertRaisesRegex(ValueError, "escapes HERMES_OBSIDIAN_VAULT"):
                            resolver("default")
                    link.unlink()
                self.assertFalse(outside.exists())


if __name__ == "__main__":
    unittest.main()
