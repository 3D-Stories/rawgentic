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

    def test_empty_gates_list_is_valid(self):
        from work_summary import validate_record
        rec = _valid_record()
        rec["gates"] = []
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
    """Drift guard: WF2's Step 16 must drive the completion summary through this
    CLI, not hand-type the block (which is exactly the inconsistency the
    run-record removes). If the subcommand is renamed, update skill + guard."""

    def test_implement_feature_invokes_work_summary_cli(self):
        content = (SKILLS_DIR / "implement-feature" / "SKILL.md").read_text()
        assert "work_summary.py summarize" in content, (
            "implement-feature/SKILL.md Step 16 must invoke "
            "`work_summary.py summarize`; if you renamed it, update this guard."
        )
