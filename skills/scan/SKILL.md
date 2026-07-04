---
name: rawgentic:scan
description: Run the full tool-based security scan (secrets, dependency CVEs, SAST, IaC) over the whole project tree via hooks/security_scan.py --full. The surviving tooling from the deprecated WF9 security-audit workflow — use for an on-demand whole-tree scan outside a PR gate. Invoke with /rawgentic:scan.
argument-hint: none (scans the active project's whole tree)
---

# Scan — whole-tree security scanners

<role>
You run the project's tool-based security scanners over the WHOLE tree — the
same fail-closed `hooks/security_scan.py` engine that gates every WF2/WF3 PR
at Step 11.5, but with `--full` (whole tree, not diff-scoped) and on demand.
This is tooling, not a workflow: no branch, no PR, no remediation loop — you
scan, report honestly, and stop. Remediation runs through WF2 with an issue.
</role>

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

## Step 1: Run the full scan

```bash
python3 hooks/security_scan.py scan \
  --project-root <activeProject.path> \
  --project-type <capabilities.project_type> \
  --full \
  --json
```

Append `--has-docker` when `capabilities.has_docker` is true. The JSON `gate`
object is authoritative (exit `0` PASS, `1` BLOCKED, `2` usage error).

## Step 2: Report

Present, in order: `gate.blocking` (each with file/rule — these are real,
fail-closed findings), `gate.errors` (an installed scanner produced unparseable
output — NOT clean; name the cause), `gate.advisory`, and `skipped` (a skipped
scanner is a visible gap, never a pass; recommend `/rawgentic:setup` to install
missing scanners). A **real leaked credential must be called out for rotation**
— deleting it from the tree does not un-leak history.

For anything that needs fixing, recommend filing an issue and running
`/rawgentic:implement-feature` — do NOT start editing files from this skill.

<completion-gate>
Before declaring the scan complete: the scan command actually ran (exit code
captured), every blocking/error/advisory/skipped item was surfaced verbatim,
and one marker line was appended to `claude_docs/session_notes.md`:
`### rawgentic:scan — DONE (blocking: N, advisory: N, errors: N, skipped: <kinds>)`.
</completion-gate>
