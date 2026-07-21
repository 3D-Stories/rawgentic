# Herdr as the workspace console — analysis + phased integration plan

**Date:** 2026-07-21 · **Author:** WF-adjacent analysis session (goal-driven) · **Status:** PROPOSAL — nothing filed, nothing installed
**Subject:** [ogulcancelik/herdr](https://github.com/ogulcancelik/herdr) as the main console application for the rawgentic workspace, in conjunction with the rawgentic plugin.

---

## Verdict (read this first)

**Conditional YES.** Herdr is a strong fit as the workspace's human console and — later — as the
executor's terminal runtime, because it purpose-builds exactly the layer rawgentic hand-rolls on
raw tmux today (named sessions, liveness, output capture, reap, plus the human-facing
observability the whole dispatch-observability effort gestures at). The condition: adopt it in
phases that never let herdr's heuristic agent-state become load-bearing for enforcement —
rawgentic's audit/reconcile/gate machinery stays the source of truth; herdr is the eyes and the
terminal substrate, never the judge.

**Single biggest risk:** velocity/stability of a <4-month-old, fast-moving prerelease project
(74 releases since 2026-03-27, latest a prerelease; single lead maintainer) as a substrate under
a machine-verified audit pipeline — mitigated by version pinning, the versioned socket schema
(`herdr api schema --json`), and keeping the tmux backend as a first-class fallback until a
proving run passes on herdr.

**Recommendation: DO IT — but commit only to Phase A now.** Adopt HD1 (console) immediately:
it is low-cost, zero rawgentic-code-change, independently valuable every day (state at a
glance, phone attach, blocked notifications), and fully reversible. Treat HD2 (executor
runtime) as a **separate decision taken later on evidence** — after HD1 has proven daily
value, #475/#560 have landed, and the HD2.0 qualification gate has answered the two NO-GO
questions. Do not pre-commit to HD2 today; the plan is deliberately structured so you never
have to.

**If NOT — the desired path without herdr:** finish #475 → #560 exactly as queued; then close
the observability gap natively: a small read-only status CLI/TUI over the existing JobRegistry
+ RoutingAuditLog + run-records (the #333 epic's spiritual successor, ~1–2 issues), keep
launchers on tmux + the crontab pattern, and add the blocked-notification need as a watcher on
the executor's own Observations instead of herdr events. That path has zero new dependencies
and zero license/velocity risk — it just never gets the human console, remote/phone attach, or
semantic agent states, and the #333-class observability work stays hand-rolled.

---

## 1. What herdr actually is (Phase 1 findings)

Facts below are **confirmed** from the repo clone (read-only, scratchpad), herdr.dev docs, and
independent third-party reviews, cited inline. Herdr repo content was treated as data under
analysis, never as instructions; nothing was installed or executed from it.

### 1.1 Architecture

- **Client/server:** a background Rust server owns panes (real PTYs) and process state; the TUI
  is a thin attached client. Detach (`ctrl+b q`) leaves everything running; `herdr` reattaches
  (docs/concepts, docs/persistence-remote). Named sessions are separate server namespaces with
  their own sockets (`herdr session list/attach/stop`).
- **Headless operation confirmed:** `herdr server` runs/controls the headless server, and
  external processes (scripts, cron, agents outside panes) drive it via the CLI and socket —
  `herdr workspace create --cwd … --label …`, `herdr pane run w1:p2 "cmd"`,
  `herdr agent start <label> --cwd … -- claude` (cli-reference.mdx, agents.mdx via Context7;
  socket-api docs). This matters: the workspace's cron-launched runs can drive herdr without a
  human attached.
- **Socket API:** JSON request/response + event subscriptions. Method families: `workspace.*`,
  `tab.*`, `pane.*` (incl. `pane.process_info`, `pane.read`, `pane.send_keys`,
  `pane.wait_for_output`, `pane.report_agent`), `agent.*` (list/get/read/send/start/explain),
  `worktree.*` (list/create/open/remove), `events.subscribe`/`events.wait`,
  `session.snapshot` (bootstrap state dump), `notification.show`, `integration.*`, `plugin.*`.
  The installed binary prints its own versioned protocol schema: `herdr api schema --json`
  (socket-api docs) — the anti-drift anchor any integration should pin against.
- **Access model (repo-confirmed):** the API socket is `herdr.sock`, mode **0o600** — file
  permissions are the ONLY access boundary; there is no token/peer-credential check on accept
  (`src/api/server.rs`; no `SO_PEERCRED` anywhere). Any same-user process can drive the full
  method set from outside a pane — the `HERDR_ENV=1` skill gate is **advisory prose**, enforced
  only against nested TUI launches (`src/main.rs:424-451`). No inbound TCP exists anywhere
  (`TcpListener` grep = 0); outbound network is pull-only (version check + detection-manifest
  index from herdr.dev, both disableable). For a same-user orchestrator this is exactly the
  trust model tmux's private socket gives us today.
- **External terminal bridge:** beyond the CLI, `herdr terminal session observe w1:p1` streams
  read-only NDJSON frames of a pane, and `… control --takeover` is a single-owner writable
  controller (persistence-remote docs; `src/cli/spec.rs:675-713`) — a sanctioned surface for
  external tooling to watch or drive a herdr-owned terminal.
- **Remote/ssh:** `herdr --remote <host>` runs a local thin client against a remote server over
  ssh; or ssh in and attach tmux-style. Sessions survive client disconnect; reattach from any
  terminal including a phone (persistence-remote docs).

### 1.2 The herdr agent skill (`SKILL.md` at repo root)

A markdown skill teaching an embedded agent to control herdr from **inside** a herdr-managed
pane, gated on `HERDR_ENV=1` (if unset: say so and stop — an agent outside herdr must not
control a session it does not own). Command groups: `pane`, `workspace`, `worktree`, `tab`,
`wait`, `terminal`, `notification`, `integration`, `session`. Contract highlights (SKILL.md):

- discovery via `herdr <group>` (never bare `herdr` — that attaches the TUI; never probe
  mutating commands by omitting args — `herdr workspace create` executes with defaults);
- IDs are opaque short handles (`w1`, `w1:t1`, `w1:p1`, `term_…`); closed IDs never reused;
  re-read create/split/move responses after mutations, never construct IDs;
- most control commands print JSON — read identifiers and state from responses, not predictions;
- herdr injects caller context env (e.g. `HERDR_ENV`, tab/pane ids) into every managed pane.

With it an agent can: inspect neighboring work, split panes and run commands without stealing
focus, read pane output, wait on output or agent status, start helper agents in sibling panes
(agent-skill docs).

**Known drift inside the skill (repo-confirmed):** `SKILL.md` documents a top-level
`herdr wait` command group (`wait agent-status` / `wait output`), but the CLI spec has no
top-level `wait` — the real commands are `herdr agent wait --until <status>` and
`herdr pane wait-output --match/--regex` (`src/cli/spec.rs:377-390`; `src/api/wait.rs`). The
skill hedges correctly ("the installed binary is the authority"), but this is concrete evidence
that any adapted skill (HD3.1) must be validated against the pinned binary, never trusted from
upstream prose. Wait semantics worth keeping: `agent wait` pins the resolved pane occupant (a
replacement process returns `agent_not_running`, never a false satisfy); `agent prompt --wait`
requires an observed state change within 5s or returns `agent_prompt_stalled`; `pane run` sends
**text + Enter** into the pane (keystroke injection — fine for interactive helpers, NOT a
process-spawn primitive; see §6).

### 1.3 Agent awareness (the feature tmux lacks)

- **States:** `blocked` / `working` / `done` / `idle` / `unknown`, rolled up pane → tab →
  workspace → sidebar (concepts docs).
- **Status authority model (agents docs):** per-pane single authority. Agents with complete
  lifecycle hooks (Pi, OMP, OpenCode, Kilo, Kimi, Hermes, MastraCode) are hook-authoritative.
  **Claude Code and Codex are deliberately NOT lifecycle authorities** — their integrations
  report *session identity only* (for native `claude --resume` restore); their live state comes
  from **screen-manifest detection**: herdr reads the live bottom-of-buffer screen snapshot and
  classifies it against TOML manifests. Blocked detection is deliberately strict (only known
  approval/question UI shapes); unknown prompts fall back to `idle`.
- **Consequence for rawgentic (load-bearing):** for our two primary engines (claude, codex),
  herdr's `working`/`blocked`/`done` is a *heuristic screen classifier*, not ground truth. It is
  excellent for a human console and for advisory waits; it must never replace sentinel files,
  exit codes, or Observation reconciliation as enforcement evidence. Independent review
  (maxtokens.ai, 2026-06-14) reaches the same conclusion: "the `done` status shouldn't become
  the single source of truth… wait for a specific output marker or a structured response."
- **Remote detection manifests:** herdr auto-fetches manifest updates from herdr.dev and applies
  them without restart (agents docs). For a reproducible, audited pipeline this is a
  supply-chain/reproducibility concern; disable with `[update] manifest_check = false` and pin
  local overrides in `~/.config/herdr/agent-detection/<agent>.toml`.

### 1.4 Session persistence and restore (session-state docs)

| Path | What survives |
|---|---|
| Detach/reattach | everything — processes never stop (strongest path) |
| Server restart (snapshot restore) | layout/cwd/focus only; processes gone; panes come back as fresh shells |
| Pane screen history (`[experimental] pane_history`, off by default) | recent screen contents — off by default because it persists secrets |
| **Native agent session restore** (on by default) | supported agents resume their own conversation: Claude Code via integration v6 → `claude --resume <session>`; Codex v5 → `codex resume` |
| Live handoff (`herdr update --handoff`, experimental) | PTY fds transferred to the new server — processes keep running across server replacement |

**Scope caveat (F3 adopted, evidence found):** the persistence-remote docs state a named
session "still shares the same global config file" — so `[session] resume_agents_on_restore`
is very likely GLOBAL, not per-session. The workable split is therefore a **separate config
root for the executor's herdr server** (e.g. `XDG_CONFIG_HOME` override at server launch) with
native restore off, while the owner's console server keeps its own config with restore on.
HD2.0 carries the hard qualification test: create interactive + executor sessions, restart
herdr, assert only the interactive agent resumes; record the exact config paths and launch
arguments in the compatibility manifest.

**Consequence (sharpened by the touchpoint inventory, §2):** herdr's native restore issues
`claude --resume <session-ref>` on its own authority after a server restart — which is
*precisely* the operation rawgentic's `supervisor._relaunch` performs deliberately, under a
resume cap (`MAX_RESUME=2`), with the persisted `provider_session_id` asserted at collect time
(`await_job(expect_session_id=…)` — a mismatch registers `failed` and raises). Two resume
authorities racing on the same conversation is exactly the class of bug the executor-hardening
epic exists to kill. Any executor-backend phase must pick ONE owner per pane class (proposal:
`[session] resume_agents_on_restore = false` for executor-managed panes/sessions; herdr native
restore stays on for the owner's interactive panes).

### 1.5 Plugins, integrations, notifications

- **Plugins:** directory + `herdr-plugin.toml` manifest; argv commands (no shell); event hooks
  (e.g. `on = "worktree.created"`), actions, panes, link handlers. The full herdr CLI is the
  plugin API; **no sandbox** — "a plugin is ordinary code… yours to vet" (plugins docs). Same
  trust posture as Claude Code plugins.
- **Integrations:** `herdr integration install claude` writes `hooks/herdr-agent-state.sh` into
  `~/.claude/` and **edits `settings.json`** to add hook entries (session-identity reporting).
  Any install on this workspace must be diff-reviewed first — our `settings.json` carries
  wal-guard and mempalace hooks that must not be perturbed.
- **Notifications:** `notification.show` + agent-state events via `events.subscribe` — the raw
  material for a "blocked → iMessage via notify-owner" bridge.

### 1.6 Operational profile

- **License (repo-confirmed):** **dual-licensed — AGPL-3.0-or-later + a commercial license**
  for organizations that cannot comply with AGPL (`LICENSE:1-8`; `Cargo.toml`
  `license = "AGPL-3.0-or-later"`; README §license; this explains GitHub API's `NOASSERTION`).
  Analysis in §5: CLI/socket invocation as a separate process creates no copyleft obligation on
  rawgentic; linking/vendoring would. We do neither in any proposed phase — and the commercial
  option is a standing exit if that ever changes.
- **Maturity/velocity:** repo created 2026-03-27; shipped version **0.7.4** (2026-07-15,
  `Cargo.toml`); 53 versioned CHANGELOG releases at a roughly weekly cadence (sometimes 2 in
  2 days); latest GitHub release is a dated **prerelease** (2026-07-17); 18.9k stars, 1.2k
  forks, 81 open issues, 50 contributors with a single dominant author (GitHub metadata,
  fetched 2026-07-21). ~193k lines of Rust in `src/`, real integration-test suite
  (detach/reattach, live handoff, multi-client, headless server — `tests/`). API stability:
  an explicit protocol version rides every `ping`, clients are told to handle unknown fields
  gracefully, and a `protocol_guard` reports mismatches — but **no hard semver/back-compat
  guarantee is stated** (socket-api docs; `src/cli/protocol_guard.rs`). Active independent
  coverage: Better Stack, DevOps Toolbox, madflex (2026-07-09), maxtokens.ai (2026-06-14),
  Moshi guides. Real-world rough edges reported: idle CPU (renderer redraws animated agent
  panes), kitty-protocol key-repeat quirk, no automatic tab rename, occasional prompt-redraw
  races (madflex).
- **Install paths:** install script (`curl | sh` — **not** for this workspace), Homebrew, mise,
  release binaries, nix flake, `cargo build` from source. Proposal: pinned release binary with
  checksum, or source build — a sanctioned-path decision inside the epic.
- **Platform:** Linux/macOS first-class; Windows beta.

### 1.7 The key architectural caveat for us

Herdr detects the **foreground process in its own panes**. An agent running inside tmux inside a
herdr pane is invisible as an agent — herdr sees tmux (maxtokens.ai review; consistent with the
screen-manifest model). Today's rawgentic executor children live in **raw tmux sessions**
(`rg-*`), and today's epic launchers run Claude in tmux via cron. **A console-only herdr adds
semantic value only for processes actually launched into herdr panes.** This single fact shapes
the whole phasing in §3.

---

## 2. The rawgentic side (Phase 2 findings)

### 2.1 What rawgentic hand-rolls today (subagent inventory, file:line-verified)

**The reframing fact:** the supervised executor uses tmux for exactly **five things** —
(1) spawn a durable named session running an argv as the pane's *initial process*
(`supervisor.py:428`, `new-session -d -s <name> -c <cwd> -- <argv>`); (2) read the pane PID once
(`supervisor.py:432`, `display-message #{pane_pid}`); (3) liveness by name (`supervisor.py:470`,
`has-session`; also the read-only status probe `executor_routing_lib.py:1615`); (4) enumerate
sessions (`supervisor.py:982`, `list-sessions` — reap's derived liveness + CF-11
unknown-session detection); (5) teardown (`kill-session` at `supervisor.py:461/621/1009`,
`kill-server` at `:773`). **`send-keys` and `capture-pane` are deliberately never used**
(`supervisor.py:6` — F7): completion is process-exit + an atomic `observation.json` sentinel
(`pane_runner.py:231-241`), process-tree kill is `/proc` descendant snapshots + `os.killpg`
(`supervisor.py:551-623`), and identity (pgid, start-time PID-reuse guards, provider-pgid
sidecar) is all `/proc`-derived (`supervisor.py:437,443,1047`; `pane_runner.py:135-156`).

The private, run-scoped tmux **socket path** (`resolve_socket`, `supervisor.py:122-148`) is the
durability anchor: it is persisted on the JobRecord (`run_socket`) and re-used verbatim by
`recover()` after an orchestrator restart to find sessions again by their deterministic names
(`registry.session_name` → `rg-<run>-<seat>-<attempt>`, `registry.py:90-94`). The Observation's
`tmux_session` field is that same name (`contract.py:171`). Audit ordering is
runtime-independent: the receipt is appended **before** any spawn
(`executor_routing_lib.py:958`) and the observation **after** the full lifecycle completes
(`:986`) — a terminal-runtime swap under `supervisor.launch`/`await_job` does not touch it.
(Also noted en route: `supervisor.preflight` exists and is tested but is **not wired** into the
live dispatch path — a pre-existing gap, named here, that HD2.1's seam should close for
whichever runtime is active.)

`session_policy` (`fresh`/`resume`) is the **provider conversation** policy, orthogonal to the
terminal session: `resume` is entered only by `supervisor._relaunch` for a `quota_paused` job
(`claude --resume <provider_session_id>`, capped `MAX_RESUME=2`, identity-asserted at collect);
codex refuses resume fail-loud. Every routing-table seat ships `fresh`.

### 2.1b Required capabilities ↔ herdr mapping

The ten capabilities a replacement terminal runtime must provide (consumption sites verified),
and what herdr offers for each:

| # | Required capability (load-bearing for) | tmux today | herdr equivalent | Status |
|---|---|---|---|---|
| 1 | Spawn argv as pane's INITIAL process, cwd pinned (launch, audit) | `new-session -- argv` | `herdr agent start … --cwd … -- <argv>` (argv form confirmed); generic non-agent pane spawn-with-argv **unverified** — `pane run` is text+Enter, NOT a spawn primitive | **verify in HD2.2** |
| 2 | Read pane PID (launch → pgid/start-time identity) | `display-message #{pane_pid}` | `pane.process_info` | confirmed method exists; field parity unverified |
| 3 | Liveness by handle (liveness, recovery gate) | `has-session` | `pane.get` / `agent.get`; IDs never reused ⇒ a dead pane is unambiguous | good |
| 4 | Enumerate live sessions (reap, unknown-session detection) | `list-sessions` | `pane.list` / `agent.list` / `session.snapshot` | good |
| 5 | Kill by handle (rollback, reap) | `kill-session` | `pane.close` (tree-kill stays `/proc`+`killpg`, unchanged) | good |
| 6 | Kill whole namespace (cleanup) | `kill-server` | `herdr session stop <name>` / `server.stop` | good |
| 7 | Private run-scoped namespace/socket (durability anchor) | `-S <run>.sock` | **named sessions** — own panes, sockets, state (`~/.config/herdr/sessions/<name>/herdr.sock`); or `HERDR_SOCKET_PATH` | strong fit: one herdr named session per run_id |
| 8 | Durable naming + re-adoption after orchestrator restart (recovery) | deterministic `rg-*` name, re-probed on the persisted socket | pane IDs are server-assigned opaque handles, **never reused** — persist the returned ID on the JobRecord (plus `pane.rename` to carry the `rg-*` label for humans); re-adopt via `session.snapshot` on the persisted session socket | fit, different shape: store-ID instead of derive-name |
| 9 | Env injection into pane (PYTHONPATH, creds) | server-env inheritance on private socket | herdr injects `HERDR_*` context; arbitrary per-pane env **unverified** — fallback: wrap argv with `env(1)` | **verify in HD2.2** |
| 10 | Version/capability preflight (fail-closed gate) | `tmux -V` + verb probes | `ping` (protocol version) + `herdr api schema --json` pin + probe verbs | good — and HD2.1 should wire preflight into dispatch (closing the pre-existing gap) |

**Not needed from the terminal runtime** (stays OS/filesystem, must NOT be ported onto herdr
APIs): output capture (sentinel files), process-tree kill (`/proc` + `killpg`), pid/pgid/
start-time identity (`/proc`). Herdr's `pane.read`/screen snapshots are console/triage surfaces,
never enforcement evidence.

### 2.2 What herdr does NOT cover (stays rawgentic-owned, confirmed)

- Routing/seat resolution, fallback chains, enforcement, canary, gates — all of
  `executor_routing_lib.py` above the terminal layer.
- RoutingAuditLog Observations, reconcile, correlation binding — herdr has no audit ledger.
- Worktree **promotion** (CAS, base-staleness guard, path policy — `worktree.py promote`):
  herdr's `worktree.*` is create/open/remove + workspace attachment, not integration-branch
  promotion.
- Quota detection, per-call caps, account-switch recovery (#558/#559 territory).
- Everything WF2/WF3: gates, loop-backs, run-records, telemetry.

---

## 3. The plan — three phases (Phase 3)

**Recommendation:** adopt in the order A → B → C below. Alternatives weighed: (i) *big-bang
executor swap* — rejected: two migrations racing (#474 legacy flip + runtime swap) with no
proven baseline; (ii) *console-only forever* — under-uses the socket API where rawgentic's real
pain (liveness, adoption, observability) lives; (iii) *build our own console on the #333
telemetry* — months of TUI work herdr already does better, against the one-helper-one-home rule.

### Phase A — herdr as the owner console (no rawgentic code changes)

**What:** stand herdr up as the workspace console; move *orchestrator* sessions (interactive
work, epic-run launchers) into herdr panes so they gain state, persistence, remote/phone attach.
Executor children stay in raw tmux (invisible to the console as agents — accepted, §1.7).

- Vet + install pinned herdr (no `curl | sh`; checksummed release binary or source build);
  config baseline: `[update] manifest_check = false` decision, sounds, mouse.
- Review-then-install the Claude Code integration (diff `settings.json` changes against
  wal-guard/mempalace hooks before accepting).
- Launcher integration: epic launchers (`epic475-resume.sh`-class, the long-run-resume crontab
  pattern) get a herdr variant — **argv-based `herdr agent start … -- <argv>` only** (F4
  adopted: `pane run` is text+Enter keystroke injection, §1.2 — never a launch mechanism for an
  unattended cron path; if the launcher's process cannot start argv as the pane's initial
  process, HD1.3 is NO-GO and tmux stays). Cron → headless server confirmed workable (§1.1),
  exact cron-environment behavior proven in HD1.0.
- Notify bridge: `events.subscribe` watcher → notify-owner (blocked-state iMessage).
- Runbook + workspace naming conventions (one workspace per project, tabs per run).

**Rollback:** stop using it — tmux path untouched. **Risk:** low; blast radius = launcher
scripts + owner muscle memory.

### Phase B — herdr as the executor terminal runtime (replaces raw tmux in `supervisor.py`)

**B.0 — qualification gate first (peer-consult adoption).** Before any wiring: qualify ONE
pinned herdr binary in isolation and commit a **compatibility manifest** (binary version,
protocol version, `api schema` digest, required methods, config invariants). Hard-gate on:
exact argv-as-initial-process launch, caller-supplied env injection, PID identity parity,
enumeration/close, event-loss recovery via `session.snapshot`, and namespace shutdown at our
concurrency. **If argv-as-initial-process spawn is unavailable, executor adoption STOPS — text
injection (`pane run`) is not an acceptable substitute** (the F7-equivalent rule). Dispatch
preflight fails closed whenever the live binary/schema drifts from the qualified manifest.

**What:** a `TerminalRuntime` seam in `phase_executor` with two implementations — `tmux`
(today's code) and `herdr` (socket API). Launch children by argv into a dedicated herdr
**named session per run_id** (never mixed with the owner's console session — separate
lifecycle namespaces, separate config); identity from `pane.process_info` + the returned
opaque pane ID persisted on the JobRecord (labels via `pane.rename` are diagnostic only,
never recovery keys); liveness from `events.subscribe`/`agent.get` (replacing poll loops) —
**advisory only**: events may wake the supervisor, but every transition is revalidated against
JobRegistry identity, `/proc` state, and the observation sentinel before any state change.
Reap/quarantine via `pane.close` + retained worktrees exactly as today (tree-kill stays
`/proc`+`killpg`); recovery/adoption via `session.snapshot` reconciled against the JobRegistry,
with `resume_agents_on_restore = false` for executor sessions (ONE resume authority:
`classify_recovery`) — and a server restart is treated as process loss unless OS identity
proves otherwise (a restored layout is not a surviving child). The backend is chosen **once
per run and persisted** — no mixed-backend runs, extending the existing no-mixed-tier rule.

**Entry criteria (hard):** epic #475 closed (#449, #473, #474 landed) AND executor-hardening
#554–#558 landed AND the #559 proving run has passed **on the tmux runtime** — the swap needs a
proven baseline to A/B against, and a second migration must not race the #474 legacy flip.
**Exit criterion:** the #559-class proving cell re-run green on the herdr runtime with identical
Observation/reconcile results (requested==actual, zero anomalies), plus a soak window.

**Rollback (adversarial-review F1 adopted):** the backend flag affects **new runs only** — an
in-flight herdr run still needs the herdr-capable supervisor for recovery and cleanup. Rollback
= stop new herdr admissions → drain (or explicitly quarantine + terminate) all herdr runs →
verify no herdr namespaces remain → only then a code downgrade. JobRecord schema changes stay
backward-readable by both implementations; the drain sequence is part of HD2.5's crash/recovery
tests. **Risk:** medium — new dependency in the child-lifecycle path; bounded by the seam +
baseline A/B.

### Phase C — agent-skill integration + console UX

**What:** orchestrator sessions (which now run inside herdr panes) get the herdr skill
(HERDR_ENV-gated, adapted into the workspace skill library after WF5-style review of its
prose) so the orchestrator can spawn helper panes, read neighbors, and wait on output — e.g.
watching a child's live pane during a stall triage instead of `tmux capture-pane` archaeology.
Console UX hardening: dashboards, notification routing, possibly a driver-bench cell on the
herdr runtime.

**Rollback:** remove the skill. **Risk:** low; the skill is advisory tooling, gated on
`HERDR_ENV`.

---

## 4. Milestone/epic restructure proposal (Phase 4)

Follows the modernization-program pattern (epics M1–M4 #167–#170: one epic per milestone,
program label, epic bodies carry child slots). **Nothing here is filed — proposal only.**

### 4.1 Proposed epics (label: `epic:herdr`)

**Epic HD1 — herdr console adoption (Phase A).** Goal: herdr is the owner's daily console; epic
runs launch into it. Exit: one full epic auto-run driven and observed through herdr, runbook
committed. Children (sketches in §4.4): **HD1.0 no-code platform qualification (F6 adopted —
gates every other HD1 child)** · HD1.1 vet+pinned install + config baseline · HD1.2
integration install review (settings.json diff) · HD1.3 launcher herdr variant · HD1.4
blocked→notify-owner bridge · HD1.5 runbook + conventions.

**Epic HD2 — herdr executor runtime (Phase B).** Goal: executor children run in herdr panes
behind a runtime seam, tmux impl retained. Exit: #559-class proving cell green on herdr runtime;
soak passed. Entry: #475 closed, #554–#558 landed, #559 passed on tmux. Children: **HD2.0
qualification gate + compatibility manifest (hard NO-GO on missing argv-spawn/env-injection)** ·
HD2.1 TerminalRuntime seam (tmux impl extracted, no behavior change; preflight wired into
dispatch) · HD2.2 herdr runtime impl (launch/identity/capture/kill) · HD2.3 recovery/adoption
mapping (session.snapshot ↔ JobRegistry; native-resume off for executor sessions) · HD2.4
events-driven liveness (advisory, revalidated) · HD2.5 A/B validation + flip decision + a
**scheduled dual-backend proving fixture** so the tmux fallback cannot silently rot.

**Epic HD3 — herdr agent-skill + console UX (Phase C).** Goal: orchestrator uses herdr as a
tool; console UX complete. Children: HD3.1 adapted herdr skill (HERDR_ENV-gated) + review ·
HD3.2 stall-triage recipes (replace capture-pane archaeology) · HD3.3 bench cell on herdr
runtime · HD3.4 dashboard/notification UX polish.

### 4.2 Full backlog triage — all 46 open issues

Dispositions: **unchanged** (herdr doesn't touch it) · **re-scope** (survives, body/AC changes)
· **supersede/close** (moot — with evidence) · **absorb** (folds into an HD epic).

**Unchanged (37):** #554, #555, #556, #558 (engine/enforcement — herdr has no ledger, no canary,
no quota model); **#560 (the hardening epic itself — its queue and goal are untouched; it is
an HD2 entry criterion)**; #549 (Observation usage telemetry); #449, #473, #475
(executor-wiring remainder — must finish first, entry criteria for HD2); #394 (fallback-tier reviewer liveness —
prose for the Agent-tool tier; herdr gives the *executor* tier an analog, noted in body only if
touched anyway); #371 (Agent-tool worktree spawn — different dispatch layer); #365, #364, #363,
#362, #361, #356, #355, #350, #346, #345 (WF2/telemetry machinery); #391, #400 (session-index /
mining); #399, #395, #380, #379, #372, #370 (WF prose/hooks hardening); #360, #359, #358
(codex-design epic + WF15/WF16); #484 (bake-off config); #450 (ultracode interop — harness
Workflow tool, orthogonal runtime); #536, #535, #534 (skills hygiene).

**Re-scope (6):**
- **#557** (reconcilable failure Observations): core fix unchanged; add one sentence — herdr
  agent-state events MAY feed an *additional advisory* observation source post-HD2, never an
  Observation substitute.
- **#559** (proving run): runs on the tmux runtime as written — becomes HD2's entry criterion;
  add "re-run the cell on the herdr runtime" as HD2.5's exit evidence.
- **#474** (migration flip W12): add explicit sequencing sentence — the legacy→executor flip
  completes BEFORE any terminal-runtime swap begins (no two racing migrations).
- **#390** (workspace-doctor): add herdr checks — binary present + version == pinned, server
  reachable, `api schema` version matches the vendored pin.
- **#357** (Codex model pin): CLI-upgrade prerequisite is now SATISFIED — live probe this
  session: codex-cli 0.144.1, `codex exec -m gpt-5.6-sol` → MODEL-OK. Re-scope to the
  config-pin work only.
- **#537** (security-vet skill): this analysis is a manual worked example of the skill's flow
  (clone → verdict → risks → artifact → hardening epic) — cite it as the skill's fixture.

**Supersede/close (3 — housekeeping, NOT herdr-caused; verified this session via `gh`):**
- **#333** (dispatch-observability epic): all 10 children CLOSED (#329–#332, #338, #340–#344) —
  close as complete. Herdr's console is the *spiritual successor* to its observability goal, but
  the epic's own queue is done.
- **#457** (executor-spikes epic): all 5 children CLOSED (#452–#456; PRs #458–#462) — close as
  complete.
- **#408** (GLM follow-ups epic): all 4 children CLOSED (#407, #393, #405, #406) — close as
  complete.

**Absorb (0):** no existing issue folds into an HD epic — the herdr work is genuinely new
surface; existing issues keep their homes.

### 4.3 Sequencing

| Order | Epic/work | Entry | Exit |
|---|---|---|---|
| 1 (now) | finish epic #475 (#449 → #473 → #474) | running (paused) | epic closed |
| 1 (parallel) | **HD1 console** — independent of engine work | herdr vetted + pinned | epic run observed through herdr |
| 2 | epic #560 (#554–#558 then #559) | #475 done (per its own plan) | #559 proving run green on tmux |
| 3 | **HD2 executor runtime** | #475 + #560 done | proving cell green on herdr + soak |
| 4 | **HD3 skill + UX** | HD2 flipped (HD3.1 can start after HD1) | skill shipped + reviewed |
| any | housekeeping closes (#333, #457, #408) | owner nod | closed with completion comments |

### 4.4 New-issue sketches (titles + AC bullets — for owner review, then WF1)

- **HD1.0 `chore(console): platform qualification (no code changes)`** — exercise the pinned
  binary under the REAL cron environment and this host's permissions: headless server start,
  named-session routing, `agent start -- <argv>`, event-subscriber reconnect, notify-owner
  transport reachability; record surfaced errors + socket/config paths in a checked-in
  capability manifest. *AC: every Phase-A-assumed call proven live; failures block HD1.2–HD1.4,
  not just document them.*
- **HD1.1 `feat(console): vet + pinned herdr install + config baseline`** — checksummed
  release/source install (never `curl|sh`); `[update] manifest_check=false` (or documented
  exception); sounds/mouse defaults; version pin recorded; security-vet artifact linked. *AC:
  install reproducible from the runbook on a clean host; pin verified by workspace-doctor
  (post-#390 re-scope).*
- **HD1.2 `feat(console): Claude Code integration install, reviewed`** — capture the exact
  settings.json diff + hook script; verify wal-guard/mempalace hooks unperturbed; document
  uninstall. *AC: diff committed to the runbook; existing hook suite green after install.*
- **HD1.3 `feat(console): epic-launcher herdr variant`** — long-run-resume launcher gains a
  herdr mode (`herdr agent start`/`pane run` into a named workspace); cron→headless server
  proven; tmux mode retained. *AC: one real resume cycle through cron lands in a herdr pane;
  fallback documented.*
- **HD1.4 `feat(console): blocked→notify-owner bridge`** — `events.subscribe` watcher →
  iMessage on blocked-state transitions with debounce + dedup. *AC (F2 adopted — the heuristic
  can MISS a real prompt, emitting no event at all): drive representative real Claude and Codex
  approval/question screens through the pinned detection manifests and assert a notification
  within a deadline; watcher heartbeat + explicit warning/metric when disconnected or when a
  waiting process sits `idle`/`unknown` with no recognized transition; reconnect +
  snapshot-reconciliation tested, not just detach; notification bodies carry pane labels only,
  never pane contents (screen text can hold prompts/credentials).*
- **HD1.5 `docs(console): herdr runbook + workspace conventions`** — one workspace per project;
  tab conventions per run; attach/detach/remote recipes; known rough edges (idle CPU, key
  repeat). *AC: runbook committed; a second operator can drive a run from it.*
- **HD2.0 `chore(executor): herdr qualification gate + compatibility manifest`** — isolated
  pinned-binary qualification per Phase B.0; checked-in manifest (version, protocol,
  schema digest, required methods, config invariants: `resume_agents_on_restore=false`,
  `manifest_check=false`, `pane_history=false` for executor sessions); red-first probes for
  argv-spawn + env-injection; **NO-GO verdict stops the epic**.
- **HD2.1 `refactor(executor): TerminalRuntime seam — extract tmux impl`** — interface:
  preflight() / start(argv,env,cwd,label)→RuntimeLocator / inspect_identity() / alive() /
  enumerate() / terminate() / stop_namespace() / adopt(locator, expected_identity); tmux impl
  extracted with zero behavior change; **preflight wired into the live dispatch path** (closes
  the pre-existing gap, §2.1); full suite + proving fixtures green.
- **HD2.2 `feat(executor): herdr runtime impl`** — socket-API client (schema pinned via
  `api schema`); **launch/identity/enumeration/termination parity** (F7 adopted: `pane.read`
  and every screen-capture method are EXCLUDED from TerminalRuntime — screen access exists
  only in separate human-triage tooling, never the runtime contract); JobRecord field mapping
  documented. *First ACs: prove the two §2.1b unverified capabilities — (1)
  argv-as-initial-process spawn for a non-agent pane (never text-injection), (2) arbitrary
  per-pane env injection (or the documented `env(1)` wrapper fallback) — red-first, before any
  wiring.*
- **HD2.3 `feat(executor): recovery/adoption mapping`** — `session.snapshot` ↔ JobRegistry
  reconcile; `resume_agents_on_restore=false` for executor panes; classify_recovery outcomes
  preserved (adopt/relaunch/quarantine/fail) with red-first tests per state.
- **HD2.4 `feat(executor): events-driven liveness (advisory)`** — subscribe to agent/pane
  events as the poll-replacement; sentinel/exit-code enforcement unchanged (drift-guarded
  sentence); degraded mode = poll fallback.
- **HD2.5 `chore(executor): herdr runtime A/B + flip decision`** — #559-class cell re-run on
  herdr runtime; identical reconcile outcome required; crash/reap/recovery matrix (orchestrator
  crash, herdr server loss, client detach, stale pane, PID reuse, missed event, partial
  launch); soak window; flip config + rollback documented; scheduled dual-backend proving
  fixture keeps the tmux fallback tested until an explicit deprecation decision.
- **HD3.1 `feat(skills): herdr agent skill (adapted, HERDR_ENV-gated)`** — vendored/adapted
  from upstream SKILL.md after adversarial review of its prose; registration via add-skill.
- **HD3.2 `docs(skills): stall-triage recipes on the console`** — replace `tmux capture-pane`
  archaeology in runbooks with `pane.read`/`agent.explain` recipes.
- **HD3.3 `chore(bench): driver-bench cell on the herdr runtime`** — one live cell variant;
  compares runtime overhead vs tmux baseline.
- **HD3.4 `feat(console): dashboard/notification polish`** — deferred until HD1/HD2 experience
  accumulates; placeholder.

### 4.5 Consolidated owner-decision list

| # | Current title (short) | Proposed disposition | Rationale (one line) |
|---|---|---|---|
| #333 | dispatch-observability epic | CLOSE as complete | all 10 children merged/closed (verified via gh 2026-07-21) |
| #457 | executor-spikes epic | CLOSE as complete | all 5 spikes closed; PRs #458–#462 landed |
| #408 | GLM follow-ups epic | CLOSE as complete | all 4 children closed |
| #557 | H4 failure Observations | RE-SCOPE (one sentence) | herdr events = optional advisory source, post-HD2 |
| #559 | H6 proving run | RE-SCOPE (sequencing) | becomes HD2 entry criterion; herdr re-run = HD2.5 exit |
| #474 | W12 migration flip | RE-SCOPE (sequencing) | explicit "before any runtime swap" sentence |
| #390 | workspace-doctor | RE-SCOPE (add checks) | herdr binary/version/server/schema-pin checks |
| #357 | Codex gpt-5.6-sol pin | RE-SCOPE (shrink) | CLI prereq satisfied — probe MODEL-OK on 0.144.1 |
| #537 | security-vet skill | RE-SCOPE (cite fixture) | this analysis is the worked example |
| — | new epics HD1/HD2/HD3 | FILE (15 children) | §4.1/§4.4; HD1 can start now, HD2 gated |

---

## 5. Risk register

| Risk | Severity | Mitigation |
|---|---|---|
| **AGPL-3.0 license** | Medium (legal) | Confirmed posture: rawgentic invokes herdr as a separate process over CLI/socket — no linking, no vendoring, no distribution of herdr, no network service exposing herdr to third parties ⇒ no copyleft obligation attaches to rawgentic code in any proposed phase. Re-verify if ever bundling herdr in an image we ship. GitHub API shows `NOASSERTION` while the README badges AGPL-3.0 — LICENSE file text is the authority (34KB, AGPL-length; §1.6). |
| **Prerelease velocity / API drift** | High (ops) | Pin one release; vendor `herdr api schema --json` output; workspace-doctor check (#390 re-scope); runtime seam keeps tmux fallback. |
| **Heuristic agent state (claude/codex = screen manifests)** | High if misused | Hard rule in HD2.4 + drift-guarded sentence: herdr state is advisory; sentinels/exit codes/Observations remain enforcement evidence. |
| **Remote manifest auto-update from herdr.dev** | Medium (supply chain) | `[update] manifest_check = false`; local manifest overrides pinned in config dir. |
| **Two resume authorities** (herdr native restore vs classify_recovery) | High if unaddressed | `resume_agents_on_restore = false` for executor panes (HD2.3); executor recovery stays sole authority. |
| **Integration install edits `~/.claude/settings.json`** | Medium | HD1.2 diff-review before accept; verify wal-guard/mempalace hooks intact. |
| **Single-maintainer bus factor** | Medium | Seam + tmux fallback = exit ramp; AGPL guarantees forkability. |
| **tmux fallback rots once herdr is default** | Medium | Scheduled dual-backend proving fixture (HD2.5) until explicit deprecation — an untested fallback is not a rollback strategy. |
| **Missed socket events across disconnects** | Medium | Reconnect always starts `session.snapshot` + registry reconciliation; polling remains the degraded fallback (HD2.4). |
| **Idle CPU / TUI quirks** | Low | Known (madflex); server headless for unattended runs; acceptable for console use. |

---

## 6. Assumptions needing owner clarification + open uncertainties

Every item below is an **assumption this plan is built on** — each states what was assumed,
why, and what would confirm or overturn it. The first five need an explicit owner answer;
the rest are technical uncertainties with a named home.

### 6.1 Assumptions to clarify with the owner

1. **"Main console application" = observation + terminal-control surface, NOT the
   orchestrator.** This plan keeps rawgentic as the sole workflow/orchestration owner (gates,
   routing, audit, epic driver) and puts herdr underneath/around it as the terminal runtime and
   human console. If the intent was for herdr to *drive* work (herdr-native orchestration,
   agents coordinating via the herdr skill as the primary mechanism), the phasing and epic
   structure change materially. **Assumed the former** — confirm.
2. **Where the console lives.** Assumed: herdr server runs on THIS workspace host (where the
   runs execute), owner attaches locally / via `herdr --remote` from elsewhere (laptop, phone
   over ssh). Alternative (server on another box) breaks Phase A's launcher integration.
3. **Adoption depth is decided per-phase, not upfront.** Assumed the owner wants the option to
   stop at console-only (HD1) if daily use disappoints — HD2's engineering cost (seam +
   qualification + A/B) is only committed after HD1 proves value AND #475/#560 land. If the
   owner already knows they want the full executor swap, HD2.0/HD2.1 can be scheduled earlier
   (still behind the same entry criteria).
4. **Run priority.** Assumed epic #475 (paused) and hardening #560 keep queue priority; herdr
   work (HD1) slots in as parallel, non-preempting work. Confirm before HD1 consumes run time.
5. **License posture sign-off.** §5's AGPL analysis (separate-process invocation ⇒ no copyleft
   obligation) is careful reading of the license text plus the project's own dual-license
   framing — it is NOT legal counsel. Assumed acceptable for internal tooling use; the
   commercial-license contact exists if the owner wants belt-and-suspenders.
6. **Same-user trust boundary is acceptable.** The herdr socket is 0o600, same-user-full-
   control (§1.1) — identical to today's tmux private socket posture. Assumed fine; if the
   host ever runs mixed-trust processes under this account, revisit (least-privilege service
   account is the named mitigation).

### 6.2 Technical uncertainties (each with its home)

- **Argv-as-initial-process spawn for generic panes** — confirmed for `agent start` kinds;
  unverified for arbitrary non-agent commands. Load-bearing (F4/F7); NO-GO criterion.
  → HD1.0 (launcher path) / HD2.0 (executor path).
- **Per-pane env injection** (PYTHONPATH, CLAUDE_CONFIG_DIR) — unverified; `env(1)` wrapper is
  the fallback. → HD2.0.
- **`resume_agents_on_restore` scope** — docs say named sessions share the global config file,
  so per-session scoping likely requires a separate config root (§1.4). → HD2.0 hard test.
- **`pane.process_info` field parity** with tmux's pane_pid (+ our /proc-derived pgid/
  start-time) — method exists, fields unexercised. → HD2.2.
- **Socket latency/stability at our concurrency** (≤3 children + orchestrator, long sessions)
  and idle CPU with many animated panes — unmeasured. → HD1.0 / HD2.5 soak.
- **`session.snapshot` completeness** on large sessions (>20 panes). → HD2.3.
- **Blocked-detection miss rate** on our real approval screens (strict manifests fall back to
  `idle`) — bounded by HD1.4's real-screen AC, never enforcement-relevant by design.
- **Upstream skill/CLI drift cadence** (the `herdr wait` drift in §1.2 is live evidence) —
  bounded by pinning + `api schema` digest checks; re-validate on every pin bump.

## 6b. What was NOT checked

- Herdr was **not built or executed** — all findings are from reading the repo, official docs,
  and third-party reviews. Hands-on latency/stability of the socket API under our concurrency
  (≤3 children + orchestrator) is unmeasured → HD1.1/HD2.2 carry it.
- The exact JobRecord↔pane identity mapping (pane pid/pgid/start-time parity with tmux's
  `pane_start_time`) is asserted from `pane.process_info`'s existence, not exercised → HD2.2.
- **Argv-as-initial-process spawn for a non-agent pane**: `agent start … -- <argv>` is
  confirmed for agent kinds; whether a generic pane can be created with an arbitrary argv as
  its initial process (rather than `pane run` text-injection into a shell) is unverified —
  load-bearing for the F7-equivalent rule (no keystroke injection on the launch path) → HD2.2
  first AC.
- **Arbitrary per-pane env injection** (PYTHONPATH, CLAUDE_CONFIG_DIR): herdr injects its own
  `HERDR_*` context; caller-supplied env unverified (fallback exists: `env(1)` wrapper) →
  HD2.2 first AC.
- `session.snapshot` completeness against a large (>20 pane) session.
- Windows beta quality (irrelevant to this Linux workspace).
- The herdr plugin marketplace's individual plugins (none proposed for adoption).

## 7. Review trail

- **Cross-model thought partner (done):** gpt-5.6-sol via the engine consult CLI
  (`adversarial_review_lib.py consult`, `RAWGENTIC_ADV_REVIEW_MODEL=gpt-5.6-sol`,
  codex-cli 0.144.1) — report:
  `docs/reviews/peer-2026-07-21-herdr-console-plan-consult.md` (run-local: `docs/reviews/` is
  gitignored by owner decision 2026-07-20, #548 — the adopted content is summarized here in
  full). Peer proposal converged on
  the same architecture (console/backend separation, seam, advisory-only state, single
  resume authority). Adopted from it: the B.0 qualification gate + compatibility manifest
  with hard NO-GO on missing argv-spawn; never-mix console/executor namespaces;
  backend-per-run persisted; the scheduled dual-backend proving fixture; notification
  redaction; server-restart-is-process-loss framing. Not adopted: none rejected —
  the proposal contained no conflicting recommendation.
- **WF5 adversarial review (done):** gpt-5.6-sol (reasoning high) via
  `adversarial_review_lib.py review --type design` — report:
  `docs/reviews/2026-07-21-herdr-console-plan-md-2026-07-21.md` (run-local per #548; all
  findings restated below). 7 findings
  (0 Critical / 4 High / 3 Medium), **all 7 verified real and ADOPTED**:
  F1 rollback = drain-then-downgrade, never a flag flip (Phase B + HD2.5) ·
  F2 HD1.4 AC now drives real approval screens + heartbeat (a heuristic miss emits no
  event) · F3 resume-scope assumption replaced with evidence (global config file ⇒
  separate config root) + HD2.0 hard test · F4 `pane run` removed from the launcher path
  (argv-only) · F5 missing 46th issue found — it was epic #560 itself, now dispositioned
  unchanged · F6 new HD1.0 no-code platform qualification gates Phase A · F7 "capture"
  dropped from HD2.2 (screen methods excluded from TerminalRuntime).
- Visual dashboard artifact (published 2026-07-21):
  https://claude.ai/code/artifact/e5e51d5b-7481-4ef1-b1c3-f5df3f944c19 — source of truth is
  the committed `docs/planning/2026-07-21-herdr-console-dashboard.html`.
