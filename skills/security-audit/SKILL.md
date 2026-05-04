---
name: rawgentic:security-audit
description: Conduct a security audit using the WF9 14-step workflow with STRIDE threat modeling, data channel enumeration, critique on audit findings, and optional remediation. Invoke with /security-audit followed by a scope (full, component, CVE, or deps).
argument-hint: Audit scope (e.g., "full", "REST API", "CVE-2024-1234", "deps")
---


# WF9: Security Audit & Remediation Workflow

<role>
You are the WF9 orchestrator implementing a 14-step security audit and remediation workflow. You use STRIDE threat modeling as the primary framework, audit all data channels independently, and ensure the audit itself is critiqued for completeness before presenting findings.
</role>

<constants>
BRANCH_PREFIX = "security/"
AUDIT_MODES:
  full: entire codebase → comprehensive report + remediation PRs
  targeted: specific component → focused report + remediation PRs
  reactive: specific CVE → impact assessment + fix PR
  dependency: npm/pip deps → delegates to WF8 (/update-deps security)
DATA_CHANNELS: Read from config.security.dataChannels[]
AUTH_MECHANISMS: Read from config.security.authMechanisms[]
SEVERITY_SLA:
  Critical: fix immediately (same session)
  High: fix within 24 hours
  Medium: fix within 1 week
  Low: fix at convenience
LOOPBACK_BUDGET:
  Step_5_to_3: max 2 iterations
  global_cap: 2
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

1b. **Disabled skill check:** After resolving the active project, read `.rawgentic_workspace.json` (if not already read in step 1) and find the active project's entry.
   - If the project entry has a `disabledSkills` array and this skill's bare name appears in it: **STOP.**
     - If the skill is one of {implement-feature, fix-bug, create-tests, update-docs}, tell user:
       "You chose [mapped BMAD alternative] for [skill] in [project]. To change, re-run `/rawgentic:setup` or edit `disabledSkills` in `.rawgentic_workspace.json`."
       Mapping: implement-feature -> bmad-dev-story, fix-bug -> bmad-dev-story, create-tests -> bmad-tea agent / bmad-testarch-* workflows, update-docs -> BMAD tech-writer.
     - Otherwise, tell user:
       "Skill [name] is disabled in [project]. Remove it from `disabledSkills` in `.rawgentic_workspace.json` to re-enable."
   - If workspace `bmadDetected` is true but the project entry has **no** `disabledSkills` field: **STOP.** Tell user:
     "BMAD detected but no skill preferences configured for [project]. Run `/rawgentic:switch` or `/rawgentic:setup` to configure."
   - Otherwise: proceed to step 2.

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

All subsequent steps use `config` and `capabilities` — never probe the filesystem for information that should be in the config.
</config-loading>

<learning-config>
If this workflow discovers new security mechanisms or auth patterns during the audit, update `.rawgentic.json` before completing:
- Append to config.security.authMechanisms[] and config.security.dataChannels[]
- Set fields that are currently null or missing
- Do NOT overwrite existing non-null values without asking the user
- Always read full file, modify in memory, write full file back
</learning-config>

<environment-setup>
Constants are populated at workflow start (Step 1) from the config loaded in `<config-loading>`:
- `REPO`: `capabilities.repo` (from config.repo.fullName)
- `PROJECT_ROOT`: the active project path from `.rawgentic_workspace.json`
- Infrastructure and database details: `config.infrastructure` and `config.database`

If any required config field is missing, STOP and ask the user. Do not assume values.
</environment-setup>

<termination-rule>
WF9 terminates after audit report delivery (audit-only mode) or after remediation deployment verification (audit+fix mode). No auto-transition. In audit+fix mode, WF9 terminates ONLY after the completion-gate (after Step 14) passes. In audit-only mode, WF9 terminates after Step 6 issue creation with step markers for Steps 1-6 logged. All steps must have markers in session notes.
</termination-rule>

<ambiguity-circuit-breaker>
If audit findings are ambiguous, severity classification is uncertain, or remediation could have unintended side effects — STOP and present to user for resolution before proceeding. Do not auto-resolve ambiguity. User has final authority.
</ambiguity-circuit-breaker>

<context-compaction>
Before context compaction, document in `claude_docs/session_notes.md`: current step number, branch name, last commit SHA, audit mode, STRIDE findings so far, remediation scope decision, and loop-back budget state.
</context-compaction>

<mandatory-rule>
In audit+fix mode, Steps 12-14 (Merge and Deploy, Post-Deploy Security Verification, Update Security Documentation) are NEVER optional. A security audit without formal closure leaves vulnerabilities untracked. Execute all three steps even if no vulnerabilities were found (the report should confirm a clean audit). In audit-only mode (user chose "Audit only" in Step 6), the workflow terminates after Step 6 — Steps 7-14 do not apply.
</mandatory-rule>

<step-tracking>
At the end of each step, log a marker in `claude_docs/session_notes.md`:
`### WF9 Step X: <Name> — DONE (<key detail>)`
This enables workflow resumption if context is lost.
</step-tracking>

## Step 1: Receive Audit Scope

### Instructions

1. **Execute `<config-loading>`** to load project configuration and build capabilities. Then populate constants from `<environment-setup>`. Log resolved values in session notes. If any required config field is missing, STOP and ask the user.
2. Parse scope and determine audit mode (full/targeted/reactive/dependency).
3. If CVE: fetch CVE details via web search or NVD.
4. If component: identify all files, endpoints, and data channels in scope.
5. If deps: suggest delegating to WF8 (`/update-deps security`).
6. Confirm scope with user.
7. Update `claude_docs/session_notes.md` with: audit scope, audit mode (full/targeted/reactive/dependency), data channels in scope, initial assessment.

### Output Format

```
Security Audit Scope:
- Mode: [full / targeted / reactive / dependency]
- Components: [list or "all"]
- Data channels: [which channels from config.security.dataChannels[] are in scope]
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

1. **Endpoint inventory:** List all API endpoints by enumerating services from `config.services[]` and discovering route definitions in each.
2. **Authentication mapping:** For each endpoint, verify auth middleware is applied per `config.security.authMechanisms[]`.
3. **Data channel mapping:** Enumerate all data channels from `config.security.dataChannels[]` and verify their auth mechanisms.
4. **Input boundary mapping:** All points where external data enters the system (request bodies, parameters, queries, WebSocket messages, message queues, etc.).
5. **Secret inventory:** Scan for hardcoded secrets, .env patterns, credential storage.
6. **Dependency inventory:** List packages with known CVEs.

### Output

Attack surface map (internal working artifact).

### Failure Modes

- Serena MCP unavailable → fall back to Grep for endpoint/symbol discovery
- Endpoint list incomplete → grep for route definitions across all files using patterns appropriate to the project's framework
- Data channel missed → cross-check against `config.security.dataChannels[]` to ensure all channels are covered

---

## Step 3: Execute STRIDE Analysis

### Instructions

For each STRIDE category, analyze the attack surface:

1. **Spoofing (Authentication):** Are all endpoints protected? Auth mechanisms from `config.security.authMechanisms[]` correctly implemented and validated? Handshake/connection auth enforced on all channels?
2. **Tampering (Input Validation):** All user inputs validated? Numeric params bounded? SQL/NoSQL injection possible? Serialization handled correctly?
3. **Repudiation (Audit Trail):** Critical operations logged? Auth events logged? Admin actions logged?
4. **Information Disclosure:** Error responses leak internals? CORS configured? Secrets in git? Health endpoints expose sensitive info?
5. **Denial of Service:** Rate limiting? Unbounded queries? Resource exhaustion vectors?
6. **Elevation of Privilege:** RBAC where needed? Unauth access to auth endpoints? Regular calls trigger admin ops?

All data channels from `config.security.dataChannels[]` must be audited independently.

### Failure Modes

- STRIDE category yields no findings → verify you checked all code paths, not just obvious ones
- Finding severity unclear → default to higher severity, let Step 5 critique adjust
- Data channel auth mechanism unclear → trace middleware/auth registration in each service's entry point as listed in `config.services[]`

---

## Step 4: Generate Audit Report

### Instructions

1. Compile findings: executive summary, per-STRIDE-category findings, per-finding detail (description, affected code, severity, remediation recommendation, effort estimate).
2. Check `config.custom` for known security debt — verify items marked as fixed are actually fixed.
3. Prioritize: Critical → High → Medium → Low.

### Failure Modes

- Previously "fixed" items not actually fixed → reclassify as open findings
- Finding count is suspiciously low → re-examine attack surface for blind spots
- Severity distribution skewed (all Low) → challenge the classification, check for under-severity bias

---

## Step 5: Quality Gate — Audit Critique

### Instructions

**Critique method preference:** Before running the critique, check the active project entry's `critiqueMethod` field in `.rawgentic_workspace.json`. If set to `"bmad-party-mode"`, use bmad-party-mode instead of the critique below. If missing or `"reflexion"`, proceed as normal.

Invoke `/reflexion:critique` — the **audit itself** is critiqued for completeness and accuracy.

Three judges evaluate:

- **Completeness judge:** All STRIDE categories covered? All data channels from `config.security.dataChannels[]` audited? Any blind spots?
- **Accuracy judge:** Findings genuine (not false positives)? Severity classification correct?
- **Remediation judge:** Recommendations actionable? Match project architecture?

ALL findings from the quality gate MUST be applied — no severity-based filtering. Apply each finding automatically. If any finding is ambiguous or conflicting, STOP and present to the user.

**Finding Auto-Application:** ALL findings from the quality gate MUST be applied automatically — no severity-based filtering. If any finding is ambiguous, conflicting, or requires judgment, STOP and present to the user for resolution.

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
git fetch origin ${capabilities.default_branch}
git checkout -b security/<audit-desc> origin/${capabilities.default_branch}
```

### Failure Modes

- Branch name conflicts with existing branch → append date suffix or disambiguate
- Origin default branch is stale → `git fetch origin` before checkout
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
gh pr create --repo ${capabilities.repo} \
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

1. Squash-merge: `gh pr merge <number> --squash --delete-branch --repo ${capabilities.repo}`
2. Deploy using `config.deploy` settings (skip if `capabilities.has_deploy` is false)
3. Extra security verification post-deploy:
   - Re-run key security tests against deployed environment
   - Verify auth endpoints reject unauthenticated requests
   - Verify rate limiting is active

### Failure Modes

- Merge conflicts → rebase on latest main and re-run tests
- Deploy fails → check deploy configuration in `config.deploy` and infrastructure connectivity
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
- E2E auth tests fail → check auth configuration consistency across services per `config.security.authMechanisms[]`

---

## Step 14: Update Security Documentation

### Instructions

1. Update `config.security` in `.rawgentic.json` and run `/reflexion:memorize` for broader insights. Move fixed items to resolved, add new patterns, update debt list.
2. Update `claude_docs/session_notes.md` with: audit summary, findings fixed, config sections updated, PR URL.
3. Close GitHub issues for fixed findings.
4. Present completion summary:

```
WF9 COMPLETE
=============

Audit Mode: [full / targeted / reactive]
STRIDE Coverage: [all 6 categories]
Data Channels Audited: [N/N from config.security.dataChannels[]]

Findings:
- Critical: [N found, N fixed]
- High: [N found, N fixed]
- Medium: [N found, N fixed / N issued]
- Low: [N found, N fixed / N issued]

Remediation:
- PR: <URL>
- Security tests added: [N]
- .rawgentic.json updated: [yes/no]

Audit Critique: [passed / N improvements applied]

WF9 complete.
```

### Failure Modes

- Previously resolved items conflict with new findings → reconcile: update or re-classify in `config.security`
- GitHub issues fail to create → verify PAT scopes (Issues r/w), retry
- Session notes too long → archival to JSONL happens automatically on next session startup

<completion-gate>
Before declaring WF9 complete, verify ALL of the following. Print the checklist with pass/fail for each item.

**Audit+Fix mode (Steps 1-14):**

1. [ ] Step markers logged for ALL executed steps (1-14) in session notes
2. [ ] Final step output (completion summary) presented to user
3. [ ] Session notes updated with completion summary
4. [ ] Audit report committed
5. [ ] Remediation PRs listed
6. [ ] Residual risks documented with severity
7. [ ] Security documentation updated (config.security in .rawgentic.json)

**Audit-only mode (Steps 1-6):**

1. [ ] Step markers logged for Steps 1-6 in session notes
2. [ ] GitHub issues created for all findings
3. [ ] Audit report presented to user
4. [ ] Session notes updated with audit summary

If ANY item fails for the active mode, go back and complete it before declaring "WF9 complete."
You may NOT output "WF9 complete" until all items for the active mode pass.
</completion-gate>

---

## Workflow Resumption

0. All step markers present but completion-gate not printed? → Run completion-gate, then terminate.
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
