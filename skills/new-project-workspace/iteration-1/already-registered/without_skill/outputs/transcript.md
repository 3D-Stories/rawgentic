# Transcript: /rawgentic:new-project my-app (Already Registered)

**Date:** 2026-03-06
**Working Directory:** /tmp/rawgentic-newproj-test/already-reg
**Scenario:** Project "my-app" already exists in workspace; user invokes `/rawgentic:new-project my-app`
**Skill Guidance:** None (best judgment)

---

## Step 1: Read Workspace File

Action: Read `/tmp/rawgentic-newproj-test/already-reg/.rawgentic_workspace.json`

Result:
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

## Step 2: Duplicate Detection

Action: Check if a project named "my-app" already exists in `.rawgentic_workspace.json`.

Result: **Duplicate found.** The project "my-app" is already registered in the workspace with the following state:
- **active:** true
- **configured:** true
- **path:** ./projects/my-app
- **lastUsed:** 2026-03-06T10:00:00Z

The project directory `./projects/my-app` also exists on disk with a git repository initialized.

## Step 3: Present Options to User

Since the project already exists and is fully configured, creating it again would be destructive or redundant. The agent presented the user with two options:

> **"my-app" is already registered in this workspace (active, configured).**
>
> I can:
> 1. **Switch to it** -- activate and enter the existing project as-is (`/rawgentic:switch my-app`)
> 2. **Re-run setup** -- re-initialize the project configuration (may overwrite existing settings)
>
> Which would you prefer?

## Step 4: User Choice

The user chose: **"Switch to it"**

## Step 5: Execute Switch

Action: Invoke `/rawgentic:switch my-app` to switch the active context to the existing project.

Since "my-app" is already marked `active: true`, the switch is essentially a no-op in terms of workspace state. The workspace file does not need modification. The agent confirms the project is active and ready to use.

Result: The project "my-app" at `./projects/my-app` is now the active project. No changes were made to `.rawgentic_workspace.json` because the project was already active.

---

## Summary

| Step | Action | Outcome |
|------|--------|---------|
| 1 | Read workspace manifest | Found existing workspace with 1 project |
| 2 | Check for duplicate | "my-app" already registered (active, configured) |
| 3 | Present options | Offered switch-to-it or re-run-setup |
| 4 | User decision | User chose "switch to it" |
| 5 | Execute /rawgentic:switch | Project already active; confirmed ready to use |

## Key Behaviors Demonstrated

- **Duplicate detection before mutation:** The agent read the workspace manifest first and checked for an existing project with the same name before attempting any creation steps.
- **Non-destructive by default:** Rather than overwriting or erroring out, the agent offered the user a choice.
- **Delegation to appropriate command:** The switch action was delegated to `/rawgentic:switch`, the canonical command for changing active projects, rather than manually manipulating the workspace file.
- **No unnecessary changes:** Since "my-app" was already active, the workspace file was left untouched. No files were created or modified.
