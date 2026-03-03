# Phase 3 Workflow: Security Audit & Remediation (v1.0)

**Date:** 2026-03-02
**Author:** Orchestrator (direct authoring)
**Inputs:** phase2-principles.md, phase2b-official-comparison.md, CLAUDE.md security standards
**Purpose:** Define the workflow for conducting security audits and remediating vulnerabilities. WF9 covers proactive audits (scheduled or user-initiated), reactive audits (responding to CVE announcements), and the full remediation cycle from discovery through verified fix.

---

## Workflow: Security Audit & Remediation

**Invocation:** `/security-audit <scope>` skill (custom Claude Code skill)
**Trigger:** User invokes `/security-audit full` (comprehensive), `/security-audit <component>` (targeted), or `/security-audit <CVE-number>` (reactive)
**Inputs:**

- Audit scope: "full", component name, CVE identifier, or GitHub issue
- Existing security documentation (CLAUDE.md security standards, STRIDE model)
- Codebase context
- Runtime environment access

**Outputs:**

- Security audit report (findings, severity, remediation recommendations)
- Merged PR(s) fixing identified vulnerabilities (if remediation invoked)
- Updated security documentation in CLAUDE.md
- Updated session notes

**Tracking:** GitHub issue per finding (created by WF9, implemented by WF9 or delegated to WF2/WF3).

**Principles enforced:**

- P1: Branch Isolation (security/ branch for fixes)
- P2: Code Formatting
- P3: Frequent Local Commits
- P4: Regular Remote Sync (push during implementation)
- P5: TDD Enforcement (security tests proving fix)
- P6: Main-to-Dev Sync
- P7: Triple-Gate Testing
- P8: Shift-Left Critique (full critique on audit findings)
- P9: Continuous Memorization (security patterns)
- P11: User-in-the-Loop (user approves remediation plan)
- P12: Conventional Commit Discipline
- P13: Pre-PR Code Review
- P14: Documentation-Gated PRs

**Diagram:** `diagrams/workflow-security-audit.excalidraw`

**Termination:** WF9 terminates after audit report delivery (audit-only mode) or after remediation deployment verification (audit + fix mode). No auto-transition.

---

## Audit Modes

| Mode           | Trigger                       | Scope                  | Output                                             |
| -------------- | ----------------------------- | ---------------------- | -------------------------------------------------- |
| **Full Audit** | `/security-audit full`        | Entire codebase        | Comprehensive report + remediation PRs             |
| **Targeted**   | `/security-audit <component>` | Specific component     | Focused report + remediation PRs                   |
| **Reactive**   | `/security-audit <CVE>`       | Specific vulnerability | Impact assessment + fix PR                         |
| **Dependency** | `/security-audit deps`        | npm/pip dependencies   | Vulnerability scan + update PRs (delegates to WF8) |

---

## STRIDE Threat Model (Per CLAUDE.md)

WF9 uses STRIDE as the primary audit framework (mandated by CLAUDE.md security standards):

| Threat                     | Category        | Audit Focus                                             |
| -------------------------- | --------------- | ------------------------------------------------------- |
| **S**poofing               | Authentication  | JWT validation, API key enforcement, session management |
| **T**ampering              | Integrity       | Input validation, SQL injection, JSONB codec safety     |
| **R**epudiation            | Non-repudiation | Audit logging, trade execution trails                   |
| **I**nformation Disclosure | Confidentiality | Secret management, error message leakage, CORS          |
| **D**enial of Service      | Availability    | Rate limiting, resource exhaustion, circuit breakers    |
| **E**levation of Privilege | Authorization   | Role checks, endpoint protection, middleware gaps       |

All data channels must be audited independently (per CLAUDE.md):

1. REST API (Express) -- JWT authenticate middleware
2. Socket.IO (Express) -- JWT handshake middleware
3. Redis pub/sub -- requirepass
4. Engine HTTP API (aiohttp) -- api_key_middleware

---

## Finding Severity Classification

| Severity     | Criteria                                                        | SLA                            |
| ------------ | --------------------------------------------------------------- | ------------------------------ |
| **Critical** | Exploitable remotely, data breach risk, auth bypass             | Fix immediately (same session) |
| **High**     | Requires some access, data corruption risk, partial auth bypass | Fix within 24 hours            |
| **Medium**   | Defense-in-depth gap, hardening opportunity                     | Fix within 1 week              |
| **Low**      | Best practice deviation, informational                          | Fix at convenience             |

---

## Remediation Decision

After audit, user decides:

1. **Audit only:** Generate report, create GitHub issues for findings, end workflow
2. **Audit + fix all:** Fix all findings in priority order (Critical -> High -> Medium -> Low)
3. **Audit + fix critical/high:** Fix only Critical and High, create issues for Medium/Low
4. **Delegate:** Create issues for all findings and delegate to WF2/WF3 for implementation

---

## Finding Application and Ambiguity Circuit Breaker

**Inherited from WF2:** Auto-apply findings. Circuit breaker on ambiguity (CRITICAL for security -- an ambiguous security fix could introduce new vulnerabilities).

---

## Workflow Resumption

**Checkpoint artifacts:**

| Artifact        | Location                       | Created at   | Purpose        |
| --------------- | ------------------------------ | ------------ | -------------- |
| Audit report    | Session notes / docs/          | Step 4       | Findings       |
| GitHub issues   | GitHub (remote)                | Step 5       | Tracking       |
| Security branch | Git (remote)                   | Step 7       | Fix code state |
| PR(s)           | GitHub (remote)                | Step 10      | Review state   |
| Session notes   | `claude_docs/session_notes.md` | Continuously | Progress log   |

**Step detection on resume:**

1. PR merged and deployed? -> Step 14
2. PR exists and CI passed? -> Step 12
3. PR exists? -> Step 11
4. Security branch has fixes with passing tests? -> Step 9
5. Security branch has partial fixes? -> Step 8 (continue fixes)
6. Security branch exists (empty)? -> Step 8
7. Remediation plan chosen (GitHub issues for audit-only)? -> Terminated
8. Audit report in session notes? -> Step 6
9. STRIDE analysis complete? -> Step 4
10. Attack surface mapped? -> Step 3
11. None -> Step 1

---

## Steps

### Step 1: Receive Audit Scope

**Type:** user decision
**Actor:** human
**Command:** `/security-audit <scope>`
**Input:** Scope (full/component/CVE/deps)
**Action:**

1. Parse scope and determine audit mode
2. If CVE: fetch CVE details from web search or NVD
3. If component: identify all files, endpoints, and data channels in scope
4. If deps: suggest delegating to WF8 (`/update-deps security`)
5. Confirm scope with user

**Output:** Validated scope: { mode, components, cve_id (if reactive), data_channels }
**Principle alignment:** P11

---

### Step 2: Enumerate Attack Surface

**Type:** automated
**Actor:** Claude (Serena MCP + Grep/Glob + codebase analysis)
**Input:** Scope from Step 1
**Action:**

1. **Endpoint inventory:** List all API endpoints (Express routes, Engine aiohttp routes)
2. **Authentication mapping:** For each endpoint, verify auth middleware is applied
3. **Data channel mapping:** Identify all data channels (REST, Socket.IO, Redis, Engine HTTP)
4. **Input boundary mapping:** Identify all points where external data enters the system (req.body, req.params, req.query, WebSocket messages, Redis messages)
5. **Secret inventory:** Scan for hardcoded secrets, .env patterns, credential storage
6. **Dependency inventory:** List all packages with known CVEs

**Output:** Attack surface map: { endpoints[], auth_coverage, data_channels[], input_boundaries[], secrets_scan, dependency_vulnerabilities[] }
**Failure mode:** (1) Serena MCP unavailable -> fall back to Grep. (2) Endpoint list is incomplete -> grep for route definitions across all files.

---

### Step 3: Execute STRIDE Analysis

**Type:** automated
**Actor:** Claude (systematic analysis per STRIDE category)
**Input:** Attack surface from Step 2, CLAUDE.md security standards
**Action:**

For each STRIDE category, analyze the attack surface:

1. **Spoofing (Authentication):**
   - Are all endpoints protected? Check for unprotected routes beyond the whitelist
   - Is JWT validation correct (algorithm, expiration, secret strength)?
   - Are API keys properly validated?
   - Is Socket.IO handshake auth enforced?

2. **Tampering (Input Validation):**
   - Are all `req.params`, `req.query`, `req.body` validated (Zod schemas)?
   - Are numeric parameters bounded (CLAUDE.md: "validate type and enforce upper bounds")?
   - Is SQL injection possible (parameterized queries only)?
   - Is JSONB handled correctly (no double-encoding)?

3. **Repudiation (Audit Trail):**
   - Are trade executions logged with complete context?
   - Are authentication events logged?
   - Are admin actions (settings changes) logged?

4. **Information Disclosure:**
   - Do error responses leak internal details (stack traces, SQL errors)?
   - Is CORS properly configured?
   - Are secrets excluded from git (check .gitignore)?
   - Does the health endpoint expose sensitive info without auth?

5. **Denial of Service:**
   - Is rate limiting configured for all public endpoints?
   - Are there unbounded queries (no LIMIT, no pagination cap)?
   - Are there resource exhaustion vectors (large file uploads, long-running queries)?

6. **Elevation of Privilege:**
   - Is there role-based access control where needed?
   - Can unauthenticated users access authenticated endpoints?
   - Can regular API calls trigger admin operations?

**Output:** STRIDE analysis: { findings_by_category[], severity_counts, coverage_gaps }

---

### Step 4: Generate Audit Report

**Type:** automated
**Actor:** Claude
**Input:** STRIDE analysis from Step 3
**Action:**

1. Compile findings into a structured report:
   - Executive summary (total findings by severity)
   - Per-category findings (STRIDE breakdown)
   - Per-finding detail: description, affected code, severity, remediation recommendation, effort estimate
2. Cross-reference with CLAUDE.md "Known Security Debt" section
3. Identify findings that are already documented as "Fixed" in CLAUDE.md -- verify they are actually fixed
4. Prioritize findings: Critical -> High -> Medium -> Low

**Output:** Security audit report document
**Principle alignment:** P8 (Shift-Left)

---

### Step 5: Quality Gate -- Audit Critique

**Type:** quality gate
**Actor:** sub-agent
**Command:** `/reflexion:critique` (the audit itself is critiqued for completeness and accuracy)
**Input:** Audit report from Step 4, attack surface from Step 2
**Action:**

Three judges evaluate the audit report:

- **Completeness judge:** Are all STRIDE categories covered? Were all data channels audited? Any blind spots?
- **Accuracy judge:** Are the findings genuine (not false positives)? Is the severity classification correct?
- **Remediation judge:** Are the recommendations actionable? Do they match the project's architecture?

**Output:** Validated audit report (critique-verified)
**Failure mode:** (1) Judges identify missed attack surfaces -> re-run Step 3 for those surfaces. (2) False positive findings -> remove from report.
**Principle alignment:** P8 (full critique on the audit itself ensures no false sense of security)

---

### Step 6: User Decision -- Remediation Scope

**Type:** user decision
**Actor:** human
**Input:** Validated audit report from Step 5
**Action:**

1. Present audit report to user with severity summary
2. User chooses remediation mode:
   - **Audit only:** Create GitHub issues for all findings, end workflow
   - **Fix all:** Proceed with remediation for all findings
   - **Fix critical/high:** Remediate Critical+High, issue the rest
   - **Delegate:** Create issues for WF2/WF3 implementation

3. For "Audit only" or "Delegate": create GitHub issues via WF1-style issue creation, then terminate
4. For fix modes: proceed to Step 7

**Output:** Remediation plan: { findings_to_fix[], findings_to_issue[], remediation_order }
**Principle alignment:** P11 (user controls remediation scope)

---

### Step 7: Create Security Branch

**Type:** automated
**Actor:** Claude
**Command:** `git checkout -b security/<audit-desc>`
**Input:** Remediation plan
**Action:**

1. `git fetch origin main`
2. `git checkout -b security/<audit-desc> origin/main`

**Output:** Active security branch
**Principle alignment:** P1

---

### Step 8: Implement Fixes (TDD)

**Type:** automated
**Actor:** Claude (via `/superpowers:test-driven-development`)
**Input:** Remediation plan from Step 6, ordered by severity
**Action:**

For each finding (Critical first, then High, Medium, Low):

1. **Write security test:** Create a test that demonstrates the vulnerability (e.g., test that an unauthenticated request is rejected)
2. **Verify test fails:** The test should fail against current code (proving the vulnerability exists)
3. **Implement fix:** Apply the minimum change to close the vulnerability
4. **Verify test passes:** Run the security test -- it should now pass
5. **Run full suite:** Ensure no regressions
6. **Commit:** `security(scope): fix <finding-summary>`

**Special considerations for security fixes:**

- Never log sensitive data (passwords, tokens, PII) in fix implementation
- Validate that fixes don't break legitimate use cases
- For auth fixes: test both positive (authenticated should work) and negative (unauthenticated should fail)
- For input validation: test boundary values, not just malicious input

**Output:** Fixed vulnerabilities with security tests on branch
**Failure mode:** (1) Fix breaks existing functionality -> investigate: is the "functionality" actually a vulnerability? (2) Fix is complex enough to warrant WF2 -> create issue and skip this finding. (3) Multiple findings interact -> fix in dependency order.
**Principle alignment:** P5 (TDD), P3 (Frequent Commits)

---

### Step 9: Code Review (Security-Focused)

**Type:** automated
**Actor:** sub-agent (4-agent review with security emphasis)
**Command:** `/code-review:code-review`
**Input:** All changes on security branch
**Action:**

1. Standard 4-agent review with extra emphasis on:
   - No new vulnerabilities introduced by fixes
   - Input validation is complete (not just partial)
   - Auth middleware is correctly applied (not just present)
   - No secrets leaked in test fixtures
2. `/reflexion:memorize` -- security findings almost always produce patterns worth documenting

**Output:** Review-clean code + CLAUDE.md security section updates
**Principle alignment:** P13, P9

---

### Step 10: Create Pull Request

**Type:** automated
**Actor:** Claude
**Command:** `/commit-commands:commit-push-pr`
**Input:** Reviewed code on security branch
**Action:**

1. Push: `git push -u origin security/<audit-desc>`
2. Create PR:
   - Title: `security: remediate <N> findings from <audit-type> audit`
   - Body: Audit summary, findings fixed (severity breakdown), test plan, STRIDE coverage
   - Labels: security, priority/high

**Output:** PR URL
**Principle alignment:** P12, P14

---

### Step 11: CI Verification (Gate 2)

**Type:** automated
**Actor:** GitHub Actions
**Input:** PR from Step 10
**Action:** Standard CI verification. Security PRs should be fast-tracked -- CI failures need immediate investigation (could indicate a broken fix).

**Output:** CI pass
**Principle alignment:** P7 (Gate 2)

---

### Step 12: Merge and Deploy (Gate 3)

**Type:** automated
**Actor:** Claude
**Command:** `gh pr merge --squash` + deploy script
**Input:** CI-passing PR
**Action:**

1. Squash-merge
2. Deploy to dev
3. Extra security verification post-deploy:
   - Re-run key security tests against deployed environment
   - Verify auth endpoints reject unauthenticated requests
   - Verify rate limiting is active

**Output:** Merged and deployed
**Principle alignment:** P6, P7 (Gate 3)

---

### Step 13: Post-Deploy Security Verification

**Type:** quality gate
**Actor:** Claude
**Command:** `/reflexion:reflect`
**Input:** Deployed security fixes, original audit report
**Action:**

1. For each Critical/High finding: verify the fix is effective in the deployed environment
2. Run E2E tests that cover auth flows
3. Verify no new errors in service logs
4. Check that health endpoints still work correctly

**Output:** Security verification pass/fail
**Failure mode:** (1) Fix doesn't work in deployed env -> investigate env-specific config. (2) New issues introduced -> hotfix or rollback.
**Principle alignment:** P7 (Gate 3 -- extra important for security)

---

### Step 14: Update Security Documentation

**Type:** automated
**Actor:** Claude
**Input:** Audit results, fixes applied
**Action:**

1. Update CLAUDE.md "Security Standards" section:
   - Move fixed items to "Fixed" subsection
   - Add any new security patterns discovered
   - Update "Known Security Debt" if items remain
2. Update session notes with audit summary
3. Close GitHub issues for fixed findings
4. Present completion summary to user

**Output:** Updated docs, closed issues, completion summary
**Principle alignment:** P14, P9

---

## Design Decisions

### D1: 14 Steps with Full Critique on Audit Report

**Rationale:** The audit report itself must be critiqued because a false sense of security is worse than known vulnerability. The 3-judge critique ensures the audit is complete, accurate, and actionable. This is one of the few workflows where the ANALYSIS (not just the implementation) gets a full critique.

### D2: STRIDE as Mandatory Framework

**Rationale:** CLAUDE.md mandates STRIDE threat modeling. WF9 operationalizes this as a systematic 6-category analysis rather than ad-hoc security review. This ensures consistent, repeatable audits.

### D3: All Data Channels Audited

**Rationale:** CLAUDE.md explicitly notes that REST-only reviews miss WebSocket, Redis, and Engine API surfaces. WF9 mandates independent audit of all data channels in Step 2.

### D4: User Controls Remediation Scope

**Rationale:** Not all findings need immediate fixing. The user may want to: (a) just document findings for now, (b) fix only critical issues, (c) delegate to other workflows. WF9 supports all modes.

### D5: Security Tests Demonstrate Vulnerability First

**Rationale:** A security test that passes from the start doesn't prove a vulnerability was fixed -- it might have been testing the wrong thing. By requiring the test to FAIL first (showing the vulnerability exists), we ensure the fix is actually addressing the real issue.

---

## Principle Coverage Matrix

| Principle                | Enforced | How                                 |
| ------------------------ | -------- | ----------------------------------- |
| P1 Branch Isolation      | Yes      | Step 7                              |
| P2 Code Formatting       | Yes      | Automated in commits                |
| P3 Frequent Commits      | Yes      | Step 8                              |
| P4 Remote Sync           | Yes      | Step 10                             |
| P5 TDD Enforcement       | Yes      | Step 8: vulnerability-first testing |
| P6 Main-to-Dev Sync      | Yes      | Step 12                             |
| P7 Triple-Gate           | Yes      | Steps 8, 11, 13                     |
| P8 Shift-Left Critique   | Yes      | Step 5: full critique on audit      |
| P9 Memorization          | Yes      | Step 9 (always for security)        |
| P10 Diagram-Driven       | N/A      |                                     |
| P11 User-in-the-Loop     | Yes      | Steps 1, 6                          |
| P12 Conventional Commits | Yes      | Step 8, 10                          |
| P13 Pre-PR Review        | Yes      | Step 9                              |
| P14 Documentation-Gated  | Yes      | Step 14                             |
