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
import re


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
        suggested = suggest_glob(rel_path)
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
