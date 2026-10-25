"""Regression tests: Kanban board metadata and selector stay inside the Vault."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hermes_cli import kanban_db as kb


class KanbanVaultMetadataTests(unittest.TestCase):
    def test_board_metadata_and_selection_persist_only_in_vault(self):
        with tempfile.TemporaryDirectory() as root:
            vault = Path(root) / "vault"
            legacy = Path(root) / "legacy"
            vault.mkdir()
            with patch.dict(os.environ, {
                "HERMES_OBSIDIAN_VAULT": str(vault),
                "HERMES_KANBAN_HOME": str(legacy),
            }, clear=True):
                meta = kb.write_board_metadata("my-board", name="My Board")
                self.assertEqual(meta["name"], "My Board")
                path = kb.board_metadata_path("my-board")
                self.assertEqual(path, vault / "06_Automation" / "Hermes" / "Kanban" / "my-board" / "board.json")
                self.assertTrue(path.is_file())
                selector = kb.set_current_board("my-board")
                self.assertTrue(selector.is_relative_to(vault))
                self.assertEqual(kb.get_current_board(), "my-board")
                self.assertIn("my-board", [x["slug"] for x in kb.list_boards()])
                self.assertFalse(legacy.exists(), "board metadata must not create a legacy home")
                kb.clear_current_board()
                self.assertFalse(selector.exists())

    def test_vault_required_for_metadata_and_selector(self):
        with tempfile.TemporaryDirectory() as root:
            with patch.dict(os.environ, {"HERMES_KANBAN_HOME": root}, clear=True):
                for fn in (
                    lambda: kb.board_metadata_path("test"),
                    lambda: kb.set_current_board("test"),
                    lambda: kb.write_board_metadata("test", name="Test"),
                ):
                    with self.assertRaisesRegex(ValueError, "HERMES_OBSIDIAN_VAULT is required"):
                        fn()
                self.assertFalse((Path(root) / "kanban").exists())

    def test_metadata_symlink_escape_is_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            vault = Path(root) / "vault"
            outside = Path(root) / "outside.json"
            vault.mkdir()
            with patch.dict(os.environ, {"HERMES_OBSIDIAN_VAULT": str(vault)}, clear=True):
                directory = kb.kanban_store_dir(board="test")
                directory.mkdir(parents=True)
                (directory / "board.json").symlink_to(outside)
                with self.assertRaisesRegex(ValueError, "escapes HERMES_OBSIDIAN_VAULT"):
                    kb.write_board_metadata("test", name="No Escape")
                self.assertFalse(outside.exists())

    def test_selector_symlink_escape_is_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            vault = Path(root) / "vault"
            outside = Path(root) / "outside-selector"
            vault.mkdir()
            with patch.dict(os.environ, {"HERMES_OBSIDIAN_VAULT": str(vault)}, clear=True):
                selector = kb.current_board_path()
                selector.parent.mkdir(parents=True)
                selector.symlink_to(outside)
                with self.assertRaisesRegex(ValueError, "escapes HERMES_OBSIDIAN_VAULT"):
                    kb.set_current_board("default")
                self.assertFalse(outside.exists())


if __name__ == "__main__":
    unittest.main()
