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
`claude_docs/session_notes/*.md`. Any file exceeding 600 lines is archived to
structured JSONL format.

### JSONL Archive Format

Each project has one JSONL file: `claude_docs/session_notes/archive/<project>.jsonl`.
Each line is a self-contained JSON object representing one archival event:

```json
{"schema_version":1,"archived_at":"2026-03-11T19:30:00Z","source_file":"rawgentic.md","line_count":750,"note":"trimmed markdown text","insights":null}
```

**Fields:**
- `schema_version` — Always `1`. Enables future migrations.
- `archived_at` — UTC ISO 8601 timestamp of archival.
- `source_file` — Original markdown filename.
- `line_count` — Line count of the original file at archival time.
- `note` — Trimmed note text (trailing whitespace stripped per line, 3+ blank
  lines collapsed to 2).
- `insights` — `null` initially, populated by Haiku enrichment (see below).

### Archival Process

1. `session-start` detects notes file >600 lines.
2. Calls `hooks/archive-notes.py <notes_file> <archive_dir>`.
3. The Python script reads the markdown, trims it, appends a JSONL entry to
   `archive/<project>.jsonl` using `fcntl.flock()` for concurrent safety,
   and resets the notes file to `# Session Notes -- <project>`.
4. An `additionalContext` message notifies the session that archival occurred.

**Validation:** Project names must match `^[a-zA-Z0-9_-]+$` (defense against
path traversal). Invalid names are skipped.

**Fallback:** If Python is unavailable, the archival step is skipped and the
notes file stays in place until the next startup.

### Haiku Enrichment

After archival, the hook checks for entries with `insights: null` across all
JSONL files. If unenriched entries exist, an `ARCHIVE_ENRICHMENT` instruction
is injected into `additionalContext`, telling Claude to use Haiku subagents in
the background to extract structured insights.

**Enriched insights schema:**
```json
{
  "summary": "one-line summary of the archival block",
  "sessions": [
    {
      "task": "WF2: Issue #5",
      "status": "COMPLETE",
      "patterns": ["lesson learned 1"],
      "decisions": ["chose X over Y because Z"],
      "artifacts": ["hooks/archive-notes.py"],
      "issues_encountered": ["problem and resolution"]
    }
  ]
}
```

Enrichment is deferred and best-effort — the archive is useful even without
enrichment (the `note` field contains the full trimmed text).

### Archive Querying

Archives are queryable via `hooks/query-archive.py`, a standalone Python script:

```
python3 hooks/query-archive.py <archive_dir> [options]
```

**Search modes:**
- `--keyword <term>` — searches `note` text + enriched `insights.summary` and
  `insights.sessions[].patterns[]`. Falls back to note-only for unenriched entries.
- `--pattern <term>` — searches `insights.sessions[].patterns[]` only (enriched entries).
- `--decision <term>` — searches `insights.sessions[].decisions[]` only (enriched entries).
- `--artifact <path>` — searches `insights.sessions[].artifacts[]` + note text.

**Filters:**
- `--project <name>` — restrict to a single project's `.jsonl` file.
- `--since <ISO-date>` — filter by `archived_at >= date`.
- `--limit <N>` — max results (default: 10).
- `--format brief|full` — brief omits note/insights, shows summary + match context.

**Integration points:**
- **Hook auto-injection:** `session-start` injects a brief archive summary (max 500
  chars) into `additionalContext` on startup/resume for bound sessions.
- **Skill protocol blocks:** `<archive-query>` blocks in fix-bug (WF3), incident
  (WF11), implement-feature (WF2), and refactor (WF4) skills query archives at
  Step 2 for relevant context (prior bugs, incidents, design decisions, patterns).
- **Interactive querying:** See issue #36 for the planned `/rawgentic:query-archives`
  skill.

### Backward Compatibility

Existing `.md` archives in the archive directory are unaffected. New archival
events produce `.jsonl` files. Both formats coexist in the archive directory.
