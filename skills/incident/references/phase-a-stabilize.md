# Phase A — Stabilize (Steps 1–6)

Read this before executing Steps 1–6. Speed over perfection: restore service first,
analyze later. The two Phase-A safety rules — the SEV-1/SEV-2
`<mandatory-verification>` gate and "get user approval before any destructive action"
— live in `SKILL.md`, not here, because a safety gate must not depend on this file
having been read. This file is their elaboration and the step detail.

---

## Step 1: Receive Incident Report

### Instructions

1. **Load config FIRST** — execute the `<config-loading>` block to populate `config` and `capabilities`. Log the resolved values in session notes.
2. Log incident start time (UTC).
3. Classify severity (SEV-1 through SEV-4).
4. Identify affected services and user impact.
5. **Create incident tracking issue:** first ensure the label exists (`gh label create incident --repo ${capabilities.repo} --color b60205 --description "Active incident" 2>/dev/null || true` — `gh issue create --label` fails on a label the repo lacks), then `gh issue create --repo ${capabilities.repo} --title "incident(SEV-X): [brief description]" --body "[initial assessment]" --label incident`. This issue tracks the full incident lifecycle — timeline, root cause, fix, verification, and follow-up items.
6. **SEV-1/SEV-2:** Skip confirmation, proceed immediately to diagnosis.
7. **SEV-3/SEV-4:** Confirm priority with user.
8. Update `claude_docs/session_notes.md` with: resolved config summary, incident description, severity classification, initial impact assessment, incident issue number.
9. Log in session notes: `### WF11 Step 1: Receive Incident Report — DONE (issue: #N)`

### Failure Modes

- Description too vague → ask for symptoms and affected services
- Multiple simultaneous incidents → triage by severity, handle SEV-1 first
- Incident is actually a feature request → redirect to WF1/WF2

---

## Step 2: Rapid Diagnosis

### Instructions

**Fast-path for code-level bugs:** If the error message identifies a specific code location (stack trace, SQL constraint violation with column name, module path in traceback), skip infrastructure checks (items 1, 4, 5) and go directly to code analysis. Still verify the service is running (item 2) but don't waste time on Docker stats or connectivity when the error is obviously a code bug.

**Full diagnostic path (infrastructure/unknown issues):**

1. **Check recent deployments:** `git log --oneline -5` on affected servers — deploy-caused?
2. **Check service health:** Hit health endpoints for all services.
3. **Check logs:** Tail last 200 lines, look for errors/exceptions.
4. **Check resources:** Docker stats for CPU/memory/disk.
5. **Check connectivity:** Services reaching each other? (verify inter-service dependencies from `config.services[]`)
6. **Use quick diagnostic playbook** for the incident type — read `references/quick-diagnostic-playbook.md`.
7. **Form hypothesis:** Most likely cause based on evidence.

Log in session notes: `### WF11 Step 2: Rapid Diagnosis — DONE (fast-path|full)`

### Failure Modes

- Can't SSH to server → check if server is down entirely (ping hosts from `config.infrastructure.hosts[]`, then check hosting console)
- No obvious errors in logs → check for silent failures (process exit without logging, OOM kills in `dmesg`)
- Multiple simultaneous failures → prioritize by dependency order (DB → API → frontend)

---

## Step 3: Determine Stabilization Strategy

### Instructions

Choose one (safest to most invasive):

1. **Restart:** Transient failure (OOM, connection drop) → restart service
2. **Rollback:** Recent deploy caused it → `git revert` and redeploy
3. **Config fix:** Misconfiguration → fix config and restart
4. **Code fix:** Bug identified in code → fix, test, deploy
5. **Workaround:** Complex root cause → temporary fix (disable feature, increase resources)
6. **Escalate:** Inconclusive diagnosis + SEV-1 → escalate to user

**The destructive-action approval rule is in `SKILL.md` and governs options 2 and 3
and any DB operation.** It is not restated as procedure here on purpose: it must hold
whether or not this file was read.

Log in session notes: `### WF11 Step 3: Strategy — [chosen strategy] (temporary|permanent)`

### Failure Modes

- Diagnosis inconclusive → present multiple strategies ranked by reversibility
- All strategies are destructive → require user approval
- Root cause spans multiple services → address in dependency order (DB first, then API, then frontend)

---

## Step 4: Execute Stabilization

### Instructions

1. Execute chosen strategy.
2. Monitor recovery: health endpoints, logs for new errors, user-facing functionality.
3. If first strategy fails, try next option from Step 3.
4. Log in session notes: `### WF11 Step 4: Execute — DONE (branch: <name>, commit: <sha>)`

### Failure Modes

- Strategy doesn't work → try next option from Step 3 list (restart → config fix → rollback → workaround)
- All strategies fail → escalate to user with full diagnostic data
- Rollback requires user approval → present the action and wait for confirmation before proceeding

---

## Step 5: Verify Service Restoration

**`<mandatory-verification>` in `SKILL.md` governs this step for SEV-1/SEV-2 and is
non-skippable.** The items below are how you satisfy it, not whether you must.

### Instructions

1. All health endpoints return healthy.
2. Critical user paths work (dashboard loads, data appears).
3. Core service processing verified (check each service in `config.services[]` as applicable).
4. Monitor 5 minutes — no recurring errors.
5. SEV-1/SEV-2: run abbreviated E2E smoke test. **Include evidence** (API response, log excerpt, or screenshot) in session notes.
6. Log in session notes: `### WF11 Step 5: Verify Restoration — DONE (health: OK|FAIL, E2E: OK|SKIP, evidence: <type>)`

### Failure Modes

- Health passes but user-facing broken → check application-level errors
- Recurring errors after monitoring → loop back to Step 3 with new evidence
- E2E fails → investigate specific failure

---

## Step 6: Stabilization Summary

### Instructions

1. Document in session notes: incident timeline, stabilization actions, temporary vs permanent fix.
2. Ask user: proceed to Phase B (RCA) now or in a separate session?
3. Log in session notes: `### WF11 Step 6: Stabilization Summary — DONE (Phase B: now|later)`

### Failure Modes

- User is unavailable for Phase B decision → default to separate session (Phase A is complete, service is restored)
- Session notes too long to capture full timeline → archival to JSONL happens automatically on next session startup
