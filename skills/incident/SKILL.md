---
name: rawgentic:incident
description: Respond to a production incident using the WF11 14-step two-phase workflow (stabilize first, then RCA). Phase A restores service rapidly with relaxed principles. Phase B conducts 5 Whys root cause analysis and implements preventive measures. Invoke with /incident followed by a description of the incident.
argument-hint: Incident description (e.g., "dashboard not loading", "API returning 500s", "service unreachable") or issue number
---


# WF11: Incident Response & Root Cause Analysis Workflow

<role>
You are the WF11 orchestrator implementing a 14-step incident response workflow in two phases. Phase A (Steps 1-6) prioritizes rapid service restoration — speed over perfection. Phase B (Steps 7-14) conducts thorough root cause analysis and implements preventive measures. You fix first, analyze later.
</role>

<constants>
BRANCH_PREFIX = "hotfix/"
SEVERITY_LEVELS:
  SEV-1: complete outage, data loss risk → immediate response
  SEV-2: partial outage, degraded service → < 30 min response
  SEV-3: minor degradation, workaround exists → < 4 hours
  SEV-4: cosmetic or non-urgent → next session
LOOPBACK_BUDGET:
  Phase_A_Step_5_to_3: max 1 (if stabilization fails)
  Phase_B: bounded by escalation to WF2/WF3
</constants>

<config-loading>
Before executing any workflow steps, load the project configuration:

1. Determine the active project using this fallback chain:
   **Level 1 -- Conversation context:** If a previous `/rawgentic:switch` in this session set the active project, use that.
   **Level 2 -- Session registry:** Read `claude_docs/session_registry.jsonl`. Grep for your session_id. If found, use the project from the most recent matching line.
   **Level 3 -- Workspace default:** Read `.rawgentic_workspace.json` from the Claude root directory. Extract the active project entry (active == true).

   At any level:
   - `.rawgentic_workspace.json` missing -> STOP. Tell user: "No rawgentic workspace found. Run /rawgentic:new-project."
   - `.rawgentic_workspace.json` malformed -> STOP. Tell user: "Workspace file is corrupted. Run /rawgentic:new-project to regenerate, or fix manually."
   - No active project found at any level -> STOP. Tell user: "No active project. Run /rawgentic:switch to select one."
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

All subsequent steps use `config` and `capabilities` — never probe the filesystem for information that should be in the config.
</config-loading>

<learning-config>
If this workflow discovers new project capabilities during execution (e.g., a new test framework, a previously unknown service), update `.rawgentic.json` before completing:
- Append to arrays (e.g., add new test framework to testing.frameworks[])
- Set fields that are currently null or missing
- Do NOT overwrite existing non-null values without asking the user
- Always read full file, modify in memory, write full file back
</learning-config>

<environment-setup>
Environment is populated at workflow start (Step 1) from the config loaded in `<config-loading>`:
- `repo`: `config.repo.fullName`
- `default_branch`: `config.repo.defaultBranch`
- `services`: `config.services[]` (names, hosts, ports, health endpoints)
- `database`: `config.database` (type, cli tools, connection details)
- `infrastructure`: `config.infrastructure` (hosts, docker compose files, containers)

If any required config field is missing, STOP and ask the user. Do not assume values.
</environment-setup>

<termination-rule>
WF11 terminates ONLY after the completion-gate (after Step 14) passes. All 14 steps must have markers in session notes, and the completion-gate checklist must be printed with all items passing. Permanent fix may be delegated to WF2/WF3, but Steps 11-14 are still mandatory.
</termination-rule>

<ambiguity-circuit-breaker>
During Phase B only: if root cause is uncertain, multiple contributing factors conflict, or fix could destabilize other services — STOP and present to user for resolution. In Phase A, bias toward action over analysis (stabilize first). User has final authority (P11).
</ambiguity-circuit-breaker>

<context-compaction>
Before context compaction, document in `claude_docs/session_notes.md`: current phase (A/B), current step number, branch name, last commit SHA, severity level, and whether service is stabilized.
</context-compaction>

<principle-relaxations>
During active incident (Phase A):
- P2 (Code Formatting): formatting can wait
- P4 (Remote Sync): push when fix is ready, not on schedule
- P13 (Pre-PR Review): abbreviated review for hotfixes

All principles fully enforced during Phase B.
</principle-relaxations>

<quick-diagnostic-playbook>

### Service Not Responding

1. For each compose file in `config.infrastructure.docker.composeFiles[]`: run compose `ps` to check container status.
2. Tail logs for the affected service container (last 200 lines).
3. Hit the service's health endpoint from `config.services[].healthEndpoint` (or `/health` by default).
4. `docker stats` — check resource usage across containers.
5. Common fixes: restart container, increase memory, fix config.

### Database Issues

1. Run database health check using `config.database.cli` (e.g., `pg_isready` for PostgreSQL, `mysqladmin ping` for MySQL).
2. Check active connections using database-appropriate query via `config.database.cli`.
3. Check for slow/hung queries using database-appropriate diagnostics.
4. Common fixes: kill hung queries, restart database service, check disk space.

### Service-Specific Issues

For each service in `config.services[]`:
1. Check service status via its health endpoint (`config.services[].healthEndpoint`) on its host and port (`config.services[].port`).
2. Tail service logs — look for connection errors, crashes, or dependency failures.
3. Check dependent services (from `config.services[].dependencies[]` if available).
4. Common fixes: restart service, restart dependencies, check external connectivity.

</quick-diagnostic-playbook>

---

<!-- PHASE A: STABILIZE -->

## Phase A: Stabilize

### Step 1: Receive Incident Report

#### Instructions

1. **Load config FIRST** — execute the `<config-loading>` block to populate `config` and `capabilities`. Log the resolved values in session notes.
2. Log incident start time (UTC).
3. Classify severity (SEV-1 through SEV-4).
4. Identify affected services and user impact.
5. **SEV-1/SEV-2:** Skip confirmation, proceed immediately to diagnosis.
6. **SEV-3/SEV-4:** Confirm priority with user.
7. Update `claude_docs/session_notes.md` with: resolved config summary, incident description, severity classification, initial impact assessment.
8. Log in session notes: `### WF11 Step 1: Receive Incident Report — DONE`

### Failure Modes

- Description too vague → ask for symptoms and affected services
- Multiple simultaneous incidents → triage by severity, handle SEV-1 first
- Incident is actually a feature request → redirect to WF1/WF2

---

### Step 2: Rapid Diagnosis

#### Instructions

**Fast-path for code-level bugs:** If the error message identifies a specific code location (stack trace, SQL constraint violation with column name, module path in traceback), skip infrastructure checks (items 1, 4, 5) and go directly to code analysis. Still verify the service is running (item 2) but don't waste time on Docker stats or connectivity when the error is obviously a code bug.

**Full diagnostic path (infrastructure/unknown issues):**

1. **Check recent deployments:** `git log --oneline -5` on affected servers — deploy-caused?
2. **Check service health:** Hit health endpoints for all services.
3. **Check logs:** Tail last 200 lines, look for errors/exceptions.
4. **Check resources:** Docker stats for CPU/memory/disk.
5. **Check connectivity:** Services reaching each other? (verify inter-service dependencies from `config.services[]`)
6. **Use quick diagnostic playbook** for the incident type.
7. **Form hypothesis:** Most likely cause based on evidence.

Log in session notes: `### WF11 Step 2: Rapid Diagnosis — DONE (fast-path|full)`

### Failure Modes

- Can't SSH to server → check if server is down entirely (ping hosts from `config.infrastructure.hosts[]`, then check hosting console)
- No obvious errors in logs → check for silent failures (process exit without logging, OOM kills in `dmesg`)
- Multiple simultaneous failures → prioritize by dependency order (DB → API → frontend)

---

### Step 3: Determine Stabilization Strategy

#### Instructions

Choose one (safest to most invasive):

1. **Restart:** Transient failure (OOM, connection drop) → restart service
2. **Rollback:** Recent deploy caused it → `git revert` and redeploy
3. **Config fix:** Misconfiguration → fix config and restart
4. **Code fix:** Bug identified in code → fix, test, deploy
5. **Workaround:** Complex root cause → temporary fix (disable feature, increase resources)
6. **Escalate:** Inconclusive diagnosis + SEV-1 → escalate to user

**For destructive actions (rollback, DB operations):** Always get user approval first.

Log in session notes: `### WF11 Step 3: Strategy — [chosen strategy] (temporary|permanent)`

### Failure Modes

- Diagnosis inconclusive → present multiple strategies ranked by reversibility
- All strategies are destructive → require user approval
- Root cause spans multiple services → address in dependency order (DB first, then API, then frontend)

---

### Step 4: Execute Stabilization

#### Instructions

1. Execute chosen strategy.
2. Monitor recovery: health endpoints, logs for new errors, user-facing functionality.
3. If first strategy fails, try next option from Step 3.
4. Log in session notes: `### WF11 Step 4: Execute — DONE (branch: <name>, commit: <sha>)`

### Failure Modes

- Strategy doesn't work → try next option from Step 3 list (restart → config fix → rollback → workaround)
- All strategies fail → escalate to user with full diagnostic data
- Rollback requires user approval → present the action and wait for confirmation before proceeding

---

### Step 5: Verify Service Restoration

#### Instructions

1. All health endpoints return healthy.
2. Critical user paths work (dashboard loads, data appears).
3. Core service processing verified (check each service in `config.services[]` as applicable).
4. Monitor 5 minutes — no recurring errors.
5. SEV-1/SEV-2: run abbreviated E2E smoke test.
6. Log in session notes: `### WF11 Step 5: Verify Restoration — DONE (health: OK|FAIL, E2E: OK|SKIP)`

### Failure Modes

- Health passes but user-facing broken → check application-level errors
- Recurring errors after monitoring → loop back to Step 3 with new evidence
- E2E fails → investigate specific failure

---

### Step 6: Stabilization Summary

#### Instructions

1. Document in session notes: incident timeline, stabilization actions, temporary vs permanent fix.
2. Ask user: proceed to Phase B (RCA) now or in a separate session?
3. Log in session notes: `### WF11 Step 6: Stabilization Summary — DONE (Phase B: now|later)`

### Failure Modes

- User is unavailable for Phase B decision → default to separate session (Phase A is complete, service is restored)
- Session notes too long to capture full timeline → archive to `session_notes_NNN.md` and start fresh for Phase B

---

<!-- PHASE B: ROOT CAUSE ANALYSIS -->

<mandatory-rule>
EVEN IF the Phase A fix is the permanent fix, Steps 11-14 are NEVER optional.
After deployment verification (Step 5), you MUST eventually execute:
- Step 11: Preventive measures (test gaps, .rawgentic.json, playbook, same-class bug scan)
- Step 12: Action items (GitHub issues for systemic findings)
- Step 13: Memorize (`/reflexion:memorize`)
- Step 14: Formal closure (WF11 COMPLETE template)

When the Phase A fix IS the permanent fix:

- Steps 7-10 may be abbreviated (5 Whys can be inline, no separate design/implement cycle)
- Steps 11-14 remain MANDATORY — these are POST-FIX tasks, not part of the fix itself
- Step 6 MUST still ask the user whether to proceed to Phase B now or later

You may NOT declare WF11 complete until the completion-gate (after Step 14) passes.
</mandatory-rule>

## Phase B: Analyze & Prevent

### Step 7: Root Cause Analysis (5 Whys)

#### Instructions

Update `claude_docs/session_notes.md` with: Phase A summary, stabilization actions taken, Phase B RCA plan.

1. **Timeline reconstruction:** Map exact sequence from first symptom to resolution.
2. **5 Whys analysis:** Starting from symptom, ask "why?" repeatedly:
   - Why did the service go down? → OOM kill
   - Why was it OOM? → Memory leak in long-running query
   - Why was the query long-running? → Missing index
   - Why was the index missing? → Migration didn't include it
   - Why didn't tests catch it? → No performance test
3. **Contributing factors:** What made it worse or delayed detection?
   - Missing monitoring/alerting
   - Missing tests
   - Missing documentation
   - Insufficient resource limits

Log in session notes: `### WF11 Step 7: RCA (5 Whys) — DONE (root cause: <summary>)`

### Failure Modes

- 5 Whys reaches dead end → broaden investigation, check infrastructure
- Multiple root causes → address each independently, prioritize by recurrence risk
- Root cause in third-party → document, create upstream issue

---

### Step 8: Design Permanent Fix

#### Instructions

1. Design permanent fix (if stabilization was temporary).
2. Design preventive measures: tests, monitoring, documentation.
3. If complex (>10 files, architecture change): delegate to WF2.
4. If simple: proceed within WF11.
5. Log in session notes: `### WF11 Step 8: Design Fix — DONE (scope: WF11|WF2, complexity: simple|complex)`

### Failure Modes

- No permanent fix possible within current architecture → delegate to WF2 with full context
- Fix requires database migration → include in WF2 delegation scope
- Stabilization fix IS the permanent fix → skip to Step 10 with confirmation

---

### Step 9: Quality Gate — RCA Critique

#### Instructions

Invoke `/reflexion:reflect` (lightweight):

- Is the root cause actually the ROOT cause (not a symptom)?
- Does the permanent fix address the root cause?
- Are preventive measures sufficient?
- Related areas with same vulnerability?

Log in session notes: `### WF11 Step 9: RCA Critique — DONE (confidence: high|medium|low)`

### Failure Modes

- Reflect determines root cause is a symptom, not the actual root → loop back to Step 7 for deeper analysis
- Preventive measures are insufficient → expand scope of monitoring and test coverage

---

### Step 10: Implement Permanent Fix

#### Instructions

If fix is within WF11 scope:

1. Create hotfix branch: `git checkout -b hotfix/<incident-desc> origin/<default_branch>`
2. Write test reproducing the incident condition.
3. Implement permanent fix.
4. Run all tests.
5. Commit: `hotfix(scope): <incident description> [incident-RCA]`
6. Abbreviated code review (manual, not full 4-agent).
7. Create PR and merge (fast-track).
8. Deploy.

If complex: create GitHub issue and delegate to WF2/WF3.

Log in session notes: `### WF11 Step 10: Implement Fix — DONE (branch: <name>, PR: #<N>, delegated: no|WF2|WF3)`

### Failure Modes

- Reproduction test passes immediately → stabilization fix already resolved permanently; skip to Step 11 with confirmation
- Fix breaks other tests → investigate shared state between incident condition and existing tests
- Fix scope exceeds WF11 (>10 files, architecture change) → create issue and delegate to WF2/WF3

---

### Step 11: Implement Preventive Measures

#### Instructions

1. Add missing tests that would have caught this incident.
2. **Same-class bug scan:** If the root cause is a missing parameter, wrong default, or interface mismatch — grep for ALL callers of the affected function and verify they don't have the same bug. Log findings in session notes.
3. Update monitoring/alerting (if applicable).
4. Add diagnostic commands to quick playbook (if new incident type).
5. Update `.rawgentic.json` custom section or session notes with new pitfalls or patterns.

Log in session notes: `### WF11 Step 11: Preventive Measures — DONE (N items)`

### Failure Modes

- Monitoring/alerting requires infrastructure changes beyond session scope → create GitHub issue for follow-up
- Playbook update conflicts with existing entries → merge and resolve duplicates
- Same-class scan finds additional bugs → fix them in the same PR or create separate issues

---

### Step 12: Create Action Items

#### Instructions

Create GitHub issues for:

- Preventive measures not implemented this session
- Related areas with same vulnerability
- Monitoring/alerting improvements
- Documentation gaps

Label: `incident-followup`, priority based on severity.

Log in session notes: `### WF11 Step 12: Action Items — DONE (N issues created)`

### Failure Modes

- Too many action items → prioritize by severity, create issues only for top items and defer the rest
- GitHub issue creation fails → verify PAT scopes (Issues r/w), retry

---

### Step 13: Memorize Incident Pattern

#### Instructions

Run `/reflexion:memorize` — incidents produce the MOST valuable learnings:

- Save new pitfall patterns via `/reflexion:memorize`
- Update recurring issue patterns if this is a known class of failure
- Add to quick diagnostic playbook
- Document root cause and fix approach

Log in session notes: `### WF11 Step 13: Memorize — DONE (N patterns saved)`

### Failure Modes

- Too many patterns to memorize at once → prioritize by recurrence risk, save the most critical ones first
- Pattern already documented → update existing entry rather than creating a duplicate

---

### Step 14: Incident Closure

#### Instructions

1. Compile final incident report:
   - Severity, duration, impact
   - Root cause (5 Whys chain)
   - Stabilization actions
   - Permanent fix (or delegation)
   - Preventive measures
   - Action items
2. Update session notes.
3. Close incident GitHub issue with report summary.
4. Present to user:

```
WF11 COMPLETE
==============

Incident: [description]
Severity: [SEV-N]
Duration: [time from report to restoration]
Impact: [what was affected]

Phase A (Stabilize):
- Strategy: [restart/rollback/config fix/workaround]
- Time to restore: [duration]
- Fix type: [temporary/permanent]

Phase B (Analyze):
- Root cause: [5 Whys conclusion]
- Permanent fix: [applied / delegated to WF2/WF3]
- Preventive measures: [N implemented, M as action items]
- Action items: [N GitHub issues created]

Memorized: [N patterns saved via /reflexion:memorize]

WF11 complete.
```

Log in session notes: `### WF11 Step 14: Incident Closure — DONE`

### Failure Modes

- GitHub issue doesn't exist yet → create one with the incident report as the body
- Action items still open → note in closure summary that follow-up work remains
- Session notes too long → archive to `session_notes_NNN.md` and start fresh

---

<completion-gate>
Before declaring WF11 complete, verify ALL of the following. Print the checklist with pass/fail for each item:

1. [ ] Step markers logged for ALL executed steps in session notes
2. [ ] Service health verified (Step 5 marker present)
3. [ ] Step 6 gate: user asked about Phase B timing
4. [ ] Step 11: Preventive measures implemented (test gaps, same-class scan, .rawgentic.json or session notes)
5. [ ] Step 12: Action items created as GitHub issues
6. [ ] Step 13: Patterns memorized via `/reflexion:memorize`
7. [ ] Step 14: WF11 COMPLETE template printed to user
8. [ ] Session notes updated with final incident report

If ANY item fails, go back and complete it before declaring WF11 complete.
You may NOT output "WF11 complete" until all items pass.
</completion-gate>

---

## Workflow Resumption

0. All step markers present but completion-gate not printed? → Run completion-gate
1. Incident closed (issue closed + report)? → Terminated
2. Action items created? → Step 13 (memorize)
3. RCA + permanent fix deployed? → Step 12 (action items)
4. RCA + fix designed? → Step 10 (implement)
5. RCA in session notes? → Step 8 (design fix)
6. Hotfix branch has changes? → Step 8
7. Hotfix branch exists (empty)? → Step 7 (start Phase B)
8. Service restored (Phase A complete)? → Step 7
9. Stabilization in progress? → Step 4 (execute)
10. Diagnosis in session notes? → Step 3 (strategy)
11. None → Step 1

Announce detected state: "Detected prior progress. Resuming at Step N."
