"""Regression coverage for Vault-only curated-memory diagnostics."""

import os

import pytest

from hermes_cli.doctor import _check_vault_curated_memory


def _inspect(vault, home):
    ok, info, warnings = [], [], []
    _check_vault_curated_memory(vault, home, ok.append, info.append, warnings.append)
    return ok, info, warnings


def test_doctor_inspects_vault_memory_and_preserves_legacy_files(tmp_path):
    home = tmp_path / "home"
    legacy = home / "memories"
    legacy.mkdir(parents=True)
    old_file = legacy / "MEMORY.md"
    old_file.write_text("legacy content", encoding="utf-8")
    vault = tmp_path / "vault"
    curated = vault / "06_Automation" / "Hermes" / "CuratedMemory"
    curated.mkdir(parents=True)
    (curated / "MEMORY.md").write_text("new memory", encoding="utf-8")
    (curated / "USER.md").write_text("new user", encoding="utf-8")

    before = old_file.read_bytes()
    ok, info, warnings = _inspect(str(vault), home)

    assert any("Vault MEMORY.md exists" in message for message in ok)
    assert any("Vault USER.md exists" in message for message in ok)
    assert any("Legacy HERMES_HOME/memories" in message for message in info)
    assert not warnings
    assert old_file.read_bytes() == before


def test_doctor_missing_vault_does_not_create_memory_directories(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    missing_vault = tmp_path / "missing-vault"

    ok, info, warnings = _inspect(str(missing_vault), home)

    assert not ok
    assert any("Vault missing or unsafe" in message for message in warnings)
    assert not missing_vault.exists()
    assert not (home / "memories").exists()


def test_doctor_unconfigured_vault_fails_closed_without_legacy_fallback(tmp_path):
    home = tmp_path / "home"
    home.mkdir()

    ok, info, warnings = _inspect("", home)

    assert not ok
    assert any("HERMES_OBSIDIAN_VAULT is required" in message for message in warnings)
    assert not (home / "memories").exists()


def test_doctor_existing_vault_does_not_initialize_curated_memory(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    vault = tmp_path / "vault"
    vault.mkdir()

    ok, info, warnings = _inspect(str(vault), home)

    assert not ok and not warnings
    assert any("CuratedMemory not created yet" in message for message in info)
    assert not (vault / "06_Automation").exists()


@pytest.mark.skipif(os.name == "nt", reason="Creating symlinks requires Windows privileges")
def test_doctor_rejects_symlinked_curated_memory_directory(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    vault = tmp_path / "vault"
    hermes = vault / "06_Automation" / "Hermes"
    hermes.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (hermes / "CuratedMemory").symlink_to(outside, target_is_directory=True)

    ok, info, warnings = _inspect(str(vault), home)

    assert not ok
    assert any("Unsafe Obsidian Vault curated-memory directory" in m for m in warnings)
    assert list(outside.iterdir()) == []


def test_doctor_warns_on_nonregular_memory_file(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    vault = tmp_path / "vault"
    curated = vault / "06_Automation" / "Hermes" / "CuratedMemory"
    curated.mkdir(parents=True)
    (curated / "MEMORY.md").mkdir()

    ok, info, warnings = _inspect(str(vault), home)

    assert any("Unsafe Vault MEMORY.md file" in message for message in warnings)
    assert any("Vault USER.md not created yet" in message for message in info)
    assert not ok
