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
  ● my-api (./projects/my-api) — active, configured
  ● rawgentic (./projects/rawgentic) — active, configured
  ○ millions (./projects/millions) — inactive
```

Use ● for active, ○ for inactive. Show configured status.

Also check `claude_docs/session_registry.jsonl` for recent sessions (last 24h) bound to each project and show them:
```
  ● my-api — 1 recent session
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

   **How to get your session ID:** Read the file `claude_docs/.current_session_id` using Bash (`cat claude_docs/.current_session_id`). This file is written by the session-start and wal-context hooks on every prompt. **Do NOT use `$CLAUDE_SESSION_ID`** — it is not available as an environment variable. **Do NOT invent a session ID** — always read it from this file.

Report:
```
Bound to: <name> (<path>)
Configured: yes/no
```

**If `configured` is `false`:** Suggest: "This project hasn't been configured yet. Run `/rawgentic:setup`." Then skip Step 5b entirely — there is no config to check for staleness.

**If `configured` is `true`:** Proceed to Step 5b before confirming "Ready."

---

## Step 5b: Config Staleness Check

After binding and before the "Ready" confirmation, run these checks in order:

### 1. Workspace-level: `defaultProtectionLevel`

Read `.rawgentic_workspace.json`. If the top-level field `defaultProtectionLevel` is missing:

1. Prompt the user:

   ```
   Your workspace is missing a default protection level.
   Choose a workspace-wide default for new or unconfigured projects:

   - sandbox  — No guards active. Good for POC / playground projects.
   - standard — Blocks destroy + mutate ops on production, 6 common security patterns.
   - strict   — All guards active. Full production projects.

   Which level? (sandbox / standard / strict)
   ```

2. Wait for the user's choice. Validate it is one of `sandbox`, `standard`, `strict`.
3. Read `.rawgentic_workspace.json`, add `"defaultProtectionLevel": "<choice>"` at the top level, and write it back (full read-modify-write).
4. Confirm: "Set workspace `defaultProtectionLevel` to **<choice>**."

This prompt runs once — subsequent binds see the field and skip.

### 2. Project-level: universal field check

Check the project's `.rawgentic.json` for the following **universal fields** (hardcoded list):

- `version`
- `project`
- `repo`
- `protectionLevel`
- `custom`

This list is intentionally small — it includes only fields that every project should have regardless of type. Optional sections (`testing`, `database`, `services`, `infrastructure`, `deploy`, `security`, `ci`, `formatting`, `documentation`) are NOT checked because projects may legitimately omit them.

**Compare field presence only** — do not validate values or nested structure.

**If any universal fields are missing:** Print an advisory warning:

```
Config advisory: your .rawgentic.json is missing: <comma-separated list of missing fields>
Run `/rawgentic:setup` to update your config (existing values will be preserved).
```

**If no fields are missing:** Silent pass — print nothing.

### 3. Confirm Ready

After both checks complete, print the final confirmation:

"Ready. All rawgentic workflow skills will use `<path>/.rawgentic.json` for this session."

---

## Step 6: Deactivate Mode (`/rawgentic:switch off <name>`)

Find the project in the workspace (same as Step 3).

1. Check `claude_docs/session_registry.jsonl` for sessions bound to this project in the last 24 hours.
2. If recent sessions found → warn: "There are recent sessions bound to **<name>**. Deactivating will not unbind them, but new sessions won't auto-bind to it. Continue?"
3. Set `active: false` for the project in `.rawgentic_workspace.json`.
4. Write the updated workspace file.

Report: "Deactivated **<name>**. It won't appear as an option for new sessions until reactivated."
