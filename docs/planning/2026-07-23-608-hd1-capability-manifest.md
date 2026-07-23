# #608 (HD1.0) — herdr platform qualification — capability manifest

No-code qualification of the pinned herdr binary under this host's real permissions and
environment. Per AC7, every Phase-A-assumed call in
`docs/planning/2026-07-21-herdr-console-plan.md` §1/§4.4 is proven live below — a failure
BLOCKS HD1.2–HD1.4 (gate semantics, not documentation).

## Binary / config / socket facts (confirmed live, this host)

| Fact | Value | Evidence |
|---|---|---|
| Version | `herdr 0.7.5` | `herdr --version` |
| Protocol | `17` | `ping` response `protocol` field |
| Config path | `~/.config/herdr/config.toml` | `herdr --help` "Config:" line |
| Socket path | `~/.config/herdr/herdr.sock` | `herdr --help` "Env:" section; confirmed fixed (see AC1) |
| Socket permissions | `srw-------` (0600, same-user) | `ls -la ~/.config/herdr/herdr.sock` |
| Logs | `herdr.log`, `herdr-client.log`, `herdr-server.log`, same dir | `herdr --help` "Logs:" line |
| Production server | PID 2877272, up 2d02h10m at check time, started 2026-07-21 12:35:30 | `ps -o pid,lstart,etime,cmd -p 2877272` |
| `HERDR_ENV` nested-launch guard | Advisory prose only — enforced against nested TUI launches, not a security boundary | Live-reproduced: a plain nested `herdr` inside this pane fails fast ("nested herdr is disabled by default") |

## AC1 — Headless server start under the real cron environment: **PARTIALLY QUALIFIED**

Two independent, real blockers prevented the strict "under actual crond" form of this AC.
Both require owner action to close; neither was worked around.

1. **crontab write denied.** Installing a sentinel-guarded one-shot probe line (fires once,
   attempts a headless named-session start, logs the result, self-disarms) was blocked outright
   by the Claude Code auto-mode permission classifier. Verified afterward: `crontab -l` is
   byte-identical to the pre-attempt backup — all 9 pre-existing entries intact, nothing to roll
   back. **Owner handoff, if they want this closed under literal crond:**
   ```
   crontab /tmp/claude-1000/-home-rocky00717-rawgentic/4e05f335-c9d3-4b4c-b474-41e728ccc53c/scratchpad/hd10/crontab-new.txt
   ```
   (appends one `* * * * *` line calling the sentinel-guarded probe script at
   `scratchpad/hd10/cron-probe.sh`; self-disarms after first fire; check
   `scratchpad/hd10/cron-probe-result.log` a minute later, then `crontab` the original backup
   file back to remove the line.)

2. **`herdr server` is a host-wide singleton with no isolated-instance escape hatch.** Pointed
   `HERDR_CONFIG_PATH` at a scratch directory specifically to start a SECOND, isolated instance
   for a clean-start trial (so the live production daemon backing this very session would not be
   touched). It refused immediately: `error: herdr server is already running / api socket:
   /home/rocky00717/.config/herdr/herdr.sock` — the singleton lock keys off a FIXED path, not
   `HERDR_CONFIG_PATH`. Did not stop the live server (PID 2877272; backs this pane and 3 others)
   to force a cold-start trial — that is a destructive action on shared live infrastructure with
   no owner authorization. **Owner handoff, if they want this closed via a real cold start:**
   authorize a bounce of the live server during a maintenance window (`herdr server stop` then
   `herdr server`), observed for a clean start with no other panes active.

**Evidence gathered anyway (honest proxy, not literal crond):** ran the sentinel-guarded probe
directly under `env -i` (clearing all inherited session vars including `HERDR_ENV`) + `setsid`
(detaching from the controlling terminal) — the properties a real cron child actually has,
without touching crontab. Under that clean environment, the bare `herdr --session <name>` form
(the CLI's default create-or-attach) **panicked** on TUI init:
```
thread 'main' panicked at .../ratatui-0.30.0/src/init.rs:299:
failed to initialize terminal: Os { code: 6, kind: Uncategorized, message: "No such device or address" }
```
Despite the panic, the underlying session registered and showed `running` (with its own real
socket) in `herdr session list` — the backing session survives the front-end crash, but this is
clearly **not** the intended headless entry point. That is `herdr server` (the "Advanced
commands" entry, "Run as headless server"), which is the one gated by finding 2 above.

**Verdict:** AC1 not fully proven this run. Recorded honestly rather than fudged; both gaps are
independently closeable by the owner (one command each) and don't require code changes.

## AC2 — Named-session routing: **PROVEN**

`herdr --session hd1-0-qual-test` created a session with its own directory and socket, distinct
from the default session's:
```
hd1-0-qual-test   running   ~/.config/herdr/sessions/hd1-0-qual-test   .../hd1-0-qual-test/herdr.sock
```
`herdr session list` showed it; `herdr session stop hd1-0-qual-test` then
`herdr session delete hd1-0-qual-test` cleanly tore it down (confirmed absent afterward). Routing
by name to a dedicated socket/directory works as Phase A assumed. (`session attach`'s interactive
experience was not separately probed — same TTY dependency as AC1's panic; the routing mechanism
itself, which is what Phase A depends on, is proven.)

## AC3 — `agent start -- <argv>`: **PROVEN**

Created an isolated tab/pane (`herdr tab create --workspace w1 --label "hd1.0-qual-probe (safe
to close)" --no-focus`) so the probe would not touch any of the 4 pre-existing live panes in this
workspace (three of which are other concurrent sessions, one of which is this very run).
```
herdr agent start hd10-probe --kind claude --pane w1:pN --timeout 20000
→ {"type":"agent_started","agent":{...,"interactive_ready":true},"argv":["claude"]}
```
Real argv (`["claude"]`) reached a real pane and was detected ready for input. Torn down via
`herdr tab close` immediately after observing success; pane count confirmed back to 4.

## AC4 — Event-subscriber reconnect: **PROVEN — with a load-bearing real finding**

Socket API framing confirmed live: JSON request/response, **one request per connection** (a
second `send` on an already-answered connection gets `BrokenPipeError` — confirmed directly).
`events.subscribe` is the one method meant to keep its connection open afterward, streaming
pushed event lines with no further request needed.

**Real finding (directly relevant to HD1.4's notify-owner bridge design):** a brand-new
`events.subscribe` call does **not** only deliver events from that point forward — it replays a
backlog of matching historical events first. Isolated proof: opened a fresh subscription,
drained **7** already-queued `tab.created`/`tab.closed` events (all from this session's own
earlier probe activity, going back to the very first AC3 test) before creating any new tab; only
after fully draining the backlog did a freshly-created, uniquely-labeled tab's event arrive and
match. Retention window/size not established (not needed for this qualification; open question
for HD1.4's implementer).

**This directly validates a risk the plan already carried** ("Missed socket events across
disconnects → Reconnect always starts `session.snapshot` + registry reconciliation" — plan §5
risk table): a consumer that reacts to the first event off a fresh/reconnected subscription
without reconciling against `session.snapshot` risks acting on stale, already-resolved state
(e.g., a blocked→notify-owner watcher could fire a notification for a block that was already
handled). HD1.4 must not skip the snapshot-reconciliation step — this is now evidenced, not just
assumed.

**Reconnect cycle proven:** disconnect (socket close) → `session.snapshot` (real reconcile call,
succeeded) → re-`events.subscribe` on a fresh connection → confirmed the reconnected subscriber
receives new live events (created a second uniquely-labeled tab; the reconnected subscriber's
next event matched it after its own backlog drained).

## AC5 — notify-owner transport reachability from a herdr context: **PROVEN**

Distinguished two different checks:
- `python3 hooks/hermes_bridge.py --self-check` — passes (11/11), but is entirely mocked
  (injected `notify`/`transport` callables) — proves the bridge's Python logic runs correctly in
  this herdr-managed pane's process environment, **not** real network reachability.
- **Real transport probe** (this run): called `hermes_bridge._default_transport` directly with a
  60-second-old `since_ms` window and the real configured recipient/chat_guid — a genuine
  read-only HTTP round-trip to the live BlueBubbles server, no message sent to the owner. Result:
  succeeded, 5 rows returned, no `BridgeUnreachable`. Confirms the notify-owner/ask-owner
  transport is reachable from a process running inside a herdr-managed pane, with a real network
  call rather than a mock.

## AC6 — This document

Checked in at `docs/planning/2026-07-23-608-hd1-capability-manifest.md` (+ rendered
`.html`), per scope.

## AC7 — Gate semantics

Per the issue's own AC7 wording, an unproven call BLOCKS HD1.2–HD1.4, not just documents the
gap. **AC1 is that unproven call.** HD1.2–HD1.4 stay blocked until the owner closes one of the
two named AC1 sub-blockers (run the crontab one-liner above, or authorize a live-server bounce).
AC2–AC5 are fully proven and impose no additional block. HD1.1 (install formalization) and
HD2.0 (executor-path qualification) are explicitly out of this issue's scope regardless.

## What was NOT checked

- `session attach`'s interactive experience (TTY-dependent, same class as AC1's panic — not
  needed to prove the routing mechanism Phase A depends on).
- `events.subscribe` backlog retention window/size (bounded by count? by time? — open question,
  not load-bearing for this qualification; named for HD1.4's implementer).
- A live install/update cycle, sounds/mouse config, or anything HD1.1-scoped.
