---
name: rawgentic:switch
description: Switch the active project in the rawgentic workspace. Accepts a project name or path. Use when you want to change which project rawgentic workflow skills operate on — invoke with /rawgentic:switch followed by a project name, or say "change project to <name>" or "switch to <name>". Also use when the session-start hook says "Run /rawgentic:switch to select one."
argument-hint: project name (e.g., my-app) or path (e.g., ./projects/my-app)
---

<role>
You are the rawgentic project switcher. Your job is to change which project is active in the workspace so that all subsequent rawgentic workflow skills operate on the correct project. You verify the target exists, update the workspace file, and report the result.
</role>

# Switch Project — `/rawgentic:switch`

Run through all 5 steps below **sequentially**.

---

## Step 1: Parse Input

The user provides a **name** or **path**. They might also use natural language like "change project to my-app" or "switch to ./projects/my-app" — extract the project identifier from whatever phrasing they use.

- **No argument and no project mentioned** → Read `.rawgentic_workspace.json` and list all registered projects. Ask the user to pick one.
- **Argument provided** → Use it as the search target in Step 2.

---

## Step 2: Find in Workspace

Read `.rawgentic_workspace.json` from the **primary working directory** (the directory Claude was launched from — NOT the plugin directory or current CWD if it has changed).

- **File missing** → STOP. Tell the user: "No rawgentic workspace found. Run `/rawgentic:new-project` first."
- **Malformed JSON** → STOP. Tell the user: "Workspace file is corrupted. Fix it manually or run `/rawgentic:new-project` to regenerate."

Search the `projects` array:
1. **Match by name first** (case-insensitive comparison).
2. **Then match by path** (normalize both paths for comparison — resolve `./projects/my-app` vs `projects/my-app`).

**If not found:**
- List all registered projects and tell the user: "No project matching '<input>' found. Did you mean one of these?"
- If none match, suggest: "Run `/rawgentic:new-project <input>` to register it."

**If found but already active:**
- Tell the user: "**<name>** is already the active project." and stop.

---

## Step 3: Verify Directory Exists

Check that the target project's `path` directory exists on disk.

**If the directory is missing:**
- Warn the user: "The directory `<path>` no longer exists on disk."
- Offer two choices:
  1. **Re-create it** → Run `/rawgentic:new-project <name>` (which handles folder creation and clone).
  2. **Remove from workspace** → Delete the entry from `.rawgentic_workspace.json` and stop.

**If the directory exists:** Continue to Step 4.

---

## Step 4: Switch

Read `.rawgentic_workspace.json`, then:

1. Set the **previously active** project's `active` to `false`.
2. Set the **target** project's `active` to `true`.
3. Update the target's `lastUsed` to the current ISO 8601 timestamp.
4. Write the updated workspace file back (full read-modify-write).

---

## Step 5: Confirm

Report the switch result:

```
Switched to: <name> (<path>)
Configured: yes/no
Last used: <timestamp>
```

**If `configured` is `false`:** Add a suggestion: "This project hasn't been configured yet. Run `/rawgentic:setup` to detect your tech stack and generate `.rawgentic.json`."

**If `configured` is `true`:** Confirm: "Ready to go. All rawgentic workflow skills will now use `<path>/.rawgentic.json`."
