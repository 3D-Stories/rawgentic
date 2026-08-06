"""Tests for hooks/reset_resume_lib.py — Part 1 of #586 (measurement library only;
PR 2 wires this into the actual scheduler/launcher, per D250).

The wave log's measured lesson (epic-871-m4-wave-log.md, "Finding — herdr's scraped
`tokens` field is STALE on this host") drives the freshness contract here: a value that
stays IDENTICAL across two reads is the EXPECTED steady state for a 5-hour reset epoch —
what actually failed on this host was the underlying capture never refreshing at all
(a frozen observation timestamp). So freshness is asserted on `observed_at` advancing
between reads, never on `resets_at` changing.
"""
import json
import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

RESET_RESUME_CLI = HOOKS_DIR / "reset_resume_lib.py"


def _run_cli(*args, input_text=None, timeout=10):
    import subprocess
    result = subprocess.run(
        ["python3", str(RESET_RESUME_CLI), *args],
        capture_output=True, text=True, timeout=timeout, input=input_text,
    )
    return result.stdout, result.stderr, result.returncode


class TestExtractResetsAt:
    def test_extracts_valid_future_epoch(self):
        from reset_resume_lib import extract_resets_at
        now = 1_000_000
        payload = {"rate_limits": {"five_hour": {"resets_at": now + 3600, "used_percentage": 42.0}}}
        result = extract_resets_at(payload, now_epoch=now)
        assert result == {"ok": True, "resets_at": now + 3600, "used_percentage": 42.0}

    def test_missing_field_is_absent_not_a_crash(self):
        from reset_resume_lib import extract_resets_at
        result = extract_resets_at({"rate_limits": {}}, now_epoch=1_000_000)
        assert result == {"ok": False, "reason": "field_absent"}

    def test_missing_rate_limits_key_entirely_is_absent(self):
        from reset_resume_lib import extract_resets_at
        result = extract_resets_at({}, now_epoch=1_000_000)
        assert result == {"ok": False, "reason": "field_absent"}

    def test_non_numeric_epoch_is_rejected(self):
        from reset_resume_lib import extract_resets_at
        payload = {"rate_limits": {"five_hour": {"resets_at": "soon"}}}
        result = extract_resets_at(payload, now_epoch=1_000_000)
        assert result == {"ok": False, "reason": "not_numeric"}

    def test_epoch_in_the_past_is_implausible(self):
        from reset_resume_lib import extract_resets_at
        now = 1_000_000
        payload = {"rate_limits": {"five_hour": {"resets_at": now - 60}}}
        result = extract_resets_at(payload, now_epoch=now)
        assert result == {"ok": False, "reason": "not_in_future"}

    def test_epoch_too_far_in_future_is_implausible(self):
        from reset_resume_lib import extract_resets_at
        now = 1_000_000
        payload = {"rate_limits": {"five_hour": {"resets_at": now + 100_000}}}
        result = extract_resets_at(payload, now_epoch=now, max_future_seconds=6 * 3600)
        assert result == {"ok": False, "reason": "too_far_future"}

    def test_used_percentage_optional(self):
        from reset_resume_lib import extract_resets_at
        now = 1_000_000
        payload = {"rate_limits": {"five_hour": {"resets_at": now + 100}}}
        result = extract_resets_at(payload, now_epoch=now)
        assert result == {"ok": True, "resets_at": now + 100, "used_percentage": None}

    # --- Review finding (Medium, confidence 0.99): a non-dict payload/rate_limits/five_hour
    # value raised AttributeError instead of returning a clean ok:false result. ---

    def test_non_dict_top_level_payload_is_malformed_not_a_crash(self):
        from reset_resume_lib import extract_resets_at
        result = extract_resets_at(["not", "a", "dict"], now_epoch=1_000_000)
        assert result == {"ok": False, "reason": "malformed_payload"}

    def test_null_rate_limits_value_is_malformed_not_a_crash(self):
        from reset_resume_lib import extract_resets_at
        result = extract_resets_at({"rate_limits": None}, now_epoch=1_000_000)
        assert result == {"ok": False, "reason": "malformed_payload"}

    def test_non_dict_rate_limits_value_is_malformed_not_a_crash(self):
        from reset_resume_lib import extract_resets_at
        result = extract_resets_at({"rate_limits": "nope"}, now_epoch=1_000_000)
        assert result == {"ok": False, "reason": "malformed_payload"}

    def test_null_five_hour_value_is_malformed_not_a_crash(self):
        from reset_resume_lib import extract_resets_at
        result = extract_resets_at({"rate_limits": {"five_hour": None}}, now_epoch=1_000_000)
        assert result == {"ok": False, "reason": "malformed_payload"}

    def test_non_dict_five_hour_value_is_malformed_not_a_crash(self):
        from reset_resume_lib import extract_resets_at
        result = extract_resets_at({"rate_limits": {"five_hour": [1, 2]}}, now_epoch=1_000_000)
        assert result == {"ok": False, "reason": "malformed_payload"}

    def test_boolean_resets_at_is_rejected_not_treated_as_an_int(self):
        """bool is an int subclass in Python — True/False must not slip past the numeric check."""
        from reset_resume_lib import extract_resets_at
        payload = {"rate_limits": {"five_hour": {"resets_at": True}}}
        result = extract_resets_at(payload, now_epoch=1_000_000)
        assert result == {"ok": False, "reason": "not_numeric"}


class TestAssessFreshness:
    """`now_epoch` is REQUIRED (no default) so a caller cannot silently skip the recency
    bound by omission — a review finding (High, confidence 0.91) on the first cut of this
    function showed a relatively-advancing pair can still be an old capture replayed with a
    rewritten `observed_at`; the recency check below closes exactly that gap."""

    def test_advancing_observed_at_is_fresh_even_with_identical_resets_at(self):
        """The measured failure mode: resets_at legitimately stays constant for hours.
        That alone must never be treated as staleness."""
        from reset_resume_lib import assess_freshness
        prior = {"resets_at": 5_000_000, "observed_at": 1_000_000}
        current = {"resets_at": 5_000_000, "observed_at": 1_000_100}
        assert assess_freshness(prior, current, now_epoch=1_000_100) == {
            "fresh": True, "reason": "observed_at_advanced",
        }

    def test_frozen_observed_at_is_stale_the_herdr_defect(self):
        """Reproduces the exact measured defect: three probes spread across many minutes
        returned byte-identical output AND an unchanged revision — the capture never ran."""
        from reset_resume_lib import assess_freshness
        prior = {"resets_at": 5_000_000, "observed_at": 1_000_000}
        current = {"resets_at": 5_000_000, "observed_at": 1_000_000}
        assert assess_freshness(prior, current, now_epoch=1_000_000) == {
            "fresh": False, "reason": "observed_at_frozen",
        }

    def test_observed_at_moving_backward_is_stale(self):
        from reset_resume_lib import assess_freshness
        prior = {"resets_at": 5_000_000, "observed_at": 1_000_100}
        current = {"resets_at": 5_000_000, "observed_at": 1_000_000}
        assert assess_freshness(prior, current, now_epoch=1_000_100) == {
            "fresh": False, "reason": "observed_at_frozen",
        }

    def test_min_advance_seconds_is_respected(self):
        from reset_resume_lib import assess_freshness
        prior = {"resets_at": 5_000_000, "observed_at": 1_000_000}
        current = {"resets_at": 5_000_000, "observed_at": 1_000_000 + 0.5}
        assert assess_freshness(prior, current, now_epoch=1_000_000, min_advance_seconds=1) == {
            "fresh": False, "reason": "observed_at_frozen",
        }

    def test_advancing_pair_that_is_ancient_relative_to_now_is_still_stale(self):
        """The review finding, reproduced directly: prior->current shows real relative
        advancement, but BOTH are from hours ago — a caller re-reading an old, un-refreshed
        state file must not be told it is fresh just because the two saved timestamps differ."""
        from reset_resume_lib import assess_freshness
        prior = {"resets_at": 5_000_000, "observed_at": 1_000_000}
        current = {"resets_at": 5_000_000, "observed_at": 1_000_100}
        far_future_now = 1_000_100 + 3600  # an hour after the "current" observation
        result = assess_freshness(prior, current, now_epoch=far_future_now)
        assert result == {"fresh": False, "reason": "observed_at_stale_relative_to_now"}

    def test_max_observation_age_seconds_is_respected(self):
        from reset_resume_lib import assess_freshness
        prior = {"resets_at": 5_000_000, "observed_at": 1_000_000}
        current = {"resets_at": 5_000_000, "observed_at": 1_000_100}
        result = assess_freshness(
            prior, current, now_epoch=1_000_100 + 30, max_observation_age_seconds=10,
        )
        assert result == {"fresh": False, "reason": "observed_at_stale_relative_to_now"}

    def test_observed_at_from_the_future_relative_to_now_is_stale(self):
        """Guards clock skew / a replayed observation stamped ahead of the real clock."""
        from reset_resume_lib import assess_freshness
        prior = {"resets_at": 5_000_000, "observed_at": 1_000_000}
        current = {"resets_at": 5_000_000, "observed_at": 1_000_100}
        result = assess_freshness(prior, current, now_epoch=1_000_050)
        assert result == {"fresh": False, "reason": "observed_at_stale_relative_to_now"}


class TestComputeOneShotEpoch:
    def test_adds_fixed_lag(self):
        from reset_resume_lib import compute_one_shot_epoch
        assert compute_one_shot_epoch(1_000_000) == 1_000_060

    def test_custom_lag(self):
        from reset_resume_lib import compute_one_shot_epoch
        assert compute_one_shot_epoch(1_000_000, lag_seconds=90) == 1_000_090


class TestCheckSessionLineage:
    def test_newest_matches_lineage_tail_is_safe(self):
        from reset_resume_lib import check_session_lineage
        mtimes = {"session-old": 100, "session-new": 200}
        result = check_session_lineage("session-new", mtimes)
        assert result == {"safe": True, "reason": "newest_transcript_matches_lineage_tail"}

    def test_newest_mismatched_lineage_tail_is_unsafe(self):
        """This is the concurrent-session risk the design doc's consult finding raised:
        an unrelated session in the same workspace root can be the newest transcript."""
        from reset_resume_lib import check_session_lineage
        mtimes = {"session-ours": 100, "session-unrelated": 200}
        result = check_session_lineage("session-ours", mtimes)
        assert result == {"safe": False, "reason": "newest_transcript_not_lineage_tail"}

    def test_no_transcripts_is_unsafe(self):
        from reset_resume_lib import check_session_lineage
        assert check_session_lineage("session-ours", {}) == {"safe": False, "reason": "no_transcripts"}

    def test_tie_within_staleness_window_is_unsafe(self):
        from reset_resume_lib import check_session_lineage
        mtimes = {"session-ours": 200, "session-other": 200.4}
        result = check_session_lineage("session-ours", mtimes, tie_window_seconds=1)
        assert result == {"safe": False, "reason": "tie_within_staleness_window"}

    def test_tie_outside_staleness_window_resolves_by_mtime(self):
        from reset_resume_lib import check_session_lineage
        mtimes = {"session-ours": 100, "session-other": 200}
        result = check_session_lineage("session-other", mtimes, tie_window_seconds=1)
        assert result == {"safe": True, "reason": "newest_transcript_matches_lineage_tail"}


class TestPersistAndReadObservation:
    def test_round_trips_through_atomic_write(self, tmp_path):
        from reset_resume_lib import persist_observation, read_observation
        state_path = tmp_path / "reset-state.json"
        persist_observation(state_path, resets_at=5_000_000, observed_at=1_000_000, used_percentage=42.0)
        assert read_observation(state_path) == {
            "resets_at": 5_000_000, "observed_at": 1_000_000, "used_percentage": 42.0,
        }

    def test_read_missing_file_returns_none(self, tmp_path):
        from reset_resume_lib import read_observation
        assert read_observation(tmp_path / "does-not-exist.json") is None

    def test_read_corrupt_file_returns_none_not_a_crash(self, tmp_path):
        from reset_resume_lib import read_observation
        state_path = tmp_path / "corrupt.json"
        state_path.write_text("{not json")
        assert read_observation(state_path) is None

    # --- Review finding (Medium, confidence 0.98): valid JSON with the wrong shape/types was
    # returned as-is, so a downstream assess_freshness call raised KeyError/TypeError instead
    # of a clean failure. ---

    def test_read_wrong_shape_object_returns_none(self, tmp_path):
        from reset_resume_lib import read_observation
        state_path = tmp_path / "wrong-shape.json"
        state_path.write_text(json.dumps({"unrelated": "stuff"}))
        assert read_observation(state_path) is None

    def test_read_json_array_returns_none(self, tmp_path):
        from reset_resume_lib import read_observation
        state_path = tmp_path / "array.json"
        state_path.write_text(json.dumps([1, 2, 3]))
        assert read_observation(state_path) is None

    def test_read_string_timestamps_returns_none(self, tmp_path):
        from reset_resume_lib import read_observation
        state_path = tmp_path / "string-ts.json"
        state_path.write_text(json.dumps({"resets_at": "soon", "observed_at": "now"}))
        assert read_observation(state_path) is None

    def test_read_boolean_timestamp_returns_none(self, tmp_path):
        from reset_resume_lib import read_observation
        state_path = tmp_path / "bool-ts.json"
        state_path.write_text(json.dumps({"resets_at": True, "observed_at": 1_000_000}))
        assert read_observation(state_path) is None


class TestCLI:
    def test_extract_reads_stdin_and_prints_json(self):
        payload = json.dumps({"rate_limits": {"five_hour": {"resets_at": 2_000_100, "used_percentage": 5.0}}})
        out, err, rc = _run_cli("extract", "--now", "2000000", input_text=payload)
        assert rc == 0, err
        assert json.loads(out) == {"ok": True, "resets_at": 2_000_100, "used_percentage": 5.0}

    def test_extract_exits_nonzero_on_absent_field(self):
        out, err, rc = _run_cli("extract", "--now", "2000000", input_text="{}")
        assert rc == 1
        assert json.loads(out) == {"ok": False, "reason": "field_absent"}

    def test_one_shot_epoch_cli(self):
        out, err, rc = _run_cli("one-shot-epoch", "--resets-at", "1000000")
        assert rc == 0, err
        assert json.loads(out) == {"one_shot_epoch": 1_000_060}

    def test_persist_and_read_cli_round_trip(self, tmp_path):
        state_path = tmp_path / "state.json"
        out, err, rc = _run_cli(
            "persist", "--state-path", str(state_path),
            "--resets-at", "5000000", "--observed-at", "1000000",
        )
        assert rc == 0, err
        out2, err2, rc2 = _run_cli("read", "--state-path", str(state_path))
        assert rc2 == 0, err2
        assert json.loads(out2) == {"resets_at": 5000000, "observed_at": 1000000, "used_percentage": None}

    def test_read_cli_missing_state_exits_2(self, tmp_path):
        # Review finding (Medium, confidence 0.99): this used to print the failure JSON to
        # stderr, breaking the documented "machine JSON always on stdout" contract.
        out, err, rc = _run_cli("read", "--state-path", str(tmp_path / "nope.json"))
        assert rc == 2
        assert json.loads(out) == {"ok": False, "reason": "no_observation"}
        assert err == ""

    def test_extract_cli_malformed_payload_exits_1_with_clean_json(self):
        out, err, rc = _run_cli("extract", "--now", "1000000", input_text=json.dumps(["nope"]))
        assert rc == 1
        assert json.loads(out) == {"ok": False, "reason": "malformed_payload"}

    def test_assess_freshness_cli_requires_now(self, tmp_path):
        prior_file = tmp_path / "prior.json"
        current_file = tmp_path / "current.json"
        prior_file.write_text(json.dumps({"resets_at": 5_000_000, "observed_at": 1_000_000}))
        current_file.write_text(json.dumps({"resets_at": 5_000_000, "observed_at": 1_000_100}))
        out, err, rc = _run_cli(
            "assess-freshness", "--prior-file", str(prior_file),
            "--current-file", str(current_file), "--now", "1000100",
        )
        assert rc == 0, err
        assert json.loads(out) == {"fresh": True, "reason": "observed_at_advanced"}

    def test_assess_freshness_cli_on_malformed_file_is_clean_failure_not_a_crash(self, tmp_path):
        prior_file = tmp_path / "prior.json"
        current_file = tmp_path / "current.json"
        prior_file.write_text(json.dumps({"unrelated": "stuff"}))
        current_file.write_text(json.dumps({"resets_at": 5_000_000, "observed_at": 1_000_100}))
        out, err, rc = _run_cli(
            "assess-freshness", "--prior-file", str(prior_file),
            "--current-file", str(current_file), "--now", "1000100",
        )
        assert rc == 1
        assert json.loads(out) == {"fresh": False, "reason": "malformed_observation"}
