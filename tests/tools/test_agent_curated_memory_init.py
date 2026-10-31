"""Regression: enabled curated memory must not fail open during agent startup.

Exercise the actual initialization block from AIAgent.__init__ in isolation.
The full constructor configures unrelated network/tool integrations, so the
focused AST extraction keeps this test deterministic and offline.
"""

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest


def _run_curated_memory_init(config, *, skip_memory=False):
    source = (Path(__file__).resolve().parents[2] / "run_agent.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    agent = next(
        node for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "AIAgent"
    )
    init = next(
        node for node in agent.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "__init__"
    )
    block = next(
        node for node in init.body
        if isinstance(node, ast.If)
        and "self._memory_store.load_from_disk()" in (
            ast.get_source_segment(source, node) or ""
        )
    )
    code = compile(
        ast.fix_missing_locations(ast.Module(body=[block], type_ignores=[])),
        "<curated-memory-init>",
        "exec",
    )
    instance = SimpleNamespace(
        _memory_store=None,
        _memory_enabled=False,
        _user_profile_enabled=False,
        _memory_nudge_interval=10,
    )
    exec(
        code,
        {"logger": __import__("logging").getLogger(__name__)},
        {"self": instance, "_agent_cfg": {"memory": config}, "skip_memory": skip_memory},
    )
    return instance


def test_enabled_curated_memory_missing_vault_fails_agent_init(monkeypatch):
    monkeypatch.delenv("HERMES_OBSIDIAN_VAULT", raising=False)
    with pytest.raises(RuntimeError, match="Vault-backed curated memory"):
        _run_curated_memory_init({"memory_enabled": True})


def test_enabled_user_profile_missing_vault_fails_agent_init(monkeypatch):
    monkeypatch.delenv("HERMES_OBSIDIAN_VAULT", raising=False)
    with pytest.raises(RuntimeError, match="Vault-backed curated memory"):
        _run_curated_memory_init({"user_profile_enabled": True})


def test_disabled_curated_memory_does_not_require_vault(monkeypatch):
    monkeypatch.delenv("HERMES_OBSIDIAN_VAULT", raising=False)
    instance = _run_curated_memory_init(
        {"memory_enabled": False, "user_profile_enabled": False}
    )
    assert instance._memory_store is None


def test_skip_memory_does_not_require_vault(monkeypatch):
    monkeypatch.delenv("HERMES_OBSIDIAN_VAULT", raising=False)
    instance = _run_curated_memory_init({"memory_enabled": True}, skip_memory=True)
    assert instance._memory_store is None


def test_enabled_curated_memory_loads_from_valid_vault(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    vault.mkdir()
    monkeypatch.setenv("HERMES_OBSIDIAN_VAULT", str(vault))
    instance = _run_curated_memory_init({"memory_enabled": True})
    assert instance._memory_store is not None
    assert (vault / "06_Automation" / "Hermes" / "CuratedMemory").is_dir()
