# Phase 3 Workflow: Dependency Update (v1.0)

**Date:** 2026-03-02
**Author:** Orchestrator (direct authoring)
**Inputs:** phase2-principles.md, phase2b-official-comparison.md
**Purpose:** Define the workflow for updating project dependencies (npm packages, Python packages, Docker base images, system tools). WF8 prioritizes security scanning, compatibility verification, and rollback readiness.

---

## Workflow: Dependency Update

**Invocation:** `/update-deps <scope>` skill (custom Claude Code skill)
**Trigger:** User invokes `/update-deps "update all npm packages"` or `/update-deps <issue-number>` or `/update-deps security` (security-only updates)
**Inputs:**

- Update scope: "all", "security", specific package name(s), or GitHub issue
- Existing lock files (package-lock.json, requirements.txt/pyproject.toml)
- Current test suite results (baseline)
- Runtime environment access

**Outputs:**

- Merged PR with updated dependencies
- Updated lock files
- Passing test suite proving compatibility
- Deployed and verified in dev environment

**Tracking:** GitHub issue (if exists) or session notes.

**Principles enforced:**

- P1: Branch Isolation (deps/ branch)
- P2: Code Formatting
- P3: Frequent Local Commits
- P4: Regular Remote Sync
- P5: TDD Enforcement (tests prove compatibility)
- P6: Main-to-Dev Sync
- P7: Triple-Gate Testing
- P8: Shift-Left Critique (security audit + breaking change analysis)
- P11: User-in-the-Loop (approve major version bumps)
- P12: Conventional Commit Discipline
- P13: Pre-PR Code Review (focused review for dependency changes)
- P14: Documentation-Gated PRs

**Diagram:** `diagrams/workflow-dependency-update.excalidraw`

**Termination:** WF8 terminates after deployment verification. No auto-transition.

---

## Update Categories

| Category     | Scope              | Risk   | Approval                                  |
| ------------ | ------------------ | ------ | ----------------------------------------- |
| **Patch**    | x.y.Z bumps only   | Low    | Auto-approve                              |
| **Minor**    | x.Y.z bumps        | Medium | Auto-approve if tests pass                |
| **Major**    | X.y.z bumps        | High   | User must approve each                    |
| **Security** | Known CVE fixes    | Varies | Auto-approve patches, user-approve majors |
| **Docker**   | Base image updates | High   | User must approve                         |

---

## Dependency Audit Strategy

Before updating, WF8 audits the dependency landscape:

1. **Vulnerability scan:** `npm audit` (dashboard), `pip-audit` (engine if available), or manual CVE check
2. **Outdated inventory:** `npm outdated` (dashboard), `pip list --outdated` (engine)
3. **Breaking change analysis:** For each major bump, check CHANGELOG/release notes for breaking changes
4. **Compatibility matrix:** Verify peer dependency requirements won't conflict

---

## Rollback Strategy

Dependency updates are high-risk because failures may only surface in production-like environments. WF8 requires:

1. Lock files committed BEFORE update (baseline snapshot)
2. Lock files committed AFTER update (new state)
3. Rollback = `git revert` the update commit (restores old lock files)
4. For Docker base images: tag the old image before pulling new one

---

## Finding Application and Ambiguity Circuit Breaker

**Inherited from WF2:** Auto-apply findings. Circuit breaker on ambiguity (especially for breaking changes that could affect production).

---

## Global Loop-Back Budget

- Step 5 (incremental update): max 2 fix-and-retry cycles per failing update (then defer that update)
- Step 9 (CI): max 2 fix-and-retry cycles
- **Global cap: No loop-backs to earlier design steps.** Dependency updates don't have a design/critique cycle — failures are addressed by deferring the problematic update and proceeding with others.

---

## Workflow Resumption

**Checkpoint artifacts:**

| Artifact      | Location                       | Created at   | Purpose         |
| ------------- | ------------------------------ | ------------ | --------------- |
| Deps branch   | Git (remote)                   | Step 4       | Change state    |
| Audit report  | Session notes                  | Step 2       | Baseline        |
| Lock files    | Git (committed)                | Step 5       | Rollback target |
| PR            | GitHub (remote)                | Step 8       | Review state    |
| Session notes | `claude_docs/session_notes.md` | Continuously | Progress log    |

**Step detection on resume:**

1. PR merged? → Step 11
2. PR exists and CI passed? → Step 10
3. PR exists? → Step 9
4. Deps branch has updated packages with passing tests? → Step 7
5. Deps branch has partial updates committed? → Step 5 (continue incremental)
6. Deps branch exists (empty)? → Step 5
7. Update plan in session notes? → Step 4
8. Audit report in session notes? → Step 3
9. None → Step 1

---

## Steps

### Step 1: Receive Update Scope

**Type:** user decision
**Actor:** human
**Command:** `/update-deps <scope>`
**Input:** Update scope (all/security/specific packages/issue number)
**Action:**

1. Parse scope: determine which package manager(s) affected (npm, pip, docker)
2. If issue: fetch via `gh issue view`
3. Confirm scope with user
4. For "all" scope: warn about risk and suggest starting with security-only

**Output:** Validated scope: { packages, managers, category, issue_number (optional) }
**Principle alignment:** P11

---

### Step 2: Audit Current Dependencies

**Type:** automated
**Actor:** Claude
**Command:** `npm audit`, `npm outdated`, check requirements.txt, check Dockerfiles
**Input:** Scope from Step 1
**Action:**

1. **Dashboard (npm):**
   - Run `npm audit` to identify known vulnerabilities
   - Run `npm outdated` to see available updates
   - Check `package.json` for pinned vs range versions
2. **Engine (pip):**
   - Check `requirements.txt` for outdated packages
   - Cross-reference with PyPI for latest versions
   - Check for known CVEs in current versions
3. **Docker:**
   - Check base image versions in Dockerfiles
   - Identify available newer tags
4. **Categorize each update:** patch/minor/major/security
5. **Check breaking changes:** For major bumps, read CHANGELOG/migration guides

**Output:** Audit report: { vulnerabilities, available_updates[], breaking_changes[], recommendations }
**Failure mode:** (1) `npm audit` reports false positives → check if advisory applies to our usage. (2) No updates available → report clean and exit workflow.

---

### Step 3: Plan Update Strategy

**Type:** user decision
**Actor:** Claude + human (for major bumps)
**Input:** Audit report from Step 2
**Action:**

1. **Group updates by risk:**
   - Group 1: Patch updates (auto-approve)
   - Group 2: Minor updates (auto-approve if tests pass)
   - Group 3: Major updates (require individual user approval)
   - Group 4: Security fixes (prioritize regardless of version type)
2. **For each major bump:** Present breaking changes, migration effort, and recommendation to user. User approves or defers each.
3. **Order of application:** Security first → patches → minors → approved majors
4. **Docker updates:** Plan separately — base image changes may require Dockerfile adjustments

**Output:** Approved update plan: { approved_updates[], deferred_updates[], application_order }
**Principle alignment:** P11 (user approves majors), P8 (shift-left risk analysis)

---

### Step 4: Create Dependency Branch

**Type:** automated
**Actor:** Claude
**Command:** `git checkout -b deps/<scope-desc>`
**Input:** Branch name from plan
**Action:**

1. `git fetch origin main`
2. `git checkout -b deps/<scope-desc> origin/main`

**Output:** Active deps branch
**Principle alignment:** P1

---

### Step 5: Apply Updates Incrementally

**Type:** automated
**Actor:** Claude
**Input:** Approved update plan from Step 3
**Action:**

For each update group (in order: security → patch → minor → major):

1. **Snapshot:** Commit current lock files if not already committed
2. **Apply updates:**
   - npm: `npm install <package>@<version>` (one at a time for majors, batch for patches)
   - pip: Update requirements.txt and run `pip install -r requirements.txt`
   - Docker: Update FROM tag in Dockerfile
3. **Run tests:**
   - Dashboard: `npx vitest run`
   - Engine: `python -m pytest tests/ -v`
4. **If tests pass:** Commit: `deps(scope): update <package> from x.y.z to a.b.c`
5. **If tests fail:**
   - For patches/minors: investigate — likely a real compatibility issue. Attempt to fix.
   - For majors: check migration guide. Apply code changes needed for compatibility.
   - If fix is non-trivial (>30 min effort): defer the update and note in session notes
6. **After code changes for compatibility:** Commit the code change and the dependency update together

**Output:** Updated dependencies committed on branch, all tests passing
**Failure mode:** (1) Update causes test failures that can't be easily resolved → defer that update, proceed with others. (2) Peer dependency conflict → resolve or defer. (3) Docker build fails → fix Dockerfile or defer.
**Principle alignment:** P5 (TDD — tests prove compatibility), P3 (Frequent Commits)

---

### Step 6: Security Verification

**Type:** quality gate
**Actor:** Claude
**Command:** Re-run audit tools
**Input:** Updated dependencies on branch
**Action:**

1. Re-run `npm audit` — verify vulnerability count decreased (or is zero)
2. Check that no NEW vulnerabilities were introduced by updates
3. Verify all security-targeted updates were successfully applied
4. Check that `npm audit` severity levels are acceptable

**Output:** Security audit clean OR list of remaining vulnerabilities with justification
**Failure mode:** (1) Updates introduced new vulnerabilities → investigate transitive deps. (2) Some vulnerabilities unfixable (no patch available) → document and accept.
**Principle alignment:** P8 (Shift-Left)

---

### Step 7: Code Review

**Type:** automated
**Actor:** Claude (focused review, not full 4-agent)
**Command:** Manual review of diff
**Input:** All changes on deps branch
**Action:**

Dependency updates need a focused review, not the full 4-agent suite:

1. **Lock file review:** Verify lock file changes match intended updates only (no unexpected transitive bumps)
2. **Code change review:** If any source code was modified for compatibility, review those changes
3. **Dockerfile review:** If Docker base images changed, verify build succeeds
4. **Documentation:** Update CLAUDE.md if tech stack versions changed
5. **Conditional memorize:** If the update reveals a project-specific pattern (e.g., a recurring peer dependency conflict, a package that always breaks tests on major bumps, or a Docker base image gotcha), run `/reflexion:memorize` to capture the pattern in CLAUDE.md. Skip for routine updates.

**Output:** Reviewed changes + optional CLAUDE.md updates
**Failure mode:** (1) Unexpected transitive dependency appears → investigate if it's safe.
**Principle alignment:** P13, P9 (Conditional Memorization)

---

### Step 8: Create Pull Request

**Type:** automated
**Actor:** Claude
**Command:** `/commit-commands:commit-push-pr`
**Input:** Reviewed changes on deps branch
**Action:**

1. Push: `git push -u origin deps/<scope-desc>`
2. Create PR:
   - Title: `deps: update <summary>`
   - Body: List all updates (from → to), breaking changes addressed, security fixes, test results
   - Labels: dependencies, security (if applicable)

**Output:** PR URL
**Principle alignment:** P12, P14

---

### Step 9: CI Verification (Gate 2)

**Type:** automated
**Actor:** GitHub Actions
**Input:** PR from Step 8
**Action:** Wait for CI. Dependency updates are particularly prone to CI failures (different env, different Node version, etc.). Max 2 fix-and-retry cycles.

**Output:** CI pass
**Principle alignment:** P7 (Gate 2)

---

### Step 10: Merge and Deploy (Gate 3)

**Type:** automated + manual verification
**Actor:** Claude
**Command:** `gh pr merge --squash` + deploy script
**Input:** CI-passing PR
**Action:**

1. Squash-merge
2. Deploy to dev
3. **Extra verification for dependency updates:**
   - Verify Docker containers build and start successfully
   - Verify no runtime import errors (some dep issues only surface at runtime)
   - Check service health endpoints

**Output:** Merged and deployed
**Failure mode:** (1) Runtime failure after deploy → rollback via `git revert` on main, redeploy. (2) Container won't start → check Docker logs, likely a base image issue.
**Principle alignment:** P6, P7 (Gate 3)

---

### Step 11: Post-Deploy Smoke Test

**Type:** quality gate
**Actor:** Claude
**Input:** Deployed dev environment
**Action:**

1. Hit health endpoints (dashboard + engine)
2. Run E2E tests (if scope warrants) on the dev server
3. Check for runtime errors in Docker logs (last 5 minutes)
4. Verify no performance degradation (page load times, API response times)

**Output:** Smoke test pass/fail
**Principle alignment:** P7 (Gate 3 — extra important for deps)

---

### Step 12: Completion Summary

**Type:** automated
**Actor:** Claude
**Input:** All artifacts
**Action:**

1. Update session notes with: updates applied, deferred updates (with reason), security status
2. Close GitHub issue if exists
3. Present summary: what updated, what deferred, security status, PR link

**Output:** Session notes updated
**Principle alignment:** P14

---

## Design Decisions

### D1: Incremental Application (Not Batch)

**Rationale:** Applying all updates at once makes it impossible to identify which update caused a test failure. Incremental application (one group at a time, tests between groups) isolates failures. Patches can be batched (low risk), but majors are applied individually.

### D2: No Full Critique Gate

**Rationale:** Dependency updates don't involve design decisions. The quality gates are: tests pass (behavioral verification), security audit (vulnerability check), and runtime smoke test (integration verification). A 3-judge critique would have nothing meaningful to evaluate.

### D3: Deferred Updates are Acceptable

**Rationale:** Not all updates need to happen now. If a major version bump requires significant code changes, deferring it is often the right call. WF8 documents deferred updates so they can be addressed later (potentially via WF2 if code changes are substantial).

### D4: Docker Updates Treated Separately

**Rationale:** Docker base image updates have a different risk profile than library updates. A new Node.js base image might change system libraries, default configs, or available packages. These changes only surface at build/runtime, not in unit tests. Extra deployment verification is warranted.

### D5: Focused Review (Not 4-Agent)

**Rationale:** The 4-agent code review suite (type-design, silent-failure, simplifier, reviewer) is designed for application code changes. For dependency updates, the review focus is: lock file correctness, transitive dependency safety, and any compatibility code changes. A focused manual review is more appropriate.

---

## Principle Coverage Matrix

| Principle                | Enforced    | How                                             |
| ------------------------ | ----------- | ----------------------------------------------- |
| P1 Branch Isolation      | Yes         | Step 4                                          |
| P2 Code Formatting       | Yes         | Automated in commits                            |
| P3 Frequent Commits      | Yes         | Step 5 (per-group commits)                      |
| P4 Remote Sync           | Yes         | Step 8                                          |
| P5 TDD Enforcement       | Yes         | Step 5 (tests after each group)                 |
| P6 Main-to-Dev Sync      | Yes         | Step 10                                         |
| P7 Triple-Gate           | Yes         | Steps 5 (local), 9 (CI), 11 (smoke)             |
| P8 Shift-Left Critique   | Yes         | Steps 2-3 (audit + breaking change analysis)    |
| P9 Memorization          | Conditional | Only if update reveals project-specific pattern |
| P10 Diagram-Driven       | N/A         |                                                 |
| P11 User-in-the-Loop     | Yes         | Steps 1, 3 (major approvals)                    |
| P12 Conventional Commits | Yes         | Step 5                                          |
| P13 Pre-PR Review        | Yes         | Step 7 (focused)                                |
| P14 Documentation-Gated  | Yes         | Steps 7, 8, 12                                  |
