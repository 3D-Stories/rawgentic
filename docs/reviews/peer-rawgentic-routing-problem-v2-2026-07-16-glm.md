# Peer Consult — .rawgentic-routing-problem-v2.md

- Date: 2026-07-16
- Reviewer: GLM (model glm-5.2, peer designer)

## Approach

Generalize the existing bench cell-runner into a small, project-agnostic execution engine that owns every model invocation (claude-cli / codex-cli / zhipuai-sdk), emits a strict JSON observation contract, and is the single chokepoint where requested-vs-actual routing is asserted. WF2 and rawgentic stop naming models in prose; instead they resolve a SEAT (intake/design/plan/build/review/ship) to a routing entry, and the executor honors it. Every seat has an explicit primary and a declared fallback chain that never drops below the seat's floor. The driver becomes a pure Claude Code orchestrator: it sequences seats, feeds context, and reacts to executor JSON, but it is never itself a 'model of last resort.' Competitive rounds (design, and flagged build bake-offs) are just the executor running N candidate cells against N adapters and feeding outputs to a judge cell whose adapter family differs from all authors. A PostToolUse hook consumes the executor's emitted actual-model field and fails the run on mismatch, closing the 'trusted routing' gap.

## Key decisions

- Executor boundary: a new `phase_executor` package (reusable by kukakuka) with three layers: (a) `adapters/` — one per engine: `claude_cli_adapter` (wraps `claude --print --model X --output-format `), `codex_cli_adapter`, `zhipuai_sdk_adapter`; each exposes `run(request)->Observation`; (b) `engine.py` — takes {seat, model, prompt, context/files, judge?}, looks up the routing entry, selects adapter by model family, enforces concurrency/quota lanes, writes a deterministic capture-dir observation, returns structured JSON; (c) `contract.py` — the shared Observation schema: seat, requested_model, actual_model, engine, usage{tokens,cost_proxy}, timing, exit_status, parsed_payload, raw_path. Generalize `fixture-v2-campaign-run-cell` rather than rewrite; lift its command templates and capture discipline verbatim into the adapters.
- Routing table: a config object (not code logic), `routing.toml`/`routing.py` declarative map of SEAT -> {primary, fallback_chain, floor, adapter_hint, min_floor}. Loaded by the executor at start; WF2/plan_lib only ever pass a SEAT string. This keeps the engine project-agnostic while project policy lives in config. Never-Haiku and the <=3 Claude concurrency are enforced here as preflight assertions.
- Seat table (purposeful, no interactive-driver fallback): intake -> fable-5 (floor 86, median 88; best floor, quality parity with opus) chain [fable -> sol(83) -> opus]; design -> competitive sol-vs-opus round, winner used (sol median 87.5 #1, opus 87 #2; cross-judge caveat noted) — NO single-seat fallback; the judge picks; design-author fallback only within the author's own family for hard unavailability; plan -> opus-4-8 (best floor 82; median 83 is noise but floor wins for a planning seat where worst-case matters) chain [opus -> terra(78 floor, 85 med) -> sol]; build -> sonnet-5 default (76 med, floor 62, 1.74x cheaper than opus, gap within sd) chain [sonnet -> opus -> terra]; super-complicated flagged tasks -> bake-off {sonnet, opus, terra/sol}, judge picks, telemetry logged; review -> fable-5 (real +2.5 over sol, floor 86) chain per the DATA ranking [fable -> sol(85.5) -> sonnet(85)]; ship -> sonnet-5 (real +6 over sol, +3 over opus, floor 82) chain [sonnet -> opus -> fable]. Note on review chain: follow measured ranking fable>sol>sonnet>opus, so the interim chain ends at sonnet, and once a gpt lane exists the chain is fable->sol->(gpt review model); we do NOT route fable->opus because opus is #4 by the data.
- Review cross-model invariant enforced in config: the review seat's adapter family must differ from the design-winner author family; the executor asserts this before dispatching review (gpt-authored design -> Claude/glm reviewer; Claude-authored -> codex/zhipuai reviewer).
- Competitive design round mechanics: judge = glm-5.2 (shares no engine with sol[gpt] or opus[claude]); rubric sourced from the same bench-#14 design rubric (reuse the gemini/glm batch judge machinery, not a new rubric); latency budget = both authors run concurrently in independent quota lanes, judge runs after both Observations land, hard timeout per author = existing adversarial_review_lib timeout; on judge failure (timeout/parse-fail) fail-closed: re-run judge once, then fall back to deterministic tiebreak = sol (bench #1 median) but emit a `judge_degraded` flag and exclude the round from telemetry. Winner feeds the run as the design Observation payload; loser payload + scores persisted to a `bakeoff_results` log for retirement telemetry (rolling win-rate, floor-cell regressions).
- Deterministic complexity gate lives in `plan_lib` (where riskLevel/complexity already live), NOT in the executor and NOT in prose. Inputs: issue.complexity (WF2 Step 2), task.riskLevel (fail-closed), diff_size estimate (lines touched from the plan), file_count touched, and security-surface globs (auth/payments/secrets/CI/migration paths) as a hard override. Gate = code predicate: flag-to-bakeoff iff riskLevel==high OR complexity in {high,critical} OR security_surface_hit OR diff_size>threshold OR file_count>threshold. Thresholds are config, not prose. The executor receives a `bakeoff=true` flag and runs the candidate set.
- Routing enforcement hook: PostToolUse Python hook registered in hooks. intercepts the executor tool; it reads the Observation JSON from capture-dir, compares observation.requested_model == observation.actual_model (string-normalized to family+version), and on mismatch fails the tool call with a non-retryable error. PreToolUse hook additionally asserts the requested model is in the seat's allowed chain (config-loaded) and is not Haiku-family. The hook also appends every Observation to a per-run `routing_audit.l` so the whole run's actual routing is reconstructable post-hoc.
- Sync vs detached: default synchronous (WF2 blocks on the seat result); competitive rounds and bake-offs run N candidate cells concurrently inside the executor (still sync from WF2's view) using the existing per-engine repetition/capture-dir discipline. Detached mode is a later kukakuka concern; not built now.
- Driver-bench proposal: fixture = N synthetic end-to-end micro-projects (each with a planted bug + a planted design tradeoff), run the full seat pipeline under a fixed driver, score (a) did the correct seat sequence execute, (b) did routing assertions hold, (c) did the competitive/judge path produce the bench-expected winner, (d) wall-clock + token burn, (e) human-rated final-ship quality. Baseline the current driver against a trivial 'always-opus' driver to isolate orchestrator effect. Cost ~ one full 6-seat run per fixture x N; estimate N=8 => roughly 8x a single ship run (~$300-500 burn-proxy given fable-heavy seats). Start with N=3.
- Over-engineering to cut: (1) do not build detached/async execution now — sync is enough; (2) do not build a new judge rubric — reuse bench-#14's; (3) do not build dynamic fallback reordering or ML-based routing — static config chains; (4) do not build a live quota-aware load shedder — the two quota pools (Claude Max + Codex) are handled by splitting adapter families, not by a scheduler; (5) skip a formal loser-retirement pipeline initially — just persist bakeoff_results and review quarterly.

## Risks

- fable-5 is the primary for intake AND review; both are expensive ($7.06/$11.63 burn-proxy) and both lean on the same quota — a fable outage pushes intake onto sol (floor drops to 80) and review onto sol (loses the real +2.5 edge), which materially weakens two seats at once. Need a documented degraded-mode expectation, not just a chain.
- The design-round judge-dependence caveat is real: glm-5.2 picking between a gpt-authored and a claude-authored draft could share Gemini-bench bias patterns. If glm is the only neutral judge available, we have no second opinion; a judge-failure tiebreak = sol bakes that bias into fallback.
- Routing assertions depend on the executor emitting a truthful actual_model. If claude-cli or codex-cli ever silently substitutes a model without reflecting it in JSON/header, the hook cannot catch it. Mitigation: pin CLI versions and assert on the exact header field, but this is trust-in-CLI by another name.
- The deterministic complexity gate uses diff_size/file_count from the PLAN, which are estimates; a task can look small (1 file) but be semantically critical (security_surface glob must catch this). If the security glob list is incomplete, misclassification flows straight to a single-sonnet run with no bakeoff.
- Concurrent Claude subagent cap (<=3) collides with design round (opus author) + sonnet build + opus/sonnet elsewhere + the Claude Code driver itself. The executor must reserve one slot for the driver and treat 2 as the working ceiling; a competitive round may block the pipeline.
- The review-chain fix (fable->sol->sonnet) means a gpt-authored design that fails fable AND sol could land on a Claude reviewer (sonnet) — violating the cross-model invariant if the design winner was opus( claude ). The executor's invariant check must be chain-aware: it cannot just pick the next chain entry, it must skip entries sharing the author engine, which can shorten the usable chain below the floor.
- plan seat choosing opus on floor (82) over sol/terra whose medians are competitive is defensible but fragile: if the next bench widens sol's plan median lead, the floor-based justification silently persists because routing is config, not auto-updated. Needs a bench-driven refresh discipline.
- Driver-bench cost estimate is rough and could balloon if competitive rounds multiply per fixture; without a cap on N and on bakeoff breadth it becomes the expensive part of the system.

## Sketch

``nphase_executor/                      # project-agnostic core, reusable by kukakuka
  contract.py
    @dataclass Observation:
      seat, requested_model, actual_model, engine_family,
      usage{input,output,cost_proxy}, timing_ms, exit_status,
      parsed_payload: dict, raw_capture_path: str, judge_degraded: Optional[bool]
  adapters/
    base.py      class EngineAdapter: run(req)->Observation
    claude_cli.py   # wraps: claude --print --model X --output-format 
    codex_cli.py    # wraps codex equivalent, parses header for actual_model
    zhipuai.py      # sdk path from adversarial_review_lib
  engine.py
    class PhaseExecutor:
      def __init__(routing: RoutingTable, concurrency_limits, capture_root): ...
      def run_seat(seat, prompt, context, files=None) -> Observation
      def run_competitive(seat, candidates:[model], judge_model, rubric)
                   -> (winner_obs, loser_obs, judge_obs, bakeoff_record)
      # internal: enforce never-haiku, <=3 claude, cross-model invariant,
      #           write capture-dir observation, emit routing_audit line
  routing/
    schema.py     # SEAT -> {primary, fallback[], floor, adapter_hint, min_floor,
                 #   forbidden_engines_when_author_is:[...]}
    default.py    # the declarative table (data-driven, no prose)
      intake:   fable-5  -> sol  -> opus            (floor 86)
      design:   COMPETITIVE(sol, opus) judge=glm-5.2
      plan:     opus-4-8 -> terra -> sol            (floor 82)
      build:    sonnet-5  -> opus -> terra          (default)
                if gate.bakeoff: COMPETITIVE(sonnet,opus,terra) judge=glm-5.2
      review:   fable-5  -> sol -> sonnet           (skip claude if author=opus)
      ship:     sonnet-5  -> opus -> fable          (floor 82)
  hooks/
    pre_tool_use.py   # assert requested in seat chain, not haiku
    post_tool_use.py  # assert obs.requested_model==obs.actual_model;
                      # append obs to routing_audit.l; else fail run

wf2 /
  plan_lib.py
    # deterministic gate, code only:
    def needs_bakeoff(task, issue, plan_diff_est) -> bool:
       return (task.riskLevel=='high'
            or issue.complexity in {'high','critical'}
            or hits_security_surface(plan_diff_est)
            or plan_diff_est.lines > cfg.BAKEOFF_DIFF_LINES
            or plan_diff_est.file_count > cfg.BAKEOFF_FILE_COUNT)
  # WF2 Step: executor.run_seat('design', ...)
  #           -> if competitive, executor.run_competitive(['sol','opus'],'glm-5.2', bench14_design_rubric)
  #           executor.run_seat('review', ..., author_engine=design_winner.engine_family)

telemetry/
  bakeoff_results.l   # winner, loser, scores, judge_model, degraded?
  routing_audit.l     # per-call requested/actual, per-run reconstructable
  driver_bench/           # N fixtures, seat-sequence + assertion + cost scores
``

---
_Peer proposal (report-only). Synthesize at your discretion._
