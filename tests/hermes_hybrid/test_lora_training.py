"""SDXL LoRA queue: validation, idempotency and self-contained notebook syntax."""
from __future__ import annotations

import json

import pytest

from hermes_hybrid.drive_vault import DriveVault, FakeDriveTransport
from hermes_hybrid.lora_training import (
    LORA_ROOT, build_notebook, report, submit, validate_lora_spec,
)


@pytest.fixture
def vault():
    transport = FakeDriveTransport()
    transport.seed_root()
    return DriveVault(transport, sleeper=lambda _: None)


def spec(**changes):
    data = {
        "model_path": "06_Automation/Hermes/LoRA/base/wai-v14.safetensors",
        "dataset_path": "06_Automation/Hermes/LoRA/dataset/keinastyle",
    }
    data.update(changes)
    return data


def test_training_defaults_and_monochrome_control():
    out = validate_lora_spec(spec())
    assert out["steps"] == 2800
    assert out["resolution"] == 768
    assert out["network_dim"] == 16
    assert out["network_alpha"] == 8
    assert out["monochrome_weight"] == 0.7
    assert out["executor"] == "colab"


@pytest.mark.parametrize("changes", [
    {"model_path": "/etc/passwd"},
    {"dataset_path": "06_Automation/Hermes/../private"},
    {"dataset_path": "https://example.invalid"},
    {"dataset_path": "06_Automation/Hermes/bad\\path"},
    {"model_path": "06_Automation/Hermes/model.ckpt"},
    {"trigger": "bad; touch /tmp/owned"},
    {"steps": 2800.1},
    {"steps": True},
    {"steps": 1},
    {"resolution": 777},
    {"network_dim": 7},
    {"network_alpha": 100},
    {"monochrome_weight": 0},
    {"learning_rate": 1},
])
def test_rejects_unsafe_or_invalid_spec(changes):
    with pytest.raises(ValueError):
        validate_lora_spec(spec(**changes))


def test_submit_notebook_and_progress_status(vault):
    new = submit(vault, spec(), job_id="lora-test-1")
    assert new["state"] == "queued"
    root = LORA_ROOT + "/lora-test-1"
    notebook = json.loads(vault.read_bytes(root + "/train.ipynb").decode("utf-8"))
    assert notebook["nbformat"] == 4
    assert len(notebook["cells"]) == 3
    source = "".join(notebook["cells"][2]["source"])
    compile(source, "colab-training-cell", "exec")
    assert "lora-test-1" in source
    assert "sdxl_train_network.py" in source
    assert "save_state" in source
    assert "progress.json" in source
    assert "print(" in source
    state = report(vault, "lora-test-1")
    assert state["found"] is True
    assert state["status"]["state"] == "queued"
    assert state["progress"] is None
    assert state["result"] is None
    repeated = submit(vault, spec(), job_id="lora-test-1")
    assert repeated["state"] == "already_exists"
    with pytest.raises(ValueError):
        submit(vault, spec(steps=900), job_id="lora-test-1")


def test_pinned_wai_v14_without_local_model():
    spec_without_path = spec()
    del spec_without_path["model_path"]
    clean = validate_lora_spec(spec_without_path)
    assert clean["model_path"] is None
    assert clean["model_source"] == "wai_v140"
    with pytest.raises(ValueError):
        validate_lora_spec(spec(model_source="wai_v140"))
    with pytest.raises(ValueError):
        validate_lora_spec({"model_source": "arbitrary_url", "dataset_path": spec()["dataset_path"]})


def test_public_wai_model_hash_in_notebook():
    code = "".join(build_notebook("lora-abc")["cells"][2]["source"])
    compile(code, "colab-lora-trainer", "exec")
    assert "bdb59bac77d94ae7a55ff893170f9554c3f349e48a1b73c0c17c0b7c6f4d41a2" in code
    assert "MODEL_URL" in code
    assert "downloading_model" in code


def test_generated_notebook_never_executed_on_hermes_host():
    notebook = build_notebook("lora-123")
    assert all(cell["execution_count"] is None for cell in notebook["cells"][1:])
    assert all(not cell["outputs"] for cell in notebook["cells"][1:])
    source = "".join(notebook["cells"][2]["source"])
    assert '__JOB_ID__' not in source
    assert '"lora-123"' in source


def test_missing_status_returns_not_found(vault):
    assert report(vault, "lora-missing") == {"job_id": "lora-missing", "found": False}
