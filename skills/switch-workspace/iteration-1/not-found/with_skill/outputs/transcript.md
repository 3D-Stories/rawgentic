# Transcript: `/rawgentic:switch nonexistent-project`

**Scenario:** not-found
**Working directory:** `/tmp/rawgentic-switch-test/not-found`
**Input argument:** `nonexistent-project`
**Workspace file:** `/tmp/rawgentic-switch-test/not-found/.rawgentic_workspace.json`

---

## Step 1: Parse Input

The user invoked `/rawgentic:switch nonexistent-project`.

- An argument was provided: `nonexistent-project`.
- This is used as the search target for Step 2.

**Result:** Search target is `nonexistent-project`.

---

## Step 2: Find in Workspace

Read `.rawgentic_workspace.json` from the working directory.

**File contents:**

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

- File exists and is valid JSON.
- The `projects` array contains 1 entry: `my-app`.

**Search by name (case-insensitive):**
- `"nonexistent-project"` vs `"my-app"` -- no match.

**Search by path (normalized):**
- No project path contains `nonexistent-project` -- no match.

**Result:** Project `nonexistent-project` was NOT found in the workspace. Per the skill instructions, the process STOPS here with an error message. Steps 3, 4, and 5 are not executed.

---

## Output to User

No project matching 'nonexistent-project' found. Did you mean one of these?

| # | Name   | Path              | Active | Configured |
|---|--------|-------------------|--------|------------|
| 1 | my-app | ./projects/my-app | yes    | yes        |

If none of these match, run `/rawgentic:new-project nonexistent-project` to register it.

---

## Steps Not Executed

- **Step 3 (Verify Directory Exists):** Skipped -- no matching project found.
- **Step 4 (Switch):** Skipped -- no matching project found.
- **Step 5 (Confirm):** Skipped -- no matching project found.

---

## Workspace File Mutation

**No changes were made to `.rawgentic_workspace.json`.** The workspace file remains unchanged because the requested project does not exist in the workspace. No switch occurred.
