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
