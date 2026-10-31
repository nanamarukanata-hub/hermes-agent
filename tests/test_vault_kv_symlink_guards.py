"""Regression guards for SQLite-free VaultDir persistence paths."""

import fcntl
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
    assert reopened.next_id() == 1
    assert VaultDir(root, "06_Automation", "Hermes", "RetainQueue").next_id() == 2

def test_counter_persists_last_allocated_id(tmp_path):
    """Restarting a Vault must not skip IDs or reuse an existing allocation."""
    root = tmp_path / "vault"
    vault = VaultDir(root, "06_Automation", "Hermes", "RetainQueue")
    assert vault.next_id("sequence") == 1
    assert json.loads(vault.path("sequence").read_text(encoding="utf-8")) == {"next": 1}
    assert VaultDir(root, "06_Automation", "Hermes", "RetainQueue").next_id("sequence") == 2
    assert json.loads(vault.path("sequence").read_text(encoding="utf-8")) == {"next": 2}


def test_legacy_counter_is_never_reissued(tmp_path):
    """Old next+1 counters may leave a gap, but must never reuse an ID."""
    vault = VaultDir(tmp_path / "vault", "06_Automation", "Hermes", "RetainQueue")
    vault.path("sequence").write_text(json.dumps({"next": 8}) + "\n", encoding="utf-8")
    assert vault.next_id("sequence") == 9
    assert vault.next_id("sequence") == 10


@pytest.mark.parametrize("subpath", ["", ".", "..", "../outside", "nested/escape", "/absolute"])
def test_invalid_subpath_rejected_before_directory_creation(tmp_path, subpath):
    root = tmp_path / "vault"
    with pytest.raises(ValueError, match="invalid vault subpath"):
        VaultDir(root, subpath)
    assert not root.exists()


def test_preexisting_symlinked_parent_cannot_redirect_vault_creation(tmp_path):
    root = tmp_path / "vault"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "06_Automation").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="symlink"):
        VaultDir(root, "06_Automation", "Hermes", "RetainQueue")
    assert list(outside.iterdir()) == []


def test_parent_replaced_by_symlink_after_init_blocks_all_io(tmp_path):
    root = tmp_path / "vault"
    vault = VaultDir(root, "06_Automation", "Hermes", "RetainQueue")
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "06_Automation").rename(tmp_path / "original-automation")
    (root / "06_Automation").symlink_to(outside, target_is_directory=True)

    for operation in (
        lambda: vault.write("item", {"value": 1}),
        lambda: vault.read("item"),
        lambda: vault.list_ids(),
        lambda: vault.next_id("sequence"),
        lambda: vault.delete("item"),
    ):
        with pytest.raises(ValueError, match="symlink"):
            operation()
    assert list(outside.iterdir()) == []


def test_symlinked_root_is_rejected_before_any_write(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    alias = tmp_path / "vault-alias"
    alias.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        VaultDir(alias, "06_Automation", "Hermes", "RetainQueue")
    assert list(outside.iterdir()) == []


def test_record_read_does_not_follow_link_swapped_at_open(tmp_path, monkeypatch):
    """The link can be installed after VaultDir.path's initial check."""
    outside = tmp_path / "outside-secret.json"
    secret = '{"secret": "must-not-be-read"}'
    outside.write_text(secret, encoding="utf-8")
    vault = VaultDir(tmp_path / "vault", "06_Automation", "Hermes", "RetainQueue")
    vault.write("item", {"value": "safe"})
    record = vault.path("item")
    real_open = os.open
    swapped = False

    def swap_before_open(path, flags, *args, **kwargs):
        nonlocal swapped
        if os.fspath(path) == os.fspath(record) and not swapped:
            swapped = True
            record.unlink()
            record.symlink_to(outside)
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", swap_before_open)
    assert vault.read("item") is None
    assert swapped
    assert outside.read_text(encoding="utf-8") == secret


def test_record_read_rejects_fifo_without_blocking(tmp_path):
    vault = VaultDir(tmp_path / "vault", "06_Automation", "Hermes", "RetainQueue")
    os.mkfifo(vault.path("item"))
    assert vault.read("item") is None

def test_lock_inode_swap_after_flock_aborts_update(tmp_path, monkeypatch):
    """A replaced lock inode must not allow an unlocked JSON update."""
    vault = VaultDir(tmp_path / "vault", "06_Automation", "Hermes", "RetainQueue")
    real_flock = fcntl.flock
    swapped = False

    def swap_after_acquire(fd, operation):
        nonlocal swapped
        result = real_flock(fd, operation)
        if operation == fcntl.LOCK_EX and not swapped:
            swapped = True
            vault.lock_file.rename(vault.dir / ".detached-writer.lock")
            vault.lock_file.write_text("", encoding="utf-8")
        return result

    monkeypatch.setattr(fcntl, "flock", swap_after_acquire)
    with pytest.raises(ValueError, match="vault lock changed"):
        vault.write("item", {"value": "must-not-write"})
    assert swapped
    assert not vault.path("item").exists()


def test_fifo_lock_is_rejected_without_blocking(tmp_path):
    """An attacker-controlled FIFO cannot be used as the writer lock."""
    vault = VaultDir(tmp_path / "vault", "06_Automation", "Hermes", "RetainQueue")
    os.mkfifo(vault.lock_file)
    with pytest.raises(ValueError, match="vault lock is not a regular file"):
        vault.write("item", {"value": "must-not-write"})
    assert not vault.path("item").exists()
