---
name: rawgentic:security-audit
description: Conduct a security audit using the WF9 14-step workflow with STRIDE threat modeling, 4-channel enumeration, critique on audit findings, and optional remediation. Invoke with /security-audit followed by a scope (full, component, CVE, or deps).
argument-hint: Audit scope (e.g., "full", "REST API", "CVE-2024-1234", "deps")
---

# WF9: Security Audit & Remediation Workflow

<role>
You are the WF9 orchestrator implementing a 14-step security audit and remediation workflow. You use STRIDE threat modeling as the primary framework (mandated by CLAUDE.md), audit all 4 data channels independently, and ensure the audit itself is critiqued for completeness before presenting findings.
</role>

<constants>
REPO = "<inferred from `git remote -v` at workflow start>"
PROJECT_ROOT = "<inferred from `git rev-parse --show-toplevel`>"
BRANCH_PREFIX = "security/"
AUDIT_MODES:
  full: entire codebase → comprehensive report + remediation PRs
  targeted: specific component → focused report + remediation PRs
  reactive: specific CVE → impact assessment + fix PR
  dependency: npm/pip deps → delegates to WF8 (/update-deps security)
DATA_CHANNELS:
  - REST API (Express) — JWT authenticate middleware
  - Socket.IO (Express) — JWT handshake middleware
  - Redis pub/sub — requirepass
  - Engine HTTP API (aiohttp) — api_key_middleware
SEVERITY_SLA:
  Critical: fix immediately (same session)
  High: fix within 24 hours
  Medium: fix within 1 week
  Low: fix at convenience
LOOPBACK_BUDGET:
  Step_5_to_3: max 2 iterations
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
WF9 terminates after audit report delivery (audit-only mode) or after remediation deployment verification (audit+fix mode). No auto-transition.
</termination-rule>

<ambiguity-circuit-breaker>
If audit findings are ambiguous, severity classification is uncertain, or remediation could have unintended side effects — STOP and present to user for resolution before proceeding. Do not auto-resolve ambiguity. User has final authority (P11).
</ambiguity-circuit-breaker>

<context-compaction>
Per CLAUDE.md shared invariant #9: before context compaction, document in `claude_docs/session_notes.md`: current step number, branch name, last commit SHA, audit mode, STRIDE findings so far, remediation scope decision, and loop-back budget state.
</context-compaction>

## Step 1: Receive Audit Scope

### Instructions

1. Parse scope and determine audit mode (full/targeted/reactive/dependency).
2. If CVE: fetch CVE details via web search or NVD.
3. If component: identify all files, endpoints, and data channels in scope.
4. If deps: suggest delegating to WF8 (`/update-deps security`).
5. Confirm scope with user.
6. Update `claude_docs/session_notes.md` with: audit scope, audit mode (full/targeted/reactive/dependency), data channels in scope, initial assessment.

### Output Format

```
Security Audit Scope:
- Mode: [full / targeted / reactive / dependency]
- Components: [list or "all"]
- Data channels: [which of the 4 channels are in scope]
- CVE: [if reactive]

Proceeding to enumerate attack surface. Confirm scope.
```

### Failure Modes

- Scope too broad ("everything") → ask user to prioritize specific component or STRIDE category
- CVE not found → verify CVE ID format, try alternative CVE databases
- Component not recognized → list available components and ask for clarification
- Dependency audit requested → delegate to WF8 (`/update-deps security`)

---

## Step 2: Enumerate Attack Surface

### Instructions

1. **Endpoint inventory:** List all API endpoints (Express routes, Engine aiohttp routes).
2. **Authentication mapping:** For each endpoint, verify auth middleware is applied.
3. **Data channel mapping:** Identify all 4 data channels and their auth mechanisms.
4. **Input boundary mapping:** All points where external data enters (req.body, req.params, req.query, WebSocket messages, Redis messages).
5. **Secret inventory:** Scan for hardcoded secrets, .env patterns, credential storage.
6. **Dependency inventory:** List packages with known CVEs.

### Output

Attack surface map (internal working artifact).

### Failure Modes

- Serena MCP unavailable → fall back to Grep for endpoint/symbol discovery
- Endpoint list incomplete → grep for route definitions across all files (`app.get`, `app.post`, `router.`, `@routes.`)
- Data channel missed → cross-check against CLAUDE.md 4-channel list (REST, Socket.IO, Redis, Engine HTTP)

---

## Step 3: Execute STRIDE Analysis

### Instructions

For each STRIDE category, analyze the attack surface:

1. **Spoofing (Authentication):** Are all endpoints protected? JWT validation correct? API keys validated? Socket.IO handshake auth enforced?
2. **Tampering (Input Validation):** All req.params/query/body validated (Zod)? Numeric params bounded? SQL injection possible? JSONB handled correctly?
3. **Repudiation (Audit Trail):** Trade executions logged? Auth events logged? Admin actions logged?
4. **Information Disclosure:** Error responses leak internals? CORS configured? Secrets in git? Health endpoint expose sensitive info?
5. **Denial of Service:** Rate limiting? Unbounded queries? Resource exhaustion vectors?
6. **Elevation of Privilege:** RBAC where needed? Unauth access to auth endpoints? Regular calls trigger admin ops?

All 4 data channels must be audited independently (per CLAUDE.md mandate).

### Failure Modes

- STRIDE category yields no findings → verify you checked all code paths, not just obvious ones
- Finding severity unclear → default to higher severity, let Step 5 critique adjust
- Data channel auth mechanism unclear → trace middleware registration in Express.js and aiohttp app setup

---

## Step 4: Generate Audit Report

### Instructions

1. Compile findings: executive summary, per-STRIDE-category findings, per-finding detail (description, affected code, severity, remediation recommendation, effort estimate).
2. Cross-reference with CLAUDE.md "Known Security Debt" — verify "Fixed" items are actually fixed.
3. Prioritize: Critical → High → Medium → Low.

### Failure Modes

- CLAUDE.md "Fixed" items not actually fixed → reclassify as open findings
- Finding count is suspiciously low → re-examine attack surface for blind spots
- Severity distribution skewed (all Low) → challenge the classification, check for under-severity bias

---

## Step 5: Quality Gate — Audit Critique

### Instructions

Invoke `/reflexion:critique` — the **audit itself** is critiqued for completeness and accuracy.

Three judges evaluate:

- **Completeness judge:** All STRIDE categories covered? All 4 data channels audited? Any blind spots?
- **Accuracy judge:** Findings genuine (not false positives)? Severity classification correct?
- **Remediation judge:** Recommendations actionable? Match project architecture?

ALL findings from the quality gate MUST be applied — no severity-based filtering. Apply each finding automatically. If any finding is ambiguous or conflicting, STOP and present to the user.

**Finding Auto-Application (Shared Invariant #2):** ALL findings from the quality gate MUST be applied automatically — no severity-based filtering. If any finding is ambiguous, conflicting, or requires judgment, STOP and present to the user for resolution (P11).

Update `claude_docs/session_notes.md` with: STRIDE findings summary, severity counts, critique results, and loop-back budget state.

### Failure Modes

- Judges identify missed attack surfaces → re-run Step 3 for those surfaces (max 2 iterations)
- False positive findings → remove from report
- Critique finds severity misclassifications → adjust and re-sort findings

---

## Step 6: User Decision — Remediation Scope

### Instructions

Present validated audit report to user. User chooses:

1. **Audit only:** Create GitHub issues for all findings, end workflow
2. **Fix all:** Remediate all findings (Critical → High → Medium → Low)
3. **Fix critical/high:** Remediate Critical+High, create issues for Medium/Low
4. **Delegate:** Create issues and delegate to `/implement-feature` (WF2) or `/fix-bug` (WF3)

For "Audit only" or "Delegate": create GitHub issues with structured content (severity, STRIDE category, affected code paths, recommended remediation), then terminate.
For fix modes: proceed to Step 7.

### Failure Modes

- User is unavailable for decision → default to "Audit only" (create issues, preserve findings)
- User wants to fix some but not all → create custom fix list, issue the rest

---

## Step 7: Create Security Branch

### Instructions

```bash
git fetch origin main
git checkout -b security/<audit-desc> origin/main
```

### Failure Modes

- Branch name conflicts with existing branch → append date suffix or disambiguate
- Origin/main is stale → `git fetch origin` before checkout
- Uncommitted changes block checkout → stash or commit first

---

## Step 8: Implement Fixes (Vulnerability-First TDD)

### Instructions

For each finding (Critical first, then High, Medium, Low):

1. **Write security test:** Test that demonstrates the vulnerability (e.g., unauthenticated request should be rejected).
2. **Verify test fails:** Proves the vulnerability exists.
3. **Implement fix:** Minimum change to close the vulnerability.
4. **Verify test passes.**
5. **Run full suite:** No regressions.
6. **Commit:** `security(scope): fix <finding-summary>`

**Security-specific considerations:**

- Never log sensitive data (passwords, tokens, PII) in fix
- Test both positive (auth works) and negative (unauth rejected) paths
- For input validation: test boundary values, not just malicious input

### Failure Modes

- Fix breaks existing functionality → investigate if "functionality" is actually a vulnerability
- Fix is complex (>WF9 scope) → create issue, delegate to `/implement-feature` (WF2)
- Multiple findings interact → fix in dependency order

---

## Step 9: Code Review (Security-Focused)

### Instructions

**Review scope:** Security-focused 4-agent review — prioritize vulnerability closure verification and ensure no new attack surface is introduced.

Launch 4-agent review with extra emphasis on:

- No new vulnerabilities introduced by fixes
- Input validation complete (not partial)
- Auth middleware correctly applied (not just present)
- No secrets leaked in test fixtures

Run `/reflexion:memorize` — security findings almost always produce patterns worth documenting.

### Failure Modes

- Review finds new vulnerabilities introduced by fixes → fix before proceeding (security fixes must not create new attack surface)
- Partial input validation found → complete validation before PR
- Secrets leaked in test fixtures → remove and rotate if real credentials
- Memorize produces no insights → review audit patterns, security findings almost always have learnings

---

## Step 10: Create Pull Request

### Instructions

```bash
git push -u origin security/<audit-desc>
gh pr create --repo ${REPO} \
  --title "security: remediate <N> findings from <audit-type> audit" \
  --body "$(cat <<'EOF'
## Summary
- Audit mode: [full/targeted/reactive]
- Findings fixed: [N] (X Critical, Y High, Z Medium, W Low)
- STRIDE coverage: [all 6 categories / subset]

## Test plan
- [ ] Security tests prove each vulnerability is fixed
- [ ] Full test suite passes
- [ ] CI passes
- [ ] Post-deploy auth verification

Generated with [Claude Code](https://claude.com/claude-code) using WF9
EOF
)" \
  --label "security"
```

### Failure Modes

- Push fails (auth issue) → check GitHub PAT scopes (needs Contents r/w)
- PR create fails → verify branch has commits ahead of main
- Label "security" doesn't exist → create it or omit label

---

## Step 11: CI Verification

### Instructions

Wait for CI via `gh run list --branch <branch>`. Security PRs should be fast-tracked — CI failures need immediate investigation.

### Failure Modes

- CI fails on security tests → investigate: fix may be incomplete or test environment differs
- CI fails on unrelated tests → check if security fix has side effects; fix if related, otherwise document
- CI not triggered → verify branch is pushed and workflow is configured for the branch

---

## Step 12: Merge and Deploy

### Instructions

1. Squash-merge: `gh pr merge <number> --squash --delete-branch --repo ${REPO}`
2. Deploy: `${PROJECT_ROOT}/scripts/deploy-dev.sh`
3. Extra security verification post-deploy:
   - Re-run key security tests against deployed environment
   - Verify auth endpoints reject unauthenticated requests
   - Verify rate limiting is active

### Failure Modes

- Merge conflicts → rebase on latest main and re-run tests
- Deploy script fails → check SSH connectivity to my-api-dev, verify Docker status
- Post-deploy security verification fails → investigate environment-specific config differences

---

## Step 13: Post-Deploy Security Verification

### Instructions

1. For each Critical/High finding: verify fix is effective in deployed environment.
2. Run E2E tests covering auth flows.
3. Verify no new errors in service logs.
4. Health endpoints working correctly.

### Failure Modes

- Fix doesn't work in deployed env → investigate env-specific config (SSL, CORS origins, rate limit settings)
- New issues introduced by deploy → hotfix or rollback depending on severity
- E2E auth tests fail → check JWT secret consistency between containers

---

## Step 14: Update Security Documentation

### Instructions

1. Update CLAUDE.md "Security Standards" section: move fixed items to "Fixed", add new patterns, update debt list.
2. Update `claude_docs/session_notes.md` with: audit summary, findings fixed, CLAUDE.md sections updated, PR URL.
3. Close GitHub issues for fixed findings.
4. Present completion summary:

```
WF9 COMPLETE
=============

Audit Mode: [full / targeted / reactive]
STRIDE Coverage: [all 6 categories]
Data Channels Audited: [4/4]

Findings:
- Critical: [N found, N fixed]
- High: [N found, N fixed]
- Medium: [N found, N fixed / N issued]
- Low: [N found, N fixed / N issued]

Remediation:
- PR: <URL>
- Security tests added: [N]
- CLAUDE.md updated: [yes/no]

Audit Critique: [passed / N improvements applied]

WF9 complete.
```

### Failure Modes

- CLAUDE.md "Fixed" items conflict with new findings → reconcile: update or re-classify
- GitHub issues fail to create → verify PAT scopes (Issues r/w), retry
- Session notes too long → archive to `session_notes_NNN.md` and start fresh

---

## Workflow Resumption

1. PR merged and deployed? → Step 14 (update docs)
2. PR exists and CI passed? → Step 12 (merge)
3. PR exists? → Step 11 (CI)
4. Security branch has fixes with passing tests? → Step 9 (review)
5. Security branch has partial fixes? → Step 8 (continue)
6. Security branch exists (empty)? → Step 8
7. Remediation plan chosen (audit-only)? → Terminated
8. Audit report in session notes? → Step 6 (user decision)
9. STRIDE analysis complete? → Step 4 (generate report)
10. Attack surface mapped? → Step 3 (STRIDE)
11. None → Step 1

Announce detected state: "Detected prior progress. Resuming at Step N."
