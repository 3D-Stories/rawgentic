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
# wal_read_stdin: Reads stdin into WAL_RAW_INPUT. Safe to call before jq check.
# Sets: WAL_RAW_INPUT
wal_read_stdin() {
  WAL_RAW_INPUT=$(cat)
}

# wal_parse_fields: Extracts common fields from WAL_RAW_INPUT using jq.
# Requires: WAL_RAW_INPUT to be set (via wal_read_stdin).
# Sets: WAL_INPUT, WAL_TOOL_NAME, WAL_SESSION_ID, WAL_TOOL_USE_ID, WAL_CWD
wal_parse_fields() {
  WAL_INPUT="$WAL_RAW_INPUT"
  WAL_TOOL_NAME=$(printf '%s' "$WAL_RAW_INPUT" | "$WAL_JQ" -r '.tool_name // "unknown"')
  WAL_SESSION_ID=$(printf '%s' "$WAL_RAW_INPUT" | "$WAL_JQ" -r '.session_id // "unknown"')
  WAL_TOOL_USE_ID=$(printf '%s' "$WAL_RAW_INPUT" | "$WAL_JQ" -r '.tool_use_id // "unknown"')
  WAL_CWD=$(printf '%s' "$WAL_RAW_INPUT" | "$WAL_JQ" -r '.cwd // "."')
}

# wal_parse_input: Backward-compatible wrapper that reads stdin and extracts fields.
# Sets: WAL_RAW_INPUT, WAL_INPUT, WAL_TOOL_NAME, WAL_SESSION_ID, WAL_TOOL_USE_ID, WAL_CWD
wal_parse_input() {
  wal_read_stdin
  wal_parse_fields
}

# --- Workspace file resolution ---
# Searches up the directory tree from WAL_CWD for .rawgentic_workspace.json.
# Sets: WAL_WORKSPACE_FILE (path to workspace json, empty if not found)
#       WAL_WORKSPACE_ROOT (directory containing workspace json)
# Requires: WAL_CWD to be set.
wal_find_workspace() {
  WAL_WORKSPACE_FILE=""
  WAL_WORKSPACE_ROOT=""
  local dir="$WAL_CWD"
  local i=0
  while [ "$i" -lt 5 ] && [ "$dir" != "/" ]; do
    if [ -f "$dir/.rawgentic_workspace.json" ]; then
      WAL_WORKSPACE_FILE="$dir/.rawgentic_workspace.json"
      WAL_WORKSPACE_ROOT="$dir"
      return 0
    fi
    dir=$(cd "$dir/.." 2>/dev/null && pwd) || break
    i=$((i + 1))
  done
  return 1
}

# --- claude_docs path resolution ---
# Resolves the claude_docs directory from workspace config.
# If claudeDocsPath is set, uses that (with ~ expansion and path validation).
# Otherwise falls back to workspace-relative claude_docs/.
# Requires: WAL_WORKSPACE_FILE, WAL_WORKSPACE_ROOT (or WAL_CWD) to be set.
# Sets: WAL_CLAUDE_DOCS
wal_resolve_claude_docs() {
  local root="${WAL_WORKSPACE_ROOT:-$WAL_CWD}"
  WAL_CLAUDE_DOCS="$root/claude_docs"

  if [ -n "${WAL_WORKSPACE_FILE:-}" ] && [ -f "$WAL_WORKSPACE_FILE" ]; then
    local cdp
    cdp=$("$WAL_JQ" -r '.claudeDocsPath // ""' "$WAL_WORKSPACE_FILE" 2>/dev/null || true)
    if [ -n "$cdp" ]; then
      local had_tilde=false
      if [ "${cdp#\~}" != "$cdp" ]; then
        had_tilde=true
        cdp="${cdp/#\~/$HOME}"
      fi
      local resolved
      resolved=$(realpath -m "$cdp" 2>/dev/null || echo "$cdp")
      if [ "$had_tilde" = true ]; then
        # Tilde-expanded paths must stay under $HOME (path traversal guard)
        if [ "${resolved#$HOME/}" != "$resolved" ] || [ "$resolved" = "$HOME" ]; then
          WAL_CLAUDE_DOCS="$resolved"
        else
          echo "wal-lib: WARNING: claudeDocsPath traversal rejected: $cdp" >&2
        fi
      else
        # Explicit absolute paths are trusted
        WAL_CLAUDE_DOCS="$resolved"
      fi
    fi
  fi
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
  # Resolve claude_docs path if not already done
  if [ -z "${WAL_CLAUDE_DOCS:-}" ]; then
    wal_resolve_claude_docs
  fi
  WAL_DIR="$WAL_CLAUDE_DOCS/wal"
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
  [ -n "${WAL_FILE:-}" ] || return 0
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
#       WAL_PROJECT_PATH (absolute path to project directory, empty if unresolvable)
# Requires: WAL_SESSION_ID, WAL_CWD to be set.
wal_resolve_project() {
  WAL_PROJECT=""
  WAL_PROJECT_PATH=""
  # Resolve claude_docs path if not already done
  if [ -z "${WAL_CLAUDE_DOCS:-}" ]; then
    wal_resolve_claude_docs
  fi
  local root="${WAL_WORKSPACE_ROOT:-$WAL_CWD}"
  local registry_file="$WAL_CLAUDE_DOCS/session_registry.jsonl"

  # Check session registry first
  if [ -f "$registry_file" ] && [ "$WAL_SESSION_ID" != "unknown" ]; then
    local reg_line
    reg_line=$(grep "$WAL_SESSION_ID" "$registry_file" 2>/dev/null | tail -1 || true)
    if [ -n "$reg_line" ]; then
      WAL_PROJECT=$(printf '%s' "$reg_line" | "$WAL_JQ" -r '.project // ""' 2>/dev/null || true)
      WAL_PROJECT_PATH=$(printf '%s' "$reg_line" | "$WAL_JQ" -r '.project_path // ""' 2>/dev/null || true)
    fi
  fi

  # If project_path is relative, resolve against workspace root
  if [ -n "$WAL_PROJECT_PATH" ] && [ "${WAL_PROJECT_PATH#/}" = "$WAL_PROJECT_PATH" ]; then
    WAL_PROJECT_PATH="$root/$WAL_PROJECT_PATH"
  fi
}

# --- Protection level resolution ---
# Resolves per-project WAL guard protection level.
# Resolution order:
#   1. guards.wal explicit array in project .rawgentic.json → use exactly those rules
#   2. protectionLevel preset in project .rawgentic.json → expand to curated rule set
#   3. defaultProtectionLevel in workspace .rawgentic_workspace.json
#   4. Nothing found → strict (all patterns active)
# Preset expansions:
#   sandbox:  "" (empty = nothing active)
#   standard: "scp-prod rsync-prod docker-prod-destroy ansible-prod-mutate kubectl-prod-destroy helm-prod-destroy terraform-prod-destroy"
#   strict:   "ALL"
# Sets: WAL_PROTECTION_LEVEL (sandbox|standard|strict)
#       WAL_ACTIVE_WAL_GUARDS (space-delimited rule names, or "ALL" for strict)
# Requires: WAL_PROJECT_PATH, WAL_WORKSPACE_ROOT to be set.
wal_resolve_protection_level() {
  WAL_PROTECTION_LEVEL="strict"
  WAL_ACTIVE_WAL_GUARDS="ALL"

  local project_config="${WAL_PROJECT_PATH:+$WAL_PROJECT_PATH/.rawgentic.json}"
  local ws_config="${WAL_WORKSPACE_ROOT:+$WAL_WORKSPACE_ROOT/.rawgentic_workspace.json}"
  local level=""

  # 1. Check project .rawgentic.json for explicit guards.wal array
  if [ -n "$project_config" ] && [ -f "$project_config" ]; then
    local explicit_guards
    explicit_guards=$("$WAL_JQ" -r '
      if .guards and .guards.wal and (.guards.wal | type == "array") then
        .guards.wal | join(" ")
      else
        ""
      end
    ' "$project_config" 2>/dev/null || true)

    if [ -n "$explicit_guards" ]; then
      WAL_PROTECTION_LEVEL="custom"
      WAL_ACTIVE_WAL_GUARDS="$explicit_guards"
      return 0
    fi

    # 2. Check project .rawgentic.json for protectionLevel preset
    level=$("$WAL_JQ" -r '.protectionLevel // ""' "$project_config" 2>/dev/null || true)
  fi

  # 3. Check workspace .rawgentic_workspace.json for defaultProtectionLevel
  if [ -z "$level" ] && [ -n "$ws_config" ] && [ -f "$ws_config" ]; then
    level=$("$WAL_JQ" -r '.defaultProtectionLevel // ""' "$ws_config" 2>/dev/null || true)
  fi

  # 4. Nothing found → strict
  if [ -z "$level" ]; then
    level="strict"
  fi

  # Expand preset to rule set
  case "$level" in
    sandbox)
      WAL_PROTECTION_LEVEL="sandbox"
      WAL_ACTIVE_WAL_GUARDS=""
      ;;
    standard)
      WAL_PROTECTION_LEVEL="standard"
      WAL_ACTIVE_WAL_GUARDS="scp-prod rsync-prod docker-prod-destroy ansible-prod-mutate kubectl-prod-destroy helm-prod-destroy terraform-prod-destroy"
      ;;
    strict)
      WAL_PROTECTION_LEVEL="strict"
      WAL_ACTIVE_WAL_GUARDS="ALL"
      ;;
    *)
      # Invalid level → strict + warning
      echo "wal-lib: WARNING: invalid protectionLevel '$level', defaulting to strict" >&2
      WAL_PROTECTION_LEVEL="strict"
      WAL_ACTIVE_WAL_GUARDS="ALL"
      ;;
  esac
}
