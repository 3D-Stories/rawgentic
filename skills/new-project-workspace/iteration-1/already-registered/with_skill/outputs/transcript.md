# Skill Test Transcript: rawgentic:new-project (Already Registered)

**Skill:** `/rawgentic:new-project`
**Date:** 2026-03-06
**Working Directory:** `/tmp/rawgentic-newproj-test/already-reg`
**Input Argument:** `my-app`
**Scenario:** Project `my-app` is already registered in `.rawgentic_workspace.json`

---

## Pre-conditions

### Workspace File (`.rawgentic_workspace.json`)

The file exists at `/tmp/rawgentic-newproj-test/already-reg/.rawgentic_workspace.json` with the following contents:

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

---

## Step-by-Step Execution

### Step 1: Parse Input

- **Input:** `my-app`
- **Classification:** Bare name (no path separators)
- **Derived name:** `my-app`
- **Derived path:** `./projects/my-app`
- **Output to user:**
  > Registering project: **my-app** at `./projects/my-app`

**Result:** Proceed to Step 2.

---

### Step 2: Check If Already Registered

- **Action:** Read `.rawgentic_workspace.json` -- file exists.
- **Search:** Look for an entry in `projects` array matching name `my-app` or path `./projects/my-app`.
- **Match found:** Yes. Entry at index 0 matches both name (`my-app`) and path (`./projects/my-app`).
- **Output to user:**
  > **my-app** is already registered in the workspace.
- **Choices offered:**
  1. **Switch to it** -- Run `/rawgentic:switch my-app` and stop.
  2. **Re-run setup** -- Switch to it, then run `/rawgentic:setup` and stop.

**User choice:** Switch to it.

**Action that would be taken:** Invoke `/rawgentic:switch my-app` to switch the active project context to `my-app`.

**NOTE:** Per test instructions, `/rawgentic:switch` was NOT actually invoked. This is a dry-run notation only.

**Result:** STOP. The skill halts here. Steps 3 through 6 are not executed.

---

### Steps 3-6: Not Reached

Steps 3 (Folder Check), 4 (Create Workspace File If Missing), 5 (Register the Project), and 6 (Delegate to Setup) were **not executed** because the skill detected that `my-app` is already registered and the user chose to switch to it, which terminates the flow at Step 2.

---

## Summary

| Step | Description                  | Outcome                              |
|------|------------------------------|--------------------------------------|
| 1    | Parse Input                  | Parsed `my-app` as bare name, path `./projects/my-app` |
| 2    | Check If Already Registered  | Match found -- user chose "switch to it" -- STOP |
| 3    | Folder Check                 | Not reached                          |
| 4    | Create Workspace File        | Not reached                          |
| 5    | Register the Project         | Not reached                          |
| 6    | Delegate to Setup            | Not reached                          |

**Final state:** No modifications were made to `.rawgentic_workspace.json`. The skill would have delegated to `/rawgentic:switch my-app` but this invocation was intentionally skipped per test parameters.
