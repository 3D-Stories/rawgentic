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
from security_guard_lib import (
    check_word_boundary,
    sanitize_path_for_message,
    suggest_glob,
    match_patterns,
    filter_exceptions,
    format_deny,
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
