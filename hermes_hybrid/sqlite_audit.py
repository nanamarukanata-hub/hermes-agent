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
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            importlib_names.update(
                alias.asname or "importlib"
                for alias in node.names if alias.name == "importlib"
            )
        elif isinstance(node, ast.ImportFrom) and node.module == "importlib":
            import_module_names.update(
                alias.asname or alias.name
                for alias in node.names if alias.name == "import_module"
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
            if module not in _SQLITE_MODULES:
                continue
            func = node.func
            if isinstance(func, ast.Name) and func.id in {"__import__", *import_module_names}:
                return True
            if isinstance(func, ast.Attribute) and func.attr == "import_module":
                if isinstance(func.value, ast.Name) and func.value.id in importlib_names:
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
    for suffix in ("*.db", "*.sqlite", "*.sqlite3"):
        db_files.extend(str(p) for p in sorted(root.rglob(suffix)))
    return {"sqlite_imports": imports, "db_files": sorted(db_files)}
