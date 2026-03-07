---
name: rawgentic:switch
description: Bind this session to a project in the rawgentic workspace, or manage project activation. Use when starting work on a specific project, when the system says "Multiple projects active", or when you want to enable/disable projects. Invoke with /rawgentic:switch followed by a project name, "off <name>" to deactivate, or no args to list all.
argument-hint: project name (e.g., my-app), "off <name>" to deactivate, or empty to list
---

<role>
You are the rawgentic project switcher. Your job is to bind the current session to a project so all subsequent rawgentic workflow skills and hooks operate on the correct project. You do NOT deactivate other projects — multiple projects can be active simultaneously for concurrent sessions.
</role>

# Switch / Bind Project — `/rawgentic:switch`

Run through the steps below **sequentially**.

---

## Step 1: Parse Input

The user provides a **name**, **path**, or subcommand. They might also use natural language.

- **No argument and no project mentioned** → Go to Step 2 (list mode).
- **"off <name>"** → Go to Step 6 (deactivate mode).
- **Name or path provided** → Use it as the search target in Step 3.

---

## Step 2: List Mode

Read `.rawgentic_workspace.json` from the **primary working directory**.

- **File missing** → STOP. Tell the user: "No rawgentic workspace found. Run `/rawgentic:new-project` first."

Display all registered projects:

```
Projects in workspace:
  ● chorestory (./projects/chorestory) — active, configured
  ● rawgentic (./projects/rawgentic) — active, configured
  ○ millions (./projects/millions) — inactive
```

Use ● for active, ○ for inactive. Show configured status.

Also check `claude_docs/session_registry.jsonl` for recent sessions (last 24h) bound to each project and show them:
```
  ● chorestory — 1 recent session
  ● rawgentic — 2 recent sessions
```

Then ask: "Which project do you want to bind this session to?"

---

## Step 3: Find in Workspace

Read `.rawgentic_workspace.json` from the **primary working directory**.

- **File missing** → STOP. Tell the user: "No rawgentic workspace found. Run `/rawgentic:new-project` first."
- **Malformed JSON** → STOP. Tell the user: "Workspace file is corrupted."

Search the `projects` array:
1. **Match by name first** (case-insensitive comparison).
2. **Then match by path** (normalize both paths).

**If not found:** List all projects and suggest: "No project matching '<input>'. Did you mean one of these?"

---

## Step 4: Verify Directory Exists

Check that the target project's `path` directory exists on disk.

**Path resolution:** Resolve relative paths against the workspace root directory.

**If missing:** Warn: "The directory `<path>` no longer exists. Run `/rawgentic:new-project <name>` to re-create, or `/rawgentic:switch off <name>` to deregister."

---

## Step 5: Bind Session

Read `.rawgentic_workspace.json`, then:

1. Set the target project's `active` to `true` (if it wasn't already — this enables a project that was previously deactivated).
2. Update the target's `lastUsed` to the current ISO 8601 timestamp.
3. Write the updated workspace file back (full read-modify-write).
4. **Do NOT set any other project's `active` to `false`.** Multiple projects can be active simultaneously.
5. **Register in session registry:** Append a line to `claude_docs/session_registry.jsonl`:
   ```json
   {"session_id":"<your session_id>","project":"<target project name>","project_path":"<target project path>","started":"<current ISO 8601 timestamp>","cwd":"<workspace root>"}
   ```
   Create the file and `claude_docs/session_notes/` directory if they don't exist.

   **How to get your session ID:** Read `claude_docs/session_registry.jsonl` and find the most recent entry matching this session (you can grep for entries written in the last few minutes). Copy the `session_id` value from that entry. Alternatively, if this is a new session with no registry entry yet, read the session ID from the session-start hook context that was provided at the start of this conversation. **Do NOT use `$CLAUDE_SESSION_ID`** — it is not available as an environment variable.

Report:
```
Bound to: <name> (<path>)
Configured: yes/no
```

**If `configured` is `false`:** Suggest: "This project hasn't been configured yet. Run `/rawgentic:setup`."

**If `configured` is `true`:** Confirm: "Ready. All rawgentic workflow skills will use `<path>/.rawgentic.json` for this session."

---

## Step 6: Deactivate Mode (`/rawgentic:switch off <name>`)

Find the project in the workspace (same as Step 3).

1. Check `claude_docs/session_registry.jsonl` for sessions bound to this project in the last 24 hours.
2. If recent sessions found → warn: "There are recent sessions bound to **<name>**. Deactivating will not unbind them, but new sessions won't auto-bind to it. Continue?"
3. Set `active: false` for the project in `.rawgentic_workspace.json`.
4. Write the updated workspace file.

Report: "Deactivated **<name>**. It won't appear as an option for new sessions until reactivated."
