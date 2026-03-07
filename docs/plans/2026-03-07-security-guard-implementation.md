# Security Guard Hook Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Implement a PreToolUse security hook in the rawgentic plugin that hard-blocks dangerous code patterns with per-project exception lists, replacing the broken official security-guidance plugin.

**Architecture:** A Python PreToolUse hook (`security-guard.py`) checks file writes against externalized patterns (`security-patterns.json`), matches exceptions from `.rawgentic.json`, and outputs JSON deny decisions via stdout. A separate SessionStart bash hook (`security-guard-check.sh`) detects if the official plugin is still enabled. All path matching uses `fnmatch` with a dummy-prefix trick to handle root-level `**` patterns.

**Tech Stack:** Python 3.13 (stdlib only: json, sys, os, fnmatch, pathlib, re), Bash, pytest

**Design doc:** `docs/plans/2026-03-07-security-guard-design.md`

---

### Task 1: Create test infrastructure and pure utility functions

**Files:**
- Create: `tests/hooks/test_security_guard.py`
- Create: `hooks/security_guard_lib.py`

**Step 1: Create test file with tests for pure utility functions**

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
    sanitize_path_for_message,
    suggest_glob,
    check_word_boundary,
)


class TestGlobMatch:
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


class TestNormalizePath:
    def test_strips_project_root(self):
        assert normalize_path("/home/user/project/src/foo.js", "/home/user/project") == "src/foo.js"

    def test_strips_trailing_slash(self):
        assert normalize_path("/home/user/project/src/foo.js", "/home/user/project/") == "src/foo.js"

    def test_root_level_file(self):
        assert normalize_path("/home/user/project/foo.js", "/home/user/project") == "foo.js"

    def test_no_project_root_returns_abs_path(self):
        assert normalize_path("/home/user/project/src/foo.js", None) == "/home/user/project/src/foo.js"


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


class TestCheckWordBoundary:
    def test_eval_standalone(self):
        assert check_word_boundary("x = eval(code)", "eval(") is True

    def test_eval_at_start(self):
        assert check_word_boundary("eval(code)", "eval(") is True

    def test_medieval_false_positive(self):
        assert check_word_boundary("medieval(castle)", "eval(") is False

    def test_pickle_dot(self):
        assert check_word_boundary("import pickle.", "pickle.") is True

    def test_pickled_data(self):
        assert check_word_boundary("pickled_data = 1", "pickle") is False

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

Run: `cd $PLUGIN_ROOT && python3 -m pytest tests/hooks/test_security_guard.py -v 2>&1 | tail -20`
Expected: FAIL with `ModuleNotFoundError: No module named 'security_guard_lib'`

**Step 3: Implement security_guard_lib.py with all pure functions**

Create `hooks/security_guard_lib.py`:

```python
#!/usr/bin/env python3
"""Pure utility functions for the security-guard hook.

All functions in this module are pure (no I/O) for easy testing.
I/O operations live in security-guard.py (the main hook script).
"""
import fnmatch
import json
import re


def glob_match(path_str, pattern):
    """Match a relative path against a glob pattern.

    Uses fnmatch with a dummy prefix to handle root-level ** patterns.
    On Linux, fnmatch treats * as matching /, so ** effectively works
    as recursive glob.
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

    Only checks new/written content, not content being replaced.
    """
    if tool_name == "Write":
        return tool_input.get("content", "")
    elif tool_name == "Edit":
        return tool_input.get("new_string", "")
    elif tool_name == "MultiEdit":
        edits = tool_input.get("edits", [])
        return " ".join(e.get("new_string", "") for e in edits)
    elif tool_name == "NotebookEdit":
        return tool_input.get("new_source", "")
    return ""


def check_word_boundary(content, substring):
    """Check if substring appears with a non-alpha char before it.

    Prevents matches like medieval( for the eval( pattern.
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
    """Sanitize a file path for inclusion in systemMessage."""
    sanitized = re.sub(r"[^a-zA-Z0-9._/\-]", "", path_str)
    return sanitized[:200]


def suggest_glob(rel_path):
    """Suggest an exception glob pattern for a file path."""
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
            continue
    return matches


def filter_exceptions(matches, exceptions, rel_path):
    """Remove matches that have a corresponding exception."""
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
    """Format the deny JSON output for unexcepted pattern matches."""
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

Run: `cd $PLUGIN_ROOT && python3 -m pytest tests/hooks/test_security_guard.py -v 2>&1 | tail -40`
Expected: All tests PASS

**Step 5: Commit**

```bash
git add tests/hooks/test_security_guard.py hooks/security_guard_lib.py
git commit -m "feat(security-guard): add pure utility functions with tests

Implements glob_match, normalize_path, extract_content, check_word_boundary,
sanitize_path_for_message, suggest_glob, match_patterns, filter_exceptions,
and format_deny. All pure functions with no I/O dependencies."
```

---

### Task 2: Add pattern matching and exception integration tests

**Files:**
- Modify: `tests/hooks/test_security_guard.py`

**Step 1: Append integration tests to the test file**

Add to end of `tests/hooks/test_security_guard.py`:

```python
from security_guard_lib import match_patterns, filter_exceptions, format_deny


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
```

**Step 2: Run tests to verify they pass**

Run: `cd $PLUGIN_ROOT && python3 -m pytest tests/hooks/test_security_guard.py -v 2>&1 | tail -50`
Expected: All tests PASS

**Step 3: Commit**

```bash
git add tests/hooks/test_security_guard.py
git commit -m "test(security-guard): add pattern matching and exception integration tests"
```

---

### Task 3: Create security-patterns.json seed data

**Files:**
- Create: `hooks/security-patterns.json`

**Step 1: Create the patterns file**

Create `hooks/security-patterns.json` with the full pattern set from the design doc. See the design doc "Patterns (initial seed)" table for the 10 patterns including both `.yml` and `.yaml` workflow variants. Each pattern includes `ruleName`, `source`, `type`, `substrings`/`pathPattern`, `wordBoundary`, `reminder`, and `suggestedGlobs`.

Note: Use the exact reminder text from Anthropic's official `security_reminder_hook.py` for upstream patterns. The `child_process_exec` pattern should NOT include bare `exec(` (removed to avoid matching Python's `exec()` in non-Node contexts) -- only `child_process.exec` and `execSync(`.

**Step 2: Validate the JSON**

Run: `python3 -c "import json; d=json.load(open('hooks/security-patterns.json')); print(f'{len(d)} patterns loaded'); [print(f'  {p[\"ruleName\"]}') for p in d]"`
Expected: `10 patterns loaded` with all rule names

**Step 3: Commit**

```bash
git add hooks/security-patterns.json
git commit -m "feat(security-guard): add security patterns seed data

10 patterns seeded from Anthropic's security-guidance plugin with
wordBoundary flags and source field for sync differentiation."
```

---

### Task 4: Create the main security-guard.py hook script

**Files:**
- Create: `hooks/security-guard.py`

**Step 1: Write the main hook script**

Create `hooks/security-guard.py` with shebang `#!/usr/bin/env python3`. The script:

1. Reads stdin JSON, extracts `tool_name` and `tool_input`
2. Gets `file_path` (or `notebook_path` for NotebookEdit)
3. Loads patterns from `SCRIPT_DIR / "security-patterns.json"`
4. Finds project root by walking up from file_path looking for `.rawgentic.json`
5. Normalizes file_path to project-relative
6. Extracts content via `extract_content()`
7. Runs `match_patterns()` -- if no matches, exit 0
8. Loads exceptions from `.rawgentic.json`
9. Runs `filter_exceptions()` -- if all excepted, exit 0
10. Outputs `format_deny()` JSON to stdout, exit 0

All errors -> exit 0 (fail-open).

Import `security_guard_lib` from same directory via `sys.path.insert(0, SCRIPT_DIR)`.

**Step 2: Make it executable**

Run: `chmod +x hooks/security-guard.py`

**Step 3: Test manually**

Run: `echo '{"tool_name":"Write","tool_input":{"file_path":"/tmp/test/src/app.js","content":"x = eval(code)"}}' | python3 hooks/security-guard.py`
Expected: JSON with `permissionDecision: deny`

Run: `echo '{"tool_name":"Write","tool_input":{"file_path":"/tmp/test/src/app.js","content":"console.log(1)"}}' | python3 hooks/security-guard.py`
Expected: No output (allowed)

**Step 4: Commit**

```bash
git add hooks/security-guard.py
git commit -m "feat(security-guard): add main PreToolUse hook script

Orchestrates pattern loading, path normalization, content extraction,
pattern matching, exception filtering, and deny output. Fail-open."
```

---

### Task 5: Create the SessionStart conflict detection hook

**Files:**
- Create: `hooks/security-guard-check.sh`

**Step 1: Write the SessionStart hook**

Create `hooks/security-guard-check.sh` -- a bash script that:
1. Reads `~/.claude/settings.json`
2. Uses python3 one-liner to check if `security-guidance@claude-plugins-official` is enabled
3. If yes: outputs JSON `{"systemMessage":"...disable instructions..."}` to stdout
4. If no or any error: exits 0 silently

**Step 2: Make executable**

Run: `chmod +x hooks/security-guard-check.sh`

**Step 3: Test it**

Run: `bash hooks/security-guard-check.sh`
Expected: JSON warning (since the official plugin is currently enabled)

**Step 4: Commit**

```bash
git add hooks/security-guard-check.sh
git commit -m "feat(security-guard): add SessionStart conflict detection hook"
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

Add to the `SessionStart` array:

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

Run: `python3 -c "import json; json.load(open('hooks/hooks.json')); print('Valid JSON')"`
Expected: `Valid JSON`

**Step 4: Commit**

```bash
git add hooks/hooks.json
git commit -m "feat(security-guard): register hooks in hooks.json

Adds PreToolUse hook for Edit|Write|MultiEdit|NotebookEdit and
SessionStart hook for conflict detection."
```

---

### Task 7: Create the sync-security-patterns skill

**Files:**
- Create: `skills/sync-security-patterns/SKILL.md`

**Step 1: Create the skill markdown**

The skill instructs Claude to:
1. Read the official plugin's `security_reminder_hook.py`
2. Extract `SECURITY_PATTERNS` entries
3. Merge into `hooks/security-patterns.json` respecting the `source` field
4. Report changes
5. Commit

**Step 2: Commit**

```bash
git add skills/sync-security-patterns/SKILL.md
git commit -m "feat(security-guard): add sync-security-patterns skill"
```

---

### Task 8: End-to-end integration tests

**Files:**
- Create: `tests/hooks/test_security_guard_e2e.py`

**Step 1: Write E2E tests**

Create `tests/hooks/test_security_guard_e2e.py` that:
- Runs `hooks/security-guard.py` as a subprocess with `subprocess.run()`
- Tests: safe content allowed, dangerous content blocked, word boundary (medieval), Edit old_string not checked, exceptions with temp `.rawgentic.json` in `tempfile.TemporaryDirectory`, malformed stdin handled, empty stdin handled

**Step 2: Run all tests**

Run: `python3 -m pytest tests/hooks/ -v 2>&1 | tail -60`
Expected: All tests PASS

**Step 3: Commit**

```bash
git add tests/hooks/test_security_guard_e2e.py
git commit -m "test(security-guard): add end-to-end integration tests"
```

---

### Task 9: Final verification

**Step 1: Run full test suite**

Run: `python3 -m pytest tests/hooks/ -v --tb=short`
Expected: All PASS

**Step 2: Verify hooks.json**

Run: `python3 -c "import json; h=json.load(open('hooks/hooks.json')); print([m['matcher'] for m in h['hooks']['PreToolUse']]); print([m['matcher'] for m in h['hooks']['SessionStart']])"`
Expected: PreToolUse has 3 entries including `Edit|Write|MultiEdit|NotebookEdit`; SessionStart has 2 entries

**Step 3: Verify executables**

Run: `ls -la hooks/security-guard.py hooks/security-guard-check.sh | awk '{print $1, $NF}'`
Expected: Both executable

**Step 4: Test deny flow**

Run: `echo '{"tool_name":"Write","tool_input":{"file_path":"/tmp/x/src/app.js","content":"eval(x)"}}' | python3 hooks/security-guard.py | python3 -m json.tool`
Expected: Formatted JSON with `permissionDecision: deny`

**Step 5: Test conflict detection**

Run: `bash hooks/security-guard-check.sh`
Expected: JSON warning about disabling official plugin

**Step 6: Clean git status**

Run: `git status -s`
Expected: No uncommitted changes
