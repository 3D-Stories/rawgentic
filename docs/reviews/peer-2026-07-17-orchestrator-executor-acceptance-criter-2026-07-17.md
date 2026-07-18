# Peer Consult — 2026-07-17-orchestrator-executor-acceptance-criteria.md

- Date: 2026-07-17
- Reviewer: Codex (peer designer)

## Approach

Build phase_executor as the sole process-control and policy-enforcement boundary for every WF2/WF3 model call. The interactive session remains the orchestrator: it creates a versioned run plan, evaluates workflow gates, launches executor jobs, consumes Observation envelopes, and interacts with the owner. The executor resolves routing, reserves quota and budget, creates an isolated worktree, launches a headless provider agent as the initial process of a named tmux session, verifies its result, and finalizes audit artifacts. Implement the new path behind a run-level architecture version, prove all seats in shadow/integration runs, then atomically cut WF2 and WF3 over; never mix dispatch architectures within one run.

## Key decisions

- Use a run-level hard cutover. Existing runs may resume under their recorded architecture version, but every newly cut-over WF2/WF3 run uses phase_executor exclusively; Agent-tool phase dispatch is not a runtime fallback.
- Treat tmux supervision as an internal executor facility, not a third component or transport. Workers are headless processes; completion is determined by process exit plus an atomically finalized Observation sentinel.
- Make the executor API asynchronous at its core: launch(job) -> handle, status(handle), await(handle), cancel(handle), and recover(run_id). Preserve a synchronous run_seat wrapper for sequential workflow, bake-off, and bench callers.
- Have the engine own git worktree lifecycle for every mutating provider. This gives Claude and Codex identical isolation, deterministic paths, explicit cleanup, and reliable resume from the original cwd. Retain failed or dirty worktrees for diagnosis instead of deleting them automatically.
- Define a versioned seat capability manifest containing provider/model/effort, allowed and denied tools, permission mode, mutation class, worktree policy, timeout, budget, quota pool, and output requirements. Reject a dispatch before launch if its manifest is incomplete or inconsistent.
- Use least-privilege explicit tool grants plus non-interactive denial behavior. Pin guardrail-bearing non-bare execution, explicitly deny dangerous tools where supported, and require a startup canary to prove hooks and security controls loaded before allowing mutation.
- Give cross-model agents equivalent rights according to seat capability, not provider identity. A Codex build or design job may receive workspace-write only inside its engine-managed worktree and under the same gates, budget, and audit contract as Claude.
- Extend Observation with a typed work_product object rather than overloading parsed_payload. Include worktree, base/head commit, changed paths, generated documents, test executions, and integration status; preserve provider-native output separately.
- Make executor output descriptive, not self-integrating. Mutating workers produce changes in isolated worktrees; the orchestrator validates required gates and explicitly promotes the selected commit or patch into the canonical workflow branch.
- Create an immutable expected-dispatch manifest at run start. Reconciliation compares expected jobs, launch records, finalized Observations, routing audit entries, and promoted work products; missing, duplicate, unaudited, or unexpected calls fail the run.
- Classify subscription exhaustion as a quota-pool pause. Stop launching jobs in that pool, preserve live/recoverable state, and expose a resumable run condition rather than consuming fallback attempts as model failures.
- Use run-prefixed tmux names and a durable job registry. Recovery adopts a session only when its identity, command digest, worktree, and capture directory match; otherwise it quarantines the session for explicit cancellation.
- Route all WF2/WF3 model-based utility fan-out through executor jobs as well as named phase seats. Pure deterministic orchestration logic may remain in-process; no second model-dispatch mechanism remains.
- Keep the Claude adapter on the CLI initially for provider symmetry and transparent tmux supervision. Hide invocation behind an adapter interface so a later SDK implementation can be introduced without changing workflow or audit contracts.
- Point live bench cells at the public executor API and the same manifests used by workflows. Bench mode may supply fixtures and scoring, but cannot substitute adapters, bypass gates, or use a separate routing path.

## Risks

- Atomic cutover increases release coordination and requires a complete end-to-end proving environment before activation.
- Explicit tool whitelists can abort legitimate work when manifests omit a needed capability; capability-contract tests and clear denial telemetry are essential.
- Hook behavior or CLI defaults may drift, especially around bare execution. A per-launch guardrail canary must fail closed rather than relying only on pinned versions.
- Worktree promotion can conflict with concurrent canonical-branch changes or conceal uncommitted artifacts. Promotion needs base-SHA validation, a clean-worktree requirement, and an explicit conflict result.
- tmux session survival does not guarantee worker health. The supervisor must distinguish running, exited-without-sentinel, quota-paused, timed-out, and orphaned states.
- Credentials or environment files copied into worktrees can broaden exposure. Worktree population needs an allowlist and secret-handling policy.
- Provider effort vocabularies are not necessarily equivalent. Adapter-specific mappings must be validated and the requested native value recorded alongside the normalized policy value.
- A structured work-product report can be inaccurate if supplied only by the agent. The executor should independently derive git changes, commit identity, and command exit evidence.
- Parallel jobs can exceed shared subscription limits even when individual budgets are bounded. Launch admission must combine concurrency, quota-pool health, and aggregate run budget.
- Retaining failed worktrees and tmux logs aids recovery but creates disk, secret-retention, and cleanup risks; retention limits and redaction are required.
- Architecture-versioned resume can prolong support for the legacy path. Set a bounded migration window and refuse legacy resumes after an explicit archival procedure.
- Full cross-model mutation increases supply-chain and command-execution exposure; identical rights must mean identical containment and gates, not merely equivalent sandbox labels.

## Sketch

Run start: Orchestrator writes RunPlan{architecture_version, workflow_revision, expected_jobs, budget}.

For each model job:
Orchestrator -> Executor.launch(JobSpec{run_id, job_id, seat, prompt_ref, context_refs, dependencies})
Executor -> validate capability manifest -> reserve quota/budget -> resolve requested model+effort -> create isolated worktree when required -> create capture directory and registry entry -> start `tmux new-session -d -s <run-seat-attempt> <headless-wrapper>`.
Headless wrapper -> verify non-bare guardrails -> invoke provider adapter -> perform agent work -> capture provider identity/usage/output -> independently inventory git changes/tests -> atomically finalize Observation vNext.
Executor.await -> classify exit/sentinel state -> verify requested==actual -> append routing audit -> release quota -> return ResultEnvelope.
Orchestrator -> evaluate phase gates -> optionally request further executor jobs -> promote an approved work product -> record DISPATCH summary.
Run end -> reconcile RunPlan against registry + Observations + routing audit + promotions; any mismatch blocks completion.

Core interfaces:
launch(job) -> JobHandle
status(handle) -> JobState
await(handle, timeout) -> ResultEnvelope
cancel(handle, reason) -> Observation
recover(run_id) -> RecoveryReport
run_seat(job) -> ResultEnvelope  // compatibility wrapper

Observation work_product:
{kind, worktree_path, base_sha, head_sha, changed_paths[], documents[], tests[{command_digest,status,exit_code,report_ref}], promotion:{status,target_sha}}

Delivery order: schemas and capability manifests -> full-seat enforcement/audit -> agentic adapters -> supervisor and registry -> worktree lifecycle/promotion -> guardrail canaries -> workflow integration -> recovery/limit tests -> real WF2 proving run -> real WF3 proving run -> live bench on the identical executor path -> atomic activation.

---
_Peer proposal (report-only). Synthesize at your discretion._
