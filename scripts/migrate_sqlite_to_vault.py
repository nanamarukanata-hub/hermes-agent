#!/usr/bin/env python3
"""Read-only export of legacy SQLite stores to Obsidian Vault JSON.

Usage:
    python3 scripts/migrate_sqlite_to_vault.py --vault <vault-root> [--dry-run]

Exports, when the legacy file exists and the Vault target is empty
(unless --force):
  - <hermes_home>/retaindb_queue.db  -> Vault/06_Automation/Hermes/RetainQueue/<id>.json
  - <hermes_home>/memory_store.db    -> Vault/06_Automation/Hermes/HolographicMemory/facts/<id>.json
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
import sqlite3
import sys
from pathlib import Path


def _read_table(conn: sqlite3.Connection, name: str) -> list[dict]:
    try:
        cur = conn.execute(f"SELECT * FROM {name}")
    except sqlite3.Error:
        return []
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def _write_json(dest: Path, data: dict, dry: bool, force: bool) -> bool:
    if dest.exists() and not force:
        return False
    if dry:
        return True
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f".{dest.name}.tmp-{os.getpid()}")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    os.replace(tmp, dest)
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description="Export legacy SQLite stores to Vault JSON (read-only).")
    ap.add_argument("--vault", required=True, help="Obsidian Vault root")
    ap.add_argument("--home", default=None, help="HERMES_HOME (default: env or ~/.hermes)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    home = Path(args.home).expanduser() if args.home else Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes")).expanduser()
    vault = Path(args.vault).expanduser()
    tree = vault / "06_Automation" / "Hermes"
    counts: dict[str, int] = {}

    # RetainDB queue
    db = home / "retaindb_queue.db"
    if db.exists():
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            rows = _read_table(conn, "pending")
        finally:
            conn.close()
        n = 0
        for row in rows:
            try:
                messages = json.loads(row.get("messages_json") or "[]")
            except json.JSONDecodeError:
                messages = []
            data = {"user_id": row.get("user_id"), "session_id": row.get("session_id"), "messages": messages,
                    "created_at": row.get("created_at"), "last_error": row.get("last_error")}
            if _write_json(tree / "RetainQueue" / f"{row.get('id')}.json", data, args.dry_run, args.force):
                n += 1
        counts["retaindb_queue"] = n

    # Holographic facts
    db = home / "memory_store.db"
    if db.exists():
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            rows = _read_table(conn, "facts")
        finally:
            conn.close()
        n = 0
        for row in rows:
            vec = row.get("hrr_vector")
            data = dict(row)
            data["hrr_vector_b64"] = base64.b64encode(vec).decode("ascii") if isinstance(vec, (bytes, bytearray)) else None
            data.pop("hrr_vector", None)
            if _write_json(tree / "HolographicMemory" / "facts" / f"{row.get('fact_id')}.json", data, args.dry_run, args.force):
                n += 1
        counts["holographic_facts"] = n

    # API responses
    db = home / "response_store.db"
    if db.exists():
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            rows = _read_table(conn, "responses")
            conv = _read_table(conn, "conversations")
        finally:
            conn.close()
        n = 0
        for row in rows:
            try:
                data = json.loads(row.get("data") or "null")
            except json.JSONDecodeError:
                data = None
            record = {"response_id": row.get("response_id"), "data": data, "accessed_at": row.get("accessed_at")}
            if _write_json(tree / "ApiResponses" / f"{row.get('response_id')}.json", record, args.dry_run, args.force):
                n += 1
        counts["api_responses"] = n
        if conv and not args.dry_run:
            mapping = {r.get("name"): r.get("response_id") for r in conv if r.get("name")}
            dest = tree / "ApiResponses" / "conversations.json"
            if not dest.exists() or args.force:
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(json.dumps(mapping, indent=2) + "\n", encoding="utf-8")

    print(json.dumps(counts, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
