"""Audit a directory for runtime SQLite use.

Returns every Python file that imports sqlite3 (or aiosqlite) and
every *.db / *.sqlite file found, excluding migration tools that are
explicitly marked read-only in their own filename (``migrate_*``).
The hybrid package itself must audit clean.
"""
from __future__ import annotations

import re
from pathlib import Path

_IMPORT_RE = re.compile(r"^\s*(import|from)\s+(sqlite3|aiosqlite)\b", re.MULTILINE)


def audit(path: str | Path) -> dict[str, list[str]]:
    root = Path(path)
    imports: list[str] = []
    db_files: list[str] = []
    for py in sorted(root.rglob("*.py")):
        if py.name.startswith("migrate_"):
            continue
        if _IMPORT_RE.search(py.read_text(encoding="utf-8", errors="ignore")):
            imports.append(str(py))
    for suffix in ("*.db", "*.sqlite", "*.sqlite3"):
        db_files.extend(str(p) for p in sorted(root.rglob(suffix)))
    return {"sqlite_imports": imports, "db_files": db_files}
