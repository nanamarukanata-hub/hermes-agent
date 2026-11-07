"""Hermes Drive-backed SDXL LoRA jobs for a user-started Colab GPU runtime.

A job stores validated data only. Hermes never executes remote code, starts an
idle Colab runtime, or pretends that a WebSocket handshake executes a cell.
The generated notebook is self-contained and requires Colab user authorization.
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from hermes_hybrid.colab_jobs import _AUTH_CELL
from hermes_hybrid.drive_vault import DriveVault, VAULT_ROOT_NAME
from hermes_hybrid.vault_kv import validate_id

LORA_ROOT = "06_Automation/Hermes/LoRAJobs"
_ALLOWED_PREFIX = "06_Automation/Hermes/"
_PART = re.compile(r"^[A-Za-z0-9_. -]+$")
_TRIGGER = re.compile(r"^[A-Za-z][A-Za-z0-9_]{1,31}$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_path(value: Any, *, suffix: str | None = None) -> str:
    if not isinstance(value, str) or not value.startswith(_ALLOWED_PREFIX) or len(value) > 500:
        raise ValueError("model_path and dataset_path must be Vault-relative under 06_Automation/Hermes/")
    if any(part in ("", ".", "..") or not _PART.fullmatch(part) for part in value.split("/")):
        raise ValueError("invalid Vault-relative path")
    if "\\" in value:
        raise ValueError("invalid path separator")
    if suffix is not None and not value.lower().endswith(suffix):
        raise ValueError("base model must be a .safetensors checkpoint")
    return value


def validate_lora_spec(spec: dict) -> dict:
    """Validate explicit SDXL parameters; never accept arbitrary shell commands."""
    if not isinstance(spec, dict):
        raise ValueError("LoRA spec must be an object")
    if spec.get("type", "sdxl_lora") != "sdxl_lora":
        raise ValueError("only SDXL LoRA is supported")
    requested_model = spec.get("model_path")
    model_source = spec.get("model_source")
    if requested_model:
        if model_source is not None:
            raise ValueError("choose model_path or model_source, not both")
        model = _safe_path(requested_model, suffix=".safetensors")
    else:
        if model_source not in (None, "wai_v140"):
            raise ValueError("only the pinned public wai_v140 model source is allowed")
        model = None
        model_source = "wai_v140"
    data = _safe_path(spec.get("dataset_path"))
    trigger = spec.get("trigger", "keinastyle")
    if not isinstance(trigger, str) or not _TRIGGER.fullmatch(trigger):
        raise ValueError("trigger must be 2-32 ASCII alphanumeric/underscore characters")
    steps = spec.get("steps", 2800)
    resolution = spec.get("resolution", 768)
    dim = spec.get("network_dim", 16)
    alpha = spec.get("network_alpha", 8)
    seed = spec.get("seed", 42)
    checkpoint_steps = spec.get("checkpoint_steps", 400)
    monochrome_weight = spec.get("monochrome_weight", 0.7)
    learning_rate = spec.get("learning_rate", 0.0001)
    if type(steps) is not int or not (100 <= steps <= 8000):
        raise ValueError("steps must be an integer from 100 to 8000")
    if type(resolution) is not int or resolution not in (512, 640, 768, 896, 1024):
        raise ValueError("resolution must be one of 512,640,768,896,1024")
    if type(dim) is not int or dim not in (8, 16, 32, 64):
        raise ValueError("network_dim must be 8,16,32,64")
    if type(alpha) is not int or not (1 <= alpha <= dim):
        raise ValueError("network_alpha must be an integer in 1..network_dim")
    if type(seed) is not int or not (0 <= seed < 2**32):
        raise ValueError("seed must be a nonnegative 32-bit integer")
    if type(checkpoint_steps) is not int or not (100 <= checkpoint_steps <= steps):
        raise ValueError("checkpoint_steps must be between 100 and steps")
    if type(monochrome_weight) not in (int, float) or not (0.1 <= monochrome_weight <= 1):
        raise ValueError("monochrome_weight must be between 0.1 and 1.0")
    if type(learning_rate) not in (int, float) or not (0.00001 <= learning_rate <= 0.0005):
        raise ValueError("learning_rate must be between 1e-5 and 5e-4")
    return {"type": "sdxl_lora", "executor": "colab",
            "model_path": model, "model_source": model_source,
            "dataset_path": data, "trigger": trigger,
            "steps": steps, "resolution": resolution,
            "network_dim": dim, "network_alpha": alpha, "seed": seed,
            "checkpoint_steps": checkpoint_steps,
            "monochrome_weight": float(monochrome_weight),
            "learning_rate": float(learning_rate)}


def submit(vault: DriveVault, spec: dict, job_id: str | None = None) -> dict:
    clean = validate_lora_spec(spec)
    name = validate_id(job_id or "lora-" + uuid.uuid4().hex[:20])
    base = LORA_ROOT + "/" + name
    existing = vault.read_json(base + "/job.json")
    if existing is not None:
        if existing.get("spec") != clean:
            raise ValueError("job_id already exists with a different LoRA spec")
        if not vault.exists(base + "/train.ipynb"):
            vault.write_bytes(base + "/train.ipynb", notebook_bytes(name),
                              mime_type="application/x-ipynb+json")
        return {"job_id": name, "state": "already_exists", "notebook": base + "/train.ipynb"}
    notebook = notebook_bytes(name)
    vault.write_json(base + "/job.json", {
        "job_id": name, "schema": 1, "spec": clean, "created_at": _now()
    })
    vault.write_bytes(base + "/train.ipynb", notebook, mime_type="application/x-ipynb+json")
    vault.write_json(base + "/status.json", {
        "job_id": name, "state": "queued", "updated_at": _now()
    })
    return {"job_id": name, "state": "queued", "notebook": base + "/train.ipynb",
            "note": "Notebook must be executed by an authorized Colab GPU session."}


def lora_health(vault: DriveVault) -> dict:
    """Separate heartbeat: LoRA-only GPU notebook does not consume ColabJobs."""
    from hermes_hybrid.colab_dispatch import _health

    return _health(vault, LORA_ROOT + "/worker.json", current_time=None)


def report(vault: DriveVault, job_id: str) -> dict:
    name = validate_id(job_id)
    base = LORA_ROOT + "/" + name
    job = vault.read_json(base + "/job.json")
    if job is None:
        return {"job_id": name, "found": False}
    return {"job_id": name, "found": True,
            "spec": job.get("spec"),
            "status": vault.read_json(base + "/status.json"),
            "progress": vault.read_json(base + "/progress.json"),
            "result": vault.read_json(base + "/result.json"),
            "lora_worker": lora_health(vault),
            "notebook": base + "/train.ipynb"}


# This cell is standalone: the user's Colab session executes it after Drive auth.
# Do not interpolate user-controlled code. Only a validated job ID is injected.
_TRAIN_CELL = r'''
import hashlib
import json
import os
import random
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

JOB_ID = "__JOB_ID__"
VAULT_NAME = "__VAULT_NAME__"
VAULT = (Path("/content/drive/MyDrive") / VAULT_NAME).resolve()
JOB = VAULT / "06_Automation" / "Hermes" / "LoRAJobs" / JOB_ID
SRC_ROOT = (VAULT / "06_Automation" / "Hermes").resolve()
GIT_REVISION = "cb70b96858477e716a2096292ab8aab433277f38"
MODEL_URL = ("https://huggingface.co/arcacolab/models-moved/resolve/"
             "f41cca2847de07c193cb67619cf864b19a53033b/waiIllustriousSDXL_v140.safetensors")
MODEL_SHA256 = "bdb59bac77d94ae7a55ff893170f9554c3f349e48a1b73c0c17c0b7c6f4d41a2"
SCRIPT_REPO = Path("/content/hermes-sd-scripts")
IMAGES = {".png", ".jpg", ".jpeg", ".webp"}
_LOSS = re.compile(r"(?:^|[,\s])loss[=:]\s*([0-9.eE+-]+)", re.IGNORECASE)
_STEPS = re.compile(r"(\d+)\s*/\s*(\d+)")

def stamp():
    return datetime.now(timezone.utc).isoformat()

def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    # Google Drive FUSE is not a transactional DB; this file remains small.
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

def state(name, **extra):
    write_json(JOB / "status.json", {"job_id": JOB_ID, "state": name,
                                     "updated_at": stamp(), **extra})

def safe_source(value):
    if not isinstance(value, str) or not value.startswith("06_Automation/Hermes/"):
        raise ValueError("path outside canonical Hermes Vault")
    parts = value.split("/")
    if any(part in ("", ".", "..") or not re.fullmatch(r"[A-Za-z0-9_. -]+", part) for part in parts):
        raise ValueError("invalid path")
    item = (VAULT.joinpath(*parts)).resolve()
    if not item.is_relative_to(SRC_ROOT):
        raise ValueError("path escapes Hermes subtree")
    return item

def main():
    job = json.loads((JOB / "job.json").read_text(encoding="utf-8"))
    assert job.get("schema") == 1 and job.get("job_id") == JOB_ID
    spec = job["spec"]
    assert spec.get("type") == "sdxl_lora" and spec.get("executor") == "colab"
    assert re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{1,31}", spec["trigger"])
    assert isinstance(spec["steps"], int) and 100 <= spec["steps"] <= 8000
    assert spec["resolution"] in (512, 640, 768, 896, 1024)
    assert spec["network_dim"] in (8, 16, 32, 64)
    assert 1 <= spec["network_alpha"] <= spec["network_dim"]
    assert 0.1 <= spec["monochrome_weight"] <= 1
    assert 0.00001 <= spec["learning_rate"] <= 0.0005
    dataset = safe_source(spec["dataset_path"])
    model = safe_source(spec["model_path"]) if spec.get("model_path") else None
    if not dataset.is_dir():
        raise FileNotFoundError("dataset directory is missing in the Drive Vault")
    if model is not None and (not model.is_file() or model.suffix.lower() != ".safetensors"):
        raise FileNotFoundError("SDXL .safetensors checkpoint is missing in the Drive Vault")
    if model is None and spec.get("model_source") != "wai_v140":
        raise ValueError("unknown model_source")
    # Never silently train on CPU if the Colab T4 is unavailable.
    import torch
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA GPU unavailable; Colab runtime must be GPU-enabled")
    gpu_name = torch.cuda.get_device_name(0)
    state("preparing", gpu=gpu_name)
    # Distinct from ColabJobs/worker.json: this notebook trains, but does
    # not process the ordinary echo/hash queue. A daemon heartbeat survives
    # lengthy dependency installation and dataset staging.
    def lora_heartbeat():
        beat_file = VAULT / "06_Automation" / "Hermes" / "LoRAJobs" / "worker.json"
        while True:
            try:
                status = json.loads((JOB / "status.json").read_text(encoding="utf-8"))
                terminal = status.get("state") in ("done", "failed")
                write_json(beat_file, {
                    "job_id": JOB_ID, "worker_id": JOB_ID, "state": "stopped" if terminal else "running",
                    "last_seen_at": stamp(), "device": {"gpu": {"available": True, "name": gpu_name}}
                })
                if terminal:
                    return
            except Exception:
                pass
            time.sleep(30)
    threading.Thread(target=lora_heartbeat, name="hermes-lora-heartbeat",
                     daemon=True).start()
    if model is None:
        state("downloading_model", gpu=gpu_name)
        model = Path("/content/hermes-lora-models/waiIllustriousSDXL_v140.safetensors")
        model.parent.mkdir(parents=True, exist_ok=True)
        def valid_checkpoint(path):
            if not path.is_file():
                return False
            digest = hashlib.sha256()
            with path.open("rb") as fh:
                for chunk in iter(lambda: fh.read(4 * 1024 * 1024), b""):
                    digest.update(chunk)
            return digest.hexdigest() == MODEL_SHA256
        if not valid_checkpoint(model):
            partial = model.with_suffix(".download")
            try:
                with urllib.request.urlopen(MODEL_URL, timeout=60) as remote, partial.open("wb") as dst:
                    shutil.copyfileobj(remote, dst, length=4 * 1024 * 1024)
                if not valid_checkpoint(partial):
                    raise ValueError("WAI v14 downloaded checkpoint SHA256 mismatch")
                partial.replace(model)
            finally:
                partial.unlink(missing_ok=True)
        state("preparing", gpu=gpu_name)
    images = sorted(p for p in dataset.rglob("*") if p.suffix.lower() in IMAGES and p.is_file())
    if not 10 <= len(images) <= 6000:
        raise ValueError("dataset must contain between 10 and 6000 image files")
    work = Path("/content/hermes-lora-work") / JOB_ID / "train"
    folder = work / ("1_" + spec["trigger"])
    folder.mkdir(parents=True, exist_ok=True)
    rng = random.Random(spec["seed"])
    chosen, monochrome = 0, 0
    for index, source in enumerate(images):
        if not source.resolve().is_relative_to(dataset.resolve()):
            raise ValueError("dataset contains a link outside the dataset folder")
        caption = source.with_suffix(".txt")
        text = caption.read_text(encoding="utf-8").strip() if caption.is_file() else spec["trigger"]
        if "monochrome" in text.lower():
            monochrome += 1
            if rng.random() > spec["monochrome_weight"]:
                continue
        image_name = "img_%05d%s" % (index, source.suffix.lower())
        shutil.copy2(source, folder / image_name)
        (folder / ("img_%05d.txt" % index)).write_text(text or spec["trigger"], encoding="utf-8")
        chosen += 1
    if chosen < 10:
        raise ValueError("too few images after monochrome weighting")
    write_json(JOB / "dataset-summary.json", {
        "total_images": len(images), "selected_images": chosen,
        "monochrome_captioned_images": monochrome, "gpu": gpu_name,
        "note": "Only captions containing 'monochrome' are downweighted."
    })
    state("installing", gpu=gpu_name)
    if not (SCRIPT_REPO / "sdxl_train_network.py").is_file():
        subprocess.run(["git", "clone", "--filter=blob:none",
                        "https://github.com/kohya-ss/sd-scripts.git", str(SCRIPT_REPO)], check=True)
    subprocess.run(["git", "-C", str(SCRIPT_REPO), "checkout", "--detach", GIT_REVISION], check=True)
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-r",
                    str(SCRIPT_REPO / "requirements.txt")], check=True)
    output = JOB / "output"
    logs = JOB / "logs"
    output.mkdir(parents=True, exist_ok=True)
    logs.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable, "-m", "accelerate.commands.launch",
        "--num_cpu_threads_per_process", "2",
        str(SCRIPT_REPO / "sdxl_train_network.py"),
        "--pretrained_model_name_or_path", str(model),
        "--train_data_dir", str(work),
        "--output_dir", str(output),
        "--logging_dir", str(logs),
        "--output_name", spec["trigger"],
        "--resolution", str(spec["resolution"]) + "," + str(spec["resolution"]),
        "--train_batch_size", "1",
        "--max_train_steps", str(spec["steps"]),
        "--network_module", "networks.lora",
        "--network_dim", str(spec["network_dim"]),
        "--network_alpha", str(spec["network_alpha"]),
        "--network_train_unet_only",
        "--learning_rate", str(spec["learning_rate"]),
        "--optimizer_type", "AdamW8bit",
        "--lr_scheduler", "cosine",
        "--lr_warmup_steps", "100",
        "--seed", str(spec["seed"]),
        "--mixed_precision", "fp16",
        "--save_precision", "fp16",
        "--save_model_as", "safetensors",
        "--gradient_checkpointing", "--cache_latents",
        "--cache_text_encoder_outputs",
        "--no_half_vae", "--sdpa", "--enable_bucket",
        "--save_every_n_steps", str(spec["checkpoint_steps"]),
        "--save_last_n_steps", "2",
        "--save_last_n_steps_state", "1",
        "--save_state", "--save_state_on_train_end",
        "--log_with", "tensorboard",
    ]
    states = sorted(output.glob("*-state"), key=lambda p: p.stat().st_mtime)
    if states:
        command.extend(["--resume", str(states[-1])])
    write_json(JOB / "run-config.json", {
        "runner_commit": GIT_REVISION, "gpu": gpu_name,
        "steps_requested": spec["steps"], "resolution": spec["resolution"],
        "resume_from": states[-1].name if states else None
    })
    state("running", gpu=gpu_name)
    proc = subprocess.Popen(command, cwd=str(SCRIPT_REPO),
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace", bufsize=1)
    progress = {"job_id": JOB_ID, "state": "running", "step": 0,
                "steps_total": spec["steps"], "loss": None,
                "gpu": gpu_name, "updated_at": stamp()}
    stop = threading.Event()
    def pulse():
        while not stop.wait(30):
            progress["updated_at"] = stamp()
            write_json(JOB / "progress.json", dict(progress))
    thread = threading.Thread(target=pulse, daemon=True)
    thread.start()
    try:
        with (JOB / "training.log").open("a", encoding="utf-8") as log:
            for line in proc.stdout:
                log.write(line)
                log.flush()
                match = _STEPS.search(line)
                if match and int(match.group(2)) == spec["steps"]:
                    progress["step"] = min(int(match.group(1)), spec["steps"])
                loss = _LOSS.search(line)
                if loss:
                    try:
                        progress["loss"] = float(loss.group(1))
                    except ValueError:
                        pass
        code = proc.wait()
    finally:
        stop.set()
        thread.join(timeout=5)
    progress["updated_at"] = stamp()
    if code != 0:
        progress["state"] = "failed"
        write_json(JOB / "progress.json", progress)
        raise RuntimeError("sdxl_train_network.py failed (exit %d); inspect training.log" % code)
    checkpoints = sorted(p.name for p in output.glob("*.safetensors"))
    if not checkpoints:
        raise RuntimeError("trainer exited successfully without a .safetensors checkpoint")
    progress["state"] = "done"
    progress["step"] = spec["steps"]
    write_json(JOB / "progress.json", progress)
    write_json(JOB / "result.json", {
        "job_id": JOB_ID, "ok": True, "state": "done",
        "checkpoints": checkpoints, "finished_at": stamp()
    })
    state("done", gpu=gpu_name)
    print("SDXL LoRA done:", checkpoints[-1])

try:
    main()
except Exception as exc:
    state("failed", error_type=type(exc).__name__, error_message=str(exc)[:240])
    write_json(JOB / "result.json", {
        "job_id": JOB_ID, "ok": False, "error": type(exc).__name__,
        "finished_at": stamp()
    })
    raise
'''


def build_notebook(job_id: str, vault_name: str = VAULT_ROOT_NAME) -> dict:
    validate_id(job_id)
    if not vault_name or "/" in vault_name or "\\" in vault_name or vault_name in (".", ".."):
        raise ValueError("invalid Vault name")
    source = _TRAIN_CELL.replace("__JOB_ID__", job_id).replace("__VAULT_NAME__", vault_name)
    return {"nbformat": 4, "nbformat_minor": 0,
            "metadata": {"colab": {"name": "hermes-sdxl-lora-" + job_id + ".ipynb",
                                   "provenance": []},
                         "kernelspec": {"name": "python3", "display_name": "Python 3"}},
            "cells": [
                {"cell_type": "markdown", "metadata": {},
                 "source": ["# Hermes SDXL LoRA training\n",
                            "Run authorization cell then trainer cell on an explicitly enabled GPU runtime. "
                            "Training writes status, progress, checkpoints and logs to the canonical Drive Vault. "
                            "The notebook cannot start a disconnected Colab runtime.\n"]},
                {"cell_type": "code", "metadata": {}, "execution_count": None,
                 "outputs": [], "source": _AUTH_CELL.splitlines(keepends=True)},
                {"cell_type": "code", "metadata": {}, "execution_count": None,
                 "outputs": [], "source": source.splitlines(keepends=True)}
            ]}


def notebook_bytes(job_id: str) -> bytes:
    return (json.dumps(build_notebook(job_id), ensure_ascii=False, indent=2) + "\n").encode("utf-8")
