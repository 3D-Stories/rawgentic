# Session Notes

## Overview

Per-project markdown files that track workflow progress across context
compactions and session resumptions. Located at
`claude_docs/session_notes/<project>.md`, auto-created when a session binds to
a project (via auto-bind or `/rawgentic:switch`).

## How Notes Are Populated

**Initialization.** The `wal-context` hook creates the file on first auto-bind
with a single header line:
```
# Session Notes -- <project>
```
The `session-start` hook uses the same format when re-creating after trimming.

**Context injection.** On every prompt, `wal-context` reads the notes file and
extracts status. It looks for a header matching `ID: <session_id>`, parses the
nearest `## Task:` line, checks for a `COMPLETE` marker, and computes staleness.
This is injected as `additionalContext` so the model always knows the current
task and status.

**Step markers.** Workflow skills append markers to track completed steps:
```
### WF2 Step X: <Name> -- DONE (<key detail>)
```
All executed steps must have markers; the workflow completion gate verifies this.

**Compaction recovery.** Before context compacts, workflow skills document the
current step, feature branch name, last commit SHA, loop-back budget state,
circuit breaker state, and detected capabilities. This enables the resumption
protocol to pick up where it left off.

## Session Registry

Maps Claude session IDs to projects so hooks know which project a session is
working on.

- **File:** `claude_docs/session_registry.jsonl`
- **Entry format** (one JSON object per line):
  ```json
  {"session_id":"<id>","project":"<name>","project_path":"<path>","started":"<ISO 8601>","cwd":"<workspace root>"}
  ```
- **Written by:** `/rawgentic:switch` or auto-bind in `wal-context` (when
  exactly one project is active).
- **Read by:** `session-start` and `wal-context` on every invocation; they grep
  for the current session ID and take the last matching line.

The session ID is persisted to `claude_docs/.current_session_id` by both hooks
so `/rawgentic:switch` can read it (env vars are not available to skills).

## Per-Project Handoff

A **handoff** is a short "where I left off / what's next" briefing carried from
one session to the next. Because every rawgentic-workspace session shares one
`CLAUDE_PROJECT_DIR` (the workspace root), the generic `remember` plugin's
workspace-level handoff (`.remember/remember.md`) cannot distinguish the bound
project — switching projects would mix handoffs. rawgentic therefore keeps a
**per-bound-project** handoff:

- **File:** `claude_docs/session_notes/<project>.handoff.md` (one per project,
  alongside the project's `<project>.md` notes).
- **Injected by:** the `session-start` hook (SECTION 2e) on the fresh-context
  events — `startup`, `resume`, and `clear` — for the **bound** project only
  (resolved from the session registry). Skipped on `compact` (which already
  preserves context). The hook also surfaces the file as the **write target** so
  the next handoff lands in the right place, superseding the workspace-level
  remember-plugin handoff for rawgentic-bound sessions.
- **Persistent, not consumed-on-read.** Unlike the remember plugin (which clears
  its handoff after injecting it), this file is left in place and simply
  overwritten when a new handoff is written — so a crash before the next handoff
  is recorded never loses the briefing.
- **Size cap:** injection is bounded by `RAWGENTIC_HANDOFF_MAX_CHARS` (default
  `8000`); longer handoffs are truncated in-context with a pointer to the full
  file.

Write the next handoff by overwriting `claude_docs/session_notes/<project>.handoff.md`
with the current state and next actions.

## Session Lifecycle

1. **Session starts.** `session-start` fires, trims oversized notes (see
   below), runs WAL recovery, checks security pattern staleness, emits
   workspace context.
2. **First prompt.** `wal-context` fires. If the session has no registry entry
   and exactly one project is active, it auto-binds: writes a registry entry
   and creates the notes file if missing.
3. **Multiple active projects.** If more than one project is active and no
   registry entry exists, `wal-context` prompts the user to run
   `/rawgentic:switch <name>`.
4. **Switch.** `/rawgentic:switch` reads the session ID from
   `.current_session_id`, appends a registry entry, and activates the project.
5. **Every subsequent prompt.** `wal-context` reads the registry, resolves the
   bound project, reads its notes file, injects task/status context.
6. **Workflow execution.** Skills append step markers and compaction recovery
   info to the notes file as they progress.
7. **Next session startup.** `session-start` trims oversized notes files
   before the new session begins.

## Size Handler

On `startup` and `compact` events, `session-start` runs `notes-size-handler.py`
on every `*.md` file in `claude_docs/session_notes/`.

### Behavior

- **Threshold:** 800 lines
- **Action:** Trim to the most recent 200 lines
- **Header:** Adds `# Session Notes -- <project>` and a
  `<!-- Trimmed from N lines at TIMESTAMP -->` comment

### Process

1. `session-start` iterates all `*.md` files in the session notes directory.
2. For each file, calls `hooks/notes-size-handler.py <notes_file> --session-id <id>`.
3. The Python script checks line count; if ≤800, exits with no action.
4. If >800 lines: optionally POSTs full content to the memorypalace server at
   `localhost:PORT/ingest` (best-effort, 2s timeout), then trims to last 200 lines.
5. Uses `fcntl.flock()` for exclusive access and atomic writes via
   `tempfile.mkstemp()` + `os.replace()`.

**Validation:** Project names (derived from filename stem) must match
`^[a-zA-Z0-9_-]+$`. Invalid names are skipped.

**Stdout isolation:** The size handler's stdout is redirected to `/dev/null` in
session-start to prevent JSON output from polluting the hook's own JSON response.

## Historical Archives (Inert)

The directory `claude_docs/session_notes/archive/` may contain JSONL files from
the legacy archival system (removed in v2.22.0). These files are **not deleted**
and may be used for backfill by the memorypalace plugin in the future. Nothing
currently reads from or writes to this directory.
