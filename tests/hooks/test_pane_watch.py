"""Tests for the blocked -> notify-owner watcher (#612, epic #667).

**Every event shape here was OBSERVED on the wire, not inferred from the schema.** The first version
of these tests synthesised `pane_agent_status_changed` frames carrying a `state_change_seq`, and both
the event kind and the field turned out not to exist as used — the tests passed by feeding an
invention back to itself, which is the exact defect class this epic has now hit five times. So:

- `pane.agent_status_changed` was never observed firing (0 frames across 135 s on 8 panes and 90 s on
  two), needs a `pane_id`, and carries no sequence number. It is not used.
- `pane.updated` IS global, fires on real status changes, and embeds a pane record with
  `agent_status`, `label`, `workspace_id`, `tab_id`, `agent` and a monotonic `revision`.
- A fresh subscription replays a backlog: created/closed frames for six long-gone panes inside 450 ms.
- Wire names are snake_case; subscription names are dotted.

The fixtures below are trimmed copies of real captured frames.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
HOOKS = REPO_ROOT / "hooks"
if str(HOOKS) not in sys.path:
    sys.path.insert(0, str(HOOKS))

import pane_watch_lib as pw  # noqa: E402

# A real `pane_updated` frame, trimmed. Note `terminal_title`: pane CONTENTS ride along on every
# single one of these, which is why AC5 is about provenance rather than keyword hygiene.
def _updated(pane_id, status, revision=1, label=None, name=None):
    pane = {"agent": "claude", "agent_status": status, "cwd": "/home/x",
            "focused": False, "pane_id": pane_id, "revision": revision,
            "tab_id": "w1:t1", "workspace_id": "w1",
            "terminal_title": "SECRET prompt text", "terminal_title_stripped": "SECRET prompt text"}
    if label:
        pane["label"] = label
    if name:
        pane["name"] = name
    return json.dumps({"data": {"pane": pane, "type": "pane_updated"}, "event": "pane_updated"})


def _closed(pane_id):
    return json.dumps({"data": {"pane_id": pane_id, "type": "pane_closed", "workspace_id": "w1"},
                       "event": "pane_closed"})


def _created(pane_id, name=None):
    pane = {"pane_id": pane_id, "workspace_id": "w1", "agent_status": "unknown", "revision": 0}
    if name:
        pane["name"] = name
    return json.dumps({"data": {"pane": pane, "type": "pane_created"}, "event": "pane_created"})


def _snapshot(panes):
    """Shaped like the real `herdr api snapshot`: panes carry `revision`, NOT `state_change_seq`
    (that lives on `snapshot.agents`), and NOT `label`."""
    return {"snapshot": {"panes": [
        {"pane_id": p, "workspace_id": "w1", "tab_id": "w1:t1", "agent_status": s,
         "revision": r, "name": n, "terminal_title": "SECRET prompt text"}
        for p, s, r, n in panes]}}


def _agent_snapshot(panes):
    """`_snapshot` plus the `agents` node, i.e. panes that HAVE an agent.

    The poll source diffs only agent-bearing panes (a pane with no agent cannot be an agent waiting
    on a human), so a poll fixture needs this shape. `state_change_seq` is set EQUAL to the pane's
    `revision` here purely so these fixtures keep the same advancement arithmetic they had before the
    key changed — the live shapes differ (seq ~2000 vs revision <25), and
    `_snapshot_with_agents` is the fixture for asserting that difference.
    """
    doc = _snapshot(panes)
    doc["snapshot"]["agents"] = [
        {"pane_id": p, "agent_status": s, "revision": r, "state_change_seq": r, "agent": "claude"}
        for p, s, r, _n in panes]
    return doc


class TestSubscriptionIsGlobal:
    def test_only_global_subscriptions_are_requested(self):
        """`pane.agent_status_changed` needs a pane_id and was never observed firing; subscriptions
        are fixed per connection, so a per-pane design leaves later panes uncovered forever."""
        subs = pw.build_subscriptions()
        assert {s["type"] for s in subs} == {"pane.updated", "pane.created", "pane.closed"}
        for s in subs:
            assert "pane_id" not in s

    def test_output_matched_is_never_requested(self):
        """It carries `matched_line` and a whole `read.text`. AC5 starts by not receiving contents."""
        assert all(s["type"] != "pane.output_matched" for s in pw.build_subscriptions())

    def test_the_request_is_a_valid_events_subscribe(self):
        req = pw.subscribe_request()
        assert req["method"] == "events.subscribe"
        assert isinstance(req["params"]["subscriptions"], list)


class TestParsingRealFrames:
    def test_a_real_pane_updated_frame_parses(self):
        ev = pw.parse_event(_updated("w1:pA", "blocked", revision=7, label="alpha"))
        assert ev is not None
        assert (ev.kind, ev.pane_id, ev.status, ev.revision) == ("pane_updated", "w1:pA",
                                                                 "blocked", 7)
        assert ev.pane["label"] == "alpha"

    def test_the_dotted_subscription_name_is_not_what_arrives(self):
        assert pw.parse_event(json.dumps({"event": "pane.updated",
                                          "data": {"pane": {"pane_id": "w1:pA"}}})) is None

    def test_pane_agent_status_changed_is_not_consumed(self):
        """Pinning the decision: this kind was never observed firing, so nothing depends on it."""
        line = json.dumps({"event": "pane_agent_status_changed",
                           "data": {"pane_id": "w1:pA", "agent_status": "blocked"}})
        assert pw.parse_event(line) is None

    def test_an_unknown_status_yields_no_status(self):
        ev = pw.parse_event(_updated("w1:pA", "banana", revision=2))
        assert ev is not None and ev.status is None

    def test_the_ack_and_error_frames_are_distinguished(self):
        assert pw.is_subscription_ack(
            json.dumps({"id": "x", "result": {"type": "subscription_started"}})) is True
        assert pw.is_subscription_ack(_updated("w1:pA", "idle")) is False
        err = pw.parse_error(json.dumps({"id": "", "error": {"code": "invalid_request",
                                                            "message": "missing field `pane_id`"}}))
        assert err and "pane_id" in err

    @pytest.mark.parametrize("junk", ["", "  ", "{not json", '{"event":"pane_updated"}',
                                      '{"event":"pane_updated","data":{"pane":{}}}'])
    def test_malformed_lines_yield_none_rather_than_raising(self, junk):
        assert pw.parse_event(junk) is None


class TestBlockedTransition:
    def test_into_blocked_is_a_transition(self):
        assert pw.is_blocked_transition("working", "blocked") is True

    def test_blocked_to_blocked_is_not(self):
        assert pw.is_blocked_transition("blocked", "blocked") is False

    def test_a_first_observation_of_blocked_IS_a_transition(self):
        """An agent already waiting when the watcher starts. Requiring a prior status would miss it,
        which is F2's failure mode."""
        assert pw.is_blocked_transition(None, "blocked") is True

    @pytest.mark.parametrize("status", ["idle", "working", "done", "unknown"])
    def test_leaving_blocked_notifies_nothing(self, status):
        assert pw.is_blocked_transition("blocked", status) is False


class TestReconciliationDefeatsTheReplay:
    def _rec(self):
        return pw.Reconciler(_snapshot([("w1:pA", "working", 5, "alpha"),
                                        ("w1:pB", "idle", 2, "beta")]))

    def test_an_event_for_a_vanished_pane_is_dropped(self):
        assert self._rec().accepts(pw.parse_event(_updated("w1:pB9", "blocked", 1))) is False

    def test_a_stale_revision_is_dropped(self):
        rec = self._rec()
        assert rec.accepts(pw.parse_event(_updated("w1:pA", "blocked", 4))) is False
        assert rec.accepts(pw.parse_event(_updated("w1:pA", "blocked", 5))) is False

    def test_a_newer_revision_is_accepted_once(self):
        rec = self._rec()
        assert rec.accepts(pw.parse_event(_updated("w1:pA", "blocked", 6))) is True
        assert rec.accepts(pw.parse_event(_updated("w1:pA", "blocked", 6))) is False

    def test_a_revisionless_event_FAILS_CLOSED(self):
        """Without a revision there is no way to tell a replay from a live event, and guessing wrong
        pages the owner about a dead pane."""
        line = json.dumps({"event": "pane_updated",
                           "data": {"pane": {"pane_id": "w1:pA", "agent_status": "blocked"}}})
        assert self._rec().accepts(pw.parse_event(line)) is False

    def test_a_pane_created_after_the_snapshot_can_be_learned(self):
        rec = self._rec()
        assert rec.accepts(pw.parse_event(_updated("w1:pNEW", "blocked", 1))) is False
        rec.register_pane({"pane_id": "w1:pNEW", "workspace_id": "w1", "name": "newborn"},
                          live_pane_ids={"w1:pNEW"})
        assert rec.accepts(pw.parse_event(_updated("w1:pNEW", "blocked", 1))) is True

    def test_a_closed_pane_is_forgotten(self):
        rec = self._rec()
        rec.forget_pane("w1:pA")
        assert rec.accepts(pw.parse_event(_updated("w1:pA", "blocked", 9))) is False

    def test_a_snapshot_without_panes_is_refused(self):
        with pytest.raises(pw.WatchError):
            pw.Reconciler({"snapshot": {}})


class TestDebounceCommitsOnlyOnSuccess:
    def test_a_reserved_window_is_not_consumed_until_commit(self):
        """The lost-notification bug: a failed transport used to consume the window, so with no
        further transition the owner never heard anything."""
        d = pw.Debouncer(window_s=60)
        assert d.allow("w1:pA", now=1000.0) is True
        assert d.allow("w1:pA", now=1001.0) is True          # nothing committed yet
        d.commit("w1:pA", now=1001.0)
        assert d.allow("w1:pA", now=1030.0) is False

    def test_release_reopens_the_window(self):
        d = pw.Debouncer(window_s=60)
        d.commit("w1:pA", now=1000.0)
        assert d.allow("w1:pA", now=1010.0) is False
        d.release("w1:pA")
        assert d.allow("w1:pA", now=1010.0) is True

    def test_the_window_expires(self):
        d = pw.Debouncer(window_s=60)
        d.commit("w1:pA", now=1000.0)
        assert d.allow("w1:pA", now=1061.0) is True

    def test_debounce_is_per_pane(self):
        d = pw.Debouncer(window_s=60)
        d.commit("w1:pA", now=1000.0)
        assert d.allow("w1:pB", now=1001.0) is True


class TestAC5IsAProvenanceBoundary:
    def test_the_body_uses_the_label_and_never_the_terminal_title(self):
        pane = json.loads(_updated("w1:pA", "blocked", 1, label="alpha"))["data"]["pane"]
        body = pw.body_for_pane(pane, status="blocked")
        assert "alpha" in body and "blocked" in body
        assert "SECRET" not in body

    def test_the_fallback_chain_is_label_then_name_then_pane_id(self):
        assert "beta" in pw.body_for_pane(
            {"pane_id": "w1:pB", "name": "beta"}, status="blocked")
        assert "w1:pC" in pw.body_for_pane({"pane_id": "w1:pC"}, status="blocked")

    def test_a_caller_cannot_supply_display_text_at_all(self):
        """The review's point: a key allowlist would still pass `label=event.title`. The builder
        takes a pane RECORD and selects its own fields, so there is no parameter to abuse."""
        with pytest.raises(TypeError):
            pw.body_for_pane({"pane_id": "w1:pA"}, status="blocked", label="SECRET")

    def test_a_label_that_IS_the_terminal_title_is_refused(self):
        """The remaining provenance hole, closed: if something upstream copied screen text into the
        label, the body would leak it while every key looked legitimate."""
        with pytest.raises(pw.WatchError):
            pw.body_for_pane({"pane_id": "w1:pA", "label": "SECRET prompt text",
                              "terminal_title": "SECRET prompt text"}, status="blocked")

    def test_a_non_record_or_bad_status_is_refused(self):
        with pytest.raises(pw.WatchError):
            pw.body_for_pane("not a record", status="blocked")
        with pytest.raises(pw.WatchError):
            pw.body_for_pane({"pane_id": "w1:pA"}, status="banana")


class TestHeartbeatAndStall:
    def test_heartbeat_is_due_after_the_interval(self):
        assert pw.heartbeat_due(last=1000.0, now=1000.0, interval_s=300) is False
        assert pw.heartbeat_due(last=1000.0, now=1301.0, interval_s=300) is True

    def test_the_heartbeat_is_written_where_an_external_observer_can_read_it(self, tmp_path):
        """A predicate is not a timer and a dead watcher cannot report itself, so liveness has to
        land on disk for something else to check."""
        path = tmp_path / "beat.json"
        pw.write_heartbeat(str(path), now=1234.5, report={"events": 3, "notified": [1]})
        doc = json.loads(path.read_text())
        assert doc["ts"] == 1234.5 and doc["events"] == 3 and doc["notified"] == 1

    @pytest.mark.parametrize("status", ["idle", "unknown"])
    def test_a_stale_pane_warns(self, status):
        w = pw.stall_warning(pane={"pane_id": "w1:pA", "label": "alpha", "agent_status": status},
                             since=1000.0, now=1000.0 + 1801, threshold_s=1800)
        assert w is not None and "alpha" in w

    def test_the_warning_says_what_it_is_NOT(self):
        """AC3's signal cannot distinguish a missed prompt from an idle pane, and the message must
        not imply otherwise — the review was right that the earlier claim was too strong."""
        w = pw.stall_warning(pane={"pane_id": "w1:pA", "label": "alpha", "agent_status": "idle"},
                             since=0.0, now=99999.0, threshold_s=1800)
        assert "NOT a missed-prompt detector" in w

    def test_a_working_pane_does_not_warn(self):
        assert pw.stall_warning(
            pane={"pane_id": "w1:pA", "label": "a", "agent_status": "working"},
            since=0.0, now=99999.0, threshold_s=1800) is None

    def test_a_stall_warning_carries_no_contents(self):
        w = pw.stall_warning(pane={"pane_id": "w1:pA", "label": "alpha", "agent_status": "idle",
                                   "terminal_title": "SECRET prompt text"},
                             since=0.0, now=99999.0, threshold_s=1800)
        assert "SECRET" not in w


class TestWatchStreamEndToEnd:
    def _rec(self):
        return pw.Reconciler(_snapshot([("w1:pA", "working", 5, "alpha"),
                                        ("w1:pB", "idle", 2, "beta")]))

    def _run(self, lines, sent, **kw):
        return pw.watch_stream(lines, reconciler=self._rec(),
                               sender=lambda body: (sent.append(body), 0)[1],
                               clock=kw.pop("clock", lambda: 1000.0),
                               emit=lambda _m: None, **kw)

    def test_a_replay_burst_notifies_nobody(self):
        """The measured failure: a fresh subscription's first 450 ms carried frames for six panes
        that were already gone. Without reconciliation the watcher's FIRST act pages the owner."""
        sent = []
        lines = [json.dumps({"id": "x", "result": {"type": "subscription_started"}})]
        lines += [_updated(g, "blocked", 1) for g in
                  ("w1:pB1", "w1:pB3", "w1:pB4", "w1:pB7", "w1:pB8", "w1:pB9")]
        report = self._run(lines, sent)
        assert report["subscribed"] is True
        assert sent == [] and report["dropped"] == 6

    def test_a_live_block_notifies_once_with_a_label_and_no_contents(self):
        sent = []
        report = self._run([_updated("w1:pA", "blocked", 6, label="alpha")], sent)
        assert len(sent) == 1
        assert "alpha" in sent[0] and "SECRET" not in sent[0]
        assert report["notified"][0]["pane_id"] == "w1:pA"

    def test_a_second_block_inside_the_window_is_suppressed(self):
        sent = []
        lines = [_updated("w1:pA", "blocked", 6), _updated("w1:pA", "working", 7),
                 _updated("w1:pA", "blocked", 8)]
        report = self._run(lines, sent, debouncer=pw.Debouncer(window_s=60))
        assert len(sent) == 1 and report["suppressed"] == 1

    def test_a_failed_send_is_retried_on_the_NEXT_blocked_frame(self):
        """Step 11 finding 3, and the earlier version of this test HID it by inserting a
        `working` frame in between — which made the second attempt a fresh transition rather than a
        retry. With two consecutive blocked frames, the retry only happens if a failed send leaves
        the transition state alone."""
        attempts = []
        report = pw.watch_stream(
            [_updated("w1:pA", "blocked", 6), _updated("w1:pA", "blocked", 7)],
            reconciler=self._rec(), sender=lambda b: (attempts.append(b), 1)[1],
            clock=lambda: 1000.0, debouncer=pw.Debouncer(window_s=600), emit=lambda _m: None)
        assert len(attempts) == 2, "a failed send must not consume the transition"
        assert report["send_failures"] == 2 and report["notified"] == []

    def test_a_successful_send_DOES_consume_the_transition(self):
        sent = []
        report = self._run([_updated("w1:pA", "blocked", 6), _updated("w1:pA", "blocked", 7)], sent,
                           debouncer=pw.Debouncer(window_s=600))
        assert len(sent) == 1, sent
        # Suppression happens at the TRANSITION check, not the debounce: after a confirmed send
        # `prior` is `blocked`, so a second blocked frame is simply not a transition.
        assert len(report["notified"]) == 1

    def test_a_closed_pane_stops_notifying(self):
        sent = []
        self._run([_closed("w1:pA"), _updated("w1:pA", "blocked", 6)], sent)
        assert sent == []

    def test_a_pane_created_after_the_snapshot_can_notify(self):
        """Legitimate only because `pane.created` IS subscribed now — the Step 11 BLOCKER was that
        this frame was manufactured by the test and never requested from herdr."""
        sent = []
        self._run([_created("w1:pNEW", name="newborn"),
                   _updated("w1:pNEW", "blocked", 1, name="newborn")], sent,
                  live_panes=lambda: {"w1:pA", "w1:pB", "w1:pNEW"})
        assert len(sent) == 1 and "newborn" in sent[0]

    def test_an_error_frame_is_reported(self):
        sent = []
        report = self._run([json.dumps({"id": "", "error": {"code": "invalid_request",
                                                           "message": "missing field `pane_id`"}})],
                          sent)
        assert report["errors"] and sent == []

    def test_a_none_line_is_a_read_timeout_that_fires_the_heartbeat(self):
        """This is what makes the heartbeat real during silence rather than a predicate nobody
        calls."""
        beats = []
        report = pw.watch_stream([None, None], reconciler=self._rec(),
                                 sender=lambda b: 0, clock=lambda: 1000.0,
                                 emit=lambda _m: None,
                                 beat=lambda now, rep: beats.append(now))
        assert report["heartbeats"] == 2 and len(beats) == 2
        assert report["events"] == 0


class TestCLI:
    def test_help_works(self):
        proc = subprocess.run([sys.executable, str(HOOKS / "pane_watch_lib.py"), "watch", "--help"],
                              capture_output=True, text=True, check=False)
        assert proc.returncode == 0
        assert "--dry-run" in proc.stdout and "--heartbeat-path" in proc.stdout

    def test_dry_run_prints_the_request_and_sends_nothing(self):
        """`--source events` is explicit since #679 made polling the default: the subscription is
        dead in production, but its request shape is still worth pinning — five instruments' worth
        of knowledge lives in it, and it is one flag away if herdr fixes the feed."""
        proc = subprocess.run([sys.executable, str(HOOKS / "pane_watch_lib.py"), "watch",
                               "--source", "events", "--dry-run"],
                              capture_output=True, text=True, check=False)
        assert proc.returncode == 0
        req = json.loads(proc.stdout)
        assert req["method"] == "events.subscribe"
        types = {s["type"] for s in req["params"]["subscriptions"]}
        assert types == {"pane.updated", "pane.created", "pane.closed"}


class TestStep11Findings:
    """One test per confirmed Step-11 finding. Several of these fail against the previous commit."""

    def _snap(self, panes):
        return _snapshot(panes)

    def test_pane_created_is_actually_subscribed(self):
        """BLOCKER: registration depended on a `pane_created` frame that was never requested, so
        every pane created after the snapshot was ignored forever."""
        assert {"type": "pane.created"} in pw.build_subscriptions()

    def test_a_pane_already_blocked_at_startup_is_notified(self):
        """Finding 2: the snapshot was never examined for blocked panes, the replayed frame is
        rejected as equal to the baseline, and the transition map starts empty — so an agent already
        waiting before the watcher existed was never reported."""
        rec = pw.Reconciler(self._snap([("w1:pA", "blocked", 6, "alpha"),
                                        ("w1:pB", "working", 2, "beta")]))
        sent = []
        out = pw.startup_sweep(rec, sender=lambda b: (sent.append(b), 0)[1], now=1000.0,
                               emit=lambda _m: None)
        assert out["notified"] == ["w1:pA"], out
        assert len(sent) == 1 and "alpha" in sent[0] and "SECRET" not in sent[0]

    def test_the_startup_sweep_ignores_unblocked_panes(self):
        rec = pw.Reconciler(self._snap([("w1:pA", "working", 6, "alpha")]))
        sent = []
        out = pw.startup_sweep(rec, sender=lambda b: (sent.append(b), 0)[1], now=1000.0,
                               emit=lambda _m: None)
        assert out["notified"] == [] and sent == []

    def test_the_startup_sweep_does_not_consume_the_window_on_failure(self):
        rec = pw.Reconciler(self._snap([("w1:pA", "blocked", 6, "alpha")]))
        d = pw.Debouncer(window_s=600)
        out = pw.startup_sweep(rec, sender=lambda b: 1, now=1000.0, debouncer=d,
                               emit=lambda _m: None)
        assert out["send_failures"] == ["w1:pA"]
        assert d.allow("w1:pA", now=1001.0) is True

    def test_a_refusal_does_not_echo_the_screen_text_it_refuses(self):
        """Finding 5: the message interpolated the identity, and `main` prints it to stderr — so the
        approval text or credential being refused landed in the watcher log."""
        with pytest.raises(pw.WatchError) as exc:
            pw.body_for_pane({"pane_id": "w1:pA", "label": "SECRET prompt text",
                              "terminal_title": "SECRET prompt text"}, status="blocked")
        assert "SECRET" not in str(exc.value)
        assert "w1:pA" in str(exc.value) and "terminal_title" in str(exc.value)

    def test_a_statusless_frame_does_not_advance_the_revision(self):
        """Finding 6: advancing on an unrecognised status meant a CORRECTED frame at the same
        revision was then dropped, losing a real block behind one bad frame."""
        rec = pw.Reconciler(self._snap([("w1:pA", "working", 5, "alpha")]))
        sent = []
        report = pw.watch_stream([_updated("w1:pA", "banana", 6),
                                  _updated("w1:pA", "blocked", 6)],
                                 reconciler=rec, sender=lambda b: (sent.append(b), 0)[1],
                                 clock=lambda: 1000.0, emit=lambda _m: None)
        assert len(sent) == 1, "the corrected frame at the same revision must still be seen"
        assert report["dropped"] >= 1

    def test_socket_close_is_reported_as_an_error(self):
        """Finding 4: EOF used to return quietly and the CLI exited 0, so an unattended watcher
        silently stopped after a herdr restart."""
        report = pw.watch_stream(
            [json.dumps({"id": "x", "result": {"type": "subscription_started"}}), pw._EOF],
            reconciler=pw.Reconciler(self._snap([("w1:pA", "working", 5, "alpha")])),
            sender=lambda b: 0, clock=lambda: 1000.0, emit=lambda _m: None)
        # Source-neutral since #679: the same signal now arrives from a closed socket OR a poll
        # source that has given up, so the message names neither.
        assert report["errors"] and "input layer ended" in report["errors"][0]

    def test_the_stall_warning_is_actually_emitted_by_the_loop(self):
        """Finding 7: `stall_warning` shipped as dead code — nothing tracked `since` or called it."""
        emitted = []
        clock = iter([1000.0, 1000.0, 99999.0, 99999.0, 99999.0])
        rec = pw.Reconciler(self._snap([("w1:pA", "working", 5, "alpha")]))
        report = pw.watch_stream([_updated("w1:pA", "idle", 6), None],
                                 reconciler=rec, sender=lambda b: 0,
                                 clock=lambda: next(clock), emit=emitted.append,
                                 heartbeat_s=1.0, stall_s=1800.0)
        assert report["stalls"] == ["w1:pA"], report
        assert any("stale-pane warning" in m for m in emitted), emitted


class TestStep11Pass2Findings:
    """One test per confirmed pass-2 finding."""

    def _rec(self):
        return pw.Reconciler(_snapshot([("w1:pA", "working", 5, "alpha")]))

    def test_a_REPLAYED_creation_is_refused(self):
        """BLOCKER: subscribing to `pane.created` reopened the replay hole. The replay carries
        `created -> updated(blocked) -> closed` for long-gone panes, and an unconditional
        registration gave such a pane no revision baseline — so its blocked frame was accepted and
        the owner paged about a pane that died hours ago."""
        sent = []
        report = pw.watch_stream(
            [_created("w1:pDEAD"), _updated("w1:pDEAD", "blocked", 1), _closed("w1:pDEAD")],
            reconciler=self._rec(), sender=lambda b: (sent.append(b), 0)[1],
            clock=lambda: 1000.0, emit=lambda _m: None,
            live_panes=lambda: {"w1:pA"})          # pDEAD is NOT in the current pane set
        assert sent == [], sent
        assert report["notified"] == []

    def test_registration_FAILS_CLOSED_when_the_live_set_is_unreadable(self):
        """A false page is the failure this class of bug produces, so an unknown answer refuses."""
        rec = self._rec()
        assert rec.register_pane({"pane_id": "w1:pNEW"}, live_pane_ids=None) is False
        assert rec.register_pane({"pane_id": "w1:pNEW"}, live_pane_ids=set()) is False

    def test_live_pane_ids_returns_empty_on_failure(self):
        assert pw.live_pane_ids(runner=lambda argv: type("P", (), {"returncode": 1,
                                                                  "stdout": ""})()) == set()

    def test_the_stall_warning_refuses_a_screen_derived_identity(self):
        """HIGH: the warning rendered the identity without the body builder's refusal, and the
        heartbeat loop emits it — so a label copied from `terminal_title` leaked into the log."""
        with pytest.raises(pw.WatchError) as exc:
            pw.stall_warning(pane={"pane_id": "w1:pA", "label": "SECRET prompt text",
                                   "terminal_title": "SECRET prompt text",
                                   "agent_status": "idle"},
                             since=0.0, now=99999.0, threshold_s=1800)
        assert "SECRET" not in str(exc.value)

    def test_a_failed_send_rolls_the_revision_back_so_redelivery_retries(self):
        """HIGH: `accepts()` consumed the revision before the send, so a stable blocked pane that
        produced no further update was never retried — the block was lost."""
        attempts = []
        pw.watch_stream([_updated("w1:pA", "blocked", 6), _updated("w1:pA", "blocked", 6)],
                        reconciler=self._rec(), sender=lambda b: (attempts.append(b), 1)[1],
                        clock=lambda: 1000.0, debouncer=pw.Debouncer(window_s=600),
                        emit=lambda _m: None)
        assert len(attempts) == 2, "the SAME revision must be eligible again after a failed send"

    def test_one_continuous_block_pages_once_even_across_a_window_expiry(self):
        """One page per continuous block. After a confirmed send `prior` is `blocked`, so later
        frames are not transitions — the heartbeat is pushed out of the way so its retry pass cannot
        supply a second send."""
        sent = []
        ticks = iter([1000.0, 1000.0, 1000.0, 2000.0, 2000.0, 2000.0, 3000.0, 3000.0, 3000.0])
        report = pw.watch_stream(
            [_updated("w1:pA", "blocked", 6), _updated("w1:pA", "blocked", 7),
             _updated("w1:pA", "blocked", 8)],
            reconciler=self._rec(), sender=lambda b: (sent.append(b), 0)[1],
            clock=lambda: next(ticks), debouncer=pw.Debouncer(window_s=60),
            emit=lambda _m: None, heartbeat_s=10_000.0)
        assert len(sent) == 1, sent
        assert len(report["notified"]) == 1, report

    def test_a_DEBOUNCE_SUPPRESSED_transition_sends_nothing(self):
        """The suppressed branch reached for real: the window was committed by something ELSE (a
        startup sweep or a pending retry) for a pane whose `prior` is unset.

        **Honest limit, stated because two earlier versions of this test overclaimed and a reviewer
        caught both.** This pins that suppression happens and that nothing is sent. It does NOT pin
        the `prior` write on that branch: I mutation-tested it, and with that assignment removed this
        test still passes, because the other guards (the transition check after a confirmed send, and
        the seeded sweep state) already prevent the duplicate. That write is therefore defensive
        belt-and-braces, not an independently observable behaviour — so no test here claims to
        enforce it."""
        rec = self._rec()
        d = pw.Debouncer(window_s=600)
        d.commit("w1:pA", now=1000.0)          # window taken by another path, prior NOT seeded
        sent = []
        ticks = iter([1010.0, 1010.0, 1010.0, 99999.0, 99999.0, 99999.0])
        report = pw.watch_stream(
            [_updated("w1:pA", "blocked", 6), _updated("w1:pA", "blocked", 7)],
            reconciler=rec, sender=lambda b: (sent.append(b), 0)[1],
            clock=lambda: next(ticks), debouncer=d, emit=lambda _m: None,
            heartbeat_s=10_000.0)
        assert report["suppressed"] >= 1, report
        # and the recorded `prior` is what stops the post-window frame paging again
        assert sent == [], sent

    def test_a_pane_already_idle_at_startup_can_warn(self):
        """MEDIUM: `since` was populated only by accepted stream updates, so the stalest panes —
        the ones already idle at startup — were exactly the ones the warning could not see."""
        emitted = []
        clock = iter([1000.0, 99999.0, 99999.0, 99999.0])
        rec = pw.Reconciler(_snapshot([("w1:pIDLE", "idle", 3, "sleepy")]))
        report = pw.watch_stream([None], reconciler=rec, sender=lambda b: 0,
                                 clock=lambda: next(clock), emit=emitted.append,
                                 heartbeat_s=1.0, stall_s=1800.0)
        assert report["stalls"] == ["w1:pIDLE"], report
        assert any("sleepy" in m for m in emitted)

    def test_closing_a_pane_clears_its_stall_bookkeeping(self):
        emitted = []
        clock = iter([1000.0, 99999.0, 99999.0, 99999.0, 99999.0, 99999.0])
        rec = pw.Reconciler(_snapshot([("w1:pIDLE", "idle", 3, "sleepy")]))
        report = pw.watch_stream([None, _closed("w1:pIDLE"), None],
                                 reconciler=rec, sender=lambda b: 0,
                                 clock=lambda: next(clock), emit=emitted.append,
                                 heartbeat_s=1.0, stall_s=1800.0)
        assert report["stalls"] == [], report
        # Also catches removal of the `since` cleanup alone: if `since` still held the pane, the
        # second heartbeat would re-emit its warning for a pane that no longer exists.
        assert sum("sleepy" in m for m in emitted) == 1, emitted


class TestDeliveryIsRetriedNotLost:
    """Step 11 pass-3 BLOCKER: one failed send could permanently lose a real block on two paths.
    Replaced three per-case patches with ONE pending-retry mechanism, and these tests pin it."""

    def test_a_failed_startup_sweep_is_retried_by_the_heartbeat(self):
        """The sweep released the debounce but scheduled no retry, and a stable blocked pane emits
        nothing newer — so the owner was never told at all."""
        rec = pw.Reconciler(_snapshot([("w1:pA", "blocked", 6, "alpha")]))
        attempts = []
        d = pw.Debouncer(window_s=0.0)
        sweep = pw.startup_sweep(rec, sender=lambda b: (attempts.append(b), 1)[1], now=1000.0,
                                debouncer=d, emit=lambda _m: None)
        assert sweep["pending"], "a failed sweep send must be handed on as pending"
        report = pw.watch_stream([None], reconciler=rec,
                                 sender=lambda b: (attempts.append(b), 0)[1],
                                 clock=lambda: 2000.0, debouncer=d, emit=lambda _m: None,
                                 heartbeat_s=1.0, already_notified=sweep["notified"],
                                 pending=sweep["pending"])
        assert len(attempts) == 2, "the heartbeat must retry the pending send"
        assert report["notified"] and report["notified"][0].get("retry") is True

    def test_a_new_panes_failed_send_is_retried_even_with_a_None_baseline(self):
        """`rollback()` restored only integer baselines, so a newly registered pane (baseline `None`)
        kept its consumed revision and redelivery was dropped. The earlier version of this test only
        poked `rollback` directly and passed with the whole pending mechanism removed, so this one
        drives `watch_stream` and a real sender."""
        rec = pw.Reconciler(_snapshot([("w1:pA", "working", 5, "alpha")]))
        rec.register_pane({"pane_id": "w1:pNEW", "name": "newborn"}, live_pane_ids={"w1:pNEW"})
        assert rec.revision_of("w1:pNEW") is None
        sent = []
        results = iter([1, 0])
        ticks = iter([1000.0, 1000.0, 1000.0, 2000.0, 2000.0, 2000.0])
        report = pw.watch_stream([_updated("w1:pNEW", "blocked", 1, name="newborn"), None],
                                 reconciler=rec,
                                 sender=lambda b: (sent.append(b), next(results))[1],
                                 clock=lambda: next(ticks), debouncer=pw.Debouncer(window_s=0.0),
                                 emit=lambda _m: None, heartbeat_s=1.0)
        assert len(sent) == 2, f"the failed send must be retried by the heartbeat: {report}"
        assert report["pending_at_exit"] == [], report

    def test_a_deferred_registration_notifies_even_if_the_block_arrived_FIRST(self):
        """Step 11 pass-4 BLOCKER, and the earlier version of this test HID it by putting the
        heartbeat before the blocked update. The real ordering is: creation deferred (live set
        unreadable) -> `blocked` update arrives and is DROPPED because the pane is still unknown ->
        heartbeat registers it. Registering from the queued creation record is not enough: that record
        says `unknown`/revision 0, so nothing would ever notify. The heartbeat must sweep the pane's
        CURRENT state."""
        rec = pw.Reconciler(_snapshot([("w1:pA", "working", 5, "alpha")]))
        sent = []
        readable = {"yes": False}
        ticks = iter([1000.0, 1000.0, 1000.0, 2000.0, 2000.0, 2000.0, 2000.0])

        def live():
            return {"w1:pA", "w1:pNEW"} if readable["yes"] else set()

        def current(pane_id):
            return {"pane_id": pane_id, "workspace_id": "w1", "tab_id": "w1:t1",
                    "agent_status": "blocked", "revision": 4, "name": "newborn",
                    "terminal_title": "SECRET prompt text"}

        report = pw.watch_stream(
            [_created("w1:pNEW", name="newborn"),              # deferred: live set unreadable
             _updated("w1:pNEW", "blocked", 1, name="newborn"),  # DROPPED: pane still unknown
             None],                                            # heartbeat: register + sweep
            reconciler=rec, sender=lambda b: (sent.append(b), 0)[1],
            clock=lambda: next(ticks), emit=lambda _m: readable.__setitem__("yes", True),
            heartbeat_s=1.0, live_panes=live, current_pane=current)
        assert len(sent) == 1, f"the deferred pane's block must still be reported: {report}"
        assert "newborn" in sent[0] and "SECRET" not in sent[0]

    def test_a_deferred_registration_STAYS_pending_when_its_state_is_unreadable(self):
        """Step 11 pass-5 BLOCKER: the registration was dropped the moment `register_pane` succeeded,
        BEFORE the current-state snapshot. If that second snapshot then failed, the pane was
        registered but never swept — and a stable block emits nothing more, so it ended with no
        notification AND no pending evidence."""
        rec = pw.Reconciler(_snapshot([("w1:pA", "working", 5, "alpha")]))
        sent = []
        ticks = iter([1000.0, 1000.0, 2000.0, 3000.0])
        calls = {"n": 0}
        live_calls = {"n": 0}

        def live():
            # unreadable at creation time (so the registration is DEFERRED), readable afterwards
            live_calls["n"] += 1
            return set() if live_calls["n"] == 1 else {"w1:pA", "w1:pNEW"}

        def current(_pane_id):
            calls["n"] += 1
            if calls["n"] == 1:
                return None                      # the second snapshot fails on the first attempt
            return {"pane_id": "w1:pNEW", "workspace_id": "w1", "agent_status": "blocked",
                    "name": "newborn", "revision": 4}

        report = pw.watch_stream(
            [_created("w1:pNEW", name="newborn"), None, None],
            reconciler=rec, sender=lambda b: (sent.append(b), 0)[1],
            clock=lambda: next(ticks), emit=lambda _m: None, heartbeat_s=1.0,
            live_panes=live, current_pane=current)
        assert calls["n"] >= 2, f"the sweep must be retried, not abandoned: {report}"
        assert len(sent) == 1, f"the block must still be reported once readable: {report}"

    def test_a_stale_pending_entry_is_cleared_by_a_successful_stream_send(self):
        """Step 11 pass-4: a newer frame succeeding left the older pending entry in place, so the
        next heartbeat paged the same continuous block a second time."""
        rec = pw.Reconciler(_snapshot([("w1:pA", "working", 5, "alpha")]))
        sent = []
        results = iter([1, 0])                       # first send fails, second succeeds
        # The heartbeat line must land well AFTER the debounce window, or the drain is suppressed and
        # the test cannot observe a duplicate at all (which is how the first version of it passed
        # even with the clearing removed). One tick is consumed per line.
        # clock() is called once for `last_beat` at entry, then once per line.
        ticks = iter([1000.0, 1000.0, 1001.0, 99999.0])
        report = pw.watch_stream(
            [_updated("w1:pA", "blocked", 6), _updated("w1:pA", "blocked", 7), None],
            reconciler=rec, sender=lambda b: (sent.append(b), next(results))[1],
            clock=lambda: next(ticks), debouncer=pw.Debouncer(window_s=0.0),
            emit=lambda _m: None, heartbeat_s=1.0)
        assert len(sent) == 2, sent
        assert report["pending_at_exit"] == [], report

    def test_a_pending_send_for_a_pane_CLOSED_IN_THE_SAME_BATCH_is_dropped(self):
        """Step 11 pass-5 caught the earlier version of this test calling `forget_pane()` by hand and
        feeding `[None]` — it never fed a close EVENT, so it passed while the real ordering stayed
        broken. Here the close arrives as a frame with the heartbeat due on that same line, which is
        exactly when the drain used to run first and page for an already-closed pane."""
        rec = pw.Reconciler(_snapshot([("w1:pA", "blocked", 6, "alpha")]))
        sent = []
        # clock() is called once for `last_beat` at entry, then once per line — so these two values
        # are what actually make the heartbeat DUE on the close line. Without that the drain never
        # runs and the test cannot observe the ordering at all.
        ticks = iter([1000.0, 99999.0])
        report = pw.watch_stream([_closed("w1:pA")], reconciler=rec,
                                 sender=lambda b: (sent.append(b), 0)[1],
                                 clock=lambda: next(ticks), emit=lambda _m: None, heartbeat_s=1.0,
                                 pending={"w1:pA": {"pane_id": "w1:pA", "name": "alpha"}})
        assert sent == [], f"a pane closed in this very batch must not be paged: {sent}"
        assert report["pending_at_exit"] == []

    def test_a_sender_EXCEPTION_is_a_failed_send_not_a_dead_watcher(self):
        """Step 11 pass-4: sender calls were unguarded and `main` catches only `WatchError`, so the
        real sender's `TimeoutExpired` killed the watcher AND bypassed the pending queue."""
        rec = pw.Reconciler(_snapshot([("w1:pA", "working", 5, "alpha")]))

        def boom(_body):
            raise subprocess.TimeoutExpired(cmd=["notify"], timeout=60)

        report = pw.watch_stream([_updated("w1:pA", "blocked", 6)], reconciler=rec,
                                 sender=boom, clock=lambda: 1000.0, emit=lambda _m: None,
                                 heartbeat_s=10_000.0)
        assert report["send_failures"] == 1, report
        assert report["pending_at_exit"] == ["w1:pA"], report

    def test_a_STARTUP_sender_exception_is_also_a_failed_send(self):
        """Step 11 pass-5: the guard lived only in `watch_stream`, and the sweep runs FIRST — so the
        real sender's TimeoutExpired killed the watcher before any pending map was returned."""
        rec = pw.Reconciler(_snapshot([("w1:pA", "blocked", 6, "alpha")]))

        def boom(_body):
            raise subprocess.TimeoutExpired(cmd=["notify"], timeout=60)

        out = pw.startup_sweep(rec, sender=boom, now=1000.0, emit=lambda _m: None)
        assert out["send_failures"] == ["w1:pA"], out
        assert "w1:pA" in out["pending"], out

    def test_the_sweep_pending_record_carries_no_pane_contents(self):
        """AC5, and this one is about a REPORT rather than a body: the sweep stored the whole snapshot
        record and `_cmd_watch` prints it as JSON, so `terminal_title` reached stdout."""
        rec = pw.Reconciler(_snapshot([("w1:pA", "blocked", 6, "alpha")]))
        out = pw.startup_sweep(rec, sender=lambda b: 1, now=1000.0, emit=lambda _m: None)
        assert "SECRET" not in json.dumps(out), out

    def test_the_sweep_does_not_page_twice_for_one_continuous_block(self):
        """The stream started with an empty `prior`, so a pane the sweep had already paged looked
        like a fresh transition once the debounce window expired."""
        rec = pw.Reconciler(_snapshot([("w1:pA", "blocked", 6, "alpha")]))
        sent = []
        d = pw.Debouncer(window_s=60)
        sweep = pw.startup_sweep(rec, sender=lambda b: (sent.append(b), 0)[1], now=1000.0,
                                 debouncer=d, emit=lambda _m: None)
        assert sweep["notified"] == ["w1:pA"]
        pw.watch_stream([_updated("w1:pA", "blocked", 7)], reconciler=rec,
                        sender=lambda b: (sent.append(b), 0)[1],
                        clock=lambda: 99999.0, debouncer=d, emit=lambda _m: None,
                        heartbeat_s=10_000.0, already_notified=sweep["notified"],
                        pending=sweep["pending"])
        assert len(sent) == 1, f"one continuous block must page once, not twice: {sent}"

    def test_live_pane_ids_survives_a_runner_exception(self):
        """It caught only WatchError, so a TimeoutExpired terminated the whole watcher instead of
        returning the empty set its contract promises."""
        def boom(argv):
            raise subprocess.TimeoutExpired(cmd=argv, timeout=1)
        assert pw.live_pane_ids(runner=boom) == set()

    def test_pending_sends_are_reported_at_exit(self):
        """So an operator can see what was never delivered, rather than it vanishing with the process."""
        rec = pw.Reconciler(_snapshot([("w1:pA", "working", 5, "alpha")]))
        report = pw.watch_stream([_updated("w1:pA", "blocked", 6)], reconciler=rec,
                                 sender=lambda b: 1, clock=lambda: 1000.0,
                                 emit=lambda _m: None, heartbeat_s=10_000.0)
        assert report["pending_at_exit"] == ["w1:pA"], report


def calls_seen(fn):
    """True when a stubbed `current_pane` was actually invoked — i.e. the deferred sweep ran."""
    return getattr(fn, "called", False)


class TestStep11Pass6:
    def _rec(self):
        return pw.Reconciler(_snapshot([("w1:pA", "working", 5, "alpha")]))

    def test_an_unresolved_deferred_registration_leaves_EVIDENCE_at_exit(self):
        """Step 11 pass-6 BLOCKER: `pending_at_exit` reported only `pending_sends`, so a genuinely
        blocked pane whose registration never resolved exited with notified=[] AND pending_at_exit=[]
        — the only trace was a log line about a deferred creation, which says nothing about an
        undelivered block."""
        sent = []
        report = pw.watch_stream(
            [_created("w1:pNEW", name="newborn"),
             _updated("w1:pNEW", "blocked", 1, name="newborn"), pw._EOF],
            reconciler=self._rec(), sender=lambda b: (sent.append(b), 0)[1],
            clock=lambda: 1000.0, emit=lambda _m: None,
            live_panes=lambda: set())                 # never readable
        assert sent == []
        assert report["pending_registrations_at_exit"] == ["w1:pNEW"], report
        assert any("never learned" in e for e in report["errors"]), report

    def test_a_deferred_sweep_clears_an_older_pending_send(self):
        """Step 11 pass-6: a successful deferred sweep committed the notification but left an older
        failed stream send queued, so the next drain paged the same block again."""
        # Step 11 pass-7 caught the earlier version PRE-REGISTERING the pane, so `current_pane` was
        # never called and it exercised an ordinary retry rather than the deferred sweep at all.
        rec = self._rec()
        sent = []
        results = iter([1, 0, 0])
        ticks = iter([1000.0, 1000.0, 1000.0, 2000.0, 99999.0])
        live_calls = {"n": 0}

        def current(_pid):
            current.called = True
            return {"pane_id": "w1:pNEW", "workspace_id": "w1", "agent_status": "blocked",
                    "name": "newborn", "revision": 9}
        current.called = False

        def live():
            live_calls["n"] += 1
            return set() if live_calls["n"] == 1 else {"w1:pA", "w1:pNEW"}

        report = pw.watch_stream(
            [_created("w1:pNEW", name="newborn"),                 # deferred: live set unreadable
             _updated("w1:pNEW", "blocked", 1, name="newborn"),   # dropped: still unknown
             None, None],
            reconciler=rec, sender=lambda b: (sent.append(b), next(results))[1],
            clock=lambda: next(ticks), debouncer=pw.Debouncer(window_s=0.0),
            emit=lambda _m: None, heartbeat_s=1.0,
            live_panes=live, current_pane=current)
        assert calls_seen(current), "the deferred sweep must have run"
        assert len(sent) <= 2, f"one continuous block must not page a third time: {report}"
        assert report["pending_at_exit"] == [], report

    def test_rollback_is_what_makes_the_STREAM_path_retry(self):
        """Step 11 pass-6: the earlier None-baseline test passed with `rollback()` no-op'd, because
        the heartbeat retry bypassed reconciliation entirely. This drives the STREAM path only (the
        heartbeat is pushed out of reach), so it fails if the rollback is removed."""
        rec = self._rec()
        sent = []
        results = iter([1, 0])
        report = pw.watch_stream(
            [_updated("w1:pA", "blocked", 6), _updated("w1:pA", "blocked", 6)],
            reconciler=rec, sender=lambda b: (sent.append(b), next(results))[1],
            clock=lambda: 1000.0, debouncer=pw.Debouncer(window_s=0.0),
            emit=lambda _m: None, heartbeat_s=10_000.0)
        assert len(sent) == 2, f"the SAME revision must be eligible again after a failure: {report}"


class TestAnAC5RefusalStillLeavesEvidence:
    def test_a_refused_body_becomes_a_recorded_failed_send_not_an_abort(self):
        """Step 11 pass-7 BLOCKER: the refusal re-raised out of `watch_stream`, bypassing report
        finalization — so a blocked pane whose label equals its `terminal_title` ended with NO
        notification and NO structured evidence. Refusing to name it is right; losing the accounting
        that proves something was owed is not."""
        rec = pw.Reconciler(_snapshot([("w1:pA", "working", 5, "alpha")]))
        leaky = json.loads(_updated("w1:pA", "blocked", 6))["data"]["pane"]
        leaky["label"] = leaky["terminal_title"]           # identity == screen text
        line = json.dumps({"event": "pane_updated", "data": {"pane": leaky}})
        report = pw.watch_stream([line], reconciler=rec, sender=lambda b: 0,
                                 clock=lambda: 1000.0, emit=lambda _m: None,
                                 heartbeat_s=10_000.0)
        assert report["notified"] == []
        assert report["pending_at_exit"] == ["w1:pA"], report
        assert any("AC5 refusal" in e for e in report["errors"]), report
        assert "SECRET" not in json.dumps(report), report


class TestAnAC5RefusalIsNeverRetriedWithTheOffendingLabel:
    """Step 11 pass-8 CRITICAL, and this leak was created BY my own pass-7 fix. `safe_record` kept the
    offending `label` while stripping `terminal_title`, so the retry could no longer repeat the
    provenance comparison and DELIVERED the screen text. Reproduced by the reviewer on both paths."""

    def _leaky_line(self):
        pane = json.loads(_updated("w1:pA", "blocked", 6))["data"]["pane"]
        pane["label"] = pane["terminal_title"]          # identity == screen text
        return json.dumps({"event": "pane_updated", "data": {"pane": pane}})

    def test_the_retry_after_a_refusal_never_sends_the_screen_text(self):
        rec = pw.Reconciler(_snapshot([("w1:pA", "working", 5, "alpha")]))
        sent = []
        ticks = iter([1000.0, 1000.0, 99999.0])
        report = pw.watch_stream([self._leaky_line(), None], reconciler=rec,
                                 sender=lambda b: (sent.append(b), 0)[1],
                                 clock=lambda: next(ticks), debouncer=pw.Debouncer(window_s=0.0),
                                 emit=lambda _m: None, heartbeat_s=1.0)
        assert all("SECRET" not in b for b in sent), sent
        assert any("AC5 refusal" in e for e in report["errors"]), report

    def test_a_startup_refusal_reaches_the_TOP_LEVEL_report(self):
        """Pass-8: startup refusals were stranded in `sweep["errors"]`, so the top-level accounting the
        previous pass claimed was simply not there."""
        pane = {"pane_id": "w1:pA", "workspace_id": "w1", "agent_status": "blocked",
                "label": "SECRET prompt text", "terminal_title": "SECRET prompt text",
                "revision": 6}
        rec = pw.Reconciler({"snapshot": {"panes": [pane]}})
        sweep = pw.startup_sweep(rec, sender=lambda b: 0, now=1000.0, emit=lambda _m: None)
        assert sweep.get("errors"), sweep
        report = pw.watch_stream([], reconciler=rec, sender=lambda b: 0, clock=lambda: 1000.0,
                                 emit=lambda _m: None, pending=sweep["pending"],
                                 pending_errors=sweep.get("errors"))
        assert any("AC5 refusal" in e for e in report["errors"]), report
        assert "SECRET" not in json.dumps(report), report

    def test_a_stall_refusal_does_not_destroy_the_report(self):
        """Pass-8: an unhandled refusal in the stall loop aborted everything, so a DIFFERENT pane's
        stale label destroyed the accounting for a genuinely blocked one."""
        panes = [{"pane_id": "w1:pIDLE", "workspace_id": "w1", "agent_status": "idle",
                  "label": "SECRET prompt text", "terminal_title": "SECRET prompt text",
                  "revision": 1},
                 {"pane_id": "w1:pA", "workspace_id": "w1", "agent_status": "working",
                  "revision": 5, "name": "alpha"}]
        rec = pw.Reconciler({"snapshot": {"panes": panes}})
        ticks = iter([1000.0, 1000.0, 99999.0])
        report = pw.watch_stream([_updated("w1:pA", "blocked", 6, name="alpha"), None],
                                 reconciler=rec, sender=lambda b: 1,
                                 clock=lambda: next(ticks), emit=lambda _m: None,
                                 heartbeat_s=1.0)
        assert report["pending_at_exit"] == ["w1:pA"], report
        assert "SECRET" not in json.dumps(report), report


class TestSafeRecordSanitizesAtTheBoundary:
    """Step 11 pass-9: `safe_record` retaining a `label` while stripping `terminal_title` was the
    SINGLE root cause behind three separately-reported leaks — every later consumer had the offending
    label but not the field to compare it against, so the check silently could not fire. Sanitizing
    here kills the whole class."""

    def test_a_screen_derived_label_is_DROPPED_not_carried(self):
        rec = pw.safe_record({"pane_id": "w1:pA", "label": "SECRET prompt text",
                              "terminal_title": "SECRET prompt text", "workspace_id": "w1"})
        assert "SECRET" not in json.dumps(rec), rec
        assert rec["pane_id"] == "w1:pA"

    def test_a_screen_derived_NAME_is_also_dropped(self):
        rec = pw.safe_record({"pane_id": "w1:pA", "name": "SECRET prompt text",
                              "terminal_title_stripped": "SECRET prompt text"})
        assert "SECRET" not in json.dumps(rec), rec

    def test_a_genuine_label_survives(self):
        rec = pw.safe_record({"pane_id": "w1:pA", "label": "lumenquire-s9",
                              "terminal_title": "SECRET prompt text"})
        assert rec["label"] == "lumenquire-s9"
        assert "SECRET" not in json.dumps(rec)

    def test_a_body_built_from_a_sanitized_record_cannot_leak(self):
        rec = pw.safe_record({"pane_id": "w1:pA", "label": "SECRET prompt text",
                              "terminal_title": "SECRET prompt text"})
        body = pw.body_for_pane(rec, status="blocked")
        assert "SECRET" not in body and "w1:pA" in body

    def test_the_deferred_sweep_failure_branch_cannot_leak_on_retry(self):
        """The exact pass-9 reproduction: the deferred sweep refuses, its failure branch queues the
        record, and the immediately following drain sends it."""
        rec = pw.Reconciler(_snapshot([("w1:pA", "working", 5, "alpha")]))
        sent = []
        ticks = iter([1000.0, 1000.0, 2000.0, 3000.0])
        live_calls = {"n": 0}

        def live():
            live_calls["n"] += 1
            return set() if live_calls["n"] == 1 else {"w1:pA", "w1:pNEW"}

        def current(_pid):
            return {"pane_id": "w1:pNEW", "workspace_id": "w1", "agent_status": "blocked",
                    "label": "SECRET prompt text", "terminal_title": "SECRET prompt text",
                    "revision": 4}

        report = pw.watch_stream(
            [_created("w1:pNEW"), None, None], reconciler=rec,
            sender=lambda b: (sent.append(b), 0)[1], clock=lambda: next(ticks),
            debouncer=pw.Debouncer(window_s=0.0), emit=lambda m: sent.append(f"LOG:{m}"),
            heartbeat_s=1.0, live_panes=live, current_pane=current)
        assert all("SECRET" not in x for x in sent), sent
        assert "SECRET" not in json.dumps(report), report


class TestTheDeferredSweepInstallsItsRevisionBaseline:
    def test_a_stale_frame_after_a_deferred_sweep_is_dropped(self):
        """Step 11 pass-10: the sweep installed fresh METADATA but `safe_record()` omits `revision`,
        so the baseline stayed `None` and `accepts()` would take ANY positive revision — including one
        older than the snapshot just read. Reproduced by the reviewer: a fresh `working@9` record
        followed by a stale `blocked@1` frame notified the owner."""
        rec = pw.Reconciler(_snapshot([("w1:pA", "working", 5, "alpha")]))
        sent = []
        ticks = iter([1000.0, 1000.0, 2000.0, 3000.0])
        live_calls = {"n": 0}

        def live():
            live_calls["n"] += 1
            return set() if live_calls["n"] == 1 else {"w1:pA", "w1:pNEW"}

        def current(_pid):
            return {"pane_id": "w1:pNEW", "workspace_id": "w1", "agent_status": "working",
                    "name": "newborn", "revision": 9}

        report = pw.watch_stream(
            [_created("w1:pNEW", name="newborn"), None,
             _updated("w1:pNEW", "blocked", 1, name="newborn")],   # STALE: older than the snapshot
            reconciler=rec, sender=lambda b: (sent.append(b), 0)[1],
            clock=lambda: next(ticks), debouncer=pw.Debouncer(window_s=0.0),
            emit=lambda _m: None, heartbeat_s=1.0, live_panes=live, current_pane=current)
        assert sent == [], f"a revision older than the installed baseline must be dropped: {report}"
        assert rec.revision_of("w1:pNEW") == 9, rec.revision_of("w1:pNEW")
        assert rec.meta("w1:pNEW").get("name") == "newborn", "fresh metadata must still be installed"


class TestSenderCmdOverride:
    """`--sender-cmd` (#612 follow-up, found by the epic #667 UAT).

    Without it the watcher's only transport is notify.sh, so every acceptance check for
    W2-W7 pages the owner's phone for real and the feature is effectively untestable
    end-to-end. The seam already existed — `sender` is injected into both `startup_sweep`
    and `watch_stream`; only the CLI hardcoded `_default_sender`.
    """

    def test_sender_from_cmd_runs_the_command_and_feeds_the_body_on_stdin(self, tmp_path):
        log = tmp_path / "sent.log"
        sender = pw.sender_from_cmd(f"cat >> {log}")
        assert sender("pane w1:pB8 is blocked") == 0
        assert log.read_text().strip() == "pane w1:pB8 is blocked"

    def test_sender_from_cmd_reports_a_failing_command_as_nonzero(self):
        sender = pw.sender_from_cmd("exit 7")
        assert sender("body") == 7

    def test_sender_from_cmd_never_raises_on_a_broken_command(self):
        """A bad --sender-cmd must degrade to a failed send, not kill the watcher."""
        sender = pw.sender_from_cmd("this-command-does-not-exist-uat667")
        assert sender("body") != 0

    def test_cli_exposes_sender_cmd(self):
        proc = subprocess.run(
            [sys.executable, str(HOOKS / "pane_watch_lib.py"), "watch", "--help"],
            capture_output=True, text=True, check=False)
        assert "--sender-cmd" in proc.stdout

    def test_default_sender_is_still_the_default(self, monkeypatch):
        """Omitting the flag must not silently change the production transport."""
        monkeypatch.setattr(pw, "read_snapshot", lambda *a, **k: {"panes": []})
        assert pw.resolve_sender(None) is pw._default_sender

    def test_resolve_sender_prefers_the_override(self, tmp_path):
        log = tmp_path / "o.log"
        sender = pw.resolve_sender(f"cat >> {log}")
        assert sender is not pw._default_sender
        assert sender("x") == 0


# ---------------------------------------------------------------------------
# #679 — the poll input layer
# ---------------------------------------------------------------------------

def _proc(stdout, returncode=0):
    """A stand-in for `subprocess.run`'s result, which is all `read_snapshot` reads."""
    class _P:
        pass
    p = _P()
    p.returncode = returncode
    p.stdout = stdout
    return p


def _runner_for(docs):
    """A `read_snapshot` runner that serves `docs` in order, then repeats the last one.

    An entry that is not a dict is served as a FAILED call (returncode 1), which is how the
    consecutive-failure ceiling is exercised without patching internals.
    """
    seen = {"i": 0}

    def _run(argv):
        assert argv == ["herdr", "api", "snapshot"], argv
        i = min(seen["i"], len(docs) - 1)
        seen["i"] += 1
        doc = docs[i]
        if not isinstance(doc, dict):
            return _proc("", returncode=1)
        return _proc(json.dumps({"result": doc}))

    return _run


class _Clock:
    """A fake clock that only moves when `sleep` is called — so a poll loop is deterministic."""

    def __init__(self, start=1000.0):
        self.t = float(start)

    def __call__(self):
        return self.t

    def sleep(self, seconds):
        self.t += max(0.0, float(seconds))


def _drain(gen, limit):
    """Pull at most `limit` items, stopping early at `_EOF` (which is yielded, then StopIteration)."""
    out = []
    for item in gen:
        out.append(item)
        if item is pw._EOF or len(out) >= limit:
            break
    return out


def _events_only(lines):
    return [pw.parse_event(x) for x in lines if isinstance(x, str) and pw.parse_event(x) is not None]


class TestPollLinesIsADropInForTheDeadSubscription:
    """#679: `events.subscribe` delivers a backlog burst then goes permanently silent.

    Five instruments agreed, including a controlled stimulus (three `herdr pane rename` calls on a
    live subscription produced 0 frames) and duration-independence (39 events at a 12 s window, 39
    at 12 s, 39 at 50 s). The fix keeps the whole brain and replaces only the line source, so these
    tests assert the SOURCE's contract: the same wire shapes `parse_event` already accepts.
    """

    def test_the_ack_comes_only_after_a_real_poll_succeeded(self):
        """Step-4 finding: a self-issued ack is not evidence. `_cmd_watch` returns 0 only when
        `report["subscribed"]` is true, so an ack emitted before the first read would let a source
        that can NEVER read a snapshot report a healthy watcher — recreating #679's whole failure
        mode in the fix for it."""
        seed = _agent_snapshot([("w1:pA", "idle", 1, "a")])
        clock = _Clock()
        gen = pw.poll_lines(snapshot=seed, interval_s=5, heartbeat_s=300,
                            runner=_runner_for([seed]), clock=clock, sleep=clock.sleep)
        assert pw.is_subscription_ack(next(gen))
        assert clock.t > 1000.0, "the ack must cost at least one poll interval, not zero"

    def test_a_source_that_can_never_read_never_acks(self):
        seed = _agent_snapshot([("w1:pA", "idle", 1, "a")])
        clock = _Clock()
        lines = _drain(pw.poll_lines(snapshot=seed, interval_s=5, heartbeat_s=300,
                                     runner=_runner_for([None]), clock=clock,
                                     sleep=clock.sleep), 10)
        assert not any(isinstance(x, str) and pw.is_subscription_ack(x) for x in lines)
        assert lines[-1] is pw._EOF
        report = pw.watch_stream([x for x in lines if x is not None],
                                 reconciler=pw.Reconciler(seed), sender=lambda body: 0,
                                 clock=clock, heartbeat_s=300)
        assert report["subscribed"] is False, "a dead poll source must not report a live watcher"

    def test_both_ack_types_are_accepted(self):
        """The poll source cannot honestly say `subscription_started`, and the events source must
        keep working — so the predicate accepts both rather than either one lying."""
        assert pw.is_subscription_ack(json.dumps({"result": {"type": "poll_started"}}))
        assert pw.is_subscription_ack(json.dumps({"result": {"type": "subscription_started"}}))
        assert not pw.is_subscription_ack(json.dumps({"result": {"type": "something_else"}}))

    def test_the_seed_snapshot_means_the_first_poll_emits_only_what_changed(self):
        """Seeded from the SAME document the Reconciler was built from. Otherwise the first poll
        would either re-announce all 8 panes or (worse) miss a pane that blocked between the
        startup sweep's snapshot and the first poll."""
        seed = _agent_snapshot([("w1:pA", "idle", 1, "a"), ("w1:pB", "idle", 1, "b")])
        after = _agent_snapshot([("w1:pA", "blocked", 2, "a"), ("w1:pB", "idle", 1, "b")])
        clock = _Clock()
        lines = _drain(pw.poll_lines(snapshot=seed, interval_s=5, heartbeat_s=300,
                                     runner=_runner_for([after]), clock=clock,
                                     sleep=clock.sleep), 4)
        events = _events_only(lines)
        assert [(e.kind, e.pane_id, e.status) for e in events] == [
            ("pane_updated", "w1:pA", "blocked")]

    def test_a_close_is_emitted_before_an_update_from_the_same_cycle(self):
        """Step-4 finding (gpt-5.6-sol). `watch_stream` applies a `pane_closed` line BEFORE its
        heartbeat drain precisely so a due beat cannot page for a pane that is already gone
        (`pane_watch_lib.py` early-lifecycle branch). If an update from the same poll were consumed
        first and the heartbeat came due on it, the drain would run while the closed pane was still
        known — reopening the exact hole that guard exists to close."""
        seed = _agent_snapshot([("w1:pA", "idle", 1, "a"), ("w1:pB", "blocked", 1, "b")])
        after = _agent_snapshot([("w1:pA", "blocked", 2, "a")])
        clock = _Clock()
        lines = _drain(pw.poll_lines(snapshot=seed, interval_s=5, heartbeat_s=300,
                                     runner=_runner_for([after]), clock=clock,
                                     sleep=clock.sleep), 4)
        kinds = [(e.kind, e.pane_id) for e in _events_only(lines)]
        assert kinds == [("pane_closed", "w1:pB"), ("pane_updated", "w1:pA")]

    def test_a_new_pane_is_announced_as_created_then_updated(self):
        """`watch_stream` learns an unknown pane only from a creation, and the update is what
        carries its status — so a pane that appears already blocked needs both frames."""
        seed = _agent_snapshot([("w1:pA", "idle", 1, "a")])
        after = _agent_snapshot([("w1:pA", "idle", 1, "a"), ("w1:pC", "blocked", 4, "c")])
        clock = _Clock()
        lines = _drain(pw.poll_lines(snapshot=seed, interval_s=5, heartbeat_s=300,
                                     runner=_runner_for([after]), clock=clock,
                                     sleep=clock.sleep), 5)
        kinds = [(e.kind, e.pane_id) for e in _events_only(lines)]
        assert kinds == [("pane_created", "w1:pC"), ("pane_updated", "w1:pC")]

    def test_an_unchanged_fleet_yields_none_on_the_heartbeat_interval_not_every_poll(self):
        """`watch_stream` runs its ENTIRE drain (heartbeat write, pending-send retries, stall
        warnings) whenever the line is None. Yielding one per 5 s poll would silently move that
        cadence from 300 s to 5 s, so the idle signal is paced by `heartbeat_s`."""
        seed = _agent_snapshot([("w1:pA", "idle", 1, "a")])
        clock = _Clock()
        gen = pw.poll_lines(snapshot=seed, interval_s=5, heartbeat_s=30,
                            runner=_runner_for([seed]), clock=clock, sleep=clock.sleep)
        next(gen)                       # the ack
        # A quiet fleet yields ONLY idle signals, so counting them per pull proves nothing — the
        # claim is about their PACING. Each one must cost a heartbeat interval of clock, not a poll
        # interval: 6 quiet polls at 5 s before each None, never one per poll.
        marks = []
        for _ in range(3):
            before = clock.t
            assert next(gen) is None
            marks.append(clock.t - before)
        assert all(gap >= 30 for gap in marks), f"idle signals paced at {marks}, expected >=30s each"

    def test_consecutive_snapshot_failures_are_tolerated_then_stop_the_watcher_loudly(self):
        """A herdr restart is not a watcher bug, but a watcher that has stopped watching must be
        loud — the same fail-loud contract the socket path's EOF carries."""
        seed = _agent_snapshot([("w1:pA", "idle", 1, "a")])
        clock = _Clock()
        lines = _drain(pw.poll_lines(snapshot=seed, interval_s=5, heartbeat_s=300,
                                     runner=_runner_for([None]), clock=clock,
                                     sleep=clock.sleep), 12)
        assert lines[-1] is pw._EOF
        # Step-4 finding: the tolerated failures must NOT each yield an idle signal. `watch_stream`
        # runs its whole drain on any None, so one per failed poll would move the retry cadence from
        # 300 s to 5 s and hammer an already-failing transport.
        assert lines[:-1] == [], f"failures leaked idle signals before the heartbeat was due: {lines}"

    def test_a_failure_run_still_beats_when_the_heartbeat_is_genuinely_due(self):
        """The counters are only useful if they reach the heartbeat file — an outage must still
        surface, just on the heartbeat's schedule rather than the poll's."""
        seed = _agent_snapshot([("w1:pA", "idle", 1, "a")])
        clock = _Clock()
        stats = {}
        # interval BELOW heartbeat, or the clamp legitimately rewrites it (see clamp_poll_interval).
        lines = _drain(pw.poll_lines(snapshot=seed, interval_s=2, heartbeat_s=3,
                                     runner=_runner_for([None]), clock=clock,
                                     sleep=clock.sleep, stats=stats), 12)
        assert lines[-1] is pw._EOF
        idles = lines.count(None)
        assert idles >= 1, "an outage must still reach the heartbeat file"
        assert idles < pw.POLL_MAX_CONSECUTIVE_FAILURES + 1, (
            f"{idles} idle signals for {pw.POLL_MAX_CONSECUTIVE_FAILURES + 1} failed polls — the "
            "failure path is back to beating once per poll")
        assert stats["poll_failures"] == pw.POLL_MAX_CONSECUTIVE_FAILURES + 1
        assert stats["polls"] == 0

    def test_a_revision_going_backwards_stops_the_watcher_instead_of_going_blind(self):
        """Step-4 finding, and the hole that tolerating failures opened. The socket path died on a
        herdr restart (EOF -> restart -> fresh snapshot). A poll loop survives it, and if revisions
        reset the stale baselines make `Reconciler.accepts` refuse every later frame for those panes
        — silently blind, which is #679's failure mode reintroduced by its own fix."""
        seed = _agent_snapshot([("w1:pA", "idle", 24, "a")])
        after_restart = _agent_snapshot([("w1:pA", "blocked", 1, "a")])
        clock = _Clock()
        lines = _drain(pw.poll_lines(snapshot=seed, interval_s=5, heartbeat_s=300,
                                     runner=_runner_for([after_restart]), clock=clock,
                                     sleep=clock.sleep), 6)
        assert lines[-1] is pw._EOF
        errors = [pw.parse_error(x) for x in lines if isinstance(x, str)]
        assert any(e and "pane_revision_regressed" in e for e in errors), lines
        # And the report must carry BOTH the reason and the stopped-watching error, not just a code.
        report = pw.watch_stream(lines, reconciler=pw.Reconciler(seed),
                                 sender=lambda body: 0, clock=clock, heartbeat_s=300)
        assert any("regressed" in e for e in report["errors"])
        assert any("stopped watching" in e for e in report["errors"])

    def test_a_recovered_snapshot_resets_the_failure_counter(self):
        """Otherwise transient failures spread over hours would eventually kill a healthy watcher.

        The sequence discriminates: fail, fail, OK, fail, fail, fail, OK. WITHOUT a reset the
        cumulative count reaches 5 and the watcher dies; WITH one, neither run exceeds the ceiling.
        """
        seed = _agent_snapshot([("w1:pA", "idle", 1, "a")])
        ok = _agent_snapshot([("w1:pA", "blocked", 2, "a")])
        clock = _Clock()
        lines = _drain(pw.poll_lines(snapshot=seed, interval_s=5, heartbeat_s=300,
                                     runner=_runner_for([None, None, ok, None, None, None, ok]),
                                     clock=clock, sleep=clock.sleep), 8)
        assert pw._EOF not in lines
        assert [(e.kind, e.pane_id) for e in _events_only(lines)] == [
            ("pane_updated", "w1:pA")]

    def test_a_status_change_with_no_revision_bump_is_dropped_by_the_reconciler(self):
        """An honest ceiling, asserted rather than hoped: the reconciler's revision rule is what
        defeats the backlog replay, and a frame at a stale revision is refused. The event path had
        exactly this hole; polling does not widen it. Measured basis for it not mattering: a real
        block moved `revision` 1 -> 3 on this host (#679 evidence 5)."""
        seed = _agent_snapshot([("w1:pA", "idle", 5, "a")])
        after = _agent_snapshot([("w1:pA", "blocked", 5, "a")])
        clock = _Clock()
        lines = _drain(pw.poll_lines(snapshot=seed, interval_s=5, heartbeat_s=300,
                                     runner=_runner_for([after]), clock=clock,
                                     sleep=clock.sleep), 4)
        emitted = _events_only(lines)
        assert [(e.kind, e.status) for e in emitted] == [("pane_updated", "blocked")]
        sent = []
        report = pw.watch_stream([x for x in lines if isinstance(x, str)],
                                 reconciler=pw.Reconciler(seed),
                                 sender=lambda body: sent.append(body) or 0,
                                 clock=clock, heartbeat_s=300)
        assert sent == [] and report["dropped"] == 1


class TestThePollSourceDrivesTheWholeWatcher:
    """The money test: the real brain, fed by the real new source, notifies exactly once."""

    def test_a_pane_going_blocked_notifies_exactly_once_with_no_pane_contents(self):
        seed = _agent_snapshot([("w1:pA", "idle", 1, "alpha")])
        after = _agent_snapshot([("w1:pA", "blocked", 2, "alpha")])
        clock = _Clock()
        sent = []
        lines = _drain(pw.poll_lines(snapshot=seed, interval_s=5, heartbeat_s=300,
                                     runner=_runner_for([after]), clock=clock,
                                     sleep=clock.sleep), 4)
        report = pw.watch_stream(lines, reconciler=pw.Reconciler(seed),
                                 sender=lambda body: sent.append(body) or 0,
                                 clock=clock, heartbeat_s=300)
        assert report["subscribed"] is True
        assert len(sent) == 1 and len(report["notified"]) == 1
        assert "SECRET prompt text" not in sent[0]
        assert "alpha" in sent[0] and "blocked" in sent[0]

    def test_a_batch_with_a_due_heartbeat_never_pages_for_a_pane_the_same_poll_proves_gone(self):
        """Step-4 finding #2, end to end rather than by frame order alone.

        `watch_stream` guards a closure only when the CURRENT line is `pane_closed`; with the
        heartbeat due on an earlier line the drain runs first and retries a pending send for a pane
        that this very snapshot says is gone. Emitting closes first is what prevents it, so the test
        asserts the OUTCOME (no page) and not just the ordering.
        """
        seed = _agent_snapshot([("w1:pA", "idle", 1, "alpha"), ("w1:pB", "blocked", 1, "bravo")])
        after = _agent_snapshot([("w1:pA", "blocked", 2, "alpha")])          # B closed, A updated
        gen_clock = _Clock()
        lines = _drain(pw.poll_lines(snapshot=seed, interval_s=5, heartbeat_s=300,
                                     runner=_runner_for([after]), clock=gen_clock,
                                     sleep=gen_clock.sleep), 5)
        # A clock that advances past the heartbeat interval on EVERY read, so the drain is due on
        # the first line the stream consumes.
        now = {"t": 1000.0}

        def stepping_clock():
            now["t"] += 400.0
            return now["t"]

        sent = []
        report = pw.watch_stream(
            lines, reconciler=pw.Reconciler(seed),
            sender=lambda body: sent.append(body) or 0, clock=stepping_clock,
            heartbeat_s=300, pending={"w1:pB": {"pane_id": "w1:pB", "name": "bravo"}})
        assert not any("bravo" in body or "w1:pB" in body for body in sent), (
            f"paged for a pane the same poll proved gone: {sent}")
        assert "w1:pB" not in report["pending_at_exit"]

    def test_a_persistent_block_is_caught_even_when_the_consumer_stalls_past_the_interval(self):
        """Step-4 finding #3, answered as a bounded ceiling rather than a promise.

        The generator is PULL-driven, so a 60 s sender call or a heavy drain suspends sampling
        outright — unlike a socket, nothing buffers transitions meanwhile. The honest contract is
        therefore: a block that PERSISTS is always detected on the next sample, however late that
        sample is; a block that begins and clears inside one sampling gap is not reported — which is
        the same trade the 60 s debounce already makes.
        """
        seed = _agent_snapshot([("w1:pA", "idle", 1, "alpha")])
        blocked = _agent_snapshot([("w1:pA", "blocked", 2, "alpha")])
        clock = _Clock()
        gen = pw.poll_lines(snapshot=seed, interval_s=5, heartbeat_s=300,
                            runner=_runner_for([seed, blocked]), clock=clock, sleep=clock.sleep)
        assert pw.is_subscription_ack(next(gen))
        clock.t += 600.0                       # the consumer spent ten minutes inside one send
        # The stall makes the heartbeat genuinely due, so an idle signal legitimately comes first;
        # what matters is that the very next real frame still carries the CURRENT blocked state.
        event = next(pw.parse_event(x) for x in gen
                     if isinstance(x, str) and pw.parse_event(x) is not None)
        assert (event.kind, event.status) == ("pane_updated", "blocked")

    def test_a_second_identical_poll_does_not_re_notify(self):
        """Polling re-reads the SAME blocked state forever, so the dedup that made the event path
        safe is now load-bearing on every single cycle."""
        seed = _agent_snapshot([("w1:pA", "idle", 1, "alpha")])
        after = _agent_snapshot([("w1:pA", "blocked", 2, "alpha")])
        clock = _Clock()
        sent = []
        lines = _drain(pw.poll_lines(snapshot=seed, interval_s=5, heartbeat_s=300,
                                     runner=_runner_for([after, after, after]), clock=clock,
                                     sleep=clock.sleep), 6)
        report = pw.watch_stream(lines, reconciler=pw.Reconciler(seed),
                                 sender=lambda body: sent.append(body) or 0,
                                 clock=clock, heartbeat_s=300)
        assert len(sent) == 1, f"re-notified on an unchanged poll: {sent}"
        assert report["notified"]


class TestTheHeartbeatProvesTheInputLayerIsAlive:
    """#679 evidence 3 was duration-independence: `events` = 39 at 12 s, 39 at 12 s, 39 at 50 s.

    On a quiet fleet a HEALTHY watcher also reports a flat `events`, so that counter can never
    distinguish "alive and nothing happening" from "input layer dead" — which is exactly how the
    dead subscription hid for a whole epic. A monotonically growing poll count can.
    """

    def test_the_heartbeat_carries_the_poll_count(self, tmp_path):
        target = tmp_path / "hb.json"
        pw.write_heartbeat(str(target), now=123.0, report={"events": 0, "notified": []},
                           extra={"polls": 7, "poll_failures": 1})
        payload = json.loads(target.read_text())
        assert payload["events"] == 0 and payload["polls"] == 7
        assert payload["poll_failures"] == 1

    def test_extra_cannot_clobber_the_liveness_fields(self, tmp_path):
        """`ts` is what an external observer alerts on staleness with — a caller must not be able
        to freeze it."""
        target = tmp_path / "hb.json"
        pw.write_heartbeat(str(target), now=456.0, extra={"ts": 0, "pid": 0, "polls": 3})
        payload = json.loads(target.read_text())
        assert payload["ts"] == 456.0 and payload["pid"] == os.getpid()
        assert payload["polls"] == 3

    def test_poll_lines_counts_its_polls_into_the_stats_dict(self):
        seed = _agent_snapshot([("w1:pA", "idle", 1, "a")])
        clock = _Clock()
        stats = {}
        gen = pw.poll_lines(snapshot=seed, interval_s=5, heartbeat_s=3000,
                            runner=_runner_for([seed]), clock=clock, sleep=clock.sleep,
                            stats=stats)
        next(gen)
        for _ in range(3):
            next(gen, None)
        assert stats["polls"] >= 3


class TestThePollSourceIsTheDefault:
    """The subscription is dead in production (#679), so the default input layer must be the one
    that works. The events path stays reachable — it is one flag away if herdr fixes the feed, and
    its tests are the written record of what five instruments learned."""

    def test_dry_run_defaults_to_the_poll_plan(self):
        proc = subprocess.run([sys.executable, str(HOOKS / "pane_watch_lib.py"), "watch",
                               "--dry-run"], capture_output=True, text=True, check=False)
        assert proc.returncode == 0
        plan = json.loads(proc.stdout)
        assert plan["source"] == "poll"
        assert plan["command"] == ["herdr", "api", "snapshot"]
        assert plan["interval_s"] == pw.DEFAULT_POLL_INTERVAL_S

    def test_cli_exposes_the_poll_flags(self):
        proc = subprocess.run([sys.executable, str(HOOKS / "pane_watch_lib.py"), "watch", "--help"],
                              capture_output=True, text=True, check=False)
        assert "--poll-interval-s" in proc.stdout and "--source" in proc.stdout


def _snapshot_with_agents(panes, agents):
    """`herdr api snapshot` INCLUDING its `agents` node — where `state_change_seq` actually lives.

    Measured live 2026-07-28: `snapshot.panes[]` carries `revision`; `snapshot.agents[]` carries
    `pane_id`, `agent_status` AND `state_change_seq`, and the two numbers are unrelated
    (`w1:pCX rev=3 seq=2142`). `agents` entries are `(pane_id, status, revision, seq)`.
    """
    doc = _snapshot(panes)
    doc["snapshot"]["agents"] = [
        {"pane_id": p, "agent_status": s, "revision": r, "state_change_seq": q, "agent": "claude"}
        for p, s, r, q in agents]
    return doc


class TestTheDedupKeyIsTheSequenceNotTheRevision:
    """The bug the LIVE run found, which no unit test could have guessed.

    Driving a real Claude pane to a permission prompt produced `working` at `revision` 3 and then
    `blocked` at `revision` 3 — the SAME number, because herdr bumps `revision` on pane-record
    changes, not on every `agent_status` transition. `Reconciler.accepts` requires a STRICTLY newer
    key, so it refused the one frame the whole feature exists for: a genuine block, `polls` climbing,
    `poll_failures: 0`, and ZERO notifications. The transport was only the first of two reasons the
    watcher could never fire.

    `state_change_seq` is the key #612's first design wanted and dropped because the `pane.updated`
    EVENT does not carry it. The SNAPSHOT does, on its `agents` records — observed advancing
    2148 -> 2151 across the same transition that left `revision` unmoved.
    """

    def test_merge_agent_sequences_copies_the_sequence_onto_its_pane(self):
        doc = _snapshot_with_agents([("w1:pA", "working", 3, "a"), ("w1:pDash", "unknown", 0, None)],
                                    [("w1:pA", "working", 3, 2142)])
        panes = pw.panes_by_id(pw.merge_agent_sequences(doc))
        assert panes["w1:pA"]["state_change_seq"] == 2142
        # The dashboard pane has no agent at all, so it keeps falling back to `revision`.
        assert "state_change_seq" not in panes["w1:pDash"]
        assert pw._revision_of(panes["w1:pA"]) == 2142
        assert pw._revision_of(panes["w1:pDash"]) == 0

    def test_merge_does_not_mutate_the_snapshot_it_was_given(self):
        doc = _snapshot_with_agents([("w1:pA", "working", 3, "a")], [("w1:pA", "working", 3, 2142)])
        pw.merge_agent_sequences(doc)
        assert "state_change_seq" not in doc["snapshot"]["panes"][0]

    def test_the_poll_source_enriches_its_own_snapshots(self):
        """`read_snapshot` deliberately does NOT enrich (that broke the events path — see
        `TestTheEventsPathKeepsItsOwnKey`), so the poll source does it for both its seed and every
        poll it takes."""
        doc = _snapshot_with_agents([("w1:pA", "working", 3, "a")], [("w1:pA", "working", 3, 2142)])
        after = _snapshot_with_agents([("w1:pA", "blocked", 3, "a")],
                                      [("w1:pA", "blocked", 3, 2151)])
        clock = _Clock()
        lines = _drain(pw.poll_lines(snapshot=doc, interval_s=5, heartbeat_s=300,
                                     runner=_runner_for([after]), clock=clock,
                                     sleep=clock.sleep), 4)
        kinds = [(e.kind, e.pane_id, e.revision) for e in _events_only(lines)]
        assert kinds == [("pane_updated", "w1:pA", 2151)], (
            f"an unenriched seed made the first cycle re-baseline the whole fleet: {kinds}")

    def test_parse_event_prefers_the_sequence_and_still_falls_back_to_revision(self):
        with_seq = json.dumps({"event": "pane_updated", "data": {"pane": {
            "pane_id": "w1:pA", "agent_status": "blocked", "revision": 3,
            "state_change_seq": 2151}}})
        assert pw.parse_event(with_seq).revision == 2151
        # An events-path frame carries no sequence, so that path is byte-identical to before.
        assert pw.parse_event(_updated("w1:pA", "blocked", revision=7)).revision == 7

    def test_the_live_case_notifies_a_status_change_with_no_revision_bump(self):
        """working@rev3,seq2148 -> blocked@rev3,seq2151. This is the exact pair measured on the box,
        and against a revision-only key it produced no notification at all."""
        seed = _snapshot_with_agents([("w1:pA", "working", 3, "uat679")],
                                     [("w1:pA", "working", 3, 2148)])
        after = _snapshot_with_agents([("w1:pA", "blocked", 3, "uat679")],
                                      [("w1:pA", "blocked", 3, 2151)])
        clock = _Clock()
        sent = []
        seed_enriched = pw.merge_agent_sequences(seed)
        lines = _drain(pw.poll_lines(snapshot=seed_enriched, interval_s=5, heartbeat_s=300,
                                     runner=_runner_for([after]), clock=clock,
                                     sleep=clock.sleep), 4)
        report = pw.watch_stream(lines, reconciler=pw.Reconciler(seed_enriched),
                                 sender=lambda body: sent.append(body) or 0,
                                 clock=clock, heartbeat_s=300)
        assert len(sent) == 1, f"the live-measured transition sent {len(sent)} notifications"
        assert report["dropped"] == 0
        assert "SECRET prompt text" not in sent[0]

    def test_a_change_with_neither_key_advancing_is_still_dropped(self):
        """The honest remaining ceiling, and it stays asserted: if NOTHING monotonic moved there is
        no way to tell a real change from a replayed frame, and the reconciler refuses."""
        seed = _snapshot_with_agents([("w1:pA", "working", 3, "a")], [("w1:pA", "working", 3, 2148)])
        after = _snapshot_with_agents([("w1:pA", "blocked", 3, "a")], [("w1:pA", "blocked", 3, 2148)])
        clock = _Clock()
        sent = []
        seed_enriched = pw.merge_agent_sequences(seed)
        lines = _drain(pw.poll_lines(snapshot=seed_enriched, interval_s=5, heartbeat_s=300,
                                     runner=_runner_for([after]), clock=clock,
                                     sleep=clock.sleep), 4)
        report = pw.watch_stream(lines, reconciler=pw.Reconciler(seed_enriched),
                                 sender=lambda body: sent.append(body) or 0,
                                 clock=clock, heartbeat_s=300)
        assert sent == [] and report["dropped"] == 1


class TestTheRegressionGuardAfterTheStep4Findings:
    def test_closes_are_yielded_before_the_regression_error(self):
        """Step-4 pass-2 finding #1: the guard used to emit its error and `_EOF` BEFORE the batch's
        closes, so a due heartbeat drained on the error line while the closed pane was still known —
        the same false page the ack ordering was fixed for."""
        seed = _agent_snapshot([("w1:pA", "idle", 24, "a"), ("w1:pB", "blocked", 1, "b")])
        after = _agent_snapshot([("w1:pA", "blocked", 1, "a")])        # A regressed 24 -> 1, B closed
        clock = _Clock()
        lines = _drain(pw.poll_lines(snapshot=seed, interval_s=5, heartbeat_s=300,
                                     runner=_runner_for([after]), clock=clock,
                                     sleep=clock.sleep), 6)
        kinds = [x for x in lines if isinstance(x, str)]
        first = pw.parse_event(kinds[0])
        assert first is not None and (first.kind, first.pane_id) == ("pane_closed", "w1:pB")
        assert any(pw.parse_error(x) and "regressed" in pw.parse_error(x) for x in kinds)
        assert lines[-1] is pw._EOF

    def test_a_key_that_vanishes_and_returns_lower_re_baselines_instead_of_going_blind(self):
        """Step-4 pass-2 finding #2 was that `24 -> None -> 1` bridged the guard and left the
        reconciler holding baseline 24 — a subscribed, error-free, PERMANENTLY BLIND watcher.

        The pass-3 fix answers it better than the `_EOF` that pass 2 earned: a key vanishing means the
        agent detached, and its return is an attach, which re-baselines through a synthetic close. So
        the outcome is neither blindness nor death — the pane is re-registered and its block is
        delivered. The genuine regression (a key present on both sides moving backwards) still stops
        the watcher loudly; that is the sibling test.
        """
        seed = _agent_snapshot([("w1:pA", "idle", 24, "a")])
        no_key = {"snapshot": {"panes": [{"pane_id": "w1:pA", "workspace_id": "w1",
                                          "agent_status": "idle", "name": "a"}]}}
        lower = _agent_snapshot([("w1:pA", "blocked", 1, "a")])
        clock = _Clock()
        sent = []
        lines = _drain(pw.poll_lines(snapshot=seed, interval_s=5, heartbeat_s=300,
                                     runner=_runner_for([no_key, lower]), clock=clock,
                                     sleep=clock.sleep), 6)
        kinds = [(e.kind, e.pane_id) for e in _events_only(lines)]
        assert ("pane_closed", "w1:pA") in kinds and ("pane_created", "w1:pA") in kinds, kinds
        report = pw.watch_stream(lines, reconciler=pw.Reconciler(seed),
                                 sender=lambda body: sent.append(body) or 0, clock=clock,
                                 heartbeat_s=300, live_panes=lambda: {"w1:pA"})
        assert len(sent) == 1, (
            f"the block was lost behind a stale baseline — the blindness pass 2 found: {report}")


class TestThePollIntervalIsValidated:
    """Step-4 pass-2 finding #5: `argparse`'s `type=float` accepts 0, negatives, NaN and inf."""

    @pytest.mark.parametrize("bad", [0, -5, float("nan"), float("inf"), "abc", None])
    def test_a_nonpositive_or_nonfinite_interval_falls_back_to_the_default(self, bad):
        assert pw.clamp_poll_interval(bad, 300.0) == pw.DEFAULT_POLL_INTERVAL_S

    def test_an_interval_longer_than_the_heartbeat_is_clamped_to_it(self):
        """Otherwise the idle signal is held past its own deadline and the advertised heartbeat
        cadence silently becomes the poll interval."""
        assert pw.clamp_poll_interval(600.0, 300.0) == 300.0

    def test_a_sane_interval_is_left_alone(self):
        assert pw.clamp_poll_interval(5.0, 300.0) == 5.0

    def test_a_tight_loop_interval_does_not_reach_the_generator(self):
        seed = _agent_snapshot([("w1:pA", "idle", 1, "a")])
        clock = _Clock()
        gen = pw.poll_lines(snapshot=seed, interval_s=0, heartbeat_s=300,
                            runner=_runner_for([_agent_snapshot([("w1:pA", "blocked", 2, "a")])]),
                            clock=clock, sleep=clock.sleep)
        next(gen)
        assert clock.t >= 1000.0 + pw.DEFAULT_POLL_INTERVAL_S, (
            "a zero interval polled without sleeping — that is a tight subprocess loop")


class TestTheDetectionGapIsCumulativeAndSaysSo:
    def test_one_drain_walks_every_pending_send_so_the_gap_scales_with_the_queue(self):
        """Step-4 pass-2 finding #3, asserted instead of argued about.

        The design first claimed a gap of `interval + one sender timeout`. That was false: ONE drain
        walks every pending send sequentially, each able to burn its full 60 s subprocess timeout,
        and the generator is pull-driven so no sampling happens meanwhile. This pins the real shape,
        which the design now states honestly — the queue is only non-empty when the transport is
        already failing, so what degrades is lateness, never loss of a persistent block.
        """
        now = {"t": 1000.0}
        sent = []

        def clock():
            return now["t"]

        def slow_sender(body):
            now["t"] += 60.0                      # one send, one full subprocess timeout
            sent.append(body)
            return 0

        panes = [(f"w1:pQ{i}", "blocked", 1, f"n{i}") for i in range(3)]
        seed = _snapshot(panes)
        pending = {p: {"pane_id": p, "name": n} for p, _s, _r, n in panes}
        report = pw.watch_stream([None], reconciler=pw.Reconciler(seed), sender=slow_sender,
                                 clock=clock, heartbeat_s=300, pending=dict(pending),
                                 emit=lambda _m: None)
        assert len(report["notified"]) == 3, report["notified"]
        assert now["t"] - 1000.0 >= 180.0, (
            "three pending sends cost less than three sender timeouts — the cumulative bound the "
            "design documents is not what the code does")


class TestOnlyAgentBearingPanesAreDiffed:
    """Step-4 pass-3 finding #1, reproduced before fixing: `state_change_seq` counts in the thousands
    and `revision` in single digits, so letting one fall back to the other compared unrelated numeric
    domains. A routine agent DETACH — a Claude session exits, its pane stays open — read as
    `2148 -> 5` and killed the entire watcher as a bogus "source reset"."""

    def test_an_agent_detaching_does_not_kill_the_watcher(self):
        seed = pw.merge_agent_sequences(
            _snapshot_with_agents([("w1:pA", "working", 4, "a")], [("w1:pA", "working", 4, 2148)]))
        detached = _snapshot_with_agents([("w1:pA", "idle", 5, "a")], [])
        clock = _Clock()
        lines = _drain(pw.poll_lines(snapshot=seed, interval_s=5, heartbeat_s=300,
                                     runner=_runner_for([detached]), clock=clock,
                                     sleep=clock.sleep), 5)
        assert pw._EOF not in lines, "a routine agent detach was misread as a source reset"
        assert not any(isinstance(x, str) and pw.parse_error(x) for x in lines)

    def test_a_pane_with_no_agent_is_not_diffed_at_all(self):
        """It cannot be an agent waiting on a human, so its churn is noise — and diffing it is what
        drags `revision` into a `state_change_seq` comparison."""
        seed = pw.merge_agent_sequences(
            _snapshot_with_agents([("w1:pDash", "unknown", 0, None)], []))
        after = _snapshot_with_agents([("w1:pDash", "idle", 9, None)], [])
        clock = _Clock()
        gen = pw.poll_lines(snapshot=seed, interval_s=5, heartbeat_s=30,
                            runner=_runner_for([after]), clock=clock, sleep=clock.sleep)
        first = next(gen)
        assert first is None or pw.parse_event(first) is None, (
            f"an agent-less pane produced a pane event: {first}")

    def test_an_agent_attaching_re_baselines_instead_of_comparing_across_kinds(self):
        """The reconciler still holds that pane's `revision` baseline, and a sequence frame compared
        against it is the very cross-kind comparison this fix removes. A synthetic close makes
        `forget_pane` drop the stale baseline so the creation registers with none at all — and the
        pane is then notified for on its own terms."""
        seed = pw.merge_agent_sequences(_snapshot_with_agents([("w1:pA", "idle", 9000, "a")], []))
        attached = _snapshot_with_agents([("w1:pA", "blocked", 9000, "a")],
                                         [("w1:pA", "blocked", 9000, 12)])
        clock = _Clock()
        sent = []
        lines = _drain(pw.poll_lines(snapshot=seed, interval_s=5, heartbeat_s=300,
                                     runner=_runner_for([attached]), clock=clock,
                                     sleep=clock.sleep), 6)
        kinds = [(e.kind, e.pane_id) for e in _events_only(lines)]
        assert kinds[0] == ("pane_closed", "w1:pA"), kinds
        assert ("pane_created", "w1:pA") in kinds and ("pane_updated", "w1:pA") in kinds
        # The baseline (revision 9000) is far ABOVE the new sequence (12), so without the re-baseline
        # the block would have been silently refused. It must still notify.
        report = pw.watch_stream(lines, reconciler=pw.Reconciler(seed),
                                 sender=lambda body: sent.append(body) or 0, clock=clock,
                                 heartbeat_s=300, live_panes=lambda: {"w1:pA"})
        assert len(sent) == 1, f"a re-baselined pane's block was lost: {sent} / {report}"


class TestTheEventsPathKeepsItsOwnKey:
    """Step-4 pass-3 finding #2, reproduced: enriching centrally looked tidy and broke the retained
    recovery path. The reconciler's baseline became a sequence (~2148) while real event frames carry
    only `revision` (~5), so every event would have been silently dropped the day herdr repairs the
    feed — with the watcher still reporting subscribed and exiting 0."""

    def test_read_snapshot_does_not_enrich(self):
        doc = _snapshot_with_agents([("w1:pA", "working", 4, "a")], [("w1:pA", "working", 4, 2148)])
        got = pw.read_snapshot(_runner_for([doc]))
        assert "state_change_seq" not in pw.panes_by_id(got)["w1:pA"]
        assert pw._revision_of(pw.panes_by_id(got)["w1:pA"]) == 4

    def test_a_revision_only_event_frame_is_accepted_against_an_unenriched_baseline(self):
        doc = _snapshot_with_agents([("w1:pA", "working", 4, "a")], [("w1:pA", "working", 4, 2148)])
        rec = pw.Reconciler(pw.read_snapshot(_runner_for([doc])))
        assert rec.revision_of("w1:pA") == 4
        assert rec.accepts(pw.parse_event(_updated("w1:pA", "blocked", revision=5))) is True

    def test_the_enriched_baseline_would_have_refused_it(self):
        """The refuted arrangement, pinned so nobody re-introduces central enrichment."""
        doc = _snapshot_with_agents([("w1:pA", "working", 4, "a")], [("w1:pA", "working", 4, 2148)])
        rec = pw.Reconciler(pw.merge_agent_sequences(pw.read_snapshot(_runner_for([doc]))))
        assert rec.revision_of("w1:pA") == 2148
        assert rec.accepts(pw.parse_event(_updated("w1:pA", "blocked", revision=5))) is False
