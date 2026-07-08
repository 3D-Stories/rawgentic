"""Tests for plan_lib.validate_index — the #314 delegated-read index validator.

An index is produced by a cheap analysis-role reader subagent and is a
HYPOTHESIS until validated (repo mistake #9: agents die and return vacuous
results). Fail-closed like validate_build_receipt: any structural problem,
verdict-shaped content, coverage miss, or fabricated quote rejects, and the
caller falls back to reading the artifact inline. Rejection is safe by
construction; acceptance is what must be earned.
"""
import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))


def _valid_index(**overrides):
    idx = {
        "surface": "step11-diff",
        "source_ref": "origin/main..abc123def456",
        "entries": [
            {"locator": "hooks/plan_lib.py:100-140", "risk_tag": "none",
             "one_line": "adds a pure validator beside validate_build_receipt"},
            {"locator": "tests/hooks/test_plan_lib_index.py", "risk_tag": "none",
             "one_line": "red-first tests for every reject branch"},
        ],
        "coverage": {
            "expected": ["hooks/plan_lib.py", "tests/hooks/test_plan_lib_index.py"],
            "indexed": ["hooks/plan_lib.py", "tests/hooks/test_plan_lib_index.py"],
        },
        "evidence": [],
        "truncated": False,
    }
    idx.update(overrides)
    return idx


EXPECTED = ["hooks/plan_lib.py", "tests/hooks/test_plan_lib_index.py"]


class TestValidateIndexHappyPath:
    def test_valid_step11_index_accepts(self):
        from plan_lib import validate_index
        ok, errors = validate_index(_valid_index(), EXPECTED)
        assert ok, errors
        assert errors == []

    def test_valid_step2_map_discovered_entries_accept(self):
        """step2-map drop-guard direction: entries may exceed the fed hint list —
        coverage.indexed tracks fed units examined, NOT discovered entries."""
        from plan_lib import validate_index
        idx = _valid_index(
            surface="step2-map",
            source_ref="abc123def456",
            entries=[
                {"locator": "hooks/plan_lib.py", "component": "validator",
                 "risk_tag": "none", "one_line": "fed component"},
                {"locator": "hooks/work_summary.py", "component": "telemetry",
                 "risk_tag": "none", "one_line": "DISCOVERED beyond the fed list"},
            ],
            coverage={"expected": ["validator"], "indexed": ["validator"]},
        )
        ok, errors = validate_index(idx, ["validator"])
        assert ok, errors

    def test_evidence_verified_against_artifact_text(self):
        from plan_lib import validate_index
        idx = _valid_index(evidence=[
            {"file": "hooks/plan_lib.py", "line": 3,
             "text": "a real line that exists in the artifact"},
        ])
        artifact = "first\nsecond\na real line that exists in the artifact\n"
        ok, errors = validate_index(idx, EXPECTED, artifact_text=artifact)
        assert ok, errors


class TestValidateIndexRejects:
    def _reject(self, idx, expected=None, artifact_text=None):
        from plan_lib import validate_index
        ok, errors = validate_index(idx, expected or EXPECTED,
                                    artifact_text=artifact_text)
        assert not ok
        assert errors
        return errors

    def test_non_dict_rejects(self):
        for bad in (None, [], "index", 42):
            self._reject(bad)

    def test_empty_entries_reject(self):
        """A vacuous return (dead reader) must never validate."""
        self._reject(_valid_index(entries=[]))

    def test_unknown_top_level_key_rejects(self):
        self._reject(_valid_index(verdict="ship it"))

    def test_unknown_entry_key_rejects(self):
        idx = _valid_index()
        idx["entries"][0]["severity"] = "Critical"
        self._reject(idx)

    def test_one_line_over_120_chars_rejects(self):
        idx = _valid_index()
        idx["entries"][0]["one_line"] = "x" * 121
        self._reject(idx)

    def test_patch_shaped_one_line_rejects(self):
        idx = _valid_index()
        idx["entries"][0]["one_line"] = "+    return True  # add this line"
        self._reject(idx)

    def test_patch_shaped_evidence_rejects(self):
        idx = _valid_index(evidence=[
            {"file": "hooks/plan_lib.py", "line": 1, "text": "-    old_code()"},
        ])
        self._reject(idx, artifact_text="-    old_code()\n")

    def test_coverage_dropped_unit_rejects(self):
        """AC5 core: the reader silently omitted a fed unit."""
        idx = _valid_index(coverage={
            "expected": EXPECTED, "indexed": ["hooks/plan_lib.py"]})
        self._reject(idx)

    def test_coverage_foreign_unit_rejects(self):
        idx = _valid_index(coverage={
            "expected": EXPECTED,
            "indexed": EXPECTED + ["something/invented.py"]})
        self._reject(idx)

    def test_expected_mismatch_with_fed_list_rejects(self):
        """coverage.expected must equal what the dispatcher actually fed."""
        self._reject(_valid_index(), expected=["a/totally/different.py"])

    def test_step11_locator_outside_expected_rejects(self):
        idx = _valid_index()
        idx["entries"][0]["locator"] = "not/in/diff.py:1-5"
        self._reject(idx)

    def test_fabricated_quote_rejects(self):
        """Anti-hallucination: evidence text absent from the artifact."""
        idx = _valid_index(evidence=[
            {"file": "hooks/plan_lib.py", "line": 3,
             "text": "this line was never in the artifact"},
        ])
        self._reject(idx, artifact_text="real line one\nreal line two\n")

    def test_truncated_rejects(self):
        """A partial index is never accepted for judgment surfaces."""
        self._reject(_valid_index(truncated=True))

    def test_unknown_surface_rejects(self):
        self._reject(_valid_index(surface="step99-nonsense"))

    def test_missing_required_key_rejects(self):
        idx = _valid_index()
        del idx["coverage"]
        self._reject(idx)

    def test_malformed_evidence_rejects(self):
        self._reject(_valid_index(evidence=[{"file": "x"}]))

    def test_verdict_prose_in_one_line_is_not_mechanically_rejected(self):
        """AC3 honesty pin: a terse prose verdict FITS the schema — the design
        explicitly concedes no mechanical check rejects semantics. The guard is
        the orchestrator's raw-bytes re-read contract (drift-guarded prose),
        not this validator. This test pins that the validator does NOT
        over-claim: schema-valid prose passes."""
        from plan_lib import validate_index
        idx = _valid_index()
        idx["entries"][0]["one_line"] = "removes the auth check — security regression, should fail the gate"
        ok, _ = validate_index(idx, EXPECTED)
        assert ok


class TestReadDelegateThresholds:
    def test_defaults(self):
        import plan_lib
        assert plan_lib.WF2_READ_DELEGATE_BYTES_DIFF == 65536
        assert plan_lib.WF2_READ_DELEGATE_BYTES_LOG == 32768

    def test_env_clamp_and_malformed(self):
        import subprocess
        code = ("import sys; sys.path.insert(0, sys.argv[1]); import plan_lib; "
                "print(plan_lib.WF2_READ_DELEGATE_BYTES_DIFF, "
                "plan_lib.WF2_READ_DELEGATE_BYTES_LOG)")
        def run(env_diff=None, env_log=None):
            import os
            env = dict(os.environ)
            if env_diff is not None:
                env["WF2_READ_DELEGATE_BYTES_DIFF"] = env_diff
            if env_log is not None:
                env["WF2_READ_DELEGATE_BYTES_LOG"] = env_log
            out = subprocess.run(
                [sys.executable, "-c", code, str(HOOKS_DIR)],
                capture_output=True, text=True, env=env, timeout=30)
            return out.stdout.split()
        assert run(env_diff="100")[0] == "4096"          # clamp low
        assert run(env_diff="999999999")[0] == "10485760"  # clamp high
        assert run(env_diff="banana")[0] == "65536"      # malformed → default
        assert run(env_log="8192")[1] == "8192"          # in-range honored
