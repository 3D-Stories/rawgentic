# Per-phase model routing for WF2/WF3 — from bench #14 (two-judge, 6 models × 6 phases)

**Date:** 2026-07-16 · **Status:** awaiting owner approval · **Source data:** bench #14
GLM-5.2 rejudge (`rawgentic-next` `docs/measurements/model-bench/2026-07-14-glm-rejudge/`
— report md/html + `scores.json` + `tradeoff.json`, 216 frozen cells)
· **Peers consulted:** gpt-5.6-sol (Codex) + glm-5.2 (Zhipu), independent proposals in
`docs/reviews/peer-rawgentic-routing-problem-2026-07-16{,-glm}.md`

Every load-bearing claim below is **[C]onfirmed** (with its evidence) or **[I]nferred**
(with what would confirm it). All numbers were recomputed from `tradeoff.json` /
`scores.json` in this run — never taken from report prose.

---

## 1. The verdict up front

| Seat | Model | Why (one line) |
|---|---|---|
| **Interactive driver** | **claude-opus-4-8** | Tightest reliability floor on the phases the driver does inline (design/plan worst-cells 81/82 vs sonnet's 46/28; 6/6 gates vs 5/6) [C: scores.json perCell] |
| **Review subagents** (WF2 Steps 4/8a/11, WF3 review) | **claude-fable-5**, fallback opus-4-8 | The ONLY statistically real Claude quality edge in the bench: review +4 vs opus, +3 vs sonnet at pooled sd ~2 [C: recomputed] — and review gates everything downstream |
| **Analysis subagents** | **claude-sonnet-5** (unchanged) | No real Claude gap on analysis-adjacent phases; cheapest; intro pricing ~⅓ off through 2026-08-31 [C: live pricing fetch] |
| **Implementation subagents** | ceiling **claude-opus-4-8** (unchanged); `select_impl_model` down-routes standard tasks to sonnet | Build gap opus−sonnet = +1 at pooled sd 7.6 — inside noise — at 1.74× the cost. The complexity fork the owner asked for **already exists in code** [C: model_routing_lib.py:121-152] |
| **Per-phase config schema** | **Deferred** | Data supports 3 real distinctions, not 6; both peers independently converged on deferral [C: both peer reports] |

Net config change: **one value** — `modelRouting.review: opus → fable` — plus a fallback
chain, a provenance stamp, and skill-prose wiring. Everything else is confirmation that
existing machinery already implements the desired behavior.

---

## 2. What the bench actually says (recomputed)

### 2.1 Quality (GLM-5.2 judge, median-of-medians 0–100) × cost (USD per phase, 6 cells)

| Phase | sonnet-5 | opus-4-8 | fable-5 | best gpt-5.6 |
|---|---|---|---|---|
| intake | 85 / $2.52 | **88.5** / $3.95 | 88 / $7.06 | terra 85 / $0.99 |
| design | 84 / $4.28 | **87** / $5.46 | 86 / $9.46 | sol 87.5 / $1.92 |
| plan | **85** / $6.69 | 83 / $7.71 | 84 / $13.47 | terra 85 / $2.67 |
| build | 76 / $6.85 | **77** / $11.92 | 77 / $16.62 | sol 62 / $7.12 |
| review | 85 / $5.71 | 84 / $7.19 | **88** / $11.63 | sol 85.5 / $5.93 |
| ship | **88** / $2.83 | 85 / $4.41 | 84 / $6.14 | sol 82 / $2.59 |

[C: recomputed from `tradeoff.json` this run.] Claude costs are billed-token figures at
$3/$15 sonnet, $5/$25 opus, $10/$50 fable per M; gpt rows are `est@12/M` **estimates** —
cross-engine cost comparison is soft and is NOT used to drive any routing choice here.

### 2.2 Which gaps are real (gap vs pooled per-cell sd, n=6 cells/model/phase)

Only three Claude-family gaps clear the noise floor [C: recomputed from `scores.json`
perCell; n=6 cells per model×phase except fable plan n=5 (one null cell, §2.3)]:

| Gap | Size | Pooled sd | Verdict |
|---|---|---|---|
| fable − opus, review | +4.0 | 2.0 | **real** |
| fable − sonnet, review | +3.0 | 1.9 | **real** |
| sonnet − opus, ship | +3.0 | 2.9 | **real (borderline)** |
| fable − sonnet, intake | +3.0 | 2.6 | real (opus ≈ fable there, +0.5 at sd 2.7 — parity) |
| opus − sonnet, build | +1.0 | 7.6 | noise |
| opus − sonnet, intake | +3.5 | 3.8 | noise |
| opus − sonnet, design | +3.0 | 8.7 | noise |
| sonnet − opus, plan | +2.0 | 11.7 | noise |

**Where quality is inside noise, cost decides** — the owner's stated rule. That rule,
applied honestly, sends far more work to sonnet than the raw medians suggest.

### 2.3 Reliability floors — the fact that picks the driver

Worst cell of 6 (harder brief a) [C: scores.json perCell]:

| | design floor | plan floor | build floor | design gates | plan gates |
|---|---|---|---|---|---|
| opus-4-8 | **81** | **82** | 62 | **6/6 all five** | **6/6 all five** |
| sonnet-5 | 46 | 28 | 62 | 5/6 each | 5/6 each |
| fable-5 | 84 | 80 (n=5¹) | 38 | 6/6 all five | 4/5 on P-G1, 5/5 on P-G2..G5 |

¹ fable plan has one null cell (brief a rep 2) — median over 5. Named, not hidden.

Brief a is simply the harder brief for every model (mean slot deviation −1.0..−2.3 vs
+0.4 for brief b) [C: recomputed] — the collapses are genuine model failures under
difficulty, not a fixture artifact.

### 2.4 Pricing (verified live this run)

[C: fetched `platform.claude.com/docs/en/about-claude/pricing` 2026-07-16]
opus-4-8 $5/$25 · sonnet-5 **intro $2/$10 through 2026-08-31**, then $3/$15 · fable-5
$10/$50 (per M in/out; cache read 0.1×). The bench's sonnet costs are anchored at
post-intro $3/$15, so sonnet's *current* API-equivalent is ~⅓ lower than table values
through August. Fast-mode opus-4-8 exists at $10/$50 — not used in the bench, not part
of this plan.

### 2.5 Known limitations (stated, not buried)

- Single benchmark; 2 briefs × 3 reps; n=6 cells per model×phase. Medians are
  directional; floors are safety evidence. [C: fixture design]
- GLM-5.2 judges stricter than Gemini (every per-model mean Δ negative, widest on
  build); cross-JUDGE absolutes aren't comparable — all comparisons here are within the
  judge-uniform GLM matrix. [C: report correlation section, ρ=0.621]
- build B-G2/B-G3 gates: 0/6 for opus AND fable, 2/6 sonnet — those two gates fail
  nearly universally and say more about the gate than the models. [C: gateFrac]
- Driver economics are subscription usage-window burn, not API dollars; API-equivalent
  figures are the proxy for burn rate. [I: no per-window burn telemetry exists — the
  run-record `usage` capture would confirm; see child issue 6]

---

## 3. Driver recommendation: opus-4-8 (not sonnet-5)

**Recommendation: drive interactive and unattended orchestration sessions on
claude-opus-4-8.** Both independent peers reached the same conclusion unprompted.

The driver personally executes what the bench calls intake, design, plan and the
orchestration glue — the phases where a bad output silently corrupts everything
downstream. The decisive facts:

1. **Tail risk, not medians.** Sonnet's design/plan medians are competitive (84/85),
   but one cell in six collapsed to 46 (design) and 28 (plan) on the harder brief, and
   its gate pass-rate there is 5/6. Opus's worst cells are 81/82 with 6/6 gates. A
   driver that writes one catastrophic design in six hard tasks is expensive in exactly
   the way that doesn't show up in a median. [C: §2.3]
2. **Sonnet's real edges are dispatchable.** Its ship edge (+3, real) and cheapness are
   captured by routing ship-shaped *tasks* to sonnet subagents (§5) — the driver seat
   doesn't need to be sonnet to harvest them.
3. **Cost containment comes from farming, not from a cheaper driver.** With analysis,
   standard implementation, and ship tasks on sonnet and review on fable, the opus
   driver's own token share shrinks; that is where the burn goes down without giving up
   the floor. [I: exact driver-share depends on issue mix — run-record usage telemetry
   (child 6) would quantify it]

**The alternative and why it loses:** sonnet-5 as driver saves roughly ⅓–½ of driver
burn [C: pricing ratio] and matches opus on medians everywhere but intake/design
[C: §2.1, gaps inside noise]. It loses on the one thing a driver can't delegate:
the reliability floor on inline design/plan work. If a future bench shows sonnet's
collapse cells were brief-specific noise, revisit (§6 refresh rule).

---

## 4. Per-phase routing table (the plan)

"Seat" = who actually executes that phase's work in WF2/WF3 today
[C: WF2 SKILL.md steps; dispatch points at SKILL.md:145-156, steps.md:961-965].

| Phase | Seat today | Routed model | Quality/cost basis | Fork |
|---|---|---|---|---|
| intake (WF1/Step 1-2) | driver inline | **opus-4-8** (driver) | opus 88.5 tops; ≈fable at half fable's cost; real edge over sonnet | none — driver covers it |
| design (Step 3-4) | driver inline + review judge | **opus-4-8** (driver); critique judge → **fable-5** | driver floor argument (§3); judge is a review-role dispatch | none |
| plan (Step 5) | driver inline | **opus-4-8** (driver) | plan gaps all inside noise; opus floor 82 vs sonnet 28 | none |
| build (Step 7-8) | implementation subagents | ceiling **opus-4-8**; standard/trivial tasks **down-route to sonnet-5** | opus−sonnet +1 @ sd 7.6 = noise; sonnet 57% of opus cost — the owner's 88-vs-87 example, verbatim | **existing** `select_impl_model(ceiling, riskLevel, complexity)`: high-risk or complex → opus; else sonnet [C: model_routing_lib.py:121-152] |
| review (Steps 4/8a/11; WF3 review) | review subagents | **fable-5**, fallback **opus-4-8** on quota exhaustion | the one real quality edge (+4/+3); review volume is bounded: 1 + high-risk-count + 1 per run [C: plan_lib.py estimate_agents] | quota circuit-breaker, run-scoped (§5.2) |
| ship (Steps 12-16) | driver inline today | ship-shaped tasks → **sonnet-5** via standard-task down-route; final merge/verify stays driver | sonnet 88 real edge AND cheapest — double win | reuse implementation fork; no new machinery |

gpt-5.6 is deliberately **not** routed into Claude seats: its costs are estimates
(`est@12/M`), its build quality craters (47–62), and gpt/glm already serve the
orthogonal cross-model gates (WF5 adversarial review, WF13 peer consult). [C: §2.1;
both peers flagged the same]

---

## 5. What actually changes in WF2/WF3

The audit finding of this run: **most of the desired behavior already exists.** The
changes are one config value, one fallback mechanism, prose wiring, and provenance.

### 5.1 Config (workspace `.rawgentic_workspace.json`, rawgentic entry)

```json
"modelRouting": {
  "review": "fable",
  "analysis": "sonnet",
  "implementation": "opus",
  "provenance": { "bench": "#14 glm-rejudge 2026-07-14", "decided": "2026-07-16" }
}
```

- `review: opus → fable` is the only value change. `resolve()` already accepts it;
  fable is not in `_BELOW_OPUS`, so no soft-floor warning fires
  [C: model_routing_lib.py:24-26,113-116]. Local resolver acceptance is NOT proof a
  real fable dispatch works under this workspace's account/entitlements — child 2's
  AC requires one real dispatch before the flip merges (adversarial-review fix).
- **Sequencing (must-fix from both reviewers):** this flip merges only AFTER the
  fallback chain (child 1) is deployed — see §7 dependency order.
- `provenance` is a new, ignored-by-resolve() annotation field — needs a
  schema-tolerance check only. [I: verify resolve() ignores unknown keys — child 1
  includes the test]

### 5.2 Review fallback chain (the one real mechanism to build)

When fable quota is exhausted mid-run (historically real), review dispatch falls back
to opus — never lower (review soft floor stays). Both peers specified the same shape:

Transition contract (adversarial-review fix — the trigger must be enumerable, not vibes):

- **Breaker triggers** — a fable dispatch result counts as quota/capacity exhaustion
  ONLY when it matches an enumerated signal class: (a) an explicit rate/usage-limit
  error from the harness/provider (HTTP 429 or an error string matching the harness's
  usage-limit / quota-exhausted family), or (b) the known vacuous-death signature of a
  limit-killed subagent (`confirmedCount: 0` + empty body — established in the repo
  manual §4.9). Child 2 owns the exact classifier list, seeded from real captured
  errors (see child 2 AC).
- **Everything else is NOT a trigger**: timeout, 5xx, parse failure, or an unfavorable
  review result goes down the existing retry/error path on fable — model substitution
  is only for exhaustion. [C: gpt peer, decision 4]
- **Trip semantics**: the first triggering dispatch atomically sets run-scoped
  `fable_exhausted`, then that same review is **replayed once on opus immediately**;
  all subsequent review dispatches in the run go straight to opus. Concurrent in-flight
  fable dispatches at trip time are left to finish; each one that returns a trigger is
  replayed once on opus (no storms — one replay per review, ever).
- **Concurrency clamp (honest status)**: the ≤3 concurrent Claude subagent ceiling is
  today a workspace-manual rule enforced by dispatch discipline only — **no
  programmatic clamp exists** [C: grep of hooks/ this run]. Step 8a fan-out must
  respect it in prose now; child 2's AC adds at minimum a logged concurrent-dispatch
  count so violations are visible, with a code-level clamp as a stretch goal.

### 5.3 Skill prose (WF2 `implement-feature`, WF3 `fix-bug`)

- `<model-routing-resolve>` block: document the fallback chain + circuit breaker for
  the review role (mechanism in 5.2). [C: block exists at SKILL.md:145-156]
- Step 12-16 (ship): the down-route only fires for **delegated** tasks
  [C: steps.md:961 — select_impl_model runs at the implementation-delegation boundary],
  and ship work is driver-inline today, so classification alone routes nothing
  (adversarial-review catch). The change is therefore a **delegation boundary**: WF2
  ship steps that are self-contained artifact edits — README/changelog entry, version
  bump ×3, docs updates — become delegable tasks (riskLevel standard) dispatched
  through the existing implementation path, landing on sonnet via the down-route.
  **Driver-only, never delegated:** merge decision/execution, CI-lane triage, deploy
  and its verification, run-record telemetry (Step 16). Child 4 tests BOTH paths by
  asserting the actual executing model (delegation on → sonnet; delegation off →
  driver inline, unchanged).
- WF3: same review fallback; diagnosis stays analysis=sonnet; fix tasks already flow
  through the implementation fork. [C: fix-bug SKILL.md:106-109 resolves review role]
- Driver guidance: a short "driver seat" note in both skills naming opus-4-8 as the
  recommended session model with the §3 rationale — guidance, not enforcement (the
  harness owns session model). 

### 5.4 What is explicitly NOT changing

- `select_impl_model` logic — already correct; the bench validates it. 
- The 3-role schema — no per-phase keys (deferred, §6).
- analysis role value — stays sonnet.
- WF5/WF13 backends (gpt/glm) — orthogonal cross-model gates, untouched.
- No auto-tuner. No Haiku anywhere (enforced in code) [C: model_routing_lib.py:88-91].

---

## 6. How bench data drives routing going forward (provenance + refresh)

The mechanism both peers converged on — human-in-the-loop, no auto-tuner:

1. **Provenance stamp** in config (§5.1) ties current values to bench #14.
2. **Refresh rule** (documented in `docs/model-routing.md`, new — child 5 carries the
   full executable decision table; the contract, made precise per adversarial review):
   - **Role→phase map:** review → review phase; analysis → intake+design (mean of the
     two gaps); implementation ceiling → build; driver seat → design+plan (each must
     independently pass).
   - **Gap test:** candidate's median − incumbent's median > pooled sd, where pooled
     sd = mean of the two models' population sd over their VALID cells (null cells
     dropped and named; minimum n=5 valid cells per side or the comparison is void).
     This is an **effect-size heuristic, not a significance test** — stated as such.
   - **Floor test:** candidate's worst valid cell ≥ 70 for subagent seats, ≥ 80 for
     the driver seat; driver seat additionally requires every design+plan gate ≥ 5/6.
     (Thresholds chosen from bench #14's spread: opus floors 81/82, the collapse cells
     46/28/38 are what the rule must exclude. Owner may retune in child 5.)
   - A value moves only when BOTH tests pass; ties/void comparisons hold the incumbent.
     Re-stamp provenance on every decision, including "no change".
3. **Per-phase schema gate:** open a per-phase design spike only when a future bench
   shows ≥2 real distinctions the 3-role model cannot express. [C: both peers,
   independently identical rule]
4. Run-record telemetry (`usage` capture, already in WF2 Step 16) provides the
   production-side signal — retry rates, gate failures, per-role burn — to sanity-check
   bench conclusions between reports. [C: hooks/usage_capture.py exists; extension in
   child 6]

---

## 7. Epic + child issues (ready to file on approval)

**Epic: "Route WF2/WF3 model seats from bench #14 evidence (driver=opus, review=fable, ship→sonnet)"**
Body carries the §1 verdict table + link to this doc. Children, in **dependency
order** (adversarial-review fix: the fallback mechanism MUST exist before the config
flip goes live — both reviewers independently flagged the original ordering as a
production window with fable live and no fallback):

0. **Prerequisite (owner-gated): repair `~/.codex/config.toml` CCR hijack + WF5/WF13
   smoke test** — remove the two CCR-managed blocks (owner approval required; global
   config), then one live WF5 review + one WF13 consult on the gpt backend as the
   epic's acceptance signal that the cross-model gates this plan leans on actually
   run. If the owner declines, the epic proceeds but the plan's WF5/WF13 references
   are re-stamped "glm-backend only". (chore, S, blocks nothing else structurally)
1. **Review fallback chain: run-scoped fable→opus circuit breaker** — the §5.2
   transition contract: enumerated trigger classifier (seeded from real captured
   errors), atomic trip + single immediate opus replay, in-flight handling, logged
   concurrent-dispatch count (clamp visibility), tests for trip/no-trip/replay-once/
   never-below-opus. (feat, M — **blocks child 2**)
2. **Retune rawgentic `modelRouting` (review→fable) + provenance field** — the config
   flip, `resolve()` unknown-key tolerance test, never-Haiku regression test. AC
   (adversarial-review fix): one REAL WF2/WF3 review dispatch under the exact
   workspace config recording the resolved model and a successful fable response, plus
   one real captured quota/capacity error validated against child 1's classifier.
   **Depends on child 1 — do not merge review=fable before the circuit breaker is
   deployed.** (feat, S)
3. **WF2/WF3 skill prose: fallback wiring + Step 8a concurrency note + driver-seat
   guidance** — `<model-routing-resolve>` block edit (shared-block sync), drift-guard
   anchor. (docs/feat, S)
4. **Ship-task delegation boundary** — make self-contained ship artifacts
   (README/changelog, version bump, docs) delegable standard-risk tasks through the
   existing implementation path; driver-only list (merge, CI triage, deploy+verify,
   Step 16) stays inline. Tests assert the actual executing model on BOTH paths
   (delegation on → sonnet; off → driver unchanged). (feat, M)
5. **`docs/model-routing.md`: provenance + refresh-rule doc** — the §6 executable
   decision table (role→phase map, pooled-sd formula, min-n, null handling, floor and
   gate thresholds, tie behavior, effect-size-not-significance statement) with the
   gap>sd AND floor rule as the canonical drift-guardable sentence. (docs, S)
6. **Routing telemetry in run records** — per-dispatch: role, preferred/actual model,
   selector inputs, fallback reason, queue/concurrency count; extends the run-record
   schema (append-only store rules apply). (feat, M)
7. **(Deferred — record only)** per-phase schema spike, gated on the §6.3 evidence rule.
   Filed as a `deferred` issue so the gate is written down, not remembered.

Multi-PR conventions: children 0–6 are each single-PR; only the last references
`Closes` on the epic; child 2's body carries `depends on #<child-1>`. [C: repo manual
§2; driver_lib parse_depends_on reads child-body deps]

---

## 8. Owner attention required

> **⚠ These five items are YOUR decisions — none was executed unattended.** Everything
> above is analysis and drafts; nothing routes differently until you approve, and the
> two environment problems (items 2 and 5) were worked around, not fixed.

1. **APPROVE/REJECT the plan** — specifically driver=opus (§3), review=fable (§4), and
   filing children 1–7.
2. **`~/.codex/config.toml` is broken for every Codex-backed flow** (WF5 gpt backend,
   WF13, codex-rescue): a leftover CCR "managed profile" points Codex at
   `http://127.0.0.1:3456` (dead — you said not to restart CCR) with
   `model=openai/gpt-4o-mini`. This run worked around it with a scratchpad
   `CODEX_HOME` (auth copied, native provider, gpt-5.6-sol) — **not durable**. Fix =
   delete the two CCR-managed blocks from `~/.codex/config.toml` (backup exists in
   scratchpad). One-line decision; I did not edit your global config unattended.
3. **Fable quota exposure**: review=fable puts fable on every WF2/WF3 run (bounded
   volume, with opus fallback) — if fable quota is already tight for other work, say so
   and review stays opus (child 1 flips one value back).
4. **Sonnet intro pricing expires 2026-08-31** — the ship/analysis cost case weakens
   ~⅓ then; the routing still stands on the no-real-gap argument, but re-check at the
   next bench.
5. **zhipuai SDK gap in system python3**: the GLM backend of WF5/WF13 currently needs
   the `.venv-bench` interpreter (PEP 668 blocks `pip3 --user` on this host). Decide:
   venv-wrapper in the skill docs, or a `pipx`/system exception. This run used the venv
   python directly.

---

## Appendix: method

Recomputation scripts run inline this session against `tradeoff.json` (quality, cost,
q/$, time per phase×model) and `scores.json` (per-cell medians, spreads, gate
fractions, slot-deviation audit). Peer consults ran via WF13 `--backend both`
(gpt-5.6-sol via clean CODEX_HOME; glm-5.2 via z.ai Coding Plan endpoint). Pricing
fetched live from platform.claude.com docs. Complexity-fork machinery confirmed by
reading `hooks/model_routing_lib.py`, `hooks/plan_lib.py`, `hooks/work_summary.py`,
and the WF2/WF3 skill sources at the cited lines.
