# Purposeful per-phase model routing + deterministic execution engine — rev 3

**Date:** 2026-07-16 (rev 3 — owner design review applied) · **Status:** awaiting owner
approval of the reworked epic · **Source data:** bench #14 GLM-5.2 rejudge
(`rawgentic-next` `docs/measurements/model-bench/2026-07-14-glm-rejudge/`, 216 frozen
cells, 6 models × 6 phases × 2 briefs × 3 reps) · **Thought partners this rev:** the
owner (live), gpt-5.6-sol, glm-5.2 (independent proposals:
`docs/reviews/peer-rawgentic-routing-problem-v2-2026-07-16{,-glm}.md`).

Rev 1 routed Claude-only and was corrected. Rev 2 ran the six-model comparison and a
decision register; the owner's review of rev 2 redirected the architecture itself:
**no fallback seat** (every phase gets a purposeful subject), **no prose routing**
(deterministic code + config), and **a Python execution engine** replacing
prose-subagent dispatch. Rev 3 is that design.

Every load-bearing claim is **[C]onfirmed** (evidence) or **[I]nferred** (what would
confirm). Numbers recomputed from `tradeoff.json`/`scores.json` — never prose.

## 0. The owner's directives that shaped this rev (verbatim intent)

1. No fallback position — "every stage should have a very purposeful subject." The
   interactive driver is a pure orchestrator, never a model of last resort. (The
   orchestrator role itself was never benchmarked — a driver-bench child closes that.)
2. Review fallback follows the DATA: fable 88 > sol 85.5 > sonnet 85 > opus 84 —
   "why is review not fable with a sol fallback? Opus is number four."
3. Design: sol competes — both authors draft, **a judge decides which draft wins each
   round, and the winner is used in the run.**
4. Default implementer = sonnet ("it only being one point"); super-complicated tasks are
   flagged and become **multi-model implementation bake-offs**; and "I don't want to
   allow the prose to determine whether it's complicated enough" — the gate is code.
5. "Instead of using subagents with prose, build a python app that takes model and
   prompt input, calls the run and outputs the run outputs in json" — reusable by
   kukakuka.
6. "Can we build hooks to ensure it is routing properly?" — yes: routing verified, not
   trusted (§3.4).
7. Cross-model review invariant (D9, approved): adversarial reviewer never shares the
   author's engine.
8. Every premium seat carries a fallback chain covering full unavailability (quota,
   model leaving the subscription/API, entitlement) — config-declared.

## 1. The seat table (the plan, one screen)

| Seat | Primary | Chain (floor-constrained, config-declared) | Deciding data |
|---|---|---|---|
| **orchestrator** (the Claude Code session) | opus-4-8 *(interim)* | n/a — session model | Unbenchmarked role; strong-model-on-top evidence (OptAgent: weak-top collapses 53.9%→0%) [C: AC1 verdict, PR #153 record]; **driver-bench child measures it** |
| **intake** | opus-4-8 | opus → fable → sonnet | Median leader 88.5 [81]; owner picked opus over the peers' fable-primary to keep fable quota concentrated on review where its edge is REAL, not parity |
| **design** | **COMPETITIVE: sol vs opus**, judge glm-5.2, winner used in the run | author lanes fall back in-family only (sol→terra; opus→fable); judge fail → §3.3 hybrid | sol 87.5 [85] best floor+sd in bench vs opus 87 [81]; gap noise; cross-judge caveat (Gemini bench: fable ≥ sol) — competition + telemetry settles it with evidence |
| **plan** | opus-4-8 | opus → fable → terra | Best floor 82 (terra 78, sonnet 28); medians all noise; planning defects propagate into every build task |
| **build** | sonnet-5 (default) | sonnet → opus → terra; **gate-flagged → BAKE-OFF {sonnet, opus, terra}**, judge glm on deterministic test evidence | opus−sonnet +1 @ sd 7.6 = noise at 1.74× cost (owner's one-point rule); gpt disqualified as *default* (62 [18]) but terra rides bake-offs as free-quota signal |
| **review** | fable-5 | **fable → sol → sonnet** (interim, pre-gpt-lane: fable → sonnet). **opus dropped — #4 by data** (owner correction) | fable +2.5 over sol REAL; chain = measured ranking |
| **ship** | sonnet-5 | sonnet → opus → fable | sonnet 88 REAL edge (+3 opus, +6 sol) AND cheapest |

Cross-cutting rules: never Haiku (config + hook enforced); chains skip entries that
would violate the cross-model invariant for the artifact under review (chain-aware
skip, not blind next-entry — glm's catch); a chain exhausting its eligible entries is a
**handled hard failure**, never a silent downgrade; ≤3 concurrent Claude subagents with
**one slot reserved for the driver → working ceiling 2** (glm's catch).

## 2. Six-model evidence (unchanged from rev 2; the basis)

Median [worst cell] of 6 cells, GLM-5.2 judge; cost USD/phase (gpt = est@12/M, soft):

| Phase | luna | terra | sol | sonnet-5 | opus-4-8 | fable-5 |
|---|---|---|---|---|---|---|
| intake | 82 [76] $1.36 | 85 [78] $0.99 | 83 [80] $1.10 | 85 [80] $2.52 | **88.5** [81] $3.95 | 88 [86] $7.06 |
| design | 86.5 [84] $2.93 | 86 [84] $2.65 | **87.5** [85] $1.92 | 84 [46] $4.28 | 87 [81] $5.46 | 86 [84] $9.46 |
| plan | 82 [79] $2.97 | **85** [78] $2.67 | 81 [80] $2.63 | **85** [28] $6.69 | 83 [82] $7.71 | 84 [80] $13.47 |
| build | 47 [16] $7.89 | 57 [50] $9.95 | 62 [18] $7.12 | 76 [62] $6.85 | **77** [62] $11.92 | **77** [38] $16.62 |
| review | 82 [78] $7.57 | 84 [80] $6.59 | 85.5 [83] $5.93 | 85 [84] $5.71 | 84 [82] $7.19 | **88** [86] $11.63 |
| ship | 81 [78] $2.83 | 80 [76] $2.63 | 82 [80] $2.59 | **88** [82] $2.83 | 85 [82] $4.41 | 84 [82] $6.14 |

REAL gaps (> pooled per-cell sd, valid-n ≥5): fable review (+2.5 over sol); sonnet ship
(+6 over sol, +3 over opus); gpt build disqualification (−15..−30, floors 16–18).
Everything else noise — floors, cost, and mechanics decide. Cross-judge caveat: the
Gemini-judged fixture-v2 had fable ≥ sol on all phases — sol's design lead is
judge-dependent, which is exactly what the competitive rounds resolve with evidence.
Subscription economics: Claude Max + Codex are **independent quota pools**; gpt seats
load-shed the Claude 5-hour window; dollar figures are burn proxies (real out-of-pocket
$0 both engines). Noise method: effect-size heuristic (gap vs pooled per-cell sd), not a
significance test; fable plan n=5 (one named null); brief a is harder for all models.

## 3. The execution engine (replaces prose-subagent dispatch for routed seats)

Owner directive #5; both peers converged independently on the same shape. **Generalize
`model_bench_lib.py fixture-v2-campaign-run-cell`** — its per-engine command templates
(`claude --print --model X --output-format json`, codex CLI, zhipuai SDK) and
capture-dir discipline are proven by 400+ bench cells [C: bench #14 lineage]. Do not
clean-room.

### 3.1 Package (`phase_executor/`, in this repo now; extracted when kukakuka consumes it)

- `contract.py` — versioned Observation schema: `{schema_version, run_id, attempt_id,
  seat, engine, requested_model, actual_model, prompt_hash, context_hashes, usage
  {input, output, cached, cost_proxy}, timing_ms, process {exit_code, timed_out},
  parse_status, parsed_payload, raw_capture_path, fallback_reason, judge_degraded?,
  routing_config_digest}`. **`actual_model` is mandatory evidence — absent identity is
  a failure, not an unknown success** (sol's rule).
- `adapters/` — `claude_cli.py`, `codex_cli.py` (parses header for actual model),
  `zhipuai_sdk.py` (invocation pattern from `adversarial_review_lib`). Each:
  `run(request) -> Observation`. Adapter owns the model flag — no user-supplied
  override reaches the command line.
- `engine.py` — `run_seat(seat, prompt, context) -> Observation` and
  `run_competitive(seat, candidates, judge, rubric) -> (winner, losers, judge_obs,
  record)`. Enforces: chain membership, never-Haiku, cross-model invariant
  (chain-aware skip), Claude working-ceiling 2, capture + audit append. Synchronous
  (WF2 blocks on results); competitive candidates run concurrently across quota pools.
  Detached mode deliberately NOT built (both peers: cut it).
- `routing/` — declarative seat table (the §1 table as data): `seat → {primary,
  chain[], floor, forbidden_engines_when_author_is[]}`. Projects own their table; the
  engine is policy-free (kukakuka supplies its own).

### 3.2 Deterministic complexity gate (owner directive #4 — code, not prose)

Lives beside `plan_lib` (where `riskLevel` fail-closed and `issue.complexity` already
exist [C: plan_lib.py:170,202-205; work_summary.py:208-211]):

```python
def needs_bakeoff(task, issue, plan_est, cfg) -> GateDecision:
    return (task.riskLevel == "high"
        or issue.complexity == "complex"
        or hits_security_surface(plan_est.files)   # repo-owned globs: auth/secrets/
                                                   # payments/migrations/CI/crypto
        or plan_est.lines > cfg.BAKEOFF_DIFF_LINES
        or plan_est.file_count > cfg.BAKEOFF_FILE_COUNT)
```

Thresholds are config. Missing/invalid mandatory metadata → bake-off (fail-closed,
sol's rule). Returns decision + reason codes + input snapshot + policy digest; the
executor recomputes the digest at admission (gate can't be edited between plan and
run). Known limit, named: plan-time size estimates can undershoot — the security-glob
override is the backstop, and the glob list completeness is a maintained artifact.

### 3.3 Competitive rounds (design always; build when gated)

- Authors run concurrently on independent quota pools against byte-identical
  prompt/context snapshots, isolated captures.
- Judge = **glm-5.2** (shares no author engine), bench-#14 rubric reused verbatim
  (both peers: no new rubric). Judge returns winner + criterion scores + confidence on
  **anonymized, randomly-ordered drafts**; build bake-offs judge on deterministic
  test/static-analysis evidence + anonymized patches, not vibes.
- **Judge failure (after one retry) — owner-picked hybrid:** interactive session →
  stop and ask the owner; headless → winner = incumbent lane (opus), round flagged
  `judge_degraded`, excluded from telemetry, surfaced in the morning report.
- Winner's exact bytes (hashed) become the phase artifact; losers + scores persist to
  `bakeoff_results.jsonl`. Retirement of a chronic loser needs a preregistered sample
  size and downstream-outcome evidence, not raw win counts (sol's rule) — reviewed
  quarterly, no auto-retirement.
- **D9 wiring:** the winner's engine determines the adversarial-review backend —
  gpt-authored winner → Claude/glm reviewer, never gpt.

### 3.3b Parallelization contract (owner directive, added live)

Dispatch is parallel wherever the work is independent — serial execution is the
exception that must justify itself:

- **Bake-offs are always fully parallel:** all candidates launch simultaneously —
  build's {sonnet, opus, terra} = 2 Claude slots (exactly the working ceiling) + terra
  on the codex pool concurrently, **each in an isolated git worktree** so builders
  cannot collide on the shared tree [C: worktree isolation is the established
  parallel-build mechanism — Agent tool `isolation: worktree`; the executor's capture
  dirs adopt the same discipline]. Wall-clock = slowest candidate, not the sum.
- **Design rounds:** both authors (sol on codex pool, opus on Claude pool) launch
  together; the judge (glm, third pool) fires the moment both observations land.
  Three quota pools, zero contention.
- **Independent seats/tasks pipeline:** where the WF2 DAG allows (e.g. multiple
  standard build tasks with no dependency edges), the executor runs them concurrently
  under the Claude working ceiling (2), queuing overflow rather than serializing
  everything — queue wait is logged in the Observation (`queued_ms`) so stalls are
  diagnosable.
- **The ceiling accounting:** cross-engine candidates (terra/sol/glm) never consume
  Claude slots — parallelizing across pools is free capacity, which is exactly the
  subscription-economics argument for keeping gpt lanes in the bake-off set.
- E1/E5 acceptance criteria include a live demonstration: a 3-candidate bake-off whose
  total wall-clock is within 1.3× the slowest single candidate.

### 3.4 Routing enforcement hooks (owner directive #6 — verified, not trusted)

- **PreToolUse:** requested model ∈ the seat's declared chain; not Haiku-family;
  routing-config digest matches; gate digest present when build claims `default`.
- **PostToolUse:** `requested_model == actual_model` (normalized), sourced from the
  provider's own output (claude JSON / codex header) — mismatch fails the call,
  non-retryable; Observation appended to per-run `routing_audit.jsonl`.
- **Run-end audit:** reconciles expected seat calls vs audit records; refuses ship on
  missing/duplicate/unverified calls; WF2 accepts only executor result envelopes so
  uninstrumented dispatch can't sneak past the hooks (sol's orphan-work rule).
- Honest limit (glm): if a CLI ever silently substitutes a model without reflecting it
  in its own output, the hook can't see it — pin CLI versions, fixture-test the
  parsers.

### 3.5 Driver-bench (closing the gap the owner named)

The orchestrator role was never measured. Design (sol's stubbed-matrix shape, adopted):
fixtures = synthetic issues + **stubbed executor responses** + injected failures
(quota, model-retired, judge timeout) + expected state-transition graph. Score: correct
seat/fallback selection, gate computation, cross-model enforcement, winner propagation,
audit completeness, recovery, token burn. Matrix: 12 fixtures × 3 reps × 2 driver
models (opus, sonnet) = 72 stubbed runs (cheap — stubs), then 3 live end-to-end runs.
Orchestrator seat stays opus until this reports.

## 4. Epic restructure (supersedes the rev-1 child set — owner approval needed)

The executor changes the mechanism, so the filed children need rework. Proposed:

| Child | Status | Content |
|---|---|---|
| #414 CCR repair | **stands** | unchanged; still owner-gated; still blocks any gpt lane work |
| E1 (new) | **file** | `phase_executor/` package: contract + adapters + engine + routing config + tests (generalize bench cell-runner). The foundation — blocks E2–E6. (feat, L) |
| E2 (new) | **file** | Routing enforcement hooks (Pre/Post/run-end audit) + `routing_audit.jsonl` (feat, M) |
| E3 (reworks #415+#416) | **rewrite** | Seat table config incl. fallback chains (full unavailability class, config-declared) + the review flip to fable→sol→sonnet + provenance stamp. Depends on E1+E2. |
| E4 (reworks #418) | **rewrite** | Ship + intake + plan seats through the executor (delegation boundaries; driver-only list stands: merge, CI triage, deploy+verify, Step 16) |
| E5 (new) | **file** | Competitive design rounds + build bake-off {sonnet, opus, terra} + glm judge + hybrid judge-failure policy + `bakeoff_results.jsonl` (feat, L) |
| E6 (new) | **file** | Deterministic complexity gate in plan_lib + security-surface globs + thresholds config (feat, M) |
| #417 skill prose | **shrinks** | WF2/WF3 prose now just calls the executor per seat; D9 backend-selection rule; driver-seat note |
| #419 refresh-rule doc | **stands** | + seat-table refresh discipline (glm's config-rot catch: floor-based picks must be re-derived every bench, not persist silently) |
| #420 telemetry | **stands** | now largely emitted by the executor (routing_audit + bakeoff_results feed run records) |
| #421 per-phase schema | **closes** | the routing config IS the per-phase schema — evidence gate met by owner directive |
| E7 (new) | **file** | Driver-bench (stubbed matrix + 3 live) (feat, M) |

Sequencing: #414 (independent) · E1 → E2 → E3 → {E4, E5, E6} → E7. WF2 keeps working
throughout — the executor lands behind the existing prose path and seats cut over one
at a time (review first: highest value, bounded volume).

## 5. Owner attention

> **⚠ Your calls.** Decisions already made this rev (live, with you): sonnet default
> implementer · competitive design pilot with judge-selected winner used in-run ·
> intake=opus (fable quota concentrated on review) · hybrid judge-failure policy ·
> bake-off = {sonnet, opus, terra} · executor lives in-repo now · review chain drops
> opus (#4 by data) · D9 stands.

1. **Approve the epic restructure** (§4): file E1–E7, rewrite #415/#416/#418, shrink
   #417, close #421. Rev-1 children #415/#416/#418 get superseded-by comments, not
   silent edits.
2. **#414 (CCR repair) is now on the critical path** for the sol lanes (design
   competition + review fallback) — the scratch CODEX_HOME workaround serves interim
   but isn't durable.
3. **Orchestrator stays opus until driver-bench (E7) reports** — confirm you're OK
   holding that seat decision on its evidence.
4. **zhipuai venv gap** (unchanged from rev 2): glm judge calls run via `.venv-bench`
   python — durable home decision pending (venv wrapper in skill docs vs pipx).
5. **kukakuka linkage**: E1's contract review is the moment to sanity-check kukakuka's
   input needs (its model+prompt→JSON shape) so the extraction later is a move, not a
   rewrite.

## Appendix: method

Rev 3 synthesized from: owner's live design review (per-decision feedback + four
resolved forks), independent peer proposals from gpt-5.6-sol and glm-5.2 on the v2
problem artifact (both consulted blind; glm's first attempt returned vacuous and was
retried successfully — named, not hidden), bench #14 recomputation, and code-confirmed
machinery (`model_bench_lib` cell-runner, `model_routing_lib`, `plan_lib`,
`adversarial_review_lib`, hooks registry). Peer convergences adopted without debate;
divergences (judge-failure policy, intake seat, bake-off breadth, package home) were
resolved by the owner. Prior revs' peer/adversarial reports remain in `docs/reviews/`.
