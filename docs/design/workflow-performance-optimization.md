# Phase 3 Workflow: Performance Optimization (v1.0)

**Date:** 2026-03-02
**Author:** Orchestrator (direct authoring)
**Inputs:** phase2-principles.md, phase2b-official-comparison.md
**Purpose:** Define the workflow for identifying and resolving performance bottlenecks. WF10 is measurement-driven: every optimization must be justified by profiling data and validated by benchmark improvement.

---

## Workflow: Performance Optimization

**Invocation:** `/optimize-perf <scope>` skill (custom Claude Code skill)
**Trigger:** User invokes `/optimize-perf "API response times"` or `/optimize-perf <issue-number>` or `/optimize-perf db-queries`
**Inputs:**

- Performance scope: component name, symptom description, or GitHub issue
- Existing performance baselines (if any)
- Runtime environment access (for profiling and benchmarking)

**Outputs:**

- Performance analysis report (bottlenecks identified, baseline measurements)
- Merged PR with optimizations (code changes + benchmark evidence)
- Updated documentation with performance characteristics
- Deployed and verified improvements in dev environment

**Tracking:** GitHub issue (if exists) or session notes.

**Principles enforced:**

- P1: Branch Isolation (perf/ branch)
- P2: Code Formatting
- P3: Frequent Local Commits
- P4: Regular Remote Sync (push during implementation)
- P5: TDD Enforcement (benchmark tests proving improvement)
- P6: Main-to-Dev Sync
- P7: Triple-Gate Testing
- P8: Shift-Left Critique (full critique on optimization approach)
- P9: Continuous Memorization (performance patterns)
- P11: User-in-the-Loop (user approves optimization targets)
- P12: Conventional Commit Discipline
- P13: Pre-PR Code Review
- P14: Documentation-Gated PRs

**Diagram:** `diagrams/workflow-performance-optimization.excalidraw`

**Termination:** WF10 terminates after deployment verification with benchmark comparison. No auto-transition.

---

## Core Principle: Measure, Don't Guess

WF10 enforces a strict measurement-driven approach:

1. **Never optimize without a baseline measurement** -- you can't prove improvement without knowing the starting point
2. **Never assume a bottleneck** -- profile first, then optimize what the data shows
3. **Always measure after optimization** -- confirm the change actually helped
4. **Watch for regression in other areas** -- optimizing one thing can slow down another

---

## Optimization Categories

| Category     | Scope                  | Profile Tool              | Example                  |
| ------------ | ---------------------- | ------------------------- | ------------------------ |
| **Query**    | PostgreSQL queries     | `EXPLAIN ANALYZE`         | Slow analytics endpoint  |
| **API**      | HTTP endpoint latency  | Response time measurement | Settings page load time  |
| **Compute**  | Python/JS CPU-bound    | cProfile / Node profiler  | ML inference time        |
| **Memory**   | RAM consumption        | Process memory tracking   | Engine memory leak       |
| **Network**  | Cross-VM communication | Latency measurement       | Engine-to-DB round trips |
| **Frontend** | Page load/render       | Lighthouse / Web Vitals   | Dashboard initial load   |

---

## Benchmark Strategy

Every optimization requires before/after benchmarks:

1. **Baseline benchmark:** Run before any code changes, document exact conditions
2. **Target:** Define acceptable improvement threshold (e.g., "50% reduction in p95 latency")
3. **Post-optimization benchmark:** Run after changes, same conditions as baseline
4. **Regression check:** Verify no degradation in unrelated areas

Benchmarks must be **reproducible** -- document the exact commands, data set, and environment used.

---

## Finding Application and Ambiguity Circuit Breaker

**Inherited from WF2:** Auto-apply findings. Circuit breaker on ambiguity.

---

## Workflow Resumption

**Checkpoint artifacts:**

| Artifact       | Location                       | Created at   | Purpose        |
| -------------- | ------------------------------ | ------------ | -------------- |
| Baseline data  | Session notes                  | Step 3       | Before metrics |
| Profile report | Session notes                  | Step 3       | Bottleneck map |
| Perf branch    | Git (remote)                   | Step 6       | Code state     |
| Benchmark data | Session notes / committed file | Steps 3, 9   | Before/after   |
| PR             | GitHub (remote)                | Step 10      | Review state   |
| Session notes  | `claude_docs/session_notes.md` | Continuously | Progress log   |

**Step detection on resume:**

1. PR merged? -> Step 14
2. PR exists and CI passed? -> Step 13
3. PR exists? -> Step 12
4. Perf branch has optimized code with benchmarks? -> Step 10
5. Perf branch has code changes? -> Step 9
6. Perf branch exists (empty)? -> Step 8
7. Optimization design in session notes? -> Step 5
8. Baseline/profile data in session notes? -> Step 4
9. None -> Step 1

---

## Steps

### Step 1: Receive Performance Scope

**Type:** user decision
**Actor:** human
**Command:** `/optimize-perf <scope>`
**Input:** Performance scope (symptom, component, issue number)
**Action:**

1. Parse scope: identify which subsystem is affected
2. If issue: fetch via `gh issue view`
3. Clarify performance expectations:
   - What is "slow"? (current latency, expected latency)
   - What is the acceptable target? (specific numbers, not "faster")
   - Is this user-facing or internal?
4. Confirm scope with user

**Output:** Validated scope: { description, subsystem, current_metric (if known), target_metric, issue_number (optional) }
**Principle alignment:** P11

---

### Step 2: Analyze Performance Context

**Type:** automated
**Actor:** Claude (Serena MCP + codebase analysis)
**Input:** Scope from Step 1
**Action:**

1. **Code path mapping:** Using Serena, trace the code path from entry point to response (for API/query) or full execution cycle (for compute/batch)
2. **Architecture review:** Identify caching layers, database queries, network calls, compute-intensive operations along the path
3. **Existing optimization inventory:** Check for existing caching, indexing, pagination, connection pooling
4. **Environment context:** Document the target environment (Docker resource limits, VM specs, PostgreSQL config)
5. **Historical context:** Check session notes and MEMORY.md for prior performance work

**Output:** Context analysis: { code_path, architecture_layers, existing_optimizations, environment, history }

---

### Step 3: Profile and Establish Baseline

**Type:** automated
**Actor:** Claude (via runtime environment)
**Input:** Context from Step 2
**Action:**

1. **Establish baseline metrics:**
   - For queries: `EXPLAIN ANALYZE` on the specific queries
   - For API endpoints: measure response time with `curl -w` or similar
   - For compute: time the specific operation
   - For frontend: capture current page load metrics
2. **Profile the hot path:**
   - For Python: use `cProfile` or `time.perf_counter()` instrumentation
   - For Node.js: use `--prof` or timing instrumentation
   - For PostgreSQL: `EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT)` with real data
3. **Identify bottleneck(s):** From profiling data, rank operations by time/resource consumption
4. **Document baseline:** Record exact measurements, conditions, data volumes

**Output:** Profile report: { baseline_metrics, bottleneck_ranking, profiling_data, test_conditions }
**Failure mode:** (1) Cannot reproduce slowness -> check data volume, concurrent load. (2) Profiling overhead skews results -> use lightweight instrumentation. (3) Bottleneck is external (network, third-party API) -> document and present to user.
**Principle alignment:** P8 (Shift-Left -- profile before optimizing)

---

### Step 4: Design Optimization Approach

**Type:** automated
**Actor:** Claude
**Command:** `/superpowers:brainstorming`
**Input:** Profile report from Step 3, bottleneck ranking
**Action:**

For each identified bottleneck:

1. **Generate optimization options:**
   - Query: indexing, query rewrite, materialized views, caching
   - API: caching (Redis/in-memory), pagination, response trimming, connection pooling
   - Compute: algorithm improvement, batch processing, async processing, caching results
   - Memory: reduce allocations, fix leaks, use streaming/generators
   - Network: reduce round trips, batch requests, connection pooling
   - Frontend: code splitting, lazy loading, image optimization, CDN
2. **Evaluate trade-offs:** Each option assessed for: implementation effort, expected improvement, maintenance cost, risk
3. **Recommend approach:** Select optimization(s) with best effort-to-improvement ratio
4. **Define success criteria:** Specific metric targets (e.g., "p95 response time < 200ms")

**Output:** Optimization design: { optimizations[], success_criteria, expected_improvement, approach_rationale }
**Failure mode:** (1) No clear optimization path -> the current performance may be near-optimal for the architecture. Present to user. (2) Optimization requires architectural change -> suggest WF2 for redesign.

---

### Step 5: Quality Gate -- Optimization Critique

**Type:** quality gate
**Actor:** sub-agent
**Command:** `/reflexion:critique`
**Input:** Optimization design from Step 4, profile data from Step 3
**Action:**

Three judges evaluate:

- **Effectiveness judge:** Will the proposed optimizations actually address the measured bottlenecks? Are the expected improvements realistic?
- **Safety judge:** Could the optimizations introduce bugs, data inconsistencies, or new failure modes? (e.g., caching stale data, race conditions in async processing)
- **Simplicity judge:** Is this the simplest optimization that achieves the target? Are we over-engineering? (Principle: premature optimization is the root of all evil)

**Output:** Validated optimization design OR loop back to Step 4 (max 2 iterations)
**Principle alignment:** P8 (full critique -- optimization trade-offs are subtle and easy to get wrong)

---

### Step 6: Create Performance Branch

**Type:** automated
**Actor:** Claude
**Command:** `git checkout -b perf/<desc>`
**Input:** Branch name from plan
**Action:**

1. `git fetch origin main`
2. `git checkout -b perf/<desc> origin/main`

**Output:** Active performance branch
**Principle alignment:** P1

---

### Step 7: Create Performance Plan

**Type:** automated
**Actor:** Claude
**Command:** `/superpowers:writing-plans`
**Input:** Validated optimization design from Step 5
**Action:**

1. Order tasks:
   - Task 1: Add benchmark test(s) that capture current performance
   - Task 2-N: Implement each optimization
   - Task N+1: Run benchmarks and compare
   - Task N+2: Update documentation with performance characteristics
2. Each task has: file path, action, expected metric improvement

**Output:** Ordered implementation plan
**Principle alignment:** P5 (benchmark tests first)

---

### Step 8: Implement Optimizations (Benchmark-Driven)

**Type:** automated
**Actor:** Claude (via `/superpowers:executing-plans`)
**Input:** Plan from Step 7
**Action:**

1. **Write benchmark tests:** Tests that measure the specific metrics being optimized. These serve as both verification and regression detection.
2. **Implement each optimization:**
   - Apply the optimization
   - Run benchmark test -- verify improvement
   - Run full test suite -- verify no regressions
   - Commit: `perf(scope): <optimization description>`
3. **For database optimizations:**
   - Add indexes via migration script (if needed)
   - Verify with `EXPLAIN ANALYZE` before and after
   - Check that index doesn't slow down writes significantly
4. **For caching:**
   - Implement cache invalidation strategy
   - Test cache hit/miss paths
   - Test stale cache handling

**Output:** Optimized code with benchmark evidence on branch
**Failure mode:** (1) Optimization doesn't improve metrics -> revert and try next approach. (2) Optimization causes test failures -> investigate side effects. (3) Improvement is marginal -> discuss with user whether to proceed or revert.
**Principle alignment:** P5 (TDD via benchmarks), P3 (Frequent Commits)

---

### Step 9: Post-Optimization Benchmark

**Type:** quality gate
**Actor:** Claude
**Input:** Optimized code, baseline metrics from Step 3
**Action:**

1. Run the EXACT same benchmarks as Step 3 (same conditions, same data)
2. Compare before/after metrics
3. Calculate improvement percentages
4. Verify success criteria are met
5. Check for regressions in related areas
6. Document results with evidence

**Output:** Benchmark comparison: { baseline_metrics, optimized_metrics, improvement_pct, regressions (if any) }
**Failure mode:** (1) No improvement -> revert optimizations, investigate further. (2) Improvement below target -> discuss with user. (3) Regressions detected -> investigate and fix.
**Principle alignment:** P8 (measurement-driven verification)

---

### Step 10: Code Review + Memorize

**Type:** automated
**Actor:** sub-agent (4-agent review) + `/reflexion:memorize`
**Command:** `/code-review:code-review` + `/reflexion:memorize`
**Input:** All changes on perf branch, benchmark results
**Action:**

1. **Code review:** Focus on: (a) optimization correctness, (b) cache invalidation safety, (c) no new failure modes, (d) code clarity
2. **Memorize:** Performance optimizations almost always reveal patterns: query patterns, caching strategies, architecture bottlenecks. Curate into CLAUDE.md.

**Output:** Review-clean code + CLAUDE.md updates
**Principle alignment:** P13, P9

---

### Step 11: Create Pull Request

**Type:** automated
**Actor:** Claude
**Command:** `/commit-commands:commit-push-pr`
**Input:** Reviewed code, benchmark results
**Action:**

1. Push: `git push -u origin perf/<desc>`
2. Create PR:
   - Title: `perf(scope): <optimization summary>`
   - Body: Problem statement, profile data, optimization approach, benchmark results (before/after), test plan
   - Labels: performance, size/S|M|L

**Output:** PR URL
**Principle alignment:** P12, P14

---

### Step 12: CI Verification (Gate 2)

**Type:** quality gate
**Actor:** GitHub Actions
**Input:** PR from Step 11
**Action:** Standard CI verification. Performance changes should pass all tests. Max 2 fix-and-retry cycles.

**Output:** CI pass
**Principle alignment:** P7 (Gate 2)

---

### Step 13: Merge and Deploy (Gate 3)

**Type:** automated
**Actor:** Claude
**Command:** `gh pr merge --squash` + deploy script
**Input:** CI-passing PR
**Action:**

1. Squash-merge
2. Deploy to dev
3. Post-deploy: re-run benchmarks against deployed environment to confirm improvement holds in production-like conditions

**Output:** Merged, deployed, and verified
**Principle alignment:** P6, P7 (Gate 3)

---

### Step 14: Post-Deploy Performance Verification

**Type:** quality gate
**Actor:** Claude
**Command:** Benchmark against deployed environment
**Input:** Deployed code, baseline metrics
**Action:**

1. Run benchmarks against deployed dev environment
2. Compare with pre-optimization baseline
3. Verify no performance regressions in other areas
4. Health check all services
5. Document final results

**Output:** Deployment performance verification
**Failure mode:** (1) Performance worse in deployed env -> investigate: different data volume, different resource allocation, cold cache effects.

---

### Step 15: Completion Summary

**Type:** automated
**Actor:** Claude
**Input:** All artifacts
**Action:**

1. Update session notes with: bottleneck identified, optimization applied, benchmark results (before/after)
2. Close GitHub issue (if exists) with benchmark evidence
3. Present summary: what was slow, what was done, how much faster, PR link

**Output:** Session notes updated, issue closed
**Principle alignment:** P14

---

## Design Decisions

### D1: 15 Steps (Profile-Heavy)

**Rationale:** Performance optimization requires more measurement steps than other workflows: baseline profiling (Step 3), post-optimization benchmark (Step 9), and post-deploy verification (Step 14). These extra measurement steps are non-negotiable -- without them, "optimization" is guesswork.

### D2: Full Critique on Optimization Design

**Rationale:** Performance optimizations often have subtle trade-offs (caching introduces staleness, indexing slows writes, async processing adds complexity). The 3-judge critique catches these trade-offs before implementation.

### D3: Benchmark Tests as TDD Substitute

**Rationale:** Traditional TDD (write failing test, make it pass) doesn't quite fit performance work. Instead, WF10 uses benchmark tests as the "test" -- establish baseline metric, implement optimization, verify metric improved. This is measurement-driven development.

### D4: Revert on No Improvement

**Rationale:** An optimization that doesn't measurably improve performance is complexity for no benefit. WF10 explicitly reverts changes that don't meet the success criteria. Better to have simple code that's slow than complex code that's equally slow.

### D5: Optimization Scope Escalation to WF2

**Rationale:** Some performance issues require architectural redesign (e.g., moving from synchronous to event-driven, adding a caching layer, restructuring database schema). These are feature changes, not optimizations -- WF2 is the appropriate workflow.

---

## Principle Coverage Matrix

| Principle                | Enforced    | How                                     |
| ------------------------ | ----------- | --------------------------------------- |
| P1 Branch Isolation      | Yes         | Step 6                                  |
| P2 Code Formatting       | Yes         | Automated in commits                    |
| P3 Frequent Commits      | Yes         | Step 8                                  |
| P4 Remote Sync           | Yes         | Step 11                                 |
| P5 TDD Enforcement       | Yes         | Step 8: benchmark-driven                |
| P6 Main-to-Dev Sync      | Yes         | Step 13                                 |
| P7 Triple-Gate           | Yes         | Steps 8 (local), 12 (CI), 14 (deployed) |
| P8 Shift-Left Critique   | Yes         | Step 5: full critique                   |
| P9 Memorization          | Yes         | Step 10 (always)                        |
| P10 Diagram-Driven       | Conditional | Only for architectural changes          |
| P11 User-in-the-Loop     | Yes         | Steps 1, 5 (circuit breaker)            |
| P12 Conventional Commits | Yes         | Step 8, 11                              |
| P13 Pre-PR Review        | Yes         | Step 10                                 |
| P14 Documentation-Gated  | Yes         | Steps 11, 15                            |
