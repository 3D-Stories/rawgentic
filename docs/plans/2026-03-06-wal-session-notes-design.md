# WAL & Session Notes Integration into Rawgentic Plugin

**Date:** 2026-03-06
**Status:** Approved
**Context:** Brainstorming session on WAL robustness, context compaction protection, and rawgentic concurrent session support.

---

## Problem Statement

Three related problems converged into one design:

1. **Context compaction vulnerability** — When Claude Code compacts conversation context, Claude loses awareness of what it did. The WAL has durable data on disk but nothing re-injects it. Claude doesn't know what it doesn't know.

2. **Session notes replacement** — Claude sometimes overwrites the entire `session_notes.md` instead of appending, destroying historical context. The current CLAUDE.md instruction ("log to session_notes.md") is too vague — Claude interprets "update" as "rewrite."

3. **Rawgentic concurrent session race condition** — Two sessions sharing the same CWD both read `active: true` from `.rawgentic_workspace.json`. `/rawgentic:switch` in one session mutates the shared file, silently changing the active project for the other.

## Solution: Session Registry + Per-Project Notes + Hook Enforcement

A single `session_registry.jsonl` bridges all three problems: it maps session_id to project (solving #3), survives compaction (solving #1), and routes session notes to per-project files where APPEND-ONLY is enforced by hook prompts (solving #2).

---

## Architecture

### File Structure

```
{cwd}/claude_docs/
├── wal.jsonl                          # WAL audit log (existing, unchanged)
├── session_registry.jsonl             # NEW: session → project mapping (JSONL, append-only)
├── session_notes/
│   ├── data_catalogue.md              # Rolling notes for this project
│   ├── STARS-COC-POC.md               # Rolling notes for this project
│   └── archive/
│       ├── data_catalogue_2026-02-28.md
│       └── STARS-COC-POC_2026-03-01.md
```

### Session Registry (`session_registry.jsonl`)

Append-only JSONL. One line per session, written when Claude identifies its project:

```jsonl
{"session_id":"abc123","project":"data_catalogue","project_path":"./projects/data_catalogue","started":"2026-03-06T15:00:00Z","cwd":"/home/candrosoff/claude"}
{"session_id":"def456","project":"STARS-COC-POC","project_path":"./projects/STARS-COC-POC","started":"2026-03-06T15:30:00Z","cwd":"/home/candrosoff/claude"}
```

**Who writes:**
- `/rawgentic:switch` — registers session when switching projects
- `/rawgentic:new-project` — registers session for newly created project
- Any session's first action — CLAUDE.md instruction tells Claude to register if no entry exists
- Stop hook — catches unregistered sessions as a last resort

**Who reads:**
- UserPromptSubmit hook — injects project context on every prompt
- Rawgentic config-loading block — level 2 fallback for active project
- Stop hook — routes to correct session notes file
- SessionStart hook — knows which project's notes to archive

### Session Notes Format (per-project `.md`)

Each session APPENDS a block with a machine-parseable header:

```markdown
---

# Session: 2026-03-06 | ID: abc123 | Status: COMPLETE
## Task: WF3 Bug Fix: Issue #155
## Project: data_catalogue

### Changes Made
1. **exit_monitor.py** — 3 fixes:
   - Replaced `_is_market_open()` with `is_market_hours(runtime_config)`
   ...

### Verification
- 101 tests passed, 0 failures
- CI green

### Next Steps
- Deploy to dev
```

Conventions:
- `---` separator before each session block
- Line 1: `# Session: YYYY-MM-DD | ID: {session_id} | Status: {IN PROGRESS|COMPLETE}`
- `## Task:` and `## Project:` on dedicated lines (grep-parseable)
- Freeform detailed notes after header (rich narrative style)

### Archival

Mechanical, triggered by SessionStart hook:
- Check line count of each `session_notes/{project}.md`
- If >800 lines: move to `archive/{project}_{date}.md`, create fresh file with `# Session Notes — {project}` header
- Never discard — archive is permanent

---

## Hook Design

### Current State (in `~/.claude/settings.json` + `~/.claude/hooks/`)

| Hook | Event | Script | Purpose |
|------|-------|--------|---------|
| WAL Guard | PreToolUse (Bash) | `wal-guard.sh` | Block dangerous commands |
| WAL Pre | PreToolUse (Bash\|Edit\|Write\|NotebookEdit\|Task) | `wal-pre.sh` | Log INTENT |
| WAL Post | PostToolUse (same) | `wal-post.sh` | Log DONE |
| WAL Post-Fail | PostToolUseFailure (same) | `wal-post-fail.sh` | Log FAIL |
| WAL Session Start | SessionStart | `wal-session-start.sh` | Detect incomplete ops, rotate WAL |
| Session Notes Reminder | Pre/PostToolUse (TaskUpdate) | `session-notes-reminder.sh` | Remind Claude to update notes |

### Target State (in rawgentic plugin `hooks/`)

All hooks move into the rawgentic plugin's `hooks/` directory and are registered via `hooks/hooks.json`:

| Hook | Event | Script | Purpose | New? |
|------|-------|--------|---------|------|
| WAL Guard | PreToolUse (Bash) | `hooks/wal-guard` | Block dangerous commands | Moved |
| WAL Pre | PreToolUse (Bash\|Edit\|Write\|NotebookEdit\|Task) | `hooks/wal-pre` | Log INTENT | Moved |
| WAL Post | PostToolUse (same) | `hooks/wal-post` | Log DONE | Moved |
| WAL Post-Fail | PostToolUseFailure (same) | `hooks/wal-post-fail` | Log FAIL | Moved |
| Session Start | SessionStart | `hooks/session-start` | Detect incomplete ops, rotate WAL, archive session notes, inject project context | Enhanced |
| Context Injector | UserPromptSubmit | `hooks/wal-context` | **NEW**: Inject session context on every prompt | **New** |
| Stop Gate | Stop | `hooks/wal-stop` | **NEW**: Force session notes update before stopping | **New** |

The `session-notes-reminder.sh` (TaskUpdate hook) is **removed** — the Stop hook replaces it with a stronger enforcement mechanism.

### Hook Details

#### `hooks/wal-context` (UserPromptSubmit) — NEW

Injects lightweight context on every user prompt:

1. Read `session_id` from hook input JSON
2. Lookup project: `grep {session_id} claude_docs/session_registry.jsonl | tail -1`
3. If no registration: inject "Register this session" reminder
4. Read last 10 WAL entries: `grep {session_id} claude_docs/wal.jsonl | tail -10`
5. Read session header: `grep -A3 "ID: {session_id}" claude_docs/session_notes/{project}.md | head -4`
6. Return `additionalContext`

Output (~150-250 tokens):
```
SESSION CONTEXT [data_catalogue | abc123]:
  Task: WF3 Bug Fix: Issue #155
  Status: IN PROGRESS

  Recent actions (last 10):
    [15:30:01] Edit: edit /home/.../exit_monitor.py
    [15:30:05] Bash: python -m pytest tests/
    [15:30:12] Edit: edit /home/.../test_exit_monitor.py

  Full session notes: claude_docs/session_notes/data_catalogue.md
```

Error-tolerant: `_do_context 2>/dev/null || true; exit 0`

#### `hooks/wal-stop` (Stop) — NEW

Forces session notes update before Claude stops:

1. Read `session_id` from hook input
2. Lookup project in registry
3. **Gate 1 — Registry check:** If unregistered, return `continue: false` with instruction to register + update notes
4. **Gate 2 — Notes freshness:** Check if `session_notes/{project}.md` was modified in last 2 minutes
5. If fresh → `continue: true`
6. If stale → `continue: false` with APPEND-ONLY prompt:

Stop hook prompt language:
```
Before finishing, APPEND a new session summary to claude_docs/session_notes/{project}.md

RULES:
- APPEND ONLY. Do NOT modify, rewrite, or remove any existing content in the file.
- Start with a --- separator, then the structured header:
  # Session: {date} | ID: {session_id} | Status: COMPLETE
  ## Task: <what you worked on>
  ## Project: {project}
- After the header, include: changes made, verification results, and next steps.
- If the file does not exist, create it with:
  # Session Notes — {project}
  Then append your session block below.

Also ensure you are registered in claude_docs/session_registry.jsonl.
```

#### `hooks/session-start` (SessionStart) — ENHANCED

Merges existing rawgentic session-start + WAL session-start:

1. **WAL operations:** Sanitize `wal.jsonl`, rotate if >5000 lines, detect incomplete ops
2. **Session notes archival:** For each `session_notes/*.md`, check line count. If >800, move to `archive/{project}_{date}.md`
3. **Project context:** Read workspace JSON, find active project, inject context (existing behavior)
4. **Registry context:** If session_id found in registry, inject project from registry (overrides workspace default)

#### `hooks/wal-lib.sh` — SHARED LIBRARY (existing, moved)

All common functions: jq resolution, input parsing, WAL path setup, phase append, summary extraction. No changes to API — just moves from `~/.claude/hooks/` to rawgentic `hooks/`.

### hooks.json (updated)

```json
{
  "hooks": {
    "SessionStart": [
      {
        "matcher": "startup|resume|clear|compact",
        "hooks": [
          {
            "type": "command",
            "command": "'${CLAUDE_PLUGIN_ROOT}/hooks/session-start'",
            "timeout": 10
          }
        ]
      }
    ],
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "'${CLAUDE_PLUGIN_ROOT}/hooks/wal-guard'",
            "timeout": 5
          }
        ]
      },
      {
        "matcher": "Bash|Edit|Write|NotebookEdit|Task",
        "hooks": [
          {
            "type": "command",
            "command": "'${CLAUDE_PLUGIN_ROOT}/hooks/wal-pre'",
            "timeout": 5
          }
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "Bash|Edit|Write|NotebookEdit|Task",
        "hooks": [
          {
            "type": "command",
            "command": "'${CLAUDE_PLUGIN_ROOT}/hooks/wal-post'",
            "timeout": 5
          }
        ]
      }
    ],
    "PostToolUseFailure": [
      {
        "matcher": "Bash|Edit|Write|NotebookEdit|Task",
        "hooks": [
          {
            "type": "command",
            "command": "'${CLAUDE_PLUGIN_ROOT}/hooks/wal-post-fail'",
            "timeout": 5
          }
        ]
      }
    ],
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "'${CLAUDE_PLUGIN_ROOT}/hooks/wal-context'",
            "timeout": 5
          }
        ]
      }
    ],
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "'${CLAUDE_PLUGIN_ROOT}/hooks/wal-stop'",
            "timeout": 10
          }
        ]
      }
    ]
  }
}
```

---

## Rawgentic Config-Loading Integration

### Current Config-Loading Block (in all 9 workflow skills)

```
Extract the active project entry (active == true) from .rawgentic_workspace.json
```

### New Config-Loading Fallback Chain

```
1. Conversation context (set by /rawgentic:switch in this session)
     ↓ (lost after compaction)
2. Session registry (claude_docs/session_registry.jsonl → grep session_id)
     ↓ (missing if brand new session, never registered)
3. Workspace JSON default (.rawgentic_workspace.json → active: true)
```

The UserPromptSubmit hook injects from level 2 on every prompt, so after compaction level 1 is effectively restored. The chain is self-healing.

### Rawgentic Skill Changes

- `/rawgentic:switch` — append to `session_registry.jsonl` when switching, update `lastUsed` in workspace JSON, set conversation context
- `/rawgentic:new-project` — append to `session_registry.jsonl` for new project
- All 9 workflow skills — update `<config-loading>` block with 3-level fallback
- `/rawgentic:setup` — update config-loading equivalent in Step 1

---

## CLAUDE.md Changes

### Level 1: `~/.claude/CLAUDE.md` (Global)

Replace the existing WAL Protocol section:

```markdown
## WAL Protocol (Write-Ahead Logging)

**IMPORTANT: This protocol is ALWAYS active. Hooks enforce it automatically.**

Every mutation action (Bash, Edit, Write, NotebookEdit, Task) is logged to `claude_docs/wal.jsonl`
before and after execution via hooks. You do not need to manually write to the WAL — the hooks handle it.

### Session Registration
On your first meaningful action in a new session, if the UserPromptSubmit hook has not already
identified your project, append a registration line to `claude_docs/session_registry.jsonl`:
  {"session_id":"<your session_id>","project":"<project_name>","project_path":"<relative_path>","started":"<ISO timestamp>","cwd":"<working directory>"}
Use the rawgentic workspace or project CLAUDE.md to determine the project name.

### Session Notes
- Notes are stored per-project at `claude_docs/session_notes/{project}.md`
- **APPEND ONLY** — never modify or remove existing content
- Use the structured header format:
  ---
  # Session: YYYY-MM-DD | ID: {session_id} | Status: IN PROGRESS
  ## Task: <task description>
  ## Project: <project name>
- Update status to COMPLETE when done
- The Stop hook will block you from finishing if notes aren't updated

### WAL Log Format
The WAL at `claude_docs/wal.jsonl` contains one JSON object per line:
  INTENT → logged before tool execution
  DONE   → logged after successful execution
  FAIL   → logged after failed execution
An INTENT without a matching DONE/FAIL means the operation was interrupted.

### Session Start
1. The hooks automatically check for incomplete WAL operations and report them
2. If incomplete operations are reported, assess whether recovery action is needed
3. The UserPromptSubmit hook injects your session context on every prompt —
   this survives context compaction
```

### Level 2: `~/claude/CLAUDE.md` (Workspace)

Add section:

```markdown
## Session Notes
- Registry: ./claude_docs/session_registry.jsonl
- Notes: ./claude_docs/session_notes/{project}.md
- Archive: ./claude_docs/session_notes/archive/
- Projects: see .rawgentic_workspace.json for valid project names and paths
```

### Level 3: Project CLAUDE.md files

No changes needed. Project name already available via `PROJECT_ROOT`, `REPO`, or rawgentic config.

---

## Migration Plan

### Phase 1: Move hooks to rawgentic plugin
1. Copy hook scripts to `rawgentic/hooks/` (wal-lib.sh, wal-guard, wal-pre, wal-post, wal-post-fail)
2. Update `rawgentic/hooks/hooks.json` with all hook registrations
3. Merge session-start hooks (rawgentic's + WAL's) into one script
4. Remove hooks from `~/.claude/hooks/` (except leave wal-guard as global fallback)
5. Remove hook registrations from `~/.claude/settings.json`
6. Remove `session-notes-reminder.sh` (replaced by Stop hook)

### Phase 2: Add new hooks
7. Create `hooks/wal-context` (UserPromptSubmit)
8. Create `hooks/wal-stop` (Stop)
9. Create `claude_docs/session_notes/` directory structure

### Phase 3: Update rawgentic skills
10. Update config-loading block in all 9 workflow skills + setup
11. Update `/rawgentic:switch` to write session registry
12. Update `/rawgentic:new-project` to write session registry

### Phase 4: Update CLAUDE.md files
13. Update `~/.claude/CLAUDE.md` with new WAL protocol
14. Update `~/claude/CLAUDE.md` with session notes section

---

## Error Handling

| Scenario | Behavior |
|----------|----------|
| jq missing | Guard: fail-closed (deny all). Loggers: exit 0 silently. Context hook: exit 0 silently. |
| Session registry missing | Context hook: inject "register session" reminder. Stop hook: force registration. |
| Session notes file missing | Stop hook: instruct Claude to create with header. Context hook: skip notes section. |
| Malformed JSONL | Session-start sanitizes via `jq -c '.'`. Context hook greps raw text (doesn't parse). |
| WAL file missing | Session-start: nothing to check. Context hook: skip WAL section. |
| Disk full | All loggers: exit 0 silently (error-tolerant). Guard: still works (reads stdin, no writes). |
| Concurrent writes | Registry: JSONL append is atomic on Linux (< PIPE_BUF). Notes: per-project files eliminate cross-session races. |

---

## Verification Criteria

1. **Guard still blocks** — `rm -rf`, `git push --force`, etc. all blocked after migration
2. **INTENT/DONE pairs appear** — normal tool use still logged to `wal.jsonl`
3. **Context injection works** — every prompt shows session context in `additionalContext`
4. **Stop hook fires** — Claude cannot stop without updating session notes
5. **Archival works** — session notes >800 lines are moved to archive
6. **Concurrent sessions isolated** — two sessions on different projects write to separate notes files
7. **Compaction recovery** — after compaction, Claude still knows its project and recent actions
8. **Config-loading fallback** — rawgentic skills find active project via registry after compaction
9. **No-jq degradation** — guard denies, loggers exit 0, context hook exits 0
10. **Plugin install/uninstall** — installing rawgentic registers all hooks, uninstalling removes them cleanly
