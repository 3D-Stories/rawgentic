---
name: switch
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
  ● my-frontend (./projects/my-frontend) — active, configured
  ○ side-project (./projects/side-project) — inactive
```

Use ● for active, ○ for inactive. Show configured status.

Also check `claude_docs/session_registry.jsonl` for recent sessions (last 24h) bound to each project and show them:
```
  ● my-api — 1 recent session
  ● my-frontend — 2 recent sessions
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

   **How to get your session ID — use the per-session env var, NOT the shared file:**

   The session ID is in `$CLAUDE_CODE_SESSION_ID`, set per Claude Code process (so it is unique and correct even when multiple sessions run **concurrently**). Register in **two expansion-free Bash calls** so the command contains no `$(...)` command substitution: a command containing `$(...)` (or backticks) is flagged "Contains expansion" and **always** triggers a permission prompt that **no `permissions.allow` rule can suppress** — keeping it expansion-free lets a `Bash(printf:*)` / `Bash(date:*)` / `Bash(printenv:*)` allow rule auto-approve the bind.

   **Call 1 — read the session ID and timestamp** (two allowlistable leading binaries, no command substitution):

   ```bash
   printenv CLAUDE_CODE_SESSION_ID; date -u +%Y-%m-%dT%H:%M:%SZ
   ```

   **Call 2 — append the registry line, inlining those two values as literals** (starts with `printf`, no `$(...)`, only `>>` redirection, so it matches a `Bash(printf:*)` allow rule):

   ```bash
   printf '{"session_id":"%s","project":"%s","project_path":"%s","started":"%s","cwd":"%s"}\n' "<SESSION_ID from call 1>" "<target project name>" "<target project path>" "<TIMESTAMP from call 1>" "<workspace root>" >> claude_docs/session_registry.jsonl
   ```

   Because `$CLAUDE_CODE_SESSION_ID` is per-process, reading it in call 1 and writing in call 2 is still race-free — there is no shared state between the two calls to corrupt.

   **Do NOT** read `claude_docs/.current_session_id` as the source: that file is **shared across all sessions** and is overwritten by every session on every prompt, so under concurrent sessions it can return *another* session's id and bind the wrong session. If `printenv` prints nothing (only older Claude Code that does not set the env var), STOP and ask the user rather than guessing. (The legacy name `$CLAUDE_SESSION_ID` is **not** set — the correct variable is `$CLAUDE_CODE_SESSION_ID`.) **Do NOT invent a session ID.**

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

### 2b. Feature-gap staleness nudge (#234)

The universal-field check above only catches a *malformed/old-shape* config. It does
NOT catch a project that predates newer **setup-requiring features** (adversarial
review, model routing, peer consult, design artifact, …) — that project has a valid
config, it's just behind. `hooks/post_update_reconcile.py`'s SessionStart pass nudges
these once per plugin version, but switching to a project is the moment to surface
*its own* gap. Run the per-project staleness check for the project you just bound
(`<name>` = the target project's name; resolve the workspace + `claude_docs` paths as
in Step 5):

```bash
python3 hooks/post_update_reconcile.py --staleness-project <name> \
  --workspace .rawgentic_workspace.json --state-dir claude_docs
```

It prints an advisory line (or nothing) — surface any output to the user verbatim.
This is **advisory, never blocking**: it always shows the gap on an explicit switch
(no once-per-version gate, unlike the SessionStart `--staleness-active` pass), and it
respects the workspace-level `"setupPrompt": false` opt-out. Fail-open: a non-zero
exit or empty output means "nothing to nudge" — continue.

### 3. Headless Access Check

If the current session has `RAWGENTIC_HEADLESS=1` set (headless mode), check the target project's `headlessEnabled` field in `.rawgentic_workspace.json`. The field accepts a bool (legacy) or an object `{"enabled": bool, "triggers": [...], "auth": "..."}` (#165) — apply the SAME verdict the session-start gate computes:

- **If `headlessEnabled` is `true`:** Silent pass — headless mode allowed, any trigger.
- **If it is an object with `enabled: true`:** allowed only when `triggers` is absent, OR `$RAWGENTIC_HEADLESS_TRIGGER` is a member of the `triggers` array. A non-member, an unset trigger env, or a malformed `triggers` value fails CLOSED — STOP and tell user: "Headless mode for **[project-name]** does not allow this trigger (RAWGENTIC_HEADLESS_TRIGGER is not in the headlessEnabled.triggers allowlist)."
- **If `headlessEnabled` is `false`, `{"enabled": false, ...}`, missing, or any other shape:** STOP and tell user:
  "Headless mode is not enabled for **[project-name]**. Run `/rawgentic:setup` to enable it, or set `headlessEnabled: true` in the project's entry in `.rawgentic_workspace.json`."

If not in headless mode: skip this check entirely.

### 4. Confirm Ready

After all checks complete, print the final confirmation:

"Ready. All rawgentic workflow skills will use `<path>/.rawgentic.json` for this session."

---

## Step 6: Deactivate Mode (`/rawgentic:switch off <name>`)

Find the project in the workspace (same as Step 3).

1. Check `claude_docs/session_registry.jsonl` for sessions bound to this project in the last 24 hours.
2. If recent sessions found → warn: "There are recent sessions bound to **<name>**. Deactivating will not unbind them, but new sessions won't auto-bind to it. Continue?"
3. Set `active: false` for the project in `.rawgentic_workspace.json`.
4. Write the updated workspace file.

Report: "Deactivated **<name>**. It won't appear as an option for new sessions until reactivated."
