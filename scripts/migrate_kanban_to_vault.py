#!/usr/bin/env python3
"""Read-only export of a legacy Kanban SQLite board to Obsidian Vault JSON.

Usage:
    python3 scripts/migrate_kanban_to_vault.py <kanban.db> <vault-root> [--board slug]

Reads the legacy ``kanban.db`` with a read-only SQLite connection and
writes one JSON file per task under
``<vault>/06_Automation/Hermes/Kanban/<board>/<task_id>.json``,
plus ``links.json``, ``comments.json``, ``events.json`` and
``runs.json`` in the same board directory.  The legacy ``.db`` file
is never modified or deleted; a ``.migrated`` marker records the
source path and export time so a second run refuses to overwrite
already-exported Vault files unless ``--force`` is given.

This script is a migration tool, not runtime persistence: the Kanban
runtime still uses SQLite (see docs/HERMES-HYBRID.md), and this export
is the first step toward a Vault-backed Kanban store.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from pathlib import Path

# Match kanban_db's board slug rules and vault_kv's record-id rules.
_BOARD_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_RECORD_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_RESERVED_NAMES = frozenset({"links", "comments", "events", "runs"})


def _dump_table(conn: sqlite3.Connection, name: str, *, optional: bool = False) -> list[dict]:
    """Read a table without silently discarding schema or I/O errors.

    Older Kanban schemas may lack optional tables, but a missing tasks
    table or an unreadable existing table must abort migration.
    """
    try:
        cur = conn.execute(f"SELECT * FROM {name}")
    except sqlite3.OperationalError as exc:
        if optional and str(exc).lower() == f"no such table: {name}":
            return []
        raise
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def main() -> int:
    ap = argparse.ArgumentParser(description="Export legacy Kanban DB to Vault JSON (read-only).")
    ap.add_argument("db", help="Legacy kanban.db path")
    ap.add_argument("vault", help="Obsidian Vault root")
    ap.add_argument("--board", default="default", help="Board slug (default: default)")
    ap.add_argument("--force", action="store_true", help="Overwrite existing Vault files")
    args = ap.parse_args()

    if not _BOARD_SLUG_RE.fullmatch(args.board):
        print(f"error: invalid board slug: {args.board!r}", file=sys.stderr)
        return 2

    db_path = Path(args.db).expanduser()
    if not db_path.exists():
        print(f"error: legacy DB not found: {db_path}", file=sys.stderr)
        return 2
    out = Path(args.vault).expanduser() / "06_Automation" / "Hermes" / "Kanban" / args.board
    marker = out / ".migrated"
    if marker.exists() and not args.force:
        print(f"error: already migrated (marker {marker} exists; use --force to re-export)", file=sys.stderr)
        return 2
    # A pre-existing symlink must not redirect an export outside the Vault.
    vault_root = Path(args.vault).expanduser().resolve()
    if not out.resolve().is_relative_to(vault_root):
        print("error: board destination resolves outside the Vault", file=sys.stderr)
        return 2

    # as_uri() escapes '?' and '#' in legacy DB filenames. A single
    # read transaction gives all exported tables one consistent snapshot.
    try:
        conn = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("BEGIN")
            tasks = _dump_table(conn, "tasks")
            links = _dump_table(conn, "task_links", optional=True)
            comments = _dump_table(conn, "task_comments", optional=True)
            events = _dump_table(conn, "task_events", optional=True)
            runs = _dump_table(conn, "task_runs", optional=True)
        finally:
            conn.close()
    except sqlite3.Error as exc:
        print(f"error: cannot read legacy Kanban database: {exc}", file=sys.stderr)
        return 2


    # Validate all names and preflight collisions BEFORE any Vault writes.
    # Silent skips would incorrectly mark a partial migration successful.
    task_ids = [str(task.get("id", "")) for task in tasks]
    if (any(not _RECORD_ID_RE.fullmatch(task_id)
            or task_id in (".", "..")
            or task_id in _RESERVED_NAMES for task_id in task_ids)
            or len(task_ids) != len(set(task_ids))):
        print("error: unsafe, duplicate or reserved task id; no files exported", file=sys.stderr)
        return 2
    destinations = [out / f"{task_id}.json" for task_id in task_ids]
    destinations.extend(out / f"{name}.json" for name in ("links", "comments", "events", "runs"))
    if not args.force and any(dest.exists() or dest.is_symlink() for dest in destinations):
        print("error: Vault export destination already exists; use --force to re-export", file=sys.stderr)
        return 2
    out.mkdir(parents=True, exist_ok=True)

    for task, dest in zip(tasks, destinations):
        tmp = dest.with_name(f".{dest.name}.tmp-{os.getpid()}")
        tmp.write_text(json.dumps(task, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
        os.replace(tmp, dest)
    for name, rows in (("links", links), ("comments", comments), ("events", events), ("runs", runs)):
        dest = out / f"{name}.json"
        tmp = dest.with_name(f".{dest.name}.tmp-{os.getpid()}")
        tmp.write_text(json.dumps(rows, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
        os.replace(tmp, dest)
    marker.write_text(json.dumps({"source": str(db_path), "board": args.board, "tasks": len(tasks)}) + "\n", encoding="utf-8")
    print(f"exported {len(tasks)} tasks, {len(links)} links, {len(comments)} comments, {len(events)} events, {len(runs)} runs to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
