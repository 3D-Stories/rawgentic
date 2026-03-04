## SDLC Workflow Principles

### P1-P14 Quick Reference

| ID  | Name                    | Summary                                                                                                 |
| --- | ----------------------- | ------------------------------------------------------------------------------------------------------- |
| P1  | Branch Isolation        | Every workflow gets its own branch; parallel workflows use git worktrees                                |
| P2  | Code Formatting         | Auto-format before every commit (Prettier for JS, Black for Python)                                     |
| P3  | Frequent Local Commits  | Commit after each meaningful change; never lose more than 15 min of work                                |
| P4  | Regular Remote Sync     | Push to remote at every natural checkpoint (after tests pass, after review)                             |
| P5  | TDD Enforcement         | Write tests before implementation; variant per workflow type                                            |
| P6  | Main-to-Dev Sync        | After merge to main, deploy to dev immediately via `${DEPLOY_COMMAND}`                                  |
| P7  | Triple-Gate Testing     | Gate 1: local tests, Gate 2: CI (GitHub Actions), Gate 3: post-deploy E2E                               |
| P8  | Shift-Left Critique     | Quality gates (critique/reflect) run BEFORE implementation, not after                                   |
| P9  | Continuous Memorization | Curate reusable insights into CLAUDE.md via `/reflexion:memorize`                                       |
| P10 | Diagram-Driven Design   | Create/update Excalidraw diagrams for architectural changes                                             |
| P11 | User-in-the-Loop Gates  | User approval required before merge; ambiguity circuit breaker stops and asks                           |
| P12 | Conventional Commit     | `<type>(scope): <desc>` — type matches branch prefix (feat/fix/refactor/docs/deps/perf/security/hotfix) |
| P13 | Pre-PR Code Review      | Code review BEFORE creating PR, not after; scope calibrated per workflow                                |
| P14 | Documentation-Gated PRs | PR description must document what changed, why, and how to test                                         |

### Critique Strategy Matrix

| Workflow | Critique Level     | Gate Command          | When                          |
| -------- | ------------------ | --------------------- | ----------------------------- |
| WF1      | Full critique      | `/reflexion:critique` | After brainstorming           |
| WF2      | Full critique      | `/reflexion:critique` | After design, before impl     |
| WF3      | Reflect only       | `/reflexion:reflect`  | After RCA, before fix         |
| WF4      | Category-based     | Full or Reflect       | Full for extract/restructure  |
| WF7      | Reflect + user     | `/reflexion:reflect`  | After draft, before apply     |
| WF8      | None (audit-based) | npm audit + tests     | Automated verification        |
| WF9      | Full (on audit)    | `/reflexion:critique` | Critique the audit findings   |
| WF10     | Full critique      | `/reflexion:critique` | After optimization design     |
| WF11     | Reflect (on RCA)   | `/reflexion:reflect`  | Phase B only; Phase A = speed |

### Shared Base Protocol — Step Archetypes (A-L)

All code-producing workflows share these step patterns:

- **A. Receive Scope** — Parse user intent, classify, confirm. P11 enforced.
- **B. Analyze Context** — Codebase analysis via Serena/Context7/Grep. Identify affected files, deps, tests.
- **C. Design/Plan** — Design generation. `/superpowers:brainstorming` for complex, inline for simple.
- **D. Quality Gate** — Critique or reflect per matrix above. Some workflows skip (WF8, WF11 Phase A).
- **E. Create Branch** — `git checkout -b <prefix>/<desc> origin/main`. P1 enforced.
- **F. Implement (TDD)** — Core work. TDD variant per workflow. P5, P3 enforced.
- **G. Code Review** — Pre-PR review. Full 4-agent (WF2/WF4/WF10), focused (WF3/WF8), abbreviated (WF11).
- **H. Create PR** — `gh pr create`. Conventional commit title. P12, P14 enforced.
- **I. CI Verification** — GitHub Actions. Max 2 fix-and-retry cycles. P7 Gate 2.
- **J. Merge + Deploy** — `gh pr merge --squash` + `${DEPLOY_COMMAND}`. P6, P7 Gate 3 start.
- **K. Post-Deploy Verify** — Health endpoints, E2E tests, Docker logs. P7 Gate 3 completion.
- **L. Completion Summary** — Update session notes, close issue, present summary. P14.

### Shared Invariants (All Code-Producing Workflows)

1. **Ambiguity Circuit Breaker**: STOP and ask user when findings are ambiguous or conflicting.
2. **Finding Auto-Application**: Apply ALL quality gate findings automatically. No severity filtering.
3. **Workflow Resumption**: Checkpoint artifacts + step detection algorithms for mid-workflow recovery.
4. **Session Notes**: Continuous documentation in `claude_docs/session_notes.md`.
5. **Commit Convention**: `<type>(scope): <description>` matching branch prefix.
6. **Branch from main**: All branches from `origin/main` (not feature branches).
7. **Squash merge**: All PRs use `gh pr merge --squash`.
8. **Deploy to dev only**: Workflows deploy to dev environments. Production is outside workflow scope.
9. **Context compaction protocol**: Before compaction, document current step, quality gate state, branch name, last commit SHA.

---

## Project Constants (generated by /rawgentic:setup)

REPO = "${REPO}"
PROJECT_ROOT = "${PROJECT_ROOT}"
DEV_HOST = "${DEV_HOST}"           # Optional — only if remote deployment
ENGINE_HOST = "${ENGINE_HOST}" # Optional — only if multi-VM
DB_NAME = "${DB_NAME}"             # Optional — only if database
DB_USER = "${DB_USER}" # Optional
POSTGRES_CONTAINER = "${POSTGRES_CONTAINER}"  # Optional — only if Docker
COMPOSE_INFRA = "${COMPOSE_INFRA}" # Optional
COMPOSE_ENGINE = "${COMPOSE_ENGINE}" # Optional
DASHBOARD_PORT = "${DASHBOARD_PORT}" # Optional — dashboard/frontend port
ENGINE_API_PORT = "${ENGINE_API_PORT}" # Optional — engine/backend API port
SMB_USER = "${SMB_USER}" # Optional — only if SMB shares

## Test Commands (detected by /rawgentic:setup)

# Unit tests:

# ${TEST_COMMAND_UNIT}

# E2E tests (if applicable):

# ${TEST_COMMAND_E2E}

## Deploy Commands (if applicable)

# ${DEPLOY_COMMAND}
