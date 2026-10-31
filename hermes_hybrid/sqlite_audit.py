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
    # Resolve only unambiguous module-level string assignments. This catches
    # common SQLite URLs and import names passed through constants, without
    # treating an unrelated string literal as an active database connection.
    bindings: dict[str, list[ast.expr]] = {}
    for statement in tree.body:
        if isinstance(statement, ast.Assign):
            for target in statement.targets:
                if isinstance(target, ast.Name):
                    bindings.setdefault(target.id, []).append(statement.value)
        elif isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name):
            if statement.value is not None:
                bindings.setdefault(statement.target.id, []).append(statement.value)
        elif isinstance(statement, ast.AugAssign) and isinstance(statement.target, ast.Name):
            bindings.setdefault(statement.target.id, []).append(statement.value)
    constants = {
        name: values[0].value
        for name, values in bindings.items()
        if len(values) == 1
        and isinstance(values[0], ast.Constant)
        and isinstance(values[0].value, str)
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(alias.name.split(".", 1)[0] in _SQLITE_MODULES for alias in node.names):
                return True
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.module.split(".", 1)[0] in _SQLITE_MODULES:
                return True
        elif isinstance(node, ast.Call):
            # Both importlib and SQLAlchemy accept the relevant argument by
            # keyword. An audit must not miss import_module(name="sqlite3")
            # or create_engine(url="sqlite:///memory.db").
            first = node.args[0] if node.args else next(
                (kw.value for kw in node.keywords if kw.arg in {"name", "url"}),
                None,
            )
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                argument = first.value
            elif isinstance(first, ast.Name):
                argument = constants.get(first.id)
            else:
                argument = None
            if argument is None:
                continue
            module = argument.split(".", 1)[0]
            func = node.func
            if module in _SQLITE_MODULES:
                if isinstance(func, ast.Name) and func.id in {"__import__", *import_module_names}:
                    return True
                if isinstance(func, ast.Attribute) and func.attr == "import_module":
                    if isinstance(func.value, ast.Name) and func.value.id in importlib_names:
                        return True
            # SQLAlchemy opens SQLite without an explicit sqlite3 import.
            # Match known engine factories, not arbitrary strings mentioning SQLite.
            if argument.startswith(("sqlite:", "sqlite+")) and "://" in argument:
                if isinstance(func, ast.Name) and func.id in engine_names:
                    return True
                if isinstance(func, ast.Attribute) and func.attr in _ENGINE_FACTORIES:
                    # Support both sqlalchemy.create_engine(...) and the
                    # nested sqlalchemy.ext.asyncio.create_async_engine(...).
                    root = func.value
                    while isinstance(root, ast.Attribute):
                        root = root.value
                    if isinstance(root, ast.Name) and root.id in sqlalchemy_names:
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
