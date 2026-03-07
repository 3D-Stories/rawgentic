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
BRANCH_PREFIX = "perf/"
OPTIMIZATION_CATEGORIES:
  query: Database queries -> use database-appropriate query analysis from config
  api: HTTP endpoint latency -> response time measurement
  compute: CPU-bound operations -> select profiler based on config.techStack
  memory: RAM consumption -> process memory tracking
  network: cross-VM communication -> latency measurement
  frontend: page load/render -> select frontend performance tools based on config.services
LOOPBACK_BUDGET:
  Step_5_to_4: max 2 iterations
  global_cap: 2
PROFILING_TOOLS: Select profiling tools based on config.techStack and config.services
</constants>

<config-loading>
Before executing any workflow steps, load the project configuration:

1. Read `.rawgentic_workspace.json` from the Claude root directory.
   - Missing -> STOP. Tell user: "No rawgentic workspace found. Run /rawgentic:new-project."
   - Malformed JSON -> STOP. Tell user: "Workspace file is corrupted. Run /rawgentic:new-project to regenerate, or fix manually."
   - Extract the active project entry (active == true).
   - **Path resolution:** The `activeProject.path` may be relative (e.g., `./projects/my-app`). Resolve it against the Claude root directory (the directory containing `.rawgentic_workspace.json`) to get the absolute path for file operations.

2. Read `<activeProject.path>/.rawgentic.json`.
   - Missing -> STOP. Tell user: "Active project <name> has no config. Run /rawgentic:setup."
   - Malformed JSON -> STOP. Tell user: "Project config is corrupted. Run /rawgentic:setup to regenerate."
   - Check `config.version`. If version > 1 (or missing), warn user about version mismatch.
   - Parse full JSON into `config` object.

3. Build the `capabilities` object from config:
   - has_tests: config.testing exists AND config.testing.frameworks.length > 0
   - test_commands: config.testing.frameworks[].command
   - has_ci: config.ci exists AND config.ci.provider exists
   - has_deploy: config.deploy exists AND config.deploy.method exists and != "manual"
   - has_database: config.database exists AND config.database.type exists
   - has_docker: config.infrastructure exists AND config.infrastructure.docker.composeFiles.length > 0
   - project_type: config.project.type
   - repo: config.repo.fullName
   - default_branch: config.repo.defaultBranch

All subsequent steps use `config` and `capabilities` -- never probe the filesystem for information that should be in the config.
</config-loading>

<learning-config>
If this workflow discovers new performance characteristics or profiling tools during optimization, update `.rawgentic.json` before completing:
- Append to arrays (e.g., add new test framework for benchmarks to testing.frameworks[])
- Set fields that are currently null or missing (e.g., discovered service ports)
- Do NOT overwrite existing non-null values without asking the user
- Always read full file, modify in memory, write full file back
</learning-config>

<environment-setup>
Constants are populated at workflow start (Step 1) from config:
- `capabilities.repo`: from config.repo.fullName
- `PROJECT_ROOT`: from active project path in `.rawgentic_workspace.json`
- Infrastructure, database, and service details: from `config.infrastructure`, `config.database`, and `config.services`

If any required config field is missing or null, STOP and ask the user. Do not assume values.
</environment-setup>

<termination-rule>
WF10 terminates after deployment verification with benchmark comparison. No auto-transition. WF10 terminates ONLY after the completion-gate (after Step 15) passes. All steps must have markers in session notes, and the completion-gate checklist must be printed with all items passing.
</termination-rule>

<ambiguity-circuit-breaker>
If profiling results are inconclusive, optimization approaches conflict, or benchmark improvements are within noise margin -- STOP and present to user for resolution before proceeding. Do not auto-resolve ambiguity. User has final authority (P11).
</ambiguity-circuit-breaker>

<context-compaction>
Before context compaction, document in `claude_docs/session_notes.md`: current step number, branch name, last commit SHA, baseline measurements, optimization category, and loop-back budget state.
</context-compaction>

<measure-dont-guess>
1. Never optimize without a baseline measurement
2. Never assume a bottleneck -- profile first
3. Always measure after optimization -- confirm improvement
4. Watch for regression in other areas
</measure-dont-guess>

<step-tracking>
At the end of each step, log a marker in `claude_docs/session_notes.md`:
`### WF10 Step X: <Name> -- DONE (<key detail>)`
This enables workflow resumption if context is lost.
</step-tracking>

## Step 1: Receive Performance Scope

### Instructions

1. **Load project configuration** per `<config-loading>`. Resolve `capabilities.repo` and `PROJECT_ROOT`. Log resolved values in session notes. If any config field cannot be resolved, STOP and ask the user.
2. Parse scope: identify affected subsystem.
3. If issue: fetch via `gh issue view <number> --repo ${capabilities.repo}`
4. Clarify expectations:
   - What is "slow"? (current latency, expected latency)
   - What is the acceptable target? (specific numbers, not "faster")
   - Is this user-facing or internal?
5. Confirm scope with user.
6. Update `claude_docs/session_notes.md` with: performance target, baseline metrics, optimization scope.

### Failure Modes

- Scope too vague ("it's slow") -> ask for specific metrics, endpoints, or operations
- Issue not found -> verify issue number and repo
- No measurable target defined -> require specific numbers before proceeding (e.g., "p95 < 200ms", not "faster")
- Scope spans multiple subsystems -> prioritize by user impact, handle one at a time

---

## Step 2: Analyze Performance Context

### Instructions

1. **Code path mapping:** Use Serena to trace entry point to response.
2. **Architecture review:** Identify caching layers, DB queries, network calls, compute operations using `config.infrastructure` and `config.services`.
3. **Existing optimization inventory:** Check for existing caching, indexing, pagination, connection pooling.
4. **Environment context:** Review `config.infrastructure` for Docker resource limits, VM specs; review `config.database` for database configuration.
5. **Historical context:** Check session notes/MEMORY.md for prior performance work.

### Failure Modes

- Serena MCP unavailable -> fall back to Grep/Glob for code path tracing
- Code path is too complex to trace fully -> focus on the hot path identified by profiling in Step 3
- No historical context found -> proceed without baseline comparison, document as first measurement

---

## Step 3: Profile and Establish Baseline

### Instructions

1. **Establish baseline metrics** (select tools based on `config.techStack` and `config.services`):
   - Queries: If `capabilities.has_database`, use database-appropriate query analysis (e.g., EXPLAIN ANALYZE for PostgreSQL, EXPLAIN for MySQL)
   - API endpoints: measure response time with `curl -w` or timing
   - Compute: time the specific operation
   - Frontend: If `config.services` has frontend type, use appropriate frontend performance tools (e.g., Lighthouse for web, bundle analysis for SPAs)
2. **Profile the hot path** (select profiler based on `config.techStack`):
   - Use the profiling tools appropriate for the project's tech stack (e.g., cProfile for Python, --prof for Node.js, pprof for Go, JMH for Java)
   - If `capabilities.has_database`, run query analysis with real data
3. **Identify bottleneck(s):** Rank operations by time/resource consumption.
4. **Document baseline:** Record exact measurements, conditions, data volumes.

### Failure Modes

- Cannot reproduce slowness -> check data volume, concurrent load
- Profiling overhead skews results -> use lightweight instrumentation
- Bottleneck is external (network, third-party API) -> document, present to user

---

## Step 4: Design Optimization Approach

### Instructions

Invoke `/superpowers:brainstorming` for each bottleneck:

1. **Generate options** (adapt based on `config.techStack` and `config.database`):
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

- No clear optimization path -> current performance may be near-optimal for the architecture; present to user
- Optimization requires architectural change -> suggest delegating to WF2 for redesign
- Multiple conflicting approaches -> present trade-offs to user for decision (P11)

---

## Step 5: Quality Gate -- Optimization Critique

### Instructions

Invoke `/reflexion:critique` -- three judges evaluate:

- **Effectiveness judge:** Will optimizations actually address measured bottlenecks? Expected improvements realistic?
- **Safety judge:** Could optimizations introduce bugs, data inconsistencies, new failure modes?
- **Simplicity judge:** Is this the simplest optimization that achieves the target? Over-engineering?

### Output

Validated optimization design OR loop back to Step 4 (max 2 iterations).

### Failure Modes

- Judges find optimization won't address measured bottleneck -> loop back to Step 4 with refined approach
- Safety judge flags data inconsistency risk (e.g., stale cache) -> add cache invalidation strategy before proceeding
- Simplicity judge flags over-engineering -> simplify approach, consider simpler alternative
- Loop-back budget exhausted (2 iterations) -> proceed with best available design, document caveats

---

## Step 6: Create Performance Branch

### Instructions

```bash
git fetch origin ${capabilities.default_branch}
git checkout -b perf/<desc> origin/${capabilities.default_branch}
```

### Failure Modes

- Branch name conflicts -> append date suffix or disambiguate
- Origin branch is stale -> `git fetch origin` before checkout
- Uncommitted changes block checkout -> stash or commit first

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

- Plan has too many optimizations for one PR -> split into incremental PRs, each with its own benchmark
- Benchmark test design unclear -> model after existing test patterns, measure wall-clock time or query cost

---

## Step 8: Implement Optimizations (Benchmark-Driven)

### Instructions

1. **Write benchmark tests:** Tests that measure the specific metrics being optimized.
2. **Implement each optimization:**
   - Apply optimization
   - Run benchmark test -- verify improvement
   - Run full test suite -- verify no regressions
   - Commit: `perf(scope): <optimization description>`
3. **For database optimizations** (if `capabilities.has_database`):
   - Add indexes via migration (if needed)
   - Verify with database-appropriate query analysis before and after
   - Check index doesn't slow writes significantly
4. **For caching:**
   - Implement cache invalidation strategy
   - Test cache hit/miss paths
   - Test stale cache handling

### Failure Modes

- Optimization doesn't improve metrics -> revert, try next approach
- Optimization causes test failures -> investigate side effects
- Improvement is marginal -> discuss with user: proceed or revert

Push to remote: `git push origin perf/<branch>` (P4: Regular Remote Sync -- checkpoint after tests pass).

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

- No improvement -> revert optimizations, investigate further
- Below target -> discuss with user
- Regressions detected -> investigate and fix

---

## Step 10: Code Review + Memorize

### Instructions

Launch 4-agent review focused on: optimization correctness, cache invalidation safety, no new failure modes, code clarity.

Run `/reflexion:memorize` -- performance optimizations almost always reveal patterns: query patterns, caching strategies, architecture bottlenecks.

### Failure Modes

- Review finds cache invalidation gap -> add invalidation strategy before PR
- Review finds correctness issue -> fix and re-run benchmarks to confirm improvement still holds
- Memorize produces no insights -> review optimization trade-offs and architectural decisions for reusable patterns

---

## Step 11: Create Pull Request

### Instructions

```bash
git push -u origin perf/<desc>
gh pr create --repo ${capabilities.repo} \
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

Push to remote: `git push -u origin perf/<desc>` (P4: Regular Remote Sync -- checkpoint after PR creation).

### Failure Modes

- Push fails (auth issue) -> check GitHub PAT scopes (needs Contents r/w)
- PR create fails -> verify branch has commits ahead of main
- Label "performance" doesn't exist -> create it or omit label
- Benchmark table formatting broken -> verify markdown renders correctly in PR preview

---

## Step 12: CI Verification

### Instructions

Wait for CI via `gh run list --branch <branch>`. Max 2 fix-and-retry cycles.

### Failure Modes

- CI fails on benchmark tests -> investigate: test environment may differ from local
- CI fails on unrelated tests -> check if optimization has side effects; fix if related
- CI not triggered -> verify branch is pushed and workflow is configured
- Max 2 fix-and-retry cycles exceeded -> STOP and present to user

---

## Step 13: Merge and Deploy

### Instructions

1. Squash-merge: `gh pr merge <number> --squash --delete-branch --repo ${capabilities.repo}`
2. Deploy per `config.deploy` method (if `capabilities.has_deploy`). If no automated deploy configured, notify user to deploy manually.
3. Re-run benchmarks against deployed environment to confirm improvement holds.

### Failure Modes

- Merge conflicts -> rebase on latest default branch and re-run tests
- Deploy fails -> check connectivity and service status per `config.infrastructure`
- Post-deploy benchmarks show no improvement -> investigate cold cache, different data volume, resource allocation

---

## Step 14: Post-Deploy Performance Verification

### Instructions

1. Run benchmarks against deployed dev environment.
2. Compare with pre-optimization baseline.
3. Verify no regressions in other areas.
4. Health check all services.
5. Document final results.

### Failure Modes

- Performance worse in deployed env -> investigate: different data volume, resource allocation, cold cache

---

## Step 15: Completion Summary

### Instructions

1. Update `claude_docs/session_notes.md`
2. Close GitHub issue if exists with benchmark evidence
3. Apply `<learning-config>` -- update `.rawgentic.json` with any discovered performance characteristics or profiling tools.
4. Present summary:

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

- GitHub issue doesn't exist -> create one with benchmark evidence as the body
- Session notes too long -> archive to `session_notes_NNN.md` and start fresh
- Success criteria not met -> document partial improvement and remaining gap in summary

---

<completion-gate>
Before declaring WF10 complete, verify ALL of the following. Print the checklist with pass/fail for each item:

1. [ ] Step markers logged for ALL executed steps in session notes
2. [ ] Final step output (completion summary) presented to user
3. [ ] Session notes updated with completion summary
4. [ ] Benchmark before/after table presented
5. [ ] PR URL documented
6. [ ] E2E passed
7. [ ] Performance metrics documented

If ANY item fails, go back and complete it before declaring "WF10 complete."
You may NOT output "WF10 complete" until all items pass.
</completion-gate>

---

## Workflow Resumption

0. All step markers present but completion-gate not printed? -> Run completion-gate, then terminate.
1. PR merged? -> Step 14 (post-deploy verify)
2. PR exists and CI passed? -> Step 13 (merge)
3. PR exists? -> Step 12 (CI)
4. Perf branch has optimized code with benchmarks? -> Step 10 (review)
5. Perf branch has code changes? -> Step 9 (benchmark)
6. Perf branch exists (empty)? -> Step 8 (implement)
7. Optimization design in session notes? -> Step 5 (critique)
8. Baseline/profile data in session notes? -> Step 4 (design)
9. None -> Step 1


Announce detected state: "Detected prior progress. Resuming at Step N."
