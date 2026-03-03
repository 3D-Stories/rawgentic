---
name: rawgentic:incident
description: Respond to a production incident using the WF11 14-step two-phase workflow (stabilize first, then RCA). Phase A restores service rapidly with relaxed principles. Phase B conducts 5 Whys root cause analysis and implements preventive measures. Invoke with /incident followed by a description of the incident.
argument-hint: Incident description (e.g., "dashboard not loading", "engine stopped trading") or issue number
---

# WF11: Incident Response & Root Cause Analysis Workflow

<role>
You are the WF11 orchestrator implementing a 14-step incident response workflow in two phases. Phase A (Steps 1-6) prioritizes rapid service restoration — speed over perfection. Phase B (Steps 7-14) conducts thorough root cause analysis and implements preventive measures. You fix first, analyze later.
</role>

<constants>
REPO = "<inferred from `git remote -v` at workflow start>"
PROJECT_ROOT = "<inferred from `git rev-parse --show-toplevel`>"
ENGINE_HOST = "<from CLAUDE.md infrastructure — darwin server IP>"
POSTGRES_CONTAINER = "<from CLAUDE.md infrastructure — PostgreSQL container name>"
DB_USER = "<from CLAUDE.md database section — dev database user>"
DB_NAME = "<from CLAUDE.md database section — dev database name>"
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

<environment-setup>
Constants are populated at workflow start (Step 1) by running:
- `REPO`: `git remote get-url origin | sed 's|.*github.com[:/]||;s|\.git$||'`
- `PROJECT_ROOT`: `git rev-parse --show-toplevel`
- `ENGINE_HOST`: Read from CLAUDE.md infrastructure section (darwin server IP)
- `POSTGRES_CONTAINER`: Read from CLAUDE.md infrastructure section (PostgreSQL container name)
- `DB_USER`: Read from CLAUDE.md database section (dev database user)
- `DB_NAME`: Read from CLAUDE.md database section (dev database name)
- Other constants: Read from CLAUDE.md infrastructure and database sections

If any constant cannot be resolved, STOP and ask the user. Do not assume values.
</environment-setup>

<termination-rule>
WF11 terminates after RCA is complete and action items are created. Permanent fix may be delegated to WF2/WF3.
</termination-rule>

<ambiguity-circuit-breaker>
During Phase B only: if root cause is uncertain, multiple contributing factors conflict, or fix could destabilize other services — STOP and present to user for resolution. In Phase A, bias toward action over analysis (stabilize first). User has final authority (P11).
</ambiguity-circuit-breaker>

<context-compaction>
Per CLAUDE.md shared invariant #9: before context compaction, document in `claude_docs/session_notes.md`: current phase (A/B), current step number, branch name, last commit SHA, severity level, and whether service is stabilized.
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

1. `docker compose -f <compose-file> ps` — check container status
2. `docker compose -f <compose-file> logs -f <service> --tail=200` — check logs
3. `curl http://<host>:<port>/health` — health check
4. `docker stats` — check resources
5. Common fixes: restart container, increase memory, fix config

### Database Issues

1. `docker exec ${POSTGRES_CONTAINER} pg_isready` — PostgreSQL up?
2. `docker exec ${POSTGRES_CONTAINER} psql -U ${DB_USER} -c "SELECT count(*) FROM pg_stat_activity"` — connections
3. `docker exec ${POSTGRES_CONTAINER} psql -U ${DB_USER} -c "SELECT * FROM pg_stat_activity WHERE state = 'active' AND query_start < now() - interval '30 seconds'"` — slow queries
4. Common fixes: kill hung queries, restart PostgreSQL, check disk space

### Trading Engine Issues

1. `curl -H "X-API-Key: $KEY" http://${ENGINE_HOST}:8080/status` — engine status
2. Engine logs: look for "connected"/"disconnected" — IBKR connection
3. `curl -H "X-API-Key: $KEY" http://${ENGINE_HOST}:8080/scheduler` — scheduler status
4. Common fixes: restart engine, restart IB Gateway, check market hours

</quick-diagnostic-playbook>

---

<!-- PHASE A: STABILIZE -->

## Phase A: Stabilize

### Step 1: Receive Incident Report

#### Instructions

1. Log incident start time.
2. Classify severity (SEV-1 through SEV-4).
3. Identify affected services and user impact.
4. **SEV-1/SEV-2:** Skip confirmation, proceed immediately to diagnosis.
5. **SEV-3/SEV-4:** Confirm priority with user.
6. Update `claude_docs/session_notes.md` with: incident description, severity classification, initial impact assessment.

### Failure Modes

- Description too vague → ask for symptoms and affected services
- Multiple simultaneous incidents → triage by severity, handle SEV-1 first
- Incident is actually a feature request → redirect to WF1/WF2

---

### Step 2: Rapid Diagnosis

#### Instructions

1. **Check recent deployments:** `git log --oneline -5` on affected servers — deploy-caused?
2. **Check service health:** Hit health endpoints for all services.
3. **Check logs:** Tail last 200 lines, look for errors/exceptions.
4. **Check resources:** Docker stats for CPU/memory/disk.
5. **Check connectivity:** Services reaching each other? (dashboard↔postgres, engine↔postgres, engine↔redis)
6. **Use quick diagnostic playbook** for the incident type.
7. **Form hypothesis:** Most likely cause based on evidence.

### Failure Modes

- Can't SSH to server → check if server is down entirely (ping first, then Proxmox console)
- No obvious errors in logs → check for silent failures (process exit without logging, OOM kills in `dmesg`)
- Multiple simultaneous failures → prioritize by dependency order (DB → API → frontend)

---

### Step 3: Determine Stabilization Strategy

#### Instructions

Choose one (safest to most invasive):

1. **Restart:** Transient failure (OOM, connection drop) → restart service
2. **Rollback:** Recent deploy caused it → `git revert` and redeploy
3. **Config fix:** Misconfiguration → fix config and restart
4. **Workaround:** Complex root cause → temporary fix (disable feature, increase resources)
5. **Escalate:** Inconclusive diagnosis + SEV-1 → escalate to user

**For destructive actions (rollback, DB operations):** Always get user approval first.

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

### Failure Modes

- Strategy doesn't work → try next option from Step 3 list (restart → config fix → rollback → workaround)
- All strategies fail → escalate to user with full diagnostic data
- Rollback requires user approval → present the action and wait for confirmation before proceeding

---

### Step 5: Verify Service Restoration

#### Instructions

1. All health endpoints return healthy.
2. Critical user paths work (dashboard loads, data appears).
3. Engine processing (if applicable).
4. Monitor 5 minutes — no recurring errors.
5. SEV-1/SEV-2: run abbreviated E2E smoke test.

### Failure Modes

- Health passes but user-facing broken → check application-level errors
- Recurring errors after monitoring → loop back to Step 3 with new evidence
- E2E fails → investigate specific failure

---

### Step 6: Stabilization Summary

#### Instructions

1. Document in session notes: incident timeline, stabilization actions, temporary vs permanent fix.
2. Ask user: proceed to Phase B (RCA) now or in a separate session?

### Failure Modes

- User is unavailable for Phase B decision → default to separate session (Phase A is complete, service is restored)
- Session notes too long to capture full timeline → archive to `session_notes_NNN.md` and start fresh for Phase B

---

<!-- PHASE B: ROOT CAUSE ANALYSIS -->

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

### Failure Modes

- Reflect determines root cause is a symptom, not the actual root → loop back to Step 7 for deeper analysis
- Preventive measures are insufficient → expand scope of monitoring and test coverage

---

### Step 10: Implement Permanent Fix

#### Instructions

If fix is within WF11 scope:

1. Create hotfix branch: `git checkout -b hotfix/<incident-desc> origin/main`
2. Write test reproducing the incident condition.
3. Implement permanent fix.
4. Run all tests.
5. Commit: `hotfix(scope): <incident description> [incident-RCA]`
6. Abbreviated code review (manual, not full 4-agent).
7. Create PR and merge (fast-track).
8. Deploy.

If complex: create GitHub issue and delegate to WF2/WF3.

### Failure Modes

- Reproduction test passes immediately → stabilization fix already resolved permanently; skip to Step 11 with confirmation
- Fix breaks other tests → investigate shared state between incident condition and existing tests
- Fix scope exceeds WF11 (>10 files, architecture change) → create issue and delegate to WF2/WF3

---

### Step 11: Implement Preventive Measures

#### Instructions

1. Add missing tests.
2. Update monitoring/alerting (if applicable).
3. Add diagnostic commands to quick playbook (if new incident type).
4. Update CLAUDE.md with new pitfalls or patterns.

### Failure Modes

- Monitoring/alerting requires infrastructure changes beyond session scope → create GitHub issue for follow-up
- Playbook update conflicts with existing entries → merge and resolve duplicates

---

### Step 12: Create Action Items

#### Instructions

Create GitHub issues for:

- Preventive measures not implemented this session
- Related areas with same vulnerability
- Monitoring/alerting improvements
- Documentation gaps

Label: `incident-followup`, priority based on severity.

### Failure Modes

- Too many action items → prioritize by severity, create issues only for top items and defer the rest
- GitHub issue creation fails → verify PAT scopes (Issues r/w), retry

---

### Step 13: Memorize Incident Pattern

#### Instructions

Run `/reflexion:memorize` — incidents produce the MOST valuable learnings. Curate into CLAUDE.md:

- New entry in "Known Pitfalls" or relevant section
- Update "Known Recurring Issues" if this is a pattern
- Add to quick diagnostic playbook
- Document root cause and fix approach

### Failure Modes

- CLAUDE.md is too long → move detailed incident patterns to a dedicated memory topic file and link from MEMORY.md
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

Memorized: [N patterns saved to CLAUDE.md]

WF11 complete.
```

### Failure Modes

- GitHub issue doesn't exist yet → create one with the incident report as the body
- Action items still open → note in closure summary that follow-up work remains
- Session notes too long → archive to `session_notes_NNN.md` and start fresh

---

## Workflow Resumption

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
