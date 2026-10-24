"""One-time read-only SQLite -> Obsidian migration. Never deletes legacy data.

Usage:
  HERMES_OBSIDIAN_VAULT=/local/path/to/Vault \
    python scripts/migrate_sessions_to_obsidian.py --source ~/.hermes/state.db
  HERMES_OBSIDIAN_VAULT=/local/path/to/Vault \
    python scripts/migrate_sessions_to_obsidian.py --source ~/.hermes/state.db --commit

Stop Hermes gateway and CLI before --commit. Default is dry-run.
The only sqlite3 import in this feature is in this explicit offline migration tool.
"""
from __future__ import annotations
import argparse
import json
import sqlite3
import sys
from pathlib import Path

from hermes_obsidian_state import SessionDB

_JSON_FIELDS = frozenset({
    "model_config", "tool_calls", "reasoning_details",
    "codex_reasoning_items", "codex_message_items",
})


def _decode(field, value):
    if value is None:
        return None
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    if field == "content" and isinstance(value, str) and value.startswith("\x00json:"):
        return json.loads(value[6:])
    if field in _JSON_FIELDS and isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return value
    return value


def _fetch_source(dbfile):
    source = Path(dbfile).expanduser().resolve(strict=True)
    if not source.is_file():
        raise ValueError("SQLite source must be a file")
    # Open immutable? No: allow WAL replay so migrated data includes latest
    # committed transactions. URI mode=ro never creates or changes source.
    con = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        with con:
            names = {r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}
            if not {"sessions", "messages"}.issubset(names):
                raise ValueError("Not a Hermes session database")
            sessions = [dict(x) for x in con.execute(
                "SELECT * FROM sessions ORDER BY started_at, id"
            ).fetchall()]
            messages = {}
            for row in con.execute(
                "SELECT * FROM messages ORDER BY timestamp, id"
            ).fetchall():
                msg = {k: _decode(k, v) for k, v in dict(row).items()}
                sid = msg.get("session_id")
                messages.setdefault(sid, []).append(msg)
        return sessions, messages
    finally:
        con.close()


def migrate(dbfile, commit=False):
    sessions, messages = _fetch_source(dbfile)
    target = SessionDB()
    # Preflight the entire dataset BEFORE writing any session. Reject invalid
    # session identifiers and conflicting destinations.
    prepared = []
    for session in sessions:
        sid = session["id"]
        target._folder(sid)
        record = {k: _decode(k, v) for k, v in session.items()}
        record["messages"] = messages.get(sid, [])
        record["message_count"] = len(record["messages"])
        destination = target._load(sid)
        if destination is not None:
            if (destination.get("messages") != record["messages"] or
                destination.get("source") != record.get("source")):
                raise RuntimeError(f"Conflicting Vault session: {sid}")
        prepared.append((sid, record, destination is not None))
    unknown = sorted(set(messages) - {sid for sid, _, _ in prepared})
    if unknown:
        raise RuntimeError(f"Orphan message session IDs: {unknown[:10]}")
    result = {
        "mode": "commit" if commit else "dry-run", "legacy_source": str(Path(dbfile).resolve()),
        "vault_destination": str(target.root), "sessions": len(prepared),
        "messages": sum(len(rec["messages"]) for _, rec, _ in prepared),
        "already_migrated": sum(already for _, _, already in prepared),
        "written": 0,
    }
    if not commit:
        return result
    with target._guard():
        for sid, record, already in prepared:
            if already:
                continue
            # Recheck under the writer lock; never silently overwrite a
            # concurrent edit, even if dry-run passed before another writer.
            if target._load(sid) is not None:
                raise RuntimeError(f"Destination created during migration: {sid}")
            target._save(sid, record)
            loaded = target._load(sid)
            if loaded is None or loaded.get("messages") != record["messages"]:
                raise RuntimeError(f"Post-write verification failed: {sid}")
            result["written"] += 1
    if target.message_count() < result["messages"]:
        raise RuntimeError("Verification failed: fewer messages than source")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, help="Path to the legacy state.db")
    parser.add_argument("--commit", action="store_true", help="Write into Vault; default is read-only dry-run")
    args = parser.parse_args()
    print(json.dumps(migrate(args.source, args.commit), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
