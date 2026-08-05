"""Tests for the plan_lib disposition-ledger helpers (#393).

The ledger (`claude_docs/.wf2-state/<issue>/dispositions.jsonl`) holds TERMINAL
gate decisions (adopted | declined | dissolved) fed forward as reviewer context
on pass-N adversarial dispatches. Normative record schema: design doc
docs/planning/2026-07-15-393-disposition-ledger.md §1. These tests pin:
- append_disposition: plain line append (append_review_log pattern), auto-ts.
- read_dispositions: tolerant reader — missing file -> ([], 0); a line is
  CORRUPT (skipped with a stderr warning + counted) when it fails JSON parse OR
  entry validation (schema_version != 1, missing/mistyped required fields,
  finding_key mismatch vs recompute).
- fold_dispositions: last-write-wins by finding_key in file order.
- compute_finding_key: sha256 hex (prefixed "sha256:") over the UTF-8 bytes of
  json.dumps([severity, location or "", description], separators=(",",":"),
  ensure_ascii=True) — EXACTLY the engine dedupe tuple; category deliberately
  excluded (relabel-proof identity).
- strip_reopens: optional leading "REOPENS <id>:" prefix -> (id|None, stripped).
"""
import hashlib
import importlib
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
HOOKS_DIR = REPO_ROOT / "hooks"
sys.path.insert(0, str(HOOKS_DIR))


def _reload_plan_lib():
    if "plan_lib" in sys.modules:
        return importlib.reload(sys.modules["plan_lib"])
    import plan_lib as mod
    return mod


def _expected_key(severity, location, description):
    payload = json.dumps(
        [severity, location or "", description],
        separators=(",", ":"), ensure_ascii=True,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _valid_entry(mod, **over):
    finding = {
        "severity": "High",
        "location": "hooks/x.py",
        "category": "security",
        "description": "path traversal via symlink",
    }
    finding.update(over.pop("finding", {}))
    entry = {
        "schema_version": 1,
        "id": "d-4-2-1-ab3f",
        "issue": 393,
        "gate": "4",
        "pass": 2,
        "finding_key": mod.compute_finding_key(finding),
        "finding": finding,
        "disposition": "dissolved",
        "reason": "re-litigation of settled pass-1 decision",
        "decided_by": "orchestrator-adjudication",
        "date": "2026-07-15",
    }
    entry.update(over)
    return entry



def _append_raw(path, entry):
    """Write a (possibly invalid) entry as a raw JSONL line, bypassing the
    fail-closed writer — reader-tolerance tests need malformed lines on disk."""
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, separators=(",", ":")) + "\n")


# --- compute_finding_key: the exact engine-dedupe-tuple identity ---

class TestComputeFindingKey:
    def test_exact_algorithm(self):
        mod = _reload_plan_lib()
        f = {"severity": "High", "location": "a.py", "category": "x",
             "description": "desc"}
        assert mod.compute_finding_key(f) == _expected_key("High", "a.py", "desc")

    def test_missing_location_folds_to_empty_string(self):
        mod = _reload_plan_lib()
        f = {"severity": "Medium", "location": None, "description": "d"}
        assert mod.compute_finding_key(f) == _expected_key("Medium", "", "d")
        f2 = {"severity": "Medium", "description": "d"}
        assert mod.compute_finding_key(f2) == _expected_key("Medium", "", "d")

    def test_category_excluded_from_identity(self):
        # Relabel-proof: same severity+location+description under a different
        # category MUST collapse to the same key (category relabeling cannot
        # dodge the join backstop).
        mod = _reload_plan_lib()
        a = {"severity": "High", "location": "a.py", "category": "security",
             "description": "d"}
        b = dict(a, category="correctness")
        assert mod.compute_finding_key(a) == mod.compute_finding_key(b)

    def test_docstring_names_category_exclusion(self):
        mod = _reload_plan_lib()
        assert "category" in (mod.compute_finding_key.__doc__ or "")


# --- append_disposition: plain append + auto-ts ---

class TestAppendDisposition:
    def test_append_roundtrip_auto_ts(self, tmp_path):
        mod = _reload_plan_lib()
        path = tmp_path / "dispositions.jsonl"
        entry = _valid_entry(mod)
        mod.append_disposition(str(path), entry)
        lines = path.read_text().splitlines()
        assert len(lines) == 1
        stored = json.loads(lines[0])
        assert stored["ts"]  # auto-added ISO timestamp
        assert stored["date"] == "2026-07-15"  # retained alongside ts
        assert stored["finding"]["description"] == entry["finding"]["description"]

    def test_plain_line_append_two_entries(self, tmp_path):
        mod = _reload_plan_lib()
        path = tmp_path / "dispositions.jsonl"
        mod.append_disposition(str(path), _valid_entry(mod))
        mod.append_disposition(str(path), _valid_entry(mod, id="d-4-2-2-ff00"))
        assert len(path.read_text().splitlines()) == 2

    def test_docstring_states_deferrals_boundary(self):
        mod = _reload_plan_lib()
        assert "deferral" in (mod.append_disposition.__doc__ or "").lower()


# --- read_dispositions: tolerant reader with skipped count ---

class TestReadDispositions:
    def test_missing_file_returns_empty_and_zero(self, tmp_path):
        mod = _reload_plan_lib()
        entries, skipped = mod.read_dispositions(str(tmp_path / "none.jsonl"))
        assert entries == [] and skipped == 0

    def test_valid_entries_roundtrip(self, tmp_path):
        mod = _reload_plan_lib()
        path = tmp_path / "d.jsonl"
        mod.append_disposition(str(path), _valid_entry(mod))
        entries, skipped = mod.read_dispositions(str(path))
        assert len(entries) == 1 and skipped == 0
        assert entries[0]["disposition"] == "dissolved"

    def test_json_garbage_line_skipped_with_warning(self, tmp_path, capsys):
        mod = _reload_plan_lib()
        path = tmp_path / "d.jsonl"
        mod.append_disposition(str(path), _valid_entry(mod))
        with open(path, "a", encoding="utf-8") as f:
            f.write("{not json\n")
        entries, skipped = mod.read_dispositions(str(path))
        assert len(entries) == 1 and skipped == 1
        assert "dispositions" in capsys.readouterr().err

    def test_non_utf8_line_skipped_not_fatal(self, tmp_path, capsys):
        # 8a R2 High: decoding must happen per line INSIDE the tolerant path —
        # a single non-UTF-8 byte must cost one line, never the whole ledger.
        mod = _reload_plan_lib()
        path = tmp_path / "d.jsonl"
        mod.append_disposition(str(path), _valid_entry(mod))
        with open(path, "ab") as f:
            f.write(b'{"bad": "\xff\xfe"}\n')
        good = _valid_entry(mod, id="d-11-3-1-cafe",
                            finding={"description": "post-corruption entry"})
        good["finding_key"] = mod.compute_finding_key(good["finding"])
        mod.append_disposition(str(path), good)
        entries, skipped = mod.read_dispositions(str(path))
        assert len(entries) == 2 and skipped == 1
        assert "dispositions" in capsys.readouterr().err

    def test_wrong_schema_version_skipped(self, tmp_path):
        mod = _reload_plan_lib()
        path = tmp_path / "d.jsonl"
        _append_raw(path, _valid_entry(mod, schema_version=2))
        entries, skipped = mod.read_dispositions(str(path))
        assert entries == [] and skipped == 1

    def test_missing_required_field_skipped(self, tmp_path):
        mod = _reload_plan_lib()
        path = tmp_path / "d.jsonl"
        bad = _valid_entry(mod)
        del bad["disposition"]
        _append_raw(path, bad)
        entries, skipped = mod.read_dispositions(str(path))
        assert entries == [] and skipped == 1

    def test_mistyped_field_skipped(self, tmp_path):
        mod = _reload_plan_lib()
        path = tmp_path / "d.jsonl"
        _append_raw(path, _valid_entry(mod, issue="393"))
        entries, skipped = mod.read_dispositions(str(path))
        assert entries == [] and skipped == 1

    def test_invalid_disposition_value_skipped(self, tmp_path):
        # 'deferred' is NOT a ledger disposition — deferrals live in
        # deferrals.json (resolution pipeline); the ledger is terminal-only.
        mod = _reload_plan_lib()
        path = tmp_path / "d.jsonl"
        _append_raw(path, _valid_entry(mod, disposition="deferred"))
        entries, skipped = mod.read_dispositions(str(path))
        assert entries == [] and skipped == 1

    def test_finding_key_mismatch_skipped(self, tmp_path):
        mod = _reload_plan_lib()
        path = tmp_path / "d.jsonl"
        _append_raw(path, _valid_entry(mod, finding_key="sha256:" + "0" * 64))
        entries, skipped = mod.read_dispositions(str(path))
        assert entries == [] and skipped == 1

    def test_valid_entries_survive_corrupt_neighbours(self, tmp_path):
        mod = _reload_plan_lib()
        path = tmp_path / "d.jsonl"
        mod.append_disposition(str(path), _valid_entry(mod))
        _append_raw(path, _valid_entry(mod, schema_version=99))
        good = _valid_entry(mod, id="d-11-3-1-cafe",
                            finding={"description": "other finding"})
        good["finding_key"] = mod.compute_finding_key(good["finding"])
        mod.append_disposition(str(path), good)
        entries, skipped = mod.read_dispositions(str(path))
        assert len(entries) == 2 and skipped == 1

    def test_docstring_states_deferrals_boundary(self):
        mod = _reload_plan_lib()
        assert "deferral" in (mod.read_dispositions.__doc__ or "").lower()


# --- fold_dispositions: last-write-wins by finding_key in file order ---

class TestFoldDispositions:
    def test_last_write_wins(self, tmp_path):
        mod = _reload_plan_lib()
        path = tmp_path / "d.jsonl"
        mod.append_disposition(str(path), _valid_entry(mod, disposition="declined"))
        mod.append_disposition(
            str(path), _valid_entry(mod, id="d-11-3-1-cafe", disposition="adopted"))
        entries, _ = mod.read_dispositions(str(path))
        folded = mod.fold_dispositions(entries)
        assert len(folded) == 1
        assert folded[0]["disposition"] == "adopted"
        assert folded[0]["id"] == "d-11-3-1-cafe"

    def test_distinct_keys_all_kept(self, tmp_path):
        mod = _reload_plan_lib()
        path = tmp_path / "d.jsonl"
        mod.append_disposition(str(path), _valid_entry(mod))
        other = _valid_entry(mod, id="d-6-1-1-beef",
                             finding={"description": "a different finding"})
        other["finding_key"] = mod.compute_finding_key(other["finding"])
        mod.append_disposition(str(path), other)
        entries, _ = mod.read_dispositions(str(path))
        assert len(mod.fold_dispositions(entries)) == 2


# --- strip_reopens: the REOPENS <id>: description-prefix convention ---

class TestStripReopens:
    def test_valid_prefix_parsed(self):
        mod = _reload_plan_lib()
        rid, text = mod.strip_reopens(
            "REOPENS d-4-2-1-ab3f: new evidence — the cap is bypassed on retry")
        assert rid == "d-4-2-1-ab3f"
        assert text == "new evidence — the cap is bypassed on retry"

    def test_all_gate_forms_parse(self):
        # 8a R1 Low adopt: pin the non-trivial gate ids (8a, 11) and
        # multi-digit pass/seq so a regex tweak cannot regress them silently.
        mod = _reload_plan_lib()
        for rid in ("d-8a-2-1-ab3f", "d-11-12-34-Zz09"):
            got, text = mod.strip_reopens(f"REOPENS {rid}: delta text")
            assert got == rid and text == "delta text"

    def test_no_prefix_passthrough(self):
        mod = _reload_plan_lib()
        rid, text = mod.strip_reopens("plain finding description")
        assert rid is None and text == "plain finding description"

    def test_bare_reopens_not_an_exemption(self):
        mod = _reload_plan_lib()
        rid, text = mod.strip_reopens("REOPENS : no id given")
        assert rid is None and text == "REOPENS : no id given"

    def test_malformed_id_shape_not_stripped(self):
        # id shape is d-<gate>-<pass>-<seq>-<tok>
        mod = _reload_plan_lib()
        rid, text = mod.strip_reopens("REOPENS xyz: something")
        assert rid is None and text == "REOPENS xyz: something"

    def test_empty_delta_text_not_an_exemption(self):
        # A prefix with no delta text after the colon does not exempt.
        mod = _reload_plan_lib()
        rid, text = mod.strip_reopens("REOPENS d-4-2-1-ab3f:")
        assert rid is None


# --- Step 11 adopts (#393 pre-PR review) ---

class TestStep11Adopts:
    def test_fold_moves_superseded_key_to_end(self):
        # A3/R3 convergence: a key re-decided LATER must sort by its LAST
        # occurrence, so the cap drops oldest-DECIDED first, honouring the
        # most-recent-kept contract.
        mod = _reload_plan_lib()
        a1 = _valid_entry(mod, disposition="declined")           # key A, pass 1
        b = _valid_entry(mod, id="d-6-1-1-beef",
                         finding={"description": "other finding"})
        b["finding_key"] = mod.compute_finding_key(b["finding"])  # key B, pass 2
        a2 = _valid_entry(mod, id="d-11-3-1-cafe", disposition="adopted")  # key A again, pass 3
        folded = mod.fold_dispositions([a1, b, a2])
        assert [e["id"] for e in folded] == ["d-6-1-1-beef", "d-11-3-1-cafe"]

    def test_append_disposition_rejects_invalid_fails_closed(self, tmp_path):
        # A4: a malformed record must fail LOUDLY at gate close (the write),
        # not silently vanish at the next pass's tolerant read.
        import pytest as _pytest
        mod = _reload_plan_lib()
        path = tmp_path / "d.jsonl"
        bad = _valid_entry(mod, disposition="deferred")
        with _pytest.raises(ValueError):
            mod.append_disposition(str(path), bad)
        assert not path.exists()  # nothing persisted


# --- #798: the Step-4 budget-exhausted close ---------------------------------
#
# The design gate closes budget-exhausted instead of escalating the owner, but the
# close must be LOUD: an `adopted` ledger entry per applied finding, a TOP-LEVEL
# run-record `extra` row, and a canonical session-note marker. A close that renders
# identically to a clean pass defeats the whole point (#798 AC2).

def _finding(desc="design lacks a rollback path", sev="High", cat="correctness",
             loc="design.md:12"):
    return {"severity": sev, "category": cat, "description": desc, "location": loc}


def _tokens(*vals):
    """Deterministic token factory so ids are assertable."""
    it = iter(vals)
    return lambda: next(it)


class TestBudgetExhaustedClose:
    def _call(self, mod, **over):
        kwargs = dict(
            issue=798, gate="4", passes=3,
            findings=[_finding()],
            ledger_path="claude_docs/.wf2-state/798/dispositions.jsonl",
            date="2026-08-01",
            token_factory=_tokens("aa11", "bb22", "cc33"),
        )
        kwargs.update(over)
        return mod.budget_exhausted_close(**kwargs)

    def test_entries_are_adopted_not_declined(self):
        # The close APPLIES the final findings, so recording them `declined` would be
        # false — and `declined` carries suppression semantics (reviewers are told not
        # to re-raise declined findings), which would bury an unfixed security finding.
        mod = _reload_plan_lib()
        res = self._call(mod)
        assert [e["disposition"] for e in res.entries] == ["adopted"]

    def test_every_entry_validates_against_the_ledger_writer(self, tmp_path):
        # The helper's output must be directly appendable — append_disposition fails
        # closed, so an entry that does not validate would blow up at gate close.
        mod = _reload_plan_lib()
        res = self._call(mod, findings=[_finding(), _finding("second flaw")])
        path = tmp_path / "d.jsonl"
        for e in res.entries:
            mod.append_disposition(str(path), e)
        assert len(path.read_text().strip().splitlines()) == 2

    def test_reason_carries_the_pass_count(self):
        # AC2: the pass count is what makes a budget-exhausted close self-describing.
        mod = _reload_plan_lib()
        res = self._call(mod, passes=3)
        assert all("passes=3" in e["reason"] for e in res.entries)

    def test_run_record_extra_is_a_top_level_label_value_row(self):
        # A gate-row `extra` validates silently and renders NOTHING (work_summary
        # renders only top-level extra); it must be the top-level {label,value} shape.
        mod = _reload_plan_lib()
        res = self._call(mod)
        assert set(res.run_record_extra) == {"label", "value"}
        assert res.run_record_extra["label"] == "design_gate_close"
        v = res.run_record_extra["value"]
        assert "budget_exhausted" in v and "passes=3" in v and "ledger=" in v

    def test_session_note_is_the_canonical_marker(self):
        mod = _reload_plan_lib()
        res = self._call(mod)
        assert res.session_note.startswith(
            "### WF2 Step 4 — design gate CLOSED budget-exhausted (#798:")
        assert "passes=3" in res.session_note

    def test_duplicate_findings_collapse_by_finding_key_first_wins(self):
        mod = _reload_plan_lib()
        dup = _finding()
        res = self._call(mod, findings=[dup, dict(dup)])
        assert len(res.entries) == 1

    def test_input_order_is_preserved(self):
        mod = _reload_plan_lib()
        res = self._call(mod,
                         findings=[_finding("first"), _finding("second")],
                         token_factory=_tokens("aa11", "bb22"))
        descs = [e["finding"]["description"] for e in res.entries]
        assert descs == ["first", "second"]

    def test_empty_findings_still_reports_the_exhaustion(self):
        # A close with nothing left to adopt is still a budget-exhausted close and
        # must not render as a clean pass.
        mod = _reload_plan_lib()
        res = self._call(mod, findings=[])
        assert res.entries == ()   # frozen dataclass -> immutable tuple
        assert "budget_exhausted" in res.run_record_extra["value"]

    def test_rejects_passes_below_one(self):
        import pytest as _pytest
        mod = _reload_plan_lib()
        with _pytest.raises(ValueError):
            self._call(mod, passes=0)

    def test_rejects_empty_ledger_path(self):
        import pytest as _pytest
        mod = _reload_plan_lib()
        with _pytest.raises(ValueError):
            self._call(mod, ledger_path="")

    def test_rejects_malformed_finding(self):
        import pytest as _pytest
        mod = _reload_plan_lib()
        with _pytest.raises(ValueError):
            self._call(mod, findings=[{"severity": "High"}])

    def test_clean_close_and_exhausted_close_are_distinguishable(self):
        # AC2's real requirement: the persisted evidence must differ. A clean close
        # emits no design_gate_close row at all.
        mod = _reload_plan_lib()
        res = self._call(mod)
        clean_record_extras = []
        assert res.run_record_extra not in clean_record_extras
        assert res.run_record_extra["label"] == "design_gate_close"


# --- #798 T2: the close-design-gate CLI adapter ------------------------------
#
# The adapter exists so AC2/AC3 are provable by executing the real close rather than
# by pinning prose. It ENFORCES eligibility (a persist-only adapter would happily
# write a forbidden close) and is all-or-nothing.

import subprocess  # noqa: E402

CLI = str(HOOKS_DIR / "plan_lib.py")


def _counters(tmp_path, design=2, total=2, **rest):
    state = {"design": design, "tdd": 0, "review": 0, "review_design": 0,
             "spec_tighten": 0, "total": total}
    state.update(rest)
    p = tmp_path / "loopback_counters.json"
    p.write_text(json.dumps(state))
    return p


def _findings_file(tmp_path):
    p = tmp_path / "f.json"
    p.write_text(json.dumps([{"severity": "High", "category": "correctness",
                              "description": "a flaw", "location": "d.md:1"}]))
    return p


def _run(tmp_path, *, breaker="clear", design=2, total=2, spec_tighten=0):
    ledger = tmp_path / "dispositions.jsonl"
    note = tmp_path / "notes.md"
    rec = tmp_path / "extra.json"
    proc = subprocess.run(
        [sys.executable, CLI, "close-design-gate",
         "--issue", "798", "--gate", "4", "--passes", "3",
         "--findings-file", str(_findings_file(tmp_path)),
         "--counters", str(_counters(tmp_path, design=design, total=total,
                                     spec_tighten=spec_tighten)),
         "--breaker-result", breaker,
         "--ledger", str(ledger), "--record-out", str(rec), "--note-out", str(note),
         "--date", "2026-08-01", "--project-root", str(tmp_path)],
        capture_output=True, text=True)
    return proc, ledger, rec, note


class TestCloseDesignGateCLI:
    def test_eligible_close_writes_all_three_artifacts(self, tmp_path):
        proc, ledger, rec, note = _run(tmp_path)
        assert proc.returncode == 0, proc.stderr
        assert ledger.exists() and rec.exists() and note.exists()
        extra = json.loads(rec.read_text())
        assert extra["label"] == "design_gate_close"
        assert "budget_exhausted" in extra["value"] and "passes=3" in extra["value"]
        assert note.read_text().lstrip().startswith("### WF2 Step 4 — design gate CLOSED")

    def test_global_cap_reached_refuses_and_writes_nothing(self, tmp_path):
        # design cap reached AND global cap reached -> the global rule requires escalate.
        # consume_loopback checks the source cap FIRST and returns, so this case would
        # otherwise read as design-cap-caused and silently close. The state must be a
        # REAL one: _read_loopback_state always recomputes total from the per-source
        # values, so total is driven here by a spec_tighten consume (qbar-P3-F1's own
        # example: design=2, spec_tighten=1, total=3).
        proc, ledger, rec, note = _run(tmp_path, design=2, spec_tighten=1)
        assert proc.returncode != 0
        assert not ledger.exists() and not rec.exists() and not note.exists()

    def test_design_cap_not_reached_refuses(self, tmp_path):
        proc, ledger, rec, note = _run(tmp_path, design=1, total=2)
        assert proc.returncode != 0
        assert not ledger.exists()

    def test_ambiguous_breaker_refuses(self, tmp_path):
        # AC4: an ambiguous finding must STOP and escalate, never close.
        proc, ledger, rec, note = _run(tmp_path, breaker="ambiguous")
        assert proc.returncode != 0
        assert not ledger.exists()

    def test_conflicting_breaker_refuses(self, tmp_path):
        proc, ledger, rec, note = _run(tmp_path, breaker="conflicting")
        assert proc.returncode != 0
        assert not ledger.exists()

    def test_note_out_appends_never_truncates(self, tmp_path):
        # Session notes are append-only (workspace rule).
        proc, ledger, rec, note = _run(tmp_path)
        assert proc.returncode == 0
        first = note.read_text()
        note.write_text("PRIOR ENTRY\n" + first)
        proc2, *_ = _run(tmp_path)
        assert proc2.returncode == 0
        assert "PRIOR ENTRY" in note.read_text()


# --- #798 Step-11 review fixes ------------------------------------------------
# Seven High findings from two independent reviewers on the pre-PR diff. Each test
# below reproduces one defect that the first implementation shipped.

class TestCloseDesignGateHardening:
    def test_corrupt_counter_refuses_close(self, tmp_path):
        # R1-H1: _read_loopback_state resets a corrupt source to 0 BEFORE recomputing
        # total, so {design:2, tdd:"corrupt", total:3} became design=2,total=2 and the
        # close was approved — defeating the stated fail-closed contract.
        ledger = tmp_path / "d.jsonl"
        c = tmp_path / "counters.json"
        # R1's exact example: with no other source consumed, the reset makes total
        # recompute to 2, so the GLOBAL guard does not catch it — only a corruption
        # check can.
        c.write_text(json.dumps({"design": 2, "tdd": "corrupt", "review": 0,
                                 "review_design": 0, "spec_tighten": 0, "total": 3}))
        proc = subprocess.run(
            [sys.executable, CLI, "close-design-gate", "--issue", "798", "--gate", "4",
             "--findings-file", str(_findings_file(tmp_path)), "--counters", str(c),
             "--breaker-result", "clear", "--ledger", str(ledger),
             "--record-out", str(tmp_path / "e.json"),
             "--note-out", str(tmp_path / "n.md"), "--date", "2026-08-01"],
            capture_output=True, text=True)
        assert proc.returncode != 0, "a corrupt counter must fail closed"
        assert not ledger.exists()

    def test_non_step4_gate_is_rejected(self, tmp_path):
        # R1-H3 / R2-H2: --gate 11 wrote adopted gate-11 entries and a
        # "WF2 Step 11 — design gate CLOSED" marker, escaping the narrow Step-4 policy.
        ledger = tmp_path / "d.jsonl"
        proc = subprocess.run(
            [sys.executable, CLI, "close-design-gate", "--issue", "798", "--gate", "11",
             "--findings-file", str(_findings_file(tmp_path)),
             "--counters", str(_counters(tmp_path)), "--breaker-result", "clear",
             "--ledger", str(ledger), "--record-out", str(tmp_path / "e.json"),
             "--note-out", str(tmp_path / "n.md"), "--date", "2026-08-01"],
            capture_output=True, text=True)
        assert proc.returncode != 0
        assert not ledger.exists()

    def test_ambiguous_finding_refuses_even_when_breaker_says_clear(self, tmp_path):
        # R2-H2: the adapter trusted --breaker-result. A caller passing `clear` with an
        # ambiguous finding in the file closed successfully.
        ledger = tmp_path / "d.jsonl"
        ff = tmp_path / "f.json"
        ff.write_text(json.dumps([{"severity": "High", "category": "correctness",
                                   "description": "a flaw", "location": "d.md:1",
                                   "ambiguity_flag": "ambiguous"}]))
        proc = subprocess.run(
            [sys.executable, CLI, "close-design-gate", "--issue", "798", "--gate", "4",
             "--findings-file", str(ff), "--counters", str(_counters(tmp_path)),
             "--breaker-result", "clear", "--ledger", str(ledger),
             "--record-out", str(tmp_path / "e.json"),
             "--note-out", str(tmp_path / "n.md"), "--date", "2026-08-01"],
            capture_output=True, text=True)
        assert proc.returncode != 0, "an ambiguous finding must never ride a 'clear' close"
        assert not ledger.exists()

    def test_paths_outside_the_project_root_are_refused(self, tmp_path):
        # R2-H4: --ledger/--record-out/--note-out accepted arbitrary absolute paths,
        # making an ordinary-looking command an arbitrary-write gadget.
        outside = tmp_path / "outside.jsonl"
        proc = subprocess.run(
            [sys.executable, CLI, "close-design-gate", "--issue", "798", "--gate", "4",
             "--findings-file", str(_findings_file(tmp_path)),
             "--counters", str(_counters(tmp_path)), "--breaker-result", "clear",
             "--ledger", str(outside), "--record-out", str(tmp_path / "e.json"),
             "--note-out", str(tmp_path / "n.md"), "--date", "2026-08-01"],
            capture_output=True, text=True)
        assert proc.returncode != 0, "a write target outside the project root must be refused"
        assert not outside.exists()

    def test_ledger_write_failure_rolls_back(self, tmp_path, monkeypatch):
        # R1-H2: a failure on entry 2 of 3 left entry 1 behind. Nothing may survive.
        mod = _reload_plan_lib()
        ledger = tmp_path / "d.jsonl"
        findings = [_finding("one"), _finding("two"), _finding("three")]
        res = mod.budget_exhausted_close(
            issue=798, gate="4", passes=3, findings=findings,
            ledger_path=str(ledger), date="2026-08-01",
            token_factory=_tokens("aa11", "bb22", "cc33"))
        calls = {"n": 0}
        real = mod.append_disposition

        def flaky(path, entry):
            calls["n"] += 1
            if calls["n"] == 2:
                raise OSError("disk full")
            return real(path, entry)

        monkeypatch.setattr(mod, "append_disposition", flaky)
        with __import__("pytest").raises(OSError):
            mod.persist_close(res, ledger_path=str(ledger))
        assert not ledger.exists() or ledger.read_text() == "", \
            "a partial ledger must be rolled back, not left behind"


class TestCloseIsVisibleInTheRenderedRunRecord:
    """R2-H3: the adapter can succeed while the RENDERED run record still reads as a
    clean pass. Unit tests on the extra object never invoked the renderer, so the one
    thing AC2 actually promises — that a human can tell the two apart — went unproven."""

    def _record(self, extra=None):
        rec = {
            "schema_version": 1, "workflow": "implement-feature", "issue": 798,
            "title": "t", "complexity": "standard", "branch": "b", "pr": None,
            "gates": [{"step": "4", "name": "Design Critique", "findings": 7,
                       "resolved": 7, "status": "pass"}],
        }
        if extra is not None:
            rec["extra"] = [extra]
        return rec

    def _render(self, rec):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "work_summary", str(HOOKS_DIR / "work_summary.py"))
        ws = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(ws)
        return ws.render_summary(rec)

    def test_rendered_clean_and_exhausted_closes_differ(self):
        mod = _reload_plan_lib()
        res = mod.budget_exhausted_close(
            issue=798, gate="4", passes=3, findings=[_finding()],
            ledger_path="claude_docs/.wf2-state/798/dispositions.jsonl",
            date="2026-08-01", token_factory=_tokens("aa11"))
        clean = self._render(self._record())
        exhausted = self._render(self._record(res.run_record_extra))
        assert clean != exhausted, "the two closes must not render identically"
        assert "design_gate_close" in exhausted
        assert "passes=3" in exhausted
        assert "design_gate_close" not in clean

    def test_extra_on_the_gate_row_would_render_nothing(self):
        # The trap this guards: a gate-row key validates silently and disappears.
        gate_row_rec = self._record()
        gate_row_rec["gates"][0]["extra"] = {"label": "design_gate_close",
                                             "value": "budget_exhausted passes=3"}
        assert "design_gate_close" not in self._render(gate_row_rec), (
            "if this ever starts rendering, the top-level requirement can be relaxed")

    def test_empty_findings_file_refuses_at_the_cli(self, tmp_path):
        # adv-diff High: a budget-exhausted close happens BECAUSE findings kept coming,
        # so an empty list is far likelier to be a collection/serialization bug than a
        # genuine zero-finding close. The pure helper still permits it (callers may have
        # legitimately adopted everything earlier); the executable boundary refuses.
        ledger = tmp_path / "d.jsonl"
        ff = tmp_path / "f.json"
        ff.write_text("[]")
        proc = subprocess.run(
            [sys.executable, CLI, "close-design-gate", "--issue", "798", "--gate", "4",
             "--findings-file", str(ff), "--counters", str(_counters(tmp_path)),
             "--breaker-result", "clear", "--ledger", str(ledger),
             "--record-out", str(tmp_path / "e.json"),
             "--note-out", str(tmp_path / "n.md"), "--date", "2026-08-01",
             "--project-root", str(tmp_path)],
            capture_output=True, text=True)
        assert proc.returncode != 0, "an empty findings file must not read as a clean close"
        assert not ledger.exists()


# --- #796 candidate 3: the assert-pr-body CLI adapter -----------------------------------
#
# The two pure functions (`assert_pr_body_has_deferred_section`, `assert_deferrals_recorded`)
# have shipped since #138 with NO production caller — they run only inside the end-of-run
# completion gate, which is exactly why the #781 H1 slip fired AFTER merge. This adapter is
# the caller, so Step 12 can execute the check instead of re-deriving it as prose.
#
# Two review findings from #796's design passes are asserted here because both are the
# reason the candidate was not shippable as first drafted:
#   1. a plan that parses to ZERO tasks must be rc 2 (caller error), never a vacuous pass;
#   2. --plan-file must be bound to the gate's recorded plan_digest, so the gate cannot be
#      satisfied against a plan revised after the gate was taken.

import json as _json  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
from pathlib import Path  # noqa: E402

_CLI = str(Path(__file__).resolve().parent.parent.parent / "hooks" / "plan_lib.py")


def _apb_plan(tmp_path, *, deferred=False, tasks=True):
    # Formats are the real ones parse_tasks enforces: a NUMERIC task id
    # (_TASK_HEADER_RE = ^###\s+Task\s+([0-9.]+)), and a deferral expressed as
    # `- verification: deferred-to-target (<reason>)` with a mandatory parenthesized reason.
    body = ""
    if tasks:
        body = "### Task 1: do the thing\n- riskLevel: standard\n"
        if deferred:
            body += "- verification: deferred-to-target (needs the live target)\n"
    p = tmp_path / "impl-plan.md"
    p.write_text("# Plan\n\n" + body, encoding="utf-8")
    return p


def _apb_run(args):
    return subprocess.run([sys.executable, _CLI, "assert-pr-body", *args],
                          capture_output=True, text=True, check=False)


# (the _apb_gate helper and the --gate-file digest-binding cells left with the
#  complexity gate — M0d, #866; assert-pr-body no longer takes --gate-file)


class TestAssertPrBodyAdapter:
    def test_a_plan_with_no_deferrals_passes(self, tmp_path) -> None:
        plan = _apb_plan(tmp_path, deferred=False)
        pr = tmp_path / "pr.md"; pr.write_text("## Summary\nnothing deferred\n", encoding="utf-8")
        r = _apb_run(["--plan-file", str(plan), "--pr-body-file", str(pr),
                  "--project-root", str(tmp_path)])
        assert r.returncode == 0, r.stderr

    def test_a_deferred_task_without_the_pr_section_FAILS_the_gate(self, tmp_path) -> None:
        plan = _apb_plan(tmp_path, deferred=True)
        pr = tmp_path / "pr.md"; pr.write_text("## Summary\nno section here\n", encoding="utf-8")
        r = _apb_run(["--plan-file", str(plan), "--pr-body-file", str(pr),
                  "--project-root", str(tmp_path)])
        assert r.returncode == 1, f"expected a gate failure, got {r.returncode}: {r.stdout}{r.stderr}"
        assert "Deferred verification" in (r.stdout + r.stderr)

    def test_a_deferred_task_WITH_the_pr_section_passes(self, tmp_path) -> None:
        plan = _apb_plan(tmp_path, deferred=True)
        pr = tmp_path / "pr.md"
        pr.write_text("## Summary\n\n## Deferred verification\n- T1: on the target\n", encoding="utf-8")
        r = _apb_run(["--plan-file", str(plan), "--pr-body-file", str(pr),
                  "--project-root", str(tmp_path)])
        assert r.returncode == 0, r.stdout + r.stderr

    def test_a_ZERO_TASK_plan_is_rc2_not_a_vacuous_pass(self, tmp_path) -> None:
        """#796 review finding 1. `assert_pr_body_has_deferred_section` returns (True, []) on an
        empty deferred list BY DESIGN — the section is omitted-when-empty. But a plan that parses
        to no tasks AT ALL means a wrong path or a malformed plan, and letting that read as a gate
        PASS is the vacuous pass the reviewers refused. It must be a caller error."""
        plan = _apb_plan(tmp_path, tasks=False)
        pr = tmp_path / "pr.md"; pr.write_text("## Summary\n", encoding="utf-8")
        r = _apb_run(["--plan-file", str(plan), "--pr-body-file", str(pr),
                  "--project-root", str(tmp_path)])
        assert r.returncode == 2, f"expected rc 2 for a zero-task plan, got {r.returncode}"
        assert "no tasks" in (r.stdout + r.stderr).lower()

    def test_an_unreadable_plan_file_is_rc2(self, tmp_path) -> None:
        pr = tmp_path / "pr.md"; pr.write_text("x\n", encoding="utf-8")
        r = _apb_run(["--plan-file", str(tmp_path / "nope.md"), "--pr-body-file", str(pr),
                  "--project-root", str(tmp_path)])
        assert r.returncode == 2
        # discriminating: before the verb existed this passed because argparse rejected an
        # unknown subcommand with rc 2 (the #730 review trap). Require the real diagnosis.
        assert "cannot read --plan-file" in r.stderr


# --- #903: a budget-exhausted close requires disposed Critical/High findings ---
#
# #798 let the gate close on budget exhaustion alone. Observed live on #874: the design
# source cap was reached with the breaker clear, so the close was available while a High
# design finding was unresolved and an AC was known unmet — the run refused it by hand.
# A second instance is on record (epic #667 child #665: budget exhausted with two findings
# still open, run proceeded). The close is only for exhaustion over RESOLVED ground.


def _disposed(sev="High", disp="applied", reason=None, desc="a flaw", loc="d.md:1"):
    f = {"severity": sev, "category": "correctness", "description": desc, "location": loc}
    if disp is not None:
        f["terminal_disposition"] = disp
    if reason is not None:
        f["disposition_reason"] = reason
    return f


class TestSevereFindingsAreDisposed:
    """AC1: the predicate that makes 'exhaustion over RESOLVED ground' checkable."""

    def test_an_undisposed_high_is_refused(self):
        mod = _reload_plan_lib()
        ok, why = mod.severe_findings_are_disposed([_disposed(disp=None)])
        assert ok is False
        assert "terminal disposition" in why

    def test_an_undisposed_critical_is_refused(self):
        mod = _reload_plan_lib()
        ok, _ = mod.severe_findings_are_disposed([_disposed(sev="Critical", disp=None)])
        assert ok is False

    def test_applied_needs_no_reason(self):
        mod = _reload_plan_lib()
        ok, why = mod.severe_findings_are_disposed([_disposed(disp="applied")])
        assert ok is True, why

    def test_refuted_without_evidence_is_refused(self):
        # AC1 spells the disposition "refuted-with-evidence" — the evidence is the point,
        # so a bare token must not satisfy it (the findings_are_unambiguous lesson:
        # an executable boundary must not assert a property it never checks).
        mod = _reload_plan_lib()
        ok, why = mod.severe_findings_are_disposed([_disposed(disp="refuted")])
        assert ok is False
        assert "disposition_reason" in why

    def test_refuted_with_evidence_passes(self):
        mod = _reload_plan_lib()
        ok, why = mod.severe_findings_are_disposed(
            [_disposed(disp="refuted", reason="the cited call site does not exist")])
        assert ok is True, why

    def test_deferred_without_rationale_is_refused(self):
        mod = _reload_plan_lib()
        ok, _ = mod.severe_findings_are_disposed([_disposed(disp="deferred")])
        assert ok is False

    def test_deferred_with_rationale_passes(self):
        mod = _reload_plan_lib()
        ok, why = mod.severe_findings_are_disposed(
            [_disposed(disp="deferred", reason="target-only surface, tracked in #123")])
        assert ok is True, why

    def test_a_whitespace_only_reason_is_not_a_reason(self):
        mod = _reload_plan_lib()
        ok, _ = mod.severe_findings_are_disposed([_disposed(disp="refuted", reason="   ")])
        assert ok is False

    def test_medium_and_low_need_no_disposition(self):
        # Scope is exactly Critical/High (AC1); lower severities are advisory and must not
        # be able to block a legitimate close.
        mod = _reload_plan_lib()
        ok, why = mod.severe_findings_are_disposed(
            [_disposed(sev="Medium", disp=None), _disposed(sev="Low", disp=None)])
        assert ok is True, why

    def test_severity_match_is_case_insensitive(self):
        mod = _reload_plan_lib()
        ok, _ = mod.severe_findings_are_disposed([_disposed(sev="HIGH", disp=None)])
        assert ok is False, "a shouted severity must not slip past the gate"

    def test_disposition_match_is_case_insensitive(self):
        mod = _reload_plan_lib()
        ok, why = mod.severe_findings_are_disposed([_disposed(disp="Applied")])
        assert ok is True, why

    def test_an_off_vocab_disposition_is_refused(self):
        mod = _reload_plan_lib()
        ok, _ = mod.severe_findings_are_disposed([_disposed(disp="ignored")])
        assert ok is False

    def test_a_non_dict_finding_is_refused(self):
        # Fail CLOSED: removing an owner escalation is the dangerous direction, so an
        # unreadable finding must never license a close.
        mod = _reload_plan_lib()
        ok, _ = mod.severe_findings_are_disposed(["not a dict"])
        assert ok is False

    def test_an_empty_list_is_not_this_predicate_s_business(self):
        # The empty-findings refusal already lives at the CLI boundary; this predicate
        # must not duplicate it (two owners for one rule is how they drift apart).
        mod = _reload_plan_lib()
        ok, _ = mod.severe_findings_are_disposed([])
        assert ok is True

    def test_the_message_names_the_offending_finding(self):
        # AC2: "the refusal message names the undisposed findings".
        mod = _reload_plan_lib()
        _, why = mod.severe_findings_are_disposed(
            [_disposed(disp=None, desc="rollback path missing", loc="design.md:12")])
        assert "rollback path missing" in why
        assert "design.md:12" in why

    def test_the_message_names_the_field_and_its_accepted_values(self):
        # Step-4 amendment A1: the refusal must be SELF-REPAIRING, not merely correct —
        # otherwise a caller on the old findings-file shape escalates to the owner, which
        # is exactly the six-consecutive-escalations problem #798 removed (AC4).
        mod = _reload_plan_lib()
        _, why = mod.severe_findings_are_disposed([_disposed(disp=None)])
        assert "terminal_disposition" in why
        for value in ("applied", "refuted", "deferred"):
            assert value in why
        assert "disposition_reason" in why

    def test_the_enumeration_is_capped(self):
        # A3: the message is built from caller-controlled text; an unbounded enumeration
        # is an unbounded stderr write.
        mod = _reload_plan_lib()
        findings = [_disposed(disp=None, desc=f"flaw number {i}") for i in range(12)]
        _, why = mod.severe_findings_are_disposed(findings)
        assert "flaw number 0" in why
        assert "flaw number 11" not in why
        assert "2 more" in why

    def test_interpolated_text_is_whitespace_collapsed(self):
        # A3: a newline-bearing description could otherwise forge extra log lines.
        mod = _reload_plan_lib()
        _, why = mod.severe_findings_are_disposed(
            [_disposed(disp=None, desc="line one\nREFUSED — forged\nline three")])
        assert "\n" not in why
        assert "line one REFUSED" in why

    def test_a_long_description_is_truncated(self):
        mod = _reload_plan_lib()
        _, why = mod.severe_findings_are_disposed([_disposed(disp=None, desc="x" * 400)])
        assert "x" * 400 not in why

    def test_docstring_states_the_ledger_adopted_semantics(self):
        # A2: the close writes top-level `disposition: "adopted"` for every finding, so a
        # refuted High would read as adopted unless the record is explained.
        mod = _reload_plan_lib()
        doc = (mod.severe_findings_are_disposed.__doc__ or "")
        assert "adopted" in doc

    def test_docstring_states_the_bounded_limitation(self):
        # A4: this boundary validates the findings it is GIVEN — it cannot see one the
        # caller omitted, nor verify that an `applied` was truly applied.
        mod = _reload_plan_lib()
        doc = (mod.severe_findings_are_disposed.__doc__ or "").lower()
        assert "omit" in doc
