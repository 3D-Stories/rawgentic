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
