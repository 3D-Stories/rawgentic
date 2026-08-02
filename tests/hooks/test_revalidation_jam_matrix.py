"""#840 — the JAM MATRIX. Every refusal the queue gate can raise, driven to a good state.

**Why this file exists (owner decision 2026-08-02, after round 8).** Eight adversarial review
rounds on this PR produced six defects of one single class: *a refusal whose own printed remedy
does not clear it*. Round 2 shipped one, round 3 another, rounds 6, 7 and 8 each found a fresh
variant of a shape an earlier round had already "fixed" — because each fix patched the exact
state the reviewer named and left its twin alive. The technique that found every one of them was
the same: construct the state, read the message, EXECUTE what it says, and see whether the gate
opens.

That technique was being run by hand, one guess per round. This file runs it exhaustively.

**The property, and it is the whole file.** For every reachable campaign state: if the gate
refuses, the refusal must name a remedy that a person can actually execute, and executing it must
reach a state where the gate no longer refuses — within a bounded number of steps. A refusal that
names nothing is a jam. A remedy that does not advance is a jam. A remedy that needs a second
remedy the message never mentioned is a jam delivered one step at a time.

Nothing here asserts a message's wording. It asserts that the message WORKS.

The remedies are executed through the same production entry points an operator would use:
``record_child_outcome`` is the CLI's own function, and ``_simulate_revalidate_children`` builds
the worklist and then hands it to ``driver_lib.rebuild_receipt`` — the production helper the skill
itself calls. That indirection is deliberate: a harness that re-implements the procedure in its
own words tests the harness, and the receipt's drop/clear rules are exactly where this issue kept
going wrong.

**What this sweep actually found**, after the two round-8 class fixes landed: 280 states whose
refusal named no runnable remedy (every one a bare `DriverStateError` from the receipt validator,
of which an earlier round-8 fix had patched precisely one), 39 where a corrupt record belonging to
a NON-eligible child could never be rebuilt because the worklist is eligible-only, and — once a
malformed `validated_against` was added to the dimensions — 810 more where the prescribed remedy
crashed outright and 24 where the gate hard-errored on a value the worklist had already been
taught to tolerate. None of those were reported by any of the eight review rounds.
"""
import itertools
import re
import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parents[2] / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

import driver_lib as dl  # noqa: E402

HEAD = "fb9293c630a6e7477a638a07853dfb0846cc9cf5"
OLD = "3d4e1607d2ccb7178956f9afa05ab0dbb0cbe25d"
BODY_HASH = "9" * 64

# Every documented `record-child-outcome` status, so a parsed remedy is executed rather than
# guessed at. `deferred|abandoned` is an owner CHOICE; the matrix takes the first, and
# `test_either_owner_choice_clears_the_gate` proves the other one works too.
_STATUS_RE = re.compile(r"record-child-outcome --issue (\d+) --status ([a-z|]+)")


def _claim():
    return {"kind": "cause", "quoted_from_body": "the cause is a swallowed Enter",
            "checked_against": f"hooks/launcher_lib.py@{HEAD}",
            "evidence": "the send path uses bracketed paste", "verdict": "holds"}


def _record(*, to_sha=HEAD, pending=None, malformed=False):
    record = {"body_hash": BODY_HASH, "from_sha": OLD, "to_sha": to_sha, "extraction": "paths",
              "depth": "quick", "outcome": "still_valid", "claims": [_claim()],
              "validated_at": 1_754_000_000}
    if pending is not None:
        record["pending_disposition"] = pending
        record["outcome"] = None
    if malformed:
        record["body_hash"] = "bad"
    return record


def _simulate_revalidate_children(state, observed_head, probe=None):
    """Run what `skills/revalidate-children/SKILL.md` documents. Returns the new state.

    **An EMPTY worklist still advances the receipt, and that is not a shortcut.** SKILL.md step 7
    advances `validated_head` "only when every eligible child is stamped or marked" — a condition
    that is vacuously satisfied when no child is eligible. So a campaign whose children are all
    `in_progress`, `merged` or probe-confirmed closed is armed by running the skill even though it
    audits nothing, and modelling it any other way would invent a jam the production procedure
    does not have. What an empty worklist must NEVER do is leave the head where it was: that is
    the real round-6 High 3 shape, and `drive_to_open`'s convergence check is what catches it.

    A `pending_disposition` SURVIVES: the skill rediscovers the marker and cannot clear it (that
    is an owner decision by design), and an undisposed child stays UNSTAMPED because a stamped
    child is selectable. Modelling that faithfully is what makes the mixed cases honest.
    """
    numbers = [issue["number"] for issue in state.get("issues", [])]
    work = dl.revalidation_worklist(
        state, observed_head,
        extractions={n: ([], "none") for n in numbers},
        changed_by_child={n: set() for n in numbers},
        issue_state_probe=probe)
    prior = state.get("queue_revalidation") or {}
    prior_children = prior.get("children") if isinstance(prior, dict) else {}
    prior_children = prior_children if isinstance(prior_children, dict) else {}
    audited = {}
    for item in work:
        number = item["number"]
        was = prior_children.get(str(number))
        # A pending marker SURVIVES an audit: the skill rediscovers it and cannot clear it.
        pending = was.get("pending_disposition") if isinstance(was, dict) else None
        audited[number] = _record(to_sha=observed_head, pending=pending)
    # The receipt itself is written by the PRODUCTION helper, not re-implemented here. A harness
    # that models the procedure in its own words tests the model, not the product — and the
    # drop/clear rules are exactly where this issue kept going wrong.
    return dl.rebuild_receipt(state, observed_head, audited)


def _apply_remedy(state, exc, observed_head, probe):
    """Execute what the refusal tells the operator to do. Returns ``(new state, actions taken)``.

    **Dispatches on the STRUCTURAL `remedy` attribute, never on the message text (round-9 High
    3).** The old version substring-matched `revalidate-children`, which is corruption-controlled
    — a receipt whose `version` field held that literal string produced a refusal naming nothing
    runnable, and this harness scored it recoverable. The guard and its test shared one blind
    spot, so text matching is gone from both sides.
    """
    message = str(exc)
    declared = getattr(exc, "remedy", None)
    if declared not in {"owner", "revalidate", "both"}:
        raise AssertionError(
            f"the refusal declares no structural remedy ({declared!r}), so an operator has "
            f"nothing to act on:\n  {message}")
    actions = set()
    new = state
    if declared in {"owner", "both"}:
        outcomes = _STATUS_RE.findall(message)
        if not outcomes:
            raise AssertionError(
                f"remedy {declared!r} promises an owner write-back but the message prints no "
                f"record-child-outcome command:\n  {message}")
        for number, status in outcomes:
            new = dl.record_child_outcome(new, int(number), status.split("|")[0])
        actions.add("owner")
    if declared in {"revalidate", "both"}:
        new = _simulate_revalidate_children(new, observed_head, probe)
        actions.add("revalidate")
    return new, actions


def drive_to_open(state, observed_head=HEAD, probe=None, max_steps=3):
    """Apply the printed remedy until the gate stops refusing. Returns the steps taken.

    `max_steps` is 3 rather than 1 because a genuinely two-part state (an owner decision AND a
    stale head) legitimately needs two actions — but the message must name BOTH, which is round
    8's Medium 1. A state needing more than three is a remedy chain no operator would follow.
    """
    steps = []
    disclosed = None
    for _ in range(max_steps):
        try:
            dl.next_ready_issue(state, observed_head=observed_head, issue_state_probe=probe)
            return steps
        except dl.DriverStateError as exc:
            steps.append(str(exc))
            state, actions = _apply_remedy(state, exc, observed_head, probe)
            # **Every action must have been DISCLOSED by the FIRST refusal (round-9 Medium 1).**
            # Bounding the chain at 3 proved only that it converges — a first message naming one
            # remedy, followed by a second naming another, converged happily and reproduced round
            # 8's Medium 1 defect while this guard stayed green. A remedy delivered one
            # undisclosed step at a time is the same failure as a remedy that does nothing; the
            # operator walks away after the first one.
            if disclosed is None:
                disclosed = {"owner", "revalidate"} if getattr(exc, "remedy", None) == "both" \
                    else {getattr(exc, "remedy", None)}
            elif not actions <= disclosed:
                raise AssertionError(
                    f"the first refusal disclosed {sorted(disclosed)} but a later step required "
                    f"{sorted(actions - disclosed)}. Chain:\n  " + "\n  ".join(steps)) from exc
    try:
        dl.next_ready_issue(state, observed_head=observed_head, issue_state_probe=probe)
    except dl.DriverStateError as exc:
        raise AssertionError(
            f"the gate never opened after {max_steps} prescribed remedies. Chain:\n  "
            + "\n  ".join(steps) + f"\nstill refusing with:\n  {exc}") from exc
    return steps


# ---------------------------------------------------------------------------------------------
# The matrix. One child, every combination of the dimensions that reach a different gate branch.
# ---------------------------------------------------------------------------------------------
_STATUSES = ["queued", "pr_open", "in_progress", "merged", "deferred", "abandoned"]
# `"abc"` is a MALFORMED stamp, and it is reachable: `queue.schema.json` constrains
# `validated_against` to a string and nothing more, so a hand-edited or half-written state really
# does carry these. Round 3 shipped a jam on exactly this value.
_STAMPS = [None, OLD, HEAD, "abc"]
_RECORDS = ["absent", "current", "stale", "malformed", "pending_current", "pending_stale",
            "not_a_dict", "missing_fields"]
# `"not-a-sha"` is the Step-11 finding-3 receipt: schema-valid, semantically impossible.
_RECEIPT_HEADS = [None, OLD, HEAD, "not-a-sha"]
_PROBES = [None, "confirmed_merged", "confirmed_abandoned", "unknown", "garbage"]


def _build(status, stamp, record_kind, receipt_head):
    issue = {"number": 1, "status": status}
    if stamp is not None:
        issue["validated_against"] = stamp
    state = {"version": 1, "campaign": "epic-756", "epic": 756, "project": "rawgentic",
             "generation": 1, "issues": [issue], "base_default_branch_sha": OLD}
    if receipt_head is None:
        return state
    children = {}
    if record_kind == "current":
        children["1"] = _record(to_sha=receipt_head)
    elif record_kind == "stale":
        children["1"] = _record(to_sha=OLD)
    elif record_kind == "malformed":
        children["1"] = _record(to_sha=receipt_head, malformed=True)
    elif record_kind == "pending_current":
        children["1"] = _record(to_sha=receipt_head, pending="issue_obsolete")
    elif record_kind == "pending_stale":
        children["1"] = _record(to_sha=OLD, pending="issue_obsolete")
    elif record_kind == "not_a_dict":
        children["1"] = "evidence"
    elif record_kind == "missing_fields":
        children["1"] = {"body_hash": BODY_HASH, "to_sha": receipt_head}
    state["queue_revalidation"] = {"version": 1, "extractor_version": 1,
                                   "validated_head": receipt_head, "children": children}
    return state


def _every_state():
    for status, stamp, record_kind, receipt_head, probe in itertools.product(
            _STATUSES, _STAMPS, _RECORDS, _RECEIPT_HEADS, _PROBES):
        label = (f"{status}-stamp_{stamp and stamp[:4]}-{record_kind}-"
                 f"receipt_{receipt_head and receipt_head[:4]}-probe_{probe}")
        yield label, _build(status, stamp, record_kind, receipt_head), probe


class TestEveryRefusalIsRecoverable:
    """**The property.** Every state either passes the gate or carries a remedy that clears it.

    Not one of the 3840 may leave an operator holding a refusal with nothing to run.

    **Deliberately ONE test that loops, not 3840 parametrized cases.** The states are generated,
    so parametrizing them would add ~3800 to the suite total and make a `Suite old→new` delta
    that this repo reads as a coverage signal report a coverage change that did not happen. It
    collects EVERY failing state before reporting, rather than stopping at the first — which is
    the property that matters here, since the whole point is to see a defect CLASS at once rather
    than one instance per round."""

    def test_the_printed_remedy_opens_the_gate(self):
        failures = []
        checked = 0
        for label, state, probe in _every_state():
            checked += 1
            callable_probe = (lambda _n, _p=probe: _p) if probe else None
            try:
                drive_to_open(state, HEAD, callable_probe)
            except AssertionError as exc:
                failures.append(f"{label}: {exc}")
            except Exception as exc:                                    # noqa: BLE001
                # A remedy that CRASHES is a jam too — 810 states did exactly that until
                # `rebuild_receipt` learned to clear an unusable stamp.
                failures.append(f"{label}: the remedy raised "
                                f"{type(exc).__name__}: {exc}")
        assert checked == 3840, f"the matrix generated {checked} states, not 3840"
        assert not failures, (f"{len(failures)}/{checked} states leave the operator stuck:\n"
                              + "\n".join(failures[:25])
                              + (f"\n… and {len(failures) - 25} more" if len(failures) > 25 else ""))


class TestTheMatrixIsNotVacuous:
    """A sweep that refuses nothing proves nothing. These pin that the matrix really does exercise
    the refusal paths — the failure mode where a harness passes because it never fired."""

    def test_the_matrix_contains_states_that_actually_refuse(self):
        refused = 0
        for status, stamp, record_kind, receipt_head, probe in itertools.product(
                _STATUSES, _STAMPS, _RECORDS, _RECEIPT_HEADS, _PROBES):
            state = _build(status, stamp, record_kind, receipt_head)
            callable_probe = (lambda _n, _p=probe: _p) if probe else None
            try:
                dl.next_ready_issue(state, observed_head=HEAD, issue_state_probe=callable_probe)
            except dl.DriverStateError:
                refused += 1
        assert refused > 1500, f"only {refused}/3840 states refuse — the matrix is not " \
                               "exercising the gate"

    def test_a_remedy_that_does_nothing_is_caught(self):
        """The harness's own sabotage check: if `_apply_remedy` returned the state unchanged,
        `drive_to_open` must FAIL rather than pass quietly."""
        state = _build("queued", None, "absent", None)
        with pytest.raises(AssertionError, match="never opened"):
            _no_op = lambda s, _e, _h, _p: (s, set())               # noqa: E731
            saved, globals()["_apply_remedy"] = _apply_remedy, _no_op
            try:
                drive_to_open(state, HEAD, None)
            finally:
                globals()["_apply_remedy"] = saved

    def test_an_empty_worklist_still_arms_the_campaign(self):
        """A campaign with nothing eligible — every child merged, or in flight — must still be
        armable, because the head clause refuses it unconditionally. If running the skill could
        not advance the head here, the gate would be permanently closed on exactly the mid-child
        handoff it exists to serve (the current child is `in_progress`, so nothing is eligible)."""
        state = {"version": 1, "campaign": "c", "epic": 1, "project": "p", "generation": 1,
                 "issues": [{"number": 1, "status": "merged"}]}
        armed = _simulate_revalidate_children(state, HEAD)
        assert armed["queue_revalidation"]["validated_head"] == HEAD
        dl.next_ready_issue(armed, observed_head=HEAD)


class TestTheOwnerChoiceIsRealOnBothBranches:
    """`deferred|abandoned` is presented as a genuine choice, so BOTH must clear the gate. The
    matrix executes only the first; this covers the other."""

    @pytest.mark.parametrize("chosen", ["deferred", "abandoned"])
    def test_either_owner_choice_clears_the_gate(self, chosen):
        state = _build("queued", None, "pending_current", HEAD)
        cleared = dl.record_child_outcome(state, 1, chosen)
        dl.next_ready_issue(cleared, observed_head=HEAD)


class TestTwoChildStatesWhereOneChildUnblocksAnother:
    """The round-6 High 2 question, made systematic: *what does this object let SOMEBODY ELSE do?*

    A child that cannot be selected can still satisfy another child's dependency, so a jam can
    live entirely in the relationship between two children and never appear in a single-child
    sweep."""

    def _pair(self, first_status, first_record, dependent_stamp=HEAD, receipt_head=HEAD):
        children = {}
        if first_record == "pending":
            children["1"] = _record(to_sha=receipt_head, pending="issue_obsolete")
        elif first_record == "current":
            children["1"] = _record(to_sha=receipt_head)
        if dependent_stamp == receipt_head:
            children["2"] = _record(to_sha=receipt_head)
        second = {"number": 2, "status": "queued", "depends_on": [1]}
        if dependent_stamp is not None:
            second["validated_against"] = dependent_stamp
        return {"version": 1, "campaign": "epic-756", "epic": 756, "project": "rawgentic",
                "generation": 1, "base_default_branch_sha": OLD,
                "issues": [{"number": 1, "status": first_status}, second],
                "queue_revalidation": {"version": 1, "extractor_version": 1,
                                       "validated_head": receipt_head, "children": children}}

    @pytest.mark.parametrize("first_status", _STATUSES)
    @pytest.mark.parametrize("first_record", ["absent", "current", "pending"])
    @pytest.mark.parametrize("probe", [None, "confirmed_merged"])
    def test_a_blocking_sibling_never_jams_its_dependent(self, first_status, first_record, probe):
        state = self._pair(first_status, first_record)
        callable_probe = (lambda _n: probe) if probe else None
        drive_to_open(state, HEAD, callable_probe)

    def test_a_stamped_obsolete_prerequisite_is_never_silently_selectable(self):
        """The safety twin of the whole file. Recoverability must never be bought by letting an
        obsolete child through — round 8's High 2 fix swallows a validator refusal, and this is
        what proves the swallow did not open a hole."""
        state = self._pair("queued", "pending")
        state["issues"][0]["validated_against"] = HEAD
        with pytest.raises(dl.QueueRevalidationRequired):
            dl.next_ready_issue(state, observed_head=HEAD)


class TestTheSwallowedValidatorRefusalOpensNoHole:
    """**The invariant behind round 8's High 2 fix, pinned directly.**

    `_refuse_unrevalidated_queue` swallows the `QueueRevalidationRequired` that
    `validate_queue_revalidation` raises for the stamped-plus-pending shape, so that the
    probe-aware owner-gate remedy below it can be reached. That is safe ONLY IF the owner-gate
    pass reports a superset of the shapes the validator fires on — otherwise the swallow would
    make an obsolete stamped child selectable, which is the single outcome this gate exists to
    prevent.

    The code carries a `preempted` re-raise as a backstop, and that line is deliberately
    UNREACHABLE while the superset holds — sabotaging it does not turn the suite red, which under
    this repo's own rules makes it a guard keyed to a shape that cannot occur. So the superset
    claim is proved HERE instead, by sweep, rather than left as a comment asserting itself."""

    def test_every_state_the_validator_refuses_is_also_refused_by_the_gate(self):
        checked = leaks = 0
        examples = []
        for label, state, probe in _every_state():
            try:
                dl.validate_queue_revalidation(state)
                continue                       # the validator is happy; nothing to prove here
            except dl.QueueRevalidationRequired:
                pass
            except dl.DriverStateError:
                continue                       # structural errors propagate; not the swallowed kind
            checked += 1
            callable_probe = (lambda _n, _p=probe: _p) if probe else None
            try:
                dl.next_ready_issue(state, observed_head=HEAD, issue_state_probe=callable_probe)
            except dl.DriverStateError as exc:
                # **The refusal must come from the OWNER-GATE pass, not from the `preempted`
                # re-raise (round-9 Medium 2).** The first version of this test accepted any
                # `DriverStateError`, and a reviewer proved it passed against a surrogate gate
                # that only re-ran the validator and never executed the superset pass at all —
                # so the test asserting the invariant did not test the invariant. The owner-gate
                # pass is identified by the structured `outstanding` list it attaches; the
                # validator's own refusal carries none.
                if getattr(exc, "outstanding", None) is None:
                    leaks += 1
                    examples.append(f"{label} (refused by the PREEMPTED validator, not the "
                                    f"owner-gate pass)")
                continue
            leaks += 1
            examples.append(f"{label} (SELECTABLE)")
        assert checked > 0, "no state reached the swallowed branch — this sweep proves nothing"
        assert not leaks, (f"{leaks}/{checked} states are refused by the receipt validator but "
                           f"SELECTABLE through the gate: {examples[:10]}")


def _live_owner_gate(state):
    """Issue numbers whose durable status is undisposed AND whose receipt carries a pending
    marker under ANY key spelling. Deliberately does NOT use `children.get(str(n))` — that is
    the lookup round-9 H2 proved unreliable, so a safety property must not inherit it."""
    reval = state.get("queue_revalidation")
    children = reval.get("children") if isinstance(reval, dict) else None
    if not isinstance(children, dict):
        return set()
    marked = {}
    for key, record in children.items():
        if not isinstance(record, dict):
            continue
        pending = record.get("pending_disposition")
        if not (isinstance(pending, str) and pending):
            continue
        try:
            marked[int(str(key))] = pending
        except (TypeError, ValueError):
            continue
    return {issue["number"] for issue in state.get("issues", [])
            if issue["number"] in marked
            and issue.get("status") not in dl._DISPOSED_STATUSES}


class TestTheGateNeverOpensOverALiveOwnerDecision:
    """**The SAFETY half of the sweep — round 9 H1 exists because this was missing.**

    The recoverability sweep asks only "does the gate OPEN?". Round 9 found a fix that opened it
    by DESTROYING the owner's obligation: `rebuild_receipt` dropped a `pending_disposition` and
    the dependent was released with nobody having decided anything. The recoverability sweep
    scored that as a PASS, because the gate did open.

    A recoverability property with no safety counterpart actively rewards laundering. Two lenses
    found this independently and the sweep found neither, which is a design error in the sweep.

    The property: while a durably-undisposed child carries a pending marker, NOTHING an operator
    can run — least of all the prescribed remedy — may make the queue selectable. Only a durable
    owner outcome retires it."""

    def _marked(self, first_status="pr_open", record_head=HEAD, malformed=False, key="1"):
        record = _record(to_sha=record_head, pending="issue_obsolete", malformed=malformed)
        return {"version": 1, "campaign": "c", "epic": 756, "project": "p", "generation": 1,
                "base_default_branch_sha": OLD,
                "issues": [{"number": 1, "status": first_status},
                           {"number": 2, "status": "queued", "validated_against": HEAD,
                            "depends_on": [1]}],
                "queue_revalidation": {"version": 1, "extractor_version": 1,
                                       "validated_head": HEAD,
                                       "children": {key: record, "2": _record()}}}

    @pytest.mark.parametrize("policy", ["merged", "pr_open"])
    @pytest.mark.parametrize("record_head,malformed,label", [
        (HEAD, False, "valid current marker"),
        (OLD, False, "marker on a stale-head record"),
        (HEAD, True, "marker on a malformed record"),
    ])
    def test_only_an_owner_write_back_may_retire_the_obligation(
            self, policy, record_head, malformed, label):
        """Recording an outcome legitimately retires the marker — that IS the remedy. What must
        never retire it is the revalidation skill, which is a machine auditing bodies. So the
        chain is walked and each step is judged by WHICH remedy it ran."""
        state = self._marked(record_head=record_head, malformed=malformed)
        assert _live_owner_gate(state) == {1}, label
        for _ in range(3):
            try:
                dl.next_ready_issue(state, deps_satisfied_by=policy, observed_head=HEAD)
            except dl.DriverStateError as exc:
                message = str(exc)
                owner_write_back = bool(_STATUS_RE.search(message))
                try:
                    state, _actions = _apply_remedy(state, exc, HEAD, None)
                except (AssertionError, dl.DriverStateError):
                    return                     # refused to launder: correct
                if owner_write_back:
                    return                     # the owner decided; the obligation is properly gone
                assert _live_owner_gate(state) == {1}, (
                    f"{label} under {policy}: the revalidation remedy RETIRED a live owner "
                    f"decision — no owner recorded an outcome for #1")
                continue
            raise AssertionError(
                f"{label} under {policy}: the queue became selectable while #1 still carries a "
                f"live pending_disposition and its durable status is undisposed")

    @pytest.mark.parametrize("key", ["1", "01", "001"])
    def test_the_marker_holds_however_its_receipt_key_is_spelled(self, key):
        """Round 9 H2: `"01"` passed validation but every consumer looked up `"1"`."""
        state = self._marked(key=key)
        with pytest.raises(dl.DriverStateError):
            dl.next_ready_issue(state, deps_satisfied_by="pr_open", observed_head=HEAD)

    def test_an_audit_cannot_stand_in_for_the_owners_decision(self):
        """A clean audited record must not overwrite a live marker — that is a machine closing a
        child, which this design forbids everywhere else."""
        state = self._marked()
        try:
            rebuilt = dl.rebuild_receipt(state, HEAD, {1: _record()})
        except dl.DriverStateError:
            return                             # refused: correct
        assert _live_owner_gate(rebuilt) == {1}, \
            "an audited record replaced a live pending_disposition"

    def test_recording_the_outcome_IS_what_retires_it(self):
        """The negative twin — the gate must not become impossible to clear."""
        cleared = dl.record_child_outcome(self._marked(), 1, "deferred")
        assert _live_owner_gate(cleared) == set()
        dl.next_ready_issue(cleared, deps_satisfied_by="pr_open", observed_head=HEAD)
