# Phase 3 Workflow: Incident Response & Root Cause Analysis (v1.0)

**Date:** 2026-03-02
**Author:** Orchestrator (direct authoring)
**Inputs:** phase2-principles.md, phase2b-official-comparison.md
**Purpose:** Define the workflow for responding to production incidents (service outages, data corruption, security breaches) and conducting thorough post-incident root cause analysis. WF11 prioritizes rapid restoration over root cause identification — fix first, analyze later.

---

## Workflow: Incident Response & RCA

**Invocation:** `/incident <description>` skill (custom Claude Code skill)
**Trigger:** User invokes `/incident "dashboard not loading"` or `/incident "engine stopped trading"` or `/incident <issue-number>`
**Inputs:**

- Incident description (symptoms, affected services, user impact)
- Runtime environment access (SSH to ${ENGINE_HOST} and ${DEV_HOST} for immediate diagnosis)
- Service logs and health endpoints
- Recent deployment history (to check if this is a deploy-caused regression)

**Outputs:**

- Incident resolution (service restored)
- Root Cause Analysis (RCA) document
- Merged PR with permanent fix (if quick fix was temporary)
- Post-incident action items (GitHub issues for improvements)
- Updated CLAUDE.md with lessons learned

**Tracking:** GitHub issue for the incident (auto-created in Step 1, closed in Step 14) + follow-up issues for action items.

**Principles enforced:**

- P1: Branch Isolation (hotfix/ branch for permanent fix)
- P3: Frequent Local Commits (during fix implementation)
- P5: TDD Enforcement (tests proving the fix)
- P6: Main-to-Dev Sync (deploy fix immediately)
- P7: Triple-Gate Testing (abbreviated for hotfixes)
- P8: Shift-Left Critique (RCA uses systematic analysis)
- P9: Continuous Memorization (incident patterns are the MOST important memories)
- P11: User-in-the-Loop (user controls incident priority and resolution approach)
- P12: Conventional Commit Discipline
- P14: Documentation-Gated

**Principles relaxed during active incident:**

- P2 (Code Formatting): Formatting can wait during active incident
- P4 (Remote Sync): Push when fix is ready, not on schedule
- P13 (Pre-PR Review): Abbreviated review for hotfixes (not full 4-agent)

**Diagram:** `.claude/framework-build/diagrams/workflow-incident-response.excalidraw`

**Termination:** WF11 terminates after RCA is complete and action items are created. The permanent fix may be delegated to WF2/WF3.

---

## Incident Severity Levels

| Severity  | Criteria                                | Response Time | Example                               |
| --------- | --------------------------------------- | ------------- | ------------------------------------- |
| **SEV-1** | Complete service outage, data loss risk | Immediate     | Engine crashed, DB unreachable        |
| **SEV-2** | Partial outage, degraded service        | < 30 min      | Dashboard loads but no real-time data |
| **SEV-3** | Minor degradation, workaround exists    | < 4 hours     | Slow API responses, stale cache       |
| **SEV-4** | Cosmetic or non-urgent issue            | Next session  | UI glitch, log noise                  |

---

## Two-Phase Approach: Stabilize First, Then Analyze

WF11 has two distinct phases:

### Phase A: Stabilize (Steps 1-6)

**Goal:** Restore service as quickly as possible. Speed over perfection. A temporary workaround is acceptable if it restores service.

### Phase B: Analyze & Prevent (Steps 7-14)

**Goal:** Understand root cause, implement permanent fix, prevent recurrence. Thoroughness over speed.

Phase B can happen in the same session (for simple incidents) or a separate session (for complex ones requiring investigation).

---

## Quick Diagnostic Playbook

For common incident types, WF11 provides quick diagnostic steps:

### Service Not Responding

1. Check container status: `docker compose -f <compose-file> ps`
2. Check logs: `docker compose -f <compose-file> logs -f <service> --tail=200`
3. Check health: `curl http://<host>:<port>/health`
4. Check resources: `docker stats`
5. Common fixes: restart container, increase memory limit, fix config

### Database Issues

1. Check PostgreSQL: `docker exec ${POSTGRES_CONTAINER} pg_isready`
2. Check connections: `docker exec ${POSTGRES_CONTAINER} psql -U ${DB_USER} -c "SELECT count(*) FROM pg_stat_activity"`
3. Check slow queries: `docker exec ${POSTGRES_CONTAINER} psql -U ${DB_USER} -c "SELECT * FROM pg_stat_activity WHERE state = 'active' AND query_start < now() - interval '30 seconds'"`
4. Common fixes: kill hung queries, restart PostgreSQL, check disk space

### Trading Engine Issues

1. Check engine status: `curl -H "X-API-Key: $KEY" http://${ENGINE_HOST}:${ENGINE_API_PORT}/status`
2. Check broker connection: engine logs for "connected"/"disconnected"
3. Check scheduler: `curl -H "X-API-Key: $KEY" http://${ENGINE_HOST}:${ENGINE_API_PORT}/scheduler`
4. Common fixes: restart engine, restart IB Gateway, check market hours

---

## Finding Application and Ambiguity Circuit Breaker

During Phase A (stabilization): **No circuit breaker** — take the fastest path to restore service. Document decisions for Phase B review.

During Phase B (analysis/fix): Standard circuit breaker behavior. Ambiguous root causes require user input.

---

## Workflow Resumption

**Checkpoint artifacts:**

| Artifact      | Location                       | Created at   | Purpose          |
| ------------- | ------------------------------ | ------------ | ---------------- |
| Incident log  | Session notes                  | Step 1       | Timeline         |
| Diagnosis     | Session notes                  | Step 2       | Findings         |
| Hotfix branch | Git (remote)                   | Step 7       | Fix state        |
| RCA document  | Session notes / docs/          | Step 9       | Analysis         |
| Action items  | GitHub issues                  | Step 12      | Follow-up        |
| Session notes | `claude_docs/session_notes.md` | Continuously | Full audit trail |

**Step detection on resume:**

1. Incident closed (issue closed + report complete)? → Terminated
2. Action items created? → Step 13
3. RCA document + permanent fix deployed? → Step 12
4. RCA document + fix designed? → Step 10
5. RCA document in session notes? → Step 8
6. Hotfix branch has code changes? → Step 8
7. Hotfix branch exists (empty)? → Step 7
8. Service restored (Phase A complete)? → Step 7
9. Stabilization in progress? → Step 4
10. Diagnosis in session notes? → Step 3
11. None → Step 1

---

## Steps

### Phase A: Stabilize

#### Step 1: Receive Incident Report

**Type:** user decision (urgent)
**Actor:** human
**Command:** `/incident <description>`
**Input:** Incident description, severity assessment
**Action:**

1. Log incident start time
2. Classify severity (SEV-1 through SEV-4)
3. Identify affected services and user impact
4. For SEV-1/SEV-2: skip confirmation, proceed immediately to diagnosis
5. For SEV-3/SEV-4: confirm priority with user

**Output:** Incident record: { description, severity, affected_services, start_time, impact }
**Failure mode:** (1) Description too vague to classify → ask user for symptoms and affected services. (2) Multiple incidents reported simultaneously → triage by severity, handle SEV-1 first. (3) Incident is actually a feature request → redirect to WF1/WF2.
**Principle alignment:** P11 (but abbreviated for urgency)

---

#### Step 2: Rapid Diagnosis

**Type:** automated (urgent)
**Actor:** Claude (via SSH to affected servers)
**Input:** Incident record from Step 1
**Action:**

1. **Check recent deployments:** `git log --oneline -5` on affected servers — was this caused by a recent deploy?
2. **Check service health:** Hit health endpoints for all services
3. **Check logs:** Tail last 200 lines of affected service logs, look for errors/exceptions
4. **Check resources:** Docker stats for CPU/memory/disk
5. **Check connectivity:** Can services reach each other (dashboard↔postgres, engine↔postgres, engine↔redis)?
6. **Use quick diagnostic playbook** for the incident type
7. **Form hypothesis:** Based on evidence, what is the most likely cause?

**Output:** Diagnosis: { recent_deploys, service_health, error_messages, resource_status, hypothesis }
**Failure mode:** (1) Can't SSH to server → check if server is down entirely. (2) No obvious errors → check for silent failures. (3) Multiple simultaneous failures → prioritize by dependency order.

---

#### Step 3: Determine Stabilization Strategy

**Type:** automated + user approval for destructive actions
**Actor:** Claude
**Input:** Diagnosis from Step 2
**Action:**

Choose one:

1. **Restart:** If diagnosis suggests a transient failure (OOM, connection drop), restart the affected service
2. **Rollback:** If diagnosis points to a recent deploy, `git revert` the deploy commit and redeploy
3. **Config fix:** If diagnosis points to misconfiguration, fix the config and restart
4. **Workaround:** If root cause is complex, apply a temporary workaround (disable feature, increase resources, restart with safe mode)
5. **Escalate:** If diagnosis is inconclusive and SEV-1, escalate to user for manual intervention

For destructive actions (rollback, database operations, data fixes): **always get user approval first**.

**Output:** Stabilization plan: { strategy, actions, requires_user_approval }
**Failure mode:** (1) Diagnosis is inconclusive → present multiple strategies ranked by reversibility (restart > config fix > rollback > workaround). (2) All strategies are destructive → require user approval before proceeding. (3) Root cause spans multiple services → address in dependency order (DB first, then API, then frontend).
**Principle alignment:** P11 (user approves destructive actions)

---

#### Step 4: Execute Stabilization

**Type:** automated (with user approval if needed)
**Actor:** Claude
**Input:** Stabilization plan from Step 3
**Action:**

1. Execute the chosen strategy
2. Monitor service recovery:
   - Check health endpoints
   - Check logs for new errors
   - Verify user-facing functionality works
3. If first strategy fails, try next option from Step 3

**Output:** Service status: { restored: boolean, strategy_used, recovery_time }
**Failure mode:** (1) Strategy doesn't work → try next option. (2) All strategies fail → escalate to user with full diagnostic data.

---

#### Step 5: Verify Service Restoration

**Type:** quality gate (abbreviated)
**Actor:** Claude
**Input:** Restored service
**Action:**

1. Hit all health endpoints — all should return healthy
2. Check critical user paths work (dashboard loads, data appears)
3. Check engine is processing (if applicable)
4. Monitor for 5 minutes — no recurring errors
5. For SEV-1/SEV-2: run abbreviated E2E smoke test

**Output:** Restoration verified (or not — may need to loop back to Step 3)
**Failure mode:** (1) Health endpoints pass but user-facing functionality broken → check application-level errors, not just container health. (2) Recurring errors after 5-min monitoring → service is unstable, loop back to Step 3 with new evidence. (3) E2E smoke test fails → investigate specific failure, may indicate incomplete fix.

---

#### Step 6: Stabilization Summary

**Type:** automated
**Actor:** Claude
**Input:** All Phase A data
**Action:**

1. Document in session notes:
   - Incident timeline (report → diagnosis → fix → verification)
   - What was done to stabilize
   - Whether the fix is temporary or permanent
2. Ask user: proceed to Phase B (RCA) now or in a separate session?

**Output:** Stabilization documented, user decision on Phase B timing
**Failure mode:** (1) User is unavailable for Phase B decision → default to separate session (Phase A is complete, service is restored). (2) Session notes too long to capture full timeline → archive to `session_notes_NNN.md` and start fresh for Phase B.

---

### Phase B: Analyze & Prevent

#### Step 7: Root Cause Analysis

**Type:** automated
**Actor:** Claude (deep analysis)
**Input:** All Phase A data, codebase access, logs
**Action:**

1. **Timeline reconstruction:** Map the exact sequence of events from first symptom to resolution
2. **5 Whys analysis:** Starting from the symptom, ask "why?" repeatedly until reaching the root cause:
   - Why did the service go down? → OOM kill
   - Why was it OOM? → Memory leak in long-running query
   - Why was the query long-running? → Missing index on new table
   - Why was the index missing? → Migration script didn't include it
   - Why didn't tests catch it? → No test for query performance under load
3. **Contributing factors:** Identify factors that made the incident worse or delayed detection:
   - Missing monitoring/alerting
   - Missing tests
   - Missing documentation
   - Insufficient resource limits

**Output:** RCA document: { timeline, root_cause, contributing_factors, 5_whys_chain }
**Failure mode:** (1) 5 Whys reaches a dead end before root cause → broaden investigation scope, check infrastructure and external factors. (2) Multiple root causes identified → address each independently, prioritize by recurrence risk. (3) Root cause is in a third-party dependency → document and create upstream issue.

---

#### Step 8: Design Permanent Fix

**Type:** automated
**Actor:** Claude
**Input:** RCA from Step 7
**Action:**

1. Design the permanent fix (if the stabilization fix was temporary)
2. Design preventive measures:
   - Tests that would have caught this
   - Monitoring/alerting that would have detected it sooner
   - Documentation that would have helped diagnosis
3. If permanent fix is complex (>10 files, architecture change): suggest delegating to WF2
4. If permanent fix is simple: proceed within WF11

**Output:** Fix design: { permanent_fix, preventive_measures, complexity_assessment }
**Failure mode:** (1) No permanent fix possible within current architecture → delegate to WF2 with full context. (2) Fix requires database migration → include in WF2 delegation scope. (3) Stabilization fix IS the permanent fix → skip to Step 10 with confirmation.

---

#### Step 9: Quality Gate — RCA Critique

**Type:** quality gate
**Actor:** sub-agent
**Command:** `/reflexion:reflect` (lightweight — RCA is already thorough)
**Input:** RCA document + fix design
**Action:**

Reflect on:

- Is the root cause actually the ROOT cause (not a symptom)?
- Does the permanent fix address the root cause (not just the symptom)?
- Are the preventive measures sufficient to prevent recurrence?
- Are there related areas that might have the same vulnerability?

**Output:** Validated RCA and fix design
**Failure mode:** (1) Reflect determines root cause is a symptom, not the actual root → loop back to Step 7 for deeper analysis. (2) Preventive measures are insufficient → expand scope.
**Principle alignment:** P8

---

#### Step 10: Implement Permanent Fix (if within WF11 scope)

**Type:** automated
**Actor:** Claude
**Input:** Validated fix design from Step 9
**Action:**

If the fix is simple enough for WF11:

1. Create hotfix branch: `git checkout -b hotfix/<incident-desc>`
2. Write test that reproduces the incident condition
3. Implement the permanent fix
4. Run all tests
5. Commit: `hotfix(scope): <incident description> [incident-RCA]`
6. Abbreviated code review (manual, not full 4-agent — hotfixes are urgent)
7. Create PR and merge (fast-track)
8. Deploy

If complex: create GitHub issue and delegate to WF2/WF3.

**Output:** Permanent fix deployed OR GitHub issue created for delegation
**Failure mode:** (1) Reproduction test passes immediately → stabilization fix already resolved permanently. (2) Fix breaks other tests → investigate shared state. (3) Fix scope exceeds WF11 → create issue and delegate.
**Principle alignment:** P5 (TDD), P1 (Branch Isolation)

---

#### Step 11: Implement Preventive Measures

**Type:** automated
**Actor:** Claude
**Input:** Preventive measures from Step 8
**Action:**

1. Add missing tests (committed alongside permanent fix or in separate PR)
2. Update monitoring/alerting configuration (if applicable)
3. Add diagnostic commands to quick playbook (if this incident type is new)
4. Update CLAUDE.md with new pitfalls or patterns

**Output:** Preventive measures implemented
**Failure mode:** (1) Monitoring/alerting requires infrastructure changes beyond session scope → create GitHub issue for follow-up. (2) Playbook update conflicts with existing entries → merge and resolve.
**Principle alignment:** P9 (Memorization — incident patterns are the most valuable memories)

---

#### Step 12: Create Action Items

**Type:** automated
**Actor:** Claude
**Input:** All Phase B findings, preventive measures not yet implemented
**Action:**

1. Create GitHub issues for:
   - Any preventive measures not implemented in this session
   - Related areas that may have the same vulnerability
   - Monitoring/alerting improvements
   - Documentation gaps identified during incident
2. Label issues appropriately (incident-followup, priority based on severity)

**Output:** GitHub issues created
**Failure mode:** (1) Too many action items → prioritize by severity and create issues only for top items, defer the rest.
**Principle alignment:** P14

---

#### Step 13: Memorize Incident Pattern

**Type:** automated
**Actor:** Claude
**Command:** `/reflexion:memorize`
**Input:** Full incident data (RCA, fix, preventive measures)
**Action:**

Curate insights into CLAUDE.md:

- New entry in "Known Pitfalls" or relevant section
- Update "Known Recurring Issues" if this is a pattern
- Add to quick diagnostic playbook if applicable
- Document the root cause and fix approach for future reference

**Output:** CLAUDE.md + MEMORY.md updated
**Failure mode:** (1) CLAUDE.md is too long → move detailed incident patterns to a dedicated memory topic file and link from MEMORY.md. (2) Pattern already documented → update existing entry rather than creating duplicate.
**Principle alignment:** P9 (Continuous Memorization — incidents produce the most valuable learnings)

---

#### Step 14: Incident Closure

**Type:** automated
**Actor:** Claude
**Input:** All artifacts
**Action:**

1. Compile final incident report:
   - Severity, duration, impact
   - Root cause (5 Whys)
   - Stabilization actions
   - Permanent fix (or delegation)
   - Preventive measures
   - Action items
2. Update session notes with full incident report
3. Close incident GitHub issue with report summary
4. Present to user

**Output:** Incident closed, report complete
**Failure mode:** (1) GitHub issue doesn't exist yet → create one with the incident report as the body. (2) Action items still open → note in closure summary that follow-up work remains.
**Principle alignment:** P14

---

## Design Decisions

### D1: Two-Phase Approach (Stabilize, Then Analyze)

**Rationale:** During an active incident, the priority is restoring service, not understanding why it broke. Mixing diagnosis with analysis leads to longer outages. Phase A is time-boxed and action-oriented. Phase B is thorough and prevention-oriented. They can happen in the same or different sessions.

### D2: Relaxed Principles During Phase A

**Rationale:** During an active SEV-1 incident, running Prettier or doing a full 4-agent code review wastes critical minutes. Principles P2, P4, and P13 are relaxed during stabilization but enforced during the permanent fix (Phase B, Step 10).

### D3: Quick Diagnostic Playbook

**Rationale:** Common incidents (service down, DB issues, engine problems) have known diagnostic paths. Encoding these in the workflow saves time during stressful incidents. The playbook grows with each incident (via Step 11/13).

### D4: 5 Whys for Root Cause

**Rationale:** The "5 Whys" technique prevents stopping at the proximate cause (e.g., "server ran out of memory") and pushes to the systemic cause (e.g., "no load test for the new query pattern"). This produces actionable preventive measures.

### D5: Mandatory Memorization

**Rationale:** Incidents are the most valuable learning opportunities. Every incident produces patterns, pitfalls, and diagnostic techniques that prevent future incidents. Memorization is not conditional in WF11 — it ALWAYS runs.

### D6: Delegation for Complex Permanent Fixes

**Rationale:** If the permanent fix requires 15+ files, architectural changes, or database migrations, it's too complex for a hotfix workflow. Delegate to WF2/WF3 where the full design-critique-plan-TDD pipeline can ensure quality. The temporary stabilization fix buys time.

---

## Principle Coverage Matrix

| Principle                | Phase A           | Phase B         | How                               |
| ------------------------ | ----------------- | --------------- | --------------------------------- |
| P1 Branch Isolation      | N/A (direct fix)  | Yes (Step 10)   | hotfix/ branch                    |
| P2 Code Formatting       | Relaxed           | Yes             | Automated in permanent fix        |
| P3 Frequent Commits      | Yes               | Yes             | During fix implementation         |
| P4 Remote Sync           | Relaxed           | Yes             | Push when ready                   |
| P5 TDD Enforcement       | Abbreviated       | Yes             | Step 10: test reproduces incident |
| P6 Main-to-Dev Sync      | Yes               | Yes             | Deploy immediately                |
| P7 Triple-Gate           | Abbreviated       | Yes             | Step 10: local + CI + deploy      |
| P8 Shift-Left Critique   | N/A               | Yes             | Step 9: RCA reflect               |
| P9 Memorization          | N/A               | Yes (mandatory) | Step 13                           |
| P10 Diagram-Driven       | N/A               | N/A             | Incidents rarely need diagrams    |
| P11 User-in-the-Loop     | Yes (destructive) | Yes             | Steps 3, 6                        |
| P12 Conventional Commits | Yes               | Yes             | Step 10                           |
| P13 Pre-PR Review        | Relaxed           | Abbreviated     | Step 10                           |
| P14 Documentation-Gated  | Yes               | Yes             | Steps 6, 12, 14                   |
