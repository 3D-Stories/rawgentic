#!/bin/bash
# WAL Shared Library — Common functions for all WAL hooks.
# Source this file from hook scripts: source "$SCRIPT_DIR/wal-lib.sh"

# --- jq resolution ---
# Finds jq binary, preferring local install. Sets WAL_JQ.
_resolve_jq() {
  WAL_JQ="${HOME}/.local/bin/jq"
  if [ ! -x "$WAL_JQ" ]; then
    WAL_JQ="jq"
  fi
}
_resolve_jq

# --- Input parsing ---
# Reads stdin and extracts common fields into variables.
# Sets: WAL_INPUT, WAL_TOOL_NAME, WAL_SESSION_ID, WAL_TOOL_USE_ID, WAL_CWD
wal_parse_input() {
  WAL_INPUT=$(cat)
  WAL_TOOL_NAME=$(printf '%s' "$WAL_INPUT" | "$WAL_JQ" -r '.tool_name // "unknown"')
  WAL_SESSION_ID=$(printf '%s' "$WAL_INPUT" | "$WAL_JQ" -r '.session_id // "unknown"')
  WAL_TOOL_USE_ID=$(printf '%s' "$WAL_INPUT" | "$WAL_JQ" -r '.tool_use_id // "unknown"')
  WAL_CWD=$(printf '%s' "$WAL_INPUT" | "$WAL_JQ" -r '.cwd // "."')
}

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

# --- Summary extraction ---
# Builds a short human-readable summary based on tool type.
# Requires: WAL_INPUT, WAL_TOOL_NAME to be set.
# Sets: WAL_SUMMARY
wal_extract_summary() {
  case "$WAL_TOOL_NAME" in
    Bash)
      WAL_SUMMARY=$(printf '%s' "$WAL_INPUT" | "$WAL_JQ" -r '.tool_input.command // ""' | head -c 200)
      ;;
    Edit)
      local file
      file=$(printf '%s' "$WAL_INPUT" | "$WAL_JQ" -r '.tool_input.file_path // ""')
      WAL_SUMMARY="edit $file"
      ;;
    Write)
      local file
      file=$(printf '%s' "$WAL_INPUT" | "$WAL_JQ" -r '.tool_input.file_path // ""')
      WAL_SUMMARY="write $file"
      ;;
    NotebookEdit)
      local file
      file=$(printf '%s' "$WAL_INPUT" | "$WAL_JQ" -r '.tool_input.notebook_path // ""')
      WAL_SUMMARY="notebook-edit $file"
      ;;
    Task)
      local desc
      desc=$(printf '%s' "$WAL_INPUT" | "$WAL_JQ" -r '.tool_input.description // ""')
      WAL_SUMMARY="task: $desc"
      ;;
    *)
      WAL_SUMMARY="$WAL_TOOL_NAME"
      ;;
  esac
}

# --- Phase append ---
# Appends a JSON line to the WAL file.
# Usage: wal_append_phase "INTENT" [extra jq --arg flags...]
#   For INTENT: wal_append_phase "INTENT" --arg summary "$WAL_SUMMARY" --arg cwd "$WAL_CWD"
#   For DONE/FAIL: wal_append_phase "DONE"
# Requires: WAL_FILE, WAL_SESSION_ID, WAL_TOOL_NAME, WAL_TOOL_USE_ID to be set.
wal_append_phase() {
  local phase="$1"
  shift
  local ts
  ts=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

  if [ "$phase" = "INTENT" ]; then
    "$WAL_JQ" -nc \
      --arg ts "$ts" \
      --arg session "$WAL_SESSION_ID" \
      --arg tool "$WAL_TOOL_NAME" \
      --arg tool_use_id "$WAL_TOOL_USE_ID" \
      --arg phase "$phase" \
      "$@" \
      '{ts:$ts, phase:$phase, session:$session, tool:$tool, tool_use_id:$tool_use_id, summary:$summary, cwd:$cwd}' \
      >> "$WAL_FILE"
  else
    "$WAL_JQ" -nc \
      --arg ts "$ts" \
      --arg session "$WAL_SESSION_ID" \
      --arg tool "$WAL_TOOL_NAME" \
      --arg tool_use_id "$WAL_TOOL_USE_ID" \
      --arg phase "$phase" \
      '{ts:$ts, phase:$phase, session:$session, tool:$tool, tool_use_id:$tool_use_id}' \
      >> "$WAL_FILE"
  fi
}

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
