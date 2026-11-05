"""Read-only migration of pending RetainDB SQLite queue rows into Obsidian Vault.

Stop Hermes before running to prevent writes during migration. The source
SQLite database is opened read-only and never changed or removed. Run once
with --apply after reviewing the default dry-run counts.

Example:
  python -m scripts.migrate_retaindb_queue \
      --sqlite-db ~/.hermes/retaindb_queue.db \
      --vault /path/to/ObsidianVault --apply
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
from urllib.parse import quote

from hermes_hybrid.vault_kv import VaultDir


def migrate(sqlite_db: Path, vault: Path, *, apply: bool = False) -> dict[str, int]:
    sqlite_db = Path(sqlite_db).expanduser().resolve(strict=True)
    vault = Path(vault).expanduser().resolve(strict=True)
    if not sqlite_db.is_file() or not vault.is_dir():
        raise ValueError("SQLite source must be a file and Vault must exist")
    # SQLite URI mode=ro prevents implicit creation and all source writes.
    uri = "file:" + quote(str(sqlite_db), safe="/") + "?mode=ro"
    queue_dir = vault / "06_Automation" / "Hermes" / "RetainQueue"
    store = VaultDir(queue_dir) if apply else None
    counts = {"found": 0, "migrated": 0, "already_present": 0, "invalid": 0}
    with sqlite3.connect(uri, uri=True) as conn:
        rows = conn.execute(
            "SELECT id, user_id, session_id, messages_json, created_at, last_error "
            "FROM pending ORDER BY id"
        )
        for row_id, user_id, session_id, messages_json, created_at, last_error in rows:
            counts["found"] += 1
            try:
                messages = json.loads(messages_json)
                if (not isinstance(row_id, int) or row_id < 1
                        or not isinstance(user_id, str)
                        or not isinstance(session_id, str)
                        or not isinstance(messages, list)):
                    raise ValueError("invalid legacy row")
            except (TypeError, ValueError):
                counts["invalid"] += 1
                continue
            record_id = f"legacy-{row_id}"
            path = queue_dir / f"{record_id}.json"
            if path.exists():
                counts["already_present"] += 1
                continue
            if apply:
                store.write(record_id, {
                    "id": record_id,
                    "user_id": user_id,
                    "session_id": session_id,
                    "messages": messages,
                    "created_at": created_at,
                    "last_error": last_error,
                })
                counts["migrated"] += 1
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sqlite-db", required=True, type=Path)
    parser.add_argument("--vault", required=True, type=Path)
    parser.add_argument("--apply", action="store_true",
                        help="Write Vault JSON records; default is dry-run")
    args = parser.parse_args()
    print(json.dumps(migrate(args.sqlite_db, args.vault, apply=args.apply)))


if __name__ == "__main__":
    main()
