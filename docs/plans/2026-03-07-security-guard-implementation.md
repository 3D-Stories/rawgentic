# Security Guard Hook Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Implement a PreToolUse security hook in the rawgentic plugin that hard-blocks dangerous code patterns with per-project exception lists, replacing the broken official security-guidance plugin.

**Architecture:** A Python PreToolUse hook (`security-guard.py`) checks file writes against externalized patterns (`security-patterns.json`), matches exceptions from `.rawgentic.json`, and outputs JSON deny decisions via stdout. A separate SessionStart bash hook (`security-guard-check.sh`) detects if the official plugin is still enabled. All path matching uses `fnmatch` with a dummy-prefix trick to handle root-level `**` patterns.

**Tech Stack:** Python 3.13 (stdlib only: json, sys, os, fnmatch, pathlib, re), Bash, pytest

**Design doc:** `docs/plans/2026-03-07-security-guard-design.md`

> **Note on path matching:** The design doc specifies `PurePath.match()` for glob matching. This was changed to `fnmatch` with a dummy-prefix trick because `PurePath('__tests__/foo.js').match('**/__tests__/**')` returns `False` on Python 3.12+ -- root-level paths fail to match `**` prefixed patterns. The `fnmatch` approach passes all edge cases on Linux. **Limitation:** On macOS, `fnmatch` `*` does not cross `/` boundaries (due to `FNM_PATHNAME`), so this hook is Linux-only. Document in code if cross-platform is needed later.

---

### Task 1a: Create test infrastructure and basic utility tests

**Files:**
- Create: `tests/__init__.py`
- Create: `tests/hooks/__init__.py`
- Create: `tests/hooks/test_security_guard.py` (part 1)
- Create: `hooks/security_guard_lib.py` (part 1: glob_match, normalize_path, extract_content)

**Step 1: Create test directories**

Run: `cd $PLUGIN_ROOT && mkdir -p tests/hooks && touch tests/__init__.py tests/hooks/__init__.py`

**Step 2: Create test file with tests for glob_match, normalize_path, extract_content**

Create `tests/hooks/test_security_guard.py`:

```python
#!/usr/bin/env python3
"""Tests for security-guard hook pure functions."""
import pytest
import json
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "hooks"))

from security_guard_lib import (
    glob_match,
    normalize_path,
    extract_content,
)


class TestGlobMatch:
    """Test glob_match handles ** patterns including root-level.

    Note: Uses fnmatch with dummy prefix instead of PurePath.match()
    because PurePath.match fails on root-level ** patterns in Python 3.12+.
    This approach is Linux-only (fnmatch * matches / on Linux but not macOS).
    """

    def test_nested_tests_dir(self):
        assert glob_match("src/__tests__/foo.js", "**/__tests__/**") is True

    def test_root_level_tests_dir(self):
        assert glob_match("__tests__/foo.js", "**/__tests__/**") is True

    def test_deeply_nested_tests_dir(self):
        assert glob_match("deep/src/__tests__/unit/foo.js", "**/__tests__/**") is True

    def test_no_match(self):
        assert glob_match("src/app.js", "**/__tests__/**") is False

    def test_root_level_test_dir(self):
        assert glob_match("test/foo.js", "**/test/**") is True

    def test_github_workflows_yml(self):
        assert glob_match(".github/workflows/ci.yml", ".github/workflows/*.yml") is True

    def test_github_workflows_yaml(self):
        assert glob_match(".github/workflows/ci.yaml", ".github/workflows/*.yaml") is True

    def test_non_workflow_yml(self):
        assert glob_match("src/deploy.yml", ".github/workflows/*.yml") is False

    def test_test_file_extension(self):
        assert glob_match("src/foo.test.js", "**/*.test.*") is True

    def test_root_test_file_extension(self):
        assert glob_match("foo.test.js", "**/*.test.*") is True

    def test_spec_file(self):
        assert glob_match("src/foo.spec.ts", "**/*.spec.*") is True

    def test_non_leading_doublestar(self):
        """Patterns with ** not at start use bare fnmatch."""
        assert glob_match("src/deep/__tests__/foo.js", "src/**/__tests__/**") is True


class TestNormalizePath:
    def test_strips_project_root(self):
        assert normalize_path("/home/user/project/src/foo.js", "/home/user/project") == "src/foo.js"

    def test_strips_trailing_slash(self):
        assert normalize_path("/home/user/project/src/foo.js", "/home/user/project/") == "src/foo.js"

    def test_root_level_file(self):
        assert normalize_path("/home/user/project/foo.js", "/home/user/project") == "foo.js"

    def test_no_project_root_returns_abs_path(self):
        assert normalize_path("/home/user/project/src/foo.js", None) == "/home/user/project/src/foo.js"

    def test_path_outside_project_returns_abs_path(self):
        assert normalize_path("/other/path/foo.js", "/home/user/project") == "/other/path/foo.js"


class TestExtractContent:
    def test_write_tool(self):
        assert extract_content("Write", {"content": "hello", "file_path": "f.js"}) == "hello"

    def test_edit_tool_uses_new_string(self):
        assert extract_content("Edit", {"old_string": "dangerous", "new_string": "safe", "file_path": "f.js"}) == "safe"

    def test_multiedit_concatenates(self):
        result = extract_content("MultiEdit", {"file_path": "f.js", "edits": [{"new_string": "a"}, {"new_string": "b"}]})
        assert "a" in result and "b" in result

    def test_notebook_edit(self):
        assert extract_content("NotebookEdit", {"notebook_path": "nb.ipynb", "new_source": "import os"}) == "import os"

    def test_unknown_tool_returns_empty(self):
        assert extract_content("Bash", {"command": "ls"}) == ""

    def test_missing_content_returns_empty(self):
        assert extract_content("Write", {"file_path": "f.js"}) == ""

    def test_none_content_returns_empty(self):
        """When content key exists but value is None, return empty string."""
        assert extract_content("Write", {"file_path": "f.js", "content": None}) == ""

    def test_none_new_string_returns_empty(self):
        assert extract_content("Edit", {"file_path": "f.js", "new_string": None}) == ""
```

**Step 3: Run tests to verify they fail**

Run: `cd $PLUGIN_ROOT && python3 -m pytest tests/hooks/test_security_guard.py -v 2>&1 | tail -20`
Expected: FAIL with `ModuleNotFoundError: No module named 'security_guard_lib'`

**Step 4: Implement part 1 of security_guard_lib.py**

Create `hooks/security_guard_lib.py`:

```python
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
```

**Step 5: Run tests to verify they pass**

Run: `cd $PLUGIN_ROOT && python3 -m pytest tests/hooks/test_security_guard.py -v 2>&1 | tail -30`
Expected: All tests PASS

**Step 6: Commit**

```bash
git add tests/__init__.py tests/hooks/__init__.py tests/hooks/test_security_guard.py hooks/security_guard_lib.py
git commit -m "feat(security-guard): add glob_match, normalize_path, extract_content with tests

Pure utility functions for path matching (fnmatch + dummy prefix),
path normalization, and content extraction for 4 tool types.
Handles None values in tool_input safely."
```

---

### Task 1b: Add word boundary, sanitize, suggest, match, filter, format functions

**Files:**
- Modify: `tests/hooks/test_security_guard.py` (append tests)
- Modify: `hooks/security_guard_lib.py` (append functions)

**Step 1: Append tests for remaining functions**

Append to `tests/hooks/test_security_guard.py`:

```python
from security_guard_lib import (
    check_word_boundary,
    sanitize_path_for_message,
    suggest_glob,
    match_patterns,
    filter_exceptions,
    format_deny,
)


class TestCheckWordBoundary:
    """Test word boundary checking for substring matches.

    The word boundary check ensures the character BEFORE the match
    is not alphabetic. The substring itself should include a trailing
    delimiter (like '(' or '.') to prevent after-boundary issues.
    """

    def test_eval_standalone(self):
        assert check_word_boundary("x = eval(code)", "eval(") is True

    def test_eval_at_start(self):
        assert check_word_boundary("eval(code)", "eval(") is True

    def test_medieval_false_positive(self):
        assert check_word_boundary("medieval(castle)", "eval(") is False

    def test_pickle_dot_standalone(self):
        assert check_word_boundary("import pickle.", "pickle.") is True

    def test_pickled_with_dot_pattern(self):
        """pickled_data does not contain 'pickle.' (with dot)."""
        assert check_word_boundary("pickled_data = 1", "pickle.") is False

    def test_document_write_paren(self):
        assert check_word_boundary("document.write(x)", "document.write(") is True

    def test_document_writestream(self):
        assert check_word_boundary("document.writeStream", "document.write(") is False

    def test_space_before(self):
        assert check_word_boundary("x = eval(y)", "eval(") is True

    def test_dot_before(self):
        assert check_word_boundary("obj.eval(y)", "eval(") is True

    def test_newline_before(self):
        assert check_word_boundary("\neval(y)", "eval(") is True

    def test_substring_at_end_of_content(self):
        assert check_word_boundary("use eval(", "eval(") is True

    def test_import_pickle(self):
        """import pickle matches 'import pickle' pattern."""
        assert check_word_boundary("import pickle\nimport json", "import pickle") is True


class TestSanitizePathForMessage:
    def test_normal_path(self):
        assert sanitize_path_for_message("src/foo.js") == "src/foo.js"

    def test_strips_special_chars(self):
        result = sanitize_path_for_message("src/foo;rm -rf/.js")
        assert ";" not in result
        assert " " not in result

    def test_truncates_long_path(self):
        long_path = "a/" * 200 + "foo.js"
        assert len(sanitize_path_for_message(long_path)) <= 200


class TestSuggestGlob:
    def test_tests_dir(self):
        assert suggest_glob("src/__tests__/unit/foo.test.js") == "**/__tests__/**"

    def test_test_dir(self):
        assert suggest_glob("src/test/foo.js") == "**/test/**"

    def test_spec_dir(self):
        assert suggest_glob("src/spec/foo.js") == "**/spec/**"

    def test_github_workflows(self):
        assert suggest_glob(".github/workflows/ci.yml") == ".github/workflows/**"

    def test_fallback_to_file_path(self):
        assert suggest_glob("src/utils/helper.js") == "src/utils/helper.js"
```

**Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/hooks/test_security_guard.py::TestCheckWordBoundary -v 2>&1 | tail -10`
Expected: FAIL with `ImportError`

**Step 3: Append remaining functions to security_guard_lib.py**

Append to `hooks/security_guard_lib.py`:

```python


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
```

**Step 4: Run tests to verify they pass**

Run: `cd $PLUGIN_ROOT && python3 -m pytest tests/hooks/test_security_guard.py -v 2>&1 | tail -50`
Expected: All tests PASS

**Step 5: Commit**

```bash
git add tests/hooks/test_security_guard.py hooks/security_guard_lib.py
git commit -m "feat(security-guard): add word boundary, matching, filtering, and formatting

Completes security_guard_lib with check_word_boundary, sanitize_path_for_message,
suggest_glob, match_patterns, filter_exceptions, and format_deny."
```

---

### Task 2: Add pattern matching and exception integration tests

**Files:**
- Modify: `tests/hooks/test_security_guard.py`

**Step 1: Append integration tests to the test file**

Append to `tests/hooks/test_security_guard.py`:

```python


# Sample patterns for integration testing (mirrors security-patterns.json)
SAMPLE_PATTERNS = [
    {
        "ruleName": "eval_injection",
        "source": "upstream",
        "type": "substring",
        "substrings": ["eval("],
        "wordBoundary": True,
        "reminder": "eval() is dangerous.",
        "suggestedGlobs": ["**/__tests__/**"],
    },
    {
        "ruleName": "innerHTML_xss",
        "source": "upstream",
        "type": "substring",
        "substrings": [".innerHTML =", ".innerHTML="],
        "wordBoundary": False,
        "reminder": "innerHTML is dangerous.",
        "suggestedGlobs": [],
    },
    {
        "ruleName": "github_actions_workflow",
        "source": "upstream",
        "type": "path",
        "pathPattern": ".github/workflows/*.yml",
        "reminder": "GH Actions injection risk.",
        "suggestedGlobs": [],
    },
]


class TestMatchPatterns:
    def test_eval_matches(self):
        matches = match_patterns("src/app.js", "x = eval(code)", SAMPLE_PATTERNS)
        assert len(matches) == 1 and matches[0]["ruleName"] == "eval_injection"

    def test_eval_word_boundary_blocks_medieval(self):
        assert match_patterns("src/app.js", "medieval(castle)", SAMPLE_PATTERNS) == []

    def test_innerhtml_matches(self):
        matches = match_patterns("src/app.js", 'el.innerHTML = "hi"', SAMPLE_PATTERNS)
        assert len(matches) == 1 and matches[0]["ruleName"] == "innerHTML_xss"

    def test_path_pattern_matches_workflow(self):
        matches = match_patterns(".github/workflows/ci.yml", "name: CI", SAMPLE_PATTERNS)
        assert len(matches) == 1 and matches[0]["ruleName"] == "github_actions_workflow"

    def test_no_match(self):
        assert match_patterns("src/app.js", "console.log('hello')", SAMPLE_PATTERNS) == []

    def test_multiple_matches(self):
        matches = match_patterns("src/app.js", 'eval(x); el.innerHTML = "y"', SAMPLE_PATTERNS)
        assert len(matches) == 2

    def test_broken_pattern_skipped(self):
        assert match_patterns("src/app.js", "eval(x)", [{"type": "substring"}]) == []

    def test_pattern_missing_pathPattern(self):
        """Path pattern with no pathPattern key is skipped."""
        assert match_patterns("src/app.js", "test", [{"type": "path"}]) == []


class TestFilterExceptions:
    def test_exception_removes_match(self):
        matches = [{"ruleName": "eval_injection", "reminder": "..."}]
        exceptions = [{"rule": "eval_injection", "pathPattern": "**/__tests__/**"}]
        assert filter_exceptions(matches, exceptions, "src/__tests__/foo.test.js") == []

    def test_wrong_rule_not_excepted(self):
        matches = [{"ruleName": "eval_injection", "reminder": "..."}]
        exceptions = [{"rule": "innerHTML_xss", "pathPattern": "**/__tests__/**"}]
        assert len(filter_exceptions(matches, exceptions, "src/__tests__/foo.test.js")) == 1

    def test_wrong_path_not_excepted(self):
        matches = [{"ruleName": "eval_injection", "reminder": "..."}]
        exceptions = [{"rule": "eval_injection", "pathPattern": "**/__tests__/**"}]
        assert len(filter_exceptions(matches, exceptions, "src/app.js")) == 1

    def test_partial_exception(self):
        matches = [
            {"ruleName": "eval_injection", "reminder": "..."},
            {"ruleName": "innerHTML_xss", "reminder": "..."},
        ]
        exceptions = [{"rule": "eval_injection", "pathPattern": "**/__tests__/**"}]
        result = filter_exceptions(matches, exceptions, "src/__tests__/foo.test.js")
        assert len(result) == 1 and result[0]["ruleName"] == "innerHTML_xss"

    def test_empty_exceptions(self):
        matches = [{"ruleName": "eval_injection", "reminder": "..."}]
        assert len(filter_exceptions(matches, [], "src/__tests__/foo.test.js")) == 1

    def test_exception_missing_securityExceptions_key(self):
        """rawgentic.json with no securityExceptions -> empty list -> no exceptions."""
        matches = [{"ruleName": "eval_injection", "reminder": "..."}]
        assert len(filter_exceptions(matches, [], "src/__tests__/foo.test.js")) == 1


class TestFormatDeny:
    def test_single_match_structure(self):
        result = format_deny([{"ruleName": "eval_injection", "reminder": "eval bad."}], "src/__tests__/foo.test.js")
        assert result["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
        assert result["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert "eval_injection" in result["systemMessage"]
        assert "DO NOT retry" in result["systemMessage"]
        assert "**/__tests__/**" in result["systemMessage"]

    def test_multiple_matches_aggregated(self):
        result = format_deny([
            {"ruleName": "eval_injection", "reminder": "eval bad."},
            {"ruleName": "innerHTML_xss", "reminder": "innerHTML bad."},
        ], "src/app.js")
        assert "eval_injection" in result["systemMessage"]
        assert "innerHTML_xss" in result["systemMessage"]

    def test_output_is_valid_json(self):
        result = format_deny([{"ruleName": "eval_injection", "reminder": "bad."}], "src/app.js")
        parsed = json.loads(json.dumps(result))
        assert parsed["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_path_sanitized_in_message(self):
        result = format_deny([{"ruleName": "eval_injection", "reminder": "bad."}], "src/foo;rm -rf/.js")
        assert ";" not in result["systemMessage"]

    def test_empty_matches_returns_empty_dict(self):
        assert format_deny([], "src/app.js") == {}

    def test_reminder_with_newlines_is_valid_json(self):
        result = format_deny([{"ruleName": "test", "reminder": "line1\nline2\n\"quoted\""}], "src/app.js")
        json_str = json.dumps(result)
        parsed = json.loads(json_str)
        assert "line1" in parsed["systemMessage"]
```

**Step 2: Run tests to verify they pass**

Run: `cd $PLUGIN_ROOT && python3 -m pytest tests/hooks/test_security_guard.py -v 2>&1 | tail -60`
Expected: All tests PASS

**Step 3: Commit**

```bash
git add tests/hooks/test_security_guard.py
git commit -m "test(security-guard): add pattern matching and exception integration tests

Covers multi-match aggregation, broken patterns, missing keys,
empty matches, and JSON validity with special characters."
```

---

### Task 3: Create security-patterns.json seed data

**Files:**
- Create: `hooks/security-patterns.json`

**Step 1: Create the patterns file with all 10 patterns**

Create `hooks/security-patterns.json`:

```json
[
  {
    "ruleName": "eval_injection",
    "source": "upstream",
    "type": "substring",
    "substrings": ["eval("],
    "wordBoundary": true,
    "reminder": "eval() executes arbitrary code and is a major security risk. Consider using JSON.parse() for data parsing or alternative design patterns that don't require code evaluation. Only use eval() if you truly need to evaluate arbitrary code.",
    "suggestedGlobs": ["**/__tests__/**", "**/*.test.*", "**/*.spec.*"]
  },
  {
    "ruleName": "new_function_injection",
    "source": "upstream",
    "type": "substring",
    "substrings": ["new Function"],
    "wordBoundary": false,
    "reminder": "Using new Function() with dynamic strings can lead to code injection vulnerabilities. Consider alternative approaches that don't evaluate arbitrary code. Only use new Function() if you truly need to evaluate arbitrary dynamic code.",
    "suggestedGlobs": ["**/__tests__/**", "**/*.test.*"]
  },
  {
    "ruleName": "child_process_exec",
    "source": "upstream",
    "type": "substring",
    "substrings": ["child_process.exec", "execSync("],
    "wordBoundary": false,
    "reminder": "Using child_process.exec() can lead to command injection vulnerabilities. Use execFile() or spawn() with an argument array instead, which prevents shell injection. Only use exec() if you absolutely need shell features and the input is guaranteed to be safe.",
    "suggestedGlobs": []
  },
  {
    "ruleName": "react_dangerously_set_html",
    "source": "upstream",
    "type": "substring",
    "substrings": ["dangerouslySetInnerHTML"],
    "wordBoundary": false,
    "reminder": "dangerouslySetInnerHTML can lead to XSS vulnerabilities if used with untrusted content. Ensure all content is properly sanitized using an HTML sanitizer library like DOMPurify, or use safe alternatives.",
    "suggestedGlobs": []
  },
  {
    "ruleName": "document_write_xss",
    "source": "upstream",
    "type": "substring",
    "substrings": ["document.write("],
    "wordBoundary": true,
    "reminder": "document.write() can be exploited for XSS attacks and has performance issues. Use DOM manipulation methods like createElement() and appendChild() instead.",
    "suggestedGlobs": []
  },
  {
    "ruleName": "innerHTML_xss",
    "source": "upstream",
    "type": "substring",
    "substrings": [".innerHTML =", ".innerHTML="],
    "wordBoundary": false,
    "reminder": "Setting innerHTML with untrusted content can lead to XSS vulnerabilities. Use textContent for plain text or safe DOM methods for HTML content. If you need HTML support, consider using an HTML sanitizer library such as DOMPurify.",
    "suggestedGlobs": []
  },
  {
    "ruleName": "pickle_deserialization",
    "source": "upstream",
    "type": "substring",
    "substrings": ["import pickle", "pickle."],
    "wordBoundary": true,
    "reminder": "Using pickle with untrusted content can lead to arbitrary code execution. Consider using JSON or other safe serialization formats instead. Only use pickle if it is explicitly needed or requested by the user.",
    "suggestedGlobs": []
  },
  {
    "ruleName": "os_system_injection",
    "source": "upstream",
    "type": "substring",
    "substrings": ["os.system", "from os import system"],
    "wordBoundary": false,
    "reminder": "os.system() should only be used with static arguments and never with arguments that could be user-controlled. Use subprocess.run() with an argument list instead.",
    "suggestedGlobs": []
  },
  {
    "ruleName": "github_actions_workflow",
    "source": "upstream",
    "type": "path",
    "pathPattern": ".github/workflows/*.yml",
    "reminder": "You are editing a GitHub Actions workflow file. Be aware of command injection risks:\n\n1. Never use untrusted input (issue titles, PR descriptions, commit messages) directly in run: commands\n2. Use environment variables instead: env: TITLE: ${{ github.event.issue.title }}\n3. Review: https://github.blog/security/vulnerability-research/how-to-catch-github-actions-workflow-injections-before-attackers-do/",
    "suggestedGlobs": []
  },
  {
    "ruleName": "github_actions_workflow_yaml",
    "source": "upstream",
    "type": "path",
    "pathPattern": ".github/workflows/*.yaml",
    "reminder": "You are editing a GitHub Actions workflow file. Be aware of command injection risks:\n\n1. Never use untrusted input (issue titles, PR descriptions, commit messages) directly in run: commands\n2. Use environment variables instead: env: TITLE: ${{ github.event.issue.title }}\n3. Review: https://github.blog/security/vulnerability-research/how-to-catch-github-actions-workflow-injections-before-attackers-do/",
    "suggestedGlobs": []
  }
]
```

**Step 2: Validate the JSON**

Run: `cd $PLUGIN_ROOT && python3 -c "import json; d=json.load(open('hooks/security-patterns.json')); print(f'{len(d)} patterns loaded'); [print(f'  {p[\"ruleName\"]} ({p[\"source\"]})') for p in d]"`
Expected: `10 patterns loaded` with all rule names and `upstream` source

**Step 3: Commit**

```bash
git add hooks/security-patterns.json
git commit -m "feat(security-guard): add security patterns seed data

10 patterns seeded from Anthropic's security-guidance plugin with
wordBoundary flags and source field for sync differentiation.
child_process_exec excludes bare exec( to avoid Python false positives."
```

---

### Task 4: Create the main security-guard.py hook script

**Files:**
- Create: `hooks/security-guard.py`

**Step 1: Write the main hook script**

Create `hooks/security-guard.py`:

```python
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
```

**Step 2: Make it executable**

Run: `chmod +x $PLUGIN_ROOT/hooks/security-guard.py`

**Step 3: Test manually -- blocked content**

Run: `cd $PLUGIN_ROOT && echo '{"tool_name":"Write","tool_input":{"file_path":"/tmp/test/src/app.js","content":"x = eval(code)"}}' | python3 hooks/security-guard.py`
Expected: JSON with `permissionDecision: deny` and `eval_injection` in systemMessage

**Step 4: Test manually -- safe content**

Run: `echo '{"tool_name":"Write","tool_input":{"file_path":"/tmp/test/src/app.js","content":"console.log(1)"}}' | python3 hooks/security-guard.py`
Expected: No output (exit 0, allowed)

**Step 5: Test manually -- malformed input**

Run: `echo 'not json' | python3 hooks/security-guard.py; echo "exit: $?"`
Expected: No output, exit code 0

**Step 6: Commit**

```bash
git add hooks/security-guard.py
git commit -m "feat(security-guard): add main PreToolUse hook script

Orchestrates pattern loading, path normalization, content extraction,
pattern matching, exception filtering, and deny output.
Fail-open on all errors. Emits systemMessage warnings for missing
or malformed config files."
```

---

### Task 5: Create the SessionStart conflict detection hook

**Files:**
- Create: `hooks/security-guard-check.sh`

**Step 1: Write the SessionStart hook**

Create `hooks/security-guard-check.sh`:

```bash
#!/bin/bash
# Security Guard Check -- SessionStart hook that detects conflict
# with the official security-guidance plugin.
# Hook: SessionStart (matcher: startup|resume)
# Uses additionalContext (matching existing session-start hook format).
# Silently skips on any error.

set -euo pipefail

SETTINGS_FILE="$HOME/.claude/settings.json"

# Skip if settings file doesn't exist
if [ ! -f "$SETTINGS_FILE" ]; then
  exit 0
fi

# Check if security-guidance plugin is enabled
CONFLICT=$(python3 -c "
import json, sys
try:
    with open('$SETTINGS_FILE') as f:
        s = json.load(f)
    enabled = s.get('enabledPlugins', {})
    if enabled.get('security-guidance@claude-plugins-official', False):
        print('yes')
except Exception:
    pass
" 2>/dev/null || true)

if [ "$CONFLICT" = "yes" ]; then
  cat <<'JSONEOF'
{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":"The official security-guidance plugin must be disabled for rawgentic's security guard to work correctly. Both plugins check the same patterns, but the official one uses a broken blocking mechanism that auto-retries, and running both causes a confusing double-block loop. Run: claude plugin disable security-guidance@claude-plugins-official"}}
JSONEOF
fi

exit 0
```

**Step 2: Make it executable**

Run: `chmod +x $PLUGIN_ROOT/hooks/security-guard-check.sh`

**Step 3: Test it -- conflict detected**

Run: `cd $PLUGIN_ROOT && bash hooks/security-guard-check.sh`
Expected: JSON with `additionalContext` warning about disabling the official plugin

**Step 4: Test it -- no conflict (simulate by checking output format)**

Run: `bash hooks/security-guard-check.sh | python3 -c "import sys,json; d=json.load(sys.stdin); assert d['hookSpecificOutput']['hookEventName']=='SessionStart'; print('Format OK')"`
Expected: `Format OK`

**Step 5: Commit**

```bash
git add hooks/security-guard-check.sh
git commit -m "feat(security-guard): add SessionStart conflict detection hook

Uses additionalContext format matching existing session-start hook.
Checks once per session if the official security-guidance plugin
is still enabled."
```

---

### Task 6: Update hooks.json to register both hooks

**Files:**
- Modify: `hooks/hooks.json`

**Step 1: Add SecurityGuard PreToolUse entry**

Add to the `PreToolUse` array (after the wal-pre entry):

```json
{
  "matcher": "Edit|Write|MultiEdit|NotebookEdit",
  "hooks": [
    {
      "type": "command",
      "command": "'${CLAUDE_PLUGIN_ROOT}/hooks/security-guard.py'",
      "timeout": 5
    }
  ]
}
```

**Step 2: Add SessionStart entry**

Add to the `SessionStart` array (after the existing session-start entry). Uses `startup|resume` only (not `clear|compact`) because the conflict warning only needs to appear once per real session start:

```json
{
  "matcher": "startup|resume",
  "hooks": [
    {
      "type": "command",
      "command": "'${CLAUDE_PLUGIN_ROOT}/hooks/security-guard-check.sh'",
      "timeout": 3
    }
  ]
}
```

**Step 3: Validate JSON**

Run: `cd $PLUGIN_ROOT && python3 -c "import json; json.load(open('hooks/hooks.json')); print('Valid JSON')"`
Expected: `Valid JSON`

**Step 4: Verify all matchers**

Run: `python3 -c "import json; h=json.load(open('hooks/hooks.json')); print('PreToolUse:', [m['matcher'] for m in h['hooks']['PreToolUse']]); print('SessionStart:', [m['matcher'] for m in h['hooks']['SessionStart']])"`
Expected: PreToolUse has 3 entries; SessionStart has 2 entries

**Step 5: Commit**

```bash
git add hooks/hooks.json
git commit -m "feat(security-guard): register hooks in hooks.json

Adds PreToolUse hook for Edit|Write|MultiEdit|NotebookEdit and
SessionStart hook (startup|resume only) for conflict detection."
```

---

### Task 7: Create the sync-security-patterns skill

**Files:**
- Create: `skills/sync-security-patterns/SKILL.md`

**Step 1: Create the skill**

Create `skills/sync-security-patterns/SKILL.md`:

```markdown
---
name: sync-security-patterns
description: Sync security patterns from the official security-guidance plugin into rawgentic's security-patterns.json. Use when the official plugin has been updated and you want to pull in new patterns.
---

# Sync Security Patterns

Merge the latest patterns from Anthropic's `security-guidance` plugin into rawgentic's `hooks/security-patterns.json`.

## Steps

1. Read the official plugin's pattern definitions from:

   ```
   ~/.claude/plugins/marketplaces/claude-plugins-official/plugins/security-guidance/hooks/security_reminder_hook.py
   ```

   Look for the `SECURITY_PATTERNS` list. Extract each entry's `ruleName`, `substrings`, and `reminder`.

2. Read the current `hooks/security-patterns.json` from the rawgentic plugin at `${CLAUDE_PLUGIN_ROOT}/hooks/security-patterns.json`.

3. For each pattern from the official plugin:
   - If a pattern with the same `ruleName` exists AND has `"source": "upstream"`: update its `substrings` and `reminder` fields (preserve `wordBoundary`, `suggestedGlobs`, and `type`)
   - If a pattern with the same `ruleName` exists AND has `"source": "custom"`: **skip it** (user owns this pattern)
   - If no pattern with that `ruleName` exists: add it with `"source": "upstream"`, `"wordBoundary": false`, `"type": "substring"`, and empty `"suggestedGlobs"`

4. Check for upstream patterns in `security-patterns.json` that are no longer in the official plugin. Flag these for user review but do NOT auto-delete them.

5. Write the updated `hooks/security-patterns.json`.

6. Report:
   - **Added:** new patterns from upstream
   - **Updated:** existing upstream patterns with changed reminders/substrings
   - **Unchanged:** patterns that were already in sync
   - **Preserved:** custom patterns that were not touched
   - **Flagged:** upstream patterns no longer in the official plugin (ask user whether to keep or remove)

7. Commit the change with message: `chore(security-guard): sync patterns from security-guidance`
```

**Step 2: Commit**

```bash
git add skills/sync-security-patterns/SKILL.md
git commit -m "feat(security-guard): add sync-security-patterns skill

Skill for merging upstream pattern updates from Anthropic's
security-guidance plugin into rawgentic's security-patterns.json.
Respects source field to preserve custom patterns."
```

---

### Task 8: End-to-end integration tests

**Files:**
- Create: `tests/hooks/test_security_guard_e2e.py`

**Step 1: Write E2E tests**

Create `tests/hooks/test_security_guard_e2e.py`:

```python
#!/usr/bin/env python3
"""End-to-end tests for security-guard.py hook.

Invokes the hook as a subprocess with simulated stdin,
just like Claude Code does.
"""
import json
import os
import subprocess
import tempfile
import pytest
from pathlib import Path


HOOK_SCRIPT = str(Path(__file__).resolve().parent.parent.parent / "hooks" / "security-guard.py")


def run_hook(tool_name, tool_input):
    """Run security-guard.py with given input. Returns (exit_code, stdout_parsed, stderr)."""
    input_data = json.dumps({
        "tool_name": tool_name,
        "tool_input": tool_input,
        "session_id": "test-session",
    })
    result = subprocess.run(
        ["python3", HOOK_SCRIPT],
        input=input_data,
        capture_output=True,
        text=True,
        timeout=10,
    )
    stdout_parsed = None
    if result.stdout.strip():
        try:
            stdout_parsed = json.loads(result.stdout)
        except json.JSONDecodeError:
            pass
    return result.returncode, stdout_parsed, result.stderr


class TestE2EAllow:
    def test_safe_content_allowed(self):
        code, out, _ = run_hook("Write", {
            "file_path": "/tmp/test/src/app.js",
            "content": "console.log('hello')",
        })
        assert code == 0
        assert out is None

    def test_no_file_path_allowed(self):
        code, out, _ = run_hook("Write", {"content": "eval(x)"})
        assert code == 0
        assert out is None

    def test_unknown_tool_allowed(self):
        code, out, _ = run_hook("Bash", {"command": "echo eval(x)"})
        assert code == 0
        assert out is None


class TestE2EDeny:
    def test_eval_blocked(self):
        code, out, _ = run_hook("Write", {
            "file_path": "/tmp/test/src/app.js",
            "content": "x = eval(code)",
        })
        assert code == 0
        assert out is not None
        assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert "eval_injection" in out["systemMessage"]

    def test_innerhtml_blocked(self):
        code, out, _ = run_hook("Write", {
            "file_path": "/tmp/test/src/app.js",
            "content": 'el.innerHTML = "<b>hi</b>"',
        })
        assert code == 0
        assert out is not None
        assert out["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_medieval_not_blocked(self):
        code, out, _ = run_hook("Write", {
            "file_path": "/tmp/test/src/game.js",
            "content": "medieval(castle)",
        })
        assert code == 0
        assert out is None

    def test_edit_only_checks_new_string(self):
        """Removing eval should be allowed."""
        code, out, _ = run_hook("Edit", {
            "file_path": "/tmp/test/src/app.js",
            "old_string": "x = eval(code)",
            "new_string": "x = JSON.parse(code)",
        })
        assert code == 0
        assert out is None

    def test_notebook_edit_blocked(self):
        code, out, _ = run_hook("NotebookEdit", {
            "notebook_path": "/tmp/test/nb.ipynb",
            "new_source": "import pickle\npickle.loads(data)",
        })
        assert code == 0
        assert out is not None
        assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


class TestE2EExceptions:
    def test_exception_allows_blocked_pattern(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = {
                "securityExceptions": [{
                    "rule": "eval_injection",
                    "pathPattern": "**/__tests__/**",
                    "addedBy": "user",
                    "date": "2026-03-07",
                }]
            }
            with open(os.path.join(tmpdir, ".rawgentic.json"), "w") as f:
                json.dump(config, f)

            test_dir = os.path.join(tmpdir, "src", "__tests__")
            os.makedirs(test_dir)
            test_file = os.path.join(test_dir, "foo.test.js")

            code, out, _ = run_hook("Write", {
                "file_path": test_file,
                "content": "eval(testCode)",
            })
            assert code == 0
            assert out is None  # allowed by exception

    def test_exception_does_not_apply_to_wrong_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = {
                "securityExceptions": [{
                    "rule": "eval_injection",
                    "pathPattern": "**/__tests__/**",
                    "addedBy": "user",
                    "date": "2026-03-07",
                }]
            }
            with open(os.path.join(tmpdir, ".rawgentic.json"), "w") as f:
                json.dump(config, f)

            src_dir = os.path.join(tmpdir, "src")
            os.makedirs(src_dir)
            src_file = os.path.join(src_dir, "app.js")

            code, out, _ = run_hook("Write", {
                "file_path": src_file,
                "content": "eval(userInput)",
            })
            assert code == 0
            assert out is not None
            assert out["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_rawgentic_json_without_exceptions_key(self):
        """rawgentic.json with no securityExceptions key -> all patterns block."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with open(os.path.join(tmpdir, ".rawgentic.json"), "w") as f:
                json.dump({"project": "test"}, f)

            src_dir = os.path.join(tmpdir, "src")
            os.makedirs(src_dir)

            code, out, _ = run_hook("Write", {
                "file_path": os.path.join(src_dir, "app.js"),
                "content": "eval(x)",
            })
            assert code == 0
            assert out is not None
            assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


class TestE2EErrorHandling:
    def test_malformed_stdin(self):
        result = subprocess.run(
            ["python3", HOOK_SCRIPT],
            input="not json at all",
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0

    def test_empty_stdin(self):
        result = subprocess.run(
            ["python3", HOOK_SCRIPT],
            input="",
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0
```

**Step 2: Run all tests**

Run: `cd $PLUGIN_ROOT && python3 -m pytest tests/hooks/ -v 2>&1 | tail -80`
Expected: All tests PASS

**Step 3: Commit**

```bash
git add tests/hooks/test_security_guard_e2e.py
git commit -m "test(security-guard): add end-to-end integration tests

Subprocess-based tests covering: allow/deny decisions, word boundary
(medieval), Edit old_string bypass, NotebookEdit, exceptions with
temp .rawgentic.json, missing securityExceptions key, and error handling."
```

---

### Task 9: Final verification

**Step 1: Run full test suite**

Run: `cd $PLUGIN_ROOT && python3 -m pytest tests/hooks/ -v --tb=short`
Expected: All PASS

**Step 2: Verify hooks.json**

Run: `python3 -c "import json; h=json.load(open('hooks/hooks.json')); print('PreToolUse:', [m['matcher'] for m in h['hooks']['PreToolUse']]); print('SessionStart:', [m['matcher'] for m in h['hooks']['SessionStart']])"`
Expected: PreToolUse has 3 entries including `Edit|Write|MultiEdit|NotebookEdit`; SessionStart has 2 entries

**Step 3: Verify executables**

Run: `ls -la hooks/security-guard.py hooks/security-guard-check.sh | awk '{print $1, $NF}'`
Expected: Both show executable permissions

**Step 4: Test deny flow**

Run: `echo '{"tool_name":"Write","tool_input":{"file_path":"/tmp/x/src/app.js","content":"eval(x)"}}' | python3 hooks/security-guard.py | python3 -m json.tool`
Expected: Formatted JSON with `permissionDecision: deny`

**Step 5: Test conflict detection**

Run: `bash hooks/security-guard-check.sh | python3 -m json.tool`
Expected: JSON with `additionalContext` warning about disabling official plugin

**Step 6: Clean git status**

Run: `git status -s`
Expected: No uncommitted changes
