# WAL & Session Notes Integration — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Move the WAL hook system into the rawgentic plugin, add context compaction protection (UserPromptSubmit hook), session notes enforcement (Stop hook), and a session registry for concurrent session isolation.

**Architecture:** All hooks move from `~/.claude/hooks/` to the rawgentic plugin's `hooks/` directory. Two new hooks (wal-context, wal-stop) add context injection and session notes enforcement. A session registry (`session_registry.jsonl`) maps session_id to project. Per-project session notes replace the monolithic `session_notes.md`. The 9 workflow skills get a 3-level config-loading fallback chain.

**Tech Stack:** Bash, jq, python3 (existing rawgentic session-start uses python3), Claude Code plugin hooks API

**Design Doc:** `docs/plans/2026-03-06-wal-session-notes-design.md`

---

## Task 1: Verify Hook Payload Schema (Phase 0)

We need to confirm that `session_id` is available in UserPromptSubmit and Stop hook payloads before writing hooks that depend on it.

**Files:**
- Create: `hooks/test-payload-dump` (temporary, deleted after verification)

**Step 1: Write a stub hook that dumps input JSON**

Create `hooks/test-payload-dump`:

```bash
#!/usr/bin/env bash
# Temporary hook to dump input JSON for schema verification
INPUT=$(cat)
DUMP_DIR="${HOME}/.claude/hook-payload-dumps"
mkdir -p "$DUMP_DIR"
HOOK_EVENT=$(echo "$INPUT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('hook_event_name','unknown'))" 2>/dev/null || echo "unknown")
TS=$(date +%s)
echo "$INPUT" | python3 -m json.tool > "$DUMP_DIR/${HOOK_EVENT}_${TS}.json" 2>/dev/null || echo "$INPUT" > "$DUMP_DIR/${HOOK_EVENT}_${TS}.raw"
exit 0
```

Make it executable:

```bash
chmod +x hooks/test-payload-dump
```

**Step 2: Temporarily register it in hooks.json**

Update `hooks/hooks.json` to add UserPromptSubmit and Stop entries pointing to the dump hook:

```json
{
  "hooks": {
    "SessionStart": [
      {
        "matcher": "startup|resume|clear|compact",
        "hooks": [
          {
            "type": "command",
            "command": "${CLAUDE_PLUGIN_ROOT}/hooks/session-start",
            "async": false
          }
        ]
      }
    ],
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "${CLAUDE_PLUGIN_ROOT}/hooks/test-payload-dump",
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
            "command": "${CLAUDE_PLUGIN_ROOT}/hooks/test-payload-dump",
            "timeout": 5
          }
        ]
      }
    ]
  }
}
```

**Step 3: Test in a separate Claude session**

Open a new Claude Code session in `~/claude/`. Type any prompt (triggers UserPromptSubmit), then type `/quit` or let it stop (triggers Stop). Then check the dumps:

```bash
ls -la ~/.claude/hook-payload-dumps/
cat ~/.claude/hook-payload-dumps/UserPromptSubmit_*.json
cat ~/.claude/hook-payload-dumps/Stop_*.json
```

**Step 4: Record which fields are available**

Verify that `session_id` exists in both payloads. Also note `cwd` and any other fields. If `session_id` is NOT in the payload, check for `CLAUDE_SESSION_ID` env var by adding `env > "$DUMP_DIR/env_${TS}.txt"` to the dump script and re-testing.

Document findings in a comment at the top of the plan file or in the commit message.

**Step 5: Clean up and commit**

Remove the test hook and revert hooks.json:

```bash
rm hooks/test-payload-dump
rm -rf ~/.claude/hook-payload-dumps/
```

Restore `hooks/hooks.json` to its original content (SessionStart only).

```bash
git add hooks/hooks.json
git commit -m "chore: verify UserPromptSubmit and Stop hook payload schema

Confirmed session_id is [available/unavailable] in hook payloads.
[Document findings here]

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Task 2: Copy WAL Hook Scripts to Plugin

Move existing WAL hooks from `~/.claude/hooks/` into the rawgentic plugin's `hooks/` directory. No logic changes — just copy and rename (drop `.sh` extension to match rawgentic convention).

**Files:**
- Create: `hooks/wal-lib.sh`
- Create: `hooks/wal-guard`
- Create: `hooks/wal-pre`
- Create: `hooks/wal-post`
- Create: `hooks/wal-post-fail`

**Step 1: Copy all 5 WAL scripts**

```bash
cp ~/.claude/hooks/wal-lib.sh hooks/wal-lib.sh
cp ~/.claude/hooks/wal-guard.sh hooks/wal-guard
cp ~/.claude/hooks/wal-pre.sh hooks/wal-pre
cp ~/.claude/hooks/wal-post.sh hooks/wal-post
cp ~/.claude/hooks/wal-post-fail.sh hooks/wal-post-fail
chmod +x hooks/wal-guard hooks/wal-pre hooks/wal-post hooks/wal-post-fail
```

**Step 2: Verify scripts work from new location**

Test the guard from the plugin directory:

```bash
echo '{"tool_name":"Bash","tool_input":{"command":"echo hello"},"session_id":"test","tool_use_id":"t1","cwd":"/tmp"}' | hooks/wal-guard
# Expected: exit 0, no output (command allowed)

echo '{"tool_name":"Bash","tool_input":{"command":"rm -rf /"},"session_id":"test","tool_use_id":"t1","cwd":"/tmp"}' | hooks/wal-guard
# Expected: JSON deny output with "Destructive rm command"
```

Test the pre-logger:

```bash
echo '{"tool_name":"Bash","tool_input":{"command":"ls"},"session_id":"test","tool_use_id":"t1","cwd":"/tmp"}' | hooks/wal-pre
# Expected: exit 0, INTENT line in /tmp/claude_docs/wal.jsonl
```

**Step 3: Commit**

```bash
git add hooks/wal-lib.sh hooks/wal-guard hooks/wal-pre hooks/wal-post hooks/wal-post-fail
git commit -m "feat: copy WAL hook scripts into rawgentic plugin

Copies wal-lib.sh, wal-guard, wal-pre, wal-post, wal-post-fail from
~/.claude/hooks/ with no logic changes. Drops .sh extension to match
rawgentic hook naming convention.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Task 3: Merge Session-Start Hooks

Combine the existing rawgentic `hooks/session-start` (workspace context injection) with the WAL `wal-session-start.sh` (incomplete ops detection, WAL rotation, sanitization) into a single unified script. Add session notes archival (>600 lines, startup only).

**Files:**
- Modify: `hooks/session-start`

**Step 1: Write the merged session-start hook**

The merged script must:
1. WAL operations: sanitize, rotate (>5000 lines), detect incomplete ops
2. Session notes archival: only on `startup` event, >600 lines → archive/
3. Rawgentic workspace context: existing behavior (python3 parsing)
4. Session registry lookup: if session_id in registry, inject project from there

Replace `hooks/session-start` with:

```bash
#!/usr/bin/env bash
# Unified SessionStart hook — WAL recovery + session notes archival + rawgentic context
# Fires on: startup, resume, clear, compact
# Design: docs/plans/2026-03-06-wal-session-notes-design.md

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# --- Shared utilities ---
escape_for_json() {
    local s="$1"
    s="${s//\\/\\\\}"
    s="${s//\"/\\\"}"
    s="${s//$'\n'/\\n}"
    s="${s//$'\r'/\\r}"
    s="${s//$'\t'/\\t}"
    printf '%s' "$s"
}

# --- Read input ---
INPUT=$(cat)

# Try jq first, fall back to python3
JQ="${HOME}/.local/bin/jq"
[ -x "$JQ" ] || JQ="jq"

if command -v "$JQ" &>/dev/null; then
    CWD=$(printf '%s' "$INPUT" | "$JQ" -r '.cwd // "."')
    SESSION_ID=$(printf '%s' "$INPUT" | "$JQ" -r '.session_id // "unknown"')
    EVENT_TYPE=$(printf '%s' "$INPUT" | "$JQ" -r '.hook_event_name // "startup"')
else
    CWD=$(printf '%s' "$INPUT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('cwd','.'))" 2>/dev/null || echo ".")
    SESSION_ID=$(printf '%s' "$INPUT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('session_id','unknown'))" 2>/dev/null || echo "unknown")
    EVENT_TYPE="startup"
fi

CONTEXT_PARTS=()

# =========================================================================
# SECTION 1: WAL Operations (always runs)
# =========================================================================
_do_wal_ops() {
    if ! command -v "$JQ" &>/dev/null; then
        return 0
    fi

    WAL_FILE="$CWD/claude_docs/wal.jsonl"
    [ -f "$WAL_FILE" ] || return 0

    # Sanitize malformed lines
    CLEAN_FILE=$(mktemp)
    "$JQ" -c '.' "$WAL_FILE" 2>/dev/null > "$CLEAN_FILE" || true
    [ -s "$CLEAN_FILE" ] || { rm -f "$CLEAN_FILE"; return 0; }

    # WAL Rotation (>5000 lines)
    LINE_COUNT=$(wc -l < "$CLEAN_FILE")
    if [ "$LINE_COUNT" -gt 5000 ]; then
        CUTOFF=$(date -u -d '7 days ago' +"%Y-%m-%dT%H:%M:%SZ" 2>/dev/null || date -u -v-7d +"%Y-%m-%dT%H:%M:%SZ" 2>/dev/null || printf '')
        if [ -n "$CUTOFF" ]; then
            ROTATED=$("$JQ" -sc --arg cutoff "$CUTOFF" '
                (group_by(.tool_use_id)
                 | map(select(
                     (map(.phase) | index("INTENT")) != null
                     and (map(.phase) | index("DONE")) == null
                     and (map(.phase) | index("FAIL")) == null
                   ))
                 | map(.[0].tool_use_id)
                ) as $incomplete_ids
                |
                [.[] | select(.ts >= $cutoff or (.tool_use_id as $id | $incomplete_ids | index($id) != null))]
                | .[]
            ' "$CLEAN_FILE" 2>/dev/null) || true
            if [ -n "$ROTATED" ]; then
                printf '%s\n' "$ROTATED" > "$CLEAN_FILE"
                cp "$CLEAN_FILE" "$WAL_FILE"
            fi
        fi
    fi

    # Find incomplete operations
    INCOMPLETE=$("$JQ" -sc '
        group_by(.tool_use_id)
        | map(
            select(
              (map(.phase) | index("INTENT")) != null
              and (map(.phase) | index("DONE")) == null
              and (map(.phase) | index("FAIL")) == null
            )
            | map(select(.phase == "INTENT"))[0]
          )
        | .[]
    ' "$CLEAN_FILE" 2>/dev/null) || true

    rm -f "$CLEAN_FILE"

    if [ -n "$INCOMPLETE" ]; then
        COUNT=$(printf '%s\n' "$INCOMPLETE" | wc -l)
        SUMMARY=$(printf '%s\n' "$INCOMPLETE" | "$JQ" -r '"  - [" + .ts + "] " + .tool + ": " + .summary' 2>/dev/null | head -20)
        CONTEXT_PARTS+=("WAL RECOVERY: Found $COUNT incomplete operation(s) from previous session(s):
$SUMMARY
Review these and determine if any recovery action is needed.")
    fi
}
_do_wal_ops 2>/dev/null || true

# =========================================================================
# SECTION 2: Session Notes Archival (startup event only)
# =========================================================================
_do_archival() {
    [ "$EVENT_TYPE" = "startup" ] || return 0

    NOTES_DIR="$CWD/claude_docs/session_notes"
    [ -d "$NOTES_DIR" ] || return 0

    ARCHIVE_DIR="$NOTES_DIR/archive"

    for notes_file in "$NOTES_DIR"/*.md; do
        [ -f "$notes_file" ] || continue
        LINE_COUNT=$(wc -l < "$notes_file")
        if [ "$LINE_COUNT" -gt 600 ]; then
            mkdir -p "$ARCHIVE_DIR"
            BASENAME=$(basename "$notes_file" .md)
            DATE_STAMP=$(date -u +"%Y-%m-%d")
            mv "$notes_file" "$ARCHIVE_DIR/${BASENAME}_${DATE_STAMP}.md"
            printf '# Session Notes -- %s\n' "$BASENAME" > "$notes_file"
            CONTEXT_PARTS+=("Session notes for '$BASENAME' archived ($LINE_COUNT lines) to session_notes/archive/.")
        fi
    done
}
_do_archival 2>/dev/null || true

# =========================================================================
# SECTION 3: Rawgentic Workspace Context (always runs)
# =========================================================================

# Check session registry first
_registry_project=""
REGISTRY_FILE="$CWD/claude_docs/session_registry.jsonl"
if [ -f "$REGISTRY_FILE" ] && [ "$SESSION_ID" != "unknown" ]; then
    _registry_line=$(grep "$SESSION_ID" "$REGISTRY_FILE" 2>/dev/null | tail -1 || true)
    if [ -n "$_registry_line" ] && command -v "$JQ" &>/dev/null; then
        _registry_project=$(printf '%s' "$_registry_line" | "$JQ" -r '.project // ""' 2>/dev/null || true)
    fi
fi

# Find workspace file
WORKSPACE_FILE=""
if [ -f "$CWD/.rawgentic_workspace.json" ]; then
    WORKSPACE_FILE="$CWD/.rawgentic_workspace.json"
elif [ -f "$CWD/../.rawgentic_workspace.json" ]; then
    WORKSPACE_FILE="$CWD/../.rawgentic_workspace.json"
fi

if [ -z "$WORKSPACE_FILE" ]; then
    CONTEXT_PARTS+=("No rawgentic workspace found. Run /rawgentic:new-project to get started.")
else
    if ! python3 -c "import json; json.load(open('${WORKSPACE_FILE}'))" 2>/dev/null; then
        CONTEXT_PARTS+=("Rawgentic workspace file is corrupted. Run /rawgentic:new-project to regenerate, or fix .rawgentic_workspace.json manually.")
    else
        PROJECTS_COUNT=$(python3 -c "import json; d=json.load(open('${WORKSPACE_FILE}')); print(len(d.get('projects',[])))")
        if [ "$PROJECTS_COUNT" -eq 0 ]; then
            CONTEXT_PARTS+=("Rawgentic workspace exists but no projects registered. Run /rawgentic:new-project.")
        else
            # If registry knows our project, report that; otherwise use active project
            if [ -n "$_registry_project" ]; then
                ACTIVE_INFO=$(python3 -c "
import json
d = json.load(open('${WORKSPACE_FILE}'))
for p in d.get('projects', []):
    if p['name'] == '${_registry_project}':
        print(f\"{p['name']}|{p['path']}|{p.get('configured', False)}\")
        break
" 2>/dev/null || true)
            fi

            # Fall back to active project if registry didn't match
            if [ -z "${ACTIVE_INFO:-}" ]; then
                ACTIVE_INFO=$(python3 -c "
import json
d = json.load(open('${WORKSPACE_FILE}'))
for p in d.get('projects', []):
    if p.get('active'):
        print(f\"{p['name']}|{p['path']}|{p.get('configured', False)}\")
        break
" 2>/dev/null || true)
            fi

            if [ -n "${ACTIVE_INFO:-}" ]; then
                IFS='|' read -r ACTIVE_NAME ACTIVE_PATH CONFIGURED <<< "$ACTIVE_INFO"
                if [ "$CONFIGURED" = "True" ]; then
                    CONTEXT_PARTS+=("Active project: ${ACTIVE_NAME} (${ACTIVE_PATH}).")
                else
                    CONTEXT_PARTS+=("Active project: ${ACTIVE_NAME} (${ACTIVE_PATH}). Config missing -- run /rawgentic:setup.")
                fi
            else
                CONTEXT_PARTS+=("Rawgentic workspace has projects but none is active. Run /rawgentic:switch to select one.")
            fi
        fi
    fi
fi

# =========================================================================
# SECTION 4: Emit combined context
# =========================================================================
if [ ${#CONTEXT_PARTS[@]} -eq 0 ]; then
    exit 0
fi

# Join all context parts with double newline
COMBINED=""
for part in "${CONTEXT_PARTS[@]}"; do
    if [ -n "$COMBINED" ]; then
        COMBINED="${COMBINED}

${part}"
    else
        COMBINED="$part"
    fi
done

ESCAPED=$(escape_for_json "$COMBINED")
cat <<HOOKEOF
{
  "hookSpecificOutput": {
    "hookEventName": "SessionStart",
    "additionalContext": "${ESCAPED}"
  }
}
HOOKEOF
```

**Step 2: Test the merged hook**

Test with a mock startup input:

```bash
echo '{"hook_event_name":"startup","cwd":"/home/candrosoff/claude","session_id":"test-123"}' | hooks/session-start
```

Expected: JSON output with rawgentic workspace context (and WAL recovery info if WAL has incomplete ops).

Test that archival only runs on startup:

```bash
echo '{"hook_event_name":"compact","cwd":"/home/candrosoff/claude","session_id":"test-123"}' | hooks/session-start
# Should NOT trigger archival even if notes > 600 lines
```

**Step 3: Commit**

```bash
git add hooks/session-start
git commit -m "feat: merge WAL session-start into rawgentic session-start hook

Unified hook handles: WAL sanitization, rotation (>5000 lines),
incomplete ops detection, session notes archival (>600 lines, startup
only), rawgentic workspace context injection, and session registry
lookup as project override.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Task 4: Create `wal-context` Hook (UserPromptSubmit)

Injects lightweight session context on every user prompt. Survives context compaction.

**Files:**
- Create: `hooks/wal-context`

**Step 1: Write the wal-context hook**

Create `hooks/wal-context`:

```bash
#!/usr/bin/env bash
# UserPromptSubmit hook — Injects session context on every prompt.
# Survives context compaction by re-injecting from disk.
# Error-tolerant: always exits 0.
# Design: docs/plans/2026-03-06-wal-session-notes-design.md

_do_context() {
    set -euo pipefail

    INPUT=$(cat)

    JQ="${HOME}/.local/bin/jq"
    [ -x "$JQ" ] || JQ="jq"
    command -v "$JQ" &>/dev/null || exit 0

    SESSION_ID=$(printf '%s' "$INPUT" | "$JQ" -r '.session_id // "unknown"')
    CWD=$(printf '%s' "$INPUT" | "$JQ" -r '.cwd // "."')

    [ "$SESSION_ID" = "unknown" ] && exit 0

    # Check for rawgentic workspace — skip if not a rawgentic project
    WORKSPACE_FILE=""
    [ -f "$CWD/.rawgentic_workspace.json" ] && WORKSPACE_FILE="$CWD/.rawgentic_workspace.json"
    [ -z "$WORKSPACE_FILE" ] && [ -f "$CWD/../.rawgentic_workspace.json" ] && WORKSPACE_FILE="$CWD/../.rawgentic_workspace.json"
    [ -z "$WORKSPACE_FILE" ] && exit 0

    REGISTRY_FILE="$CWD/claude_docs/session_registry.jsonl"
    WAL_FILE="$CWD/claude_docs/wal.jsonl"
    NOTES_DIR="$CWD/claude_docs/session_notes"

    # --- Lookup project from registry ---
    PROJECT=""
    PROJECT_PATH=""
    if [ -f "$REGISTRY_FILE" ]; then
        REG_LINE=$(grep "$SESSION_ID" "$REGISTRY_FILE" 2>/dev/null | tail -1 || true)
        if [ -n "$REG_LINE" ]; then
            PROJECT=$(printf '%s' "$REG_LINE" | "$JQ" -r '.project // ""')
            PROJECT_PATH=$(printf '%s' "$REG_LINE" | "$JQ" -r '.project_path // ""')
        fi
    fi

    # If not registered, inject reminder and exit
    if [ -z "$PROJECT" ]; then
        "$JQ" -nc --arg ctx "SESSION: Not registered. Use /rawgentic:switch or /rawgentic:new-project to register this session." \
            '{additionalContext: $ctx}'
        exit 0
    fi

    CONTEXT="SESSION CONTEXT [${PROJECT} | ${SESSION_ID}]:"

    # --- Session notes status ---
    NOTES_FILE="$NOTES_DIR/${PROJECT}.md"
    if [ -f "$NOTES_FILE" ]; then
        # Find last session header for this session_id
        LAST_HEADER=$(grep "ID: ${SESSION_ID}" "$NOTES_FILE" 2>/dev/null | tail -1 || true)
        if [ -n "$LAST_HEADER" ]; then
            # Extract task from the line after the header
            TASK_LINE=$(grep -A2 "ID: ${SESSION_ID}" "$NOTES_FILE" 2>/dev/null | grep "^## Task:" | tail -1 || true)
            TASK="${TASK_LINE#*## Task: }"
            [ -z "$TASK" ] && TASK="(unknown)"

            # Extract status
            STATUS="IN PROGRESS"
            if printf '%s' "$LAST_HEADER" | grep -q "COMPLETE"; then
                STATUS="COMPLETE"
            fi

            # Extract timestamp and compute age
            HEADER_TS=$(printf '%s' "$LAST_HEADER" | sed -n 's/.*Session: \([^ ]*\).*/\1/p')
            AGE=""
            if [ -n "$HEADER_TS" ]; then
                HEADER_EPOCH=$(date -d "$HEADER_TS" +%s 2>/dev/null || true)
                NOW_EPOCH=$(date +%s)
                if [ -n "$HEADER_EPOCH" ]; then
                    DIFF_MIN=$(( (NOW_EPOCH - HEADER_EPOCH) / 60 ))
                    AGE="Session notes last updated: ${DIFF_MIN} minutes ago"
                fi
            fi

            CONTEXT="${CONTEXT}
  Task: ${TASK}
  Status: ${STATUS}"
            [ -n "$AGE" ] && CONTEXT="${CONTEXT}
  ${AGE}"
        else
            CONTEXT="${CONTEXT}
  No session notes for this session yet."
        fi
    else
        CONTEXT="${CONTEXT}
  No session notes file found for project '${PROJECT}'."
    fi

    # --- Recent WAL actions ---
    if [ -f "$WAL_FILE" ]; then
        RECENT=$(grep "$SESSION_ID" "$WAL_FILE" 2>/dev/null | tail -10 | "$JQ" -r '"    [" + .ts[11:19] + "] " + .phase + ": " + (.summary // .tool)' 2>/dev/null || true)
        if [ -n "$RECENT" ]; then
            CONTEXT="${CONTEXT}

  Recent actions (last 10):
${RECENT}"
        fi
    fi

    CONTEXT="${CONTEXT}

  Full session notes: claude_docs/session_notes/${PROJECT}.md"

    "$JQ" -nc --arg ctx "$CONTEXT" '{additionalContext: $ctx}'
}

_do_context 2>/dev/null || true
exit 0
```

Make it executable:

```bash
chmod +x hooks/wal-context
```

**Step 2: Test the hook**

Create a mock session registry and test:

```bash
mkdir -p /tmp/test-context/claude_docs/session_notes
echo '{"session_id":"abc123","project":"data_catalogue","project_path":"./projects/data_catalogue","started":"2026-03-06T15:00:00Z","cwd":"/tmp/test-context"}' > /tmp/test-context/claude_docs/session_registry.jsonl
echo '{}' > /tmp/test-context/.rawgentic_workspace.json

# Test with registered session
echo '{"session_id":"abc123","cwd":"/tmp/test-context"}' | hooks/wal-context
# Expected: JSON with additionalContext containing "SESSION CONTEXT [data_catalogue | abc123]"

# Test with unregistered session
echo '{"session_id":"unknown-session","cwd":"/tmp/test-context"}' | hooks/wal-context
# Expected: JSON with "Not registered" reminder

# Clean up
rm -rf /tmp/test-context
```

**Step 3: Commit**

```bash
git add hooks/wal-context
git commit -m "feat: add wal-context hook (UserPromptSubmit)

Injects lightweight session context on every prompt:
- Project name and session_id from registry
- Current task and status from session notes
- Notes freshness (minutes since last update)
- Last 10 WAL actions
Survives context compaction. Error-tolerant (always exits 0).

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Task 5: Create `wal-stop` Hook (Stop)

Forces session notes update before Claude stops. Includes rawgentic guard clause, retry limit, and timestamp-based freshness check.

**Files:**
- Create: `hooks/wal-stop`

**Step 1: Write the wal-stop hook**

Create `hooks/wal-stop`:

```bash
#!/usr/bin/env bash
# Stop hook — Forces session notes update before Claude stops.
# Guard: exits immediately for non-rawgentic sessions.
# Retry limit: max 2 attempts, then allows stop.
# Freshness: checks timestamp in session notes header (5-minute threshold).
# Design: docs/plans/2026-03-06-wal-session-notes-design.md

_do_stop() {
    set -euo pipefail

    INPUT=$(cat)

    JQ="${HOME}/.local/bin/jq"
    [ -x "$JQ" ] || JQ="jq"
    command -v "$JQ" &>/dev/null || exit 0

    SESSION_ID=$(printf '%s' "$INPUT" | "$JQ" -r '.session_id // "unknown"')
    CWD=$(printf '%s' "$INPUT" | "$JQ" -r '.cwd // "."')

    [ "$SESSION_ID" = "unknown" ] && exit 0

    # --- Guard clause: not a rawgentic session ---
    if [ ! -f "$CWD/.rawgentic_workspace.json" ] && [ ! -f "$CWD/../.rawgentic_workspace.json" ]; then
        exit 0
    fi

    # --- Retry limit: max 2 attempts ---
    ATTEMPT_FILE="/tmp/wal-stop-${SESSION_ID}"
    ATTEMPT=0
    if [ -f "$ATTEMPT_FILE" ]; then
        ATTEMPT=$(cat "$ATTEMPT_FILE" 2>/dev/null || echo "0")
    fi

    if [ "$ATTEMPT" -ge 2 ]; then
        # Max retries exceeded — allow stop, log warning
        rm -f "$ATTEMPT_FILE"
        WAL_FILE="$CWD/claude_docs/wal.jsonl"
        if [ -f "$WAL_FILE" ] && command -v "$JQ" &>/dev/null; then
            "$JQ" -nc \
                --arg ts "$(date -u +"%Y-%m-%dT%H:%M:%SZ")" \
                --arg session "$SESSION_ID" \
                '{ts:$ts, phase:"WARN", session:$session, summary:"Stop hook: session notes not updated after 2 attempts"}' \
                >> "$WAL_FILE" 2>/dev/null || true
        fi
        # Return continue: true
        printf '%s\n' '{"hookSpecificOutput":{"hookEventName":"Stop","continue":true}}'
        exit 0
    fi

    # --- Lookup project from registry ---
    REGISTRY_FILE="$CWD/claude_docs/session_registry.jsonl"
    PROJECT=""
    if [ -f "$REGISTRY_FILE" ]; then
        REG_LINE=$(grep "$SESSION_ID" "$REGISTRY_FILE" 2>/dev/null | tail -1 || true)
        if [ -n "$REG_LINE" ]; then
            PROJECT=$(printf '%s' "$REG_LINE" | "$JQ" -r '.project // ""')
        fi
    fi

    # --- Gate 1: Registry check ---
    if [ -z "$PROJECT" ]; then
        # Increment attempt counter
        echo "$(( ATTEMPT + 1 ))" > "$ATTEMPT_FILE"
        "$JQ" -nc --arg reason "Before finishing, register this session and update session notes.

Use /rawgentic:switch <project> to register, then APPEND a session summary to claude_docs/session_notes/<project>.md.

RULES:
- APPEND ONLY. Do NOT modify, rewrite, or remove existing content.
- Start with a --- separator, then:
  # Session: $(date -u +"%Y-%m-%dT%H:%M:%SZ") | ID: ${SESSION_ID} | Status: COMPLETE
  ## Task: <what you worked on>
  ## Project: <project name>
- Include: changes made, verification results, next steps.
- If the file does not exist, create it with a header line first.
Also append a registration line to claude_docs/session_registry.jsonl." \
            '{hookSpecificOutput:{hookEventName:"Stop",continue:false,reason:$reason}}'
        exit 0
    fi

    # --- Gate 2: Notes timestamp check ---
    NOTES_FILE="$CWD/claude_docs/session_notes/${PROJECT}.md"
    NEEDS_UPDATE=true

    if [ -f "$NOTES_FILE" ]; then
        # Find last session header for this session_id
        LAST_HEADER=$(grep "ID: ${SESSION_ID}" "$NOTES_FILE" 2>/dev/null | tail -1 || true)
        if [ -n "$LAST_HEADER" ]; then
            # Extract ISO timestamp
            HEADER_TS=$(printf '%s' "$LAST_HEADER" | sed -n 's/.*Session: \([^ ]*\).*/\1/p')
            if [ -n "$HEADER_TS" ]; then
                HEADER_EPOCH=$(date -d "$HEADER_TS" +%s 2>/dev/null || echo "0")
                NOW_EPOCH=$(date +%s)
                DIFF_SEC=$(( NOW_EPOCH - HEADER_EPOCH ))
                # 5-minute threshold = 300 seconds
                if [ "$DIFF_SEC" -lt 300 ]; then
                    NEEDS_UPDATE=false
                fi
            fi
        fi
    fi

    if [ "$NEEDS_UPDATE" = true ]; then
        # Increment attempt counter
        echo "$(( ATTEMPT + 1 ))" > "$ATTEMPT_FILE"
        "$JQ" -nc --arg reason "Before finishing, APPEND a new session summary to claude_docs/session_notes/${PROJECT}.md

RULES:
- APPEND ONLY. Do NOT modify, rewrite, or remove any existing content in the file.
- Start with a --- separator, then the structured header:
  # Session: $(date -u +"%Y-%m-%dT%H:%M:%SZ") | ID: ${SESSION_ID} | Status: COMPLETE
  ## Task: <what you worked on>
  ## Project: ${PROJECT}
- After the header, include: changes made, verification results, and next steps.
- If the file does not exist, create it with:
  # Session Notes -- ${PROJECT}
  Then append your session block below.

Also ensure you are registered in claude_docs/session_registry.jsonl." \
            '{hookSpecificOutput:{hookEventName:"Stop",continue:false,reason:$reason}}'
        exit 0
    fi

    # Notes are fresh — allow stop
    rm -f "$ATTEMPT_FILE"
    printf '%s\n' '{"hookSpecificOutput":{"hookEventName":"Stop","continue":true}}'
}

_do_stop 2>/dev/null || true
exit 0
```

Make it executable:

```bash
chmod +x hooks/wal-stop
```

**Step 2: Test the hook**

```bash
# Setup mock environment
mkdir -p /tmp/test-stop/claude_docs/session_notes
echo '{}' > /tmp/test-stop/.rawgentic_workspace.json
echo '{"session_id":"abc123","project":"myproj","project_path":"./projects/myproj","started":"2026-03-06T15:00:00Z","cwd":"/tmp/test-stop"}' > /tmp/test-stop/claude_docs/session_registry.jsonl

# Test 1: No notes file — should block
echo '{"session_id":"abc123","cwd":"/tmp/test-stop"}' | hooks/wal-stop
# Expected: continue: false, reason contains "APPEND a new session summary"

# Test 2: Fresh notes — should allow
NOW=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
printf '# Session Notes -- myproj\n\n---\n\n# Session: %s | ID: abc123 | Status: IN PROGRESS\n## Task: Testing\n## Project: myproj\n' "$NOW" > /tmp/test-stop/claude_docs/session_notes/myproj.md
echo '{"session_id":"abc123","cwd":"/tmp/test-stop"}' | hooks/wal-stop
# Expected: continue: true

# Test 3: Non-rawgentic session — should allow immediately
rm /tmp/test-stop/.rawgentic_workspace.json
echo '{"session_id":"abc123","cwd":"/tmp/test-stop"}' | hooks/wal-stop
# Expected: exit 0 (no output or continue: true)

# Test 4: Retry limit — should allow after 2 attempts
echo '{}' > /tmp/test-stop/.rawgentic_workspace.json
rm /tmp/test-stop/claude_docs/session_notes/myproj.md
echo '{"session_id":"retry-test","cwd":"/tmp/test-stop"}' | hooks/wal-stop  # attempt 1
echo '{"session_id":"retry-test","cwd":"/tmp/test-stop"}' | hooks/wal-stop  # attempt 2
echo '{"session_id":"retry-test","cwd":"/tmp/test-stop"}' | hooks/wal-stop  # attempt 3 — should be continue: true
# Expected: first two return continue: false, third returns continue: true

# Clean up
rm -rf /tmp/test-stop /tmp/wal-stop-*
```

**Step 3: Commit**

```bash
git add hooks/wal-stop
git commit -m "feat: add wal-stop hook (Stop)

Forces session notes update before Claude stops:
- Guard clause: skips non-rawgentic sessions
- Retry limit: max 2 attempts, then allows stop with WAL warning
- Timestamp-based freshness: 5-minute threshold using ISO timestamp
  from session notes header (not file mtime)
- APPEND-ONLY enforcement via prompt language

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Task 6: Update hooks.json with All Hook Registrations

Register all hooks in the plugin's hooks.json, matching the design doc.

**Files:**
- Modify: `hooks/hooks.json`

**Step 1: Write the complete hooks.json**

Replace `hooks/hooks.json` with the full registration:

```json
{
  "hooks": {
    "SessionStart": [
      {
        "matcher": "startup|resume|clear|compact",
        "hooks": [
          {
            "type": "command",
            "command": "${CLAUDE_PLUGIN_ROOT}/hooks/session-start",
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
            "command": "${CLAUDE_PLUGIN_ROOT}/hooks/wal-guard",
            "timeout": 5
          }
        ]
      },
      {
        "matcher": "Bash|Edit|Write|NotebookEdit|Task",
        "hooks": [
          {
            "type": "command",
            "command": "${CLAUDE_PLUGIN_ROOT}/hooks/wal-pre",
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
            "command": "${CLAUDE_PLUGIN_ROOT}/hooks/wal-post",
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
            "command": "${CLAUDE_PLUGIN_ROOT}/hooks/wal-post-fail",
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
            "command": "${CLAUDE_PLUGIN_ROOT}/hooks/wal-context",
            "timeout": 3
          }
        ]
      }
    ],
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "${CLAUDE_PLUGIN_ROOT}/hooks/wal-stop",
            "timeout": 10
          }
        ]
      }
    ]
  }
}
```

Note: UserPromptSubmit timeout set to 3s (hot path, minimize latency).

**Step 2: Validate JSON**

```bash
python3 -m json.tool hooks/hooks.json > /dev/null
# Expected: no error
```

**Step 3: Commit**

```bash
git add hooks/hooks.json
git commit -m "feat: register all WAL hooks in plugin hooks.json

Adds PreToolUse (guard + pre), PostToolUse, PostToolUseFailure,
UserPromptSubmit (wal-context, 3s timeout), and Stop (wal-stop)
to the existing SessionStart registration.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Task 7: Atomic Hook Migration (Remove from settings.json)

Remove all WAL hook registrations from `~/.claude/settings.json` now that they're registered in the plugin. This must be done atomically with Task 6 to prevent double-firing.

**Important:** This task modifies a file OUTSIDE the rawgentic repo. Do not commit this to the rawgentic repo — it's a local environment change.

**Files:**
- Modify: `~/.claude/settings.json`

**Step 1: Remove all hooks from settings.json**

Edit `~/.claude/settings.json` to remove the entire `hooks` section. Keep only `permissions` and `enabledPlugins`:

The `hooks` key at line 13 through line 93 should be completely removed. The resulting file should look like:

```json
{
  "permissions": {
    "allow": [
      "Read",
      "Edit",
      "Read(~/.claude/**)",
      "Edit(~/.claude/**)",
      "Read(claude_docs/**)",
      "Write(claude_docs/**)",
      "Edit(claude_docs/**)"
    ]
  },
  "enabledPlugins": {
    ...existing plugins...
  },
  "effortLevel": "high"
}
```

**Step 2: Verify hooks fire from plugin only**

Start a new Claude Code session. Check that:
1. Session-start hook fires (rawgentic context appears)
2. Run a Bash command — check WAL gets INTENT/DONE entries
3. Guard blocks `rm -rf /tmp/test` (if tested)

**Step 3: Delete old hook scripts (keep wal-guard as global fallback)**

```bash
rm ~/.claude/hooks/wal-pre.sh
rm ~/.claude/hooks/wal-post.sh
rm ~/.claude/hooks/wal-post-fail.sh
rm ~/.claude/hooks/wal-session-start.sh
rm ~/.claude/hooks/session-notes-reminder.sh
# Keep wal-guard.sh and wal-lib.sh as global fallback
```

**Step 4: Commit the rawgentic-side changes (if any pending)**

This step has no rawgentic repo changes — it's environment-only. If there are pending changes:

```bash
git add -A
git commit -m "chore: clean up after hook migration

Hooks now registered via plugin hooks.json. Old ~/.claude/hooks/
scripts removed (except wal-guard.sh global fallback).
session-notes-reminder.sh removed (replaced by Stop hook).

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Task 8: Create Session Notes Directory Structure

Create the `session_notes/` directory and archive subdirectory in the workspace's `claude_docs/`.

**Files:**
- Create: `claude_docs/session_notes/.gitkeep` (in the workspace, not plugin)

**Step 1: Create directories**

This runs in the WORKSPACE directory (`~/claude/`), not the rawgentic plugin directory:

```bash
mkdir -p ~/claude/claude_docs/session_notes/archive
touch ~/claude/claude_docs/session_notes/.gitkeep
touch ~/claude/claude_docs/session_notes/archive/.gitkeep
```

**Step 2: Commit in rawgentic repo (no changes — this is workspace-level)**

No rawgentic commit needed. The directories are in the workspace, not the plugin.

---

## Task 9: Update Config-Loading Block in All 9 Workflow Skills

Replace the config-loading block in all 9 workflow skills with the 3-level fallback chain.

**Files:**
- Modify: `skills/implement-feature/SKILL.md`
- Modify: `skills/fix-bug/SKILL.md`
- Modify: `skills/create-issue/SKILL.md`
- Modify: `skills/refactor/SKILL.md`
- Modify: `skills/update-docs/SKILL.md`
- Modify: `skills/update-deps/SKILL.md`
- Modify: `skills/security-audit/SKILL.md`
- Modify: `skills/optimize-perf/SKILL.md`
- Modify: `skills/incident/SKILL.md`

**Step 1: Identify the exact old text**

All 9 skills have this identical `<config-loading>` block (verbatim):

```
<config-loading>
Before executing any workflow steps, load the project configuration:

1. Read `.rawgentic_workspace.json` from the Claude root directory.
   - Missing -> STOP. Tell user: "No rawgentic workspace found. Run /rawgentic:new-project."
   - Malformed JSON -> STOP. Tell user: "Workspace file is corrupted. Run /rawgentic:new-project to regenerate, or fix manually."
   - Extract the active project entry (active == true).
   - **Path resolution:** The `activeProject.path` may be relative (e.g., `./projects/my-app`). Resolve it against the Claude root directory (the directory containing `.rawgentic_workspace.json`) to get the absolute path for file operations.
```

**Step 2: Write the replacement config-loading block**

The new block adds a 3-level fallback for determining the active project:

```
<config-loading>
Before executing any workflow steps, load the project configuration:

1. Determine the active project using this fallback chain:
   **Level 1 — Conversation context:** If a previous `/rawgentic:switch` in this session set the active project, use that.
   **Level 2 — Session registry:** Read `claude_docs/session_registry.jsonl`. Grep for your session_id. If found, use the project from the most recent matching line.
   **Level 3 — Workspace default:** Read `.rawgentic_workspace.json` from the Claude root directory. Extract the active project entry (active == true).

   At any level:
   - `.rawgentic_workspace.json` missing -> STOP. Tell user: "No rawgentic workspace found. Run /rawgentic:new-project."
   - `.rawgentic_workspace.json` malformed -> STOP. Tell user: "Workspace file is corrupted. Run /rawgentic:new-project to regenerate, or fix manually."
   - No active project found at any level -> STOP. Tell user: "No active project. Run /rawgentic:switch to select one."
   - **Path resolution:** The `activeProject.path` may be relative (e.g., `./projects/my-app`). Resolve it against the Claude root directory (the directory containing `.rawgentic_workspace.json`) to get the absolute path for file operations.
```

**Step 3: Apply the replacement to all 9 skills**

For each of the 9 skill files, use the Edit tool to replace the old step 1 text with the new fallback chain. The old text starts with:

```
1. Read `.rawgentic_workspace.json` from the Claude root directory.
   - Missing -> STOP. Tell user: "No rawgentic workspace found. Run /rawgentic:new-project."
   - Malformed JSON -> STOP. Tell user: "Workspace file is corrupted. Run /rawgentic:new-project to regenerate, or fix manually."
   - Extract the active project entry (active == true).
   - **Path resolution:** The `activeProject.path` may be relative (e.g., `./projects/my-app`). Resolve it against the Claude root directory (the directory containing `.rawgentic_workspace.json`) to get the absolute path for file operations.
```

And the new text is:

```
1. Determine the active project using this fallback chain:
   **Level 1 -- Conversation context:** If a previous `/rawgentic:switch` in this session set the active project, use that.
   **Level 2 -- Session registry:** Read `claude_docs/session_registry.jsonl`. Grep for your session_id. If found, use the project from the most recent matching line.
   **Level 3 -- Workspace default:** Read `.rawgentic_workspace.json` from the Claude root directory. Extract the active project entry (active == true).

   At any level:
   - `.rawgentic_workspace.json` missing -> STOP. Tell user: "No rawgentic workspace found. Run /rawgentic:new-project."
   - `.rawgentic_workspace.json` malformed -> STOP. Tell user: "Workspace file is corrupted. Run /rawgentic:new-project to regenerate, or fix manually."
   - No active project found at any level -> STOP. Tell user: "No active project. Run /rawgentic:switch to select one."
   - **Path resolution:** The `activeProject.path` may be relative (e.g., `./projects/my-app`). Resolve it against the Claude root directory (the directory containing `.rawgentic_workspace.json`) to get the absolute path for file operations.
```

Apply this replacement to all 9 files using `replace_all: false` (each file has exactly one occurrence).

**Step 4: Verify the replacement**

```bash
grep -c "Session registry" skills/*/SKILL.md
# Expected: 9 files with count 1 each
grep -c "active == true" skills/*/SKILL.md
# Expected: 9 files with count 1 each (inside the Level 3 line)
```

**Step 5: Commit**

```bash
git add skills/*/SKILL.md
git commit -m "feat: add 3-level config-loading fallback to all 9 workflow skills

Workflow skills now determine active project via:
1. Conversation context (set by /rawgentic:switch)
2. Session registry (survives context compaction)
3. Workspace JSON default (original behavior)

Applied to: implement-feature, fix-bug, create-issue, refactor,
update-docs, update-deps, security-audit, optimize-perf, incident.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Task 10: Update Setup Skill Config-Loading

The setup skill has a different config-loading pattern (Step 1, not a `<config-loading>` block). Update it with the same fallback chain.

**Files:**
- Modify: `skills/setup/SKILL.md`

**Step 1: Update Step 1 in setup skill**

The current Step 1 (lines 23-33) reads `.rawgentic_workspace.json` and extracts the active project. Add the fallback chain before the workspace read:

Replace the current Step 1 text:

```
## Step 1: Verify Context

Read `.rawgentic_workspace.json` from the Claude root directory (the directory Claude was launched from).

- **File missing** -> STOP. Tell the user: "No rawgentic workspace found. Run `/rawgentic:new-project` first to register a project."
- **Malformed JSON** -> STOP. Tell the user: "Workspace file is corrupted. Run `/rawgentic:new-project` to regenerate, or fix `.rawgentic_workspace.json` manually."
- **No active project** (no entry with `active: true`) -> STOP. Tell the user: "No active project. Run `/rawgentic:switch` to select one."

Extract the active project's `name` and `path`. Confirm to the user:
```

With:

```
## Step 1: Verify Context

Determine the active project using this fallback chain:
1. **Conversation context:** If a previous `/rawgentic:switch` in this session set the active project, use that.
2. **Session registry:** Read `claude_docs/session_registry.jsonl`. Grep for your session_id. If found, use the project from the most recent matching line.
3. **Workspace default:** Read `.rawgentic_workspace.json` from the Claude root directory (the directory Claude was launched from). Extract the active project entry (active == true).

At any level:
- `.rawgentic_workspace.json` **missing** -> STOP. Tell the user: "No rawgentic workspace found. Run `/rawgentic:new-project` first to register a project."
- `.rawgentic_workspace.json` **malformed** -> STOP. Tell the user: "Workspace file is corrupted. Run `/rawgentic:new-project` to regenerate, or fix `.rawgentic_workspace.json` manually."
- **No active project** found at any level -> STOP. Tell the user: "No active project. Run `/rawgentic:switch` to select one."

Extract the active project's `name` and `path`. Confirm to the user:
```

**Step 2: Verify**

```bash
grep "Session registry" skills/setup/SKILL.md
# Expected: one match
```

**Step 3: Commit**

```bash
git add skills/setup/SKILL.md
git commit -m "feat: add 3-level config-loading fallback to setup skill

Setup skill now uses the same fallback chain as workflow skills:
conversation context -> session registry -> workspace JSON default.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Task 11: Update Switch Skill to Write Session Registry

Add session registry write to `/rawgentic:switch` after the workspace file update.

**Files:**
- Modify: `skills/switch/SKILL.md`

**Step 1: Add registry write to Step 4**

After the current Step 4 content (lines 62-71), add a new sub-step. Insert after the `lastUsed` update (item 3) and before the write-back (item 4):

Add this as item 4 (renumber old 4 to 5):

Find the text:

```
3. Update the target's `lastUsed` to the current ISO 8601 timestamp.
4. Write the updated workspace file back (full read-modify-write).
```

Replace with:

```
3. Update the target's `lastUsed` to the current ISO 8601 timestamp.
4. Write the updated workspace file back (full read-modify-write).
5. **Register in session registry:** Append a line to `claude_docs/session_registry.jsonl`:
   ```json
   {"session_id":"<your session_id>","project":"<target project name>","project_path":"<target project path>","started":"<current ISO 8601 timestamp>","cwd":"<workspace root>"}
   ```
   Create the file and `claude_docs/session_notes/` directory if they don't exist.
```

**Step 2: Verify**

```bash
grep "session_registry" skills/switch/SKILL.md
# Expected: one or more matches
```

**Step 3: Commit**

```bash
git add skills/switch/SKILL.md
git commit -m "feat: add session registry write to switch skill

/rawgentic:switch now appends to claude_docs/session_registry.jsonl
when switching projects. Creates the file and session_notes directory
if they don't exist.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Task 12: Update New-Project Skill to Write Session Registry

Add session registry write to `/rawgentic:new-project` after project registration.

**Files:**
- Modify: `skills/new-project/SKILL.md`

**Step 1: Add registry write to Step 5**

After the current Step 5 content (registering the project in workspace JSON), add a registry write sub-step.

Find the text:

```
3. Write the updated workspace file back (full read-modify-write — never patch in place).

Confirm to the user:
> Registered **<name>** as the active project.
```

Replace with:

```
3. Write the updated workspace file back (full read-modify-write -- never patch in place).
4. **Register in session registry:** Create `claude_docs/session_notes/` directory if it doesn't exist. Append a line to `claude_docs/session_registry.jsonl`:
   ```json
   {"session_id":"<your session_id>","project":"<name>","project_path":"./<relative-path>","started":"<current ISO 8601 timestamp>","cwd":"<WORKSPACE_ROOT>"}
   ```

Confirm to the user:
> Registered **<name>** as the active project.
```

**Step 2: Verify**

```bash
grep "session_registry" skills/new-project/SKILL.md
# Expected: one or more matches
```

**Step 3: Commit**

```bash
git add skills/new-project/SKILL.md
git commit -m "feat: add session registry write to new-project skill

/rawgentic:new-project now appends to claude_docs/session_registry.jsonl
when registering a new project. Creates session_notes directory
if it doesn't exist.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Task 13: Update Global CLAUDE.md

Replace the WAL Protocol section in `~/.claude/CLAUDE.md` with the updated version that references session registry and per-project notes.

**Files:**
- Modify: `~/.claude/CLAUDE.md`

**Step 1: Replace the WAL Protocol section**

Replace everything from `## WAL Protocol (Write-Ahead Logging)` through the end of `### Session Start` section (before `---`) with:

```markdown
## WAL Protocol (Write-Ahead Logging)

**IMPORTANT: This protocol is ALWAYS active. Hooks enforce it automatically.**

Every mutation action (Bash, Edit, Write, NotebookEdit, Task) is logged to `claude_docs/wal.jsonl`
before and after execution via hooks. You do not need to manually write to the WAL -- the hooks handle it.

### Session Registration
Session registration is handled automatically by `/rawgentic:switch` and `/rawgentic:new-project`.
If the UserPromptSubmit hook reports you are unregistered, use one of these skills to register.
The Stop hook will also catch unregistered sessions as a fallback.

Registry format (one JSONL line per session):
  {"session_id":"<id>","project":"<name>","project_path":"<path>","started":"<ISO datetime>","cwd":"<cwd>"}

### Session Notes
- Notes are stored per-project at `claude_docs/session_notes/{project}.md`
- **APPEND ONLY** -- never modify or remove existing content
- Use the structured header format:
  ---
  # Session: {ISO 8601 datetime} | ID: {session_id} | Status: IN PROGRESS
  ## Task: <task description>
  ## Project: <project name>
- Append multiple IN PROGRESS blocks throughout your session (each with a fresh timestamp)
- Append a final COMPLETE block when done
- The Stop hook will block you from finishing if notes aren't recent (within 5 minutes)

### WAL Log Format
The WAL at `claude_docs/wal.jsonl` contains one JSON object per line:
  INTENT -> logged before tool execution
  DONE   -> logged after successful execution
  FAIL   -> logged after failed execution
An INTENT without a matching DONE/FAIL means the operation was interrupted.

### Session Start
1. The hooks automatically check for incomplete WAL operations and report them
2. If incomplete operations are reported, assess whether recovery action is needed
3. The UserPromptSubmit hook injects your session context on every prompt --
   this survives context compaction
```

**Step 2: Remove the old "Session Notes (Soft)" section**

The old section referencing `session_notes.md` should be removed — it's replaced by the new per-project notes section above.

**Step 3: Verify**

```bash
grep "session_registry" ~/.claude/CLAUDE.md
# Expected: one or more matches
grep "session_notes.md" ~/.claude/CLAUDE.md
# Expected: no matches (old reference removed)
```

**Step 4: No git commit** — this file is in `~/.claude/`, not in the rawgentic repo.

---

## Task 14: Update Workspace CLAUDE.md

Add session notes section to `~/claude/CLAUDE.md`.

**Files:**
- Modify: `~/claude/CLAUDE.md`

**Step 1: Add session notes section**

After the `## Workspace Structure` section, add:

```markdown
## Session Notes
- Registry: ./claude_docs/session_registry.jsonl
- Notes: ./claude_docs/session_notes/{project}.md
- Archive: ./claude_docs/session_notes/archive/
- Projects: see .rawgentic_workspace.json for valid project names and paths
```

**Step 2: Verify**

```bash
grep "session_registry" ~/claude/CLAUDE.md
# Expected: one match
```

**Step 3: No git commit** — this file is in `~/claude/`, not in the rawgentic repo.

---

## Task 15: Integration Smoke Test

End-to-end verification that all components work together.

**Step 1: Start a fresh Claude Code session in ~/claude/**

Verify:
- [ ] Session-start hook fires with rawgentic workspace context
- [ ] No double hook firing (WAL entries don't have duplicates)

**Step 2: Run a command and check WAL**

```bash
tail -5 ~/claude/claude_docs/wal.jsonl
```

Verify:
- [ ] INTENT/DONE pairs appear for the command
- [ ] No duplicate entries (idempotency)

**Step 3: Check UserPromptSubmit context injection**

Type any prompt. The hook should inject session context. If unregistered, it should say "Not registered."

**Step 4: Test /rawgentic:switch**

Run `/rawgentic:switch data_catalogue`. Verify:
- [ ] Session registry entry created in `claude_docs/session_registry.jsonl`
- [ ] `session_notes/` directory created
- [ ] Next prompt shows "SESSION CONTEXT [data_catalogue | ...]"

**Step 5: Test Stop hook**

Try to stop the session (Ctrl+C or /quit). Verify:
- [ ] Stop hook blocks with "APPEND a new session summary" prompt
- [ ] After writing notes, stop is allowed

**Step 6: Test guard**

```bash
echo "test" | rm -rf /tmp/nonexistent
```

Verify:
- [ ] Guard blocks with "Destructive rm command" message

**Step 7: Document results**

```bash
git add -A
git commit -m "feat: WAL & session notes integration complete

All hooks migrated to rawgentic plugin. New hooks:
- wal-context (UserPromptSubmit): context compaction protection
- wal-stop (Stop): session notes enforcement
Session registry and per-project notes operational.
Config-loading fallback chain in all 9 workflow skills + setup.
CLAUDE.md files updated at global and workspace levels.

Verification: [document test results]

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Summary

| Task | Description | Files Changed | Commit |
|------|-------------|---------------|--------|
| 1 | Verify hook payloads | hooks/test-payload-dump (temp) | chore: verify payloads |
| 2 | Copy WAL scripts to plugin | 5 new files in hooks/ | feat: copy WAL hooks |
| 3 | Merge session-start hooks | hooks/session-start | feat: merge session-start |
| 4 | Create wal-context hook | hooks/wal-context | feat: add wal-context |
| 5 | Create wal-stop hook | hooks/wal-stop | feat: add wal-stop |
| 6 | Update hooks.json | hooks/hooks.json | feat: register all hooks |
| 7 | Remove hooks from settings.json | ~/.claude/settings.json | chore: clean up migration |
| 8 | Create session_notes directories | ~/claude/claude_docs/ | (workspace-level, no commit) |
| 9 | Update 9 workflow skills | 9x skills/*/SKILL.md | feat: 3-level config-loading |
| 10 | Update setup skill | skills/setup/SKILL.md | feat: setup fallback chain |
| 11 | Update switch skill | skills/switch/SKILL.md | feat: switch registry write |
| 12 | Update new-project skill | skills/new-project/SKILL.md | feat: new-project registry |
| 13 | Update global CLAUDE.md | ~/.claude/CLAUDE.md | (not in repo) |
| 14 | Update workspace CLAUDE.md | ~/claude/CLAUDE.md | (not in repo) |
| 15 | Integration smoke test | — | feat: integration complete |
