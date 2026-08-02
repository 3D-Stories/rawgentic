"""#840 — the `queue_revalidation` state shape and its fail-closed type checks.

Why these validators exist at all, and why they raise rather than accumulate errors:
`validate_driver_state` (`driver_lib.py:913`) is deliberately permissive — it inspects only
`number`/`status`/`depends_on` and has no unknown-key branch — and
`docs/driver-state/queue.schema.json` sets `additionalProperties: true` at BOTH levels. So a
malformed `validated_against` or a fabricated stamp would pass every existing check silently.
A gate whose provenance can be garbage is not a gate, so these fail closed in the raising
style of `_in_queue_deps:148-151`.

Pure functions imported directly per `docs/testing.md:5-8`.
"""
import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parents[2] / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

import driver_lib as dl  # noqa: E402

SHA = "3d4e1607d2ccb7178956f9afa05ab0dbb0cbe25d"
OTHER_SHA = "5b7b44c9f6554cd8955a01963dd7bb7bd0a8811a"


def _claim(verdict="holds"):
    return {"kind": "citation", "quoted_from_body": "hooks/a.py:1 is the cause",
            "checked_against": f"hooks/a.py@{SHA}",
            "evidence": "line 1 reads `import os`", "verdict": verdict}


class TestValidatedAgainst:
    def test_a_full_sha_is_accepted(self):
        assert dl.validate_validated_against(SHA) == SHA

    @pytest.mark.parametrize("bad", ["", "3d4e160", "z" * 40, SHA + "0", "  " + SHA])
    def test_a_malformed_sha_raises(self, bad):
        with pytest.raises(dl.DriverStateError):
            dl.validate_validated_against(bad)

    def test_a_bool_raises_rather_than_passing_as_int(self):
        """`isinstance(True, int)` is True in Python, and that has bitten this module before
        (`_is_int` exists for exactly this). A `True` stamp must never read as provenance."""
        with pytest.raises(dl.DriverStateError):
            dl.validate_validated_against(True)

    @pytest.mark.parametrize("bad", [None, 42, [SHA], {"sha": SHA}])
    def test_a_non_string_raises(self, bad):
        with pytest.raises(dl.DriverStateError):
            dl.validate_validated_against(bad)


class TestClaimsEvidence:
    def test_a_well_formed_claim_list_is_accepted(self):
        assert dl.validate_claims([_claim()]) == 1

    def test_an_empty_claim_list_is_REFUSED(self):
        """This is the whole point of the owner's ruling: a stamp with no evidence would let
        an agent mark every child valid while checking nothing."""
        with pytest.raises(dl.DriverStateError):
            dl.validate_claims([])

    def test_an_absent_claim_list_is_refused(self):
        with pytest.raises(dl.DriverStateError):
            dl.validate_claims(None)

    @pytest.mark.parametrize("missing",
                             ["kind", "quoted_from_body", "checked_against", "evidence", "verdict"])
    def test_a_claim_missing_any_required_field_raises(self, missing):
        claim = _claim()
        del claim[missing]
        with pytest.raises(dl.DriverStateError):
            dl.validate_claims([claim])

    @pytest.mark.parametrize("field", ["quoted_from_body", "evidence"])
    def test_an_empty_evidence_string_raises(self, field):
        """A present-but-blank field is the cheapest possible fake, so it must not pass."""
        claim = _claim()
        claim[field] = "   "
        with pytest.raises(dl.DriverStateError):
            dl.validate_claims([claim])

    def test_an_unknown_verdict_raises(self):
        with pytest.raises(dl.DriverStateError):
            dl.validate_claims([_claim(verdict="probably_fine")])

    def test_an_unknown_claim_kind_raises(self):
        claim = _claim()
        claim["kind"] = "vibes"
        with pytest.raises(dl.DriverStateError):
            dl.validate_claims([claim])


class TestChildRecord:
    def _record(self, **over):
        rec = {"body_hash": "a" * 64, "from_sha": OTHER_SHA, "to_sha": SHA,
               "extraction": "paths", "depth": "deep", "outcome": "still_valid",
               "pending_disposition": None, "claims": [_claim()],
               "correction_comment": None, "validated_at": 1754100000}
        rec.update(over)
        return rec

    def test_a_well_formed_record_validates(self):
        assert dl.validate_revalidation_child(self._record()) is True

    def test_issue_obsolete_is_not_a_valid_outcome(self):
        """It lives only in `pending_disposition`, so a STAMPED child can never carry it —
        a design-gate finding: an obsolete child was otherwise selectable."""
        with pytest.raises(dl.DriverStateError):
            dl.validate_revalidation_child(self._record(outcome="issue_obsolete"))

    def test_pending_disposition_accepts_issue_obsolete_or_none(self):
        assert dl.validate_revalidation_child(
            self._record(pending_disposition="issue_obsolete")) is True
        assert dl.validate_revalidation_child(self._record(pending_disposition=None)) is True

    def test_an_unknown_pending_disposition_raises(self):
        with pytest.raises(dl.DriverStateError):
            dl.validate_revalidation_child(self._record(pending_disposition="deferred"))

    @pytest.mark.parametrize("field,bad", [("extraction", "maybe"), ("depth", "medium"),
                                           ("outcome", "fine")])
    def test_an_off_vocabulary_enum_raises(self, field, bad):
        with pytest.raises(dl.DriverStateError):
            dl.validate_revalidation_child(self._record(**{field: bad}))

    def test_an_empty_claims_list_raises_through_the_record(self):
        with pytest.raises(dl.DriverStateError):
            dl.validate_revalidation_child(self._record(claims=[]))


class TestBackwardCompatibility:
    def test_a_state_with_no_queue_revalidation_key_still_validates(self):
        """Every pre-#840 campaign has no such key. PR 1 must change nothing for them."""
        state = {"schema_version": 2, "campaign": "epic-475", "epic": 475,
                 "issues": [{"number": 464, "status": "merged", "depends_on": []},
                            {"number": 465, "status": "queued", "depends_on": []}]}
        ok, errors = dl.validate_driver_state(state)
        assert ok is True, errors

    def test_a_state_carrying_queue_revalidation_also_validates(self):
        state = {"schema_version": 2, "campaign": "epic-756", "epic": 756,
                 "issues": [{"number": 840, "status": "queued", "depends_on": [],
                             "validated_against": SHA}],
                 "queue_revalidation": {"version": 1, "extractor_version": 1,
                                        "validated_head": SHA, "children": {}}}
        ok, errors = dl.validate_driver_state(state)
        assert ok is True, errors


class TestQueueRevalidationRequiredExists:
    def test_it_is_defined_but_is_a_driver_state_error(self):
        """PR 1 DEFINES the exception; PR 2 is the only thing that may raise it. Subclassing
        DriverStateError keeps every existing `except DriverStateError` caller correct."""
        assert issubclass(dl.QueueRevalidationRequired, dl.DriverStateError)

    def test_pr1_never_raises_it_from_next_ready_issue(self):
        """The design gate refused a split that shipped the gate before its clearing
        mechanism. This asserts PR 1 really is inert: selection still behaves exactly as it
        did, even on a state carrying revalidation provenance."""
        state = {"schema_version": 2, "campaign": "c", "epic": 1,
                 "issues": [{"number": 840, "status": "queued", "depends_on": []}],
                 "queue_revalidation": {"version": 1, "extractor_version": 1,
                                        "validated_head": OTHER_SHA, "children": {}}}
        assert dl.next_ready_issue(state) == 840
