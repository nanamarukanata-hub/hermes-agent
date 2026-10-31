"""Regression tests: Kanban board metadata and selector stay inside the Vault."""
import json
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


    def test_selector_open_time_symlink_swap_does_not_overwrite_outside(self):
        with tempfile.TemporaryDirectory() as root:
            vault = Path(root) / "vault"
            outside = Path(root) / "outside-selector"
            vault.mkdir()
            outside.write_text("untouched", encoding="utf-8")
            with patch.dict(os.environ, {"HERMES_OBSIDIAN_VAULT": str(vault)}, clear=True):
                original_replace = os.replace

                def swap_before_replace(src, dst):
                    Path(dst).symlink_to(outside)
                    return original_replace(src, dst)

                with patch.object(kb.os, "replace", side_effect=swap_before_replace):
                    selector = kb.set_current_board("default")
                self.assertEqual(outside.read_text(encoding="utf-8"), "untouched")
                self.assertFalse(selector.is_symlink())
                self.assertEqual(selector.read_text(encoding="utf-8"), "default\n")

    def test_metadata_open_time_symlink_swap_does_not_overwrite_outside(self):
        with tempfile.TemporaryDirectory() as root:
            vault = Path(root) / "vault"
            outside = Path(root) / "outside-board.json"
            vault.mkdir()
            outside.write_text("untouched", encoding="utf-8")
            with patch.dict(os.environ, {"HERMES_OBSIDIAN_VAULT": str(vault)}, clear=True):
                original_replace = os.replace

                def swap_before_replace(src, dst):
                    Path(dst).symlink_to(outside)
                    return original_replace(src, dst)

                with patch.object(kb.os, "replace", side_effect=swap_before_replace):
                    meta = kb.write_board_metadata("test", name="Protected")
                path = kb.board_metadata_path("test")
                self.assertEqual(outside.read_text(encoding="utf-8"), "untouched")
                self.assertFalse(path.is_symlink())
                self.assertEqual(meta["name"], "Protected")
                self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["name"], "Protected")

    def test_failed_atomic_replace_cleans_temporary_file(self):
        with tempfile.TemporaryDirectory() as root:
            vault = Path(root) / "vault"
            vault.mkdir()
            with patch.dict(os.environ, {"HERMES_OBSIDIAN_VAULT": str(vault)}, clear=True):
                selector = kb.current_board_path()
                with patch.object(kb.os, "replace", side_effect=OSError("injected failure")):
                    with self.assertRaisesRegex(OSError, "injected failure"):
                        kb.set_current_board("default")
                self.assertFalse(selector.exists())
                self.assertEqual(list(selector.parent.glob("..current-board.tmp-*")), [])


if __name__ == "__main__":
    unittest.main()
