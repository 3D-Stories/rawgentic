# rawgentic:switch Transcript -- /rawgentic:switch backend-api

**Working directory:** /tmp/rawgentic-switch-test/normal-switch
**Workspace file:** .rawgentic_workspace.json
**Skill file:** /home/candrosoff/claude/projects/rawgentic/skills/switch/SKILL.md

---

## Step 1: Parse Input

- **Input received:** `backend-api`
- **Interpretation:** The user provided a project name as a direct argument.
- **Decision:** Use `backend-api` as the search target for Step 2. No need to prompt the user for clarification -- the argument is clear and unambiguous.

---

## Step 2: Find in Workspace

- **Read `.rawgentic_workspace.json`:** File exists and contains valid JSON.
- **Workspace contents:** Two projects registered:
  1. `frontend-app` (path: `./projects/frontend-app`, active: **true**, configured: true)
  2. `backend-api` (path: `./projects/backend-api`, active: **false**, configured: false)
- **Search by name (case-insensitive):** `backend-api` matches the second entry exactly.
- **Already active check:** `active` is `false`, so the project is not already active. Proceed.
- **Decision:** Match found. Continue to Step 3.

---

## Step 3: Verify Directory Exists

- **Target path:** `./projects/backend-api` (resolved to `/tmp/rawgentic-switch-test/normal-switch/projects/backend-api`)
- **Check:** Ran `ls -d /tmp/rawgentic-switch-test/normal-switch/projects/backend-api` -- directory exists.
- **Decision:** Directory is present on disk. Continue to Step 4.

---

## Step 4: Switch

- **Read-modify-write `.rawgentic_workspace.json`:**
  1. Set `frontend-app.active` from `true` to `false` (deactivate previous project).
  2. Set `backend-api.active` from `false` to `true` (activate target project).
  3. Set `backend-api.lastUsed` to `2026-03-06T00:00:00Z` (current date ISO 8601 timestamp).
  4. All other fields remain unchanged.
- **Write updated JSON** to the outputs directory (not modifying the fixture file per task instructions).

---

## Step 5: Confirm

Output to the user:

```
Switched to: backend-api (./projects/backend-api)
Configured: no
Last used: 2026-03-06T00:00:00Z
```

**Additional suggestion (because `configured` is `false`):**

> This project hasn't been configured yet. Run `/rawgentic:setup` to detect your tech stack and generate `.rawgentic.json`.

---

## Summary

| Field | Before | After |
|---|---|---|
| frontend-app.active | true | false |
| backend-api.active | false | true |
| backend-api.lastUsed | 2026-03-05T15:00:00Z | 2026-03-06T00:00:00Z |

The switch completed successfully. All 5 steps of the skill were followed sequentially. No errors or edge cases were encountered. The fixture files were not modified -- outputs were written only to the designated outputs directory.
