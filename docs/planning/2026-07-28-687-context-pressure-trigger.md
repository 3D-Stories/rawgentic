# #687 — the context-pressure trigger: design

Epic #684, child 3/3. Implements the **trigger half** of #654: wire the already-shipped measurement
(PR #675, `docs/planning/2026-07-28-654-q1-context-measurement.md`) to a hook, so a session's
"my context is getting full" behaviour stops being model judgment and becomes a signal.

Owner-reported symptom, 2026-07-28: *"the sessions identifying that their context is getting high and
suggesting or doing clear ... works sometimes, but not reliable at all."*

**A hook cannot forget. That is the whole design.**

## Verdict up front

One new module, `hooks/context_meter.py`, registered on **both `UserPromptSubmit` and `PostToolUse`**. It
reads the session's own transcript, computes in-context tokens as a **fraction of the window**, and emits
`additionalContext` at two tiers — once each per session **per effective window**. It carries **no new
mechanism for the handoff itself**: the handoff is already built, and duplicating it would be a defect.
The deliverable is the trigger, the seam rule, and one decision the epic's predecessor could not make
from inside a build.

## Live probes run before this design committed (`<probe-before-design>`)

Every number here was read on this host on 2026-07-28. None is inferred.

| # | Probe | Result | Consequence for the design |
|---|---|---|---|
| 1 | `message.model` on a live 1M-window session | **`claude-opus-5`, no `[1m]` marker** | Window size is **NOT** derivable from the transcript. Kills the "read the model, infer the window" design. |
| 2 | All-zero `message.usage` row | **Real** — line 1872 of `1b895e69-e90a-43ee-9da6-fe5b55dd8d4f.jsonl`, all four fields `0`, in a transcript whose max in-context total is **809,778** | A last-row reader reports **0%** on a nearly-full session. The reader must take the last **non-zero** row. |
| 3 | `~/.claude/projects/*/<session-id>.jsonl` | **1 hit** across 79 project dirs | The transcript resolves by glob with no slug rule to reproduce. Probe 9 later made the payload's `transcript_path` the primary route and this glob the **fallback**. |
| 4 | Fields a hook receives on stdin | `session_id`, `cwd` confirmed (`hooks/wal-lib.sh:50-53`) | `transcript_path` is documented for the *statusline* (#654) and **no in-repo hook consumes it**, so at this point it was treated as optional. Probe 9 then confirmed it live on both events, promoting it to the primary route (hardened) with the glob as fallback. |
| 5 | Ambient "durable launcher is armed" marker | **None exists.** It is a caller assertion only: `--launcher-armed` / `--fresh-launch-supported` (`hooks/launcher_lib.py:2440`, comment at `:2156`) | AC4's unattended split must be an explicit **declaration**, not an inferred marker. Asserting it falsely to satisfy a guard is not an option (the predecessor hit exactly this and correctly refused). |
| 6 | This session, live, at design time | **159,416 tokens** in context | = 15.9% of a 1M window, **79.7%** of a 200k one. The window is the crux, not the token count. |
| 7 | Predecessor session's maximum in-context total | **809,778 / 1,000,000 = 81.0%**, with no compaction observed | Auto-compaction had **not** fired by 81% on a 1M window. This is the only real datum bounding the directive threshold, and it is a *lower* bound on the compaction point. |

## Approach chosen, and the two rejected

**A. Transcript reader on `UserPromptSubmit` **and** `PostToolUse`** — chosen.
Works headless (probe 4 of #654: a subagent with no UI produced a reading), in-repo, testable, and
`additionalContext` has in-repo precedent (`hooks/wal-context:43`).

**Why BOTH events, and why `UserPromptSubmit` alone would have shipped a dead feature.** My first draft
registered on `UserPromptSubmit` only and rejected `PostToolUse` as "10–30× too fast". That reasoning
conflated *turn* with *user turn*, and the cross-model consult's third risk ("a workflow may advance
within one assistant turn before another `UserPromptSubmit` can observe it") points at the fatal case:
**`UserPromptSubmit` fires once per user prompt.** In a long autonomous run — an epic auto-run, a
headless WF2, this very session — the owner sends ONE prompt and the session then works for hours. A
`UserPromptSubmit`-only meter would evaluate once, at the start, on an empty context, and never again.
It would be silently dead in exactly the runs that need it most.

So the two cadence arms are carried by two events, which is what makes AC9's "whichever comes first"
mean something:

| Arm | Event that can fire it | The case it covers |
|---|---|---|
| 5 **turns** | `UserPromptSubmit` (the turn counter increments only here) | interactive back-and-forth |
| 5 **minutes** | `PostToolUse` (and `UserPromptSubmit`) | long autonomous runs, where events are plentiful and user prompts are not |

Cost of riding `PostToolUse`: when the cadence has not elapsed the hook reads one small JSON state file,
compares two integers and exits — it does **not** touch the transcript. Precedent for a Python hook on
that event is `hooks/step_state_post.py`.

**B. Statusline bridge** — rejected, and #654 already ruled on it: `~/.claude/rawgentic-statusline.sh`
is a user-level file outside any git repo, so it cannot ship as a tested rawgentic PR, and it renders
nothing headless. Documented as an optional addendum in `docs/context-meter.md`; not shipped.

**C. An external wall-clock timer** (crontab/systemd writing a sentinel the hook reads) — rejected here,
costed in "Cadence" below.

## Module surface — `hooks/context_meter.py`

Pure core, all I/O and the clock injected; `main(argv)` is the only impure function
(`registry_prune.py` is the exemplar).

```python
IN_CONTEXT_FIELDS = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")

DEFAULT_WINDOW = 200_000          # conservative floor; see "Window size" below
KNOWN_WINDOWS = (200_000, 1_000_000)
DEFAULT_CHECK_IN_PCT = 60         # AC6 — "start looking for a break"
DEFAULT_ACT_PCT      = 70         # AC3 — "act now"; provisional, see below
MIN_TIER_GAP_PCT     = 10         # check_in must sit this far below act
DEFAULT_EVERY_TURNS  = 5          # AC9
DEFAULT_EVERY_SECONDS = 300       # AC9

def usage_total(usage) -> int
    # sum IN_CONTEXT_FIELDS; any non-int / missing field contributes 0. Never raises.

def read_used_tokens(read_tail) -> int | None
    # last row whose usage_total > 0. None when no usage row parses at all.
    # Unparseable lines are skipped, not fatal (a transcript is appended to live and the
    # final line can be a partial write).
    # READS BACKWARD in bounded blocks (see "Cost" below) — it must never parse a whole
    # transcript, because the largest on this host is 82,948,830 bytes.

def resolve_transcript(session_id, *, payload_path=None, projects_dir=..., glob_fn=glob.glob) -> str | None
    # Hardened, in this order:
    #   1. session_id must match [A-Za-z0-9-]{8,64} — checked BEFORE it reaches any glob
    #      pattern or path join. Precedent for the PRINCIPLE (sanitize a filename component
    #      before it enters a path) is step_state.sanitize_project, step_state.py:49-64 —
    #      which SANITIZES unsafe characters; this design REJECTS instead, because a
    #      sanitized session id would silently address the wrong session's transcript.
    #   2. payload_path is accepted only when ALL hold: basename == f"{session_id}.jsonl";
    #      os.path.realpath() is contained under realpath(projects_dir); it is a regular
    #      file, not a symlink. Any failure falls through to (3) rather than trusting it.
    #   3. else the glob — and ONLY when it returns exactly ONE hit. Two hits is ambiguous
    #      and yields None (never pick arbitrarily). The single hit then passes the SAME
    #      basename/containment/symlink checks: being rooted at projects_dir makes
    #      containment likely, not certain, since a symlink planted inside the tree still
    #      points out of it.

def resolve_window(cfg_value, env_value, observed_tokens, *, warn=None) -> tuple[int, str]
    # (window, provenance). Strict parse, clamp, safe default + stderr warning on a bad value.
    # ESCALATION: when observed_tokens exceeds the resolved window, step up to the next
    # KNOWN_WINDOWS tier — a window a session has already exceeded is provably wrong.

def thresholds(cfg, env, *, warn=None) -> tuple[int, int]
    # (check_in_pct, act_pct). Clamped to 1..99; act must exceed check_in by at least
    # MIN_TIER_GAP_PCT, else BOTH fall back to the defaults with a warning. Never returns an
    # inverted or squeezed pair.

def tier_for(fraction, check_in_pct, act_pct) -> str        # "none" | "advisory" | "directive"

def is_safe_seam(armed, current) -> tuple[bool, str]       # pointer transition, see AC7 below

def should_check(state, *, now, every_turns, every_seconds) -> bool

def nag_text(*, tier, used, window, provenance, seam, seam_reason,
             headless, fresh_handoff_capable) -> str
    # two independent capability values, never one `unattended` boolean

def evaluate(payload, *, read_text, glob_fn, now, env, cfg, pointer) -> Decision
    # Decision = (emit_text | None, next_state | None, tier). The whole pipeline, pure.
```

### The production adapter — how `main()` actually obtains `cfg` and `pointer`

The first draft passed `cfg` and `pointer` into `evaluate` and never said where they come from. Both
reviewers called that out, and it is a genuine hole: without a resolution path, AC6's configurable
percentage and AC7's seam have no live input. The flow, in order, all of it fail-open:

1. **Subagent guard, first.** If the payload carries a truthy `agent_id` (or any sidechain marker), do
   nothing and exit 0 — see "Subagent policy" below.
2. **Throttle check before anything expensive.** Load the state file; if the cadence has not elapsed,
   persist the turn count (on `UserPromptSubmit`) and exit. **No workspace walk, no config read, no
   transcript open.** This is what keeps the `PostToolUse` path cheap.
3. **Resolve the workspace:** walk up from the payload's `cwd` for `.rawgentic_workspace.json` — the
   same idiom as `step_state.find_state_dir` (`step_state.py:67-90`).
4. **Resolve the bound project AND its root path:** take the **last** row in
   `claude_docs/session_registry.jsonl` whose `session_id` matches. The row's `project` gives the name
   (used for the pointer filename) and its `project_path` — a workspace-relative string like
   `./projects/p` — is `normpath`-joined to the workspace root to give the project root that
   `.rawgentic.json` is read from. A missing row, a row without a `project`, an absent `project_path`,
   or an unreadable config each degrade to **defaults for config and `unknown` for the seam**. Never
   guess a project: an unbound session gets the conservative defaults, not another project's settings.
5. **Read config:** the project's `.rawgentic.json`, `contextMeter` block only, directly. This follows
   the convention seven hooks already use for their own key (`security-guard.py:81-96`,
   `security_guard_lib.py:206-223`, `seat_outcomes_lib.py:1237-1247`, `plan_lib.py:765`, …), each
   fail-open on a malformed file. It deliberately does **not** shell out to `capabilities_lib.py derive`:
   a subprocess on a hook that rides `PostToolUse` is a per-tool-call cost the meter has not earned, and
   `derive` returns the whole capabilities object to answer a four-integer question.
6. **Read the pointer:** the step-state file at `claude_docs/wal/<project>.state.json`
   (`step_state._state_path`, `:122-123`), in-process, and **require its `session_id` to match the
   payload's** — another session's pointer is not evidence about this one. Stale beyond
   `DEFAULT_MAX_AGE_MIN` ⇒ `unknown`.

CLI: `main(argv)` runs that flow, calls `evaluate`, and on an emitting decision **writes state first,
then prints** the event-appropriate payload. Subcommands: `hook` (the stdin form Claude Code invokes) and
`read` (print the reading as JSON — the operator-debuggable path, and what the tests drive for the pure
pipeline).

### Subagent policy — declared, not left to chance

A subagent's hook invocation can carry the **parent's** `session_id`, so without a rule the meter would
read the parent's transcript, contend on the parent's state file, and let a subagent's tool calls advance
the parent's cadence. Rule: **if the payload identifies a subagent/sidechain, the meter does nothing.** A
subagent has its own short-lived context and no authority to hand over its parent's session, so there is
nothing useful for it to say. The check is defensive — if the field is absent on this Claude Code version
the branch is simply inert, so the policy cannot itself break the hook.

**Failure mode: fail-OPEN, stated in the docstring.** An absent, unreadable, or malformed transcript,
an unwritable state dir, a bad config value, or any unexpected exception ⇒ **print nothing, exit 0**.
This is a convenience nag, not a security boundary (repo decision guide, `CLAUDE.md` §3:
convenience/routing ⇒ fail-open). A meter that blocks a turn is worse than a meter that misses one.

## Window size — the crux, and the conservative call

Probe 1 says the window cannot be read. Probe 6 says the same token count is 15.9% or 79.7% depending
on which window it is. So window size is **declared, not detected**:

1. `contextMeter.windowSize` in the project's `.rawgentic.json`.
2. `RAWGENTIC_CONTEXT_WINDOW` (per-run override; the repo's env-config convention).
3. `DEFAULT_WINDOW = 200_000`.

**Why 200,000 and not 1,000,000.** The two errors are not symmetric. Assuming 1M on a 200k session
means the nag **never fires** — a silent failure, which is the exact class #687 exists to end.
Assuming 200k on a 1M session means it fires **early**: at most two messages, each naming the
assumption and how to fix it. Fail toward firing.

**The escalation rule that removes most of that cost.** A window a session has already exceeded is
provably wrong. When the observed in-context total exceeds the resolved window, `resolve_window` steps
up to the next `KNOWN_WINDOWS` tier and reports the provenance as `escalated`. So a 1M session
self-corrects the moment it passes 200k, and the residual false positive is bounded to the band below
200k — where a 1M session is genuinely fine and the nag says so, naming the config key to set.

## Thresholds — provisional, with the arithmetic and what would confirm them

- `check_in_pct = 60` (AC6, advisory): start **looking** for a seam.
- `act_pct = 70` (AC3, directive): break at the next turn, seam or no seam.
- Invariant: `check_in` must sit **at least 10 points below** `act`, or both fall back to defaults.

Both must sit below auto-compaction. My first draft called that point UNMEASURED and picked 65/75 on a
single datum; the consult argued for a ≥10-point margin; **both design reviewers then flagged the
resulting default as having no evidence behind it.** They were right that it had none, so rather than
argue the point I measured it. **This answers #654's Q4 for the 1M window.**

### Measured: probe 8, the compaction-discontinuity scan (2026-07-28, this host)

Method: over every transcript larger than 200 KB in `~/.claude/projects/`, walk the `message.usage`
rows in order, compute the in-context total for each, and record every sharp fall (a drop to under 40%
of a previous reading above 50,000 tokens) — an auto-compaction is exactly such a discontinuity.

| Quantity | Value |
|---|---|
**The scan is committed and re-runnable** — `docs/planning/2026-07-28-687-probes/compaction_scan.py`,
because the Step-4 verifier rightly refused a citation whose method lived in a `/tmp` scratch dir no
reviewer could open. Figures below are its output on this host.

| Quantity | Value |
|---|---|
| Transcripts scanned (>200 KB) | **266** |
| Sharp drops found | **86**, across **30** sessions (18 of them with a peak ≥ 900k) |
| **Highest in-context reading observed anywhere** | **999,803 tokens = 100.0% of a 1M window** |
| Top cluster of pre-drop readings | **994,859 – 999,803**, i.e. **99.5–100.0%** |
| Sessions with a drop whose peak is under 250k | **0** |

**The load-bearing conclusion, and it is a ceiling argument rather than an onset measurement.** Sampled
sessions reach **99.5–100% of a 1M window** before anything resets them. So on this corpus
auto-compaction **did not fire below ~99%** in any observed 1M session — had it, no session could have
been seen at 999,803. `act_pct = 70` therefore sits roughly **30 points** below the observed ceiling
rather than the 11 points my draft claimed. Stated as a bound on the *sampled* population, not as a
universal onset law: this is 266 transcripts from one host and one account, and Claude Code could
compact on a policy that varies by model, plan, or version.

**What this scan honestly does NOT establish, stated because the number is tempting:** the 86 drops are
not all auto-compactions. A `/clear`, a manual `/compact`, or a new cache prefix produces the same
discontinuity shape, which is why the median drop (63.1%) and the minimum (16.1%) must not be read as
onsets. Only the **ceiling** is load-bearing here, and the ceiling is what bounds the threshold.

**And the 200k window remains genuinely unmeasured — because it is unmeasurable on this host.** Zero of
the 266 transcripts belong to a session whose peak stayed under 250k, so the corpus contains no
200k-window session to scan. This is an evidenced "cannot confirm here", not an unexamined assumption.
The confirmation is one reading taken inside a real 200k-window session, by the same scan; `docs/context-meter.md`
records that, and the widely-repeated ~77% figure is still not evidence I hold. Given the 1M result — Claude
Code compacts when nearly full, not at three-quarters — a 70% directive is very likely safe on 200k too,
but "very likely" is the honest word and the config key exists so a measurement can override it without a
release.

## Cadence (AC9), and the constraint it will not paper over

State carries `turns` and `last_check_ts`. A check runs when
`turns - last_check_turn >= 5` **OR** `now - last_check_ts >= 300` — whichever comes first, exactly as
asked. Both configurable (`contextMeter.everyTurns`, `contextMeter.everySeconds`), same strict-parse /
clamp / default / warn treatment.

**Config surface, per AC6's "the project's `.rawgentic.json` … per the repo's convention", with an env
override on every value.** **Five** keys live under a `contextMeter` object in the bound project's
`.rawgentic.json`, each with an env twin that takes precedence:

| `contextMeter` key | env twin | default |
|---|---|---|
| `windowSize` | `RAWGENTIC_CONTEXT_WINDOW` | 200,000 |
| `checkInPercent` | `RAWGENTIC_CONTEXT_CHECKIN_PCT` | 60 |
| `actPercent` | `RAWGENTIC_CONTEXT_ACT_PCT` | 70 |
| `everyTurns` | `RAWGENTIC_CONTEXT_EVERY_TURNS` | 5 |
| `everySeconds` | `RAWGENTIC_CONTEXT_EVERY_SECONDS` | 300 |

Precedence is exactly **env → project config → default**, per key. AC6 offers "or workspace default" as
an alternative home; this design **does not implement a workspace-level layer** — a third precedence
tier for five integers is complexity without a caller, and the env twin already covers the case a
workspace default would (one setting applied across projects by a launcher). Named as a deliberate
omission rather than left as an implied-but-absent feature. The env layer is not decoration: a launcher that spawns a 1M-window session can set an
env var but cannot edit the target project's committed config, and window size is precisely the value
such a launcher knows and the config does not (the consult's own riskiest-assumption).

**The honest constraint:** hooks fire on **events**, not on a timer. With no turn and no tool call,
nothing runs. So the 5-minute arm is *evaluated at the next event*, not at the 5-minute mark — a session
idle for an hour notices at its next event, not at the 5-minute mark. Riding `PostToolUse` shrinks that
gap to "the next tool call", which in a working session is seconds, but it does not remove it. This is
documented as the real behaviour in `docs/context-meter.md`.

The mechanism that WOULD give a true wall-clock trigger, costed rather than implied: an external timer
(system crontab or a systemd timer) writing a sentinel the hook reads — that is the
`long-run-resume` crontab pattern, and it is **not shipped here**, because (a) it needs a crontab
write, which the permission classifier has denied before (#654's AC1), and (b) a nag that arrives when
the session is not looking at the screen changes nothing. The event-driven arm is the honest ceiling.

## Safe seam (AC7) — a decidable predicate, not a vibe

Crossing `check_in_pct` starts a **search** for a seam; it does not stop the session. The signal
already exists: the step-state pointer (`hooks/step_state.py`, shipped by #480/#499/#502) records
`{project, workflow, step, step_title, issue, entered_at}`.

**The seam is a pointer TRANSITION, not a list of blessed step numbers.** Crossing `check_in_pct`
*arms* a seam search: the hook snapshots the pointer into its state. A seam has arrived when a later
invocation reads a pointer that has **moved** — same project, both pointers valid, and
`(workflow, step, step_title, entered_at)` differs from the armed snapshot with a later `entered_at`
(re-entering the same step number on a new `entered_at` counts).

`is_safe_seam(armed, current) -> (bool, reason)` is pure and total:

| Pointer state | Verdict | Why |
|---|---|---|
| pointer moved since arming | **`seam_candidate`** | The recorded step changed. That is evidence a phase boundary passed — it is **not proof** that the tree is committed or that no wave is out (see below). |
| pointer unchanged | not a seam | The step is still running: the tree may be dirty (Step 8), a review wave may be out (Step 8a/9/11), or a `consume_loopback` may be waiting on its persist (Step 4/6/11). |
| absent / unreadable / stale beyond `step_state.DEFAULT_MAX_AGE_MIN` / different project | not a seam, `unknown` | Never *soft*-permitted. But the directive tier fires regardless of seam, so an untracked session still gets told — it just does not get the polite early version. |

**This replaces a step-number whitelist that was in my first draft**, and the swap came from the
consult. The whitelist would have hardcoded WF2/WF3 step semantics into a hook — a second source of
truth that silently rots the next time a spine changes, which is the exact drift class this repo has
drift-guard tests for. The transition rule is workflow-agnostic, needs no per-workflow table, and is
about six lines.

**Why `seam_candidate` and not `seam` — the adversarial review's third finding, taken.** A pointer
transition proves the recorded step changed and nothing more. It does not prove the tree is committed, that
no review wave is outstanding, or that the hook observed the boundary *before* work on the new step began —
and the limitation below says a transition can be observed late. Calling that "safe" would be the design
asserting a property it cannot see. So the hook reports a **candidate**, and the advisory's text asks the
session to confirm what only the session can: *"a step boundary was recorded; if your tree is clean and no
review wave is out, this is the moment to break."* The reviewer's stronger fix — a durable readiness
assertion emitted at each transition covering commit state and outstanding waves — is the right long-term
answer and is **deferred as a follow-up**: it requires every workflow step to write a new assertion, which
is a change to the WF2/WF3 spines, not to this hook.

**The limitation it inherits, named rather than hidden** (the consult's own risk #3): a workflow can
advance its pointer *and* start the next phase inside a single assistant turn, so the transition may be
observed late. Two things bound the damage — `PostToolUse` gives the meter many observation points
inside one turn, and the directive tier does not wait for a candidate at all.

The hook runs **no git commands**. A per-turn `git status` is a cost the meter has not earned, and the
pointer already answers the question the seam rule asks.

**When no seam arrives before the hard tier:** the directive tier fires **regardless of seam** and says
so — break now, accept the mid-phase seam, and capture what the resumption protocol needs (branch +
commit, the recorded test baseline, the step marker, the loop-back counters file). The seam search is
best-effort *within the advisory band only*. A seam rule that could defer forever would be the same
silent failure in a nicer costume.

**Attended vs unattended:** attended, the advisory says "look for a seam and tell me"; unattended, it
says "checkpoint and hand over at the next seam without asking".

## Unattended split (AC4) — declared, because probe 5 says it cannot be detected

**Two independent booleans, not one.** The first draft used a single `unattended` flag and the adversarial
review showed it conflates two different capabilities — a headless session with no launcher would have been
pointed at `launcher_lib handoff`, whose guard then legitimately refuses it. So:

| Value | True when | What it changes |
|---|---|---|
| `headless` | `RAWGENTIC_HEADLESS=1` | there is no human to ask, so the directive says "checkpoint and write the handoff" rather than "tell me" |
| `fresh_handoff_capable` | **both** `RAWGENTIC_LAUNCHER_ARMED=1` **and** `RAWGENTIC_FRESH_LAUNCH_SUPPORTED=1` | and only then does the nag name `launcher_lib handoff` as the route |

A headless-but-incapable session (the common case) is routed to `clear-prep` plus an explicit
durable-checkpoint-and-manual-resume instruction. There is deliberately **no inference** on either value:
probe 5 confirmed the only armed-launcher signal in the tree is a caller assertion, and
`launcher_lib.py:2156` is explicit that absence must not read as support. The two env names mirror the two
flags `launcher_lib` already requires, one for one — a launcher that can relaunch says so, and nothing
guesses on its behalf.

## Handoff plumbing (AC8) — reused, not rebuilt, plus the one decision this design owes

Everything the owner enumerated already exists:

| Piece | Where it lives |
|---|---|
| new pane + verify + tear the predecessor down **last** | `perform_handoff` (`hooks/launcher_lib.py:802`), the #665 ladder, `agent_pane_busy` fixed by #673 |
| bind it to a project | the `project_switched` ladder step (`launcher_lib.py:146,1095-1102`) **including #682's bind-first ordering fix from this epic** |
| pass the prompt | the resume prompt the handoff already arms (`fresh_session_handoff`) |
| pass the goal | `last_unmet_goal_condition` (`launcher_lib.py:604`) — a goal is on disk, not in memory (#654 Q2) |
| what to put in it | the `clear-prep` skill's existing process: mempalace checkpoint, durable handoff file, resume prompt, `/goal` text |

**The route the trigger names, and the design hole it does not paper over.** The child-boundary
`handoff` subcommand refuses unless the caller asserts `--launcher-armed` **and**
`--fresh-launch-supported` (probe 5). #687's trigger fires mainly in *interactive* sessions, which have
no armed launcher — so the directive's route is **`clear-prep`**, which is the sanctioned interactive
path and already writes every artifact in the table above. `launcher_lib handoff` is named only on the
unattended branch, where its guard is legitimately satisfied. Neither branch asserts a flag it cannot
back.

**The task-list decision (AC8's genuine gap) — the home is decided, and the TRANSFER IS DEFERRED.**
Harness task lists are session-scoped, so nothing survives a process boundary.

**First, a false claim removed.** My first draft said the home is "a canonical `## Task list` section inside
the file `clear-prep` already writes". I checked `clear-prep` rather than assert it, after the review seat
disputed it — and **the review seat was right**. `~/.claude/skills/clear-prep/SKILL.md` §3 enumerates what
goes into the handoff file (branch + commit, test-baseline counts, file:line anchors, decisions, env
gotchas, **next actions in order**) and carries **no task snapshot at all**; §5 instead requires the resume
prompt to end with `/tasklist`, and says the next session "decomposes the open work into harness tasks via
the `tasklist` skill". There is no `## Task list` section today.

**Decision: the home is the handoff file's existing `next actions, in order` list, and the transfer
mechanism is RE-DERIVATION, not serialization.** That is not a workaround — it is the mechanism
`clear-prep` §3 + §5 already specify and the one every rawgentic handoff has used. The successor reads the
handoff, runs `/tasklist`, and rebuilds the list from the next-actions. So AC8's "pass the task list in" is
satisfied *in the sense the existing plumbing supports*, using no new store — which is exactly what AC8
demands ("reuse the existing session-handoff plumbing rather than inventing a second one").

**And the part that is DEFERRED, stated plainly rather than implied away.** Re-derivation does not preser
task **identity, status, or order**. A serialized transfer that did would need three things this issue does
not build: a **writer** in `clear-prep`, a **consumer** in the resume-prompt contract, and a stable
**representation**. #687 fixes the representation so a follow-up has a target instead of a fresh argument:

```md
## Task list
- [ ] #<id> <subject> — status: pending|in_progress→pending|completed, blocked-by: #<id>,…
```

An `in_progress` task is written back as `pending` with its continuation note (the consult's detail): a
successor cannot inherit half-done in-flight state, only the intent to redo it. **Identity-preserving
task-list transfer is therefore DEFERRED, filed against `clear-prep` (writer) and the resume-prompt
contract (consumer)** — out of scope here because #687's own Scope sentence makes the deliverable the
trigger and the seam choice. Rejected alternative: a new `~/.rawgentic/tasks/<session>.json`, a second
durable store for state the handoff file already carries — the duplication AC8 calls a defect.

## State file — record-before-emit

`~/.rawgentic/context-meter/<session-id>.json`, mode `0600`, written atomically via
`atomic_write_lib.atomic_write_text` (`tempfile.mkstemp(dir=target)` → `os.replace`).

```json
{"schema_version": 1, "session_id": "…", "turns": 42, "last_check_turn": 40,
 "last_check_ts": 1785275399,
 "emitted": {"1000000": ["advisory"]},
 "assumed_window": 1000000, "window_provenance": "escalated",
 "seam_search": {"armed_at": 1785275100, "pointer": {"workflow": "wf2", "step": 8, "entered_at": "…"}}}
```

**`emitted` is keyed by EFFECTIVE WINDOW, and that is a bug fix, not decoration.** The adversarial review
found the flat `tiers_emitted: [...]` list broken: an unconfigured 1M session crosses 60% *of the assumed
200k* at 120k tokens, records `advisory`, then escalates to a 1M window at 200k — and the flat record
suppresses the **real** advisory at 600k for the rest of the session. Keying by window means an escalation
invalidates only the tiers computed against the smaller denominator. Two rules complete it:

- **Monotonic tiers:** emitting `directive` marks `advisory` satisfied for that same effective window, so a
  premature directive can never be followed by a stale advisory.
- **Escalation resets the seam search**, since a seam armed under a wrong denominator was armed for the
  wrong reason.

**Per-invocation write lifecycle** (the review found this unspecified, and unspecified meant the 5-turn arm
would never accumulate):

| Invocation | What is persisted |
|---|---|
| every `UserPromptSubmit` | the incremented `turns` — always, before any cadence decision |
| every completed check, **including tier `none`** | `last_check_turn` + `last_check_ts` |
| an emitting check | the tier, in the **same** write, **before** stdout |

**Concurrency — the reservation is an atomic create, not a lock.** The review was right that hook
serialization is unproven, and it is in fact false: parallel tool calls in one assistant block fire
several `PostToolUse` hooks at once. Two processes could each read "advisory not yet emitted" and both
emit. So the once-per-tier reservation is won with `os.open(marker, O_CREAT | O_EXCL)` on
`~/.rawgentic/context-meter/<session-id>.<window>.<tier>.emitted` — exactly one process can create it, and
the loser stays silent. This is a filesystem compare-and-swap: no lock, no lost-update window, ~3 lines.
A lost `turns` increment under the same race is left as benign and documented — the cadence fires one turn
later, which is not a defect worth a lock.

`~/.rawgentic/` is the established user-level home (`scanner-status.json` there today, mode 0600).
**But `~/.rawgentic` itself is mode `775` on this host — I checked, after the review seat flagged it** — so a
plain `mkdir` would leave `context-meter/` group- and world-readable and the "writes never leave that
subtree" claim defeatable by a pre-planted symlink. Therefore: `context-meter/` is created with mode
**`0700`** explicitly, the resolved state directory is **rejected if it is a symlink**, and the write target
is containment-checked under the real user home before any write or sweep.
**Record before emit** (`security-guard-check.sh:49-55`) — and **the reservation marker IS the record**,
deliberately the only one. The JSON above carries cadence bookkeeping *only*. An earlier draft kept an
`emitted` list inside the JSON *as well*, which the verifier correctly identified as a second source of
truth for one fact, with a nasty consequence: win the marker, fail the JSON write, and the tier could
never fire again for the whole session — the very defect the reservation was added to prevent, merely
relocated. So:

1. the message is fully rendered **before** the reservation, so nothing fallible sits in between;
2. `reserve()` creates the marker (the record);
3. the **very next statement** writes stdout;
4. if delivery still fails, `release()` unlinks the marker so a later turn retries;
5. the JSON state is saved afterwards, best-effort — losing it costs one late check, never a lost warning.

Bounded growth: on write, unlink sibling files older than 7 days.

One consult suggestion declined: `$XDG_STATE_HOME/rawgentic/...` — the repo's established user-level home
is `~/.rawgentic/` ("one helper, one home", `CLAUDE.md` §3). Its *second* suggestion — a lock around the
state write — I declined in the first draft on the reasoning that "hooks are serialized, so the race cannot
occur". **That reasoning was wrong** and the adversarial review caught it: parallel tool calls fire
concurrent `PostToolUse` hooks. The race is real; the fix above is the `O_EXCL` reservation rather than the
proposed lock, because a create-or-fail is both cheaper and sufficient for a once-per-tier guarantee.

## Platform / external dependencies

platform_apis:
- api: `{"additionalContext": "<text>"}` on stdout from a `UserPromptSubmit` hook
  feasibility: verified via existing-call-site — `hooks/wal-context:43` emits exactly this shape from exactly this event, and it is live in every session today
  failure: fail-loud
- api: `{"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": "<text>"}}` on stdout from a `PostToolUse` hook
  feasibility: verified via spike — the adversarial design review correctly refused the `UserPromptSubmit` call site as proof for this event (#226's exact-API-on-exact-surface rule), so it was spiked live on 2026-07-28: a throwaway `PostToolUse:Bash` hook emitting this exact object under a real `claude -p --settings` session, whose reply quoted the injected canary token **verbatim** and named its arrival as a `PostToolUse:Bash` hook system-reminder. Harness committed at `docs/planning/2026-07-28-687-probes/` (`ptu-hook.sh`, plus the method in its README) — the verifier rightly refused the original `/tmp` citation as unauditable.
  failure: fail-loud
  note: **The two events need DIFFERENT shapes.** The top-level `{"additionalContext": …}` form that `wal-context` uses is the proven one for `UserPromptSubmit`; `PostToolUse` takes the `hookSpecificOutput` form. The module emits the event-appropriate shape rather than assuming one works everywhere — which is precisely what the review caught the first draft assuming.
- api: Claude Code session transcript JSONL at `~/.claude/projects/<slug>/<session-id>.jsonl`, rows carrying `message.usage.{input_tokens,cache_creation_input_tokens,cache_read_input_tokens}`
  feasibility: verified via spike — probes 1, 2, 3 and 6 above read this exact shape from two real transcripts on this host 2026-07-28; the field identity `2 + 1257 + 652960 = 654219 == total_input_tokens` was verified against the statusline payload in #654
  failure: fail-silent
  surface: `context_meter.py read --json` prints `{"used": …, "window": …, "fraction": …, "tier": …, "provenance": …}` and exits 3 when no usage row parses — so a format change is one command away from being visible instead of manifesting as a meter that silently never fires. A test pins the row shape against a committed fixture transcript.
- api: `session_id` + `cwd` on hook stdin
  feasibility: verified via existing-call-site — `hooks/wal-lib.sh:50-53` parses both from the same JSON payload on every event
  failure: fail-loud
- api: `transcript_path` on hook stdin, on BOTH `UserPromptSubmit` and `PostToolUse`
  feasibility: verified via spike — probe 9 (2026-07-28, harness committed at `docs/planning/2026-07-28-687-probes/`) registered payload-dumping hooks on both events under a real `claude -p --settings` session and read the captured JSON. `UserPromptSubmit` delivered `{cwd, hook_event_name, permission_mode, prompt, prompt_id, session_id, transcript_path}`; `PostToolUse` delivered those plus `{tool_name, tool_input, tool_response, tool_use_id, duration_ms, effort}`. **`transcript_path` is present on both, and its basename is exactly `<session_id>.jsonl`** — which is what makes the hardening rule satisfiable. So the payload path is the PRIMARY route and the glob is the fallback (the reverse of my first draft).
  failure: fail-silent
  surface: the once-per-session stderr diagnostic below fires on transcript-not-found and on an ambiguous glob, so a path or schema change is visible in captured hook stderr rather than manifesting as a meter that quietly never fires; `read --json` exits 3 for the same conditions.
  limit: probe 9 captured a **top-level** session only. **No subagent payload was observed**, so the exact field name a subagent invocation carries is UNVERIFIED — which is precisely why the subagent guard is written to be inert when no marker is present (see "Subagent policy"), and why it cannot be claimed as proven.

## Error handling and failure modes

**A meter that silently disables itself recreates the exact failure class #687 exists to end** — the
adversarial review's sixth finding, and it lands. Fail-open must not mean invisible. So the three
self-disabling outcomes (transcript not found, glob ambiguous, no parseable usage row) emit a
**rate-limited one-line stderr diagnostic** — at most once per session, gated by the same state file —
while still exiting 0 and still never blocking a turn. Stderr from a hook is captured, not injected, so
this costs the session nothing and gives an operator something to find.

| Failure | Behaviour |
|---|---|
| Transcript absent / unreadable / not resolvable | No nag, exit 0, **once-per-session stderr diagnostic** |
| Glob resolves to more than one transcript | No nag, exit 0, **once-per-session stderr diagnostic** (never guess which) |
| Every usage row unparseable or all-zero | No nag, exit 0, **once-per-session stderr diagnostic**; `read` exits 3 |
| Final transcript line a partial write | Skipped; earlier rows still read |
| `windowSize` / threshold / cadence config malformed | Clamped or defaulted, stderr warning, run continues |
| Thresholds inverted in config | Both fall back to defaults with a warning |
| State dir unwritable | No nag (record-before-emit), exit 0 |
| Step-state pointer absent or stale | `seam: unknown`; the nag still fires with a caveat |
| Unknown workflow in the pointer | `seam: unknown`, same |

## Cost — and why the reader must read backward

The throttled path is one small JSON read and two integer compares; it never opens the transcript. The
**due** path is the one that needed fixing. The review seat measured the largest transcript on this host at
**82,948,830 bytes / 31,054 lines**, parsing in ~0.74 s warm — and a forward scan to the last usable row
would repeat that whole read every five minutes, in every active session, with concurrent due events
duplicating it.

So `read_used_tokens` **reads backward in bounded blocks**: seek to the end, walk **64 KiB** chunks
backward (`_BLOCK`), parse only complete lines, stop at the first usage row with a positive total. The last
non-zero usage row is almost always within the final few KB, so the common case reads one block rather
than 83 megabytes. The total-bytes bound is **`DEFAULT_MAX_BYTES = 4 MiB`**; exceeding it yields no reading
(with the diagnostic) rather than an unbounded read. The hook's `timeout` is **5 s**, declared in
`hooks/hooks.json` alongside each registration, as `wal-context` does.

## Security implications

- The state path is built from `session_id`, which is **validated against `[A-Za-z0-9-]{8,64}`** before
  it reaches any path join or glob pattern — a hostile `session_id` must not traverse
  (`../../etc/cron.d/x`) or widen a glob. Repo rule: any path component from input is canonicalized and
  containment-checked.
- The nag text quotes **no transcript content** — only integers (used, window, percentage) and the step
  pointer's own fields. A meter that echoed conversation into `additionalContext` would leak the very
  context it is measuring.
- Mode `0600` on the state file; it names a session id and token counts, nothing else.
- No network, no subprocess, no writes outside `~/.rawgentic/context-meter/`.

## Tests (AC5) — black-box via subprocess with JSON on stdin

1. Absent transcript ⇒ empty stdout, rc 0.
2. Malformed transcript (invalid JSON lines, truncated final line) ⇒ empty stdout, rc 0.
3. A usage row with **only cache reads** ⇒ counted correctly.
4. **The all-zero final row** (probe 2's real shape) ⇒ the reading comes from the last **non-zero**
   row, not 0. This is the regression test for the dog-fooding finding.
5. **Same token count, two windows:** 159,416 tokens ⇒ `none` on a 1,000,000 window and `directive` on
   a 200,000 one. The AC3 relative-threshold proof, using probe 6's real number.
6. Window **escalation**: an observed total above the resolved window steps up a tier, provenance
   `escalated`.
7. Once-per-tier record: two consecutive over-threshold invocations ⇒ exactly one emission; the
   directive tier still emits after the advisory one did.
8. Record-before-emit: an unwritable state dir ⇒ no emission.
9. Cadence: the turn arm and the seconds arm each fire independently; neither fires early.
10. `is_safe_seam` over each row of the seam table: unchanged pointer, moved pointer, re-entered same
    step with a later `entered_at`, stale pointer, absent pointer, different project.
11. Threshold config: malformed, out-of-range, inverted, and **squeezed** (`check_in` within 10 points
    of `act`) values each clamp/default with a warning.
12. `session_id` traversal attempt ⇒ rejected, no path escape, rc 0.
13. Registration: `hooks/hooks.json` wires `context_meter.py` on **both** `UserPromptSubmit` and
    `PostToolUse`.
14. **Ambiguous transcript resolution:** the glob returns more than one hit ⇒ no reading, no nag, rc 0.
    The meter must not pick one arbitrarily (consult catch).
15. **A smaller non-zero row after a larger one** (what a real compaction looks like) ⇒ the reading is
    the LAST non-zero row, not the maximum. Pins that the reader is not max-based (consult catch).
16. A `PostToolUse` invocation inside the throttle window ⇒ the transcript is never opened (asserted by
    pointing the resolver at a path that would raise if read).
17. **Escalation invalidates only the stale window's record** (the review's first finding, as a regression
    test): 159,416 tokens against a 200k assumption ⇒ premature `directive`; escalate to 1M; then
    `advisory` still fires at 600k and `directive` still fires at 700k.
18. **Monotonic tiers:** a `directive` emission marks `advisory` satisfied for the same effective window,
    so no stale advisory follows a directive.
19. **Per-event output shape:** the `UserPromptSubmit` path emits top-level `additionalContext`; the
    `PostToolUse` path emits `hookSpecificOutput.additionalContext` with `hookEventName: "PostToolUse"`.
    Both shapes asserted explicitly — this is the contract the live spike proved.
20. **Write lifecycle:** four no-tier prompts followed by a fifth across *separate subprocess
    invocations* ⇒ the fifth checks (proving `turns` persisted each time); a tier-`none` check persists
    both last-check fields.
21. **Concurrent reservation:** two processes racing the same tier ⇒ exactly one emits (drive it by
    pre-creating the `O_EXCL` marker and asserting silence).
22. `headless` vs `fresh_handoff_capable`: headless-without-launcher wording routes to `clear-prep` and
    does **not** name `launcher_lib handoff`; both-flags-set wording does.
23. The three self-disabling outcomes each emit their stderr diagnostic exactly **once** per session.
24. **AC8 route text** (the AC had zero tests): the emitted directive names `clear-prep` and the handoff
    file's next-actions list; it names `launcher_lib handoff` **only** when both capability envs are set.
25. **AC6 real config resolution** (not just value parsing): a `tmp_path` workspace + registry + project
    `.rawgentic.json` carrying a `contextMeter` block ⇒ the hook uses those values; an absent workspace,
    an unregistered session, and a malformed config each fall back to defaults without raising.
26. **Subagent guard:** a payload carrying a sidechain/agent marker ⇒ nothing written, nothing emitted,
    rc 0; a payload without one behaves normally (so the guard is inert on versions lacking the field).
27. **Bounded backward read:** a synthetic multi-megabyte transcript whose last non-zero usage row is near
    the end is read without parsing the whole file (asserted on bytes read), and a file whose only usage
    rows sit beyond the byte bound yields no reading plus the diagnostic — never an unbounded read.
28. **Path hardening beyond `session_id`:** a `transcript_path` whose basename is not
    `<session_id>.jsonl`, one resolving outside the projects root, and one that is a symlink are each
    rejected and fall through to the glob; a symlinked state directory is refused; `context-meter/` is
    created mode `0700` (asserted with `stat`) even under a `0775` parent.

## Complete file-change scope — every pinned surface a new hook touches

The review seat's fourteenth finding was that this list was missing, and it was right: a new registered
hook plus a new config object touches **more than the four version surfaces everyone remembers**. Verified
individually rather than copied from a checklist.

| Surface | Why it is in scope | Verified how |
|---|---|---|
| `hooks/context_meter.py` | the module | — |
| `hooks/hooks.json` | registration on both events, with a `timeout` | — |
| `tests/hooks/test_context_meter.py` | the tests | — |
| **`phase_executor/.../canary.py:41` `EXPECTED_REGISTRATION_DIGEST`** | a length-framed sha256 over ALL of `hooks.json` **plus the bytes of every referenced script** (`canary.py:215-248`) — this change alters it twice over. Guard: `tests/phase_executor/test_canary_digest_pin.py:20-24` | Step-2 analysis, cited file:line |
| **`README.md` `#### Hooks` inventory table** | the table lists every hook by name/event/purpose, so a new hook needs a row | **I read it myself** (`README.md` §Hooks). Note: the Step-2 analysis agent reported "no table enumerating individual hooks" — **that was wrong**, and the review seat caught it. Confirmed against the file. |
| **`templates/rawgentic-json-schema.json`** | the example-config template carrying a `$comment`-documented block per optional section (`telemetryAlerts` is the exact analogue); a new `contextMeter` block belongs there | I opened it and listed its top-level keys |
| `docs/config-reference.md` | the `contextMeter` keys, defaults, clamps, env twins | — |
| `docs/context-meter.md` (new) | tier table, seam rule, unattended split, the measured 1M onset, the unmeasured 200k caveat, the event-not-timer constraint, the statusline addendum | — |
| `docs/testing.md` | test-count prose for the new file, if it pins one | check before edit |
| 4 version surfaces + README changelog | repo convention, one PR = one bump = one entry, in the exact shape with the diagram decision and `Suite old→new` | `CLAUDE.md` §2 |
| rendered `.html` of this design | design-doc convention, via `hooks/render_artifact.py` | — |

## Scope

**In:** the transcript consumer, its registration, the tiers, the seam predicate, the config surface,
the unattended split, `docs/context-meter.md`, and the AC8 task-list-home decision.

**Out (per the issue):** a hook that runs `/clear` itself; replacing auto-compaction; the statusline
bridge (documented as an addendum, not shipped); #654's Q4/Q5 write-up.

## Cross-model peer consult — what it changed, and what it did not

An independent proposal was produced by the `gpt` backend (`docs/reviews/peer-rawgentic-peer-problem-687-2026-07-28.md`)
under the blindness rule: my draft was on disk before I read it. Four things were grafted and four
declined, all recorded so the reasoning is auditable rather than absorbed silently.

**Taken:**
1. **`PostToolUse` as a second event.** Its risk #3 pointed at the adjacent fatal case — a
   `UserPromptSubmit`-only meter is silently dead in a long autonomous run. This is the single largest
   change to the design and it came from the consult.
2. **The seam as a pointer transition** rather than a step-number whitelist. Simpler, workflow-agnostic,
   and it removes a second source of truth that would rot on the next spine change.
3. **A ≥10-point margin below the lowest observed compaction onset**, moving the defaults from 65/75 to
   60/70, plus the `check_in` ≤ `act − 10` invariant.
4. Two tests: the **ambiguous glob** case and the **smaller-non-zero-row-after-a-larger-one** case (which
   pins that the reader is last-non-zero, not max-based). Plus `schema_version` in the state file.

**Declined, with reasons:** `$XDG_STATE_HOME` (repo home is `~/.rawgentic/`); an advisory lock (single
writer, `os.replace` already atomic); a new `handoffs/<id>.json` manifest store carrying task snapshots
(AC8 calls duplicating the handoff mechanism a defect — see the task-list decision above); and extending
`fresh_session_available`'s authorization to accept an `interactive-confirmed` capability — a genuinely
good idea, but it changes #665's shipped ladder, so it is a **follow-up**, not this issue.

## Adversarial-on-design review — 10 findings, 9 taken

`docs/reviews/2026-07-28-687-context-pressure-trigger-md-2026-07-28.md` (Codex, high effort): 0 Critical,
4 High, 6 Medium. Every finding was checked against the design rather than accepted on the reviewer's
word, and nine changed it. Where the fix is smaller than the recommendation, the reduction is stated.

| # | Sev | Finding | Disposition |
|---|---|---|---|
| 1 | High | Window escalation left the flat `tiers_emitted` list keyed to a dead denominator — a premature advisory under an assumed 200k window would suppress the real one at 600k | **Taken in full.** `emitted` keyed by effective window + monotonic tiers + escalation resets the seam search. Regression test 17. |
| 2 | High | One `unattended` boolean conflated headless with fresh-handoff-capable, routing valid headless sessions at a command that refuses them | **Taken in full.** Two booleans, two env pairs, headless-but-incapable routed to `clear-prep`. Test 22. |
| 3 | High | A pointer transition does not prove nothing is in flight; calling it `seam` asserts a property the hook cannot see | **Taken, reduced.** Renamed `seam_candidate` and the advisory asks the session to confirm tree/wave state. The proposed durable readiness assertion is deferred — it changes the WF2/WF3 spines, not this hook. |
| 4 | High | `additionalContext` on `PostToolUse` was **unproven** — the cited call site is `UserPromptSubmit`-only, which #226's exact-surface rule does not extend | **Taken in full, and it changed the contract.** Spiked live; it works, but only via `hookSpecificOutput`, not the top-level form. Declared as its own `platform_apis` entry. Test 19. |
| 5 | Med | The write lifecycle for non-emitting invocations was unspecified, so the 5-turn arm would never have accumulated | **Taken in full.** Explicit per-invocation write table. Test 20. |
| 6 | Med | Silent self-disable on a transcript/format failure recreates the very failure class the feature exists to end | **Taken in full.** Once-per-session stderr diagnostic on all three outcomes, still exit 0. Test 23. |
| 7 | Med | Declaring a `## Task list` heading is not a transfer mechanism — no writer, no consumer, no representation | **Taken in full, and it shrank the claim.** The home is decided; **transfer is stated DEFERRED**, with the representation fixed and both missing call sites named. |
| 8 | Med | The 200k default's directive has no measured evidence it precedes compaction on that window | **DISCARDED, with reason.** AC3 explicitly instructs "pick a conservative default, state it as provisional, and record what would confirm it" — the design does exactly that, and the reviewer's alternative (require explicit declaration) would make the meter do nothing by default, which is the silent failure #687 exists to end. Mitigations kept: the nag names the assumed window and its provenance, and `docs/context-meter.md` tells a 200k operator to set the threshold from their own observation. |
| 9 | Med | The no-lock rejection rested on hook serialization, which is unproven | **Taken — the reasoning was simply wrong.** Parallel tool calls fire concurrent `PostToolUse` hooks. Fixed with an `O_EXCL` reservation rather than the proposed lock. Test 21. |
| 10 | Med | "Verdict up front" still said `UserPromptSubmit` only, contradicting the chosen approach | **Taken.** Fixed. |

Eleven review passes across #679 and #682 each found something real; this is the twelfth and it did too.

## Step 4 design gate — the review seat, 15 findings, verdict BLOCK → resolved

The gate's own quality-bar self-review (executor `review` seat, `gpt-5.6-sol`, deep pass) returned **15
findings — 7 High, 7 Medium, 1 Low — and a verdict of BLOCK.** Two things about that number matter and both
are stated rather than smoothed over:

1. **It was dispatched BEFORE the adversarial amendments landed** (brief sent while the design still had the
   flat tier list, the single `unattended` boolean, the `seam` label and the unproven `PostToolUse` shape).
   So findings 1, 2, 4, 6, 7 and 15 name defects the concurrent adversarial pass had already fixed — the two
   reviewers converged independently on the same four Highs, which is corroboration, not double-counting.
2. **The genuinely new findings were all real, and one of them corrected my own input.** Every disputed fact
   was checked against the file rather than accepted:

| # | Sev | New finding | Disposition |
|---|---|---|---|
| 5 | High | **The production adapter was missing** — `evaluate` received `cfg` and `pointer` with no specified way for `main()` to obtain either, so AC6 and AC7 had no live input path | **Taken.** Six-step resolution flow specified, throttle-before-anything-expensive, project resolved from the registry, pointer `session_id` required to match. |
| 3 | High | **Subagent contention** — a subagent invocation can carry the parent's `session_id`, so the meter would read the parent's transcript and contend on its state | **Taken.** Explicit subagent policy: do nothing. Written to be inert where the marker is absent, because probe 9 saw no subagent payload and I will not claim a field name I did not observe. |
| 9 | Med | **`clear-prep` does not write a `## Task list` section** — my "already writes" claim was false | **Taken, and it improved the answer.** I read `clear-prep` myself: §3 has no task snapshot, §5 mandates `/tasklist` re-derivation. The home is now the existing next-actions list, transfer is re-derivation, and identity-preserving transfer is explicitly DEFERRED. |
| 10 | Med | **`transcript_path` was used but undeclared** | **Taken.** Probe 9 dumped both events' real payloads; declared, and the payload path is now primary with the glob as fallback. |
| 12 | Med | **Path hardening stopped at `session_id`** — no basename binding, containment or symlink check; `~/.rawgentic` is `0775` so `mkdir` would not be private | **Taken in full.** I confirmed the `775` mode myself. Basename bound to the validated id, realpath containment, symlink refusal, `context-meter/` at `0700`. |
| 13 | Med | **Unbounded transcript parse** — largest transcript measured at 82,948,830 bytes, re-read every five minutes per session | **Taken.** Bounded backward read with a byte cap. I confirmed the file size independently. |
| 14 | Med | **Missing pinned surfaces** — canary digest, README hook table, config template, testing docs | **Taken.** Full file-change scope table above. **This finding also caught an error in my Step-2 analysis input**, which had reported no README hook table; there is one, and I verified it. |
| 8 | Med | The 200k default's directive had no measured evidence | **Partly taken by MEASURING (probe 8), and the 200k half remains OPEN.** Probe 8 measured only the **1M** corpus (ceiling ≈99.8%, so 70% has ~30 points of margin there) and it also showed *why* the 200k case cannot be closed here: zero of 266 transcripts belong to a 200k-window session. So the accepted finding is **mitigated, not resolved** — mitigated by the 1M evidence, the config key, and the nag naming its own assumed window; unresolved pending one real 200k observation. The identical adversarial finding F8, which I first discarded on the AC's wording, is answered the same way — and discarding it was the weaker response. |
| 11 | Med | AC4 and AC8 had zero tests; several listed tests could pass without the behaviour | **Taken.** Tests 22, 24–28 added. |

**Why this is a spec-tightening pass and not a Step-3 return.** Every accepted finding tightens a
specification the design already carried — a resolution flow it implied, a hardening rule it under-stated, a
threshold it called provisional, a claim it overstated. **No approach was reversed:** the module, both
events, the two tiers, the transition-based seam and the reuse-not-rebuild handoff stance all survive
unchanged. That is the #223 in-gate cheap path (amend + one incremental verifier, no Step-3 return), and one
`spec_tighten` loop-back was consumed for it.

## The claim I would most expect to be wrong

Not the threshold any more — probe 8 measured that, and 70% sits ~30 points below the 1M onset. What
replaces it:

**That the subagent guard fires on the right field.** Probe 9 captured only a top-level session's payload, so
I do not know what a subagent invocation actually carries. If subagent hooks arrive with the parent's
`session_id` and **no** marker my guard recognises, then every subagent tool call advances the parent's
cadence and contends on the parent's state file. The `O_EXCL` reservation still prevents a duplicate nag and
a lost turn increment is benign, so the failure mode is a meter that checks more often than designed — not a
wrong reading and not a broken guarantee. The confirmation is one payload dump from inside a subagent, which
is the same probe-9 harness pointed at a `Task` dispatch, and it is a follow-up rather than a blocker
precisely because the blast radius is cadence, not correctness.

Runner-up: **that the pointer-transition seam is observable often enough to matter.** If WF2 runs several
steps inside one assistant turn, the advisory tier may never see a transition and every real break ends up
driven by the directive tier — i.e. the polite early path degrades to the blunt one. The feature still works;
it just works less gracefully than designed, and `PostToolUse` is what makes the degradation unlikely rather
than certain.

Third: **that the 200k window behaves like the 1M one.** Unmeasurable here (zero 200k sessions in a
266-transcript corpus), so it rests on the inference that Claude Code compacts near-full on both. If 200k
compacts at 65%, the directive is late and useless on that window — the config key is the escape, and the
scan is one 200k session away from settling it.
