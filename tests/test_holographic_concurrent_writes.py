"""Concurrency regressions for the Vault-backed holographic fact store."""

import time
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from plugins.memory.holographic.store import MemoryStore


def test_two_store_instances_cannot_overwrite_simultaneous_facts(tmp_path, monkeypatch):
    """Independent gateway workers must not allocate the same fact ID."""
    monkeypatch.setenv("HERMES_OBSIDIAN_VAULT", str(tmp_path / "vault"))
    stores = [MemoryStore(hrr_dim=32), MemoryStore(hrr_dim=32)]

    # Widen the read/modify/write window to reproduce the former race.
    for store in stores:
        original = store._next_fact_id

        def delayed_next(original=original):
            next_id = original()
            time.sleep(0.15)
            return next_id

        monkeypatch.setattr(store, "_next_fact_id", delayed_next)

    ready = Barrier(2)

    def add(index):
        ready.wait(timeout=5)
        return stores[index].add_fact(f"independent worker fact {index}")

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(add, index) for index in range(2)]
        ids = [future.result(timeout=10) for future in futures]

    assert set(ids) == {1, 2}
    facts = stores[0].list_facts(limit=10)
    assert {fact["content"] for fact in facts} == {
        "independent worker fact 0",
        "independent worker fact 1",
    }


def test_two_store_instances_cannot_lose_feedback_updates(tmp_path, monkeypatch):
    """Concurrent trust updates must observe the preceding committed write."""
    monkeypatch.setenv("HERMES_OBSIDIAN_VAULT", str(tmp_path / "vault"))
    stores = [MemoryStore(hrr_dim=32), MemoryStore(hrr_dim=32)]
    fact_id = stores[0].add_fact("shared fact")

    for store in stores:
        original = store._read_fact

        def delayed_read(fact_id, original=original):
            fact = original(fact_id)
            time.sleep(0.15)
            return fact

        monkeypatch.setattr(store, "_read_fact", delayed_read)

    ready = Barrier(2)

    def feedback(index):
        ready.wait(timeout=5)
        return stores[index].record_feedback(fact_id, helpful=True)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(feedback, index) for index in range(2)]
        [future.result(timeout=10) for future in futures]

    fact = stores[0].list_facts(limit=10)[0]
    assert fact["trust_score"] == pytest.approx(0.60)
    assert fact["helpful_count"] == 2
