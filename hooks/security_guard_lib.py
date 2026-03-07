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
