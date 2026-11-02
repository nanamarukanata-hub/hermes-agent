"""Detect SQLite imports and database files inside a runtime source tree.

Audit *runtime* directories rather than a repository root containing offline
migration scripts.  Every Python file in the selected tree is inspected,
including files named ``migrate_*``: filenames alone cannot prove a module
is read-only or excluded from runtime imports.
"""
from __future__ import annotations

import ast
from pathlib import Path

# Common Python SQLite drivers, including third-party replacements and SQLCipher.
_SQLITE_MODULES = frozenset({"sqlite3", "aiosqlite", "pysqlite3", "apsw", "sqlcipher3", "pysqlcipher3"})
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
    # Resolve simple string expressions, including constant concatenations and
    # the fixed prefix of f-strings. A SQLite URL with a dynamic database path
    # is still a SQLite URL and must not evade the runtime audit.
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
    expressions = {name: values[0] for name, values in bindings.items() if len(values) == 1}

    def string_prefix(expr: ast.expr, seen: frozenset[str] = frozenset()) -> tuple[str | None, bool]:
        """Return (known prefix, fully static) without evaluating user code."""
        if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
            return expr.value, True
        if isinstance(expr, ast.Name):
            if expr.id in seen or expr.id not in expressions:
                return None, False
            return string_prefix(expressions[expr.id], seen | {expr.id})
        if isinstance(expr, ast.JoinedStr):
            prefix = ""
            for part in expr.values:
                if isinstance(part, ast.Constant) and isinstance(part.value, str):
                    prefix += part.value
                else:
                    return prefix, False
            return prefix, True
        if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Add):
            left, left_complete = string_prefix(expr.left, seen)
            if left is None:
                return None, False
            if not left_complete:
                return left, False
            right, right_complete = string_prefix(expr.right, seen)
            return left + (right or ""), right is not None and right_complete
        return None, False

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
            argument, fully_static = string_prefix(first) if first is not None else (None, False)
            if not argument:
                continue
            module = argument.split(".", 1)[0]
            func = node.func
            if fully_static and module in _SQLITE_MODULES:
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
