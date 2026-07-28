# #612 — blocked → notify-owner bridge (design, **revision 2**)

Epic #667, child 7. Written 2026-07-28. **R2 after a Step-4 review returned FAIL with a BLOCKER, and
the probes agreed with it.** Loop-back consumed: `design` 1 of 2, global 1 of 3.

R1 was built on `pane.agent_status_changed` and a `state_change_seq` dedup key. Both were wrong, and
the way they were wrong is the interesting part: R1's implementation passed 54 tests because the tests
synthesised the event shape R1 had assumed. That is the fifth time this epic has produced a green
suite over an invented contract, so R2 states the provenance of every shape it uses.

## 1. What is being built

**Only the watcher half.** The transport exists and is in daily use: `projects/sentinel/bin/notify.sh`
via the `notify-owner` skill (one-way; already handles password-out-of-argv, 0600 temp files,
HTTP-code reporting and a self-check), and `hooks/hermes_bridge.py` (#568/#584) for two-way. #608 AC5
proved notify-owner reachability from a herdr context; AC1 names that transport. This change owns
subscribe → recognise → reconcile → debounce → notify → heartbeat.

## 2. The platform, measured — every claim here was observed on the wire

| Finding | Evidence |
|---|---|
| **`pane.agent_status_changed` never observed firing** | 0 frames in two captures: 135 s subscribed to all 8 panes, then 90 s subscribed to two named panes while polling their status |
| **It cannot be subscribed globally** | its subscription requires `pane_id` (`required: ["type","pane_id"]`); a global attempt is refused `invalid_request: missing field 'pane_id'` |
| **It carries no sequence number** | properties are exactly `agent, agent_status, display_agent, pane_id, state_labels, title, workspace_id` |
| **`state_change_seq` is not where R1 thought** | it is on `snapshot.agents` (7 items), NOT on `snapshot.panes` (8 items) and NOT on any event |
| **`pane.updated` is GLOBAL and sufficient** | no `pane_id`; embeds a full pane record with `agent_status`, `label`, `workspace_id`, `tab_id`, `agent`, and a monotonic **`revision`**; observed live with real `working`/`idle`/`unknown` values |
| **`snapshot.panes` carries `revision`** | read live (`revision: 3`); it does NOT carry `label` |
| **A fresh subscription REPLAYS a backlog** | within 450 ms: created/closed frames for six long-gone panes, one created AND closed inside the replay |
| **Wire names are snake_case** | subscribe `pane.updated`, receive `pane_updated` |

R1's design would therefore have failed in the worst possible way: the event it waited on never
arrives, and had it arrived, the dedup key would have been `None` on every frame — which in R1's own
reconciler meant *accept*, i.e. page the owner for the entire replay.

## 3. The architecture that follows from those facts

**Subscribe globally to `pane.updated` + `pane.closed`.** Nothing per-pane, so the fact that
subscriptions are fixed for a connection stops mattering: a pane created later is covered by the same
global feed, and there is no re-subscribe protocol to get wrong.

**Reconcile every frame against `session.snapshot` before it can notify.** The reconciler holds each
pane's `revision` baseline; a frame is accepted only for a known pane with a strictly newer revision.
A frame with **no** revision fails CLOSED — without one there is no way to distinguish a replay from a
live event, and guessing wrong pages the owner about a dead pane. A genuinely new pane is legitimately
absent from the snapshot, so it is learned from the live `pane_created`/`pane_updated` feed and its
first frame is accepted.

**Notify on a transition INTO `blocked`**, tracked from our own observed series. A first observation of
`blocked` counts: an agent already waiting when the watcher starts is exactly the case F2 says to
expect, and requiring a known prior status would silently miss it.

**Debounce reserves, and only a CONFIRMED send commits.** A review found R1 could lose a notification
permanently — a failed transport consumed the window, and with no further transition the owner never
heard. So `allow` reserves, `commit` runs after rc 0, and `release` reopens the window on failure so
the next frame retries.

## 4. AC5 is a PROVENANCE boundary, not keyword hygiene

R1 refused forbidden keyword arguments. The review pointed out that this stops nothing: a caller can
pass `label=event.title` and every key looks legitimate. Every pane record carries
`terminal_title`/`terminal_title_stripped`, and a terminal title echoes the current task.

So `body_for_pane` is the **only** public builder, it takes a pane RECORD, and it SELECTS
`label`/`name`/`pane_id`, `workspace_id`, `tab_id`, `agent` itself. There is no parameter for
caller-supplied display text to abuse. One residual hole is closed explicitly: if something upstream
copied screen text into `label`, the body would leak it while every key looked fine — so a label
identical to the record's own `terminal_title` is refused. And `pane.output_matched` is never
subscribed at all, so `matched_line` and `read.text` never enter the process.

## 5. AC3, and an honest statement of what its signal is not

AC3 asks for a heartbeat plus a warning when a waiting process sits `idle`/`unknown` with no
recognised transition. Both ship, and the second one is described accurately rather than flatteringly.

**The heartbeat needed a timer and an observer, and R1 had neither.** A predicate is not periodic, and
a watcher blocked reading a quiet socket cannot run one — nor can a dead watcher report its own death.
So the socket read carries a timeout and yields `None` on expiry, which is what drives the heartbeat
during silence, and the heartbeat is **written to disk** (`write_heartbeat`) for something outside to
check. A heartbeat nobody reads proves nothing: an external observer alerting on staleness is required
and is named as a follow-up, not implied to exist.

**The stall warning does NOT detect a missed prompt, and says so in its own text.** The review was
right: an ordinary finished pane can sit `idle` forever, and a detector miss can leave the last status
as `working`, so `idle`/`unknown` plus elapsed time is neither necessary nor sufficient. What ships is
AC3's literal signal — a stale-pane warning — whose message states that it cannot tell a missed
approval from an idle pane. Promising more would be the same overclaim this epic has already had
caught four times.

## 6. Failure modes

| failure | behaviour |
|---|---|
| socket absent / connect refused | fail loud, non-zero, nothing sent |
| subscription refused | fail loud with herdr's own payload preserved (#673) |
| backlog replay on connect | every frame reconciled against the snapshot; the six-pane replay notifies nobody |
| frame with no `revision` | dropped, fail-closed |
| stale or equal `revision` | dropped as a replay or a re-delivery |
| pane closed | forgotten; later frames for it are dropped |
| pane created after the snapshot | learned from the live feed; first frame accepted |
| same pane blocks twice quickly | debounced; a genuine later block after the window notifies |
| transport returns non-zero | window NOT consumed, `send_failures` counted, next frame retries |
| label copied from screen text | body refused rather than leaked |
| quiet socket | read timeout fires the heartbeat and writes it to disk |
| watcher dies | its heartbeat goes stale — detectable only by an external observer (follow-up) |
| detector misses a prompt (F2) | stale-pane warning, explicitly not a miss detector |

## 7. Out of scope

Enforcement-relevant liveness (sentinel/exit-code, HD2.4), dashboard polish (HD3.4), any change to
the notify-owner transport, and the external heartbeat observer.

## 8. AC2 — what is NOT proven, stated plainly

AC2 asks for representative REAL Claude and Codex approval screens driven through the pinned detection
manifests, with a notification asserted within a deadline. **This is not discharged here.** Two
reasons, both structural rather than effort: driving a real approval screen means putting a live agent
into a blocking prompt on this host, and `pane.agent_status_changed` — the event whose name suggests
it reports exactly that — was never observed firing at all, so what herdr emits when a pane genuinely
blocks has not been captured. Every other AC is exercised against observed frame shapes; AC2 needs a
live blocked pane and is recorded as verification_deferred with the check to run.
