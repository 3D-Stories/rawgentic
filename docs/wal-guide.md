# WAL (Write-Ahead Log) Guide

## Overview

The WAL logs every mutation tool use (Bash, Edit, Write, NotebookEdit, Task) for
session recovery and audit. Each invocation produces an INTENT before execution
and a DONE or FAIL after. Sessions end with a STOP marker.

WAL files live under `claude_docs/wal/` relative to the workspace root:
- Per-project: `claude_docs/wal/<project>.jsonl`
- Legacy fallback: `claude_docs/wal.jsonl` (when no project is resolved)

## WAL Entry Format

Entries are single-line JSON (JSONL). Fields vary by phase.

**INTENT** (`hooks/wal-pre`):
```json
{"ts":"2026-03-07T12:00:00Z","phase":"INTENT","session":"abc-123","tool":"Bash","tool_use_id":"tu_01","summary":"git status","cwd":"/home/user/project"}
```

**DONE** (`hooks/wal-post`) / **FAIL** (`hooks/wal-post-fail`):
```json
{"ts":"2026-03-07T12:00:01Z","phase":"DONE","session":"abc-123","tool":"Bash","tool_use_id":"tu_01"}
```

**STOP** (`hooks/wal-stop`):
```json
{"ts":"2026-03-07T12:05:00Z","phase":"STOP","session":"abc-123","project":"myproject","summary":"Session ended"}
```

| Field         | Present in         | Description                              |
|---------------|--------------------|------------------------------------------|
| `ts`          | all                | UTC ISO-8601 timestamp                   |
| `phase`       | all                | INTENT, DONE, FAIL, or STOP             |
| `session`     | all                | Claude session ID                        |
| `tool`        | INTENT, DONE, FAIL | Tool name (Bash, Edit, Write, etc.)      |
| `tool_use_id` | INTENT, DONE, FAIL | Unique ID pairing INTENT with DONE/FAIL  |
| `summary`     | INTENT, STOP       | Human-readable description of the action |
| `cwd`         | INTENT             | Working directory at time of invocation  |
| `project`     | STOP               | Project name from session registry       |

## WAL Lifecycle

1. **wal-pre** (PreToolUse) -- resolves project, extracts summary, appends INTENT.
2. **wal-post** (PostToolUse) -- appends DONE with the same `tool_use_id`.
3. **wal-post-fail** (PostToolUseFailure) -- appends FAIL instead of DONE.
4. **wal-stop** (Stop) -- writes STOP marker and a session-end note.

All hooks source `hooks/wal-lib.sh` for shared functions: `wal_parse_input`,
`wal_find_workspace`, `wal_resolve_project`, `wal_init_file`,
`wal_extract_summary`, `wal_append_phase`. The stop hook duplicates some logic
inline. Every hook is error-tolerant (exits 0 on failure) so WAL logging never
blocks tool execution.

## Inspecting WAL Files

```bash
WAL=claude_docs/wal/myproject.jsonl

# Incomplete operations (INTENT without matching DONE or FAIL)
jq -sc 'group_by(.tool_use_id)
  | map(select(
      (map(.phase) | index("INTENT")) != null
      and (map(.phase) | index("DONE")) == null
      and (map(.phase) | index("FAIL")) == null))
  | map(map(select(.phase == "INTENT"))[0]) | .[]' "$WAL"

# Filter by session ID
jq -c 'select(.session == "TARGET_SESSION_ID")' "$WAL"

# Filter by tool name
jq -c 'select(.tool == "Bash")' "$WAL"

# Count operations per session
jq -sc '[.[] | select(.phase == "INTENT")] | group_by(.session)
  | map({session: .[0].session, count: length})' "$WAL"
```

## WAL Recovery

On every session event (startup, resume, clear, compact), `hooks/session-start`
runs WAL recovery:

1. **Resolve** -- finds the per-project WAL via session registry; falls back to
   `claude_docs/wal.jsonl`.
2. **Sanitize** -- pipes through `jq -c '.'`, discarding non-JSON lines.
3. **Rotate** -- when the file exceeds **5000 lines**, entries older than
   **7 days** are pruned. Incomplete operations are preserved regardless of age.
4. **Report** -- incomplete INTENT entries (no DONE/FAIL) are injected into the
   session context as a recovery notice (up to 20 shown).

## WAL Guard (`hooks/wal-guard`)

The WAL Guard is a separate PreToolUse hook (matcher: Bash) that blocks dangerous
commands **before** execution. Unlike the WAL logging hooks above, wal-guard can
**deny** tool use — it returns a JSON deny decision to prevent the command from
running.

### Blocked Patterns

Wal-guard blocks **production deployment commands** only:
- `ssh`/`scp`/`rsync` targeting prod hosts
- `docker compose` targeting prod with `up`/`restart`/`start`
- `ansible` targeting prod inventories
- `kubectl` targeting prod contexts/namespaces
- `helm install`/`upgrade` targeting prod
- `terraform apply` targeting prod

All patterns are case-insensitive and match anywhere in the command string.

### What Is NOT Blocked

Destructive local commands (`rm -rf`, `git push --force`, `git reset --hard`,
`git clean -f`, etc.) are **not** blocked by wal-guard. Claude's built-in safety
behavior already prompts the user before running these operations, and
hard-blocking via hooks would prevent the user from approving when they
intentionally want to proceed (hooks have no warn-and-confirm mechanism).

### Fail-Closed Behavior

If `jq` is unavailable, wal-guard denies **all** commands. This prevents
unguarded execution when the pattern-matching infrastructure is broken.

## Troubleshooting

**WAL_FILE is empty / no entries written** -- The project could not be resolved.
Verify the session appears in `claude_docs/session_registry.jsonl` with a valid
project name. Hooks silently skip WAL writes when WAL_FILE is empty.

**Corrupt entries** -- Session-start sanitization strips malformed lines
automatically. Manual fix: `jq -c '.' file.jsonl > /tmp/clean.jsonl && mv
/tmp/clean.jsonl file.jsonl`.

**Missing WAL file** -- Created on first write by `wal_init_file` (via
`mkdir -p`). No file means no mutation tools have run yet for that project.

**WAL file growing large** -- Rotation triggers at >5000 lines during
session-start. Between sessions the file is untouched; it will be pruned at
the next startup.
