---
name: switch
description: Bind this session to a project in the rawgentic workspace, or manage project activation. Use when starting work on a specific project, when the system says "Multiple projects active", or when you want to enable/disable projects. Invoke with /rawgentic:switch followed by a project name, "off <name>" to deactivate, or no args to list all.
argument-hint: project name (e.g., my-app), "off <name>" to deactivate, or empty to list
---

Bind this session to one project; never deactivate others. Steps run **in order**. Every
rule's reason, and what breaks without it: `references/why.md` — read before changing a step.

## Step 1: Parse input

No argument **and no project named** → Step 2. `off <name>` → Step 6. Else take the name or
path as the Step 3 target — extracting it when the request is free-form ("bind me to my-api");
if none or more than one is named, ask which.

## Step 2: List mode

Read `.rawgentic_workspace.json` (primary working dir). Missing → STOP: "No rawgentic
workspace found. Run `/rawgentic:new-project` first." List each as
`● name (path) — active, configured` (○ inactive) with last-24h counts from
`claude_docs/session_registry.jsonl`. Ask which to bind.

## Step 3: Find in workspace

Same file. Missing → Step 2's message. Malformed → STOP: "Workspace file is corrupted."
Match `projects[]` by name (case-insensitive), then normalized path. Not found → list all,
ask: "No project matching '<input>'. Did you mean one of these?" — echo what was typed.

## Step 4: Verify the directory

Resolve relative paths against the workspace root. Missing → warn: "The directory `<path>`
no longer exists. Run `/rawgentic:new-project <name>` to re-create, or
`/rawgentic:switch off <name>` to deregister."

## Step 5: Bind

Read-modify-write `.rawgentic_workspace.json`: target `active: true`, `lastUsed` = now.
**Never set another project's `active` to `false`.**

Append one line to `claude_docs/session_registry.jsonl` (create it and
`claude_docs/session_notes/` if absent), id from **`$CLAUDE_CODE_SESSION_ID`** — per-process,
so it is correct under **concurrent** sessions. Two expansion-free calls, no `$(...)`:

```bash
printenv CLAUDE_CODE_SESSION_ID; date -u +%Y-%m-%dT%H:%M:%SZ
```

```bash
printf '{"session_id":"%s","project":"%s","project_path":"%s","started":"%s","cwd":"%s"}\n' "<ID>" "<name>" "<path>" "<TS>" "<root>" >> claude_docs/session_registry.jsonl
```

Never take the id from `claude_docs/.current_session_id`. If `printenv` prints nothing, STOP
and ask. Never invent one.

Report `Bound to: <name> (<path>)` + `Configured: yes/no`. If `configured` is `false`,
suggest `/rawgentic:setup` and skip Step 5b.

## Step 5b: Staleness checks

### 1. Workspace `defaultProtectionLevel`

Read `.rawgentic_workspace.json`. If the **top-level** `defaultProtectionLevel` is absent, ask
the user to choose — showing what each one does, because this picks their guard posture:

- `sandbox` — no guards active. POC / playground projects.
- `standard` — blocks destroy + mutate ops on production, 6 common security patterns.
- `strict` — all guards active. Full production projects.

Validate the answer is one of the three, add it **at the top level** by full read-modify-write
(never into a project entry), then confirm: "Set workspace `defaultProtectionLevel` to
**<choice>**." Runs once — later binds see the field and skip.

### 2. Project universal-field check

Check the project's `.rawgentic.json` **via Bash — never the `Read` tool** — for `version`,
`project`, `repo`, `protectionLevel`, `custom`. Presence only. Any missing → advisory only:
"Config advisory: your .rawgentic.json is missing: <list>. Run `/rawgentic:setup` to update
your config (existing values will be preserved)." Else print nothing.

### 2b. Feature-gap staleness nudge

```bash
python3 hooks/post_update_reconcile.py --staleness-project <name> \
  --workspace .rawgentic_workspace.json --state-dir claude_docs
```

Surface any output verbatim. Advisory, never blocking — a non-zero exit **or** empty output
both mean "nothing to nudge": continue to item 3.

### 3. Headless Access Check

Only when `RAWGENTIC_HEADLESS=1`. Read the project's `headlessEnabled` — bool or
`{"enabled":…,"triggers":[…]}` — and apply the SAME verdict the session-start gate computes:

- `true` → silent pass.
- `{"enabled": true}` → allowed only if `triggers` is absent OR
  `$RAWGENTIC_HEADLESS_TRIGGER` is in it. Non-member, unset env or malformed `triggers`
  **fails CLOSED** → STOP: "Headless mode for **[project-name]** does not allow this trigger
  (RAWGENTIC_HEADLESS_TRIGGER is not in the headlessEnabled.triggers allowlist)."
- anything else → STOP: "Headless mode is not enabled for **[project-name]**. Set
  `headlessEnabled: true` in the project's `.rawgentic_workspace.json` entry to enable it."
  (Setup no longer stages this key — M0c #866 removed its config surface; the manual
  workspace edit is the only path until the headless machinery itself retires in M0d.)

### 3b. Load the project's operating rules

**Use the `Read` tool on `<project path>/.rawgentic.json`. Never Bash (`cat`/`head`/`jq`).**

Item 2 already read this file via shell; this second read is deliberate. Do not collapse
them and do not move this step.

Projects with no `CLAUDE.md` need no handling. Never announce a missing manual.

If the `Read` fails, do not report Ready. Say: bound, but the project's rules did not load.

### 4. Confirm Ready

"Ready. All rawgentic workflow skills will use `<path>/.rawgentic.json` for this session."

## Step 6: Deactivate (`off <name>`)

Find as in Step 3. If sessions bound to it in the last 24h, warn that deactivating won't
unbind them but new sessions won't auto-bind, and ask to continue. Set `active: false`,
write, report: "Deactivated **<name>**. It won't appear as an option for new sessions until
reactivated."
