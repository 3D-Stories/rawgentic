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
        proc = subprocess.run([sys.executable, str(HOOKS / "pane_watch_lib.py"), "watch",
                               "--dry-run"], capture_output=True, text=True, check=False)
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
        assert report["errors"] and "socket closed" in report["errors"][0]

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
