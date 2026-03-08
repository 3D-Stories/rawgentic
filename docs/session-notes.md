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
The `session-start` hook uses the same format when re-creating after archival.

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

## Session Lifecycle

1. **Session starts.** `session-start` fires, archives oversized notes (see
   below), runs WAL recovery, emits workspace context.
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
7. **Next session startup.** `session-start` checks all notes files for
   archival before the new session begins.

## Archival

On every `startup` event (not resume/compact/clear), `session-start` scans
`claude_docs/session_notes/*.md`. Any file exceeding 600 lines is archived:

1. Moved to `claude_docs/session_notes/archive/<project>_<YYYY-MM-DD>.md`.
2. Replaced with a fresh file containing only `# Session Notes -- <project>`.
3. An `additionalContext` message notifies the session that archival occurred.

The archive directory is created on demand.
