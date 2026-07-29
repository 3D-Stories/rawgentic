# #718 — the meter INSERTS A PROMPT at the act tier

Design doc. Every load-bearing claim below is marked **confirmed** (with the command, file:line or
artifact that proves it) or **inferred** (with what would confirm it).

## 1. Verdict first

**Prompt insertion works. Build it.** AC6 was the blocking question — whether a queued message
drains between `/goal` turns at all, or only when control returns to the human — and it is now
answered from durable artifacts rather than scraped terminal text:

- **Prose inserted into a session with an UNMET `/goal` is acted on at the next goal-driven turn
  boundary, in 17 seconds, with no human input.** Confirmed, §2 Probe A.
- **A bare slash command inserted the same way is NOT consumed.** It survived five goal-driven
  turns and was taken up only after the goal was achieved. Confirmed, §2 Probe B.

So the design proceeds unchanged in shape, and AC3's "prose, never a bare slash command" is
**load-bearing rather than stylistic** — the wrong form of the same message is inert for exactly
as long as the loop it exists to interrupt keeps running.

One correction to the record: the predecessor session's third pane observation was
non-discriminating and the owner subsequently doubted the whole prose-versus-slash finding. That
doubt is now resolved in favour of the original theory, on evidence that discriminates.

The WF5 review then promoted two unmeasured integration facts to blockers, and **a second spike
answered both (§5b) — catching a bug that would otherwise have shipped green.** An `Enter` sent
from inside the hook immediately after the paste returns `rc 0` and **submits nothing**: the text
sat in the input box through two further goal turns and past goal completion. A 1.5-second delay
between the paste and the `Enter`, still inside the hook, does submit. So the design gains a delay
constant and a real submission check instead of trusting an exit code.

## 2. AC6 — the measurement

Measured 2026-07-29, herdr 0.7.5 (`herdr --version`), probe pane `w1:pDZ` split from this
session's own `w1:pDW` and closed afterwards. Probe session: Opus 5, xhigh,
`--permission-mode acceptEdits`, cwd a throwaway scratch directory outside the repo.

Insertion route: `herdr pane send-text <pane> <text>` then a **separate**
`herdr pane send-keys <pane> Enter` — the same pair `build_send_text_argv` already builds
(`hooks/launcher_lib.py:425`).

Probe task: create one `tick-<n>.md` file per turn, Write tool only. A real `/goal` was armed
whose condition was "done when `tick-N.md` exists", so every turn after the first was produced by
the goal guard re-prompting at `Stop`. Control never returned to a human. **No ESC was ever
sent.**

### Probe A — prose, goal UNMET, loop running → DRAINED AND ACTED ON (confirmed)

Goal #1 required `tick-1.md` … `tick-8.md`.

| Time | Event |
|---|---|
| 10:42:41 | inserted prose: *"okay, please also create a file named prose-marker.md containing just the word BANANA, then carry on with the tick files."* Newest tick at insert: `tick-6.md` |
| 10:42:46 | `tick-7.md` written — the goal loop advanced AFTER the insert |
| 10:42:58 | `prose-marker.md` written, content `BANANA`, **while `tick-8.md` did not yet exist** |

Evidence: file mtimes and contents, `ls -la --time-style=+%H:%M:%S`. The goal was unmet at the
moment the prose was acted on, so this is not "the loop ended and then it drained".

### Probe B — bare slash command, goal UNMET, loop running → NOT DRAINED (confirmed)

Goal #2 required `tick-16.md`.

| Time | Event |
|---|---|
| 17:44:48 | inserted bare `/tasklist`, nothing else. Tick count: 11 |
| ~17:45:0x | tick count 13 — **two goal-driven turns later, still queued**: transcript showed `❯ /tasklist` unconsumed and the footer read `Press up to edit queued messages` |
| — | the loop continued to `tick-16.md`, five turns after the insert |
| after `✔ Goal achieved (1m · 8 turns · 4.4k tokens)` | only then was it consumed |

### Probe C — bare slash at a MET goal → NON-DISCRIMINATING (recorded, not used)

A bare `/tasklist` inserted at 17:43:24 did execute — but `tick-8.md` landed at that same moment,
so goal #1 was already met and the loop was ending. This **cannot** distinguish "drains mid-loop"
from "drained because the loop ended", so it is not evidence either way. Recorded because the
predecessor's retracted mistake had precisely this shape, and because a reader comparing Probe B
and Probe C without this note would think they conflict.

### Probe D — prose asking for a skill → acted on, but not mid-loop

Prose inserted 17:45:42 asking the session to run the tasklist skill and write
`skill-marker.md` containing `CHERRY`: the file was written with that content. But `tick-16.md`
already existed at insert time, so the goal was met.

**Supports** prose → action. **Does not establish** skill invocation by prose *inside* an unmet
loop. That remains **inferred**, resting on the #718 lumenquire measurement (prose
`okay, please run /tasklist` → `Skill(tasklist) / Successfully loaded skill` in ~60s, a different
session on a different day). What would confirm it: a probe that inserts skill-asking prose while
a goal is provably unmet, with the skill's own side effect as the artifact.

### Corroborating, weaker

Five prose messages the owner typed into this session's own working pane while a `/goal` was armed
were each delivered mid-turn, surfaced alongside tool results. Consistent with Probe A, but weaker
evidence: human keystrokes rather than the `send-text` route, and the delivery boundary was a tool
result rather than a turn end.

## 3. Why insertion at all — what is already established

- **The meter hook executes nothing today.** Its entire output is `print(payload_out)` at
  `hooks/context_meter.py:1325`, a JSON envelope containing English. Confirmed by reading.
- **Injected hook text is DATA, not instruction.** #713 probe 12: a model named the injected
  directive as possible prompt injection and refused its imperative while faithfully reporting it.
  An inserted prompt arrives as *user input*, which is the one channel the model treats as
  authoritative. Confirmed (mempalace drawer `2026-07-29-713-probes`).
- **The launcher has no content generator.** `build_fallback_launch_argv` (`:449`) and
  `goal_text` (`:388`) both take content as input. A hook cannot author "here is what remains".
  Confirmed by reading. This is why only the *initiation* becomes deterministic — the issue's Out
  of Scope, and not negotiable.

## 4. The build

### 4.1 AC1 — `launcher_lib.py insert-prompt`

`launcher_lib.py` is the only home for terminal primitives
(`skills/pane-handoff/SKILL.md`, "The one rule that must not drift"), so the subcommand goes
there rather than in the meter.

```
python3 hooks/launcher_lib.py insert-prompt --pane <pane_id> --text <prose>
```

- A pure `validate_inserted_prompt(text)` that raises `LauncherError` when the text is empty or —
  **AC3** — when the stripped text starts with `/`.
- Reuses `build_send_text_argv(pane=…, text=…)` (`:425`) for the argv pair, so the proven
  send-text-then-separate-Enter route is not re-implemented.
- rc 0 on success; non-zero on a herdr failure, so the caller decides what a failure means.

**Why "starts with `/`" is the right discriminator, not a token count:** the failure mode measured
in Probe B is a message the *client* treats as a command rather than a turn, and that is decided
by the leading character. Prose that merely *contains* a slash command
(`okay, please run /rawgentic:pane-handoff`) is the intended shape and must stay legal. Both
directions get a test.

### 4.2 AC2 + AC4 — the meter call site

In `cmd_hook`, immediately after the emit at `:1325-1326` succeeds, and only when **all** hold:

1. `event == "Stop"` — AC4. The directive-only gate at `:1256` already guarantees the tier there.
2. `tier == "directive"` — the act tier only.
3. The emit's reservation at `:1311` was won — this is the AC2 marker gate,
   `~/.rawgentic/context-meter/<session>.<window>.directive.<channel>.emitted`. The insert then
   takes its **own** reservation on the `stop-insert` channel (§4.3) so a failed insert can be
   retried at the next `Stop` without re-emitting the nag.
4. `HERDR_ENV == "1"` and `HERDR_PANE_ID` is present.
5. A project config was actually resolved, and its kill switch is not off (§4.3, fail-closed).

**Fire at `Stop`, never mid-turn, and never ESC.** At `Stop` the turn has ended and nothing is in
flight; an ESC mid-turn can kill a running suite or a half-finished commit. The owner floated ESC
and accepted this reasoning.

**Ordering: insert AFTER the emit, not instead of it.** The emit is cheap and already proven; if
the insert fails, the text has still been delivered by the old channel. Deliberately additive.

**Subprocess, and why that is a change of stance:** the module's docstring notes that its only
mention of `subprocess` is a comment explaining why it does not use one. That stance is being
narrowed on purpose, not forgotten: one bounded call, on the `Stop` path only, at the directive
tier only, once per session per window, with every exception swallowed.

### 4.3 AC5 — once only, fail-open, kill switch

**REVISED after the WF5 review — findings H1, H2, M3 and M6 all landed here. See §7.**

- **Once only, on its OWN channel.** The insert reserves
  `marker_path(home, session_id, window, "directive", "stop-insert")` — a third value in the
  existing channel dimension (`midturn`, `stop`), not a new kind of store, so #687's
  "second source of truth" warning does not apply. **It is reserved immediately before the
  insert and RELEASED if the insert fails**, exactly as the emit path already does at `:1328`.
  Sharing the emit's `stop` reservation (the first draft) would have burned the single attempt
  on a transient herdr hiccup and left only the emit channel — the channel #713 proved a model
  may refuse. **[H1]**
  The guarantee is therefore once per `(session, window, directive, stop-insert)` — stated with
  all four keys because the marker's identity includes the channel, so "once per session per
  window" was simply the wrong claim. **[M6]**
- **Fail-open, every branch:** `HERDR_ENV` unset → skip. No pane id → skip. Subprocess raises,
  times out, or exits non-zero → release the reservation and skip. `__main__` already exits 0 on
  any exception (`:1403-1407`). A meter that cannot type must never block a turn.
- **Bounded means a number.** `INSERT_TIMEOUT_S = 5`, per `subprocess.run(..., timeout=...)` call,
  and the child is killed on timeout rather than left running. Two herdr CLI calls at ~50 ms each
  measured in §2, so 5 s is ~100× headroom while still bounding the worst case a `Stop` hook can
  add to a turn. **[M3]**
- **Kill switch:** `contextMeter.insertPrompt: false` in the project's `.rawgentic.json`, default
  **true**. Read through `read_meter_config` (`:916`), the sanctioned own-block read. Because
  `validate_setup_block` **refuses unknown keys** (`:455-459`), the key must also be added to
  `SETUP_BLOCK_KEYS` (`:424`) and documented in `docs/config-reference.md` — otherwise
  `/rawgentic:setup` would reject a valid block.
- **Insertion REQUIRES a resolved project config — fail-closed.** If `read_meter_config` returned
  no block because no project was resolved, **do not insert.** The kill switch is project-scoped,
  so default-on outside a project would be a switch the operator cannot reach, auto-typing an
  authoritative imperative into unrelated herdr sessions. This inverts the first draft, which
  called that "only asking" while §4.4 deliberately relies on the imperative being obeyed — the
  two claims could not both be true. Fail-closed here matches the house rule: a boundary that
  cannot evaluate its own guard refuses. **[H2]**

### 4.4 What the inserted prose says

Prose, naming the skill, carrying the reason and the room left, and explicitly not asking for
permission — because as *user* input an imperative is honoured rather than refused:

> Context is at 52% of the window (520,000 of 1,000,000 tokens). Please run the rawgentic
> pane-handoff skill now to pass this work to a fresh pane. Run it — do not ask first. If you are
> mid-task, finish the smallest safe unit, then hand off.

It must never begin with `/`, which §4.1's validator enforces on the way through.

## 5. Risk

Auto-typing into the owner's terminal. Bounded by: the AC2 marker gate, a second reservation on
the `stop-insert` channel, the directive tier only, `Stop` only, `HERDR_ENV` required, a resolved
project config required, and the kill switch. The text is inserted into the session's **own** pane,
so no other pane is touched.

The first draft accepted a residual risk here — insertion default-on in sessions outside any
rawgentic project — and argued it was tolerable because the prose "only asks". The review rejected
that, correctly: §4.4 depends on the imperative being *obeyed*, so "only asks" and "will be acted
on" cannot both be load-bearing. That path is now fail-closed (§4.3), and the residual is gone
rather than accepted.

## 5b. The M4/M5 spike — and the design-breaking bug it caught

Run 2026-07-29 after the WF5 review, in a scratch pane (`w1:pD0`) whose own
`.claude/settings.json` registered a throwaway `Stop` hook. The hook fired only when
`stop_hook_active` was true — the production condition — and recorded what it saw to a JSON file.
The owner's `~/.claude/settings.json` was never touched. Pane closed afterwards.

### M5 — the registered hook CAN type, but an immediate Enter DOES NOT SUBMIT

| Question | Answer | Evidence |
|---|---|---|
| M5a — does the hook inherit `HERDR_ENV` / `HERDR_PANE_ID`? | **Yes** | `spike-fired.json`: `HERDR_ENV=1`, `HERDR_PANE_ID=w1:pD0`, both rounds |
| M5b — may it spawn a subprocess? | **Yes** | `send_text_rc=0`, `send_keys_rc=0`, empty stderr, both rounds |
| M5c — can it submit a prompt into its own pane? | **Not with an immediate Enter. Yes with a delay.** | below |

**Round 1 — `send-text` then Enter back to back, both inside the hook (fired 18:00:14).**
Both calls returned 0. **The text sat in the input box, unsubmitted**, through two further
goal-driven turns (`tick-7` 11:00:39, `tick-8` 11:00:45) and past `✔ Goal achieved`. An
EXTERNAL bare Enter at 18:01:39 submitted it, and the turn then ran normally
(`hook-inserted-marker.md` = `MANGO`, task list up).

**This is the bug that would have shipped.** `rc 0` from `send-keys` proves the keystroke was
delivered, **not that anything was submitted** — the same gap `launcher_lib.py:673` already warns
about for `send-text`. A meter that pastes text nobody submits has achieved nothing, and every
gate in §4.3 would have reported success.

**Round 2 — `time.sleep(1.5)` between the paste and the Enter, still inside the hook
(fired 18:03:15).** **Submitted.** The input box emptied, the prose became a user turn, and the
goal loop continued (`tick-12` at 11:03:20).

So the hypothesis that produced round 2 — *"Claude Code does not read input while its hooks
execute, therefore any in-hook delay also fails"* — is **REFUTED**. Round 1 was a race between the
bracketed paste and the keystroke, not a hook-execution barrier.

**Honest limit:** 1.5 s is the only delay value measured. Smaller values are untested, so the
constant is not tuned — it is the one number with evidence behind it.

### M4 — prose asking for a SKILL is acted on inside an unmet loop

Round 2's prose asked for the tasklist skill *and* a file. At 12 of 16 ticks — goal provably
unmet — the session consumed it and acted: *"The task list was up but stale, so I refreshed it
instead of making"* a new one, in that turn.

**Evidence-quality caveat, stated because it is my own fault:** round 2's durable file artifact is
void. I deleted `hook-inserted-marker.md` when clearing for round 2, but the session still
remembered writing it and correctly declined to redo it — *"MANGO — I wrote it when you asked the
first time. Nothing to redo"*. So M4's mid-loop evidence is transcript-grade (input box emptied →
prose became a turn → task list refreshed in that turn), while the durable prose → skill + file
artifact comes from round 1, which was not mid-loop. Both halves hold; the single combined
durable artifact does not exist.

### What this changes in the build

1. **`insert-prompt` must delay between the paste and the Enter.**
   `INSERT_SUBMIT_DELAY_S = 1.5`, inside the subcommand, not left to the caller.
2. **Never claim success from `rc 0` — and do NOT pretend to verify submission either.**
   An earlier revision of this section proposed reading the pane afterwards and requiring the
   paste affordance to be GONE. **That was wrong on two counts**, both visible in
   `pane_shows_unsubmitted_paste`'s own contract (`:502-526`): it requires a **collapsed** paste
   marker that a short single-line prose message never produces, and its docstring records that
   the same marker appears on *successful* submissions. A check that cannot discriminate is worse
   than none, because it would report confidently either way.
   What ships instead: the pane read happens **before** typing, and only to veto — if a permission
   dialog is on screen, an `Enter` would accept somebody's dialog rather than start a turn, so it
   refuses (fail-closed; `_PERMISSION_DIALOG_SIGNATURES` reused). The return value is named
   `delivered`, never `submitted`, and its reason string says so.
3. **Revise the §4.3 latency budget.** Worst case is now 1.5 s of delay plus two 5 s timeouts plus
   one pane read — about 11.5 s added to a single `Stop`, once per session per window. Stated
   rather than buried: this is the price of a submit that actually lands.

## 6. What was NOT checked — and both former blockers are now ANSWERED

The review promoted two of these from footnotes to gates, and it was right to. **Both were then
measured (§5b) and both are answered** — and the spike caught a bug that would otherwise have
shipped, which is the whole argument for having run it.

1. **[M4] — RESOLVED.** Skill-asking prose IS acted on inside an unmet goal loop (§5b). The
   specific worry — that skill invocation defers while a file write does not — found no support;
   round 1's mid-loop failure was the unsubmitted Enter, not the content. Residual: no single
   durable artifact covers "skill + mid-loop" together (§5b caveat).
2. **[M5] — RESOLVED, with a design change.** The hook inherits the herdr env, may subprocess, and
   can submit — **but only with a delay between the paste and the Enter.** An immediate Enter
   returns `rc 0` and submits nothing. See §5b.
3. Whether a delay SHORTER than 1.5 s also submits. Untested; the constant is evidenced, not tuned.
4. Whether the inserted prose survives an auto-compaction landing between the insert and the next
   turn. Not probed.
4. The 8-consecutive-continuation cap was not driven to its limit with the meter registered
   (carried over from #713).
5. Neither #713 failure has been reproduced end-to-end after its fix; that needs a long real run.

## 7. WF5 review dispositions

`/rawgentic:adversarial-review … design`, gpt backend (Codex), 2026-07-29. Report at
`docs/reviews/2026-07-29-718-meter-inserts-prompt-md-2026-07-29.md` — **gitignored**, so the
dispositions live here. **Six findings, all six CONFIRMED against the code, none refuted.**

| # | Sev | Finding | Disposition |
|---|---|---|---|
| H1 | High | Reservation consumed before insertion while every insertion failure is silently converted to success — one transient failure exhausts the session's only attempt, leaving the emit channel a model may refuse | **ACCEPTED, design changed.** Own `stop-insert` channel, reserved immediately before the insert, released on failure (§4.3) |
| H2 | High | The project-scoped kill switch is unreachable exactly where insertion defaults on (no project) — auto-typing an authoritative imperative into unrelated herdr sessions; "only asks" contradicts §4.4 | **ACCEPTED, design changed.** Insertion now requires a resolved project config, fail-closed (§4.3, §5). The reviewer caught a contradiction between two of my own sections |
| M3 | Medium | "Bounded" specified no timeout, termination behaviour or worst-case `Stop` latency | **ACCEPTED, design changed.** `INSERT_TIMEOUT_S = 5` per call, child killed on timeout (§4.3) |
| M4 | Medium | The production message depends on invoking a skill under an unmet goal; only file-writing was demonstrated in that state | **ACCEPTED as BLOCKING.** Promoted to §6.1; a probe is owed before the call site is written |
| M5 | Medium | The probe proves manual herdr commands work, not that the registered `Stop` hook may spawn a subprocess, receives the env, resolves its pane, or can type while still executing | **ACCEPTED as BLOCKING.** Promoted to §6.2; this is the central integration and it is currently inferred |
| M6 | Medium | "Once per session per window" is not implied by a marker whose identity also includes `<channel>` | **ACCEPTED, claim corrected.** Now stated as once per `(session, window, directive, stop-insert)` (§4.3) |

No finding was rejected, and none was applied piecemeal — H1/H2/M3/M6 were folded together, and
M4/M5 gate the build rather than being written around.

### 7b. Step-11 diff review dispositions (pre-PR)

`/rawgentic:adversarial-review .rawgentic-advdiff-718.diff diff`, gpt backend, on commit `5b3f556`
BEFORE the PR was opened. **Four findings, 3 High. All four confirmed against the code.**

| # | Sev | Finding | Disposition |
|---|---|---|---|
| D1 | High | A launcher exit of 0 permanently retains the once-per-window reservation and reports `inserted`, though submission is unverifiable — so an rc-0-without-submission recurrence under a slower paste suppresses every retry for that window, leaving only the channel a model may refuse | **CONFIRMED, NOT FIXED — residual, owner-facing (see §7c).** It re-opens H1's hole for the *silent* case. The honest fix is durable verification (the inserted prose appearing as a user turn in the transcript) plus release-and-retry, which is a design addition needing its own measurement |
| D2 | High | The safety check is only a PRE-paste snapshot; it neither proves the composer is empty nor re-checks after the 1.5 s delay, so a dialog opening inside that window gets accepted by the Enter | **ACCEPTED, FIXED.** A second `dialog_veto()` runs immediately before the Enter; on veto it returns with the prose explicitly reported as pasted-but-UNSUBMITTED. The "composer is empty" half is NOT solved and is folded into §7c |
| D3 | High | The fail-closed guard checks only that `project_path` is truthy — an absent, unreadable or malformed config all reach the default `True`, so the project-scoped kill switch can be unreachable and insertion still enabled | **ACCEPTED, FIXED.** New `meter_config_readable()` distinguishes "healthy config, no block" (stay ON — the common case, this repo included) from "would not parse" (refuse). It found a real hole in the guard added for H2 one review earlier |
| D4 | Medium | The insertion outcome is discarded, so disabled / skipped / timed-out / failed are indistinguishable from success, contradicting the README's "never silent about being disabled" | **ACCEPTED, FIXED.** Every non-`inserted` outcome now goes to `_warn` naming the reason and stating that only the refusable text channel fired. `inserted` stays quiet because it is self-evidencing |

### 7c. Known residuals, stated rather than buried

1. **A silent non-submission still burns the window (D1).** If `send-keys` returns 0 but nothing
   submits — the round-1 failure mode, which the 1.5 s delay is measured to avoid but does not make
   impossible — the `stop-insert` reservation is held and no retry occurs for that window. The emit
   still delivered. **Mitigation in place:** the measured delay, and the second dialog check.
   **Real fix:** verify from the transcript that the prose became a user turn, then release and
   retry. Filed as a follow-up rather than guessed at here.
2. **A non-empty composer is not detected (D2, partial).** `send-text` appends, so a half-typed
   user draft would be submitted together with the inserted prose. No reliable "composer is empty"
   signal was found: `pane_shows_unsubmitted_paste` needs a collapsed-paste marker that short prose
   never produces, and its own contract says that marker also appears after a successful submit.
   Detecting this needs a measurement, not an argument.
