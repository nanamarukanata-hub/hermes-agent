"""Regressions for the non-destructive SQLite-to-Vault export publisher."""

import json
import os
import stat
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


def _batch_paths(tmp_path):
    vault = tmp_path / "vault"
    directory = vault / "06_Automation" / "Hermes" / "RetainQueue"
    first, second = directory / "1.json", directory / "2.json"
    planned = [(first, {"user_id": "u1"}), (second, {"user_id": "u2"})]
    return vault, first, second, planned


def test_batch_publish_failure_rolls_back_earlier_records(tmp_path):
    vault, first, second, planned = _batch_paths(tmp_path)
    real_link = os.link
    calls = 0

    def fail_second(source, target):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("second publication failed")
        return real_link(source, target)

    with patch.object(migration.os, "link", side_effect=fail_second):
        with pytest.raises(OSError, match="second publication failed"):
            migration._write_batch(vault, planned, force=False)

    assert not first.exists()
    assert not second.exists()
    assert list(first.parent.glob(".*.tmp-*")) == []


def test_batch_fsync_failure_publishes_no_records(tmp_path):
    vault, first, second, planned = _batch_paths(tmp_path)
    real_fsync = os.fsync
    calls = 0

    def fail_second(fd):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("second staging fsync failed")
        return real_fsync(fd)

    with patch.object(migration.os, "fsync", side_effect=fail_second):
        with pytest.raises(OSError, match="second staging fsync failed"):
            migration._write_batch(vault, planned, force=False)

    assert not first.exists()
    assert not second.exists()
    assert list(first.parent.glob(".*.tmp-*")) == []


def test_batch_rollback_preserves_concurrent_destination(tmp_path):
    vault, first, second, planned = _batch_paths(tmp_path)
    real_link = os.link

    def competing_writer(source, target):
        if target == second:
            second.write_text("CONCURRENT", encoding="utf-8")
            raise FileExistsError("another writer published second record")
        return real_link(source, target)

    with patch.object(migration.os, "link", side_effect=competing_writer):
        with pytest.raises(FileExistsError, match="another writer"):
            migration._write_batch(vault, planned, force=False)

    assert not first.exists()
    assert second.read_text(encoding="utf-8") == "CONCURRENT"
    assert list(first.parent.glob(".*.tmp-*")) == []


def test_force_batch_fsync_failure_preserves_existing_records(tmp_path):
    vault, first, second, planned = _batch_paths(tmp_path)
    first.parent.mkdir(parents=True)
    first.write_text("ORIGINAL-1", encoding="utf-8")
    second.write_text("ORIGINAL-2", encoding="utf-8")
    real_fsync = os.fsync
    calls = 0

    def fail_second(fd):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("force staging fsync failed")
        return real_fsync(fd)

    with patch.object(migration.os, "fsync", side_effect=fail_second):
        with pytest.raises(OSError, match="force staging fsync failed"):
            migration._write_batch(vault, planned, force=True)

    assert first.read_text(encoding="utf-8") == "ORIGINAL-1"
    assert second.read_text(encoding="utf-8") == "ORIGINAL-2"
    assert list(first.parent.glob(".*.tmp-*")) == []


def test_force_batch_replaces_existing_records_after_staging(tmp_path):
    vault, first, second, planned = _batch_paths(tmp_path)
    first.parent.mkdir(parents=True)
    first.write_text("ORIGINAL-1", encoding="utf-8")
    second.write_text("ORIGINAL-2", encoding="utf-8")

    migration._write_batch(vault, planned, force=True)

    assert json.loads(first.read_text(encoding="utf-8")) == {"user_id": "u1"}
    assert json.loads(second.read_text(encoding="utf-8")) == {"user_id": "u2"}
    assert list(first.parent.glob(".*.tmp-*")) == []


def test_force_batch_second_replace_failure_restores_originals(tmp_path):
    vault, first, second, planned = _batch_paths(tmp_path)
    first.parent.mkdir(parents=True)
    first.write_text("ORIGINAL-1", encoding="utf-8")
    second.write_text("ORIGINAL-2", encoding="utf-8")
    real_replace = os.replace

    def fail_second(source, target):
        if target == second:
            raise OSError("second replacement failed")
        return real_replace(source, target)

    with patch.object(migration.os, "replace", side_effect=fail_second):
        with pytest.raises(OSError, match="second replacement failed"):
            migration._write_batch(vault, planned, force=True)

    assert first.read_text(encoding="utf-8") == "ORIGINAL-1"
    assert second.read_text(encoding="utf-8") == "ORIGINAL-2"
    assert list(first.parent.glob(".*.tmp-*")) == []
    assert list(first.parent.glob(".*.backup-*")) == []


def test_force_batch_replace_failure_removes_newly_published_record(tmp_path):
    vault, first, second, planned = _batch_paths(tmp_path)
    first.parent.mkdir(parents=True)
    # The first destination is new; the second has an existing original.
    second.write_text("ORIGINAL-2", encoding="utf-8")
    real_replace = os.replace

    def fail_second(source, target):
        if target == second:
            raise OSError("second replacement failed")
        return real_replace(source, target)

    with patch.object(migration.os, "replace", side_effect=fail_second):
        with pytest.raises(OSError, match="second replacement failed"):
            migration._write_batch(vault, planned, force=True)

    assert not first.exists()
    assert second.read_text(encoding="utf-8") == "ORIGINAL-2"
    assert list(first.parent.glob(".*.backup-*")) == []


def test_force_rollback_preserves_concurrent_writer_and_original_backup(tmp_path):
    vault, first, second, planned = _batch_paths(tmp_path)
    first.parent.mkdir(parents=True)
    first.write_text("ORIGINAL-1", encoding="utf-8")
    second.write_text("ORIGINAL-2", encoding="utf-8")
    real_replace = os.replace

    def competing_replace(source, target):
        if target == second:
            competing = first.with_name("competing.json")
            competing.write_text("CONCURRENT", encoding="utf-8")
            real_replace(competing, first)
            raise OSError("second replacement failed")
        return real_replace(source, target)

    with patch.object(migration.os, "replace", side_effect=competing_replace):
        with pytest.raises(OSError, match="second replacement failed") as caught:
            migration._write_batch(vault, planned, force=True)

    assert first.read_text(encoding="utf-8") == "CONCURRENT"
    assert second.read_text(encoding="utf-8") == "ORIGINAL-2"
    retained = list(first.parent.glob(".1.json.backup-*"))
    assert len(retained) == 1
    assert retained[0].read_text(encoding="utf-8") == "ORIGINAL-1"
    assert any("rollback incomplete" in note for note in caught.value.__notes__)


def test_force_batch_preserves_existing_file_permissions(tmp_path):
    vault, first, second, planned = _batch_paths(tmp_path)
    first.parent.mkdir(parents=True)
    first.write_text("ORIGINAL-1", encoding="utf-8")
    second.write_text("ORIGINAL-2", encoding="utf-8")
    first.chmod(0o640)
    second.chmod(0o600)

    migration._write_batch(vault, planned, force=True)

    assert stat.S_IMODE(first.stat().st_mode) == 0o640
    assert stat.S_IMODE(second.stat().st_mode) == 0o600
    assert json.loads(first.read_text(encoding="utf-8")) == {"user_id": "u1"}
    assert json.loads(second.read_text(encoding="utf-8")) == {"user_id": "u2"}


@pytest.mark.parametrize("force", [False, True])
def test_batch_refuses_unrecovered_backup_without_publishing(tmp_path, force):
    vault, first, second, planned = _batch_paths(tmp_path)
    first.parent.mkdir(parents=True)
    original = first.parent / ".1.json.backup-interrupted"
    original.write_text("IRREPLACEABLE-ORIGINAL", encoding="utf-8")

    with pytest.raises(RuntimeError, match="unrecovered Vault migration backup"):
        migration._write_batch(vault, planned, force=force)

    assert original.read_text(encoding="utf-8") == "IRREPLACEABLE-ORIGINAL"
    assert not first.exists()
    assert not second.exists()
    assert list(first.parent.glob(".*.tmp-*")) == []
