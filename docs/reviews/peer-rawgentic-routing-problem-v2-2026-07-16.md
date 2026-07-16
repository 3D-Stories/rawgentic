# Peer Consult — .rawgentic-routing-problem-v2.md

- Date: 2026-07-16
- Reviewer: Codex (peer designer)

## Approach

Extract the proven subprocess, capture-directory, retry, and JSON-parsing logic from `fixture-v2-campaign-run-cell` into a small project-agnostic `phaseexec` package; retain the benchmark runner as a compatibility client rather than replacing it. The core has `contracts` (versioned request/result JSON schemas), `runner` (timeouts, attempts, capture, cancellation), and adapters for `claude-cli`, `codex-cli`, and `zhipuai-sdk`. Each adapter must construct its own command, parse the provider-reported model, and return one normalized result containing run/attempt IDs, requested and actual canonical model IDs, engine, prompt/context hashes, usage, timing, process exit status, parse status, stdout/stderr capture references, and structured output. Provider text never supplies routing decisions.

Keep routing and workflow policy outside the reusable core. Rawgentic owns a versioned routing YAML plus a deterministic policy library; kukakuka can supply a different policy. The executor receives an already resolved call manifest, validates it, invokes exactly one model, and emits one JSON document. Start with synchronous execution because WF2 requires the result before advancing. Preserve run directories and stable IDs so a thin `submit/status/cancel` detached wrapper can be added without changing contracts; do not build a daemon initially. WF2 Step 2 calls a Rawgentic orchestration function that resolves a seat, invokes `phaseexec`, validates the result, and advances only on success.

Purposeful seats and eligibility floors are:
- Intake: `fable-5 -> opus-4-8 -> sonnet-5 -> sol`, floor median 83 and worst-cell 80. Fable is primary because its median is tied near the top while its 86 floor materially exceeds Opus's 81; every fallback still meets the declared floor.
- Design GPT author seat: `sol -> terra -> luna`, floor median 86 and worst 84. Design Claude author seat: `opus-4-8 -> fable-5`, floor median 86 and worst 81. These are two deliberate author lanes, not generic fallbacks; both run each round. If either lane is exhausted, the competitive round fails rather than becoming a one-author run.
- Plan: `opus-4-8 -> fable-5 -> sol`, floor median 81 and worst 80. Opus is primary despite its fourth-place median because its worst cell, 82, is best and planning defects propagate into every build task.
- Build: `sonnet-5 -> opus-4-8`, floor median 76 and worst 62. Sonnet is the default on statistically equivalent quality at 1/1.74 the burn proxy; Opus is the only measured fallback meeting the robustness floor. GPT models and Fable are excluded by their build floors.
- Review: canonical measured order `fable-5 -> sol -> sonnet-5 -> opus-4-8`, floor median 84 and worst 82, followed by an author-engine eligibility filter. Thus GPT-authored work uses Fable first, while Claude-authored work skips Claude entries and uses Sol. Before the Sol production lane is enabled, the ordinary interim order is `fable-5 -> sonnet-5 -> opus-4-8`; Claude-authored work must use an explicit provisional `glm-5.2 -> gemini` cross-engine review seat that is enabled only after passing the same review acceptance suite, otherwise it fails closed. Exhausting eligible fallbacks is a handled hard failure, never a downgrade below the floor.
- Ship: `sonnet-5 -> opus-4-8 -> fable-5`, floor median 84 and worst 82. Sonnet has the only measured real ship advantage; both fallbacks preserve the observed floor.

A design round runs Sol and Opus in isolated captures against byte-identical prompt/context snapshots, then sends anonymized, randomly ordered drafts to GLM-5.2. The rubric is the versioned bench-14 design rubric augmented only with executable requirements: constraint coverage, correctness, feasibility, explicit tradeoffs, and implementation/testability. Give each author the same deadline, run them in parallel across quota pools, and budget the judge separately—for example 2x author timeout plus a 90-second judge timeout, with an overall round deadline. The judge must return winner A/B, criterion scores, reasons, and confidence. Gemini is a declared judge fallback using the identical schema and rubric. If both judges fail, disagree under a configured dual-judge mode, return a tie, or produce low confidence, the round stops for human resolution; it never silently prefers the incumbent. The selected draft's exact bytes and hash become the design artifact consumed by planning. Telemetry stores anonymized winner, rubric scores, judge/model versions, latency, usage, later build/review/ship outcomes, and author identity revealed only after judging. Retirement requires a preregistered sample size and a meaningful loss or downstream-regression threshold, not raw win count alone.

The build complexity gate is a pure, versioned function evaluated after planning and again immediately before execution. Inputs are schema-validated facts: fail-closed `riskLevel`; normalized `issue.complexity`; count of declared target files and subsystems; estimated change-size bucket from structured plan fields; and matches against repository-owned glob categories for auth/identity, cryptography/secrets, payment/billing, permissions, migrations/data loss, dependency or lockfile changes, public API/schema changes, concurrency/distributed state, deployment/infra, and generated-code boundaries. High/critical risk, explicitly super-complex issues, any critical security/data-loss category, or a configurable score threshold triggers a bake-off. Missing or invalid mandatory metadata also triggers it. The policy returns a decision, reason codes, input snapshot, policy version, and digest. A flagged task runs Sonnet and Opus in isolated worktrees, executes the same deterministic test/static-analysis suite, and uses GLM-5.2 with deterministic test evidence plus anonymized patches to choose; failure to obtain two valid candidates fails the bake-off. Runtime diff size is recorded for calibration but cannot be the initial routing signal because it is unavailable before routing.

Routing enforcement is split across hooks. PreToolUse requires a valid run manifest, known phase/seat, routing-config digest, gate digest where applicable, allowed adapter, canonical requested model, fallback reason/evidence, author engine for review, and an adapter-built invocation with no user-supplied model override. PostToolUse validates the result schema and capture hashes; requires success exit and parse statuses; canonicalizes only explicitly declared aliases; asserts requested model equals provider-reported actual model for every attempt; checks reviewer engine differs from author engine; and checks that competitive winners and bake-off winners reference real candidate hashes. Any missing actual-model evidence, mismatch, undeclared fallback, below-floor model, cross-engine violation, malformed output, or incomplete attempt fails the run. A final audit reconciles expected calls against result records and refuses ship on missing, duplicate, mismatched, or unverified calls.

Benchmark the driver as an orchestrator, not as an answer author. Fixtures should provide a synthetic issue, repository/task metadata, controlled executor responses, injected quota/model/judge failures, and an expected state-transition graph. Score correct seat and fallback selection, gate calculation, context minimization, ordering and dependency handling, recovery behavior, cross-engine enforcement, propagation of the winning artifact, audit completeness, direct-authoring violations, wall time, tokens, and quota-pool utilization. Use deterministic stubbed executors for the main matrix and a small live validation subset. A reasonable pilot is 12 fixtures x 3 repetitions x 2 Claude driver models = 72 stubbed runs, plus 12–18 live runs; at an assumed $1–$3 of driver usage per run, budget roughly $75–$250 before live downstream model calls, while recording actual usage to replace the estimate.

Cut initially: a distributed scheduler, service/database deployment, automatic routing changes from telemetry, three-or-more-model build tournaments, continuous dual judging, semantic complexity classification, and a generic workflow DSL. A library, YAML policy, JSON Schema, run-directory/JSONL audit, isolated worktrees, and synchronous WF2 integration are sufficient for the first production slice.

## Key decisions

- The reusable executor performs invocation and observation only; phase routing, quality floors, complexity gating, and workflow composition remain project-owned policy.
- Provider-reported actual model identity is mandatory evidence on every attempt; absent identity is a failure, not an unknown-success state.
- Fallbacks are ordered, configuration-declared, floor-constrained, and terminate in a clear hard failure after eligible models are exhausted.
- Design always runs two independent author lanes and uses the judged winner's exact artifact in the real workflow.
- GLM-5.2 is the primary design/build judge because it shares neither candidate author's engine; Gemini is a schema-compatible fallback.
- Review routing uses measured rank order plus an author-engine filter, yielding Fable for GPT authors and Sol for Claude authors once the GPT lane is operational.
- Sonnet is the default implementer; only deterministic policy reason codes can trigger a Sonnet-versus-Opus bake-off.
- The gate runs both at plan finalization for visibility and at executor admission for enforcement, with matching policy/input digests required.
- Start synchronously with durable captures and stable IDs; add detached execution as a wrapper only when real workflow latency demonstrates the need.
- Telemetry may recommend a future policy change but cannot mutate production routing automatically.

## Risks

- The GLM-5.2 judge may reproduce the judge-dependent Sol design advantage; anonymization, downstream-outcome tracking, and periodic cross-judge calibration are necessary.
- The provisional GLM/Gemini review seat lacks the same measured review evidence as the named models; it must remain calibration-gated and visibly provisional.
- Structured planning metadata can be incomplete or conservatively inflated, causing excessive bake-offs; fail-closed behavior needs monitoring and threshold recalibration.
- Opus and Sonnet bake-off patches may be difficult to compare if tests are weak or changes are structurally different; isolated worktrees and evidence-first judging reduce but do not remove this risk.
- CLI output formats and model identifiers can change; adapters need fixture-based parser tests and explicit alias/version updates rather than permissive matching.
- Cross-engine filtering can exhaust a review chain during quota incidents, intentionally reducing availability to preserve independence.
- Concurrent author/build candidates can contend for repository resources or external test services; captures and test environments must be isolated.
- Cost and latency can rise sharply if competitive design or build bake-offs trigger too often; trigger rate and marginal downstream benefit should be reported together.
- A hook-only boundary can be bypassed by uninstrumented invocation paths; WF2 should accept only executor result envelopes and the final audit should reject orphan work.

## Sketch

phaseexec.run(request)
  validate RequestV1
  adapter = adapters.for_engine(request.engine)
  invocation = adapter.build(request)          # adapter owns model flag
  pre_hook.verify(request, invocation, policy_digest)
  observation = runner.execute(invocation)
  result = adapter.normalize(observation)
  post_hook.verify(request, result)
  audit.append(result)
  return ResultV1

rawgentic.run_phase(phase, artifact, metadata):
  decision = routing.resolve(phase, metadata, availability)
  manifest = manifest_for(decision, artifact)
  return phaseexec.run(manifest)

rawgentic.run_design_round(context):
  sol, opus = parallel(run(design_gpt), run(design_claude))
  verdict = judge(glm_5_2, fallback=gemini,
                  drafts=anonymize_and_shuffle(sol, opus), rubric=RUBRIC_V14)
  require decisive(verdict)
  winner = immutable_candidate(verdict)
  reviewer = routing.resolve('review', author_engine=winner.engine)
  return DesignArtifact(content=winner.content, hash=winner.hash,
                        verdict=verdict, reviewer=reviewer)

rawgentic.run_build(task):
  gate = complexity_policy.evaluate(task, repo_glob_policy)
  require gate.digest == executor_recomputed_digest
  if gate.mode == 'default':
      return run(build_sonnet)
  candidates = isolated_parallel(run(sonnet), run(opus))
  evidence = run_same_test_suite(candidates)
  return judge_and_select(glm_5_2, candidates, evidence)

ResultV1 = {
  schema_version, run_id, attempt_id, phase, seat,
  engine, requested_model, actual_model,
  request_hash, prompt_hash, context_hashes,
  started_at, duration_ms, timeout_ms,
  usage: {input_tokens, output_tokens, cached_tokens, provider_cost},
  process: {exit_code, signal, timed_out},
  parse: {status, provider_response_id},
  output, capture_refs, fallback_reason,
  routing_config_digest, policy_decision_digest
}

---
_Peer proposal (report-only). Synthesize at your discretion._
