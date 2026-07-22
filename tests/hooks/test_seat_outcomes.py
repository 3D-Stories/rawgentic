"""#473 (W11, epic #475) — the I3 seat-outcomes sidecar: row schema + derivation +
redaction (T1), consume-time-validated non-destructive harvest (T2), baselines + bench
anchors (T3), advisory alerts + config + run-end CLI (T4). Black-box where a CLI exists,
pure-import for the core helpers. Fixtures use REAL RoutingAuditLog envelopes, not
hand-rolled dicts (design §6).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOKS = REPO_ROOT / "hooks"
PE_SRC = REPO_ROOT / "phase_executor" / "src"
CLI = HOOKS / "seat_outcomes_lib.py"

for p in (str(HOOKS), str(PE_SRC)):
    if p not in sys.path:
        sys.path.insert(0, p)

import seat_outcomes_lib as so  # noqa: E402


# ---------------------------------------------------------------------------
# Fixture builders — real audit envelopes via RoutingAuditLog
# ---------------------------------------------------------------------------

def _obs(**over):
    """A full schema-valid v2 Observation dict (dispatched_lane present)."""
    d = {
        "schema_version": "2", "run_id": "wf2-473-x", "attempt_id": "0-aaaa1111",
        "correlation_id": "473-step8-t1", "seat": "review", "engine": "claude",
        "transport": "native", "requested_model": "claude-opus-4-8",
        "actual_model": "claude-opus-4-8", "prompt_hash": "sha256:x", "context_hashes": [],
        "usage": {"input": 10, "output": 20, "cached": 5, "cost_proxy": 0.4},
        "timing_ms": 41000, "queued_ms": 3, "process": {"exit_code": 0, "timed_out": False},
        "parse_status": "ok", "parsed_payload": None, "raw_capture_path": "/home/x/cap",
        "fallback_reason": None, "routing_config_digest": "sha256:d",
        "dispatched_lane": {"provider": "anthropic", "transport": "native",
                            "auth_mode": "subscription_oauth", "pool": "claude",
                            "credential_ref": "secret-token-xyz"},
    }
    d.update(over)
    return d


def _write_audit(tmp_path, run_id, obs_list):
    """Write a real routing-audit.jsonl (receipt+observation pairs) via RoutingAuditLog."""
    import phase_executor.enforce as enforce  # noqa: PLC0415  # pylint: disable=no-name-in-module

    capture_root = tmp_path / ".rawgentic" / "runs"
    log = enforce.RoutingAuditLog(capture_root, run_id)
    for i, o in enumerate(obs_list):
        o = dict(o)
        o["run_id"] = run_id
        lane = o["dispatched_lane"]
        r = enforce.PreReceipt(
            nonce=f"nonce{i}", seat=o["seat"], correlation_id=o["correlation_id"],
            attempt_id=o["attempt_id"],
            target_identity=enforce.target_identity({"model": o["requested_model"], "lane": lane}),
            config_digest=o["routing_config_digest"], gate_digest=None,
            author_provider="openai", verdict="pass", violations=())
        log.append_receipt(r)
        log.append_observation(o, receipt=r)
    return capture_root


# ---------------------------------------------------------------------------
# T1 — row derivation, enums, grammar + path-shape redaction, digest, validator
# ---------------------------------------------------------------------------

class TestDeriveRow:
    def test_core_fields_mapped(self):
        row = so.derive_seat_outcome(_obs(), issue=473)
        assert row["schema_version"] == "1"
        assert row["run_id"] == "wf2-473-x"
        assert row["attempt_id"] == "0-aaaa1111"
        assert row["seat"] == "review"
        assert row["model"] == "claude-opus-4-8"
        assert row["models_match"] is True
        assert row["issue"] == 473
        assert row["timing_ms"] == 41000
        assert row["parse_status"] == "ok"

    def test_credential_ref_never_copied(self):
        row = so.derive_seat_outcome(_obs(), issue=473)
        assert "credential_ref" not in json.dumps(row)
        assert row["lane"] == {"provider": "anthropic", "transport": "native",
                               "auth_mode": "subscription_oauth", "pool": "claude"}

    def test_raw_capture_path_and_prompt_hash_absent(self):
        blob = json.dumps(so.derive_seat_outcome(_obs(), issue=473))
        for denied in ("raw_capture_path", "prompt_hash", "context_hashes",
                       "parsed_payload", "/home/x/cap"):
            assert denied not in blob

    def test_model_null_when_actual_null(self):
        row = so.derive_seat_outcome(_obs(actual_model=None, parse_status="launch_error",
                                          usage=None), issue=None)
        assert row["model"] is None
        assert row["models_match"] is None

    def test_model_null_when_canonicalize_empty(self):
        # a non-null actual that canonicalizes to "" must NOT form an empty-model group
        row = so.derive_seat_outcome(_obs(actual_model="-", requested_model="-"), issue=None)
        assert row["model"] is None

    def test_issue_must_be_positive_or_null(self):
        assert so.derive_seat_outcome(_obs(), issue=0)["issue"] is None
        assert so.derive_seat_outcome(_obs(), issue=-5)["issue"] is None
        assert so.derive_seat_outcome(_obs(), issue=None)["issue"] is None

    def test_structured_fallback_never_raw(self):
        o = _obs(fallback_reason="fallback from claude-opus-4-8: timeout")
        row = so.derive_seat_outcome(o, issue=473)
        assert row["fallback"]["kind"] == "model_fallback"
        assert "fallback from" not in json.dumps(row["fallback"])
        assert so.derive_seat_outcome(_obs(fallback_reason=None), issue=1)["fallback"] is None
        assert so.derive_seat_outcome(_obs(fallback_reason="weird text"),
                                      issue=1)["fallback"]["kind"] == "other"


class TestRedaction:
    @pytest.mark.parametrize("evil", [
        "/root/secret", "/opt/data/key", "/Users/name/token", "../private",
        "C:/Users/x", "\\\\host\\share", "a/b/c/d",
    ])
    def test_path_shaped_values_redacted_to_null(self, evil):
        row = so.derive_seat_outcome(_obs(correlation_id=evil), issue=1)
        assert row["correlation_id"] is None, f"path-shaped {evil!r} was retained"
        assert row["redacted_fields"] >= 1

    @pytest.mark.parametrize("evil", [
        "has space", "back`tick", "@mention", "[bracket]", "line\nbreak", 'quo"te',
    ])
    def test_free_text_metachars_redacted_to_null(self, evil):
        row = so.derive_seat_outcome(_obs(correlation_id=evil), issue=1)
        assert row["correlation_id"] is None
        assert row["redacted_fields"] >= 1

    def test_legit_provider_model_slash_survives(self):
        row = so.derive_seat_outcome(_obs(requested_model="provider/model-x",
                                          actual_model="provider/model-x"), issue=1)
        assert row["requested_model"] == "provider/model-x"

    def test_grammar_valid_nonpath_residual_retained(self):
        # documented residual: a grammar-shaped, non-path opaque token in a trace key is kept
        row = so.derive_seat_outcome(_obs(correlation_id="AKIA1234567890ABCDEF"), issue=1)
        assert row["correlation_id"] == "AKIA1234567890ABCDEF"

    def test_value_sweep_no_absolute_paths(self):
        row = so.derive_seat_outcome(_obs(), issue=473)
        import re  # noqa: PLC0415
        assert not re.search(r'(?:^|[\s":=])/(?:home|tmp|var|etc|root|opt|Users)/',
                             json.dumps(row))


class TestEnums:
    def test_parse_status_enum_rejected_when_off_vocab(self):
        row = so.derive_seat_outcome(_obs(parse_status="bogus_status"), issue=1)
        # off-vocab enum → redacted to null (not stored raw)
        assert row["parse_status"] is None

    def test_canary_verdict_enum(self):
        o = _obs()
        o["canary_result"] = {"verdict": "pass", "policy_id": "p", "policy_revision": 1,
                              "provider": "anthropic", "profile": "x",
                              "required_checks": [], "passed_checks": [], "violations": []}
        assert so.derive_seat_outcome(o, issue=1)["canary_verdict"] == "pass"
        assert so.derive_seat_outcome(_obs(), issue=1)["canary_verdict"] is None

    def test_token_counts_integer_only(self):
        o = _obs()
        o["usage"] = {"input": 10, "output": 20, "cached": 5, "cost_proxy": 0.4}
        row = so.derive_seat_outcome(o, issue=1)
        assert row["usage"]["input"] == 10 and isinstance(row["usage"]["input"], int)


class TestContentDigest:
    def test_digest_excludes_recorded_at_and_issue(self):
        a = so.derive_seat_outcome(_obs(), issue=473)
        b = so.derive_seat_outcome(_obs(), issue=999)
        assert so.content_digest(a) == so.content_digest(b)

    def test_digest_key_order_independent(self):
        row = so.derive_seat_outcome(_obs(), issue=1)
        reordered = dict(reversed(list(row.items())))
        assert so.content_digest(row) == so.content_digest(reordered)

    def test_digest_changes_with_content(self):
        a = so.derive_seat_outcome(_obs(timing_ms=100), issue=1)
        b = so.derive_seat_outcome(_obs(timing_ms=200), issue=1)
        assert so.content_digest(a) != so.content_digest(b)


class TestValidator:
    def test_valid_row_passes(self):
        assert so.validate_seat_outcome(so.derive_seat_outcome(_obs(), issue=1)) == []

    def test_bool_as_int_rejected(self):
        row = so.derive_seat_outcome(_obs(), issue=1)
        row["timing_ms"] = True
        assert so.validate_seat_outcome(row)

    def test_unknown_key_rejected(self):
        row = so.derive_seat_outcome(_obs(), issue=1)
        row["surprise"] = 1
        assert so.validate_seat_outcome(row)

    def test_bad_recorded_at_rejected(self):
        row = so.derive_seat_outcome(_obs(), issue=1)
        row["recorded_at"] = "not-a-date"
        assert so.validate_seat_outcome(row)

    def test_missing_idempotency_key_rejected(self):
        row = so.derive_seat_outcome(_obs(), issue=1)
        del row["attempt_id"]
        assert so.validate_seat_outcome(row)


# ---------------------------------------------------------------------------
# T2 — consume-time validated, binding-checked, non-destructive locked harvest
# ---------------------------------------------------------------------------

NOW = "2026-07-22T12:00:00Z"


def _harvest(tmp_path, run_id, obs_list, store=None, issue=None):
    cap = _write_audit(tmp_path, run_id, obs_list)
    store = store or (tmp_path / "seat-outcomes.jsonl")
    res = so.harvest(cap, run_id, store, issue=issue, now=NOW)
    return res, store


class TestHarvest:
    def test_appends_valid_rows(self, tmp_path):
        res, store = _harvest(tmp_path, "wf2-473-x",
                              [_obs(attempt_id="0-a"), _obs(attempt_id="0-b")], issue=473)
        assert res["rows_appended"] == 2
        rows = [json.loads(l) for l in store.read_text().splitlines()]
        assert len(rows) == 2
        assert all(so.validate_seat_outcome(r) == [] for r in rows)
        assert all(r["issue"] == 473 for r in rows)

    def test_idempotent_rerun_appends_nothing(self, tmp_path):
        cap = _write_audit(tmp_path, "wf2-473-x", [_obs(attempt_id="0-a")])
        store = tmp_path / "seat-outcomes.jsonl"
        so.harvest(cap, "wf2-473-x", store, issue=1, now=NOW)
        res2 = so.harvest(cap, "wf2-473-x", store, issue=1, now=NOW)
        assert res2["rows_appended"] == 0
        assert len(store.read_text().splitlines()) == 1

    def test_inner_run_id_filter(self, tmp_path):
        # observation whose inner run_id != --run-id is excluded
        cap = _write_audit(tmp_path, "wf2-OTHER", [_obs(attempt_id="0-a")])
        store = tmp_path / "seat-outcomes.jsonl"
        res = so.harvest(cap, "wf2-473-x", store, issue=1, now=NOW)
        assert res["rows_appended"] == 0

    def test_missing_capture_dir_zero_rows(self, tmp_path):
        store = tmp_path / "seat-outcomes.jsonl"
        res = so.harvest(tmp_path / ".rawgentic" / "runs", "nope", store, issue=1, now=NOW)
        assert res["rows_appended"] == 0
        assert res.get("note")

    def test_malformed_audit_line_counted_not_fatal(self, tmp_path):
        cap = _write_audit(tmp_path, "wf2-473-x", [_obs(attempt_id="0-a")])
        audit = cap / "wf2-473-x" / "routing-audit.jsonl"
        with audit.open("a", encoding="utf-8") as f:
            f.write("{not json\n")
        store = tmp_path / "seat-outcomes.jsonl"
        res = so.harvest(cap, "wf2-473-x", store, issue=1, now=NOW)
        assert res["rows_appended"] == 1
        assert res["skipped_malformed"] == 1


class TestBinding:
    def test_failed_verdict_unbound(self, tmp_path):
        import phase_executor.enforce as enforce  # noqa: PLC0415  # pylint: disable=no-name-in-module
        cap = tmp_path / ".rawgentic" / "runs"
        log = enforce.RoutingAuditLog(cap, "wf2-473-x")
        o = _obs(attempt_id="0-a")
        o["run_id"] = "wf2-473-x"
        lane = o["dispatched_lane"]
        # append a receipt/obs pair but with a NON-pass verdict receipt
        r = enforce.PreReceipt(nonce="n0", seat=o["seat"], correlation_id=o["correlation_id"],
                               attempt_id=o["attempt_id"],
                               target_identity=enforce.target_identity(
                                   {"model": o["requested_model"], "lane": lane}),
                               config_digest=o["routing_config_digest"], gate_digest=None,
                               author_provider="openai", verdict="pass", violations=())
        log.append_receipt(r)
        log.append_observation(o, receipt=r)
        # now hand-craft a failed-verdict receipt referenced by a second obs
        audit = log.path
        rec = json.loads(audit.read_text().splitlines()[0])
        rec["verdict"] = "fail"
        rec["nonce"] = "nbad"
        o2 = dict(o); o2["attempt_id"] = "0-b"
        with audit.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
            f.write(json.dumps({"kind": "observation", "receipt_nonce": "nbad",
                                "observation": {**o2, "attempt_id": "0-b"}}) + "\n")
        store = tmp_path / "seat-outcomes.jsonl"
        res = so.harvest(cap, "wf2-473-x", store, issue=1, now=NOW)
        assert res["rows_appended"] == 1  # only the pass-verdict one
        assert res["skipped_unbound"] >= 1

    def test_orphan_nonce_unbound(self, tmp_path):
        cap = _write_audit(tmp_path, "wf2-473-x", [_obs(attempt_id="0-a")])
        audit = cap / "wf2-473-x" / "routing-audit.jsonl"
        o = _obs(attempt_id="0-orphan"); o["run_id"] = "wf2-473-x"
        with audit.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"kind": "observation", "receipt_nonce": "no-such-nonce",
                                "observation": o}) + "\n")
        store = tmp_path / "seat-outcomes.jsonl"
        res = so.harvest(cap, "wf2-473-x", store, issue=1, now=NOW)
        assert res["skipped_unbound"] >= 1
        assert res["rows_appended"] == 1


class TestNonDestructiveRewrite:
    def test_future_version_row_quarantined_not_recommitted(self, tmp_path):
        # Step-11 fix: an unknown/future schema_version row could carry un-redacted content;
        # it is moved to the gitignored quarantine, NOT re-committed to the tracked store.
        store = tmp_path / "seat-outcomes.jsonl"
        store.write_text(json.dumps({"schema_version": "9", "leaked": "/root/secret"}) + "\n")
        res, _ = _harvest(tmp_path, "wf2-473-x", [_obs(attempt_id="0-a")], store=store, issue=1)
        committed = store.read_text()
        assert "/root/secret" not in committed  # never re-committed
        assert not any(json.loads(l).get("schema_version") == "9"
                       for l in committed.splitlines())
        assert res["quarantined"] >= 1
        q = tmp_path / "seat-outcomes.jsonl.quarantine"
        assert q.exists() and "/root/secret" in q.read_text()  # preserved for diagnosis

    def test_invalid_interior_row_quarantined_not_committed(self, tmp_path):
        store = tmp_path / "seat-outcomes.jsonl"
        # a v1 row that fails validation (bad recorded_at) sitting BEFORE a valid trailing one
        bad = {"schema_version": "1", "run_id": "old", "attempt_id": "0-old",
               "recorded_at": "garbage", "leaked": "/root/secret"}
        good = so.derive_seat_outcome(_obs(attempt_id="0-keep"), issue=2, recorded_at=NOW)
        store.write_text(json.dumps(bad) + "\n" + json.dumps(good) + "\n")
        res, _ = _harvest(tmp_path, "wf2-473-x", [_obs(attempt_id="0-a")], store=store, issue=1)
        committed = store.read_text()
        assert "/root/secret" not in committed  # never re-committed
        assert res["quarantined"] >= 1
        q = tmp_path / "seat-outcomes.jsonl.quarantine"
        assert q.exists() and "0-old" in q.read_text()  # preserved for diagnosis

    def test_digest_conflict_aborts_before_write(self, tmp_path):
        store = tmp_path / "seat-outcomes.jsonl"
        # pre-seed a row with the SAME (run_id, attempt_id) but different content
        existing = so.derive_seat_outcome(_obs(attempt_id="0-a", timing_ms=999), issue=1,
                                          recorded_at=NOW)
        store.write_text(json.dumps(existing) + "\n")
        cap = _write_audit(tmp_path, "wf2-473-x", [_obs(attempt_id="0-a", timing_ms=1)])
        with pytest.raises(so.DigestConflict):
            so.harvest(cap, "wf2-473-x", store, issue=1, now=NOW)
        # original untouched
        assert json.loads(store.read_text().splitlines()[0])["timing_ms"] == 999


class TestStorePaths:
    def test_absent_default_store_is_empty_history(self, tmp_path):
        res, store = _harvest(tmp_path, "wf2-473-x", [_obs(attempt_id="0-a")], issue=1)
        assert res["rows_appended"] == 1
        assert store.exists()

    def test_gitignore_has_lock_and_quarantine(self):
        gi = (REPO_ROOT / ".gitignore").read_text()
        assert "seat-outcomes.jsonl.lock" in gi
        assert "seat-outcomes.jsonl.quarantine" in gi


# ---------------------------------------------------------------------------
# T3 — baselines (nearest-rank, closed schema), review baseline, bench anchors
# ---------------------------------------------------------------------------

def _row(seat="review", model="claude-opus-4-8", timing=None, cost=None, fallback=None,
         mm=True, run="wf2-x", att="0-a", ts="2026-07-22T12:00:00Z"):
    r = so.derive_seat_outcome(_obs(seat=seat, requested_model=model, actual_model=model,
                                    attempt_id=att, run_id=run,
                                    timing_ms=timing if timing is not None else 100,
                                    usage={"input": 1, "output": 1, "cached": 0,
                                           "cost_proxy": cost if cost is not None else 0.1},
                                    fallback_reason=fallback,
                                    dispatched_lane={"provider": "anthropic",
                                                     "transport": "native",
                                                     "auth_mode": "x", "pool": "claude",
                                                     "credential_ref": None}),
                               issue=1, recorded_at=ts)
    if not mm:
        r["models_match"] = False
    r.pop("redacted_fields", None)
    return r


class TestBaselines:
    def test_insufficient_history_no_percentiles(self):
        rows = [_row(timing=t, att=f"0-{t}") for t in (10, 20, 30)]
        b = so.compute_baselines(rows, min_n=5)
        g = b["groups"]["review|claude-opus-4-8"]["metrics"]["timing_ms"]
        assert g["status"] == "insufficient_history"
        assert "p50" not in g and "p90" not in g

    def test_nearest_rank_p50_p90(self):
        rows = [_row(timing=v, att=f"0-{i}") for i, v in enumerate([10, 20, 30, 40, 50])]
        g = so.compute_baselines(rows, min_n=5)["groups"]["review|claude-opus-4-8"]
        t = g["metrics"]["timing_ms"]
        assert t["status"] == "ok"
        assert t["p50"] == 30  # median
        assert t["p90"] == 50  # nearest-rank ceil(0.9*5)-1 = index 4

    def test_unknown_model_count_only(self):
        rows = [_row(model="-", att=f"0-{i}") for i in range(6)]  # canonicalizes to null
        b = so.compute_baselines(rows, min_n=5)
        assert b["unknown_model_rows"] == 6
        assert not b["groups"]

    def test_fallback_rate_full_denominator(self):
        rows = ([_row(fallback="fallback from x: timeout", att=f"0-f{i}") for i in range(2)]
                + [_row(fallback=None, att=f"0-n{i}") for i in range(3)])
        g = so.compute_baselines(rows, min_n=5)["groups"]["review|claude-opus-4-8"]
        fr = g["metrics"]["fallback_rate"]
        assert fr["n"] == 5 and fr["numerator"] == 2  # null = observed "primary served"

    def test_exclude_run_id(self):
        rows = [_row(run="keep", att=f"0-{i}") for i in range(5)] + \
               [_row(run="drop", timing=9999, att=f"0-d{i}") for i in range(5)]
        g = so.compute_baselines(rows, min_n=5, exclude_run_id="drop")
        t = g["groups"]["review|claude-opus-4-8"]["metrics"]["timing_ms"]
        assert t["n"] == 5 and t["p90"] == 100

    def test_cost_fallback_to_budget(self):
        r = _row(att="0-cb")
        r["usage"]["cost_proxy"] = None
        r["budget"] = {"reserved_usd": 1.0, "spent_usd": 0.7}
        rows = [r] + [_row(cost=0.1, att=f"0-{i}") for i in range(4)]
        g = so.compute_baselines(rows, min_n=5)["groups"]["review|claude-opus-4-8"]
        assert g["metrics"]["cost"]["n"] == 5  # the budget-fallback row counted


class TestReviewBaseline:
    def _rec(self, run, ch, workflow="implement-feature"):
        return {"workflow": workflow, "run_id": run,
                "gates": [{"step": "11", "findings_critical": ch[0], "findings_high": ch[1]}]}

    def test_workflow_filter_and_percentile(self):
        recs = [self._rec(f"r{i}", (1, i)) for i in range(5)]
        recs.append(self._rec("other", (9, 9), workflow="fix-bug"))
        b = so.compute_review_baseline(recs, workflow="implement-feature", min_n=5)
        assert b["status"] == "ok"
        assert b["n"] == 5

    def test_dedupe_by_run_id_latest(self):
        recs = [self._rec("dup", (0, 0)), self._rec("dup", (5, 5))]
        recs += [self._rec(f"r{i}", (0, 1)) for i in range(4)]
        b = so.compute_review_baseline(recs, workflow="implement-feature", min_n=5)
        assert b["n"] == 5  # dup counted once

    def test_records_without_run_id_ineligible(self):
        recs = [{"workflow": "implement-feature",
                 "gates": [{"findings_critical": 1, "findings_high": 1}]} for _ in range(6)]
        b = so.compute_review_baseline(recs, workflow="implement-feature", min_n=5)
        assert b["status"] == "insufficient_history"


class TestBenchAnchors:
    def test_stubbed_per_model_recompute(self, tmp_path):
        bench = tmp_path / "driver-bench"
        bench.mkdir()
        (bench / "stubbed-baseline.json").write_text(json.dumps({
            "cells": [{"fixture": "f1", "model": "opus", "rep": 0, "scores": {"acc": 1.0}},
                      {"fixture": "f1", "model": "opus", "rep": 1, "scores": {"acc": 0.5}}],
            "models": ["opus"]}))
        a = so.load_bench_anchors(bench)
        assert a["stubbed"]["status"] == "ok"
        assert a["stubbed"]["per_model"]["opus"]["acc"] == 0.75

    def test_live_aborted_is_partial_with_last_completed(self, tmp_path):
        bench = tmp_path / "driver-bench"
        bench.mkdir()
        (bench / "live-20260722T090000.json").write_text(json.dumps(
            {"cells": [{"fixture": "f", "model": "live", "rep": 0, "scores": {"acc": 0.9}}]}))
        (bench / "live-20260722T100000.json").write_text(json.dumps(
            {"aborted": True, "abort_reason": "budget",
             "cells": [{"fixture": "f", "model": "live", "rep": 0, "scores": {"acc": 0.1}}]}))
        a = so.load_bench_anchors(bench)
        assert a["live"]["status"] == "partial"
        assert a["live"]["last_completed"] is not None

    def test_missing_bench_unavailable(self, tmp_path):
        a = so.load_bench_anchors(tmp_path / "nope")
        assert a["stubbed"]["status"] == "unavailable"
        assert a["live"]["status"] == "unavailable"


# ---------------------------------------------------------------------------
# T4 — advisory alert rules, telemetryAlerts config, run-end / validate-config CLI
# ---------------------------------------------------------------------------

def _valid_record(run_id, issue):
    """A minimal record that passes work_summary.validate_record(strict=True)."""
    return {
        "workflow": "implement-feature", "workflow_version": "3.90.2",
        "issue": {"number": issue, "type": "feature", "complexity": "standard"},
        "changes": {"files_changed": 1, "insertions": 1, "deletions": 0, "commits": 1},
        "tests": {"added": 1, "passing": 1, "total": 1},
        "gates": [{"step": "11", "name": "Code Review", "findings": 0, "resolved": 0,
                   "status": "pass", "reviewer_kind": "inline"}],
        "security_scan": {"ran": True, "blocking_resolved": 0, "advisory": 0, "skipped": []},
        "loop_backs": {"used": 0, "budget": 3},
        "outcome": {"pr_number": None, "pr_url": None, "merged": False, "ci": "skipped",
                    "deploy": "not_applicable"},
        "run_id": run_id, "dispatches": [],
    }


def _baseline_with_p90(seat="review", model="claude-opus-4-8", timing_p90=100, cost_p90=1.0):
    return {"groups": {f"{seat}|{model}": {"window": 30, "metrics": {
        "timing_ms": {"n": 8, "missing": 0, "status": "ok", "p50": 50, "p90": timing_p90},
        "cost": {"n": 8, "missing": 0, "status": "ok", "p50": 0.5, "p90": cost_p90},
        "fallback_rate": {"n": 8, "missing": 0, "status": "ok", "numerator": 0, "value": 0.0},
        "mismatch_rate": {"n": 8, "missing": 0, "status": "ok", "numerator": 0, "value": 0.0},
    }}}, "review_findings": {"n": 6, "status": "ok", "p50": 2, "p90": 5},
        "unknown_model_rows": 0, "bench_anchors": None, "notes": []}


class TestAlertRules:
    def test_only_fired_results_have_fired_status(self):
        rows = [_row(fallback="fallback from x: timeout", att="0-f")]
        rec = {"run_id": "r", "workflow": "implement-feature", "dispatches": [], "gates": []}
        results = so.evaluate_alerts(rec, rows, _baseline_with_p90(), so.DEFAULT_THRESHOLDS)
        fired = [r for r in results if r["status"] == "fired"]
        assert any(r["rule"] == "fallback_fired" for r in fired)
        assert all(r.get("advisory") is True for r in results)

    def test_no_quota_rule_shipped(self):
        assert not any("quota" in k for k in so.DEFAULT_THRESHOLDS)

    def test_statistical_rule_not_evaluated_without_baseline(self):
        rows = [_row(timing=99999, att="0-slow")]
        rec = {"run_id": "r", "workflow": "implement-feature", "dispatches": [], "gates": []}
        empty = {"groups": {}, "review_findings": None, "unknown_model_rows": 0,
                 "bench_anchors": None, "notes": []}
        results = so.evaluate_alerts(rec, rows, empty, so.DEFAULT_THRESHOLDS)
        wt = [r for r in results if r["rule"] == "seat_wall_time_p90"][0]
        assert wt["status"] == "not_evaluated" and wt["reason"] == "no_baseline"

    def test_seat_wall_time_fires_over_p90(self):
        rows = [_row(timing=500, att="0-slow")]
        rec = {"run_id": "r", "workflow": "implement-feature", "dispatches": [], "gates": []}
        results = so.evaluate_alerts(rec, rows, _baseline_with_p90(timing_p90=100),
                                    so.DEFAULT_THRESHOLDS)
        wt = [r for r in results if r["rule"] == "seat_wall_time_p90"][0]
        assert wt["status"] == "fired" and wt["observed"] == 500

    def test_statistical_cardinality_one_per_group(self):
        rows = [_row(timing=500, att=f"0-{i}") for i in range(3)]  # 3 slow rows, same group
        rec = {"run_id": "r", "workflow": "implement-feature", "dispatches": [], "gates": []}
        results = so.evaluate_alerts(rec, rows, _baseline_with_p90(timing_p90=100),
                                    so.DEFAULT_THRESHOLDS)
        wt = [r for r in results if r["rule"] == "seat_wall_time_p90" and r["status"] == "fired"]
        assert len(wt) == 1  # one result per (rule, seat, model)

    def test_dispatch_failures_record_level(self):
        rec = {"run_id": "r", "workflow": "implement-feature",
               "dispatches": [{"outcome": "error"}, {"outcome": "dead"}, {"outcome": "ok"}],
               "gates": []}
        results = so.evaluate_alerts(rec, [], _baseline_with_p90(), so.DEFAULT_THRESHOLDS)
        df = [r for r in results if r["rule"] == "dispatch_failures"][0]
        assert df["status"] == "fired" and df["seat"] is None

    def test_global_disable_one_result_per_rule(self):
        th = so.load_thresholds_from_block({"version": 1, "enabled": False})
        results = so.evaluate_alerts({"run_id": "r", "workflow": "implement-feature",
                                      "dispatches": [{"outcome": "error"}], "gates": []},
                                     [_row(att="0-a")], _baseline_with_p90(), th)
        assert all(r["status"] == "disabled" for r in results)
        assert len(results) == len(so.DEFAULT_THRESHOLDS)

    def test_message_has_no_free_text_injection(self):
        rows = [_row(seat="rev/iew", att="0-a", timing=500)]  # seat gets redacted upstream normally
        rec = {"run_id": "r", "workflow": "implement-feature", "dispatches": [], "gates": []}
        results = so.evaluate_alerts(rec, rows, _baseline_with_p90(timing_p90=1),
                                    so.DEFAULT_THRESHOLDS)
        for r in results:
            msg = r.get("message") or ""
            assert "\n" not in msg
            assert "`" not in msg


class TestConfig:
    def test_defaults_when_absent(self):
        th = so.load_thresholds_from_block(None)
        assert th == so.load_thresholds_from_block({"version": 1})

    def test_count_rule_false_disables(self):
        errs = so.validate_telemetry_alerts({"version": 1, "thresholds": {"fallback_fired": False}})
        assert errs == []

    def test_count_rule_rejects_bool_true_as_int(self):
        # a count rule takes false|int; True is not a valid count
        errs = so.validate_telemetry_alerts({"version": 1, "thresholds": {"fallback_fired": True}})
        assert errs

    def test_nan_inf_rejected(self):
        assert so.validate_telemetry_alerts({"version": 1, "thresholds": {"fallback_fired": float("inf")}})

    def test_window_bounds(self):
        assert so.validate_telemetry_alerts({"version": 1, "windowSize": 0})
        assert so.validate_telemetry_alerts({"version": 1, "minSamples": 99, "windowSize": 30})

    def test_unknown_key_strict_rejects(self):
        assert so.validate_telemetry_alerts({"version": 1, "bogus": 1})

    def test_enabled_false_survives_malformed_sibling_runtime(self):
        # runtime fail-open: a valid enabled:false beside a bad key still disables
        th = so.load_thresholds_from_block({"version": 1, "enabled": False, "windowSize": "bad"})
        assert th["enabled"] is False


class TestRunEndCLI:
    def _run(self, args, cwd):
        return subprocess.run([sys.executable, str(CLI), *args], capture_output=True,
                              text=True, cwd=str(cwd))

    def test_validate_config_verb(self, tmp_path):
        ok = self._run(["validate-config", "--json", json.dumps({"version": 1})], tmp_path)
        assert ok.returncode == 0
        bad = self._run(["validate-config", "--json", json.dumps({"version": 2})], tmp_path)
        assert bad.returncode != 0

    def test_run_end_end_to_end(self, tmp_path):
        run_id = "wf2-473-e2e"
        cap = _write_audit(tmp_path, run_id, [_obs(attempt_id="0-a"), _obs(attempt_id="0-b")])
        rec = _valid_record(run_id, 473)
        rec_file = tmp_path / "rec.json"
        rec_file.write_text(json.dumps(rec))
        store = tmp_path / "docs" / "measurements" / "seat-outcomes.jsonl"
        res = self._run(["run-end", "--run-id", run_id, "--record-file", str(rec_file),
                         "--project-root", str(tmp_path), "--capture-root", str(cap),
                         "--json"], tmp_path)
        assert res.returncode == 0, res.stderr
        out = json.loads(res.stdout)
        assert out["rows_appended"] == 2
        assert store.exists()
        assert all(json.loads(l)["issue"] == 473 for l in store.read_text().splitlines())

    def test_run_end_run_id_mismatch_usage_error(self, tmp_path):
        run_id = "wf2-473-mm"
        cap = _write_audit(tmp_path, run_id, [_obs(attempt_id="0-a")])
        rec = _valid_record("DIFFERENT", 1)
        rec_file = tmp_path / "rec.json"
        rec_file.write_text(json.dumps(rec))
        res = self._run(["run-end", "--run-id", run_id, "--record-file", str(rec_file),
                         "--project-root", str(tmp_path), "--capture-root", str(cap)], tmp_path)
        assert res.returncode == 2


# ---------------------------------------------------------------------------
# T5 — additive I2 join fields (run_id + per-gate severity counts) in work_summary
# ---------------------------------------------------------------------------

class TestI2AdditiveFields:
    def _ws(self):
        import work_summary  # noqa: PLC0415
        return work_summary

    def test_run_id_present_valid(self):
        rec = _valid_record("wf2-473-x", 473)
        assert self._ws().validate_record(rec, strict=True) == []

    def test_run_id_present_invalid_grammar(self):
        rec = _valid_record("has space/../x", 473)
        assert any("run_id" in e for e in self._ws().validate_record(rec, strict=True))

    def test_run_id_absent_legacy_tolerated(self):
        rec = _valid_record("x", 473)
        del rec["run_id"]
        assert self._ws().validate_record(rec, strict=True) == []

    def test_gate_severity_both_or_neither(self):
        rec = _valid_record("x", 1)
        rec["gates"][0]["findings_critical"] = 1  # high missing
        assert any("findings_high" in e or "both" in e.lower()
                   for e in self._ws().validate_record(rec, strict=True))

    def test_gate_severity_sum_bound(self):
        rec = _valid_record("x", 1)
        g = rec["gates"][0]
        g["findings"] = 2
        g["findings_critical"], g["findings_high"] = 2, 3  # sum 5 > findings 2
        assert any("exceed" in e.lower() or "sum" in e.lower()
                   for e in self._ws().validate_record(rec, strict=True))

    def test_gate_severity_valid(self):
        rec = _valid_record("x", 1)
        g = rec["gates"][0]
        g["findings"], g["resolved"] = 5, 5
        g["findings_critical"], g["findings_high"] = 2, 3
        assert self._ws().validate_record(rec, strict=True) == []

    def test_sidecar_record_run_id_join(self, tmp_path):
        # a sidecar row and an I2 record share the run_id join key
        run_id = "wf2-473-join"
        cap = _write_audit(tmp_path, run_id, [_obs(attempt_id="0-a")])
        store = tmp_path / "seat-outcomes.jsonl"
        so.harvest(cap, run_id, store, issue=1, now=NOW)
        row = json.loads(store.read_text().splitlines()[0])
        rec = _valid_record(run_id, 1)
        assert row["run_id"] == rec["run_id"]


# ---------------------------------------------------------------------------
# T7 — prose wiring (WF2/WF3 Step 16) + config-surface agreement drift guards
# ---------------------------------------------------------------------------

class TestStep16Wiring:
    def _steps(self, skill):
        import re as _re  # noqa: PLC0415  # whitespace-normalize wrapped prose (repo drift-guard convention)
        raw = (REPO_ROOT / "skills" / skill / "references" / "steps.md").read_text()
        return _re.sub(r"\s+", " ", raw)

    def test_wf2_wires_run_end_before_summarize(self):
        t = self._steps("implement-feature")
        i_re = t.index("seat_outcomes_lib.py run-end")
        i_sum = t.index("work_summary.py summarize")
        assert i_re < i_sum, "run-end must be invoked before summarize"

    def test_wf3_wires_run_end_before_summarize(self):
        t = self._steps("fix-bug")
        assert "seat_outcomes_lib.py run-end" in t
        assert t.index("seat_outcomes_lib.py run-end") < t.index("work_summary.py summarize")

    def test_both_workflows_declare_loud_continue(self):
        for skill in ("implement-feature", "fix-bug"):
            t = self._steps(skill)
            assert "loud-log-and-continue" in t and "MUST NOT block" in t

    def test_extra_rows_fold_via_json_not_shell(self):
        for skill in ("implement-feature", "fix-bug"):
            t = self._steps(skill)
            assert "extra_rows" in t and "JSON read-modify-write" in t


class TestConfigSurfaceAgreement:
    def test_template_and_defaults_agree_on_rules(self):
        tmpl = json.loads((REPO_ROOT / "templates" / "rawgentic-json-schema.json").read_text())
        tmpl_rules = set(tmpl["telemetryAlerts"]["thresholds"])
        assert tmpl_rules == set(so.DEFAULT_THRESHOLDS)

    def test_config_reference_documents_the_key(self):
        cr = (REPO_ROOT / "docs" / "config-reference.md").read_text()
        assert "telemetryAlerts" in cr
        for rule in so.DEFAULT_THRESHOLDS:
            assert rule in cr, f"rule {rule} not documented in config-reference"

    def test_template_defaults_match_code_defaults(self):
        tmpl = json.loads((REPO_ROOT / "templates" / "rawgentic-json-schema.json").read_text())
        assert tmpl["telemetryAlerts"]["thresholds"] == so.DEFAULT_THRESHOLDS
        assert tmpl["telemetryAlerts"]["windowSize"] == so.DEFAULT_WINDOW
        assert tmpl["telemetryAlerts"]["minSamples"] == so.DEFAULT_MIN_SAMPLES

    def test_setup_step_2j_present(self):
        s = (REPO_ROOT / "skills" / "setup" / "SKILL.md").read_text()
        assert "Step 2j" in s and "telemetryAlerts" in s and "validate-config" in s


# ---------------------------------------------------------------------------
# Step-11 remediation regressions (redaction holes, harvest, binding, config)
# ---------------------------------------------------------------------------

class TestStep11Regressions:
    def test_model_path_bypass_closed(self):
        # H1: actual_model="/root/secret" must NOT survive in `model` via canonicalization
        row = so.derive_seat_outcome(_obs(actual_model="/root/.aws/credentials",
                                          requested_model="claude-opus-4-8"), issue=1)
        blob = json.dumps(row)
        assert "/root" not in blob and "credentials" not in blob
        assert row["model"] is None and row["actual_model"] is None

    def test_trailing_newline_rejected(self):
        row = so.derive_seat_outcome(_obs(correlation_id="wf2-x\n"), issue=1)
        assert row["correlation_id"] is None  # $-vs-\Z fix

    def test_one_slash_path_rejected_on_nonmodel_field(self):
        # H3: the model single-slash exception must NOT apply to run_id/correlation/seat
        for evil in ("etc/passwd", "secrets/token", "home/user"):
            row = so.derive_seat_outcome(_obs(correlation_id=evil), issue=1)
            assert row["correlation_id"] is None, f"{evil} leaked in correlation_id"

    def test_validator_rejects_stored_row_missing_key(self):
        row = so.derive_seat_outcome(_obs(), issue=1)
        row.pop("redacted_fields", None)
        del row["seat"]
        assert so.validate_seat_outcome(row)  # complete-key-set check → no KeyError downstream

    def test_validator_rejects_planted_path_in_stored_field(self):
        row = so.derive_seat_outcome(_obs(), issue=1)
        row.pop("redacted_fields", None)
        row["engine"] = "/root/x"  # hand-planted path in a stored row
        assert so.validate_seat_outcome(row)

    def test_binding_rejects_missing_nonce(self, tmp_path):
        cap = _write_audit(tmp_path, "wf2-473-x", [_obs(attempt_id="0-a")])
        audit = cap / "wf2-473-x" / "routing-audit.jsonl"
        o = _obs(attempt_id="0-nn"); o["run_id"] = "wf2-473-x"
        # an observation envelope with NO receipt_nonce + a receipt with NO nonce must not match
        with audit.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"kind": "receipt", "verdict": "pass"}) + "\n")
            f.write(json.dumps({"kind": "observation", "observation": o}) + "\n")
        store = tmp_path / "seat-outcomes.jsonl"
        res = so.harvest(cap, "wf2-473-x", store, issue=1, now=NOW)
        assert res["rows_appended"] == 1  # only the well-formed pair
        assert res["skipped_unbound"] >= 1

    def test_harvest_run_id_traversal_contained(self, tmp_path):
        # H6: a traversal run_id must not read outside the capture root
        cap = tmp_path / ".rawgentic" / "runs"
        cap.mkdir(parents=True)
        store = tmp_path / "seat-outcomes.jsonl"
        res = so.harvest(cap, "../../../etc", store, issue=1, now=NOW)
        assert res["rows_appended"] == 0  # sanitized → no such capture dir, no crash/escape

    def test_non_object_json_line_counted_not_fatal(self, tmp_path):
        cap = _write_audit(tmp_path, "wf2-473-x", [_obs(attempt_id="0-a")])
        audit = cap / "wf2-473-x" / "routing-audit.jsonl"
        with audit.open("a", encoding="utf-8") as f:
            f.write("[1,2,3]\n")   # valid JSON, not an object
        store = tmp_path / "seat-outcomes.jsonl"
        res = so.harvest(cap, "wf2-473-x", store, issue=1, now=NOW)
        assert res["rows_appended"] == 1 and res["skipped_malformed"] >= 1

    def test_duplicate_observation_nonce_unbound(self, tmp_path):
        import phase_executor.enforce as enforce  # noqa: PLC0415  # pylint: disable=no-name-in-module
        cap = tmp_path / ".rawgentic" / "runs"
        log = enforce.RoutingAuditLog(cap, "wf2-473-x")
        o = _obs(attempt_id="0-a"); o["run_id"] = "wf2-473-x"
        lane = o["dispatched_lane"]
        r = enforce.PreReceipt(nonce="nA", seat=o["seat"], correlation_id=o["correlation_id"],
                               attempt_id=o["attempt_id"],
                               target_identity=enforce.target_identity(
                                   {"model": o["requested_model"], "lane": lane}),
                               config_digest=o["routing_config_digest"], gate_digest=None,
                               author_provider="openai", verdict="pass", violations=())
        log.append_receipt(r)
        log.append_observation(o, receipt=r)
        log.append_observation(o, receipt=r)  # SECOND obs referencing the same nonce
        store = tmp_path / "seat-outcomes.jsonl"
        res = so.harvest(cap, "wf2-473-x", store, issue=1, now=NOW)
        assert res["rows_appended"] == 0  # ambiguous → both unbound
        assert res["skipped_unbound"] >= 2

    def test_config_window_applied_to_baselines(self, tmp_path):
        # F14: windowSize/minSamples from config must size the baselines (order fix)
        run_id = "wf2-473-cfg"
        cap = _write_audit(tmp_path, run_id, [_obs(attempt_id=f"0-{i}") for i in range(3)])
        (tmp_path / ".rawgentic.json").write_text(json.dumps(
            {"telemetryAlerts": {"version": 1, "minSamples": 2}}))
        rec = _valid_record(run_id, 1)
        rf = tmp_path / "rec.json"; rf.write_text(json.dumps(rec))
        res = subprocess.run([sys.executable, str(CLI), "run-end", "--run-id", run_id,
                              "--record-file", str(rf), "--project-root", str(tmp_path),
                              "--capture-root", str(cap), "--json"],
                             capture_output=True, text=True, cwd=str(tmp_path))
        assert res.returncode == 0, res.stderr
        out = json.loads(res.stdout)
        g = compute = out  # baselines are internal; assert the seat group reached n>=minSamples ok
        # 3 rows, minSamples 2 → timing baseline should be "ok" (would be insufficient at default 5)
        # re-derive via baselines verb
        b = subprocess.run([sys.executable, str(CLI), "baselines", "--project-root", str(tmp_path),
                            "--json"], capture_output=True, text=True, cwd=str(tmp_path))
        bd = json.loads(b.stdout)
        grp = bd["groups"].get("review|claude-opus-4-8", {}).get("metrics", {}).get("timing_ms", {})
        assert grp.get("status") == "ok"

    def test_disabled_rule_clean_result(self):
        th = so.load_thresholds_from_block({"version": 1, "thresholds": {"model_mismatch": False}})
        rows = [_row(mm=False, att="0-mm")]  # a mismatch present
        results = so.evaluate_alerts({"run_id": "r", "workflow": "implement-feature",
                                      "dispatches": [], "gates": []}, rows,
                                     _baseline_with_p90(), th)
        mmr = [r for r in results if r["rule"] == "model_mismatch"][0]
        assert mmr["status"] == "disabled" and mmr["reason"] == "disabled"
        assert mmr["observed"] is None and mmr["message"] is None

    def test_msg_strips_html_and_controls(self):
        out = so._msg("a\tb<script>@x`|*#\n(y)")
        for bad in ("\t", "<", ">", "@", "`", "|", "*", "#", "\n"):
            assert bad not in out  # every injection metachar dropped
        assert "a" in out and "(y)" in out  # safe chars survive

    def test_review_rule_missing_severity_not_evaluated(self):
        rec = {"run_id": "r", "workflow": "implement-feature", "dispatches": [],
               "gates": [{"step": "11", "findings": 3, "resolved": 3}]}  # no severity split
        results = so.evaluate_alerts(rec, [], _baseline_with_p90(), so.DEFAULT_THRESHOLDS)
        rf = [r for r in results if r["rule"] == "review_findings_p90"][0]
        assert rf["status"] == "not_evaluated" and rf["reason"] == "missing_input"


class TestStep11bResiduals:
    def test_budget_subvalidation(self):
        row = so.derive_seat_outcome(_obs(), issue=1)
        row.pop("redacted_fields", None)
        row["budget"] = {"leak": "/root/x"}  # planted arbitrary budget
        assert so.validate_seat_outcome(row)

    def test_budget_valid_shape_passes(self):
        row = so.derive_seat_outcome(_obs(), issue=1)
        row.pop("redacted_fields", None)
        row["budget"] = {"reserved_usd": 1.0, "spent_usd": 0.5}
        assert so.validate_seat_outcome(row) == []

    def test_audit_size_bound_single_fd(self, tmp_path):
        # size check + read share one fd (no separate _fstat_size); over-bound still aborts
        cap = _write_audit(tmp_path, "wf2-473-x", [_obs(attempt_id="0-a")])
        audit = cap / "wf2-473-x" / "routing-audit.jsonl"
        audit.write_text("x" * (10 * 1024 * 1024 + 10))  # exceed AUDIT_MAX_BYTES
        store = tmp_path / "seat-outcomes.jsonl"
        with pytest.raises(so.HarvestBounds):
            so.harvest(cap, "wf2-473-x", store, issue=1, now=NOW)

    def test_standalone_harvest_bad_record_issue_null(self, tmp_path):
        run_id = "wf2-473-sh"
        cap = _write_audit(tmp_path, run_id, [_obs(attempt_id="0-a")])
        # an arbitrary record with a positive issue but NO matching run_id must not attribute
        rf = tmp_path / "rec.json"
        rf.write_text(json.dumps({"issue": {"number": 999}, "run_id": "SOMETHING-ELSE"}))
        store = tmp_path / "docs" / "measurements" / "seat-outcomes.jsonl"
        res = subprocess.run([sys.executable, str(CLI), "harvest", "--run-id", run_id,
                              "--record-file", str(rf), "--project-root", str(tmp_path),
                              "--capture-root", str(cap)],
                             capture_output=True, text=True, cwd=str(tmp_path))
        assert res.returncode == 0, res.stderr
        rows = [json.loads(l) for l in store.read_text().splitlines()]
        assert all(r["issue"] is None for r in rows)  # never cross-attributed
