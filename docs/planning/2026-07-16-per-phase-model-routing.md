# Per-phase model routing for WF2/WF3 — six-model verdict from bench #14

**Date:** 2026-07-16 (rev 2 — six-model scope per owner correction) · **Status:** awaiting
owner approval · **Source data:** bench #14 GLM-5.2 rejudge (`rawgentic-next`
`docs/measurements/model-bench/2026-07-14-glm-rejudge/` — report md/html + `scores.json` +
`tradeoff.json`, 216 frozen cells: **6 models** × 6 phases × 2 briefs × 3 reps)
· **Peers consulted:** gpt-5.6-sol (Codex) + glm-5.2 (Zhipu), independent proposals in
`docs/reviews/peer-rawgentic-routing-problem-2026-07-16{,-glm}.md` · adversarial review
both backends, 11 findings applied.

The six candidates: **claude-fable-5, claude-opus-4-8, claude-sonnet-5, gpt-5.6-sol,
gpt-5.6-terra, gpt-5.6-luna.** Rev 1 of this doc silently narrowed the routing seats to
Claude models; rev 2 runs the full six-model comparison first and then applies the seat
constraints openly.

Every load-bearing claim is **[C]onfirmed** (with evidence) or **[I]nferred** (with what
would confirm it). All numbers recomputed from `tradeoff.json`/`scores.json` this run —
never report prose.


## 1. Six-model verdict, phase by phase

Median of 6 cells [worst cell] (per-cell sd), GLM-5.2 judge. **Bold = best in bench.**
Cost = USD/phase; gpt costs are `est@12/M` estimates (soft), Claude costs are billed
tokens at list rates. [C: recomputed]

| Phase | luna | terra | sol | sonnet-5 | opus-4-8 | fable-5 | Best-in-6, and is the gap real? |
|---|---|---|---|---|---|---|---|
| intake | 82 [76] | 85 [78] | 83 [80] | 85 [80] | **88.5** [81] | 88 [86] | opus — but vs terra +3.5 @ sd 3.9 = **noise** |
| design | 86.5 [84] | 86 [84] | **87.5** [85] | 84 [46] | 87 [81] | 86 [84] | sol — vs opus +0.5 @ sd 2.1 = **noise**, but sol has the best floor (85) and sd (1.2) |
| plan | 82 [79] | **85** [78] | 81 [80] | **85** [28] | 83 [82] | 84 [80] | terra/sonnet tie — all gaps noise (pooled sd up to 12.3); opus has the best floor (82) |
| build | 47 [16] | 57 [50] | 62 [18] | 76 [62] | **77** [62] | **77** [38] | opus/fable — gpt is **disqualified for real**: 15–30 pts behind, floors 16–18 |
| review | 82 [78] | 84 [80] | 85.5 [83] | 85 [84] | 84 [82] | **88** [86] | fable — vs runner-up sol +2.5 @ sd 1.6 = **REAL** |
| ship | 81 [78] | 80 [76] | 82 [80] | **88** [82] | 85 [82] | 84 [82] | sonnet — vs best gpt (sol) +6 @ sd 2.4 = **REAL** |

Cost row (USD/phase, for the same cells):
intake $1.36/$0.99/$1.10/$2.52/$3.95/$7.06 · design $2.93/$2.65/$1.92/$4.28/$5.46/$9.46 ·
plan $2.97/$2.67/$2.63/$6.69/$7.71/$13.47 · build $7.89/$9.95/$7.12/$6.85/$11.92/$16.62 ·
review $7.57/$6.59/$5.93/$5.71/$7.19/$11.63 · ship $2.83/$2.63/$2.59/$2.83/$4.41/$6.14
(order: luna/terra/sol/sonnet/opus/fable). [C: tradeoff.json]

**What survives the noise floor across all six models** [C: gap > pooled per-cell sd,
valid-n ≥5]:
1. **fable-5 is the best reviewer in the bench** (+2.5 over sol, +3 over sonnet, +4 over opus).
2. **sonnet-5 is the best shipper in the bench** (+3 over opus, +6 over sol).
3. **gpt-5.6 (all three) cannot build** in this harness — the one disqualifying gap.
4. Everything else — including every intake/design/plan ranking — is inside noise, so
   **floors, gates, cost, and dispatch mechanics decide**, not medians.

### Cross-judge caveat on sol

The earlier fixture-v2 capture (2 candidates, **Gemini** judge, 2026-07-12) had
**fable ≥ sol on all six phases**, sol never clearly winning one. Under GLM, sol tops
design and runs second at review. Two judges, two orderings for sol's text phases —
sol's design case is **judge-dependent** and gets a pilot, not a seat (decision D3).
[C: fixture-v2 verdict, docs/measurements lineage; GLM matrix above]

### Engine mechanics (a seat constraint, stated openly this time)

Two facts, one hard, one soft:
- **Hard:** the interactive driver IS a Claude Code session — no gpt model can hold that
  seat regardless of subscription or quality.
- **Soft:** WF2/WF3 role routing dispatches **Claude** subagents via the Agent `model:`
  parameter [C: SKILL.md:145-156]. gpt-5.6 models execute through the Codex CLI — a lane
  that already exists and serves WF5/WF13/codex-rescue (and ran the bench's gpt cells)
  [C: adversarial_review_lib.py codex exec path], but is not wired into role routing.
  A gpt subagent seat costs the **one-time** D8 lane build, not per-run dollars.

### Subscription economics (owner correction, rev 2)

The owner holds BOTH a Claude Max and a Codex subscription. Every candidate's work is
therefore **$0 out-of-pocket** — the `est@12/M` gpt figures and the Claude billed-token
figures are API-equivalents, useful for burn-rate proxies only. The real scarce resources
are the two subscription quotas, and they are **independent pools**: work routed to a gpt
model through the codex lane load-sheds the Claude 5-hour usage window — the binding
constraint on long runs in this workspace [C: overnight-run history; workspace manual
"concurrent Codex job is fine — different provider, different quota"]. This reframes
D3/D6/D8: a gpt seat on a text phase is not "extra cost for noise-level quality" but
"free capacity from the second pool at noise-level quality difference" — provided the
lane exists and the cross-judge caveat clears.


## 2. Decision register — every decision, its alternatives, and the deciding facts

**D1 — Interactive driver: claude-opus-4-8.** *(confidence: high)*
- Alternatives weighed: sonnet-5 (cheaper, ship edge); terra (plan median tie, 4× cheaper
  than opus at intake); "sol drives design" (see D3).
- Deciding facts: the driver executes intake/design/plan inline, where a single collapse
  poisons the run. Worst-cells: opus 81/82 with 6/6 gates; sonnet 46/28 with 5/6; terra
  plan floor 78. Sonnet's real edges (ship) are harvested by subagents instead (D5).
  gpt models can't hold the seat at all — the driver IS a Claude session.
- What would flip it: a future bench where sonnet's design/plan floors clear 80 on the
  hard brief.

**D2 — Review seat: claude-fable-5, run-scoped fallback to opus.** *(confidence: high)*
- Alternatives: opus (current), sonnet, sol (runner-up, 85.5, cheapest reviewer).
- Deciding facts: fable's +2.5..+4 review edge is the strongest REAL gap in the bench,
  against all five others; review gates everything downstream (Steps 4/8a/11). Volume is
  bounded (1 + high-risk-count + 1 per run). sol as reviewer would also need the new gpt
  dispatch lane AND loses the cross-judge caveat.
- Quota risk owned: fallback chain lands BEFORE the flip (#415 blocks #416).
- What would flip it: fable quota pressure the owner rejects (§4 item 3), or the edge
  vanishing at the next bench.

**D3 — Design stays driver-inline (opus) NOW; sol gets an evidence pilot.** *(confidence: medium — this is the genuine six-model finding)*
- Alternatives: (a) keep inline opus; (b) dispatch design drafting to a sol subagent via
  a new codex lane, driver reviews and owns.
- Facts for sol: best design floor in the bench (85 vs opus 81), tightest sd (1.2),
  gate-clean, ~35% of opus cost (soft basis). Facts against switching now: median gap is
  noise (+0.5); the Gemini-judged bench ranked fable>sol on design; a codex dispatch lane
  doesn't exist for role routing; design handoff cost (driver still must own the design)
  was never measured.
- Subscription reframe (owner correction): sol drafts spend CODEX quota, not the Claude
  window — on the two-pool economics the pilot's marginal cost is ~zero, which is why it
  is worth running despite the noise-level median gap.
- Decision: keep opus inline; file a **pilot child** — dispatch design DRAFTS to sol in
  N real WF2 runs behind the driver's ownership, judge the drafts, then decide with
  two-judge evidence. No seat change on one judge's noise-level ranking.
- What would flip it: pilot shows sol drafts survive driver review with less rework at
  lower Claude-window burn.

**D4 — Implementation ceiling: opus-4-8; standard tasks down-route to sonnet-5.** *(confidence: high)*
- Alternatives: fable ceiling; sonnet-only; any gpt — disqualified (build 47–62,
  floors 16–18, REAL gap).
- Deciding facts: opus−sonnet build gap +1 @ sd 7.6 = noise, at 1.74× cost — the owner's
  stated fork case, and the fork **already exists**: `select_impl_model(ceiling,
  riskLevel, complexity)` routes high-risk/complex → ceiling, else sonnet
  [C: model_routing_lib.py:121-152]. Fable adds cost (1.4× opus), no build edge, and a
  38 floor.
- What would flip it: nothing pending — this is confirmation of existing machinery.

**D5 — Ship tasks: sonnet-5, via a NEW delegation boundary.** *(confidence: high)*
- Alternatives: keep driver-inline (opus); sol ($2.59, cheapest — but −6 REAL vs sonnet).
- Deciding facts: sonnet is the bench's best shipper (REAL edge over everyone) AND
  cheapest-or-tied among Claude. The catch both adversarial reviewers surfaced: the
  down-route only fires for DELEGATED tasks [C: steps.md:961], and ship work is inline
  today — so the change is the delegation boundary itself (#418). Driver keeps merge,
  CI triage, deploy+verify, Step 16.
- What would flip it: delegation overhead exceeding the quality/cost win in practice
  (telemetry, #420).

**D6 — Intake/plan: stay with the driver (opus).** *(confidence: medium-high)*
- Alternatives: terra subagent for intake ($0.99, median −3.5 in noise) and plan (median
  +2 in noise).
- Deciding facts: both phases are the driver's own thinking surface; all gaps are noise
  and terra's plan floor (78) is below opus (82). The subscription reframe cuts both
  ways here: dispatching would load-shed the Claude window (free capacity), but intake
  and plan feed the driver's OWN next step — the handoff cost is highest exactly where
  the driver must re-ingest the output.
- What would flip it: a bench showing a REAL terra edge, or the gpt lane existing anyway
  (built for D3's pilot) making a low-risk trial cheap — revisit then, with the
  load-shedding argument on the table.

**D7 — No per-phase config schema yet.** *(confidence: high)*
- The data supports exactly 3 real distinctions (review, ship, gpt-can't-build); all are
  expressible in the existing 3-role model + delegation boundary. Both peer models
  converged on deferral unprompted. Evidence gate recorded as #421.

**D8 — gpt dispatch lane: file as a child, build only if D3's pilot needs it.** *(confidence: medium)*
- The lane (codex-exec dispatch for role-routed work, mirroring WF5's invocation shape)
  is the prerequisite for ANY gpt seat. Don't build speculatively; the pilot child
  carries it.


## 3. What the bench actually says (recomputed) — supporting detail

### 3.1 Noise methodology

Gap test: |median A − median B| > pooled per-cell sd (mean of the two models' population
sd over valid cells; n=6 per model×phase except fable plan n=5, one null cell at brief a
rep 2 — named, not hidden). This is an **effect-size heuristic, not a significance
test**. Brief a is simply harder for every model (mean slot deviation −1.0..−2.3 vs +0.4
for brief b) [C: recomputed] — the collapse cells are genuine failures under difficulty,
not fixture artifacts.

### 3.2 Reliability floors — the driver-seat decider

Worst cell of 6, phases the driver does inline [C: scores.json perCell]:

| | design floor | plan floor | design gates | plan gates |
|---|---|---|---|---|
| opus-4-8 | **81** | **82** | 6/6 all five | 6/6 all five |
| sonnet-5 | 46 | 28 | 5/6 each | 5/6 each |
| fable-5 | 84 | 80 (n=5) | 6/6 all five | 4/5 on P-G1, 5/5 rest |
| sol | **85** | 80 | 6/6 all five | 6/6 all five |
| terra | 84 | 78 | 6/6 all five | 6/6 all five |
| luna | 84 | 79 | 6/6 all five | 6/6 all five |

Note what this table shows honestly: **on text phases the gpt models are floor-solid and
gate-clean** — the gpt weakness is exclusively build. That is exactly why D3 is a pilot
rather than a dismissal.

Build gates B-G2/B-G3 fail 0/6 for opus AND fable, 2/6 sonnet — those two gates say more
about the gate than the models [C: gateFrac].

### 3.3 Pricing (verified live this run)

[C: fetched platform.claude.com/docs pricing 2026-07-16] opus-4-8 $5/$25 · sonnet-5
**intro $2/$10 through 2026-08-31** then $3/$15 · fable-5 $10/$50 (per M; cache read
0.1×). Bench sonnet costs are anchored at post-intro $3/$15 — sonnet's current
API-equivalent is ~⅓ lower through August. gpt rows are `est@12/M` estimates: informative
for order-of-magnitude, never load-bearing alone.

### 3.4 Known limitations

Single benchmark; 2 briefs × 3 reps; n=6 (one n=5) cells per model×phase. GLM judges
stricter than Gemini (pooled ρ=0.621; every per-model mean Δ negative) — absolute scores
don't transfer across judges, so all comparisons here are within the judge-uniform GLM
matrix, with the sol cross-judge caveat named in §1. Driver economics are subscription
usage-window burn; API-equivalent is the proxy [I: run-record usage telemetry (#420)
would quantify real burn share].


## 4. Owner attention required

> **⚠ These are YOUR decisions — none was executed unattended.** Nothing routes
> differently until you approve; the environment problems (items 2 and 5) were worked
> around, not fixed.

1. **APPROVE/REJECT the decision register** (D1–D8) — specifically driver=opus (D1),
   review=fable (D2), the sol design pilot instead of a seat change (D3), and ship
   delegation (D5).
2. **`~/.codex/config.toml` is CCR-hijacked** — every Codex-backed flow (WF5 gpt, WF13,
   codex-rescue) fails until the two CCR-managed blocks are removed. Worked around this
   run via a scratch `CODEX_HOME` — not durable. Fix = #414, needs your OK (global config).
3. **Fable quota exposure**: review=fable puts fable on every WF2/WF3 run (bounded, with
   the #415 fallback). If fable quota is already committed elsewhere, say so — review
   stays opus and D2 flips.
4. **Sonnet intro pricing expires 2026-08-31** — D4/D5 cost margins shrink ~⅓; the
   decisions still stand on the no-real-gap / real-edge arguments.
5. **zhipuai SDK absent from system python3** (PEP 668) — the GLM backend of WF5/WF13
   ran via `.venv-bench` python. Decide the durable home (venv wrapper in skill docs, or
   pipx).
6. **NEW (rev 2): approve filing the two additional children** — sol design pilot (D3)
   and the gpt dispatch lane it depends on (D8) — or fold them into #421's deferred
   record.


## 5. WF2/WF3 changes (unchanged from rev 1 except D3/D8 additions)

Config: `modelRouting.review: opus → fable` + provenance stamp (child #416, gated on
#415's fallback chain). Fallback transition contract: enumerated trigger classes
(429/usage-limit family, vacuous-death signature `confirmedCount: 0` + empty body),
atomic trip + one immediate opus replay, in-flight dispatches replay once each, never
below opus, never Haiku. No programmatic ≤3-subagent clamp exists today [C: grep this
run] — #415 adds logged concurrency counts, code clamp stretch. Ship delegation boundary
(#418) with driver-only list (merge, CI triage, deploy+verify, Step 16) and both-path
executing-model tests. Skill prose + drift guards (#417). Refresh-rule doc (#419):
role→phase map, pooled-sd formula, min-n 5, floors ≥70 subagent / ≥80 driver +
gates ≥5/6, move only on gap>sd AND floor pass, re-stamp provenance every decision.
Telemetry (#420). Deferred per-phase schema (#421).

## 6. Epic + children

Filed (epic **#422**): #414 CCR repair (owner-gated) · #415 fallback chain (**blocks
#416**) · #416 review→fable flip · #417 skill prose · #418 ship delegation · #419
refresh-rule doc · #420 telemetry · #421 deferred schema spike.

**Proposed rev-2 additions (await §4 item 6):**
- **sol design pilot (D3):** dispatch design DRAFTS to gpt-5.6-sol behind driver
  ownership for N real WF2 runs; score drafts with both judges; seat decision follows
  the #419 refresh rule.
- **gpt dispatch lane (D8):** codex-exec dispatch for role-routed work (WF5-shaped
  invocation, output contract, timeout/dead-job protocol per docs/codex-reliability.md).
  Prerequisite of the pilot; built only with it.


## Appendix: method

All numbers recomputed inline this session from `tradeoff.json` (quality, cost, time) and
`scores.json` (per-cell medians, spreads, floors, gate fractions, slot-deviation audit)
across **all six models**. Peer consults: WF13 `--backend both` (gpt-5.6-sol via clean
CODEX_HOME; glm-5.2 via z.ai). Adversarial review: WF5 `--backend both`, 11 findings, all
applied (reports in docs/reviews/). Pricing fetched live. Machinery confirmed by reading
model_routing_lib.py, plan_lib.py, work_summary.py, and the WF2/WF3 skill sources at the
cited lines. Rev 2 adds the six-model comparison, the decision register, the sol
cross-judge caveat, and D3/D6/D8 — correcting rev 1's silent Claude-only narrowing.
