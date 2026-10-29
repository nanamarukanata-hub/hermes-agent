"""Regressions for the non-destructive SQLite-to-Vault export publisher."""

import json
import os
from unittest.mock import patch

import pytest

from scripts import migrate_sqlite_to_vault as migration


def test_preplanted_pid_temp_symlink_cannot_clobber_external(tmp_path):
    vault = tmp_path / "vault"
    dest = vault / "06_Automation" / "Hermes" / "RetainQueue" / "1.json"
    dest.parent.mkdir(parents=True)
    external = tmp_path / "external.txt"
    external.write_text("KEEP", encoding="utf-8")
    planted = dest.with_name(f".{dest.name}.tmp-{os.getpid()}")
    planted.symlink_to(external)

    migration._write_json(vault, dest, {"user_id": "u1"}, force=False)

    assert json.loads(dest.read_text(encoding="utf-8")) == {"user_id": "u1"}
    assert external.read_text(encoding="utf-8") == "KEEP"
    assert planted.is_symlink()


def test_failed_publish_removes_unique_temp_file(tmp_path):
    vault = tmp_path / "vault"
    dest = vault / "06_Automation" / "Hermes" / "RetainQueue" / "1.json"
    with patch.object(migration.os, "link", side_effect=OSError("simulated publish failure")):
        with pytest.raises(OSError, match="simulated publish failure"):
            migration._write_json(vault, dest, {"user_id": "u1"}, force=False)

    assert not dest.exists()
    assert list(dest.parent.glob(".1.json.tmp-*")) == []
