# Multi-Project Concurrent Sessions Design

## Problem

The rawgentic workspace uses a single `active` flag (mutex) — only one project can be active at a time. When running two Claude Code sessions simultaneously from the same workspace root (e.g., one on my-api, one on rawgentic), the system breaks:

1. **Session misattribution** — new sessions auto-register to whatever project is `active`, regardless of what they're actually working on
2. **WAL pollution** — a single shared `wal.jsonl` mixes operations from all sessions/projects, producing noisy recovery on resume
3. **Session notes misdirection** — notes go to the wrong project file when sessions aren't properly bound
4. **Switch conflict** — `/rawgentic:switch` deactivates the other project, disrupting the concurrent session

## Design Principles

- Session registry is the source of truth for "what project is this session working on"
- Workspace config is a project **registry** (what exists, what's provisioned), not a session binding mechanism
- Per-project isolation for WAL and session notes — no cross-project noise
- Hard gate on unbound sessions — force explicit project selection before work begins
- Hard gate on cross-project file writes — prevent accidental work in the wrong project

## Architecture

### Three-layer separation of concerns

| Layer | File | Concern |
|-------|------|---------|
| Project registry | `.rawgentic_workspace.json` | What projects exist, which are provisioned (`active: true` = directory exists, configured) |
| Session binding | `claude_docs/session_registry.jsonl` | Which project each session is bound to (authoritative) |
| Project isolation | `claude_docs/wal/{project}.jsonl` | Per-project WAL; session notes already per-project |

### Data flow

```
Session starts (startup/resume)
    │
    ├─ SessionStart hook (session-start)
    │   ├─ 1. Project directory reconciliation
    │   │   └─ For each active project, verify directory exists on disk
    │   │       └─ Missing → set active:false, prompt user: "remove or re-setup?"
    │   ├─ 2. Resume context (resume events only)
    │   │   └─ Lookup session_id in registry → "Resuming work on: <project>"
    │   ├─ 3. WAL recovery (per-project WAL file)
    │   ├─ 4. Session notes archival
    │   └─ 5. Security guard conflict check (existing)
    │
First user prompt
    │
    ├─ wal-context hook (UserPromptSubmit)
    │   ├─ Check registry for session_id → found → use that project
    │   ├─ Not found, 1 active project → auto-bind, write to registry
    │   └─ Not found, multiple active → inject "use /rawgentic:switch"
    │
    ├─ wal-guard hook (PreToolUse, if unbound + multiple active)
    │   └─ DENY all tool calls: "Session not bound. Ask user which project."
    │
    ├─ wal-guard hook (PreToolUse, if bound)
    │   └─ Cross-project file check: DENY writes to other project directories
    │
User runs /rawgentic:switch <project>
    │
    ├─ Bind session to project (write to registry)
    ├─ Enable project if not already active
    └─ All subsequent tool calls proceed
```

## Component Changes

### 1. Workspace Config — no schema change

`active` keeps its current meaning with one clarification: `active: true` means "this project is fully provisioned — directory exists, `.rawgentic.json` present, ready for sessions." It is set by `rawgentic:new-project` at the end of successful setup, NOT by `rawgentic:switch`.

Multiple projects can have `active: true` simultaneously. This is the key behavioral change — previously enforced as a mutex, now a multi-select.

No version bump. No field renames. Existing workspace files work as-is.

### 2. Session binding — detection cascade (wal-context hook)

The `wal-context` hook fires on every `UserPromptSubmit`. New cascade:

1. **Registry hit** — `session_id` found in `session_registry.jsonl` → use that project. (No change from today.)
2. **Single active** — only 1 project has `active: true` → auto-bind to it, write to registry. (Backward compat.)
3. **Multiple active, no registry** → inject `additionalContext`: "Multiple projects active: my-api, rawgentic. Use `/rawgentic:switch <name>` to bind this session."

**Changes to wal-context hook:**
- Line 44: `select(.active == true)` returns multiple results — handle by counting
- If count == 1 → auto-bind (current behavior)
- If count > 1 → prompt user (new behavior)
- If count == 0 → "No active projects" message (current behavior, unchanged)

### 3. Hard gate — unbound session blocking (wal-guard hook)

Add a new check at the **top** of `wal-guard` (before the Bash-command pattern checks):

1. Read `session_registry.jsonl`, check if current `session_id` has a binding
2. Read `.rawgentic_workspace.json`, count `active: true` projects
3. If **unbound AND multiple active projects** → DENY with `systemMessage`:
   > "This session isn't bound to a project yet. Multiple projects are active. Ask the user which project they want to work on, then run `/rawgentic:switch <name>`."
4. If **unbound AND exactly one active** → allow (wal-context will auto-bind on next prompt)
5. If **bound** → continue to existing checks

**Important:** The wal-guard currently only matches `Bash` tools. The unbound-session check and cross-project check need to apply to ALL tools (Edit, Write, Read, Bash, etc.). This requires either:
- **Option A:** Change wal-guard's matcher to match all tools, then early-exit for non-Bash tools after the binding/cross-project checks
- **Option B:** Create a separate hook (`wal-bind-guard`) with a broad matcher for the binding check, keep wal-guard for Bash-specific blocks

**Recommendation:** Option B — separation of concerns. `wal-bind-guard` handles session binding enforcement and cross-project guards. `wal-guard` stays focused on dangerous Bash commands.

### 4. Cross-project file guard (new wal-bind-guard hook)

When a session is bound to project X, prevent file operations on project Y's directory.

**Logic:**
1. Extract file path from tool_input (`file_path`, `notebook_path`)
2. Resolve all project paths from workspace config
3. If file path falls under a different project's directory → DENY:
   > "You're bound to **rawgentic** but this file is in **my-api**. Switch first with `/rawgentic:switch my-api`, or ask the user if this cross-project edit is intentional."
4. If file path is outside all project directories → allow (system files, temp files, etc.)

**Applies to:** Edit, Write, MultiEdit, NotebookEdit, Read
**Does NOT apply to:** Bash (commands may legitimately reference other project paths), Agent (delegates may need cross-project context)

### 5. Per-project WAL isolation

**New directory structure:**
```
claude_docs/
├── session_registry.jsonl
├── session_notes/
│   ├── my-api.md
│   └── rawgentic.md
├── wal/
│   ├── my-api.jsonl
│   └── rawgentic.jsonl
└── wal.jsonl                  ← archived, no longer written to
```

**Changes to wal-lib.sh:**
- New function `wal_resolve_project()`: reads registry for session_id → returns project name
- `wal_init_file()` changes: WAL_FILE becomes `claude_docs/wal/${PROJECT}.jsonl` instead of `claude_docs/wal.jsonl`
- If session is unbound → skip WAL writes (one prompt of un-logged activity is acceptable)

**Changes to hooks using wal-lib.sh:**
- `wal-pre`: Calls `wal_resolve_project()`, writes to per-project WAL
- `wal-post`: Same
- `wal-post-fail`: Same

**Migration:** Old `wal.jsonl` stays as-is. No historical data migration. New entries go to per-project files.

### 6. SessionStart hook — reconciliation + resume context

Add two new sections to the existing `session-start` hook:

**Section 0 (new, runs first): Project directory reconciliation**

For each project with `active: true` in workspace config:
1. Resolve path to absolute
2. Check if directory exists on disk
3. If missing → set `active: false` in workspace config, add to `CONTEXT_PARTS`:
   > "Project **<name>** is registered but directory `<path>` is missing. Was it removed, or did setup crash? Reply 'remove' to deregister, or 'setup' to re-run `/rawgentic:new-project <name>`."

**Section 3 modification: Resume context**

On `resume` events, if session_id is found in registry:
- Replace the generic "Active project: X" message with: "Resuming work on project: **<name>** (`<path>`)"
- If registry entry exists but project is no longer active (directory was deleted): "Your previous session was bound to **<name>**, but that project's directory no longer exists."

### 7. Switch skill changes

| Command | Behavior |
|---------|----------|
| `/rawgentic:switch <project>` | Binds this session to project. Writes to registry. Marks project `active: true` if it wasn't (enables a previously disabled project). Does **NOT** deactivate any other project. |
| `/rawgentic:switch off <project>` | Sets project `active: false`. Warns if any recent sessions are bound to it (check registry for entries within last 24h). |
| `/rawgentic:switch` (no args) | Lists all projects with status (active/inactive) and any bound sessions from registry. |

**Key change:** Step 4 of the current skill sets old project `active: false` — remove this. Only set target project `active: true` and write registry binding.

### 8. new-project skill change

**Remove:** Step that deactivates any currently active project before activating the new one.
**Keep:** Setting `active: true` on the new project at the end of successful provisioning.

### 9. Config-loading pattern update (all 12 workflow skills)

The `<config-loading>` pattern's Level 3 fallback currently reads:
> "Extract the active project entry (active == true)"

This assumes single-active. The updated pattern:

```
<config-loading>
1. Determine the active project using this fallback chain:
   Level 1 -- Conversation context: Previous /rawgentic:switch in this session.
   Level 2 -- Session registry: Read claude_docs/session_registry.jsonl. Grep for session_id. Use the project from the most recent matching line.
   Level 3 -- Workspace default: Read .rawgentic_workspace.json. If exactly one project has active == true, use it. If multiple, STOP and tell user: "Multiple active projects. Run /rawgentic:switch <name> to bind this session."
```

**Affected skills (12):**
- implement-feature, fix-bug, refactor, update-deps, incident, create-issue, setup, security-audit, optimize-perf, update-docs, switch, new-project

## Hook registration (hooks.json)

New entry for `wal-bind-guard`:

```json
{
  "matcher": "Edit|Write|MultiEdit|NotebookEdit|Read",
  "hooks": [
    {
      "type": "command",
      "command": "'${CLAUDE_PLUGIN_ROOT}/hooks/wal-bind-guard'",
      "timeout": 3
    }
  ]
}
```

## Error handling

| Condition | Behavior |
|-----------|----------|
| Session registry file missing | Create on first write. Treat as "no binding" on read. |
| Session registry malformed line | Skip that line, continue grep. |
| Workspace config missing | STOP with "No workspace found" (existing behavior). |
| jq unavailable in wal-bind-guard | Allow all operations (fail-open, unlike wal-guard which is fail-closed for Bash). The binding guard is advisory — the security guard handles dangerous patterns separately. |
| WAL directory missing | Create `claude_docs/wal/` on first write. |
| Project path resolution fails | Allow the operation, log warning. |
| Cross-project deny on Read tool | Deny with explanation. User can override by switching projects. |

## Impact analysis — full list of changes

### Hooks (modify)
| Hook | Change |
|------|--------|
| `wal-context` | Multi-active detection cascade (count active projects) |
| `wal-guard` | No change (stays Bash-only) |
| `wal-pre` | Use `wal_resolve_project()`, write to per-project WAL |
| `wal-post` | Same |
| `wal-post-fail` | Same |
| `wal-stop` | Read from per-project WAL |
| `wal-lib.sh` | Add `wal_resolve_project()`, update `wal_init_file()` |
| `session-start` | Add reconciliation section, update resume context, per-project WAL recovery |

### Hooks (create)
| Hook | Purpose |
|------|---------|
| `wal-bind-guard` | Session binding enforcement + cross-project file guard |

### Skills (modify)
| Skill | Change |
|-------|--------|
| `switch` | Remove deactivation of other projects, add `off` subcommand, add session binding |
| `new-project` | Remove deactivation of other projects |
| 10 workflow skills | Update `<config-loading>` Level 3 to handle multiple active |

### Files (create)
| File | Purpose |
|------|---------|
| `hooks/wal-bind-guard` | New PreToolUse hook |
| `claude_docs/wal/` | Per-project WAL directory |

### Files (no change)
| File | Why unchanged |
|------|---------------|
| `.rawgentic_workspace.json` | No schema change, `active` semantics clarified |
| `session_registry.jsonl` | No schema change, already supports multi-project |
| `session_notes/{project}.md` | Already per-project |

## Testing strategy

- **Unit tests:** wal-bind-guard deny/allow logic (unbound, bound, cross-project, outside-all-projects)
- **Integration tests:** wal-context cascade with 0, 1, 2+ active projects
- **E2E tests:** Full flow — new session with multiple active projects, denied until switch, then allowed
- **Regression:** Existing single-project behavior unchanged when only 1 project is active

## Future work

- `rawgentic:housekeeping` skill — prune stale registry entries, archive old WAL files (see GitHub issue)
