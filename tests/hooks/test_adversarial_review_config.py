"""Tests for adversarial_review_lib config loading + enablement (issue #77, Task 1).

Config lives in .rawgentic_workspace.json per-project entry (sibling to
critiqueMethod / headlessEnabled). Loading is FAIL-CLOSED: any error, missing
file, missing key, or malformed value resolves to disabled — never raises,
never silently enables.
"""
import json
import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

import adversarial_review_lib as arl  # noqa: E402


def _write_ws(tmp_path: Path, projects: list[dict]) -> Path:
    ws = tmp_path / ".rawgentic_workspace.json"
    ws.write_text(json.dumps({"version": 1, "projects": projects}, indent=2))
    return ws


# --- load_adversarial_review_config ---

def test_load_missing_file_returns_disabled(tmp_path):
    cfg = arl.load_adversarial_review_config(str(tmp_path / "nope.json"), "p")
    assert cfg.enabled is False
    assert cfg.workflows == ()


def test_load_missing_project_returns_disabled(tmp_path):
    ws = _write_ws(tmp_path, [{"name": "other", "path": "./projects/other"}])
    cfg = arl.load_adversarial_review_config(str(ws), "missing")
    assert cfg.enabled is False
    assert cfg.workflows == ()


def test_load_no_field_returns_disabled(tmp_path):
    ws = _write_ws(tmp_path, [{"name": "p", "path": "./projects/p"}])
    cfg = arl.load_adversarial_review_config(str(ws), "p")
    assert cfg.enabled is False
    assert cfg.workflows == ()


def test_load_full_object(tmp_path):
    ws = _write_ws(tmp_path, [{
        "name": "p", "path": "./projects/p",
        "adversarialReview": {"enabled": True, "workflows": ["implement-feature", "fix-bug"]},
    }])
    cfg = arl.load_adversarial_review_config(str(ws), "p")
    assert cfg.enabled is True
    assert cfg.workflows == ("implement-feature", "fix-bug")


def test_load_bool_shorthand_true(tmp_path):
    ws = _write_ws(tmp_path, [{"name": "p", "path": "./projects/p", "adversarialReview": True}])
    cfg = arl.load_adversarial_review_config(str(ws), "p")
    assert cfg.enabled is True
    assert cfg.workflows == ()


def test_load_bool_shorthand_false(tmp_path):
    ws = _write_ws(tmp_path, [{"name": "p", "path": "./projects/p", "adversarialReview": False}])
    cfg = arl.load_adversarial_review_config(str(ws), "p")
    assert cfg.enabled is False


def test_load_malformed_json_returns_disabled(tmp_path):
    ws = tmp_path / ".rawgentic_workspace.json"
    ws.write_text("{ this is not json ]")
    cfg = arl.load_adversarial_review_config(str(ws), "p")
    assert cfg.enabled is False
    assert cfg.workflows == ()


def test_load_bad_type_returns_disabled(tmp_path):
    # adversarialReview is a string -> not a valid shape -> disabled, no crash
    ws = _write_ws(tmp_path, [{"name": "p", "path": "./projects/p", "adversarialReview": "yes"}])
    cfg = arl.load_adversarial_review_config(str(ws), "p")
    assert cfg.enabled is False


def test_load_enabled_non_bool_coerced_fail_closed(tmp_path):
    # enabled present but not a bool -> treat as disabled (do not silently enable)
    ws = _write_ws(tmp_path, [{
        "name": "p", "path": "./projects/p",
        "adversarialReview": {"enabled": "true", "workflows": ["fix-bug"]},
    }])
    cfg = arl.load_adversarial_review_config(str(ws), "p")
    assert cfg.enabled is False


def test_load_workflows_non_list_ignored(tmp_path):
    ws = _write_ws(tmp_path, [{
        "name": "p", "path": "./projects/p",
        "adversarialReview": {"enabled": True, "workflows": "implement-feature"},
    }])
    cfg = arl.load_adversarial_review_config(str(ws), "p")
    assert cfg.enabled is True
    assert cfg.workflows == ()  # non-list -> empty, not crash


def test_load_workflows_filters_non_strings(tmp_path):
    ws = _write_ws(tmp_path, [{
        "name": "p", "path": "./projects/p",
        "adversarialReview": {"enabled": True, "workflows": ["fix-bug", 5, None, "implement-feature"]},
    }])
    cfg = arl.load_adversarial_review_config(str(ws), "p")
    assert cfg.workflows == ("fix-bug", "implement-feature")


# --- is_enabled_for ---

def test_is_enabled_for_true_when_listed(tmp_path):
    ws = _write_ws(tmp_path, [{
        "name": "p", "path": "./projects/p",
        "adversarialReview": {"enabled": True, "workflows": ["implement-feature"]},
    }])
    assert arl.is_enabled_for(str(ws), "p", "implement-feature") is True
    assert arl.is_enabled_for(str(ws), "p", "fix-bug") is False


def test_is_enabled_for_false_when_disabled_even_if_listed(tmp_path):
    ws = _write_ws(tmp_path, [{
        "name": "p", "path": "./projects/p",
        "adversarialReview": {"enabled": False, "workflows": ["implement-feature"]},
    }])
    assert arl.is_enabled_for(str(ws), "p", "implement-feature") is False


def test_is_enabled_for_bool_shorthand_has_no_workflows(tmp_path):
    # bool shorthand enables but lists no workflows -> embedded gates stay off
    ws = _write_ws(tmp_path, [{"name": "p", "path": "./projects/p", "adversarialReview": True}])
    assert arl.is_enabled_for(str(ws), "p", "implement-feature") is False


def test_is_enabled_for_missing_file_false(tmp_path):
    assert arl.is_enabled_for(str(tmp_path / "nope.json"), "p", "implement-feature") is False


@pytest.mark.parametrize("skill", ["create-issue", "refactor", "implement-feature", "fix-bug"])
def test_is_enabled_for_accepts_all_workflow_skill_names(tmp_path, skill):
    """#79: is_enabled_for is skill-name-generic — WF1/WF4 need no engine change."""
    ws = _write_ws(tmp_path, [{
        "name": "p", "path": "./projects/p",
        "adversarialReview": {"enabled": True, "workflows": [skill]},
    }])
    assert arl.is_enabled_for(str(ws), "p", skill) is True
    # a different skill not in the list stays off
    assert arl.is_enabled_for(str(ws), "p", "some-other-skill") is False


# --- wholeIssueDelegation (#133): the "no lib change" claim, proven end-to-end ---

def test_is_enabled_for_reads_whole_issue_delegation_key(tmp_path):
    """#133: is_enabled_for(..., key="wholeIssueDelegation") reads the arbitrary
    per-project field through the same loader, so the feature needs NO lib change.
    Guards against a regression that hardcodes the key set."""
    ws = _write_ws(tmp_path, [{
        "name": "p", "path": "./projects/p",
        "wholeIssueDelegation": {"enabled": True, "workflows": ["implement-feature"]},
    }])
    assert arl.is_enabled_for(str(ws), "p", "implement-feature", key="wholeIssueDelegation") is True
    # a skill not in the list stays off
    assert arl.is_enabled_for(str(ws), "p", "fix-bug", key="wholeIssueDelegation") is False
    # and the default key is independent — absent adversarialReview stays off
    assert arl.is_enabled_for(str(ws), "p", "implement-feature") is False


def test_whole_issue_delegation_absent_is_disabled(tmp_path):
    ws = _write_ws(tmp_path, [{"name": "p", "path": "./projects/p"}])
    assert arl.is_enabled_for(str(ws), "p", "implement-feature", key="wholeIssueDelegation") is False


def test_whole_issue_delegation_disabled_flag(tmp_path):
    ws = _write_ws(tmp_path, [{
        "name": "p", "path": "./projects/p",
        "wholeIssueDelegation": {"enabled": False, "workflows": ["implement-feature"]},
    }])
    assert arl.is_enabled_for(str(ws), "p", "implement-feature", key="wholeIssueDelegation") is False
