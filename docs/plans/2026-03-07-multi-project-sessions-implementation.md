# Multi-Project Concurrent Sessions Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Enable multiple Claude Code sessions to work on different rawgentic projects simultaneously from the same workspace root, with per-project WAL isolation, session binding enforcement, and cross-project file guards.

**Architecture:** Session registry (`session_registry.jsonl`) becomes the authoritative source for session→project binding. Multiple projects can be `active: true` simultaneously (provisioned). A new `wal-bind-guard` hook enforces binding before tool use and prevents cross-project file writes. WAL files split from a shared `wal.jsonl` to per-project `wal/{project}.jsonl`.

**Tech Stack:** Bash (hooks), jq, Python 3 (session-start hook), Markdown (skills)

**Design doc:** `docs/plans/2026-03-07-multi-project-sessions-design.md`

---

### Task 1: Update wal-lib.sh with project resolution

**Files:**
- Modify: `hooks/wal-lib.sh`

**Step 1: Add `wal_resolve_project()` function**

Append after the existing `wal_append_phase()` function in `hooks/wal-lib.sh`:

```bash

# --- Project resolution ---
# Resolves which project the current session is bound to.
# Reads session registry and workspace config.
# Sets: WAL_PROJECT (empty string if unresolvable)
# Requires: WAL_SESSION_ID, WAL_CWD to be set.
wal_resolve_project() {
  WAL_PROJECT=""
  local registry_file="$WAL_CWD/claude_docs/session_registry.jsonl"

  # Check session registry first
  if [ -f "$registry_file" ] && [ "$WAL_SESSION_ID" != "unknown" ]; then
    local reg_line
    reg_line=$(grep "$WAL_SESSION_ID" "$registry_file" 2>/dev/null | tail -1 || true)
    if [ -n "$reg_line" ]; then
      WAL_PROJECT=$(printf '%s' "$reg_line" | "$WAL_JQ" -r '.project // ""' 2>/dev/null || true)
    fi
  fi
}
```

**Step 2: Update `wal_init_file()` to use per-project WAL**

Replace the existing `wal_init_file()` function:

```bash
# --- WAL file setup ---
# Creates claude_docs/wal directory and sets WAL_FILE path.
# If WAL_PROJECT is set, uses per-project WAL file.
# If WAL_PROJECT is empty, skips (WAL_FILE left empty).
# Requires: WAL_CWD to be set. WAL_PROJECT should be set via wal_resolve_project().
wal_init_file() {
  WAL_FILE=""
  if [ -z "${WAL_PROJECT:-}" ]; then
    return 0
  fi
  WAL_DIR="$WAL_CWD/claude_docs/wal"
  mkdir -p "$WAL_DIR"
  WAL_FILE="$WAL_DIR/${WAL_PROJECT}.jsonl"
}
```

**Step 3: Test with mock data**

Run:
```bash
cd $PLUGIN_ROOT && \
  echo '{"session_id":"test-123","tool_name":"Write","tool_input":{"file_path":"/tmp/test.js","command":""},"tool_use_id":"tu_1","cwd":"$WORKSPACE_ROOT"}' | \
  bash -c '
    source hooks/wal-lib.sh
    wal_parse_input
    echo "SESSION_ID=$WAL_SESSION_ID"
    wal_resolve_project
    echo "PROJECT=$WAL_PROJECT"
    wal_init_file
    echo "WAL_FILE=$WAL_FILE"
  '
```
Expected: `SESSION_ID=test-123`, `PROJECT=` (empty — no registry entry for test-123), `WAL_FILE=` (empty — unbound)

Run with a real session ID from the registry:
```bash
REAL_SID=$(tail -1 $WORKSPACE_ROOT/claude_docs/session_registry.jsonl | jq -r '.session_id')
echo "{\"session_id\":\"$REAL_SID\",\"tool_name\":\"Write\",\"tool_input\":{\"file_path\":\"/tmp/test.js\"},\"tool_use_id\":\"tu_1\",\"cwd\":\"$WORKSPACE_ROOT\"}" | \
  bash -c '
    source hooks/wal-lib.sh
    wal_parse_input
    wal_resolve_project
    echo "PROJECT=$WAL_PROJECT"
    wal_init_file
    echo "WAL_FILE=$WAL_FILE"
  '
```
Expected: `PROJECT=my-api` (or whatever the last registered project is), `WAL_FILE=.../claude_docs/wal/my-api.jsonl`

**Step 4: Commit**

```bash
git add hooks/wal-lib.sh
git commit -m "feat(wal): add project resolution and per-project WAL paths

wal_resolve_project() reads session registry to determine bound project.
wal_init_file() now writes to claude_docs/wal/{project}.jsonl.
Unbound sessions get WAL_FILE='' (skip writes)."
```

---

### Task 2: Update wal-pre, wal-post, wal-post-fail for per-project WAL

**Files:**
- Modify: `hooks/wal-pre`
- Modify: `hooks/wal-post`
- Modify: `hooks/wal-post-fail`

**Step 1: Update wal-pre**

Replace `hooks/wal-pre` contents:

```bash
#!/bin/bash
# WAL Pre-Tool Logger — Logs INTENT before mutation tool execution.
# Hook: PreToolUse (matcher: Bash|Edit|Write|NotebookEdit|Task)
# Appends to $CWD/claude_docs/wal/{project}.jsonl
# Error-tolerant: always exits 0 even if logging fails.
# Skips WAL write if session is unbound (no project resolved).

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

_do_log() {
  set -euo pipefail
  source "$SCRIPT_DIR/wal-lib.sh"
  wal_parse_input
  wal_resolve_project
  wal_init_file
  [ -z "$WAL_FILE" ] && return 0
  wal_extract_summary
  wal_append_phase "INTENT" --arg summary "$WAL_SUMMARY" --arg cwd "$WAL_CWD"
}

_do_log 2>/dev/null || true
exit 0
```

**Step 2: Update wal-post**

Replace `hooks/wal-post` contents:

```bash
#!/bin/bash
# WAL Post-Tool Logger — Logs DONE after successful tool execution.
# Hook: PostToolUse (matcher: Bash|Edit|Write|NotebookEdit|Task)
# Appends to $CWD/claude_docs/wal/{project}.jsonl
# Error-tolerant: always exits 0 even if logging fails.
# Skips WAL write if session is unbound (no project resolved).

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

_do_log() {
  set -euo pipefail
  source "$SCRIPT_DIR/wal-lib.sh"
  wal_parse_input
  wal_resolve_project
  wal_init_file
  [ -z "$WAL_FILE" ] && return 0
  wal_append_phase "DONE"
}

_do_log 2>/dev/null || true
exit 0
```

**Step 3: Update wal-post-fail**

Replace `hooks/wal-post-fail` contents:

```bash
#!/bin/bash
# WAL Post-Failure Logger — Logs FAIL after tool execution failure.
# Hook: PostToolUseFailure (matcher: Bash|Edit|Write|NotebookEdit|Task)
# Appends to $CWD/claude_docs/wal/{project}.jsonl
# Error-tolerant: always exits 0 even if logging fails.
# Skips WAL write if session is unbound (no project resolved).

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

_do_log() {
  set -euo pipefail
  source "$SCRIPT_DIR/wal-lib.sh"
  wal_parse_input
  wal_resolve_project
  wal_init_file
  [ -z "$WAL_FILE" ] && return 0
  wal_append_phase "FAIL"
}

_do_log 2>/dev/null || true
exit 0
```

**Step 4: Test — verify per-project WAL write**

```bash
cd $PLUGIN_ROOT && \
  REAL_SID=$(tail -1 $WORKSPACE_ROOT/claude_docs/session_registry.jsonl | jq -r '.session_id') && \
  echo "{\"session_id\":\"$REAL_SID\",\"tool_name\":\"Write\",\"tool_input\":{\"file_path\":\"/tmp/test.js\"},\"tool_use_id\":\"tu_test1\",\"cwd\":\"$WORKSPACE_ROOT\"}" | \
  bash hooks/wal-pre && \
  echo "--- WAL directory contents:" && \
  ls -la $WORKSPACE_ROOT/claude_docs/wal/ 2>/dev/null && \
  echo "--- Last line:" && \
  tail -1 $WORKSPACE_ROOT/claude_docs/wal/*.jsonl 2>/dev/null
```
Expected: New `wal/` directory with per-project `.jsonl` file containing an INTENT entry

**Step 5: Commit**

```bash
git add hooks/wal-pre hooks/wal-post hooks/wal-post-fail
git commit -m "feat(wal): switch pre/post/fail hooks to per-project WAL

All three hooks now call wal_resolve_project() before wal_init_file().
Writes go to claude_docs/wal/{project}.jsonl. Unbound sessions skip
WAL writes silently."
```

---

### Task 3: Update wal-stop for per-project WAL

**Files:**
- Modify: `hooks/wal-stop`

**Step 1: Update wal-stop**

Two changes needed in `hooks/wal-stop`:

1. **Line 37** — The `active` fallback currently assumes single-active. With multiple active, `jq` returns multiple names joined by newlines. Take the first one as a best-effort fallback (this is the stop hook — we need a project name, any reasonable match is better than nothing):

Replace line 37:
```bash
        PROJECT=$("$JQ" -r '.projects[] | select(.active == true) | .name' "$WORKSPACE_FILE" 2>/dev/null || true)
```
With:
```bash
        # Multi-active fallback: with multiple active projects, pick the first one.
        # The stop hook needs *some* project name for WAL entry. Best-effort is acceptable here
        # because the session is ending — the WAL entry is informational, not authoritative.
        PROJECT=$("$JQ" -r '[.projects[] | select(.active == true) | .name] | first // ""' "$WORKSPACE_FILE" 2>/dev/null || true)
```

2. **Lines 69-75** — Update WAL path from shared to per-project:

Replace:
```bash
    # Log to WAL
    WAL_FILE="$CWD/claude_docs/wal.jsonl"
    "$JQ" -nc \
        --arg ts "$TS" \
        --arg session "$SESSION_ID" \
        --arg project "$PROJECT" \
        '{ts:$ts, phase:"STOP", session:$session, project:$project, summary:"Session ended"}' \
        >> "$WAL_FILE" 2>/dev/null || true
```
With:
```bash
    # Log to per-project WAL
    WAL_DIR="$CWD/claude_docs/wal"
    mkdir -p "$WAL_DIR" 2>/dev/null || true
    "$JQ" -nc \
        --arg ts "$TS" \
        --arg session "$SESSION_ID" \
        --arg project "$PROJECT" \
        '{ts:$ts, phase:"STOP", session:$session, project:$project, summary:"Session ended"}' \
        >> "$WAL_DIR/${PROJECT}.jsonl" 2>/dev/null || true
```

**Step 2: Commit**

```bash
git add hooks/wal-stop
git commit -m "feat(wal): update stop hook for per-project WAL and multi-active

Stop hook now writes to claude_docs/wal/{project}.jsonl.
Active project fallback uses jq first/array to handle multiple active."
```

---

### Task 4: Update wal-context for multi-active detection cascade

**Files:**
- Modify: `hooks/wal-context`

**Step 1: Rewrite the unregistered-session handling block**

In `hooks/wal-context`, replace lines 42-75 (the entire "If not registered, try to infer project from active workspace entry" block) with the new cascade:

```bash
    # If not registered, use detection cascade
    if [ -z "$PROJECT" ]; then
        # Count active projects
        ACTIVE_COUNT=$("$JQ" '[.projects[] | select(.active == true)] | length' "$WORKSPACE_FILE" 2>/dev/null || echo "0")

        if [ "$ACTIVE_COUNT" -eq 0 ]; then
            "$JQ" -nc --arg ctx "No active projects in workspace. Use /rawgentic:new-project to set one up." \
                '{additionalContext: $ctx}'
            exit 0
        elif [ "$ACTIVE_COUNT" -eq 1 ]; then
            # Single active project — auto-bind
            ACTIVE_PROJECT=$("$JQ" -r '.projects[] | select(.active == true) | .name' "$WORKSPACE_FILE" 2>/dev/null || true)
            ACTIVE_PATH=$("$JQ" -r '.projects[] | select(.active == true) | .path' "$WORKSPACE_FILE" 2>/dev/null || true)

            if [ -n "$ACTIVE_PROJECT" ]; then
                # Auto-register this session for the active project
                mkdir -p "$CWD/claude_docs/session_notes"
                TS=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
                "$JQ" -nc \
                    --arg sid "$SESSION_ID" \
                    --arg proj "$ACTIVE_PROJECT" \
                    --arg ppath "$ACTIVE_PATH" \
                    --arg ts "$TS" \
                    --arg cwd "$CWD" \
                    '{session_id:$sid, project:$proj, project_path:$ppath, started:$ts, cwd:$cwd}' \
                    >> "$REGISTRY_FILE" 2>/dev/null || true

                # Initialize session notes file if missing
                NOTES_FILE="$NOTES_DIR/${ACTIVE_PROJECT}.md"
                if [ ! -f "$NOTES_FILE" ]; then
                    mkdir -p "$NOTES_DIR"
                    printf '# Session Notes -- %s\n' "$ACTIVE_PROJECT" > "$NOTES_FILE"
                fi

                PROJECT="$ACTIVE_PROJECT"
                PROJECT_PATH="$ACTIVE_PATH"
            fi
        else
            # Multiple active projects — prompt user to bind
            ACTIVE_NAMES=$("$JQ" -r '[.projects[] | select(.active == true) | .name] | join(", ")' "$WORKSPACE_FILE" 2>/dev/null || true)
            "$JQ" -nc --arg ctx "Multiple projects active: ${ACTIVE_NAMES}. Use /rawgentic:switch <name> to bind this session to a project." \
                '{additionalContext: $ctx}'
            exit 0
        fi
    fi
```

**Step 2: Test — single active project (backward compat)**

Set up: ensure only one project is active in `.rawgentic_workspace.json`.

```bash
cd $PLUGIN_ROOT && \
  echo '{"session_id":"test-single-active","cwd":"$WORKSPACE_ROOT","hook_event_name":"submit"}' | \
  bash hooks/wal-context
```
Expected: JSON with `additionalContext` containing session context for the single active project.

**Step 3: Test — multiple active projects**

Temporarily set two projects active:
```bash
cd $WORKSPACE_ROOT && \
  python3 -c "
import json
with open('.rawgentic_workspace.json') as f:
    ws = json.load(f)
count = sum(1 for p in ws['projects'] if p.get('active'))
print(f'Active count: {count}')
names = [p['name'] for p in ws['projects'] if p.get('active')]
print(f'Active projects: {names}')
"
```

If only one is active, temporarily enable a second:
```bash
python3 -c "
import json
with open('.rawgentic_workspace.json') as f:
    ws = json.load(f)
for p in ws['projects']:
    if p['name'] == 'rawgentic':
        p['active'] = True
with open('.rawgentic_workspace.json', 'w') as f:
    json.dump(ws, f, indent=2)
print('Set rawgentic active=true')
"
```

Then test:
```bash
echo '{"session_id":"test-multi-active","cwd":"$WORKSPACE_ROOT","hook_event_name":"submit"}' | \
  bash $PLUGIN_ROOT/hooks/wal-context
```
Expected: JSON with `additionalContext` containing "Multiple projects active: my-api, rawgentic. Use /rawgentic:switch..."

**Step 4: Revert test data and commit**

Revert the workspace back to its original state (only my-api active), then commit:
```bash
cd $WORKSPACE_ROOT && \
  python3 -c "
import json
with open('.rawgentic_workspace.json') as f:
    ws = json.load(f)
for p in ws['projects']:
    if p['name'] == 'rawgentic':
        p['active'] = False
with open('.rawgentic_workspace.json', 'w') as f:
    json.dump(ws, f, indent=2)
" && \
cd projects/rawgentic && \
git add hooks/wal-context && \
git commit -m "feat(wal): multi-active detection cascade in wal-context

Counts active projects: 0 -> no active message, 1 -> auto-bind
(backward compat), 2+ -> prompt user to /rawgentic:switch."
```

---

### Task 5: Create wal-bind-guard hook

**Files:**
- Create: `hooks/wal-bind-guard`

**Step 1: Write the hook**

Create `hooks/wal-bind-guard`:

```bash
#!/bin/bash
# WAL Bind Guard — Enforces session binding and prevents cross-project file writes.
# Hook: PreToolUse (matcher: Edit|Write|MultiEdit|NotebookEdit|Read)
# Denies tool use if:
#   1. Session is unbound and multiple projects are active
#   2. File path belongs to a different project than the bound one
# Fail-open: if jq is unavailable or any error occurs, allows the operation.
# Design: docs/plans/2026-03-07-multi-project-sessions-design.md

set -euo pipefail

INPUT=$(cat)

JQ="${HOME}/.local/bin/jq"
[ -x "$JQ" ] || JQ="jq"
command -v "$JQ" &>/dev/null || exit 0

SESSION_ID=$(printf '%s' "$INPUT" | "$JQ" -r '.session_id // "unknown"')
CWD=$(printf '%s' "$INPUT" | "$JQ" -r '.cwd // "."')
TOOL_NAME=$(printf '%s' "$INPUT" | "$JQ" -r '.tool_name // ""')

[ "$SESSION_ID" = "unknown" ] && exit 0

# --- Find workspace ---
WORKSPACE_FILE=""
[ -f "$CWD/.rawgentic_workspace.json" ] && WORKSPACE_FILE="$CWD/.rawgentic_workspace.json"
[ -z "$WORKSPACE_FILE" ] && [ -f "$CWD/../.rawgentic_workspace.json" ] && WORKSPACE_FILE="$CWD/../.rawgentic_workspace.json"
[ -z "$WORKSPACE_FILE" ] && exit 0

# --- Resolve bound project from registry ---
REGISTRY_FILE="$CWD/claude_docs/session_registry.jsonl"
BOUND_PROJECT=""
if [ -f "$REGISTRY_FILE" ]; then
    REG_LINE=$(grep "$SESSION_ID" "$REGISTRY_FILE" 2>/dev/null | tail -1 || true)
    if [ -n "$REG_LINE" ]; then
        BOUND_PROJECT=$(printf '%s' "$REG_LINE" | "$JQ" -r '.project // ""' 2>/dev/null || true)
    fi
fi

# --- Gate 1: Unbound session check ---
if [ -z "$BOUND_PROJECT" ]; then
    ACTIVE_COUNT=$("$JQ" '[.projects[] | select(.active == true)] | length' "$WORKSPACE_FILE" 2>/dev/null || echo "0")
    if [ "$ACTIVE_COUNT" -gt 1 ]; then
        ACTIVE_NAMES=$("$JQ" -r '[.projects[] | select(.active == true) | .name] | join(", ")' "$WORKSPACE_FILE" 2>/dev/null || true)
        cat <<DENYEOF
{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny"},"systemMessage":"This session isn't bound to a project yet. Multiple projects are active: ${ACTIVE_NAMES}. Ask the user which project they want to work on, then run /rawgentic:switch <name> to bind this session."}
DENYEOF
        exit 0
    fi
    # Single or zero active — allow (wal-context handles auto-bind)
    exit 0
fi

# --- Gate 2: Cross-project file guard ---
# Extract file path from tool input
FILE_PATH=""
if [ "$TOOL_NAME" = "NotebookEdit" ]; then
    FILE_PATH=$(printf '%s' "$INPUT" | "$JQ" -r '.tool_input.notebook_path // ""' 2>/dev/null || true)
else
    FILE_PATH=$(printf '%s' "$INPUT" | "$JQ" -r '.tool_input.file_path // ""' 2>/dev/null || true)
fi

# No file path or not absolute — allow
[ -z "$FILE_PATH" ] && exit 0
[[ "$FILE_PATH" != /* ]] && exit 0

# Resolve workspace root (directory containing .rawgentic_workspace.json)
WORKSPACE_ROOT=$(cd "$(dirname "$WORKSPACE_FILE")" && pwd 2>/dev/null) || {
    # Path resolution failed — fail-open, log to stderr for debugging
    echo "wal-bind-guard: could not resolve workspace root from $WORKSPACE_FILE" >&2
    exit 0
}

# Check if file is under a DIFFERENT project's directory
VIOLATION=$("$JQ" -r --arg file "$FILE_PATH" --arg bound "$BOUND_PROJECT" --arg root "$WORKSPACE_ROOT" '
  .projects[]
  | select(.name != $bound and .active == true)
  | .path as $p
  | ($root + "/" + ($p | ltrimstr("./")) + "/") as $abs
  | select($file | startswith($abs))
  | .name
' "$WORKSPACE_FILE" 2>/dev/null | head -1 || true)

if [ -n "$VIOLATION" ]; then
    cat <<DENYEOF
{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny"},"systemMessage":"You're bound to **${BOUND_PROJECT}** but this file is in **${VIOLATION}**. Switch first with /rawgentic:switch ${VIOLATION}, or ask the user if this cross-project edit is intentional."}
DENYEOF
    exit 0
fi

# All checks passed — allow
exit 0
```

**Step 2: Make executable**

```bash
chmod +x $PLUGIN_ROOT/hooks/wal-bind-guard
```

**Step 3: Test — unbound session, multiple active**

Temporarily set two projects active, then test:
```bash
cd $WORKSPACE_ROOT && \
  python3 -c "
import json
with open('.rawgentic_workspace.json') as f:
    ws = json.load(f)
for p in ws['projects']:
    if p['name'] == 'rawgentic':
        p['active'] = True
with open('.rawgentic_workspace.json', 'w') as f:
    json.dump(ws, f, indent=2)
" && \
echo '{"session_id":"unbound-test","tool_name":"Write","tool_input":{"file_path":"/tmp/test.js"},"cwd":"$WORKSPACE_ROOT"}' | \
  bash $PLUGIN_ROOT/hooks/wal-bind-guard
```
Expected: JSON with `permissionDecision: deny` and message about binding

**Step 4: Test — bound session, same project file**

```bash
REAL_SID=$(tail -1 $WORKSPACE_ROOT/claude_docs/session_registry.jsonl | jq -r '.session_id') && \
echo "{\"session_id\":\"$REAL_SID\",\"tool_name\":\"Write\",\"tool_input\":{\"file_path\":\"$WORKSPACE_ROOT/projects/my-api/src/app.js\"},\"cwd\":\"$WORKSPACE_ROOT\"}" | \
  bash $PLUGIN_ROOT/hooks/wal-bind-guard
```
Expected: No output (exit 0 — allowed, file is in bound project)

**Step 5: Test — bound session, cross-project file**

```bash
REAL_SID=$(tail -1 $WORKSPACE_ROOT/claude_docs/session_registry.jsonl | jq -r '.session_id') && \
echo "{\"session_id\":\"$REAL_SID\",\"tool_name\":\"Write\",\"tool_input\":{\"file_path\":\"$PLUGIN_ROOT/hooks/test.sh\"},\"cwd\":\"$WORKSPACE_ROOT\"}" | \
  bash $PLUGIN_ROOT/hooks/wal-bind-guard
```
Expected: JSON with `permissionDecision: deny` and message about cross-project write (session is bound to my-api, file is in rawgentic)

**Step 6: Revert test data and commit**

```bash
cd $WORKSPACE_ROOT && \
  python3 -c "
import json
with open('.rawgentic_workspace.json') as f:
    ws = json.load(f)
for p in ws['projects']:
    if p['name'] == 'rawgentic':
        p['active'] = False
with open('.rawgentic_workspace.json', 'w') as f:
    json.dump(ws, f, indent=2)
" && \
cd projects/rawgentic && \
git add hooks/wal-bind-guard && \
git commit -m "feat(wal): add bind guard for session binding and cross-project protection

New PreToolUse hook that:
1. Denies all tool calls if session is unbound and multiple projects active
2. Denies file writes to directories of other active projects
Fail-open on jq unavailable or errors."
```

---

### Task 6: Update session-start hook

**Files:**
- Modify: `hooks/session-start`

This is the largest change. Three modifications:

**Step 1: Add Section 0 — Project directory reconciliation**

Insert a new section BEFORE Section 1 (WAL Operations). The insertion point is after line 38 (`CONTEXT_PARTS=()`) but this new section also needs access to `WORKSPACE_FILE` and `_registry_project`, which are defined later in the current code (Section 3, lines 138-153). You must either:
- Move the workspace-file and registry-project resolution **above** Section 1 (recommended — they're read-only lookups), or
- Have Section 0 do its own workspace-file resolution internally.

**Recommended approach:** Move lines 137-153 (registry lookup + workspace file resolution) to just after `CONTEXT_PARTS=()` on line 38. Then insert Section 0 after those moved lines, before `# SECTION 1`.

The resulting order after line 38 (`CONTEXT_PARTS=()`) should be:
1. Registry project lookup (moved from lines 137-145)
2. Workspace file resolution (moved from lines 147-153)
3. **Section 0: Reconciliation** (new)
4. Section 1: WAL Operations (existing)
5. Section 2: Archival (existing)
6. Section 3: Rawgentic Workspace Context (remaining logic only — project display)

Section 0 code:

```bash
# =========================================================================
# SECTION 0: Project Directory Reconciliation (startup/resume only)
# =========================================================================
_do_reconciliation() {
    [ "$EVENT_TYPE" = "startup" ] || [ "$EVENT_TYPE" = "resume" ] || return 0
    [ -z "$WORKSPACE_FILE" ] && return 0

    if ! command -v python3 &>/dev/null; then
        return 0
    fi

    # Check each active project's directory exists
    MISSING_PROJECTS=$(python3 -c "
import json, os
ws = json.load(open('${WORKSPACE_FILE}'))
ws_dir = os.path.dirname(os.path.abspath('${WORKSPACE_FILE}'))
missing = []
for p in ws.get('projects', []):
    if p.get('active'):
        abs_path = os.path.normpath(os.path.join(ws_dir, p['path']))
        if not os.path.isdir(abs_path):
            missing.append(f\"{p['name']}|{p['path']}\")
for m in missing:
    print(m)
" 2>/dev/null || true)

    if [ -n "$MISSING_PROJECTS" ]; then
        # Deactivate missing projects in workspace config
        # Pass missing names via env var to avoid shell injection
        MISSING_NAMES=$(echo "$MISSING_PROJECTS" | cut -d'|' -f1 | paste -sd',' -)
        MISSING_NAMES="$MISSING_NAMES" python3 -c "
import json, os
ws_path = os.environ.get('WORKSPACE_FILE', '${WORKSPACE_FILE}')
missing_names = set(os.environ.get('MISSING_NAMES', '').split(','))
with open(ws_path) as f:
    ws = json.load(f)
for p in ws.get('projects', []):
    if p['name'] in missing_names:
        p['active'] = False
with open(ws_path, 'w') as f:
    json.dump(ws, f, indent=2)
" 2>/dev/null || true

        # Build context message
        while IFS='|' read -r name path; do
            CONTEXT_PARTS+=("Project **${name}** is registered but directory \`${path}\` is missing. Was it removed, or did setup crash? Reply 'remove' to deregister, or 'setup' to re-run \`/rawgentic:new-project ${name}\`.")
        done <<< "$MISSING_PROJECTS"
    fi
}
_do_reconciliation 2>/dev/null || true
```

**Step 2: Update Section 1 — WAL recovery to use per-project WAL**

Replace line 48 in Section 1:
```bash
    WAL_FILE="$CWD/claude_docs/wal.jsonl"
```
With:
```bash
    # Determine WAL file: per-project if bound, fall back to legacy
    WAL_FILE=""
    if [ -n "$_registry_project" ]; then
        WAL_FILE="$CWD/claude_docs/wal/${_registry_project}.jsonl"
    fi
    # Fall back to legacy WAL if per-project doesn't exist
    if [ -z "$WAL_FILE" ] || [ ! -f "$WAL_FILE" ]; then
        WAL_FILE="$CWD/claude_docs/wal.jsonl"
    fi
```

**Step 3: Update Section 3 — Resume context**

Replace the entire block from `# If registry knows our project` (line 165) through the closing `fi` of `$PROJECTS_COUNT -eq 0 / else` on line 199. This is the block that starts with:
```bash
            # If registry knows our project, report that; otherwise use active project
            if [ -n "$_registry_project" ]; then
```
and ends at the matching `fi` that closes the `else` of `$PROJECTS_COUNT -eq 0`.

**Important:** The workspace file resolution and registry lookup will have been moved to before Section 1 (per Step 1), so Section 3 will look different. The block to replace is everything inside the `$PROJECTS_COUNT > 0` else branch that handles project display.

Replace with:

```python
            # Determine project context
            if [ -n "$_registry_project" ]; then
                # Session is bound — show bound project info
                ACTIVE_INFO=$(python3 -c "
import json
d = json.load(open('${WORKSPACE_FILE}'))
for p in d.get('projects', []):
    if p['name'] == '${_registry_project}':
        print(f\"{p['name']}|{p['path']}|{p.get('configured', False)}|{p.get('active', False)}\")
        break
" 2>/dev/null || true)

                if [ -n "${ACTIVE_INFO:-}" ]; then
                    IFS='|' read -r ACTIVE_NAME ACTIVE_PATH CONFIGURED IS_ACTIVE <<< "$ACTIVE_INFO"
                    if [ "$EVENT_TYPE" = "resume" ]; then
                        if [ "$IS_ACTIVE" = "True" ]; then
                            CONTEXT_PARTS+=("Resuming work on project: **${ACTIVE_NAME}** (${ACTIVE_PATH}).")
                        else
                            CONTEXT_PARTS+=("Your previous session was bound to **${ACTIVE_NAME}**, but that project is no longer active (directory may have been removed).")
                        fi
                    else
                        if [ "$CONFIGURED" = "True" ]; then
                            CONTEXT_PARTS+=("Active project: ${ACTIVE_NAME} (${ACTIVE_PATH}).")
                        else
                            CONTEXT_PARTS+=("Active project: ${ACTIVE_NAME} (${ACTIVE_PATH}). Config missing -- run /rawgentic:setup.")
                        fi
                    fi
                fi
            else
                # No registry binding — show active project summary
                ACTIVE_SUMMARY=$(python3 -c "
import json
d = json.load(open('${WORKSPACE_FILE}'))
active = [p['name'] for p in d.get('projects', []) if p.get('active')]
if len(active) == 0:
    print('NONE')
elif len(active) == 1:
    p = [p for p in d['projects'] if p.get('active')][0]
    conf = 'True' if p.get('configured') else 'False'
    print(f\"SINGLE|{p['name']}|{p['path']}|{conf}\")
else:
    print(f\"MULTI|{', '.join(active)}\")
" 2>/dev/null || true)

                case "${ACTIVE_SUMMARY%%|*}" in
                    NONE)
                        CONTEXT_PARTS+=("Rawgentic workspace has projects but none is active. Run /rawgentic:switch to select one.")
                        ;;
                    SINGLE)
                        IFS='|' read -r _ ACTIVE_NAME ACTIVE_PATH CONFIGURED <<< "$ACTIVE_SUMMARY"
                        if [ "$CONFIGURED" = "True" ]; then
                            CONTEXT_PARTS+=("Active project: ${ACTIVE_NAME} (${ACTIVE_PATH}).")
                        else
                            CONTEXT_PARTS+=("Active project: ${ACTIVE_NAME} (${ACTIVE_PATH}). Config missing -- run /rawgentic:setup.")
                        fi
                        ;;
                    MULTI)
                        NAMES="${ACTIVE_SUMMARY#MULTI|}"
                        CONTEXT_PARTS+=("Multiple projects active: ${NAMES}. Use /rawgentic:switch <name> to bind this session to a project.")
                        ;;
                esac
            fi
```

**Step 4: Test — startup with all projects active**

```bash
cd $PLUGIN_ROOT && \
echo '{"session_id":"test-startup","cwd":"$WORKSPACE_ROOT","hook_event_name":"startup"}' | \
  bash hooks/session-start
```
Expected: JSON with `additionalContext` showing active project info

**Step 5: Test — resume with registry binding**

```bash
REAL_SID=$(tail -1 $WORKSPACE_ROOT/claude_docs/session_registry.jsonl | jq -r '.session_id') && \
echo "{\"session_id\":\"$REAL_SID\",\"cwd\":\"$WORKSPACE_ROOT\",\"hook_event_name\":\"resume\"}" | \
  bash $PLUGIN_ROOT/hooks/session-start
```
Expected: JSON with `additionalContext` containing "Resuming work on project: **my-api**"

**Step 6: Commit**

```bash
cd $PLUGIN_ROOT && \
git add hooks/session-start && \
git commit -m "feat(session-start): add reconciliation, resume context, per-project WAL recovery

Section 0: Checks active project directories exist, deactivates missing.
Section 1: WAL recovery reads per-project WAL, falls back to legacy.
Section 3: Shows resume context for bound sessions, handles multi-active."
```

---

### Task 7: Register wal-bind-guard in hooks.json

**Files:**
- Modify: `hooks/hooks.json`

**Step 1: Add PreToolUse entry for wal-bind-guard**

Add a new entry to the `PreToolUse` array, BEFORE the wal-pre entry (so binding is checked before WAL logging). Insert after the wal-guard (Bash) entry:

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
},
```

**Step 2: Validate JSON**

```bash
cd $PLUGIN_ROOT && \
python3 -c "import json; h=json.load(open('hooks/hooks.json')); print('PreToolUse entries:', len(h['hooks']['PreToolUse'])); [print(f'  {e[\"matcher\"]}') for e in h['hooks']['PreToolUse']]"
```
Expected: 4 PreToolUse entries: `Bash`, `Edit|Write|MultiEdit|NotebookEdit|Read`, `Bash|Edit|Write|NotebookEdit|Task`, `Edit|Write|MultiEdit|NotebookEdit`

**Step 3: Commit**

```bash
git add hooks/hooks.json && \
git commit -m "feat(hooks): register wal-bind-guard in hooks.json

Adds PreToolUse hook for Edit|Write|MultiEdit|NotebookEdit|Read.
Placed before wal-pre so binding is enforced before WAL logging."
```

---

### Task 8: Update switch skill

**Files:**
- Modify: `skills/switch/SKILL.md`

**Step 1: Read the current switch skill**

Read from `skills/switch/SKILL.md` (the source in the repo, not the cache).

**Step 2: Rewrite the skill**

Replace the entire content of `skills/switch/SKILL.md` with:

```markdown
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
```

**Step 3: Commit**

```bash
cd $PLUGIN_ROOT && \
git add skills/switch/SKILL.md && \
git commit -m "feat(switch): rewrite for multi-project concurrent sessions

Switch now binds the current session to a project without deactivating
others. Adds 'off' subcommand to deactivate, list mode shows all
projects with recent session counts. Multiple projects can be active."
```

---

### Task 9: Update new-project skill

**Files:**
- Modify: `skills/new-project/SKILL.md`

**Step 1: Read the current new-project skill**

Read `skills/new-project/SKILL.md` and find the deactivation step (around line 128).

**Step 2: Remove the deactivation instruction**

Find and remove the line that says:
```
1. **Deactivate** any project that currently has `active: true` (set it to `false`).
```

Keep the instruction that sets the new project's `active: true`.

**Step 3: Commit**

```bash
cd $PLUGIN_ROOT && \
git add skills/new-project/SKILL.md && \
git commit -m "feat(new-project): remove single-active enforcement

new-project no longer deactivates other projects when activating a new
one. Multiple projects can be active simultaneously."
```

---

### Task 10: Update config-loading pattern in all workflow skills

**Files (9 skills with `<config-loading>`):**
- Modify: `skills/implement-feature/SKILL.md`
- Modify: `skills/fix-bug/SKILL.md`
- Modify: `skills/refactor/SKILL.md`
- Modify: `skills/update-deps/SKILL.md`
- Modify: `skills/incident/SKILL.md`
- Modify: `skills/create-issue/SKILL.md`
- Modify: `skills/security-audit/SKILL.md`
- Modify: `skills/optimize-perf/SKILL.md`
- Modify: `skills/update-docs/SKILL.md`

**Note:** `new-project` and `switch` do NOT use the `<config-loading>` pattern — they manage project activation directly (updated in Tasks 8 and 9). `setup` has a slightly different format handled separately in Step 3.

**Step 1: Define the updated config-loading pattern**

In each skill, find the `<config-loading>` section. The Level 3 fallback currently reads:

```
   **Level 3 -- Workspace default:** Read `.rawgentic_workspace.json` from the Claude root directory. Extract the active project entry (active == true).
```

Replace with:

```
   **Level 3 -- Workspace default:** Read `.rawgentic_workspace.json` from the Claude root directory. If exactly one project has `active == true`, use it. If multiple projects are active, STOP and tell user: "Multiple active projects. Run `/rawgentic:switch <name>` to bind this session."
```

Also update the error case:
```
   - No active project found at any level -> STOP. Tell user: "No active project. Run /rawgentic:switch to select one."
```
Changes to:
```
   - No active project found at any level -> STOP. Tell user: "No active project. Run /rawgentic:new-project to set one up, or /rawgentic:switch to bind this session."
```

**Step 2: Apply to all 9 skills**

For each skill, read the file, find the `<config-loading>` section, and make the Level 3 replacement. The line numbers vary by skill (see audit results) but the text to replace is identical in all 9.

**Step 3: Also update setup skill**

The `setup` skill has a slightly different format but the same logic. Find its fallback chain and make the equivalent change.

**Step 4: Commit**

```bash
cd $PLUGIN_ROOT && \
git add skills/*/SKILL.md && \
git commit -m "feat(skills): update config-loading for multi-active projects

Level 3 fallback now handles multiple active projects by stopping
and asking user to /rawgentic:switch. Applied to all 9 workflow skills
plus setup skill."
```

---

### Task 11: E2E Integration Test

**Purpose:** Verify the full flow works end-to-end before shipping.

**Step 1: Set up multi-active state**

```bash
cd $WORKSPACE_ROOT && \
python3 -c "
import json
with open('.rawgentic_workspace.json') as f:
    ws = json.load(f)
for p in ws['projects']:
    if p['name'] in ('my-api', 'rawgentic'):
        p['active'] = True
with open('.rawgentic_workspace.json', 'w') as f:
    json.dump(ws, f, indent=2)
print('Both my-api and rawgentic set active=true')
"
```

**Step 2: Test unbound session is blocked (wal-bind-guard)**

```bash
echo '{"session_id":"e2e-unbound","tool_name":"Write","tool_input":{"file_path":"/tmp/test.js"},"cwd":"$WORKSPACE_ROOT"}' | \
  bash $PLUGIN_ROOT/hooks/wal-bind-guard
```
Expected: JSON with `permissionDecision: deny` mentioning "Multiple projects are active"

**Step 3: Test wal-context cascade with multiple active**

```bash
echo '{"session_id":"e2e-multi","cwd":"$WORKSPACE_ROOT","hook_event_name":"submit"}' | \
  bash $PLUGIN_ROOT/hooks/wal-context
```
Expected: JSON with `additionalContext` containing "Multiple projects active"

**Step 4: Test session-start with multiple active (startup)**

```bash
echo '{"session_id":"e2e-startup","cwd":"$WORKSPACE_ROOT","hook_event_name":"startup"}' | \
  bash $PLUGIN_ROOT/hooks/session-start
```
Expected: JSON mentioning multiple active projects

**Step 5: Test bound session allows same-project, denies cross-project**

```bash
# Register a session to my-api
echo '{"session_id":"e2e-bound","project":"my-api","project_path":"./projects/my-api","started":"2026-03-07T22:00:00Z","cwd":"$WORKSPACE_ROOT"}' \
  >> $WORKSPACE_ROOT/claude_docs/session_registry.jsonl

# Same project — should ALLOW (no output)
echo '{"session_id":"e2e-bound","tool_name":"Write","tool_input":{"file_path":"$WORKSPACE_ROOT/projects/my-api/test.js"},"cwd":"$WORKSPACE_ROOT"}' | \
  bash $PLUGIN_ROOT/hooks/wal-bind-guard

# Cross project — should DENY
echo '{"session_id":"e2e-bound","tool_name":"Write","tool_input":{"file_path":"$PLUGIN_ROOT/test.js"},"cwd":"$WORKSPACE_ROOT"}' | \
  bash $PLUGIN_ROOT/hooks/wal-bind-guard
```
Expected: First command produces no output (allow). Second produces JSON with `permissionDecision: deny`.

**Step 6: Test per-project WAL write**

```bash
echo '{"session_id":"e2e-bound","tool_name":"Write","tool_input":{"file_path":"$WORKSPACE_ROOT/projects/my-api/test.js"},"tool_use_id":"tu_e2e","cwd":"$WORKSPACE_ROOT"}' | \
  bash $PLUGIN_ROOT/hooks/wal-pre && \
ls -la $WORKSPACE_ROOT/claude_docs/wal/ && \
tail -1 $WORKSPACE_ROOT/claude_docs/wal/my-api.jsonl
```
Expected: WAL entry written to `wal/my-api.jsonl` (not `wal.jsonl`)

**Step 7: Clean up test data**

```bash
cd $WORKSPACE_ROOT && \
python3 -c "
import json
with open('.rawgentic_workspace.json') as f:
    ws = json.load(f)
for p in ws['projects']:
    if p['name'] == 'rawgentic':
        p['active'] = False
with open('.rawgentic_workspace.json', 'w') as f:
    json.dump(ws, f, indent=2)
" && \
# Remove test registry entry
sed -i '/e2e-bound/d' claude_docs/session_registry.jsonl 2>/dev/null || true && \
rm -f claude_docs/wal/my-api.jsonl 2>/dev/null || true && \
echo "E2E cleanup complete"
```

---

### Task 12: Version bump, push, and plugin reinstall

**Files:**
- Modify: `.claude-plugin/plugin.json`

**Step 1: Bump version**

Update `.claude-plugin/plugin.json` version from `2.4.0` to `2.5.0`.

**Step 1b: Add .gitignore entry for wal directory (in workspace root)**

Ensure `claude_docs/wal/` is gitignored in the workspace root. Check if `.gitignore` exists in `$WORKSPACE_ROOT/` and add the entry if missing:

```bash
cd $WORKSPACE_ROOT && \
grep -q 'claude_docs/wal/' .gitignore 2>/dev/null || echo 'claude_docs/wal/' >> .gitignore
```

**Step 2: Run full manual verification**

```bash
cd $PLUGIN_ROOT && \
echo "--- Hook files:" && \
ls -la hooks/wal-bind-guard hooks/wal-context hooks/wal-lib.sh hooks/wal-pre hooks/wal-post hooks/wal-post-fail hooks/wal-stop hooks/session-start && \
echo "--- hooks.json PreToolUse entries:" && \
python3 -c "import json; [print(f'  {e[\"matcher\"]}') for e in json.load(open('hooks/hooks.json'))['hooks']['PreToolUse']]" && \
echo "--- Git status:" && \
git status -s
```

**Step 3: Commit and push**

```bash
git add .claude-plugin/plugin.json && \
git commit -m "chore: bump version to 2.5.0

Multi-project concurrent sessions: session binding, per-project WAL,
cross-project guards, directory reconciliation on startup." && \
git push origin main
```

**Step 4: Reinstall plugin**

```bash
claude plugin remove rawgentic@rawgentic 2>&1 && claude plugin install rawgentic@rawgentic 2>&1
```

**Step 5: Verify by enabling both projects**

```bash
cd $WORKSPACE_ROOT && \
python3 -c "
import json
with open('.rawgentic_workspace.json') as f:
    ws = json.load(f)
for p in ws['projects']:
    if p['name'] in ('my-api', 'rawgentic'):
        p['active'] = True
with open('.rawgentic_workspace.json', 'w') as f:
    json.dump(ws, f, indent=2)
print('Both projects now active')
"
```

Then start a new Claude session — the session-start hook should show both projects as active and prompt for binding.
