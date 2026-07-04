"""Tests for adversarial_review_lib "diff" artifact type + confidence map (#131, Task 2)."""
import sys
from pathlib import Path

HOOKS_DIR = Path(__file__).resolve().parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

import adversarial_review_lib as arl  # noqa: E402


def test_diff_in_artifact_types():
    assert "diff" in arl.ARTIFACT_TYPES


def test_type_lens_has_diff_key_with_refutation_contract():
    lens = arl._TYPE_LENS["diff"]
    assert "fail-open" in lens
    assert "bypass" in lens
    assert "vacuous" in lens
    assert "+/-" in lens


def test_build_prompt_diff_includes_lens_and_nonce_fence():
    prompt = arl.build_prompt("SOME DIFF TEXT", "diff")
    assert arl._TYPE_LENS["diff"] in prompt
    assert "=== BEGIN UNTRUSTED ARTIFACT" in prompt
    assert "=== END UNTRUSTED ARTIFACT" in prompt


def test_build_prompt_unknown_type_falls_back_to_generic_lens():
    prompt = arl.build_prompt("SOME TEXT", "not-a-real-type")
    assert arl._TYPE_LENS["generic"] in prompt


def test_adv_confidence_to_float_keys_and_values():
    mapping = arl.ADV_CONFIDENCE_TO_FLOAT
    assert set(mapping.keys()) == {"high", "medium", "low"}
    for value in mapping.values():
        assert isinstance(value, float)
        assert 0 < value <= 1
    assert mapping["high"] > mapping["medium"] > mapping["low"]
