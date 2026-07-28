#!/usr/bin/env python3
"""Blocked-pane watcher: herdr pane-state poll -> notify-owner (#612, #679, epic #667/#684).

WHY THIS EXISTS
---------------
An agent that hits an approval prompt sits `blocked` until a human answers. On an unattended run
nobody is looking, so the run stalls until someone notices. This watches herdr's socket for panes
entering `blocked` and hands ONE line to the transport that already exists.

THE TRANSPORT IS NOT REBUILT HERE (run-start finding F-1)
---------------------------------------------------------
`projects/sentinel/bin/notify.sh`, wrapped by the `notify-owner` workspace skill, already sends
one-way iMessage and already handles password-out-of-argv, 0600 temp files, HTTP-code reporting and a
self-check; `hooks/hermes_bridge.py` (#568/#584) is the two-way path. #608 AC5 proved notify-owner
reachability from a herdr context, and #612 AC1 names that transport. This module owns
subscribe -> recognise -> reconcile -> debounce -> notify -> heartbeat, and nothing else.

WHAT THE LIVE PROBES OVERTURNED — read this before changing the event layer
---------------------------------------------------------------------------
A first version of this module was built on `pane.agent_status_changed` and a `state_change_seq`
dedup key. A Step-4 review called both wrong and the probes agreed. All of the following is measured
against herdr 0.7.5 on this host, 2026-07-28:

1. **`pane.agent_status_changed` was never observed firing at all** — 0 frames across two captures
   (135 s subscribed to all 8 panes, then 90 s subscribed to two specific panes). It also requires a
   `pane_id`, so it cannot be subscribed globally, and subscriptions are fixed for a connection —
   meaning a pane created later would never be covered. It is not used here.
2. **`pane.agent_status_changed` carries NO sequence number.** Its properties are exactly
   `agent, agent_status, display_agent, pane_id, state_labels, title, workspace_id`. The
   `state_change_seq` the first design keyed on lives on `snapshot.agents`, NOT on the event and NOT
   on `snapshot.panes`. A reconciler keyed on it would have received `None` for every real event.
3. **`pane.updated` is GLOBAL and carries everything needed.** No `pane_id`; the payload embeds a
   full pane record with `agent_status`, `label`, `workspace_id`, `tab_id`, `agent`, and a
   **monotonic `revision`** — which is the dedup key the first design wanted and could not find.
   Observed live, repeatedly, with real `working`/`idle`/`unknown` values.
4. **A fresh subscription REPLAYS a backlog.** Within 450 ms a subscribe delivered created/closed
   events for six panes that were long gone, one of them created AND closed inside the replay. So
   reconciliation against `session.snapshot` (whose panes carry `revision`) is load-bearing: without
   it a fresh watcher's FIRST act would be to page the owner about panes that no longer exist.
5. Wire names are snake_case (`pane_updated`) while subscription names are dotted (`pane.updated`).
   A parser keyed on the name it subscribed with sees nothing, silently.

AND THEN THE WHOLE TRANSPORT TURNED OUT TO BE DEAD (#679) — read this before changing the source
-----------------------------------------------------------------------------------------------
`events.subscribe` on herdr 0.7.5 delivers a one-time backlog burst of ~39 frames in under 3 seconds
and then delivers **nothing, ever**. The epic #667 UAT found it: this module subscribed, drained the
backlog, and sat silent forever — heartbeating, reporting healthy, never notifying. Five independent
instruments agreed, including a **controlled stimulus** (three `herdr pane rename` calls on a pane we
owned, on a live subscription -> 0 frames) and **duration-independence** (39 events at a 12 s window,
39 at 12 s, 39 at 50 s — a live feed cannot be duration-independent). Ruled out with evidence: the
request shape is correct, `events.subscribe` accepts only `subscriptions` (no `since`/`replay`), a
keepalive is impossible (`ping` on a subscribed connection returns `Connection reset by peer`, so the
socket cannot be written to at all), and the server exposes exactly two `events.*` methods out of 90.

So the DEFAULT input layer is now `poll_lines`, which diffs `herdr api snapshot` reads. Owner
decision 2026-07-28: poll, not `events.wait` in a loop (contract unverified, may drop transitions
between calls). Two things make this cheap rather than a rewrite:

- **`watch_stream` takes `lines` as its first argument and never touched a socket anyway**, so the
  debounce, the provenance boundary, the delivery-retry mechanism and the startup sweep are untouched.
  Be precise about the seam, though — a Step-11 finding corrected an earlier, broader claim here:
  polling replaces the transport, but **source-specific key normalization also crosses parsing,
  reconciler initialization and deferred registration**. `_revision_of` prefers `state_change_seq`
  where the poll path put one; `read_snapshot` deliberately does NOT enrich, so the events path keeps
  `revision`; and `current_pane_record(enrich=...)` follows whichever source is running. "Only the
  line source changed" would be a comfortable description and a false one.
- **The one other project on this box that displays live herdr pane state polls the CLI too**
  (`projects/herdr-dashboard` shells out to `herdr tab list`/`herdr pane list`; it has no socket
  client at all). Polling is the shape that demonstrably works here.

The cost is a latency floor of one poll interval, and a block that clears inside one interval is not
reported — which is a block nobody wanted a text about. `socket_lines` and the whole subscription
path are RETAINED behind `--source events`: they are one flag away if herdr fixes the feed, and
their tests are the written record of the five instruments above.

**The heartbeat's `events` counter cannot prove liveness on its own, and that is why this hid for a
whole epic:** a healthy watcher on a quiet fleet also reports a flat `events`. `polls` (in the
heartbeat's payload, via `write_heartbeat(extra=...)`) is the counter that distinguishes "alive and
nothing happening" from "input layer dead".

AC5 IS A REDACTION RULE, AND A KEY ALLOWLIST ALONE IS NOT ENOUGH
----------------------------------------------------------------
Bodies carry pane LABELS only, never pane contents. The payloads hand us contents whether we want
them or not: `PaneOutputMatchedEvent.matched_line`, a whole `PaneReadResult.text`, and every pane
record's `terminal_title`/`terminal_title_stripped`. A review pointed out that refusing a `title=`
keyword does not stop a caller passing `label=event.title`, so the boundary here is PROVENANCE, not
keyword hygiene: `body_for_pane` is the only public builder, it SELECTS its own fields from a pane
record, and it accepts no caller-supplied display text at all. The watcher also never subscribes to
`pane.output_matched`, so contents are refused a layer earlier too.

FAIL MODE: fail-loud. A watcher that silently stops watching is worse than none, because a quiet
fleet and a dead watcher look identical from outside. Socket and subscription failures exit non-zero
with herdr's own error payload preserved (#673's lesson), and the heartbeat exists so an EXTERNAL
observer can tell the difference — see `heartbeat_path` and the caveat on `stall_warning`.

Pure core + injected effects (`registry_prune.py` is the house exemplar).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import socket
import subprocess
import sys
import time
from dataclasses import dataclass

# Wire names (snake_case) — NOT the dotted subscription names.
EVENT_PANE_UPDATED = "pane_updated"
EVENT_PANE_CLOSED = "pane_closed"
EVENT_PANE_CREATED = "pane_created"

BLOCKED = "blocked"
AGENT_STATUSES = frozenset({"idle", "working", "blocked", "done", "unknown"})
STALLABLE_STATUSES = frozenset({"idle", "unknown"})

# Anything here is pane CONTENTS. Never selected into a body; a pane record carrying them is fine,
# because the builder picks fields rather than splatting.
CONTENT_FIELDS = frozenset({
    "terminal_title", "terminal_title_stripped", "title", "matched_line", "text", "read",
    "lines", "screen", "output",
})

DEFAULT_DEBOUNCE_S = 60.0
DEFAULT_HEARTBEAT_S = 300.0
DEFAULT_STALL_S = 1800.0
DEFAULT_SOCKET = "~/.config/herdr/herdr.sock"
DEFAULT_HEARTBEAT_PATH = "~/.claude/rawgentic-pane-watch-heartbeat.json"

# 5 s: a human being waited on does not care, and one `herdr api snapshot` is cheap. A block that
# clears inside one interval is one nobody wanted a text about.
DEFAULT_POLL_INTERVAL_S = 5.0
# A herdr restart is not a watcher bug, so a short run of failures is absorbed. Past this the
# watcher stops LOUDLY — a dead watcher and a quiet fleet must never look alike (#679).
POLL_MAX_CONSECUTIVE_FAILURES = 3
# The one error code that is TERMINAL rather than informational: every baseline is stale, so no
# pending notification can still be trusted. Handled before the heartbeat drain in `watch_stream`.
ERROR_SOURCE_RESET = "pane_revision_regressed"
SOURCE_POLL = "poll"
SOURCE_EVENTS = "events"


# A sentinel yielded when the socket closes, so the caller can tell EOF from silence.
_EOF = object()


class WatchError(RuntimeError):
    """A fail-loud refusal: socket failure, subscription refusal, or a redaction violation."""


@dataclass(frozen=True)
class PaneEvent:
    """One normalised wire event. `pane` is the embedded record for `pane_updated`."""
    kind: str
    pane_id: str
    status: str | None = None
    revision: int | None = None
    pane: dict | None = None


# ---------------------------------------------------------------------------
# subscriptions
# ---------------------------------------------------------------------------

def build_subscriptions() -> list[dict]:
    """The `events.subscribe` params.

    GLOBAL only, and deliberately so. `pane.agent_status_changed` needs a `pane_id`, was never
    observed firing, and would leave panes created after the subscription permanently uncovered
    (subscriptions are fixed for a connection). `pane.updated` needs no pane id, fires on real
    status changes, and embeds the whole pane record.

    `pane.created` IS requested (Step 11 BLOCKER): registration used to depend on a `pane_created`
    frame that was never subscribed, so every pane created after the initial snapshot was silently
    ignored forever — a new pane could go `working -> blocked` and both frames were dropped.

    `pane.output_matched` is never requested: it carries `matched_line` and a whole `read.text`, and
    the cheapest way to honour AC5 is to never receive contents at all.
    """
    return [{"type": "pane.updated"}, {"type": "pane.created"}, {"type": "pane.closed"}]


def subscribe_request(request_id: str = "rawgentic-pane-watch") -> dict:
    return {"id": request_id, "method": "events.subscribe",
            "params": {"subscriptions": build_subscriptions()}}


# ---------------------------------------------------------------------------
# parsing — lenient about junk, strict about names
# ---------------------------------------------------------------------------

def _load(line) -> dict | None:
    if not isinstance(line, str) or not line.strip():
        return None
    try:
        doc = json.loads(line)
    except ValueError:
        return None
    return doc if isinstance(doc, dict) else None


# Both ack types mean the same thing — "the input layer confirmed itself live" — which is what
# `report["subscribed"]` has always meant. The poll source cannot honestly claim
# `subscription_started` (it subscribes to nothing), and the events source must keep working, so the
# predicate accepts both rather than either one lying.
ACK_TYPES = frozenset({"subscription_started", "poll_started"})


def ack_kind(line):
    """The ack's TYPE (`subscription_started` / `poll_started`), or None.

    Returning the kind rather than a bool is what lets the report and the operator log name the input
    layer that actually acked, instead of hardcoding the socket's word for it.
    """
    doc = _load(line)
    result = doc.get("result") if doc else None
    if isinstance(result, dict) and result.get("type") in ACK_TYPES:
        return result["type"]
    return None


def is_subscription_ack(line) -> bool:
    """The input layer is LIVE, not merely connected. A connected-but-unsubscribed socket reports
    nothing and looks exactly like a quiet fleet — and so does a poll loop that never ran."""
    return ack_kind(line) is not None


def parse_error(line) -> str | None:
    """herdr's own error message, preserved rather than reduced to a return code (#673)."""
    doc = _load(line)
    err = doc.get("error") if doc else None
    if not isinstance(err, dict):
        return None
    return f"{err.get('code')}: {err.get('message')}"


def parse_event(line) -> PaneEvent | None:
    """One wire line -> a normalised event, or None for anything unusable.

    Returns None rather than raising: the stream is a live socket and one malformed frame must not
    turn a hiccup into a silent outage. Keyed on the SNAKE_CASE wire names; the dotted subscription
    name is not accepted, because silently matching both would hide exactly that confusion.
    """
    doc = _load(line)
    if doc is None:
        return None
    kind, data = doc.get("event"), doc.get("data")
    if not isinstance(kind, str) or not isinstance(data, dict):
        return None

    if kind == EVENT_PANE_UPDATED:
        pane = data.get("pane")
        if not isinstance(pane, dict) or not isinstance(pane.get("pane_id"), str):
            return None
        status = pane.get("agent_status")
        return PaneEvent(kind=kind, pane_id=pane["pane_id"],
                         status=status if status in AGENT_STATUSES else None,
                         revision=_revision_of(pane),
                         pane=pane)

    if kind in (EVENT_PANE_CLOSED, EVENT_PANE_CREATED):
        pane_id = data.get("pane_id") or (data.get("pane") or {}).get("pane_id")
        if not isinstance(pane_id, str):
            return None
        return PaneEvent(kind=kind, pane_id=pane_id,
                         pane=data.get("pane") if isinstance(data.get("pane"), dict) else None)

    return None


# ---------------------------------------------------------------------------
# the transition rule
# ---------------------------------------------------------------------------

def is_blocked_transition(prior, new) -> bool:
    """A transition INTO `blocked`.

    `prior is None` counts: a pane whose first observed status is `blocked` is an agent waiting on a
    human right now, and requiring a known prior status would silently miss it — the failure mode
    #612's description adopts from F2. Same-status repeats are not transitions, so a reconnect that
    re-observes `blocked` does not re-notify.
    """
    return new == BLOCKED and prior != BLOCKED


# ---------------------------------------------------------------------------
# reconciliation — the answer to the backlog replay
# ---------------------------------------------------------------------------

class Reconciler:
    """Accepts an event only for a pane we believe exists, with a NEWER `revision`.

    Built from `session.snapshot`, whose panes carry `revision`. The replay is immediate and real, so
    this is what stands between a fresh watcher and a burst of notifications about dead panes.

    A genuinely NEW pane is legitimately absent from the snapshot, so `register_pane` learns it from
    the live feed; such a pane has no baseline and its first event is accepted.
    """

    def __init__(self, snapshot):
        self._rev: dict[str, int | None] = {}
        self._meta: dict[str, dict] = {}
        node = snapshot.get("snapshot", snapshot) if isinstance(snapshot, dict) else {}
        panes = node.get("panes") if isinstance(node, dict) else None
        if not isinstance(panes, list):
            raise WatchError("session.snapshot carried no pane list — refusing to reconcile against "
                             "nothing, because that would accept the entire backlog replay")
        for pane in panes:
            if isinstance(pane, dict) and isinstance(pane.get("pane_id"), str):
                self._rev[pane["pane_id"]] = _revision_of(pane)
                self._meta[pane["pane_id"]] = pane

    def known_panes(self) -> list[str]:
        return sorted(self._rev)

    def register_pane(self, pane, *, live_pane_ids=None) -> bool:
        """Learn a pane from the live feed. Returns True if it was registered.

        Step 11 pass-2 BLOCKER: subscribing to `pane.created` (the pass-1 fix) reopened the very hole
        the reconciler exists to close. The replay contains `created -> updated(blocked) -> closed`
        for long-gone panes, and an unconditional registration gives such a pane no revision baseline
        — so its replayed blocked frame is accepted and the owner is paged about a pane that died
        hours ago, before the replayed `closed` is even processed.

        So a creation is only believed when the pane is in a CURRENTLY observed pane set.
        `live_pane_ids=None` means the caller could not check, and then the creation is refused:
        fail-closed, because a false page is the failure this whole class of bug produces.
        """
        if not (isinstance(pane, dict) and isinstance(pane.get("pane_id"), str)):
            return False
        pane_id = pane["pane_id"]
        if pane_id in self._rev:
            self._meta[pane_id] = pane
            return True
        if not live_pane_ids or pane_id not in live_pane_ids:
            return False
        self._rev.setdefault(pane_id, None)
        self._meta[pane_id] = pane
        return True

    def set_revision(self, pane_id: str, revision) -> None:
        """Install a known baseline for a pane we already track.

        Step 11 pass-10: the deferred sweep installed fresh METADATA via `register_pane`, but
        `safe_record()` deliberately omits `revision`, so the baseline stayed `None` and `accepts()`
        would then take ANY positive revision — including one older than the snapshot it had just
        read. That defeats the replay-reconciliation invariant and can poison `prior` before the real
        transition, so the baseline is installed explicitly alongside the metadata.
        """
        if pane_id in self._rev and isinstance(revision, int) and not isinstance(revision, bool):
            self._rev[pane_id] = revision

    def revision_of(self, pane_id: str):
        return self._rev.get(pane_id)

    def rollback(self, pane_id: str, revision) -> None:
        """Un-consume a revision after a FAILED send.

        Step 11 pass-2: `accepts()` advances the baseline before the notification is attempted, so a
        stable blocked pane that produced no further update was never retried — the block was lost
        even though the transition state had been preserved. Rolling the baseline back makes the same
        frame eligible again on redelivery.
        """
        if pane_id in self._rev:
            # `None` is a real baseline (a newly registered pane has no history), and restoring it
            # matters: Step 11 pass-3 found a new pane's first failed send left the consumed revision
            # in place, so redelivery of the same frame was dropped and the block was lost.
            self._rev[pane_id] = revision if isinstance(revision, int) else None

    def forget_pane(self, pane_id: str) -> None:
        self._rev.pop(pane_id, None)
        self._meta.pop(pane_id, None)

    def meta(self, pane_id: str) -> dict:
        return self._meta.get(pane_id, {})

    def accepts(self, event) -> bool:
        if not isinstance(event, PaneEvent) or event.pane_id not in self._rev:
            return False
        baseline = self._rev[event.pane_id]
        if event.revision is None:
            return False        # fail CLOSED: no revision means no way to tell replay from live
        if baseline is not None and event.revision <= baseline:
            return False
        self._rev[event.pane_id] = event.revision
        if isinstance(event.pane, dict):
            self._meta[event.pane_id] = event.pane
        return True


# ---------------------------------------------------------------------------
# debounce
# ---------------------------------------------------------------------------

class Debouncer:
    """One notification per pane per window, and the window is committed only on a CONFIRMED send.

    A review found the earlier version could lose a notification permanently: if the transport failed
    once, the debounce had already recorded the attempt, and with no further transition the owner
    never heard about it. So `allow` only reserves, and `commit` is called after a successful send;
    `release` puts it back on failure so the next event retries.
    """

    def __init__(self, window_s: float = DEFAULT_DEBOUNCE_S):
        self.window_s = float(window_s)
        self._last: dict[str, float] = {}

    def allow(self, pane_id: str, now: float) -> bool:
        last = self._last.get(pane_id)
        return last is None or (now - last) > self.window_s

    def commit(self, pane_id: str, now: float) -> None:
        self._last[pane_id] = now

    def release(self, pane_id: str) -> None:
        self._last.pop(pane_id, None)


# ---------------------------------------------------------------------------
# AC5 — provenance, not keyword hygiene
# ---------------------------------------------------------------------------

def _refuse_screen_derived_identity(pane, who) -> None:
    """Raise when the chosen identity is identical to a screen-derived field.

    Shared by the body builder AND the stall warning. Step 11 pass-2 found the warning rendered the
    identity directly and the heartbeat loop emitted it, so a label copied from `terminal_title`
    leaked into the watcher log even though the notification body correctly refused it. One helper,
    both call sites — the whole point of AC5 being a provenance boundary rather than a per-site check.
    """
    for field in CONTENT_FIELDS:
        value = pane.get(field)
        if isinstance(value, str) and value and value.strip() == who:
            raise WatchError(
                f"refusing to render pane {pane.get('pane_id')!r}: its identity is identical to its "
                f"{field!r}, which is screen-derived — AC5 permits labels only, because screen text "
                "can carry prompts and credentials (value withheld)")


def _identity_of(pane) -> str:
    """label -> name -> pane_id, selected from the pane record itself.

    Labels are optional and sparse (#613 §9 measured 3 of 8 panes carrying one), so the fallback is
    defined rather than left to render `None`. A title is NEVER a fallback, however tempting: it is
    screen-derived and can carry prompt text.
    """
    for key in ("label", "name"):
        value = pane.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return pane.get("pane_id") or "<unknown pane>"


def safe_record(pane) -> dict:
    """The allowlisted subset of a pane record — the ONLY shape allowed to leave this module.

    Step 11 pass-5 (AC5 failure, reproduced): a failed startup send stored the whole snapshot record
    in `pending`, and `_cmd_watch` prints the sweep as JSON — so `terminal_title` ("SECRET prompt
    text" in the reproduction) reached stdout and the logs. Bodies were never the only exit; a report
    is one too.
    """
    pane = pane if isinstance(pane, dict) else {}
    out = {k: pane[k] for k in
           ("pane_id", "label", "name", "workspace_id", "tab_id", "agent", "agent_status")
           if isinstance(pane.get(k), str)}
    # SANITIZE AT THE BOUNDARY, once (Step 11 pass-9). Retaining a `label` while stripping
    # `terminal_title` was the single root cause behind three separate reported leaks: any later
    # consumer — the pending retry, the deferred sweep's failure branch, the stall warning reading
    # cached metadata — had the offending label but no longer had the field to compare it against, so
    # the provenance check silently could not fire and the screen text went out. Dropping an
    # unverifiable identity HERE means no stored record can carry one, on any path, ever. The identity
    # then falls back to `name` or `pane_id`, which are not screen-derived.
    contents = {pane[f].strip() for f in CONTENT_FIELDS
                if isinstance(pane.get(f), str) and pane[f].strip()}
    for field in ("label", "name"):
        value = out.get(field)
        if isinstance(value, str) and value.strip() in contents:
            out.pop(field)
    return out


def body_for_pane(pane, *, status) -> str:
    """The ONLY public body builder. Selects its own fields from a pane record.

    Deliberately takes no caller-supplied display text. A review showed that a key allowlist alone
    is not a provenance boundary — `label=event.title` would sail through it — so the builder reads
    `label`/`name`/`pane_id` itself and never receives a caller's string. Contents present on the
    record (`terminal_title` and friends) are simply not selected.
    """
    if not isinstance(pane, dict):
        raise WatchError("body_for_pane needs a pane record, so the identity has a known provenance")
    if status not in AGENT_STATUSES:
        raise WatchError(f"refusing to build a body for an unknown status {status!r}")
    who = _identity_of(pane)
    _refuse_screen_derived_identity(pane, who)
    where = " ".join(p for p in (pane.get("workspace_id"), pane.get("tab_id"))
                     if isinstance(p, str) and p)
    agent = pane.get("agent")
    agent_part = f" [{agent}]" if isinstance(agent, str) and agent.strip() else ""
    tail = f" ({where})" if where else ""
    return f"herdr: {who}{agent_part} is {status} — waiting on you{tail}"


# ---------------------------------------------------------------------------
# AC3 — heartbeat and the stall warning
# ---------------------------------------------------------------------------

def heartbeat_due(*, last: float, now: float, interval_s: float = DEFAULT_HEARTBEAT_S) -> bool:
    """`>=`, not `>`. Step-8a finding: with a strict `>` a poll landing exactly ON the deadline was
    not due, so when the poll interval is clamped to exactly `heartbeat_s` the quiet heartbeat fired
    every SECOND interval — a watcher advertising a 300 s heartbeat that beat every 600 s."""
    return (now - last) >= float(interval_s)


def write_heartbeat(path: str, *, now: float, report=None, extra=None) -> None:
    """Persist liveness so an EXTERNAL observer can detect a dead watcher.

    This is the half a review correctly called missing: a predicate is not a timer, and a watcher
    blocked on a quiet socket cannot report its own death. The socket read below carries a timeout so
    this fires during silence, and something outside (sentinel, cron) must alert on staleness — a
    heartbeat nobody reads proves nothing.

    `extra` carries the input layer's own counters (#679: `polls`, `poll_failures`). `events` alone
    could never prove the input layer was alive — a healthy watcher on a quiet fleet reports a flat
    `events` too, which is exactly how a permanently silent subscription passed for healthy through
    a whole epic. `ts` and `pid` are not overridable: `ts` is what staleness alerting reads, so a
    caller must not be able to freeze it.
    """
    payload = {"ts": now, "pid": os.getpid()}
    if isinstance(report, dict):
        payload["events"] = report.get("events")
        payload["notified"] = len(report.get("notified") or [])
    if isinstance(extra, dict):
        payload.update({k: v for k, v in extra.items() if k not in ("ts", "pid")})
    target = os.path.expanduser(path)
    os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import atomic_write_lib  # pylint: disable=import-outside-toplevel
    atomic_write_lib.atomic_write_text(target, json.dumps(payload) + "\n")


def stall_warning(*, pane, since: float, now: float,
                  threshold_s: float = DEFAULT_STALL_S) -> str | None:
    """AC3's literal signal — a waiting process sitting `idle`/`unknown` with no recognised
    transition — and an honest statement of what it is not.

    **It does not detect a missed prompt.** A review made the point and it is correct: an ordinary
    finished pane can sit `idle` forever, while a detector miss can leave the last status as
    `working`, so `idle`/`unknown` plus elapsed time is neither necessary nor sufficient. What this
    is: a stale-pane warning, which is what AC3 asks for in as many words. The wording says "worth a
    look", never "something is wrong", because promising more would be the kind of claim this epic
    has already shipped four times and had caught four times.
    """
    pane = pane if isinstance(pane, dict) else {}
    if pane.get("agent_status") not in STALLABLE_STATUSES:
        return None
    if (now - since) <= float(threshold_s):
        return None
    who = _identity_of(pane)
    _refuse_screen_derived_identity(pane, who)     # same boundary as the body builder
    mins = int((now - since) // 60)
    return (f"herdr: {who} has been {pane.get('agent_status')} for ~{mins}m with no "
            "recognised transition. This is a stale-pane warning, NOT a missed-prompt detector — "
            "it cannot tell a missed approval from a pane that is simply idle. Worth a look.")


# ---------------------------------------------------------------------------
# effects, all injected
# ---------------------------------------------------------------------------

def _default_sender(body: str) -> int:
    """The EXISTING transport (F-1). Not reimplemented here."""
    script = os.path.expanduser("~/rawgentic/projects/sentinel/bin/notify.sh")
    proc = subprocess.run([script, body], capture_output=True, text=True, check=False,
                          shell=False, timeout=60)
    return proc.returncode


def sender_from_cmd(cmd: str):
    """Build a sender that pipes the body to `cmd` on stdin, for testing the watcher.

    Fail-OPEN as a SENDER (any exception becomes a non-zero rc, never a raise): `_send`
    already treats a non-zero rc as a failed send and records it, and a watcher that dies
    because the operator mistyped a test command is worse than one that reports the send
    failed.

    `shell=True` is deliberate. `cmd` is an operator-supplied argument on the same command
    line that launched the process — it carries the invoker's own privileges, so there is no
    boundary here to harden. It is NOT read from config or from any pane/wire data. The body
    goes on STDIN, never interpolated into the command, so pane text can never reach the
    shell.
    """
    def _send_via_cmd(body: str) -> int:
        try:
            proc = subprocess.run(cmd, shell=True, input=body, capture_output=True,
                                  text=True, check=False, timeout=60)
            return proc.returncode
        except Exception:  # noqa: BLE001 - a bad test command must not kill the watcher
            return 1
    return _send_via_cmd


def resolve_sender(sender_cmd):
    """The production transport unless an override was explicitly asked for."""
    return _default_sender if not sender_cmd else sender_from_cmd(sender_cmd)


def _pane_list(snapshot):
    """The pane list inside a snapshot document, or None when there isn't one.

    `None` and `[]` are DIFFERENT answers and both callers care: an empty list is a legitimate
    fleet with no panes, while a missing list means the document is unusable. Collapsing them is how
    a broken snapshot would read as "everything closed" — which on the poll path would emit a
    `pane_closed` for every pane alive.
    """
    node = snapshot.get("snapshot", snapshot) if isinstance(snapshot, dict) else {}
    panes = node.get("panes") if isinstance(node, dict) else None
    return panes if isinstance(panes, list) else None


def panes_by_id(snapshot) -> dict:
    """Snapshot pane records keyed by pane id; `{}` when the document is unusable or empty."""
    return {p["pane_id"]: p for p in (_pane_list(snapshot) or [])
            if isinstance(p, dict) and isinstance(p.get("pane_id"), str)}


def _int_or_none(value):
    """`bool` is excluded exactly as in `parse_event`: `True` is an int in Python, and a truthy
    sequence comparing as 1 would be a silent lie."""
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _revision_of(pane):
    """THE dedup key: `state_change_seq` when the record has one, else `revision`.

    **`revision` alone is not enough, and this was measured, not reasoned.** Driving a real Claude
    pane to a permission prompt on 2026-07-28 produced `working` at `revision` 3 and then `blocked`
    at `revision` 3 — the SAME number. herdr bumps `revision` on pane-record changes, not on every
    `agent_status` transition, so a revision-keyed reconciler refuses the very frame the watcher
    exists for (`accepts()` requires a STRICTLY newer key). The live watcher proved it: a genuine
    block, `polls` climbing, `poll_failures: 0`, and **zero notifications**.

    `state_change_seq` is the key #612's first design wanted and abandoned, because the
    `pane.updated` EVENT does not carry it. The SNAPSHOT does — on its `agents` records — so the
    poll path can have what the event path never could. Observed advancing 2148 -> 2151 across the
    same transition that left `revision` unmoved.

    Falling back to `revision` keeps the events path byte-identical (no event frame carries a
    sequence) and covers a pane with no agent at all, like the dashboard pane.
    """
    if not isinstance(pane, dict):
        return None
    seq = _int_or_none(pane.get("state_change_seq"))
    return seq if seq is not None else _int_or_none(pane.get("revision"))


def _has_sequence(pane) -> bool:
    """Does this record carry a `state_change_seq` — i.e. does the pane have an agent at all?

    The poll path diffs ONLY these. A pane with no agent cannot be an agent waiting on a human, and
    including it would mean comparing a `state_change_seq` (thousands) against a `revision` (single
    digits) the moment an agent attaches or detaches.
    """
    return isinstance(pane, dict) and _int_or_none(pane.get("state_change_seq")) is not None


def usable_pane_map(pane_list):
    """`{pane_id: record}` when EVERY entry is usable, else None.

    Step-11 adversarial finding: the old comprehension silently FILTERED unusable entries, so a
    corrupt response was still counted as a successful poll — and a filtered-out pane looks CLOSED to
    the diff, which is worse than blindness: it emits a false close and drops the pane from tracking
    while the heartbeat reports rising healthy polls. Duplicate ids are refused for the same reason
    (one silently wins and the other vanishes). An invalid snapshot is a FAILED poll instead.
    """
    if not isinstance(pane_list, list):
        return None
    out = {}
    for pane in pane_list:
        if not isinstance(pane, dict):
            return None
        pane_id = pane.get("pane_id")
        if not isinstance(pane_id, str) or not pane_id.strip():
            return None
        if pane_id in out:
            return None
        out[pane_id] = pane
    return out


def agent_view_usable(snapshot) -> bool:
    """Can this snapshot's `agents` node be trusted to say which panes have agents?

    Step-8a finding, reproduced by two reviewers independently: an absent or malformed `agents` node
    made `merge_agent_sequences` return unenriched panes, `_has_sequence` then excluded EVERY pane, and
    the poll still reset the failure counter, incremented `polls` and emitted its healthy ack. A real
    block would produce no events and no notification while the heartbeat reported rising polls, zero
    failures and a live input layer — #679's exact failure mode, rebuilt through a different door.

    So an unusable agent view is a FAILED poll, subject to the consecutive-failure ceiling, and the
    watcher dies loudly rather than watching nothing in apparent good health. Fail-closed, which is
    this module's documented contract.

    An EMPTY list is usable: it is herdr truthfully saying no pane is running an agent. That case is
    visible instead of guessed — `agents_tracked` rides in the heartbeat.
    """
    node = snapshot.get("snapshot", snapshot) if isinstance(snapshot, dict) else {}
    if not isinstance(node, dict):
        return False
    agents = node.get("agents")
    if not isinstance(agents, list):
        return False
    for agent in agents:
        # An entry naming a pane but carrying no usable key is the blindness case: skipping it
        # silently is what excludes a real agent pane from tracking altogether.
        if not isinstance(agent, dict):
            return False
        if isinstance(agent.get("pane_id"), str) and _int_or_none(agent.get("state_change_seq")) is None:
            return False
    return True


def merge_agent_sequences(snapshot):
    """Copy each agent's `state_change_seq` onto its pane record.

    The two live on DIFFERENT nodes of one snapshot: `snapshot.panes[]` carries `revision`,
    `snapshot.agents[]` carries `state_change_seq` (plus its own `pane_id`). A pane with no agent is
    left exactly as it was, so it keeps falling back to `revision`.
    """
    panes = _pane_list(snapshot)
    if panes is None:
        return snapshot
    node = snapshot.get("snapshot", snapshot) if isinstance(snapshot, dict) else {}
    agents = node.get("agents") if isinstance(node, dict) else None
    seqs = {a["pane_id"]: a["state_change_seq"] for a in (agents or [])
            if isinstance(a, dict) and isinstance(a.get("pane_id"), str)
            and _int_or_none(a.get("state_change_seq")) is not None}
    if not seqs:
        return snapshot
    merged = []
    for pane in panes:
        if isinstance(pane, dict) and pane.get("pane_id") in seqs:
            pane = dict(pane)
            pane["state_change_seq"] = seqs[pane["pane_id"]]
        merged.append(pane)
    out = dict(snapshot)
    if isinstance(out.get("snapshot"), dict):
        out["snapshot"] = dict(out["snapshot"])
        out["snapshot"]["panes"] = merged
    else:
        out["panes"] = merged
    return out


def live_pane_ids(runner=None) -> set:
    """The pane ids herdr currently knows. Used to tell a real creation from a replayed one.

    Returns an EMPTY set when it cannot be read, which makes `register_pane` refuse — fail-closed,
    because believing a replayed creation is what pages the owner about a dead pane.
    """
    try:
        snap = read_snapshot(runner)
    except Exception:  # pylint: disable=broad-except
        # Deliberately broad (Step 11 pass-3): this only ever answers "which panes exist right now",
        # and a `TimeoutExpired` or `FileNotFoundError` escaping here terminated the whole watcher
        # instead of returning the empty set the contract promises.
        return set()
    return set(panes_by_id(snap))


def current_pane_record(pane_id: str, runner=None, *, enrich: bool = False) -> dict | None:
    """One pane's CURRENT record from a fresh snapshot, or None.

    Used when a deferred registration finally lands: the queued creation record is stale (`unknown`,
    revision 0), so believing it silently dropped a pane that had blocked in the meantime.
    """
    try:
        snap = read_snapshot(runner)
    except Exception:  # pylint: disable=broad-except
        return None
    # `enrich` follows the SOURCE's key kind: the deferred sweep installs this record's key as a
    # reconciler baseline, so on the poll path it must be a `state_change_seq` and on the events path
    # a `revision`. Getting it wrong here means one pane's baseline is in the wrong domain forever.
    return panes_by_id(merge_agent_sequences(snap) if enrich else snap).get(pane_id)


def read_snapshot(runner=None) -> dict:
    runner = runner or (lambda argv: subprocess.run(argv, capture_output=True, text=True,
                                                    check=False, shell=False, timeout=30))
    proc = runner(["herdr", "api", "snapshot"])
    if getattr(proc, "returncode", 1) != 0:
        raise WatchError("herdr api snapshot failed — refusing to watch without a baseline to "
                         "reconcile the backlog replay against")
    try:
        doc = json.loads(getattr(proc, "stdout", "") or "")
    except ValueError as exc:
        raise WatchError(f"herdr api snapshot returned unparseable JSON: {exc}") from exc
    # Deliberately NOT enriched here. Enriching centrally looked tidy and broke the retained
    # `--source events` path: the reconciler's baseline became a `state_change_seq` (~2148) while real
    # event frames carry only `revision` (~5), so every event would have been silently dropped the day
    # herdr repairs the feed. The key kind belongs to the SOURCE, so the poll path enriches and the
    # events path does not (Step-4 pass-3 finding, reproduced).
    return doc.get("result", doc) if isinstance(doc, dict) else {}


def socket_lines(sock_path: str, request: dict, *, timeout_s: float = 30.0):
    """Connect, subscribe, then yield wire lines. Yields None on a read timeout so the caller can
    run its heartbeat during silence — the fix for "a predicate is not a timer"."""
    path = os.path.expanduser(sock_path)
    conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    conn.settimeout(timeout_s)
    try:
        conn.connect(path)
    except OSError as exc:
        raise WatchError(f"cannot connect to the herdr socket at {path}: {exc}") from exc
    conn.sendall((json.dumps(request) + "\n").encode())
    buf = b""
    try:
        while True:
            try:
                chunk = conn.recv(65536)
            except socket.timeout:
                yield None
                continue
            if not chunk:
                # Step 11 finding 4: this used to just return, and `_cmd_watch` then exited 0 —
                # so an unattended watcher SILENTLY stopped after a herdr restart. A watcher that
                # has stopped watching must be loud, because a dead watcher and a quiet fleet look
                # identical from outside.
                yield _EOF
                return
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if line.strip():
                    yield line.decode("utf-8", "replace")
    finally:
        conn.close()


def clamp_poll_interval(interval_s, heartbeat_s=DEFAULT_HEARTBEAT_S) -> float:
    """Strict parse, clamp, safe default, stderr warning — the house rule for a tunable.

    Step-4 finding: `argparse`'s `type=float` accepts `0`, `-5`, `nan` and `inf`. A non-positive or
    NaN interval skips the sleep entirely and hammers `herdr api snapshot` in a tight subprocess
    loop; an infinite one never polls again. And an interval LONGER than `heartbeat_s` holds the idle
    signal past its own deadline, so the heartbeat cadence the watcher advertises silently becomes
    the poll interval — the same class of quiet contradiction #679 is about.
    """
    ceiling = DEFAULT_HEARTBEAT_S
    try:
        candidate_ceiling = float(heartbeat_s)
        if math.isfinite(candidate_ceiling) and candidate_ceiling > 0:
            ceiling = candidate_ceiling
    except (TypeError, ValueError):
        pass
    try:
        value = float(interval_s)
    except (TypeError, ValueError):
        value = None
    if value is None or not math.isfinite(value) or value <= 0:
        print(f"pane-watch: poll interval {interval_s!r} is not a positive finite number — "
              f"using {DEFAULT_POLL_INTERVAL_S}s", file=sys.stderr)
        value = DEFAULT_POLL_INTERVAL_S
    if value > ceiling:
        print(f"pane-watch: poll interval {value}s exceeds the heartbeat interval {ceiling}s, which "
              f"would delay the heartbeat past its own deadline — clamping to {ceiling}s",
              file=sys.stderr)
        value = ceiling
    return value


def clamp_heartbeat(heartbeat_s) -> float:
    """Step-8a finding: `clamp_poll_interval` validated `heartbeat_s` only to pick a ceiling and then
    let the RAW value through to both heartbeat checks and `watch_stream`. NaN or infinity means
    `heartbeat_due` never fires, disabling idle heartbeats, pending-send retries AND stall warnings
    outright; zero or negative means the whole drain runs on every frame. The CLI accepted all four.
    """
    try:
        value = float(heartbeat_s)
    except (TypeError, ValueError):
        value = None
    if value is None or not math.isfinite(value) or value <= 0:
        print(f"pane-watch: heartbeat interval {heartbeat_s!r} is not a positive finite number — "
              f"using {DEFAULT_HEARTBEAT_S}s", file=sys.stderr)
        value = DEFAULT_HEARTBEAT_S
    return value


def poll_ack() -> str:
    """The poll source's "I am live" line. See `ACK_TYPES` for why it is not `subscription_started`."""
    return json.dumps({"result": {"type": "poll_started"}})


def _frame(kind: str, data: dict) -> str:
    """A wire-shaped line in exactly the shape `parse_event` already accepts.

    Deliberately routed through the SAME parser as a real socket frame rather than constructing
    `PaneEvent`s directly: the parser is where the status allowlist, the revision type-check and the
    pane_id validation live, and a source that skipped it would be a second, untested door into the
    brain.
    """
    return json.dumps({"event": kind, "data": data})


def poll_lines(*, snapshot, interval_s: float = DEFAULT_POLL_INTERVAL_S,
               heartbeat_s: float = DEFAULT_HEARTBEAT_S, runner=None,
               clock=time.time, sleep=time.sleep, stats=None, live_ids=None):
    """Yield wire lines by DIFFING `herdr api snapshot` reads — the drop-in for `socket_lines`.

    Why this is the default: `events.subscribe` is dead (#679 — see the module docstring). Same
    contract as the socket source, so `watch_stream` is untouched: JSON lines, `None` for "idle, run
    your heartbeat", `_EOF` for "the input layer has died, stop loudly".

    `snapshot` is the document the `Reconciler` was built from, passed in rather than re-read. That
    is load-bearing: a second read would open a window where a pane blocking between the startup
    sweep's snapshot and the first poll is seeded as already-known and never emits a frame at all.

    Emission order within a cycle is **closes, then creations, then updates**, and that is not
    cosmetic — `watch_stream` applies a `pane_closed` line BEFORE its heartbeat drain precisely so a
    due beat cannot page for a pane that is already gone. Consuming an update from the same cycle
    first would run the drain while the closed pane was still known.
    """
    interval_s = clamp_poll_interval(interval_s, heartbeat_s)
    stats = stats if isinstance(stats, dict) else {}
    stats.setdefault("polls", 0)
    stats.setdefault("poll_failures", 0)
    # The SEED is enriched here too, not just the polls. `_cmd_watch` already passes an enriched
    # document and `merge_agent_sequences` is idempotent, but a seed that skipped it would make every
    # agent-bearing pane look freshly attached on the first cycle — a spurious re-baseline for the
    # whole fleet.
    prev = panes_by_id(merge_agent_sequences(snapshot))
    # A per-pane HIGH-WATER MARK of the last integer key we have ever seen, kept independently of
    # `prev`. Step-4 finding: comparing only against the immediately previous snapshot let a single
    # unreadable revision bridge a regression (`24 -> None -> 1`), after which the reconciler still
    # held baseline 24 and silently refused every later frame — a subscribed, error-free, permanently
    # blind watcher, which is the exact failure #679 exists to end.
    high = {pid: _revision_of(rec) for pid, rec in prev.items()
            if _has_sequence(rec) and _revision_of(rec) is not None}
    failures = 0
    acked = False
    last_idle = clock()
    next_at = clock() + interval_s
    while True:
        delay = next_at - clock()
        if delay > 0:
            sleep(delay)
        # Schedule from the PREVIOUS tick, not from "now". `watch_stream` PULLS from this generator,
        # so a sender call (up to 60 s) or a heartbeat drain suspends sampling entirely, and
        # `sleep(interval)` would then drift by the consumer's work on every single cycle. A tick
        # already in the past is skipped, never queued up as a burst.
        next_at += interval_s
        now = clock()
        if next_at <= now:
            next_at = now + interval_s

        try:
            # Enriched HERE, on the poll path only: `state_change_seq` is the poll source's key, and
            # `read_snapshot` deliberately does not add it so the events path keeps its own.
            raw = read_snapshot(runner)
            # The WHOLE snapshot is validated before this poll can count as a success: an unusable
            # agent view (see `agent_view_usable`) or any malformed/duplicate pane entry (see
            # `usable_pane_map`) is a FAILED poll, never a quiet one.
            current = (usable_pane_map(_pane_list(merge_agent_sequences(raw)))
                       if agent_view_usable(raw) else None)
        except Exception:  # pylint: disable=broad-except
            # Deliberately broad, like `live_pane_ids`: this is the one place a herdr restart shows
            # up, and `TimeoutExpired`/`FileNotFoundError` escaping here would kill the watcher on a
            # transient failure the next poll would have survived.
            current = None
        if current is None:
            failures += 1
            stats["poll_failures"] += 1
            if failures > POLL_MAX_CONSECUTIVE_FAILURES:
                # Fail-loud, same as the socket path's EOF: `watch_stream` records that the watcher
                # has stopped watching and the CLI exits non-zero so a supervisor restarts us.
                yield _EOF
                return
            # Step-4 finding (gpt-5.6-sol, #4): a `None` per failed poll would silently move
            # `watch_stream`'s ENTIRE drain — heartbeat write, pending-send retries, stall warnings —
            # from `heartbeat_s` to the poll interval, and re-invoke an already-failing transport
            # every few seconds. Failures are counted internally and only surface as an idle signal
            # when the heartbeat is genuinely due; `poll_failures` is what an observer reads.
            if heartbeat_due(last=last_idle, now=clock(), interval_s=heartbeat_s):
                last_idle = clock()
                yield None
            continue
        failures = 0
        stats["polls"] += 1
        if live_ids is not None:
            # Step-8a finding, reproduced: `watch_stream` validates a creation against
            # `live_panes()`, and on the poll path that meant a SECOND `herdr api snapshot` read whose
            # failure returns an empty set — the creation was deferred, its accompanying blocked frame
            # dropped, and if the block cleared before the heartbeat retry the block was lost forever
            # with no notification, no error and no pending entry. A creation the poll synthesized is
            # already proven live BY the snapshot that produced it, so the set is shared from here and
            # the redundant read (and its 30 s timeout) disappears.
            live_ids.clear()
            live_ids.update(current)

        # ONLY PANES THAT HAVE AN AGENT ARE TRACKED, and that is what keeps the key single-domain.
        # Step-4 pass-3 finding, reproduced: `state_change_seq` counts in the thousands while
        # `revision` counts in single digits, so letting one fall back to the other meant a routine
        # agent DETACH (a Claude session exits, its pane stays open) read as 2148 -> 5 and killed the
        # whole watcher as a "source reset". A pane with no agent has nothing that can be waiting on a
        # human, so it is simply not diffed — no comparison across kinds ever happens.
        seq_now = {pid: rec for pid, rec in current.items() if _has_sequence(rec)}
        seq_before = {pid: rec for pid, rec in prev.items() if _has_sequence(rec)}
        # In the heartbeat, so an external observer can tell "herdr says no pane is running an agent"
        # (a truthful 0) from a watcher that has gone blind. `polls` alone cannot say which.
        stats["agents_tracked"] = len(seq_now)

        # CLOSES LEAD THE BATCH ABSOLUTELY, ahead of the regression guard and the ack. Step-4
        # finding: emitting the regression error before them broke that invariant — a regressing pane
        # A alongside a closing pane B, with the heartbeat due, drained on A's error line while B was
        # still known and retried B's pending send for a pane the same snapshot proves is gone.
        gone = sorted(set(prev) - set(current))
        # An agent ATTACHING to an already-known pane is a re-baseline, not an update: the reconciler
        # still holds that pane's `revision` baseline, and a `state_change_seq` frame compared against
        # it is exactly the cross-kind comparison this fix removes. A synthetic close makes
        # `forget_pane` drop the stale baseline so the following creation registers with none at all
        # and the update is accepted on its own terms.
        attached = sorted(pid for pid in set(seq_now) & set(prev) if pid not in seq_before)
        # An agent DETACHING is a lifecycle removal too, and a Step-11 finding corrected an earlier
        # comment here that claimed there was "no pending send to lose". There can be: a pane that
        # blocked, failed its send, and then had its agent exit stayed live in the reconciler with its
        # pending entry intact, so the next heartbeat drain could page the owner about a block whose
        # agent is already gone. The close clears reconciliation, the pending send, `prior` and the
        # stall entry in one existing code path.
        detached = sorted(pid for pid in set(seq_before) & set(current) if pid not in seq_now)
        for pane_id in gone + detached + attached:
            yield _frame(EVENT_PANE_CLOSED, {"pane_id": pane_id})
        for pane_id in detached + attached:
            high.pop(pane_id, None)

        # The hole that tolerating failures opened. A herdr restart used to kill the socket outright
        # (EOF -> loud stop -> supervisor restart -> fresh snapshot). A poll loop SURVIVES a restart,
        # and if the source's counters reset it would hold baselines from the dead process:
        # `Reconciler.accepts` refuses every frame at or below a stale baseline, so those panes go
        # silently blind. Compared against the HIGH-WATER mark rather than `prev`, because a single
        # unreadable key would otherwise bridge the regression (`24 -> None -> 1`) and the guard
        # would never fire while the reconciler stayed permanently blind. Only ever sequence-vs-
        # sequence, because only agent-bearing panes reach here.
        regressed = sorted(pane_id for pane_id in set(seq_now) & set(high)
                           if _revision_of(seq_now[pane_id]) is not None
                           and _revision_of(seq_now[pane_id]) < high[pane_id])
        if regressed:
            yield json.dumps({"error": {
                "code": "pane_revision_regressed",
                "message": ("pane state key went backwards for " + ", ".join(regressed) +
                            " — the pane source was reset (herdr restart or a reused pane id), so "
                            "every baseline is stale and blocked frames would be silently "
                            "refused; stopping so a supervisor restarts us with a fresh snapshot")}})
            yield _EOF
            return
        for pane_id, record in seq_now.items():
            key = _revision_of(record)
            if key is not None and key > high.get(pane_id, key - 1):
                high[pane_id] = key

        frames = []
        if not acked:
            # Step-4 finding (gpt-5.6-sol, #6): the ack used to be self-issued BEFORE the first
            # poll, so a source that could never read a snapshot still reported a live input layer
            # and the CLI still exited 0. The ack now means what `subscription_started` means — one
            # real read has happened.
            #
            # It sits AFTER this batch's closes, not before them, and a test earned that ordering:
            # `watch_stream` runs its whole drain on ANY line once the heartbeat is due — including
            # a non-event line like this ack — and a drain that runs before a close is applied
            # retries a pending send for a pane the same snapshot proves is gone. Closes lead the
            # batch absolutely; everything else, this line included, follows them.
            acked = True
            frames.append(poll_ack())
        # A brand-new pane, and a pane whose agent just attached (re-baselined by the synthetic close
        # above), both need BOTH frames: `watch_stream` learns a pane only from a creation, while the
        # status that might page the owner rides on the update.
        for pane_id in sorted((set(seq_now) - set(prev)) | set(attached)):
            frames.append(_frame(EVENT_PANE_CREATED, {"pane": seq_now[pane_id]}))
            frames.append(_frame(EVENT_PANE_UPDATED, {"pane": seq_now[pane_id]}))
        for pane_id in sorted(set(seq_now) & set(seq_before)):
            pane, before = seq_now[pane_id], seq_before[pane_id]
            # Keyed on `_revision_of`, so a `state_change_seq` bump counts as a change even when
            # `revision` sat still — which is what a real block does.
            if ((_revision_of(pane), pane.get("agent_status"))
                    != (_revision_of(before), before.get("agent_status"))):
                frames.append(_frame(EVENT_PANE_UPDATED, {"pane": pane}))
        closed_any = bool(gone or detached or attached)
        prev = current

        if frames:
            last_idle = clock()
            for frame in frames:
                yield frame
        elif closed_any:
            last_idle = clock()          # closes were already yielded above; this cycle was not idle
        elif heartbeat_due(last=last_idle, now=clock(), interval_s=heartbeat_s):
            # Paced by `heartbeat_s`, NOT by the poll interval: `watch_stream` runs its whole drain
            # (heartbeat write, pending-send retries, stall warnings) on any `None`, so one per poll
            # would silently move that cadence from 300 s to 5 s.
            last_idle = clock()
            yield None


def startup_sweep(reconciler, *, sender, now, debouncer=None, emit=print) -> dict:
    """Notify for panes that are ALREADY `blocked` when the watcher starts.

    Step 11 finding 2: without this, the advertised "a first observation of blocked counts" missed
    precisely the case that matters most — an agent that was already waiting before the watcher
    existed. The snapshot records it as `blocked`, the replayed frame (if any) is rejected as equal to
    the baseline, and the transition map starts empty, so nobody was ever told.

    This does mean a watcher restart re-notifies a still-blocked pane. That is the correct trade: an
    agent still waiting after a restart still needs a human, and a duplicate page is a far smaller
    harm than silence.
    """
    debouncer = debouncer or Debouncer()
    out = {"notified": [], "send_failures": [], "pending": {}}
    for pane_id in reconciler.known_panes():
        pane = reconciler.meta(pane_id)
        if pane.get("agent_status") != BLOCKED:
            continue
        if not debouncer.allow(pane_id, now):
            continue
        try:
            rc = int(sender(body_for_pane(pane, status=BLOCKED)))
        except WatchError as exc:
            # Same as the stream path: a refusal is a failed send, recorded, never an abort.
            out.setdefault("errors", []).append(
                f"AC5 refusal for a blocked pane {pane_id}: {exc}")
            emit(f"pane-watch: startup AC5 refusal — {exc}")
            rc = 1
        except Exception as exc:  # pylint: disable=broad-except
            # Step 11 pass-5: this call was unguarded while the guard lived only in `watch_stream`,
            # and the sweep runs FIRST — so the real sender's `TimeoutExpired` killed the watcher
            # before any pending map was returned.
            emit(f"pane-watch: startup sender raised {type(exc).__name__} — treating as failed")
            rc = 1
        if rc == 0:
            debouncer.commit(pane_id, now)
            out["notified"].append(pane_id)
            emit(f"pane-watch: startup sweep notified for {pane_id} (already blocked)")
        else:
            debouncer.release(pane_id)
            out["send_failures"].append(pane_id)
            # Step 11 pass-3 BLOCKER: releasing the window scheduled no retry, and a stable blocked
            # pane emits nothing newer — so the owner was never told. Hand it to the stream as
            # PENDING so the heartbeat retries it.
            out["pending"][pane_id] = ({"pane_id": pane_id} if out.get("errors") and
                                       any(pane_id in e for e in out["errors"])
                                       else safe_record(pane))
            emit(f"pane-watch: startup sweep SEND FAILED for {pane_id} (rc={rc}) — pending retry")
    return out


def watch_stream(lines, *, reconciler, sender, clock=time.time, debouncer=None,
                 heartbeat_s: float = DEFAULT_HEARTBEAT_S, stall_s: float = DEFAULT_STALL_S,
                 emit=print, beat=None, live_panes=None, already_notified=None,
                 pending=None, current_pane=None, pending_errors=None) -> dict:
    """Drive a stream of wire lines. All I/O is via `sender`, `clock`, `emit` and `beat`.

    A `None` line is a read timeout, not an event: it exists so the heartbeat fires during silence.
    Never calls sys.exit — the caller decides the exit code.
    """
    refused_identity: set = set()

    def _send(pane_rec) -> int:
        """Every sender call goes through here. Step 11 pass-4: the calls were unguarded and `main`
        catches only `WatchError`, so the real sender's `TimeoutExpired` killed the watcher and the
        notification bypassed `pending_sends` entirely — the one outcome this mechanism exists to
        prevent. An exception is a failed send, nothing more."""
        try:
            return int(sender(body_for_pane(pane_rec, status=BLOCKED)))
        except WatchError as exc:
            # Step 11 pass-7: re-raising bypassed report finalization, so a pane whose label equals
            # its `terminal_title` ended with NO notification and NO structured evidence — the
            # refusal was right, but it destroyed the accounting that proves something was owed.
            #
            # Step 11 pass-8 (CRITICAL, and this was MY leak): the pending record kept the offending
            # `label` while `safe_record` stripped `terminal_title`, so the retry could no longer
            # repeat the provenance comparison and DELIVERED the screen text. A refused pane is
            # therefore re-queued by pane_id ALONE — its identity falls back to the pane id, which is
            # never screen-derived.
            report["errors"].append(f"AC5 refusal for a blocked pane: {exc}")
            emit(f"pane-watch: AC5 refusal — cannot safely name this pane: {exc}")
            refused_identity.add(pane_rec.get("pane_id"))
            return 1
        except Exception as exc:  # pylint: disable=broad-except
            emit(f"pane-watch: sender raised {type(exc).__name__} — treating as a failed send")
            return 1

    debouncer = debouncer or Debouncer()
    # Seeded from the startup sweep. Step 11 pass-3: the stream started with an empty `prior`, so a
    # pane the sweep had ALREADY paged looked like a fresh transition once the debounce window
    # expired, and the owner was paged twice for one continuous block.
    prior: dict[str, str] = {p: BLOCKED for p in (already_notified or ())}
    # The ONE delivery-retry mechanism, replacing three per-case patches. A failed send lands here
    # and the heartbeat retries it, so delivery no longer depends on another frame arriving —
    # which was the hole pass-3 found on both the sweep and the new-pane paths.
    pending_sends: dict[str, dict] = dict(pending or {})
    sweep_errors: list = list((pending_errors or []))
    # A creation refused because the live-pane set could not be read. Retried the same way, so a
    # transient snapshot failure no longer blinds the watcher to that pane forever.
    pending_registrations: dict[str, dict] = {}
    # Step 11 finding 7: `stall_warning` shipped as dead code — nothing tracked `since` or called it,
    # so an idle/unknown pane could sit stale forever without the warning AC3 asks for.
    # Seeded from the snapshot so a pane ALREADY idle/unknown at startup can warn. Step 11 pass-2:
    # populating this only from accepted stream updates meant such a pane never warned unless it
    # later changed status — i.e. the stalest panes were the ones the warning could not see.
    since: dict[str, float] = {}
    report = {"subscribed": False, "events": 0, "notified": [], "suppressed": 0,
              "dropped": 0, "errors": [], "heartbeats": 0, "send_failures": 0, "stalls": [],
              "pending_at_exit": []}
    report["errors"].extend(sweep_errors)     # pass-8: startup refusals were stranded in the sweep
    last_beat = clock()
    for pane_id in reconciler.known_panes():
        since.setdefault(pane_id, last_beat)
    for line in lines:
        now = clock()
        if line is _EOF:
            # Source-neutral wording (#679): the same signal now arrives from a closed socket OR a
            # poll source that has given up, and a message naming only the socket would misdescribe
            # half the ways a watcher can die.
            report["errors"].append("input layer ended — the watcher has stopped watching")
            emit("pane-watch: INPUT LAYER ENDED — stopping loudly so a supervisor restarts us")
            break
        # Step 11 pass-5: a LIFECYCLE line is applied BEFORE the heartbeat drain. With the heartbeat
        # due on a `pane_closed` line, the drain used to run first and page for a pane that this very
        # line says is gone — the "known pane" check could not help, because the pane was still known.
        # A SOURCE RESET is terminal and must be handled BEFORE the heartbeat drain. Step-11
        # adversarial finding: the regression error was an ordinary line, so with the heartbeat due
        # the drain ran on it and could retry pending sends for still-present panes immediately after
        # the source told us every baseline is stale — paging the owner about the PREVIOUS session on
        # the way out. Every baseline being stale means no pending notification can still be trusted.
        early_err = parse_error(line) if isinstance(line, str) else None
        if early_err is not None and ERROR_SOURCE_RESET in early_err:
            report["errors"].append(early_err)
            pending_sends.clear()
            pending_registrations.clear()
            prior.clear()
            since.clear()
            report["stalls"] = []
            for known in list(reconciler.known_panes()):
                reconciler.forget_pane(known)
            report["errors"].append(
                "input layer reported a source reset — the watcher has stopped watching")
            emit("pane-watch: SOURCE RESET — dropped every pending notification as untrustworthy and "
                 "stopping loudly so a supervisor restarts us with a fresh snapshot")
            break
        early = parse_event(line) if isinstance(line, str) else None
        if early is not None and early.kind == EVENT_PANE_CLOSED:
            reconciler.forget_pane(early.pane_id)
            prior.pop(early.pane_id, None)
            since.pop(early.pane_id, None)
            pending_sends.pop(early.pane_id, None)
            pending_registrations.pop(early.pane_id, None)
            report["stalls"] = [x for x in report["stalls"] if x != early.pane_id]
            report["events"] += 1
            continue
        if line is None or heartbeat_due(last=last_beat, now=now, interval_s=heartbeat_s):
            last_beat = now
            report["heartbeats"] += 1
            if beat is not None:
                beat(now, report)
            for pane_id, pane_rec in list(pending_registrations.items()):
                ids = live_panes() if callable(live_panes) else None
                if reconciler.register_pane(pane_rec, live_pane_ids=ids):
                    since.setdefault(pane_id, now)
                    # Step 11 pass-4 BLOCKER: registering was not enough. A `blocked` update that
                    # arrived while the pane was still unknown was dropped, and the creation record
                    # is stale (`unknown`, revision 0), so nothing ever notified. Sweep the pane's
                    # CURRENT state instead of trusting the record we queued.
                    current = current_pane(pane_id) if callable(current_pane) else None
                    if current is None:
                        # Step 11 pass-5 BLOCKER: the registration used to be dropped the moment
                        # `register_pane` succeeded, BEFORE this second snapshot. If that snapshot
                        # then failed, the pane was registered but never swept — and a stable block
                        # emits nothing further, so it ended with no notification AND no pending
                        # entry. The registration stays pending until its sweep resolves.
                        emit(f"pane-watch: {pane_id} registered but its current state is unreadable "
                             "— keeping it pending for the next heartbeat")
                        continue
                    pending_registrations.pop(pane_id, None)
                    # Install the FRESH record (pass-9): the reconciler otherwise holds the scrubbed
                    # creation metadata, and `stall_warning` reads from there.
                    reconciler.register_pane(safe_record(current), live_pane_ids={pane_id})
                    # `_revision_of`, not `current["revision"]`: since #679 the baseline key is
                    # `state_change_seq` when present, and installing a raw revision here would
                    # compare two different numbers forever after.
                    reconciler.set_revision(pane_id, _revision_of(current))
                    emit(f"pane-watch: registered {pane_id} on retry")
                    if current.get("agent_status") == BLOCKED and prior.get(pane_id) != BLOCKED:
                        if debouncer.allow(pane_id, now):
                            rc = _send(current)
                            if rc == 0:
                                debouncer.commit(pane_id, now)
                                prior[pane_id] = BLOCKED
                                # Step 11 pass-6: an older failed stream send for this same pane was
                                # left in the queue, so the next drain paged the same continuous
                                # block a second time.
                                pending_sends.pop(pane_id, None)
                                report["notified"].append({"pane_id": pane_id, "rc": rc,
                                                           "deferred_sweep": True})
                                emit(f"pane-watch: deferred sweep notified for {pane_id}")
                            else:
                                debouncer.release(pane_id)
                                pending_sends[pane_id] = safe_record(current)
            for pane_id, pane_rec in list(pending_sends.items()):
                if pane_id not in reconciler.known_panes():
                    # Step 11 pass-4: with the heartbeat due on the close line, the drain ran before
                    # the close was processed and paged for an already-closed pane.
                    pending_sends.pop(pane_id, None)
                    emit(f"pane-watch: dropping pending send for {pane_id} — pane is gone")
                    continue
                if not debouncer.allow(pane_id, now):
                    continue
                rc = _send(pane_rec)
                if rc == 0:
                    debouncer.commit(pane_id, now)
                    prior[pane_id] = BLOCKED
                    pending_sends.pop(pane_id, None)
                    report["notified"].append({"pane_id": pane_id, "rc": rc, "retry": True})
                    emit(f"pane-watch: retry succeeded for {pane_id}")
                else:
                    debouncer.release(pane_id)
                    emit(f"pane-watch: retry failed for {pane_id} — still pending")
            for pane_id, first_seen in list(since.items()):
                try:
                    warning = stall_warning(pane=reconciler.meta(pane_id), since=first_seen,
                                            now=now, threshold_s=stall_s)
                except WatchError as exc:
                    # Step 11 pass-8: an unhandled refusal here aborted the whole loop, so a DIFFERENT
                    # pane's stale-label problem destroyed the accounting for a genuinely blocked one.
                    report["errors"].append(f"AC5 refusal while warning about {pane_id}: {exc}")
                    continue
                if warning is not None and pane_id not in report["stalls"]:
                    report["stalls"].append(pane_id)
                    emit(warning)
            if line is None:
                continue
        ack = ack_kind(line)
        if ack is not None:
            report["subscribed"] = True          # retained: existing consumers and tests read it
            # Step-11 finding: the log said `subscription_started` whatever the source, which
            # contradicts this module's own point that a poll loop cannot honestly claim a
            # subscription — and it leaked the old abstraction into operator-visible output.
            report["input_ack"] = ack
            emit(f"pane-watch: {ack}")
            continue
        err = parse_error(line)
        if err is not None:
            report["errors"].append(err)
            emit(f"pane-watch: herdr error: {err}")
            continue
        event = parse_event(line)
        if event is None:
            report["dropped"] += 1
            continue
        report["events"] += 1

        if event.kind == EVENT_PANE_CREATED:
            ids = live_panes() if callable(live_panes) else None
            if reconciler.register_pane(event.pane or {"pane_id": event.pane_id},
                                       live_pane_ids=ids):
                since.setdefault(event.pane_id, now)
            else:
                report["dropped"] += 1
                if ids:
                    emit(f"pane-watch: ignoring creation of {event.pane_id} — not in the current "
                         "pane set, so this is the backlog replay")
                else:
                    # The live set could not be READ, which is not the same as "the pane is dead".
                    # Refusing now is right (fail-closed against a false page) but forgetting would
                    # blind us to a real pane forever, so it is retried.
                    pending_registrations[event.pane_id] = safe_record(
                        event.pane or {"pane_id": event.pane_id})
                    emit(f"pane-watch: creation of {event.pane_id} deferred — live pane set "
                         "unreadable, will retry")
            continue
        if event.kind == EVENT_PANE_CLOSED:
            reconciler.forget_pane(event.pane_id)
            prior.pop(event.pane_id, None)
            # Step 11 pass-2: neither of these was cleaned, leaving unbounded stale bookkeeping and
            # a `stalls` entry for a pane that no longer exists.
            since.pop(event.pane_id, None)
            pending_sends.pop(event.pane_id, None)
            pending_registrations.pop(event.pane_id, None)
            report["stalls"] = [p for p in report["stalls"] if p != event.pane_id]
            continue
        # Step 11 finding 6: a frame whose status is absent or unrecognised must not advance the
        # revision baseline. Advancing it meant a CORRECTED frame at the same revision was then
        # dropped, so a real block could be lost behind one unparseable status.
        if event.status is None:
            report["dropped"] += 1
            continue
        was_revision = reconciler.revision_of(event.pane_id)
        if not reconciler.accepts(event):
            report["dropped"] += 1
            continue

        was = prior.get(event.pane_id)
        if was != event.status:
            since[event.pane_id] = now
            report["stalls"] = [p for p in report["stalls"] if p != event.pane_id]
        if not is_blocked_transition(was, event.status):
            prior[event.pane_id] = event.status
            continue
        if not debouncer.allow(event.pane_id, now):
            # Record it: Step 11 pass-2 found a suppressed transition left `prior` unset, so a later
            # frame after the window expired looked like a NEW block and paged again even though the
            # pane had never left `blocked`.
            prior[event.pane_id] = event.status
            report["suppressed"] += 1
            continue
        rc = _send(event.pane or reconciler.meta(event.pane_id))
        if rc == 0:
            debouncer.commit(event.pane_id, now)
            prior[event.pane_id] = event.status
            # Step 11 pass-4: a newer frame succeeding did not clear an older pending entry, so the
            # next heartbeat sent the same block a second time.
            pending_sends.pop(event.pane_id, None)
            report["notified"].append({"pane_id": event.pane_id, "rc": rc})
            emit(f"pane-watch: notified for {event.pane_id}")
        else:
            # Step 11 finding 3: releasing the debounce was not enough. `prior` used to advance to
            # `blocked` BEFORE the send, so after a failure the next blocked frame was no longer a
            # transition and the sender was never retried — the notification was still lost, just
            # more subtly. Neither the window nor the transition state is consumed by a failed send.
            debouncer.release(event.pane_id)
            reconciler.rollback(event.pane_id, was_revision)
            pending_sends[event.pane_id] = (
                {"pane_id": event.pane_id} if event.pane_id in refused_identity
                else safe_record(event.pane or reconciler.meta(event.pane_id)))
            report["send_failures"] += 1
            emit(f"pane-watch: SEND FAILED for {event.pane_id} (rc={rc}) — pending retry")
    # Step 11 pass-6: this reported only `pending_sends`, so an unresolved DEFERRED REGISTRATION left
    # no structured evidence at all — a genuinely blocked pane could exit with notified=[] and
    # pending_at_exit=[]. Both queues are reported now, and separately, because they mean different
    # things: one is "we could not deliver", the other "we could not even learn about this pane".
    report["pending_at_exit"] = sorted(pending_sends)
    report["pending_registrations_at_exit"] = sorted(pending_registrations)
    if report["pending_registrations_at_exit"]:
        report["errors"].append(
            "unresolved deferred registrations at exit: "
            + ", ".join(report["pending_registrations_at_exit"])
            + " — these panes were never learned, so a block on them was never checked")
    return report


# ---------------------------------------------------------------------------
# thin CLI
# ---------------------------------------------------------------------------

def input_plan(args) -> dict:
    """What `--dry-run` prints: the selected input layer, without touching it."""
    if getattr(args, "source", SOURCE_POLL) == SOURCE_EVENTS:
        return subscribe_request()
    return {"source": SOURCE_POLL, "interval_s": float(args.poll_interval_s),
            "command": ["herdr", "api", "snapshot"]}


def _cmd_watch(args) -> int:
    source = getattr(args, "source", SOURCE_POLL)
    if args.dry_run:
        print(json.dumps(input_plan(args), indent=2))
        return 0
    # Normalized ONCE here and passed everywhere, rather than each consumer re-deriving it.
    heartbeat_s = clamp_heartbeat(args.heartbeat_s)
    sender = resolve_sender(getattr(args, "sender_cmd", None))
    # ONE snapshot read feeds both the reconciler and the poll baseline. A second read would open a
    # window in which a pane that blocked in between is seeded as already-known and never notified.
    snapshot = read_snapshot()
    if source == SOURCE_POLL:
        # The key kind belongs to the source: the poll path keys on `state_change_seq`, the events
        # path on `revision`, and the reconciler's baseline must match whichever will feed it.
        snapshot = merge_agent_sequences(snapshot)
    reconciler = Reconciler(snapshot)
    debouncer = Debouncer(args.debounce_s)
    sweep = startup_sweep(reconciler, sender=sender, now=time.time(),
                          debouncer=debouncer)
    stats: dict = {}
    # Shared with `watch_stream` so a poll-synthesized creation is validated against the very snapshot
    # that produced it, instead of costing a second `herdr api snapshot` read whose failure silently
    # deferred the registration and dropped the blocked frame riding with it.
    poll_live_ids: set = set()
    if source == SOURCE_EVENTS:
        lines = socket_lines(args.socket, subscribe_request(), timeout_s=heartbeat_s)
    else:
        lines = poll_lines(snapshot=snapshot, interval_s=args.poll_interval_s,
                          heartbeat_s=heartbeat_s, stats=stats, live_ids=poll_live_ids)
    report = watch_stream(
        lines, reconciler=reconciler, sender=sender,
        debouncer=debouncer, heartbeat_s=heartbeat_s, stall_s=args.stall_s,
        beat=lambda now, rep: write_heartbeat(args.heartbeat_path, now=now, report=rep,
                                              extra=dict(stats)),
        live_panes=(live_pane_ids if source == SOURCE_EVENTS else lambda: set(poll_live_ids)),
        already_notified=sweep["notified"],
        pending=sweep["pending"],
        current_pane=lambda pid: current_pane_record(pid, enrich=(source == SOURCE_POLL)),
        pending_errors=sweep.get("errors"))
    report["startup_sweep"] = sweep
    # Which input layer actually ran, recorded rather than inferred — the #679 UAT could not tell a
    # dead feed from a quiet fleet, and a report that does not name its source repeats that.
    report["source"] = source
    report["poll_stats"] = dict(stats) if source == SOURCE_POLL else None
    print(json.dumps(report, indent=2))
    return 0 if report["subscribed"] and not report["errors"] else 4


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="watch herdr panes for blocked transitions and notify the owner (#612)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_watch = sub.add_parser("watch", help="watch panes and notify on blocked transitions")
    p_watch.add_argument("--source", choices=(SOURCE_POLL, SOURCE_EVENTS), default=SOURCE_POLL,
                         help="input layer. 'poll' (default) diffs `herdr api snapshot`; 'events' "
                              "uses events.subscribe, which on herdr 0.7.5 delivers a backlog "
                              "burst and then nothing, ever (#679) — retained for the day that is "
                              "fixed.")
    p_watch.add_argument("--poll-interval-s", type=float, default=DEFAULT_POLL_INTERVAL_S,
                         help="seconds between pane-state polls (--source poll)")
    p_watch.add_argument("--socket", default=DEFAULT_SOCKET)
    p_watch.add_argument("--debounce-s", type=float, default=DEFAULT_DEBOUNCE_S)
    p_watch.add_argument("--heartbeat-s", type=float, default=DEFAULT_HEARTBEAT_S)
    p_watch.add_argument("--heartbeat-path", default=DEFAULT_HEARTBEAT_PATH)
    p_watch.add_argument("--stall-s", type=float, default=DEFAULT_STALL_S)
    p_watch.add_argument("--dry-run", action="store_true",
                         help="print the subscribe request and exit; sends nothing")
    p_watch.add_argument("--sender-cmd", default=None,
                         help="send notifications by piping the body to this shell command "
                              "instead of notify.sh (e.g. 'cat >> /tmp/uat.log'). For "
                              "acceptance-testing the watcher without paging the owner.")
    args = parser.parse_args(argv)
    try:
        if args.cmd == "watch":
            return _cmd_watch(args)
    except WatchError as exc:
        print(f"pane_watch_lib: {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    sys.exit(main())
