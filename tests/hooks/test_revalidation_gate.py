"""#840 PR 2 — the enforcement half: the head-and-provenance gate and its propagation.

PR 1 shipped the machinery and enforced NOTHING (`QueueRevalidationRequired` defined but never
raised, `next_ready_issue` unchanged). These tests are the enforcement contract.

**Every case here is built so the guard under test is the ONLY thing that can refuse it.** That
is not stylistic. The previous session found four rows in this issue's own test plan that would
have stayed GREEN under their own sabotage, because each asserted a value some other rule already
forced. The generalisable rule, now in the design doc: *a test that asserts another test exists,
or asserts a value some other rule already forces, is not mutation-sensitive.*

The isolation that matters most, and why it is fiddly: the per-child provenance clause and the
head-comparison clause both refuse the obvious stale-queue state, so a test built the naive way
proves only that ONE of them fired. `TestHeadComparisonInIsolation` therefore constructs a state
whose children are all stamped at the observed head while the RECEIPT still names the old one —
the provenance clause passes, so only the head comparison can refuse it.

Pure functions imported directly per `docs/testing.md:5-8`.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parents[2] / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

import driver_lib as dl  # noqa: E402
import launcher_lib as ll  # noqa: E402

HEAD = "fb9293c630a6e7477a638a07853dfb0846cc9cf5"
OLD = "3d4e1607d2ccb7178956f9afa05ab0dbb0cbe25d"
SENTINEL = "abcdef0123456789abcdef0123456789abcdef01"
BODY_HASH = "9" * 64


def _claim(verdict="holds"):
    return {"kind": "cause", "quoted_from_body": "the cause is a swallowed Enter",
            "checked_against": f"hooks/launcher_lib.py@{HEAD}",
            "evidence": "the send path uses bracketed paste, so the claim no longer holds",
            "verdict": verdict}


def _receipt_child(*, to_sha=HEAD, outcome="still_valid", pending=None, correction=None,
                   extraction="paths", depth="quick", verdict="holds"):
    """One `queue_revalidation.children[<n>]` record, valid per `validate_revalidation_child`."""
    record = {"body_hash": BODY_HASH, "from_sha": OLD, "to_sha": to_sha,
              "extraction": extraction, "depth": depth, "outcome": outcome,
              "claims": [_claim(verdict)], "validated_at": 1_754_000_000}
    if pending is not None:
        record["pending_disposition"] = pending
        record["outcome"] = None
    if correction is not None:
        record["correction_comment"] = correction
    return record


def _iss(number, status="queued", *, validated_against=None, depends_on=None):
    entry = {"number": number, "status": status}
    if validated_against is not None:
        entry["validated_against"] = validated_against
    if depends_on is not None:
        entry["depends_on"] = depends_on
    return entry


def _state(*issues, reval=None, **extra):
    state = {"version": 1, "campaign": "epic-756", "epic": 756, "project": "rawgentic",
             "generation": 1, "issues": list(issues)}
    if reval is not None:
        state["queue_revalidation"] = reval
    state.update(extra)
    return state


def _reval(validated_head=HEAD, children=None):
    return {"version": 1, "extractor_version": 1, "validated_head": validated_head,
            "children": children if children is not None else {}}


# --------------------------------------------------------------------------- #
# AC3b — the in-session gate
# --------------------------------------------------------------------------- #

class TestPerChildProvenanceClause:
    """A child with no provenance must never be handed out, even at an unmoved head.

    Isolation: `validated_head` EQUALS `observed_head`, so the head comparison passes and only
    the per-child clause can refuse. Deleting that clause returns #1 instead of raising.
    """

    def test_an_unstamped_eligible_child_refuses_the_whole_queue(self):
        state = _state(
            _iss(1, validated_against=HEAD),
            _iss(2),                                   # never validated — no provenance at all
            reval=_reval(HEAD, {"1": _receipt_child()}))
        with pytest.raises(dl.QueueRevalidationRequired):
            dl.next_ready_issue(state, observed_head=HEAD)

    def test_a_child_stamped_at_a_stale_head_refuses(self):
        state = _state(
            _iss(1, validated_against=HEAD),
            _iss(2, validated_against=OLD),
            reval=_reval(HEAD, {"1": _receipt_child()}))
        with pytest.raises(dl.QueueRevalidationRequired):
            dl.next_ready_issue(state, observed_head=HEAD)

    def test_a_terminal_child_needs_no_stamp(self):
        """Eligibility is EFFECTIVE `queued`. A merged child carries no provenance and must not
        jam the queue — otherwise every campaign deadlocks on its own history."""
        state = _state(
            _iss(1, "merged"),
            _iss(2, validated_against=HEAD),
            reval=_reval(HEAD, {"2": _receipt_child()}))
        assert dl.next_ready_issue(state, observed_head=HEAD) == 2


class TestHeadComparisonInIsolation:
    """The receipt is stale while every child LOOKS current.

    This is the case the naive test cannot see: children stamped at the observed head satisfy the
    per-child clause, so the head comparison is the only guard left. It is also a real failure —
    stamps advancing without the receipt advancing atomically is exactly what `validated_head`
    exists to prevent.
    """

    def test_a_receipt_behind_the_observed_head_refuses(self):
        state = _state(
            _iss(1, validated_against=HEAD),
            _iss(2, validated_against=HEAD),
            reval=_reval(OLD, {}))                     # receipt still names the OLD head
        with pytest.raises(dl.QueueRevalidationRequired):
            dl.next_ready_issue(state, observed_head=HEAD)

    def test_the_isolation_holds_the_per_child_clause_would_pass(self):
        """Proof the case really is isolated, so this file cannot silently stop testing the head
        clause. Every eligible child's stamp equals the observed head."""
        state = _state(_iss(1, validated_against=HEAD), _iss(2, validated_against=HEAD),
                       reval=_reval(OLD, {}))
        eligible = [i for i in state["issues"] if i["status"] == "queued"]
        assert eligible and all(i["validated_against"] == HEAD for i in eligible)


class TestObsoleteChildIsNeverSelected:
    """`pending_disposition` non-null ⇒ outstanding, regardless of stamps.

    Isolation per the design's own correction: the child is otherwise FULLY stamped and current,
    differing ONLY by `pending_disposition`. The r4 version of this test was blind — the
    unstamped-provenance refusal blocked it anyway, so deleting the obsolete check left it green.
    """

    def test_an_obsolete_marked_child_refuses_the_queue(self):
        state = _state(
            _iss(1, validated_against=HEAD),
            reval=_reval(HEAD, {"1": _receipt_child(pending="issue_obsolete")}))
        with pytest.raises(dl.QueueRevalidationRequired):
            dl.next_ready_issue(state, observed_head=HEAD)

    def test_the_identical_state_without_the_marker_is_selectable(self):
        """The other half of the isolation: same state, marker removed, child comes back. Without
        this, a gate that refused everything would pass the test above."""
        state = _state(
            _iss(1, validated_against=HEAD),
            reval=_reval(HEAD, {"1": _receipt_child()}))
        assert dl.next_ready_issue(state, observed_head=HEAD) == 1


class TestTheLegacyContractIsExplicitNeverSilent:
    """An optional enforcement input is a bypass — this repo has shipped that defect once
    already (`tests/hooks/test_driver_state_write_back.py:304-306`)."""

    def test_a_revalidation_campaign_without_observed_head_raises(self):
        """The discriminator is the STATE, not the argument. A campaign that opted into
        revalidation and is then queried with no observation must refuse, not silently skip."""
        state = _state(_iss(1, validated_against=HEAD),
                       reval=_reval(HEAD, {"1": _receipt_child()}))
        with pytest.raises(dl.DriverStateError):
            dl.next_ready_issue(state, observed_head=None)

    def test_a_pre_840_state_is_byte_identical_without_an_observation(self):
        """Every campaign that predates #840 carries no `queue_revalidation` and must behave
        exactly as it did — this is the regression guard for the whole existing fleet."""
        state = _state(_iss(1, "merged"), _iss(2), _iss(3, depends_on=[2]))
        assert dl.next_ready_issue(state) == 2
        assert dl.next_ready_issue(state, observed_head=None) == 2

    def test_a_fully_revalidated_campaign_still_advances(self):
        """A gate that never opens is not a gate. Two eligible children, both current, receipt
        current: selection proceeds in queue order."""
        state = _state(
            _iss(1, validated_against=HEAD),
            _iss(2, validated_against=HEAD),
            reval=_reval(HEAD, {"1": _receipt_child(), "2": _receipt_child()}))
        assert dl.next_ready_issue(state, observed_head=HEAD) == 1


# --------------------------------------------------------------------------- #
# §4 — `observed_head` must be FRESHLY OBSERVED, not merely supplied
# --------------------------------------------------------------------------- #

class TestObserveHead:
    """The ONLY permitted source of `observed_head`. Both return codes checked — r2 omitted `-C`
    on the fetch, which a reviewer flagged as able to update a different checkout while leaving
    the target stale."""

    @staticmethod
    def _runner(fetch_rc=0, rev_rc=0, rev_out=HEAD + "\n", calls=None):
        def run(argv, *_a, **_kw):
            if calls is not None:
                calls.append(argv)
            rc, out = ((fetch_rc, "") if "fetch" in argv else (rev_rc, rev_out))
            return type("P", (), {"returncode": rc, "stdout": out, "stderr": ""})()
        return run

    def test_it_fetches_then_rev_parses_both_scoped_with_dash_C(self):
        calls = []
        assert ll.observe_head("/repo", runner=self._runner(calls=calls)) == HEAD
        assert calls[0][:4] == ["git", "-C", "/repo", "fetch"], calls[0]
        assert calls[1][:3] == ["git", "-C", "/repo"], calls[1]
        assert "rev-parse" in calls[1]

    def test_a_failed_fetch_raises_rather_than_returning_a_stale_sha(self):
        with pytest.raises(ll.LauncherError):
            ll.observe_head("/repo", runner=self._runner(fetch_rc=1))

    def test_a_failed_rev_parse_raises(self):
        with pytest.raises(ll.LauncherError):
            ll.observe_head("/repo", runner=self._runner(rev_rc=128))

    @pytest.mark.parametrize("bad", ["", "\n", "fb9293c6\n", "z" * 40 + "\n", HEAD + "0\n"])
    def test_a_non_sha_stdout_raises(self, bad):
        """A rc-0 command with unreadable output must not become provenance."""
        with pytest.raises(ll.LauncherError):
            ll.observe_head("/repo", runner=self._runner(rev_out=bad))


class TestTheObservedHeadDataflow:
    """Assert the DATAFLOW, not that a source test exists.

    The r4 version of this row asserted a source-level test EXISTS, so deleting that test made it
    vacuous. This patches `observe_head` to a sentinel and proves the sentinel is what reaches the
    comparison — a cached value, a literal, or a state-derived head fails.
    """

    def test_the_sentinel_from_observe_head_reaches_the_refusal(self, monkeypatch):
        state = _state(_iss(1, validated_against=HEAD),
                       reval=_reval(HEAD, {"1": _receipt_child()}))
        monkeypatch.setattr(ll, "observe_head", lambda *_a, **_kw: SENTINEL)
        result = ll.resume_prompt_for_state(state, project="rawgentic", repo_root="/repo")
        assert result["outcome"] == "revalidation_required", result
        # The state is CURRENT at HEAD. Only a head observed as SENTINEL can refuse it, so this
        # is proof the wrapper's return value — not `validated_head` — fed the comparison.
        assert SENTINEL in repr(result), result

    def test_the_same_state_passes_when_the_observation_matches(self, monkeypatch):
        state = _state(_iss(1, validated_against=HEAD),
                       reval=_reval(HEAD, {"1": _receipt_child()}))
        monkeypatch.setattr(ll, "observe_head", lambda *_a, **_kw: HEAD)
        result = ll.resume_prompt_for_state(state, project="rawgentic", repo_root="/repo")
        assert result["outcome"] == "ready", result


# --------------------------------------------------------------------------- #
# §6 — propagation that does not collapse to None
# --------------------------------------------------------------------------- #

class TestRefusalPropagation:
    """`None` already means "nothing ready" and is reported as *the epic finished*. A stale queue
    announced as completion is the worst available failure, so it must be representable."""

    def test_fresh_session_handoff_reports_revalidation_required_with_a_worklist(self):
        state = _state(_iss(1, validated_against=HEAD), _iss(2),
                       reval=_reval(HEAD, {"1": _receipt_child()}))
        disp = dl.fresh_session_handoff(
            state, mode=dl.FRESH_SESSION_MODE, project="rawgentic", observed_head=HEAD)
        assert disp["outcome"] == "revalidation_required", disp
        assert [w["number"] for w in disp["worklist"]] == [2], disp

    def test_resume_prompt_for_state_returns_a_result_object_never_a_bare_none(self, monkeypatch):
        state = _state(_iss(1, validated_against=HEAD), _iss(2),
                       reval=_reval(HEAD, {"1": _receipt_child()}))
        monkeypatch.setattr(ll, "observe_head", lambda *_a, **_kw: HEAD)
        result = ll.resume_prompt_for_state(state, project="rawgentic", repo_root="/repo")
        assert result is not None
        assert result["outcome"] == "revalidation_required"
        assert result.get("prompt") is None

    def test_a_blocked_campaign_is_still_distinguishable_from_a_refusal(self):
        """The refusal must not swallow the outcomes that already existed."""
        state = _state(_iss(1, "deferred"), _iss(2, "abandoned"))
        disp = dl.fresh_session_handoff(state, mode=dl.FRESH_SESSION_MODE, project="rawgentic")
        assert disp["outcome"] == "blocked", disp


# --------------------------------------------------------------------------- #
# the CLI, black-box via subprocess per `docs/testing.md:5-8`
# --------------------------------------------------------------------------- #

CLI = HOOKS_DIR / "launcher_lib.py"


def _git(cwd, *argv):
    proc = subprocess.run(["git", "-C", str(cwd), *argv], capture_output=True, text=True,
                          check=False)
    assert proc.returncode == 0, f"git {argv}: {proc.stderr}"
    return proc.stdout.strip()


def _repo_with_origin(tmp_path):
    """A real repository with a real `origin/main`, so `observe_head` is exercised against git
    rather than a fake runner. Local paths only — no network."""
    origin = tmp_path / "origin.git"
    work = tmp_path / "work"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(origin)],
                   capture_output=True, check=True)
    subprocess.run(["git", "init", "-b", "main", str(work)], capture_output=True, check=True)
    _git(work, "config", "user.email", "t@example.com")
    _git(work, "config", "user.name", "t")
    (work / "f.txt").write_text("one\n", encoding="utf-8")
    _git(work, "add", "f.txt")
    _git(work, "commit", "-m", "one")
    _git(work, "remote", "add", "origin", str(origin))
    _git(work, "push", "-u", "origin", "main")
    return work, _git(work, "rev-parse", "HEAD")


def _handoff_argv(state_path, tmp_path, project_root):
    return ["handoff", "--driver-state", str(state_path), "--anchor-pane", "w1:p1",
            "--name", "child4", "--project-root", str(project_root), "--project", "rawgentic",
            "--cwd", str(project_root), "--registry", "/reg.jsonl",
            "--transcript-dir", str(tmp_path), "--goal-condition", "keep going",
            "--launch-mode", "fresh", "--herdr-mode", "herdr"]


def _write_state(tmp_path, state):
    path = tmp_path / "campaign.json"
    path.write_text(json.dumps(state), encoding="utf-8")
    return path


class TestHandoffCLIRefusals:
    def test_an_unobservable_head_refuses_fail_closed(self, tmp_path):
        """`--project-root` is not a repository, so `observe_head` cannot confirm a head. The
        command must refuse rather than proceed on an unconfirmed one."""
        state = _state(_iss(1, validated_against=HEAD),
                       reval=_reval(HEAD, {"1": _receipt_child()}),
                       session_mode=dl.FRESH_SESSION_MODE)
        path = _write_state(tmp_path, state)
        not_a_repo = tmp_path / "elsewhere"
        not_a_repo.mkdir()
        proc = subprocess.run([sys.executable, str(CLI),
                               *_handoff_argv(path, tmp_path, not_a_repo)],
                              capture_output=True, text=True, check=False)
        assert proc.returncode == 5, (proc.returncode, proc.stdout, proc.stderr)
        assert "refusing to observe the head" in proc.stderr, proc.stderr

    def test_a_stale_queue_gets_its_own_rc_not_the_complete_rc(self, tmp_path):
        """The rc is the whole point (§6): reported through the generic "no handoff" branch, a
        stale queue would be indistinguishable from a finished epic."""
        work, head = _repo_with_origin(tmp_path)
        state = _state(_iss(1, validated_against=head), _iss(2),
                       reval=_reval(head, {"1": _receipt_child(to_sha=head)}),
                       session_mode=dl.FRESH_SESSION_MODE)
        path = _write_state(tmp_path, state)
        proc = subprocess.run([sys.executable, str(CLI), *_handoff_argv(path, tmp_path, work)],
                              capture_output=True, text=True, check=False)
        assert proc.returncode == 6, (proc.returncode, proc.stdout, proc.stderr)
        payload = json.loads(proc.stdout)
        assert payload["outcome"] == "revalidation_required"
        assert payload["observed_head"] == head, payload
        assert [w["number"] for w in payload["worklist"]] == [2], payload

    def test_a_receiptless_campaign_says_enforcement_is_off_rather_than_failing_cli(self, tmp_path):
        """Every pre-#840 campaign must keep working. The note is mandatory — a silent
        fail-open is how a real miss comes to look like a deliberate no-op (#695 M2's lesson)."""
        state = _state(_iss(1), session_mode=dl.FRESH_SESSION_MODE)
        path = _write_state(tmp_path, state)
        not_a_repo = tmp_path / "elsewhere"
        not_a_repo.mkdir()
        proc = subprocess.run([sys.executable, str(CLI),
                               *_handoff_argv(path, tmp_path, not_a_repo)],
                              capture_output=True, text=True, check=False)
        assert proc.returncode != 5, (proc.returncode, proc.stderr)
        assert "enforcement is OFF" in proc.stderr, proc.stderr


# --------------------------------------------------------------------------- #
# §7 — the ladder rung AND its producer. The rung alone is a landmine, not a no-op.
# --------------------------------------------------------------------------- #

class TestTheRungExistsAndIsFirst:
    def test_queue_revalidated_leads_the_mid_child_ladder(self):
        """FIRST, because a successor handed a stale queue has already read the wrong issue
        bodies by the time any later rung could object."""
        assert [s["step"] for s in ll.mid_child_verification_steps()][0] == "queue_revalidated"

    def test_the_launch_ladder_does_not_carry_it(self):
        """The ad-hoc handoff uses the launch ladder. Adding the rung there would refuse every
        ad-hoc handoff, which has no campaign to revalidate."""
        assert "queue_revalidated" not in [s["step"] for s in ll.handoff_verification_steps()]

    def test_an_unreported_rung_stays_fail_closed(self):
        """The reason the producer is mandatory. Every OTHER rung passes and teardown is still
        refused, naming this one — so shipping the rung without a producer would jam every
        mid-child handoff and every teardown."""
        results = {s["step"]: True for s in ll.mid_child_verification_steps()
                   if s["step"] != "queue_revalidated"}
        allowed, reason = ll.teardown_allowed(results,
                                              steps=ll.mid_child_verification_steps())
        assert allowed is False
        assert "queue_revalidated" in reason, reason


class TestTheProducer:
    """The rung's result is produced by the LAUNCHER reading the durable receipt — never by a
    caller-supplied verification result. An agent asserting its own homework is the vacuous pass
    this whole issue exists to eliminate (peer-consult finding)."""

    def test_a_missing_campaign_context_fails_closed(self):
        passed, reason = ll.produce_queue_revalidated(None)
        assert passed is False
        assert "no campaign context" in reason

    def test_a_half_supplied_context_fails_closed(self):
        passed, _ = ll.produce_queue_revalidated({"driver_state_path": "/x"})
        assert passed is False

    def test_an_unreadable_state_file_fails_closed(self, tmp_path):
        passed, reason = ll.produce_queue_revalidated(
            {"driver_state_path": str(tmp_path / "missing.json"), "repo_root": str(tmp_path)})
        assert passed is False
        assert "could not read" in reason or "does not hold" in reason

    def test_a_receiptless_campaign_passes_with_the_reason_recorded(self, tmp_path):
        """A stated limit, not an oversight: every campaign predating #840 has no receipt, and
        the mid-child ladder is used only by campaign paths, so failing them would break every
        existing mid-child handoff — the same landmine in another shape."""
        path = _write_state(tmp_path, _state(_iss(1)))
        passed, reason = ll.produce_queue_revalidated(
            {"driver_state_path": str(path), "repo_root": str(tmp_path)})
        assert passed is True
        assert "OFF" in reason, reason

    def test_a_stale_receipt_fails_against_a_freshly_observed_head(self, tmp_path):
        """A fully SELF-CONSISTENT receipt that is simply behind: #1 is stamped at OLD and the
        receipt attests OLD with matching evidence. Nothing about it is malformed — the only thing
        wrong is that `main` has moved, which is precisely what this gate is for."""
        work, head = _repo_with_origin(tmp_path)
        path = _write_state(tmp_path, _state(
            _iss(1, validated_against=OLD),
            reval=_reval(OLD, {"1": _receipt_child(to_sha=OLD)})))
        passed, reason = ll.produce_queue_revalidated(
            {"driver_state_path": str(path), "repo_root": str(work)})
        assert passed is False
        assert head in reason, reason
        assert head != OLD

    def test_a_current_receipt_passes_against_a_freshly_observed_head(self, tmp_path):
        work, head = _repo_with_origin(tmp_path)
        path = _write_state(tmp_path, _state(
            _iss(1, validated_against=head),
            reval=_reval(head, {"1": _receipt_child(to_sha=head)})))
        passed, reason = ll.produce_queue_revalidated(
            {"driver_state_path": str(path), "repo_root": str(work)})
        assert passed is True, reason
        assert head in reason

    def test_an_unobservable_head_fails_closed_even_with_a_valid_receipt(self, tmp_path):
        """The receipt looks perfect; the head cannot be confirmed. Fail closed — what this
        gates is an irreversible teardown."""
        not_a_repo = tmp_path / "elsewhere"
        not_a_repo.mkdir()
        path = _write_state(tmp_path, _state(
            _iss(1, validated_against=HEAD),
            reval=_reval(HEAD, {"1": _receipt_child()})))
        passed, reason = ll.produce_queue_revalidated(
            {"driver_state_path": str(path), "repo_root": str(not_a_repo)})
        assert passed is False
        assert "refusing to observe the head" in reason


class TestPerformHandoffRefusesBeforeAnyEffect:
    """The strongest form of the gate: with a stale queue, no pane is ever split.

    A refusal that has already spawned a successor leaves an orphan nobody owns, so the position
    of the check — after argument validation, before the first effect — is the contract, not an
    implementation detail.
    """

    @staticmethod
    def _runner(calls):
        def run(argv, timeout=180):
            calls.append(argv)
            if argv[:3] == ["herdr", "pane", "list"]:
                return type("P", (), {"returncode": 0, "stderr": "", "stdout": json.dumps(
                    {"result": {"panes": [{"pane_id": "w1:p1"}]}})})()
            if argv[:3] == ["herdr", "pane", "split"]:
                return type("P", (), {"returncode": 0, "stderr": "", "stdout": json.dumps(
                    {"result": {"pane_id": "w1:p2"}})})()
            return type("P", (), {"returncode": 0, "stdout": "", "stderr": ""})()
        return run

    def _handoff(self, tmp_path, state_path, repo_root, calls):
        (tmp_path / "t").mkdir(exist_ok=True)
        return ll.perform_handoff(
            anchor_pane="w1:p1", cwd=str(tmp_path), project_root=str(tmp_path), name="succ",
            expected_project="rawgentic", goal_condition="keep going",
            resume_prompt="marker-x — do the thing",
            registry_path=str(tmp_path / "reg.jsonl"), transcript_dir=str(tmp_path / "t"),
            runner=self._runner(calls), sleeper=lambda _s: None, read_text=lambda _p: "",
            prompt_marker="marker-x", steps=ll.mid_child_verification_steps(),
            teardown=False, on_successor=lambda _p, _s: True,
            campaign_context={"driver_state_path": str(state_path),
                              "repo_root": str(repo_root)})

    def test_a_stale_queue_refuses_without_splitting_a_pane(self, tmp_path):
        work, _head = _repo_with_origin(tmp_path)
        state_path = _write_state(tmp_path, _state(
            _iss(1, validated_against=OLD), reval=_reval(OLD, {})))
        calls = []
        out = self._handoff(tmp_path, state_path, work, calls)
        assert out["failed_step"] == "queue_revalidated", out
        assert out["results"]["queue_revalidated"] is False
        assert not any(a[:3] == ["herdr", "pane", "split"] for a in calls), \
            "a refusal that has already split a pane leaves an orphan nobody owns"

    def test_a_missing_campaign_context_refuses_rather_than_skipping_the_check(self, tmp_path):
        """Deviation from the design's literal wording, recorded deliberately.

        The design said an absent `campaign_context` means "the ad-hoc case, check does not run".
        Verified at source: NO path uses the mid-child ladder without campaign state — the ad-hoc
        handoff (`_cmd_ad_hoc_handoff`) passes no `steps=` and gets the three-rung launch ladder,
        which carries no such rung. So "absent" on THIS ladder can only be a campaign caller that
        forgot the argument, and passing the rung for it would be a silent skip of the whole gate.
        Failing toward less scrutiny is the one direction this design must never take, so absence
        refuses here as well as being pinned by a source-level call-site test.
        """
        (tmp_path / "t").mkdir(exist_ok=True)
        calls = []
        out = ll.perform_handoff(
            anchor_pane="w1:p1", cwd=str(tmp_path), project_root=str(tmp_path), name="succ",
            expected_project="rawgentic", goal_condition="keep going",
            resume_prompt="marker-x — do the thing",
            registry_path=str(tmp_path / "reg.jsonl"), transcript_dir=str(tmp_path / "t"),
            runner=self._runner(calls), sleeper=lambda _s: None, read_text=lambda _p: "",
            prompt_marker="marker-x", steps=ll.mid_child_verification_steps(),
            teardown=False, on_successor=lambda _p, _s: True)
        assert out["failed_step"] == "queue_revalidated", out
        assert not any(a[:3] == ["herdr", "pane", "split"] for a in calls)

    def test_a_bad_argument_still_raises_rather_than_being_masked(self, tmp_path):
        """The check sits AFTER caller-mismatch validation on purpose: a malformed argument is a
        caller bug and must keep raising, not be reported as a queue refusal."""
        work, _head = _repo_with_origin(tmp_path)
        state_path = _write_state(tmp_path, _state(
            _iss(1, validated_against=OLD), reval=_reval(OLD, {})))
        (tmp_path / "t").mkdir(exist_ok=True)
        with pytest.raises(ll.LauncherError):
            ll.perform_handoff(
                anchor_pane="w1:p1", cwd=str(tmp_path), project_root=str(tmp_path), name="succ",
                expected_project="rawgentic", goal_condition="keep going",
                resume_prompt="no marker here",           # prompt_marker absent from the prompt
                registry_path=str(tmp_path / "reg.jsonl"), transcript_dir=str(tmp_path / "t"),
                runner=self._runner([]), sleeper=lambda _s: None, read_text=lambda _p: "",
                prompt_marker="marker-x", steps=ll.mid_child_verification_steps(),
                teardown=False, on_successor=lambda _p, _s: True,
                campaign_context={"driver_state_path": str(state_path),
                                  "repo_root": str(work)})


# --------------------------------------------------------------------------- #
# §8 — `handoff_pending.queue`: two producers, one exact claim-time consumer
# --------------------------------------------------------------------------- #

def _current_campaign():
    """A campaign that is fully revalidated at HEAD, so the gate opens and a handoff can be
    written. Two children, so ORDER is observable."""
    return _state(
        _iss(1, validated_against=HEAD),
        _iss(2, validated_against=HEAD),
        reval=_reval(HEAD, {"1": _receipt_child(),
                            "2": _receipt_child(extraction="ambiguous", depth="deep")}))


class TestTheQueuePayloadProducers:
    def test_the_fresh_session_disposition_carries_the_ordered_queue(self):
        disp = dl.fresh_session_handoff(_current_campaign(), mode=dl.FRESH_SESSION_MODE,
                                        project="rawgentic", observed_head=HEAD)
        assert disp["outcome"] == "ready", disp
        assert disp["queue"]["validated_head"] == HEAD
        assert [c["number"] for c in disp["queue"]["children"]] == [1, 2]

    def test_the_payload_carries_the_revalidation_result_not_just_the_stamp(self):
        """The successor must CONSUME the revalidation rather than re-derive it (AC4a), so the
        per-child depth/extraction/outcome ride along."""
        payload = dl.revalidated_queue_payload(_current_campaign())
        second = payload["children"][1]
        assert second["extraction"] == "ambiguous" and second["depth"] == "deep"
        assert second["outcome"] == "still_valid"

    def test_the_mid_child_disposition_carries_it_too(self):
        state = _state(_iss(1, "in_progress", validated_against=HEAD),
                       reval=_reval(HEAD, {"1": _receipt_child()}))
        position = {"issue": 1, "step": "8", "branch": "feat/x", "test_baseline": "1/0",
                    "predecessor_pane": "w1:p1", "predecessor_session": "s1",
                    "goal_condition": "keep going", "project": "rawgentic",
                    "project_path": "/p", "repo_root": "/p"}
        disp = dl.mid_child_handoff(state, position=position)
        assert disp["outcome"] == "ready", disp
        assert disp["queue"]["validated_head"] == HEAD


class TestOpenHandoffMandatoryQueue:
    def test_a_revalidation_campaign_missing_queue_raises(self):
        """Optional propagation was refused: a producer that dropped `queue` would have
        `open_handoff` quietly write the legacy record and bypass claim-time validation."""
        state = _current_campaign()
        disp = dl.fresh_session_handoff(state, mode=dl.FRESH_SESSION_MODE, project="rawgentic",
                                        observed_head=HEAD)
        del disp["queue"]
        with pytest.raises(dl.DriverStateError, match="must carry"):
            dl.open_handoff(state, disp, now_ts=1000)

    def test_a_pre_840_state_still_writes_exactly_three_keys(self):
        """The #569 contract, pinned by `test_driver_lib.py:618`/`:791`. The discriminator is the
        STATE's receipt, not the disposition's `kind` — `fresh_session_handoff`'s ready
        disposition has no `kind` at all yet is a campaign producer, so keying on `kind` would
        have broken this."""
        state = _state(_iss(1, "merged"), _iss(2), session_mode=dl.FRESH_SESSION_MODE)
        disp = dl.fresh_session_handoff(state, mode=dl.FRESH_SESSION_MODE, project="rawgentic")
        new = dl.open_handoff(state, disp, now_ts=1000)
        assert set(new["handoff_pending"]) == {"generation", "next_issue", "written_ts"}

    def test_a_revalidation_campaign_writes_the_queue(self):
        state = _current_campaign()
        disp = dl.fresh_session_handoff(state, mode=dl.FRESH_SESSION_MODE, project="rawgentic",
                                        observed_head=HEAD)
        new = dl.open_handoff(state, disp, now_ts=1000)
        assert new["handoff_pending"]["queue"]["validated_head"] == HEAD


class TestTheClaimTimeConsumer:
    """`handoff_claim` validates the COMPLETE ORDERED payload. "Head and membership" was refuted:
    membership admits a reordered queue — and order decides which child runs next."""

    def _pending(self, mutate=None):
        state = _current_campaign()
        disp = dl.fresh_session_handoff(state, mode=dl.FRESH_SESSION_MODE, project="rawgentic",
                                        observed_head=HEAD)
        new = dl.open_handoff(state, disp, now_ts=1000)
        if mutate is not None:
            mutate(new["handoff_pending"]["queue"])
        return new

    def test_a_faithful_payload_claims_successfully(self):
        ok, _new = dl.handoff_claim(self._pending(), 2, claimant="succ", now_ts=1)
        assert ok is True

    def test_a_reordered_queue_fails_the_claim(self):
        def reorder(queue):
            queue["children"].reverse()
        ok, _new = dl.handoff_claim(self._pending(reorder), 2, claimant="succ", now_ts=1)
        assert ok is False, "a reordered queue hands children out in the wrong dependency order"

    @pytest.mark.parametrize("field,value", [
        ("status", "merged"),
        ("validated_against", OLD),
        ("depth", "quick"),
        ("outcome", "body_corrected"),
        ("extraction", "none"),
        ("correction_comment", "https://example.invalid/fake"),
    ])
    def test_a_falsified_per_child_field_fails_the_claim(self, field, value):
        def falsify(queue):
            queue["children"][1][field] = value
        ok, _new = dl.handoff_claim(self._pending(falsify), 2, claimant="succ", now_ts=1)
        assert ok is False, f"a falsified {field} was accepted"

    def test_a_falsified_validated_head_fails_the_claim(self):
        def falsify(queue):
            queue["validated_head"] = OLD
        ok, _new = dl.handoff_claim(self._pending(falsify), 2, claimant="succ", now_ts=1)
        assert ok is False

    def test_a_dropped_child_fails_the_claim(self):
        def drop(queue):
            queue["children"].pop()
        ok, _new = dl.handoff_claim(self._pending(drop), 2, claimant="succ", now_ts=1)
        assert ok is False

    def test_a_pre_840_claim_is_unaffected(self):
        """Every existing campaign claims exactly as it did — the consumer only engages when the
        state carries a receipt."""
        state = _state(_iss(1, "merged"), _iss(2), session_mode=dl.FRESH_SESSION_MODE)
        disp = dl.fresh_session_handoff(state, mode=dl.FRESH_SESSION_MODE, project="rawgentic")
        new = dl.open_handoff(state, disp, now_ts=1000)
        ok, _new = dl.handoff_claim(new, 2, claimant="succ", now_ts=1)
        assert ok is True


# --------------------------------------------------------------------------- #
# §12 rewritten row — the MANDATORY correction consumer at child start
# --------------------------------------------------------------------------- #

CORRECTION_URL = "https://github.com/3D-Stories/rawgentic/issues/840#issuecomment-1"


def _broken_claim():
    return {"kind": "cause",
            "quoted_from_body": "next_ready_issue is at hooks/driver_lib.py:289",
            "checked_against": f"hooks/driver_lib.py@{HEAD}",
            "evidence": "line 289 is mid-docstring; the function is now at :795",
            "verdict": "broken"}


def _corrected_child():
    return {"body_hash": BODY_HASH, "from_sha": OLD, "to_sha": HEAD, "extraction": "paths",
            "depth": "deep", "outcome": "body_corrected", "claims": [_broken_claim()],
            "correction_comment": CORRECTION_URL, "validated_at": 1_754_000_000}


class TestTheCorrectionConsumer:
    """Assert the BUILT PROMPT STRING, not that a source test exists.

    The r4 version of this row asserted a source test existed and pinned only epic-run prose, so
    deleting that test made it vacuous and the successor could still be handed nothing. What
    matters is the artifact the successor actually receives.
    """

    def test_the_fresh_session_prompt_carries_the_evidence_and_the_url(self):
        state = _state(_iss(1, validated_against=HEAD),
                       reval=_reval(HEAD, {"1": _corrected_child()}))
        disp = dl.fresh_session_handoff(state, mode=dl.FRESH_SESSION_MODE, project="rawgentic",
                                        observed_head=HEAD)
        assert disp["outcome"] == "ready", disp
        prompt = disp["resume_prompt"]
        assert "driver_lib.py:289" in prompt, prompt
        assert "the function is now at :795" in prompt, prompt
        assert CORRECTION_URL in prompt, prompt

    def test_the_mid_child_prompt_carries_it_too(self):
        state = _state(_iss(1, "in_progress", validated_against=HEAD),
                       reval=_reval(HEAD, {"1": _corrected_child()}))
        position = {"issue": 1, "step": "8", "branch": "feat/x", "test_baseline": "1/0",
                    "predecessor_pane": "w1:p1", "predecessor_session": "s1",
                    "goal_condition": "keep going", "project": "rawgentic",
                    "project_path": "/p", "repo_root": "/p"}
        prompt = dl.mid_child_handoff(state, position=position)["resume_prompt"]
        assert CORRECTION_URL in prompt and "the function is now at :795" in prompt, prompt

    def test_a_still_valid_child_adds_nothing(self):
        """A correction clause on a child with nothing wrong would train agents to ignore it."""
        state = _state(_iss(1, validated_against=HEAD),
                       reval=_reval(HEAD, {"1": _receipt_child()}))
        disp = dl.fresh_session_handoff(state, mode=dl.FRESH_SESSION_MODE, project="rawgentic",
                                        observed_head=HEAD)
        assert "CORRECTION" not in disp["resume_prompt"]

    def test_a_pre_840_prompt_is_unchanged(self):
        state = _state(_iss(1, "merged"), _iss(2))
        disp = dl.fresh_session_handoff(state, mode=dl.FRESH_SESSION_MODE, project="rawgentic")
        assert "CORRECTION" not in disp["resume_prompt"]

    def test_the_evidence_is_rendered_not_merely_linked(self):
        """A bare URL is an instruction to go and read something, which an unattended successor
        may not do. The quoted evidence IS the correction."""
        clause = dl.corrections_clause(
            _state(_iss(1), reval=_reval(HEAD, {"1": _corrected_child()})), 1)
        assert "line 289 is mid-docstring" in clause
        assert "deliberately NOT edited" in clause

    def test_an_obsolete_marked_child_says_so_in_the_prompt(self):
        clause = dl.corrections_clause(
            _state(_iss(1), reval=_reval(HEAD, {"1": _receipt_child(pending="issue_obsolete")})), 1)
        assert "issue_obsolete" in clause and "owner decision" in clause
