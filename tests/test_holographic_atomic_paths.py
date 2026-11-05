"""Regression tests for atomic writes and bank filename containment."""

import base64
import json
import os

import pytest

from plugins.memory.holographic import holographic as hrr
from plugins.memory.holographic import store as store_module
from plugins.memory.holographic.store import MemoryStore


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_OBSIDIAN_VAULT", str(tmp_path / "vault"))
    return MemoryStore(hrr_dim=32)


def test_preplanted_pid_temp_symlink_cannot_clobber_external_file(store, tmp_path):
    """The former PID-only temp filename was predictable and followed links."""
    external = tmp_path / "outside.txt"
    external.write_text("must remain unchanged", encoding="utf-8")
    planted = store.facts_dir / f".1.json.tmp-{os.getpid()}"
    planted.symlink_to(external)

    fact_id = store.add_fact("atomic symlink regression")
    assert (store.facts_dir / f"{fact_id}.json").is_file()
    assert external.read_text(encoding="utf-8") == "must remain unchanged"
    assert planted.is_symlink()


def test_failed_atomic_replace_cleans_up_unique_temp(tmp_path, monkeypatch):
    target = tmp_path / "target.json"

    def fail_replace(*_args):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(store_module.os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated replace failure"):
        store_module._atomic_write_json(target, {"fact_id": 1})

    assert not target.exists()
    assert list(tmp_path.glob(".target.json.tmp-*")) == []


def test_bank_lookup_cannot_read_parent_directory(store):
    external = store.db_path / "outside.json"
    external.write_text(
        json.dumps({"vector_b64": base64.b64encode(b"private").decode("ascii")}),
        encoding="utf-8",
    )
    assert store.get_bank_vector("../outside") is None
    assert external.is_file()


def test_bank_lookup_does_not_follow_symlink(store, tmp_path):
    external = tmp_path / "private.json"
    external.write_text(
        json.dumps({"vector_b64": base64.b64encode(b"private").decode("ascii")}),
        encoding="utf-8",
    )
    (store.banks_dir / "cat_general.json").symlink_to(external)
    assert store.get_bank_vector("cat:general") is None
    assert external.is_file()


def test_category_bank_name_is_single_encoded_filename(store, monkeypatch):
    category = "a/../nested"
    store._hrr_available = True
    monkeypatch.setattr(
        store, "_all_facts",
        lambda: [{"category": category, "hrr_vector_b64": base64.b64encode(b"raw").decode("ascii")}],
    )
    monkeypatch.setattr(hrr, "bytes_to_phases", lambda value: value)
    monkeypatch.setattr(hrr, "bundle", lambda *values: b"bundled")
    monkeypatch.setattr(hrr, "snr_estimate", lambda *_args: None)
    monkeypatch.setattr(hrr, "phases_to_bytes", lambda value: value)

    store._rebuild_bank(category)
    bank_path = store._bank_path(f"cat:{category}")
    assert bank_path.parent == store.banks_dir
    assert bank_path.is_file()
    assert not (store.banks_dir / "cat_a").exists()
    assert store.get_bank_vector(f"cat:{category}") == b"bundled"
