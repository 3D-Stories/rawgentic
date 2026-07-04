# Workflow Modernization Roadmap

Date: 2026-07-04 · Companion to [findings](2026-07-04-workflow-modernization-review.md) · **Draft issues are NOT filed — owner approval required first** (per the review engagement's terms).

Dedup policy: extends open issues #148 (driver build), #85 (worktree Step 8), #115/#116 (telemetry) rather than duplicating; #48/#51/#52 (headless) fold into M4's pilot; #143 and #122 are untouched by this roadmap and remain standalone.

---

## Milestones at a glance

| Milestone | Theme | Version | Ships when | Success metric |
|---|---|---|---|---|
| **M1** | Instrument + additive wins | v2.54–2.55 | ~1 week of WF2 runs | run-records carry usage + reviewer-kind fields on 3 consecutive runs; goal guard fires 0 false blocks |
| **M2** | Restructure + deprecate | **v3.0.0** | after M1 data | WF2 invocation load ≤450 lines; suite green; corpus −44%; upgrade guide published |
| **M3** | Multi-issue autonomy | v3.1 | after M2 | a 5-issue campaign completes dependency-ordered with 0 ordering violations, 0 operator interventions between issues |
| **M4** | Headless platform | v3.2 | after M3 | 1 issue shipped PR-terminal via label-triggered Action with STATUS comments, no laptop attached |

Sequencing rationale: **measure before switching** (M1 instruments reviewer yield before M2 changes review defaults); **test-infra before restructure** (the corpus helper lands before any prose moves, so the suite never goes red); **restructure before driver** (M3's driver pattern references the v3 spine); **platform last** (the Action pilot rides on goal guard + driver + a stable v3 surface). Each milestone is independently shippable and independently measurable.

---

## M1 — Instrument + additive wins (v2.54–2.55)

### Epic E1: Routing + review telemetry

**Draft issue A — `feat(config,routing): modelRouting accepts {model, effort} objects`**
- ACs: (1) `modelRouting.<role>` accepts either the current string or `{model, effort}`; string ≡ `{model, effort: null}` — all 29 existing configs parse unchanged. (2) `model_routing_lib.resolve` returns the pair; never-Haiku bump applies to the model member exactly as today (haiku→sonnet, warned). (3) WF2/WF3 dispatch prose carries resolved effort onto Agent calls when non-null. (4) `/rawgentic:setup` offers the upgrade, never rewrites silently. (5) Drift-guard `test_model_routing_dispatch` extended, green.
- Scope in: schema, resolver, dispatch sites, setup question. Out: any change to *which* models are routed; agent-definition files (M3).
- Components: `hooks/model_routing_lib.py`, WF2/WF3 SKILL dispatch blocks, setup SKILL, tests. Risk: low (additive; fail-open resolver unchanged). Size: **S**.
- Metric: resolver round-trips all 29 configs; 0 routing regressions in next 3 run-records.

**Draft issue B — `feat(work_summary,telemetry): usage + reviewer-kind fields in run-records; commit the store`** *(extends #115 + #116 — file as one issue cross-referencing both, or fold into them)*
- ACs: (1) run-record schema gains optional `usage: {input_tokens, output_tokens, cost_estimate_usd, wall_clock_s, model_mix}` — best-effort, never blocks Step 16. (2) Each `gates[]` review entry gains `reviewer_kind: inline|reflexion|builtin_code_review|codex|hand_rolled_multi` (canonicalization per #116). (3) `docs/measurements/run_records.jsonl` is committed (append-only) — the current file is untracked [confirmed in review]; decide committed-raw vs committed-weekly-aggregate with #115's fleet design. (4) `ccusage` documented as the local backfill path for token numbers.
- Scope in: writer, schema doc `docs/run-records.md`, committing the store. Out: fleet aggregation implementation (stays #115), dashboards.
- Components: `hooks/work_summary.py`, `references/run-record.md`, tests. Risk: low. Size: **S–M**.
- Metric: 3 consecutive run-records carry both new field groups.

### Epic E2: Goal guard

**Draft issue C — `feat(wf2,wf3): AC-derived /goal guard (Step 1b)`**
- ACs: (1) After issue validation, WF2 extracts numbered ACs and presents a compact goal text for the user to verify then run via `/goal` (skill cannot set it — [confirmed: code.claude.com/docs/en/goal.md]). (2) Goal text template includes the escape disjunct: "…or a blocker is posted to the issue via the ERROR protocol" so blocked runs clear honestly. (3) ≤4,000 chars guaranteed: overflow falls back to "all numbered ACs of issue #N as written". (4) Headless: `wf1-created`-labeled issues use their ACs verbatim (pre-approved at creation); unlabeled issues skip the guard; PR-terminal wording ("PR open with green CI", never "merged"). (5) WF3 variant: repro documented + regression test red→green + PR green. (6) run-record gains `goal_guard: set|skipped|fired`. (7) Headless annotation count pins recomputed (`tests/hooks/test_headless.py`).
- Scope in: Step 1b prose in both skills, goal-text templates, run-record field. Out: driver/campaign-level goals (M3), any change to the completion gate or `<termination-rule>`.
- Components: WF2 + WF3 SKILL.md, `references/headless.md`, work_summary, tests. Risk: low (optional step, no gate touched). Size: **S**.
- Metric: premature-termination incidents = 0 across next campaign; 0 false-block incidents (goal firing when work was legitimately done).

---

## M2 — Restructure + deprecate (v3.0.0)

### Epic E3: Progressive disclosure

**Draft issue D — `test(infra): skill_corpus() helper — drift guards assert over SKILL.md ∪ references/`** *(precursor; must merge before any prose moves)*
- ACs: (1) `tests/` helper returns concatenated SKILL.md + `references/*.md` per skill. (2) The prose-pinning guards enumerated in the findings doc (§AC4) are ported to it. (3) Suite green with zero prose moved (pure refactor of tests). (4) Headless-annotation count test parameterized by corpus, pins kept.
- Risk: low. Size: **S**. Metric: suite green pre/post with identical pass count.

**Draft issue E — `refactor(wf2): split SKILL.md to ≤450-line spine + references/`**
- ACs: (1) SKILL.md ≤450 lines: role, happy-path, mandatory-steps table, constants, shared blocks, 1–3 lines per step + explicit "Read references/steps.md §N before executing Step N" pointers. (2) `references/steps.md` (per-step detail), `references/state-and-resume.md`; headless per-step detail into existing `references/headless.md`. (3) **Every gate, constant, and mandatory-step semantic preserved verbatim** — restructure, not rewrite; diff reviewed on that criterion. (4) Full suite green (via issue D's corpus helper). (5) Dead-weight pass as a separate commit (reviewable cuts only). (6) 2 shared blocks extracted: model-routing-resolve, loop-back-budget.
- Scope out: WF3 (next issue), any behavioral change to steps.
- Risk: **medium** — mitigations: corpus-helper first, verbatim-gate diff review, WF5 adversarial pass on the split plan, skill-creator eval run (WF1-rewrite precedent). Size: **L**.
- Metric: invocation-loaded lines 1,584→≤450; loop-backs/review-findings/CI failures non-regressed over next 5 run-records.

**Draft issue F — `refactor(wf3): fix-bug as a lane over the shared WF2 spine references`**
- ACs: (1) WF3 SKILL ≤350 lines; bug-specific steps (repro-first, regression-test-red gate, complexity escalation) stay first-class; shared step detail read from WF2's references. (2) Suite green. (3) WF11's comms/post-mortem checklist lands as `references/incident.md` + a WF3 hotfix-lane pointer (the WF11 merge).
- Risk: medium. Size: **M**. Metric: same non-regression window as issue E.

### Epic E4: Deprecation + release

**Draft issue G — `feat(skills): deprecation stubs for WF4/WF7/WF8/WF9/WF10/WF12 (+WF11 merge)`**
- ACs: (1) Each deprecated skill's SKILL.md becomes a stub: frontmatter preserved (still invocable), body = one-paragraph redirect to the replacement (WF2 issue-type / built-in `/security-review` / superpowers TDD / WF3 hotfix lane) and exits. (2) `security_scan.py --full` gets a thin `/rawgentic:scan` utility skill so WF9's *tooling* survives its skill — **no fail-closed gate weakened; Step 11.5 untouched** (drift-guard asserts scan invocation unchanged). (3) README + docs pages updated; setup no longer asks capability questions that only served deprecated workflows. (4) Stubs live for exactly one minor cycle; removal is issue H.
- Risk: low-medium (consumer-visible). Size: **M**.
- Metric: 0 stub-redirect firings over the stub cycle confirms the usage evidence; any firing = data for a keep re-verdict.

**Draft issue H — `release: v3.0.0 — remove stubs, publish upgrade guide`**
- ACs: (1) `docs/upgrade-3.0.md`: what moved, what's gone, replacement table, cache-refresh steps (`claude plugin remove/install`), config-upgrade notes. (2) Stub removal. (3) Marketplace + STARS mirror verified post-publish (version-drift validation green). (4) CHANGELOG.
- Risk: low (mechanical, gated on G's cycle). Size: **S**.

### Epic E5: Review-gate switch *(gated on M1's reviewer-kind data)*

**Draft issue I — `feat(wf2): Step 4 reflect-only default + Step 11 built-in /code-review + WF5 diff pass for high-risk`**
- ACs: (1) Step 4: `/reflexion:critique` 3-judge panel removed; reflect-only (or inline quality-bar) for all lanes; WF5 remains the high-stakes design gate (opt-in per config, unchanged). (2) Step 11 simple/standard lanes: built-in `/code-review` typed findings, filtered through the existing severity-banded confidence table; complex lane keeps multi-agent review until the A/B metric settles. (3) Cross-model WF5 diff pass triggering unchanged. (4) Decision recorded with M1 data attached: if built-in+Codex matched hand-rolled yield over ≥10 runs at lower cost, the hand-rolled path is deleted; otherwise this issue is **abandoned with the data cited**.
- Risk: medium (review quality is the product's core promise) — mitigated by data-gate + WF5 unchanged. Size: **M**.
- Metric: findings-yield per token by reviewer_kind, before vs after; Critical-miss rate must stay 0.

---

## M3 — Multi-issue autonomy (v3.1)

### Epic E6: Driver + agents

**Draft issue J — `feat(driver): dependency DAG + epic anchor on the #148 queue (depends on #148)`** *(comment/extend #148 first; file only what #148's build doesn't absorb)*
- ACs: (1) Queue schema v2: `issues[].depends_on[]` parsed from issue bodies (`depends on #N`, `blocked by #N`, task-list refs) + `gh api` issue relationships. (2) Topo-sort at campaign start; **cycles halt fail-closed with the cycle printed**. (3) Advance rule: first queued issue whose deps are `merged` (policy knob `deps_satisfied_by: merged|pr_open`, default merged). (4) Deferred dependency parks dependents (`cross-issue-dependency` reason); loop continues with independent issues. (5) **Epic anchor:** driver accepts an epic issue number (queue derived from its task list) or an inline list (offers to create the epic, one `gh` call); epic checkboxes mirrored one-way *from* the state file — the state file remains the sole machine source of truth; **headless runs refuse to start without an epic** (it is the STATUS/QUESTION channel). (6) On subscription auth, a rate-limit lockout maps to a `budget` DEFER with resume-after-window-reset noted in the ledger. (7) v1 state files still readable.
- Risk: medium. Size: **M**. Metric: 5-issue dependency-ordered campaign, 0 ordering violations; epic checkboxes match state file at campaign end.

**Draft issue K — `feat(agents): bundled subagent definitions with routed model+effort and worktree isolation`** *(extends #85)*
- ACs: (1) Plugin ships `agents/rawgentic-implementer.md` (model per routing, `isolation: worktree`) and `agents/rawgentic-reviewer.md` (review model, read-heavy tools). (2) WF2 dispatch prose says "dispatch a rawgentic-implementer" — the 19 scattered model-dispatch paragraphs collapse. (3) Never-Haiku enforced in the definitions and asserted by a drift guard. (4) Worktree availability probe (#136, shipped) consulted; graceful fallback to non-isolated dispatch. (5) #85's parallel Step 8 becomes a config-gated option once isolation is default.
- Risk: medium. Size: **M**. Metric: parallel-task run completes with 0 cross-task file conflicts; dispatch prose line count in WF2 drops.

---

## M4 — Headless platform (v3.2)

### Epic E7: GitHub-native autonomy

**Draft issue L — `feat(headless): claude-code-action pilot — label-triggered WF2 (folds #48, #51, #52)`**
- ACs: (1) Repo workflow: issue labeled `rawgentic:auto` → Action runs headless WF2 via `prompt: /rawgentic:implement-feature <n>` with plugin inputs; PR-terminal. (2) STATUS comments at step boundaries (#48) are the Action's progress surface. (3) Large-PR warning (#51) posts as a PR comment. (4) Progress guardrails (#52) = Action timeout + heartbeat STATUS. (5) `headlessEnabled` gains a per-trigger allowlist config (additive). (6) Goal set at session start from pre-approved ACs (M1 issue C's headless mode). (7) **Auth-mode decision documented per repo**: subscription OAuth via `claude setup-token` (`CLAUDE_CODE_OAUTH_TOKEN` — officially supported for Actions; campaign shares the owner's plan bucket, so schedule off-hours and handle lockout as DEFER) vs API key (isolated dollar budget); default = subscription OAuth, the majority-user case. (8) Secrets as repo secrets; no new egress paths; wal-guard SSH block inherently satisfied (runner-local).
- Risk: medium-high (autonomous writes on a real repo) — bounded: PR-terminal, branch protection, one label-gated repo first. Size: **L**.
- Metric: 1 issue shipped end-to-end (label → PR with green CI) with zero operator touches; QUESTION protocol exercised once deliberately.

**Draft issue M — `ci(security): claude-code-security-review lane on PRs`**
- ACs: (1) Action reviews PR diffs semantically; findings as PR comments; non-blocking initially, promotion to required after 10 clean-signal PRs. (2) Local Step 11.5 scanners unchanged (complement, not replacement).
- Risk: low. Size: **S**. Metric: true/false-positive tally over first 10 PRs.

---

## Epic → AC traceability

| Epic | Serves ACs |
|---|---|
| E1 routing+telemetry | AC1, AC9 |
| E2 goal guard | AC2, AC10 |
| E3 progressive disclosure | AC4, AC5, AC8c |
| E4 deprecation+release | AC12, AC11 |
| E5 review-gate switch | AC6, AC9 |
| E6 driver+agents | AC3, AC5, AC1 |
| E7 headless platform | AC8e, AC10, AC7 |

Constraints honored throughout: never-Haiku (asserted in A, K), PRs-only (every issue ships via PR), no fail-closed gate weakened (explicit AC in G; E5 is data-gated and leaves WF5 untouched).
