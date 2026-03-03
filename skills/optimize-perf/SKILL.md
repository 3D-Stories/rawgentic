---
name: rawgentic:optimize-perf
description: Optimize performance using the WF10 15-step workflow with baseline profiling, benchmark-driven development, full critique on optimization design, and before/after evidence. Invoke with /optimize-perf followed by a scope description or issue number.
argument-hint: Performance scope (e.g., "API response times", "db-queries", or issue number)
---

# WF10: Performance Optimization Workflow

<role>
You are the WF10 orchestrator implementing a 15-step performance optimization workflow. You enforce a strict measurement-driven approach: every optimization must be justified by profiling data and validated by benchmark improvement. No guessing, no premature optimization.
</role>

<constants>
REPO = "<inferred from `git remote -v` at workflow start>"
PROJECT_ROOT = "<inferred from `git rev-parse --show-toplevel`>"
BRANCH_PREFIX = "perf/"
OPTIMIZATION_CATEGORIES:
  query: PostgreSQL queries → EXPLAIN ANALYZE
  api: HTTP endpoint latency → response time measurement
  compute: CPU-bound operations → cProfile / Node profiler
  memory: RAM consumption → process memory tracking
  network: cross-VM communication → latency measurement
  frontend: page load/render → Lighthouse / Web Vitals
LOOPBACK_BUDGET:
  Step_5_to_4: max 2 iterations
  global_cap: 2
</constants>

<environment-setup>
Constants are populated at workflow start (Step 1) by running:
- `REPO`: `git remote get-url origin | sed 's|.*github.com[:/]||;s|\.git$||'`
- `PROJECT_ROOT`: `git rev-parse --show-toplevel`
- Other constants: Read from CLAUDE.md infrastructure and database sections

If any constant cannot be resolved, STOP and ask the user. Do not assume values.
</environment-setup>

<termination-rule>
WF10 terminates after deployment verification with benchmark comparison. No auto-transition.
</termination-rule>

<ambiguity-circuit-breaker>
If profiling results are inconclusive, optimization approaches conflict, or benchmark improvements are within noise margin — STOP and present to user for resolution before proceeding. Do not auto-resolve ambiguity. User has final authority (P11).
</ambiguity-circuit-breaker>

<context-compaction>
Per CLAUDE.md shared invariant #9: before context compaction, document in `claude_docs/session_notes.md`: current step number, branch name, last commit SHA, baseline measurements, optimization category, and loop-back budget state.
</context-compaction>

<measure-dont-guess>
1. Never optimize without a baseline measurement
2. Never assume a bottleneck — profile first
3. Always measure after optimization — confirm improvement
4. Watch for regression in other areas
</measure-dont-guess>

## Step 1: Receive Performance Scope

### Instructions

1. Parse scope: identify affected subsystem.
2. If issue: fetch via `gh issue view <number> --repo ${REPO}`
3. Clarify expectations:
   - What is "slow"? (current latency, expected latency)
   - What is the acceptable target? (specific numbers, not "faster")
   - Is this user-facing or internal?
4. Confirm scope with user.
5. Update `claude_docs/session_notes.md` with: performance target, baseline metrics, optimization scope.

### Failure Modes

- Scope too vague ("it's slow") → ask for specific metrics, endpoints, or operations
- Issue not found → verify issue number and repo
- No measurable target defined → require specific numbers before proceeding (e.g., "p95 < 200ms", not "faster")
- Scope spans multiple subsystems → prioritize by user impact, handle one at a time

---

## Step 2: Analyze Performance Context

### Instructions

1. **Code path mapping:** Use Serena to trace entry point to response.
2. **Architecture review:** Identify caching layers, DB queries, network calls, compute operations.
3. **Existing optimization inventory:** Check for existing caching, indexing, pagination, connection pooling.
4. **Environment context:** Docker resource limits, VM specs, PostgreSQL config.
5. **Historical context:** Check session notes/MEMORY.md for prior performance work.

### Failure Modes

- Serena MCP unavailable → fall back to Grep/Glob for code path tracing
- Code path is too complex to trace fully → focus on the hot path identified by profiling in Step 3
- No historical context found → proceed without baseline comparison, document as first measurement

---

## Step 3: Profile and Establish Baseline

### Instructions

1. **Establish baseline metrics:**
   - Queries: `EXPLAIN ANALYZE` on specific queries
   - API endpoints: measure response time with `curl -w` or timing
   - Compute: time the specific operation
   - Frontend: capture page load metrics
2. **Profile the hot path:**
   - Python: `cProfile` or `time.perf_counter()` instrumentation
   - Node.js: timing instrumentation
   - PostgreSQL: `EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT)` with real data
3. **Identify bottleneck(s):** Rank operations by time/resource consumption.
4. **Document baseline:** Record exact measurements, conditions, data volumes.

### Failure Modes

- Cannot reproduce slowness → check data volume, concurrent load
- Profiling overhead skews results → use lightweight instrumentation
- Bottleneck is external (network, third-party API) → document, present to user

---

## Step 4: Design Optimization Approach

### Instructions

Invoke `/superpowers:brainstorming` for each bottleneck:

1. **Generate options:**
   - Query: indexing, query rewrite, materialized views, caching
   - API: caching (Redis/in-memory), pagination, response trimming
   - Compute: algorithm improvement, batch processing, async, caching
   - Memory: reduce allocations, fix leaks, streaming/generators
   - Network: reduce round trips, batch requests, connection pooling
   - Frontend: code splitting, lazy loading, image optimization
2. **Evaluate trade-offs:** Implementation effort, expected improvement, maintenance cost, risk.
3. **Recommend approach:** Best effort-to-improvement ratio.
4. **Define success criteria:** Specific metric targets (e.g., "p95 response time < 200ms").

If the optimization involves architectural changes (new caching layer, query restructuring, service-level changes), create or update an Excalidraw diagram to document the before/after architecture (P10: Diagram-Driven Design).

### Failure Modes

- No clear optimization path → current performance may be near-optimal for the architecture; present to user
- Optimization requires architectural change → suggest delegating to WF2 for redesign
- Multiple conflicting approaches → present trade-offs to user for decision (P11)

---

## Step 5: Quality Gate — Optimization Critique

### Instructions

Invoke `/reflexion:critique` — three judges evaluate:

- **Effectiveness judge:** Will optimizations actually address measured bottlenecks? Expected improvements realistic?
- **Safety judge:** Could optimizations introduce bugs, data inconsistencies, new failure modes?
- **Simplicity judge:** Is this the simplest optimization that achieves the target? Over-engineering?

### Output

Validated optimization design OR loop back to Step 4 (max 2 iterations).

### Failure Modes

- Judges find optimization won't address measured bottleneck → loop back to Step 4 with refined approach
- Safety judge flags data inconsistency risk (e.g., stale cache) → add cache invalidation strategy before proceeding
- Simplicity judge flags over-engineering → simplify approach, consider simpler alternative
- Loop-back budget exhausted (2 iterations) → proceed with best available design, document caveats

---

## Step 6: Create Performance Branch

### Instructions

```bash
git fetch origin main
git checkout -b perf/<desc> origin/main
```

### Failure Modes

- Branch name conflicts → append date suffix or disambiguate
- Origin/main is stale → `git fetch origin` before checkout
- Uncommitted changes block checkout → stash or commit first

---

## Step 7: Create Performance Plan

### Instructions

1. Order tasks:
   - Task 1: Add benchmark test(s) capturing current performance
   - Task 2-N: Implement each optimization
   - Task N+1: Run benchmarks and compare
   - Task N+2: Update documentation
2. Each task: file path, action, expected metric improvement.

### Failure Modes

- Plan has too many optimizations for one PR → split into incremental PRs, each with its own benchmark
- Benchmark test design unclear → model after existing test patterns, measure wall-clock time or query cost

---

## Step 8: Implement Optimizations (Benchmark-Driven)

### Instructions

1. **Write benchmark tests:** Tests that measure the specific metrics being optimized.
2. **Implement each optimization:**
   - Apply optimization
   - Run benchmark test — verify improvement
   - Run full test suite — verify no regressions
   - Commit: `perf(scope): <optimization description>`
3. **For database optimizations:**
   - Add indexes via migration (if needed)
   - Verify with `EXPLAIN ANALYZE` before and after
   - Check index doesn't slow writes significantly
4. **For caching:**
   - Implement cache invalidation strategy
   - Test cache hit/miss paths
   - Test stale cache handling

### Failure Modes

- Optimization doesn't improve metrics → revert, try next approach
- Optimization causes test failures → investigate side effects
- Improvement is marginal → discuss with user: proceed or revert

Push to remote: `git push origin perf/<branch>` (P4: Regular Remote Sync — checkpoint after tests pass).

Update `claude_docs/session_notes.md` with: optimizations applied, benchmark results so far, test status.

---

## Step 9: Post-Optimization Benchmark

### Instructions

1. Run EXACT same benchmarks as Step 3 (same conditions, same data).
2. Compare before/after metrics.
3. Calculate improvement percentages.
4. Verify success criteria are met.
5. Check for regressions in related areas.
6. Document results with evidence.

### Output

Benchmark comparison: { baseline, optimized, improvement_pct, regressions }

### Failure Modes

- No improvement → revert optimizations, investigate further
- Below target → discuss with user
- Regressions detected → investigate and fix

---

## Step 10: Code Review + Memorize

### Instructions

Launch 4-agent review focused on: optimization correctness, cache invalidation safety, no new failure modes, code clarity.

Run `/reflexion:memorize` — performance optimizations almost always reveal patterns: query patterns, caching strategies, architecture bottlenecks.

### Failure Modes

- Review finds cache invalidation gap → add invalidation strategy before PR
- Review finds correctness issue → fix and re-run benchmarks to confirm improvement still holds
- Memorize produces no insights → review optimization trade-offs and architectural decisions for CLAUDE.md patterns

---

## Step 11: Create Pull Request

### Instructions

```bash
git push -u origin perf/<desc>
gh pr create --repo ${REPO} \
  --title "perf(scope): <optimization summary>" \
  --body "$(cat <<'EOF'
## Summary
- Bottleneck: [what was slow]
- Optimization: [what was done]

## Benchmark Results
| Metric | Before | After | Improvement |
|--------|--------|-------|-------------|
| [metric] | [value] | [value] | [X%] |

## Test plan
- [ ] Benchmark tests confirm improvement
- [ ] Full test suite passes
- [ ] CI passes
- [ ] Post-deploy benchmarks confirm

Generated with [Claude Code](https://claude.com/claude-code) using WF10
EOF
)" \
  --label "performance"
```

Push to remote: `git push -u origin perf/<desc>` (P4: Regular Remote Sync — checkpoint after PR creation).

### Failure Modes

- Push fails (auth issue) → check GitHub PAT scopes (needs Contents r/w)
- PR create fails → verify branch has commits ahead of main
- Label "performance" doesn't exist → create it or omit label
- Benchmark table formatting broken → verify markdown renders correctly in PR preview

---

## Step 12: CI Verification

### Instructions

Wait for CI via `gh run list --branch <branch>`. Max 2 fix-and-retry cycles.

### Failure Modes

- CI fails on benchmark tests → investigate: test environment may differ from local
- CI fails on unrelated tests → check if optimization has side effects; fix if related
- CI not triggered → verify branch is pushed and workflow is configured
- Max 2 fix-and-retry cycles exceeded → STOP and present to user

---

## Step 13: Merge and Deploy

### Instructions

1. Squash-merge: `gh pr merge <number> --squash --delete-branch --repo ${REPO}`
2. Deploy: `${PROJECT_ROOT}/scripts/deploy-dev.sh`
3. Re-run benchmarks against deployed environment to confirm improvement holds.

### Failure Modes

- Merge conflicts → rebase on latest main and re-run tests
- Deploy script fails → check SSH connectivity to chorestory-dev, verify Docker status
- Post-deploy benchmarks show no improvement → investigate cold cache, different data volume, resource allocation

---

## Step 14: Post-Deploy Performance Verification

### Instructions

1. Run benchmarks against deployed dev environment.
2. Compare with pre-optimization baseline.
3. Verify no regressions in other areas.
4. Health check all services.
5. Document final results.

### Failure Modes

- Performance worse in deployed env → investigate: different data volume, resource allocation, cold cache

---

## Step 15: Completion Summary

### Instructions

1. Update `claude_docs/session_notes.md`
2. Close GitHub issue if exists with benchmark evidence
3. Present summary:

```
WF10 COMPLETE
==============

Bottleneck: [what was slow]
Optimization: [what was done]
Category: [query/api/compute/memory/network/frontend]

Benchmark Results:
| Metric | Before | After | Improvement |
|--------|--------|-------|-------------|
| [metric] | [value] | [value] | [X%] |

Success Criteria: [met / not met]

Quality Gates:
- Optimization critique: [passed / N findings applied]
- Code review: [4-agent review passed]
- Memorized patterns: [N insights saved]
- CI: [passed]
- Post-deploy benchmark: [confirmed]

Loop-backs used: N / 2 (global cap)

WF10 complete.
```

### Failure Modes

- GitHub issue doesn't exist → create one with benchmark evidence as the body
- Session notes too long → archive to `session_notes_NNN.md` and start fresh
- Success criteria not met → document partial improvement and remaining gap in summary

---

## Workflow Resumption

1. PR merged? → Step 14 (post-deploy verify)
2. PR exists and CI passed? → Step 13 (merge)
3. PR exists? → Step 12 (CI)
4. Perf branch has optimized code with benchmarks? → Step 10 (review)
5. Perf branch has code changes? → Step 9 (benchmark)
6. Perf branch exists (empty)? → Step 8 (implement)
7. Optimization design in session notes? → Step 5 (critique)
8. Baseline/profile data in session notes? → Step 4 (design)
9. None → Step 1

Announce detected state: "Detected prior progress. Resuming at Step N."
