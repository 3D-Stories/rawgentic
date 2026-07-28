#!/usr/bin/env python3
"""Blocked-pane watcher: herdr `events.subscribe` -> notify-owner (#612, epic #667).

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


def is_subscription_ack(line) -> bool:
    """`{"result":{"type":"subscription_started"}}` — the socket is LIVE, not merely connected. A
    connected-but-unsubscribed socket reports nothing and looks exactly like a quiet fleet."""
    doc = _load(line)
    result = doc.get("result") if doc else None
    return isinstance(result, dict) and result.get("type") == "subscription_started"


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
        rev = pane.get("revision")
        return PaneEvent(kind=kind, pane_id=pane["pane_id"],
                         status=status if status in AGENT_STATUSES else None,
                         revision=rev if isinstance(rev, int) and not isinstance(rev, bool) else None,
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
                rev = pane.get("revision")
                self._rev[pane["pane_id"]] = rev if isinstance(rev, int) else None
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
    return {k: pane[k] for k in
            ("pane_id", "label", "name", "workspace_id", "tab_id", "agent", "agent_status")
            if isinstance(pane.get(k), str)}


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
    return (now - last) > float(interval_s)


def write_heartbeat(path: str, *, now: float, report=None) -> None:
    """Persist liveness so an EXTERNAL observer can detect a dead watcher.

    This is the half a review correctly called missing: a predicate is not a timer, and a watcher
    blocked on a quiet socket cannot report its own death. The socket read below carries a timeout so
    this fires during silence, and something outside (sentinel, cron) must alert on staleness — a
    heartbeat nobody reads proves nothing.
    """
    payload = {"ts": now, "pid": os.getpid()}
    if isinstance(report, dict):
        payload["events"] = report.get("events")
        payload["notified"] = len(report.get("notified") or [])
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
    node = snap.get("snapshot", snap) if isinstance(snap, dict) else {}
    panes = node.get("panes") if isinstance(node, dict) else None
    if not isinstance(panes, list):
        return set()
    return {p["pane_id"] for p in panes
            if isinstance(p, dict) and isinstance(p.get("pane_id"), str)}


def current_pane_record(pane_id: str, runner=None) -> dict | None:
    """One pane's CURRENT record from a fresh snapshot, or None.

    Used when a deferred registration finally lands: the queued creation record is stale (`unknown`,
    revision 0), so believing it silently dropped a pane that had blocked in the meantime.
    """
    try:
        snap = read_snapshot(runner)
    except Exception:  # pylint: disable=broad-except
        return None
    node = snap.get("snapshot", snap) if isinstance(snap, dict) else {}
    panes = node.get("panes") if isinstance(node, dict) else None
    if not isinstance(panes, list):
        return None
    for pane in panes:
        if isinstance(pane, dict) and pane.get("pane_id") == pane_id:
            return pane
    return None


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
        except WatchError:
            raise
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
            out["pending"][pane_id] = safe_record(pane)
            emit(f"pane-watch: startup sweep SEND FAILED for {pane_id} (rc={rc}) — pending retry")
    return out


def watch_stream(lines, *, reconciler, sender, clock=time.time, debouncer=None,
                 heartbeat_s: float = DEFAULT_HEARTBEAT_S, stall_s: float = DEFAULT_STALL_S,
                 emit=print, beat=None, live_panes=None, already_notified=None,
                 pending=None, current_pane=None) -> dict:
    """Drive a stream of wire lines. All I/O is via `sender`, `clock`, `emit` and `beat`.

    A `None` line is a read timeout, not an event: it exists so the heartbeat fires during silence.
    Never calls sys.exit — the caller decides the exit code.
    """
    def _send(pane_rec) -> int:
        """Every sender call goes through here. Step 11 pass-4: the calls were unguarded and `main`
        catches only `WatchError`, so the real sender's `TimeoutExpired` killed the watcher and the
        notification bypassed `pending_sends` entirely — the one outcome this mechanism exists to
        prevent. An exception is a failed send, nothing more."""
        try:
            return int(sender(body_for_pane(pane_rec, status=BLOCKED)))
        except WatchError:
            raise
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
    last_beat = clock()
    for pane_id in reconciler.known_panes():
        since.setdefault(pane_id, last_beat)
    for line in lines:
        now = clock()
        if line is _EOF:
            report["errors"].append("socket closed by herdr — the watcher has stopped watching")
            emit("pane-watch: SOCKET CLOSED — stopping loudly so a supervisor restarts us")
            break
        # Step 11 pass-5: a LIFECYCLE line is applied BEFORE the heartbeat drain. With the heartbeat
        # due on a `pane_closed` line, the drain used to run first and page for a pane that this very
        # line says is gone — the "known pane" check could not help, because the pane was still known.
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
                warning = stall_warning(pane=reconciler.meta(pane_id), since=first_seen, now=now,
                                        threshold_s=stall_s)
                if warning is not None and pane_id not in report["stalls"]:
                    report["stalls"].append(pane_id)
                    emit(warning)
            if line is None:
                continue
        if is_subscription_ack(line):
            report["subscribed"] = True
            emit("pane-watch: subscription_started")
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
            pending_sends[event.pane_id] = safe_record(
                event.pane or reconciler.meta(event.pane_id))
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

def _cmd_watch(args) -> int:
    request = subscribe_request()
    if args.dry_run:
        print(json.dumps(request, indent=2))
        return 0
    reconciler = Reconciler(read_snapshot())
    debouncer = Debouncer(args.debounce_s)
    sweep = startup_sweep(reconciler, sender=_default_sender, now=time.time(),
                          debouncer=debouncer)
    lines = socket_lines(args.socket, request, timeout_s=args.heartbeat_s)
    report = watch_stream(
        lines, reconciler=reconciler, sender=_default_sender,
        debouncer=debouncer, heartbeat_s=args.heartbeat_s, stall_s=args.stall_s,
        beat=lambda now, rep: write_heartbeat(args.heartbeat_path, now=now, report=rep),
        live_panes=live_pane_ids, already_notified=sweep["notified"],
        pending=sweep["pending"], current_pane=current_pane_record)
    report["startup_sweep"] = sweep
    print(json.dumps(report, indent=2))
    return 0 if report["subscribed"] and not report["errors"] else 4


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="watch herdr panes for blocked transitions and notify the owner (#612)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_watch = sub.add_parser("watch", help="subscribe and notify on blocked transitions")
    p_watch.add_argument("--socket", default=DEFAULT_SOCKET)
    p_watch.add_argument("--debounce-s", type=float, default=DEFAULT_DEBOUNCE_S)
    p_watch.add_argument("--heartbeat-s", type=float, default=DEFAULT_HEARTBEAT_S)
    p_watch.add_argument("--heartbeat-path", default=DEFAULT_HEARTBEAT_PATH)
    p_watch.add_argument("--stall-s", type=float, default=DEFAULT_STALL_S)
    p_watch.add_argument("--dry-run", action="store_true",
                         help="print the subscribe request and exit; sends nothing")
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
