# rawgentic

**9 battle-tested SDLC workflows for Claude Code**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Claude Code Plugin](https://img.shields.io/badge/Claude%20Code-Plugin-purple)](https://docs.anthropic.com/en/docs/claude-code)

---

## What is Rawgentic?

Claude Code is powerful but unstructured. Complex tasks — building features, fixing bugs, running security audits — need consistent quality gates, test-driven development, and deployment verification. Without guardrails, it's easy to skip code review, forget to run CI, or merge without testing.

**Rawgentic** provides 9 workflow skills that enforce 14 software engineering principles (P1-P14). Each workflow is a multi-step guided process with quality gates, code review, CI verification, and deployment. Workflows share a common set of 12 step archetypes (A-L) that standardize how Claude Code handles everything from receiving scope to post-deploy verification.

**Philosophy:**

- **Reproduce-first TDD** — Write failing tests before fixing code
- **Shift-left critique** — Quality gates run BEFORE implementation, not after
- **Conventional commits** — `<type>(scope): <desc>` matching branch prefix
- **Triple-gate testing** — Local tests, CI (GitHub Actions), post-deploy E2E

---

## Quick Start

```bash
# Install the plugin
claude plugin add github:3D-Stories/rawgentic

# Configure your project (first-time setup)
/rawgentic:setup

# Start using workflows
/rawgentic:fix-bug 42
```

---

## Prerequisites

| Requirement                      | Check                        | Install                                                         |
| -------------------------------- | ---------------------------- | --------------------------------------------------------------- |
| Claude Code CLI                  | `claude --version`           | [Install guide](https://docs.anthropic.com/en/docs/claude-code) |
| GitHub CLI                       | `gh auth status`             | `brew install gh` / [gh install](https://cli.github.com/)       |
| Git repository                   | `git status`                 | `git init`                                                      |
| reflexion plugin                 | `/reflexion:reflect`         | `claude plugin add reflexion@context-engineering-kit`           |
| superpowers plugin (recommended) | `/superpowers:brainstorming` | `claude plugin add superpowers@claude-plugins-official`         |

---

## Workflows

| Workflow                 | Skill                          | Steps | Use When                                            |
| ------------------------ | ------------------------------ | ----- | --------------------------------------------------- |
| Issue Creation           | `/rawgentic:create-issue`      | 9     | Planning a feature or reporting a bug               |
| Feature Implementation   | `/rawgentic:implement-feature` | 16    | Building a new feature from a GitHub issue          |
| Bug Fix                  | `/rawgentic:fix-bug`           | 14    | Fixing a bug with reproduce-first TDD               |
| Refactoring              | `/rawgentic:refactor`          | 15    | Restructuring code while preserving behavior        |
| Documentation            | `/rawgentic:update-docs`       | 10    | Creating or updating project documentation          |
| Dependency Update        | `/rawgentic:update-deps`       | 12    | Updating npm/pip/Docker dependencies                |
| Security Audit           | `/rawgentic:security-audit`    | 14    | STRIDE threat modeling and vulnerability assessment |
| Performance Optimization | `/rawgentic:optimize-perf`     | 15    | Benchmark-driven performance improvements           |
| Incident Response        | `/rawgentic:incident`          | 14    | Production incident: stabilize first, then RCA      |

<details>
<summary><strong>Issue Creation (WF1)</strong> — 9 steps</summary>

**Purpose:** Create well-structured GitHub issues (feature requests or bug reports) through brainstorming, multi-agent critique, and user review. Ensures issues have clear acceptance criteria, scope boundaries, and labels.

**Invocation:** `/rawgentic:create-issue Add dark mode support to the dashboard`

**Steps:** Receive scope, brainstorm, critique (3-judge panel), apply fixes (max 2 loops), memorize insights, user review, create issue, completion summary.

**Quality Gates:** Full critique via `/reflexion:critique` after brainstorming. Volume thresholds: >5 Critical OR >5 High OR >10 Medium OR >10 Low triggers redesign.

**Key Features:**

- 3-judge critique panel (architecture, security, maintainability)
- Ambiguity circuit breaker — stops and asks when findings conflict
- Always terminates after issue creation (never auto-transitions to WF2)
</details>

<details>
<summary><strong>Feature Implementation (WF2)</strong> — 16 steps</summary>

**Purpose:** Take a GitHub issue and implement it end-to-end: codebase analysis, design, TDD, code review, PR creation, CI verification, deployment, and post-deploy testing.

**Invocation:** `/rawgentic:implement-feature 155` or `/rawgentic:implement-feature https://github.com/org/repo/issues/155`

**Steps:** Parse issue, analyze codebase, design solution, critique design, create branch, TDD implementation (write tests, write code, verify), code review (4-agent panel), create PR, CI verification (max 2 fix cycles), merge + deploy, post-deploy E2E, completion.

**Quality Gates:** Full critique after design. 4-agent code review (general, security, performance, test coverage) before PR.

**Key Features:**

- Global loopback budget of 3 across all retry loops
- TDD: tests written before implementation code
- Squash merge with conventional commit title
</details>

<details>
<summary><strong>Bug Fix (WF3)</strong> — 14 steps</summary>

**Purpose:** Fix bugs using a reproduce-first methodology. Ensures the bug is reproducible with a failing test before any fix is attempted.

**Invocation:** `/rawgentic:fix-bug 42` or `/rawgentic:fix-bug Login fails when password contains special characters`

**Steps:** Receive report, reproduce bug (write failing test), root cause analysis, reflect on RCA, create branch, implement fix (make test pass), regression check, code review, create PR, CI verification, merge + deploy, post-deploy verify, completion.

**Quality Gates:** `/reflexion:reflect` after RCA (not full critique — speed matters for bugs).

**Key Features:**

- Reproduce-first: failing test MUST exist before any code changes
- Complexity escalation: if fix touches 10+ files, upgrades to WF2
- Focused code review (not full 4-agent panel)
</details>

<details>
<summary><strong>Refactoring (WF4)</strong> — 15 steps</summary>

**Purpose:** Restructure code to improve maintainability, readability, or performance without changing external behavior.

**Invocation:** `/rawgentic:refactor Extract the validation logic from UserController into a shared service`

**Steps:** Receive scope, classify refactoring type (rename/extract/restructure/move), analyze code structure, design approach, critique (full for extract/restructure, reflect for rename/move), create branch, implement with behavior preservation tests, code review, create PR, CI, merge + deploy, post-deploy, completion.

**Quality Gates:** Category-based — full critique for extract/restructure, reflect only for rename/move.

**Key Features:**

- Behavior preservation is the primary constraint
- Classification drives critique level (simple renames skip full critique)
- Reference tracking via `find_referencing_symbols` ensures nothing breaks
</details>

<details>
<summary><strong>Documentation (WF7)</strong> — 10 steps</summary>

**Purpose:** Create or update project documentation with accuracy verification against the actual codebase.

**Invocation:** `/rawgentic:update-docs Update the API reference for the analytics endpoints`

**Steps:** Receive scope, analyze existing docs + code, draft documentation, reflect on draft, create branch, write docs, verify accuracy against code, create PR, merge + deploy, completion.

**Quality Gates:** `/reflexion:reflect` after drafting.

**Key Features:**

- Code-verified: documentation claims are checked against actual source code
- Supports API references, guides, README updates, architecture docs
</details>

<details>
<summary><strong>Dependency Update (WF8)</strong> — 12 steps</summary>

**Purpose:** Update npm, pip, or Docker dependencies safely with automated vulnerability checks and compatibility verification.

**Invocation:** `/rawgentic:update-deps` or `/rawgentic:update-deps Update React to v19`

**Steps:** Receive scope, audit current dependencies (`npm audit` / `pip-audit`), plan updates, create branch, apply updates, run tests, check for breaking changes, focused code review, create PR, CI verification, merge + deploy, completion.

**Quality Gates:** Audit-based (no critique/reflect). `npm audit` + test suite + CI are the gates.

**Key Features:**

- Automated vulnerability scanning before and after updates
- Breaking change detection via test suite comparison
- Separate handling for security patches (fast track) vs major upgrades
</details>

<details>
<summary><strong>Security Audit (WF9)</strong> — 14 steps</summary>

**Purpose:** Comprehensive security assessment using STRIDE threat modeling, covering all data channels (REST, WebSocket, Redis, internal APIs).

**Invocation:** `/rawgentic:security-audit` or `/rawgentic:security-audit Focus on the authentication system`

**Steps:** Receive scope, enumerate attack surfaces (STRIDE), analyze each surface, generate findings report, critique the audit findings, create branch, implement remediations, code review (security-focused), create PR, CI, merge + deploy, post-deploy security verify, completion.

**Quality Gates:** Full critique — but applied to the audit findings, not the design.

**Key Features:**

- STRIDE threat model (Spoofing, Tampering, Repudiation, Info Disclosure, DoS, Elevation)
- Multi-channel audit: REST API, WebSocket, Redis pub/sub, internal HTTP APIs
- Severity-based prioritization with evidence requirements
</details>

<details>
<summary><strong>Performance Optimization (WF10)</strong> — 15 steps</summary>

**Purpose:** Benchmark-driven performance improvements. Measure before optimizing, verify improvements with numbers.

**Invocation:** `/rawgentic:optimize-perf Reduce dashboard load time` or `/rawgentic:optimize-perf Database queries are slow on the analytics page`

**Steps:** Receive scope, establish baseline benchmarks, profile bottlenecks, design optimization, full critique of design, create branch, implement optimization, measure improvement, code review, create PR, CI, merge + deploy, post-deploy benchmark, completion.

**Quality Gates:** Full critique after optimization design.

**Key Features:**

- Benchmark-first: baseline measurements required before any changes
- Improvement measured quantitatively (before vs after)
- Prevents premature optimization (must justify with profiling data)
</details>

<details>
<summary><strong>Incident Response (WF11)</strong> — 14 steps, 2 phases</summary>

**Purpose:** Two-phase incident handling: Phase A restores service rapidly (relaxed principles), Phase B conducts thorough root cause analysis and implements preventive measures.

**Invocation:** `/rawgentic:incident Dashboard is not loading` or `/rawgentic:incident 203`

**Phase A (Stabilize — Steps 1-6):** Receive incident, rapid diagnosis, determine strategy, execute stabilization, verify restoration, stabilization summary.

**Phase B (Analyze & Prevent — Steps 7-14):** 5 Whys RCA, design permanent fix, critique RCA, implement fix, preventive measures, action items, memorize pattern, closure.

**Quality Gates:** None in Phase A (speed over perfection). `/reflexion:reflect` in Phase B after RCA.

**Key Features:**

- Fix first, analyze later — Phase A prioritizes uptime
- Severity classification (SEV-1 through SEV-4) drives response urgency
- Incident patterns memorized for future prevention
- Phase A can hotfix directly to main; Phase B follows full workflow
</details>

---

## How It Works

### 14 Principles (P1-P14)

| ID  | Name                    | Summary                                                                  |
| --- | ----------------------- | ------------------------------------------------------------------------ |
| P1  | Branch Isolation        | Every workflow gets its own branch; parallel work uses git worktrees     |
| P2  | Code Formatting         | Auto-format before every commit (Prettier for JS, Black for Python)      |
| P3  | Frequent Local Commits  | Commit after each meaningful change; never lose more than 15 min of work |
| P4  | Regular Remote Sync     | Push to remote at every natural checkpoint                               |
| P5  | TDD Enforcement         | Write tests before implementation; variant per workflow type             |
| P6  | Main-to-Dev Sync        | After merge to main, deploy to dev immediately                           |
| P7  | Triple-Gate Testing     | Gate 1: local tests, Gate 2: CI, Gate 3: post-deploy E2E                 |
| P8  | Shift-Left Critique     | Quality gates run BEFORE implementation, not after                       |
| P9  | Continuous Memorization | Curate reusable insights into CLAUDE.md                                  |
| P10 | Diagram-Driven Design   | Create/update diagrams for architectural changes                         |
| P11 | User-in-the-Loop Gates  | User approval required before merge; ambiguity stops and asks            |
| P12 | Conventional Commit     | `<type>(scope): <desc>` — type matches branch prefix                     |
| P13 | Pre-PR Code Review      | Code review BEFORE creating PR, not after                                |
| P14 | Documentation-Gated PRs | PR must document what changed, why, and how to test                      |

### Step Archetypes (A-L)

All workflows share 12 common step patterns:

- **A. Receive Scope** — Parse user intent, classify, confirm
- **B. Analyze Context** — Codebase analysis, identify affected files
- **C. Design/Plan** — Solution design with brainstorming for complex tasks
- **D. Quality Gate** — Critique or reflect per workflow type
- **E. Create Branch** — `git checkout -b <prefix>/<desc> origin/main`
- **F. Implement (TDD)** — Core work with test-first development
- **G. Code Review** — Pre-PR review, scope calibrated per workflow
- **H. Create PR** — `gh pr create` with conventional commit title
- **I. CI Verification** — GitHub Actions, max 2 fix-and-retry cycles
- **J. Merge + Deploy** — Squash merge + deploy to dev
- **K. Post-Deploy Verify** — Health endpoints, E2E tests, Docker logs
- **L. Completion Summary** — Update session notes, close issue, present summary

### Quality Gate Strategy

| Workflow                   | Critique Level     | Gate                  | When                         |
| -------------------------- | ------------------ | --------------------- | ---------------------------- |
| WF1 Issue Creation         | Full critique      | `/reflexion:critique` | After brainstorming          |
| WF2 Feature Implementation | Full critique      | `/reflexion:critique` | After design                 |
| WF3 Bug Fix                | Reflect only       | `/reflexion:reflect`  | After RCA                    |
| WF4 Refactoring            | Category-based     | Full or Reflect       | Full for extract/restructure |
| WF7 Documentation          | Reflect only       | `/reflexion:reflect`  | After draft                  |
| WF8 Dependency Update      | None (audit-based) | `npm audit` + tests   | Automated                    |
| WF9 Security Audit         | Full (on audit)    | `/reflexion:critique` | Critique the findings        |
| WF10 Performance           | Full critique      | `/reflexion:critique` | After optimization design    |
| WF11 Incident              | Phase-dependent    | `/reflexion:reflect`  | Phase B only                 |

### Shared Invariants

1. **Ambiguity Circuit Breaker** — STOP and ask user when findings conflict
2. **Finding Auto-Application** — Apply ALL quality gate findings automatically
3. **Workflow Resumption** — Checkpoint artifacts for mid-workflow recovery
4. **Session Notes** — Continuous documentation in `claude_docs/session_notes.md`
5. **Commit Convention** — `<type>(scope): <description>` matching branch prefix
6. **Branch from main** — All branches from `origin/main`
7. **Squash merge** — All PRs use `gh pr merge --squash`
8. **Deploy to dev only** — Workflows deploy to dev; production is outside scope
9. **Context compaction protocol** — Document state before context compaction

---

## Configuration

### How `/rawgentic:setup` Works

The setup wizard runs once per project to configure your environment:

1. **Check prerequisites** — Verifies Claude Code CLI, `gh`, Git repo, required plugins
2. **Detect project config** — Scans for tech stack (package.json, requirements.txt, Dockerfile), CI config, test commands
3. **Generate CLAUDE.md section** — Reads `templates/claude-md-sdlc-section.md`, fills in detected values, asks for anything it can't auto-detect
4. **Verify** — Confirms all `${CONSTANT}` references resolve correctly

### Constants

The setup wizard populates these project-specific values in your CLAUDE.md:

| Constant                | Example                         | Source                          |
| ----------------------- | ------------------------------- | ------------------------------- |
| `${REPO}`               | `org/repo-name`                 | `git remote get-url origin`     |
| `${PROJECT_ROOT}`       | `/home/user/project`            | `git rev-parse --show-toplevel` |
| `${DEV_HOST}`           | `192.168.1.100`                 | User-provided                   |
| `${ENGINE_HOST}`        | `192.168.1.101`                 | User-provided                   |
| `${DASHBOARD_PORT}`     | `3000`                          | Detected or user-provided       |
| `${ENGINE_API_PORT}`    | `8080`                          | Detected or user-provided       |
| `${DB_NAME}`            | `myapp_dev`                     | Detected or user-provided       |
| `${DB_USER}`            | `myapp_dev`                     | Detected or user-provided       |
| `${POSTGRES_CONTAINER}` | `myapp-postgres-dev`            | `docker compose config`         |
| `${COMPOSE_INFRA}`      | `docker-compose.infra.dev.yml`  | Detected                        |
| `${COMPOSE_ENGINE}`     | `docker-compose.engine.dev.yml` | Detected                        |

### How Constants Work

Constants use prompt engineering, not template substitution. When Claude reads a skill that references `${REPO}`, it looks up the value from your CLAUDE.md's Project Constants section and mentally substitutes it. No runtime variable binding occurs.

---

## Troubleshooting

| Problem                          | Cause                                              | Solution                                                      |
| -------------------------------- | -------------------------------------------------- | ------------------------------------------------------------- |
| Quality gate blocks workflow     | reflexion plugin not installed                     | `claude plugin add reflexion@context-engineering-kit`         |
| `${REPO}` not resolved           | Setup not run                                      | Run `/rawgentic:setup` to populate CLAUDE.md constants        |
| Workflow resumes at wrong step   | Context compacted mid-workflow                     | Re-invoke the skill — resumption protocol detects state       |
| CI verification hangs            | `gh pr checks` doesn't work with fine-grained PATs | Skills use `gh run list` instead (already handled)            |
| Ambiguity circuit breaker fires  | Quality gate found conflicting findings            | Expected — review findings, tell Claude how to proceed        |
| Tests fail in unfamiliar project | Test commands not configured                       | Run `/rawgentic:setup` to detect your test commands           |
| Workflow upgrades to WF2         | Bug fix classified as complex (10+ files)          | Expected — complex bugs get the full feature workflow         |
| Context runs out mid-workflow    | Long workflow exceeded context window              | Skills document state before compaction — re-invoke to resume |

---

## Design Documentation

The `docs/` directory contains detailed design documentation for contributors:

- **[Principles](docs/principles.md)** — P1-P14 definitions with rationale and enforcement mechanisms
- **[Consolidation](docs/consolidation.md)** — Step archetype mappings, shared protocol, principle coverage matrix
- **Design documents** (in `docs/design/`):
  - [Issue Creation (WF1)](docs/design/workflow-issue-creation.md)
  - [Feature Implementation (WF2)](docs/design/workflow-feature-implementation.md)
  - [Bug Fix (WF3)](docs/design/workflow-bug-fix.md)
  - [Refactoring (WF4)](docs/design/workflow-refactoring.md)
  - [Documentation (WF7)](docs/design/workflow-documentation.md)
  - [Dependency Update (WF8)](docs/design/workflow-dependency-update.md)
  - [Security Audit (WF9)](docs/design/workflow-security-audit.md)
  - [Performance Optimization (WF10)](docs/design/workflow-performance-optimization.md)
  - [Incident Response (WF11)](docs/design/workflow-incident-response.md)
- **Diagrams** (in `diagrams/`): Excalidraw visual diagrams for each workflow and the framework architecture

---

## Contributing

Contributions are welcome. To get started:

1. Fork the repository
2. Create a feature branch: `git checkout -b feat/your-feature`
3. Make your changes following the conventional commit format
4. Submit a pull request

For major changes, please open an issue first to discuss the approach.

---

## License

[MIT](LICENSE)
