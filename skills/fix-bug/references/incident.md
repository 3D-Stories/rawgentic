# WF3 incident lane — comms + post-mortem checklist

When a bug fix is an **INCIDENT** (production down, data-loss risk, time-critical
outage), run WF3's hotfix lane — branch `hotfix/<incident-desc>` from a
freshly-fetched default, `hotfix(scope):` commit prefix, abbreviated review,
fast-track PR — **plus** the communications and post-mortem checklist below. This
pairing **replaces the deprecated standalone WF11 flow**: the checklist content
here is copied from WF11 (the incident-response workflow) and attributed to it.
The point of the incident lane is that a bug fix under an active outage needs two
things a routine fix does not — *communication while it burns* and a *post-mortem
after it is out* — and both used to live only in WF11.

Reproduce-first still applies in spirit (a failing test that captures the
incident condition), but during Phase A stabilization, speed wins: push when the
fix is ready, not on a schedule; abbreviated review for hotfixes; formatting can
wait. All principles are fully enforced again once the service is stable.

---

## Severity triage (WF11 constants)

Classify the incident to set the comms cadence:

- **SEV-1:** complete outage, data-loss risk -> immediate response
- **SEV-2:** partial outage, degraded service -> < 30 min response
- **SEV-3:** minor degradation, workaround exists -> < 4 hours
- **SEV-4:** cosmetic or non-urgent -> next session

SEV-1/SEV-2: skip confirmation, proceed immediately to diagnosis. SEV-3/SEV-4:
confirm priority with the user.

---

## Communications & status updates (copied from WF11)

**Open an incident tracking issue immediately (WF11 Step 1).** This issue tracks
the full incident lifecycle — timeline, root cause, fix, verification, and
follow-up items:

```
gh issue create --repo ${capabilities.repo} \
  --title "incident(SEV-X): [brief description]" \
  --body "[initial assessment]" --label incident
```

**Verify restoration with evidence before you declare it stable (WF11 Step 5).**
For SEV-1/SEV-2 this is mandatory and non-skippable: include evidence (API
response, log excerpt, or screenshot) proving containment worked, and post it to
the tracking issue / session notes.

**Close the loop when the fix is out (WF11 Step 14).** Close the incident
tracking issue (created above) with a final summary comment **linking the PR, the
post-mortem findings, and the follow-up action-item issues.** Then present the
closure report to the user:

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
```

---

## Post-mortem: root cause (5 Whys, copied from WF11 Step 7)

Reconstruct the timeline (first symptom -> resolution), then run 5 Whys —
**minimum 3 levels**, each documented, not just the final answer:

```
5 Whys:
1. Why did the incident happen? -> [direct cause]
2. Why did [direct cause] exist? -> [design/implementation gap]
3. Why did [design gap] exist? -> [process gap]
4. Why did [process gap] exist? -> [organizational/knowledge gap]
5. Why did [organizational gap] exist? -> [root cause]
```

Stop when you reach a cause that is actionable (fixable by a process change, a
test, or a code change). Also capture **contributing factors** — missing
monitoring/alerting, missing tests, missing documentation, insufficient resource
limits.

---

## Preventive measures (copied from WF11 Step 11)

1. Add missing tests that would have caught this incident.
2. **Same-class bug scan:** if the root cause is a missing parameter, wrong
   default, or interface mismatch — grep for ALL callers of the affected function
   and verify they don't have the same bug. Log findings in session notes.
3. Update monitoring/alerting (if applicable).
4. Add diagnostic commands to a quick playbook (if a new incident type).
5. **Update or create an operational playbook entry** for this incident class.
   Include: detection signals, immediate containment actions, verification steps.
   Link it from project docs or CLAUDE.md.
6. Update `.rawgentic.json` custom section or session notes with new pitfalls or
   patterns.

---

## Action items (copied from WF11 Step 12)

Create GitHub issues for:

- Preventive measures not implemented this session
- Related areas with the same vulnerability
- Monitoring/alerting improvements
- Documentation gaps

Label: `incident-followup`, priority based on severity. If there are too many,
prioritize by severity and defer the rest.

---

## Memorize the pattern (copied from WF11 Step 13)

Incidents produce the MOST valuable learnings. For each, curate it into memory: if a mempalace MCP server is available (`mcp__mempalace__*` tools loaded), store it via `mempalace_kg_add` (a fact/decision) or `mempalace_add_drawer` (a note), scoped to this project; otherwise — or if the mempalace store call fails — append it to the project `CLAUDE.md` / `MEMORY.md`:

- Save new pitfall patterns
- Update recurring-issue patterns if this is a known class of failure
- Add to the quick diagnostic playbook
- Document the root cause and fix approach

Even if the Phase A fix IS the permanent fix, the post-mortem tasks above
(preventive measures, action items, memorize, closure) are **not part of the fix
itself** — they still run before the incident is considered closed.
