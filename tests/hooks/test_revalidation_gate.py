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
import os
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

    def test_a_receiptless_campaign_is_REFUSED_by_the_cli(self, tmp_path):
        """Step-11 finding 1 at the CLI. This previously asserted an "enforcement is OFF" note and
        that the command carried on; the gate is now universal, so an un-armed campaign gets the
        revalidation exit code and an actionable reason."""
        work, _head = _repo_with_origin(tmp_path)
        state = _state(_iss(1), session_mode=dl.FRESH_SESSION_MODE)
        path = _write_state(tmp_path, state)
        proc = subprocess.run([sys.executable, str(CLI), *_handoff_argv(path, tmp_path, work)],
                              capture_output=True, text=True, check=False)
        assert proc.returncode == 6, (proc.returncode, proc.stdout, proc.stderr)
        assert "never been revalidated" in proc.stdout, proc.stdout

    def test_the_head_is_observed_even_for_an_unarmed_campaign(self, tmp_path):
        """The mechanism behind finding 1: the observation used to be skipped when no receipt was
        present, which is what let selection proceed with neither. `--project-root` is not a repo,
        so a command that still observes must exit 5 rather than sailing past."""
        state = _state(_iss(1), session_mode=dl.FRESH_SESSION_MODE)
        path = _write_state(tmp_path, state)
        not_a_repo = tmp_path / "elsewhere"
        not_a_repo.mkdir()
        proc = subprocess.run([sys.executable, str(CLI),
                               *_handoff_argv(path, tmp_path, not_a_repo)],
                              capture_output=True, text=True, check=False)
        assert proc.returncode == 5, (proc.returncode, proc.stdout, proc.stderr)
        assert "refusing to observe the head" in proc.stderr, proc.stderr


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

    def test_a_receiptless_campaign_FAILS(self, tmp_path):
        """**Step-11 finding 1 (Critical), owner decision 2026-08-02.** This asserted the OPPOSITE
        until the review: a receipt-less campaign passed the rung "with the reason recorded", on a
        compatibility argument. The reviewer refuted it and the refutation is decisive — a refusal
        is recoverable by running the skill this PR ships, whereas silent passage is the one
        failure direction this design forbids, and nothing in the code would ever have created
        that first receipt.

        A real repo is used so the head IS observable: this must fail on the RECEIPT, not because
        git was unavailable."""
        work, _head = _repo_with_origin(tmp_path)
        path = _write_state(tmp_path, _state(_iss(1)))
        passed, reason = ll.produce_queue_revalidated(
            {"driver_state_path": str(path), "repo_root": str(work)})
        assert passed is False
        assert "never been revalidated" in reason, reason
        assert "revalidate-children" in reason, "the refusal must name its own remedy"

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
        # INVERTED with the owner-gate cut (#848): the marker is surfaced but explicitly
        # non-blocking. It used to say an owner decision was needed "before any work
        # starts", which stalled an unattended successor on a gate that no longer exists.
        assert "issue_obsolete" in clause, clause
        assert "not a blocker" in clause, clause
        assert "before any work starts" not in clause, clause


# --------------------------------------------------------------------------- #
# §4 — every CAMPAIGN call site supplies the enforcement inputs (source-level)
# --------------------------------------------------------------------------- #

class TestTheCampaignCallSites:
    """What is LEFT of the source-level pins after Step-11 round 3, finding 7 — and why.

    Four positive substring pins lived here: `_cmd_handoff` observes and threads the head,
    `resume_prompt_for_state` observes it, `_cmd_mid_child_handoff` supplies `campaign_context`,
    and `retire_predecessor` produces the rung. **All four are DELETED**, because they were
    redundant AND misleading:

    * Redundant — round 2 already added the runtime replacements and said so in
      `TestFinding6TheCallSitesArePinnedAtRUNTIME`, but never removed what they replaced. The
      contracts are now owned by, respectively:
      `TestFinding6...::test_cmd_handoff_refuses_a_stale_campaign_end_to_end`,
      `TestTheObservedHeadDataflow::test_the_sentinel_from_observe_head_reaches_the_refusal`
      (a monkeypatched sentinel — dataflow, not spelling),
      `TestFinding5...::test_the_command_passes_a_real_campaign_context` (asserts the dict
      `perform_handoff` actually received), and
      `TestFinding6...::test_retire_predecessor_refuses_a_stale_campaign_without_touching_the_pane`.
    * Misleading — every one survived the exact sabotages this file's own comments admit:
      `campaign_context=None` still satisfies `"campaign_context=" in src`; discarding the
      producer's result and hardcoding the rung `True` still passed; overwriting `observed_head`
      with a cached value still satisfied both substring checks. Substring presence is not
      dataflow.

    The old docstring also claimed "adding a campaign caller without the argument fails CI here".
    **That was false** — each pin inspected one NAMED function, so a brand-new call site was never
    examined at all. Removing the tests removes no coverage; it removes a claim of coverage.

    The one pin kept below is NEGATIVE, and a negative has no runtime twin: no test can prove a
    ladder rung that must never appear is absent by observing behaviour that never runs.
    """

    @staticmethod
    def _source(name):
        import inspect
        return inspect.getsource(getattr(ll, name))

    def test_the_ad_hoc_site_source_still_carries_no_campaign_context(self):
        """SOURCE-SHAPE pin, and named so — it proves what the text says, not what the code does.

        An ad-hoc handoff has no campaign and uses the three-rung launch ladder. It must not
        acquire a campaign argument or a `steps=` override by copy-paste from the mid-child site
        next door, which is the realistic way this breaks. Kept as a substring check because the
        contract IS absence; if it ever gains a runtime twin, delete this."""
        src = self._source("_cmd_ad_hoc_handoff")
        assert "campaign_context=" not in src
        assert "steps=" not in src, "an ad-hoc handoff must keep the default launch ladder"


# --------------------------------------------------------------------------- #
# Step-11 review round — one regression test per finding, runtime not source
# --------------------------------------------------------------------------- #

class TestFinding2SingleSessionIsNoLongerABypass:
    """**Critical.** `single-session` is the epic driver's DEFAULT mode and its documented
    fallback. `fresh_session_handoff` used to check the mode FIRST and return `single_session`
    before selection, so an armed campaign with a STALE receipt advanced anyway — even when a
    freshly observed head was supplied. That was the main path, not a corner."""

    def _stale_armed(self):
        return _state(_iss(1, validated_against=OLD), _iss(2, validated_against=OLD),
                      session_mode="single-session",
                      reval=_reval(OLD, {"1": _receipt_child(to_sha=OLD),
                                         "2": _receipt_child(to_sha=OLD)}))

    def test_single_session_mode_is_gated(self):
        disp = dl.fresh_session_handoff(self._stale_armed(), mode="single-session",
                                        project="rawgentic", observed_head=HEAD)
        assert disp["outcome"] == "revalidation_required", disp

    def test_a_current_campaign_still_gets_its_single_session_verdict(self):
        """The gate must not swallow the mode verdict it sits in front of — `single_session` is
        load-bearing for #569 and still has to be reachable."""
        state = _state(_iss(1, validated_against=HEAD), session_mode="single-session",
                       reval=_reval(HEAD, {"1": _receipt_child()}))
        disp = dl.fresh_session_handoff(state, mode="single-session", project="rawgentic",
                                        observed_head=HEAD)
        assert disp["outcome"] == "single_session", disp

    def test_an_armed_campaign_cannot_decide_without_an_observation(self):
        with pytest.raises(dl.DriverStateError):
            dl.fresh_session_handoff(self._stale_armed(), mode="single-session",
                                     project="rawgentic")


class TestFinding2TheGatedSelectionCommandExists:
    """The other half of finding 2: moving the gate above the mode check was necessary but not
    sufficient, because the in-session loop still had no caller that makes a REAL observation.
    A pure function cannot fetch."""

    def _run(self, tmp_path, state, repo_root, *extra):
        path = _write_state(tmp_path, state)
        return subprocess.run(
            [sys.executable, str(CLI), "next-child", "--driver-state", str(path),
             "--project-root", str(repo_root), "--no-probe", *extra],
            capture_output=True, text=True, check=False)

    def test_a_current_campaign_yields_the_next_child(self, tmp_path):
        work, head = _repo_with_origin(tmp_path)
        state = _state(_iss(1, validated_against=head),
                       reval=_reval(head, {"1": _receipt_child(to_sha=head)}))
        proc = self._run(tmp_path, state, work)
        assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)
        payload = json.loads(proc.stdout)
        assert payload == {"outcome": "ready", "next_issue": 1, "observed_head": head}

    def test_a_stale_campaign_exits_6_with_the_worklist(self, tmp_path):
        work, head = _repo_with_origin(tmp_path)
        state = _state(_iss(1, validated_against=OLD),
                       reval=_reval(OLD, {"1": _receipt_child(to_sha=OLD)}))
        proc = self._run(tmp_path, state, work)
        assert proc.returncode == 6, (proc.returncode, proc.stdout, proc.stderr)
        payload = json.loads(proc.stdout)
        assert payload["observed_head"] == head and payload["validated_head"] == OLD
        assert [w["number"] for w in payload["worklist"]] == [1]

    def test_an_unobservable_head_exits_5(self, tmp_path):
        not_a_repo = tmp_path / "nope"
        not_a_repo.mkdir()
        proc = self._run(tmp_path, _state(_iss(1)), not_a_repo)
        assert proc.returncode == 5, (proc.returncode, proc.stderr)

    def test_an_unarmed_campaign_exits_6_not_0(self, tmp_path):
        work, _head = _repo_with_origin(tmp_path)
        proc = self._run(tmp_path, _state(_iss(1)), work)
        assert proc.returncode == 6, (proc.returncode, proc.stdout, proc.stderr)


class TestFinding3TheNoEligibleShortcutNoLongerFalsePasses:
    """**Critical.** The eligibility shortcut returned before `validate_queue_revalidation` and
    before the head comparison, so with one `in_progress` child and no queued child the producer
    reported the ladder rung PASSED on a malformed *or* stale receipt — on the path that gates an
    irreversible teardown."""

    def test_a_malformed_receipt_raises_even_with_nothing_eligible(self):
        state = _state(_iss(1, "in_progress"))
        state["queue_revalidation"] = {"validated_head": "not-a-sha"}
        with pytest.raises(dl.DriverStateError):
            dl.next_ready_issue(state, observed_head=HEAD)

    def test_a_stale_receipt_refuses_even_with_nothing_eligible(self):
        state = _state(_iss(1, "in_progress"), reval=_reval(OLD, {}))
        with pytest.raises(dl.QueueRevalidationRequired):
            dl.next_ready_issue(state, observed_head=HEAD)

    def test_the_producer_fails_on_both_shapes(self, tmp_path):
        work, head = _repo_with_origin(tmp_path)
        for label, reval in (("malformed", {"validated_head": "not-a-sha"}),
                             ("stale", _reval(OLD, {}))):
            state = _state(_iss(1, "in_progress"))
            state["queue_revalidation"] = reval
            path = _write_state(tmp_path, state)
            passed, reason = ll.produce_queue_revalidated(
                {"driver_state_path": str(path), "repo_root": str(work)})
            assert passed is False, f"{label} receipt passed the rung: {reason}"
        assert head  # the head really was observable, so these failed on the receipt

    def test_a_current_receipt_with_nothing_eligible_still_passes(self, tmp_path):
        """The negative twin: the fix must not refuse a perfectly good mid-child handoff whose
        only child is in progress. Without this, a gate that refused everything would pass above."""
        work, head = _repo_with_origin(tmp_path)
        state = _state(_iss(1, "in_progress"), reval=_reval(head, {}))
        path = _write_state(tmp_path, state)
        passed, reason = ll.produce_queue_revalidated(
            {"driver_state_path": str(path), "repo_root": str(work)})
        assert passed is True, reason


class TestFinding4AnInvalidatedPayloadIsLegibleAndRecoverable:
    """**High.** Exact equality is KEPT — it is what detects a reordered or falsified payload. What
    was missing was a legible refusal and a stated recovery, because the claim failure was reported
    as "a foreign or live claim holds it", sending the operator after a session that does not exist.
    """

    def _armed_with_pending(self):
        state = _state(_iss(1, validated_against=HEAD), _iss(2, validated_against=HEAD),
                       session_mode=dl.FRESH_SESSION_MODE,
                       reval=_reval(HEAD, {"1": _receipt_child(), "2": _receipt_child()}))
        disp = dl.fresh_session_handoff(state, mode=dl.FRESH_SESSION_MODE, project="rawgentic",
                                        observed_head=HEAD)
        return dl.open_handoff(state, disp, now_ts=1000)

    def test_the_PREDICATE_separates_a_moved_queue_from_a_competing_claim(self):
        """Renamed at round 3 (finding 7): this exercises `handoff_queue_is_current` ALONE, so it
        cannot say anything about what is *reported* — deleting `retire_predecessor`'s
        `queue_changed` branch entirely would leave it green. The reporting claim is owned by
        `tests/hooks/test_mid_child_handoff.py::TestRetireRefusesBeforeAnythingDestructive`, which
        drives missing, tampered and live-claim payloads through `retire_predecessor` itself."""
        pending = self._armed_with_pending()
        assert dl.handoff_queue_is_current(pending) is True
        after = dl.record_child_outcome(pending, 1, "merged")
        assert dl.handoff_claim(after, 2, claimant="s", now_ts=1)[0] is False
        assert dl.handoff_queue_is_current(after) is False, \
            "the launcher needs this to tell a queue change from a competing claim"

    def test_the_PREDICATE_also_returns_False_for_a_tampered_payload(self):
        """Honest about the limit: this predicate says "the payload no longer matches state", which
        covers both a legitimate move and tampering. It is a reporting aid, not an authorisation —
        and per round-3 High 3 it is outranked by a live claim at the call site."""
        pending = self._armed_with_pending()
        pending["handoff_pending"]["queue"]["children"].reverse()
        assert dl.handoff_queue_is_current(pending) is False

    def test_a_fresh_handoff_recovers_the_run(self):
        """The recovery that already existed and is now documented: a NEW disposition regenerates
        the payload from current state and its generation is claimable."""
        after = dl.record_child_outcome(self._armed_with_pending(), 1, "merged")
        disp2 = dl.fresh_session_handoff(after, mode=dl.FRESH_SESSION_MODE, project="rawgentic",
                                        observed_head=HEAD)
        assert disp2["outcome"] == "ready" and disp2["next_issue"] == 2, disp2
        regenerated = dl.open_handoff(after, disp2, now_ts=2000)
        assert dl.handoff_claim(regenerated, disp2["generation"],
                                claimant="s", now_ts=1)[0] is True

    def test_a_pre_840_state_is_reported_current(self):
        state = _state(_iss(1, "merged"), _iss(2), session_mode=dl.FRESH_SESSION_MODE)
        disp = dl.fresh_session_handoff(state, mode=dl.FRESH_SESSION_MODE, project="rawgentic")
        assert dl.handoff_queue_is_current(dl.open_handoff(state, disp, now_ts=1)) is True


class TestFinding5NoGenerationIsSpentOnAnUncheckedQueue:
    """**Medium.** `_cmd_mid_child_handoff` bumped `generation` and wrote `handoff_pending` before
    `perform_handoff` ran the first-rung check, and the cancelling `try/finally` began after that
    write — so an abrupt death in between left an uncancelled generation for a queue nobody had
    checked. The check now runs before anything durable is written."""

    def test_a_stale_queue_costs_no_generation(self, tmp_path):
        work, _head = _repo_with_origin(tmp_path)
        state = _state(_iss(1, "in_progress"), generation=7, reval=_reval(OLD, {}))
        path = _write_state(tmp_path, state)
        # The predecessor's own transcript, carrying the live goal row the command reads its
        # condition from verbatim (#611: never retype a goal).
        own = tmp_path / "own.jsonl"
        own.write_text(json.dumps({
            "type": "user", "sessionId": "pred-1",
            "attachment": {"type": "goal_status", "met": False, "sentinel": True,
                           "condition": "keep going"}}) + "\n", encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, str(CLI), "mid-child-handoff",
             "--driver-state", str(path), "--anchor-pane", "w1:p1", "--name", "succ",
             "--project", "rawgentic", "--project-path", str(work), "--project-root", str(work),
             "--cwd", str(work), "--registry", str(tmp_path / "reg.jsonl"),
             "--transcript-dir", str(tmp_path), "--issue", "1", "--step", "8",
             "--branch", "feat/x", "--test-baseline", "1/0",
             "--goal-condition-from", str(own), "--repo-root", str(work),
             "--predecessor-session", "pred-1"],
            # The session identity is PINNED in the child environment, not inherited and not
            # assumed absent. Both directions bit: with no `--predecessor-session` the command
            # refused ("refusing to guess which session is handing over") in CI where
            # CLAUDE_CODE_SESSION_ID is unset; with the flag alone it refused in an interactive
            # session because the flag CONTRADICTED the ambient id. Setting both to the same value
            # is the only form that is deterministic in either environment.
            env={**os.environ, "CLAUDE_CODE_SESSION_ID": "pred-1"},
            capture_output=True, text=True, check=False)
        assert proc.returncode == 6, (proc.returncode, proc.stdout, proc.stderr)
        written = json.loads(path.read_text(encoding="utf-8"))
        assert written["generation"] == 7, "a refused handoff must not spend a generation"
        assert "handoff_pending" not in written, written


class TestFinding6TheCallSitesArePinnedAtRUNTIME:
    """**Medium — my own tests.** The source-level pins survived every sabotage the reviewer named:
    `campaign_context=None` still satisfies `assert "campaign_context=" in src`; discarding the
    producer's result and hardcoding the rung `True` still passed; overwriting `observed_head` with
    a cached head still satisfied both substring checks. Substring presence is not dataflow.

    These replace them with runtime assertions: a genuinely stale campaign must be REFUSED through
    each production entry point, which no amount of argument-shaped text can fake.
    """

    def test_cmd_handoff_refuses_a_stale_campaign_end_to_end(self, tmp_path):
        work, _head = _repo_with_origin(tmp_path)
        state = _state(_iss(1, validated_against=OLD), session_mode=dl.FRESH_SESSION_MODE,
                       reval=_reval(OLD, {"1": _receipt_child(to_sha=OLD)}))
        path = _write_state(tmp_path, state)
        proc = subprocess.run([sys.executable, str(CLI), *_handoff_argv(path, tmp_path, work)],
                              capture_output=True, text=True, check=False)
        assert proc.returncode == 6, (proc.returncode, proc.stdout, proc.stderr)

    def test_retire_predecessor_refuses_a_stale_campaign_without_touching_the_pane(self, tmp_path):
        """The teardown path is irreversible, so the assertion is that NO destructive herdr call is
        made — not merely that the verdict was negative."""
        work, _head = _repo_with_origin(tmp_path)
        calls = []

        def runner(argv, timeout=180):
            calls.append(list(argv))
            if argv[:3] == ["herdr", "pane", "get"]:
                return type("P", (), {"returncode": 0, "stderr": "", "stdout": json.dumps(
                    {"result": {"pane_id": argv[3], "agent_session": {"value": "pred"}}})})()
            if argv[0] == "git":
                import subprocess as sp
                return sp.run(argv, capture_output=True, text=True, check=False)
            return type("P", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        position = {"issue": 1, "step": "8", "branch": "main", "test_baseline": "1/0",
                    "predecessor_pane": "w1:p1", "predecessor_session": "pred",
                    "goal_condition": "keep going", "project": "rawgentic",
                    "project_path": str(work), "repo_root": str(work)}
        state = _state(_iss(1, "in_progress"), generation=5, reval=_reval(OLD, {}))
        # An armed campaign's pending record must carry the ordered payload, else `handoff_claim`
        # refuses on the payload before the rung is ever evaluated — which would make this test
        # pass for the wrong reason. Derived from the production function so it cannot drift.
        state["handoff_pending"] = {
            "generation": 5, "next_issue": 1, "written_ts": 1,
            "kind": dl.MID_CHILD_HANDOFF_KIND, "position": position,
            "queue": dl.revalidated_queue_payload(state),
            "successor": {"pane": "w1:p2", "session": "succ"}}
        path = _write_state(tmp_path, state)
        (tmp_path / "t").mkdir(exist_ok=True)
        out = ll.retire_predecessor(
            driver_state_path=str(path), session_id="succ", anchor_pane="w1:p1",
            transcript_dir=str(tmp_path / "t"), registry_path=str(tmp_path / "reg.jsonl"),
            runner=runner, sleeper=lambda _s: None, now_ts=1000)
        assert out["outcome"] == "teardown_refused", out
        assert out["results"].get("queue_revalidated") is False, out["results"]
        assert not any(a[:3] == ["herdr", "pane", "close"] for a in calls), \
            "a refused teardown must never close the predecessor's pane"


# --------------------------------------------------------------------------- #
# Step-11 ROUND 2 — the universal gate must be recoverable, not just strict
# --------------------------------------------------------------------------- #

class TestRound2Finding1TheFirstArmIsAlwaysPossible:
    """**High.** The gate became universal, which meant every legacy campaign is refused until
    armed. `revalidation_worklist` RAISED for an unstamped child when the campaign carried no
    `base_default_branch_sha` — a field `queue.schema.json` makes optional and nullable. So a
    schema-valid pre-#840 campaign was refused by the gate and the clearing skill could not build
    its first worklist: re-running it changed nothing. A migration with no way through is worse
    than no migration."""

    def test_a_campaign_with_no_base_can_still_build_its_first_worklist(self):
        state = _state(_iss(1))                      # never validated, and no campaign base
        assert "base_default_branch_sha" not in state
        work = dl.revalidation_worklist(
            state, HEAD, extractions={1: (["hooks/driver_lib.py"], "paths")},
            changed_by_child={1: set()})
        assert [w["number"] for w in work] == [1]
        assert work[0]["from_sha"] == HEAD and work[0]["to_sha"] == HEAD, work[0]

    def test_the_no_base_first_arm_is_forced_DEEP(self):
        """It must fail toward MORE scrutiny. With no baseline nothing can be shown to be
        untouched, so an empty changed-set must NOT buy `quick` — which is exactly what the naive
        `from_sha == to_sha` range would compute."""
        work = dl.revalidation_worklist(
            _state(_iss(1)), HEAD,
            extractions={1: (["hooks/driver_lib.py"], "paths")},
            changed_by_child={1: set()})
        assert work[0]["depth"] == "deep", work[0]

    def test_a_campaign_WITH_a_base_still_uses_it(self):
        """The negative twin: the fallback must not swallow a real baseline, or every first arm
        would silently lose its range."""
        state = _state(_iss(1), base_default_branch_sha=OLD)
        work = dl.revalidation_worklist(
            state, HEAD, extractions={1: ([], "none")}, changed_by_child={1: set()})
        assert work[0]["from_sha"] == OLD, work[0]

    def test_the_round_trip_actually_opens_the_gate(self):
        """The claim that matters: refused -> arm -> selectable. A test that only checks the
        worklist builds would not prove the migration completes."""
        state = _state(_iss(1))
        with pytest.raises(dl.QueueRevalidationRequired):
            dl.next_ready_issue(state, observed_head=HEAD)
        work = dl.revalidation_worklist(
            state, HEAD, extractions={1: ([], "none")}, changed_by_child={1: set()})
        item = work[0]
        armed = dict(state)
        armed["issues"] = [dict(state["issues"][0], validated_against=HEAD)]
        armed["queue_revalidation"] = {
            "version": 1, "extractor_version": 1, "validated_head": HEAD,
            "children": {"1": {"body_hash": BODY_HASH, "from_sha": item["from_sha"],
                               "to_sha": item["to_sha"], "extraction": item["extraction"],
                               "depth": item["depth"], "outcome": "still_valid",
                               "claims": [_claim()], "validated_at": 1}}}
        dl.validate_queue_revalidation(armed)        # the receipt shape must be legal
        assert dl.next_ready_issue(armed, observed_head=HEAD) == 1


class TestRound7Low1OneUnattestedStampMustNotPoisonTheOthers:
    """**Low.** `base` is loop-invariant, and the round-6 fix set it to `None` inside the loop for
    an unattested current-stamped child — so every LATER unstamped child silently lost the valid
    campaign base and got `head..head`/`deep`/`unavailable` too. It fails toward more scrutiny so
    it was never unsafe, but it records provenance that is simply untrue, and cross-child
    contamination through a loop variable is close to invisible in review. Mine."""

    def test_a_later_child_keeps_the_campaign_base(self):
        state = _state(_iss(1, validated_against=HEAD),          # stamped, unattested
                       _iss(2),                                   # unstamped -> must use base
                       base_default_branch_sha=OLD)
        work = dl.revalidation_worklist(
            state, HEAD, extractions={1: ([], "none"), 2: ([], "none")},
            changed_by_child={1: set(), 2: set()})
        by = {w["number"]: w for w in work}
        assert by[1]["baseline"] == "unavailable", by[1]
        assert by[2]["baseline"] == "base" and by[2]["from_sha"] == OLD, by[2]

    def test_order_does_not_matter(self):
        """Asserted explicitly because the bug was order-dependent: it only bit children that
        came AFTER the unattested one, so a one-child or fortunately-ordered fixture hides it."""
        state = _state(_iss(2), _iss(1, validated_against=HEAD),
                       base_default_branch_sha=OLD)
        work = dl.revalidation_worklist(
            state, HEAD, extractions={1: ([], "none"), 2: ([], "none")},
            changed_by_child={1: set(), 2: set()})
        assert {w["number"]: w["baseline"] for w in work} == {1: "unavailable", 2: "base"}


class TestRound6Finding3AStaleReceiptMustLeaveTheSkillSomethingToDo:
    """**High — the fifth unrecoverable jam in this PR, and the one nobody had looked for.**
    The head clause refuses whenever the receipt does not attest the observed head, and tells the
    operator to run `/rawgentic:revalidate-children`. But `revalidation_worklist` skipped any
    child whose stamp already equalled the observed head — regardless of whether the RECEIPT
    covered it — so for a child stamped at the new head under a stale or absent receipt the
    worklist came back empty. The skill then had nothing to audit and could not advance the
    receipt the gate demands. Refused, with the documented remedy a no-op.

    A stamp is only evidence if a current receipt vouches for it. Where it does not, the child is
    audited again, and its stamp is NOT trusted as a baseline either: there is no range to diff
    and nothing attesting it, so the range collapses to `head..head` with depth forced `deep`.
    """

    def _stale(self, **over):
        state = _state(_iss(1, validated_against=HEAD), base_default_branch_sha=SENTINEL,
                       reval=_reval(OLD, {"1": _receipt_child(to_sha=OLD)}))
        state.update(over)
        return state

    def test_the_refusal_now_comes_with_work_to_do(self):
        state = self._stale()
        with pytest.raises(dl.QueueRevalidationRequired):
            dl.next_ready_issue(state, observed_head=HEAD)
        work = dl.revalidation_worklist(state, HEAD, extractions={1: ([], "none")},
                                        changed_by_child={1: set()})
        assert [w["number"] for w in work] == [1], \
            "a refusal whose remedy produces an empty worklist can never be cleared"

    def test_the_untrusted_stamp_is_not_used_as_a_baseline(self):
        work = dl.revalidation_worklist(self._stale(), HEAD, extractions={1: ([], "none")},
                                        changed_by_child={1: set()})
        assert work[0]["from_sha"] == HEAD and work[0]["to_sha"] == HEAD, work[0]
        assert work[0]["depth"] == "deep", "an unattested stamp must not buy `quick`"
        assert work[0]["baseline"] == "unavailable", work[0]

    def test_the_same_holds_when_the_receipt_is_absent_entirely(self):
        state = _state(_iss(1, validated_against=HEAD), base_default_branch_sha=SENTINEL)
        with pytest.raises(dl.QueueRevalidationRequired):
            dl.next_ready_issue(state, observed_head=HEAD)
        work = dl.revalidation_worklist(state, HEAD, extractions={1: ([], "none")},
                                        changed_by_child={1: set()})
        assert [w["number"] for w in work] == [1] and work[0]["depth"] == "deep", work

    def test_a_CURRENT_receipt_still_skips_the_child(self):
        """The negative twin, and it is the common case — without it the fix would re-audit every
        already-validated child on every run, which is the cost the stamp exists to avoid."""
        state = _state(_iss(1, validated_against=HEAD),
                       reval=_reval(HEAD, {"1": _receipt_child()}))
        assert dl.revalidation_worklist(state, HEAD, extractions={},
                                        changed_by_child={}) == []

    def test_a_current_receipt_that_does_not_COVER_the_child_still_audits_it(self):
        """A receipt at the right head but carrying no entry for this child vouches for nothing."""
        state = _state(_iss(1, validated_against=HEAD), _iss(2, validated_against=HEAD),
                       reval=_reval(HEAD, {"2": _receipt_child()}))
        work = dl.revalidation_worklist(state, HEAD, extractions={1: ([], "none")},
                                        changed_by_child={1: set()})
        assert [w["number"] for w in work] == [1], work


class TestRound4Finding1ThePolicyKnobReachesSelection:
    """**High.** `fresh_session_handoff` called `next_ready_issue` without `deps_satisfied_by`,
    so it took the `"merged"` default and the persisted `policy.deps_satisfied_by` was silently
    discarded (`queue.schema.json` defines it; `docs/multi-issue-driver.md` documents the
    stacked-PR flow that depends on it).

    Concrete failure: a headless campaign with `deps_satisfied_by: "pr_open"`, child #1 `pr_open`
    and queued child #2 depending on #1. The advance rule says #2 is ready and the direct call
    agrees — but every path through `next-child` reported `blocked`/rc 3, which the driver reads
    as "nothing left". The documented flow stalls permanently, and #840's own gate work is what
    routed selection through that path.
    """

    def _stacked(self, **policy):
        return _state(_iss(1, "pr_open"), _iss(2, depends_on=[1], validated_against=HEAD),
                      reval=_reval(HEAD, {"2": _receipt_child()}),
                      policy=policy or {"deps_satisfied_by": "pr_open"})

    def test_the_pure_selector_and_the_disposition_agree(self):
        state = self._stacked()
        assert dl.next_ready_issue(state, "pr_open", observed_head=HEAD) == 2, \
            "fixture check: the advance rule really does consider #2 ready"
        disp = dl.fresh_session_handoff(state, mode=dl.FRESH_SESSION_MODE,
                                        project="rawgentic", observed_head=HEAD)
        assert disp["outcome"] == "ready" and disp["next_issue"] == 2, disp

    def test_the_default_is_unchanged_when_no_policy_is_set(self):
        """The negative twin: absent policy must still mean `merged`, or every campaign silently
        loosens to the stacked-PR rule — the opposite defect and a far worse one."""
        state = _state(_iss(1, "pr_open"), _iss(2, depends_on=[1], validated_against=HEAD),
                       reval=_reval(HEAD, {"2": _receipt_child()}))
        assert dl.next_ready_issue(state, observed_head=HEAD) is None
        disp = dl.fresh_session_handoff(state, mode=dl.FRESH_SESSION_MODE,
                                        project="rawgentic", observed_head=HEAD)
        assert disp["outcome"] == "blocked", disp

    @pytest.mark.parametrize("bad", ["", "PR_OPEN", "anything", 7, None, True])
    def test_an_unusable_policy_value_falls_back_to_the_STRICTER_default(self, bad):
        """Fail toward strictness, and never toward a jam. `pr_open` is the LOOSER rule, so a
        value that cannot be read must not buy it — but raising would strand a campaign over a
        typo, which is the unrecoverable-migration class rounds 2-4 kept finding."""
        state = self._stacked(deps_satisfied_by=bad)
        assert dl.campaign_deps_satisfied_by(state) == "merged"
        disp = dl.fresh_session_handoff(state, mode=dl.FRESH_SESSION_MODE,
                                        project="rawgentic", observed_head=HEAD)
        assert disp["outcome"] == "blocked", disp

    def test_next_child_end_to_end_honours_the_policy(self, tmp_path):
        """The reviewer asked for this one specifically: the pure function agreeing proves
        nothing about the CLI the driver actually runs."""
        work, head = _repo_with_origin(tmp_path)
        state = _state(_iss(1, "pr_open"), _iss(2, depends_on=[1], validated_against=head),
                       reval=_reval(head, {"2": _receipt_child(to_sha=head)}),
                       policy={"deps_satisfied_by": "pr_open"})
        path = _write_state(tmp_path, state)
        proc = subprocess.run(
            [sys.executable, str(CLI), "next-child", "--driver-state", str(path),
             "--project-root", str(work), "--no-probe"],
            capture_output=True, text=True, check=False)
        assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)
        assert json.loads(proc.stdout)["next_issue"] == 2, proc.stdout


class TestRound3Finding1UnusableBaselinesAreAlsoRecoverable:
    """**High.** Round 2 fixed the jam for `base_default_branch_sha is None` only. But
    `queue.schema.json:36` accepts ANY string with no format or reachability constraint, so
    `""`, `"abc"` and a well-formed-but-force-pushed SHA all still reached the strict validator
    and RAISED — the same unrecoverable migration in a different costume. A stale
    `issues[].validated_against` whose commit was pruned has the identical problem.

    The rule: a baseline that cannot be READ and a baseline that cannot be RESOLVED are the same
    situation — there is no range to diff, so nothing can be shown untouched and everything must
    be looked at. Both collapse to `from_sha == to_sha == observed_head`, forced `deep`, and
    provenance recorded as unavailable.

    **Malformed is decidable here; unresolvable is I/O.** So the pure function decides the first
    itself and ACCEPTS the second as an injected fact — `unresolvable_shas` — which the skill
    fills by probing. That split is the whole reason this is not one check.
    """

    def _work(self, state, **kw):
        return dl.revalidation_worklist(
            state, HEAD, extractions={1: (["hooks/driver_lib.py"], "paths")},
            changed_by_child={1: set()}, **kw)

    @pytest.mark.parametrize("base", ["", "abc", "not-a-sha", "A" * 40, " " + "a" * 39])
    def test_a_malformed_base_falls_back_instead_of_raising(self, base):
        work = self._work(_state(_iss(1), base_default_branch_sha=base))
        assert [w["number"] for w in work] == [1]
        assert work[0]["from_sha"] == HEAD and work[0]["to_sha"] == HEAD, work[0]
        assert work[0]["depth"] == "deep", work[0]
        assert work[0]["baseline"] == "unavailable", work[0]

    def test_an_unresolvable_but_well_formed_base_falls_back(self):
        """The force-pushed / pruned commit. Format cannot detect it, so the skill's probe says
        so and the pure function honours it — otherwise the skill jams one step later on a
        `git diff` whose left endpoint does not exist."""
        gone = "d" * 40
        work = self._work(_state(_iss(1), base_default_branch_sha=gone),
                          unresolvable_shas={gone})
        assert work[0]["from_sha"] == HEAD and work[0]["depth"] == "deep", work[0]
        assert work[0]["baseline"] == "unavailable", work[0]

    def test_a_malformed_child_stamp_falls_back_instead_of_raising(self):
        """Round 2 covered the campaign base only; a corrupt per-child stamp jammed the same way."""
        work = self._work(_state(_iss(1, validated_against="short"),
                                 base_default_branch_sha=OLD))
        assert work[0]["from_sha"] == HEAD and work[0]["depth"] == "deep", work[0]
        assert work[0]["baseline"] == "unavailable", work[0]

    def test_an_unresolvable_child_stamp_falls_back(self):
        gone = "e" * 40
        work = self._work(_state(_iss(1, validated_against=gone),
                                 base_default_branch_sha=OLD),
                          unresolvable_shas={gone})
        assert work[0]["from_sha"] == HEAD and work[0]["depth"] == "deep", work[0]
        assert work[0]["baseline"] == "unavailable", work[0]

    def test_an_unusable_stamp_does_NOT_silently_fall_through_to_the_campaign_base(self):
        """The tempting near-miss: 'the stamp is bad, so use the base'. That would date the range
        from a commit this child was never validated at and could buy `quick` on a real diff."""
        work = self._work(_state(_iss(1, validated_against="short"),
                                 base_default_branch_sha=OLD))
        assert work[0]["from_sha"] != OLD, work[0]

    def test_a_usable_base_and_stamp_are_still_used(self):
        """The negative twin — without it the fix could force every child to `unavailable`/deep
        and every test above would still pass."""
        stamped = self._work(_state(_iss(1, validated_against=OLD),
                                    base_default_branch_sha=SENTINEL))
        assert stamped[0]["from_sha"] == OLD and stamped[0]["baseline"] == "stamp", stamped[0]
        based = self._work(_state(_iss(1), base_default_branch_sha=OLD))
        assert based[0]["from_sha"] == OLD and based[0]["baseline"] == "base", based[0]
        assert based[0]["depth"] == "quick", "a real range must still be allowed to buy quick"

    def test_an_unrelated_shas_probe_does_not_disturb_a_good_baseline(self):
        """`unresolvable_shas` naming some OTHER commit must change nothing."""
        work = self._work(_state(_iss(1), base_default_branch_sha=OLD),
                          unresolvable_shas={"f" * 40})
        assert work[0]["from_sha"] == OLD and work[0]["baseline"] == "base", work[0]

    def test_the_round_trip_actually_opens_the_gate_for_a_malformed_base(self):
        """The claim that matters: refused -> arm -> selectable. Building a worklist is not proof
        the migration completes."""
        state = _state(_iss(1), base_default_branch_sha="not-a-sha")
        with pytest.raises(dl.QueueRevalidationRequired):
            dl.next_ready_issue(state, observed_head=HEAD)
        item = self._work(state)[0]
        armed = dict(state)
        armed["issues"] = [dict(state["issues"][0], validated_against=HEAD)]
        armed["queue_revalidation"] = {
            "version": 1, "extractor_version": 1, "validated_head": HEAD,
            "children": {"1": {"body_hash": BODY_HASH, "from_sha": item["from_sha"],
                               "to_sha": item["to_sha"], "extraction": item["extraction"],
                               "depth": item["depth"], "outcome": "still_valid",
                               "claims": [_claim()], "validated_at": 1}}}
        dl.validate_queue_revalidation(armed)
        assert dl.next_ready_issue(armed, observed_head=HEAD) == 1


class TestRound2Finding2NoProjectIsNotNothingReady:
    """**High.** `next-child` folds every non-ready disposition into rc 3, which it documents as
    "nothing ready". `project` is not required by the schema and a default single-session campaign
    never needed one, so a fresh, ready campaign lacking it reported as complete-or-blocked and the
    loop could stop for good."""

    def test_a_ready_campaign_without_a_project_does_not_report_nothing_ready(self, tmp_path):
        work, head = _repo_with_origin(tmp_path)
        state = _state(_iss(1, validated_against=head),
                       reval=_reval(head, {"1": _receipt_child(to_sha=head)}))
        del state["project"]
        path = _write_state(tmp_path, state)
        proc = subprocess.run(
            [sys.executable, str(CLI), "next-child", "--driver-state", str(path),
             "--project-root", str(work), "--no-probe"],
            capture_output=True, text=True, check=False)
        assert proc.returncode != 3, (
            "a config error must not masquerade as 'nothing ready' — the loop stops on rc 3")
        assert proc.returncode == 2, (proc.returncode, proc.stdout, proc.stderr)
        assert "no valid `project`" in proc.stderr, proc.stderr
        assert json.loads(proc.stdout)["next_issue"] == 1, "selection DID succeed; say so"

    def test_supplying_the_project_makes_the_same_campaign_ready(self, tmp_path):
        work, head = _repo_with_origin(tmp_path)
        state = _state(_iss(1, validated_against=head),
                       reval=_reval(head, {"1": _receipt_child(to_sha=head)}))
        del state["project"]
        path = _write_state(tmp_path, state)
        proc = subprocess.run(
            [sys.executable, str(CLI), "next-child", "--driver-state", str(path),
             "--project-root", str(work), "--project", "rawgentic", "--no-probe"],
            capture_output=True, text=True, check=False)
        assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)


class TestRound2Finding3TheMigrationShapeIsDiagnosedHonestly:
    """**High.** A pre-#840 handoff in flight has a pending record with NO queue. Arming the
    campaign then makes `handoff_claim` require one, so the claim fails — and
    `handoff_queue_is_current` returned True for exactly that shape, so the failure was reported as
    "a foreign or live claim holds it": the one diagnosis that sends an operator looking for a
    competing session instead of opening a new generation."""

    def _legacy_pending_then_armed(self):
        state = _state(_iss(1, "merged"), _iss(2), session_mode=dl.FRESH_SESSION_MODE)
        disp = dl.fresh_session_handoff(state, mode=dl.FRESH_SESSION_MODE, project="rawgentic")
        legacy = dl.open_handoff(state, disp, now_ts=1000)
        assert "queue" not in legacy["handoff_pending"], "this fixture IS the legacy shape"
        armed = dict(legacy)
        armed["issues"] = [dict(legacy["issues"][0]),
                           dict(legacy["issues"][1], validated_against=HEAD)]
        armed["queue_revalidation"] = _reval(HEAD, {"2": _receipt_child()})
        return armed

    def test_the_PREDICATE_returns_False_for_a_queueless_record_under_a_receipt(self):
        """Renamed at round 3 (finding 7) for the same reason as its siblings: it exercises the
        predicate only. What the caller REPORTS for this shape — and that a live claim outranks
        it — is asserted in `test_mid_child_handoff.py` against `retire_predecessor`."""
        armed = self._legacy_pending_then_armed()
        assert dl.handoff_claim(armed, armed["generation"],
                               claimant="s", now_ts=1)[0] is False
        assert dl.handoff_queue_is_current(armed) is False, \
            "a missing payload under a live receipt is a queue change, not a foreign claim"

    def test_a_campaign_with_no_receipt_at_all_is_still_reported_current(self):
        """The boundary: this predicate must stay True for every genuinely pre-#840 state, or every
        legacy claim refusal would be mis-reported the other way."""
        state = _state(_iss(1, "merged"), _iss(2), session_mode=dl.FRESH_SESSION_MODE)
        disp = dl.fresh_session_handoff(state, mode=dl.FRESH_SESSION_MODE, project="rawgentic")
        assert dl.handoff_queue_is_current(dl.open_handoff(state, disp, now_ts=1)) is True

    def test_a_state_with_a_receipt_and_no_pending_record_is_current(self):
        state = _state(_iss(1, validated_against=HEAD), reval=_reval(HEAD, {"1": _receipt_child()}))
        assert dl.handoff_queue_is_current(state) is True


class TestRound2Finding5TheMidChildContextIsPinnedAtRUNTIME:
    """**Medium.** The mid-child `campaign_context` propagation was still guarded only by a
    substring assertion that survives `campaign_context=None`, and the stale-campaign command test
    exits at the earlier pre-check so it never reaches the propagation. This drives a CURRENT armed
    campaign through the command and asserts the value `perform_handoff` actually received."""

    def test_the_command_passes_a_real_campaign_context(self, tmp_path, monkeypatch):
        # Pinned rather than inherited — see the note in TestFinding5: an ambient id that differs
        # from `--predecessor-session` is refused as a contradiction.
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "pred-1")
        work, head = _repo_with_origin(tmp_path)
        seen = {}

        def fake_perform(**kw):
            seen.update(kw)
            return {"ok": True, "results": {}, "failed_step": None, "new_pane": "w1:p2",
                    "session_id": "succ", "truncated": False, "cleanup": None,
                    "teardown_skipped": None}

        monkeypatch.setattr(ll, "perform_handoff", fake_perform)
        state = _state(_iss(1, "in_progress"), reval=_reval(head, {}))
        path = _write_state(tmp_path, state)
        own = tmp_path / "own.jsonl"
        own.write_text(json.dumps({
            "type": "user", "sessionId": "pred-1",
            "attachment": {"type": "goal_status", "met": False, "sentinel": True,
                           "condition": "keep going"}}) + "\n", encoding="utf-8")
        rc = ll.main([
            "mid-child-handoff", "--driver-state", str(path), "--anchor-pane", "w1:p1",
            "--name", "succ", "--project", "rawgentic", "--project-path", str(work),
            "--project-root", str(work), "--cwd", str(work),
            "--registry", str(tmp_path / "reg.jsonl"), "--transcript-dir", str(tmp_path),
            "--issue", "1", "--step", "8", "--branch", "main", "--test-baseline", "1/0",
            "--goal-condition-from", str(own), "--repo-root", str(work),
            "--predecessor-session", "pred-1"])
        assert rc == 0, rc
        context = seen.get("campaign_context")
        assert isinstance(context, dict), f"campaign_context was {context!r} — None passes the old "\
                                          f"substring test but disables the rung's producer"
        assert context["driver_state_path"] == str(path)
        assert context["repo_root"] == str(work)


class TestRound8H1TheProducerMustSeeWhatSelectionSees:
    """**Round 8, High 1.** `produce_queue_revalidated` called `next_ready_issue` WITHOUT the
    `issue_state_probe`, so the ladder rung and the selection it gates disagreed about the queue.

    The divergence is not cosmetic — it is the sixth unrecoverable jam in this issue. A durably
    `queued` sibling the probe confirms merged is invisible to the probe-less call, which refuses
    `#1: never revalidated` and sends the operator to a skill that cannot help: the documented
    worklist call raises on the missing extraction, and supplying the probe returns an EMPTY
    worklist because the child is (correctly) not eligible. Nothing the operator can run advances
    the receipt the rung demands.

    The probe is DERIVED inside the producer rather than accepted as an optional argument callers
    must remember — #695's own finding, that an optional corroboration nobody threads in ships
    dead, applies to this call site exactly as it did to selection."""

    def _merged_sibling_state(self):
        """#1 durably queued and UNSTAMPED but really merged; #2 stamped, attested, depends on #1."""
        return _state(_iss(1), _iss(2, validated_against=HEAD, depends_on=[1]),
                      reval=_reval(HEAD, {"2": _receipt_child()}))

    def test_the_producer_passes_when_the_probe_clears_the_stale_sibling(self, tmp_path):
        work, head = _repo_with_origin(tmp_path)
        state = _state(_iss(1), _iss(2, validated_against=head, depends_on=[1]),
                       reval=_reval(head, {"2": _receipt_child(to_sha=head)}))
        path = _write_state(tmp_path, state)
        passed, reason = ll.produce_queue_revalidated(
            {"driver_state_path": str(path), "repo_root": str(work)},
            issue_state_probe=lambda n: "confirmed_merged" if n == 1 else "unknown")
        assert passed is True, reason

    def test_the_negative_twin_without_corroboration_it_still_refuses(self, tmp_path):
        """Proves the probe is what changes the verdict, not the fixture being trivially green."""
        work, head = _repo_with_origin(tmp_path)
        state = _state(_iss(1), _iss(2, validated_against=head, depends_on=[1]),
                       reval=_reval(head, {"2": _receipt_child(to_sha=head)}))
        path = _write_state(tmp_path, state)
        passed, reason = ll.produce_queue_revalidated(
            {"driver_state_path": str(path), "repo_root": str(work)},
            issue_state_probe=lambda _n: "unknown")
        assert passed is False
        assert "#1" in reason, reason

    def test_the_producer_derives_a_probe_when_none_is_injected(self, tmp_path, monkeypatch):
        """The anti-dead-corroboration guard. An injectable probe that no production caller
        supplies is the exact shape #695 shipped once already, so the DEFAULT must derive one."""
        work, head = _repo_with_origin(tmp_path)
        path = _write_state(tmp_path, _state(
            _iss(1), _iss(2, validated_against=head, depends_on=[1]),
            reval=_reval(head, {"2": _receipt_child(to_sha=head)})))
        asked = []
        monkeypatch.setattr(ll, "_issue_state_probe_for",
                            lambda root: (asked.append(root),
                                          lambda n: "confirmed_merged" if n == 1 else "unknown")[1])
        passed, reason = ll.produce_queue_revalidated(
            {"driver_state_path": str(path), "repo_root": str(work)})
        assert asked == [str(work)], f"the producer never derived a probe: {asked!r}"
        assert passed is True, reason

    def test_the_documented_skill_call_takes_the_probe_and_leaves_no_phantom_work(self):
        """The remedy half. With the probe threaded through, the worklist names only the child
        that genuinely needs a look — never the merged sibling the operator cannot revalidate."""
        state = _state(_iss(1), _iss(2), reval=_reval(HEAD, {}))
        work = dl.revalidation_worklist(
            state, HEAD, extractions={2: ([], "none")}, changed_by_child={2: set()},
            issue_state_probe=lambda n: "confirmed_merged" if n == 1 else "unknown")
        assert [item["number"] for item in work] == [2], work


class TestRound8H3AMalformedReceiptEntryIsNotEvidence:
    """**Round 8, High 3.** `_receipt_covers_child` equated KEY PRESENCE with attestation, so a
    structurally invalid current record counted as covering its child: selection refused with a
    hard data error while `revalidation_worklist` returned nothing to rebuild. That is the same
    refusal-with-no-remedy shape round 6's High 3 closed for absent receipts, reopened for
    corrupt ones."""

    def _malformed(self):
        bad = _receipt_child()
        bad["body_hash"] = "bad"
        return _state(_iss(1, validated_against=HEAD), reval=_reval(HEAD, {"1": bad}))

    def test_a_malformed_current_record_does_not_cover_its_child(self):
        assert dl._receipt_covers_child(self._malformed(), 1, HEAD) is False

    def test_the_worklist_therefore_has_work_to_do(self):
        work = dl.revalidation_worklist(self._malformed(), HEAD,
                                        extractions={1: ([], "none")},
                                        changed_by_child={1: set()})
        assert [item["number"] for item in work] == [1], work

    def test_the_refusal_names_the_remedy_that_rebuilds_it(self):
        with pytest.raises(dl.DriverStateError) as exc:
            dl.next_ready_issue(self._malformed(), observed_head=HEAD)
        assert "revalidate-children" in str(exc.value), str(exc.value)

    def test_and_executing_that_remedy_opens_the_gate(self):
        """Rebuilding the entry is what the skill does; the gate must then pass."""
        state = self._malformed()
        state["queue_revalidation"]["children"]["1"] = _receipt_child()
        assert dl.next_ready_issue(state, observed_head=HEAD) == 1

    def test_a_record_attesting_a_different_head_does_not_cover_either(self):
        state = _state(_iss(1, validated_against=HEAD),
                       reval=_reval(HEAD, {"1": _receipt_child(to_sha=OLD)}))
        assert dl._receipt_covers_child(state, 1, HEAD) is False

    def test_the_valid_current_record_still_covers_its_child(self):
        """The negative twin — the round-6 fix must not become 'nothing is ever covered'."""
        state = _state(_iss(1, validated_against=HEAD), reval=_reval(HEAD, {"1": _receipt_child()}))
        assert dl._receipt_covers_child(state, 1, HEAD) is True


class TestRound8RebuildReceiptIsTheArmingProcedure:
    """**Round 8 sweep.** `rebuild_receipt` is SKILL.md step 7 made executable. It exists because
    the step was prose, and prose is where three of this issue's eight review rounds found
    defects: a paragraph describing which records survive a rebuild is re-derived, slightly
    differently, by every session that reads it.

    Its rules are the ones the sweep proved cannot be left implicit."""

    def _reval(self, **kw):
        return _reval(**kw)

    def test_an_unreadable_record_is_dropped_rather_than_carried_forward(self):
        """The 39-state jam. A corrupt record for a NON-eligible child was never rewritten,
        because the worklist only audits eligible ones — so the campaign refused for ever."""
        bad = _receipt_child()
        bad["body_hash"] = "bad"
        state = _state(_iss(1, "merged"), reval=_reval(HEAD, {"1": bad}))
        rebuilt = dl.rebuild_receipt(state, HEAD, {})
        assert rebuilt["queue_revalidation"]["children"] == {}
        assert dl.validate_queue_revalidation(rebuilt) is True

    def test_a_record_attesting_another_head_is_dropped(self):
        state = _state(_iss(1, "merged"), reval=_reval(HEAD, {"1": _receipt_child(to_sha=OLD)}))
        assert dl.rebuild_receipt(state, HEAD, {})["queue_revalidation"]["children"] == {}

    def test_a_stamp_whose_evidence_was_dropped_is_cleared(self):
        """The stamp is the claim, the record is the evidence. Losing the evidence must lose the
        claim — otherwise the linkage invariant refuses a state this function just produced."""
        bad = _receipt_child()
        bad["body_hash"] = "bad"
        state = _state(_iss(1, "merged", validated_against=HEAD), reval=_reval(HEAD, {"1": bad}))
        rebuilt = dl.rebuild_receipt(state, HEAD, {})
        assert "validated_against" not in rebuilt["issues"][0]

    def test_an_unusable_stamp_is_cleared_so_the_remedy_cannot_crash(self):
        """810 states. `rebuild_receipt` left a malformed stamp in place and then refused its own
        output at the fail-closed validation, turning the documented remedy into a traceback."""
        state = _state(_iss(1, validated_against="abc"))
        rebuilt = dl.rebuild_receipt(state, HEAD, {1: _receipt_child()})
        assert rebuilt["issues"][0]["validated_against"] == HEAD

    def test_an_empty_audit_still_advances_the_head(self):
        """A campaign whose children are all merged or in flight has nothing to audit, but the
        head clause refuses it unconditionally — so it must still be armable, or the gate is shut
        for good on the mid-child handoff it exists to serve."""
        state = _state(_iss(1, "in_progress"))
        rebuilt = dl.rebuild_receipt(state, HEAD, {})
        assert rebuilt["queue_revalidation"]["validated_head"] == HEAD
        assert dl.next_ready_issue(rebuilt, observed_head=HEAD) is None

    def test_an_audited_child_is_stamped(self):
        state = _state(_iss(1))
        rebuilt = dl.rebuild_receipt(state, HEAD, {1: _receipt_child()})
        assert rebuilt["issues"][0]["validated_against"] == HEAD
        assert dl.next_ready_issue(rebuilt, observed_head=HEAD) == 1

    def test_a_pending_child_IS_stamped_while_the_owner_gate_is_out(self):
        """**Inverted with the owner gate (#848).** The "an obsolete child stays unstamped" rule
        existed only to keep it un-selectable; with nothing gating on the marker the rule stopped
        protecting anything and started jamming the queue instead — the child was never stamped,
        so the per-child provenance clause refused for ever and re-running the skill changed
        nothing. Found by the jam sweep after the cut. #848 restores both together."""
        state = _state(_iss(1))
        rebuilt = dl.rebuild_receipt(
            state, HEAD, {1: _receipt_child(pending="issue_obsolete")})
        assert rebuilt["issues"][0]["validated_against"] == HEAD
        assert dl.validate_queue_revalidation(rebuilt) is True
        assert dl.next_ready_issue(rebuilt, observed_head=HEAD) == 1

    def test_it_never_mutates_the_state_it_was_given(self):
        state = _state(_iss(1, validated_against=OLD), reval=_reval(OLD, {}))
        before = json.dumps(state, sort_keys=True)
        dl.rebuild_receipt(state, HEAD, {1: _receipt_child()})
        assert json.dumps(state, sort_keys=True) == before

    def test_an_audit_record_for_the_wrong_head_is_REFUSED(self):
        """Fail-closed on the caller's own mistake: a record computed against another head would
        make the receipt attest a validation that never happened at this one."""
        with pytest.raises(dl.DriverStateError, match="attests"):
            dl.rebuild_receipt(_state(_iss(1)), HEAD, {1: _receipt_child(to_sha=OLD)})

    def test_a_malformed_audit_record_is_REFUSED(self):
        bad = _receipt_child()
        del bad["claims"]
        with pytest.raises(dl.DriverStateError):
            dl.rebuild_receipt(_state(_iss(1)), HEAD, {1: bad})


class TestRound8EveryReceiptRefusalNamesItsRemedy:
    """**Round 8 sweep, 280 states.** Every bare `DriverStateError` out of the receipt validator
    named nothing an operator could run. The fix is a wrapper at the function boundary rather than
    a patch per `raise` — which is the difference between fixing this class and fixing one more
    instance of it."""

    @pytest.mark.parametrize("broken,label", [
        ({"version": 2, "extractor_version": 1, "validated_head": HEAD, "children": {}},
         "unsupported version"),
        ({"version": 1, "extractor_version": 1, "validated_head": "not-a-sha", "children": {}},
         "malformed head"),
        ({"version": 1, "extractor_version": 1, "validated_head": HEAD, "children": []},
         "children not an object"),
        ({"version": 1, "extractor_version": 1, "validated_head": HEAD, "children": {"x": {}}},
         "key is not a number"),
        ({"version": 1, "extractor_version": 1, "validated_head": HEAD, "children": {"1": "e"}},
         "record is not an object"),
        ({"version": 1, "extractor_version": 1, "validated_head": HEAD,
          "children": {"1": _receipt_child(to_sha=OLD)}}, "record attests another head"),
        ({"version": 1, "extractor_version": 1, "validated_head": HEAD, "children": {}},
         "stamped with no evidence"),
    ])
    def test_the_refusal_names_the_revalidation_skill(self, broken, label):
        state = _state(_iss(1, validated_against=HEAD), reval=broken)
        with pytest.raises(dl.DriverStateError) as exc:
            dl.validate_queue_revalidation(state)
        assert "revalidate-children" in str(exc.value), f"{label}: {exc.value}"


    def test_a_valid_receipt_still_passes(self):
        state = _state(_iss(1, validated_against=HEAD), reval=_reval(HEAD, {"1": _receipt_child()}))
        assert dl.validate_queue_revalidation(state) is True


class TestRound8AnUnusableStampIsStaleProvenanceNotACrash:
    """**Round 8 sweep, 24 states.** The gate hard-errored on `validated_against: "abc"` while
    `revalidation_worklist` had already been taught at round-3 High 1 to treat exactly that value
    as a baseline problem. Two halves of one feature disagreeing about whether a value is
    recoverable is how the operator ends up holding a traceback."""

    def test_the_gate_reports_it_as_outstanding_rather_than_raising_a_data_error(self):
        state = _state(_iss(1, validated_against="abc"))
        with pytest.raises(dl.QueueRevalidationRequired) as exc:
            dl.next_ready_issue(state, observed_head=HEAD)
        assert "unusable stamp" in str(exc.value), str(exc.value)

    def test_and_the_prescribed_remedy_clears_it(self):
        state = _state(_iss(1, validated_against="abc"))
        assert dl.next_ready_issue(dl.rebuild_receipt(state, HEAD, {1: _receipt_child()}),
                                   observed_head=HEAD) == 1

    def test_a_well_formed_stale_stamp_keeps_its_own_wording(self):
        """The negative twin — 'unusable' must not swallow the ordinary stale case."""
        state = _state(_iss(1, validated_against=OLD), reval=_reval(HEAD, {}))
        with pytest.raises(dl.QueueRevalidationRequired) as exc:
            dl.next_ready_issue(state, observed_head=HEAD)
        assert "stale head" in str(exc.value), str(exc.value)


class TestRound10TheRemainingTwoFindingsInTheShippedHalf:
    """The two round-10 findings that were NOT in the cut owner gate, so they had to be fixed
    rather than removed. Written after a sabotage pass found both fixes untested — the guards
    survived their own sabotage, which under this repo's rules means they were not guards at all."""

    def test_a_head_movement_mid_audit_names_its_remedy(self):
        """All three lenses. `origin/main` moving during a long audit is the most ordinary thing
        that can happen, and it refused with a bare mismatch and no next step."""
        with pytest.raises(dl.DriverStateError) as exc:
            dl.rebuild_receipt(_state(_iss(1)), HEAD, {1: _receipt_child(to_sha=OLD)})
        message = str(exc.value)
        assert "revalidate-children" in message, message
        assert getattr(exc.value, "remedy", None) == "revalidate", message

    def test_and_executing_that_remedy_opens_the_gate(self):
        """Re-auditing against the newly observed head is what the message prescribes."""
        rebuilt = dl.rebuild_receipt(_state(_iss(1)), HEAD, {1: _receipt_child(to_sha=HEAD)})
        assert dl.next_ready_issue(rebuilt, observed_head=HEAD) == 1

    @pytest.mark.parametrize("broken,label", [
        ({"version": 2, "extractor_version": 1, "validated_head": HEAD, "children": {}},
         "unsupported version"),
        ({"version": 1, "extractor_version": 1, "validated_head": HEAD, "children": {"01": {}}},
         "non-canonical key"),
    ])
    def test_a_recoverable_receipt_error_becomes_a_disposition_not_a_traceback(self, broken, label):
        """Round-10 Medium 1. Only `QueueRevalidationRequired` was caught, so these escaped as a
        bare `DriverStateError` — and `launcher_lib.main` catches only `LauncherError`, so the
        real handoff CLI would have exited with an UNCAUGHT TRACEBACK on a state whose own message
        says to fix it by running one skill."""
        state = _state(_iss(1), reval=broken)
        disposition = dl.fresh_session_handoff(
            state, mode=dl.FRESH_SESSION_MODE, observed_head=HEAD)
        assert disposition["outcome"] == "revalidation_required", (label, disposition)
        assert "revalidate-children" in disposition["reason"], label

    def test_a_GENUINELY_corrupt_state_still_propagates(self):
        """The negative twin, and the reason the widening keys on `remedy` rather than catching
        every `DriverStateError`: a state file that is simply unusable must stay rc 2, not be
        dressed up as something one skill run can fix."""
        state = _state(_iss(1))
        state["issues"] = [{"number": "not-an-int", "status": "queued"}]
        with pytest.raises(dl.DriverStateError):
            dl.fresh_session_handoff(state, mode=dl.FRESH_SESSION_MODE, observed_head=HEAD)


class TestRound11AnUnattestedStampIsNotABaseline:
    """**Round 11, High 2.** A `validated_against` stamp was trusted as the left endpoint of the
    diff without checking that anything attested THAT stamp. A child stamped at an old head under
    a CURRENT receipt carrying no entry for it therefore got `baseline="stamp"` and `depth="quick"`
    — and `quick` explicitly takes citation claims as-is, so dropped or corrupt evidence suppressed
    exactly the checks `deep` would have run. That is failing toward LESS scrutiny, the one
    direction this design forbids.

    `rebuild_receipt(state, HEAD, {})` produces this state on its own: it drops a record attesting
    an older head and leaves the matching stamp behind."""

    def _unattested_stale_stamp(self):
        return _state(_iss(1, validated_against=OLD),
                      reval=_reval(HEAD, {}), base_default_branch_sha=None)

    def test_an_unattested_stale_stamp_forces_deep(self):
        work = dl.revalidation_worklist(self._unattested_stale_stamp(), HEAD,
                                        extractions={1: ([], "none")}, changed_by_child={1: set()})
        assert len(work) == 1
        assert work[0]["baseline"] == "unavailable", work[0]
        assert work[0]["depth"] == "deep", work[0]
        assert work[0]["from_sha"] == HEAD, work[0]

    def test_an_ATTESTED_stamp_is_still_a_usable_baseline(self):
        """The negative twin, and the reason this is not just 'always go deep': the ordinary
        incremental case — the receipt attests the same head the child is stamped at — must keep
        its narrow range, or `quick` never applies and the affordability lever is gone."""
        state = _state(_iss(1, validated_against=OLD),
                       reval=_reval(OLD, {"1": _receipt_child(to_sha=OLD)}))
        work = dl.revalidation_worklist(state, HEAD, extractions={1: ([], "none")},
                                        changed_by_child={1: set()})
        assert work[0]["baseline"] == "stamp", work[0]
        assert work[0]["from_sha"] == OLD, work[0]

    def test_a_malformed_stamp_also_fails_toward_more_scrutiny(self):
        state = _state(_iss(1, validated_against="abc"), reval=_reval(HEAD, {}))
        work = dl.revalidation_worklist(state, HEAD, extractions={1: ([], "none")},
                                        changed_by_child={1: set()})
        assert work[0]["depth"] == "deep", work[0]


class TestRound11TheRebuildCLIRefusesCollidingKeys:
    """**Round 11 (two lenses).** `_cmd_rebuild_receipt` converted `audited` keys with
    `{int(key): value}`, so `"1"` and `"01"` collapsed to the same integer: one record silently
    won, the command returned rc 0, and the gate then selected that child on evidence the operator
    never meant to supply. Losing audit evidence while REPORTING SUCCESS is the worst failure mode
    this command has, and it is the same non-canonical-key defect the receipt validator already
    refuses — this path just had its own conversion.

    Driven through the real CLI, because the jam matrix exercises the pure helper and would not
    have caught it (round-11 Medium, and now stated honestly in that file's docstring)."""

    def _campaign(self, tmp_path, audited):
        work, head = _repo_with_origin(tmp_path)
        path = _write_state(tmp_path, _state(_iss(1)))
        audited_path = tmp_path / "audited.json"
        audited_path.write_text(json.dumps(audited(head)), encoding="utf-8")
        return work, head, path, audited_path

    def _record_at(self, head):
        rec = _receipt_child(to_sha=head)
        rec["from_sha"] = OLD
        return rec

    def test_two_spellings_of_one_issue_are_REFUSED(self, tmp_path):
        work, head, path, audited_path = self._campaign(
            tmp_path, lambda h: {"1": self._record_at(h), "01": self._record_at(h)})
        rc = ll.main(["rebuild-receipt", "--driver-state", str(path),
                      "--project-root", str(work), "--audited", str(audited_path)])
        assert rc == 2, rc

    def test_a_non_canonical_key_alone_is_REFUSED(self, tmp_path):
        work, head, path, audited_path = self._campaign(
            tmp_path, lambda h: {"01": self._record_at(h)})
        rc = ll.main(["rebuild-receipt", "--driver-state", str(path),
                      "--project-root", str(work), "--audited", str(audited_path)])
        assert rc == 2, rc

    def test_the_canonical_spelling_still_works(self, tmp_path):
        """The negative twin — refusing collisions must not refuse the ordinary call."""
        work, head, path, audited_path = self._campaign(
            tmp_path, lambda h: {"1": self._record_at(h)})
        rc = ll.main(["rebuild-receipt", "--driver-state", str(path),
                      "--project-root", str(work), "--audited", str(audited_path)])
        assert rc == 0, rc
        persisted = json.loads(path.read_text(encoding="utf-8"))
        assert persisted["queue_revalidation"]["validated_head"] == head
        assert list(persisted["queue_revalidation"]["children"]) == ["1"]


def _corrected(to_sha=HEAD, validated_at=1_754_000_000, url="https://example.invalid/c/1"):
    """A record carrying SUCCESSOR-FACING evidence: a broken claim plus a correction comment."""
    rec = _receipt_child(to_sha=to_sha, verdict="broken")
    rec["outcome"] = "body_corrected"
    rec["correction_comment"] = url
    rec["validated_at"] = validated_at
    return rec


class TestRound12EvidenceTheSuccessorNeedsIsNeverSilentlyDropped:
    """**Round 12, found independently by two lenses, and in the half this PR still ships.**

    `rebuild_receipt` drops any record that does not attest the head being validated. For the
    ACTIVE `in_progress` child that deleted its `body_corrected` record — and the worklist is
    `queued`-only, so nothing re-supplied it. `produce_queue_revalidated` then reported the rung
    PASSED, so the rung authorised the handoff and eventually the teardown while the successor
    lost the mandatory correction and resumed from the stale body.

    That is #840's own thesis defeated on the exact one-`in_progress` path the feature serves.

    Both halves are required. Refusing alone would be another unrecoverable jam — the operator
    needs a way to supply the replacement, which is why the child also becomes a worklist
    candidate."""

    def _active_corrected(self):
        return _state(_iss(1, "in_progress", validated_against=OLD),
                      reval=_reval(OLD, {"1": _corrected(to_sha=OLD)}))

    def test_the_correction_is_in_the_prompt_before_any_rebuild(self):
        """Baseline — without this the test below could pass for the wrong reason."""
        assert "CORRECTION for #1" in dl.corrections_clause(self._active_corrected(), 1)

    def test_rebuilding_REFUSES_to_drop_it(self):
        with pytest.raises(dl.DriverStateError) as exc:
            dl.rebuild_receipt(self._active_corrected(), HEAD, {})
        message = str(exc.value)
        assert "#1" in message, message
        assert "revalidate-children" in message, message
        assert getattr(exc.value, "remedy", None) == "revalidate", message

    def test_the_worklist_OFFERS_that_child_so_the_refusal_is_clearable(self):
        """The remedy half. A `queued`-only worklist is what made the evidence unreplaceable."""
        work = dl.revalidation_worklist(self._active_corrected(), HEAD,
                                        extractions={1: ([], "none")}, changed_by_child={1: set()})
        assert [item["number"] for item in work] == [1], work

    def test_and_supplying_the_replacement_keeps_the_correction(self):
        """Executing the prescribed remedy must reach a good state AND retain the evidence."""
        rebuilt = dl.rebuild_receipt(self._active_corrected(), HEAD, {1: _corrected(to_sha=HEAD)})
        assert "CORRECTION for #1" in dl.corrections_clause(rebuilt, 1)
        assert rebuilt["queue_revalidation"]["validated_head"] == HEAD

    def test_a_child_with_NO_successor_facing_evidence_is_still_dropped_freely(self):
        """The negative twin — this must not become 'never drop anything', which would resurrect
        the corrupt-record jam round 8 closed."""
        state = _state(_iss(1, "in_progress", validated_against=OLD),
                       reval=_reval(OLD, {"1": _receipt_child(to_sha=OLD)}))
        rebuilt = dl.rebuild_receipt(state, HEAD, {})
        assert rebuilt["queue_revalidation"]["children"] == {}

    def test_a_DISPOSED_child_may_lose_its_record(self):
        """Nobody will be handed a merged child, so its correction has no consumer left."""
        state = _state(_iss(1, "merged", validated_against=OLD),
                       reval=_reval(OLD, {"1": _corrected(to_sha=OLD)}))
        assert dl.rebuild_receipt(state, HEAD, {})["queue_revalidation"]["children"] == {}

    def test_an_OLDER_audit_cannot_overwrite_a_newer_same_head_record(self):
        """Round 12 lens B. The lock serialises WRITES; it does not order EVIDENCE. A session
        holding a record prepared earlier could replace a newer correction at the same head."""
        newer = _state(_iss(1), reval=_reval(HEAD, {"1": _corrected(to_sha=HEAD,
                                                                   validated_at=200)}))
        older = _receipt_child(to_sha=HEAD)
        older["validated_at"] = 100
        with pytest.raises(dl.DriverStateError, match="older"):
            dl.rebuild_receipt(newer, HEAD, {1: older})

    def test_a_NEWER_audit_replaces_it_normally(self):
        """The negative twin — ordinary re-auditing must still work."""
        state = _state(_iss(1), reval=_reval(HEAD, {"1": _corrected(to_sha=HEAD,
                                                                   validated_at=100)}))
        fresh = _receipt_child(to_sha=HEAD)
        fresh["validated_at"] = 200
        rebuilt = dl.rebuild_receipt(state, HEAD, {1: fresh})
        assert rebuilt["queue_revalidation"]["children"]["1"]["validated_at"] == 200


class TestRound12LiteralDuplicateJsonKeys:
    """**Round 12.** The round-11 canonical-key fix catches `"1"` versus `"01"` but not `"1"`
    twice in the same object: `json.load` silently keeps the last BEFORE the check can see either.
    Audit evidence is discarded while the command reports success and opens the gate — the same
    defect the round-11 fix was written for, one layer earlier in the pipeline.

    Written with literal duplicate JSON TEXT, because a Python dict cannot express it — which is
    exactly why the round-11 test missed it."""

    def test_duplicate_properties_are_REFUSED(self, tmp_path):
        work, head = _repo_with_origin(tmp_path)
        path = _write_state(tmp_path, _state(_iss(1)))
        rec = json.dumps(_receipt_child(to_sha=head))
        audited = tmp_path / "audited.json"
        audited.write_text(f'{{"1": {rec}, "1": {rec}}}', encoding="utf-8")
        rc = ll.main(["rebuild-receipt", "--driver-state", str(path),
                      "--project-root", str(work), "--audited", str(audited)])
        assert rc == 2, rc

    def test_a_single_property_still_works(self, tmp_path):
        work, head = _repo_with_origin(tmp_path)
        path = _write_state(tmp_path, _state(_iss(1)))
        audited = tmp_path / "audited.json"
        audited.write_text(json.dumps({"1": _receipt_child(to_sha=head)}), encoding="utf-8")
        rc = ll.main(["rebuild-receipt", "--driver-state", str(path),
                      "--project-root", str(work), "--audited", str(audited)])
        assert rc == 0, rc
