# Phase 4: Unified SDLC Framework Architecture

**Date:** 2026-03-02
**Author:** Orchestrator (direct authoring)
**Inputs:** All Phase 3 workflow designs (WF1-WF11 v1.1/v2.3), phase2-principles.md (P1-P14), phase2b-official-comparison.md, consolidated critique findings
**Purpose:** Synthesize all 9 workflow designs into a unified framework with shared abstractions, cross-workflow decision matrices, a workflow selector, and escalation paths.

---

## 1. Framework Overview

### 1.1 Workflow Catalog

| ID   | Name                   | Skill                | Steps | Branch Prefix | Critique Level  |
| ---- | ---------------------- | -------------------- | ----- | ------------- | --------------- |
| WF1  | Issue Creation         | `/create-issue`      | 9     | —             | Full critique   |
| WF2  | Feature Implementation | `/implement-feature` | 16    | feature/ fix/ | Full critique   |
| WF3  | Bug Fix                | `/fix-bug`           | 14    | fix/          | Reflect only    |
| WF4  | Refactoring            | `/refactor`          | 14    | refactor/     | Category-based  |
| WF7  | Documentation          | `/update-docs`       | 10    | docs/         | Reflect + user  |
| WF8  | Dependency Update      | `/update-deps`       | 12    | deps/         | Audit-based     |
| WF9  | Security Audit         | `/security-audit`    | 14    | security/     | Full (on audit) |
| WF10 | Performance Optim.     | `/optimize-perf`     | 15    | perf/         | Full critique   |
| WF11 | Incident Response      | `/incident`          | 14    | hotfix/       | Reflect (RCA)   |

### 1.2 Numbering Gaps

WF5 (Code Review) and WF6 (Testing) are intentionally absent — their functionality is embedded as quality gates inside other workflows (Step 11 in WF2, Step 9 in WF3, etc.) rather than standalone workflows. This avoids artificial workflow boundaries for activities that are always part of a larger pipeline.

---

## 2. Shared Base Protocol

All code-producing workflows (WF2, WF3, WF4, WF8, WF9, WF10, WF11) share a common "tail" of steps that handle the PR→CI→merge→deploy→verify pipeline. WF1 and WF7 do not produce code in the traditional sense (WF1 creates issues, WF7 creates docs) but share a subset.

### 2.1 Common Step Archetypes

| Archetype             | WF2     | WF3     | WF4     | WF8     | WF9       | WF10      | WF11     | WF7     | WF1     |
| --------------------- | ------- | ------- | ------- | ------- | --------- | --------- | -------- | ------- | ------- |
| A. Receive Scope      | Step 1  | Step 1  | Step 1  | Step 1  | Step 1    | Step 1    | Step 1   | Step 1  | Step 1  |
| B. Analyze Context    | Step 2  | Step 2  | Step 2  | Step 2  | Step 2    | Step 2    | Step 2   | Step 2  | —       |
| C. Design/Plan        | Step 3  | Step 3§ | Step 3  | Step 3  | Steps 3-4 | Steps 3-4 | Step 3   | Step 3  | Step 2  |
| D. Quality Gate       | Step 4  | Step 4  | Step 4  | —       | Step 5    | Step 5    | —        | Step 6¶ | Step 3  |
| E. Create Branch      | Step 7  | Step 6  | Step 6  | Step 4  | Step 7    | Step 6    | Step 10† | Step 4  | —       |
| F. Implement (TDD)    | Step 8  | Step 7  | Step 7  | Step 5  | Step 8    | Step 8    | Step 10† | Step 5  | —       |
| G. Code Review        | Step 11 | Step 9  | Step 9  | Step 7  | Step 9    | Step 10   | Step 10† | —       | —       |
| H. Create PR          | Step 12 | Step 10 | Step 10 | Step 8  | Step 10   | Step 11   | Step 10† | Step 7  | Step 8‡ |
| I. CI Verification    | Step 13 | Step 11 | Step 11 | Step 9  | Step 11   | Step 12   | Step 10† | Step 8  | —       |
| J. Merge + Deploy     | Step 14 | Step 12 | Step 12 | Step 10 | Step 12   | Step 13   | Step 10† | Step 9  | —       |
| K. Post-Deploy Verify | Step 15 | Step 13 | Step 13 | Step 11 | Step 13   | Step 14   | —        | —       | —       |
| L. Completion Summary | Step 16 | Step 14 | Step 14 | Step 12 | Step 14   | Step 15   | Step 14  | Step 10 | Step 9  |

**† WF11 Note:** WF11 bundles archetypes E-J into a single Step 10 ("Implement Permanent Fix") for speed. Phase B Steps 7-9 (RCA, Design Fix, RCA Critique) map to B-D but at Phase B scope. Steps 11-13 (Preventive Measures, Action Items, Memorize) are incident-specific with no standard archetype equivalent.

**‡ WF1 Note:** WF1 Step 8 is "Create Issue" (not "Create PR") — archetype H is the nearest analog as both produce a GitHub artifact.

**§ WF3 Note:** WF3 Step 3 is "Root Cause Analysis" — a specialized design/plan step focused on RCA rather than feature design. Step 5 "Create Fix Plan" is an additional planning step that extends archetype C.

**¶ WF7 Note:** WF7 Step 6 is "Accuracy Reflect + User Review" — a quality gate combining `/reflexion:reflect` with mandatory user review. It runs post-implementation (after Step 5), unlike the pre-implementation quality gates in WF2-WF4. This positioning is intentional: documentation accuracy can only be verified after content is written.

### 2.2 Archetype Definitions

**A. Receive Scope** — Human provides intent. Claude parses, classifies, confirms. Output: validated scope object. Always has P11 (User-in-the-Loop).

**B. Analyze Context** — Automated codebase analysis. Uses Serena MCP + Context7 + Grep/Glob. Identifies affected files, dependencies, existing tests. Not applicable to WF1 (no codebase analysis needed for issue creation).

**C. Design/Plan** — Automated design generation. Uses `/superpowers:brainstorming` for complex designs, inline analysis for simpler ones. Produces structured design document.

**D. Quality Gate** — Critique or reflect on the design. Level varies by workflow risk (see Section 4). Some workflows skip this (WF8, WF11 Phase A, WF7).

**E. Create Branch** — `git checkout -b <prefix>/<desc> origin/main`. Branch prefix varies by workflow type. Enforces P1 (Branch Isolation).

**F. Implement (TDD)** — The core work. TDD variant varies by workflow (see Section 3). Enforces P5 (TDD), P3 (Frequent Commits).

**G. Code Review** — Pre-PR review. Full 4-agent suite for WF2/WF4/WF10. Focused review for WF8. Abbreviated for WF11. See Section 5.

**H. Create PR** — `gh pr create`. Conventional commit title. Enforces P12, P14 (Documentation-Gated).

**I. CI Verification** — GitHub Actions. Max 2 fix-and-retry cycles. Enforces P7 (Gate 2).

**J. Merge + Deploy** — `gh pr merge --squash` + deploy-dev.sh. Enforces P6 (Main-to-Dev Sync), P7 (Gate 3 start).

**K. Post-Deploy Verify** — Hit health endpoints, run E2E tests (scope-dependent), check Docker logs. Enforces P7 (Gate 3 completion). Some workflows have domain-specific verification (WF10: benchmarks, WF9: security scan, WF8: smoke test).

**L. Completion Summary** — Update session notes, close GitHub issue, present summary. Enforces P14.

### 2.3 Shared Invariants

These behaviors are identical across ALL code-producing workflows:

1. **Ambiguity Circuit Breaker**: STOP and ask user when findings are ambiguous, conflicting, or require judgment. No attended/unattended distinction.
2. **Finding Auto-Application**: Apply ALL quality gate findings automatically. No severity-based filtering.
3. **Workflow Resumption**: All workflows have checkpoint artifacts + numbered step detection algorithms.
4. **Session Notes**: Continuous documentation in `claude_docs/session_notes.md`.
5. **Commit Convention**: `<type>(scope): <description>` where type = feat/fix/refactor/docs/deps/perf/security/hotfix matching branch prefix.
6. **Branch from main**: All branches created from `origin/main` (not from other feature branches).
7. **Squash merge**: All PRs use `gh pr merge --squash` (not regular merge or rebase).
8. **Deploy to dev only**: Workflows deploy to my-api-dev + darwin dev environments. Production deployment is outside workflow scope.
9. **Context compaction protocol**: Before compaction, document current step, quality gate state, branch name, last commit SHA.

---

## 3. TDD Variant Matrix

Each workflow that produces code uses a tailored TDD approach:

| Workflow | TDD Variant             | Pattern                                                           | Rationale                                         |
| -------- | ----------------------- | ----------------------------------------------------------------- | ------------------------------------------------- |
| WF2      | Standard TDD            | Write test → Red → Implement → Green → Refactor                   | New features have defined acceptance criteria     |
| WF3      | Reproduce-First TDD     | Write failing test reproducing bug → Fix → Green                  | Bug fixes have concrete "before" state to capture |
| WF4      | Characterization-First  | Write tests capturing current behavior → Refactor → Re-run        | Refactoring must prove NO behavior change         |
| WF8      | Test-After              | Apply update → Run existing tests → Fix failures                  | Deps don't need new tests; existing tests verify  |
| WF9      | Vulnerability-First TDD | Write test exploiting vulnerability → Fix → Test fails to exploit | Security fixes prove the vulnerability is closed  |
| WF10     | Benchmark-First TDD     | Establish baseline metric → Optimize → Verify improvement         | Performance gains must be measured, not assumed   |
| WF11     | Regression TDD          | Write test exposing root cause → Fix permanently → Verify         | Incidents need tests preventing recurrence        |

**WF7 (Documentation)**: No TDD — documentation has no code to test. Accuracy is verified by grep-checking facts against code.

**WF1 (Issue Creation)**: No TDD — issue creation produces requirements, not code.

---

## 4. Critique Level Decision Matrix

The critique level (full, reflect, or none) is calibrated to the **reversal cost** of the workflow's output:

| Workflow | Critique Level     | Gate Command                                              | Reversal Cost | Rationale                                                                |
| -------- | ------------------ | --------------------------------------------------------- | ------------- | ------------------------------------------------------------------------ |
| WF1      | Full critique      | `/reflexion:critique`                                     | Low           | Cheap to fix issues, but catching bad ideas early saves downstream waste |
| WF2      | Full critique      | `/reflexion:critique`                                     | High          | Feature code is expensive to revert after merge/deploy                   |
| WF3      | Reflect only       | `/reflexion:reflect`                                      | Low           | Bug fixes are small, focused, easy to revert                             |
| WF4      | Category-based     | Full for Extract/Restructure, Reflect for Rename/Simplify | Medium        | Structural refactoring can break things subtly                           |
| WF7      | Reflect + user     | `/reflexion:reflect`                                      | Low           | Docs are easy to fix, but user review catches inaccuracy                 |
| WF8      | None (audit-based) | npm audit + test suite                                    | Medium        | Deps verified by automated audit + existing tests                        |
| WF9      | Full (on audit)    | `/reflexion:critique`                                     | High          | Security findings have high consequence if wrong                         |
| WF10     | Full critique      | `/reflexion:critique`                                     | Medium        | Optimization trade-offs are subtle (caching staleness, etc.)             |
| WF11     | Reflect (on RCA)   | `/reflexion:reflect`                                      | Low (hotfix)  | Speed > thoroughness during incident; RCA gets reflect                   |

---

## 5. Code Review Scope Matrix

Pre-PR code review scope is calibrated to what the workflow produces:

| Workflow | Review Approach    | What to Focus On                                                            |
| -------- | ------------------ | --------------------------------------------------------------------------- |
| WF2      | Full 4-agent suite | Type design, silent failures, code simplification, style                    |
| WF3      | Focused (2-agent)  | Silent failures, style (type design rarely changes in bug fixes)            |
| WF4      | Full 4-agent suite | Type design (refactoring creates new abstractions), behavioral preservation |
| WF7      | None (user review) | User reviews accuracy; no code review needed                                |
| WF8      | Focused (manual)   | Lock file correctness, transitive deps, compatibility code changes          |
| WF9      | Security-focused   | Vulnerability closure verification, no new attack surface introduced        |
| WF10     | Full 4-agent suite | Cache invalidation safety, no new failure modes, code clarity               |
| WF11     | Abbreviated        | Hotfix correctness, no regressions (speed > thoroughness)                   |

---

## 6. P9 Memorization Decision Matrix

Not all workflows generate insights worth memorizing. This matrix determines when `/reflexion:memorize` runs:

| Workflow | Memorize?   | Condition                                                 | What to Memorize                                   |
| -------- | ----------- | --------------------------------------------------------- | -------------------------------------------------- |
| WF1      | Conditional | Only if critique surfaces reusable specification patterns | Issue authoring patterns, requirement gaps         |
| WF2      | Always      | Every feature reveals codebase patterns                   | Architecture decisions, API patterns, gotchas      |
| WF3      | Conditional | Only if fix reveals recurring bug pattern                 | Bug root cause patterns, fragile code areas        |
| WF4      | Always      | Refactoring reveals structural patterns                   | Code organization patterns, abstraction insights   |
| WF7      | Conditional | Only if doc restructuring reveals maintenance patterns    | Documentation conventions, stale doc patterns      |
| WF8      | Conditional | Only if update reveals project-specific dep pattern       | Dep conflicts, migration gotchas, Docker issues    |
| WF9      | Always      | Security patterns are the MOST important memories         | Vulnerability patterns, security debt, STRIDE gaps |
| WF10     | Always      | Performance patterns are highly reusable                  | Bottleneck patterns, query optimizations, caching  |
| WF11     | Always      | Incident patterns prevent recurrence                      | Incident root causes, diagnostic shortcuts         |

**"Always" workflows**: WF2, WF4, WF9, WF10, WF11 — these always produce insights worth preserving.
**"Conditional" workflows**: WF1, WF3, WF7, WF8 — memorize only when the work reveals something reusable.

---

## 7. Escalation Path Map

### 7.1 Upward Escalation (simpler → more complex)

| From | To  | Trigger                                         | Detection Point              |
| ---- | --- | ----------------------------------------------- | ---------------------------- |
| WF3  | WF2 | Bug fix touches 10+ files, cross-service        | Step 2                       |
| WF4  | WF2 | Refactoring requires behavior change            | Step 1/Step 4/Step 7b/Step 9 |
| WF10 | WF2 | Performance fix requires architectural redesign | Step 4                       |
| WF11 | WF2 | Permanent fix is a feature-scale change         | Step 8                       |
| WF11 | WF3 | Permanent fix is a targeted bug fix             | Step 8                       |
| WF9  | WF2 | Security fix requires feature-scale change      | Step 6                       |
| WF9  | WF3 | Security fix is a targeted code fix             | Step 6                       |
| WF9  | WF8 | Security issue is a dependency vulnerability    | Step 6                       |

### 7.2 Lateral Delegation

| From | To  | Trigger                                         | Detection Point |
| ---- | --- | ----------------------------------------------- | --------------- |
| WF1  | WF2 | Issue created → user starts implementation      | After Step 9    |
| WF8  | WF9 | Dependency audit finds security vulnerability   | Step 2          |
| WF11 | WF9 | Incident root cause is a security vulnerability | Step 7          |

### 7.3 Escalation Protocol

When a workflow escalates to another:

1. **Document the escalation** in session notes: which workflow, why, what was completed so far
2. **Preserve artifacts**: design documents, branch (if any), test results carry forward
3. **Restart at Step 1 of target workflow**: but with pre-populated context from the source workflow
4. **No auto-transition**: escalation is announced to the user and requires explicit invocation of the target workflow skill

---

## 8. Workflow Selector

### 8.1 Decision Tree

When a user wants to start work, the framework needs a clear routing decision:

```
User wants to do something
  │
  ├─ "There's a production incident!" ──────────────────────► WF11 /incident
  │
  ├─ "I want to create a new issue" ────────────────────────► WF1 /create-issue
  │
  ├─ "I want to implement this issue" ──────────────────────► WF2 /implement-feature
  │
  ├─ "There's a bug" (with issue number) ───────────────────► WF3 /fix-bug
  │
  ├─ "I want to refactor this code" ────────────────────────► WF4 /refactor
  │
  ├─ "Update the documentation" ────────────────────────────► WF7 /update-docs
  │
  ├─ "Update dependencies" ─────────────────────────────────► WF8 /update-deps
  │
  ├─ "Run a security audit" ────────────────────────────────► WF9 /security-audit
  │
  ├─ "Optimize performance" ────────────────────────────────► WF10 /optimize-perf
  │
  └─ Unclear intent ───► Ask: "Is this a new feature, bug fix, refactoring, or something else?"
```

### 8.2 Ambiguous Cases

| User Says                   | Resolution                                               |
| --------------------------- | -------------------------------------------------------- |
| "Fix this slow query"       | WF10 (performance), not WF3 (bug) — slowness is perf     |
| "This endpoint is broken"   | WF3 (bug fix) — broken = incorrect behavior              |
| "Clean up this code"        | WF4 (refactoring) — cleanup = structural improvement     |
| "Add tests for X"           | WF2 (feature) — adding tests IS a feature                |
| "The dashboard crashes"     | WF11 (incident) if production, WF3 (bug fix) if dev only |
| "Update Node.js version"    | WF8 (dependency update) — runtime is a dependency        |
| "Check for vulnerabilities" | WF9 (security audit)                                     |
| "This needs a redesign"     | If behavior changes: WF2. If no behavior change: WF4     |

---

## 9. Principle Coverage Summary

### 9.1 Full Matrix

| Principle | WF1 | WF2 | WF3 | WF4 | WF7 | WF8 | WF9 | WF10 | WF11 |
| --------- | --- | --- | --- | --- | --- | --- | --- | ---- | ---- |
| P1        | —   | ✓   | ✓   | ✓   | ✓   | ✓   | ✓   | ✓    | ✓    |
| P2        | —   | ✓   | ✓   | ✓   | ✓   | ✓   | ✓   | ✓    | ~    |
| P3        | —   | ✓   | ✓   | ✓   | ✓   | ✓   | ✓   | ✓    | ✓    |
| P4        | —   | ✓   | ✓   | ✓   | ✓   | ✓   | ✓   | ✓    | ~    |
| P5        | —   | ✓   | ✓   | ✓   | —   | ✓   | ✓   | ✓    | ✓    |
| P6        | —   | ✓   | ✓   | ✓   | ✓   | ✓   | ✓   | ✓    | ✓    |
| P7        | —   | ✓   | ✓   | ✓   | —   | ✓   | ✓   | ✓    | ~    |
| P8        | ✓   | ✓   | ✓   | ✓   | ✓   | ✓   | ✓   | ✓    | ✓    |
| P9        | C   | ✓   | C   | ✓   | C   | C   | ✓   | ✓    | ✓    |
| P10       | ✓   | ✓   | —   | —   | —   | —   | —   | C    | —    |
| P11       | ✓   | ✓   | ✓   | ✓   | ✓   | ✓   | ✓   | ✓    | ✓    |
| P12       | ✓   | ✓   | ✓   | ✓   | ✓   | ✓   | ✓   | ✓    | ✓    |
| P13       | —   | ✓   | ✓   | ✓   | —   | ✓   | ✓   | ✓    | ~    |
| P14       | —   | ✓   | ✓   | ✓   | ✓   | ✓   | ✓   | ✓    | ✓    |

**Legend:** ✓ = Fully enforced | C = Conditional | ~ = Relaxed during active phase | — = N/A (justified)

### 9.2 Justified Relaxations

| Principle | Workflow | Justification                                               |
| --------- | -------- | ----------------------------------------------------------- |
| P1-P7     | WF1      | Issue creation produces no code — branching/testing N/A     |
| P5, P7    | WF7      | Documentation produces no executable code                   |
| P10       | WF3-WF11 | Diagrams only for architectural changes, not all workflows  |
| P2, P4    | WF11     | Formatting and remote sync relaxed during active incident   |
| P7        | WF11     | Triple-gate abbreviated for hotfixes (speed > thoroughness) |
| P13       | WF11     | Abbreviated review for hotfixes                             |
| P13       | WF7      | User reviews docs directly; formal code review not needed   |

---

## 10. Loop-Back Budget Summary

| Workflow | Budget                                                                                                           | Global Cap   |
| -------- | ---------------------------------------------------------------------------------------------------------------- | ------------ |
| WF1      | Step 3 → Step 2: max 2 iterations                                                                                | 2            |
| WF2      | Step 4 → Step 3: max 2; Step 8/11 → Step 3: max 1 each                                                           | 3            |
| WF3      | Step 4 → Step 3: max 1; Step 9 → Step 3: max 1                                                                   | 2            |
| WF4      | Step 4 → Step 3: max 2; Step 9 → Step 7b: max 1                                                                  | 3            |
| WF7      | Step 6 → Step 3: no fixed limit (user-driven)                                                                    | ∞ (user)     |
| WF8      | Step 5: max 2 per update; Step 9: max 2                                                                          | 4            |
| WF9      | Step 5 → Step 3: max 2 iterations                                                                                | 2            |
| WF10     | Step 5 → Step 4: max 2 iterations                                                                                | 2            |
| WF11     | Phase A: Step 5 → Step 3 (if stabilization fails); Phase B: Step 9 → Step 7 (RCA reflect), bounded by escalation | 2/escalation |

---

## 11. Token Cost Estimation

Approximate token costs for each workflow execution (based on WF1/WF2 benchmarks):

| Workflow | Estimated Input | Estimated Output | Notes                                        |
| -------- | --------------- | ---------------- | -------------------------------------------- |
| WF1      | ~35K-60K        | ~100K-150K       | Heavy brainstorming + critique sub-agents    |
| WF2      | ~50K-80K        | ~150K-250K       | Full design + critique + 4-agent review      |
| WF3      | ~25K-40K        | ~60K-100K        | Simplified path, reflect instead of critique |
| WF4      | ~35K-55K        | ~80K-140K        | Characterization tests add analysis overhead |
| WF7      | ~15K-25K        | ~30K-50K         | Lightest workflow — docs only                |
| WF8      | ~20K-35K        | ~50K-80K         | Audit tools do heavy lifting, less LLM work  |
| WF9      | ~45K-70K        | ~120K-200K       | STRIDE is thorough; critique on audit itself |
| WF10     | ~40K-65K        | ~100K-180K       | Profiling analysis + critique + benchmarking |
| WF11     | ~30K-50K        | ~70K-120K        | Phase A is fast; Phase B adds RCA overhead   |

---

## 12. Tooling Summary

### 12.1 Official Claude Code Tools (used across workflows)

| Tool               | Used By            | Purpose                        |
| ------------------ | ------------------ | ------------------------------ |
| Glob               | All                | File search                    |
| Grep               | All                | Content search                 |
| Read               | All                | File reading                   |
| Edit               | WF2-WF11           | File editing                   |
| Write              | WF2-WF11           | File creation                  |
| Bash               | All                | Shell commands                 |
| Agent (sub-agents) | WF1-WF4, WF9-WF10  | Parallel judge panels, reviews |
| WebSearch          | WF1, WF2, WF8, WF9 | Library docs, CVE lookups      |

### 12.2 MCP Tools

| MCP Server | Used By            | Purpose                               |
| ---------- | ------------------ | ------------------------------------- |
| Serena     | WF2-WF4, WF9-WF10  | Symbol-level code navigation          |
| Context7   | WF2, WF8           | Library documentation lookup          |
| mem0       | WF2, WF4, WF9-WF11 | Cross-project memory persistence      |
| Excalidraw | WF1, WF2           | Diagram creation (when MCP available) |

### 12.3 Plugin Skills

| Skill                             | Used By                  | Purpose                        |
| --------------------------------- | ------------------------ | ------------------------------ |
| `/reflexion:critique`             | WF1, WF2, WF4, WF9, WF10 | Full 3-judge critique          |
| `/reflexion:reflect`              | WF3, WF7, WF11           | Lightweight drift check        |
| `/reflexion:memorize`             | All (conditional)        | Curate insights to CLAUDE.md   |
| `/superpowers:brainstorming`      | WF1, WF2, WF10           | Design pipeline                |
| `/superpowers:writing-plans`      | WF2, WF10                | Implementation plan generation |
| `/superpowers:executing-plans`    | WF2, WF10                | Plan execution                 |
| `/commit-commands:commit-push-pr` | WF2-WF4, WF8-WF11        | PR creation pipeline           |
| `/code-review:code-review`        | WF2, WF4, WF10           | 4-agent code review            |

### 12.4 PR Review Toolkit Agents

| Agent                 | Used By       | Purpose                      |
| --------------------- | ------------- | ---------------------------- |
| type-design-analyzer  | WF2, WF4      | Type/interface design review |
| silent-failure-hunter | WF2, WF3      | Error handling review        |
| code-simplifier       | WF2, WF4      | Code clarity review          |
| code-reviewer         | WF2-WF4, WF10 | Style and best practices     |

---

## 13. Implementation Dependency Graph

The workflows and their supporting infrastructure should be implemented in this order:

### Tier 0: Foundation (no workflow dependencies)

- Phase 2 principles in CLAUDE.md
- GitHub issue templates (`.github/ISSUE_TEMPLATE/`)
- Reflexion plugin skills (critique, reflect, memorize)

### Tier 1: Core Workflows (depends on Tier 0)

- **WF1** — Issue Creation (standalone, no code-producing deps)
- **WF7** — Documentation (simplest code-producing workflow)

### Tier 2: Primary Workflows (depends on Tier 0 + Tier 1)

- **WF2** — Feature Implementation (the reference workflow)
- **WF3** — Bug Fix (simplified WF2)

### Tier 3: Specialized Workflows (depends on Tier 2)

- **WF4** — Refactoring
- **WF8** — Dependency Update
- **WF9** — Security Audit
- **WF10** — Performance Optimization
- **WF11** — Incident Response

### Tier 4: Meta-Infrastructure

- Workflow selector meta-skill (routes to appropriate workflow)
- Cross-workflow reporting (dashboard of workflow runs, patterns)

---

## 14. Architecture Diagram

**File:** `.claude/framework-build/diagrams/framework-architecture.excalidraw`

The architecture diagram shows:

1. User intent entering the Workflow Selector
2. Selector routing to one of 9 workflows
3. Each workflow as a horizontal swim lane with its step archetypes
4. Shared base protocol highlighted (branch, PR, CI, merge, deploy)
5. Escalation paths as arrows between workflows
6. Quality gate types (critique, reflect, audit) marked per workflow
7. Principle enforcement points marked on shared archetypes

---

## 15. Design Decisions

### D1: Shared Base as Documentation, Not Code Abstraction

**Decision:** The shared base protocol (Section 2) is documented as a reference pattern, NOT implemented as a shared code module that workflows inherit from.

**Rationale:** Each workflow's skill file is a self-contained markdown document. Claude Code reads the entire skill file into context when invoked. Making workflows reference a separate "base.md" file would require an extra file read and make the skill harder to understand in isolation. Duplication across skill files is acceptable because: (a) the duplicated parts are simple (branch creation, PR creation), (b) each workflow may need slight variations, and (c) skill files are maintained by Claude, not humans.

### D2: No Downward Escalation

**Decision:** Workflows do NOT automatically downgrade (e.g., WF2 → WF3 when the feature turns out to be a simple bug fix).

**Rationale:** Downward escalation adds complexity for marginal benefit. If a user starts WF2 and the implementation is simple, WF2's fast path already handles it (skip full critique, use reflect). The user can also simply complete WF2 — there's no penalty for using a "bigger" workflow on a small task (just a few extra quality gates that take seconds). Upward escalation (WF3 → WF2) is important because it prevents underestimating complexity; downward escalation is not because overestimating has low cost.

### D3: WF3 Remains Separate from WF2

**Decision:** Despite the Solution Architect's recommendation to merge WF3 into WF2, WF3 remains a separate workflow.

**Rationale:** WF3's reproduce-first TDD pattern is fundamentally different from WF2's standard TDD. The mental model is different: "fix a known bug" vs. "build a new feature." Separate invocation (`/fix-bug` vs `/implement-feature`) gives the user clear semantic intent. The 2-step difference (14 vs 16) is not redundant — it's the removal of plan drift check and separate memorize step, which are genuinely unnecessary for bug fixes.

### D4: Workflow Selector is a Future Enhancement

**Decision:** The workflow selector (Section 8) is documented as a decision tree but NOT implemented as a skill in Phase 6.

**Rationale:** Users already know which skill to invoke. The selector adds value when onboarding new team members or when intent is ambiguous, but the framework works without it. Phase 6 implements the 9 core workflow skills; the selector can be added in a future phase.

### D5: Token Estimates are Rough Approximations

**Decision:** Token estimates (Section 11) are based on WF1/WF2 benchmarks and extrapolation. They are NOT measured for WF3-WF11.

**Rationale:** Actual token costs depend on codebase size, issue complexity, number of sub-agents spawned, and critique depth. The estimates provide order-of-magnitude guidance for cost planning. Actual costs will be measured during Phase 6 implementation and Phase 7 validation.

---

## 16. Open Questions for Phase 5

1. **Skill file format:** Should all 9 workflow skills use the same skill file template (header, instructions, step loop), or should each be fully custom?
2. **Shared step functions:** Should common operations (branch creation, PR creation, deploy) be extracted into a shared utility skill that other skills call?
3. **Workflow telemetry:** Should workflow runs be logged to a structured file (JSON) for later analysis of time-per-step, token cost, escalation frequency?
4. **CI integration:** Should the GitHub Actions workflow (`test.yml`) be extended with workflow-specific test suites (e.g., security-specific tests for WF9)?
5. **Tooling audits:** Should WF3-WF11 get individual tooling audits (like WF1/WF2), or is the consolidated tooling summary (Section 12) sufficient for implementation specs?
