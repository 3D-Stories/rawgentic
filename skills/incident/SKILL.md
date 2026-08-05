---
name: incident
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

<phase-step-mapping>
Always reference steps by number AND name to avoid confusion:

| Phase | Steps | Purpose |
|-------|-------|---------|
| Phase A (Stabilize) | Steps 1–6 | Rapid service restoration |
| Phase B (Analyze & Prevent) | Steps 7–14 | Root cause analysis and permanent fix |

If step numbering feels wrong during execution, re-check this table.
</phase-step-mapping>

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

2. Load the config and derive capabilities with the helper CLI (one tested
   source of truth — never hand-derive the `capabilities` object, so every
   config-driven skill and the docs table cannot drift apart):
   ```bash
   python3 hooks/capabilities_lib.py derive \
     --config <activeProject.path>/.rawgentic.json
   ```
   - **Non-zero exit** -> the config is missing, corrupt, or invalid. **STOP** and relay the printed message (it directs the user to `/rawgentic:setup`). A `config.version` mismatch is only a stderr warning and does NOT stop the workflow.
   - **Exit 0** -> stdout is `{"config": {...}, "capabilities": {...}}`. Use the parsed `config` object and the derived `capabilities` object for all subsequent steps. The `capabilities` fields are: `has_tests`, `test_commands`, `has_ci`, `ci_quarantined`, `ci_quarantine_reason`, `ci_quarantined_since`, `has_deploy`, `deploy_method`, `has_database`, `has_docker`, `project_type`, `repo`, `default_branch`, `migration_dir`. Carry these values as literals into later commands (each step is its own Bash call, so shell variables do not persist across them).

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

<step-marker-enforcement>
Each step MUST begin with its marker logged in session notes. Skipping a marker = skipping the step — this is not allowed.
Before transitioning between phases (Phase A → Phase B), list ALL completed step markers in session notes. Any missing markers must be either completed or explicitly justified with user approval.
</step-marker-enforcement>

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

<safety-gates>
These two rules are in the always-loaded body ON PURPOSE (#909). Both are enforcement,
not procedure, and neither may depend on a `references/` file having been read — a
reference read is prose-enforced, so an incident could otherwise skip a safety gate and
produce no error and no test failure.

<mandatory-verification>
**For SEV-1 and SEV-2: Step 5 is MANDATORY and non-skippable.** Must include evidence (screenshot, API response, or log excerpt) proving containment worked. Cannot proceed to Phase B without Step 5 sign-off from the user.
</mandatory-verification>

**For destructive actions (rollback, DB operations): Always get user approval first.**
This governs Step 3's rollback and config-fix strategies and every database operation in
any step.
</safety-gates>

<references>
Progressive disclosure. This file carries the contract, the safety gates, and a
one-line-per-step spine; the full per-step instructions and failure modes live in
per-phase reference files, read on demand by this contract:
- `references/phase-a-stabilize.md` — Steps 1–6 in full. **Read before executing Steps 1–6.**
- `references/phase-b-analyze.md` — Steps 7–14 in full. **Read before executing Steps 7–14.**
- `references/quick-diagnostic-playbook.md` — per-class first moves (service / database /
  service-specific). **Read before executing Step 2 item 6.**
</references>

---

## Phase A: Stabilize (Steps 1–6)

Speed over perfection. **Read `references/phase-a-stabilize.md` before executing Steps 1–6.**

- **Step 1 — Receive Incident Report.** Load config first; log start time (UTC); classify SEV-1..SEV-4; identify affected services and impact; create the incident tracking issue (bootstrap the `incident` label BEFORE using it); SEV-1/2 skip confirmation, SEV-3/4 confirm priority.
- **Step 2 — Rapid Diagnosis.** Fast-path straight to code analysis when the error names a code location; otherwise deployments → health → logs → resources → connectivity → playbook → hypothesis.
- **Step 3 — Determine Stabilization Strategy.** Pick the least invasive that works: restart → rollback → config fix → code fix → workaround → escalate. Rollback, config fix, and any DB operation are governed by `<safety-gates>`.
- **Step 4 — Execute Stabilization.** Execute; monitor recovery; fall through to the next strategy on failure.
- **Step 5 — Verify Service Restoration.** Health, critical user paths, 5-minute watch, and for SEV-1/2 an abbreviated E2E smoke with evidence. Governed by `<mandatory-verification>` in `<safety-gates>` — non-skippable for SEV-1/2.
- **Step 6 — Stabilization Summary.** Timeline + temporary-vs-permanent; ask the user whether Phase B runs now or in a separate session.

---

<mandatory-rule>
EVEN IF the Phase A fix is the permanent fix, Steps 11-14 are NEVER optional.
After deployment verification (Step 5), you MUST eventually execute:
- Step 11: Preventive measures (test gaps, .rawgentic.json, playbook, same-class bug scan)
- Step 12: Action items (GitHub issues for systemic findings)
- Step 13: Memorize (mempalace / `CLAUDE.md`)
- Step 14: Formal closure (WF11 COMPLETE template)

When the Phase A fix IS the permanent fix:

- Steps 7-10 may be abbreviated (5 Whys can be inline, no separate design/implement cycle)
- Steps 11-14 remain MANDATORY — these are POST-FIX tasks, not part of the fix itself
- Step 6 MUST still ask the user whether to proceed to Phase B now or later

You may NOT declare WF11 complete until the completion-gate (after Step 14) passes.
</mandatory-rule>

## Phase B: Analyze & Prevent (Steps 7–14)

All principles are fully enforced again here. **Read `references/phase-b-analyze.md` before
executing Steps 7–14.**

- **Step 7 — Root Cause Analysis (5 Whys).** Timeline reconstruction; 5 Whys with a minimum of 3 documented levels; contributing factors.
- **Step 8 — Design Permanent Fix.** Design the fix and the preventive measures, and document the design on the tracking issue BEFORE any code; delegate to WF2 if complex (>10 files or an architecture change).
- **Step 9 — Quality Gate: RCA Critique.** Quality-bar rubric over root-cause depth, fix adequacy, preventive sufficiency, and same-class exposure.
- **Step 10 — Implement Permanent Fix.** Hotfix branch from a freshly-fetched default; reproduction test first; `hotfix(scope):` commit prefix; PR and deploy.
- **Step 11 — Implement Preventive Measures.** Missing tests, same-class caller scan, monitoring, playbook entry, config/pitfall capture.
- **Step 12 — Create Action Items.** GitHub issues for everything not done this session, labelled `incident-followup`.
- **Step 13 — Memorize Incident Pattern.** Curate to mempalace when available, else the project `CLAUDE.md` / `MEMORY.md`.
- **Step 14 — Incident Closure.** Final report, close the tracking issue, print the WF11 COMPLETE template.

---

<completion-gate>
Before declaring WF11 complete, verify ALL of the following. Print the checklist with pass/fail for each item:

1. [ ] Step markers logged for ALL 14 steps in session notes (list each marker and verify presence)
2. [ ] Service health verified (Step 5 marker present with evidence)
3. [ ] Step 6 gate: user asked about Phase B timing
4. [ ] Step 11: Preventive measures implemented (test gaps, same-class scan, .rawgentic.json or session notes)
5. [ ] Step 12: Action items created as GitHub issues
6. [ ] Step 13: Patterns memorized (mempalace / `CLAUDE.md`)
7. [ ] Step 14: WF11 COMPLETE template printed to user
8. [ ] Session notes updated with final incident report
9. [ ] Incident tracking issue (created in Step 1) closed with final summary

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
