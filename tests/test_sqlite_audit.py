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



@pytest.mark.parametrize("source", [
    "from sqlalchemy import create_engine\\ndb = create_engine('sqlite:///session.db')\\n",
    "from sqlalchemy import create_engine as ce\\ndb = ce('sqlite+pysqlite:///session.db')\\n",
    "import sqlalchemy as sa\\ndb = sa.create_engine('sqlite:///:memory:')\\n",
    "from sqlalchemy.ext.asyncio import create_async_engine as ae\\ndb = ae('sqlite+aiosqlite:///session.db')\\n",
    "import sqlalchemy.ext.asyncio as sa\\ndb = sa.create_async_engine('sqlite+aiosqlite:///session.db')\\n",
])
def test_sqlalchemy_sqlite_engine_without_sqlite_import_is_detected(tmp_path, source):
    path = tmp_path / "runtime.py"
    path.write_text(source, encoding="utf-8")
    assert audit(tmp_path)["sqlite_imports"] == [str(path)]


@pytest.mark.parametrize("source", [
    "from sqlalchemy import create_engine\\ndb = create_engine('postgresql+psycopg://localhost/db')\\n",
    "example = 'sqlite:///session.db'\\n",
    "def create_engine(url): return url\\ndb = create_engine('sqlite:///not_sqlalchemy.db')\\n",
])
def test_sqlalchemy_audit_avoids_unrelated_strings_and_engines(tmp_path, source):
    path = tmp_path / "runtime.py"
    path.write_text(source, encoding="utf-8")
    assert audit(tmp_path)["sqlite_imports"] == []


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


def test_sqlite_sidecars_are_detected_even_without_main_db(tmp_path):
    """WAL, SHM and rollback journals are persistent SQLite artifacts."""
    extensions = (".db", ".sqlite", ".sqlite3")
    suffixes = ("-wal", "-shm", "-journal")
    names = {f"state{ext}{suffix}" for ext in extensions for suffix in suffixes}
    for name in names:
        (tmp_path / name).write_bytes(b"untouched")
    (tmp_path / "unrelated.db-backup").write_bytes(b"ignore")
    (tmp_path / "directory.db").mkdir()
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}
    assert {Path(p).name for p in audit(tmp_path)["db_files"]} == names
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()} == before

# These are active Discord/session/Vault runtime entry points, not offline
# SQLite-to-Vault migration tools. A regression here would reintroduce a
# database dependency even when the synthetic audit fixtures still pass.
_RUNTIME_SQLITE_FREE_MODULES = (
    "hermes_state.py",
    "hermes_obsidian_state.py",
    "gateway/session.py",
    "gateway/run.py",
    "gateway/platforms/discord.py",
    "gateway/platforms/helpers.py",
    "hermes_hybrid/obsidian_store.py",
    "hermes_hybrid/session_manager.py",
    "hermes_hybrid/gateway_adapter.py",
    "hermes_hybrid/vault_kv.py",
    "hermes_cli/kanban_db.py",
)


@pytest.mark.parametrize("relative_path", _RUNTIME_SQLITE_FREE_MODULES)
def test_core_runtime_modules_do_not_import_sqlite(relative_path):
    from hermes_hybrid.sqlite_audit import _uses_sqlite

    path = Path(__file__).resolve().parents[1] / relative_path
    assert not _uses_sqlite(path.read_text(encoding="utf-8"), str(path)), (
        f"SQLite runtime import reintroduced in {relative_path}"
    )
