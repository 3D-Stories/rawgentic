# The owner's 16 notes on executor economics — answered

> **Correction (2026-08-01, #791).** The dollar figures in this document — including the
> "$220 → $185, −16%" comparison — were computed with a rate card that was wrong in both
> directions. Fable was priced BELOW Opus 5 when it is actually 2x its per-token cost, which
> inverts the orchestrator-cost conclusion this doc drew. The card is corrected in
> `hooks/usage_capture.py`; the numbers below are left as written (this is a dated measurement
> record) but should not be used for cost decisions. Token counts and timings are unaffected.

**Written 2026-08-01 in response to the owner's review of the token-usage analysis
(`https://rawgentic-analysis-executor-tokens.vercel.app`).** Every answer below cites a recorded
artifact — executor receipts under `.rawgentic/runs/wf2-762-ff40b6d5/`, the routing table, the
run-record store, hook source at file:line, or the #762 retrospective
(`https://rawgentic-analysis-762.vercel.app`). Claims that are inferred rather than checked say
so, and name the spike that would confirm them.

**Hosted:** `https://rawgentic-analysis-owner-notes.vercel.app`. The committed `.html` beside
this file is the source of truth.

**UPDATED 2026-08-01 (second pass).** The owner replied to all 16 answers. Their replies changed
three of them and settled four decisions. **Read "The owner's replies" section at the bottom
before acting on any answer above it** — where the two disagree, the reply wins. Corrections that
matter most: answer 2's route-by-provider recommendation is REJECTED (route by phase); answer 6's
"codex seats are nearly free" is now FALSE (codex went weekly-only and the pool was exhausted);
answer 9's rate-card error is worse than filed (every row is wrong, verified against live
pricing).

## The headline first

**The owner's top suspicion was right.** The analysis-seat failures were budget-cap kills:
6 of the 8 failed Step-2 analysis dispatches in #762 died at the seat's $2 `max_budget_usd`
ceiling ("Reached maximum budget ($2)", killed at $2.01–$2.28) with the output file **empty** —
the spend was lost AND the partial work discarded, and the chain then re-paid cache warmup on the
next model. The other 2 were HTTP 429 (org monthly spend limit). Receipts:
`.rawgentic/runs/wf2-762-ff40b6d5/analysis/*/transport.stdout.txt`. The twist: the cap was
already raised to $10 by the #762 T3 retune, but two structural defects remain even at $10 — a
kill discards partials (no salvage), and the chain blind-retries the same oversized brief on all
three models. Answer 8 has the full economics.

**Verdict table** — one row per note, so nothing gets lost:

| Note | Topic | Verdict | Action |
|---|---|---|---|
| 1 | Why build → codex | Containment constraint — and now a single point of failure | #799 (urgent: codex quota gone) |
| 2 | Orchestrator vs seats | **CORRECTED by owner: route by phase fit, not by provider** | Recommendation withdrawn |
| 3 | New session, warm cache | Possible in principle, defeated in practice | New issue D (spike S3) |
| 4 | Why not Opus-reviews-Fable | Rule is cross-VENDOR; relaxation proposed | Owner decision 2 |
| 5 | Where the +71% went | Five measured mechanisms | Levers listed; #777 + issue F |
| 6 | Why cache writes/reads so heavy | Fresh session per dispatch | #794 — now an architecture gate |
| 7 | 5-hour-window guard at 90% | Endpoint CONFIRMED working (S1 done) | #792 — widened to codex weekly |
| 8 | Why analysis caps exist | Runaway brake, but only **11 days old** — kill semantics are the bug | #778 (tokens, not dollars) |
| 9 | Is the analysis trustworthy | Token counts yes, dollars NO — **every card row wrong** | #791 (S2 done) |
| 10 | Per-dispatch telemetry | Already recorded; aggregation missing | #777 joined to the epic |
| 11 | Executor in herdr panes | Panes DID spawn — transient splits that self-close | #795 |
| 12 | Are 35/50 the right thresholds | Owner set **55/75** | #797 |
| 13 | Fable vs Opus orchestrator burn | Volume, not price — but Opus is also 2x cheaper | Decided on price; A/B not run |
| 14 | Prose we could script | Eight candidates ranked | #796 — owner: "script what we can" |
| 15 | Diff review cost | Five research-backed fixes | #793 — owner: "do all that!" |
| 16 | Does the epic already cover this | Partly — map below | Nine issues now fill the gaps |

## The 16 answers

### 1. Why is build going to codex?

Because mutating dispatch is codex-only by an explicit allowlist, not by routing preference. The
build seat's primary is claude-sonnet-5 with chain opus-5 → gpt-5.6-terra
(`phase_executor/src/phase_executor/routing/rawgentic.routing-table.json`), but
`MUTATING_FS_SANDBOXED = {codex}` (#470 §2a — "until an FS-sandbox child ships") means the canary
refuses any mutating Claude composition with exit 6 before a process starts. Build work is always
mutating, so it always skips both Anthropic entries and lands on terra. This is a **containment
constraint**: codex is the only engine we currently sandbox for filesystem writes. #779 is the
adjacent issue. Confirmed at `phase_executor` canary source; the routing table names the chain.

### 2. How do we balance orchestrator-inline vs different models per phase?

Route by **provider economics**, not by phase symmetry. Two facts from the #762 data:

- Seats on the codex pool are nearly free with respect to the Claude 5-hour window — 132 of 135
  review dispatches ran on sol, build on terra (token-usage doc). Dispatch those aggressively.
- Claude-LANE seats pay a fresh-session cache warmup on every dispatch (answer 6). The analysis
  seat is the only mostly-Claude seat — and it is exactly the one that was failing.

Recommendation: cross-provider phases (review, design, build) stay on seats; same-model
reasoning (analysis) defaults to **orchestrator-inline**, with a seat only when isolation is
genuinely needed. Inline analysis reuses the session's already-warm cache instead of writing a
new one. This also dissolves most of note 8's cap problem: inline work has no per-dispatch
budget to die against.

### 3. Can a new executor session still use the cache?

In principle yes, in practice no — and that gap is fixable. Anthropic prompt cache is org-scoped
and keyed on exact prompt-prefix **bytes** plus model, not on session. But Claude Code injects
per-session bytes early in the prompt (the scratchpad path contains the session id; gitStatus is
volatile), so two dispatches never share a prefix and every fresh seat session re-writes its
~46k–132k token prompt at 1.25–2× price. Three levers, cheapest first: stabilize the seat prompt
prefix (frozen system prompt, volatile content last), reuse a per-run seat session (resume
instead of `session_policy: "fresh"`), or move Claude-lane analysis inline (answer 2). **Spike S3
confirms or kills the first lever:** dispatch two byte-identical briefs back-to-back and read
`cache_read_input_tokens` on the second's first turn. New issue D.

### 4. Why can't Fable-designed work be reviewed by Opus?

Under the current rule it can't because the rule is **cross-vendor**, not cross-model (D3,
ratified this epic): Anthropic-authored work → only codex reviewers eligible; if codex fails
twice, the review is reported NOT OBTAINED. The honest rationale: same-vendor models share
training lineage, so their blind spots correlate more than cross-vendor pairs. But
Opus-5-reviews-Fable-5 is far from self-review, and the current rule has a real cost — a
single-point dependency on the codex pool. Also worth noting: build diffs are now
terra-AUTHORED (answer 1), so Anthropic reviewers are **already eligible for build diffs** under
the existing rule. Proposal for the owner (this amends a ratified decision, so it is presented,
not self-decided — owner decision 2): the invariant becomes "reviewer model ≠ author model",
cross-vendor PREFERRED for design gates, same-vendor cross-model allowed elsewhere. That unlocks
Opus review capacity and removes the single point of failure.

### 5. Where did the +71% orchestrator growth come from, and where do we cut?

Five measured mechanisms, from the #762 retrospective:

| Mechanism | Measured cost | Lever |
|---|---|---|
| Mid-child handoff re-read tax | ~90M input tokens per child (~$40 nominal) | Rolling log summary; fewer handoffs |
| Step-4 ceremony (3 passes to a foregone budget-exhausted close) | 6 consecutive children, 7 identical owner answers | **Codify the close — biggest per-child saver** (owner decision 3) |
| Growing decision-log/notes re-read per leg | Grows with every leg | Rolling summary of the log |
| 13 stale pin-guards → red suite → extra full runs | 2 extra full-suite cycles in #762 | Derive pin-guard files into task allowlists mechanically (issue F) |
| Fresh-session cache warmup per dispatch | 46k–132k write + up to 3.1M read per dispatch | Answers 3 and 6 (issue D) |

One correction to the number itself: +71% **overstates** effective burn, because the input
column includes cache reads (charged ~0.1×). The dollar-true growth is smaller — but the window
throughput cost is real, because reads count against the 5-hour window.

### 6. Why is one analysis attempt 132k cache tokens written and 3.0M read?

Because every dispatch is a brand-new CLI session (`session_policy: "fresh"`). Turn 1 writes the
entire seat prompt to cache — observed 46k–132k tokens at 1.25–2× the base input price. Then the
seat loops, and **every subsequent turn re-reads the whole conversation so far from cache** —
observed 1.5M–3.1M cumulative reads per dispatch. Reads are cheap per token (~0.1×) but they are
not free, and they all count against the 5-hour window. Inline work in the orchestrator would
reuse the session's warm cache instead of paying the write again. The fix set is answer 3's
three levers; new issue D.

### 7. Build a guard that stops dispatching at 90% of the 5-hour window.

Agreed, and it is buildable today. Two confirmed mechanisms:

- `GET https://api.anthropic.com/api/oauth/usage` with the OAuth bearer token,
  `anthropic-beta: oauth-2025-04-20`, and a `User-Agent: claude-code/<version>` header (without
  the UA you hit a strict 429 bucket). Returns `five_hour.utilization` and `resets_at`, plus the
  seven-day bucket.
- Claude Code ≥ 2.1.80 pipes `rate_limits.five_hour.used_percentage` and `resets_at` into the
  statusline script's stdin JSON — zero API calls. The herdr pane footer **already renders these
  percentages live** (observed on a running pane: `💰 63.0%` and `📊 45.0%`) — that is the
  scripted reader the owner remembered; the guard should reuse that exact read path.

Prior art found on GitHub (owner's clue 2): `shirley-xue-2025/usage-guard` (external daemon,
pause file at 90%), `hiinaspace/claude-quota`, `minhvoio/ai-usage-monitors`, and
anthropics/claude-code issues 34199 / 31637 / 30930 / 21943 documenting the endpoint and the
statusline-stdin workaround. Design sketch: a pre-dispatch check in
`executor_routing_lib.py dispatch` — when `five_hour.utilization ≥ 90`, refuse Claude-lane
dispatches with exit 3 (`quota_wall`, retryable); codex lanes unaffected; plus a WF2 seam check
so the orchestrator itself pauses at a clean boundary instead of mid-child. **Spike S1** (5
minutes, curl with this account's token) unlocks it. New issue B.

### 8. Why do the analysis caps exist if they kill the work?

The cap is a runaway-spend brake — without one, a looping seat can burn without bound (the org
429s in the same receipt set show the monthly ceiling is real). The bug is not the cap's
existence; it is the **kill semantics**. Today a budget kill discards the partial output
(`output.md` empty — spend lost AND work lost) and the chain blind-retries the same oversized
brief on the next model at full price: opus → fable → sonnet, ~$12.6 wasted on one Step-2 in
#762. Every seat has a budget in the routing table (analysis now 10.0 after the T3 retune,
review 5.0, design 5.0, build 10.0), but budgets are only **enforceable on Anthropic lanes** —
codex lanes enforce a timeout instead (`phase_executor/src/phase_executor/contract.py:366`).
Analysis is the only all-Anthropic chain, which is why it is the only seat where the cap kills.
The balance the owner asks for: keep the brake, fix the semantics — salvage partial output at
the kill boundary, and on budget death re-scope the brief (or split it) instead of blind-retrying
it 3×. That is exactly #778, which the owner deferred post-epic. The same correlation ids failed
on all three models, proving the BRIEF outgrew the attempt — not a model fault.

### 9. Is the token analysis even trustworthy, given it measured us rebuilding ourselves?

The owner is right three times over:

- **Epic-children confound** — every measured child was heavy framework work on the system
  itself; normal feature children will look different.
- **Dogfood bias** — the system being measured was the system being rebuilt mid-measurement.
- **The rate card is wrong in BOTH directions** (found this session):
  `hooks/usage_capture.py:53-60` prices claude-fable-5 at $3/$15 — Sonnet rates — versus $10/$50
  actual (3.3× UNDER), and prices claude-opus-5 and claude-opus-4-8 at $15/$75 — Opus-4.1-era
  rates — versus $5/$25 actual (3× OVER). Cache rows are wrong proportionally.

Consequence: **token counts are real** (summed from transcripts); **every dollar column in
`run_records.jsonl` and the token-usage doc's "$220→$185, −16%" claim is unreliable**, and so is
any cross-model dollar comparison. Fix order: rate card first (issue A, tiny — spike S2 verifies
live pricing since the cached skill data is from 2026-06-24), then #777 aggregation, then re-run
the comparison on post-epic, normal-shaped children.

### 10. Prioritize granular per-dispatch telemetry.

Mostly already there — the gap is aggregation. Every executor observation already records
`usage`, `timing_ms`, and `queued_ms` per dispatch (that is what the retrospective was built
from). What is missing is exactly what #777 describes: aggregating per-dispatch usage into
run-records with per-PHASE attribution. #777 is filed but NOT epic-joined. Recommendation
(owner decision 1): join #777 into the epic EARLY — it feeds notes 5, 9, 12, and 13, and every
future comparison gets cheaper once it exists.

### 11. Launch executor deployments in herdr panes with live token tracking.

Partially exists already. The executor's supervised/mutating path launches in a terminal
backend, and this project's config sets `executorTerminalBackend: {build: "herdr"}`
(`projects/rawgentic/.rawgentic.json:14`) — **build-seat dispatches already get herdr panes**
(#638 HerdrBackend). What is invisible: sync non-mutating dispatches (analysis, review) run
headless. Proposal, per the owner's instinct to reuse the pane-handoff machinery: an option to
run supervised-with-pane for ALL seats, plus a live token ticker in the pane fed from the two
sources we already have — statusline stdin `rate_limits` for the window, observation `usage` for
the dispatch. New issue E.

### 12. Are 35% and 50% still the right handoff thresholds?

Thoughts, as asked — the trade is real and the answer is "not yet, and not blind." The
thresholds live at `hooks/context_meter.py:84-85` (`DEFAULT_CHECK_IN_PCT=35`,
`DEFAULT_ACT_PCT=50`, env-tunable). With 1M-token windows, 35% = 350k tokens. Raising them saves
handoffs — each mid-child handoff costs ~90M tokens of successor re-read — but two forces push
back: per-turn cache reads grow with context size (a fuller window makes EVERY turn more
expensive), and published research plus our own experience says instruction-following degrades
at 60–80% fill. Sequencing recommendation: (1) fix the re-read cost itself first (rolling log
summary — the tax shrinks no matter where the threshold sits), (2) land #777 so we can SEE
per-phase burn against fill, (3) then experiment, e.g. 45/60. One more data point for urgency:
today's meter fired the directive tier at 69%, well past the 50% act line — meter reliability is
#729/#734, both already in the epic.

### 13. Why does Fable burn so much more than Opus as the orchestrator?

**Volume, not price.** It looked like price because the rate-card bug (answer 9) masked it —
the card prices Fable BELOW opus-5 when it is really 2× opus-5 per token ($10/$50 vs $5/$25),
and both have the same 1M window. The measured difference is behavioral: always-on thinking,
longer turns, and more verification/tool calls per task → more turns, each paying a
full-context cache read. Whether Fable pays for itself in fewer loop-backs (fewer review
findings, fewer re-runs) is genuinely **untested** — that is a driver-bench A/B (spike S6;
#430 already exists as the bench harness candidate).

### 14. What prose in WF2/WF3 could we script instead?

Eight candidates, ranked by pain observed this epic:

| Rank | Candidate | Evidence |
|---|---|---|
| 1 | Version-bump ×4 + changelog scaffold verb | Every PR; 4 surfaces, one forgotten = red CI |
| 2 | Pin-guard-allowlist derivation | 13 stale pins slipped scoped verification in #762 |
| 3 | Step-12 PR-body asserts at AUTHORING time | The H1 slip on #781 fired only at end-of-run |
| 4 | Merge-verification bundle (squash SHA + issue closed + box ticked) | Hand-done every child |
| 5 | ci-wait verb on the check-runs API | Ad-hoc polling every PR |
| 6 | usage-capture-legs verb (issue → registry legs → summed usage) | Hand-done for #762's 5 legs |
| 7 | D6 sweep table emitter | Owed at every resume |
| 8 | Marker-writer verb for session notes | Every step of every child |

Each is small; they batch into one issue (F).

### 15. Is the diff review eating tokens, and is there a better way?

Two separate things are being conflated, and the owner's memory is half right. The code-review
**CI lane** was removed 2026-07-30 (owner decision — it duplicated WF2's own Step 11 and was the
single biggest PR wall-clock item). What remains is the **adversarial diff review**: the opt-in
cross-model (codex) layer at Step 11, configured via `adversarialReview`. On #762 it read the
full 3316-line patch inline, took ~8–10 minutes, and then FAILED with `truncated: true`
(correctly not counted as a pass — tokens spent, nothing gained). Research (exa, 2026-08-01:
Cloudflare's ai-code-review write-up plus five others) converges on five fixes:

1. Write per-file patches to disk and let the reviewer read selectively — never inline the
   whole diff.
2. Strip noise before review — lockfiles and generated files; OUR diffs carry regenerated
   diagram snapshots, DATA blobs, and stubbed-baseline.json.
3. Risk-tier: docs-only diffs skip the adversarial layer entirely.
4. Detect truncation and RETRY with a lower findings cap or chunked input, instead of
   terminal-failing.
5. Chunk per file with 3–8 context lines, then one aggregation pass.

Concrete rework of `adversarial_review_lib` diff mode; medium effort. Spike S5 (strip generated
files from the #762 patch, measure the residual) sizes it. New issue C.

### 16. Do future epic issues already solve any of this?

Partly. The honest map:

| Covered already | By |
|---|---|
| Notes 7/8 — caps and budget semantics | #778 (owner-deferred post-epic) |
| Notes 5/9/10/12 — telemetry aggregation | #777 (filed, NOT epic-joined — decision 1) |
| Note 12 — meter reliability | #729 / #734 (IN the epic) |
| Design-phase economics | #775 |
| Notes 1/2 — build path | #779 |
| Audit hygiene | #766 |
| Note 4-adjacent verification | #764 |

| NOT covered anywhere — new issues to file | From |
|---|---|
| A. Rate-card fix (tiny) | Answer 9 |
| B. 5-hour-window guard | Answer 7 |
| C. Adversarial-diff economics | Answer 15 |
| D. Seat cache-reuse | Answers 3/6 |
| E. Herdr pane live-visibility for all seats | Answer 11 |
| F. Prose→script batch | Answer 14 |

## Spikes — confirm before building

| Spike | Effort | Unlocks | Status |
|---|---|---|---|
| S1: curl `/api/oauth/usage` with this account's token + UA header | 5 min | Issue B | Pending |
| S2: verify live fable-5/opus-5 pricing on platform.claude.com | 10 min | Issue A | Pending |
| S3: two byte-identical dispatches back-to-back, read `cache_read_input_tokens` on the 2nd | 15 min | Issue D | Pending |
| S4: read the 8 failed analysis receipts | — | Answer 8 | **DONE** (budget kill ×6, 429 ×2) |
| S5: strip generated files from the #762 patch, measure residual size | 30 min | Issue C | Pending |
| S6: driver-bench Fable-vs-Opus-5 orchestrator A/B | larger | Answer 13 | Pending (#430 exists) |

## The plan — how this changes epic #756

The epic stays **PAUSED** (owner instruction, D35). Nothing below starts until the owner says
resume. What changes at resume:

1. **File issues A–F now** (they are review output, not epic work) — done in this same session,
   each citing the evidence above.
2. **Three owner decisions, presented together:** join #777 into the epic early (answer 10)?
   amend the review-pairing invariant to reviewer ≠ author (answer 4)? codify the Step-4
   budget-exhausted close (answer 5's biggest per-child saver)?
3. **At resume, the owed D6 sweep runs first** (#762 + #782 findings) and now ALSO triages
   issues A–F for epic membership — the sweep's queue-reassessment mandate covers them.
4. **Sequencing principle from answer 12:** cheap structural fixes (rate card, rolling log
   summary, pin-guard derivation) land before any threshold retuning or model A/B, so the
   expensive experiments measure a system that isn't bleeding from known holes.

## The owner's replies (2026-08-01, second pass)

The owner read all 16 answers and replied to each. Their replies are authoritative: where a reply
contradicts an answer above, the reply wins and the answer is marked corrected here.

### Four decisions, settled

| Decision | Owner's call | Status |
|---|---|---|
| Join #777 into epic #756 | **Yes, join early** | DONE — queued ahead of #766 in the epic body |
| Review pairing invariant | **Amend to reviewer ≠ author** (cross-model, not cross-vendor) | DONE — workspace manual updated, D3 superseded |
| Codify the Step-4 budget-exhausted close | **Codify permanently** | Filed as #798 |
| Merge the findings PR | **Merge it** | DONE — squash 727cf81, v3.113.1 |

### Three answers CORRECTED by the owner

**Answer 2 is REJECTED.** The recommendation was to route by provider economics. The owner:
"i dont want to route by providers, i want to route by what is best for each phase." The
recommendation is withdrawn. Phase fit is the routing criterion; quota pressure is handled by a
guard (#792), not by bending the routing philosophy around it.

**Answer 6's premise is now FALSE.** "Seats on the codex pool are nearly free with respect to the
Claude window" was true when written and is not true now. The owner: codex moved from a 5-hour
plus weekly limit to **weekly only**, and "it did today and used one of my free resets." Codex is
a scarce pool with its own budget. Consequence: #792 must guard the codex weekly pool and the
Claude 7-day window, not just the Claude 5-hour window; and the single-build-engine exposure
(answer 1) became urgent enough to file separately as #799.

**Answer 9 understated the damage.** Spike S2 verified live pricing at platform.claude.com on
2026-08-01: **every row of the rate card is wrong**, not the three originally named. Fable 5 is
$10/$50 (card said $3/$15); Opus 5, 4.8 and 4.7 are $5/$25 (card said $15/$75 — deprecated
Opus-4.1 pricing); Sonnet 5 is $2/$10 on introductory pricing through 2026-08-31 (card said
$3/$15); Haiku 4.5 is $1/$5 (card said $0.80/$4). Live pricing also has TWO cache-write tiers
(5-minute at 1.25x, 1-hour at 2x) where the card models one. Two further traps for any comparison:
the Sonnet introductory rate expires 2026-08-31, and Claude 4.7-and-later models use a newer
tokenizer producing roughly 30 percent more tokens for the same text, so raw token counts are not
comparable across model generations.

### Two questions answered with evidence

**"When did the caps get put in?" (note 8).** The owner's memory was right: they are recent. Per-call
caps arrived in commit `92fbb0d` on **2026-07-21** (`feat(executor): genuine quota detection +
per-call caps + classifier persistence (H5)`, #558/#567). The routing table itself predates them
by three days (`d8d6ae5`, #424/#432, 2026-07-18) and shipped without per-call budgets. So the caps
are **11 days old**, against months of running without them. The runaway they defend against has no
recorded instance in this repo's history; the failure they caused is measured. The owner's cost
comparison — "8/10 analysis phases failing is worse than 1/100 analysis sessions running away" —
stands on the evidence. Recorded on #778, which also carries the owner's requirement that any
surviving cap be **token-denominated, never dollar-denominated**.

**"I have never seen a pane spawn" (note 11).** They did spawn; they were invisible. Three
confirmations: `capabilities_lib.derive_capabilities()` returns `executor_terminal_backend = herdr`
for this project; `hooks/executor_routing_lib.py:485` routes the build seat to it; and
`.rawgentic/runtime/registry/jobs.json` carries `"terminal_backend": "herdr"` on four #762 build
jobs. The reason nobody saw them: `HerdrBackend.new_session` uses `pane split --current`, which
splits the **calling process's own pane** and inherits its tab, then `_close_and_verify` closes the
pane when the dispatch finishes. Some dispatches ran 18 seconds. A short-lived split in a tab you
are not watching, that tidies itself away, looks exactly like nothing happening. #795 therefore
needs durable pane labels and keep-open-on-completion, not just wider seat coverage.

### Answer 3 is now an architecture decision gate

The owner: "if we cant get cache working, we may need to abandon executor path and go back to
orchastrator with sub-agents." Spike S3 is promoted to the first acceptance criterion of #794 and
runs before any implementation. If the cache miss turns out to be caused by volatility inside
Claude Code rather than by our own prompt bytes, that is the trigger to cost the
orchestrator-with-subagents path honestly and put the architecture choice to the owner — not to
keep optimizing around it. The negative result gets reported, not buried.

### Note 13 resolved in practice

The owner: "fable is hard to justify as the limits are soooo much lower," and they switched the
orchestrator to Opus 5 mid-session. The pricing confirms the instinct: Opus 5 at $5/$25 is half
Fable's $10/$50 per token with the same 1M context window. The A/B (spike S6) would need
`driver-bench`, which already shipped (#430, closed) — so if the data is ever wanted it is a run
of existing machinery, not new construction. Not worth running to re-derive a decision already
made on price.

## Spike status (owner asked how these get tackled)

| Spike | Status | Where it lives now |
|---|---|---|
| S1 — OAuth usage endpoint | **DONE 2026-08-01** — works; `five_hour` 19.0, `seven_day` 66.0, dollar fields null so the guard must key on `utilization` | Result posted to #792 |
| S2 — live pricing | **DONE 2026-08-01** — every card row wrong | Result posted to #791 |
| S3 — cache reuse on identical briefs | Promoted to **AC1 of #794**, runs first | #794 |
| S4 — read the failed analysis receipts | **DONE** (previous session) | Answer 8 above |
| S5 — strip generated files from the #762 patch | **AC1 of #793**, sizes the rest | #793 |
| S6 — Fable vs Opus orchestrator A/B | Not scheduled — decision made on price; `driver-bench` exists if data is ever wanted | #430 (closed, shipped) |

No spike is homeless: the two cheap ones are done, two are the first acceptance criterion of their
issue, and one is deliberately not being run with the reason stated.

## Issues filed from this review

| Issue | Scope |
|---|---|
| #791 | Rate-card fix (every row) |
| #792 | Quota guard — Claude 5-hour and 7-day, plus the codex weekly pool |
| #793 | Adversarial-diff economics |
| #794 | Seat cache-reuse — and the executor-vs-subagents decision gate |
| #795 | Herdr pane visibility, durable labels, keep-open |
| #796 | Prose-to-script batch (eight verbs) |
| #797 | Context-meter thresholds to 55/75, rolling log summary, reload-vs-fill measured |
| #798 | Codify the Step-4 budget-exhausted close |
| #799 | Unblock Claude-lane mutating builds |
