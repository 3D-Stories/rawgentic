# Transcript: /rawgentic:switch nonexistent-project (Project Not Found)

**Working directory:** `/tmp/rawgentic-switch-test/not-found`
**Command:** `/rawgentic:switch nonexistent-project`
**Date:** 2026-03-06

---

## Step 1: Read the workspace configuration

Read `/tmp/rawgentic-switch-test/not-found/.rawgentic_workspace.json`.

**Contents:**
```json
{
  "version": 1,
  "projectsDir": "./projects",
  "projects": [
    {
      "name": "my-app",
      "path": "./projects/my-app",
      "active": true,
      "lastUsed": "2026-03-06T10:00:00Z",
      "configured": true
    }
  ]
}
```

**Observation:** The workspace contains exactly one project: `my-app` (active: true, configured: true).

## Step 2: Look up the requested project

Search the `projects` array for an entry with `"name": "nonexistent-project"`.

**Result:** No project named `nonexistent-project` exists in the workspace.

## Step 3: Determine available projects

The only project registered in this workspace is:
- `my-app` (currently active)

## Step 4: Reject the switch

Since `nonexistent-project` is not defined in `.rawgentic_workspace.json`, the switch cannot be performed. No changes are made to the workspace file.

## Step 5: Report error to user

**Error:** Project "nonexistent-project" not found in this workspace.

Available projects:
- **my-app** (active)

To switch to a valid project, run: `/rawgentic:switch my-app`
To add a new project, register it in `.rawgentic_workspace.json` first.

---

## Outcome

- **Switch performed:** No
- **Workspace file modified:** No
- **Reason:** The target project `nonexistent-project` does not exist in the workspace configuration. The workspace state remains unchanged with `my-app` as the active project.
