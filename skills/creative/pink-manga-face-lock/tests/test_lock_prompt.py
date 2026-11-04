"""Regression tests for the no-GPU pink manga face style suppression composer."""
import importlib.util
from pathlib import Path

p = Path(__file__).resolve().parents[1] / "scripts" / "lock_prompt.py"
spec = importlib.util.spec_from_file_location("pink_face_lock", p)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_original_reference_constraints_are_first():
    v = module.build("girl", "keep pose", "keep hoodie", "anime, wide open circular eyes")
    assert "half-lidded red eyes" in v["positive_prompt"]
    assert "wide open circular eyes" not in v["positive_prompt"].lower()
    assert v["removed_conflicts"] == ["wide open circular eyes"]
    assert "keep hoodie" in v["positive_prompt"]


def test_prompt_only_provider_gets_suppressions():
    v = module.build("girl", base_negative="watermark")
    assert "stock innocent happy smile" in v["prompt_only_prompt"]
    assert "watermark" in v["prompt_only_prompt"]
    assert "wide round-eyed expression" in v["negative_prompt"]
    assert not v["automatic_style_validation"]
    assert not v["reference_image_attached"]


def test_bad_input_fails():
    try:
        module.build(" ")
    except ValueError:
        pass
    else:
        raise AssertionError("empty subject accepted")
