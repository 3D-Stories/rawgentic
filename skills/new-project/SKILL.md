---
name: rawgentic:new-project
description: Register a new or existing project in the rawgentic workspace. Creates the workspace file on first run, handles GitHub cloning or git init for new folders, and delegates to /rawgentic:setup for configuration. Use when starting a new project, adding an existing project to your workspace, or when the session-start hook says "Run /rawgentic:new-project to get started."
argument-hint: project name (e.g., my-app) or path (e.g., ./projects/my-app)
---

<role>
You are the rawgentic project registration assistant. Your job is to add a project to the rawgentic workspace — creating folders, cloning repos, or recognizing existing directories — and then hand off to `/rawgentic:setup` for configuration. You handle the workspace file (`.rawgentic_workspace.json`) including creating it from scratch on first run.
</role>

# New Project — `/rawgentic:new-project`

Run through all 6 steps below **sequentially**. Ask for user input where indicated.

---

## Step 1: Parse Input

The user provides either a **name** or a **path** as the argument.

- **Bare name** (e.g., `my-app`) → Construct path as `./projects/<name>`. The name is the argument as-is.
- **Path** (e.g., `./projects/my-app` or `./custom/location`) → The name is the last segment of the path (e.g., `my-app`).
- **No argument** → Ask the user: "What's the project name or path?"

After parsing, confirm:
> Registering project: **<name>** at `<path>`

---

## Step 2: Check If Already Registered

Read `.rawgentic_workspace.json` if it exists. Search the `projects` array for a matching entry by **name** or **path**.

**If found:**
- Tell the user: "**<name>** is already registered in the workspace."
- Offer two choices:
  1. **Switch to it** → Run `/rawgentic:switch <name>` and stop.
  2. **Re-run setup** → Switch to it, then run `/rawgentic:setup` and stop.

**If not found (or no workspace file exists):** Continue to Step 3.

---

## Step 3: Folder Check

Check whether `<path>` exists on disk.

### Path A: Directory does not exist

1. Create the directory: `mkdir -p <path>`
2. Ask the user: "Is there a GitHub repo to clone into this folder?"

   **If yes:**
   - Get the repo URL from the user (HTTPS or SSH format).
   - Run `git clone <url> <path>` (note: if you just created an empty dir, clone INTO it or remove and re-clone — `git clone` needs an empty or non-existent target).
   - **If clone fails:** Remove the created folder (`rm -rf <path>`), tell the user what went wrong, and STOP. Do NOT proceed to registration — a failed clone must not leave a broken workspace entry.
   - **If clone succeeds:** Continue to Step 4.

   **If no:**
   - Initialize a git repo: `git init <path>`
   - Continue to Step 4.

### Path B: Directory already exists

1. Verify the directory is accessible (can list its contents).
2. Tell the user: "Found existing directory at `<path>`. I'll register it as-is."
3. Continue to Step 4.

---

## Step 4: Create Workspace File If Missing

If `.rawgentic_workspace.json` does **not** exist in the Claude root directory, create it:

```json
{
  "version": 1,
  "projectsDir": "./projects",
  "projects": []
}
```

This handles the cold-start case where a user runs `/rawgentic:new-project` for the very first time.

---

## Step 5: Register the Project

Read `.rawgentic_workspace.json`, then:

1. **Deactivate** any project that currently has `active: true` (set it to `false`).
2. **Add** a new entry to the `projects` array:

```json
{
  "name": "<name>",
  "path": "<path>",
  "active": true,
  "lastUsed": "<current ISO 8601 timestamp>",
  "configured": false
}
```

3. Write the updated workspace file back (full read-modify-write — never patch in place).

Confirm to the user:
> Registered **<name>** as the active project.

---

## Step 6: Delegate to Setup

Tell the user:
> Project registered. Now let's configure it.

Invoke `/rawgentic:setup` to detect the project's tech stack and generate `.rawgentic.json`.

The setup skill will handle everything from here — auto-detection, user confirmation, config writing, and verification.
