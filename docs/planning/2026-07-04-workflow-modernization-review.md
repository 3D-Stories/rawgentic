# Workflow Modernization Review — Findings & Proposals

Date: 2026-07-04 · Engagement: research + design, **report-only** (no implementation in this review)
Reviewed: rawgentic v2.53.0 @ main `6b7d009` · Reviewer: Fable 5 session per `claude_docs/prompts/2026-07-04-fable5-rawgentic-review.md`
Companion docs: [roadmap](2026-07-04-workflow-modernization-roadmap.md) · [interactive dashboard](2026-07-04-workflow-modernization-review.html)

**Evidence marking:** every load-bearing claim is tagged **[C]** confirmed (file:line / command output / live-fetched URL) or **[I]** inferred (with what would confirm it). External docs were live-fetched 2026-07-04 by research subagents; doc URLs cited inline.

---

## Executive summary

rawgentic's proven core is **4 of 13 numbered workflows** (WF1 create-issue, WF2 implement-feature, WF3 fix-bug, WF5 adversarial-review) plus the infrastructure utilities. All 12 recorded run-records are WF2 runs [C]; session-note history shows zero invocations of WF4/7/8/9/10/11/12 [C, bounded by available notes]. Meanwhile WF2's SKILL.md is 1,584 lines — 3× the official progressive-disclosure guidance — and the harness has grown primitives (/goal, plugin-bundled subagent definitions, worktree isolation, claude-code-action v1.0 GA) that replace hand-rolled machinery.

The five highest-leverage changes, in order:

1. **Deprecate 7 unused workflows + restructure the core 4 to <500-line SKILL.md spines** (AC12+AC4): removes ~3,476 lines (45% of corpus) from the maintenance surface and cuts WF2's per-invocation context load ~70%.
2. **Ship plugin-bundled subagent definitions with model+effort frontmatter** (AC1+AC5): replaces prose "dispatch with model: X" instructions with enforceable definitions; codifies the evidence-backed strong-model-on-top topology.
3. **Cut review-gate spend to what the telemetry supports** (AC6): single reflect at Step 4 for all lanes + built-in `/code-review` + WF5 Codex diff pass at Step 11 — the 3-judge panel has no measured wins over the lean spine (10/10 issues, 0 loop-backs, campaign 131–140).
4. **Build the multi-issue driver (#148) with a dependency DAG + campaign-level /goal** (AC2+AC3): first-class multi-issue runs instead of "first issue faithful, rest loose".
5. **Pilot claude-code-action for label-triggered headless WF2 runs** (AC8+AC10): the overnight end-state, on GA tooling.

Versioning verdict: the deprecations + WF2 restructure warrant **v3.0.0** with a written upgrade guide (AC11).

---

## Evidence base

| Source | What it grounds |
|---|---|
| `wc -l skills/*/SKILL.md` [C] | corpus 7,751 lines; WF2 1,584; only 4 `references/*.md` files exist (3 in implement-feature, 1 in fix-bug) |
| `docs/measurements/run_records.jsonl` [C] | 12 records, **all** `workflow: implement-feature`; file is untracked and not gitignored (`git check-ignore` exit 1) — telemetry lives only on this host |
| `claude_docs/session_notes/` grep [C] | invocation traces only for WF1/WF2/WF3/WF5 + switch/setup/add-exception/adversarial-review/interview-adjacent; zero for WF4/7/8/9/10/11/12, peer-consult, sync-security-patterns, new-project |
| `docs/design/` mtimes [C] | 7 of the workflow design docs untouched since 2026-03-06..11 (dependency-update, documentation, incident-response, performance-optimization, refactoring, security-audit, test-suite-creation); actively-evolved docs are feature-implementation, issue-creation, bug-fix, adversarial-review + the July design series |
| `claude_docs/session_notes/campaign-131-140.handoff.md` [C] | 10 issues merged 2026-07-04 via lean spine (1 design draft + 1 Codex adversarial pass + Step 11 Codex diff + scan), 0 loop-backs, 0 review-budget overruns, suite 1,631 passing |
| `.rawgentic_workspace.json` [C] | 29 registered projects; 2 with `modelRouting` configured (rawgentic, 3dstories-studio: `{review: opus, analysis: sonnet, implementation: opus}`); `headlessEnabled: false` everywhere |
| `.github/workflows/` [C] | `ci.yml` + `mirror-to-stars.yml` (STARS dual-publish confirmed in-repo) |
| Harness-primitives audit (subagent, live docs fetch, saved report) [C] | per-primitive capabilities + doc URLs, cited inline below |
| Grep of plugin call sites [C] | reflexion referenced in 8 skills (WF2 ×19 lines), superpowers:brainstorming in 4 (create-tests, setup, refactor, optimize-perf), Codex engine in WF5/WF13 + opt-in gates |
| Drift-guard inventory [C] | tests pinning SKILL prose: `test_wf2_clarity`, `test_skill_helpers`, `test_wf2_parallelism`, `test_wf2_impact_metrics`, `test_trivial_work_check`, `test_bind_command_expansion_free`, `test_interview_skill`, `test_shared_block_drift`, `tests/hooks/test_headless` (annotation counts), `test_model_routing_dispatch`, `test_peer_consult_registration`, `test_adversarial_review_registration` |

Caveat on usage evidence: session notes rotate/archive, so "zero traces" is **[C] for the retained notes** and **[I] for all time**. The three independent signals (run-records, notes, design-doc mtimes) all point the same way, which is why AC12 treats it as decisive.

---

## AC1 — Model delegation & effort escalation: the verdict

**Verdict: strong-model-on-top.** The top-level orchestrator should be the strongest model in play (Fable/Opus), delegating bounded mechanical work down to Sonnet — not a cheap model escalating up. Confidence: **high (~80%) for long-horizon coding/agentic work** like WF2; the honest carve-out is high-volume per-query request streams, where cascade/routing literature legitimately inverts the answer — a shape no rawgentic workflow has. "Escalate up" survives only as a *mid-loop consultation* pattern (Anthropic's advisor tool), never as the topology.

### The evidence

1. **rawgentic's own field data [C].** Campaign 131–140: Fable 5 orchestrated 10 issues end-to-end with `analysis: sonnet` down-routing and opus implementation/review — 10/10 merged with green CI, **0 loop-backs, 0 review-budget overruns** (`campaign-131-140.handoff.md`). The failure mode the loop-back budget exists to catch (bad design → rework) never fired under a strong orchestrator. No comparable run with a weak orchestrator exists in the telemetry [C], so the comparison is one-sided — but the strong-top runs put a hard ceiling on what a cheaper topology could save (see math below), and the downside risk is unbounded rework.
2. **Error asymmetry [I → argued].** An orchestrator error (wrong decomposition, wrong acceptance judgment, wrong resume point) poisons every downstream task and is detected late; a worker error is contained to one task and caught by the gates (tests, review, CI). Escalation-up additionally requires the weak model to *know* it is out of its depth — metacognition is precisely what distinguishes model tiers, so the cheap-top topology gates its own escape hatch on the skill it lacks. What would confirm: a controlled A/B on a 10-issue backlog; the roadmap's measurement plan (AC9) makes that runnable.
3. **Price math [C: pricing live-fetched 2026-07-04 by two independent research agents, both from platform.claude.com/docs/en/about-claude/pricing — they agree].** Opus 4.8 $5/$25 per MTok (cache read $0.50); Sonnet 5 $2/$10 intro through 2026-08-31, then $3/$15; Fable 5 $10/$50; Haiku 4.5 $1/$5 (barred by never-Haiku). Cache-aware modeled run (20 orchestrator turns, context growing to 200k under prompt caching ≈ 2.0M cache-read + 200k cache-write + 20k output; 5 subagent tasks à 50k in / 5k out; arithmetic script-verified by the research agent):
   - **A — Opus orchestrator + 5 Sonnet workers: $3.50/run**
   - **B — Sonnet orchestrator, 2 of 5 hard tasks escalated to Opus: $2.30/run**
   - The strong-top premium is **~$1.20/run (1.5×)** — and **one failed-and-retried run (~$2.30) erases two runs of savings**. If a weaker orchestrator causes even one extra retry per two runs, cheap-on-top is *more expensive* — before counting wall-clock and human review time. rawgentic's loop-back budget is exactly the retry cost in question, and the strong-top recorded rate is 0.
4. **Prompt caching is what makes strong-on-top affordable [C: cache hit = 0.1× input].** Without caching the same Opus orchestrator run costs $11.00 vs $4.40 Sonnet (a 2.1–2.3× premium); with caching it collapses to 1.5×. Naive "context × price × turns" math overstates the strong-top premium ~4×. The orchestrator's long context is the cache-resident part; workers are short-lived and cold — exactly where Sonnet's discount applies.
5. **External evidence [C, live-fetched, URLs per item]:**
   - **OptAgent 25-pairing benchmark** (arxiv.org/pdf/2601.20005 — the most direct test of this exact question: 5 orchestrator tiers × 5 specialist tiers): *"benchmark accuracy is primarily driven by the orchestrator tier… Upgrading the orchestrator yields substantial gains even when specialist agents remain small"*; *"under constrained budgets, allocating the most capable model to the orchestrator role yields the largest and most reliable performance return."* Weak-orchestrator configs collapse on complex tasks (53.9% → 0.0%); strong-orchestrator configs hold 91–95%. Caveats: preprint; domain is building-operations agents, not coding.
   - **Anthropic's production multi-agent research system** (anthropic.com/engineering/built-multi-agent-research-system): Opus lead + Sonnet subagents beat single-agent Opus by 90.2% on their internal eval — Anthropic's revealed topology preference. (Honest framing: an A/B vs a Sonnet-led topology was not published.)
   - **Claude Code's own design** (code.claude.com/docs/en/sub-agents): cost control is positioned at the *worker* level ("route tasks to faster, cheaper models"); the built-in Explore agent is capped at the session model — the tooling assumes strong-on-top, cheapen-downward.
   - **Magentic-One** (arxiv.org/abs/2411.04468): stronger reasoning model (o1) in the orchestrator outer loop, GPT-4o workers.
   - **MAST failure taxonomy** (arxiv.org/abs/2503.13657, 200+ traces across 7 frameworks): **41.8% of multi-agent failures are specification/system-design, 36.9% inter-agent misalignment** — 78.7% concentrated in exactly the work the orchestrator does; only 21.3% are verification failures. Orchestrator capability is where the failure mass lives.
   - **The counter-evidence, adjudicated:** FrugalGPT (arxiv.org/abs/2305.05176, up to 98% cost cut at GPT-4 quality) and RouteLLM (~95% quality routing 85% of queries cheap) are *per-query cascade/routing* results — the router classifies one request; it never decomposes, sequences, or synthesizes long-horizon state. Anthropic's advisor tool (platform.claude.com/docs/en/agents-and-tools/tool-use/advisor-tool) is the strongest escalate-up primitive — a cheap executor consulting a strong advisor mid-generation — but its own data shows the failure mode: weak executors *under-call* the advisor (Haiku needed a scripted nudge worth ~7pp pass rate), i.e. escalation blindness is measured, not hypothetical. And even there the advisor must be at least as capable as the executor: the strong model always sits above the loop.

### Decision matrix (task class × model + effort)

| Task class | Model | Effort | Why |
|---|---|---|---|
| Top-level orchestration (the WF2 session) | strongest available (Fable/Opus) | high (default) | decomposition/acceptance errors propagate to every task; cache makes the premium cheap |
| Design & architecture (Step 3) | opus (or inline in a Fable session) | xhigh | highest reasoning density per token in the whole run |
| Adversarial/security review (WF5 verify, Step 11 high-risk) | opus + Codex peer | xhigh | miss cost is asymmetric; cross-model diversity catches what same-model misses |
| Standard code review (Step 11, standard lane) | opus | high | `review: opus` already configured; matches severity-banded confidence design |
| Standard implementation task (riskLevel medium) | opus ceiling, sonnet down-route per task (#132 semantics) | medium–high | per-task ceiling is already the shipped design; keep |
| Mechanical implementation (riskLevel low: boilerplate, renames, test scaffolds) | sonnet | low–medium | bounded blast radius, gates catch failures cheaply; 2.5× cheaper |
| Analysis / codebase inventory / search | sonnet | medium | Explore-class work; volume-heavy, judgment-light |
| Anything | ~~haiku~~ | — | **never** (hard rule, `hooks/model_routing_lib.py` [C]) |

**Effort ladder** (session default `high`; effort settable per subagent, inherited otherwise [C: code.claude.com/docs/en/model-config.md]): `low` = mechanical single-file edits; `medium` = bounded implementation with a written plan; `high` = orchestration, standard review, debugging; `xhigh` = architecture, security, adversarial verification, gnarly root-cause work. `/fast` (Opus 4.8 at $10/$50, ~2.5× faster [C: code.claude.com/docs/en/fast-mode.md]) is an *interactive latency* tool — worth it when a human is waiting, never for headless/batch runs where cost scales and nobody is watching. Enable at session start or not at all (mid-session enable re-caches the whole context at fast rates [C]).

### What `modelRouting` should become

Current: `{review: "opus", analysis: "sonnet", implementation: "opus"}` — already the right *models* [C: matches the matrix]. Proposal: extend each role value to accept `{model, effort}` objects with string shorthand preserved (`"opus"` ≡ `{model: "opus", effort: null}`) — additive, non-breaking [AC11: transparent]. Enforcement moves from prose to **plugin-bundled subagent definitions** (see AC5), where `model:` and effort guidance live in frontmatter instead of 19 scattered SKILL.md dispatch paragraphs.

**Rejected alternative:** cheap-model-on-top with escalation. Loses on error asymmetry, metacognition gating, and expected cost once any rework is priced in; saves ≤15% in the zero-failure best case (math above).

**Metric (AC9):** cost/issue and loop-backs/issue from run-records, compared across routing configs; falsified if a Sonnet-orchestrated 10-issue campaign matches loop-back rate and total cost.

---

## AC2 — /goal integration into workflows

### Confirmed capabilities [C: code.claude.com/docs/en/goal.md]

`/goal` sets a session completion condition; a small evaluator model checks it each turn; the agent cannot stop until it holds; auto-clears when met. Works headless (`claude -p "/goal …"` runs to completion in one invocation). Limits: one goal per session, **4,000-char condition**, optional turn/time bound inside the condition. **Skills cannot invoke /goal** — it is a session command; a skill can only construct the text and hand it to the user (or the driver/Action sets it at session start). This constraint shapes the whole design.

### Proposal: AC-derived goal construction in WF2 (and WF3)

New WF2 **Step 1b (goal guard, optional but default-on when interactive):**

1. Extract acceptance criteria from the validated issue body (WF1-conformant issues have numbered, testable ACs [C: create-issue SKILL quality-bar]).
2. Present the ACs to the user for verify/edit (one confirmation, not a redesign loop).
3. Emit a **compact goal text** — a completion condition, not instructions (the 131–140 campaign proved this pattern [C: handoff]): `Issue #N done: verified ACs met (<compressed list>), PR open with green CI, run-record persisted — or a blocker is posted to the issue via the ERROR protocol.`
4. Instruct the user to run `/goal <text>`. The skill cannot set it; the prose says exactly what to paste.

Design points:
- **Escape clause is part of the condition.** The "or a blocker is posted via the ERROR protocol" disjunct means a legitimately-blocked run satisfies the goal *honestly* by posting the error comment — no lying, no manual `/goal clear` needed in the common failure path. Manual clear remains the fallback for tool outages.
- **Size:** compressed AC list; if ACs exceed the 4,000-char budget, reference the issue: "all numbered ACs of issue #N as written at <timestamp>". The evaluator reads the transcript, which contains the fetched issue body.
- **Interplay with `<termination-rule>` and the completion gate:** the goal is belt-and-suspenders *above* WF2's own gate — it fires only if the orchestrator tries to stop early. It auto-clears when Step 16's deliverables exist in the transcript. No change to the gate itself; no gate is weakened.
- **Headless:** no user to verify ACs → two modes: (a) issues carrying the `wf1-created` label (ACs already user-approved at issue creation) use their ACs verbatim; (b) otherwise skip the goal guard (current behavior) — never auto-approve unverified ACs. The driver or the GitHub Action sets the goal at session start via the prompt (`claude -p "/goal …"` confirmed headless [C]). PR-terminal rule holds: the headless goal text ends at "PR open with green CI", never "merged".
- **WF3:** same pattern with bug semantics: `repro documented, regression test red→green, PR open with green CI`.
- **Multi-issue:** the campaign-level goal wraps the driver (AC3), per-issue goals are not stacked (one goal per session [C]) — the driver session carries the campaign goal; per-issue WF2 quality is guarded by WF2's own gates.

**Rejected alternative:** encoding the full AC text + step instructions in the goal. The goal re-injects on every stop attempt; instructions belong in the skill, the goal is the condition (token burn + drift risk).

**Metric (AC9):** premature-termination incidents per campaign (run-record field: `goal_guard: set|skipped|fired`); target = 0 unfinished-but-stopped runs.

**Headless statement (AC10):** fully specified above; interacts with #48 (STATUS comments give the evaluator legible progress) — extend, don't duplicate.

---

## AC3 — Multi-issue runs in one invocation

### Why today's behavior degrades [C+I]

`/rawgentic:implement-feature Issues 3002-3008` runs issue one through the 16-step machine faithfully, then drifts: the termination rule says stop, the operator prompt says continue, and by issue three the 1,584-line skill text is compaction-eroded context, not live instructions [I — mechanism; the degradation itself is the user's report and matches the termination-rule design [C: WF2 `<termination-rule>`]]. The fix is structural: **fresh WF2 invocation per issue**, which is exactly the shipped design decision in `docs/design/2026-07-04-multi-issue-driver.md` [C] and its build issue **#148** [C].

### Proposal: extend #148 with a dependency DAG (do not duplicate it)

#148 already specifies: pattern doc + committed queue state file (`claude_docs/.driver-state/<campaign>.json`), status machine with `pr_open` (headless-terminal), DEFER taxonomy with branch preservation, rollback anchors, reconciliation table, optional evidence-gated `driver_lib.py` [C: design doc]. This review **endorses that design unchanged** and adds one layer:

**Dependency-ordered execution.** At campaign start:
1. For each queued issue, parse prerequisites from: issue-body markers (`depends on #N`, `blocked by #N`), GitHub task-list references, and native issue relationships (`gh api graphql` timeline/tracked-by where available).
2. Record edges in the queue file: `issues[].depends_on: [numbers]` (additive schema field, `schema_version: 2`).
3. Topo-sort into execution order; **halt at campaign start on cycles** (fail-closed: a cycle is a planning error, not something to guess through). Ties broken by the existing `policy.order` (impact).
4. At each advance, the driver picks the first `queued` issue whose `depends_on` are all `merged` (or `pr_open` when headless — an explicit policy knob `deps_satisfied_by: merged|pr_open`, default `merged`, because depending on unmerged code is how integration surprises happen).
5. DEFER of a dependency parks its dependents with `deferred_reason: cross-issue-dependency` — the loop continues with independent issues, never stalls.

Where it lives: the **driver pattern layer** (per #148's pattern-not-skill decision) plus `depends_on` validation in the optional `driver_lib.py` if/when it ships. **Not inside WF2** — WF2 stays per-issue; the driver owns ordering. A "WF14 driver skill" remains rejected for the reasons the design doc already argued (ceremony, gate-weakening temptation) [C: design doc "Decision (AC2)"].

Campaign-level `/goal` (AC2) + compaction-between-issues (proven in 131–140 [C: handoff "Compact between issues"]) complete the picture.

**Metric (AC9):** issues/campaign completed without operator intervention; ordering violations (dependent built before dependency) = 0; measured via driver state file + run-records.

**Headless (AC10):** inherits #148's `pr_open`-terminal semantics; `deps_satisfied_by: pr_open` lets an overnight campaign proceed past open PRs at the operator's explicit risk.

---

## AC4 — Shrink the workflow skill files

### The problem, quantified [C]

WF2 SKILL.md = 1,584 lines, loaded in full on every invocation. Official guidance: keep SKILL.md focused, push reference material to `references/` loaded on demand [C: code.claude.com/docs/en/skills.md]; the community norm (and skill-creator guidance) is <500 lines. Only 4 reference files exist across the whole plugin [C]. The corpus is 7,751 lines, of which ~3,476 belong to workflows with no recorded usage (see AC12) — deprecation is the single biggest line-count lever.

### Target information architecture (core 4 workflows)

| Layer | Contents | WF2 target |
|---|---|---|
| **SKILL.md (spine)** | role, happy-path, mandatory-steps table, constants, config-loading (shared block), 1–3 lines per step + "read references/X before executing step N" pointers, termination rule | **≤450 lines** (from 1,584) |
| **references/steps.md** | per-step detail: full Step 2 classification, Step 4 gate protocol, Step 8/8a implementation + per-task review detail, Step 11 protocol, Step 13–15 | ~600 lines |
| **references/state-and-resume.md** | state files, loop-back budget mechanics, resumption protocol (already largely delegated to `resume_lib` [C]) | ~150 lines |
| **references/headless.md** | already exists [C]; absorbs the per-step headless annotations' *detail*, leaving one-line markers in the spine | grows ~50 |
| **shared/blocks/** | config-loading (exists), model-routing-resolve, loop-back-budget (WF2/WF3 shared) | +2 blocks |
| **hook CLIs** | already the pattern: `resume_lib`, `plan_lib`, `capabilities_lib` — prose describing *how* they work shrinks to invocation + contract | no new code required |

Net effect: per-invocation context load drops from 1,584 lines to ~450 + on-demand reads of the step file for the current phase (~70% reduction in always-loaded prose). fix-bug: 734 → ~350 via the same split + shared blocks. setup: 643 → ~400 (question flows to references). The lean-rewrite precedent is WF1: 3-judge panel → single quality-bar at ~⅓ the tokens with equal output [C: create-issue SKILL `<why-this-is-lean>`].

**Dead-weight pass:** prose a current-generation model doesn't need spelled out — verbose why-mandatory rationales (keep the table, cut the essays), duplicated examples, restated harness behavior. Applied *after* the structural split so cuts are reviewable.

### Drift-guard survival plan (named tests)

The migration must keep every guard green **by teaching the guards about the corpus, not by weakening them**: introduce a test helper `skill_corpus(skill_name)` that returns SKILL.md ∪ `references/*.md` concatenated, and point the prose-pinning assertions at it. Affected guards [C: grep]: `test_wf2_clarity.py`, `test_skill_helpers.py`, `test_wf2_parallelism.py`, `test_wf2_impact_metrics.py`, `test_trivial_work_check.py`, `tests/hooks/test_headless.py` (annotation *counts* — recount after the move, keep the pin), `test_model_routing_dispatch.py`, `test_shared_block_drift.py` (unchanged — blocks stay), `test_bind_command_expansion_free.py`, `test_interview_skill.py`, `test_adversarial_review_registration.py`, `test_peer_consult_registration.py`. One-time test-infra change, shipped in the same PR as the first split so the suite never goes red.

**Rejected alternative:** rewriting WF2 from scratch lean (like WF1). WF1's rewrite was safe because issue drafting is judgment-light; WF2's gates encode hard-won failure lessons (Step 11 caught 2 Criticals when skipped [C: SKILL mandatory-steps]) — restructure preserves every gate verbatim, a rewrite risks silently dropping one.

**Metric (AC9):** lines + tokens loaded at invocation (measurable: `wc` + tokenizer count of SKILL.md); run-record quality metrics (loop-backs, review findings, CI failures) must not regress across 5 post-restructure runs.

**Compat (AC11):** internal layout only — transparent to configs and consumers; requires plugin cache update (standard); in-flight `.wf2-state/` sessions unaffected (state schema untouched).

---

## AC5 — Modernize with current harness primitives

Verdicts per primitive (capabilities live-confirmed [C: harness audit, doc URLs per row]):

| Primitive | Status [C] | rawgentic today | Verdict |
|---|---|---|---|
| **Plugin-bundled subagent definitions** (frontmatter: `model`, `tools`, `isolation: worktree`, `background`) | GA; plugins can ship agent types | 19 prose dispatch instructions in WF2; modelRouting resolved per-dispatch via CLI | **ADOPT — highest leverage.** Ship `agents/rawgentic-implementer.md` (model from routing, `isolation: worktree`), `agents/rawgentic-reviewer.md` (opus, read-heavy tools). Prose shrinks to "dispatch a rawgentic-implementer"; routing becomes enforceable frontmatter. Directly serves AC1+AC4. |
| **Worktree isolation** (`isolation: worktree`; auto-cleanup; zero token cost) | GA | #85 open (worktree-parallel Step 8); #136 just shipped the availability probe [C: 6b7d009] | **ADOPT** — implementer agent def gets `isolation: worktree`; unblocks parallel independent tasks in Step 8 (extend #85, don't duplicate). |
| **/goal** | GA, headless, 4k-char | not used | **ADOPT** per AC2. |
| **/tasks + TaskCreate** | GA but **session-scoped; no cross-session persistence** | session-notes step markers (durable files) | **KEEP markers.** They are the resumption substrate across sessions/compaction — tasks reset on new session [C: agent-sdk/todo-tracking.md]. Optional: mirror markers into tasks for live UX; never as source of truth. |
| **Workflow tool** | **experimental, disabled by default** (env-var gated) [C] | n/a | **DEFER.** Do not build plugin behavior on an experimental gate. Revisit at GA; candidate first use = Step 11 multi-reviewer fan-out. |
| **/loop** | GA; skills loopable v2.1.196+; jitter up to 30min | Step 13 polls CI via 30s bash loop | **MARGINAL.** Jitter makes /loop wrong for CI watching; `gh run watch` or the existing poll is better. Use /loop only for campaign-level "resume the driver" overnight ticks. |
| **/fast** | Research preview; Opus-only; 2× price | not referenced | **DOCUMENT ONLY** — recommend for interactive sessions in docs; never in headless paths (cost, nobody waiting). |
| **claude-code-action v1.0** | GA; invokes plugin skills via `prompt: /plugin:skill` [C: github-actions.md] | headless mode exists but self-hosted only | **ADOPT as pilot** — AC8(e). |
| **Hooks (23+ events incl. SubagentStart/Stop, TaskCompleted, PostToolBatch)** | GA | SessionStart, PreToolUse (wal-guard), PreCompact | **EXTEND for telemetry**: SubagentStop as a capture point for per-dispatch model/duration into run-records (AC9). Investigate-first (hook payload fields [I]). |
| **Background tasks / Cron** | GA; 7-day expiry, jitter | n/a | No workflow fit today; driver uses fresh invocations, not crons. **PASS.** |
| **Effort parameter** | GA; per-subagent, inherited | not modeled | **ADOPT** in modelRouting extension (AC1) + agent-def frontmatter guidance. |

Hand-rolled machinery that stays hand-rolled, deliberately: session-notes markers (durability), `resume_lib` step detection (tested code beats prose *and* beats harness resume, which doesn't know WF2's semantics), the security scan (no harness equivalent), loop-back budget (`plan_lib` counters — harness has no notion of design loop-backs).

---

## AC6 — Installed plugin/skill usage: keep / replace / remove

| Plugin | Used by [C: grep] | Verdict | Evidence |
|---|---|---|---|
| **reflexion** (`critique`/`reflect`/`memorize`) | WF2 (reflect ×4 sites, critique full-path), WF9/WF10/WF4/setup (critique), WF7 (reflect), WF11 (reflect+memorize ×5) | **REPLACE in the spine; drop the 3-judge panel.** Keep `reflect` as the Step 4 fast-path check *for now*; retire `critique` (3-judge) from WF2 entirely — the lean spine (no panel) shipped 10/10 issues with 0 loop-backs [C: handoff], and the owner's own telemetry found NL self-critique ≈ 0 measured gain (`reference_agentic_critique_economics` [C: owner memory]). High-stakes design scrutiny is WF5's job (cross-model, report-only) — that's the pass that has caught real Criticals [C: WF5 history]. Most other reflexion call sites die with their host workflows (AC12). Longer term: an inline quality-bar checklist (WF1 pattern) replaces `reflect` too, removing the dependency from the core path. |
| **codex** (WF5 engine, WF13, opt-in gates) | adversarial-review, peer-consult, WF2 Step 3/11 opt-ins | **KEEP — load-bearing.** Cross-model diversity is the one thing same-model review can't provide; the diff-stage pass found the foreign-key laundering hole in #133 [C: handoff] and Criticals across the program [C: owner memory]. |
| **superpowers** | brainstorming in create-tests/setup/refactor/optimize-perf; TDD/verification skills overlap WF2 prose | **KEEP + increase leverage.** 3 of 4 brainstorming call sites die with deprecated workflows; setup's stays. AC4's restructure should *delegate* — WF2's TDD prose (RED-GREEN-REFACTOR restated inline) becomes "follow superpowers:test-driven-development" + rawgentic-specific deltas only. |
| **Built-in `/code-review`** (typed findings via ReportFindings) + `/security-review` | never referenced by skills [C] | **ADOPT at Step 11.** Proposal: Step 11 = built-in `/code-review` (same-model, typed, severity-ranked — replaces the hand-rolled multi-agent reviewer roles for simple/standard lanes) **+** WF5 Codex diff pass (cross-model) for high-risk/complex. The severity-banded confidence thresholds port over as finding filters. 3-agent hand-rolled review retained only for `complex_feature` until the A/B metric (below) settles it. **Is reflexion still needed as a reviewer? No — but WF5 is not replaceable by built-ins** (built-ins are same-model; WF5's value is the *different* model). |
| **skill-creator** | eval workspaces exist in-repo [C: create-issue-workspace] | **KEEP** — it's how WF1's lean rewrite was validated; use it again for the WF2 restructure evals. |
| **rawgentic-memorypalace** | session hooks; not workflow-invoked | **KEEP** (session infrastructure, out of workflow scope). `reflexion:memorize` call sites (WF9/WF11/WF4) die with their hosts; WF2 Step 10 already treats memorize as optional-background. |
| **context7 / exa / firecrawl** | research utilities, not workflow deps | **KEEP** (used by this review itself). |
| **caveman / ponytail / sdd / context-engineering-kit(fpf) / drawio / dataviz / generate-image / playwright** | no rawgentic workflow call sites [C] | **KEEP, out of scope** — session-level or other-project tooling; no action. (drawio headless export has a known blank-icon gotcha [C: owner memory] — don't wire it into automated doc generation.) |

**Metric (AC9):** review findings by stage (already in run-records `gates` [C]) split by reviewer kind (built-in vs hand-rolled vs Codex); tokens per review cycle. Decision rule: if built-in `/code-review` + WF5 matches hand-rolled 3-agent finding yield over 10 runs at lower cost, the hand-rolled path is deleted.

---

## AC7 — Not-installed skills/plugins worth adopting

Swept [C]: official plugin dir in `anthropics/claude-code` (13 plugins), `claude-plugins-official` marketplace (568 plugins in marketplace.json), `anthropics/skills` (document/creative only — nothing SDLC-relevant), awesome-claude-code, targeted category searches. (Firecrawl was quota-blocked mid-sweep; WebSearch/WebFetch + firecrawl GitHub-history search used instead.)

### Shortlist (ranked)

| # | Candidate | What / where it slots | Evidence & cost |
|---|---|---|---|
| 1 | **claude-code-action** (github.com/anthropics/claude-code-action) | The headless substrate: `@claude` mentions, scheduled runs, issue triage, PR review — invokes plugin skills directly. Slots under AC8e's pilot + AC3 overnight campaigns | [C] official, v1.0 GA; Anthropic runs `claude-issue-triage.yml` on its own repo; adoption confirmed in coder/coder, nextflow-io. Cost: workflow YAML + API-key secret, no plugin |
| 2 | **ralph-wiggum** (official plugin) | Stop-hook loop: `/ralph-loop "<prompt>" --completion-promise --max-iterations` — a campaign-loop primitive | [C] README fetched. Caveat [C]: completion detection is exact-string match; `--max-iterations` is the real safeguard. See adjudication below — `/goal` beats it for our use |
| 3 | **claude-code-security-review** (github.com/anthropics/claude-code-security-review) | Semantic security review of PR diffs in CI, false-positive filtering. Slots beside Step 11.5 (local scanners stay) as the CI-side lane | [C] 5.5k stars, **261 dependent repos** — strongest real-usage signal found. Cost: workflow YAML + key |
| 4 | **Socket MCP** (github.com/SocketDev/socket-mcp) | Supply-chain scoring for deps; optional PreToolUse hook blocking installs of packages scoring <20 — guardrail for unattended campaigns that install deps | [C] hosted server, zero setup; hook behavior [I] — verify the hook script before trusting it in campaigns |
| 5 | **code-review** (official plugin) | 4–5 parallel reviewer agents with 80%-confidence filtering, designed for the Action's headless lane | [C] merged PR anthropics/claude-code#10227; adoption in agno-agi/agno. Gotcha [C]: lives in the demo marketplace — verify install coordinates via `/plugin` Discover |
| 6 | **ccusage** (github.com/ryoppippi/ccusage) | Token/cost telemetry from local session JSONL — feeds AC9's `usage` fields without API changes | [C] `npx ccusage@latest`, no upload; active June 2026 [I: exact last-commit] |
| 7 | **pr-review-toolkit** (official plugin) | 6 specialist reviewers (silent-failure-hunter, pr-test-analyzer…) — dimension-specific, complements Codex cross-model | [C] README; local/interactive |
| 8 | **hookify** (official plugin) | Generates guardrail hooks from observed conversation patterns — codify "never do X" before unattended runs | [C] description; workflow detail [I] |

**Top-3: claude-code-action, claude-code-security-review, ccusage.** (ralph-wiggum ranks #2 on capability but is superseded for rawgentic by native `/goal` — see rejected table; Socket MCP pulls forward if overnight runs will install dependencies unattended.)

### Rejected [C: sweep report]

feature-dev/commit-commands (duplicate WF2/WF3 + superpowers) · spec-kit toolkits (sdd installed) · bmad-github (BMAD removed 2026-07-03) · memory managers (mempalace covers) · claude-squad/Crystal/gwq (standalone apps, not plugins; superpowers worktree skill covers) · Upkeep (4 stars — maintenance signal too weak) · coderabbit/mergify/jfrog/aikido/dash0/datadog (paid SaaS; overlap Codex/trivy/CI) · GitHub MCP server (`gh` CLI is the established path; tool-count bloat) · unsourced marketplace "CI analyzer" skills (no provenance) · release-automation plugins (nothing credible in plugin form; release-please via Actions remains the way).

---

## AC8 — Technology & architecture adjustments

**(a) Orchestration → Workflow-tool scripts / Agent SDK.** Workflow tool is experimental and disabled by default [C: audit §6] — **defer** (see AC5). Agent SDK: rawgentic's distribution model is "markdown plugin, zero runtime deps" [C: repo]; moving orchestration into SDK code changes the product into an app. **Verdict: no** for the plugin; the SDK path is the right shape only if a hosted "rawgentic service" ever becomes a goal. Benefit small, migration huge, gate risk (re-implementing fail-closed gates in a new runtime) high.

**(b) Session-notes/state-files → harness-native state.** Split verdict: /goal adopted (AC2); /tasks rejected as source of truth (session-scoped [C]); session-notes markers + committed state files stay — they are what survives compaction, `/clear`, and machine moves, and the resumption protocol is built on them [C: resume_lib]. **Verdict: hybrid, mostly keep.**

**(c) Consolidate the 13-workflow surface.** Yes — see AC12. End state: **6 numbered workflows** (WF1, WF2, WF3-as-lane, WF5, WF13-on-probation, + driver pattern) + utilities (setup, switch, new-project, add-exception, interview, sync-security-patterns). WF3 shares WF2's spine today by duplication (734 lines, largely mirrored structure [C: wc + structure]); after AC4 it becomes a **lane over shared references** (bug-entry: repro-first, regression-test-red gate) rather than a sibling copy. Benefit: one spine to maintain; risk: low (WF3's distinct steps are few); cost: medium (the references split must land first).

**(d) Hooks-as-CLI vs hooks-as-MCP-server.** **Keep CLI.** The hook libs are pure functions invoked per-call [C]; an MCP server adds a lifecycle (start, health, version skew with the cache) and schema ceremony for zero capability gain — everything runs local. MCP would pay off only if the hooks needed to serve *other* tools/agents concurrently, which nothing on the roadmap needs. Gate risk of a migration: real (fail-closed CLIs are battle-tested); benefit: none identified. **Verdict: no.**

**(e) GitHub-native automation (claude-code-action).** **Adopt as a bounded pilot.** v1.0 GA invokes plugin skills directly (`prompt: /rawgentic:implement-feature …` with `plugins:` input) [C: audit §13]. Pilot: label-triggered — issue labeled `rawgentic:auto` fires a workflow that runs headless WF2; PR-terminal contract already matches Action semantics (the Action can't merge past branch protection anyway). This is the real answer to overnight autonomy: runner isolation, no SSH surface (headless SSH already hook-blocked [C: WF2 headless block]), CI-native audit trail. Cost: Actions minutes + API tokens; secrets management (API key as repo secret); the headless QUESTION protocol maps to issue comments (#48 extends naturally). Risk to gates: none weakened — the same skills run; `headlessEnabled` must gain a per-trigger allowlist (config addition, additive). Fold #48/#51/#52 into this pilot's milestone (AC10).

---

## AC9 — Measurement plan

Substrate: `docs/measurements/run_records.jsonl` (schema exists: gates, loop_backs, tests, security_scan, outcome, lane [C: jq of 12 records]). Gaps found:

1. **The file is untracked** [C: `git check-ignore` exit 1, `git ls-files` empty] — single-host, unbackupped, invisible to the fleet. Fix: commit it (append-only, per-repo) or aggregate to committed weekly summaries; #115 (fleet aggregation) is the existing issue — extend it.
2. **No token/cost fields.** `work_summary.py` records gates and counts but not spend. Add optional `usage: {input_tokens, output_tokens, cost_estimate, wall_clock_s, model_mix}` — populated best-effort (SubagentStop hook payloads or operator-entered from `/cost`), never blocking Step 16 (#116's canonicalization pass is the natural vehicle).
3. **Per-proposal metrics** (stated in each AC section above; summarized): AC1 cost+loop-backs per issue by routing config · AC2 goal-guard firings / premature stops · AC3 campaign completion rate + ordering violations · AC4 invocation-context tokens + quality non-regression over 5 runs · AC6 findings-yield per reviewer kind per token · AC8e headless pilot: issues shipped/week without operator touch · AC12 maintenance events on deprecated surface (target: 0 because surface is gone).

Rule adopted from the prompt: **no roadmap issue ships without its metric named in the issue body.**

---

## AC10 — Headless / autonomous parity

Per-proposal statements are inline above; the contract layer:

- The **references/headless.md contract** (QUESTION post→label→suspend→exit; ERROR; PR-terminal; no remote ops; wal-guard SSH block) is preserved verbatim by every proposal [C: no proposal touches it].
- AC2 /goal: headless-capable [C]; goal set by driver/Action at session start; escape-clause disjunct keeps blocked runs honest.
- AC3 driver: `pr_open`-terminal headless semantics already designed [C: #148 design]; DAG layer adds `deps_satisfied_by` knob.
- AC4 restructure: headless annotations move to references/headless.md with the *count pins recomputed, not dropped* (`tests/hooks/test_headless.py` [C]).
- AC8e Action pilot: the headless platform itself; #48 (STATUS comments) becomes the Action's progress surface, #51 (large-PR warning) posts as a PR comment, #52 (progress guardrails) = Action timeout + step-boundary STATUS heartbeats. All three fold into milestone M4 rather than standing alone.

---

## AC11 — Back-compat & downstream consumers

### Consumer inventory [C]

| Consumer | Evidence | Sensitivity |
|---|---|---|
| STARS dual-publish mirror | `.github/workflows/mirror-to-stars.yml` in-repo | mirrors on merge; restructure = more files moved, same mechanism — transparent |
| Plugin marketplace validation | rejects version drift [C: owner memory `reference_marketplace_validation`] | every release must bump plugin.json exactly once — unchanged process |
| 29 workspace projects' configs | `.rawgentic_workspace.json` (29 projects, 2 with modelRouting) [C] | schema changes must be additive or migrated by `/rawgentic:setup` |
| Installed plugin caches | stale-cache gotcha [C: owner memory `reference_claude_plugin_marketplace_refresh`]; cache pins old version until remove/install | every behavioral change lands only after cache refresh — document in upgrade guide |
| In-flight `.wf2-state/` + `.rawgentic/review-state/` | session + committed pointers [C: WF2 state-files] | schema untouched by all proposals |
| Headless installs (`RAWGENTIC_HEADLESS`) | all projects currently `headlessEnabled: false` [C] | Action pilot adds config, breaks nothing existing |

### Compat matrix (per proposal)

| Proposal | Class | Migration | Rollback |
|---|---|---|---|
| AC1 modelRouting `{model, effort}` extension | **transparent** (string shorthand preserved) | none; setup offers upgrade | revert config value |
| AC2 goal guard (Step 1b) | **transparent** (optional step; skill prose only) | none | remove step |
| AC3 driver + DAG | **additive** (new state file schema v2; v1 files readable) | driver upgrades v1→v2 on read | state file is per-campaign; finish or discard |
| AC4 SKILL restructure | **transparent to configs; breaking for prose-pinning forks** (none known) | cache refresh; test-helper PR first | git revert; old cache version still runs |
| AC5 bundled agent defs | **additive** | none | delete agents/ |
| AC6 Step 4/11 review change | **behavioral, migratable** — projects with `adversarialReview`/`peerConsult` config keep working; 3-judge path removed | config keys unchanged; upgrade guide notes the new default | version pin to 2.x |
| AC12 deprecations (7 workflows) | **BREAKING** | stub SKILL.md per deprecated workflow for one minor cycle: frontmatter kept (invocable), body = "deprecated → use X" + exit; removal in the next major | reinstall previous version from marketplace history |
| AC8e Action pilot | **additive** (new workflow file + config key) | opt-in per repo | delete workflow file |

### Versioning verdict

Ship AC12 stubs + AC4 restructure + AC6 review change together as **v3.0.0** with `docs/upgrade-3.0.md` (what moved, what's deprecated, cache-refresh steps, config upgrade). Everything additive (AC1/2/3/5/8e) can land as 2.x minors beforehand. Rationale: semver honesty — removing invocable skills and changing default review behavior is major; bundling the majors into one boundary gives consumers a single migration event instead of a drip.

---

## AC12 — Workflow deprecation audit

Evidence lines: **R** = run-records (12/12 = WF2 only [C]) · **N** = session-notes traces [C, bounded] · **D** = design-doc mtime [C] · **S** = structural role.

| Workflow | Lines | Evidence | Verdict |
|---|---|---|---|
| WF1 create-issue | 181 | N: used; lean rewrite proven | **KEEP** |
| WF2 implement-feature | 1,584 | R: 12/12; N: heavy | **KEEP** (restructure per AC4) |
| WF3 fix-bug | 734 | N: used ("WF3 runs", complexity-escalation path) | **KEEP → MERGE onto WF2 spine as bug lane** (AC8c) |
| WF4 refactor | 535 | R: 0; N: 0; D: Mar 6 | **DEPRECATE** → WF2 with a refactor-type issue (Step 2 already classifies; superpowers:brainstorming for approach exploration) |
| WF5 adversarial-review | 243 | N: heavy; real Criticals caught | **KEEP** |
| WF7 update-docs | 488 | R: 0; N: 0; D: Mar 6; known `origin main` hardcode bug left unfixed for lack of use [C: handoff #140 note] | **DEPRECATE** → plain session or WF2 docs-type issue |
| WF8 update-deps | 424 | R: 0; N: 0; D: Mar 6 | **DEPRECATE** → security_scan's osv/trivy already cover the audit half; bumps are WF2 issues; Socket MCP (AC7 #4) adds supply-chain scoring if unattended installs arrive |
| WF9 security-audit | 490 | R: 0; N: 0; D: Mar 6 | **DEPRECATE the workflow, keep the tooling**: `security_scan.py --full` (the actual scanner, shared with Step 11.5 [C: README]) gets a thin utility invocation (`/rawgentic:scan`); STRIDE analysis → built-in `/security-review` + WF5. **No fail-closed gate weakened** — Step 11.5 untouched; WF9 the *skill* is prose around the same hook lib. |
| WF10 optimize-perf | 482 | R: 0; N: 0; D: Mar 6 | **DEPRECATE** → WF2 perf-type issue (profiling checklist worth keeping → references/perf-checklist.md in WF2) |
| WF11 incident | 537 | R: 0; N: 0; D: Mar 8; incidents are rare by nature — absence is weaker evidence here [I] | **MERGE → WF3 hotfix lane** (incident = fix-bug + urgency + comms checklist; the comms/post-mortem checklist survives as references/incident.md) |
| WF12 create-tests | 520 | R: 0; N: 0; D: Mar 7 | **DEPRECATE** → WF2 test-type issue + superpowers TDD skills |
| WF13 peer-consult | 219 | shipped 2026-07-03 [C: v2.46.0]; opt-in, default-off | **KEEP on probation** — too new to judge; re-audit after 10 run-records with `peerConsult` enabled |
| interview | 23 | N: adjacent traces; 23 lines ≈ zero cost | **KEEP** |
| sync-security-patterns | 44 | feeds the security-guard hook; utility | **KEEP** |
| setup / switch / new-project / add-exception | 643/202/198/204 | infrastructure, N: used | **KEEP** (setup shrinks under AC4) |

**Quantified saving [C: wc]:** deprecations + WF11 merge remove **3,476 lines of SKILL prose (44.8% of corpus)**, plus their share of: setup's capability questions, headless annotation pins, shared-block sync targets, docs/ workflow pages, and future drift-guard maintenance. Each deprecated workflow also stops being a place where behavior silently rots (WF7's stale `origin main` bug is the existing proof [C]).

**Graceful path:** one minor cycle (v2.54) ships stubs — frontmatter preserved so invocation still resolves, body redirects and exits; v3.0.0 removes them. README + marketplace description updated in the same PRs (house rule [C: CLAUDE.md pre-PR checklist]).

---

## Rejected-alternatives register (cross-AC)

| Alternative | Rejected because |
|---|---|
| Cheap-model orchestrator topology | AC1: error asymmetry + metacognition gating; ≤15% best-case saving vs unbounded rework downside |
| Goal text = full instructions | AC2: goal is a condition; re-injection burn |
| WF14 "driver skill" | AC3: #148's pattern-not-skill decision stands; ceremony + gate-weakening temptation |
| WF2 ground-up lean rewrite | AC4: gates encode failure lessons; restructure preserves them verbatim |
| /tasks as workflow state | AC5: session-scoped, resets on new session [C] |
| Workflow-tool orchestration now | AC5/AC8a: experimental, env-gated [C] |
| Hooks as MCP server | AC8d: lifecycle cost, zero capability gain |
| Agent-SDK rewrite | AC8a: changes product shape; kills zero-dep distribution |
| Deleting WF9's scanner with the skill | AC12: scanner is `security_scan.py` (shared, fail-closed) — only the prose wrapper is deprecated |
| Silent deletion of deprecated skills | AC12: stub-for-one-minor-cycle policy; marketplace consumers get a migration window |
| ralph-wiggum as the campaign-loop primitive | AC3/AC7: exact-string completion detection vs `/goal`'s model-evaluated condition; native `/goal` is GA, headless, and needs no extra plugin — ralph's `--max-iterations` budget-backstop idea survives as the driver's `budget` DEFER policy |

---

## What this review could not confirm

- **All-time usage** of the deprecated workflows — session notes rotate; verdicts rest on three converging signals, not a complete log. A final call can wait for the stub cycle: a stub that never fires its redirect for a whole minor cycle is definitive.
- **Weak-orchestrator loop-back rate** — no counterfactual run exists; AC1's verdict is evidence-backed but the controlled A/B is future work (the measurement plan makes it cheap).
- **SubagentStop hook payload fields** for token capture (AC9) — needs a spike; marked [I].
- **Live pricing accuracy** — fetched 2026-07-04 by two independent subagents from platform.claude.com; they agree, which is strong but not proof. Prices change (Sonnet 5 intro pricing ends 2026-08-31); re-verify at implementation time.
- **The AC1 cost model's session shape** — 20 turns / 2M cache-reads is an assumption; real WF2 sessions vary, shifting the strong-top premium between the 1.5× (cached) and 2.3× (uncached) bounds. The verdict's direction survives both bounds; the margin doesn't.
- **Socket MCP's install-blocking hook** and the official code-review plugin's exact install coordinates (demo marketplace vs claude-plugins-official — agno's PR #6562 shows people get this wrong) — both flagged [I] in AC7; verify before wiring into anything unattended.
