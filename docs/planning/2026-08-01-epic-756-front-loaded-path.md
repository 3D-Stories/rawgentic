# The front-loaded path through epic #756

**Ten issues, in order, and exactly what you get after each one.** Owner decision 2026-08-01: the
nine issues from the executor-economics review are front-loaded ahead of the remaining original
children, ranked most impactful to least, with #777 folded into the same ranking.

**The principle in one sentence:** fix what every remaining child pays for, before running fifteen
more children through it.

## Where we are standing

| | |
|---|---|
| Children merged | **7 of 31** (#735, #733, #732, #758, #767, #765, #762) |
| Front-loaded block | **10** — the nine review issues plus #777 |
| Original tail, unchanged | **14** — #766 onward |
| Status | **PAUSED** by owner instruction. Nothing starts without a resume. |

The tail is untouched on purpose. Re-ranking work we have not re-examined would be motion, not
progress.

## Read this table first

Each row is one stage. **Cost** is build effort. **Pays back** is what it returns across the
fifteen children still to come. **Unlocks** is what becomes possible that was not possible before.

| # | Issue | Cost | Pays back | Unlocks |
|---|---|---|---|---|
| 1 | **#798** Step-4 close | S | One design pass and one owner escalation, per child | Unattended runs stop stalling on a foregone answer |
| 2 | **#791** Rate card | XS | Every dollar figure becomes true | Any cost comparison at all |
| 3 | **#794** Cache reuse | M + **GATE** | Fresh-session warmup on every dispatch | The answer to "is the executor path viable" |
| 4 | **#792** Quota guard | S | Whole attempts lost to a wall | Overnight runs that survive the window |
| 5 | **#777** Per-phase telemetry | M | Guesswork about where time and tokens go | Measuring every stage below this one |
| 6 | **#799** Claude build lane | S | Single-engine exposure | The terra-vs-Opus-5 bake-off |
| 7 | **#797** Thresholds + rolling summary | M | Roughly 90M input tokens per child | Longer, cheaper legs between handoffs |
| 8 | **#796** Prose to script | M | Per-child ceremony, eight ways | Mechanical steps that cannot be skipped |
| 9 | **#793** Diff-review economics | M | Eight to ten minutes per child, often for nothing | A review layer that finishes |
| 10 | **#795** Pane visibility | S | Blind dispatches | Watching a run happen |

## Stage 1 — #798, codify the Step-4 close

**Before.** The design gate runs its passes, exhausts its budget, and asks the owner what to do.
It has now done this on **six consecutive children**, and received **seven identical answers**.

**After.** Budget exhaustion after the maximum passes is a legitimate close, recorded loudly with
its pass count and unresolved findings. Nobody is asked again.

**You can now:** leave a run unattended through the design gate. This is the only issue in the
list that pays back on the very next child with no prerequisite whatsoever, which is why it is
first despite being small.

**Watch for:** the close must stay loud. A silent close is a worse defect than the escalation it
replaces.

## Stage 2 — #791, fix the rate card

**Before.** Every row of `RATE_CARD` is wrong. Fable is billed at Sonnet rates in our own
accounting; Opus 5 is billed at deprecated Opus 4.1 rates. Verified against live pricing
2026-08-01.

| Model | Card said | Actually |
|---|---|---|
| claude-fable-5 | $3 / $15 | **$10 / $50** |
| claude-opus-5 | $15 / $75 | **$5 / $25** |
| claude-opus-4-8 | $15 / $75 | **$5 / $25** |
| claude-sonnet-5 | $3 / $15 | **$2 / $10** (introductory, ends 2026-08-31) |
| claude-haiku-4-5 | $0.80 / $4 | **$1 / $5** |

**After.** Every cost figure the system reports is true.

**You can now:** compare anything to anything. Until this lands, every stage below produces
numbers that cannot be trusted, including the telemetry in stage 5 and the bake-off in stage 6.
It is one dictionary. It is second only because stage 1 needs no prerequisite at all.

**Watch for:** the Sonnet introductory rate expires 2026-08-31, and models from 4.7 onward use a
tokenizer producing roughly 30 percent more tokens for the same text. Cross-generation token
counts are not comparable.

## Stage 3 — #794, cache reuse, and the gate

**This stage is different from every other one.** It carries a fifteen-minute spike whose answer
can invalidate the four stages after it.

**Before.** Every dispatch is a brand-new session. Turn one writes 46k to 132k tokens to cache at
1.25 to 2 times price; each turn after re-reads the whole conversation, observed at 1.5M to 3.1M
cumulative reads per dispatch.

**The spike, first, before any implementation.** Dispatch two byte-identical briefs back to back
and read `cache_read_input_tokens` on the second one's first turn. Three outcomes:

| Result | What it means | What happens next |
|---|---|---|
| Cache hit | Prefix stabilisation works | Implement, keep the executor, continue down the list |
| Miss, our own volatile bytes | Fixable at our end | Quantify the work, then decide |
| Miss, volatility inside the harness | Not reachable from here | **Stop. Cost the orchestrator-with-subagents path and put the architecture choice to the owner** |

**You can now:** know whether the executor is worth further investment. That is the real
deliverable of this stage, and it is why the stage sits third rather than seventh.

**The dependency this creates:** stages 6 (#799) and 10 (#795) both invest further in the
executor. Neither should start until this spike returns.

## Stage 4 — #792, the quota guard

**Before.** Nothing stops a dispatch when the window is nearly gone. On 2026-08-01 the codex
weekly pool hit zero and recovering it cost a finite reset. The Claude seven-day window read 66
percent the same day.

**After.** A pre-dispatch check refuses at a threshold, per pool, with a retryable exit and the
pool named — so the orchestrator can route to the other provider instead of stalling.

**You can now:** start a long run without checking a dial first. Confirmed available today: the
usage endpoint returns `five_hour` and `seven_day` utilisation live. The dollar fields are null on
this plan, so the guard keys on utilisation.

**Scope correction already recorded:** this must guard the **codex weekly pool** as well. The
original framing assumed codex was effectively free. It is not.

## Stage 5 — #777, per-phase telemetry

**Before.** Per-dispatch usage and timing are already recorded in every observation. Nothing
aggregates them into run-records with per-phase attribution, so "where did the time go" is
answered by hand, once, after the fact.

**After.** Every run reports its own phase breakdown.

**You can now:** measure the effect of stages 7 through 10 instead of believing them. This is the
instrument the rest of the list is read with — and it is fifth rather than first because
telemetry multiplied by a wrong rate card is a confident wrong answer.

## Stage 6 — #799, open the Claude build lane

**Gated by stage 3.**

**Before.** `MUTATING_FS_SANDBOXED = {codex}` refuses every mutating Claude composition before a
process starts. Build work is always mutating, so every build in this epic has run on
gpt-5.6-terra. That is one engine, with no fallback, on a pool that hit zero this week.

The concern about that engine is evidence-backed, and it is narrower than it first looked.
**openai/codex#33816** (open, `model-behavior`) documents terra "fabricat[ing] process completion"
and launching duplicate commands, with the reporter noting it yields "3-4 background terminals
running the same build/test command." But the same issue reports **sol behaves identically**, and
false completion is a class property of current agentic models rather than a terra defect.

**After.** Claude-lane builds run with writes confined to the dispatch's own worktree, enforced
outside the agent — the owner's chosen option, accepted trade, reversible in one line.

**You can now:** run the bake-off. `BUILD_MODELS` **already** reads
`("claude-sonnet-5", "claude-opus-5", "gpt-5.6-terra")` — the competitor set exists in code and
has never been able to run, because two of its three entries are refused. External benchmarks
will not settle this for us: one harness puts terra 23 points behind sol, another puts them 1.7
points apart. Our own tasks are the only tiebreaker.

**Honest note:** this does not buy immunity from fabricated completion. It buys engine choice,
quota headroom, and measurability.

## Stage 7 — #797, thresholds and the rolling summary

**Before.** Each mid-child handoff costs roughly **90M input tokens** of successor re-read, about
$40 nominal, and #762 had four of them. The decision log grows monotonically and is re-read whole
every leg.

**Partly banked already:** the thresholds moved to **55/75** on 2026-08-01 and are live now,
verified against the running plugin. What remains here is the durable default, the rolling
summary, and the meter overshoot — today's meter fired its directive tier at 69 percent when the
act line was 50.

**You can now:** run longer legs that cost less to hand over. The rolling summary is the piece
that shrinks the tax regardless of where the threshold sits.

**Watch for:** raising the act line on a meter that already overshoots can push a real handoff
past the degradation point. Fix the overshoot in the same change or say why not.

## Stage 8 — #796, turn prose into scripts

**Before.** Eight mechanical procedures live as prose the orchestrator re-derives every run.
Ranked by pain actually observed this epic: version bump across four surfaces; pin-guard allowlist
derivation (thirteen stale pins slipped two gates in #762); PR-body asserts at authoring time (the
H1 slip fired after #781 had already merged); merge verification; CI wait; usage capture across
legs; the sweep table; session markers.

**After.** Each is a verb with a test.

**You can now:** stop losing a full gate cycle to a surface someone forgot. Prose can be skipped
under context pressure. A verb cannot.

## Stage 9 — #793, make diff review finish

**Before.** The adversarial diff layer inlines the whole patch. On #762 it read 3,316 lines, ran
eight to ten minutes, and then failed `truncated: true` — tokens spent, nothing returned.

**After.** Per-file patches on disk, generated files stripped, docs-only diffs skipped visibly,
truncation triggering a bounded retry instead of a terminal failure.

**You can now:** count the review layer as a gate that reports, rather than one that sometimes
costs ten minutes and returns nothing.

**Sequenced by its own spike:** strip the generated files from #762's patch and measure what is
left. If the residual is small, noise-stripping alone may be the whole fix.

## Stage 10 — #795, see the run happen

**Before.** Build dispatches already open herdr panes — four #762 jobs carry
`terminal_backend: herdr` in the registry. Nobody has seen one, because the backend splits the
caller's own pane and closes it on completion. An eighteen-second split in a tab you are not
watching looks exactly like nothing happening. Analysis and review dispatches are headless
entirely.

**After.** Durable pane labels, keep-open on completion, coverage beyond the build seat, and a
live token ticker fed from the two sources that already exist.

**You can now:** watch a workflow run. Last by throughput impact, and the one that changes the
day-to-day experience most.

**Gated by stage 3**, like stage 6 — it invests further in the executor.

## What the picture looks like at the end

Reading the "pays back" column as a whole, after stage 10 and before the tail resumes:

- Every remaining child costs **one fewer design pass and one fewer escalation** (1).
- Every number the system reports is **true** (2), and **attributable to a phase** (5).
- A run **survives its own window** (4) instead of dying at a wall.
- Builds have **more than one engine**, chosen on our own measurements (3, 6).
- Handoffs cost **materially less**, and happen **less often** (7).
- The steps that get skipped under pressure **cannot be** (8).
- The review layer **finishes** (9).
- And you can **watch the whole thing** (10).

Then the original fourteen — #766 onward — run through a machine that has stopped charging them
for its own defects.

## The one thing most likely to be wrong here

The ranking assumes the executor path survives stage 3. If spike S3 says cache reuse is
unreachable, then stages 6 and 10 are investments in an architecture we are leaving, and this
whole order needs re-deriving with the owner. That is the reason S3 sits at fifteen minutes and
third place instead of somewhere comfortable near the end.
