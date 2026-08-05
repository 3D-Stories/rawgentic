# Phase B — Analyze & Prevent (Steps 7–14)

Read this before executing Steps 7–14. All principles are fully enforced again here
(the Phase-A relaxations in `<principle-relaxations>` no longer apply). Steps 11–14 are
mandatory even when the Phase A fix IS the permanent fix — the `<mandatory-rule>` in
`SKILL.md` owns that, not this file.

---

## Step 7: Root Cause Analysis (5 Whys)

### Instructions

Update `claude_docs/session_notes.md` with: Phase A summary, stabilization actions taken, Phase B RCA plan.

1. **Timeline reconstruction:** Map exact sequence from first symptom to resolution.
2. **5 Whys analysis:** Starting from symptom, ask "why?" repeatedly. Document each level — minimum 3 levels required:
   ```
   5 Whys:
   1. Why did the incident happen? → [direct cause]
   2. Why did [direct cause] exist? → [design/implementation gap]
   3. Why did [design gap] exist? → [process gap]
   4. Why did [process gap] exist? → [organizational/knowledge gap]
   5. Why did [organizational gap] exist? → [root cause]
   ```
   Each level must be documented, not just the final answer. Stop when you reach a cause that is actionable (can be fixed by a process change, test, or code change).
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

## Step 8: Design Permanent Fix

### Instructions

1. Design permanent fix (if stabilization was temporary).
2. Design preventive measures: tests, monitoring, documentation.
3. **Document the fix design** in a comment on the incident tracking issue (or in session notes) BEFORE writing code. The design must be reviewable independently of the implementation. Step 10 should reference this design.
4. If complex (>10 files, architecture change): delegate to WF2.
5. If simple: proceed within WF11.
6. Log in session notes: `### WF11 Step 8: Design Fix — DONE (scope: WF11|WF2, complexity: simple|complex, design: documented)`

### Failure Modes

- No permanent fix possible within current architecture → delegate to WF2 with full context
- Fix requires database migration → include in WF2 delegation scope
- Stabilization fix IS the permanent fix → skip to Step 10 with confirmation

---

## Step 9: Quality Gate — RCA Critique

### Instructions

Apply the quality-bar rubric — a skeptical, lightweight self-review (cite evidence, don't rubber-stamp) — over:

- Is the root cause actually the ROOT cause (not a symptom)?
- Does the permanent fix address the root cause?
- Are preventive measures sufficient?
- Related areas with same vulnerability?

Log in session notes: `### WF11 Step 9: RCA Critique — DONE (confidence: high|medium|low)`

### Failure Modes

- Reflect determines root cause is a symptom, not the actual root → loop back to Step 7 for deeper analysis
- Preventive measures are insufficient → expand scope of monitoring and test coverage

---

## Step 10: Implement Permanent Fix

### Instructions

If fix is within WF11 scope:

1. Create hotfix branch from a freshly-fetched default (a stale `origin/<default_branch>` ref would silently base the hotfix on old code): `git fetch origin <default_branch> && git checkout -b hotfix/<incident-desc> origin/<default_branch>`
2. Write test reproducing the incident condition.
3. Implement permanent fix.
4. Run all tests.
5. Commit using `hotfix(scope):` prefix — NOT `fix(scope):`. The `hotfix()` prefix distinguishes emergency incident fixes from normal bug fixes in git history, which is important for post-incident analysis and release notes. Example: `hotfix(engine): prevent duplicate order execution [incident-RCA]`
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

## Step 11: Implement Preventive Measures

### Instructions

1. Add missing tests that would have caught this incident.
2. **Same-class bug scan:** If the root cause is a missing parameter, wrong default, or interface mismatch — grep for ALL callers of the affected function and verify they don't have the same bug. Log findings in session notes.
3. Update monitoring/alerting (if applicable).
4. Add diagnostic commands to quick playbook (if new incident type) — `references/quick-diagnostic-playbook.md`.
5. **Update or create operational playbook entry** for this incident class. Include: detection signals, immediate containment actions, verification steps. Link from project docs or CLAUDE.md.
6. Update `.rawgentic.json` custom section or session notes with new pitfalls or patterns.

Log in session notes: `### WF11 Step 11: Preventive Measures — DONE (N items, playbook: updated|created|N/A)`

### Failure Modes

- Monitoring/alerting requires infrastructure changes beyond session scope → create GitHub issue for follow-up
- Playbook update conflicts with existing entries → merge and resolve duplicates
- Same-class scan finds additional bugs → fix them in the same PR or create separate issues

---

## Step 12: Create Action Items

### Instructions

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

## Step 13: Memorize Incident Pattern

### Instructions

Incidents produce the MOST valuable learnings — for each pattern, curate it into memory: if a mempalace MCP server is available (`mcp__mempalace__*` tools loaded), store it via `mempalace_kg_add` (a fact/decision) or `mempalace_add_drawer` (a note), scoped to this project; otherwise — or if the mempalace store call fails — append it to the project `CLAUDE.md` / `MEMORY.md`:

- Save new pitfall patterns
- Update recurring issue patterns if this is a known class of failure
- Add to quick diagnostic playbook
- Document root cause and fix approach

Log in session notes: `### WF11 Step 13: Memorize — DONE (N patterns saved)`

### Failure Modes

- Too many patterns to memorize at once → prioritize by recurrence risk, save the most critical ones first
- Pattern already documented → update existing entry rather than creating a duplicate

---

## Step 14: Incident Closure

### Instructions

1. Compile final incident report:
   - Severity, duration, impact
   - Root cause (5 Whys chain)
   - Stabilization actions
   - Permanent fix (or delegation)
   - Preventive measures
   - Action items
2. Update session notes.
3. **Close the incident tracking issue** (created in Step 1) with a final summary comment linking PR, post-mortem findings, and follow-up action item issues.
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

Memorized: [N patterns saved to mempalace / CLAUDE.md]

WF11 complete.
```

Log in session notes: `### WF11 Step 14: Incident Closure — DONE`

### Failure Modes

- GitHub issue doesn't exist yet → create one with the incident report as the body
- Action items still open → note in closure summary that follow-up work remains
- Session notes too long → archival to JSONL happens automatically on next session startup
