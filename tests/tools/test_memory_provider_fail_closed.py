"""Configured memory providers must not silently disappear at agent startup.

The full AIAgent constructor initializes unrelated integrations. Exercise its
real provider-init AST block with deterministic fake providers instead.
"""

import ast
import logging
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from agent.memory_manager import MemoryManager


class FakeProvider:
    name = "test_provider"

    def __init__(self, *, available=True, fail=False):
        self.available = available
        self.fail = fail
        self.initialized = False

    def is_available(self):
        return self.available

    def get_tool_schemas(self):
        return []

    def initialize(self, session_id, **kwargs):
        self.initialized = True
        if self.fail:
            raise OSError("backend unavailable")


def _run_agent_provider_init(monkeypatch, provider, *, name="test_provider", skip=False):
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
        if isinstance(node, ast.FunctionDef) and node.name == "__init__"
    )
    block = next(
        node for node in init.body
        if isinstance(node, ast.If)
        and "self._memory_manager.initialize_all(" in (
            ast.get_source_segment(source, node) or ""
        )
    )
    module = ModuleType("plugins.memory")
    module.load_memory_provider = lambda _name: provider
    monkeypatch.setitem(sys.modules, "plugins.memory", module)
    instance = SimpleNamespace(
        session_id="session-1",
        _session_db=None,
        _user_id=None,
        _user_name=None,
        _chat_id=None,
        _chat_name=None,
        _chat_type=None,
        _thread_id=None,
        _gateway_session_key=None,
        _memory_manager=None,
    )
    scope = {
        "self": instance,
        "skip_memory": skip,
        "mem_config": {"provider": name},
        "platform": "discord",
        "get_hermes_home": lambda: Path("/tmp"),
        "logger": logging.getLogger(__name__),
    }
    exec(
        compile(
            ast.fix_missing_locations(ast.Module(body=[block], type_ignores=[])),
            "<memory-provider-init>",
            "exec",
        ),
        scope,
        scope,
    )
    return instance


def test_manager_default_keeps_optional_provider_compatibility():
    manager = MemoryManager()
    manager.add_provider(FakeProvider(fail=True))
    manager.initialize_all("session-1")


def test_manager_strict_initialization_propagates_failure():
    manager = MemoryManager()
    manager.add_provider(FakeProvider(fail=True))
    with pytest.raises(RuntimeError, match="failed to initialize"):
        manager.initialize_all("session-1", raise_on_failure=True)


def test_configured_provider_initialization_failure_blocks_agent(monkeypatch):
    with pytest.raises(RuntimeError, match="Configured memory provider"):
        _run_agent_provider_init(monkeypatch, FakeProvider(fail=True))


def test_unavailable_configured_provider_blocks_agent(monkeypatch):
    with pytest.raises(RuntimeError, match="Configured memory provider"):
        _run_agent_provider_init(monkeypatch, FakeProvider(available=False))


def test_missing_configured_provider_blocks_agent(monkeypatch):
    with pytest.raises(RuntimeError, match="Configured memory provider"):
        _run_agent_provider_init(monkeypatch, None)


def test_healthy_configured_provider_initializes(monkeypatch):
    provider = FakeProvider()
    instance = _run_agent_provider_init(monkeypatch, provider)
    assert provider.initialized
    assert instance._memory_manager.get_provider(provider.name) is provider


def test_unconfigured_provider_does_not_require_backend(monkeypatch):
    instance = _run_agent_provider_init(monkeypatch, None, name="")
    assert instance._memory_manager is None


def test_skip_memory_does_not_initialize_provider(monkeypatch):
    provider = FakeProvider(fail=True)
    instance = _run_agent_provider_init(monkeypatch, provider, skip=True)
    assert instance._memory_manager is None
    assert not provider.initialized


class SyncProvider(FakeProvider):
    def __init__(self, *, fail_sync=False):
        super().__init__()
        self.fail_sync = fail_sync
        self.events = []

    def sync_turn(self, user_content, assistant_content, *, session_id=""):
        self.events.append(("sync", session_id))
        if self.fail_sync:
            raise OSError("vault write unavailable")

    def queue_prefetch(self, query, *, session_id=""):
        self.events.append(("prefetch", session_id))


def _run_agent_sync(manager, *, interrupted=False):
    """Execute the actual AIAgent._sync_memory method without its integrations."""
    source = (Path(__file__).resolve().parents[2] / "run_agent.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    agent = next(
        node for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "AIAgent"
    )
    method = next(
        node for node in agent.body
        if isinstance(node, ast.FunctionDef) and node.name == "_sync_memory"
    )
    scope = {"Any": object, "logger": logging.getLogger(__name__)}
    exec(
        compile(
            ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[])),
            "<memory-sync>",
            "exec",
        ),
        scope,
    )
    instance = SimpleNamespace(_memory_manager=manager, session_id="session-1")
    scope["_sync_memory"](
        instance,
        original_user_message="user text",
        final_response="assistant text",
        interrupted=interrupted,
    )


def test_manager_default_sync_keeps_best_effort_compatibility(caplog):
    provider = SyncProvider(fail_sync=True)
    manager = MemoryManager()
    manager.add_provider(provider)
    manager.sync_all("user", "assistant", session_id="session-1")
    assert provider.events == [("sync", "session-1")]
    assert "sync_turn failed" in caplog.text


def test_manager_strict_sync_propagates_write_failure():
    manager = MemoryManager()
    manager.add_provider(SyncProvider(fail_sync=True))
    with pytest.raises(RuntimeError, match="failed to sync turn"):
        manager.sync_all(
            "user", "assistant", session_id="session-1", raise_on_failure=True
        )


def test_agent_failed_sync_is_logged_without_prefetch(caplog):
    provider = SyncProvider(fail_sync=True)
    manager = MemoryManager()
    manager.add_provider(provider)
    with caplog.at_level(logging.ERROR):
        _run_agent_sync(manager)
    assert "Memory provider turn sync failed" in caplog.text
    assert "vault write unavailable" in caplog.text
    assert provider.events == [("sync", "session-1")]


def test_agent_successful_sync_prefetches_next_turn():
    provider = SyncProvider()
    manager = MemoryManager()
    manager.add_provider(provider)
    _run_agent_sync(manager)
    assert provider.events == [
        ("sync", "session-1"), ("prefetch", "session-1")
    ]


def test_agent_interrupted_turn_skips_sync_and_prefetch():
    provider = SyncProvider()
    manager = MemoryManager()
    manager.add_provider(provider)
    _run_agent_sync(manager, interrupted=True)
    assert provider.events == []
