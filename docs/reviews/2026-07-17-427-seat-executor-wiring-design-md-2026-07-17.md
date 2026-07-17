# Adversarial Review — 2026-07-17-427-seat-executor-wiring-design.md

- Date: 2026-07-17
- Artifact type: design
- Reviewer: Codex (model config-default, reasoning effort high)
- Findings: 6 (Critical 1, High 3, Medium 2, Low 0)

## Summary

The artifact proposes executor-routing CLI glue for ship, intake, and plan seats, but defers the workflow call sites that would invoke it. Its main risks are a guaranteed dormant integration, unsafe reliance on checkout-local ignore configuration, silent cutover rollback on malformed configuration, and unproven runtime-provider feasibility.

## Findings

### 1. [Critical] completeness · high confidence — Problem / Out of scope

> Out of scope (later children): the WF2/WF3 *prose* that calls this CLI (#417); run-end
> `reconcile_run` over a whole WF run + telemetry rollups (#420); competitive rounds / bake-off (#428);
> complexity gate (#429).

The workflow call sites that would invoke the new CLI are explicitly deferred. Therefore implementing #427 as written cannot make any WF2/WF3 seat route through the executor, contradicting the stated goal of shipping the first effective consumer and establishing a verified choke point.

**Recommendation:** Move the exact WF2/WF3 `resolve-seat` and conditional `dispatch` call sites from #417 into #427, opt in at least one seat, and add an integration test proving that the workflow reaches `run_seat`; otherwise rename and rescope #427 as a dormant adapter and remove the claims that it routes seats or ships the first workflow consumer.

### 2. [High] correctness · high confidence — executorRouting schema / Error handling and failure modes

> An absent block, an absent seat, or a malformed block →
> `inherit` (fail-safe, stderr warning — routing that can't evaluate must not brick the workflow; repo
> §3 fail-open-for-routing decision guide). An unknown seat/mode/version inside an otherwise-valid block
> → that seat drops to `inherit` + a loud warning.

A malformed configuration that was intended to opt a seat into the executor returns a successful legacy route. Because the only fault surface is stderr, an orchestrator that does not capture and assert that diagnostic can complete successfully while bypassing the intended executor choke point, creating a false cutover state.

**Recommendation:** In the `executorRouting` schema and CLI contract, distinguish absence from invalidity: keep an absent block or seat as `inherit`, but make any present malformed block, unsupported version, unknown seat, or invalid mode return the structured exit-2 error and prevent workflow execution.

### 3. [High] correctness · high confidence — Path derivation

> - quota permits: `<workspace>/.rawgentic/runtime/phase-executor/permits/` (workspace-wide, so the
>   inter-process `claude:2` ceiling actually holds ACROSS concurrent runs, not just within one)
> 
> `run_id` and `project` are validated as path-safe components (reject `/`, `..`, control chars, empty)
> before any path is built — deriving from a validated id is safer than accepting an arbitrary
> `--capture-root` from stdin. Both dirs live under the git-ignored `.rawgentic/`. Named limit (peer):
> the workspace-wide permit dir assumes compatible pool defs across projects; the run records the
> snapshot digest so #420 reconciliation can flag a cross-project pool conflict rather than silently
> partitioning permits and weakening the ceiling.

The design claims the workspace-wide directory enforces a real cross-run ceiling while admitting that each project may supply incompatible pool definitions. Detecting that conflict during later run-end reconciliation cannot prevent already-executed calls from exceeding the intended provider ceiling, so the quota guarantee fails before the conflict is surfaced.

**Recommendation:** Add a workspace-authoritative pool-definition record in #427. Before acquiring any permit, atomically create or compare its snapshot digest and reject dispatch with a surfaced non-retryable error when a project disagrees; do not defer this compatibility check to #420 reconciliation.

### 4. [High] security · high confidence — Confirmed facts / File changes / Security implications

> **`.rawgentic/` is git-ignored** (`.git/info/exclude:7`) — derived capture/permit dirs under it are
>   never committed.

`.git/info/exclude` is checkout-local rather than repository-distributed configuration, so this evidence does not establish that `.rawgentic/` is ignored in other clones or CI worktrees. Captured prompts, observations, audit records, or permits can consequently appear as untracked files and be committed accidentally.

**Recommendation:** Add `/.rawgentic/` to the repository `.gitignore`, include that file in #427's changes, and add a test or release check using `git check-ignore` against a representative capture path.

### 5. [Medium] feasibility · high confidence — Platform / external dependencies; Test strategy — @live

> - api: subprocess claude --print --model <m> --output-format json (and codex/zhipu adapters) via phase_executor.adapters
>   feasibility: verified via existing-call-site — phase_executor.adapters.claude_cli, fixture-tested against captured provider output; bench cell-runner lineage (400+ cells)
>   failure: fail-loud
>   surface: non-zero process exit / non-ok Observation.parse_status → dispatch exits 3/4; the @live test (RUN_LIVE=1) asserts reported actual_model == the seat primary

An adapter implementation, captured-output fixtures, and lineage do not prove that the exact Claude, Codex, and Zhipu subprocesses are executable with the required model flags, credentials, feature permissions, and sandbox policy in this project's real hook/CI environment. The only proposed live check is optional and covers one Claude primary, leaving Codex, Zhipu, and fallback execution unverifiable from the provided text; opted-in routes can therefore fail only after deployment.

**Recommendation:** Expand Platform / external dependencies with citations to the applicable capability or manifest configuration and completed spikes from the same hook/CI execution environment for each provider adapter and exact model form. Make a zero-egress executable/credential preflight mandatory before opt-in, and keep any provider without such evidence out of eligible seat chains.
**Ambiguity:** The artifact provides no project capability or manifest evidence establishing which provider subprocesses are permitted in the target runtime.

### 6. [Medium] internal-consistency · high confidence — Error handling and failure modes

> - **Identity breach / pre-check denial:** exit 4 (non-retryable); the receipt+obs are already audited
>   (the breach is the record most needed).

A pre-check denial occurs before the real provider call, so no provider Observation exists to be audited. Combining it with a post-dispatch identity breach under the assertion that both receipt and observation already exist leaves the required audit record shape and append ordering impossible to implement consistently.

**Recommendation:** Split this Error handling entry into two contracts: pre-check denial appends the denial receipt only and performs no provider call; identity breach appends the successful pre-receipt, returned Observation, and failed post-check before returning exit 4. Add separate tests for both audit sequences.

---
_Report-only: this review does not edit the artifact. Findings are advisory; incorporate them at your discretion._