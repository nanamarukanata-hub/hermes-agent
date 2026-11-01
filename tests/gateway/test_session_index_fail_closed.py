"""A damaged Obsidian gateway routing index must never be silently replaced."""

import json
import os

import pytest

from gateway.config import GatewayConfig, Platform
from gateway.session import SessionSource, SessionStore


def _store(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir(exist_ok=True)
    return SessionStore(
        sessions_dir=tmp_path / "unused-legacy-sessions",
        config=GatewayConfig(),
        vault_path=vault,
    )


@pytest.mark.parametrize("payload", [
    b'{"broken": ',
    b'[]',
    b'{"bad-entry": null}',
    b'\xff\xfe',
])
def test_corrupt_vault_routing_index_fails_closed_without_overwrite(tmp_path, payload):
    store = _store(tmp_path)
    index = store.sessions_dir / "sessions.json"
    index.parent.mkdir(parents=True, exist_ok=True)
    index.write_bytes(payload)

    source = SessionSource(platform=Platform.DISCORD, chat_id="room", user_id="42")
    with pytest.raises(RuntimeError, match="refusing to overwrite"):
        store.get_or_create_session(source)

    assert index.read_bytes() == payload
    assert store._loaded is False
    assert store._entries == {}
    assert not (tmp_path / "unused-legacy-sessions").exists()


@pytest.mark.parametrize("invalid_record", [
    None,
    {"session_key": "removed", "session_id": "old", "platform": "uninstalled-platform", "chat_type": "dm"},
])
def test_partial_index_load_does_not_leave_recovered_entries(tmp_path, invalid_record):
    store = _store(tmp_path)
    source = SessionSource(platform=Platform.DISCORD, chat_id="room", user_id="42")
    original = store.get_or_create_session(source)
    index = store.sessions_dir / "sessions.json"
    data = json.loads(index.read_text(encoding="utf-8"))
    assert original.session_key in data
    data["malformed"] = invalid_record
    damaged = json.dumps(data).encode("utf-8")
    index.write_bytes(damaged)

    restarted = _store(tmp_path)
    with pytest.raises(RuntimeError, match="refusing to overwrite"):
        restarted._ensure_loaded()

    assert restarted._entries == {}
    assert restarted._loaded is False
    assert index.read_bytes() == damaged


def test_symlinked_vault_root_is_rejected_before_initialization(tmp_path):
    real_vault = tmp_path / "real-vault"
    real_vault.mkdir()
    alias = tmp_path / "vault-alias"
    alias.symlink_to(real_vault, target_is_directory=True)

    with pytest.raises(RuntimeError, match="symlink"):
        SessionStore(
            sessions_dir=tmp_path / "unused-legacy-sessions",
            config=GatewayConfig(),
            vault_path=alias,
        )
    assert not (real_vault / "06_Automation").exists()


def test_symlinked_gateway_routing_directory_cannot_escape_vault(tmp_path):
    store = _store(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    store.sessions_dir.parent.mkdir(parents=True, exist_ok=True)
    store.sessions_dir.symlink_to(outside, target_is_directory=True)

    source = SessionSource(platform=Platform.DISCORD, chat_id="room", user_id="42")
    with pytest.raises(RuntimeError, match="symlink"):
        store.get_or_create_session(source)

    assert not (outside / "sessions.json").exists()
    assert store._loaded is False


def test_symlinked_routing_index_is_never_read_or_overwritten(tmp_path):
    store = _store(tmp_path)
    outside = tmp_path / "outside-index.json"
    payload = b'{"sensitive": "outside-vault"}'
    outside.write_bytes(payload)
    index = store.sessions_dir / "sessions.json"
    index.parent.mkdir(parents=True, exist_ok=True)
    index.symlink_to(outside)

    source = SessionSource(platform=Platform.DISCORD, chat_id="room", user_id="42")
    with pytest.raises(RuntimeError, match="symlink"):
        store.get_or_create_session(source)

    assert outside.read_bytes() == payload
    assert index.is_symlink()
    assert store._loaded is False


def test_symlink_swap_after_load_blocks_subsequent_index_save(tmp_path):
    store = _store(tmp_path)
    source = SessionSource(platform=Platform.DISCORD, chat_id="room", user_id="42")
    entry = store.get_or_create_session(source)
    routing_dir = store.sessions_dir
    original_dir = routing_dir.with_name("GatewayRouting-original")
    routing_dir.rename(original_dir)
    outside = tmp_path / "outside"
    outside.mkdir()
    routing_dir.symlink_to(outside, target_is_directory=True)

    with pytest.raises(RuntimeError, match="symlink"):
        store.update_session(entry.session_key)

    assert not (outside / "sessions.json").exists()
    assert (original_dir / "sessions.json").exists()


def test_two_gateway_instances_cannot_overwrite_each_others_new_sessions(tmp_path):
    """A stale instance must not replace the index after another writer commits."""
    first = _store(tmp_path)
    second = _store(tmp_path)
    first._ensure_loaded()
    second._ensure_loaded()

    alice = SessionSource(platform=Platform.DISCORD, chat_id="alice", user_id="1")
    bob = SessionSource(platform=Platform.DISCORD, chat_id="bob", user_id="2")
    first_entry = first.get_or_create_session(alice)
    index = first.sessions_dir / "sessions.json"
    committed = index.read_bytes()

    with pytest.raises(RuntimeError, match="changed on disk"):
        second.get_or_create_session(bob)

    assert index.read_bytes() == committed
    assert list(json.loads(committed)) == [first_entry.session_key]
    # A stale in-memory index must not be reused to overwrite the newer file.
    with pytest.raises(RuntimeError, match="changed on disk"):
        second.update_session(second._generate_session_key(bob))


def test_stale_gateway_instance_cannot_discard_later_session(tmp_path):
    first = _store(tmp_path)
    initial = SessionSource(platform=Platform.DISCORD, chat_id="initial", user_id="1")
    initial_entry = first.get_or_create_session(initial)

    second = _store(tmp_path)
    second._ensure_loaded()
    later = SessionSource(platform=Platform.DISCORD, chat_id="later", user_id="2")
    later_entry = first.get_or_create_session(later)
    committed = (first.sessions_dir / "sessions.json").read_bytes()

    other = SessionSource(platform=Platform.DISCORD, chat_id="other", user_id="3")
    with pytest.raises(RuntimeError, match="changed on disk"):
        second.get_or_create_session(other)

    assert (first.sessions_dir / "sessions.json").read_bytes() == committed
    assert set(json.loads(committed)) == {initial_entry.session_key, later_entry.session_key}


def test_symlinked_gateway_lock_cannot_redirect_index_write(tmp_path):
    store = _store(tmp_path)
    outside = tmp_path / "outside-lock"
    outside.write_bytes(b"outside")
    lock_path = store.sessions_dir / ".sessions.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.symlink_to(outside)

    source = SessionSource(platform=Platform.DISCORD, chat_id="room", user_id="42")
    with pytest.raises(RuntimeError, match="symlink"):
        store.get_or_create_session(source)

    assert outside.read_bytes() == b"outside"
    assert not (store.sessions_dir / "sessions.json").exists()


@pytest.mark.skipif(os.name == "nt", reason="fcntl-specific inode replacement simulation")
def test_lock_inode_replaced_during_acquisition_fails_closed(tmp_path, monkeypatch):
    """A lock replaced while a writer waits cannot protect the index."""
    import fcntl

    store = _store(tmp_path)
    lock_path = store.sessions_dir / ".sessions.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_bytes(b"original")
    original_flock = fcntl.flock
    replaced = False

    def replace_before_lock_returns(fd, operation):
        nonlocal replaced
        if operation == fcntl.LOCK_EX and not replaced:
            replaced = True
            lock_path.unlink()
            lock_path.write_bytes(b"replacement")
        return original_flock(fd, operation)

    monkeypatch.setattr(fcntl, "flock", replace_before_lock_returns)
    source = SessionSource(platform=Platform.DISCORD, chat_id="room", user_id="42")
    with pytest.raises(RuntimeError, match="lock changed during acquisition"):
        store.get_or_create_session(source)

    assert replaced
    assert lock_path.read_bytes() == b"replacement"
    assert not (store.sessions_dir / "sessions.json").exists()


def _stale_gateway_writer_process(vault, ready, proceed, result):
    """Load the index before another process commits, then attempt a stale save."""
    try:
        store = SessionStore(
            sessions_dir=vault.parent / "unused-legacy-sessions",
            config=GatewayConfig(),
            vault_path=vault,
        )
        store._ensure_loaded()
        ready.set()
        if not proceed.wait(timeout=15):
            result.put("worker timed out waiting for parent")
            return
        source = SessionSource(platform=Platform.DISCORD, chat_id="child", user_id="2")
        try:
            store.get_or_create_session(source)
        except RuntimeError as exc:
            result.put(str(exc))
        else:
            result.put("unexpected stale write success")
    except Exception as exc:
        result.put(f"worker exception: {type(exc).__name__}: {exc}")


@pytest.mark.skipif(os.name == "nt", reason="POSIX fork process isolation regression")
def test_separate_process_stale_gateway_writer_cannot_overwrite(tmp_path):
    """Two real OS processes must not lose a committed routing index update."""
    import multiprocessing

    ctx = multiprocessing.get_context("fork")
    vault = tmp_path / "vault"
    vault.mkdir()
    ready, proceed = ctx.Event(), ctx.Event()
    result = ctx.Queue()
    worker = ctx.Process(
        target=_stale_gateway_writer_process,
        args=(vault, ready, proceed, result),
    )
    worker.start()
    try:
        assert ready.wait(timeout=15), "child did not load the original index"
        parent = _store(tmp_path)
        source = SessionSource(platform=Platform.DISCORD, chat_id="parent", user_id="1")
        entry = parent.get_or_create_session(source)
        index = parent.sessions_dir / "sessions.json"
        committed = index.read_bytes()

        proceed.set()
        worker.join(timeout=15)
        assert worker.exitcode == 0, "child did not finish cleanly"
        assert "changed on disk" in result.get(timeout=3)
        assert index.read_bytes() == committed
        assert set(json.loads(committed)) == {entry.session_key}
    finally:
        proceed.set()
        if worker.is_alive():
            worker.terminate()
        worker.join(timeout=5)
        result.close()


@pytest.mark.skipif(os.name == "nt", reason="symlink race regression uses POSIX symlinks")
@pytest.mark.parametrize("phase", ["load", "save"])
def test_index_symlink_swapped_during_open_fails_closed(tmp_path, monkeypatch, phase):
    """The final index component cannot be redirected after the path check."""
    store = _store(tmp_path)
    source = SessionSource(platform=Platform.DISCORD, chat_id="room", user_id="42")
    if phase == "save":
        entry = store.get_or_create_session(source)
    index = store.sessions_dir / "sessions.json"
    index.parent.mkdir(parents=True, exist_ok=True)
    if phase == "load":
        index.write_text("{}", encoding="utf-8")

    backup = index.with_name("sessions-original.json")
    outside = tmp_path / "outside-index.json"
    outside_payload = b'{"outside": "must-not-read-or-write"}'
    outside.write_bytes(outside_payload)
    original_open = os.open
    swapped = False

    def swap_before_open(path, flags, mode=0o777, *, dir_fd=None):
        nonlocal swapped
        if os.fspath(path) == os.fspath(index) and not swapped:
            swapped = True
            index.rename(backup)
            index.symlink_to(outside)
        if dir_fd is None:
            return original_open(path, flags, mode)
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", swap_before_open)
    if phase == "load":
        with pytest.raises(RuntimeError, match="refusing to overwrite"):
            store._ensure_loaded()
        assert store._loaded is False
    else:
        with pytest.raises((OSError, RuntimeError)):
            store.update_session(entry.session_key)

    assert swapped
    assert index.is_symlink()
    assert backup.is_file()
    assert outside.read_bytes() == outside_payload
