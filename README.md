# rawgentic

**10 SDLC workflow skills + 3 workspace management + 1 security skill + hooks for Claude Code**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Claude Code Plugin](https://img.shields.io/badge/Claude%20Code-Plugin-purple)](https://docs.anthropic.com/en/docs/claude-code)

---

## What is Rawgentic?

Claude Code is powerful but unstructured. Complex tasks — building features, fixing bugs, running security audits — need consistent quality gates, test-driven development, and deployment verification. Without guardrails, it's easy to skip code review, forget to run CI, or merge without testing.

**Rawgentic** provides 14 skills organized in three layers:

- **Workspace management** (3 skills) — Project registration, configuration, and session binding
- **SDLC workflows** (10 skills) — Multi-step guided processes with quality gates, code review, CI verification, and deployment
- **Security & infrastructure** (1 skill + hooks) — Security pattern syncing, dangerous pattern blocking, per-project WAL logging, session binding enforcement, and cross-project file guards

All workflow skills share a **config-loading protocol** that reads project configuration from `.rawgentic.json` — no hardcoded constants, no CLAUDE.md templates, no filesystem probing.

**Philosophy:**

- **Config-driven** — All project details come from `.rawgentic.json`, not guesswork
- **Reproduce-first TDD** — Write failing tests before fixing code
- **Shift-left critique** — Quality gates run BEFORE implementation, not after
- **Conventional commits** — `<type>(scope): <desc>` matching branch prefix
- **Triple-gate testing** — Local tests, CI (GitHub Actions), post-deploy E2E

---

## Quick Start

```bash
# Install the plugin
claude plugin add github:3D-Stories/rawgentic

# Create a workspace directory and cd into it
mkdir my-org-workspace && cd my-org-workspace

# Register and configure your first project
# (setup runs automatically — scaffolds workspace CLAUDE.md on first run)
/rawgentic:new-project my-app

# Start using workflows
/rawgentic:implement-feature 155
/rawgentic:fix-bug 42
```

`/rawgentic:new-project` creates the workspace, clones or initializes a repo, scaffolds the workspace CLAUDE.md (Layer 2), and automatically runs `/rawgentic:setup` to detect your tech stack and generate `.rawgentic.json`.

### Workspace Architecture

Rawgentic uses a **three-layer CLAUDE.md hierarchy** to separate personal, workspace, and project concerns:

| Layer | File | Scope | In Git? |
|-------|------|-------|---------|
| 1 — Personal | `~/.claude/CLAUDE.md` | Developer preferences, per machine | No |
| 2 — Workspace | `{cwd-root}/CLAUDE.md` | GitHub org, PAT, team process | No |
| 3 — Project | `projects/{name}/CLAUDE.md` | Project-specific instructions | Yes |

**Key principles:**
- **Projects are standalone** — Layer 3 works without rawgentic
- **Rawgentic is additive** — removing it only affects Layer 2
- **One workspace per org** — GitHub credentials scoped to workspace
- **No absolute paths** — portable across machines

The `setup` skill scaffolds Layer 2 on first run. The `new-project` skill audits Layer 3 for conformance.

See `docs/plans/2026-03-06-plugin-overhaul-design.md` for the full design.

---

## Prerequisites

| Requirement                      | Check                        | Install                                                         |
| -------------------------------- | ---------------------------- | --------------------------------------------------------------- |
| Claude Code CLI                  | `claude --version`           | [Install guide](https://docs.anthropic.com/en/docs/claude-code) |
| GitHub CLI                       | `gh auth status`             | `brew install gh` / [gh install](https://cli.github.com/)       |
| Git repository                   | `git status`                 | `git init`                                                      |
| jq (JSON processor)              | `jq --version`               | `apt install jq` / `brew install jq`                           |
| reflexion plugin                 | `/reflexion:reflect`         | `claude plugin add reflexion@context-engineering-kit`           |
| superpowers plugin (recommended) | `/superpowers:brainstorming` | `claude plugin add superpowers@claude-plugins-official`         |

---

## Skills

### Workspace Management

| Skill                       | Purpose                                              |
| --------------------------- | ---------------------------------------------------- |
| `/rawgentic:new-project`    | Register a new or existing project in the workspace  |
| `/rawgentic:setup`          | Auto-detect tech stack and generate `.rawgentic.json` |
| `/rawgentic:switch`         | Bind this session to a project, list projects, or deactivate |

### SDLC Workflows

| Workflow                 | Skill                          | Steps | Use When                                            |
| ------------------------ | ------------------------------ | ----- | --------------------------------------------------- |
| Issue Creation           | `/rawgentic:create-issue`      | 9     | Planning a feature or reporting a bug               |
| Feature Implementation   | `/rawgentic:implement-feature` | 16    | Building a new feature from a GitHub issue          |
| Bug Fix                  | `/rawgentic:fix-bug`           | 14    | Fixing a bug with reproduce-first TDD               |
| Refactoring              | `/rawgentic:refactor`          | 14    | Restructuring code while preserving behavior        |
| Documentation            | `/rawgentic:update-docs`       | 10    | Creating or updating project documentation          |
| Dependency Update        | `/rawgentic:update-deps`       | 12    | Updating npm/pip/Docker dependencies                |
| Security Audit           | `/rawgentic:security-audit`    | 14    | STRIDE threat modeling and vulnerability assessment |
| Performance Optimization | `/rawgentic:optimize-perf`     | 15    | Benchmark-driven performance improvements           |
| Incident Response        | `/rawgentic:incident`          | 14    | Production incident: stabilize first, then RCA      |
| Test Suite Creation      | `/rawgentic:create-tests`      | 14    | Bootstrap tests or fill coverage gaps across any language |

<details>
<summary><strong>Issue Creation (WF1)</strong> — 9 steps</summary>

**Purpose:** Create well-structured GitHub issues through brainstorming, multi-agent critique, and user review.

**Invocation:** `/rawgentic:create-issue Add dark mode support to the dashboard`

**Key Features:**
- 3-judge critique panel (architecture, security, maintainability)
- Ambiguity circuit breaker — stops and asks when findings conflict
- Duplicate detection via `gh issue list` before creation
</details>

<details>
<summary><strong>Feature Implementation (WF2)</strong> — 16 steps</summary>

**Purpose:** Take a GitHub issue and implement it end-to-end with TDD, code review, and deployment.

**Invocation:** `/rawgentic:implement-feature 155`

**Key Features:**
- Config-driven: TDD mode when tests configured, Implement-Verify mode when not
- 4-agent code review (general, security, performance, test coverage)
- Global loopback budget of 3 across all retry loops
- Learning config: updates `.rawgentic.json` when new patterns discovered
</details>

<details>
<summary><strong>Bug Fix (WF3)</strong> — 14 steps</summary>

**Purpose:** Fix bugs using reproduce-first methodology with a failing test before any fix.

**Invocation:** `/rawgentic:fix-bug 42`

**Key Features:**
- Reproduce-first: failing test MUST exist before code changes
- Complexity escalation: 10+ file fixes upgrade to WF2
- Verify mode when no test framework configured
</details>

<details>
<summary><strong>Refactoring (WF4)</strong> — 14 steps</summary>

**Purpose:** Restructure code while preserving external behavior.

**Invocation:** `/rawgentic:refactor Extract validation logic into a shared service`

**Key Features:**
- Characterization tests written before refactoring
- Category-based critique (full for extract/restructure, reflect for rename/move)
- Behavioral preservation as primary constraint
</details>

<details>
<summary><strong>Documentation (WF7)</strong> — 10 steps</summary>

**Purpose:** Create or update documentation with accuracy verification against the codebase.

**Invocation:** `/rawgentic:update-docs Update the API reference`

**Key Features:**
- Accuracy-first: only documents facts verified against source code
- Uses `primaryFiles` from config, not hardcoded paths
- Learning config: adds newly discovered doc files to config
</details>

<details>
<summary><strong>Dependency Update (WF8)</strong> — 12 steps</summary>

**Purpose:** Update dependencies safely with security-first ordering.

**Invocation:** `/rawgentic:update-deps` or `/rawgentic:update-deps Update React to v19`

**Key Features:**
- Security-first ordering: CRITICAL > HIGH > MEDIUM before non-security updates
- Automated vulnerability scanning before and after
- Learning config: updates tech stack versions in `.rawgentic.json`
</details>

<details>
<summary><strong>Security Audit (WF9)</strong> — 14 steps</summary>

**Purpose:** STRIDE threat modeling across all data channels.

**Invocation:** `/rawgentic:security-audit` or `/rawgentic:security-audit Focus on authentication`

**Key Features:**
- STRIDE model (Spoofing, Tampering, Repudiation, Info Disclosure, DoS, Elevation)
- Enumerates data channels from config (REST, WebSocket, gRPC, etc.)
- Learning config: adds discovered auth mechanisms to config
</details>

<details>
<summary><strong>Performance Optimization (WF10)</strong> — 15 steps</summary>

**Purpose:** Benchmark-driven performance improvements.

**Invocation:** `/rawgentic:optimize-perf Reduce dashboard load time`

**Key Features:**
- Benchmark-first: baseline measurements required before changes
- Uses profiling tools from config (clinic-js, py-spy, etc.)
- Quantitative improvement verification
</details>

<details>
<summary><strong>Incident Response (WF11)</strong> — 14 steps, 2 phases</summary>

**Purpose:** Two-phase incident handling: stabilize first, then root cause analysis.

**Invocation:** `/rawgentic:incident Dashboard is not loading`

**Key Features:**
- Phase A (stabilize): speed over perfection, relaxed quality gates
- Phase B (RCA): 5 Whys analysis, preventive measures, pattern memorization
- SEV-1 through SEV-4 classification drives response urgency
</details>

<details>
<summary><strong>Test Suite Creation (WF12)</strong> — 14 steps</summary>

**Purpose:** Bootstrap a test suite from scratch or audit existing tests and fill coverage gaps, for any language.

**Invocation:** `/rawgentic:create-tests` or `/rawgentic:create-tests src/auth/`

**Key Features:**
- Two modes: greenfield (no tests — full bootstrap) and coverage-gap (audit + fill gaps)
- Language-agnostic: supports Python, JS/TS, Go, Rust, C/C++, Shell/Bash, Ruby, PHP, Java, Kotlin, Swift
- Uses `superpowers:brainstorming` to design testing strategy before writing any code
- Uses context7 MCP for up-to-date framework documentation
- Shell script testing via bats-core with PATH-based mocking
- Multi-language projects get per-language test infrastructure + unified runner
- Learning config: updates `.rawgentic.json` with discovered testing frameworks
</details>

### Security & Infrastructure

| Skill | Purpose |
|-------|---------|
| `/rawgentic:sync-security-patterns` | Merge upstream security patterns from Anthropic's official plugin into local config |

#### Hooks

Rawgentic includes hooks that run automatically on Claude Code events:

| Hook | Event | Purpose |
|------|-------|---------|
| `wal-pre` | PreToolUse | Logs INTENT before mutation tools execute |
| `wal-post` | PostToolUse | Logs DONE after successful execution |
| `wal-post-fail` | PostToolUseFailure | Logs FAIL after failed execution |
| `wal-stop` | Stop | Logs session end marker |
| `wal-context` | UserPromptSubmit | Injects session context (project, recent WAL activity) |
| `wal-bind-guard` | PreToolUse | Blocks tool use if session unbound with multiple active projects; blocks cross-project file writes |
| `wal-guard` | PreToolUse | Blocks dangerous bash commands (rm -rf, DROP TABLE, etc.) |
| `session-start` | SessionStart | WAL recovery, session notes archival, project reconciliation, resume context |
| `security-guard` | PreToolUse | Blocks writing dangerous patterns (credentials, secrets, eval) to files |
| `security-guard-check` | SessionStart | Warns if the official security-guidance plugin conflicts |

**Security Guard** blocks writes containing dangerous patterns (API keys, hardcoded credentials, eval/exec, SQL injection vectors). Patterns are defined in `hooks/security-patterns.json` with per-project exceptions in `.rawgentic.json`. Uses the Claude Code `permissionDecision: deny` protocol for hard blocking (not retried).

**WAL (Write-Ahead Log)** records every mutation tool call to `claude_docs/wal/{project}.jsonl`. On session resume, incomplete operations are surfaced for recovery. WAL files are per-project — each active project gets its own log.

### Multi-Project Concurrent Sessions

Multiple Claude Code sessions can work on different projects simultaneously from the same workspace root.

**How it works:**
- Multiple projects can have `active: true` in `.rawgentic_workspace.json`
- Each session is **bound** to a project via `claude_docs/session_registry.jsonl`
- WAL logs are per-project: `claude_docs/wal/{project}.jsonl`
- The `wal-bind-guard` hook enforces binding and prevents cross-project file writes

**Session binding cascade:**
1. Session already in registry → use that project
2. Exactly one active project → auto-bind
3. Multiple active projects → prompt user to run `/rawgentic:switch <name>`

**Cross-project protection:** If session A is bound to `chorestory` and tries to write a file under `projects/rawgentic/`, the `wal-bind-guard` hook denies the operation.

**Directory reconciliation:** On startup/resume, the `session-start` hook checks that all active projects' directories exist on disk. Missing directories are deactivated and the user is prompted to remove or re-setup.

---

## Configuration

### Project Config: `.rawgentic.json`

Every project gets a `.rawgentic.json` file generated by `/rawgentic:setup`. This structured config replaces the old CLAUDE.md constants approach — all workflow skills read from this file instead of probing the filesystem.

```json
{
  "version": 1,
  "project": {
    "name": "my-app",
    "type": "webapp",
    "description": "Dashboard for analytics"
  },
  "repo": {
    "provider": "github",
    "fullName": "org/my-app",
    "defaultBranch": "main"
  },
  "techStack": [
    { "name": "typescript", "version": "5.3.3" },
    { "name": "react", "version": "18.2.0" },
    { "name": "node", "version": "20.11.0" }
  ],
  "testing": {
    "framework": "vitest",
    "command": "npx vitest run --reporter=verbose",
    "coverageCommand": "npx vitest run --coverage"
  },
  "ci": {
    "provider": "github-actions",
    "configPath": ".github/workflows/ci.yml"
  }
}
```

The full schema is at `templates/rawgentic-json-schema.json`. Sections include: `project`, `repo`, `techStack`, `testing`, `ci`, `deploy`, `database`, `infrastructure`, `documentation`, `formatting`.

### Workspace File: `.rawgentic_workspace.json`

Tracks all registered projects. Created automatically by `/rawgentic:new-project`:

```json
{
  "version": 1,
  "projectsDir": "./projects",
  "projects": [
    {
      "name": "my-app",
      "path": "./projects/my-app",
      "active": true,
      "lastUsed": "2026-03-06T12:00:00Z",
      "configured": true
    }
  ]
}
```

**Multi-project:** Multiple projects can be `active: true` simultaneously. The `active` flag means the project is provisioned and available — it does not mean "this is the current session's project." Session-to-project binding is tracked in `claude_docs/session_registry.jsonl`.

### Config-Loading Protocol

All 10 workflow skills share an identical config-loading block that runs before any workflow step:

1. Read `.rawgentic_workspace.json` → find active project (if multiple are active, stop and prompt user to `/rawgentic:switch`)
2. Read `<project-path>/.rawgentic.json` → validate version
3. Build `capabilities` object (has_tests, has_ci, has_deploy, etc.)
4. All subsequent steps use config and capabilities — never probe the filesystem

This means skills adapt automatically: TDD mode when tests are configured, Implement-Verify mode when they're not. No capability detection, no guessing.

### Learning Config

During workflow execution, skills may discover new information about the project (a new auth mechanism, an updated dependency version, a new documentation file). The **learning-config protocol** updates `.rawgentic.json` safely:

- **Append** to arrays (don't replace existing entries)
- **Set** null/missing fields (don't overwrite existing values)
- Always read-modify-write (never patch in place)

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
| WF12 Test Suite Creation   | Brainstorm-driven  | `/superpowers:brainstorming` | Before writing any tests |

### Shared Invariants

1. **Config-loading protocol** — All workflow skills read `.rawgentic.json` before executing
2. **Ambiguity Circuit Breaker** — STOP and ask user when findings conflict
3. **Finding Auto-Application** — Apply ALL quality gate findings automatically
4. **Workflow Resumption** — Checkpoint artifacts for mid-workflow recovery
5. **Session Notes** — Continuous documentation in `claude_docs/session_notes/<project>.md` (auto-created by setup/new-project, auto-registered by WAL hooks)
6. **Commit Convention** — `<type>(scope): <description>` matching branch prefix
7. **Branch from default** — All branches from `origin/<defaultBranch>` (read from config)
8. **Squash merge** — All PRs use `gh pr merge --squash`
9. **Deploy to dev only** — Workflows deploy to dev; production is outside scope

---

## Troubleshooting

| Problem                          | Cause                                         | Solution                                                      |
| -------------------------------- | --------------------------------------------- | ------------------------------------------------------------- |
| "No rawgentic workspace found"   | First-time user                                | Run `/rawgentic:new-project` to register your first project   |
| "Config missing — run setup"     | Project registered but not configured          | Run `/rawgentic:setup` on the active project                  |
| Config version mismatch          | `.rawgentic.json` has wrong version            | Run `/rawgentic:setup` to regenerate config                   |
| Quality gate blocks workflow     | reflexion plugin not installed                 | `claude plugin add reflexion@context-engineering-kit`         |
| Workflow resumes at wrong step   | Context compacted mid-workflow                 | Re-invoke the skill — resumption protocol detects state       |
| CI verification hangs            | `gh pr checks` doesn't work with fine-grained PATs | Skills use `gh run list` instead (already handled)        |
| Ambiguity circuit breaker fires  | Quality gate found conflicting findings        | Expected — review findings, tell Claude how to proceed        |
| Workflow upgrades to WF2         | Bug fix classified as complex (10+ files)      | Expected — complex bugs get the full feature workflow         |
| Context runs out mid-workflow    | Long workflow exceeded context window          | Skills document state before compaction — re-invoke to resume |
| Stop hook blocks every response  | Session not registered with real session ID    | Fixed in v2.3.0 — `wal-context` auto-registers on first prompt |
| "Multiple projects active"       | Multiple projects have `active: true`         | Run `/rawgentic:switch <name>` to bind this session            |
| "Session isn't bound to a project" | Unbound session with multiple active projects | Run `/rawgentic:switch <name>` to bind                        |
| Cross-project write denied       | File path is in a different project            | Switch first with `/rawgentic:switch <target>`, or ask user   |
| Hooks fail after `cd` in Bash    | CWD drifted to project subdir, workspace file not found | Fixed in v2.5.3 — hooks now traverse up to 5 levels |
| Hook errors after plugin update  | Session still references old cache path        | Exit session, reinstall plugin, start new session              |

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
  - [Test Suite Creation (WF12)](docs/design/workflow-test-suite-creation.md)
  - [Security Guard](docs/plans/2026-03-07-security-guard-design.md)
  - [Multi-Project Concurrent Sessions](docs/plans/2026-03-07-multi-project-sessions-design.md)
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
