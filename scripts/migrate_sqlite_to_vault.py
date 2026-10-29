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
import json
import os
import re
import sqlite3
import tempfile
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



def _write_batch(vault: Path, planned: list[tuple[Path, dict]], *, force: bool) -> None:
    """Stage all exports before publishing, even with --force.

    Staging failures cannot partially overwrite existing Vault records.
    Non-force publishes are rolled back on errors. Force-mode publication
    failures may still leave partial overwrites; this is not a transaction.
    """
    prepared: list[tuple[Path, Path]] = []
    published: list[tuple[Path, Path]] = []
    try:
        # Serialization and fsync failures cannot leave published records.
        for dest, data in planned:
            _check_destination(vault, dest)
            if dest.exists():
                raise FileExistsError(f"Vault destination already exists: {dest}")
            dest.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(prefix=f".{dest.name}.tmp-", dir=dest.parent)
            tmp = Path(tmp_name)
            prepared.append((tmp, dest))
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(json.dumps(data, indent=2, ensure_ascii=False, default=str) + "\n")
                stream.flush()
                os.fsync(stream.fileno())

        for tmp, dest in prepared:
            _check_destination(vault, dest)
            if force:
                # Replace only after *every* record has been staged and fsynced.
                os.replace(tmp, dest)
            else:
                # Never overwrite a file created by another writer since preflight.
                os.link(tmp, dest)
                published.append((tmp, dest))
    except BaseException as exc:
        rollback_errors = []
        for tmp, dest in reversed(published):
            try:
                source_stat = tmp.stat(follow_symlinks=False)
                target_stat = dest.stat(follow_symlinks=False)
                # Do not remove a path replaced by a concurrent Vault writer.
                if (source_stat.st_dev, source_stat.st_ino) == (
                    target_stat.st_dev, target_stat.st_ino
                ):
                    dest.unlink()
            except FileNotFoundError:
                pass
            except OSError as error:
                rollback_errors.append(f"{dest}: {error}")
        if rollback_errors:
            exc.add_note("Vault export rollback incomplete: " + "; ".join(rollback_errors))
        raise
    finally:
        for tmp, _ in prepared:
            tmp.unlink(missing_ok=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="Export legacy SQLite stores to Vault JSON (read-only).")
    ap.add_argument("--vault", required=True, help="Obsidian Vault root")
    ap.add_argument("--home", default=None, help="HERMES_HOME (default: env or ~/.hermes)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--verify", action="store_true", help="Compare Vault export with legacy SQLite without writing")
    args = ap.parse_args()
    if args.verify and (args.dry_run or args.force):
        ap.error("--verify cannot be combined with --dry-run or --force")

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
