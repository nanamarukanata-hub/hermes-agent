"""Lossless, read-only migration of legacy holographic memory to Vault."""

import base64
import json
import sqlite3
import subprocess
import sys
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "migrate_sqlite_to_vault.py"


def _legacy_database(home: Path, *, orphan=False, ambiguous=False) -> Path:
    home.mkdir()
    db = home / "memory_store.db"
    with sqlite3.connect(db) as conn:
        conn.executescript(
            """
            CREATE TABLE facts (
                fact_id INTEGER PRIMARY KEY, content TEXT, category TEXT,
                hrr_vector BLOB, trust_score REAL
            );
            CREATE TABLE entities (
                entity_id INTEGER PRIMARY KEY, name TEXT, entity_type TEXT,
                aliases TEXT, created_at TEXT
            );
            CREATE TABLE fact_entities (fact_id INTEGER, entity_id INTEGER);
            CREATE TABLE memory_banks (
                bank_id INTEGER PRIMARY KEY, bank_name TEXT, vector BLOB,
                dim INTEGER, fact_count INTEGER, updated_at TEXT
            );
            """
        )
        conn.execute(
            "INSERT INTO facts VALUES (?, ?, ?, ?, ?)",
            (1, "Alice remembers", "notes", b"\x00\xff", 0.8),
        )
        conn.execute(
            "INSERT INTO entities VALUES (?, ?, ?, ?, ?)",
            (7, "Alice", "person", "Al", "2026-01-01"),
        )
        if ambiguous:
            conn.execute(
                "INSERT INTO entities VALUES (?, ?, ?, ?, ?)",
                (8, "alice", "person", "", "2026-01-02"),
            )
        conn.execute(
            "INSERT INTO fact_entities VALUES (?, ?)", (1, 999 if orphan else 7)
        )
        conn.execute(
            "INSERT INTO memory_banks VALUES (?, ?, ?, ?, ?, ?)",
            (3, "cat:notes", b"\x01\xfe", 1024, 1, "2026-01-03"),
        )
    return db


def _run(home: Path, vault: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--home",
            str(home),
            "--vault",
            str(vault),
            *args,
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def test_holographic_entities_links_and_banks_roundtrip(tmp_path):
    home = tmp_path / "home"
    db = _legacy_database(home)
    original = db.read_bytes()
    vault = tmp_path / "vault"

    result = _run(home, vault)
    assert result.returncode == 0, result.stderr
    counts = json.loads(result.stdout)
    assert counts["holographic_facts"] == 1
    assert counts["holographic_entities"] == 1
    assert counts["holographic_links"] == 1
    assert counts["holographic_banks"] == 1

    root = vault / "06_Automation" / "Hermes" / "HolographicMemory"
    fact = json.loads((root / "facts" / "1.json").read_text(encoding="utf-8"))
    index = json.loads((root / "entities.json").read_text(encoding="utf-8"))
    bank = json.loads((root / "banks" / "cat_notes.json").read_text(encoding="utf-8"))
    assert fact["entity_ids"] == [7]
    assert base64.b64decode(fact["hrr_vector_b64"]) == b"\x00\xff"
    assert index["by_name"] == {"Alice": 7}
    assert index["next_id"] == 8
    assert index["records"]["7"]["aliases"] == "Al"
    assert index["records"]["7"]["entity_type"] == "person"
    assert bank["bank_name"] == "cat:notes"
    assert base64.b64decode(bank["vector_b64"]) == b"\x01\xfe"
    assert bank["dim"] == 1024
    assert db.read_bytes() == original


def test_orphan_legacy_entity_link_aborts_before_writing(tmp_path):
    home = tmp_path / "home"
    db = _legacy_database(home, orphan=True)
    original = db.read_bytes()
    vault = tmp_path / "vault"

    result = _run(home, vault)
    assert result.returncode != 0
    assert "orphan legacy fact_entities" in result.stderr
    assert not (vault / "06_Automation").exists()
    assert db.read_bytes() == original


def test_ambiguous_legacy_entity_names_abort_before_writing(tmp_path):
    home = tmp_path / "home"
    _legacy_database(home, ambiguous=True)
    vault = tmp_path / "vault"

    result = _run(home, vault)
    assert result.returncode != 0
    assert "ambiguous legacy entity name" in result.stderr
    assert not (vault / "06_Automation").exists()


def test_full_holographic_dry_run_writes_nothing(tmp_path):
    home = tmp_path / "home"
    _legacy_database(home)
    vault = tmp_path / "vault"

    result = _run(home, vault, "--dry-run")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["holographic_links"] == 1
    assert not (vault / "06_Automation").exists()
