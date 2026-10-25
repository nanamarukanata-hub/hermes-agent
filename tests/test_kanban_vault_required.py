"""Kanban persistence must fail closed without a configured Obsidian Vault."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hermes_cli import kanban_db


class VaultRequiredTests(unittest.TestCase):
    def test_missing_vault_rejects_legacy_fallbacks(self):
        with tempfile.TemporaryDirectory() as home:
            with patch.dict(os.environ, {
                "HERMES_KANBAN_HOME": home,
                "HERMES_KANBAN_DB": str(Path(home) / "kanban.db"),
            }, clear=True):
                for kwargs in ({}, {"board": "default"}, {"db_path": Path(home) / "kanban.db"}):
                    with self.subTest(kwargs=kwargs):
                        with self.assertRaisesRegex(ValueError, "HERMES_OBSIDIAN_VAULT is required"):
                            kanban_db.kanban_store_dir(**kwargs)
                self.assertFalse((Path(home) / "kanban.db").exists())

    def test_vault_store_stays_inside_vault(self):
        with tempfile.TemporaryDirectory() as vault:
            with patch.dict(os.environ, {"HERMES_OBSIDIAN_VAULT": vault}, clear=True):
                target = kanban_db.kanban_store_dir(board="default")
                self.assertEqual(target, Path(vault) / "06_Automation" / "Hermes" / "Kanban" / "default")


if __name__ == "__main__":
    unittest.main()
