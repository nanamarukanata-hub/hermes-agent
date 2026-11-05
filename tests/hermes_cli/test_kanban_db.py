"""Tests for the Kanban DB layer (hermes_cli.kanban_db)."""

from __future__ import annotations

import concurrent.futures
import json
import os
import time
from pathlib import Path

import pytest

from hermes_cli import kanban_db as kb


@pytest.fixture
def kanban_home(tmp_path, monkeypatch):
    """Isolated HERMES_HOME with an empty kanban DB."""
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    kb.init_db()
    return home


def _edit_task_file(task_id, **fields):
    """Edit a task's Vault JSON file directly (Vault-structure seeding)."""
    path = kb.kanban_store_dir() / f"{task_id}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data.update(fields)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _edit_run_file(run_id, **fields):
    """Edit a run row in runs.json directly (Vault-structure seeding)."""
    path = kb.kanban_store_dir() / "runs.json"
    rows = json.loads(path.read_text(encoding="utf-8"))
    for row in rows:
        if int(row["id"]) == int(run_id):
            row.update(fields)
    path.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")


def _append_run_file(run):
    """Append a run row to runs.json and advance the run counter."""
    store = kb.kanban_store_dir()
    path = store / "runs.json"
    rows = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    rows.append(run)
    path.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
    counters_path = store / "counters.json"
    counters = json.loads(counters_path.read_text(encoding="utf-8"))
    counters["run"] = max(int(counters.get("run", 0)), int(run["id"]))
    counters_path.write_text(json.dumps(counters, indent=2) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Schema / init
# ---------------------------------------------------------------------------

def test_init_db_is_idempotent(kanban_home):
    # Second call should not error or drop data.
    with kb.connect() as conn:
        kb.create_task(conn, title="persisted")
    kb.init_db()
    with kb.connect() as conn:
        tasks = kb.list_tasks(conn)
    assert len(tasks) == 1
    assert tasks[0].title == "persisted"


def test_init_creates_expected_tables(kanban_home):
    """The Vault store has the migration layout, not SQLite tables."""
    with kb.connect() as conn:
        tid = kb.create_task(conn, title="seed")
    store = kb.kanban_store_dir()
    assert (store / f"{tid}.json").exists()
    for name in ("links", "comments", "events", "runs", "notify_subs", "counters"):
        assert (store / f"{name}.json").exists()
    task_data = json.loads((store / f"{tid}.json").read_text(encoding="utf-8"))
    assert task_data["title"] == "seed"
    assert task_data["status"] == "ready"


# ---------------------------------------------------------------------------
# Task creation + status inference
# ---------------------------------------------------------------------------

def test_create_task_no_parents_is_ready(kanban_home):
    with kb.connect() as conn:
        tid = kb.create_task(conn, title="ship it", assignee="alice")
        t = kb.get_task(conn, tid)
    assert t is not None
    assert t.status == "ready"
    assert t.assignee == "alice"
    assert t.workspace_kind == "scratch"


def test_create_task_with_parent_is_todo_until_parent_done(kanban_home):
    with kb.connect() as conn:
        p = kb.create_task(conn, title="parent")
        c = kb.create_task(conn, title="child", parents=[p])
        assert kb.get_task(conn, c).status == "todo"
        kb.complete_task(conn, p, result="ok")
        assert kb.get_task(conn, c).status == "ready"


def test_create_task_unknown_parent_errors(kanban_home):
    with kb.connect() as conn, pytest.raises(ValueError, match="unknown parent"):
        kb.create_task(conn, title="orphan", parents=["t_ghost"])


def test_workspace_kind_validation(kanban_home):
    with kb.connect() as conn, pytest.raises(ValueError, match="workspace_kind"):
        kb.create_task(conn, title="bad ws", workspace_kind="cloud")


# ---------------------------------------------------------------------------
# Links + dependency resolution
# ---------------------------------------------------------------------------

def test_link_demotes_ready_child_to_todo_when_parent_not_done(kanban_home):
    with kb.connect() as conn:
        a = kb.create_task(conn, title="a")
        b = kb.create_task(conn, title="b")
        assert kb.get_task(conn, b).status == "ready"
        kb.link_tasks(conn, a, b)
        assert kb.get_task(conn, b).status == "todo"


def test_link_keeps_ready_child_when_parent_already_done(kanban_home):
    with kb.connect() as conn:
        a = kb.create_task(conn, title="a")
        kb.complete_task(conn, a)
        b = kb.create_task(conn, title="b")
        assert kb.get_task(conn, b).status == "ready"
        kb.link_tasks(conn, a, b)
        assert kb.get_task(conn, b).status == "ready"


def test_link_rejects_self_loop(kanban_home):
    with kb.connect() as conn:
        a = kb.create_task(conn, title="a")
        with pytest.raises(ValueError, match="itself"):
            kb.link_tasks(conn, a, a)


def test_link_detects_cycle(kanban_home):
    with kb.connect() as conn:
        a = kb.create_task(conn, title="a")
        b = kb.create_task(conn, title="b", parents=[a])
        c = kb.create_task(conn, title="c", parents=[b])
        with pytest.raises(ValueError, match="cycle"):
            kb.link_tasks(conn, c, a)
        with pytest.raises(ValueError, match="cycle"):
            kb.link_tasks(conn, b, a)


def test_recompute_ready_cascades_through_chain(kanban_home):
    with kb.connect() as conn:
        a = kb.create_task(conn, title="a")
        b = kb.create_task(conn, title="b", parents=[a])
        c = kb.create_task(conn, title="c", parents=[b])
        assert [kb.get_task(conn, x).status for x in (a, b, c)] == \
               ["ready", "todo", "todo"]
        kb.complete_task(conn, a)
        assert kb.get_task(conn, b).status == "ready"
        kb.complete_task(conn, b)
        assert kb.get_task(conn, c).status == "ready"


def test_recompute_ready_fan_in_waits_for_all_parents(kanban_home):
    with kb.connect() as conn:
        a = kb.create_task(conn, title="a")
        b = kb.create_task(conn, title="b")
        c = kb.create_task(conn, title="c", parents=[a, b])
        kb.complete_task(conn, a)
        assert kb.get_task(conn, c).status == "todo"
        kb.complete_task(conn, b)
        assert kb.get_task(conn, c).status == "ready"


# ---------------------------------------------------------------------------
# Atomic claim (CAS)
# ---------------------------------------------------------------------------

def test_claim_once_wins_second_loses(kanban_home):
    with kb.connect() as conn:
        t = kb.create_task(conn, title="x", assignee="a")
        first = kb.claim_task(conn, t, claimer="host:1")
        assert first is not None and first.status == "running"
        second = kb.claim_task(conn, t, claimer="host:2")
        assert second is None


def test_claim_fails_on_non_ready(kanban_home):
    with kb.connect() as conn:
        t = kb.create_task(conn, title="x")
        # Move to todo by introducing an unsatisfied parent.
        p = kb.create_task(conn, title="p")
        kb.link_tasks(conn, p, t)
        assert kb.get_task(conn, t).status == "todo"
        assert kb.claim_task(conn, t) is None


def test_stale_claim_reclaimed(kanban_home, monkeypatch):
    import signal
    import hermes_cli.kanban_db as _kb

    with kb.connect() as conn:
        t = kb.create_task(conn, title="x", assignee="a")
        host = _kb._claimer_id().split(":", 1)[0]
        kb.claim_task(conn, t, claimer=f"{host}:worker")
        killed: list[int] = []
        state = {"alive": True}

        def _signal(pid, sig):
            killed.append(sig)
            if sig == signal.SIGTERM:
                state["alive"] = False

        kb._set_worker_pid(conn, t, 12345)
        # Rewind claim_expires so it looks stale.
        _edit_task_file(t, claim_expires=int(time.time()) - 3600)
        monkeypatch.setattr(_kb, "_pid_alive", lambda _pid: state["alive"])
        reclaimed = kb.release_stale_claims(conn, signal_fn=_signal)
        assert reclaimed == 1
        assert kb.get_task(conn, t).status == "ready"
        assert killed == [signal.SIGTERM]


def test_max_runtime_uses_current_run_start_after_retry(kanban_home):
    """A retry should get a fresh max-runtime window.

    ``tasks.started_at`` intentionally records the first time the task ever
    started. Runtime enforcement must therefore use the active
    ``task_runs.started_at`` row; otherwise every retry of an old task is
    immediately timed out again.
    """
    with kb.connect() as conn:
        host = kb._claimer_id().split(":", 1)[0]
        t = kb.create_task(
            conn, title="retry", assignee="a", max_runtime_seconds=10,
        )

        kb.claim_task(conn, t, claimer=f"{host}:first")
        first_run_id = kb.latest_run(conn, t).id
        old_started = int(time.time()) - 20
        _edit_task_file(t, started_at=old_started, worker_pid=999999)
        _edit_run_file(first_run_id, started_at=old_started, worker_pid=999999)

        timed_out = kb.enforce_max_runtime(conn, signal_fn=lambda _pid, _sig: None)
        assert timed_out == [t]
        assert kb.get_task(conn, t).status == "ready"

        kb.claim_task(conn, t, claimer=f"{host}:retry")
        retry_run = kb.latest_run(conn, t)
        _edit_task_file(t, worker_pid=999999)
        _edit_run_file(retry_run.id, worker_pid=999999)

        timed_out = kb.enforce_max_runtime(conn, signal_fn=lambda _pid, _sig: None)
        assert timed_out == []
        assert kb.get_task(conn, t).status == "running"


def test_heartbeat_extends_claim(kanban_home):
    with kb.connect() as conn:
        t = kb.create_task(conn, title="x", assignee="a")
        claimer = "host:hb"
        kb.claim_task(conn, t, claimer=claimer, ttl_seconds=60)
        original = kb.get_task(conn, t).claim_expires
        # Rewind then heartbeat.
        _edit_task_file(t, claim_expires=0)
        ok = kb.heartbeat_claim(conn, t, claimer=claimer, ttl_seconds=3600)
        assert ok
        new = kb.get_task(conn, t).claim_expires
        assert new > int(time.time()) + 3000


def test_concurrent_claims_only_one_wins(kanban_home):
    """Fire N threads claiming the same task; exactly one must win."""
    with kb.connect() as conn:
        t = kb.create_task(conn, title="race", assignee="a")

    def attempt(i):
        with kb.connect() as c:
            return kb.claim_task(c, t, claimer=f"host:{i}")

    n_workers = 8
    with concurrent.futures.ThreadPoolExecutor(max_workers=n_workers) as ex:
        results = list(ex.map(attempt, range(n_workers)))
    winners = [r for r in results if r is not None]
    assert len(winners) == 1
    assert winners[0].status == "running"


# ---------------------------------------------------------------------------
# Complete / block / unblock / archive / assign
# ---------------------------------------------------------------------------

def test_complete_records_result(kanban_home):
    with kb.connect() as conn:
        t = kb.create_task(conn, title="x")
        assert kb.complete_task(conn, t, result="done and dusted")
        task = kb.get_task(conn, t)
    assert task.status == "done"
    assert task.result == "done and dusted"
    assert task.completed_at is not None


def test_block_then_unblock(kanban_home):
    with kb.connect() as conn:
        t = kb.create_task(conn, title="x", assignee="a")
        kb.claim_task(conn, t)
        assert kb.block_task(conn, t, reason="need input")
        assert kb.get_task(conn, t).status == "blocked"
        assert kb.unblock_task(conn, t)
        assert kb.get_task(conn, t).status == "ready"


def test_assign_refuses_while_running(kanban_home):
    with kb.connect() as conn:
        t = kb.create_task(conn, title="x", assignee="a")
        kb.claim_task(conn, t)
        with pytest.raises(RuntimeError, match="currently running"):
            kb.assign_task(conn, t, "b")


def test_assign_reassigns_when_not_running(kanban_home):
    with kb.connect() as conn:
        t = kb.create_task(conn, title="x", assignee="a")
        assert kb.assign_task(conn, t, "b")
        assert kb.get_task(conn, t).assignee == "b"


def test_assignee_normalized_to_lowercase_on_create_and_assign(kanban_home):
    """Dashboard/CLI may pass title-cased profile labels; DB + spawn use canonical id."""
    with kb.connect() as conn:
        tid = kb.create_task(conn, title="cased", assignee="Jules")
        assert kb.get_task(conn, tid).assignee == "jules"
        assert kb.assign_task(conn, tid, "Librarian")
        assert kb.get_task(conn, tid).assignee == "librarian"


def test_list_tasks_assignee_filter_case_insensitive(kanban_home):
    with kb.connect() as conn:
        tid = kb.create_task(conn, title="q", assignee="jules")
        found = kb.list_tasks(conn, assignee="Jules")
        assert len(found) == 1 and found[0].id == tid


def test_archive_hides_from_default_list(kanban_home):
    with kb.connect() as conn:
        t = kb.create_task(conn, title="x")
        kb.complete_task(conn, t)
        assert kb.archive_task(conn, t)
        assert len(kb.list_tasks(conn)) == 0
        assert len(kb.list_tasks(conn, include_archived=True)) == 1


# ---------------------------------------------------------------------------
# Comments / events / worker context
# ---------------------------------------------------------------------------

def test_comments_recorded_in_order(kanban_home):
    with kb.connect() as conn:
        t = kb.create_task(conn, title="x")
        kb.add_comment(conn, t, "user", "first")
        kb.add_comment(conn, t, "researcher", "second")
        comments = kb.list_comments(conn, t)
    assert [c.body for c in comments] == ["first", "second"]
    assert [c.author for c in comments] == ["user", "researcher"]


def test_empty_comment_rejected(kanban_home):
    with kb.connect() as conn:
        t = kb.create_task(conn, title="x")
        with pytest.raises(ValueError, match="body is required"):
            kb.add_comment(conn, t, "user", "")


def test_events_capture_lifecycle(kanban_home):
    with kb.connect() as conn:
        t = kb.create_task(conn, title="x", assignee="a")
        kb.claim_task(conn, t)
        kb.complete_task(conn, t, result="ok")
        events = kb.list_events(conn, t)
    kinds = [e.kind for e in events]
    assert "created" in kinds
    assert "claimed" in kinds
    assert "completed" in kinds


def test_worker_context_includes_parent_results_and_comments(kanban_home):
    with kb.connect() as conn:
        p = kb.create_task(conn, title="p")
        kb.complete_task(conn, p, result="PARENT_RESULT_MARKER")
        c = kb.create_task(conn, title="child", parents=[p])
        kb.add_comment(conn, c, "user", "CLARIFICATION_MARKER")
        ctx = kb.build_worker_context(conn, c)
    assert "PARENT_RESULT_MARKER" in ctx
    assert "CLARIFICATION_MARKER" in ctx
    assert c in ctx
    assert "child" in ctx


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

def test_dispatch_dry_run_does_not_claim(kanban_home, all_assignees_spawnable):
    with kb.connect() as conn:
        t1 = kb.create_task(conn, title="a", assignee="alice")
        t2 = kb.create_task(conn, title="b", assignee="bob")
        res = kb.dispatch_once(conn, dry_run=True)
    assert {s[0] for s in res.spawned} == {t1, t2}
    with kb.connect() as conn:
        # Dry run must NOT mutate status.
        assert kb.get_task(conn, t1).status == "ready"
        assert kb.get_task(conn, t2).status == "ready"


def test_dispatch_skips_unassigned(kanban_home):
    with kb.connect() as conn:
        t = kb.create_task(conn, title="floater")
        res = kb.dispatch_once(conn, dry_run=True)
    assert t in res.skipped_unassigned
    assert t not in res.skipped_nonspawnable
    assert not res.spawned


def test_dispatch_skips_nonspawnable_into_separate_bucket(kanban_home, monkeypatch):
    """Tasks whose assignee fails profile_exists() must NOT land in
    ``skipped_unassigned`` (which is operator-actionable) — they go in
    the dedicated ``skipped_nonspawnable`` bucket so health telemetry
    can suppress false-positive "stuck" warnings."""
    from hermes_cli import profiles
    monkeypatch.setattr(profiles, "profile_exists", lambda name: False)
    with kb.connect() as conn:
        t = kb.create_task(conn, title="for-terminal", assignee="orion-cc")
        res = kb.dispatch_once(conn, dry_run=True)
    assert t in res.skipped_nonspawnable
    assert t not in res.skipped_unassigned
    assert not res.spawned


def test_has_spawnable_ready_false_when_only_terminal_lanes(kanban_home, monkeypatch):
    """``has_spawnable_ready`` returns False when every ready task is
    assigned to a control-plane lane — used by gateway/CLI dispatchers
    to silence the stuck-warn while terminals still have queued work."""
    from hermes_cli import profiles
    monkeypatch.setattr(profiles, "profile_exists", lambda name: False)
    with kb.connect() as conn:
        kb.create_task(conn, title="t1", assignee="orion-cc")
        kb.create_task(conn, title="t2", assignee="orion-research")
        assert kb.has_spawnable_ready(conn) is False


def test_has_spawnable_ready_true_when_real_profile_present(kanban_home, monkeypatch):
    """``has_spawnable_ready`` returns True as soon as ANY ready task
    has an assignee that maps to a real Hermes profile — preserves the
    real "stuck" signal when a daily/agent task is queued."""
    from hermes_cli import profiles
    monkeypatch.setattr(
        profiles, "profile_exists", lambda name: name == "daily"
    )
    with kb.connect() as conn:
        kb.create_task(conn, title="terminal-task", assignee="orion-cc")
        kb.create_task(conn, title="hermes-task", assignee="daily")
        assert kb.has_spawnable_ready(conn) is True


def test_has_spawnable_ready_false_on_empty_queue(kanban_home):
    """Empty queue is the trivial false case — no ready tasks at all."""
    with kb.connect() as conn:
        assert kb.has_spawnable_ready(conn) is False


def test_dispatch_promotes_ready_and_spawns(kanban_home, all_assignees_spawnable):
    spawns = []

    def fake_spawn(task, workspace):
        spawns.append((task.id, task.assignee, workspace))

    with kb.connect() as conn:
        p = kb.create_task(conn, title="p", assignee="alice")
        c = kb.create_task(conn, title="c", assignee="bob", parents=[p])
        # Finish parent outside dispatch; promotion happens inside.
        kb.complete_task(conn, p)
        res = kb.dispatch_once(conn, spawn_fn=fake_spawn)
    # Spawned c (a was already done when dispatch was called).
    assert len(spawns) == 1
    assert spawns[0][0] == c
    assert spawns[0][1] == "bob"
    # c is now running
    with kb.connect() as conn:
        assert kb.get_task(conn, c).status == "running"


def test_dispatch_spawn_failure_releases_claim(kanban_home, all_assignees_spawnable):
    def boom(task, workspace):
        raise RuntimeError("spawn failed")

    with kb.connect() as conn:
        t = kb.create_task(conn, title="boom", assignee="alice")
        kb.dispatch_once(conn, spawn_fn=boom)
        # Must return to ready so the next tick can retry.
        assert kb.get_task(conn, t).status == "ready"
        assert kb.get_task(conn, t).claim_lock is None


def test_dispatch_reclaims_stale_before_spawning(kanban_home):
    with kb.connect() as conn:
        t = kb.create_task(conn, title="x", assignee="alice")
        kb.claim_task(conn, t)
        _edit_task_file(t, claim_expires=int(time.time()) - 1)
        res = kb.dispatch_once(conn, dry_run=True)
    assert res.reclaimed == 1


# ---------------------------------------------------------------------------
# Workspace resolution
# ---------------------------------------------------------------------------

def test_scratch_workspace_created_inside_obsidian_vault(kanban_home):
    """Scratch workspaces must not fall back to legacy Hermes Home."""
    with kb.connect() as conn:
        t = kb.create_task(conn, title="x")
        task = kb.get_task(conn, t)
        ws = kb.resolve_workspace(task)
    assert ws.exists()
    assert ws.is_dir()
    assert ws.parent == kb.workspaces_root()
    vault = Path(os.environ["HERMES_OBSIDIAN_VAULT"]).resolve()
    assert ws.resolve().is_relative_to(vault)
    assert not (kanban_home / "kanban" / "workspaces").exists()


def test_dir_workspace_honors_given_path(kanban_home, tmp_path):
    target = tmp_path / "my-vault"
    with kb.connect() as conn:
        t = kb.create_task(
            conn, title="biz", workspace_kind="dir", workspace_path=str(target)
        )
        task = kb.get_task(conn, t)
        ws = kb.resolve_workspace(task)
    assert ws == target
    assert ws.exists()


def test_worktree_workspace_returns_intended_path(kanban_home, tmp_path):
    target = str(tmp_path / ".worktrees" / "my-task")
    with kb.connect() as conn:
        t = kb.create_task(
            conn, title="ship", workspace_kind="worktree", workspace_path=target
        )
        task = kb.get_task(conn, t)
        ws = kb.resolve_workspace(task)
    # We do NOT auto-create worktrees; the worker's skill handles that.
    assert str(ws) == target


# ---------------------------------------------------------------------------
# Tenancy
# ---------------------------------------------------------------------------

def test_tenant_column_filters_listings(kanban_home):
    with kb.connect() as conn:
        kb.create_task(conn, title="a1", tenant="biz-a")
        kb.create_task(conn, title="b1", tenant="biz-b")
        kb.create_task(conn, title="shared")  # no tenant
        biz_a = kb.list_tasks(conn, tenant="biz-a")
        biz_b = kb.list_tasks(conn, tenant="biz-b")
    assert [t.title for t in biz_a] == ["a1"]
    assert [t.title for t in biz_b] == ["b1"]


def test_tenant_propagates_to_events(kanban_home):
    with kb.connect() as conn:
        t = kb.create_task(conn, title="tenant-task", tenant="biz-a")
        events = kb.list_events(conn, t)
    # The "created" event should have tenant in its payload.
    created = [e for e in events if e.kind == "created"]
    assert created and created[0].payload.get("tenant") == "biz-a"


# ---------------------------------------------------------------------------
# Shared-board path resolution (issue #19348)
#
# The kanban board is a cross-profile coordination primitive: a worker
# spawned with `hermes -p <profile>` must read/write the same kanban.db
# as the dispatcher that claimed the task. These tests exercise the
# path-resolution layer directly and would have caught the regression
# where `kanban_db_path()` resolved to the active profile's HERMES_HOME.
# ---------------------------------------------------------------------------

class TestSharedBoardPaths:
    """Dispatcher and profile workers must share one Vault-backed board."""

    def _set_home(self, monkeypatch, tmp_path, hermes_home):
        vault = tmp_path / "obsidian_vault"
        vault.mkdir(exist_ok=True)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.setenv("HERMES_HOME", str(hermes_home))
        monkeypatch.setenv("HERMES_OBSIDIAN_VAULT", str(vault))
        for name in (
            "HERMES_KANBAN_HOME",
            "HERMES_KANBAN_BOARD",
            "HERMES_KANBAN_DB",
            "HERMES_KANBAN_WORKSPACES_ROOT",
        ):
            monkeypatch.delenv(name, raising=False)
        return vault

    @staticmethod
    def _store(vault):
        return vault / "06_Automation" / "Hermes" / "Kanban" / "default"

    def test_default_install_anchors_at_obsidian_vault(
        self, tmp_path, monkeypatch
    ):
        default_home = tmp_path / ".hermes"
        default_home.mkdir()
        vault = self._set_home(monkeypatch, tmp_path, default_home)
        store = self._store(vault)

        # Legacy path helpers remain for compatibility, not persistence.
        assert kb.kanban_home() == default_home
        assert kb.kanban_db_path() == default_home / "kanban.db"
        assert kb.kanban_store_dir() == store
        assert kb.workspaces_root() == store / "workspaces"
        assert kb.worker_log_path("t_demo") == store / "logs" / "t_demo.log"

        kb.init_db()
        assert (store / "counters.json").exists()
        assert not (default_home / "kanban.db").exists()
        assert not (default_home / "kanban" / "workspaces").exists()

    def test_profile_worker_resolves_to_shared_vault(
        self, tmp_path, monkeypatch
    ):
        default_home = tmp_path / ".hermes"
        default_home.mkdir()
        profile_home = default_home / "profiles" / "nehemiahkanban"
        profile_home.mkdir(parents=True)
        vault = self._set_home(monkeypatch, tmp_path, profile_home)

        assert kb.kanban_home() == default_home
        assert kb.kanban_store_dir() == self._store(vault)
        assert kb.workspaces_root() == self._store(vault) / "workspaces"
        assert kb.worker_log_path("t_0d214f19") == (
            self._store(vault) / "logs" / "t_0d214f19.log"
        )
        assert not (profile_home / "kanban.db").exists()

    def test_dispatcher_and_profile_worker_converge(
        self, tmp_path, monkeypatch
    ):
        default_home = tmp_path / ".hermes"
        default_home.mkdir()
        profile_home = default_home / "profiles" / "coder"
        profile_home.mkdir(parents=True)
        self._set_home(monkeypatch, tmp_path, default_home)

        dispatcher_paths = (
            kb.kanban_store_dir(),
            kb.workspaces_root(),
            kb.worker_log_path("t_handoff"),
        )
        monkeypatch.setenv("HERMES_HOME", str(profile_home))
        worker_paths = (
            kb.kanban_store_dir(),
            kb.workspaces_root(),
            kb.worker_log_path("t_handoff"),
        )
        assert dispatcher_paths == worker_paths

    def test_custom_hermes_home_does_not_move_vault_store(
        self, tmp_path, monkeypatch
    ):
        custom_root = tmp_path / "opt" / "hermes"
        custom_root.mkdir(parents=True)
        vault = self._set_home(monkeypatch, tmp_path, custom_root)

        assert kb.kanban_home() == custom_root
        assert kb.kanban_store_dir() == self._store(vault)
        assert kb.workspaces_root() == self._store(vault) / "workspaces"

    def test_docker_profile_layout_uses_shared_vault(
        self, tmp_path, monkeypatch
    ):
        custom_root = tmp_path / "opt" / "hermes"
        profile = custom_root / "profiles" / "coder"
        profile.mkdir(parents=True)
        vault = self._set_home(monkeypatch, tmp_path, profile)

        assert kb.kanban_home() == custom_root
        assert kb.kanban_store_dir() == self._store(vault)

    def test_kanban_home_override_cannot_redirect_persistence(
        self, tmp_path, monkeypatch
    ):
        default_home = tmp_path / ".hermes"
        default_home.mkdir()
        vault = self._set_home(monkeypatch, tmp_path, default_home)
        override = tmp_path / "shared-board"
        override.mkdir()
        monkeypatch.setenv("HERMES_KANBAN_HOME", str(override))

        assert kb.kanban_home() == override
        assert kb.kanban_store_dir() == self._store(vault)
        assert kb.workspaces_root() == self._store(vault) / "workspaces"
        assert not (override / "kanban.db").exists()

    def test_empty_home_override_falls_through(self, tmp_path, monkeypatch):
        default_home = tmp_path / ".hermes"
        default_home.mkdir()
        self._set_home(monkeypatch, tmp_path, default_home)
        monkeypatch.setenv("HERMES_KANBAN_HOME", "   ")
        assert kb.kanban_home() == default_home

    def test_dispatcher_and_worker_share_vault_task_without_sqlite(
        self, tmp_path, monkeypatch
    ):
        default_home = tmp_path / ".hermes"
        default_home.mkdir()
        profile_home = default_home / "profiles" / "nehemiahkanban"
        profile_home.mkdir(parents=True)
        vault = self._set_home(monkeypatch, tmp_path, default_home)

        kb.init_db()
        with kb.connect() as conn:
            task_id = kb.create_task(conn, title="cross-profile")
        assert (self._store(vault) / f"{task_id}.json").exists()

        monkeypatch.setenv("HERMES_HOME", str(profile_home))
        with kb.connect() as conn:
            task = kb.get_task(conn, task_id)
        assert task is not None
        assert task.title == "cross-profile"
        assert not (default_home / "kanban.db").exists()
        assert not (profile_home / "kanban.db").exists()

    def test_legacy_db_pin_cannot_bypass_vault(
        self, tmp_path, monkeypatch
    ):
        default_home = tmp_path / ".hermes"
        default_home.mkdir()
        vault = self._set_home(monkeypatch, tmp_path, default_home)
        pinned_db = tmp_path / "pinned" / "board.db"
        pinned_db.parent.mkdir()
        monkeypatch.setenv("HERMES_KANBAN_DB", str(pinned_db))

        # The compatibility path may be pinned, but runtime data cannot.
        assert kb.kanban_db_path() == pinned_db
        assert kb.kanban_store_dir() == self._store(vault)
        kb.init_db()
        assert (self._store(vault) / "counters.json").exists()
        assert not pinned_db.exists()

    def test_workspace_override_must_stay_inside_vault(
        self, tmp_path, monkeypatch
    ):
        default_home = tmp_path / ".hermes"
        default_home.mkdir()
        vault = self._set_home(monkeypatch, tmp_path, default_home)
        pinned_ws = vault / "custom-workspaces"
        monkeypatch.setenv("HERMES_KANBAN_WORKSPACES_ROOT", str(pinned_ws))
        assert kb.workspaces_root() == pinned_ws

        monkeypatch.setenv(
            "HERMES_KANBAN_WORKSPACES_ROOT", str(tmp_path / "outside-vault")
        )
        with pytest.raises(ValueError, match="escapes HERMES_OBSIDIAN_VAULT"):
            kb.workspaces_root()

    def test_empty_path_pins_fall_through(self, tmp_path, monkeypatch):
        default_home = tmp_path / ".hermes"
        default_home.mkdir()
        vault = self._set_home(monkeypatch, tmp_path, default_home)
        monkeypatch.setenv("HERMES_KANBAN_DB", "   ")
        monkeypatch.setenv("HERMES_KANBAN_WORKSPACES_ROOT", "")

        assert kb.kanban_store_dir() == self._store(vault)
        assert kb.workspaces_root() == self._store(vault) / "workspaces"

    def test_dispatcher_spawn_passes_vault_to_profile_worker(
        self, tmp_path, monkeypatch
    ):
        default_home = tmp_path / ".hermes"
        default_home.mkdir()
        vault = self._set_home(monkeypatch, tmp_path, default_home)
        captured = {}

        class _FakePopen:
            def __init__(self, cmd, **kwargs):
                captured["cmd"] = cmd
                captured["env"] = kwargs.get("env", {})
                self.pid = 4242

        monkeypatch.setattr("subprocess.Popen", _FakePopen)
        task = kb.Task(
            id="t_dispatch_env",
            title="x",
            body=None,
            assignee="coder",
            status="ready",
            priority=0,
            created_by=None,
            created_at=0,
            started_at=None,
            completed_at=None,
            workspace_kind="scratch",
            workspace_path=None,
            claim_lock=None,
            claim_expires=None,
            tenant=None,
        )
        assert kb._default_spawn(task, str(tmp_path / "ws")) == 4242

        env = captured["env"]
        assert env["HERMES_OBSIDIAN_VAULT"] == str(vault)
        assert env["HERMES_KANBAN_BOARD"] == "default"
        assert env["HERMES_KANBAN_WORKSPACES_ROOT"] == str(
            self._store(vault) / "workspaces"
        )
        assert env["HERMES_KANBAN_TASK"] == "t_dispatch_env"
        assert not (default_home / "kanban.db").exists()


# ---------------------------------------------------------------------------
# latest_summary / latest_summaries — surface task_runs.summary handoffs
# ---------------------------------------------------------------------------

def test_latest_summary_returns_none_when_no_runs(kanban_home):
    """A freshly-created task has no runs and therefore no summary."""
    with kb.connect() as conn:
        t = kb.create_task(conn, title="fresh", assignee="alice")
        assert kb.latest_summary(conn, t) is None


def test_latest_summary_returns_summary_after_complete(kanban_home):
    """``complete_task(summary=...)`` is the canonical kanban-worker
    handoff; ``latest_summary`` must surface it so dashboards/CLI can
    render what the worker actually did."""
    handoff = "shipped 3 files, ran tests, opened PR #42"
    with kb.connect() as conn:
        t = kb.create_task(conn, title="work", assignee="alice")
        kb.complete_task(conn, t, summary=handoff)
        assert kb.latest_summary(conn, t) == handoff


def test_latest_summary_picks_newest_when_multiple_runs(kanban_home):
    """When a task has been re-run (block → unblock → complete), the
    newest run's summary wins. We unblock to take the task back to
    ``ready``, then complete a second time and verify the second
    summary surfaces."""
    with kb.connect() as conn:
        t = kb.create_task(conn, title="retry", assignee="alice")
        kb.complete_task(conn, t, summary="first attempt")
        # Move back to ready by editing the task JSON — block_task /
        # unblock_task paths require an active claim, but we just want a
        # second run row to exist with a later ended_at.
        _edit_task_file(t, status="ready", completed_at=None)
        # Sleep 1s so the second run's ended_at is provably later than
        # the first (complete_task uses int(time.time())).
        time.sleep(1.05)
        kb.complete_task(conn, t, summary="second attempt — final")
        assert kb.latest_summary(conn, t) == "second attempt — final"


def test_latest_summary_skips_empty_string(kanban_home):
    """A run with an empty-string summary should not mask an earlier
    populated one — empty strings carry no information."""
    with kb.connect() as conn:
        t = kb.create_task(conn, title="t", assignee="alice")
        kb.complete_task(conn, t, summary="real handoff")
        # Inject a later run with empty summary directly. Workers
        # writing "" instead of None is a real shape we want to ignore.
        _append_run_file({
            "id": 999999, "task_id": t, "profile": "alice", "step_key": None,
            "status": "done", "claim_lock": None, "claim_expires": None,
            "worker_pid": None, "max_runtime_seconds": None,
            "last_heartbeat_at": None, "started_at": int(time.time()) + 1,
            "ended_at": int(time.time()) + 2, "outcome": "completed",
            "summary": "", "metadata": None, "error": None,
        })
        assert kb.latest_summary(conn, t) == "real handoff"


def test_latest_summaries_batch_omits_tasks_without_summary(kanban_home):
    """``latest_summaries`` is the dashboard's N+1 escape hatch — it
    must return only entries for tasks that actually have a summary,
    keep the per-task latest, and accept an empty input gracefully."""
    with kb.connect() as conn:
        t1 = kb.create_task(conn, title="a", assignee="alice")
        t2 = kb.create_task(conn, title="b", assignee="bob")
        t3 = kb.create_task(conn, title="c", assignee="carol")
        kb.complete_task(conn, t1, summary="alpha")
        kb.complete_task(conn, t3, summary="charlie")
        out = kb.latest_summaries(conn, [t1, t2, t3])
        assert out == {t1: "alpha", t3: "charlie"}
        # Empty input → empty dict, no SQL syntax error from "IN ()".
        assert kb.latest_summaries(conn, []) == {}
