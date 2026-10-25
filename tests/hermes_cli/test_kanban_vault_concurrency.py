"""Vault-store concurrency and restart-recovery tests for the Kanban
SQLite removal (Phase 4).

These replace the old SQLite contention assumptions with the Vault
guarantees: per-board flock + atomic JSON writes, CAS-equivalent claim,
and durable state readable after a process exit.
"""

import json
import multiprocessing as mp
import os
import subprocess
import sys
from pathlib import Path

import pytest

from hermes_cli import kanban_db as kb


def _claim_worker(home, tid, queue):
    """Child-process worker: try once to claim ``tid``; report winner or not."""
    os.environ["HERMES_HOME"] = home
    from hermes_cli import kanban_db as _kb

    with _kb.connect() as conn:
        queue.put(_kb.claim_task(conn, tid) is not None)


def test_concurrent_claim_single_winner_multiprocess(tmp_path, monkeypatch):
    """8 separate processes claiming the same task: exactly 1 winner."""
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    kb.init_db()
    with kb.connect() as conn:
        tid = kb.create_task(conn, title="raced", assignee="worker")

    ctx = mp.get_context("fork")
    queue = ctx.Queue()
    procs = [
        ctx.Process(target=_claim_worker, args=(str(home), tid, queue))
        for _ in range(8)
    ]
    for proc in procs:
        proc.start()
    for proc in procs:
        proc.join(timeout=60)
    assert all(not proc.is_alive() for proc in procs)
    results = [queue.get() for _ in range(8)]
    assert sum(1 for won in results if won) == 1, f"expected exactly 1 winner, got {results}"

    with kb.connect() as conn:
        task = kb.get_task(conn, tid)
        assert task.status == "running"
        assert task.claim_lock is not None
        kinds = [e.kind for e in kb.list_events(conn, tid)]
        assert kinds.count("claimed") == 1


def test_restart_recovery_after_process_exit(tmp_path, monkeypatch):
    """Data written by a child process must be readable after it exits:
    task, comment, event, and link all survive a reopen."""
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    worktree_root = Path(__file__).resolve().parents[2]
    writer = (
        "import os, sys; sys.path.insert(0, %r); "
        "from hermes_cli import kanban_db as kb; "
        "kb.init_db(); "
        "c = kb.connect(); "
        "a = kb.create_task(c, title='parent'); "
        "b = kb.create_task(c, title='child'); "
        "kb.link_tasks(c, a, b); "
        "kb.add_comment(c, a, 'tester', 'hello restart'); "
        "print(a + ' ' + b); c.close()"
        % str(worktree_root)
    )
    env = {**os.environ, "HERMES_HOME": str(home), "PYTHONPATH": str(worktree_root)}
    r = subprocess.run(
        [sys.executable, "-c", writer], capture_output=True, text=True, env=env
    )
    assert r.returncode == 0, r.stderr
    parent_id, child_id = r.stdout.strip().split()

    # Reopen in this process: everything the child wrote must be visible.
    with kb.connect() as conn:
        parent = kb.get_task(conn, parent_id)
        assert parent is not None and parent.title == "parent"
        assert kb.get_task(conn, child_id).title == "child"
        assert child_id in kb.child_ids(conn, parent_id)
        comments = kb.list_comments(conn, parent_id)
        assert len(comments) == 1 and comments[0].body == "hello restart"
        kinds = [e.kind for e in kb.list_events(conn, parent_id)]
        assert "created" in kinds and "commented" in kinds
