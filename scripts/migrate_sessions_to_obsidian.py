"""One-time read-only SQLite -> Obsidian migration. Never deletes legacy data.

Usage:
  HERMES_OBSIDIAN_VAULT=/local/path/to/Vault \
    python scripts/migrate_sessions_to_obsidian.py --source ~/.hermes/state.db
  HERMES_OBSIDIAN_VAULT=/local/path/to/Vault \
    python scripts/migrate_sessions_to_obsidian.py --source ~/.hermes/state.db --commit
  HERMES_OBSIDIAN_VAULT=/local/path/to/Vault \
    python scripts/migrate_sessions_to_obsidian.py --source ~/.hermes/state.db --verify-only

Stop Hermes gateway and CLI before --commit. Default is dry-run.
The only sqlite3 import in this feature is in this explicit offline migration tool.
"""
from __future__ import annotations
import argparse
import json
import os
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
    con = sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)
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


def migrate(dbfile, commit=False, verify=False):
    if commit and verify:
        raise ValueError('commit and verify are mutually exclusive')
    sessions, messages = _fetch_source(dbfile)
    if verify:
        # SessionDB creates its storage directories on initialization.
        # Verification must never create even empty directories in the Vault.
        configured = os.environ.get("HERMES_OBSIDIAN_VAULT")
        if not configured:
            raise RuntimeError("HERMES_OBSIDIAN_VAULT is required for verification")
        existing = (
            Path(configured).expanduser().resolve()
            / "06_Automation" / "Hermes" / "Sessions"
        )
        if not existing.is_dir():
            raise RuntimeError("Existing Vault Sessions directory required for verification")
    # Dry-run and verification must not create directories in the Vault.
    target = SessionDB(create_dirs=commit)
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
        if destination is not None and destination != record:
            # Matching messages/source alone is insufficient: the existing
            # session may have different ownership, model, timing or usage.
            # Never silently accept a partially migrated or edited record.
            raise RuntimeError(f"Conflicting Vault session: {sid}")
        prepared.append((sid, record, destination is not None))
    unknown = sorted(set(messages) - {sid for sid, _, _ in prepared})
    if unknown:
        raise RuntimeError(f"Orphan message session IDs: {unknown[:10]}")
    result = {
        "mode": "verify" if verify else ("commit" if commit else "dry-run"),
        "legacy_source": str(Path(dbfile).resolve()),
        "vault_destination": str(target.root), "sessions": len(prepared),
        "messages": sum(len(rec["messages"]) for _, rec, _ in prepared),
        "already_migrated": sum(already for _, _, already in prepared),
        "written": 0,
        "verified": 0,
    }
    if verify:
        missing = [sid for sid, _, already in prepared if not already]
        if missing:
            raise RuntimeError(f"Missing Vault sessions: {missing[:10]}")
        # Preflight above compares every source session and its entire message
        # list with the Vault, rather than relying on aggregate counts.
        result["verified"] = len(prepared)
        return result
    if not commit:
        return result
    with target._guard():
        for sid, record, already in prepared:
            # Recheck ALL destinations under the writer lock, including ones
            # that matched during preflight. A running gateway may have
            # modified an existing record between the two checks.
            current = target._load(sid)
            if already:
                if current != record:
                    raise RuntimeError(f"Conflicting Vault session changed during migration: {sid}")
                continue
            if current is not None:
                raise RuntimeError(f"Destination created during migration: {sid}")
            target._save(sid, record)
            loaded = target._load(sid)
            if loaded != record:
                raise RuntimeError(f"Post-write verification failed: {sid}")
            result["written"] += 1
    if target.message_count() < result["messages"]:
        raise RuntimeError("Verification failed: fewer messages than source")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, help="Path to the legacy state.db")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--commit", action="store_true", help="Write into Vault; default is read-only dry-run")
    modes.add_argument("--verify-only", action="store_true", help="Compare every legacy session and message with existing Vault data without writing")
    args = parser.parse_args()
    print(json.dumps(migrate(args.source, commit=args.commit, verify=args.verify_only), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
