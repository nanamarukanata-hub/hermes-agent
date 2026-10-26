"""Regression guards for SQLite-free VaultDir persistence paths."""

import json
import os

import pytest

from hermes_hybrid.vault_kv import VaultDir


def test_symlinked_lock_cannot_modify_file_outside_vault(tmp_path):
    outside = tmp_path / "outside-lock"
    outside.write_text("untouched", encoding="utf-8")
    vault = VaultDir(tmp_path / "vault", "06_Automation", "Hermes", "RetainQueue")
    vault.lock_file.symlink_to(outside)

    with pytest.raises(OSError):
        vault.write("item", {"value": 1})
    assert outside.read_text(encoding="utf-8") == "untouched"
    assert not vault.path("item").exists()


def test_precreated_pid_temp_symlinks_are_never_followed(tmp_path):
    outside = tmp_path / "outside-data"
    outside.write_text("untouched", encoding="utf-8")
    vault = VaultDir(tmp_path / "vault", "06_Automation", "Hermes", "RetainQueue")
    for target in ("item.json", "sequence.json"):
        (vault.dir / f".{target}.tmp-{os.getpid()}").symlink_to(outside)

    vault.write("item", {"value": "persisted"})
    assert vault.read("item") == {"value": "persisted"}
    assert vault.next_id("sequence") == 1
    assert vault.next_id("sequence") == 2
    assert outside.read_text(encoding="utf-8") == "untouched"


def test_symlinked_counter_cannot_be_read_or_overwritten(tmp_path):
    outside = tmp_path / "outside-counter"
    original = json.dumps({"next": 999})
    outside.write_text(original, encoding="utf-8")
    vault = VaultDir(tmp_path / "vault", "06_Automation", "Hermes", "RetainQueue")
    vault.path("sequence").symlink_to(outside)

    with pytest.raises(ValueError, match="symlink"):
        vault.next_id("sequence")
    assert outside.read_text(encoding="utf-8") == original


@pytest.mark.parametrize("counter_name", ["..", ".", "../outside", "bad/name", "bad\\name", ""])
def test_counter_name_cannot_escape_vault(tmp_path, counter_name):
    vault = VaultDir(tmp_path / "vault", "06_Automation", "Hermes", "RetainQueue")
    with pytest.raises(ValueError, match="invalid identifier"):
        vault.next_id(counter_name)


def test_counter_survives_reopening_vault_directory(tmp_path):
    root = tmp_path / "vault"
    vault = VaultDir(root, "06_Automation", "Hermes", "RetainQueue")
    assert vault.next_id("sequence") == 1
    reopened = VaultDir(root, "06_Automation", "Hermes", "RetainQueue")
    assert reopened.next_id("sequence") == 2
