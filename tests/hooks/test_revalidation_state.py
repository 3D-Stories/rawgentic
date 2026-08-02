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
        """`isinstance(True, int)` is True in Python, and that has bitten this module before.

        VACUITY NOTE (Step-11 review): this test passed even with the explicit bool clause
        deleted, because `True` is also not a `str` and the string check caught it anyway. It
        therefore proved nothing about the clause it was named for. It now asserts the
        clause's OWN reachability directly, so deleting it turns this red.
        """
        with pytest.raises(dl.DriverStateError):
            dl.validate_validated_against(True)
        import inspect
        src = inspect.getsource(dl.validate_validated_against)
        assert "isinstance(value, bool)" in src, (
            "the explicit bool rejection is the documented guard; the string check happens to "
            "cover True today, so without this assertion its removal would be invisible")

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

    def test_an_obsolete_child_carries_no_outcome(self):
        """CORRECTED after the adversarial-diff review. This test previously asserted that
        `pending_disposition: "issue_obsolete"` could sit alongside `outcome: "still_valid"`,
        which contradicts the design's own rule that an obsolete child stays UNSTAMPED — a
        stamped child is selectable. The record shape now makes the two mutually exclusive."""
        assert dl.validate_revalidation_child(
            self._record(pending_disposition="issue_obsolete", outcome=None)) is True
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


class TestReviewFindingsReceiptIntegrity:
    """Adversarial-diff review, 2026-08-02. Every case below was CONFIRMED accepted before the
    fix, and each lets a contradictory or unbound receipt support a current stamp."""

    def _record(self, **over):
        rec = {"body_hash": "a" * 64, "from_sha": OTHER_SHA, "to_sha": SHA,
               "extraction": "paths", "depth": "deep", "outcome": "still_valid",
               "pending_disposition": None, "claims": [_claim()],
               "correction_comment": None, "validated_at": 1754100000}
        rec.update(over)
        return rec

    @pytest.mark.parametrize("bad", [None, "", "zz", "A" * 64, 42, True])
    def test_a_malformed_body_hash_raises(self, bad):
        """It was presence-checked only, so `body_hash: null` passed and the receipt's
        claimed binding to the issue body meant nothing."""
        with pytest.raises(dl.DriverStateError):
            dl.validate_revalidation_child(self._record(body_hash=bad))

    def test_still_valid_with_a_broken_claim_raises(self):
        """The fields were validated independently, so a receipt could assert `still_valid`
        while its own evidence said otherwise."""
        with pytest.raises(dl.DriverStateError):
            dl.validate_revalidation_child(
                self._record(outcome="still_valid", claims=[_claim(verdict="broken")]))

    def test_body_corrected_requires_a_broken_claim(self):
        with pytest.raises(dl.DriverStateError):
            dl.validate_revalidation_child(
                self._record(outcome="body_corrected", claims=[_claim(verdict="holds")],
                             correction_comment="https://example.com/c/1"))

    def test_body_corrected_requires_a_correction_comment(self):
        with pytest.raises(dl.DriverStateError):
            dl.validate_revalidation_child(
                self._record(outcome="body_corrected", claims=[_claim(verdict="broken")],
                             correction_comment=None))

    def test_body_corrected_with_both_is_accepted(self):
        assert dl.validate_revalidation_child(
            self._record(outcome="body_corrected", claims=[_claim(verdict="broken")],
                         correction_comment="https://example.com/c/1")) is True

    def test_still_valid_must_not_carry_a_correction_comment(self):
        with pytest.raises(dl.DriverStateError):
            dl.validate_revalidation_child(
                self._record(correction_comment="https://example.com/c/1"))

    def test_a_pending_disposition_cannot_coexist_with_a_stamped_outcome(self):
        """An obsolete child must stay UNSTAMPED; carrying both let it be selectable."""
        with pytest.raises(dl.DriverStateError):
            dl.validate_revalidation_child(
                self._record(pending_disposition="issue_obsolete"))


class TestQueueRevalidationValidator:
    """Adversarial-diff review: nothing validated the campaign-level receipt or connected it
    to `issues[].validated_against`, so a fabricated receipt passed the documented entry."""

    def _state(self, **over):
        st = {"schema_version": 2, "campaign": "c", "epic": 756,
              "issues": [{"number": 840, "status": "queued", "depends_on": [],
                          "validated_against": SHA}],
              "queue_revalidation": {
                  "version": 1, "extractor_version": 1, "validated_head": SHA,
                  "children": {"840": {
                      "body_hash": "a" * 64, "from_sha": OTHER_SHA, "to_sha": SHA,
                      "extraction": "paths", "depth": "deep", "outcome": "still_valid",
                      "pending_disposition": None, "claims": [_claim()],
                      "correction_comment": None, "validated_at": 1}}}}
        st["queue_revalidation"].update(over)
        return st

    def test_a_coherent_receipt_validates(self):
        assert dl.validate_queue_revalidation(self._state()) is True

    def test_a_state_without_the_key_is_a_silent_pass(self):
        assert dl.validate_queue_revalidation({"issues": []}) is True

    def test_a_malformed_validated_head_raises(self):
        with pytest.raises(dl.DriverStateError):
            dl.validate_queue_revalidation(self._state(validated_head="nope"))

    def test_an_unknown_version_raises(self):
        with pytest.raises(dl.DriverStateError):
            dl.validate_queue_revalidation(self._state(version=99))

    def test_a_stamp_with_no_matching_receipt_child_raises(self):
        """The core linkage: an issue stamped at the current head with no receipt entry is
        exactly the fabricated-provenance case."""
        st = self._state()
        st["queue_revalidation"]["children"] = {}
        with pytest.raises(dl.DriverStateError):
            dl.validate_queue_revalidation(st)

    def test_a_receipt_child_whose_to_sha_disagrees_with_the_head_raises(self):
        st = self._state()
        st["queue_revalidation"]["children"]["840"]["to_sha"] = OTHER_SHA
        with pytest.raises(dl.DriverStateError):
            dl.validate_queue_revalidation(st)

    def test_a_non_numeric_child_key_raises(self):
        st = self._state()
        st["queue_revalidation"]["children"]["not-a-number"] = \
            st["queue_revalidation"]["children"].pop("840")
        with pytest.raises(dl.DriverStateError):
            dl.validate_queue_revalidation(st)
