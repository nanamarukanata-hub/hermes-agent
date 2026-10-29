"""Regression tests for runtime SQLite detection without writing real data."""

from pathlib import Path

import pytest

from hermes_hybrid.sqlite_audit import audit


@pytest.mark.parametrize("source", [
    "def open_db():\n    import sqlite3\n",
    "if True: import aiosqlite\n",
    "x = 1; import sqlite3 as db\n",
    "from sqlite3 import connect\n",
    "import importlib\nconn = importlib.import_module('sqlite3')\n",
    "module = __import__('aiosqlite')\n",
    "import importlib as il\nconn = il.import_module('sqlite3')\n",
    "from importlib import import_module as load\nconn = load('aiosqlite')\n",
])
def test_detects_nested_aliased_and_dynamic_imports(tmp_path, source):
    path = tmp_path / "runtime.py"
    path.write_text(source, encoding="utf-8")
    assert audit(tmp_path)["sqlite_imports"] == [str(path)]


def test_migration_filename_is_not_an_implicit_exemption(tmp_path):
    path = tmp_path / "migrate_hidden.py"
    path.write_text("import sqlite3\n", encoding="utf-8")
    assert audit(tmp_path)["sqlite_imports"] == [str(path)]


def test_comments_and_strings_do_not_count_as_imports(tmp_path):
    (tmp_path / "runtime.py").write_text(
        "# import sqlite3\nexample = 'import aiosqlite'\n", encoding="utf-8"
    )
    assert audit(tmp_path)["sqlite_imports"] == []


def test_invalid_python_is_not_silently_marked_clean(tmp_path):
    (tmp_path / "broken.py").write_text("def broken(:\n", encoding="utf-8")
    with pytest.raises(SyntaxError):
        audit(tmp_path)


def test_database_files_are_reported_without_modification(tmp_path):
    for name in ("state.db", "memory.sqlite", "cache.sqlite3"):
        (tmp_path / name).write_bytes(b"untouched")
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}
    result = audit(tmp_path)
    assert {Path(p).name for p in result["db_files"]} == set(before)
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()} == before
