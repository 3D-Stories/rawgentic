# The context meter (`hooks/context_meter.py`)

A session that runs out of context mid-task loses everything not on disk. Noticing the wall before
you hit it used to be model judgment — and it "works sometimes, but not reliable at all" (owner,
2026-07-28). This hook removes the judgment: **a hook cannot forget.**

Shipped by #687 (epic #684). Design and full rationale:
`docs/planning/2026-07-28-687-context-pressure-trigger.md`.

## What it does

Every so often (see **Cadence**) it reads the session's own transcript, computes how many tokens are
in context, divides by the context window, and — if that fraction crosses a threshold — injects a
short advisory into the session's next turn. Each tier fires **at most once per session per effective
window**.

| Tier | Default | What the message says |
|---|---|---|
| advisory | **35%** | Write the resume prompt **now**, while there is room to write a good one, and verify the delivery gates. Do not stop mid-phase. |
| directive | **50%** | Break **now**, at the next turn, seam or no seam — here is what to capture, and run `/rawgentic:pane-handoff` to actually hand over. |

Both tiers also carry one standing fact (#713): **a handoff SATISFIES a loop goal.** The work
continues in a fresh session with a full window, so handing off does not stop the work — it
relocates it. That line exists because a real run read its own `/goal` condition ("LOOP until DONE —
do not stop") as "do not hand off", and burned a 1M window to ~98% instead of passing the work on.
Nothing in the harness had told it otherwise. It is phrased as a fact rather than an order on
purpose: text injected by a hook is treated as **data**, and a measured probe showed a model
correctly refusing an injected imperative while still reading the state it carried.

It never runs `/clear` itself and never blocks a turn. It tells the session; the session acts.

## Why it is registered on three events

`UserPromptSubmit` fires once per **user prompt**. In a long autonomous run — an epic auto-run, a
an unattended run — the operator sends one prompt and the session then works for hours. A
`UserPromptSubmit`-only meter would evaluate once, on an empty context, and never again: silently
dead in exactly the runs that need it most.

So the two cadence arms ride different events:

| Arm | Event | Case it covers |
|---|---|---|
| 5 **turns** | `UserPromptSubmit` (the only place the turn counter increments) | interactive back-and-forth |
| 5 **minutes** | `PostToolUse` (and `UserPromptSubmit`) | autonomous runs, where events are plentiful and prompts are not |
| the **decision point** | `Stop` | a `/goal` loop, where the re-prompt is decided at `Stop` and a mid-turn reading is already stale |

When the cadence has not elapsed, the hook reads one small JSON file, compares two integers and
exits — it does **not** open the transcript. That matters, because it rides every tool call.

### The `Stop` arm is deliberately narrower than the other two (#713)

`/goal` is itself a session-scoped prompt-based `Stop` hook, so `Stop` is exactly where the decision
to keep going is made. A mid-turn reading cannot be present at that moment; in the run that produced
#713 the `Stop` hook fired ~10 times and not one firing carried a handoff instruction.

But at `Stop` **every channel that reaches the model also continues the turn** — both
`decision: "block"` and `hookSpecificOutput.additionalContext` keep the conversation going, and
silence is the only way to let a turn end (measured: a canary emitted at `Stop` produced a second
assistant turn for a one-prompt session). So this arm is gated three ways:

| Gate | Why |
|---|---|
| `stop_hook_active` must be **true** | True means a hook-driven loop is already continuing, so speaking costs no turn it was not already taking. False means the session is handing control back to a human, and an emission would force a turn nobody asked for. |
| **directive tier only** | At the check-in tier a forced turn buys nothing; at the act tier the extra turn *is* the handoff. |
| exempt from the **cadence throttle** | `Stop` fires once per turn, so it is already cheap — and being throttled at the decision point is the exact mistiming this arm exists to fix. |

Residual cost, stated rather than designed away: if `/goal`'s evaluator decides the goal is *met* on
the same firing where this arm first delivers, the meter's own output becomes the sole continuation
and forces **one** extra turn. It is bounded to at most one per session per effective window, it can
only happen above the act threshold in a session that was already looping, and the turn it forces
carries the handoff instruction — which at that point is the wanted behaviour.

Deliveries are recorded per **channel** (`midturn` for the two mid-turn events, `stop` for this one),
so the mid-turn arm speaking at the act threshold does not silence the decision-point delivery. Each tier is
therefore delivered at most once mid-turn and at most once at `Stop`.

## The honest limit on the 5-minute arm

Hooks fire on **events**, not on a timer. With no turn and no tool call, nothing runs — so the
5-minute arm is *evaluated at the next event*, not at the 5-minute mark. A session idle for an hour
notices at its next event. Riding `PostToolUse` shrinks that gap to "the next tool call", which in a
working session is seconds, but it does not remove it.

A true wall-clock trigger would need an external timer (crontab/systemd) writing a sentinel the hook
reads. Deliberately not shipped: it needs a crontab write (which the permission classifier has denied
before), and a nag that arrives while nobody is looking at the screen changes nothing.

## Configuration

Five keys under a `contextMeter` object in the bound project's `.rawgentic.json`. Each has an env
twin that takes precedence. Precedence is **env → project config → default**, per key.

```json
"contextMeter": {
  "windowSize": 1000000,
  "checkInPercent": 35,
  "actPercent": 50,
  "everyTurns": 5,
  "everySeconds": 300
}
```

| Key | Env twin | Default | Notes |
|---|---|---|---|
| `windowSize` | `RAWGENTIC_CONTEXT_WINDOW` | `200000` | tokens; see **Window size** |
| `checkInPercent` | `RAWGENTIC_CONTEXT_CHECKIN_PCT` | `35` | must be ≥10 below `actPercent` |
| `actPercent` | `RAWGENTIC_CONTEXT_ACT_PCT` | `50` | 1..99 |
| `everyTurns` | `RAWGENTIC_CONTEXT_EVERY_TURNS` | `5` | ≥1 |
| `everySeconds` | `RAWGENTIC_CONTEXT_EVERY_SECONDS` | `300` | ≥1 |

Any malformed, out-of-range, inverted, or squeezed value falls back to the documented default with a
stderr warning; a bad config never breaks a turn. There is deliberately **no workspace-level layer** —
a third precedence tier for five integers has no caller the env twin does not already serve.

## Window size — the one thing you may need to set

**A session's model name does not reveal its context window.** A 1M-window session records
`message.model: "claude-opus-5"` with no `[1m]` marker, so the window cannot be detected. It is
declared, and the default is the conservative **200,000**.

Why the conservative default rather than 1M: the two errors are not symmetric. Assuming 1M on a 200k
session means the nag **never fires** — a silent failure, the exact thing this hook exists to end.
Assuming 200k on a 1M session means it fires early: at most two messages, each naming the assumption
and how to fix it.

**It self-corrects.** A window a session has already exceeded is provably wrong, so once the observed
in-context total passes the assumed window the meter escalates to the next known tier (1,000,000) and
reports the provenance as `escalated`. A 1M session is therefore only mis-scaled below 200k, where it
is genuinely fine — and tiers recorded against the outgrown window are discarded, so a premature
warning cannot suppress the real one later.

If you run 1M-window sessions, set `windowSize` and skip all of that.

## Thresholds: what is measured, and what is not

`docs/planning/2026-07-28-687-probes/compaction_scan.py` scans every transcript on the host for the
in-context ceiling. **On a 1M window, sampled sessions reach 99.5–100% before anything resets them**
(highest observed: 999,803 tokens = 100.0%, across 266 transcripts). So the 50% directive has roughly
50 points of margin there. This answers the 1M half of #654's Q4.

**The 200k window is NOT measured** — this corpus contains zero 200k-window sessions, so there is
nothing to scan. Given the 1M result (Claude Code compacts when nearly full, not at three-quarters), a
50% directive is very likely safe on 200k too, but "very likely" is the honest word. **If you run a
200k-window model, take one reading:** run the scan on a session that has been compacted and set
`actPercent` below the fraction at which its in-context total dropped.

### Why the thresholds are well below the compaction ceiling (#716)

The original pair was **60/70**, sized against the ceiling above. It was safe, and it was still too
late to be *useful* — which is a different test, and the one that matters.

**Surviving until compaction was never the binding constraint. Having room to hand over well is.**
A real run (#713) took the directive at 70% of a 1M window, meaning 300,000 tokens left, and rode to
~98% anyway; the quality of its work degraded in step with the pressure, and its final task was
abandoned rather than done badly. At 35% of the same window a session has ~650,000 tokens in hand —
enough to finish the phase it is in, write a resume prompt worth reading, and verify the handoff
landed.

So the defaults are deliberately **early, not safe**: 35% to start writing, 50% to go. Compaction
margin is a floor these must clear, never the number they are set to.

## Choosing when to break — the seam

Crossing the advisory threshold starts a **search** for a good moment, it does not stop the session.
The signal is the step-state pointer (`hooks/step_state.py`), which records the machine-readable
current workflow position. Crossing the threshold snapshots that pointer; a **seam candidate** is when
a later check sees the pointer has *moved* — the workflow just entered a new step, so the instruction
lands before substantive work on it begins.

It is called a **candidate**, not "safe", on purpose. A pointer transition proves the recorded step
changed. It does **not** prove your tree is committed, that no review wave is outstanding, or that the
boundary was seen before work began. Only the session can confirm those, so the advisory asks it to.

| Pointer state | Behaviour |
|---|---|
| moved since the threshold was crossed | seam candidate — the advisory fires and says a boundary was recorded (it does NOT name the step: the pointer is a project-controlled file, so nothing from it is echoed into the model's context) |
| unchanged | wait; the advisory holds until the pointer moves or the directive tier arrives |
| no pointer at all (an ordinary, non-workflow session) | nothing to wait for — the advisory fires immediately |

**The directive tier never waits for a seam.** If no seam arrives, it fires anyway and says to accept a
mid-phase break, naming what to capture: branch + commit, the recorded test baseline, the current step
marker, the loop-back counters. A seam rule that could defer forever would be the same silent failure
in better clothes.

## Attended vs unattended

Three independent facts, because conflating them sends a session at a command that will refuse it:

| Value | True when | Effect on the message |
|---|---|---|
| `unattended` | the DECLARED supervision state says `away` or `sleeping` (#943, replacing the retired env var): no human to ask, so it says "checkpoint and write the handoff" |
| `fresh_handoff_capable` | **both** `RAWGENTIC_LAUNCHER_ARMED=1` **and** `RAWGENTIC_FRESH_LAUNCH_SUPPORTED=1` | and only then does it name `launcher_lib.py handoff` as the route |
| `herdr_available` | `HERDR_ENV=1` | a sibling pane can be spawned, so an unattended session without a launcher is routed to `/rawgentic:pane-handoff` instead of stop-and-wait (#732) |

Nothing is inferred. The only armed-launcher signal in the tree is a caller assertion
(`hooks/launcher_lib.py:2440`), and `launcher_lib.py:2156` is explicit that absence must not read as
support. A launcher that can relaunch says so. Routing precedence for the unattended case: an armed,
fresh-launch-capable launcher first (`launcher_lib.py handoff`); otherwise `pane-handoff` when
`HERDR_ENV=1`; otherwise — genuinely nothing to spawn into — `clear-prep` plus a manual-resume
instruction.

## What the handover reuses, and the one gap

The message points at machinery that already exists; #687 built no handoff of its own. `clear-prep`
writes the mempalace checkpoint, the durable handoff file, the resume prompt and the `/goal` text;
`perform_handoff` spawns and verifies a successor and tears the predecessor down last; the
`project_switched` step binds it (bind-first since #682).

**The gap, stated rather than implied:** the harness task list is session-scoped, so nothing survives a
process boundary. What actually crosses is the handoff file's `next actions, in order` list, which the
successor re-derives into a task list via `/tasklist` (`clear-prep` §3 and §5). That is
**re-derivation, not transfer** — task identity, status and order are not preserved.
Identity-preserving transfer is a filed follow-up needing a writer in `clear-prep` and a consumer in
the resume-prompt contract; the canonical representation is fixed in the design doc so that work has a
target.

## Failure behaviour

**Fail-open** — an absent, unreadable or malformed transcript, an unwritable state directory, a bad
config value, or any unexpected exception means *emit nothing, exit 0*. This is a convenience nag, not
a security boundary.

**But fail-open is not the same as invisible.** A meter that silently disables itself recreates the
exact failure class it exists to end, so three outcomes emit a **once-per-session stderr diagnostic**
while still exiting 0: transcript not resolvable, an ambiguous transcript match, and no parseable
usage row.

To check the meter by hand:

```bash
python3 hooks/context_meter.py read --session-id "$CLAUDE_CODE_SESSION_ID"
# {"fraction": 0.159, "provenance": "default", "tier": "none", "used": 159416, "window": 1000000, ...}
```

It exits **3** when no usage row parses — so a platform format change is one command away from being
visible, rather than manifesting as a meter that quietly never fires.

## State

`~/.rawgentic/context-meter/<session-id>.json`, mode `0600`, in a directory created `0700` (the parent
`~/.rawgentic` is `0775`, so a plain `mkdir` would not be private). Written atomically via
`atomic_write_lib`. Files older than 7 days are swept on write.

The JSON holds **cadence bookkeeping only**. The once-per-tier record is a separate marker file
created with `O_CREAT|O_EXCL` — a filesystem compare-and-swap, because parallel tool calls fire
concurrent `PostToolUse` hooks and two processes could otherwise both decide to warn. The marker is
created immediately before the message is written and **released if delivery fails**, so a failure
cannot silence a tier for the rest of the session.

## Subagents

A subagent invocation can carry the parent's `session_id`. The meter therefore does **nothing** when
the payload identifies a subagent or sidechain: a subagent has its own short-lived context and no
authority to hand over its parent's session. The check looks for several plausible marker keys and is
inert when none is present — no subagent payload was captured during the probes, so the exact field
name is unverified and the guard is written not to depend on it.

## Optional addendum: the statusline bridge (not shipped)

Claude Code hands the **statusline** command a `context_window` object with `used_percentage` already
calculated — no transcript reading, no window guessing. It is the better source where it exists, and
it is deliberately not shipped: `~/.claude/rawgentic-statusline.sh` is a user-level file outside any
git repository, so it cannot ship as a tested rawgentic PR, and it renders nothing headless. If you
want it, have your statusline script persist `context_window.used_percentage` and `session_id` to a
file; a future consumer could prefer that reading over the transcript one.

## Known limits, named rather than implied

Three things this deliberately does not claim, each with the follow-up that would close it:

1. **A residual TOCTOU window on the state directory.** The symlink and containment checks run
   before any mutation, and `~/.rawgentic/context-meter/` is created `0700` — but the checks are
   pathname-based, so a local attacker who can win a race inside the user's own `~/.rawgentic`
   (which is `0775` on this host, created by other tooling) could swap a directory between the
   check and the write. Closing it fully means holding verified directory descriptors and doing
   `mkdir`/`open`/`rename` relative to them; that is a filed follow-up. The exposure is a
   convenience nag's state file, and the attacker already needs local group access.
2. **No single pure `evaluate()`.** The decision helpers are pure and individually tested, but
   `cmd_hook` sequences them alongside the reads and writes, so ordering and trust-boundary
   behaviour cannot be unit-tested independently of the filesystem. Extracting a total pure
   evaluation over already-validated facts is a follow-up — it refactors the code the security
   fixes reshaped, and doing both at once is how a fix gets lost.
3. **The registry scan is bounded at 8 MiB** (~55k rows) from the end. A session whose registry row
   lies beyond that is indistinguishable from an unbound session, so the meter falls back to the
   conservative default window and **says so** via a once-per-session diagnostic. It does not
   silently pretend to have read a project's config.

Also worth knowing: a marker file whose session state file is missing ages out after 7 days, so a
session that resumed after a week-long gap could in principle be nagged twice for one tier.
