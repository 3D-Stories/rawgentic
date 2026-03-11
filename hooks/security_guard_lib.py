#!/usr/bin/env python3
"""Pure utility functions for the security-guard hook.

All functions in this module are pure (no I/O) for easy testing.
I/O operations live in security-guard.py (the main hook script).

Path matching note: Uses fnmatch instead of PurePath.match() because
PurePath.match('**/__tests__/**') fails for root-level paths like
__tests__/foo.js on Python 3.12+. The fnmatch + dummy prefix approach
works correctly on Linux. On macOS, fnmatch * does not cross /
boundaries, so this is Linux-only.
"""
import fnmatch
import json
import os
import re


# Preset expansions for security guards
SECURITY_PRESETS = {
    "sandbox": set(),
    "standard": {
        "eval_injection", "new_function_injection", "innerHTML_xss",
        "document_write_xss", "pickle_deserialization", "os_system_injection",
    },
    "strict": None,  # None means all active
}


def glob_match(path_str, pattern):
    """Match a relative path against a glob pattern.

    Uses fnmatch with a dummy prefix to handle root-level ** patterns.
    On Linux, fnmatch treats * as matching /, so ** effectively works
    as recursive glob. The dummy prefix ensures ** has something to
    match even when the path starts at the matched segment.

    Linux-only: on macOS, fnmatch * does not cross / boundaries.
    """
    if pattern.startswith("**"):
        return fnmatch.fnmatch("_/" + path_str, pattern)
    return fnmatch.fnmatch(path_str, pattern)


def normalize_path(abs_path, project_root):
    """Convert an absolute file path to project-relative."""
    if project_root is None:
        return abs_path
    root = project_root.rstrip("/") + "/"
    if abs_path.startswith(root):
        return abs_path[len(root):]
    return abs_path


def extract_content(tool_name, tool_input):
    """Extract the content to check from a tool's input.

    Only checks new/written content, not content being replaced,
    so removing dangerous patterns is not blocked.
    Uses `or ""` pattern to handle None values from tool_input.
    """
    if tool_name == "Write":
        return tool_input.get("content") or ""
    elif tool_name == "Edit":
        return tool_input.get("new_string") or ""
    elif tool_name == "MultiEdit":
        edits = tool_input.get("edits") or []
        return " ".join((e.get("new_string") or "") for e in edits)
    elif tool_name == "NotebookEdit":
        return tool_input.get("new_source") or ""
    return ""


def check_word_boundary(content, substring):
    """Check if substring appears with a non-alpha char before it.

    Prevents matches like medieval( for the eval( pattern.
    The substring should include its own trailing delimiter (like '('
    or '.') to prevent after-boundary false positives.
    """
    start = 0
    while True:
        idx = content.find(substring, start)
        if idx == -1:
            return False
        if idx == 0 or not content[idx - 1].isalpha():
            return True
        start = idx + 1


def sanitize_path_for_message(path_str):
    """Sanitize a file path for inclusion in systemMessage.

    Removes chars that could be used for prompt injection.
    Truncates to 200 characters.
    """
    sanitized = re.sub(r"[^a-zA-Z0-9._/\-]", "", path_str)
    return sanitized[:200]


def suggest_glob(rel_path):
    """Suggest an exception glob pattern for a file path.

    Detects common test/workflow directory segments and suggests
    a glob pattern. Falls back to the exact file path.
    """
    test_segments = ("__tests__", "test", "tests", "spec", "specs")
    parts = rel_path.replace("\\", "/").split("/")
    for segment in test_segments:
        if segment in parts:
            return f"**/{segment}/**"
    if ".github/workflows" in rel_path:
        return ".github/workflows/**"
    return rel_path


def match_patterns(rel_path, content, patterns):
    """Check content and path against all security patterns.

    Collects ALL matches, not just the first one.
    Broken patterns are silently skipped (fail-open).
    """
    matches = []
    for pattern in patterns:
        try:
            if pattern.get("type") == "path":
                path_pattern = pattern.get("pathPattern", "")
                if glob_match(rel_path, path_pattern):
                    matches.append(pattern)
            elif pattern.get("type") == "substring":
                word_boundary = pattern.get("wordBoundary", False)
                for sub in pattern.get("substrings", []):
                    if word_boundary:
                        if check_word_boundary(content, sub):
                            matches.append(pattern)
                            break
                    else:
                        if sub in content:
                            matches.append(pattern)
                            break
        except Exception:
            continue  # skip broken patterns (fail-open)
    return matches


def filter_exceptions(matches, exceptions, rel_path):
    """Remove matches that have a corresponding exception.

    An exception matches if both the rule name AND the pathPattern
    glob match the current file.
    """
    remaining = []
    for match in matches:
        rule_name = match.get("ruleName", "")
        has_exception = False
        for exc in exceptions:
            if exc.get("rule") == rule_name:
                exc_pattern = exc.get("pathPattern", "")
                try:
                    if glob_match(rel_path, exc_pattern):
                        has_exception = True
                        break
                except Exception:
                    continue
        if not has_exception:
            remaining.append(match)
    return remaining


def format_deny(matches, rel_path):
    """Format the deny JSON output for unexcepted pattern matches.

    Returns a dict with hookSpecificOutput (deny decision) and
    systemMessage (instructions for Claude to present to user).
    """
    if not matches:
        return {}

    safe_path = sanitize_path_for_message(rel_path)
    sections = []
    for match in matches:
        rule = match.get("ruleName", "unknown")
        reminder = match.get("reminder", "")
        suggested = suggest_glob(safe_path)
        section = (
            f"SECURITY BLOCK: Rule '{rule}' triggered on file '{safe_path}'.\n\n"
            f"{reminder}\n\n"
            f"DO NOT retry this operation. Instead, present the user with these options:\n"
            f'1. "Please use a safer approach" - rework the code to avoid the flagged pattern\n'
            f'2. "Add exception for {suggested}" - add a security exception to '
            f".rawgentic.json for this path pattern and retry\n\n"
            f"Suggested exception glob: {suggested}"
        )
        sections.append(section)

    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
        },
        "systemMessage": "\n\n---\n\n".join(sections),
    }


def load_protection_config(project_root, workspace_root=None):
    """Load protection configuration from .rawgentic.json.

    Resolution order:
    1. guards.security explicit list -> use exactly those rules
    2. protectionLevel preset -> expand to curated rule set
    3. No project config -> check workspace defaultProtectionLevel
    4. Nothing found -> strict (all active)

    Returns (level, active_rules, exclude_paths, has_new_config):
    - level: str ("sandbox", "standard", "strict", "custom")
    - active_rules: set|None (None = all active, set() = none active)
    - exclude_paths: list[str]|None (glob patterns from guards.securityExcludePaths)
    - has_new_config: bool (True if guards or protectionLevel key exists)
    """
    if project_root is None:
        return ("strict", None, None, False)

    config_path = os.path.join(project_root, ".rawgentic.json")
    try:
        with open(config_path) as f:
            config = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, IOError):
        return ("strict", None, None, False)

    guards = config.get("guards", {})
    protection_level = config.get("protectionLevel")
    has_new_config = bool(guards) or protection_level is not None

    # Extract exclude paths
    exclude_paths = guards.get("securityExcludePaths") if isinstance(guards, dict) else None

    # Resolution: explicit guards.security first
    if isinstance(guards, dict) and "security" in guards:
        rules = guards["security"]
        if isinstance(rules, list):
            return ("custom", set(rules), exclude_paths, True)

    # Resolution: protectionLevel preset
    if protection_level is not None:
        level = str(protection_level).lower()
        if level in SECURITY_PRESETS:
            return (level, SECURITY_PRESETS[level], exclude_paths, True)
        else:
            # Invalid level -> strict + could warn
            return ("strict", None, exclude_paths, True)

    # Resolution: workspace defaultProtectionLevel
    if workspace_root is not None:
        ws_config_path = os.path.join(workspace_root, ".rawgentic_workspace.json")
        try:
            with open(ws_config_path) as f:
                ws_config = json.load(f)
            default_level = ws_config.get("defaultProtectionLevel", "").lower()
            if default_level in SECURITY_PRESETS:
                return (default_level, SECURITY_PRESETS[default_level], None, False)
        except (FileNotFoundError, json.JSONDecodeError, IOError):
            pass

    # Default: strict
    return ("strict", None, None, False)


def filter_by_exclude_paths(matches, exclude_paths, rel_path):
    """Remove matches for files that match any exclude path pattern.

    If exclude_paths is None or empty, returns matches unchanged.
    Uses glob_match() for pattern matching.
    """
    if not exclude_paths:
        return matches
    for pattern in exclude_paths:
        if glob_match(rel_path, pattern):
            return []  # File is excluded from all security checks
    return matches
