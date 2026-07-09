# Rawgentic Subagent-Dispatch Audit

**Question:** Are rawgentic's workflow subagents actually invoked at runtime, or does the orchestrator do the work inline instead?
**Method:** Evidence-first, hostile audit. Ground truth = runtime session transcripts on the claude-code host (via `ssh claude-code`, proxy-jump `charlie`→10.0.17.205). Scoped to **2026-07-05 → 2026-07-08**. Read-only — no rawgentic code modified.
**Artifact:** https://claude.ai/code/artifact/c071709e-ab1d-418d-9738-8c5d51681926
**Generated:** 2026-07-09 (recon 2026-07-08)

---

## VERDICT — REFUTED on dispatch reliability; implementation-inline is scope-by-design, not a defect

Split the suspicion in two:

**(1) "Subagents are unreliably dispatched" → REFUTED.** In the 3-day window, 25 active sessions in the dogfooded rawgentic monorepo emitted **383 interactive `Agent`-tool dispatches** + **236 workflow-subagents** (17 `Workflow` runs) = **576+ subagent executions**, at **~98% success** (20 error-ish results in 1,040 project-dir dispatches, mostly *user interrupts*, not failures). Every mandatory review gate fired: `rawgentic:rawgentic-reviewer` dispatched **154×**, on Opus, real findings, **zero mandatory reviews skipped**. The decisive rebuttal to "dead dispatches get treated as success": a Step-8a review that **died on a session limit was detected, re-run, and caught a real Medium bug** (`b226b0e9:825/835/884`). When a named agent was unavailable, the orchestrator **substituted** the bundled reviewer and the gate still held (`2e8baf04:815`).

**(2) "The orchestrator does the [implementation] work inline instead" → literally TRUE, but working-as-designed.** In **6 of 6 genuine WF2/WF3 run sessions, implementation was 100% inline** (~316 `Edit`/`MultiEdit`, ~85 commits; only reviewers dispatched). The bundled `rawgentic-implementer` ran exactly **2×**, both a hand-orchestrated post-review fix fan-out, never a canonical Step-8 path. This is not a broken dispatcher:
- **Null cost incentive** — `implementation` routes to **Opus**, the orchestrator's own model, so farming a task out buys only isolation/parallelism a serial single-issue run doesn't need.
- **Owner steering** — `CLAUDE.md:122` "prefer few/serial Claude subagents" + mempalace `feedback-serial-subagents-on-fable` ("run serially, prefer inline").
- **Optional by design** — per-task delegation "optional"; whole-issue "default-off" (`steps.md:954,957`).

Reading that deliberate cost/steering policy as "the machine is broken" is the **confirmation bias**. Notably, the owner's own memory shows they *corrected* an "under-delegating" tendency via steering — the concern was real enough to manage, and it was managed, not left broken.

**Adversarial integrity:** a dedicated skeptic + drift agent (15–31 independent server-side greps each) were tasked to break this verdict. It survived, but forced three factual corrections to the draft (see §Corrections) — including one to a headline claim of mine.

---

## Ground-truth dispatch counts (window: 2026-07-05 → 2026-07-08)

| Metric | Count | Source |
|---|---:|---|
| Active sessions (rawgentic monorepo) | 25 | transcript mtime + subagent activity |
| Interactive `Agent` tool dispatches emitted | **383** | `Agent` tool_use in main transcripts (= subtype histogram sum) |
| `Workflow` tool invocations | 17 | `Workflow` tool_use in main transcripts |
| Workflow-subagent executions | **236** | `<sid>/subagents/workflows/wf_*/agent-*.jsonl` (8 run dirs) |
| Total subagent transcripts executed | **576+** | filed transcripts + inline headless results |
| Dispatch success rate | **~98%** | 20 error-ish / 1,040 project-dir `Agent` dispatches |
| Genuine runtime dispatch failures | **0** | `REVIEW_DISPATCH_FAILED` disambiguation (below) |
| Mandatory reviews skipped | **0** | review-forensics + skeptic; 1 dead review caught & re-run |
| Genuine WF2/WF3 runs with 100% inline implementation | **6 / 6** | impl-forensics (~316 edits, ~85 commits, reviewers-only) |

### Interactive dispatch by subagent type (383 total)

| subagent_type | count | class |
|---|---:|---|
| `rawgentic:rawgentic-reviewer` | **154** | rawgentic role |
| `general-purpose` | 146 | generic worker (analysis/impl/review) |
| `fast-worker` | 20 | generic worker |
| `deep-reasoner` | 18 | generic worker |
| `Explore` | 17 | built-in helper |
| `codex:codex-rescue` | 10 | cross-model rescue |
| `claude-code-guide` | 6 | built-in helper |
| `reader-proto` | 5 | built-in helper |
| `<none>` | 5 | — |
| `rawgentic:rawgentic-implementer` | **2** | rawgentic role |

- rawgentic-named-role: **156** (154 reviewer + 2 implementer)
- generic workers: **184** (carry analysis + implementation + some review)
- built-in helpers: **28**

### Interactive vs headless

| Entrypoint | Sessions | Agent dispatches | Filed transcripts | Workflow-subagents |
|---|---:|---:|---:|---:|
| interactive (`cli`) | 18 | 348 | 340 | 236 |
| headless (`sdk-cli`) | 7 | 35 | 0 | 0 |

Headless dispatches **execute** (spot-check `5347b1d2`: **10/10** Agent calls have non-empty `tool_result`s) but headless does not file separate subagent transcripts; results are inline. This reconciles 383-emitted vs 340-filed and **refutes confounder #6** (headless does not break dispatch).

### Subagent model distribution (assistant-turn weighted)

| Model | Mechanism A (interactive) | Mechanism B (Workflow tool) |
|---|---:|---:|
| Opus (`claude-opus-4-8`) | 6,499 (55%) | 2,459 (45%) |
| Sonnet (`claude-sonnet-5`) | 4,621 (39%) | 455 (8%) |
| Fable (`claude-fable-5`) | 667 (6%) | 2,525 (46%) |
| Haiku (`claude-haiku-4-5`) | 119 (1%) | 0 |

- **Mechanism B runs 46% on Fable** = genuine model-cost savings. **Mechanism A review runs on Opus** (config routes `review: opus`) → isolation/parallelism benefit, not $ savings.
- **Haiku appears only in built-in `Explore`/`claude-code-guide` helpers**, never in routed review/impl/analysis → "never Haiku for coding" honored where it applies.

---

## The model-routing config reframes the "cheaper models" premise

`/home/rocky00717/rawgentic/.rawgentic_workspace.json` → project **`rawgentic`**: `modelRouting = {review: opus, analysis: sonnet, implementation: opus}`.

Because **review and implementation both route to Opus (the orchestrator's own model)**, the brief's "farm to cheaper/smaller models" premise is **only partially operative**: genuinely cheaper work = `analysis` (Sonnet) + down-routed low-risk impl tasks + Mechanism-B Fable subagents. Review + high-risk impl run on Opus — the benefit there is **context isolation + parallelism + independent review**, not model cost. Across the window, ~48% (8,387/17,345) of subagent assistant-turns ran on sub-Opus models. **Sample size: 25 sessions / 3-day window.**

---

## INSTRUCTED dispatch-point table (from workflow instructed-map agent; all quotes verified at file:line)

Legend: **Agent-tool** = Claude subagent; **Codex** = external different-model via Bash; **inline-only** = orchestrator itself.

**MANDATORY (gate cannot be opted out):**
- **WF2 Step 8a** — 2× `rawgentic-reviewer`, fires when ANY task is `riskLevel:high` — `steps.md:1031,1040` (spine:87 "mandatory").
- **WF2 Step 11** — 3-agent `rawgentic-reviewer` pre-PR review (≥1 in small-standard lane), **"NON-NEGOTIABLE"** — `steps.md:1224`.
- **WF3 Step 9** — 2-agent code review, **"NON-NEGOTIABLE"** — `fix-bug/references/steps.md:~396`. **This is the DRIFT row**: names `pr-review-toolkit:silent-failure-hunter` + `pr-review-toolkit:code-reviewer` (an *external* toolkit, not the bundled reviewer WF2 uses; it was absent during the window — see §Drift).
- **WF5 Step 4** — Codex adversarial review (the whole workflow); external different-model — `adversarial-review/SKILL.md:161-181`.

**ESCAPABLE / inline permitted (the loophole language):**
- **WF2 Step 4 design self-review** (sharpest): gate mandatory, dispatch conditional — *"When run as a subagent, dispatch it as a `rawgentic:rawgentic-reviewer`"* (`steps.md:598`) → a full WF2 run can satisfy Step 4 with **zero subagent**.
- **WF2 Step 2** analysis fan-out — *"for a trivially small change, skip the subagent spin-up and run items 1-6 inline"* / *"If a subagent errors, fall back to running that single analysis inline"* (`steps.md:376`).
- **WF2 Step 3** design — Agent tool for complex; *"For standard features, design inline"* (`steps.md:489`).
- **WF2 Step 8** implementation — delegation only when `implementation != inherit`; *"When the `implementation` role is `inherit` (default), Step 8 runs inline exactly as today"* (`steps.md:954`). rawgentic = `opus` so it **is** expected to fire.
- **WF2 Step 10** memorization — *"If no reusable patterns: skip entirely"* (`steps.md:1169`).
- All Codex/adversarial layers (peer-consult, adversarial-on-design/plan, diff review) are opt-in and **non-blocking on failure**.
- **WF1 create-issue** — zero mandatory dispatch, single-agent by design.

**Actual in-window:** the mandatory Claude-subagent gates (Step 11 / Step 8a review) fire — reviewer dispatched 154× on Opus with real findings. WF3 Step 9's drifted dispatch was never exercised (0 `pr-review-toolkit` dispatches in-window).

---

## PERMITTED-TO-SKIP / owner steering (confounder #1 — CONFIRMED, in two places)

From `/home/rocky00717/rawgentic/CLAUDE.md`, "Subagents and long-running work" (119–135):

- **:100** — "Never route coding subagents to Haiku."
- **:120** — "Default implementation subagents to **Sonnet**; reserve Opus (`deep-reasoner`) for genuinely hard reasoning… Never Haiku."
- **:122** — "Prefer few/serial **Claude** subagents (token burn; a session-limit hit kills all in-flight agents with vacuous results — treat `confirmedCount: 0` + empty body as a dead agent, not a clean pass). A concurrent Codex job is fine — different provider, different quota."
- **:132–134** — "High-stakes changes get an independent review. Preferred: the Codex CLI… when Codex's exec sandbox is unreliable (it often is), substitute an independent Opus reviewer subagent (`rawgentic:rawgentic-reviewer`) — an established, accepted substitution." → **explains why reviewer dispatch is 154× on Opus.**

From **mempalace long-term memory** (via workflow steering-evidence agent):
- `feedback-serial-subagents-on-fable` — prefer few/serial + inline synthesis; "one Sonnet digest agent ≈ 342k tokens."
- **Counterbalanced** by `feedback-orchestration-model` — "delegate reading/execution rather than doing it inline" — and a same-session owner correction that the assistant was **"under-delegating."**
- Context: **Fable was disabled by Anthropic during the window** → the orchestrator fell back to **Opus 4.8** (explains the Opus-heavy mix).

**Reading:** the runtime pattern — few/serial subagents, Opus reviewers as the accepted Codex substitute, Sonnet-default implementation, never-Haiku honored, inline for marginal work — is **exactly the prescribed middle band.** "Inline/serial" is obedience, not defect.

---

## Confounders — ruled out or attributed

| # | Confounder | Disposition | Evidence |
|---|---|---|---|
| 1 | Owner steers inline/serial | **CAUSE (confirmed)** | `CLAUDE.md:122,120` + mempalace `feedback-serial-subagents-on-fable` |
| 2 | Model/mode context | Segmented | 18 interactive / 7 headless; Fable disabled in-window → Opus 4.8 fallback |
| 3 | Session-limit deaths → vacuous "success" | **Handled at runtime** | dead Step-8a review **detected & re-run**, caught a real bug (`b226b0e9:825/835/884`) |
| 4 | Agent tool lacks per-call `effort` | **Known & handled** | dual-path workaround documented `steps.md:150` |
| 5 | Stale plugin cache | Not a factor | bundled agents present across all window versions (2.64.2→3.24.23); plugin auto-updated 3.24.22→3.24.23 on 07-09 |
| 6 | Headless state reconstruction | **Refuted as failure mode** | headless dispatch executes 10/10 (`5347b1d2`) |

### `REVIEW_DISPATCH_FAILED` disambiguation (the strongest pro-CONFIRMED lead — it collapses)
404 raw string occurrences, categorized: **101 source-echo** (dev sessions editing/grepping the skill that *defines* the marker), **1 "runtime"** that is actually a test-file assertion (false positive), **0** assistant failure-narrations. **Genuine runtime `REVIEW_DISPATCH_FAILED` events: 0.**

---

## Telemetry gap (brief's hypothesis — PARTIALLY refuted)

`run_records.jsonl` (77 records) has **no structured subagent/dispatch field** — CONFIRMED. But the schema is richer than assumed (`usage`, `goal_guard`, `lane`, `notes`, `extra`), and the free-text `extra`/`notes` **routinely document dispatch**: "Reviewers: 2x opus", "fable" ×26, "opus" ×37, "reviewer" ×103. So it is **unstructured and inconsistent, not a total blind spot.** The plugin does not *structurally* instrument its own subagent usage (actionable — see fix).

---

## Drift (confounder #5) — corrected by the adversarial pass

**WF3 Step 9 (`fix-bug/references/steps.md`, ~line 396 after edit #320/`4e6a723`) exclusively names `pr-review-toolkit:silent-failure-hunter` + `pr-review-toolkit:code-reviewer`** on a **NON-NEGOTIABLE** gate — **zero** references to the bundled `rawgentic-reviewer` that WF2 uses.

My draft called `pr-review-toolkit` "not installed" and the gate "dead-by-construction." The skeptic/drift agents corrected this:
- The toolkit was **absent during the audit window** (installed **2026-07-09T05:34**, *after* the window; it lives under `claude-plugins-official/`, which is why it's not in the rawgentic marketplace dir and was missing from `installed_plugins.json` at recon).
- **Runtime impact in-window: nil** — 0 `pr-review-toolkit` dispatches ever. In the one in-window WF3 run that reached review, the orchestrator believed the toolkit unavailable and **substituted the bundled `rawgentic-reviewer`** — the mandatory review still ran (`2e8baf04:815`). The gate held via improvisation, not by skill correctness.
- The drift **persists in the current cache (3.24.23) and the latest source**; edit #320 moved its line numbers without removing the reference. It is a needless single point of failure with **no declared bundled fallback** — not dead today, but fragile.

---

## Corrections forced by the adversarial pass (audit integrity)

The skeptic + drift agents (15–31 independent server-side greps each) were tasked to break the REFUTED verdict and spot-check load-bearing claims. The verdict survived; **three facts did not**:

| My draft claim | Correction | Effect |
|---|---|---|
| `pr-review-toolkit` "not installed" ⇒ WF3 gate dead-by-construction | Absent *during* window (installed 07-09, after); present now. In-window WF3 run **substituted** bundled reviewer — gate **held**. | **Strengthens** — mandatory review ran even with the named agent missing |
| Active version = `3.24.22` (efe95dd) | Correct at recon; plugin **auto-updated to 3.24.23** (876c22d) on 07-09. Window ran dozens of versions across the 2→3 boundary; bundled agents present throughout. | **Neutral** — dispatch counts unaffected |
| Low implementer use partly "adoption lag" (#164 landed 07-08) | **Disproven** — bundled agents present in every window version back to 2.64.2. Cause is workload-mix + null cost incentive. | **Refines** cause, not conclusion |

**Skeptic's honest bottom line:** tasked to find any real run that *skipped* a mandatory dispatch, it found **none**. Both candidate "failures" resolved in the plugin's favor (dead review re-run; missing toolkit substituted). It converged with the deterministic evidence: dispatch is reliable; the "inline" pattern is scope-by-design.

> Transparency: the two version/toolkit-timing corrections rest on the drift + skeptic agents' live server-side captures (2026-07-09) plus my earlier direct evidence. I could not independently re-run them afterward — the `ssh` proxy-jump (host `charlie`, 10.0.17.200) began refusing auth near the end of the session. Marked accordingly in "did not check."

---

## Forensics highlights

- **Implementation is inline in canonical runs:** impl-forensics found **6/6 genuine WF2/WF3 run sessions did 100% of implementation inline** (~316 `Edit`/`MultiEdit`, ~85 commits), dispatching only reviewers.
- **The 2 `rawgentic-implementer` dispatches are real and correct** — `b226b0e9:9760` (model **opus**, "Fix code findings F1-F4") and `b226b0e9:9776` (model **sonnet**, "Fix doc/schema findings F1,F4-F7") — per-task ceiling routing working (code→opus, docs→sonnet down-route); but both belong to a hand-orchestrated post-review fix fan-out, not a canonical Step-8 path.
- **Mandatory review is reliable:** review-forensics verified reviewer dispatches across sampled sessions (18d74b41=6, da21dd7d=3, 34285614=5, 795bfcc6=21) are genuine `Agent` tool_use with `subagent_type=rawgentic:rawgentic-reviewer`, carry `input.model` (opus for the rawgentic project), execute, and return substantial findings. The one genuine dispatch failure (#271 Step 11 R2, "opus temporarily unavailable") and its session-limit-killed R1 partner were both **relaunched to completion** — matching `run_records` #271 verbatim.
- **Dispatch errors (20/1,040)** are dominated by user rejections/interrupts ("The user doesn't want to proceed…"), plus session-limit deaths (caught & re-run) and **1** "agent type 'deep-reasoner' not found" in a non-rawgentic session context (`ef6d060f:426`). None indicate systemic dispatch unreliability.

---

## Root-cause ranking (for why implementation stays inline) — each falsifiable

1. **Null cost incentive** — `implementation` routes to Opus (= orchestrator model), so farming buys only isolation a serial run doesn't need. *Falsify:* route `implementation` to Sonnet and re-measure; dispatch should rise if cost drove the choice.
2. **Owner token-economy steering + design optionality** (`CLAUDE.md:122`, mempalace; per-task "optional", whole-issue "default-off"). *Falsify:* remove the steering and re-measure.
3. **Workload-mix / dogfooding** — window is plugin-*dev*, not app-*runs*; inline editing is correct there. *Falsify:* isolate genuine `/implement-feature` runs on a downstream app.
4. **Adoption newness** — *disproven*; bundled agents present in every window version (2.64.2→3.24.23).

---

## Cheapest fix that moves the needle

**Instrument dispatch in `run_records.jsonl`.** Add a structured `dispatches[]` array (role, subagent_type, model, effort, outcome, dead?) written at the sites the skills already log to session-notes (`steps.md` already writes `dispatch <role>: model <model>, effort <effort>`). One helper + existing audit-lines. It converts "did subagents run?" from 2.3 GB of transcript archaeology into a `jq` one-liner — and would make WF3's drift and any future dispatch regression self-evident.

## The claim most likely to be wrong — and it was

My draft called `pr-review-toolkit` "not installed" and framed WF3 Step 9 as a dead gate. The adversarial pass corrected it (absent *during* window, present now; gate held via substitution). The residual risk I'd still watch: a standalone WF3 run on a machine where neither the toolkit nor a substitution path resolves — the skill names no bundled fallback, so that path is untested.

## What I did NOT check (and why)

- **Non-rawgentic projects** — no in-window subagent activity outside the rawgentic monorepo.
- **Pre-window runs (before 07-05)** — out of scope.
- **A canonical WF2 run with a parallel-eligible, high-risk multi-task plan** — the one configuration that could distinguish "inline-by-design" from "won't dispatch when it should." No such run exists in-window.
- **Every one of 576 transcripts individually** — counts are file-level exhaustive; narrative reads sampled across representative sessions.
- **Serial-vs-parallel timing of the 154 reviewer dispatches** — confirmed they ran and returned; did not time-order them.
- **Final live re-verification of the version/toolkit-timing corrections** — `ssh` proxy-jump lost auth near session end; those rest on the workflow agents' live captures + earlier direct evidence.

---

## Work Breakdown Structure (draft)

**Phase 1 — Close the observability gap (~1.5d, high impact).** Make dispatch measurable.
- Add `dispatches[]` to the run-record schema (role · subagent_type · model · effort · outcome · dead?) — 0.5d, unblocks all.
- Emit it from the existing session-note audit-lines — 0.5d.
- Ship a `jq`/CLI report: dispatch rate by role/model/workflow — 0.5d.

**Phase 2 — De-risk the WF3 review gate (~0.5d, correctness).** A NON-NEGOTIABLE gate depends on an external toolkit with no declared fallback; it only held in-window because the orchestrator improvised a substitution.
- Add the bundled `rawgentic-reviewer` as WF3 Step 9's declared fallback (make the hand-done substitution explicit) — 0.3d.
- Startup preflight: assert every named `subagent_type` resolves, else name the fallback; fail loud, not silent — 0.2d.

**Phase 3 — Verify the inline-implementation choice on real runs (~2d, confidence).**
- Run 3–5 genuine `/implement-feature` runs on a downstream app with a high-risk multi-task plan (the config that tests inline-by-design vs won't-dispatch) — 1d.
- Classify the 146 `general-purpose` dispatches by brief (impl vs analysis vs review) to measure true farm rate beyond the bundled type — 0.5d.
- Decide whether inline-impl is intended policy; if so, document it in the skill (align doc ↔ behavior) — 0.5d.

**Phase 4 — Harden dead-agent handling (~1d, robustness).**
- Assert non-vacuous subagent returns (`confirmedCount:0`+empty ⇒ dead) — 0.5d.
- Record dead/retried dispatches in the new telemetry so reliability is trend-able — 0.5d.

---

*Provenance: counts from runtime transcripts under `/home/rocky00717/.claude/projects/**` on host claude-code; skill/config quotes from installed cache `rawgentic 3.24.22` at recon (auto-updated to 3.24.23 on 07-09). Read-only — no rawgentic code modified. Method: deterministic file/JSON analysis + a 6-agent adversarial verification workflow (instructed-map, steering-evidence, impl-forensics, review-forensics, drift, skeptic), each cross-checked against the primary transcripts.*
