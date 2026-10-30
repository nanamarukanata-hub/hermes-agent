"""Detect SQLite imports and database files inside a runtime source tree.

Audit *runtime* directories rather than a repository root containing offline
migration scripts.  Every Python file in the selected tree is inspected,
including files named ``migrate_*``: filenames alone cannot prove a module
is read-only or excluded from runtime imports.
"""
from __future__ import annotations

import ast
from pathlib import Path

_SQLITE_MODULES = frozenset({"sqlite3", "aiosqlite"})
_ENGINE_FACTORIES = frozenset({"create_engine", "create_async_engine"})


def _uses_sqlite(source: str, filename: str) -> bool:
    """Find direct and common dynamic SQLite imports, including nested ones.

    Parsing instead of matching lines also avoids reporting commented-out
    imports or strings that merely mention ``sqlite3``.  Invalid Python is
    an audit failure, not evidence that a file is SQLite-free.
    """
    tree = ast.parse(source, filename=filename)
    # Resolve the common importlib aliases before looking for dynamic calls.
    importlib_names = {"importlib"}
    import_module_names: set[str] = set()
    sqlalchemy_names: set[str] = set()
    engine_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            importlib_names.update(
                alias.asname or "importlib"
                for alias in node.names if alias.name == "importlib"
            )
            sqlalchemy_names.update(
                alias.asname or "sqlalchemy"
                for alias in node.names if alias.name.startswith("sqlalchemy")
            )
        elif isinstance(node, ast.ImportFrom):
            if node.module == "importlib":
                import_module_names.update(
                    alias.asname or alias.name
                    for alias in node.names if alias.name == "import_module"
                )
            if node.module in {"sqlalchemy", "sqlalchemy.ext.asyncio"}:
                engine_names.update(
                    alias.asname or alias.name
                    for alias in node.names if alias.name in _ENGINE_FACTORIES
                )
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(alias.name.split(".", 1)[0] in _SQLITE_MODULES for alias in node.names):
                return True
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.module.split(".", 1)[0] in _SQLITE_MODULES:
                return True
        elif isinstance(node, ast.Call) and node.args:
            first = node.args[0]
            if not isinstance(first, ast.Constant) or not isinstance(first.value, str):
                continue
            module = first.value.split(".", 1)[0]
            func = node.func
            if module in _SQLITE_MODULES:
                if isinstance(func, ast.Name) and func.id in {"__import__", *import_module_names}:
                    return True
                if isinstance(func, ast.Attribute) and func.attr == "import_module":
                    if isinstance(func.value, ast.Name) and func.value.id in importlib_names:
                        return True
            # SQLAlchemy opens SQLite without an explicit sqlite3 import.
            # Match known engine factories, not arbitrary strings mentioning SQLite.
            if first.value.startswith(("sqlite:", "sqlite+")) and "://" in first.value:
                if isinstance(func, ast.Name) and func.id in engine_names:
                    return True
                if (isinstance(func, ast.Attribute) and func.attr in _ENGINE_FACTORIES
                        and isinstance(func.value, ast.Name) and func.value.id in sqlalchemy_names):
                    return True
    return False


def audit(path: str | Path) -> dict[str, list[str]]:
    """Report SQLite imports and database files beneath ``path``.

    Results are sorted, deterministic, and do not modify the inspected tree.
    """
    root = Path(path)
    imports: list[str] = []
    db_files: list[str] = []
    for py in sorted(root.rglob("*.py")):
        if _uses_sqlite(py.read_text(encoding="utf-8"), str(py)):
            imports.append(str(py))
    # SQLite may leave WAL/SHM/rollback journals after the main DB is gone.
    extensions = (".db", ".sqlite", ".sqlite3")
    endings = extensions + tuple(
        ext + suffix for ext in extensions for suffix in ("-wal", "-shm", "-journal")
    )
    for entry in root.rglob("*"):
        if entry.name.endswith(endings) and (entry.is_file() or entry.is_symlink()):
            db_files.append(str(entry))
    return {"sqlite_imports": imports, "db_files": sorted(db_files)}
