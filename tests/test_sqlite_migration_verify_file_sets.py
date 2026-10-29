"""Verify that legacy queue and API response exports contain no extra Vault files."""

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "migrate_sqlite_to_vault.py"


def _seed(home: Path) -> tuple[Path, Path]:
    home.mkdir()
    queue_db = home / "retaindb_queue.db"
    with sqlite3.connect(queue_db) as conn:
        conn.execute(
            "CREATE TABLE pending (id INTEGER PRIMARY KEY, user_id TEXT, "
            "session_id TEXT, messages_json TEXT, created_at TEXT, last_error TEXT)"
        )
        conn.execute("INSERT INTO pending VALUES (1, 'u1', 's1', '[]', '2026-01-01', NULL)")

    responses_db = home / "response_store.db"
    with sqlite3.connect(responses_db) as conn:
        conn.execute("CREATE TABLE responses (response_id TEXT, data TEXT, accessed_at TEXT)")
        conn.execute("INSERT INTO responses VALUES ('resp-1', '{\"ok\":true}', '2026-01-01')")
        conn.execute("CREATE TABLE conversations (name TEXT, response_id TEXT)")
    return queue_db, responses_db


def _run(home: Path, vault: Path, *flags: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--home", str(home), "--vault", str(vault), *flags],
        capture_output=True, text=True, check=False,
    )


def test_verify_queue_and_responses_roundtrip_is_read_only(tmp_path):
    home, vault = tmp_path / "home", tmp_path / "vault"
    sources = _seed(home)
    exported = _run(home, vault)
    assert exported.returncode == 0, exported.stderr
    root = vault / "06_Automation" / "Hermes"
    assert json.loads((root / "RetainQueue" / "1.json").read_text())["user_id"] == "u1"
    assert json.loads((root / "ApiResponses" / "resp-1.json").read_text())["data"] == {"ok": True}
    before_sources = [p.read_bytes() for p in sources]
    before_exports = {str(p): p.read_bytes() for p in root.rglob("*.json")}

    result = _run(home, vault, "--verify")

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["retaindb_queue"] == 1
    assert json.loads(result.stdout)["api_responses"] == 1
    assert [p.read_bytes() for p in sources] == before_sources
    assert {str(p): p.read_bytes() for p in root.rglob("*.json")} == before_exports


@pytest.mark.parametrize(
    ("subdir", "extra_name"),
    [
        ("RetainQueue", "99.json"),
        ("ApiResponses", "ghost.json"),
        ("ApiResponses", "conversations.json"),
    ],
)
def test_verify_rejects_unexpected_json_without_mutation(tmp_path, subdir, extra_name):
    home, vault = tmp_path / "home", tmp_path / "vault"
    sources = _seed(home)
    assert _run(home, vault).returncode == 0
    extra = vault / "06_Automation" / "Hermes" / subdir / extra_name
    extra.write_text('{"unexpected":true}', encoding="utf-8")
    before_sources = [p.read_bytes() for p in sources]
    before_extra = extra.read_bytes()

    result = _run(home, vault, "--verify")

    assert result.returncode != 0
    assert "Vault export file set mismatch" in result.stderr
    assert [p.read_bytes() for p in sources] == before_sources
    assert extra.read_bytes() == before_extra


@pytest.mark.parametrize(
    ("names", "expected_error"),
    [
        (("chat", "chat"), "duplicate legacy conversation name"),
        ((None,), "invalid legacy conversation name"),
        (("",), "invalid legacy conversation name"),
    ],
)
def test_conversation_alias_export_rejects_lossy_rows_before_writes(
    tmp_path, names, expected_error
):
    home, vault = tmp_path / "home", tmp_path / "vault"
    sources = _seed(home)
    with sqlite3.connect(sources[1]) as conn:
        conn.executemany(
            "INSERT INTO conversations (name, response_id) VALUES (?, ?)",
            [(name, "resp-1") for name in names],
        )
    before_sources = [p.read_bytes() for p in sources]

    result = _run(home, vault)

    assert result.returncode != 0
    assert expected_error in result.stderr
    # The earlier RetainQueue and response records are staged, not written.
    assert not (vault / "06_Automation" / "Hermes").exists()
    assert [p.read_bytes() for p in sources] == before_sources
