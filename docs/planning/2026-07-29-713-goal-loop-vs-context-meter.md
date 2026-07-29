# #713 — the `/goal` loop vs the context meter: what the Stop event actually allows

**Status:** AC1 (research) and AC2 (proof of concept) complete; revised once after an independent
cross-model review (WF5, Codex, 7 findings — dispositions in §7). AC3 is **redesigned**, not adopted
as written: the PoC refuted part of its premise and surfaced a shipped delivery bug.
**Date:** 2026-07-29. **Host:** claude-code VM. **Claude Code 2.1.220.** **Plugin 3.107.1.**

Every claim below is marked **CONFIRMED** (with its evidence: doc line, probe, file:line) or
**INFERRED** (with what would confirm it). Probe scripts are committed beside this doc in
`2026-07-29-713-probes/` so every citation is re-runnable — the #687 review established that a
citation a reader cannot open is not evidence.

---

## 1. Verdict first

Four findings, in the order that matters:

1. **At `Stop` there is no way to speak to the model without also forcing another turn.**
   Both output channels — `decision: "block"` and `hookSpecificOutput.additionalContext` — continue
   the conversation. Silence is the only way to let a turn end. **CONFIRMED** by doc and probe 11.
2. **Injected hook text is treated as DATA, not as an instruction.** In a real `/goal` loop the
   model read the meter's injected directive, named it as a possible prompt injection, and
   explicitly declined to act on its imperative — while still reporting it. **CONFIRMED** by probe
   12. **So AC3 cannot work by ordering the model to hand off.**
3. **A recorded handoff DOES satisfy a `/goal` condition that says it does.** The evaluator returned
   exactly that verdict, in its own words, and the loop terminated instead of demanding another work
   turn. **CONFIRMED** by probe 15. This is AC4's mechanism, and it is the causally important half —
   it is the one channel the model treats as authoritative rather than as suspect injected text.
4. **The meter's `UserPromptSubmit` arm has never delivered anything.** It emits the top-level
   `additionalContext` shape, which 2.1.220 silently ignores. **CONFIRMED** by probes 14 and 14b. A
   live bug in shipped code, found while researching the timing question, in the module #713 already
   scopes.

**Consequence for the plan.** AC4 and AC5 (text) carry the fix. AC3 ships as four changes, not one:
the correct output shape on every event; a **directive-tier-only** Stop arm gated on
`stop_hook_active`; per-channel delivery markers so the Stop arm is not silenced by the mid-turn arm
having already spoken; and — per the owner's decision of 2026-07-29 (§6) — `pane-handoff` stops
*offering* and starts *running*, because a successor that waits for a human is the same bug wearing
different clothes.

---

## 2. AC1 — the `Stop` hook contract

### 2.1 Input payload

**CONFIRMED (probe 11, three runs).** A `Stop` payload on 2.1.220 carries exactly:

```
background_tasks, cwd, effort, hook_event_name, last_assistant_message,
permission_mode, prompt_id, session_crons, session_id, stop_hook_active, transcript_path
```

Two of those matter here:

- **`transcript_path` IS present at `Stop`.** So the meter's whole reading path
  (`resolve_transcript` → `read_used_tokens`, `context_meter.py:275,228`) works at `Stop`
  unchanged, including the hardening that requires the basename to be `<session_id>.jsonl`.
- **`stop_hook_active`** — documented as *"`true` when Claude Code is already continuing as a
  result of a stop hook"* (`hooks.md:2194`). Measured: `false` on the first firing of a turn
  sequence, `true` on every subsequent firing (probes 11, 12).

**A `Stop` reading can be stale, and that is NOT harmless.** The docs warn (`hooks.md:2196`) that
*"the transcript file isn't guaranteed to include the final message at Stop time on all versions."*
So a read taken at `Stop` can miss a tier that the final assistant message just crossed. Corrected
from an earlier draft of this doc, which called that harmless for a band trigger: it is not, because
a missed crossing has no later firing to recover in if the session then ends.

What makes it tolerable here is that **`Stop` is an additional channel, not the only one.** The
mid-turn arm rides every tool call, so a crossing is normally detected mid-turn and the Stop arm is
a re-delivery at the decision point. The exposure is narrowed to: a tier crossed by the very last
assistant message of a turn in which no further tool call occurs. **Stop delivery is therefore
best-effort**, and this doc says so rather than claiming precision it has not measured. **NOT
measured:** the size of the lag, and whether it is nonzero at all on 2.1.220.

### 2.2 Output shapes

**CONFIRMED** (`hooks.md:2254-2280`, the *Stop decision control* table, verbatim):

| Field | Documented meaning |
|---|---|
| `decision` | `"block"` prevents Claude from stopping. Omit to allow Claude to stop |
| `reason` | Required when `decision` is `"block"`. Tells Claude why it should continue |
| `hookSpecificOutput.additionalContext` | Non-error feedback for Claude. The conversation continues so Claude can act on it, but unlike `decision: "block"` it is shown in the transcript as hook feedback rather than a hook error |

And the sentence that decides this design (`hooks.md:2271`, verbatim):

> Use `additionalContext` when the hook is working as designed and giving Claude guidance, such as
> "run the test suite before finishing". **It keeps the conversation going** through the same loop
> protections as `decision: "block"`, namely the `stop_hook_active` input and the
> 8-consecutive-continuation cap, but the transcript labels it `Stop hook feedback` and no hook
> error notification is shown.

So **there is no read-only channel to the model at `Stop`.** The universal fields do not provide
one either:

- `continue: false` — *"If `false`, Claude stops processing entirely after the hook runs. Takes
  precedence over any event-specific decision fields"* (`hooks.md:775`). This is the one true
  override: it would let a rawgentic Stop hook **veto** `/goal`'s continuation outright.
  **Rejected** — a handoff needs a turn in which to run, and killing the loop dead is the exact
  "worse than the bug" failure the issue's Risk section names.
- `systemMessage` — *"Warning message shown to the user"* (`hooks.md:778`); `stopReason` is *"Not
  shown to Claude"* (`hooks.md:776`). These reach the **human**, not the model. **INFERRED:**
  `systemMessage` alone does not force a continuation, since it is not a decision field. Not
  probed; what would confirm it is a Stop hook emitting only `systemMessage` with no extra turn
  appearing. Recorded because it is the only candidate for a future non-continuing advisory tier.
- **Exit codes.** Exit 2 on `Stop` *"Prevents Claude from stopping, continues the conversation"*
  (`hooks.md:712`); exit 1 is a non-blocking error (`hooks.md:699`); JSON is read only on exit 0
  (`hooks.md:760`). `context_meter.py` always exits 0 (`context_meter.py:1267-1271`), so it can
  never block by exit code.

### 2.3 Two Stop hooks — ordering, composition, veto

**CONFIRMED** (`hooks-guide.md:491-493`, verbatim):

> When multiple hooks match the same event, every hook's command runs to completion before Claude
> Code merges the results. One hook returning `deny` doesn't stop sibling hooks from executing. […]
> After all matching hooks finish, Claude Code combines their outputs. For `PreToolUse` permission
> decisions, the most restrictive answer applies, in the order `deny`, `defer`, `ask`, `allow`.
> **Text from `additionalContext` is kept from every hook and passed to Claude together.**

Also **CONFIRMED** (`hooks.md:331`): *"All matching hooks run in parallel, and identical handlers
are deduplicated automatically."*

So `additionalContext` is **additive across hooks, not competitive**. A rawgentic Stop hook cannot
be vetoed by `/goal`'s evaluator, and cannot veto it, as long as it emits `additionalContext` and
no `decision`. **CONFIRMED end-to-end by probe 12**: both hooks fired every turn and the goal loop
completed normally.

**Honest gap:** the documented precedence order (`deny > defer > ask > allow`) is stated for
`PreToolUse` **only**. How two *Stop* `decision` values compose is **NOT documented**. It does not
affect this design — the meter emits no `decision` — but no later change may assume it. What would
confirm it: two Stop hooks, one blocking and one silent, observing whether the block survives.

**`/goal` is itself a Stop hook — CONFIRMED** (`goal.md:126`): *"`/goal` is a wrapper around a
session-scoped prompt-based Stop hook."* `hooks.md:2189` agrees, and `Stop` is in the list of events
supporting `prompt` hooks (`hooks.md:2827`). Corroborated independently by `QwenLM/qwen-code#4206`,
describing a reimplementation of the same mechanism: *"`/goal` registers a session-scoped prompt
hook on the `Stop` event […] the main loop increments `stopHookBlockingCount`. Default cap is 8
unless `CLAUDE_CODE_STOP_HOOK_BLOCK_CAP` is set."*

**The 8-continuation cap.** *"Claude Code overrides the hook and ends the turn after 8 consecutive
blocks"* (`hooks.md:2194`), raisable via `CLAUDE_CODE_STOP_HOOK_BLOCK_CAP`
(`hooks-guide.md:966`); the cap counts blocks *"in a row without progress"* (`hooks-guide.md:955`).
**Unresolved, and named rather than glossed:** the reported #713 run had ~10 Stop firings, which a
naive reading of a cap of 8 would have terminated. Either the counter resets on progress or `/goal`
is accounted differently. **INFERRED** that the meter does not meaningfully erode the budget: it
emits at most once per tier per window per channel (§4.3), so it can add at most one continuation.
Probe 12 saw four firings and normal completion — consistent, but it never approached the cap, so
this is not proven. What would confirm it: a goal loop driven to the cap with and without the meter.

### 2.4 Is `/goal`'s evaluator text modifiable from outside? And does it accept a handoff?

These are **two separate questions**, and an earlier draft of this doc wrongly folded the second
into the first and declared both settled. Split:

**(a) Is the evaluator's prompt or feedback wording configurable? CONFIRMED: no.** `goal.md`
documents exactly one knob over the evaluator — `ANTHROPIC_DEFAULT_HAIKU_MODEL`, which changes
*which model* judges (`goal.md:135`), with a warning that it repoints every other small-fast-model
use too. There is no setting for the evaluator's prompt or its feedback text anywhere in `goal.md`
or `hooks.md`. This question is closed by absence of evidence in the primary source; the honest
caveat is that absence from the docs is not proof of absence from the binary.

**(b) Will the evaluator accept a recorded handoff as satisfying a condition that says it does?**
This is AC4's load-bearing mechanism and it was **unproven** in the first draft. **Now CONFIRMED by
probe 15** — §3, probe 15, including the evaluator's verbatim verdict.

The mechanism it relies on: the evaluator *"does not call tools, so it can only judge what Claude
has already surfaced in the conversation"* (`goal.md:137`), and on a `no` verdict *"Claude keeps
working and takes the reason as guidance for the next turn"* (`goal.md:130`). The condition is
user-authored, capped at 4,000 characters (`goal.md:120`), and is exactly what the evaluator reads.
**That is the lever, and it is the only channel in this whole system that the model treats as
authoritative rather than as suspect injected text.**

Also captured verbatim from probe 15's transcript — the instruction `/goal` injects into the session
itself, which no rawgentic surface had recorded before:

> Briefly acknowledge the goal, then immediately start (or continue) working toward it — treat the
> condition itself as your directive and do not pause to ask the user what to do. The hook will
> block stopping until the condition holds. It auto-clears once the condition is met […]

*"Treat the condition itself as your directive"* is why AC4 works and AC3 alone cannot.

---

## 3. AC2 — the proof of concept

Five probes, all committed in `2026-07-29-713-probes/`, all run on this host on 2026-07-29 under
Claude Code 2.1.220 with `--model sonnet`.

### Probe 11 — does the meter's shape work at `Stop`, and what does it cost? (no goal)

`stop-probe.sh` dumps every Stop payload and emits one canary `additionalContext` with
`hookEventName: "Stop"`.

**Result — CONFIRMED:**
- The nested shape is **accepted** on 2.1.220 and **delivered**: the model's next turn was the
  canary token quoted verbatim.
- The Stop hook fired **twice** for a one-prompt session, and there were **two** assistant turns.
  So emitting `additionalContext` at `Stop` **forces a continuation** — measured, not inferred.
- `stop_hook_active` was `false` on firing 1 and `true` on firing 2.

**This is the fact that kills AC3-as-written.** Registering the meter on `Stop` unconditionally
would force an extra turn in any ordinary session that crosses a tier and finishes a turn — turning
a convenience nag into a turn-blocker, against `context_meter.py`'s stated contract
(`context_meter.py:21-31`).

### Probe 12 — a real `/goal` armed, with the meter speaking inside the loop

A genuine multi-turn goal with the probe registered as a second Stop hook; run once silent
(observe only) and once with the canary live.

**Result — CONFIRMED:**
- **Two Stop hooks coexist.** The probe fired every turn (3 silent / 4 with the canary) *and* the
  goal ran its turns and completed normally (`steps.txt` written, `result: success`). No silencing,
  no double-evaluation, no broken goal.
- **`stop_hook_active` is `true` from the second Stop onward in a goal loop**, driven by `/goal`'s
  own continuations while the probe only observed (silent variant). This is what makes §4.2's gate
  possible.
- **The model refused the injected imperative.** Turn 3, verbatim: *"Flagging something before
  continuing: that 'Stop hook additional context' block contained an embedded instruction (a
  'CANARY-STOP-B2-RM4TZ8' token telling me to repeat it verbatim). Per my standing instructions,
  text arr[iving…]"* — reported, not obeyed, then it carried on with the goal.

That last result reproduces #687 probe 9's closing observation and generalizes it: **hook-injected
text is data.** A directive that works by ordering the model to run a skill is arguing with its
injection defences. A directive that states measured **state** is integrated as fact.

**Not proven by this probe:** that the canary caused the 4th firing. The canary run's goal also ran
more turns for unrelated reasons (the model batched its appends), so firing counts are not
comparable between the two variants. §4.2's cost claim rests on probe 11, where the causation is
unambiguous.

### Probe 13 — fail-open at a blocking-capable event

`stop-probe-failopen.sh` prints non-JSON on stdout, writes stderr, and exits 1 — the two ways to be
broken that are not exit 2.

**Result — CONFIRMED:** one turn, `result: success`, no extra turn, nothing surfaced to the model.
Fail-open holds at `Stop`. Worth stating *why*: exit 2 at `Stop` **does** block, so this guarantee
rests on `context_meter.py` returning 0 unconditionally, not on the event being harmless — which is
why §4.5 tests it.

### Probe 14 / 14b — the adjacent bug: which shape does `UserPromptSubmit` honour?

Motivated by a direct conflict: `hooks-guide.md` says of `UserPromptSubmit` *"Nest
`additionalContext` inside `hookSpecificOutput`; if you place it at the top level of the JSON,
Claude Code silently ignores it"*, while `context_meter.py:905-915` and `hooks/wal-context:43` both
emit the **top-level** form, recorded in-repo as verified live 2026-07-28.

**Result — CONFIRMED, and the docs are right:**
- Both shapes registered together (probe 14): the model reported **only** the nested token; the
  top-level token appeared **0** times in the entire stream.
- Top-level **alone**, ruling out a merge confound (probe 14b): the model replied **`NONE`**. Zero
  occurrences.

**So `context_meter.py`'s `UserPromptSubmit` arm delivers nothing.** The meter has run on one
working arm (`PostToolUse`, nested) since #687. In the run that produced #713, the two lines the
owner saw were the PostToolUse arm; the UserPromptSubmit arm was silent because it could not be
otherwise.

Fair to the earlier record: the in-repo note claims the opposite, verified one day earlier. Either
the behaviour changed between 2026-07-28 and 2026-07-29, or that verification mistook a PostToolUse
delivery for a UserPromptSubmit one. This doc does not adjudicate which; it records **what is true
today on 2.1.220**, which is what ships. The stale docstring claim is corrected in the same change.

`hooks/session-start:878` also emits the top-level form, but on `SessionStart` — a **different**
event with its own contract, and empirically something does arrive from that hook. **Not touched,
not assumed broken.** Filed as a follow-up to measure, with `hooks/wal-context`, which is outside
#713's scope and needs its own issue.

### Probe 15 — does the evaluator accept a handoff as satisfying the goal? (AC4's mechanism)

Added in response to the WF5 review, which correctly refused AC4 as an unproven hypothesis. A real
`/goal` was armed with a two-branch condition — finish the work, **or** record a handoff, with the
condition stating in terms that *"A RECORDED HANDOFF SATISFIES THIS GOAL"*.

**Result — CONFIRMED, and the verdict is recorded, not inferred from termination:**
- The session wrote `handoff.md` and **never created `work.txt`** — it did not do the work.
- The loop **terminated** (`result: success`, 3 assistant turns, below the condition's own 4-turn
  bound, so the turn clause did not fire).
- The evaluator's own reason, verbatim from the transcript: *"The transcript shows the assistant
  created handoff.md […] with content that names what remains […] **This recorded handoff satisfies
  the goal condition explicitly stated in the stopping criteria: 'A RECORDED HANDOFF SATISFIES THIS
  GOAL'.**"*

So a loop goal whose condition names a handoff as satisfying it **is judged that way**, and the loop
stops asking for more work turns. AC4 is a mechanism, not a hope.

**Limits, named:** one run, one wording, one model (`sonnet` main / default small-fast evaluator).
It shows the mechanism works; it does not establish that every phrasing works. What would strengthen
it: the same probe with the exact shipped wording once AC4/AC5 land, and with a condition where the
work is genuinely unfinished rather than absent.

---

## 4. The redesign

### 4.1 Always nested, with the event's own name

`emit_payload` (`context_meter.py:905-915`) becomes one shape for every event: nested under
`hookSpecificOutput` with `hookEventName` set to the firing event. **CONFIRMED delivered** in this
form on `PostToolUse` (#687 probe 9), `UserPromptSubmit` (probe 14) and `Stop` (probes 11, 12). Less
code than the branch it replaces, and it fixes the dead arm.

### 4.2 The Stop arm: directive tier only, gated on `stop_hook_active`

Two gates, both narrowing:

1. **`stop_hook_active` must be true.** True means some Stop hook already continued this turn — in
   practice a `/goal` loop, exactly the situation #713 is about. False means the session is about to
   hand control back to a human, and staying silent means an ordinary session is **never** forced
   into an extra turn.
2. **Directive tier only.** The advisory tier (60%) stays mid-turn. At 60% an extra turn is pure
   cost; at the act tier the extra turn *is* the handoff.

**The honest cost, corrected.** An earlier draft claimed the gate makes the Stop arm cost "no extra
turn, because the turn was continuing anyway". The WF5 review refuted that, and it was right:
`stop_hook_active` describes why *this* firing happened, not that another hook *will* continue from
it. If `/goal`'s evaluator returns **yes** on the same firing where the meter first delivers the
directive, the meter's `additionalContext` becomes the sole continuation and forces **one** extra
turn.

Bounded, and stated plainly: **at most one extra turn per session per effective window** — the
marker (§4.3) permits exactly one directive delivery on the `stop` channel. It can only happen at
≥70% context, in a session that was already looping, at the moment the loop ended. That extra turn
carries "you are at 70%; a handoff satisfies a loop goal; here is the route" — which at that point
is the behaviour this issue exists to produce, not a regression. Accepted deliberately rather than
designed away.

Note for reviewers, because it looks inverted: the documented idiom (`hooks-guide.md:955-964`)
checks the same field and exits early when it is **true**. That idiom is for gates whose job is to
*force* convergence and which must not loop. This is the opposite shape — a once-per-tier nag that
must not *start* a loop — so it exits early when the field is **false**. It cannot loop either way:
the marker bounds it to one emission.

Cost also stated: in a goal loop the meter is **one Stop late**, since the loop's first Stop has
`stop_hook_active: false`. The reported run had ~10 firings, so this is immaterial.

### 4.3 Per-channel delivery markers

The once-per-tier marker is keyed `(session, window, tier)` (`context_meter.py:641-643`). Left
alone, the mid-turn arm delivering the directive at 70% would **consume the marker and permanently
silence the Stop arm** — reproducing the reported bug via the fix for it.

So the key gains a **channel**: `midturn` (PostToolUse + UserPromptSubmit) or `stop`. Each tier is
then delivered at most once mid-turn and at most once at the decision point — at most two messages
per tier per session, up from one. The existing monotonic rule (a directive satisfies the advisory
for the same window) is preserved **within** each channel.

### 4.4 What the messages say — state, not orders

Given probe 12, the text leads with the **fact** and names the route, rather than issuing an
imperative that reads like injected instructions:

- the reading and the window, as now;
- **"A handoff SATISFIES a LOOP goal — the work continues in a fresh window with a full context; it
  does not stop"** (AC4), phrased as a fact about how goals are judged, which probe 15 confirms is
  true;
- `/rawgentic:pane-handoff` named as the route that both hands off and clears the predecessor's
  guard, with `clear-prep` named as what it wraps (AC3 + AC6);
- **AC5:** the check-in tier says *write the resume prompt and verify the delivery gates*, not
  "look for a seam" — at 60% there is room to do that well; at 98% there is not.

AC4's line also goes where it is **trusted** rather than suspect: the goal-authoring skills
(`goalsmith`, `long-run-resume`), so every future loop goal carries it inside the condition the
evaluator reads. That is the half probe 15 validates, and the half that would have prevented the
reported run.

### 4.5 Tests

Black-box via subprocess with a real `Stop` payload on stdin (`docs/testing.md:5-8`): the nested
shape and correct `hookEventName` per event; silence at `Stop` when `stop_hook_active` is false;
delivery when true; the advisory tier never emitting at `Stop`; channel-keyed markers not
cross-silencing; and exit 0 with empty stdout when the state dir is unwritable at `Stop` — the
fail-open property probe 13 measured.

### 4.6 Deployment wiring — the surface that actually makes this live

Named explicitly, because a design that changes only Python would pass its tests while the installed
plugin never invoked the meter at `Stop` (WF5 finding 4):

- **`hooks/hooks.json`** — the existing `Stop` array (currently `wal-stop` alone) gains a second
  entry invoking `${CLAUDE_PLUGIN_ROOT}/hooks/context_meter.py` with no matcher (`Stop` takes none)
  and a 5 s timeout, matching its other two registrations.
- **`phase_executor/src/phase_executor/canary.py`** — `EXPECTED_REGISTRATION_DIGEST` is a
  length-framed sha256 over `hooks/hooks.json` **plus every script its commands reference**, so
  editing either invalidates it and fails ~20 canary tests. It is re-pinned in the same commit;
  that test IS this repo's registration guard, and it is the mechanism that would catch a missing
  `Stop` entry.
- **The repo is not the running plugin.** Sessions load from
  `~/.claude/plugins/cache/rawgentic/rawgentic/<version>/`, so no test in this repo can prove the
  installed plugin fires at `Stop`. The probes above are that evidence, gathered by registering the
  real event on a live binary. Stated rather than papered over: post-merge, "live" requires the
  plugin-update steps in `CLAUDE.md` §7 and a new session.

### 4.7 Compatibility

**CONFIRMED: this plugin declares no minimum Claude Code version** — `.claude-plugin/plugin.json`
carries none, and no README or docs sentence states one (searched; only incidental version mentions
in changelog prose).

`additionalContext` on `Stop` is a **recent** capability. `anthropics/claude-code#60993` records the
schema *rejecting* it (*"hookEventName: Stop is not a permitted value for hookSpecificOutput"*), and
`#65495` quotes the changelog entry adding it: *"Stop and SubagentStop hooks can now return
`hookSpecificOutput.additionalContext` […]"*. Measured working on **2.1.220** (probes 11, 12).

**Chosen behaviour on an older client: silent degradation of the Stop arm only.** No version check
is added. Reasons, weighed against the review's recommendation to gate explicitly: a version probe
on a hook that rides every Stop is cost without a payoff the user can act on; the meter's mid-turn
arm is unaffected and still delivers; and the hook exits 0 regardless, so the worst case is a
transcript notice rather than a broken turn or a blocked session. **INFERRED** — what would confirm
it is a run under a pre-change binary, which this host does not have. If that inference is wrong,
the failure mode is a visible per-Stop hook-error notice, which is noisy but not dangerous, and it
would be reported rather than silent.

---

## 5. Owner decision, 2026-07-29 — spawning a successor must be seamless

**Recorded because it changes scope.** The owner's instruction, verbatim: *"Spawning a new pane,
either with the skill or with the plugin, should not be asking the user. It should be seamless."*

This resolves what an earlier draft of this doc deferred as "a separate authorization question, out
of scope" — a deferral the WF5 review flagged as leaving the second reported failure unfixed (§7,
finding 2). It was right, and the owner has now decided it.

**What actually gates it — corrected.** An earlier draft of this doc said the halt was caused by
`pane-handoff`'s own per-step permission gates. That is **wrong**, and reading
`skills/pane-handoff/SKILL.md` shows why: the skill's "gates" are *verification* gates inside one
tested command (`launcher_lib.py ad-hoc-handoff`), which polls the successor's own on-disk artifacts.
It asks the user exactly once, and only when the request is genuinely ambiguous between retiring the
pane and keeping it (`SKILL.md:123`).

The real lever is the skill's own trigger language. Its `description` says: *"ALSO **offer** it
unprompted when the context-meter reminder reaches its directive tier"* — so a model following the
skill correctly **offers** and waits. Combined with a meter directive that names `clear-prep` (which
produces the payload but neither clears the guard nor spawns the pane), a session that obeys every
instruction perfectly still stops without a successor. That is exactly what happened overnight.

**The change:** `offer` becomes `run`, and the description stops advertising itself as "all gated"
when what it means is "each delivery step is verified". Text only, in the one file the issue already
scopes.

**Left alone deliberately:** the single ambiguity question at `SKILL.md:123`, and the teardown
default. Those are about *which* handoff shape is wanted, not *whether* to hand off, and the
retirement default already exists because getting it wrong burned a real run (`SKILL.md:97-99`).

---

## 6. The second live report, folded in (2026-07-29)

The owner reported a second failure the same day, in `thewanderinginn` (pane `w1:pDP`): a session
stopped overnight *at a clean task boundary*, wrote its handoff file, and then **asked permission**
to spawn the successor — *"say the word and I'll run it"* — so the run stopped until morning.

That is the **opposite** end of the same gap from the lumenquire run in the issue body. Lumenquire
never broke and burned the window to 98%; this one broke correctly and never resumed. Both end with
no successor and a human in the loop.

**CONFIRMED** (from that session's own report, quoted by the owner): the handoff file was written;
the pane was not spawned; it ended its turn asking. **INFERRED** (that transcript not read): the
mechanism is §5's — the skill says *offer*, and the meter points at `clear-prep`.

Together the two reports bracket the fix: §4.4 stops the 98% case, §5 stops the waiting case.

---

## 7. WF5 review dispositions

Independent cross-model review (Codex, effort high): 7 findings, 0 Critical, 3 High, 4 Medium.
Report: `docs/reviews/2026-07-29-713-goal-loop-vs-context-meter-md-2026-07-29.md` (gitignored, so
the dispositions live here).

| # | Sev | Finding | Disposition |
|---|---|---|---|
| 1 | High | The `stop_hook_active` gate cannot guarantee zero extra turns — if `/goal` stops on an eligible firing, the meter becomes the sole continuation | **ACCEPTED.** §4.2 rewritten: claim corrected, Stop arm narrowed to the directive tier, and the residual cost stated as at most one extra turn per session per window, deliberately accepted. Its recommendation to drop the Stop arm entirely was **declined** — that abandons AC3's purpose, and the residual cost occurs only where a handoff is the wanted behaviour |
| 2 | High | Unattended successor authorization excluded, yet it caused the overnight halt | **ACCEPTED, and no longer deferred** — owner decision §5, implemented as the `offer` → `run` change. The review's diagnosis was right; this doc's own attribution of the gate was wrong and is corrected in §5 |
| 3 | High | "Settled; not to be re-litigated" suppresses review of AC4 | **ACCEPTED.** §2.4 split into two questions; the suppressing sentence is gone; AC4's efficacy is now evidence-backed by probe 15 rather than asserted |
| 4 | Medium | No named deployment-wiring surface; changes could pass while the plugin never fires at `Stop` | **ACCEPTED.** New §4.6 names `hooks/hooks.json`, the canary registration digest, and the repo-≠-installed-plugin limit. The recommended installed-plugin integration test is **declined as infeasible** in this repo; the live probes are the substitute and are named as such |
| 5 | Medium | A one-message `Stop` read lag is not harmless to a band trigger | **ACCEPTED.** §2.1 no longer claims harmlessness; the exposure is narrowed and named, and Stop delivery is declared best-effort |
| 6 | Medium | AC4's evaluator behaviour unproven | **ACCEPTED — probe 15 added**, with the evaluator's verbatim verdict and its limits |
| 7 | Medium | No stated minimum supported Claude Code version | **ACCEPTED in part.** New §4.7 records that none is declared and names the degradation. An explicit version check is **declined** — cost on a per-Stop hook with no user-actionable payoff; the reasoning is stated so the trade-off is auditable |

---

## 8. What was NOT checked

- No run under a Claude Code older than 2.1.220, so §4.7's degradation claim stays inferred.
- The 8-continuation cap was never driven to its limit, with or without the meter.
- `hooks/wal-context` and `hooks/session-start` were not probed; only `context_meter.py`'s own shape
  was measured and fixed here.
- No probe used a subagent, so `_is_subagent`'s behaviour at `Stop` is unmeasured. The guard is
  inert when its keys are absent, so it cannot break the hook, but "a subagent's Stop payload
  carries `agent_id`" remains unverified for this event.
- The size of the `last_assistant_message`-vs-transcript lag at `Stop` (§2.1) was not measured, nor
  whether it is nonzero at all on this version.
- Probe 15 used one wording on one model. The shipped AC4/AC5 wording is not itself probed.
- Neither reported failure was reproduced end-to-end after the fix; that needs a long real run.
