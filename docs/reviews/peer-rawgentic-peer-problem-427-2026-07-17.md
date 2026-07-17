# Peer Consult — .rawgentic-peer-problem-427.md

- Date: 2026-07-17
- Reviewer: Codex (peer designer)

## Approach

Add rawgentic-specific glue in `hooks/executor_routing_lib.py` with a thin `hooks/executor_routing.py` CLI. The glue resolves the per-project seat mode, applies an immutable workflow-operation policy, derives run directories, constructs the routing snapshot/quota/audit dependencies, and invokes `run_seat`. Keep `phase_executor/` unchanged and unaware of rawgentic configuration.

Use two CLI subcommands: `route` previews the action; `invoke` atomically resolves the action and, when appropriate, executes it. Both consume one JSON object on stdin and emit exactly one JSON object on stdout; diagnostics go to stderr. `invoke` returns one of three tagged results: `inherit` tells the orchestrator to continue through the existing prose/Agent-tool path unchanged; `driver_inline` identifies an explicitly excluded ship operation; `executor` contains the serialized `Observation`, including provider-reported `actual_model`.

Executor dispatch includes enforcement. Wrap the injected dispatch callable so every attempted target receives `check_pre` immediately before provider dispatch and its receipt is appended to `RoutingAuditLog`. After `run_seat` succeeds, call `verify_post` and append the observation and post-check. A pre-check denial, requested/actual mismatch, audit-write failure, or exhausted chain terminates the command without returning `inherit` or `driver_inline`. Run-level `reconcile_run` remains in #420 because it requires lifecycle ownership and the complete expected-seat inventory; #427 supplies the records and initial snapshot digest it will consume.

## Key decisions

- Place configuration at `.rawgentic_workspace.json` under each project as `executorRouting: {"version":1,"seats":{"intake":"inherit|executor","plan":"inherit|executor","ship":"inherit|executor"}}`. An absent block or absent seat means `inherit`. Reject unknown versions, seats, and modes. Do not add a global switch: per-seat configuration directly supports one-seat-at-a-time cutover.
- Use a new `executorRouting` block rather than extending `modelRouting`. The existing block selects legacy role models; executor routing selects a different execution mechanism with enforcement, fallback chains, quota, and audit semantics.
- `route` input is `{workspace_root, project, seat, operation}`. Its successful result is `{ok:true, action:"inherit|driver_inline|executor", seat, operation}`. It never resolves or restates a legacy model: `inherit` means execute the pre-existing branch, including its current `model_routing_lib.resolve` call.
- `invoke` accepts the routing fields plus `{prompt, run_id, correlation_id?, author_provider?, context?, effort?, timeout?}`. For `inherit` and `driver_inline`, it returns the tagged decision without dispatching. For `executor`, it returns `{ok:true, action:"executor", observation:{...}}`; the observation is the authoritative routed output and `actual_model` must be present when parsing succeeded.
- Use exit 0 for every valid tagged result, 2 for malformed input/config or invalid seat-operation combinations, 3 for quota/timeout/availability exhaustion, 4 for enforcement or requested-versus-actual breaches, and 5 for audit/capture/internal failures. Every nonzero exit still emits structured `{ok:false,error:{code,message,retryable}}`. Executor failures never convert to inline or inherit behavior.
- Encode workflow policy as constants in the glue, not editable configuration. Intake accepts only its purposeful stage operation; plan likewise. Ship accepts self-contained artifact operations for README/changelog, the three coordinated version updates, and docs. `merge`, `ci_triage`, `deploy_verify`, and `wf2_step16` always return `driver_inline` for ship. Unknown operations fail closed.
- When intake or plan is set to `executor`, every accepted invocation must either return an executor observation or fail. `inherit` is a deliberate rollout state evaluated before execution, not a runtime fallback. Ship follows the same rule for routable artifacts, while its explicit exclusion set remains driver-only.
- Apply `check_pre` inside a dispatch decorator because `run_seat` owns chain selection and may attempt multiple targets. This checks and records each actual attempt, including fallback attempts, before the underlying dispatch stub or subprocess is called. Call `verify_post` once on the final observation and treat mismatch as non-retryable.
- Derive paths rather than accepting arbitrary capture paths from stdin: capture root is `<workspace>/.rawgentic/runs/<run_id>/phase-executor/<project>/`; quota permits are shared across projects at `<workspace>/.rawgentic/runtime/phase-executor/permits/`. Validate project and run identifiers as path-safe components. The stable workspace-wide permit directory prevents concurrent runs from independently exceeding pool limits.
- Add unit tests for config defaulting/validation, operation policy, derived paths, no-inline guarantees, fallback exhaustion, enforcement ordering, audit records, and result/exit-code mapping. Add subprocess contract tests for both subcommands. Make `main(..., dispatch=_dispatch_real)` injectable; a test-only subprocess harness imports it with a deterministic dispatch stub.
- Add a two-path integration test around a small orchestrator harness. With the seat set to `executor`, the dispatch stub records the attempted target and returns a provider-style observation; assert the routed target and `Observation.actual_model`, including a chain-fallback case. With the seat absent or `inherit`, assert executor dispatch was never called, then run the unchanged legacy resolver/Agent stub and assert its invoked model, effort, and reported actual model match the pre-#427 behavior.
- Add an opt-in `@live` test for one enabled seat. It verifies real CLI discovery, credentials, subprocess invocation, provider response parsing, mandatory `actual_model`, enforcement, and durable audit output. It should not be the primary routing assertion and should avoid deliberately consuming an entire fallback chain.

## Risks

- The later prose-wiring change could treat `inherit` as generic inline execution and accidentally bypass the existing resolver. The tagged contract and integration harness should make the required continuation explicit.
- Operation names may drift from WF2/WF3 terminology before #417 wires the prose. Keep them as a small versioned enum with centralized mappings and fail on unknown values.
- Audit failure after provider execution creates an ambiguous externally executed but locally unrecorded state. Return a distinct non-retryable error carrying the correlation ID; never retry automatically because that could duplicate work.
- A workspace-wide permit directory assumes all projects use compatible pool definitions. Record the routing snapshot digest with each run and have #420 reconciliation flag conflicting pool configurations rather than partitioning permits and weakening concurrency limits.
- Tests can produce false confidence if they assert only the requested model. Stubs must independently record the dispatched target and populate provider-reported `actual_model`; assertions should compare both and exercise mismatch rejection.
- Running the hook file directly could shadow the `phase_executor` package if given the same module name. The proposed `executor_routing.py` name avoids that import collision.

## Sketch

Files:
- `hooks/executor_routing_lib.py`: config resolution, seat/operation policy, path derivation, dispatch enforcement wrapper, injectable invocation core.
- `hooks/executor_routing.py`: stdin/config I/O, dependency construction, JSON output, exit mapping.
- `tests/test_executor_routing_lib.py`: pure and injected-dispatch tests.
- `tests/test_executor_routing_cli.py`: subprocess JSON contract and two-path orchestration tests.
- configuration-schema documentation/example: document `executorRouting` and default-inherit behavior.

Example configuration:
`{"projects":{"rawgentic":{"executorRouting":{"version":1,"seats":{"intake":"executor","plan":"inherit","ship":"executor"}}}}}`

Production flow:
`invoke request -> load/validate project config -> classify seat+operation -> inherit | driver_inline | construct snapshot/quota/audit -> run_seat(wrapped_dispatch) -> verify_post -> append audit -> executor observation`

The wrapper's per-attempt flow is:
`target selected by run_seat -> check_pre -> append pre receipt -> dispatch -> engine fallback or final observation`.

A final successful record contains the run/correlation identifiers, seat, dispatched lane, requested model, provider-reported actual model, snapshot digest, and post-check result. #420 later supplies the expected-seat set and calls `reconcile_run` at workflow completion.

---
_Peer proposal (report-only). Synthesize at your discretion._
