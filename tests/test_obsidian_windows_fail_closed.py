"""Windows must fail closed until native secure Vault I/O is implemented."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hermes_obsidian_state import SessionDB


class WindowsVaultGuardTests(unittest.TestCase):
    def test_missing_fcntl_rejects_sessiondb_before_creating_files(self):
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            with patch("hermes_obsidian_state.fcntl", None):
                with self.assertRaisesRegex(
                    RuntimeError, "Windows Obsidian SessionDB is not yet supported"
                ):
                    SessionDB(vault_path=vault)
            self.assertEqual(list(vault.iterdir()), [])

    def test_missing_fcntl_rejects_legacy_db_path_without_sqlite_fallback(self):
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            with patch("hermes_obsidian_state.fcntl", None):
                with self.assertRaisesRegex(RuntimeError, "SQLite fallback is disabled"):
                    SessionDB(db_path=vault / "legacy.db", vault_path=vault)
            self.assertFalse((vault / "legacy.db").exists())
            self.assertEqual(list(vault.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
