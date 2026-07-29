"""#695 — a child shipped OUTSIDE the epic driver must still write its terminal status back.

The live defect: `claude_docs/.driver-state/epic-684-watcher-fires.json` still read
`{"number": 687, "status": "queued"}` after #687 was merged (PR #691) and closed, so a
fresh-session resume — obeying its own correct rule, "derive position from durable state,
never in-context memory" — announced #687 as the next ready child.

Two halves, belt-and-braces, because either alone leaves a live failure:

  AC1/AC3  the write-back, driven from the SINGLE-SESSION path (not the driver) — without
           it the file goes stale on every non-driver child
  AC2      the resume path refuses to believe `queued` about an issue that really shipped —
           without it the files ALREADY stale on disk still cause the harm

Three of these tests exist because the cross-model design review refuted an earlier draft
of this very file: the probe verdict vocabulary is a tri-state rather than a boolean, a
confirmed-merged prerequisite must satisfy its DEPENDENTS (not merely be skipped), and
status validation must check a legal TRANSITION rather than mere vocabulary membership.
"""

import json
import os
import subprocess
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HOOKS = os.path.join(REPO_ROOT, "hooks")
sys.path.insert(0, HOOKS)

import driver_lib as dl  # noqa: E402
import launcher_lib as ll  # noqa: E402

LAUNCHER_CLI = os.path.join(HOOKS, "launcher_lib.py")


def _state(*issues, **extra):
    st = {"schema_version": 2, "campaign": "epic-684-watcher-fires",
          "issues": [dict(i) for i in issues]}
    st.update(extra)
    return st


def _iss(number, status, **extra):
    d = {"number": number, "status": status}
    d.update(extra)
    return d


def _campaign(tmp_path, state, name="epic-684-watcher-fires.json"):
    """A driver-state file at the real discovered location."""
    root = tmp_path / "claude_docs" / ".driver-state"
    root.mkdir(parents=True, exist_ok=True)
    p = root / name
    p.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    return str(p)


class FakeProc:
    def __init__(self, returncode=0, stdout=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = ""


# ---------------------------------------------------------------------------
# AC1 — the pure transition, with a transition TABLE (review finding H4)
# ---------------------------------------------------------------------------

class TestRecordChildOutcomeIsPure:
    def test_a_queued_child_becomes_merged(self):
        st = _state(_iss(686, "merged"), _iss(687, "queued"))
        new = dl.record_child_outcome(st, 687, "merged")
        assert [i["status"] for i in new["issues"]] == ["merged", "merged"]

    def test_the_input_state_is_not_mutated(self):
        st = _state(_iss(687, "queued"))
        before = json.dumps(st, sort_keys=True)
        dl.record_child_outcome(st, 687, "merged")
        assert json.dumps(st, sort_keys=True) == before

    @pytest.mark.parametrize("bad", ["done", "complete", "MERGED", "", None, 3, True])
    def test_an_off_vocabulary_status_is_refused(self, bad):
        st = _state(_iss(687, "queued"))
        with pytest.raises(dl.DriverStateError):
            dl.record_child_outcome(st, 687, bad)

    def test_an_issue_the_queue_does_not_name_returns_None(self):
        """None is `_locked_state_update`'s abort signal, so a run on an issue outside this
        campaign writes nothing rather than inventing a queue entry."""
        st = _state(_iss(687, "queued"))
        assert dl.record_child_outcome(st, 999, "merged") is None

    def test_recording_the_status_it_already_has_returns_None(self):
        """Idempotent: the write is invoked at BOTH the merge confirmation and Step 16's
        reconciliation, so the second must be free."""
        st = _state(_iss(687, "merged"))
        assert dl.record_child_outcome(st, 687, "merged") is None

    @pytest.mark.parametrize("terminal", sorted(dl.TERMINAL_STATUSES))
    @pytest.mark.parametrize("to", ["queued", "in_progress", "pr_open"])
    def test_a_terminal_status_is_never_regressed(self, terminal, to):
        """Review finding H4: membership in VALID_STATUSES proves a legal WORD, not a legal
        TRANSITION. Walking a merged child back to `queued` is how a shipped issue gets
        re-run — the exact harm #695 is about."""
        st = _state(_iss(687, terminal))
        with pytest.raises(dl.DriverStateError, match="terminal"):
            dl.record_child_outcome(st, 687, to)

    def test_deferred_is_NOT_terminal_so_a_parked_child_can_be_requeued(self):
        st = _state(_iss(687, "deferred"))
        assert dl.record_child_outcome(st, 687, "queued")["issues"][0]["status"] == "queued"

    def test_a_malformed_queue_fails_closed(self):
        with pytest.raises(dl.DriverStateError):
            dl.record_child_outcome({"issues": [{"status": "queued"}]}, 687, "merged")


# ---------------------------------------------------------------------------
# AC1 + AC3 — the SINGLE-SESSION path, end to end through the CLI
# ---------------------------------------------------------------------------

class TestTheSingleSessionPathWritesBack:
    """AC3 is explicit that the test drives the single-session path, NOT the driver path: the
    driver already writes this file when it sequences children, so a test routed through the
    driver would pass while the real defect stayed live."""

    def _run(self, tmp_path, issue, status, *extra):
        return subprocess.run(
            [sys.executable, LAUNCHER_CLI, "record-child-outcome",
             "--issue", str(issue), "--status", status,
             "--project-root", str(tmp_path), *extra],
            capture_output=True, text=True, check=False)

    def test_the_exact_684_regression(self, tmp_path):
        """The observed file verbatim: #687 still `queued` after PR #691 merged. After the
        write-back a resume must no longer offer it."""
        path = _campaign(tmp_path, _state(_iss(687, "queued")))
        proc = self._run(tmp_path, 687, "merged")
        assert proc.returncode == 0, proc.stderr
        after = json.loads(open(path, encoding="utf-8").read())
        assert after["issues"][0]["status"] == "merged"
        assert dl.next_ready_issue(after) is None, \
            "a merged child is still offered as the next ready child"

    def test_the_campaign_is_DISCOVERED_without_being_named(self, tmp_path):
        """A single-session WF2 run does not know its campaign, so passing the path only
        moved the problem (review finding M1)."""
        path = _campaign(tmp_path, _state(_iss(687, "queued")))
        assert self._run(tmp_path, 687, "merged").returncode == 0
        assert json.loads(open(path, encoding="utf-8").read())["issues"][0]["status"] \
            == "merged"

    def test_EVERY_campaign_naming_the_issue_is_updated(self, tmp_path):
        """M1's cardinality rule: writing only the first match leaves the others stale, which
        is the defect. Both are authoritative campaigns."""
        a = _campaign(tmp_path, _state(_iss(687, "queued")), "epic-684.json")
        b = _campaign(tmp_path, _state(_iss(687, "queued")), "epic-999.json")
        assert self._run(tmp_path, 687, "merged").returncode == 0
        for p in (a, b):
            assert json.loads(open(p, encoding="utf-8").read())["issues"][0]["status"] \
                == "merged", f"{p} left stale"

    def test_no_campaign_at_all_is_fail_open(self, tmp_path):
        """A single-session run outside ANY campaign is the normal case; failing here would
        end every ordinary WF2 run on a red step."""
        assert self._run(tmp_path, 687, "merged").returncode == 0

    def test_an_issue_no_campaign_names_is_fail_open(self, tmp_path):
        path = _campaign(tmp_path, _state(_iss(687, "queued")))
        assert self._run(tmp_path, 999, "merged").returncode == 0
        assert json.loads(open(path, encoding="utf-8").read())["issues"][0]["status"] \
            == "queued"

    def test_fail_open_explains_itself_on_BOTH_streams(self, tmp_path):
        """Review finding M2: a reason that only reaches stdout is lost to any wrapper that
        captures one stream, and then a real miss looks like a deliberate no-op."""
        proc = self._run(tmp_path, 687, "merged")
        assert proc.stdout.strip(), "nothing on stdout"
        assert proc.stderr.strip(), "nothing on stderr"

    def test_an_off_vocabulary_status_is_refused_at_the_CLI(self, tmp_path):
        """Fail-open covers "no campaign names this issue". It does NOT cover a caller
        mismatch — a bad status is a bug at the call site and must be loud."""
        path = _campaign(tmp_path, _state(_iss(687, "queued")))
        proc = self._run(tmp_path, 687, "done")
        assert proc.returncode != 0
        assert json.loads(open(path, encoding="utf-8").read())["issues"][0]["status"] \
            == "queued"

    def test_a_terminal_regression_is_refused_at_the_CLI(self, tmp_path):
        path = _campaign(tmp_path, _state(_iss(687, "merged")))
        proc = self._run(tmp_path, 687, "queued")
        assert proc.returncode != 0
        assert json.loads(open(path, encoding="utf-8").read())["issues"][0]["status"] \
            == "merged"

    def test_a_corrupt_campaign_file_is_left_for_a_human(self, tmp_path):
        root = tmp_path / "claude_docs" / ".driver-state"
        root.mkdir(parents=True)
        (root / "broken.json").write_text("{not json", encoding="utf-8")
        proc = self._run(tmp_path, 687, "merged")
        # discovery skips what it cannot parse, so this is a fail-open no-op, not a rewrite
        assert proc.returncode == 0
        assert (root / "broken.json").read_text(encoding="utf-8") == "{not json"

    def test_an_explicit_path_is_still_honoured(self, tmp_path):
        path = _campaign(tmp_path, _state(_iss(687, "queued")))
        assert self._run(tmp_path, 687, "merged", "--driver-state", path).returncode == 0
        assert json.loads(open(path, encoding="utf-8").read())["issues"][0]["status"] \
            == "merged"

    def test_the_write_goes_through_the_one_locked_writer(self):
        """#665's invariant: exactly ONE locked read -> mutate -> atomic-replace cycle for
        driver state. A second, unlocked writer would erase a concurrent claim."""
        src = open(os.path.join(HOOKS, "launcher_lib.py"), encoding="utf-8").read()
        i = src.index("def _cmd_record_child_outcome")
        assert "_locked_state_update" in src[i:i + 3000], \
            "the write-back does not use the existing locked writer"

    def test_the_subcommand_is_registered(self):
        proc = subprocess.run([sys.executable, LAUNCHER_CLI, "--help"],
                              capture_output=True, text=True, check=False)
        assert "record-child-outcome" in proc.stdout


# ---------------------------------------------------------------------------
# AC2 — never believe `queued` about an issue that really shipped
# ---------------------------------------------------------------------------

class TestTheResumePathCorroboratesQueued:
    def test_a_queued_child_that_really_merged_is_skipped(self):
        st = _state(_iss(687, "queued"), _iss(688, "queued"))
        probe = {687: "confirmed_merged", 688: "confirmed_open"}.get
        assert dl.next_ready_issue(st, issue_state_probe=probe) == 688

    def test_a_queued_child_closed_WITHOUT_a_merged_pr_is_also_skipped(self):
        """Closed as not-planned is not merged, but it is still not work to pick up."""
        st = _state(_iss(687, "queued"), _iss(688, "queued"))
        probe = {687: "confirmed_abandoned", 688: "confirmed_open"}.get
        assert dl.next_ready_issue(st, issue_state_probe=probe) == 688

    def test_with_no_probe_the_behaviour_is_exactly_todays(self):
        """#163's contract and every existing caller stay byte-identical — the probe is an
        addition, not a change."""
        st = _state(_iss(687, "queued"), _iss(688, "queued"))
        assert dl.next_ready_issue(st) == 687
        assert dl.next_ready_issue(st, "merged") == 687

    def test_a_confirmed_merged_prerequisite_SATISFIES_its_dependents(self):
        """Review finding H3, and the case an earlier draft of this file got backwards.
        Skipping the stale child is not enough: if the overlay does not also reach dependency
        evaluation, a campaign whose prerequisite really merged reports "no ready child"
        forever while that prerequisite sits merged on GitHub."""
        st = _state(_iss(687, "queued"), _iss(688, "queued", depends_on=[687]))
        probe = {687: "confirmed_merged", 688: "confirmed_open"}.get
        assert dl.next_ready_issue(st, issue_state_probe=probe) == 688

    def test_a_confirmed_abandoned_prerequisite_does_NOT_satisfy_dependents(self):
        """The mirror of the above, and why the verdict is a tri-state rather than a boolean:
        an abandoned prerequisite parks its dependents, exactly as the file's own
        `abandoned` status would."""
        st = _state(_iss(687, "queued"), _iss(688, "queued", depends_on=[687]))
        probe = {687: "confirmed_abandoned", 688: "confirmed_open"}.get
        assert dl.next_ready_issue(st, issue_state_probe=probe) is None

    @pytest.mark.parametrize("verdict", ["unknown", None, "", "garbage"])
    def test_an_unusable_verdict_keeps_the_candidate(self, verdict):
        """Deliberate direction, and tested so it cannot be "tidied". The probe is
        CORROBORATION; the file is primary once AC1 keeps it correct. Turning a GitHub outage
        into a total campaign stall is worse than a visible duplicate PR — the stall is the
        silent one."""
        st = _state(_iss(687, "queued"))
        assert dl.next_ready_issue(st, issue_state_probe=lambda _n: verdict) == 687

    def test_a_raising_probe_keeps_the_candidate(self):
        def boom(_n):
            raise OSError("gh unreachable")
        assert dl.next_ready_issue(_state(_iss(687, "queued")),
                                  issue_state_probe=boom) == 687

    def test_only_queued_entries_are_probed(self):
        """It costs a network call per candidate, so it must only run where the status is the
        one we actually distrust."""
        asked = []

        def probe(n):
            asked.append(n)
            return "confirmed_open"
        st = _state(_iss(686, "merged"), _iss(687, "in_progress"), _iss(688, "queued"))
        dl.next_ready_issue(st, issue_state_probe=probe)
        assert asked == [688], f"probed non-queued entries: {asked}"

    def test_driver_lib_still_does_no_io(self):
        """The probe is INJECTED precisely so this stays true — the module docstring promises
        no I/O and the docs run it from a `python3 -c` one-liner."""
        src = open(os.path.join(HOOKS, "driver_lib.py"), encoding="utf-8").read()
        for banned in ("import subprocess", "import urllib", "import requests",
                       "subprocess.run", "urlopen"):
            assert banned not in src, f"driver_lib gained I/O: {banned}"


class TestTheProbeReachesTheRealCallSite:
    """Review finding H1 — the sharpest one. An OPTIONAL probe that no caller passes ships
    AC2 dead, and `fresh_session_handoff` is the ONE production selection site."""

    def test_fresh_session_handoff_threads_the_probe_into_selection(self):
        st = _state(_iss(687, "queued"), _iss(688, "queued"),
                    project="rawgentic", epic=684, generation=1)
        disp = dl.fresh_session_handoff(
            st, mode=dl.FRESH_SESSION_MODE, project="rawgentic",
            issue_state_probe={687: "confirmed_merged", 688: "confirmed_open"}.get)
        assert disp["outcome"] == "ready"
        assert disp["next_issue"] == 688, \
            "the handoff selected a child the probe proved already merged"

    def test_a_fully_shipped_but_stale_campaign_reports_COMPLETE(self):
        """Otherwise the epic stays open forever with nothing runnable — the same stale-file
        defect wearing a different outcome."""
        st = _state(_iss(687, "queued"), project="rawgentic", epic=684)
        disp = dl.fresh_session_handoff(
            st, mode=dl.FRESH_SESSION_MODE, project="rawgentic",
            issue_state_probe=lambda _n: "confirmed_merged")
        assert disp["outcome"] == "complete"

    def test_without_a_probe_the_stale_campaign_still_offers_the_merged_child(self):
        """The defect, pinned. This is what the file alone does, and it is why AC2 exists."""
        st = _state(_iss(687, "queued"), project="rawgentic", epic=684, generation=1)
        disp = dl.fresh_session_handoff(st, mode=dl.FRESH_SESSION_MODE,
                                       project="rawgentic")
        assert disp["outcome"] == "ready" and disp["next_issue"] == 687


# ---------------------------------------------------------------------------
# The probe itself — verdict mapping and the verified query
# ---------------------------------------------------------------------------

class TestClassifyIssueState:
    def test_the_REAL_687_payload_reads_as_confirmed_merged(self):
        """Captured verbatim from `gh api graphql` on this host — the actual regression:
        #687 closed by merged PR #691."""
        payload = {"data": {"repository": {"issue": {
            "number": 687, "state": "CLOSED", "stateReason": "COMPLETED",
            "closedByPullRequestsReferences": {"nodes": [{"number": 691, "merged": True}]}}}}}
        assert ll.classify_issue_state(payload) == "confirmed_merged"

    def test_closed_with_no_merged_pr_is_abandoned(self):
        payload = {"data": {"repository": {"issue": {
            "state": "CLOSED", "stateReason": "NOT_PLANNED",
            "closedByPullRequestsReferences": {"nodes": []}}}}}
        assert ll.classify_issue_state(payload) == "confirmed_abandoned"

    def test_closed_by_an_UNMERGED_pr_is_abandoned_not_merged(self):
        payload = {"data": {"repository": {"issue": {
            "state": "CLOSED",
            "closedByPullRequestsReferences": {"nodes": [{"number": 5, "merged": False}]}}}}}
        assert ll.classify_issue_state(payload) == "confirmed_abandoned"

    def test_open_is_confirmed_open(self):
        assert ll.classify_issue_state(
            {"data": {"repository": {"issue": {"state": "OPEN"}}}}) == "confirmed_open"

    @pytest.mark.parametrize("payload", [
        {}, None, {"data": None}, {"data": {"repository": None}},
        {"data": {"repository": {"issue": None}}},
        {"data": {"repository": {"issue": {"state": 3}}}},
        {"data": {"repository": {"issue": {"state": "WEIRD"}}}},
        {"errors": [{"message": "rate limited"}]},
    ])
    def test_anything_unreadable_is_unknown_never_a_guess(self, payload):
        assert ll.classify_issue_state(payload) == "unknown"


class TestBuildIssueStateProbe:
    def test_a_successful_query_is_classified(self):
        body = json.dumps({"data": {"repository": {"issue": {
            "state": "CLOSED",
            "closedByPullRequestsReferences": {"nodes": [{"merged": True}]}}}}})
        probe = ll.build_issue_state_probe(
            "3D-Stories/rawgentic", runner=lambda _a, _t=None: FakeProc(0, body))
        assert probe(687) == "confirmed_merged"

    def test_the_query_asks_graphql_because_issue_view_cannot_answer(self):
        """`gh issue view --json` exposes neither `stateReason` nor
        `closedByPullRequestsReferences` on the installed CLI — verified, and the reason this
        is a graphql call rather than the simpler one."""
        seen = {}

        def runner(argv, _t=None):
            seen["argv"] = argv
            return FakeProc(0, "{}")
        ll.build_issue_state_probe("o/n", runner=runner)(687)
        assert seen["argv"][:3] == ["gh", "api", "graphql"]
        assert "closedByPullRequestsReferences" in seen["argv"][-1]

    @pytest.mark.parametrize("proc", [FakeProc(1, ""), FakeProc(0, "not json"),
                                      FakeProc(0, "")])
    def test_any_failure_degrades_to_unknown(self, proc):
        assert ll.build_issue_state_probe("o/n", runner=lambda _a, _t=None: proc)(687) \
            == "unknown"

    def test_a_raising_runner_degrades_to_unknown(self):
        def boom(_a, _t=None):
            raise OSError("no gh")
        assert ll.build_issue_state_probe("o/n", runner=boom)(687) == "unknown"

    @pytest.mark.parametrize("bad", ["", "noslash", None, "/", "owner/"])
    def test_an_unusable_repo_degrades_to_unknown(self, bad):
        assert ll.build_issue_state_probe(bad)(687) == "unknown"

    @pytest.mark.parametrize("bad", ["12; rm -rf /", "abc", "", None, True])
    def test_a_non_numeric_issue_is_never_interpolated(self, bad):
        """The number reaches a query string, so it is validated as digits rather than
        trusted."""
        called = []

        def runner(argv, _t=None):
            called.append(argv)
            return FakeProc(0, "{}")
        assert ll.build_issue_state_probe("o/n", runner=runner)(bad) == "unknown"
        assert called == [], "a non-numeric issue reached the query"


class TestRepoFromGit:
    @pytest.mark.parametrize("url,expected", [
        ("https://github.com/3D-Stories/rawgentic.git", "3D-Stories/rawgentic"),
        ("https://github.com/3D-Stories/rawgentic", "3D-Stories/rawgentic"),
        ("git@github.com:3D-Stories/rawgentic.git", "3D-Stories/rawgentic"),
    ])
    def test_both_remote_forms_reduce_to_owner_name(self, url, expected):
        assert ll.repo_from_git(".", runner=lambda _a, _t=None: FakeProc(0, url)) == expected

    @pytest.mark.parametrize("proc", [FakeProc(1, ""), FakeProc(0, ""), FakeProc(0, "junk")])
    def test_any_failure_is_None_so_AC2_degrades_rather_than_breaking(self, proc):
        assert ll.repo_from_git(".", runner=lambda _a, _t=None: proc) is None

    def test_it_is_derived_rather_than_passed_as_a_flag(self):
        """H1's lesson generalised: an optional flag nobody passes ships the feature dead, so
        the repo is derived from the project's own remote."""
        src = open(os.path.join(HOOKS, "launcher_lib.py"), encoding="utf-8").read()
        assert "repo_from_git" in src


class TestDiscoverDriverStates:
    def test_only_files_naming_the_issue_match(self, tmp_path):
        _campaign(tmp_path, _state(_iss(687, "queued")), "a.json")
        _campaign(tmp_path, _state(_iss(999, "queued")), "b.json")
        hits = ll.discover_driver_states(str(tmp_path), 687)
        assert [os.path.basename(h) for h in hits] == ["a.json"]

    def test_matches_come_back_in_sorted_order(self, tmp_path):
        for name in ("c.json", "a.json", "b.json"):
            _campaign(tmp_path, _state(_iss(687, "queued")), name)
        hits = ll.discover_driver_states(str(tmp_path), 687)
        assert [os.path.basename(h) for h in hits] == ["a.json", "b.json", "c.json"]

    def test_an_absent_directory_is_empty_not_an_error(self, tmp_path):
        assert ll.discover_driver_states(str(tmp_path), 687) == []

    def test_an_unparseable_sibling_does_not_hide_a_good_match(self, tmp_path):
        root = tmp_path / "claude_docs" / ".driver-state"
        root.mkdir(parents=True)
        (root / "aaa-broken.json").write_text("{not json", encoding="utf-8")
        _campaign(tmp_path, _state(_iss(687, "queued")), "zzz-good.json")
        hits = ll.discover_driver_states(str(tmp_path), 687)
        assert [os.path.basename(h) for h in hits] == ["zzz-good.json"]


class TestTheIssueProbeDefaultsOn:
    """#695 review finding H1, generalised into a guard.

    The probe nearly shipped dead as an optional parameter nobody passed. It is now opt-OUT, and
    that direction is pinned: a future change that flips the default to off would silently
    disable AC2 everywhere while every other test still passed.
    """

    def test_the_default_is_ON(self):
        assert ll.issue_probe_enabled({}) is True
        assert ll.issue_probe_enabled({"OTHER": "0"}) is True

    @pytest.mark.parametrize("value", ["0", "off", "false", "no", "OFF", "False"])
    def test_it_can_be_switched_off_for_deterministic_tests(self, value):
        assert ll.issue_probe_enabled({ll.ISSUE_PROBE_ENV: value}) is False

    @pytest.mark.parametrize("value", ["1", "on", "true", "yes", "", "garbage"])
    def test_anything_else_leaves_it_ON(self, value):
        """Fail-safe direction: an unrecognised value must not quietly disable corroboration."""
        assert ll.issue_probe_enabled({ll.ISSUE_PROBE_ENV: value}) is True

    def test_the_handoff_cli_asks_for_a_probe_at_all(self):
        """The wiring itself, pinned: `_cmd_handoff` must reach the probe builder. Without this
        the parameter exists, the tests pass, and AC2 does nothing in production."""
        src = open(os.path.join(HOOKS, "launcher_lib.py"), encoding="utf-8").read()
        i = src.index("def _cmd_handoff")
        # slice to the next TOP-LEVEL def, not a fixed byte window: this function is ~150 lines
        # and a short window silently cut off before the wiring it is meant to check
        j = src.index("\ndef ", i + 1)
        body = src[i:j]
        assert "_issue_state_probe_for" in body
        assert "issue_state_probe=probe" in body
