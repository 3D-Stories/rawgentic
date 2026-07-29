# Design — #700: a callable ad-hoc pane handoff

**Issue:** #700 (epic #626). The gap is **exposure, not capability**: `perform_handoff`
(`hooks/launcher_lib.py:897`) already encodes the whole gated sequence #694 settled, and nothing
exposes it for an ad-hoc caller. This document decides the exposure.

## What is actually missing — confirmed by reading

- `perform_handoff` (`hooks/launcher_lib.py:897`) takes every input as a keyword argument and
  needs no campaign at all. It is already the right implementation.
- `_cmd_handoff` (`hooks/launcher_lib.py:2514`) cannot serve the ad-hoc case. It opens
  `args.driver_state` as its FIRST act (`:2522`), derives the resume prompt from
  `driver_lib.fresh_session_handoff`, and gates on `fresh_session_available(...,
  launcher_armed=args.launcher_armed, ...)` (`:2603`). An ad-hoc handoff has no driver state, no
  campaign disposition and no armed launcher, so all three refuse before any pane is split.
- The CLI has **ten** subcommands today (`main`, `hooks/launcher_lib.py:2852`): `handoff`,
  `mid-child-handoff`, `retire-predecessor`, `read-goal-condition`, `record-child-outcome`,
  `build-fallback`, `select-mode`, `build-split`, `verification-steps`, `goal-text`. (The #700
  handoff brief says eleven; the argparse block says ten. Counted from the source, not the prose —
  the count is not load-bearing for this design either way.)

## The decision

**One new subcommand, `ad-hoc-handoff`, that is a pure argument adapter onto `perform_handoff`.**
No new sequencing logic, no new gate, no second send path. The skill shells out to it and does
nothing else.

### Inputs

| Flag | Required | Why |
|---|---|---|
| `--anchor-pane` | yes | the pane to split FROM — the caller's own pane |
| `--project` | yes | `perform_handoff` builds SEND 1 from it; a bare bind enters the switch skill's list mode (#682) |
| `--project-path` | yes | binds `project_switched` to the registry's own `project_path` as well as the name (was optional in the first draft — review finding) |
| `--cwd`, `--project-root` | yes | `build_split_argv` validates containment |
| `--name` | yes | the herdr agent name for the successor |
| `--registry` | yes | `claude_docs/session_registry.jsonl` — the `project_switched` artifact |
| `--transcript-dir` | yes | `~/.claude/projects/<slug>/`; must already exist |
| `--resume-prompt` XOR `--resume-prompt-file` | yes | the work |
| `--goal-condition` XOR `--goal-condition-file` | yes | the successor's guard |
| `--prompt-marker` | **yes** | see below |
| `--teardown-predecessor` | no, default **OFF** | AC4 |

**Both text inputs accept a file.** A resume prompt and a real goal condition are routinely
multiline — §7.2 of the herdr runbook cites a 2847-char, 41-newline condition — and putting that
through argv from a skill body is the backtick/quoting hazard this repo already writes
`git commit -F <file>` to avoid. One four-line reader serves both.

**`--launch-mode` is deliberately NOT exposed.** `LAUNCH_MODES` (`hooks/launcher_lib.py:126`) has
exactly one member, `fresh`. A flag with one legal value is noise; `perform_handoff`'s own default
is already `fresh`.

### `--prompt-marker` is REQUIRED, and that is the one non-obvious call

`perform_handoff` runs the `prompt_landed` check **only when `prompt_marker is not None`**
(`hooks/launcher_lib.py:1225`). So an optional marker would let the ad-hoc caller ship with the
prompt gate silently absent — the exact "rc 0 proves transport, not arrival" hole #665 added that
check to close, and AC3 requires the gate to be live and readable from the returned record.
Requiring it is fail-closed: the caller must name a token it knows is in its own prompt.

Three validations, all in the subcommand, all before anything is sent:

1. **Non-empty and present in the prompt** — already `perform_handoff`'s (`:1030-1040`), reused.
2. **At least `PROMPT_MARKER_MIN_LEN` (8) characters** — review finding: the contract is membership,
   not distinctiveness, so a marker like `the` would match unrelated tail content and pass the gate
   before the prompt ever submitted. A length floor is a heuristic and is documented as one; the
   skill's own rule is a token unique to the handoff, `[handoff-700]`-shaped.
3. **No control characters, newline included** — and it is not cosmetic. The transcript is
   JSONL, so a literal newline inside the prompt is stored **escaped** as `\n`. A marker carrying
   a real newline can therefore never match `transcript_has_marker`'s plain substring scan
   (`:573`), and `prompt_landed` would burn its whole poll budget and fail closed after a pane, a
   session and an armed guard already existed. `_reject_control_chars` (`:251`) already exists for
   this class of refusal.

### What is reused untouched

- The **ladder** is the canonical three-step launch ladder `_VERIFICATION_STEPS` (`:189`) —
  `spawned → project_switched → goal_armed`. `evaluate_verifications` (`:763`) refuses any
  non-canonical ladder outright, so this is not a choice so much as the only legal answer for a
  launch handoff. `prompt_landed` gates separately, as an early return, exactly as it does for the
  campaign path.
- The send order, the six gates, the ownership/cleanup machinery, and the #694 refusal of a
  self-binding resume prompt (`:1024`). **The subcommand weakens none of them** (AC5): it passes
  `resume_prompt` straight through, so a prompt containing `driver_lib.BIND_DIRECTIVE` raises
  before the split as it does today.

### Teardown defaults OFF (AC4)

`perform_handoff`'s own default is `teardown=True`, which is right for a campaign — a campaign has
a predecessor to retire. An ad-hoc handoff hands off *work*, not the caller's session, so the
subcommand passes `teardown=bool(args.teardown_predecessor)` with the flag defaulting to False.
Teardown stays gated on `teardown_allowed` regardless, so opting in never lowers the bar.

## The unsubmitted-Enter defect (issue comment, 2026-07-29) — fixed in `perform_handoff`

The owner drove the #694 sequence by hand to create this issue and hit a real failure:

| Send | Gate | Result |
|---|---|---|
| `/rawgentic:switch rawgentic` | session-registry row | confirmed ~24 s |
| the resume prompt | marker in the successor's transcript | **did not submit** |
| `/goal` | `goal_status met:false` | confirmed ~4 s |

Send 2's Enter was **consumed by the still-running bind turn** (`pane read` showed
`Cooked for 50s` with the prompt sitting unsubmitted as `[Pasted text #1 +9 lines]`). The recovery
that worked was **one bare `send-keys Enter`** — no re-paste, no truncation — after which the
transcript carried exactly one occurrence of the marker.

**Root cause: the registry row proves the bind LANDED, not that its TURN ENDED.** That is the same
reasoning error #694 corrected for `goal_status met:false`, applied to `project_switched` and left
standing.

### Decision 1 — the fix goes in `perform_handoff`, not in the new subcommand

The issue's out-of-scope list forbids changing `perform_handoff`'s **send order or gates**. A
bounded post-send recovery is neither: the order stays bind → prompt → goal, `prompt_landed` still
has to pass, and nothing is added, removed or weakened. It only lets a gate pass where the buffer
was intact all along.

It cannot live anywhere else. The subcommand does not send — `perform_handoff` does — so
"fix it only in the ad-hoc path" would mean re-implementing the send in the skill or the
subcommand, which is exactly what AC2 forbids and what #696 exists to document. The campaign path
has the identical window, so fixing it once where both callers route through is also the smaller
diff. **This is a deliberate, flagged reading of the scope line, taken on the owner's own
invitation in the comment.**

### Decision 2 — ONE nudge is not enough; the recovery is a bounded nudge LOOP

The comment suggests a single bare Enter. Walking the measured timeline shows one shot probably
misses:

- bind sent at t≈0; row appears t≈24 s, **turn still running** (observed cooking for 50 s)
- prompt pasted t≈24 s, its Enter swallowed
- `prompt_landed` polls `GOAL_POLL_ATTEMPTS`(12) × `GOAL_POLL_DELAY_S`(1.5 s) ≈ 18 s → fails t≈42 s
- a lone nudge at t≈42 s is **still inside the same 50 s turn**, so it is swallowed too

So the recovery is up to `PROMPT_NUDGE_ROUNDS = 4` rounds of {one bare `send-keys Enter`, re-poll
`prompt_landed`}, worst case ≈ 90 s before failing closed — comfortably past a first turn of the
observed length, and zero cost on the happy path because it only runs when the first poll fails.

Never a re-send of the text (double submission) and never a truncation (silent corruption) —
#696's rule, which this path must not quietly break. Each nudge is recorded as its own step
(`send_resume_nudge`) so AC3's "verifiable from the returned step record" still holds.

**Why not prevent it instead** by gating send 2 on the bind's turn having *ended*: there is no
artifact for "turn ended". #694 measured `agent_status` useless for exactly this (it read `idle`
immediately after a submit, `done` mid-turn, `working` on an empty input line), and scraping
`pane read` is the terminal-output gate this runbook forbids. Recovery is the honest option.

**Each nudge is gated on a fail-safe safety check** (review High 2 — a bare Enter would otherwise
accept whatever dialog happened to be on screen, and a count bound is not a privilege bound). See
the dispositions section below for the three parts and their honest limits. Anything other than a
clear "safe" resolves to *do not nudge*, which is exactly today's behaviour.

### Consequence for the subcommand

The recovery only runs when a `prompt_marker` was supplied, because without one there is no signal
to decide on. That is a second, independent reason `--prompt-marker` is **required** here: an
ad-hoc caller without a marker would get neither the gate nor the recovery.

## Triggering — the description carries mined vocabulary, and evals pin it

The second issue comment mined 94 transcripts from 36 h and found **seven reproaches** ("Why
didn't you finish the handoff?", "Why aren't you opening the new pane and passing the session
over?") — sessions that should have triggered and did not. It also confirmed **no pane-handoff
skill exists anywhere**, and that `clear-prep` produces the payload while the user-level `herdr`
skill drives raw primitives: **#700 is the missing middle.**

Consequences taken as requirements:

- **"pass off" is the dominant verb, not "handoff".** The `description` leads with the mined set:
  `pass off` · `passoff` · `pass-off` · `pass the session/prompt/goal over` · `pass everything
  over` · `send it/everything over to a new pane` · `hand it over` · `hand off` · `handoff` ·
  `new pane` · `new pain` · `another pane` · `sibling pane` · `herdr pane` · `herder pane` ·
  `start a new session` · `resume in a new pane` · `clear the context into a new session`.
- **Dictation variants are load-bearing, not noise** — "Herder" for `herdr` and "pain" for pane are
  how the fastest asks arrive, so they are in the trigger text verbatim.
- **Context pressure is a trigger with no utterance.** Worded precisely, because the review caught
  the draft claiming a capability the skill does not have: the skill cannot observe a meter. It
  reacts to the **context-meter hook's own system-reminder text** when that reminder reaches its
  directive tier — the threshold and window belong to `contextMeter` (#687; configurability #701),
  not to this skill. Pinned by an eval case, not by a mechanism.
- **Evals** in `skills/pane-handoff-workspace/evals/evals.json`, ≥3 cases, one per phrasing cluster
  (pass-off, send-over, terse "hand it over", context-pressure).

### Decision 3 — the skill CONSUMES `clear-prep`'s output, it does not invoke it

The owner thinks of the two as one motion ("Run a clear prep and then pass…"). But `clear-prep`
lives outside this repository (user/workspace level, like the `herdr` skill this issue's own
out-of-scope list excludes), so a plugin skill cannot depend on it being installed or CI-test that
it is. The skill therefore takes the payload as input, and when there is no resume prompt yet it
points at `clear-prep` to produce one. Coupling stays a pointer, not a call.

## Scope boundary: this is a LAUNCH, not a paste into a live pane

`perform_handoff` splits a new pane and starts a fresh agent through `herdr agent start`. That is
what makes `agent wait --until idle` usable and the whole sequence verifiable.

Runbook §7.1.2 currently says an ad-hoc handoff has **no readiness primitive available**. That
stays true for the case it was written about — pasting into an **already-running sibling pane**,
which is not a herdr-registered agent, so `agent wait` returns `agent_not_found`. This subcommand
does not serve that case and must not be described as doing so. The §7.1.2 rewrite (AC7)
therefore **splits** the section: the shipped skill for "spawn a guarded successor and hand it
work", the hand recipe retained for "deliver text to a pane that already exists".

## AC2 — the guard test, and what it can honestly assert

The risk the issue names is drift: a later edit "simplifying" the skill into direct sends would
reinstate #696's defect. The guard test therefore pins two things about
`skills/pane-handoff/SKILL.md`:

1. **The forbidden primitives appear NOWHERE in the file** — not `send-text`, not `send-keys`, not
   `pane run`, prose included. The skill points at runbook §7.1.2 for that discussion instead of
   restating it.
2. **The sanctioned invocation is present** — the skill must contain
   `launcher_lib.py ad-hoc-handoff`.

Both halves are needed: (1) alone passes on a skill with every command deleted; (2) alone passes on
a skill that calls the subcommand *and* hand-rolls a send next to it.

The first draft of this guard only scanned fenced and inline code, so prose could still instruct a
reader to hand-roll a send. The review named that hole; forbidding the strings outright is both
stronger and less code. Per repo mistake #6 the guard is anchored to the ONE skill file, never a
corpus regex.

## Test plan (red first)

| Test | Asserts |
|---|---|
| `--help` names the direct inputs | the subcommand exists and takes no `--driver-state` |
| in-process, `perform_handoff` faked | every argument arrives: anchor, project, prompt, condition, marker |
| default invocation | `teardown` arrives **False** (AC4) |
| `--teardown-predecessor` | `teardown` arrives True |
| resume prompt carrying the bind directive | rc 2, refusal names #694 (AC5) |
| marker absent from the prompt | rc 2, refusal names `prompt_landed` |
| marker containing a newline | rc 2, refusal names the control character |
| marker shorter than the floor | rc 2, refusal names distinctiveness |
| `--project-path` omitted | argparse refuses (required) |
| file and inline forms | produce identical calls |
| ladder | the call passes no `steps`, i.e. the canonical launch ladder |
| AC2 guard | as above, over the skill file |

For the nudge recovery, in `perform_handoff` (all with an injected runner, no live herdr):

| Test | Asserts |
|---|---|
| prompt lands on the FIRST poll | **no** nudge is sent — happy path unchanged |
| prompt lands only after one nudge | `ok` True, exactly one `send_resume_nudge` step, `prompt_landed` True |
| prompt never lands | at most `PROMPT_NUDGE_ROUNDS` nudges, then `failed_step == "prompt_landed"`, predecessor alive, tentative pane closed |
| any nudge | argv is `herdr pane send-keys <pane> Enter` and **no** `send-text` is re-issued |
| no `prompt_marker` | no nudge at all — there is no signal to decide on |
| the goal send | still happens only after `prompt_landed` passes (order preserved) |
| `agent_status` is `blocked` | **no** nudge; `failed_step == "prompt_landed"` |
| pane read shows a permission dialog | **no** nudge |
| pane read shows no paste affordance | **no** nudge |
| `pane read` itself fails | **no** nudge — unknown state is not a licence |
| a nudge's `send-keys` returns non-zero | `failed_step == "send_resume_nudge"`, loop stops |

## Cross-model review dispositions (WF5, gpt backend, 2026-07-29 — 8 findings: 2 High, 6 Medium)

Report: `docs/reviews/2026-07-29-700-adhoc-pane-handoff-design-md-2026-07-29.md` (gitignored).
Every finding was checked against the code before being accepted.

**High 1 — the marker contract is membership, not uniqueness. PARTIALLY CONFIRMED; accepted in
part.** The claim that there is no pre-send baseline and no binding to the new successor's
transcript is **refuted by the code**: `transcript_baseline` is captured before SEND 1
(`hooks/launcher_lib.py:1169`) and `_prompt_landed` scans only `_tail(read_text(transcript_path),
transcript_baseline)` (`:1227`) on a path built from the session id herdr returned for the pane it
just created. The **uniqueness** half stands: a marker like `the` would match unrelated tail
content and pass the gate before the prompt ever submitted. Accepted → the subcommand requires a
marker of ≥ `PROMPT_MARKER_MIN_LEN` (8) characters, single-line, control-character-free, and the
skill documents that it must be a token unique to this handoff (`[handoff-700]` shape). A length
floor is a heuristic, not a proof of distinctiveness, and is documented as such.

**High 2 — the nudge sends up to four unauthenticated Enters into unknown UI state. CONFIRMED, and
the sharpest finding.** Bounding the count does not bound the privilege of what an Enter might
accept. My original answer was to name it as residual risk; that is not good enough for an
unattended path. Accepted → each nudge is now gated on a **three-part, fail-safe safety check**,
and any part saying "no" cancels the nudge:

1. `agent_status` from `herdr pane get` is **not** `blocked` (machine-readable, not scraped prose).
2. `herdr pane read --source visible` shows a paste affordance — `[Pasted text` or
   `paste again to expand` (both strings live-confirmed, runbook §7.1.2 and this issue's comment).
3. That same read shows **no** permission-dialog signature (`Do you want to`,
   `and don't ask again`, `No, and tell Claude`).

Stated honestly: (2) does **not** prove the buffer is unsubmitted — §7.1.2 records that the same
affordance appears on successful submissions. Its job is narrower and it does that job: it proves
the pane is in an input-box paste state rather than sitting on a dialog. (3) is a denylist of
current Claude Code strings; a future reword would silently cost us that half, which is why (1)
and (2) carry it too. Every failure mode — read fails, output unrecognised, status unknown —
resolves to **do not nudge**, i.e. exactly today's behaviour.

**Medium — the skill's inputs have no stated discovery rule. CONFIRMED; every one is now pinned,
and the two that mattered were verified live rather than assumed:**

| Input | Where the skill gets it |
|---|---|
| `--anchor-pane` | **`$HERDR_PANE_ID`** — live-verified present (`w1:pDH`), and cross-checked against `herdr pane list`, whose row for that pane carries this session's own id. Fallback: the `pane list` row whose `agent_session.value` equals `$CLAUDE_CODE_SESSION_ID`. |
| `--transcript-dir` | `~/.claude/projects/<cwd with every `/` replaced by `-`>` — live-verified (`/home/rocky00717/rawgentic` → `-home-rocky00717-rawgentic`) |
| `--project`, `--project-path` | the session's own `claude_docs/session_registry.jsonl` row |
| `--cwd`, `--project-root` | the workspace root and the bound project's path |
| `--registry` | `<workspace root>/claude_docs/session_registry.jsonl` |
| `--name` | a herdr agent label the caller chooses for the successor |

The skill also requires `HERDR_ENV=1`; without herdr there is no pane to hand anything to.

**Medium — a failing nudge send is indistinguishable from poll exhaustion. CONFIRMED.** Accepted →
a non-zero `send-keys` on a nudge sets `failed_step = "send_resume_nudge"` and stops, rather than
looping on to report `prompt_landed`.

**Medium — `--project-path` optional weakens `project_switched`. CONFIRMED.** The skill derives it
from the registry anyway, so optionality bought nothing. Accepted → **required**, and passed
through, so the gate binds the row to name AND path.

**Medium — the AC2 guard accepts a prose instruction to hand-roll a send. CONFIRMED, and the fix is
simpler than what it replaces.** Accepted → the guard forbids `send-text`, `send-keys` and
`pane run` **anywhere** in the skill file, prose included, and the skill therefore refers to the
runbook rather than naming those primitives. That deletes the code-context parser and the hole in
one move; the positive assertion (the sanctioned invocation is present) stays.

**Medium — no evidence an installed plugin skill can execute the repo's Python or reach herdr.
PARTIALLY CONFIRMED; resolved by construction.** The "capability/manifest entry" framing does not
apply — plugin skills run bash directly, and `sync-security-patterns` already invokes
`${CLAUDE_PLUGIN_ROOT}/hooks/...` today. What was a real bug in my draft is that a bare
`python3 hooks/launcher_lib.py` only resolves when the session is bound to *this* repo. Accepted →
the skill invokes `${CLAUDE_PLUGIN_ROOT}/hooks/launcher_lib.py`, verified present in the installed
cache (`~/.claude/plugins/cache/rawgentic/rawgentic/3.105.3/hooks/launcher_lib.py`), so the skill
works from any bound project.

**Medium — the context-meter trigger is unimplementable as written. CONFIRMED as a wording defect.**
The skill cannot "observe a meter"; it reacts to the context-meter hook's own system-reminder text,
whose threshold and window belong to `contextMeter` (#687, configurability #701). Accepted →
reworded to that, and pinned by an eval case rather than a mechanism the skill does not have.

## Step-11 diff-review dispositions (WF5 on the merged diff, 2026-07-29 — 3 findings: 2 High, 1 Medium)

Run retrospectively, after #704 merged: the design gate had run but a pre-PR diff review had not,
and this change touches `perform_handoff`, which the campaign path shares. Report:
`docs/reviews/rawgentic-advdiff-700-diff-2026-07-29.md` (gitignored). Fixed in the follow-up PR.

**Medium — nothing binds the anchor pane to the calling session, and teardown closes that pane.
CONFIRMED, and the only genuinely new finding of the three.** Verified by reading: `perform_handoff`
validates the anchor's *shape* (`validate_pane_id`) and nothing else, so a stale or mistyped
`$HERDR_PANE_ID` with `--teardown-predecessor` would split from, and then close, a stranger's pane.
`retire_predecessor` already holds the right rule (`hooks/launcher_lib.py:2108`) — a destructive
target must prove it hosts the session claiming authority over it — and it was simply absent here.
Fixed: with teardown requested, the subcommand requires `$CLAUDE_CODE_SESSION_ID` (mirroring
`_own_session_id(require_env=True)`) and refuses unless `herdr pane get <anchor>` proves that pane
hosts it. Fail-closed on an unreadable probe. Scoped to the destructive request deliberately —
without teardown a wrong anchor is a recoverable mis-split, and demanding a live herdr probe for the
harmless case would refuse every environment without herdr.

**High — the 8-character floor does not deliver the uniqueness its own comment claims. CONFIRMED as
a weak mitigation; strengthened, not closed.** The floor admits an 8-character *phrase*, which is
ordinary prose. Tightened to require a single token (no whitespace), which the bind turn's own
transcript output — prose and tool JSON — will not contain in `[handoff-700]` shape. This is still a
heuristic and both the code and the skill say so; genuine structural uniqueness would mean the
subcommand mutating the caller's prompt to inject a nonce, which was considered and rejected as the
worse trade.

**High — a stale paste affordance plus an unrecognised dialog could still let an Enter through.
CONFIRMED as a residual risk; not fixed, and deliberately so.** The reviewer is right that the
positive signal is not reliable on its own. No better mechanism is available: the affordance is the
only durable evidence about the input box, `agent_status` is measured useless for this (#694), and a
structured transcript match was already falsified (`:573-582`). The reachable path is also narrow —
the nudge only runs when `prompt_landed` is false, so the affordance in view is normally the
unsubmitted prompt's own. Bounded at four Enters into a session we just created. **This is the one
claim in #700 most likely to be wrong**, and it is recorded rather than mitigated away.

## Risk

**Low–medium**, up from the issue's "low" because the recovery touches `perform_handoff`, which the
campaign path shares.

- The subcommand and the skill are purely additive — no regression surface there.
- The nudge is scoped inside send 2's existing failure branch, runs only when the first
  `prompt_landed` poll fails, and is bounded on rounds. A handoff that succeeds today never reaches
  it, which the first test above pins.
- `build_send_text_argv`, the send order, the ladder and the six gates are untouched.
- Residual risks, both named above rather than implied away: a nudge Enter could accept a pending
  permission dialog in the successor, and the AC2 guard reads code contexts so unbackticked prose
  could still tell a reader to hand-roll a send.
