"""Regressions for the non-destructive SQLite-to-Vault export publisher."""

import hashlib
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


def test_batch_lock_rejects_concurrent_migration_without_writes(tmp_path):
    vault, first, second, planned = _batch_paths(tmp_path)

    with migration._migration_lock(vault):
        with pytest.raises(RuntimeError, match="another SQLite-to-Vault migration"):
            migration._write_batch(vault, planned, force=False)
        assert not first.exists()
        assert not second.exists()

    # The OS lock must be released, without deleting its lock-file inode.
    migration._write_batch(vault, planned, force=False)
    assert json.loads(first.read_text(encoding="utf-8")) == {"user_id": "u1"}
    assert json.loads(second.read_text(encoding="utf-8")) == {"user_id": "u2"}
    assert (vault / "06_Automation" / "Hermes" / ".sqlite-vault-migration.lock").is_file()


def test_batch_lock_released_after_failed_publish(tmp_path):
    vault, first, second, planned = _batch_paths(tmp_path)

    with patch.object(migration.os, "link", side_effect=OSError("publish failed")):
        with pytest.raises(OSError, match="publish failed"):
            migration._write_batch(vault, planned, force=False)

    migration._write_batch(vault, planned, force=False)
    assert first.exists()
    assert second.exists()


def test_batch_lock_refuses_symlinked_lock_file(tmp_path):
    vault, first, second, planned = _batch_paths(tmp_path)
    lock_path = vault / "06_Automation" / "Hermes" / ".sqlite-vault-migration.lock"
    lock_path.parent.mkdir(parents=True)
    external = tmp_path / "external.txt"
    external.write_text("DO NOT MODIFY", encoding="utf-8")
    lock_path.symlink_to(external)

    with pytest.raises(ValueError, match="symlinked Vault destination"):
        migration._write_batch(vault, planned, force=True)

    assert external.read_text(encoding="utf-8") == "DO NOT MODIFY"
    assert not first.exists()
    assert not second.exists()


def _pending_marker(vault):
    return vault / "06_Automation" / "Hermes" / ".sqlite-vault-migration.pending"


@pytest.mark.parametrize("force", [False, True])
def test_batch_refuses_interrupted_publication_before_writes(tmp_path, force):
    vault, first, second, planned = _batch_paths(tmp_path)
    marker = _pending_marker(vault)
    marker.parent.mkdir(parents=True)
    marker.write_text('{"version": 1, "destinations": ["RetainQueue/1.json"]}\n', encoding="utf-8")

    with pytest.raises(RuntimeError, match="unrecovered SQLite-to-Vault publication marker"):
        migration._write_batch(vault, planned, force=force)

    assert not first.exists()
    assert not second.exists()
    assert marker.is_file()
    assert list(first.parent.glob(".*.tmp-*")) == []


def test_successful_batch_and_complete_rollback_clear_pending_marker(tmp_path):
    vault, first, second, planned = _batch_paths(tmp_path)
    marker = _pending_marker(vault)

    with patch.object(migration.os, "link", side_effect=OSError("publication failed")):
        with pytest.raises(OSError, match="publication failed"):
            migration._write_batch(vault, planned, force=False)
    assert not marker.exists()
    assert not first.exists()
    assert not second.exists()

    migration._write_batch(vault, planned, force=False)
    assert not marker.exists()
    assert json.loads(first.read_text(encoding="utf-8")) == {"user_id": "u1"}
    assert json.loads(second.read_text(encoding="utf-8")) == {"user_id": "u2"}


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX subprocess termination")
def test_abrupt_exit_during_publish_leaves_marker_and_blocks_retry(tmp_path):
    import subprocess
    import sys
    from pathlib import Path

    vault, first, second, planned = _batch_paths(tmp_path)
    script = """
import os
import sys
from pathlib import Path
from scripts import migrate_sqlite_to_vault as migration

vault = Path(sys.argv[1])
dest = vault / "06_Automation" / "Hermes" / "RetainQueue"
planned = [(dest / "1.json", {"user_id": "u1"}), (dest / "2.json", {"user_id": "u2"})]
real_link = os.link

def exit_after_first_link(source, target):
    real_link(source, target)
    os._exit(73)

migration.os.link = exit_after_first_link
migration._write_batch(vault, planned, force=False)
"""
    env = dict(os.environ)
    repo_root = str(Path(__file__).resolve().parents[1])
    env["PYTHONPATH"] = repo_root + os.pathsep + env.get("PYTHONPATH", "")
    result = subprocess.run([sys.executable, "-c", script, str(vault)], env=env, check=False)
    assert result.returncode == 73
    assert first.is_file()
    assert not second.exists()

    marker = _pending_marker(vault)
    manifest = json.loads(marker.read_text(encoding="utf-8"))
    assert manifest["version"] == 2
    assert len(manifest["destinations"]) == 2
    assert manifest["destinations"][0]["path"].endswith("RetainQueue/1.json")
    assert manifest["destinations"][0]["new_sha256"] == hashlib.sha256(first.read_bytes()).hexdigest()
    assert manifest["destinations"][0]["backup"] is None
    with pytest.raises(RuntimeError, match="unrecovered SQLite-to-Vault publication marker"):
        migration._write_batch(vault, planned, force=True)
    assert first.is_file()
    assert not second.exists()


def test_recovery_report_identifies_published_original_and_valid_backup(tmp_path):
    vault, first, second, _ = _batch_paths(tmp_path)
    first.parent.mkdir(parents=True)
    first.write_text("NEW", encoding="utf-8")
    second.write_text("OLD", encoding="utf-8")
    backup = first.parent / ".1.json.backup-recovery"
    backup.write_text("OLD", encoding="utf-8")
    digest = lambda data: hashlib.sha256(data.encode("utf-8")).hexdigest()
    marker = _pending_marker(vault)
    marker.write_text(json.dumps({
        "version": 2, "force": True, "destinations": [
            {
                "path": str(first.relative_to(vault)),
                "new_sha256": digest("NEW"), "original_sha256": digest("OLD"),
                "backup": str(backup.relative_to(vault)),
            },
            {
                "path": str(second.relative_to(vault)),
                "new_sha256": digest("NEW"), "original_sha256": digest("OLD"),
                "backup": None,
            },
        ],
    }), encoding="utf-8")
    before = {p: p.read_bytes() for p in (marker, first, second, backup)}
    report = migration._recovery_report(vault)
    assert report["pending"] is True
    assert report["version"] == 2
    assert report["records"][0]["destination"] == "published"
    assert report["records"][0]["backup"] == "valid"
    assert report["records"][1]["destination"] == "original"
    assert report["records"][1]["backup"] == "none"
    assert all(p.read_bytes() == content for p, content in before.items())


def test_recovery_report_flags_changed_or_missing_backup(tmp_path):
    vault, first, _, _ = _batch_paths(tmp_path)
    first.parent.mkdir(parents=True)
    first.write_text("CONCURRENT", encoding="utf-8")
    backup = first.parent / ".1.json.backup-recovery"
    backup.write_text("CORRUPTED", encoding="utf-8")
    marker = _pending_marker(vault)
    digest = lambda data: hashlib.sha256(data.encode("utf-8")).hexdigest()
    marker.write_text(json.dumps({
        "version": 2, "destinations": [{
            "path": str(first.relative_to(vault)), "new_sha256": digest("NEW"),
            "original_sha256": digest("OLD"), "backup": str(backup.relative_to(vault)),
        }],
    }), encoding="utf-8")
    report = migration._recovery_report(vault)
    assert report["records"][0]["destination"] == "changed"
    assert report["records"][0]["backup"] == "changed"
    backup.unlink()
    assert migration._recovery_report(vault)["records"][0]["backup"] == "missing"


def test_recovery_report_rejects_marker_path_traversal(tmp_path):
    vault, first, _, _ = _batch_paths(tmp_path)
    first.parent.mkdir(parents=True)
    marker = _pending_marker(vault)
    marker.write_text(json.dumps({
        "version": 2, "destinations": [{
            "path": "../../outside", "new_sha256": "0" * 64,
            "original_sha256": None, "backup": None,
        }],
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="unsafe recovery path"):
        migration._recovery_report(vault)
    assert marker.exists()



def test_recovery_report_rejects_directory_marker_instead_of_reporting_clean(tmp_path):
    vault, _, _, _ = _batch_paths(tmp_path)
    marker = _pending_marker(vault)
    marker.mkdir(parents=True)
    with pytest.raises(ValueError, match="invalid SQLite-to-Vault recovery marker type"):
        migration._recovery_report(vault)
    assert marker.is_dir()


@pytest.mark.parametrize("version", [1, 2])
def test_recovery_report_rejects_duplicate_destinations(tmp_path, version):
    vault, first, _, _ = _batch_paths(tmp_path)
    marker = _pending_marker(vault)
    marker.parent.mkdir(parents=True)
    path = str(first.relative_to(vault))
    if version == 1:
        destinations = [path, path]
    else:
        record = {
            "path": path, "new_sha256": "0" * 64,
            "original_sha256": None, "backup": None,
        }
        destinations = [record, record]
    marker.write_text(
        json.dumps({"version": version, "destinations": destinations}), encoding="utf-8"
    )
    before = marker.read_bytes()
    with pytest.raises(ValueError, match="duplicate recovery destination"):
        migration._recovery_report(vault)
    assert marker.read_bytes() == before


def test_recovery_report_rejects_backup_not_belonging_to_destination(tmp_path):
    vault, first, _, _ = _batch_paths(tmp_path)
    first.parent.mkdir(parents=True)
    unrelated = first.parent / "other-record.json"
    unrelated.write_text("KEEP", encoding="utf-8")
    marker = _pending_marker(vault)
    marker.write_text(json.dumps({
        "version": 2, "destinations": [{
            "path": str(first.relative_to(vault)),
            "new_sha256": "0" * 64,
            "original_sha256": hashlib.sha256(b"KEEP").hexdigest(),
            "backup": str(unrelated.relative_to(vault)),
        }],
    }), encoding="utf-8")
    before = {p: p.read_bytes() for p in (marker, unrelated)}
    with pytest.raises(ValueError, match="invalid recovery backup path"):
        migration._recovery_report(vault)
    assert {p: p.read_bytes() for p in before} == before


def test_inspect_recovery_cli_does_not_require_legacy_sqlite(tmp_path, monkeypatch, capsys):
    vault, _, _, _ = _batch_paths(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        ["migrate_sqlite_to_vault.py", "--vault", str(vault), "--inspect-recovery"],
    )
    assert migration.main() == 0
    assert json.loads(capsys.readouterr().out) == {"pending": False, "records": []}
    assert not vault.exists()


@pytest.mark.skipif(os.name != "posix", reason="directory fsync durability is POSIX-specific")
def test_force_backup_directory_entries_are_synced_before_marker(tmp_path):
    vault, first, _, _ = _batch_paths(tmp_path)
    second = vault / "06_Automation" / "Hermes" / "ApiResponses" / "2.json"
    for path in (first, second):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("ORIGINAL", encoding="utf-8")
    planned = [(first, {"value": 1}), (second, {"value": 2})]
    marker = _pending_marker(vault)
    required_parents = {first.parent, second.parent}
    synced_parents = set()
    directory_fds = {}
    marker_created = False
    real_open, real_fsync = os.open, os.fsync

    def tracked_open(path, flags, *args, **kwargs):
        nonlocal marker_created
        if os.fspath(path) == os.fspath(marker) and flags & os.O_EXCL:
            marker_created = True
            assert synced_parents == required_parents
        fd = real_open(path, flags, *args, **kwargs)
        if os.fspath(path) in {os.fspath(p) for p in required_parents}:
            directory_fds[fd] = os.fspath(path)
        return fd

    def tracked_fsync(fd):
        if fd in directory_fds and stat.S_ISDIR(os.fstat(fd).st_mode):
            synced_parents.add(type(first)(directory_fds[fd]))
        return real_fsync(fd)

    with patch.object(migration.os, "open", side_effect=tracked_open):
        with patch.object(migration.os, "fsync", side_effect=tracked_fsync):
            migration._write_batch(vault, planned, force=True)

    assert marker_created
    assert synced_parents == required_parents
    assert json.loads(first.read_text(encoding="utf-8")) == {"value": 1}
    assert json.loads(second.read_text(encoding="utf-8")) == {"value": 2}


@pytest.mark.skipif(os.name != "posix", reason="directory fsync durability is POSIX-specific")
def test_force_backup_directory_sync_failure_prevents_publication(tmp_path):
    vault, first, second, planned = _batch_paths(tmp_path)
    first.parent.mkdir(parents=True)
    first.write_text("ORIGINAL-1", encoding="utf-8")
    second.write_text("ORIGINAL-2", encoding="utf-8")
    real_fsync = os.fsync
    marker = _pending_marker(vault)
    marker_created = False
    real_open = os.open

    def tracked_open(path, flags, *args, **kwargs):
        nonlocal marker_created
        if os.fspath(path) == os.fspath(marker) and flags & os.O_EXCL:
            marker_created = True
        return real_open(path, flags, *args, **kwargs)

    def fail_directory_fsync(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError("backup directory sync failed")
        return real_fsync(fd)

    with patch.object(migration.os, "open", side_effect=tracked_open):
        with patch.object(migration.os, "fsync", side_effect=fail_directory_fsync):
            with pytest.raises(OSError, match="backup directory sync failed"):
                migration._write_batch(vault, planned, force=True)

    assert not marker_created
    assert not marker.exists()
    assert first.read_text(encoding="utf-8") == "ORIGINAL-1"
    assert second.read_text(encoding="utf-8") == "ORIGINAL-2"
    assert list(first.parent.glob(".*.backup-*")) == []


@pytest.mark.skipif(os.name != "posix", reason="directory fsync durability is POSIX-specific")
def test_published_directory_is_synced_before_pending_marker_removed(tmp_path):
    vault, first, second, planned = _batch_paths(tmp_path)
    marker = _pending_marker(vault)
    real_fsync = os.fsync
    real_unlink = migration.Path.unlink
    publication_synced = False
    marker_removed = False

    def track_fsync(fd):
        nonlocal publication_synced
        if stat.S_ISDIR(os.fstat(fd).st_mode) and first.is_file() and second.is_file():
            publication_synced = True
        return real_fsync(fd)

    def track_unlink(self, *args, **kwargs):
        nonlocal marker_removed
        if self == marker:
            assert publication_synced, "marker removed before publication directory fsync"
            marker_removed = True
        return real_unlink(self, *args, **kwargs)

    with patch.object(migration.os, "fsync", side_effect=track_fsync):
        with patch.object(migration.Path, "unlink", track_unlink):
            migration._write_batch(vault, planned, force=False)

    assert publication_synced and marker_removed
    assert not marker.exists()
    assert first.is_file() and second.is_file()


@pytest.mark.skipif(os.name != "posix", reason="directory fsync durability is POSIX-specific")
def test_publication_directory_sync_failure_rolls_back_without_partial_export(tmp_path):
    vault, first, second, planned = _batch_paths(tmp_path)
    real_fsync = os.fsync

    def fail_published_directory_fsync(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode) and first.is_file() and second.is_file():
            raise OSError("publication directory sync failed")
        return real_fsync(fd)

    with patch.object(migration.os, "fsync", side_effect=fail_published_directory_fsync):
        with pytest.raises(OSError, match="publication directory sync failed"):
            migration._write_batch(vault, planned, force=False)

    assert not first.exists() and not second.exists()
    assert not _pending_marker(vault).exists()
    assert list(first.parent.glob(".*.tmp-*")) == []


@pytest.mark.skipif(os.name != "posix", reason="directory fsync durability is POSIX-specific")
def test_rollback_directory_sync_failure_retains_pending_marker(tmp_path):
    vault, first, second, planned = _batch_paths(tmp_path)
    real_link, real_fsync = os.link, os.fsync
    publication_failed = False

    def fail_second_link(source, target):
        nonlocal publication_failed
        if target == second:
            publication_failed = True
            raise OSError("second link failed")
        return real_link(source, target)

    def fail_rollback_directory_fsync(fd):
        if publication_failed and stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError("rollback directory sync failed")
        return real_fsync(fd)

    with patch.object(migration.os, "link", side_effect=fail_second_link):
        with patch.object(migration.os, "fsync", side_effect=fail_rollback_directory_fsync):
            with pytest.raises(OSError, match="second link failed") as caught:
                migration._write_batch(vault, planned, force=False)

    assert not first.exists() and not second.exists()
    assert _pending_marker(vault).is_file()
    assert any("rollback incomplete" in note for note in caught.value.__notes__)


def test_inspect_recovery_reports_orphaned_backup_without_pending_marker(tmp_path):
    vault, first, _, _ = _batch_paths(tmp_path)
    first.parent.mkdir(parents=True)
    backup = first.parent / ".1.json.backup-interrupted"
    backup.write_text("IRREPLACEABLE-ORIGINAL", encoding="utf-8")
    before = backup.read_bytes()

    report = migration._recovery_report(vault)

    assert report == {
        "pending": False,
        "records": [],
        "orphan_backups": [str(backup.relative_to(vault))],
        "requires_attention": True,
    }
    assert backup.read_bytes() == before
    assert not _pending_marker(vault).exists()


@pytest.mark.skipif(os.name == "nt", reason="symlink creation may require elevated privileges")
def test_inspect_recovery_refuses_symlinked_orphan_backup(tmp_path):
    vault, first, _, _ = _batch_paths(tmp_path)
    first.parent.mkdir(parents=True)
    external = tmp_path / "original-outside-vault"
    external.write_text("DO NOT TOUCH", encoding="utf-8")
    backup = first.parent / ".1.json.backup-redirect"
    backup.symlink_to(external)

    with pytest.raises(ValueError, match="symlinked Vault destination"):
        migration._recovery_report(vault)

    assert external.read_text(encoding="utf-8") == "DO NOT TOUCH"
    assert backup.is_symlink()
    assert not _pending_marker(vault).exists()
