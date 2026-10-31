"""Curated MEMORY.md/USER.md must persist exclusively inside the Obsidian Vault."""

from pathlib import Path

import pytest

from tools.memory_tool import MemoryStore, get_memory_dir


def test_missing_vault_fails_closed_without_touching_legacy_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    legacy = home / "memories" / "MEMORY.md"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("preserve legacy data", encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.delenv("HERMES_OBSIDIAN_VAULT", raising=False)

    with pytest.raises(RuntimeError, match="HERMES_OBSIDIAN_VAULT"):
        MemoryStore().load_from_disk()

    assert legacy.read_text(encoding="utf-8") == "preserve legacy data"


def test_missing_vault_directory_is_not_created(tmp_path, monkeypatch):
    missing = tmp_path / "missing-vault"
    monkeypatch.setenv("HERMES_OBSIDIAN_VAULT", str(missing))
    with pytest.raises(RuntimeError, match="existing"):
        get_memory_dir()
    assert not missing.exists()


def test_memory_roundtrip_is_vault_only(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    vault.mkdir()
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_OBSIDIAN_VAULT", str(vault))
    monkeypatch.setenv("HERMES_HOME", str(home))

    first = MemoryStore()
    first.load_from_disk()
    assert first.add("memory", "Vault-only durable fact")["success"]
    assert first.add("user", "User prefers plain text")["success"]

    expected = vault / "06_Automation" / "Hermes" / "CuratedMemory"
    assert get_memory_dir() == expected
    assert "Vault-only durable fact" in (expected / "MEMORY.md").read_text(encoding="utf-8")
    assert "User prefers plain text" in (expected / "USER.md").read_text(encoding="utf-8")
    assert not (home / "memories").exists()
    assert not list(tmp_path.rglob("*.db"))

    second = MemoryStore()
    second.load_from_disk()
    assert second.memory_entries == ["Vault-only durable fact"]
    assert second.user_entries == ["User prefers plain text"]


def test_symlinked_curated_memory_directory_is_rejected(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    parent = vault / "06_Automation" / "Hermes"
    parent.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (parent / "CuratedMemory").symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not supported")
    monkeypatch.setenv("HERMES_OBSIDIAN_VAULT", str(vault))

    with pytest.raises(ValueError, match="symlink"):
        MemoryStore().load_from_disk()
    assert not list(outside.iterdir())


def test_switching_vaults_does_not_reuse_previous_memory(tmp_path, monkeypatch):
    vault_a = tmp_path / "vault-a"
    vault_b = tmp_path / "vault-b"
    vault_a.mkdir()
    vault_b.mkdir()

    monkeypatch.setenv("HERMES_OBSIDIAN_VAULT", str(vault_a))
    first = MemoryStore()
    first.load_from_disk()
    assert first.add("memory", "only in A")["success"]

    monkeypatch.setenv("HERMES_OBSIDIAN_VAULT", str(vault_b))
    second = MemoryStore()
    second.load_from_disk()
    assert second.memory_entries == []
    assert second.add("memory", "only in B")["success"]

    assert "only in A" in (vault_a / "06_Automation/Hermes/CuratedMemory/MEMORY.md").read_text()
    assert "only in B" in (vault_b / "06_Automation/Hermes/CuratedMemory/MEMORY.md").read_text()
