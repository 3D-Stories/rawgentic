#!/usr/bin/env python3
"""Security Guard -- PreToolUse hook that blocks dangerous code patterns.

Hook: PreToolUse (matcher: Edit|Write|MultiEdit|NotebookEdit)
Protocol: JSON stdout with permissionDecision: deny
Policy: fail-open (any error -> allow the operation)

See docs/plans/2026-03-07-security-guard-design.md for full design.
"""
import json
import os
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from security_guard_lib import (
    extract_content,
    filter_by_exclude_paths,
    filter_exceptions,
    format_deny,
    load_protection_config,
    match_patterns,
    normalize_path,
)


def load_patterns():
    """Load security patterns from security-patterns.json.

    Returns list of pattern dicts, or empty list on error.
    Emits a systemMessage warning if the file is missing or malformed.
    """
    patterns_path = SCRIPT_DIR / "security-patterns.json"
    try:
        with open(patterns_path) as f:
            return json.load(f)
    except FileNotFoundError:
        _warn("security-patterns.json not found. Security guard is inactive.")
        return []
    except json.JSONDecodeError:
        _warn("security-patterns.json is malformed. Security guard is inactive.")
        return []
    except IOError:
        return []


def find_project_root(start_path):
    """Walk up from start_path to find directory containing .rawgentic.json.

    Returns absolute path string to project root, or None.
    """
    current = Path(start_path)
    if current.is_file():
        current = current.parent
    while current != current.parent:
        if (current / ".rawgentic.json").exists():
            return str(current)
        current = current.parent
    if (current / ".rawgentic.json").exists():
        return str(current)
    return None


def find_workspace_root(start_path):
    """Walk up from start_path to find directory containing .rawgentic_workspace.json."""
    current = Path(start_path)
    if current.is_file():
        current = current.parent
    for _ in range(5):
        if (current / ".rawgentic_workspace.json").exists():
            return str(current)
        if current == current.parent:
            break
        current = current.parent
    return None


def load_exceptions(project_root):
    """Load security exceptions from .rawgentic.json.

    Returns list of exception dicts, or empty list on any error.
    Missing securityExceptions key -> empty list.
    """
    if project_root is None:
        return []
    config_path = os.path.join(project_root, ".rawgentic.json")
    try:
        with open(config_path) as f:
            config = json.load(f)
        return config.get("securityExceptions") or []
    except FileNotFoundError:
        return []
    except json.JSONDecodeError:
        _warn(".rawgentic.json is malformed. Exceptions unavailable.")
        return []
    except IOError:
        return []


def get_file_path(tool_name, tool_input):
    """Extract file path from tool input."""
    if tool_name == "NotebookEdit":
        return tool_input.get("notebook_path", "")
    return tool_input.get("file_path", "")


def _warn(message):
    """Output a systemMessage warning without blocking."""
    print(json.dumps({"systemMessage": message}), file=sys.stdout)


def _log_headless_guard_block(findings, rel_path, workspace_root):
    """Log security guard blocks to WAL when in headless mode.

    In bypassPermissions mode, guards are the last defense. Blocks MUST
    be auditable via the WAL so the orchestrator can detect them.
    """
    try:
        from datetime import datetime, timezone

        # Find the WAL file for the current project
        ws_root = workspace_root or os.getcwd()
        # Read session registry to find the project
        registry_path = os.path.join(ws_root, "claude_docs", "session_registry.jsonl")
        session_id_path = os.path.join(ws_root, "claude_docs", ".current_session_id")

        session_id = "unknown"
        if os.path.isfile(session_id_path):
            with open(session_id_path) as f:
                session_id = f.read().strip()

        project = "unknown"
        if os.path.isfile(registry_path):
            with open(registry_path) as f:
                for line in f:
                    if session_id in line:
                        entry = json.loads(line)
                        project = entry.get("project", "unknown")

        wal_dir = os.path.join(ws_root, "claude_docs", "wal")
        os.makedirs(wal_dir, exist_ok=True)
        wal_file = os.path.join(wal_dir, f"{project}.jsonl")

        patterns_blocked = [f.get("name", "unknown") for f in findings]
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        entry = {
            "ts": ts,
            "phase": "GUARD_BLOCK",
            "session": session_id,
            "guard": "security-guard",
            "file": rel_path,
            "patterns": patterns_blocked,
        }
        with open(wal_file, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass  # Fail-open: logging failure must not block the deny response


def main():
    try:
        raw = sys.stdin.read()
        input_data = json.loads(raw)
    except (json.JSONDecodeError, IOError, ValueError):
        sys.exit(0)

    tool_name = input_data.get("tool_name", "")
    tool_input = input_data.get("tool_input", {})

    file_path = get_file_path(tool_name, tool_input)
    if not file_path:
        sys.exit(0)

    patterns = load_patterns()
    if not patterns:
        sys.exit(0)

    project_root = find_project_root(file_path)

    # Load protection config
    workspace_root = find_workspace_root(file_path) if project_root else None
    level, active_rules, exclude_paths, has_new_config = load_protection_config(
        project_root, workspace_root
    )

    # If no rules active (sandbox), skip all checks
    if active_rules is not None and len(active_rules) == 0:
        sys.exit(0)

    # Filter patterns to active rules
    if active_rules is not None:
        patterns = [p for p in patterns if p.get("ruleName") in active_rules]
        if not patterns:
            sys.exit(0)

    rel_path = normalize_path(file_path, project_root)
    content = extract_content(tool_name, tool_input)

    matches = match_patterns(rel_path, content, patterns)
    if not matches:
        sys.exit(0)

    # Apply exclude paths (new config model)
    if has_new_config and exclude_paths:
        matches = filter_by_exclude_paths(matches, exclude_paths, rel_path)
        if not matches:
            sys.exit(0)

    # Backward compat: use securityExceptions if no new config
    if not has_new_config:
        exceptions = load_exceptions(project_root)
        remaining = filter_exceptions(matches, exceptions, rel_path)
    else:
        remaining = matches
        # Deprecation warning: securityExceptions with new config
        exceptions = load_exceptions(project_root)
        if exceptions and not os.environ.get("_RAWGENTIC_DEPRECATION_WARNED"):
            _warn(
                "DEPRECATION: securityExceptions is ignored when protectionLevel "
                "or guards is set. Migrate exceptions to guards.securityExcludePaths. "
                "See docs/config-reference.md."
            )

    if not remaining:
        sys.exit(0)

    # Headless mode audit: log guard blocks to WAL for visibility
    # In bypassPermissions mode, guards are the last defense — blocks MUST be auditable
    if os.environ.get("RAWGENTIC_HEADLESS") == "1":
        _log_headless_guard_block(remaining, rel_path, workspace_root)

    deny_output = format_deny(remaining, rel_path)
    print(json.dumps(deny_output), file=sys.stdout)
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(0)  # fail-open on any unexpected error
