# #633 — herdr build-seat launch backend investigation — design doc

Investigation-shaped (issue's own framing): gathering data, no runtime code changes.
**Verdict: GO** for an alternate/additional `build`-seat launch backend on `herdr pane run
<pane> exec <argv>`, with the primitive mapping and context-reuse policy below. All four
gating ACs (1-4) resolved; AC5 (terminal action) is the follow-up implementation issue filed
against this doc's findings.

## AC1 — PID-identity qualification: GO (40/40)

Protocol fixed before running (`docs/planning` companion scratch — see Related below), against
pinned `herdr 0.7.5`. Worker: a python sentinel that writes `observation.json` twice (`started`
then `completed`, with its own `os.getpid()`) instead of `sleep` — closer to `pane_runner`'s
real shape.

| Condition | Reps | Passed | Failures |
|---|---|---|---|
| cold (fresh-split pane, immediate exec) | 20 | 20 | 0 |
| reused (pane warmed by one plain non-exec command first, then exec) | 20 | 20 | 0 |

Success criteria per rep: shell PID before `exec` == PID inside the sentinel == PID at
mid-run check; `cmdline` changed from `/bin/bash` to the sentinel invocation; sentinel reached
`phase: completed, exit_code: 0` on disk. GO threshold (fixed before running): 0/20 failures
in both conditions. Both conditions hit that exactly — no HYBRID/NO-GO branch triggered.

**New finding, not in the original live demo:** the pane auto-closes the instant the exec'd
process exits (normal exit or external kill) — there is no shell left once `exec` replaces it,
so nothing survives to keep the pane open. Confirmed both for a clean `sys.exit(0)` and for an
external `SIGTERM` to the process group. Cross-checked against `supervisor.py`: this matches
its EXISTING tmux design exactly — `new-session -d -s <name> -c <cwd> -- <argv>` already runs
`argv` directly as the session's sole process (no shell wrapper either), and completion status
is read from `read_sentinel()` (`supervisor.py:262`, disk-based `observation.json` under
`record.capture_dir`) rather than from tmux/herdr state. So the auto-close-on-exit behavior is
not a new constraint the herdr backend introduces — it is already how the tmux backend behaves
today; supervisor.py never depended on the session outliving the worker.

"Reused-pane (reused slot)" reading: a pane cannot host two sequential `exec`s (the first exit
kills the pane) so this condition means prior non-exec shell activity in the same pane before
the one exec call, not two execs in one pane. Documented as the deliberate interpretation, not
an evasion of the AC's intent — the alternative reading is physically impossible given the
first finding.

## Primitive selection: `pane run <pane> exec <argv>` (raw), not `agent start`/`agent prompt`

`agent start --kind claude|codex|pi|opencode|omp` and its status detection (`agent_status`:
idle/working/blocked/done, `wait agent-status`) are built for interactive coding-agent TUIs.
The `build`-seat worker is `python -m phase_executor.pane_runner <spec> <digest>` — a headless
batch subprocess, not one of herdr's recognized agent kinds; `agent_status` on every pane we
launched this way read `unknown` throughout, confirming herdr's agent-detection layer doesn't
even attempt to classify it. AC1 already qualified the raw-`exec` primitive directly (40/40).
There is no reason to route through the agent subsystem for this seat.

## AC2 — `agent_prompt_stalled` workaround: **N/A**

Per the issue's own conditional framing (load-bearing only if `agent start`/`agent prompt` is
selected). It is not — see primitive selection above. No further testing needed; citing this
decision satisfies the AC.

## AC3 — executable tmux -> herdr primitive mapping

All five mappings executed live (not paper-only) against pinned `herdr 0.7.5`, end-to-end
launch -> identify -> enumerate -> liveness-check -> terminate, using the AC1 sentinel workers.

| `supervisor.py` tmux call | herdr equivalent | Executable result |
|---|---|---|
| `new-session -d -s NAME -c CWD -- ARGV` | `pane split` (create) + `pane run PANE "exec ARGV"` (launch) | Works, but is TWO calls where tmux has ONE atomic call — see risk below |
| `display-message -p -t NAME "#{pane_pid}"` | `pane process-info --pane PANE` -> `result.process_info.shell_pid` | Confirmed stable pre- and post-exec across all 40 AC1 reps |
| `has-session -t NAME` | `pane get --pane PANE` (or `process-info`): success = alive, `{"error":{"code":"pane_not_found"}}` = dead | Tested live pane (success) and gone pane (clean structured error, exit 1) |
| `list-sessions -F "#{session_name}"` | `pane list --workspace WORKSPACE_ID`, enumerate `pane_id` | Confirmed; scoped to one workspace — the supervisor's own control-plane instance operates within exactly one, same scope as its own single tmux-server/registry-root today, not a gap |
| `kill-session -t NAME` | `pane close PANE` | **Confirmed it actually terminates the live underlying process** (not just a UI/bookkeeping close) — tested against a still-running 120s-sleep worker; process gone within 1s. Idempotent: calling it again, or against a never-existed pane ID, returns the same clean `pane_not_found` error both times — compatible with `supervisor.py`'s existing swallow-and-continue teardown pattern (`except Exception: pass` around its own `kill-session` calls) |

**Kill semantics finding, load-bearing for design:** `supervisor.py`'s real termination path
(`os.killpg(record.pane_pgid, SIGTERM/SIGKILL)`, `supervisor.py:708-738`) does not depend on
tmux's `kill-session` to actually stop the worker — `kill-session` there is only post-mortem
bookkeeping, called after the process group is already confirmed dead by PID-level checks. The
herdr backend inherits this for free: the AC1 finding that a pane auto-closes on ANY process
exit (normal or killed) means the pane already reaps itself once `killpg` does its job — a
supervisor built on herdr does not need to call `pane close` in the kill path at all. `pane
close` remains available as a direct backstop kill (confirmed above) if ever needed.

**Status/completion source is unaffected by primitive choice.** `read_sentinel()`
(`supervisor.py:262`) reads `observation.json` from `record.capture_dir` on disk, independent
of tmux or herdr entirely — this does not change under either backend.

**Known herdr liveness gaps do not carry over.** The issue asked to fold in `ogulcancelik/herdr`
#198 (agent-status can latch on `working`), #1435 (`not_implemented` on CLI/server protocol
mismatch, fixed in 0.7.5), #1439 (`wait agent-status` hangs on mid-wait pane close, fixed in
0.7.5) as known risk. All three are specific to the `agent_status`/`wait agent-status`
subsystem. This design's liveness check is pane-existence via `pane get`/`process-info`, not
agent-status — a deliberately different code path in herdr, unaffected by any of the three. Not
a dismissal of the research (the citation is fair per herdr's own tracker), just a scope note:
none of the three are inherited risk for this specific mapping.

**Named risk (real, not blocking):** the create+launch split (two herdr calls vs tmux's one
atomic call) opens a window between `pane split` and `pane run` where nothing else is racing it
today — the calling orchestrator is the only actor touching that pane ID. Safe as long as this
stays single-threaded per spawn (don't parallelize split+run against the same target pane from
two callers); worth a comment at the call site if this ships.

## AC4 — GO/NO-GO/hybrid design decision

**Pane lifecycle — spawn timing: lazy.** Spawn a pane on a seat's first actual dispatch in the
run, not pre-spawn-all-at-plan-build. A seat that never fires (e.g. `review`, when no
high-risk task exists) pays no idle-pane cost. (Restates the issue's own AC4 design point —
adopted as-is, no new evidence needed.)

**Context-reuse policy, per seat** (pane/slot reuse is a separate axis from conversation reuse):

- **review**: reuse the pane/slot across repeated calls (Step 4/8a/11) for observability
  continuity, but relaunch a FRESH session each time. Evidence: arxiv 2603.12123 — fresh-session
  review F1=28.6% vs. same-session self-review 24.6% (p=0.008) vs. repeated same-session review
  21.7% (p<0.001); a second same-session pass is not significantly better than one (p=0.107).
  Follow-up arxiv 2603.16244: even fresh-context review degrades past round 1 on a FIXED
  artifact; independent parallel reviews beat sequential iterative ones. Applicability
  boundary: WF2's Step 4/8a/11 review different, evolving artifacts (design, then per-task
  commits, then the final diff) — the degrades-past-round-1 finding is about re-reviewing the
  SAME artifact repeatedly, so it doesn't indict that pattern directly. It does validate WF2's
  existing parallel multi-reviewer dispatch, and it is a direct caution for any loop-back that
  re-reviews the SAME fixed artifact after a point-fix — keep those bounded (existing loop-back
  budgets already do this).
- **design loop-back** (Step 4 fail -> Step 3): reuse — bounded self-refine against named
  findings, already capped at `MAX_DESIGN_LOOPBACK_ITERATIONS=2`. Same-context iteration is the
  evidenced-helpful case here since the goal is patching flagged issues, not catching new ones.
- **build**: reuse within a single design version (sequential plan tasks; Step 9 drift-gate fix
  cycles against the same design); reset fresh across a design-level loop-back (build hits
  something forcing a return to Step 3) so implementation doesn't defend choices against a
  design it hasn't seen change.

**Worktree-diff contract — resolved (hard precondition for GO, now satisfied):**

Build's context-continuity is coupled to real uncommitted state in its isolated git worktree,
not just conversation history — a design-level reset must decide what happens to that diff.
Contract:

1. **Park, don't destroy.** Before resetting for a design-level loop-back, the orchestrator
   snapshots the current worktree diff — `git -C <worktree> diff` and untracked files — to a
   named, recorded artifact (a `git stash push -u -m "rawgentic-parked:<run_id>:<design_version>:<task_id>"`
   entry, or an equivalent patch file under the run's own `.rawgentic/` state), referenced in
   the run's audit/registry record. This follows this workspace's own established git-safety
   convention (never `reset --hard`/`clean -f` without a recovery path).
2. **Reset the worktree** to clean, matching the new design's starting point — inside that
   task's OWN isolated worktree only (the existing per-task worktree-isolation model already
   used for parallel task dispatch), never the shared main tree.
3. **Ownership:** the orchestrator (executor routing / WF2 driver), not the build-seat agent
   itself, performs park-then-reset atomically before the fresh build-seat context is spawned
   against the new design — the build agent never observes a half-reset state.
4. **Recovery:** the parked entry is retained for the life of the run and named in the audit
   log; a human or later task can `git stash apply`/`git apply` it if some of the discarded
   work still holds against the new design. Not auto-deleted.

Rationale for park-then-reset over preserve-in-place: the whole point of resetting context
across a design change is that the build agent should not defend stale choices. Handing a fresh
context agent a dirty worktree it didn't write recreates the exact same "defend past decisions"
risk through git state instead of chat history — so the worktree, like the conversation, resets
clean; the diff survives only in a recoverable, out-of-band form.

This resolves the issue's hard precondition — a safe contract was found, so the "NO-GO if no
safe contract can be selected" branch does not trigger.

## Named risks carried into the follow-up issue (not blocking GO, must not be dropped)

1. `exec`-injection is not herdr-vendor-documented/endorsed (its own `SKILL.md` recommends the
   plain non-`exec` form) — a real, silent regression risk on a future herdr upgrade. The AC1
   protocol + runner used for this qualification should be re-run (not re-derived) against any
   herdr version bump before trusting this backend again.
2. The two-call create+launch (vs tmux's one atomic call) — safe today, single-threaded per
   spawn only; don't parallelize split+run against one target pane.
3. herdr is a host-wide singleton daemon (per #608/HD1.0) — any herdr upgrade affects every
   pane on the host across every session; version pinning + coordinated maintenance window
   required, same constraint #608 already recorded.

## Related

#608 (HD1.0 capability manifest), #615 (HD2.0 NO-GO, epic #621 — this design does not reopen
that general TerminalRuntime-seam verdict; scoped narrowly to the `build` seat's launch backend
per #633's own scope note), #611 (separate, already-filed, different launcher path — the
Agent-tool's own parallel fan-out, not touched here). Codex (gpt-5.6-sol) adversarial review of
the originating issue plan: `docs/reviews/2026-07-23-633-herdr-build-seat-poc-plan-md-unknown-date.md`
(run-local, gitignored per repo convention).
