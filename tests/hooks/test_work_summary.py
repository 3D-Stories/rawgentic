"""Tests for hooks/work_summary.py — the WF2 Step 16 completion summary + the
per-run structured run-record (the Tier-2 telemetry substrate).

Step 16 of implement-feature (WF2) used to hand-type a free-text completion
summary. This lib renders that SAME summary deterministically AND emits a
structured run-record (issue/type, changes, tests, per-gate findings caught vs
resolved, Step 11.5 security-scan status, loop-backs, PR/CI/deploy outcome) so
the effectiveness of the agentic workflow becomes measurable across runs — the
substrate the Tier-2 A/B harness aggregates (docs/measurements names it
explicitly). One run, one JSON line appended to the store.

Design mirrors hooks/security_scan.py: the logic is PURE functions
(validate_record, normalize_record, render_summary) exhaustively unit-tested
here, and the I/O (clock, store path, append) is injected/thin so the gate
behavior is deterministic in tests. Validation is fail-closed for the *store*
(a malformed record is never persisted) but the human summary still renders
best-effort, so the user never loses their Step 16 output to a telemetry nit.
"""
import json
import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

SUMMARY_CLI = HOOKS_DIR / "work_summary.py"
SKILLS_DIR = Path(__file__).resolve().parent.parent.parent / "skills"

NOW = "2026-06-15T18:00:00Z"


def _valid_record() -> dict:
    """A complete, schema-valid run-record (modeled on PR #91's own run). Each
    call returns a fresh dict so a test can mutate it freely."""
    return {
        "workflow": "implement-feature",
        "workflow_version": "2.33.0",
        "issue": {"number": 91, "type": "feature", "complexity": "standard"},
        "changes": {"files_changed": 4, "insertions": 896, "deletions": 7,
                    "commits": 3},
        "tests": {"added": 40, "passing": 895, "total": 895},
        "gates": [
            {"step": "4", "name": "Design Critique", "findings": 3,
             "resolved": 3, "status": "pass"},
            {"step": "6", "name": "Plan Drift", "findings": 0, "resolved": 0,
             "status": "pass"},
            {"step": "11", "name": "Code Review", "findings": 5, "resolved": 5,
             "status": "pass"},
        ],
        "security_scan": {"ran": True, "blocking_resolved": 0, "advisory": 2,
                          "skipped": ["iac"]},
        "loop_backs": {"used": 1, "budget": 3},
        "outcome": {"pr_number": 91,
                    "pr_url": "https://github.com/3D-Stories/rawgentic/pull/91",
                    "merged": True, "ci": "passed", "deploy": "not_applicable"},
        "follow_ups": ["wire work_summary into WF3 Step"],
    }


# --- validate_record: happy path -------------------------------------------

class TestValidateHappyPath:
    def test_full_valid_record_has_no_errors(self):
        from work_summary import validate_record
        assert validate_record(_valid_record()) == []

    @pytest.mark.parametrize("path,value", [
        (("issue", "number"), None),         # unknown/no issue -> null
        (("issue", "complexity"), None),     # complexity optional
        (("changes", "insertions"), None),   # diff volume may be unknown
        (("changes", "deletions"), None),
        (("tests", "passing"), None),        # tests may not have been counted
        (("tests", "total"), None),
        (("outcome", "pr_number"), None),
        (("outcome", "pr_url"), None),
        (("outcome", "merged"), None),
    ])
    def test_nullable_fields_accept_null(self, path, value):
        from work_summary import validate_record
        rec = _valid_record()
        rec[path[0]][path[1]] = value
        assert validate_record(rec) == [], f"{path} should accept null"

    def test_follow_ups_optional(self):
        from work_summary import validate_record
        rec = _valid_record()
        del rec["follow_ups"]
        assert validate_record(rec) == []

    def test_extra_optional_and_valid(self):
        """`extra` carries ordered workflow-specific labeled lines (e.g. WF3's
        Root Cause / Fix) — optional, defaults to absent."""
        from work_summary import validate_record
        rec = _valid_record()
        assert validate_record(rec) == []          # absent is fine
        rec["extra"] = [{"label": "Root Cause", "value": "off-by-one in retry"},
                        {"label": "Fix", "value": "clamp index"}]
        assert validate_record(rec) == []

    @pytest.mark.parametrize("lane", ["small-standard", "full"])
    def test_lane_field_optional_and_valid(self, lane):
        """`lane` (#135) marks whether a run took the small-standard lane. It is
        OPTIONAL — validate_record only checks keys it recognizes and does not
        reject unknown top-level keys, so an old record with no `lane` key stays
        valid (backward-compatible) and a record WITH `lane` also validates."""
        from work_summary import validate_record
        rec = _valid_record()
        assert validate_record(rec) == []           # absent (pre-#135 record) is fine
        rec["lane"] = lane
        assert validate_record(rec) == []


class TestValidateExtra:
    def test_extra_must_be_list(self):
        from work_summary import validate_record
        rec = _valid_record()
        rec["extra"] = {"label": "x", "value": "y"}   # object, not a list
        assert validate_record(rec) != []

    def test_extra_item_must_be_object(self):
        from work_summary import validate_record
        rec = _valid_record()
        rec["extra"] = ["Root Cause: foo"]            # bare string, not {label,value}
        assert validate_record(rec) != []

    @pytest.mark.parametrize("item", [
        {"value": "no label"},                        # missing label
        {"label": "", "value": "empty label"},        # empty label
        {"label": "ok"},                              # missing value
        {"label": "ok", "value": 5},                  # non-string value
        {"label": 5, "value": "x"},                   # non-string label
    ])
    def test_extra_item_field_violations(self, item):
        from work_summary import validate_record
        rec = _valid_record()
        rec["extra"] = [item]
        assert validate_record(rec) != []

    def test_empty_gates_list_is_valid(self):
        from work_summary import validate_record
        rec = _valid_record()
        rec["gates"] = []
        assert validate_record(rec) == []


class TestValidateReviewerKind:
    """`reviewer_kind` (#155, task 2) canonicalizes reviewer identity per #116's
    controlled-vocabulary contract. OPTIONAL per gate — absent is valid (keeps
    every legacy record valid); when present it must be a REVIEWER_KINDS member,
    fail-closed on free text or null (omit the key instead of nulling it)."""

    def test_gate_without_reviewer_kind_is_valid(self):
        from work_summary import validate_record
        rec = _valid_record()
        assert "reviewer_kind" not in rec["gates"][0]
        assert validate_record(rec) == []

    @pytest.mark.parametrize("kind", [
        "inline", "reflexion", "builtin_code_review", "codex", "hand_rolled_multi",
    ])
    def test_each_canonical_value_is_valid(self, kind):
        from work_summary import validate_record
        rec = _valid_record()
        rec["gates"][0]["reviewer_kind"] = kind
        assert validate_record(rec) == []

    def test_free_text_value_is_error(self):
        from work_summary import validate_record
        rec = _valid_record()
        rec["gates"][0]["reviewer_kind"] = "3-agent panel"
        errs = validate_record(rec)
        assert any("gates[0].reviewer_kind" in e for e in errs)

    def test_null_is_error(self):
        """null is NOT a stand-in for absent — the caller must omit the key."""
        from work_summary import validate_record
        rec = _valid_record()
        rec["gates"][0]["reviewer_kind"] = None
        errs = validate_record(rec)
        assert any("gates[0].reviewer_kind" in e for e in errs)

    @pytest.mark.parametrize("bad", [7, True, ["codex"]])
    def test_non_string_value_is_error(self, bad):
        from work_summary import validate_record
        rec = _valid_record()
        rec["gates"][0]["reviewer_kind"] = bad
        errs = validate_record(rec)
        assert any("gates[0].reviewer_kind" in e for e in errs)

    def test_mixed_gates_with_and_without_are_valid(self):
        from work_summary import validate_record
        rec = _valid_record()
        rec["gates"][0]["reviewer_kind"] = "codex"
        assert "reviewer_kind" not in rec["gates"][1]
        assert validate_record(rec) == []


# --- validate_record: fail-closed ------------------------------------------

class TestValidateFailClosed:
    def test_non_dict_record_is_error(self):
        from work_summary import validate_record
        assert validate_record(["not", "a", "dict"]) != []
        assert validate_record(None) != []

    @pytest.mark.parametrize("key", [
        "workflow", "workflow_version", "issue", "changes", "tests", "gates",
        "security_scan", "loop_backs", "outcome",
    ])
    def test_missing_required_top_key_is_error(self, key):
        from work_summary import validate_record
        rec = _valid_record()
        del rec[key]
        errs = validate_record(rec)
        assert any(key in e for e in errs), f"missing {key} not reported: {errs}"

    @pytest.mark.parametrize("section", ["issue", "changes", "tests",
                                         "security_scan", "loop_backs", "outcome"])
    def test_section_must_be_object(self, section):
        from work_summary import validate_record
        rec = _valid_record()
        rec[section] = "not-an-object"
        assert validate_record(rec) != []

    def test_gates_must_be_list(self):
        from work_summary import validate_record
        rec = _valid_record()
        rec["gates"] = {"not": "a list"}
        assert validate_record(rec) != []

    @pytest.mark.parametrize("bad", ["", None, 5])
    def test_workflow_must_be_nonempty_str(self, bad):
        from work_summary import validate_record
        rec = _valid_record()
        rec["workflow"] = bad
        assert validate_record(rec) != []

    @pytest.mark.parametrize("field,bad", [
        ("type", "epic"),         # not in {feature,bug,chore,other}
        ("type", None),           # type is required (issue.number is the nullable one)
        ("complexity", "epic"),   # not a known complexity
    ])
    def test_issue_enum_violations(self, field, bad):
        from work_summary import validate_record
        rec = _valid_record()
        rec["issue"][field] = bad
        assert validate_record(rec) != []

    @pytest.mark.parametrize("status", ["pass", "fail", "skipped", "fast_path"])
    def test_gate_status_enum_accepts_known(self, status):
        from work_summary import validate_record
        rec = _valid_record()
        rec["gates"][0]["status"] = status
        assert validate_record(rec) == []

    def test_gate_status_enum_rejects_unknown(self):
        from work_summary import validate_record
        rec = _valid_record()
        rec["gates"][0]["status"] = "kinda-passed"
        assert validate_record(rec) != []

    @pytest.mark.parametrize("ci", ["passed", "failed", "not_configured", "skipped"])
    def test_ci_enum_accepts_known(self, ci):
        from work_summary import validate_record
        rec = _valid_record()
        rec["outcome"]["ci"] = ci
        assert validate_record(rec) == []

    def test_ci_enum_rejects_unknown(self):
        from work_summary import validate_record
        rec = _valid_record()
        rec["outcome"]["ci"] = "green"
        assert validate_record(rec) != []

    @pytest.mark.parametrize("dep", ["success", "manual", "failed", "not_applicable"])
    def test_deploy_enum_accepts_known(self, dep):
        from work_summary import validate_record
        rec = _valid_record()
        rec["outcome"]["deploy"] = dep
        assert validate_record(rec) == []

    def test_deploy_enum_rejects_unknown(self):
        from work_summary import validate_record
        rec = _valid_record()
        rec["outcome"]["deploy"] = "shipped"
        assert validate_record(rec) != []


# --- validate_record: numeric integrity (the telemetry-quality heart) -------

class TestValidateNumericIntegrity:
    def test_bool_is_not_accepted_as_int(self):
        """bool is a subclass of int in Python — a naive isinstance(x, int)
        would let `findings: true` corrupt the substrate. Must be rejected."""
        from work_summary import validate_record
        rec = _valid_record()
        rec["changes"]["files_changed"] = True
        assert validate_record(rec) != []

    @pytest.mark.parametrize("path", [
        ("changes", "files_changed"), ("changes", "commits"),
        ("tests", "added"),
    ])
    def test_negative_counts_are_errors(self, path):
        from work_summary import validate_record
        rec = _valid_record()
        rec[path[0]][path[1]] = -1
        assert validate_record(rec) != []

    def test_gate_resolved_cannot_exceed_findings(self):
        from work_summary import validate_record
        rec = _valid_record()
        rec["gates"][0] = {"step": "4", "name": "x", "findings": 2,
                           "resolved": 5, "status": "pass"}
        assert validate_record(rec) != []

    def test_tests_passing_cannot_exceed_total(self):
        from work_summary import validate_record
        rec = _valid_record()
        rec["tests"] = {"added": 1, "passing": 10, "total": 5}
        assert validate_record(rec) != []

    def test_loop_backs_used_cannot_exceed_budget(self):
        from work_summary import validate_record
        rec = _valid_record()
        rec["loop_backs"] = {"used": 9, "budget": 3}
        assert validate_record(rec) != []

    @pytest.mark.parametrize("section,key", [
        ("issue", "number"), ("issue", "complexity"),
        ("changes", "insertions"), ("changes", "deletions"),
        ("tests", "passing"), ("tests", "total"),
        ("outcome", "pr_number"), ("outcome", "pr_url"), ("outcome", "merged"),
    ])
    def test_absent_nullable_subfield_is_error_not_treated_as_null(self, section, key):
        """A nullable field means `null` is an ALLOWED VALUE — not that the key
        may be ABSENT. Tolerating absence lets a producer silently drop a field,
        so Tier-2 can't distinguish a deliberate null from a dropped field. The
        key must be present (value may be null)."""
        from work_summary import validate_record
        rec = _valid_record()
        del rec[section][key]
        errs = validate_record(rec)
        assert any(key in e for e in errs), (
            f"absent {section}.{key} should be an error, got {errs}")
        # and present-with-null stays valid (regression guard)
        rec[section][key] = None
        assert validate_record(rec) == []

    def test_security_skipped_must_be_list_of_str(self):
        from work_summary import validate_record
        rec = _valid_record()
        rec["security_scan"]["skipped"] = "iac"   # bare string, not a list
        assert validate_record(rec) != []
        rec["security_scan"]["skipped"] = [1, 2]   # list of non-str
        assert validate_record(rec) != []

    def test_security_ran_must_be_bool(self):
        from work_summary import validate_record
        rec = _valid_record()
        rec["security_scan"]["ran"] = "yes"
        assert validate_record(rec) != []

    @pytest.mark.parametrize("sec", [
        {"ran": False, "blocking_resolved": 3, "advisory": 0, "skipped": []},
        {"ran": False, "blocking_resolved": 0, "advisory": 2, "skipped": []},
        {"ran": False, "blocking_resolved": 0, "advisory": 0, "skipped": ["x"]},
    ])
    def test_security_not_run_must_be_all_zero(self, sec):
        """A scan that did not run (ran=false) cannot have resolved findings or
        skipped scanners — the render would hide them ('not run') AND the
        telemetry would be self-contradictory. The clean not-run shape is valid."""
        from work_summary import validate_record
        rec = _valid_record()
        rec["security_scan"] = sec
        assert validate_record(rec) != []
        rec["security_scan"] = {"ran": False, "blocking_resolved": 0,
                                "advisory": 0, "skipped": []}
        assert validate_record(rec) == []

    def test_duplicate_gate_step_is_error(self):
        """Two gates sharing a step id would conflate in Tier-2 gate aggregation
        (the WF3 footgun: a 'Memorize' row reusing Code Review's step 9)."""
        from work_summary import validate_record
        rec = _valid_record()
        rec["gates"] = [
            {"step": "9", "name": "Code Review", "findings": 0, "resolved": 0, "status": "pass"},
            {"step": "9", "name": "Memorize", "findings": 0, "resolved": 0, "status": "pass"},
        ]
        assert validate_record(rec) != []

    def test_gate_missing_subfield_is_error(self):
        from work_summary import validate_record
        rec = _valid_record()
        rec["gates"][0] = {"step": "4", "name": "x"}  # no findings/resolved/status
        assert validate_record(rec) != []

    def test_gate_item_must_be_object(self):
        from work_summary import validate_record
        rec = _valid_record()
        rec["gates"] = ["not-an-object"]
        assert validate_record(rec) != []


# --- normalize_record ------------------------------------------------------

class TestNormalize:
    def test_stamps_schema_version_and_generated_at(self):
        from work_summary import normalize_record, SCHEMA_VERSION
        out = normalize_record(_valid_record(), now=NOW)
        assert out["schema_version"] == SCHEMA_VERSION
        assert out["generated_at"] == NOW

    def test_does_not_mutate_input(self):
        from work_summary import normalize_record
        rec = _valid_record()
        normalize_record(rec, now=NOW)
        assert "schema_version" not in rec
        assert "generated_at" not in rec

    def test_fills_follow_ups_default(self):
        from work_summary import normalize_record
        rec = _valid_record()
        del rec["follow_ups"]
        out = normalize_record(rec, now=NOW)
        assert out["follow_ups"] == []

    def test_preserves_all_provided_fields(self):
        from work_summary import normalize_record
        out = normalize_record(_valid_record(), now=NOW)
        assert out["issue"]["number"] == 91
        assert out["outcome"]["ci"] == "passed"
        assert len(out["gates"]) == 3

    def test_explicit_schema_version_override(self):
        from work_summary import normalize_record
        out = normalize_record(_valid_record(), now=NOW, schema_version=99)
        assert out["schema_version"] == 99

    def test_fills_extra_default(self):
        from work_summary import normalize_record
        out = normalize_record(_valid_record(), now=NOW)
        assert out["extra"] == []


# --- render_summary --------------------------------------------------------

class TestRenderSummary:
    def test_contains_wf2_header_for_implement_feature(self):
        from work_summary import render_summary
        text = render_summary(_valid_record())
        assert "WF2 COMPLETE" in text

    def test_contains_pr_issue_and_outcome(self):
        from work_summary import render_summary
        text = render_summary(_valid_record())
        assert "pull/91" in text
        assert "#91" in text
        assert "passed" in text                  # ci
        assert "not_applicable" in text          # deploy

    def test_lists_each_gate(self):
        from work_summary import render_summary
        text = render_summary(_valid_record())
        assert "Design Critique" in text
        assert "Code Review" in text

    def test_security_scan_line_present(self):
        from work_summary import render_summary
        text = render_summary(_valid_record())
        assert "Security Scan" in text or "11.5" in text
        assert "iac" in text                     # the skipped kind is surfaced

    def test_security_skipped_none_when_empty(self):
        from work_summary import render_summary
        rec = _valid_record()
        rec["security_scan"]["skipped"] = []
        text = render_summary(rec)
        assert "none" in text.lower()

    def test_security_not_run_renders_cleanly(self):
        """A workflow with no security scan (e.g. WF3) sets ran=false; the render
        must not reference a Step 11.5 that didn't happen — it says 'not run'."""
        from work_summary import render_summary
        rec = _valid_record()
        rec["workflow"] = "fix-bug"
        rec["security_scan"] = {"ran": False, "blocking_resolved": 0,
                                "advisory": 0, "skipped": []}
        text = render_summary(rec)
        assert "WF3 COMPLETE" in text
        assert "not run" in text.lower()

    def test_extra_lines_rendered(self):
        from work_summary import render_summary
        rec = _valid_record()
        rec["extra"] = [{"label": "Root Cause", "value": "stale cache key"},
                        {"label": "Fix", "value": "include tenant in key"}]
        text = render_summary(rec)
        assert "Root Cause: stale cache key" in text
        assert "Fix: include tenant in key" in text

    def test_extra_absent_renders_without_extra_lines(self):
        from work_summary import render_summary
        text = render_summary(_valid_record())
        assert "Root Cause" not in text

    def test_extra_nonstring_fields_skipped_in_render(self):
        """Best-effort render runs on UNVALIDATED records: a malformed extra item
        (null label / dict value) must not leak 'None:' or a dict repr."""
        from work_summary import render_summary
        rec = {"workflow": "fix-bug",
               "extra": [{"label": None, "value": {"x": 1}},
                         {"label": "Fix", "value": "ok"}]}
        text = render_summary(rec)
        assert "Fix: ok" in text
        assert "None:" not in text
        assert "{'x'" not in text

    def test_loop_backs_rendered(self):
        from work_summary import render_summary
        text = render_summary(_valid_record())
        assert "1 / 3" in text or "1/3" in text

    def test_follow_ups_rendered(self):
        from work_summary import render_summary
        text = render_summary(_valid_record())
        assert "wire work_summary into WF3 Step" in text

    def test_renders_best_effort_on_partial_record(self):
        """The human summary must never crash on a record that fails validation
        (missing optionals / nulls) — the user always gets their Step 16 output."""
        from work_summary import render_summary
        partial = {"workflow": "implement-feature",
                   "issue": {"number": None, "type": "bug"},
                   "outcome": {"pr_url": None}}
        text = render_summary(partial)        # must not raise
        assert isinstance(text, str) and text

    def test_renders_on_empty_dict(self):
        from work_summary import render_summary
        assert isinstance(render_summary({}), str)

    @pytest.mark.parametrize("bad", [
        {"gates": 5},                 # non-iterable where a list is expected
        {"gates": "abc"},             # string is iterable but items aren't dicts
        {"issue": "x"},               # str where an object is expected
        {"outcome": [1, 2]},          # list where an object is expected
        {"tests": 5},
        {"security_scan": "nope"},
        {"loop_backs": 9},
        {"follow_ups": 7},            # non-iterable follow_ups
        {"follow_ups": "oneitem"},    # would iterate characters if not guarded
    ])
    def test_never_raises_on_wrong_typed_sections(self, bad):
        """render_summary is called on UNVALIDATED records in main's rc=1 path,
        so a wrong-typed section must degrade to a default, never raise — else a
        malformed record crashes the tool instead of rendering + exiting 1."""
        from work_summary import render_summary
        text = render_summary(bad)
        assert isinstance(text, str) and text

    @pytest.mark.parametrize("nondict", [
        [],                    # a record file holding a JSON array
        ["a", "b"],
        "not-a-record",
        42,
        None,
    ])
    def test_never_raises_on_nondict_record(self, nondict):
        """#261: the docstring promises "never raises on a ... non-dict record",
        but the verification_deferred read went through the RAW param instead of
        the _as_dict-coerced copy, so a list/str/int record raised AttributeError."""
        from work_summary import render_summary
        text = render_summary(nondict)
        assert isinstance(text, str) and text


# --- worker_token_share (#315) ----------------------------------------------

class TestWorkerTokenShare:
    """Derived worker-token-share from usage.model_mix (#315, CMA cookbook)."""

    MIX = {"claude-fable-5": {"input_tokens": 55_000_000, "output_tokens": 260_000},
           "claude-opus-4-8": {"input_tokens": 8_800_000, "output_tokens": 11_000},
           "claude-sonnet-5": {"input_tokens": 36_200_000, "output_tokens": 9_000}}

    def test_normal_split(self):
        from work_summary import worker_token_share
        share = worker_token_share(self.MIX, ["opus", "sonnet"])
        assert share == pytest.approx(45_000_000 / 100_000_000)

    def test_single_model_orchestrator_only_is_zero(self):
        from work_summary import worker_token_share
        mix = {"claude-fable-5": {"input_tokens": 100, "output_tokens": 1}}
        assert worker_token_share(mix, ["opus", "sonnet"]) == 0.0

    def test_malformed_mix_returns_none_never_raises(self):
        from work_summary import worker_token_share
        assert worker_token_share(None, ["opus"]) is None
        assert worker_token_share("nope", ["opus"]) is None
        assert worker_token_share({}, ["opus"]) is None
        assert worker_token_share({"m": "bad"}, ["opus"]) is None
        assert worker_token_share({"m": {"input_tokens": "x"}}, ["opus"]) is None

    def test_zero_total_and_no_workers_config(self):
        from work_summary import worker_token_share
        assert worker_token_share({"m": {"input_tokens": 0}}, ["opus"]) is None
        assert worker_token_share(self.MIX, []) is None
        assert worker_token_share(self.MIX, None) is None

    def test_matching_is_case_insensitive_substring(self):
        from work_summary import worker_token_share
        mix = {"CLAUDE-OPUS-4-8": {"input_tokens": 30, "output_tokens": 1},
               "claude-fable-5": {"input_tokens": 70, "output_tokens": 1}}
        assert worker_token_share(mix, ["Opus"]) == pytest.approx(0.3)

    def test_render_line_includes_share_when_workers_known(self):
        from work_summary import render_summary
        rec = _valid_record()
        rec["usage"] = {"input_tokens": 100, "output_tokens": 2,
                        "cost_estimate_usd": None, "wall_clock_s": None,
                        "model_mix": self.MIX, "capture_status": "captured"}
        text = render_summary(rec, worker_models=["opus", "sonnet"])
        assert "worker-share 45%" in text

    def test_render_line_omits_share_without_worker_models(self):
        from work_summary import render_summary
        rec = _valid_record()
        rec["usage"] = {"input_tokens": 100, "output_tokens": 2,
                        "cost_estimate_usd": None, "wall_clock_s": None,
                        "model_mix": self.MIX, "capture_status": "captured"}
        assert "worker-share" not in render_summary(rec)


class TestWorkerShareCLI:
    """summarize resolves modelRouting from the workspace file, fail-open (#315)."""

    def _project(self, tmp_path, routing=True):
        ws_root = tmp_path / "ws"
        proj = ws_root / "projects" / "myproj"
        proj.mkdir(parents=True)
        entry = {"name": "myproj", "path": "./projects/myproj", "active": True}
        if routing:
            entry["modelRouting"] = {"review": "opus", "analysis": "sonnet",
                                     "implementation": "opus"}
        (ws_root / ".rawgentic_workspace.json").write_text(
            json.dumps({"version": 1, "projects": [entry]}))
        return proj

    def _record_with_mix(self):
        rec = _valid_record()
        rec["usage"] = {"input_tokens": 100_000_000, "output_tokens": 2,
                        "cost_estimate_usd": None, "wall_clock_s": None,
                        "model_mix": TestWorkerTokenShare.MIX,
                        "capture_status": "captured"}
        return rec

    def test_share_rendered_and_injected_with_workspace_routing(self, tmp_path, capsys):
        from work_summary import main
        proj = self._project(tmp_path)
        store = proj / "store.jsonl"
        rc = main(["summarize", "--record-file",
                   _write_record(tmp_path, self._record_with_mix()),
                   "--project-root", str(proj), "--store", str(store)])
        out = capsys.readouterr().out
        assert rc == 0
        assert "worker-share 45%" in out
        stored = json.loads(store.read_text().splitlines()[0])
        assert stored["usage"]["worker_token_share"] == pytest.approx(0.45)

    def test_no_workspace_omits_share_rc0(self, tmp_path, capsys):
        from work_summary import main
        store = tmp_path / "store.jsonl"
        rc = main(["summarize", "--record-file",
                   _write_record(tmp_path, self._record_with_mix()),
                   "--project-root", str(tmp_path), "--store", str(store)])
        out = capsys.readouterr().out
        assert rc == 0
        assert "worker-share" not in out
        stored = json.loads(store.read_text().splitlines()[0])
        assert "worker_token_share" not in stored["usage"]

    def test_record_without_field_still_validates(self, tmp_path):
        """AC5: no new required key — a record with no share field passes strict."""
        from work_summary import validate_record
        assert validate_record(self._record_with_mix(), strict=True) == []


# --- resolve_store_path ----------------------------------------------------

class TestResolveStorePath:
    def test_flag_wins(self, tmp_path):
        from work_summary import resolve_store_path
        p = resolve_store_path("/explicit/store.jsonl", env={}, project_root=str(tmp_path))
        assert p == "/explicit/store.jsonl"

    def test_env_used_when_no_flag(self, tmp_path):
        from work_summary import resolve_store_path
        env = {"RAWGENTIC_RUN_RECORD_STORE": "/from/env.jsonl"}
        p = resolve_store_path(None, env=env, project_root=str(tmp_path))
        assert p == "/from/env.jsonl"

    def test_flag_beats_env(self, tmp_path):
        from work_summary import resolve_store_path
        env = {"RAWGENTIC_RUN_RECORD_STORE": "/from/env.jsonl"}
        p = resolve_store_path("/flag.jsonl", env=env, project_root=str(tmp_path))
        assert p == "/flag.jsonl"

    def test_default_is_project_measurements(self, tmp_path):
        from work_summary import resolve_store_path
        p = resolve_store_path(None, env={}, project_root=str(tmp_path))
        assert p == str(tmp_path / "docs" / "measurements" / "run_records.jsonl")


# --- load_record_file ------------------------------------------------------

class TestLoadRecordFile:
    def test_reads_valid_json(self, tmp_path):
        from work_summary import load_record_file
        f = tmp_path / "rec.json"
        f.write_text(json.dumps(_valid_record()))
        assert load_record_file(str(f))["issue"]["number"] == 91

    def test_missing_file_raises(self, tmp_path):
        from work_summary import load_record_file, WorkSummaryError
        with pytest.raises(WorkSummaryError):
            load_record_file(str(tmp_path / "nope.json"))

    def test_malformed_json_raises(self, tmp_path):
        from work_summary import load_record_file, WorkSummaryError
        f = tmp_path / "bad.json"
        f.write_text("{not valid json")
        with pytest.raises(WorkSummaryError):
            load_record_file(str(f))


# --- persist_record --------------------------------------------------------

class TestPersistRecord:
    def test_appends_single_json_line(self, tmp_path):
        from work_summary import persist_record
        store = tmp_path / "sub" / "run_records.jsonl"   # parent missing on purpose
        persist_record({"a": 1}, str(store))
        lines = store.read_text().splitlines()
        assert len(lines) == 1
        assert json.loads(lines[0]) == {"a": 1}

    def test_appends_without_overwriting(self, tmp_path):
        from work_summary import persist_record
        store = tmp_path / "run_records.jsonl"
        persist_record({"n": 1}, str(store))
        persist_record({"n": 2}, str(store))
        lines = store.read_text().splitlines()
        assert [json.loads(l)["n"] for l in lines] == [1, 2]

    def test_each_line_is_independent_json(self, tmp_path):
        from work_summary import persist_record
        store = tmp_path / "run_records.jsonl"
        persist_record(_valid_record(), str(store))
        persist_record(_valid_record(), str(store))
        for line in store.read_text().splitlines():
            json.loads(line)   # must not raise


# --- main (CLI) ------------------------------------------------------------

def _write_record(tmp_path, record, name="rec.json"):
    f = tmp_path / name
    f.write_text(json.dumps(record))
    return str(f)


class TestMainCLI:
    def test_valid_record_persists_and_renders(self, tmp_path, capsys):
        from work_summary import main
        store = tmp_path / "store.jsonl"
        rc = main(["summarize", "--record-file", _write_record(tmp_path, _valid_record()),
                   "--project-root", str(tmp_path), "--store", str(store)])
        out = capsys.readouterr().out
        assert rc == 0
        assert "WF2 COMPLETE" in out
        assert store.exists()
        stored = json.loads(store.read_text().splitlines()[0])
        assert stored["schema_version"] >= 1
        assert "generated_at" in stored

    def test_json_flag_emits_normalized_record(self, tmp_path, capsys):
        from work_summary import main
        store = tmp_path / "store.jsonl"
        rc = main(["summarize", "--record-file", _write_record(tmp_path, _valid_record()),
                   "--project-root", str(tmp_path), "--store", str(store), "--json"])
        out = capsys.readouterr().out
        assert rc == 0
        emitted = json.loads(out)             # stdout is parseable JSON
        assert emitted["schema_version"] >= 1
        assert emitted["issue"]["number"] == 91

    def test_no_persist_skips_store(self, tmp_path, capsys):
        from work_summary import main
        store = tmp_path / "store.jsonl"
        rc = main(["summarize", "--record-file", _write_record(tmp_path, _valid_record()),
                   "--project-root", str(tmp_path), "--store", str(store), "--no-persist"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "WF2 COMPLETE" in out
        assert not store.exists()

    def test_invalid_record_renders_skips_persist_exits_1(self, tmp_path, capsys):
        """The chosen failure mode: render the summary (user keeps Step 16),
        do NOT persist (store stays clean), exit 1 (skill surfaces the gap)."""
        from work_summary import main
        store = tmp_path / "store.jsonl"
        bad = _valid_record()
        bad["outcome"]["ci"] = "green"          # invalid enum
        rc = main(["summarize", "--record-file", _write_record(tmp_path, bad),
                   "--project-root", str(tmp_path), "--store", str(store)])
        captured = capsys.readouterr()
        assert rc == 1
        assert "WF2 COMPLETE" in captured.out   # rendered anyway
        assert captured.err                      # errors surfaced to stderr
        assert not store.exists()                # NOT persisted

    def test_wrong_typed_section_still_renders_and_exits_1(self, tmp_path, capsys):
        """A record whose section has the wrong TYPE (not just a bad value) must
        not crash main — it still renders best-effort, skips persist, exits 1."""
        from work_summary import main
        store = tmp_path / "store.jsonl"
        bad = _valid_record()
        bad["gates"] = 5                         # non-iterable; would crash a naive render
        rc = main(["summarize", "--record-file", _write_record(tmp_path, bad),
                   "--project-root", str(tmp_path), "--store", str(store)])
        captured = capsys.readouterr()
        assert rc == 1
        assert "COMPLETE" in captured.out
        assert not store.exists()

    def test_default_store_is_project_measurements(self, tmp_path, capsys):
        from work_summary import main
        rc = main(["summarize", "--record-file", _write_record(tmp_path, _valid_record()),
                   "--project-root", str(tmp_path)])
        assert rc == 0
        default = tmp_path / "docs" / "measurements" / "run_records.jsonl"
        assert default.exists()

    def test_missing_record_file_errors(self, tmp_path, capsys):
        from work_summary import main
        rc = main(["summarize", "--record-file", str(tmp_path / "nope.json"),
                   "--project-root", str(tmp_path)])
        assert rc != 0

    def test_no_subcommand_is_usage_error(self):
        from work_summary import main
        with pytest.raises(SystemExit) as exc:
            main([])
        assert exc.value.code == 2


# --- CLI subprocess smoke (the skill shells out to this exact invocation) ----

class TestCLISubprocessSmoke:
    def test_real_invocation_renders_and_persists(self, tmp_path):
        import subprocess
        rec_file = _write_record(tmp_path, _valid_record())
        store = tmp_path / "store.jsonl"
        r = subprocess.run(
            ["python3", str(SUMMARY_CLI), "summarize", "--record-file", rec_file,
             "--project-root", str(tmp_path), "--store", str(store)],
            capture_output=True, text=True, timeout=15)
        assert r.returncode == 0, r.stderr
        assert "WF2 COMPLETE" in r.stdout
        assert store.exists()


# --- Step 16 skill-wiring drift guard --------------------------------------

class TestWorkSummarySkillWiring:
    """Drift guard: every workflow with a completion step must drive its summary
    through this CLI, not hand-type the block (the inconsistency the run-record
    removes). If the subcommand is renamed, update the skills + this guard."""

    @pytest.mark.parametrize("skill", ["implement-feature", "fix-bug"])
    def test_skill_invokes_work_summary_cli(self, skill):
        # #159: WF3's completion step (Step 14) detail moved to references/steps.md
        # in the spine split, so this content pin reads the CORPUS (SKILL.md +
        # references/) rather than SKILL.md alone. WF2's spine-location pin for the
        # Step 16 stub lives in tests/test_wf2_clarity.py, unaffected by this.
        from tests.corpus import skill_corpus
        content = skill_corpus(skill)
        assert "work_summary.py summarize" in content, (
            f"{skill}/SKILL.md completion step must invoke "
            f"`work_summary.py summarize`; if you renamed it, update this guard."
        )

    def test_wf2_completion_step_wires_usage_capture(self):
        # #189: the whole feature exists to stop new run-records shipping null usage.
        # Live capture is a manual orchestrator instruction in the Step 16 detail — if
        # a future spine edit drops it, new runs silently regress to the #155
        # null-forever state with nothing failing. Pin the capture call to the corpus
        # so that regression goes red here. (WF2 only — WF3 wiring is a later follow-up.)
        from tests.corpus import skill_corpus
        content = skill_corpus("implement-feature")
        assert "usage_capture.py capture" in content, (
            "implement-feature Step 16 must invoke `usage_capture.py capture` to "
            "populate run-record usage; if you renamed it, update this guard."
        )


# ===========================================================================
# aggregate subcommand (#94) — Tier-2 run-record rollups
# ===========================================================================
import subprocess as _subprocess


def _store_rec(**ov):
    """A stored (normalized) run-record: a schema-valid core plus the
    generated_at/schema_version the writer stamps and the aggregate reader
    requires. Override whole sections by keyword (gates=..., outcome=..., etc.)."""
    r = _valid_record()
    r["schema_version"] = 1
    r["generated_at"] = "2026-06-15T18:00:00Z"
    for k, v in ov.items():
        r[k] = v
    return r


def _write_store(path, records):
    path.write_text("".join(json.dumps(r) + "\n" for r in records))
    return str(path)


class TestLoadStore:
    def test_reads_valid_lines(self, tmp_path):
        from work_summary import load_store
        p = _write_store(tmp_path / "s.jsonl", [_store_rec(), _store_rec()])
        recs, excl = load_store(p)
        assert len(recs) == 2 and excl == []

    def test_blank_lines_skipped_not_excluded(self, tmp_path):
        from work_summary import load_store
        p = tmp_path / "s.jsonl"
        p.write_text(json.dumps(_store_rec()) + "\n\n   \n" + json.dumps(_store_rec()) + "\n")
        recs, excl = load_store(str(p))
        assert len(recs) == 2 and excl == []

    def test_corrupt_json_line_excluded_with_lineno(self, tmp_path):
        from work_summary import load_store
        p = tmp_path / "s.jsonl"
        p.write_text(json.dumps(_store_rec()) + "\n{not json\n" + json.dumps(_store_rec()) + "\n")
        recs, excl = load_store(str(p))
        assert len(recs) == 2
        assert len(excl) == 1 and "line 2" in excl[0]

    @pytest.mark.parametrize("bad", ["42", "[]", '"x"', "null", "true"])
    def test_non_dict_json_value_excluded(self, tmp_path, bad):
        from work_summary import load_store
        p = tmp_path / "s.jsonl"
        p.write_text(bad + "\n" + json.dumps(_store_rec()) + "\n")
        recs, excl = load_store(str(p))
        assert len(recs) == 1 and len(excl) == 1 and "line 1" in excl[0]

    def test_schema_invalid_line_excluded(self, tmp_path):
        from work_summary import load_store
        bad = _store_rec()
        del bad["outcome"]
        p = _write_store(tmp_path / "s.jsonl", [bad, _store_rec()])
        recs, excl = load_store(p)
        assert len(recs) == 1 and len(excl) == 1 and "line 1" in excl[0]

    def test_missing_generated_at_excluded(self, tmp_path):
        from work_summary import load_store
        bad = _store_rec()
        del bad["generated_at"]
        p = _write_store(tmp_path / "s.jsonl", [bad])
        recs, excl = load_store(p)
        assert recs == [] and len(excl) == 1 and "generated_at" in excl[0]

    def test_non_iso_generated_at_excluded(self, tmp_path):
        from work_summary import load_store
        p = _write_store(tmp_path / "s.jsonl", [_store_rec(generated_at="not-a-date")])
        recs, excl = load_store(p)
        assert recs == [] and len(excl) == 1

    def test_empty_file_no_records_no_error(self, tmp_path):
        from work_summary import load_store
        p = tmp_path / "s.jsonl"
        p.write_text("")
        assert load_store(str(p)) == ([], [])

    def test_missing_file_raises(self, tmp_path):
        from work_summary import load_store, WorkSummaryError
        with pytest.raises(WorkSummaryError):
            load_store(str(tmp_path / "nope.jsonl"))

    def test_nul_in_path_raises(self):
        from work_summary import load_store, WorkSummaryError
        with pytest.raises(WorkSummaryError):
            load_store("/tmp/a\x00b.jsonl")


class TestFilterSince:
    def test_none_returns_all(self):
        from work_summary import filter_since
        recs = [_store_rec(generated_at="2026-01-01T00:00:00Z")]
        assert filter_since(recs, None) == recs

    def test_boundary_excludes_prior_day(self):
        from work_summary import filter_since
        a = _store_rec(generated_at="2026-06-16T23:59:59Z")
        b = _store_rec(generated_at="2026-06-17T12:00:00Z")
        assert filter_since([a, b], "2026-06-17") == [b]

    def test_same_day_full_timestamp_kept(self):
        from work_summary import filter_since
        r = _store_rec(generated_at="2026-06-17T00:00:01Z")
        assert filter_since([r], "2026-06-17") == [r]


class TestAggregateGates:
    def test_name_drift_merges_on_step(self):
        from work_summary import aggregate_records
        r1 = _store_rec(); r1["gates"] = [{"step": "4", "name": "Design Critique",
                                           "findings": 2, "resolved": 2, "status": "pass"}]
        r2 = _store_rec(); r2["gates"] = [{"step": "4", "name": "design critique (3-judge + codex)",
                                           "findings": 0, "resolved": 0, "status": "pass"}]
        g = aggregate_records([r1, r2])["gates"]
        assert set(g) == {"4"}
        assert g["4"]["runs_present"] == 2
        assert sorted(g["4"]["names"]) == sorted(
            ["Design Critique", "design critique (3-judge + codex)"])
        assert g["4"]["hit_rate"] == 0.5
        assert g["4"]["total_findings"] == 2 and g["4"]["total_resolved"] == 2
        assert g["4"]["resolution_rate"] == 1.0
        assert g["4"]["mean_findings_per_run"] == 1.0

    def test_resolution_rate_div0_is_none(self):
        from work_summary import aggregate_records
        r = _store_rec(); r["gates"] = [{"step": "6", "name": "Plan Drift",
                                         "findings": 0, "resolved": 0, "status": "pass"}]
        g = aggregate_records([r])["gates"]["6"]
        assert g["resolution_rate"] is None and g["hit_rate"] == 0.0

    def test_absent_gate_is_not_a_miss(self):
        from work_summary import aggregate_records
        r1 = _store_rec(); r1["gates"] = [{"step": "11", "name": "Code Review",
                                           "findings": 1, "resolved": 1, "status": "pass"}]
        r2 = _store_rec(); r2["gates"] = []
        g = aggregate_records([r1, r2])["gates"]["11"]
        assert g["runs_present"] == 1 and g["hit_rate"] == 1.0


class TestAggregateLoopBacks:
    def test_mean_and_cap_rate(self):
        from work_summary import aggregate_records
        recs = [_store_rec(loop_backs={"used": 3, "budget": 3}),
                _store_rec(loop_backs={"used": 1, "budget": 3})]
        lb = aggregate_records(recs)["loop_backs"]
        assert lb["mean_used"] == 2.0
        assert lb["pct_hit_cap"] == 0.5 and lb["cap_runs_considered"] == 2

    def test_budget_zero_excluded_from_cap(self):
        from work_summary import aggregate_records
        recs = [_store_rec(loop_backs={"used": 0, "budget": 0}),
                _store_rec(loop_backs={"used": 2, "budget": 2})]
        lb = aggregate_records(recs)["loop_backs"]
        assert lb["cap_runs_considered"] == 1 and lb["pct_hit_cap"] == 1.0


class TestAggregateOutcomes:
    def _o(self, **kw):
        r = _store_rec()
        r["outcome"] = {**r["outcome"], **kw}
        return r

    def test_ci_pass_rate_excludes_non_configured(self):
        from work_summary import aggregate_records
        recs = [self._o(ci="passed"), self._o(ci="failed"),
                self._o(ci="not_configured"), self._o(ci="skipped")]
        out = aggregate_records(recs)["outcomes"]
        assert out["ci_runs_considered"] == 2 and out["ci_pass_rate"] == 0.5

    def test_merge_rate_excludes_null(self):
        from work_summary import aggregate_records
        recs = [self._o(merged=True), self._o(merged=False), self._o(merged=None)]
        out = aggregate_records(recs)["outcomes"]
        assert out["merge_runs_considered"] == 2 and out["merge_rate"] == 0.5

    def test_deploy_excludes_not_applicable(self):
        from work_summary import aggregate_records
        recs = [self._o(deploy="success"), self._o(deploy="failed"),
                self._o(deploy="not_applicable")]
        out = aggregate_records(recs)["outcomes"]
        assert out["deploy_runs_considered"] == 2 and out["deploy_success_rate"] == 0.5

    def test_security_blocked_and_skips_over_ran_true(self):
        from work_summary import aggregate_records
        r1 = _store_rec(security_scan={"ran": True, "blocking_resolved": 1,
                                       "advisory": 0, "skipped": ["iac"]})
        r2 = _store_rec(security_scan={"ran": True, "blocking_resolved": 0,
                                       "advisory": 0, "skipped": ["iac", "sca"]})
        r3 = _store_rec(security_scan={"ran": False, "blocking_resolved": 0,
                                       "advisory": 0, "skipped": []})
        out = aggregate_records([r1, r2, r3])["outcomes"]
        assert out["security_runs_considered"] == 2
        assert out["security_blocked_rate"] == 0.5
        assert out["scanner_skip_freq"] == {"iac": 2, "sca": 1}

    def test_zero_denominator_rates_are_none(self):
        from work_summary import aggregate_records
        r = self._o(ci="not_configured", merged=None, deploy="not_applicable")
        r["security_scan"] = {"ran": False, "blocking_resolved": 0,
                              "advisory": 0, "skipped": []}
        out = aggregate_records([r])["outcomes"]
        assert out["ci_pass_rate"] is None and out["merge_rate"] is None
        assert out["deploy_success_rate"] is None and out["security_blocked_rate"] is None


class TestAggregateEffort:
    def test_means_and_null_exclusion(self):
        from work_summary import aggregate_records
        r1 = _store_rec(changes={"files_changed": 4, "insertions": 100,
                                 "deletions": 10, "commits": 2})
        r2 = _store_rec(changes={"files_changed": 2, "insertions": None,
                                 "deletions": None, "commits": 4})
        e = aggregate_records([r1, r2])["effort"]
        assert e["mean_files_changed"] == 3.0 and e["mean_commits"] == 3.0
        assert e["mean_insertions"] == 100.0

    def test_all_null_insertions_is_none(self):
        from work_summary import aggregate_records
        r = _store_rec(changes={"files_changed": 1, "insertions": None,
                                "deletions": None, "commits": 1})
        assert aggregate_records([r])["effort"]["mean_insertions"] is None


class TestAggregateGrouped:
    def test_group_by_version(self):
        from work_summary import aggregate_grouped
        a = _store_rec(workflow_version="2.40.0")
        b = _store_rec(workflow_version="2.41.0")
        c = _store_rec(workflow_version="2.41.0")
        g = aggregate_grouped([a, b, c], "version")
        assert set(g) == {"2.40.0", "2.41.0"}
        assert g["2.41.0"]["n"] == 2 and g["2.40.0"]["n"] == 1

    def test_group_by_complexity_none_bucket(self):
        from work_summary import aggregate_grouped
        a = _store_rec()
        a["issue"] = {**a["issue"], "complexity": None}
        g = aggregate_grouped([a], "complexity")
        assert "(none)" in g

    @pytest.mark.parametrize("dim", ["workflow", "version", "type", "complexity"])
    def test_all_dims_supported(self, dim):
        from work_summary import aggregate_grouped
        assert aggregate_grouped([_store_rec()], dim)


class TestAggregateEdge:
    def test_empty_records_no_crash(self):
        from work_summary import aggregate_records
        a = aggregate_records([])
        assert a["n"] == 0 and a["gates"] == {}
        assert a["loop_backs"]["mean_used"] is None
        assert a["outcomes"]["ci_pass_rate"] is None
        assert a["effort"]["mean_files_changed"] is None

    def test_unknown_fields_ignored(self):
        from work_summary import aggregate_records
        r = _store_rec(); r["future_field"] = "x"
        assert aggregate_records([r])["n"] == 1

    def test_render_has_no_pr_url_leak(self):
        from work_summary import aggregate_records, render_aggregate_markdown
        md = render_aggregate_markdown(aggregate_records([_store_rec()]))
        assert "https://github.com" not in md and "pr_url" not in md

    def test_render_always_shows_excluded(self):
        from work_summary import aggregate_records, render_aggregate_markdown
        md = render_aggregate_markdown(aggregate_records([_store_rec()]),
                                       excluded=["line 2: bad"])
        assert "Excluded" in md and "line 2" in md


class TestAggregateCLI:
    def test_markdown_smoke(self, tmp_path, capsys):
        from work_summary import main
        store = _write_store(tmp_path / "s.jsonl", [_store_rec(), _store_rec()])
        rc = main(["aggregate", "--store", store])
        out = capsys.readouterr().out
        assert rc == 0 and "Gate effectiveness" in out and "Records: 2" in out

    def test_json_keys(self, tmp_path, capsys):
        from work_summary import main
        store = _write_store(tmp_path / "s.jsonl", [_store_rec()])
        rc = main(["aggregate", "--store", store, "--json"])
        payload = json.loads(capsys.readouterr().out)
        assert rc == 0
        assert {"group_by", "since", "excluded", "excluded_count", "aggregate"} <= set(payload)
        assert {"n", "gates", "loop_backs", "outcomes", "effort"} <= set(payload["aggregate"])

    def test_group_by_version_cli(self, tmp_path, capsys):
        from work_summary import main
        store = _write_store(tmp_path / "s.jsonl",
                             [_store_rec(workflow_version="2.40.0"),
                              _store_rec(workflow_version="2.41.0")])
        rc = main(["aggregate", "--store", store, "--json", "--group-by", "version"])
        payload = json.loads(capsys.readouterr().out)
        assert rc == 0 and {"2.40.0", "2.41.0"} <= set(payload["aggregate"])

    def test_since_filter_cli(self, tmp_path, capsys):
        from work_summary import main
        store = _write_store(tmp_path / "s.jsonl",
                             [_store_rec(generated_at="2026-06-10T00:00:00Z"),
                              _store_rec(generated_at="2026-06-17T00:00:00Z")])
        rc = main(["aggregate", "--store", store, "--json", "--since", "2026-06-17"])
        payload = json.loads(capsys.readouterr().out)
        assert rc == 0 and payload["aggregate"]["n"] == 1

    def test_corrupt_line_exit0_stderr(self, tmp_path, capsys):
        from work_summary import main
        p = tmp_path / "s.jsonl"
        p.write_text(json.dumps(_store_rec()) + "\n{bad\n")
        rc = main(["aggregate", "--store", str(p), "--json"])
        cap = capsys.readouterr()
        assert rc == 0
        payload = json.loads(cap.out)
        assert payload["excluded_count"] == 1
        assert "exclud" in cap.err.lower()

    def test_missing_store_and_no_env_exit2(self, capsys, monkeypatch):
        from work_summary import main
        monkeypatch.delenv("RAWGENTIC_RUN_RECORD_STORE", raising=False)
        assert main(["aggregate"]) == 2

    def test_missing_file_exit2(self, tmp_path):
        from work_summary import main
        assert main(["aggregate", "--store", str(tmp_path / "nope.jsonl")]) == 2

    def test_env_store_fallback(self, tmp_path, monkeypatch):
        from work_summary import main
        store = _write_store(tmp_path / "s.jsonl", [_store_rec()])
        monkeypatch.setenv("RAWGENTIC_RUN_RECORD_STORE", store)
        assert main(["aggregate", "--json"]) == 0

    def test_bad_group_by_is_usage_error(self, tmp_path):
        from work_summary import main
        store = _write_store(tmp_path / "s.jsonl", [_store_rec()])
        with pytest.raises(SystemExit) as ei:
            main(["aggregate", "--store", store, "--group-by", "nonsense"])
        assert ei.value.code == 2

    def test_malformed_since_is_usage_error(self, tmp_path):
        from work_summary import main
        store = _write_store(tmp_path / "s.jsonl", [_store_rec()])
        # an unpadded date would silently filter everything via lexical compare;
        # fail loud instead of returning a confusing empty result.
        assert main(["aggregate", "--store", store, "--since", "2026-6-17"]) == 2

    def test_real_subprocess(self, tmp_path):
        store = _write_store(tmp_path / "s.jsonl", [_store_rec()])
        r = _subprocess.run([sys.executable, str(SUMMARY_CLI), "aggregate",
                             "--store", store], capture_output=True, text=True,
                            timeout=30)
        assert r.returncode == 0 and "Run-record aggregate" in r.stdout


# --- #138: verification_deferred (structured list) ------------------------

class TestVerificationDeferred:
    def _entry(self, tid="2"):
        return {"task_id": tid, "reason": "no makensis in dev env",
                "local_proxy": "compiled + unit-tested extractable logic",
                "target_check": "run installer on Windows target"}

    def test_absent_is_valid_old_records(self):
        from work_summary import validate_record
        assert validate_record(_valid_record()) == []  # no field = pre-#138 record

    def test_valid_list_accepted(self):
        from work_summary import validate_record
        rec = _valid_record()
        rec["verification_deferred"] = [self._entry("2"), self._entry("3")]
        assert validate_record(rec) == []

    def test_empty_list_valid(self):
        from work_summary import validate_record
        rec = _valid_record()
        rec["verification_deferred"] = []
        assert validate_record(rec) == []

    def test_not_a_list_rejected(self):
        from work_summary import validate_record
        rec = _valid_record()
        rec["verification_deferred"] = 3  # a bare count is exactly what F1 forbids
        assert any("verification_deferred" in e for e in validate_record(rec))

    def test_item_missing_field_rejected(self):
        from work_summary import validate_record
        rec = _valid_record()
        bad = self._entry()
        del bad["target_check"]
        rec["verification_deferred"] = [bad]
        assert any("target_check" in e for e in validate_record(rec))

    def test_item_empty_task_id_rejected(self):
        from work_summary import validate_record
        rec = _valid_record()
        e = self._entry()
        e["task_id"] = ""
        rec["verification_deferred"] = [e]
        assert any("task_id" in e2 for e2 in validate_record(rec))

    def test_duplicate_task_id_rejected(self):
        from work_summary import validate_record
        rec = _valid_record()
        rec["verification_deferred"] = [self._entry("2"), self._entry("2")]
        assert any("duplicate" in e.lower() for e in validate_record(rec))

    def test_render_lists_deferred_items(self):
        from work_summary import render_summary
        rec = _valid_record()
        rec["verification_deferred"] = [self._entry("2")]
        out = render_summary(rec)
        assert "deferred" in out.lower()
        assert "no makensis in dev env" in out


# --- #155 Task 1: optional top-level `usage` (strict-when-present) ---------

class TestValidateUsage:
    def _usage(self, **overrides):
        base = {"input_tokens": 12345, "output_tokens": 6789,
                "cost_estimate_usd": 1.23, "wall_clock_s": 42.5,
                "model_mix": {"opus": {"input_tokens": 100, "output_tokens": 50}}}
        base.update(overrides)
        return base

    def test_absent_is_valid_legacy_record(self):
        from work_summary import validate_record
        assert validate_record(_valid_record()) == []

    def test_all_keys_null_valid(self):
        from work_summary import validate_record
        rec = _valid_record()
        rec["usage"] = {"input_tokens": None, "output_tokens": None,
                         "cost_estimate_usd": None, "wall_clock_s": None,
                         "model_mix": None}
        assert validate_record(rec) == []

    def test_happy_path_valid(self):
        from work_summary import validate_record
        rec = _valid_record()
        rec["usage"] = self._usage()
        assert validate_record(rec) == []

    def test_not_a_dict_rejected(self):
        from work_summary import validate_record
        rec = _valid_record()
        rec["usage"] = "a lot"
        assert any("usage must be an object" in e for e in validate_record(rec))

    @pytest.mark.parametrize("key", ["input_tokens", "output_tokens",
                                      "cost_estimate_usd", "wall_clock_s",
                                      "model_mix"])
    def test_missing_key_rejected(self, key):
        from work_summary import validate_record
        rec = _valid_record()
        u = self._usage()
        del u[key]
        rec["usage"] = u
        assert any(key in e for e in validate_record(rec))

    def test_input_tokens_bool_rejected(self):
        from work_summary import validate_record
        rec = _valid_record()
        rec["usage"] = self._usage(input_tokens=True)
        assert any("input_tokens" in e for e in validate_record(rec))

    def test_input_tokens_negative_rejected(self):
        from work_summary import validate_record
        rec = _valid_record()
        rec["usage"] = self._usage(input_tokens=-1)
        assert any("input_tokens" in e for e in validate_record(rec))

    def test_cost_estimate_bool_rejected(self):
        from work_summary import validate_record
        rec = _valid_record()
        rec["usage"] = self._usage(cost_estimate_usd=True)
        assert any("cost_estimate_usd" in e for e in validate_record(rec))

    def test_cost_estimate_negative_rejected(self):
        from work_summary import validate_record
        rec = _valid_record()
        rec["usage"] = self._usage(cost_estimate_usd=-0.01)
        assert any("cost_estimate_usd" in e for e in validate_record(rec))

    def test_cost_estimate_nan_rejected(self):
        from work_summary import validate_record
        rec = _valid_record()
        rec["usage"] = self._usage(cost_estimate_usd=float("nan"))
        assert any("cost_estimate_usd" in e for e in validate_record(rec))

    def test_cost_estimate_float_valid(self):
        from work_summary import validate_record
        rec = _valid_record()
        rec["usage"] = self._usage(cost_estimate_usd=1.23)
        assert validate_record(rec) == []

    def test_model_mix_not_dict_or_null_rejected(self):
        from work_summary import validate_record
        rec = _valid_record()
        rec["usage"] = self._usage(model_mix=["opus"])
        assert any("model_mix" in e for e in validate_record(rec))

    def test_model_mix_empty_dict_valid(self):
        from work_summary import validate_record
        rec = _valid_record()
        rec["usage"] = self._usage(model_mix={})
        assert validate_record(rec) == []

    def test_model_mix_value_missing_input_tokens_rejected(self):
        from work_summary import validate_record
        rec = _valid_record()
        rec["usage"] = self._usage(model_mix={"opus": {"output_tokens": 50}})
        assert any("model_mix['opus']" in e and "input_tokens" in e
                   for e in validate_record(rec))

    def test_model_mix_inner_bool_rejected(self):
        from work_summary import validate_record
        rec = _valid_record()
        rec["usage"] = self._usage(
            model_mix={"opus": {"input_tokens": True, "output_tokens": 50}})
        assert any("model_mix['opus']" in e and "input_tokens" in e
                   for e in validate_record(rec))

    def test_model_mix_inner_negative_rejected(self):
        from work_summary import validate_record
        rec = _valid_record()
        rec["usage"] = self._usage(
            model_mix={"opus": {"input_tokens": -1, "output_tokens": 50}})
        assert any("model_mix['opus']" in e and "input_tokens" in e
                   for e in validate_record(rec))

    # --- capture_status (#189): the schema-level backstop for non-vacuity. ---
    # A usage object may carry an optional capture_status; when it claims
    # "captured", the tokens MUST be real (non-null, sum > 0). This is what makes
    # the #155 null-forever state impossible to launder as a real measurement.

    def test_capture_status_absent_ok(self):
        from work_summary import validate_record
        rec = _valid_record()
        rec["usage"] = self._usage()  # no capture_status -> unchanged behavior
        assert validate_record(rec) == []

    @pytest.mark.parametrize("status", ["captured", "unrecoverable", "unavailable"])
    def test_capture_status_valid_vocab(self, status):
        from work_summary import validate_record
        rec = _valid_record()
        # captured needs real tokens; the other two allow null (backfill/miss)
        if status == "captured":
            rec["usage"] = self._usage(capture_status=status)
        else:
            rec["usage"] = self._usage(capture_status=status, input_tokens=None,
                                       output_tokens=None, model_mix=None)
        assert validate_record(rec) == []

    @pytest.mark.parametrize("bad", ["CAPTURED", "done", "", 123, None, True])
    def test_capture_status_bad_value_rejected(self, bad):
        from work_summary import validate_record
        rec = _valid_record()
        rec["usage"] = self._usage(capture_status=bad)
        assert any("capture_status" in e for e in validate_record(rec))

    def test_captured_with_null_tokens_rejected(self):
        from work_summary import validate_record
        rec = _valid_record()
        rec["usage"] = self._usage(capture_status="captured", input_tokens=None)
        assert any("capture_status" in e and "captured" in e
                   for e in validate_record(rec))

    def test_captured_with_zero_tokens_rejected(self):
        # the exact #155 failure mode — captured claim, zero measurement
        from work_summary import validate_record
        rec = _valid_record()
        rec["usage"] = self._usage(capture_status="captured",
                                   input_tokens=0, output_tokens=0)
        assert any("capture_status" in e and "captured" in e
                   for e in validate_record(rec))

    def test_captured_with_zero_input_positive_output_rejected(self):
        # F4: a captured claim with input_tokens=0 (output>0) is anomalous — every
        # real inference turn processes input. input>0 (not just sum>0) is required.
        from work_summary import validate_record
        rec = _valid_record()
        rec["usage"] = self._usage(capture_status="captured",
                                   input_tokens=0, output_tokens=5)
        assert any("capture_status" in e and "captured" in e
                   for e in validate_record(rec))

    def test_unrecoverable_with_null_tokens_ok(self):
        # backfill of a historical row with no session-id correlator
        from work_summary import validate_record
        rec = _valid_record()
        rec["usage"] = self._usage(capture_status="unrecoverable",
                                   input_tokens=None, output_tokens=None,
                                   model_mix=None)
        assert validate_record(rec) == []


class TestValidateGoalGuard:
    """`goal_guard` (#156, AC6) is a top-level *validated-optional* scalar, same
    pattern as `reviewer_kind`: absent is valid (old records unaffected); present
    must be a member of {"set", "skipped", "fired", "deferred"}, fail-closed on
    anything else including non-strings. `deferred` added at #191 (Step 1b defers
    the per-issue goal to the epic-level goal under a campaign)."""

    def test_absent_is_valid_legacy_record(self):
        from work_summary import validate_record
        rec = _valid_record()
        assert "goal_guard" not in rec
        assert validate_record(rec) == []

    @pytest.mark.parametrize("value", ["set", "skipped", "fired", "deferred"])
    def test_each_canonical_value_is_valid(self, value):
        from work_summary import validate_record
        rec = _valid_record()
        rec["goal_guard"] = value
        assert validate_record(rec) == []

    def test_uppercase_is_error(self):
        """Case-sensitive: 'SET' is not a stand-in for 'set'."""
        from work_summary import validate_record
        rec = _valid_record()
        rec["goal_guard"] = "SET"
        errs = validate_record(rec)
        assert any("goal_guard" in e for e in errs)

    def test_free_text_value_is_error(self):
        from work_summary import validate_record
        rec = _valid_record()
        rec["goal_guard"] = "yes"
        errs = validate_record(rec)
        assert any("goal_guard" in e for e in errs)

    def test_empty_string_is_error(self):
        from work_summary import validate_record
        rec = _valid_record()
        rec["goal_guard"] = ""
        errs = validate_record(rec)
        assert any("goal_guard" in e for e in errs)

    @pytest.mark.parametrize("bad", [1, None, {}])
    def test_non_string_value_is_error(self, bad):
        from work_summary import validate_record
        rec = _valid_record()
        rec["goal_guard"] = bad
        errs = validate_record(rec)
        assert any("goal_guard" in e for e in errs)


# --- #155 Task 3: render_summary best-effort Usage line --------------------

class TestRenderUsage:
    def _usage(self, **overrides):
        base = {"input_tokens": 12345, "output_tokens": 6789,
                "cost_estimate_usd": 1.23, "wall_clock_s": 42.5,
                "model_mix": {"opus": {"input_tokens": 100, "output_tokens": 50}}}
        base.update(overrides)
        return base

    def test_no_usage_no_usage_line(self):
        from work_summary import render_summary
        text = render_summary(_valid_record())
        assert "Usage:" not in text

    def test_full_usage_renders_tokens_cost_wall_model_mix(self):
        from work_summary import render_summary
        rec = _valid_record()
        rec["usage"] = self._usage()
        text = render_summary(rec)
        assert "- Usage: 12345 in / 6789 out tokens" in text
        assert "~$1.23" in text
        assert "42.5s wall" in text
        assert "opus: 100/50" in text

    def test_null_fields_render_placeholders_no_raise(self):
        from work_summary import render_summary
        rec = _valid_record()
        rec["usage"] = {"input_tokens": None, "output_tokens": None,
                        "cost_estimate_usd": None, "wall_clock_s": None,
                        "model_mix": None}
        text = render_summary(rec)
        assert "- Usage: ? in / ? out tokens" in text
        assert "~$" not in text
        assert "wall" not in text

    def test_malformed_usage_string_no_raise_no_line(self):
        from work_summary import render_summary
        rec = _valid_record()
        rec["usage"] = "a lot of tokens"
        text = render_summary(rec)
        assert "Usage:" not in text

    def test_malformed_usage_list_no_raise_no_line(self):
        from work_summary import render_summary
        rec = _valid_record()
        rec["usage"] = ["opus"]
        text = render_summary(rec)
        assert "Usage:" not in text

    def test_malformed_usage_int_no_raise_no_line(self):
        from work_summary import render_summary
        rec = _valid_record()
        rec["usage"] = 42
        text = render_summary(rec)
        assert "Usage:" not in text

    def test_model_mix_malformed_entry_skipped(self):
        from work_summary import render_summary
        rec = _valid_record()
        rec["usage"] = self._usage(
            model_mix={"opus": {"input_tokens": 100, "output_tokens": 50},
                       "haiku": "not a dict"})
        text = render_summary(rec)
        assert "opus: 100/50" in text
        assert "haiku" not in text

    def test_cost_bool_true_not_rendered_as_cost(self):
        from work_summary import render_summary
        rec = _valid_record()
        rec["usage"] = self._usage(cost_estimate_usd=True)
        text = render_summary(rec)
        assert "~$" not in text

    def test_usage_line_before_deferred_and_ci(self):
        """Placement: after Tests, before deferred block, and CI/Deploy stay last."""
        from work_summary import render_summary
        rec = _valid_record()
        rec["usage"] = self._usage()
        rec["verification_deferred"] = [
            {"task_id": "2", "reason": "no makensis in dev env",
             "local_proxy": "unit tests", "target_check": "manual install"}]
        text = render_summary(rec)
        tests_i = text.index("- Tests:")
        usage_i = text.index("- Usage:")
        deferred_i = text.index("Verification deferred")
        ci_i = text.index("- CI:")
        deploy_i = text.index("- Deploy:")
        assert tests_i < usage_i < deferred_i < ci_i < deploy_i


class TestCommittedStorePristine:
    """Drift guard for the committed run-record store (#155 Task 4): the store
    is now checked in (docs/measurements/run_records.jsonl) and must stay a
    clean, git-tracked, fully-valid JSONL file forever after.

    Green-on-arrival by design — the RED state ("store missing/corrupt/
    untracked") is the pre-commit state this same PR fixes, not something we
    mutate the repo to reproduce. `test_a_corrupt_line_is_excluded_not_silent`
    proves Test A is not vacuous: load_store DOES fail on a bad line, just not
    on this one."""

    REPO_ROOT = HOOKS_DIR.parent
    STORE_PATH = REPO_ROOT / "docs" / "measurements" / "run_records.jsonl"

    def test_committed_store_loads_clean(self):
        from work_summary import load_store
        records, excluded = load_store(str(self.STORE_PATH))
        assert excluded == []
        assert len(records) >= 12

    def test_committed_store_is_tracked_by_git(self):
        import subprocess
        r = subprocess.run(
            ["git", "ls-files", "--error-unmatch",
             "docs/measurements/run_records.jsonl"],
            cwd=str(self.REPO_ROOT), capture_output=True)
        assert r.returncode == 0, r.stderr.decode()

    def test_a_corrupt_line_is_excluded_not_silent(self, tmp_path):
        """Non-vacuity proof for test_committed_store_loads_clean: load_store
        does flag a bad line when there is one — the real store's clean
        `excluded == []` is a genuine pass, not a check that can never fail."""
        from work_summary import load_store
        p = tmp_path / "run_records.jsonl"
        p.write_text(json.dumps(_store_rec()) + "\n{not json\n")
        records, excluded = load_store(str(p))
        assert len(records) == 1
        assert excluded != []


# --- #116: canonical gate-name registry + scanner-kind controlled vocab ---

class TestCanonicalGateRegistry:
    def test_accessor_is_workflow_aware(self):
        from work_summary import canonical_gate_name, CANONICAL_GATE_NAMES
        # WF2 and WF3 reuse step numbers for different gates — the registry disambiguates
        assert canonical_gate_name("implement-feature", "4") == "Design Critique"
        assert canonical_gate_name("implement-feature", "11") == "Code Review"
        assert canonical_gate_name("implement-feature", "9") == "Implementation Drift"
        assert canonical_gate_name("fix-bug", "4") == "Lightweight Reflect"
        assert canonical_gate_name("fix-bug", "9") == "Code Review"
        # accepts int-ish step, normalizes to str key
        assert canonical_gate_name("implement-feature", 4) == "Design Critique"
        # unknown workflow/step -> None (caller keeps its own name)
        assert canonical_gate_name("implement-feature", "99") is None
        assert canonical_gate_name("nope", "4") is None
        assert set(CANONICAL_GATE_NAMES) == {"implement-feature", "fix-bug"}

    @staticmethod
    def _doc_binds_step_to_name(doc, step, name):
        # Anchor to the step->name PAIRING (a JSON row `"step": "<s>" ... "name": "<n>"`),
        # not the bare name — a common phrase like "Code Review" appears in prose, so a bare
        # membership check is near-vacuous (review F1). Fails if the mapping row is removed.
        import re
        return re.search(
            rf'"step":\s*"{re.escape(step)}"[^\n]*"name":\s*"{re.escape(name)}"', doc
        ) is not None

    def test_wf2_registry_matches_schema_doc(self):
        # drift guard (AC4): WF2's run-record schema doc binds each step to its canonical name
        from work_summary import CANONICAL_GATE_NAMES
        doc = (SKILLS_DIR / "implement-feature" / "references" / "run-record.md").read_text()
        for step, name in CANONICAL_GATE_NAMES["implement-feature"].items():
            assert self._doc_binds_step_to_name(doc, step, name), \
                f"WF2 run-record.md does not bind step {step} -> {name!r}"

    def test_wf3_registry_matches_assembly_doc(self):
        # drift guard (AC4): WF3's Step-14 run-record assembly binds each step to its name
        from work_summary import CANONICAL_GATE_NAMES
        doc = (SKILLS_DIR / "fix-bug" / "references" / "steps.md").read_text()
        for step, name in CANONICAL_GATE_NAMES["fix-bug"].items():
            assert self._doc_binds_step_to_name(doc, step, name), \
                f"WF3 fix-bug steps.md does not bind step {step} -> {name!r}"


class TestScannerKindVocab:
    def test_kinds_content(self):
        from work_summary import SCANNER_KINDS
        assert SCANNER_KINDS == {"secrets", "sca", "sast", "iac"}

    def test_lenient_default_accepts_free_text_skip_historical(self):
        # forward-only: a pre-#116 record with a free-text skip must still LOAD (not evicted)
        from work_summary import validate_record
        rec = _valid_record()
        rec["security_scan"]["skipped"] = ["sca: osv-scanner (no lockfiles)"]
        assert validate_record(rec) == []

    def test_strict_rejects_non_kind_skip(self):
        from work_summary import validate_record
        rec = _valid_record()
        rec["security_scan"]["skipped"] = ["sca: osv-scanner (no lockfiles)"]
        errs = validate_record(rec, strict=True)
        assert any("scanner kinds" in e for e in errs), errs

    def test_strict_accepts_valid_kinds(self):
        from work_summary import validate_record
        rec = _valid_record()
        rec["security_scan"]["skipped"] = ["iac", "sca"]
        assert validate_record(rec, strict=True) == []


class TestWritePathIsStrict:
    def test_cli_write_rejects_non_kind_skip(self, tmp_path):
        import subprocess
        rec = _valid_record()
        rec["security_scan"]["skipped"] = ["sca: no lockfiles"]
        rf = tmp_path / "rec.json"
        rf.write_text(json.dumps(rec), encoding="utf-8")
        store = tmp_path / "store.jsonl"
        r = subprocess.run(
            [sys.executable, str(SUMMARY_CLI), "summarize", "--record-file", str(rf),
             "--project-root", str(tmp_path), "--store", str(store)],
            capture_output=True, text=True)
        assert r.returncode == 1, r.stderr
        assert "scanner kinds" in r.stderr
        assert not store.exists() or store.read_text().strip() == ""  # not persisted

    def test_cli_write_accepts_valid_kinds(self, tmp_path):
        import subprocess
        rec = _valid_record()
        rec["security_scan"]["skipped"] = ["iac"]
        rf = tmp_path / "rec.json"
        rf.write_text(json.dumps(rec), encoding="utf-8")
        store = tmp_path / "store.jsonl"
        r = subprocess.run(
            [sys.executable, str(SUMMARY_CLI), "summarize", "--record-file", str(rf),
             "--project-root", str(tmp_path), "--store", str(store)],
            capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        assert store.exists() and store.read_text().strip()


# --- #115: multi-store / workspace fleet aggregation ---
# Reuses the module's existing `_store_rec()` (valid record + generated_at) and
# `_write_store(path, records)` (writes JSONL, returns str(path)) helpers.


class TestLoadStores:
    def test_pools_and_origin_tags(self, tmp_path):
        from work_summary import load_stores
        a = tmp_path / "a.jsonl"; b = tmp_path / "b.jsonl"
        _write_store(a, [_store_rec(), _store_rec()])
        _write_store(b, [_store_rec()])
        records, excluded, missing = load_stores(
            [(str(a), "proj-a"), (str(b), "proj-b")], tolerate_missing=True)
        assert len(records) == 3
        assert missing == []
        srcs = sorted(r["_source"] for r in records)
        assert srcs == ["proj-a", "proj-a", "proj-b"]

    def test_missing_among_several_is_skipped_with_count(self, tmp_path):
        from work_summary import load_stores
        a = tmp_path / "a.jsonl"
        _write_store(a, [_store_rec()])
        records, excluded, missing = load_stores(
            [(str(a), "a"), (str(tmp_path / "nope.jsonl"), "gone")], tolerate_missing=True)
        assert len(records) == 1
        assert len(missing) == 1 and "gone" in missing[0]

    def test_single_missing_raises_when_not_tolerated(self, tmp_path):
        from work_summary import load_stores, WorkSummaryError
        with pytest.raises(WorkSummaryError):
            load_stores([(str(tmp_path / "nope.jsonl"), "x")], tolerate_missing=False)

    def test_excluded_lines_carry_origin(self, tmp_path):
        from work_summary import load_stores
        a = tmp_path / "a.jsonl"
        a.write_text('{"not":"valid record"}\n', encoding="utf-8")
        records, excluded, missing = load_stores([(str(a), "proj-a")], tolerate_missing=True)
        assert records == []
        assert excluded and "proj-a" in excluded[0]


class TestStoresFromWorkspace:
    def test_resolves_active_projects_only(self, tmp_path):
        from work_summary import stores_from_workspace
        (tmp_path / "projects" / "app").mkdir(parents=True)
        (tmp_path / "projects" / "off").mkdir(parents=True)
        ws = tmp_path / ".rawgentic_workspace.json"
        ws.write_text(json.dumps({"projects": [
            {"name": "app", "path": "./projects/app", "active": True},
            {"name": "off", "path": "./projects/off", "active": False},
        ]}), encoding="utf-8")
        specs, skipped = stores_from_workspace(str(ws))
        assert len(specs) == 1 and skipped == []
        path, origin = specs[0]
        assert origin == "app"
        assert path.endswith("projects/app/docs/measurements/run_records.jsonl")

    def test_active_project_without_path_is_surfaced_not_dropped(self, tmp_path):
        # review F1: an active project with a malformed/absent path must not vanish silently
        from work_summary import stores_from_workspace
        ws = tmp_path / ".rawgentic_workspace.json"
        ws.write_text(json.dumps({"projects": [
            {"name": "good", "path": "./projects/good", "active": True},
            {"name": "broken", "active": True},  # no path
        ]}), encoding="utf-8")
        specs, skipped = stores_from_workspace(str(ws))
        assert len(specs) == 1
        assert len(skipped) == 1 and "broken" in skipped[0]

    def test_malformed_workspace_raises(self, tmp_path):
        from work_summary import stores_from_workspace, WorkSummaryError
        ws = tmp_path / ".rawgentic_workspace.json"
        ws.write_text("{ not json", encoding="utf-8")
        with pytest.raises(WorkSummaryError):
            stores_from_workspace(str(ws))


class TestSourceGroupBy:
    def test_source_is_a_group_key(self):
        from work_summary import _GROUP_KEYS
        assert "source" in _GROUP_KEYS

    def test_group_by_source_partitions(self, tmp_path):
        from work_summary import load_stores, aggregate_grouped
        a = tmp_path / "a.jsonl"; b = tmp_path / "b.jsonl"
        _write_store(a, [_store_rec(), _store_rec()])
        _write_store(b, [_store_rec()])
        records, _, _ = load_stores([(str(a), "a"), (str(b), "b")], tolerate_missing=True)
        grouped = aggregate_grouped(records, "source")
        assert grouped["a"]["n"] == 2
        assert grouped["b"]["n"] == 1


class TestFleetCLI:
    REPO_ROOT = HOOKS_DIR.parent

    def _run(self, tmp_path, *extra):
        import subprocess
        return subprocess.run(
            [sys.executable, str(SUMMARY_CLI), "aggregate", "--json", *extra],
            capture_output=True, text=True)

    def test_repeatable_store_pools(self, tmp_path):
        a = tmp_path / "a.jsonl"; b = tmp_path / "b.jsonl"
        _write_store(a, [_store_rec(), _store_rec()])
        _write_store(b, [_store_rec()])
        r = self._run(tmp_path, "--store", str(a), "--store", str(b))
        assert r.returncode == 0, r.stderr
        out = json.loads(r.stdout)
        assert out["aggregate"]["n"] == 3

    def test_missing_among_several_warns_not_fatal(self, tmp_path):
        a = tmp_path / "a.jsonl"
        _write_store(a, [_store_rec()])
        r = self._run(tmp_path, "--store", str(a), "--store", str(tmp_path / "gone.jsonl"))
        assert r.returncode == 0, r.stderr
        assert "gone.jsonl" in r.stderr  # visible warning
        assert json.loads(r.stdout)["aggregate"]["n"] == 1

    def test_single_missing_store_exits_2(self, tmp_path):
        r = self._run(tmp_path, "--store", str(tmp_path / "nope.jsonl"))
        assert r.returncode == 2

    def test_workspace_and_store_together_rejected(self, tmp_path):
        # review F2: --workspace + --store together must error, not silently ignore --store
        a = tmp_path / "a.jsonl"
        _write_store(a, [_store_rec()])
        ws = tmp_path / ".rawgentic_workspace.json"
        ws.write_text(json.dumps({"projects": []}), encoding="utf-8")
        r = self._run(tmp_path, "--workspace", str(ws), "--store", str(a))
        assert r.returncode == 2
        assert "not both" in r.stderr

    def test_workspace_mode_pools(self, tmp_path):
        (tmp_path / "projects" / "app" / "docs" / "measurements").mkdir(parents=True)
        _write_store(tmp_path / "projects" / "app" / "docs" / "measurements" / "run_records.jsonl",
                     [_store_rec(), _store_rec()])
        ws = tmp_path / ".rawgentic_workspace.json"
        ws.write_text(json.dumps({"projects": [
            {"name": "app", "path": "./projects/app", "active": True},
        ]}), encoding="utf-8")
        r = self._run(tmp_path, "--workspace", str(ws))
        assert r.returncode == 0, r.stderr
        assert json.loads(r.stdout)["aggregate"]["n"] == 2
