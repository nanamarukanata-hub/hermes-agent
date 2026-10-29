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
