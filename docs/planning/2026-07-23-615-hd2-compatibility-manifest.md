# #615 (HD2.0) — herdr executor qualification gate — compatibility manifest

Isolated pinned-binary qualification per Phase B.0 (plan §2.1b/§4.4). No runtime code (HD2.2
scope) — probes + manifest only. **Verdict: NO-GO.** Per AC3 and epic #621's own owner-baked-in
decision ("HD2.0 NO-GO verdict stops this epic"), this result stops epic #621 — recorded on the
epic, not softened.

## Compatibility manifest (AC1)

| Field | Value |
|---|---|
| Binary version | `herdr 0.7.5` |
| Protocol version | `17` |
| Schema digest (sha256 of `herdr api schema --json` stdout) | `1ef4eb9ec655cb0c89726895f437d8654bdde13a22e591fda06a9015d03d88c7` |
| Required methods (socket API surface, full set) | 89 methods across `agent.*`, `client.window_title.*`, `events.*`, `integration.*`, `layout.*`, `notification.*`, `pane.*`, `plugin.*`, `popup.*`, `server.*`, `session.*`, `tab.*`, `workspace.*`, `worktree.*` — full enumeration in `herdr api schema --json`; anti-drift anchor per plan §1 ("the installed binary prints its own versioned protocol schema... the anti-drift anchor any integration should pin against") |
| Config invariants required for future executor sessions (HD2.2, not yet wired — recorded here for that implementer) | `resume_agents_on_restore=false`, `manifest_check=false`, `pane_history=false` |

Socket framing (confirmed live, relevant to any future client): JSON request/response,
newline-delimited, **one request per connection** — a second `send` on an already-answered
connection gets `BrokenPipeError`. `events.subscribe` is the one method meant to keep its
connection open afterward for the push stream (see #608's manifest for the full reconnect
finding).

## AC2 — Red-first probes

### Env-injection: **VERIFIED** (citing 2026-07-23 filing evidence + a fresh live re-confirmation this run)

`pane split --pane <id> --direction right --env KEY=VALUE` genuinely injects a real environment
variable into the launched pane's shell process — re-confirmed live:
```
herdr pane split --pane w1:pT --direction right --env HD2_ENV_PROBE=confirmed-live --no-focus
→ new pane w1:pV
(inside w1:pV) $ echo HD2_ENV_PROBE=[$HD2_ENV_PROBE]
HD2_ENV_PROBE=[confirmed-live]
```

### Argv-as-initial-process spawn for a generic (non-agent) pane: **NO-GO — fresh live probe**

**Structural finding (from every creation-time CLI surface):** `pane split`, `tab create`, and
`workspace create` accept only `--cwd`/`--env`/`--label`/`--focus` — none accepts an argv/command
override. Every new pane's initial process is always the configured default shell
(`$SHELL`/`/bin/sh`). The only two "run a command" mechanisms are `pane run <PANE_ID>
<COMMAND>...` (generic) and `agent start --kind <fixed-enum> --pane <ID>` (agent-kind-scoped,
already proven live for #608 AC3) — **both explicitly require an existing pane already at its
interactive shell prompt** (`agent start --help`: "The pane must be at its interactive shell
prompt"). Neither is a creation-time argv substitution.

**Empirical confirmation — this pane's own scrollback, read directly after `pane run`:**
```
rocky00717@claude-code:~/rawgentic$ touch /tmp/.../argv-test-marker.txt
rocky00717@claude-code:~/rawgentic$
```
The command appears as literal typed text at an already-live shell prompt, followed by the
prompt reappearing — this is keystroke-style text injection into a persistent shell process,
**not** a replacement of the pane's controlling/initial process. `herdr pane process-info`
confirms the same `shell_pid` (bash) before and after the command runs.

This is the exact behavior the plan flagged as the risk for 0.7.4's `pane run` ("text+Enter"),
now confirmed unchanged on the currently pinned 0.7.5. Per plan §2.1b's F7-equivalent rule ("If
argv-as-initial-process spawn is unavailable, executor adoption STOPS — text injection (`pane
run`) is not an acceptable substitute"), this is a hard NO-GO on the one required capability
Phase B's TerminalRuntime seam depends on for a clean `start(argv, env, cwd, label)` primitive.

## AC3 — Gate outcome

**NO-GO.** Per the issue's own AC3 and epic #621's owner-baked-in decision, this STOPS epic #621
(the herdr executor runtime, Phase B) — HD2.1 (`TerminalRuntime` seam extraction) and HD2.2
(herdr runtime impl) do not proceed on the current 0.7.5 binary. Not softened: the env-injection
half being verified does not offset the missing argv-spawn primitive, which the plan treats as
the harder, non-negotiable requirement.

Nothing here blocks HD1 (console qualification, #608 and its children) — HD1 is explicitly
independent of the executor-runtime swap per plan §4.3's sequencing table ("HD1 console —
independent of engine work"). This NO-GO is scoped to epic #621 only.

## What was NOT checked

- Whether a future herdr release adds an explicit argv-as-initial-process flag (out of scope —
  this qualifies the currently pinned 0.7.5 binary only; re-qualification is required on any
  version bump per the plan's own anti-drift anchor).
- Any HD2.2 runtime-implementation work — explicitly out of this issue's scope.
