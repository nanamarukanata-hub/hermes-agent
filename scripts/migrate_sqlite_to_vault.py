#!/usr/bin/env python3
"""Read-only export of legacy SQLite stores to Obsidian Vault JSON.

Usage:
    python3 scripts/migrate_sqlite_to_vault.py --vault <vault-root> [--dry-run]

Exports, when the legacy file exists and the Vault target is empty
(unless --force):
  - <hermes_home>/retaindb_queue.db  -> Vault/06_Automation/Hermes/RetainQueue/<id>.json
  - <hermes_home>/memory_store.db    -> Vault/06_Automation/Hermes/HolographicMemory/
                                       (facts/, banks/, entities.json)
  - <hermes_home>/response_store.db  -> Vault/06_Automation/Hermes/ApiResponses/<id>.json
  - <hermes_home>/kanban.db          -> see migrate_kanban_to_vault.py (board-aware)

Legacy .db files are never modified or deleted.  This script is a
migration tool, not runtime persistence: after migration, the runtime
stores listed above read and write only Vault JSON.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import tempfile
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote


def _read_table(conn: sqlite3.Connection, name: str, *, optional: bool = False) -> list[dict]:
    # Missing required tables and malformed schemas must not become silent empty exports.
    if optional and conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is None:
        return []
    cur = conn.execute(f"SELECT * FROM {name}")  # All names are fixed literals.
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def _safe_id(value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError(f"invalid legacy record identifier: {value!r}")
    name = str(value)
    if name in (".", "..") or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name) is None:
        raise ValueError(f"unsafe legacy record identifier: {value!r}")
    return name


def _legacy_int_id(value: object) -> int:
    """Validate integer legacy IDs before using them for links or filenames."""
    name = _safe_id(value)
    # Reject noncanonical spellings that could alias the same numeric ID.
    if re.fullmatch(r"(?:0|[1-9][0-9]*)", name) is None:
        raise ValueError(f"unsafe legacy record identifier: {value!r}")
    return int(name)


def _check_destination(vault: Path, dest: Path) -> None:
    # Reject both traversal and symlink redirection, including existing files.
    try:
        parts = dest.relative_to(vault).parts
    except ValueError as exc:
        raise ValueError(f"destination escapes Vault: {dest}") from exc
    current = vault
    if current.is_symlink():
        raise ValueError(f"symlinked Vault root: {current}")
    for part in parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"symlinked Vault destination: {current}")


def _sha256_file(path: Path) -> str:
    """Hash a staged export or recovery backup without modifying it."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _recovery_report(vault: Path) -> dict:
    """Inspect an interrupted export; never restore, remove, or overwrite files."""
    marker = vault / "06_Automation" / "Hermes" / ".sqlite-vault-migration.pending"
    _check_destination(vault, marker)
    if not marker.exists():
        return {"pending": False, "records": []}
    if not marker.is_file():
        raise ValueError("invalid SQLite-to-Vault recovery marker type")
    with marker.open(encoding="utf-8") as stream:
        manifest = json.load(stream)
    if not isinstance(manifest, dict) or manifest.get("version") not in (1, 2):
        raise ValueError("unsupported SQLite-to-Vault recovery marker")
    entries = manifest.get("destinations")
    if not isinstance(entries, list):
        raise ValueError("invalid SQLite-to-Vault recovery destinations")

    def checked_path(value: object) -> Path:
        if not isinstance(value, str) or not value:
            raise ValueError("invalid recovery path")
        relative = Path(value)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("unsafe recovery path")
        path = vault / relative
        _check_destination(vault, path)
        return path

    records = []
    seen_destinations: set[Path] = set()
    for entry in entries:
        if manifest["version"] == 1:
            dest = checked_path(entry)
            if dest in seen_destinations:
                raise ValueError("duplicate recovery destination")
            seen_destinations.add(dest)
            records.append({
                "path": str(dest.relative_to(vault)),
                "destination": "unverifiable" if dest.is_file() else "missing",
                "backup": "unverifiable",
            })
            continue
        if not isinstance(entry, dict):
            raise ValueError("invalid recovery record")
        dest = checked_path(entry.get("path"))
        if dest in seen_destinations:
            raise ValueError("duplicate recovery destination")
        seen_destinations.add(dest)
        new_hash = entry.get("new_sha256")
        old_hash = entry.get("original_sha256")
        if not isinstance(new_hash, str) or re.fullmatch(r"[0-9a-f]{64}", new_hash) is None:
            raise ValueError("invalid staged export digest")
        if old_hash is not None and (
            not isinstance(old_hash, str) or re.fullmatch(r"[0-9a-f]{64}", old_hash) is None
        ):
            raise ValueError("invalid original export digest")
        if not dest.is_file():
            destination_state = "missing"
        else:
            current_hash = _sha256_file(dest)
            destination_state = (
                "published" if current_hash == new_hash
                else "original" if current_hash == old_hash
                else "changed"
            )
        backup_name = entry.get("backup")
        if backup_name is None:
            backup_state = "none"
        else:
            backup = checked_path(backup_name)
            if backup.parent != dest.parent or not backup.name.startswith(
                f".{dest.name}.backup-"
            ):
                raise ValueError("invalid recovery backup path")
            backup_state = (
                "missing" if not backup.is_file()
                else "valid" if _sha256_file(backup) == old_hash
                else "changed"
            )
        records.append({
            "path": str(dest.relative_to(vault)),
            "destination": destination_state,
            "backup": backup_state,
        })
    # No automated recovery: a concurrent Vault writer may have changed a file.
    return {"pending": True, "version": manifest["version"], "records": records}


def _write_json(vault: Path, dest: Path, data: dict, *, force: bool) -> None:
    _check_destination(vault, dest)
    if dest.exists() and not force:
        raise FileExistsError(f"Vault destination already exists: {dest}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    # A PID-derived name could be pre-planted as a symlink to a file outside
    # the Vault. Exclusive creation prevents following that link.
    fd, tmp_name = tempfile.mkstemp(prefix=f".{dest.name}.tmp-", dir=dest.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(json.dumps(data, indent=2, ensure_ascii=False, default=str) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        if force:
            os.replace(tmp, dest)
        else:
            # Atomic no-clobber publish: a concurrent writer cannot be overwritten.
            os.link(tmp, dest)
    finally:
        tmp.unlink(missing_ok=True)



@contextmanager
def _migration_lock(vault: Path):
    """Prevent two cooperating SQLite exports from publishing concurrently.

    The lock file is intentionally retained: removing a locked file would let
    another process lock a different inode. OS locks release on process exit,
    including an abrupt termination. Verification and dry-run never lock.
    """
    lock_path = vault / "06_Automation" / "Hermes" / ".sqlite-vault-migration.lock"
    _check_destination(vault, lock_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(lock_path, flags, 0o600)
    locked = False
    try:
        # Do not trust a lock inode that was swapped out before acquisition.
        _check_destination(vault, lock_path)
        if os.name == "nt":
            import msvcrt

            if os.fstat(fd).st_size == 0:
                os.write(fd, b"\0")
            os.lseek(fd, 0, os.SEEK_SET)
            try:
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise RuntimeError("another SQLite-to-Vault migration is active") from exc
        else:
            import fcntl

            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError("another SQLite-to-Vault migration is active") from exc
        locked = True
        held = os.fstat(fd)
        current = lock_path.stat(follow_symlinks=False)
        if (held.st_dev, held.st_ino) != (current.st_dev, current.st_ino):
            raise RuntimeError("SQLite-to-Vault migration lock was replaced")
        yield
    finally:
        if locked:
            if os.name == "nt":
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _write_batch(vault: Path, planned: list[tuple[Path, dict]], *, force: bool) -> None:
    """Publish one batch while holding the cooperative Vault migration lock."""
    if not planned:
        return
    with _migration_lock(vault):
        _write_batch_locked(vault, planned, force=force)


def _write_batch_locked(vault: Path, planned: list[tuple[Path, dict]], *, force: bool) -> None:
    """Stage all exports, and roll back ordinary publication errors.

    Force mode also snapshots existing records before replacing anything.
    Rollback is best-effort, not crash-safe or an atomic multi-file commit.
    """
    prepared: list[tuple[Path, Path]] = []
    backups: dict[Path, Path | None] = {}
    original_stats: dict[Path, tuple[int, int, int, int, int] | None] = {}
    published: list[tuple[Path, Path, tuple[int, int]]] = []
    preserve_backups: set[Path] = set()
    # A durable, fail-closed marker distinguishes an interrupted publication
    # from an ordinary completed migration. It is NOT an automatic recovery log.
    marker = vault / "06_Automation" / "Hermes" / ".sqlite-vault-migration.pending"
    _check_destination(vault, marker)
    if marker.exists():
        raise RuntimeError(
            f"unrecovered SQLite-to-Vault publication marker: {marker}; "
            "inspect the Vault and backups before retrying"
        )
    marker_inode: tuple[int, int] | None = None
    rollback_incomplete = False
    try:
        # All serialization and fsyncs finish before any Vault record changes.
        for dest, data in planned:
            _check_destination(vault, dest)
            # A prior interrupted force migration may have retained the only
            # original copy here. Do not overwrite more records until the
            # operator has inspected and recovered those backups.
            if any(dest.parent.glob(f".{dest.name}.backup-*")):
                raise RuntimeError(
                    f"unrecovered Vault migration backup for {dest}; "
                    "inspect the .backup-* file before retrying"
                )
            if dest.exists() and not force:
                raise FileExistsError(f"Vault destination already exists: {dest}")
            dest.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(prefix=f".{dest.name}.tmp-", dir=dest.parent)
            tmp = Path(tmp_name)
            prepared.append((tmp, dest))
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(json.dumps(data, indent=2, ensure_ascii=False, default=str) + "\n")
                stream.flush()
                os.fsync(stream.fileno())

        if force:
            # Snapshot *every* original before the first replacement.
            # Unlike hard-link backups, copies cannot be changed by a writer
            # modifying the original inode in place after this point.
            for _, dest in prepared:
                _check_destination(vault, dest)
                if not dest.exists():
                    backups[dest] = None
                    original_stats[dest] = None
                    continue
                before = dest.stat(follow_symlinks=False)
                fingerprint = (
                    before.st_dev, before.st_ino, before.st_size,
                    before.st_mtime_ns, stat.S_IMODE(before.st_mode),
                )
                fd, backup_name = tempfile.mkstemp(
                    prefix=f".{dest.name}.backup-", dir=dest.parent
                )
                backup = Path(backup_name)
                backups[dest] = backup
                with dest.open("rb") as source, os.fdopen(fd, "wb") as stream:
                    shutil.copyfileobj(source, stream)
                    stream.flush()
                    os.fchmod(stream.fileno(), stat.S_IMODE(before.st_mode))
                    os.fsync(stream.fileno())
                after = dest.stat(follow_symlinks=False)
                if (
                    after.st_dev, after.st_ino, after.st_size,
                    after.st_mtime_ns, stat.S_IMODE(after.st_mode),
                ) != fingerprint:
                    raise RuntimeError(f"Vault destination changed during backup: {dest}")
                original_stats[dest] = fingerprint

            # fsync() on a backup file does not persist its directory entry.
            # Ensure every backup name is durable before publishing the
            # recovery marker; otherwise a crash can leave a durable marker
            # pointing at a backup that never reached disk.
            if os.name == "posix":
                backup_parents = {backup.parent for backup in backups.values() if backup is not None}
                for parent in sorted(backup_parents):
                    dir_fd = os.open(parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
                    try:
                        os.fsync(dir_fd)
                    finally:
                        os.close(dir_fd)

        # Create and fsync the marker *before* publishing any record. A
        # SIGKILL or power loss during a multi-file publication then blocks
        # further migrations rather than silently treating partial output as
        # complete. A directory fsync makes the new name durable on POSIX.
        marker_fd = os.open(
            marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        marker_stat = os.fstat(marker_fd)
        marker_inode = (marker_stat.st_dev, marker_stat.st_ino)
        with os.fdopen(marker_fd, "w", encoding="utf-8") as stream:
            records = []
            for tmp, dest in prepared:
                backup = backups.get(dest)
                records.append({
                    "path": str(dest.relative_to(vault)),
                    "new_sha256": _sha256_file(tmp),
                    "original_sha256": _sha256_file(backup) if backup is not None else None,
                    "backup": str(backup.relative_to(vault)) if backup is not None else None,
                })
            json.dump({"version": 2, "force": force, "destinations": records}, stream)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        if os.name == "posix":
            dir_fd = os.open(marker.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)

        for tmp, dest in prepared:
            _check_destination(vault, dest)
            new_stat = tmp.stat(follow_symlinks=False)
            if force:
                # Detect ordinary concurrent replacements between backup and
                # publication. This check is not a cross-process file lock.
                current = dest.stat(follow_symlinks=False) if dest.exists() else None
                fingerprint = (
                    (
                        current.st_dev, current.st_ino, current.st_size,
                        current.st_mtime_ns, stat.S_IMODE(current.st_mode),
                    )
                    if current is not None else None
                )
                if fingerprint != original_stats[dest]:
                    raise RuntimeError(f"Vault destination changed before publication: {dest}")
                if current is not None:
                    # mkstemp creates 0600 files. Preserve the original
                    # permissions instead of silently restricting readers.
                    os.chmod(tmp, stat.S_IMODE(current.st_mode))
                    with tmp.open("rb") as staged:
                        os.fsync(staged.fileno())
                os.replace(tmp, dest)
            else:
                os.link(tmp, dest)
            published.append((tmp, dest, (new_stat.st_dev, new_stat.st_ino)))
    except BaseException as exc:
        rollback_errors: list[str] = []
        for tmp, dest, new_inode in reversed(published):
            try:
                target = dest.stat(follow_symlinks=False)
                if (target.st_dev, target.st_ino) != new_inode:
                    # A concurrent writer replaced the published record.
                    # Never overwrite it during rollback; retain its backup.
                    if force and backups.get(dest) is not None:
                        preserve_backups.add(dest)
                    rollback_errors.append(f"{dest}: changed by another writer")
                    continue
                if force and backups[dest] is not None:
                    os.replace(backups[dest], dest)
                else:
                    dest.unlink()
            except FileNotFoundError:
                if force and backups.get(dest) is not None:
                    preserve_backups.add(dest)
                rollback_errors.append(f"{dest}: missing during rollback")
            except OSError as error:
                if force and backups.get(dest) is not None:
                    preserve_backups.add(dest)
                rollback_errors.append(f"{dest}: {error}")
        if rollback_errors:
            rollback_incomplete = True
            exc.add_note("Vault export rollback incomplete: " + "; ".join(rollback_errors))
            retained = [str(backups[d]) for d in preserve_backups if backups[d] is not None]
            if retained:
                exc.add_note("Retained original Vault backups for recovery: " + ", ".join(retained))
        raise
    finally:
        for tmp, _ in prepared:
            tmp.unlink(missing_ok=True)
        for dest, backup in backups.items():
            if backup is not None and dest not in preserve_backups:
                backup.unlink(missing_ok=True)
        # Only remove our own marker after complete publication or successful
        # rollback and backup cleanup. Incomplete rollback must remain blocked.
        if marker_inode is not None and not rollback_incomplete:
            try:
                current_marker = marker.stat(follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                if (current_marker.st_dev, current_marker.st_ino) == marker_inode:
                    marker.unlink()


def main() -> int:
    ap = argparse.ArgumentParser(description="Export legacy SQLite stores to Vault JSON (read-only).")
    ap.add_argument("--vault", required=True, help="Obsidian Vault root")
    ap.add_argument("--home", default=None, help="HERMES_HOME (default: env or ~/.hermes)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--verify", action="store_true", help="Compare Vault export with legacy SQLite without writing")
    ap.add_argument(
        "--inspect-recovery", action="store_true",
        help="Read-only report of an interrupted export; never restore or delete files",
    )
    args = ap.parse_args()
    if args.verify and (args.dry_run or args.force):
        ap.error("--verify cannot be combined with --dry-run or --force")
    if args.inspect_recovery and (args.verify or args.dry_run or args.force):
        ap.error("--inspect-recovery cannot be combined with --verify, --dry-run or --force")
    if args.inspect_recovery:
        vault = Path(args.vault).expanduser()
        print(json.dumps(_recovery_report(vault), indent=2))
        return 0

    home = Path(args.home).expanduser() if args.home else Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes")).expanduser()
    vault = Path(args.vault).expanduser()
    tree = vault / "06_Automation" / "Hermes"
    counts: dict[str, int] = {}
    planned: list[tuple[Path, dict]] = []
    seen: set[Path] = set()

    def stage(dest: Path, data: dict) -> None:
        _check_destination(vault, dest)
        if dest in seen:
            raise ValueError(f"duplicate Vault destination: {dest}")
        if dest.exists() and not (args.force or args.verify):
            raise FileExistsError(f"Vault destination already exists: {dest}")
        seen.add(dest)
        planned.append((dest, data))

    # RetainDB queue
    db = home / "retaindb_queue.db"
    if db.exists():
        conn = sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            rows = _read_table(conn, "pending")
        finally:
            conn.close()
        n = 0
        for row in rows:
            messages = json.loads(row.get("messages_json") or "[]")
            if not isinstance(messages, list):
                raise ValueError("RetainDB pending.messages_json must be a JSON array")
            data = {"user_id": row.get("user_id"), "session_id": row.get("session_id"), "messages": messages,
                    "created_at": row.get("created_at"), "last_error": row.get("last_error")}
            stage(tree / "RetainQueue" / f"{_safe_id(row.get('id'))}.json", data)
            n += 1
        counts["retaindb_queue"] = n

    # Holographic memory: preserve entity IDs, fact links, and HRR banks.
    # The runtime reads entities.json and each fact's entity_ids; records
    # additionally retain legacy entity metadata not used by the runtime.
    db = home / "memory_store.db"
    if db.exists():
        conn = sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            conn.execute("BEGIN")  # One consistent read-only SQLite snapshot.
            rows = _read_table(conn, "facts")
            entities = _read_table(conn, "entities", optional=True)
            links = _read_table(conn, "fact_entities", optional=True)
            banks = _read_table(conn, "memory_banks", optional=True)
        finally:
            conn.close()

        fact_ids = {_legacy_int_id(row["fact_id"]) for row in rows}
        entity_ids = {_legacy_int_id(row["entity_id"]) for row in entities}
        if len(fact_ids) != len(rows) or len(entity_ids) != len(entities):
            raise ValueError("duplicate legacy holographic identifiers")

        by_name: dict[str, int] = {}
        records: dict[str, dict] = {}
        folded_names: set[str] = set()
        for row in entities:
            name = row.get("name")
            if not isinstance(name, str) or not name.strip():
                raise ValueError("invalid legacy entity name")
            # The Vault runtime resolves entity names case-insensitively.
            # Fail rather than silently collapse two distinct legacy IDs.
            if name.casefold() in folded_names:
                raise ValueError(f"ambiguous legacy entity name: {name!r}")
            folded_names.add(name.casefold())
            entity_id = _legacy_int_id(row["entity_id"])
            by_name[name] = entity_id
            records[str(entity_id)] = dict(row)

        fact_links: dict[int, list[int]] = {fact_id: [] for fact_id in fact_ids}
        for row in links:
            fact_id, entity_id = _legacy_int_id(row["fact_id"]), _legacy_int_id(row["entity_id"])
            if fact_id not in fact_ids or entity_id not in entity_ids:
                raise ValueError("orphan legacy fact_entities reference")
            if entity_id in fact_links[fact_id]:
                raise ValueError("duplicate legacy fact_entities reference")
            fact_links[fact_id].append(entity_id)

        if entities:
            stage(tree / "HolographicMemory" / "entities.json", {
                "next_id": max(entity_ids) + 1,
                "by_name": by_name,
                "records": records,
            })
        counts["holographic_entities"] = len(entities)
        counts["holographic_links"] = len(links)

        for row in rows:
            vec = row.get("hrr_vector")
            if vec is not None and not isinstance(vec, (bytes, bytearray)):
                raise ValueError("invalid legacy fact HRR vector")
            data = dict(row)
            data["hrr_vector_b64"] = (
                base64.b64encode(vec).decode("ascii") if vec is not None else None
            )
            data.pop("hrr_vector", None)
            data["entity_ids"] = fact_links[_legacy_int_id(row["fact_id"])]
            stage(
                tree / "HolographicMemory" / "facts"
                / f"{_safe_id(row.get('fact_id'))}.json",
                data,
            )
        counts["holographic_facts"] = len(rows)

        for row in banks:
            name = row.get("bank_name")
            vector = row.get("vector")
            if not isinstance(name, str) or not name:
                raise ValueError("invalid legacy memory bank name")
            if not isinstance(vector, (bytes, bytearray)):
                raise ValueError("invalid legacy memory bank vector")
            # Match MemoryStore._bank_path exactly; stage() rejects
            # collisions (e.g. names differing only by ':' and '_').
            filename = quote(name.replace(":", "_"), safe="") + ".json"
            data = dict(row)
            data["vector_b64"] = base64.b64encode(vector).decode("ascii")
            data.pop("vector", None)
            stage(tree / "HolographicMemory" / "banks" / filename, data)
        counts["holographic_banks"] = len(banks)

    # API responses
    db = home / "response_store.db"
    if db.exists():
        conn = sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            rows = _read_table(conn, "responses")
            conv = _read_table(conn, "conversations", optional=True)
        finally:
            conn.close()
        n = 0
        for row in rows:
            data = json.loads(row.get("data") or "null")
            record = {"response_id": row.get("response_id"), "data": data, "accessed_at": row.get("accessed_at")}
            stage(tree / "ApiResponses" / f"{_safe_id(row.get('response_id'))}.json", record)
            n += 1
        counts["api_responses"] = n
        if conv:
            # A dict comprehension silently drops NULL/empty names and
            # overwrites earlier rows when names are duplicated. Reject
            # lossy exports before any Vault file is published.
            mapping: dict[str, object] = {}
            for row in conv:
                name = row.get("name")
                if not isinstance(name, str) or not name:
                    raise ValueError("invalid legacy conversation name")
                if name in mapping:
                    raise ValueError(f"duplicate legacy conversation name: {name!r}")
                mapping[name] = row.get("response_id")
            stage(tree / "ApiResponses" / "conversations.json", mapping)

    if args.verify and not counts:
        raise FileNotFoundError("no legacy SQLite sources found for verification")

    # Validate every legacy record against its published Vault JSON without writes.
    # Compare complete records, including base64 vectors, entity links and metadata.
    if args.verify:
        for dest, data in planned:
            if not dest.is_file():
                raise FileNotFoundError(f"missing Vault export: {dest}")
            with dest.open(encoding="utf-8") as stream:
                actual = json.load(stream)
            expected = json.loads(json.dumps(data, ensure_ascii=False, default=str))
            if actual != expected:
                raise ValueError(f"Vault export mismatch: {dest}")

        # Also detect unexpected HolographicMemory facts or banks: checking
        # only expected files could miss stale or duplicated records.
        if (home / "memory_store.db").exists():
            root = tree / "HolographicMemory"
            for sub in ("facts", "banks"):
                directory = root / sub
                _check_destination(vault, directory)
                expected_names = {dest.name for dest, _ in planned if dest.parent == directory}
                actual_names = {p.name for p in directory.glob("*.json")} if directory.is_dir() else set()
                if actual_names != expected_names:
                    raise ValueError(f"Vault export file set mismatch: {directory}")
            if counts["holographic_entities"] == 0 and (root / "entities.json").exists():
                raise ValueError(f"unexpected Vault entity index: {root / 'entities.json'}")
        # The record-by-record comparison above does not detect extra files.
        # Check the complete file set for the other two SQLite-backed stores,
        # just as for HolographicMemory. Only inspect a store if its legacy
        # source exists; unrelated live Vault stores are not migration inputs.
        for source_name, subdir in (
            ("retaindb_queue.db", "RetainQueue"),
            ("response_store.db", "ApiResponses"),
        ):
            if not (home / source_name).exists():
                continue
            directory = tree / subdir
            _check_destination(vault, directory)
            expected_names = {dest.name for dest, _ in planned if dest.parent == directory}
            actual_names = {p.name for p in directory.glob("*.json")} if directory.is_dir() else set()
            if actual_names != expected_names:
                raise ValueError(f"Vault export file set mismatch: {directory}")
    elif not args.dry_run:
        _write_batch(vault, planned, force=args.force)

    print(json.dumps(counts, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
