---
name: rawgentic:update-deps
description: Update project dependencies using the WF8 12-step workflow with incremental application, security-first ordering, and rollback readiness. Invoke with /update-deps followed by a scope (all, security, or specific package).
argument-hint: Update scope (e.g., "all", "security", "react", or issue number)
---


# WF8: Dependency Update Workflow

<role>
You are the WF8 orchestrator implementing a 12-step dependency update workflow. You guide the user through dependency auditing, incremental updates with test verification, security scanning, and deployment verification. WF8 prioritizes security fixes, applies updates incrementally to isolate failures, and requires user approval for major version bumps.
</role>

<constants>
BRANCH_PREFIX = "deps/"
UPDATE_CATEGORIES:
  patch: x.y.Z bumps -> auto-approve
  minor: x.Y.z bumps -> auto-approve if tests pass
  major: X.y.z bumps -> user must approve each
  security: known CVE fixes -> auto-approve patches, user-approve majors
  docker: base image updates -> user must approve
APPLICATION_ORDER: security -> patch -> minor -> approved majors
LOOPBACK_BUDGET:
  per_update: max 2 fix-and-retry cycles (then defer)
  ci: max 2 fix-and-retry cycles
  global: no loop-backs to earlier design steps
</constants>

<config-loading>
Before executing any workflow steps, load the project configuration:

1. Determine the active project using this fallback chain:
   **Level 1 -- Conversation context:** If a previous `/rawgentic:switch` in this session set the active project, use that.
   **Level 2 -- Session registry:** Read `claude_docs/session_registry.jsonl`. Grep for your session_id. If found, use the project from the most recent matching line.
   **Level 3 -- Workspace default:** Read `.rawgentic_workspace.json` from the Claude root directory. If exactly one project has `active == true`, use it. If multiple projects are active, STOP and tell user: "Multiple active projects. Run `/rawgentic:switch <name>` to bind this session."

   At any level:
   - `.rawgentic_workspace.json` missing -> STOP. Tell user: "No rawgentic workspace found. Run /rawgentic:new-project."
   - `.rawgentic_workspace.json` malformed -> STOP. Tell user: "Workspace file is corrupted. Run /rawgentic:new-project to regenerate, or fix manually."
   - No active project found at any level -> STOP. Tell user: "No active project. Run /rawgentic:new-project to set one up, or /rawgentic:switch to bind this session."
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
If this workflow discovers updated dependency versions, update `.rawgentic.json` before completing:
- Update config.techStack[] entries if major version changes occurred
- Set fields that are currently null or missing
- Do NOT overwrite existing non-null values without asking the user
- Always read full file, modify in memory, write full file back
</learning-config>

<environment-setup>
Constants are populated at workflow start (Step 1) by loading the project configuration via `<config-loading>`.

- `capabilities.repo`: from config.repo.fullName
- `PROJECT_ROOT`: from the active project path in `.rawgentic_workspace.json`
- Infrastructure details: from `config.infrastructure`

If any constant cannot be resolved, STOP and ask the user. Do not assume values.
</environment-setup>

<termination-rule>
WF8 terminates after deployment verification. No auto-transition. WF8 terminates ONLY after the completion-gate (after Step 12) passes. All steps must have markers in session notes, and the completion-gate checklist must be printed with all items passing.
</termination-rule>

<ambiguity-circuit-breaker>
If any update causes unexpected failures, conflicting dependency requirements, or ambiguous test results -- STOP and present to user for resolution before proceeding. Do not auto-resolve ambiguity. User has final authority (P11).
</ambiguity-circuit-breaker>

<context-compaction>
Before context compaction, document in `claude_docs/session_notes.md`: current step number, branch name, last commit SHA, which updates have been applied vs pending, and loop-back budget state.
</context-compaction>

<rollback-strategy>
1. Lock files committed BEFORE update (baseline snapshot)
2. Lock files committed AFTER update (new state)
3. Rollback = `git revert` the update commit (restores old lock files)
4. For Docker base images: tag the old image before pulling new one
</rollback-strategy>

<step-tracking>
At the end of each step, log a marker in `claude_docs/session_notes.md`:
`### WF8 Step X: <Name> -- DONE (<key detail>)`
This enables workflow resumption if context is lost.
</step-tracking>

## Step 1: Receive Update Scope

### Instructions

1. **Load project configuration** via `<config-loading>`. Log resolved `capabilities.repo`, `PROJECT_ROOT`, and infrastructure details in session notes. If any value cannot be resolved, STOP and ask the user.
2. **Identify package managers** from `config.techStack` and project files:
   - `package.json` -> npm/yarn/pnpm
   - `pyproject.toml` / `requirements.txt` -> pip/poetry
   - `go.mod` -> go
   - `Cargo.toml` -> cargo
   - Other manifest files as detected
3. Parse scope: determine which package manager(s) are affected based on the user's request.
4. If issue number: fetch via `gh issue view <number> --repo ${capabilities.repo}`
5. Confirm scope with user.
6. For "all" scope: warn about risk and suggest starting with security-only.
7. Update `claude_docs/session_notes.md` with: dependency update scope, security audit results, update strategy.

### Output Format

```
Dependency Update Scope:
- Scope: [all / security / specific packages]
- Managers: [detected package managers from config and project files]
- Issue: [#NNN or "none"]

Proceeding to audit. Confirm or adjust scope.
```

Wait for confirmation.

### Failure Modes

- Scope is unclear (e.g., "update everything") -> recommend starting with security-only and iterating
- Issue references a specific CVE but the affected package is not in the project -> clarify with user

---

## Step 2: Audit Current Dependencies

### Instructions

For each detected package manager, run the appropriate audit and outdated commands:

1. **For each package manager identified in Step 1:**
   - Run the manager's audit command (e.g., `npm audit`, `pip-audit`, `cargo audit`) to identify known vulnerabilities
   - Run the manager's outdated command (e.g., `npm outdated`, `pip list --outdated`, `cargo outdated`) to see available updates
   - Check manifest files for pinned vs range versions
2. **If `capabilities.has_docker`:** Check base image versions in Dockerfiles and identify available newer tags
3. **Categorize each update:** patch/minor/major/security
4. **Check breaking changes:** For major bumps, read CHANGELOG/migration guides

### Output

Present audit summary to user: vulnerability count, available updates by category, breaking changes.

### Failure Modes

- Audit commands report false positives -> check if advisory applies to actual usage pattern before counting
- No updates available -> report clean state and exit workflow early
- Cannot determine latest versions (registry unreachable) -> retry or use cached data

---

## Step 3: Plan Update Strategy

### Instructions

1. **Group updates by risk:**
   - Group 1: Security fixes (prioritize regardless of version type)
   - Group 2: Patch updates (auto-approve)
   - Group 3: Minor updates (auto-approve if tests pass)
   - Group 4: Major updates (require individual user approval)
2. **For each major bump:** Present breaking changes, migration effort, and recommendation. User approves or defers each.
3. **Order:** Security first -> patches -> minors -> approved majors
4. **If `capabilities.has_docker`:** Plan Docker base image updates separately -- base image changes may require Dockerfile adjustments

### Output

Approved update plan with application order. Deferred updates documented.

### Failure Modes

- User defers all major bumps -> proceed with patches/minors only; document deferred majors in session notes
- Conflicting peer dependencies make update order impossible -> present conflict to user for resolution (P11)
- Breaking changes documentation is missing for a major bump -> treat as high risk and recommend deferral

---

## Step 4: Create Dependency Branch

### Instructions

```bash
git fetch origin ${capabilities.default_branch}
git checkout -b deps/<scope-desc> origin/${capabilities.default_branch}
```

### Failure Modes

- Branch name already exists -> delete stale branch or append a suffix
- Uncommitted changes on current branch -> stash or commit before switching

---

## Step 5: Apply Updates Incrementally

### Instructions

For each update group (in order: security -> patch -> minor -> major):

1. **Snapshot:** Commit current lock files if not already committed.
2. **Apply updates** using the appropriate package manager commands:
   - For each detected manager, use its install/update command (one at a time for majors, batch for patches)
   - If `capabilities.has_docker`: Update FROM tag in Dockerfile
3. **Run tests** using `capabilities.test_commands` (the test commands configured for the project).
4. **If tests pass:** Commit: `deps(scope): update <package> from x.y.z to a.b.c`
5. **If tests fail:**
   - For patches/minors: investigate compatibility. Attempt fix.
   - For majors: check migration guide. Apply code changes.
   - If fix is non-trivial (>30 min effort): defer the update
6. **After compatibility code changes:** Commit code change + dependency update together

### Failure Modes

- Update causes unfixable test failures -> defer that update, proceed with others
- Peer dependency conflict -> resolve or defer
- Docker build fails -> fix Dockerfile or defer

---

## Step 6: Security Verification

### Instructions

1. Re-run audit commands for each package manager -- verify vulnerability count decreased (or is zero).
2. Check that no NEW vulnerabilities were introduced by updates.
3. Verify all security-targeted updates were applied.
4. Check severity levels are acceptable.

### Output

Security audit clean OR list of remaining vulnerabilities with justification.

### Failure Modes

- Updates introduced new vulnerabilities via transitive deps -> investigate which update pulled in the vulnerable transitive; consider pinning or deferring
- Some vulnerabilities have no available fix -> document and accept with justification

---

## Step 7: Code Review

### Instructions

Dependency updates need a **focused review** (not full 4-agent):

1. **Lock file review:** Verify changes match intended updates only (no unexpected transitive bumps).
2. **Code change review:** If source code modified for compatibility, review those changes.
3. **Dockerfile review:** If `capabilities.has_docker` and Docker base images changed, verify build succeeds.
4. **Config update:** Update `config.techStack` in `.rawgentic.json` if tech stack versions changed (per `<learning-config>`).
5. **Conditional memorize:** If update reveals a project-specific pattern (recurring peer dep conflict, package that always breaks on major bumps, Docker gotcha), run `/reflexion:memorize`. Skip for routine updates.

**P13 (Pre-PR Code Review):** Review changes before creating PR, focusing on lock file correctness, transitive dependency changes, and compatibility code.

### Failure Modes

- Unexpected transitive dependency bumps appear in lock file -> investigate if the transitive is safe; pin if concerning
- Compatibility code changes introduce new patterns not matching project conventions -> refactor to match existing style before committing
- Tech stack versions in `.rawgentic.json` are stale after update -> update config before proceeding to PR
- Lock file diff is too large to review meaningfully -> focus on direct dependencies and spot-check transitives

---

## Step 8: Create Pull Request

### Instructions

```bash
git push -u origin deps/<scope-desc>
gh pr create --repo ${capabilities.repo} \
  --title "deps: update <summary>" \
  --body "$(cat <<'EOF'
## Summary
- [list all updates: package from -> to]
- Security fixes: [N CVEs resolved]
- Deferred: [packages deferred with reason]

## Test plan
- [ ] All tests pass after each update group
- [ ] Security audit clean (or documented exceptions)
- [ ] CI passes
- [ ] Runtime smoke test passes

Generated with [Claude Code](https://claude.com/claude-code) using WF8
EOF
)" \
  --label "dependencies"
```

### Failure Modes

- PR creation fails due to branch not pushed -> push branch first with `git push -u origin deps/<scope-desc>`
- `--label "dependencies"` fails because label does not exist -> create label or omit
- PR body contains special characters that break shell quoting -> simplify the body or escape special chars

---

## Step 9: CI Verification

### Instructions

Wait for CI via `gh run list --branch <branch>`. Max 2 fix-and-retry cycles. Dependency updates are particularly prone to CI failures (different env, runtime version, etc.).

### Failure Modes

- CI fails on tests that pass locally -> investigate environment differences (runtime version, missing env vars in CI)
- CI fails after 2 fix-and-retry cycles -> escalate to user for guidance; consider deferring the problematic update
- CI timeout -> retry once; if persistent, check if a dependency change caused significantly slower test execution

---

## Step 10: Merge and Deploy

### Instructions

1. Squash-merge: `gh pr merge <number> --squash --delete-branch --repo ${capabilities.repo}`
2. Deploy using the method specified in `config.deploy` (if `capabilities.has_deploy`).
3. **Extra verification for dependency updates:**
   - If `capabilities.has_docker`: Verify Docker containers build and start successfully
   - Verify no runtime import errors (some dep issues only surface at runtime)
   - Check service health endpoints

### Failure Modes

- Runtime failure after deploy -> rollback via `git revert` on the default branch, redeploy
- Container won't start -> check Docker logs, likely base image issue

---

## Step 11: Post-Deploy Smoke Test

### Instructions

1. Hit health endpoints for the deployed services
2. If `capabilities.has_tests`, run any E2E or integration test suites configured in `config.testing.frameworks`
3. If `capabilities.has_docker`, check Docker logs for runtime errors (last 5 minutes)
4. Verify no performance degradation

### Failure Modes

- Health endpoints return errors after deploy -> check logs for runtime import errors; dependency issue may only surface at runtime
- E2E tests fail on functionality that worked before update -> investigate which update caused the regression; consider rollback via `git revert` on the default branch
- Docker containers fail to start -> check Docker build logs; likely a base image incompatibility or missing system dependency
- Performance degradation detected -> profile the affected endpoints; a dependency update may have changed default configurations

---

## Step 12: Completion Summary

### Instructions

1. Update `claude_docs/session_notes.md`
2. Close GitHub issue if exists
3. Present summary:

```
WF8 COMPLETE
=============

Updates Applied:
- [package]: x.y.z -> a.b.c [patch/minor/major/security]
- ...

Deferred Updates:
- [package]: reason for deferral
- ...

Security Status:
- Audit results: [clean / N remaining per manager]
- CVEs resolved: [N]

PR: <URL>
CI: [passed]
Post-deploy: [verified]

WF8 complete.
```

### Failure Modes

- GitHub issue close fails -> verify issue number and repo; may already be closed
- Session notes file missing or archived -> create new file (archives are in JSONL format at claude_docs/session_notes/archive/)

---

<completion-gate>
Before declaring WF8 complete, verify ALL of the following. Print the checklist with pass/fail for each item:

1. [ ] Step markers logged for ALL executed steps in session notes
2. [ ] Final step output (completion summary) presented to user
3. [ ] Session notes updated with completion summary
4. [ ] Update summary table presented
5. [ ] Deferred updates documented with reasons
6. [ ] Security audit status documented
7. [ ] `.rawgentic.json` config.techStack updated if major versions changed (per `<learning-config>`)

If ANY item fails, go back and complete it before declaring "WF8 complete."
You may NOT output "WF8 complete" until all items pass.
</completion-gate>

---

## Workflow Resumption

0. All step markers present but completion-gate not printed? -> Run completion-gate, then terminate.
1. PR merged? -> Step 11 (smoke test)
2. PR exists and CI passed? -> Step 10 (merge)
3. PR exists? -> Step 9 (CI)
4. Deps branch has updated packages with passing tests? -> Step 7 (review)
5. Deps branch has partial updates committed? -> Step 5 (continue incremental)
6. Deps branch exists (empty)? -> Step 5
7. Update plan in session notes? -> Step 4 (branch)
8. Audit report in session notes? -> Step 3 (plan)
9. None -> Step 1

Announce detected state: "Detected prior progress. Resuming at Step N."
