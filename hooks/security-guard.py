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
    filter_exceptions,
    format_deny,
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
    rel_path = normalize_path(file_path, project_root)

    content = extract_content(tool_name, tool_input)

    matches = match_patterns(rel_path, content, patterns)
    if not matches:
        sys.exit(0)

    exceptions = load_exceptions(project_root)
    remaining = filter_exceptions(matches, exceptions, rel_path)
    if not remaining:
        sys.exit(0)

    deny_output = format_deny(remaining, rel_path)
    print(json.dumps(deny_output), file=sys.stdout)
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(0)  # fail-open on any unexpected error
