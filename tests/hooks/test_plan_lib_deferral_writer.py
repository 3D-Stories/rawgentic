"""Tests for the plan_lib deferral writers, public review-log reader, and the
SEVERITY_BANDED_CONFIDENCE source-of-truth constant.

These close the gaps found in the 2026-06-16 implement-feature assessment:
- deferrals.json had a reader (get_deferred_findings) and a validator
  (assert_no_unresolved_high_deferrals) but NO writer, so Step 8a/Step 11 had to
  hand-author JSON whose resolution semantics (_deferral_is_resolved) live only in
  code -> a mistyped field silently drops a deferred Critical/High from the gate.
- Step 11 cited a non-existent public "companion reader" for the review log; only
  the private _read_review_log existed.
- SKILL.md claimed hooks/plan_lib.py is the source of truth for the banded
  confidence values, but the dict did not exist there.
"""
import importlib
import json
import os
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
HOOKS_DIR = REPO_ROOT / "hooks"
SKILL = REPO_ROOT / "skills" / "implement-feature" / "SKILL.md"
sys.path.insert(0, str(HOOKS_DIR))


def _reload_plan_lib():
    if "plan_lib" in sys.modules:
        return importlib.reload(sys.modules["plan_lib"])
    import plan_lib as mod
    return mod


# --- append_deferral: the missing create/re-defer writer ---

class TestAppendDeferral:
    def test_creates_list_file_with_safe_defaults(self, tmp_path):
        mod = _reload_plan_lib()
        path = tmp_path / "deferrals.json"
        entry = mod.append_deferral(
            str(path),
            {"finding_id": "F1", "severity": "High", "originator_reviewer_slot": "R1"},
        )
        assert path.exists()
        data = json.loads(path.read_text())
        assert isinstance(data, list) and len(data) == 1
        # Safe defaults make the entry well-formed for _deferral_is_resolved
        assert entry["status"] == "deferred"
        assert entry["defer_count"] == 1
        assert entry["concurrences"] == []
        assert entry["user_ack"] is False
        # Round-trip: a fresh High deferral is correctly seen as UNRESOLVED
        ok, unresolved = mod.assert_no_unresolved_high_deferrals(str(path))
        assert ok is False and len(unresolved) == 1

    def test_missing_finding_id_fails_closed(self, tmp_path):
        mod = _reload_plan_lib()
        path = tmp_path / "deferrals.json"
        with pytest.raises(ValueError):
            mod.append_deferral(str(path), {"severity": "High"})

    def test_missing_severity_fails_closed(self, tmp_path):
        # severity is what the exit gate keys on; a missing severity would
        # silently drop a High/Critical -> must fail loudly, not write a dud.
        mod = _reload_plan_lib()
        path = tmp_path / "deferrals.json"
        with pytest.raises(ValueError):
            mod.append_deferral(str(path), {"finding_id": "F1"})

    def test_redefer_increments_count_no_duplicate(self, tmp_path):
        mod = _reload_plan_lib()
        path = tmp_path / "deferrals.json"
        f = {"finding_id": "F1", "severity": "High", "originator_reviewer_slot": "R1"}
        mod.append_deferral(str(path), f)
        entry = mod.append_deferral(str(path), f)
        data = json.loads(path.read_text())
        assert len(data) == 1  # re-defer, not a duplicate row
        assert entry["defer_count"] == 2

    def test_preserves_extra_descriptive_fields(self, tmp_path):
        mod = _reload_plan_lib()
        path = tmp_path / "deferrals.json"
        mod.append_deferral(
            str(path),
            {"finding_id": "F1", "severity": "Critical",
             "originator_reviewer_slot": "R2", "description": "path traversal"},
        )
        data = json.loads(path.read_text())
        assert data[0]["description"] == "path traversal"


# --- resolve_deferral: the missing resolve writer (other half of the gap) ---

class TestResolveDeferral:
    def _seed(self, mod, path, **extra):
        mod.append_deferral(
            str(path),
            {"finding_id": "F1", "severity": "High",
             "originator_reviewer_slot": "R1", **extra},
        )

    def test_status_applied_resolves(self, tmp_path):
        mod = _reload_plan_lib()
        path = tmp_path / "deferrals.json"
        self._seed(mod, path)
        mod.resolve_deferral(str(path), "F1", status="applied")
        ok, _ = mod.assert_no_unresolved_high_deferrals(str(path))
        assert ok is True

    def test_independent_concurrence_resolves(self, tmp_path):
        mod = _reload_plan_lib()
        path = tmp_path / "deferrals.json"
        self._seed(mod, path)  # originator R1
        mod.resolve_deferral(str(path), "F1", add_concurrence="R2")
        ok, _ = mod.assert_no_unresolved_high_deferrals(str(path))
        assert ok is True

    def test_self_concurrence_does_not_resolve(self, tmp_path):
        mod = _reload_plan_lib()
        path = tmp_path / "deferrals.json"
        self._seed(mod, path)  # originator R1
        mod.resolve_deferral(str(path), "F1", add_concurrence="R1")
        ok, _ = mod.assert_no_unresolved_high_deferrals(str(path))
        assert ok is False

    def test_unknown_finding_id_fails_closed(self, tmp_path):
        mod = _reload_plan_lib()
        path = tmp_path / "deferrals.json"
        self._seed(mod, path)
        with pytest.raises((KeyError, ValueError)):
            mod.resolve_deferral(str(path), "DOES_NOT_EXIST", status="applied")

    def test_redeferred_concurrence_still_needs_user_ack(self, tmp_path):
        # defer_count >= 2 with an independent concurrence still requires user_ack
        # (matches _deferral_is_resolved). Verifies resolve writes both correctly.
        mod = _reload_plan_lib()
        path = tmp_path / "deferrals.json"
        f = {"finding_id": "F1", "severity": "High", "originator_reviewer_slot": "R1"}
        mod.append_deferral(str(path), f)
        mod.append_deferral(str(path), f)  # defer_count == 2
        mod.resolve_deferral(str(path), "F1", add_concurrence="R2")
        ok, _ = mod.assert_no_unresolved_high_deferrals(str(path))
        assert ok is False  # still needs ack
        mod.resolve_deferral(str(path), "F1", user_ack=True)
        ok, _ = mod.assert_no_unresolved_high_deferrals(str(path))
        assert ok is True


# --- read_review_log: the public reader Step 11 needs ---

class TestReadReviewLogPublic:
    def test_public_reader_returns_entries(self, tmp_path):
        mod = _reload_plan_lib()
        log = tmp_path / "review_log.jsonl"
        mod.append_review_log(str(log), {"task_id": "T1", "sha": "a1", "verdict": "applied"})
        mod.append_review_log(str(log), {"task_id": "T2", "sha": "b2", "verdict": "deferred"})
        entries = mod.read_review_log(str(log))
        assert [e["sha"] for e in entries] == ["a1", "b2"]

    def test_missing_log_returns_empty(self, tmp_path):
        mod = _reload_plan_lib()
        assert mod.read_review_log(str(tmp_path / "none.jsonl")) == []


# --- SEVERITY_BANDED_CONFIDENCE: make the SKILL's source-of-truth claim true ---

_EXPECTED_BANDS = {"Critical": 0.50, "High": 0.65, "Medium": 0.80, "Low": 0.90}


def _parse_skill_banded_block() -> dict:
    """Parse the SEVERITY_BANDED_CONFIDENCE block out of SKILL.md <constants>.

    Scoped to the block so it does not collide with VOLUME_THRESHOLDS, which
    also has Critical/High/Medium/Low keys.
    """
    lines = SKILL.read_text().splitlines()
    start = next(i for i, ln in enumerate(lines) if "SEVERITY_BANDED_CONFIDENCE:" in ln)
    out = {}
    for ln in lines[start + 1:]:
        m = re.match(r"^\s+(Critical|High|Medium|Low):\s*([0-9.]+)\s*$", ln)
        if not m:
            break
        out[m.group(1)] = float(m.group(2))
    return out


class TestSeverityBandedConfidence:
    def test_constant_exists_with_expected_values(self):
        mod = _reload_plan_lib()
        assert mod.SEVERITY_BANDED_CONFIDENCE == _EXPECTED_BANDS

    def test_skill_constants_block_matches_code(self):
        # Drift guard: the prose <constants> block must equal the code dict, so
        # the "source of truth is hooks/plan_lib.py" claim cannot silently rot.
        mod = _reload_plan_lib()
        assert _parse_skill_banded_block() == mod.SEVERITY_BANDED_CONFIDENCE
