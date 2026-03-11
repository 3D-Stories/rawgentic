#!/usr/bin/env python3
"""Tests for security-guard hook pure functions."""
import pytest
import json
import os
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
    filter_by_exclude_paths,
    format_deny,
    load_protection_config,
    SECURITY_PRESETS,
)
from tests.hooks.conftest import run_hook, parse_hook_output


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


class TestLoadProtectionConfig:
    """Tests for load_protection_config() resolution logic."""

    def test_no_project_root_returns_strict(self):
        """No project root -> strict, all rules active."""
        level, rules, exclude, has_new = load_protection_config(None)
        assert level == "strict"
        assert rules is None
        assert exclude is None
        assert has_new is False

    def test_missing_rawgentic_json(self, tmp_path):
        """No .rawgentic.json file -> strict fallback."""
        level, rules, exclude, has_new = load_protection_config(str(tmp_path))
        assert level == "strict"
        assert rules is None
        assert exclude is None
        assert has_new is False

    def test_malformed_json(self, tmp_path):
        """Malformed .rawgentic.json -> strict fallback."""
        (tmp_path / ".rawgentic.json").write_text("{bad json")
        level, rules, exclude, has_new = load_protection_config(str(tmp_path))
        assert level == "strict"
        assert rules is None
        assert exclude is None
        assert has_new is False

    def test_protection_level_sandbox(self, tmp_path):
        """protectionLevel: sandbox -> no rules active."""
        (tmp_path / ".rawgentic.json").write_text(json.dumps({
            "protectionLevel": "sandbox"
        }))
        level, rules, exclude, has_new = load_protection_config(str(tmp_path))
        assert level == "sandbox"
        assert rules == set()
        assert exclude is None
        assert has_new is True

    def test_protection_level_standard(self, tmp_path):
        """protectionLevel: standard -> curated 6-element set."""
        (tmp_path / ".rawgentic.json").write_text(json.dumps({
            "protectionLevel": "standard"
        }))
        level, rules, exclude, has_new = load_protection_config(str(tmp_path))
        assert level == "standard"
        assert isinstance(rules, set)
        assert len(rules) == 6
        assert "eval_injection" in rules
        assert "innerHTML_xss" in rules
        assert has_new is True

    def test_protection_level_strict(self, tmp_path):
        """protectionLevel: strict -> all rules active (None)."""
        (tmp_path / ".rawgentic.json").write_text(json.dumps({
            "protectionLevel": "strict"
        }))
        level, rules, exclude, has_new = load_protection_config(str(tmp_path))
        assert level == "strict"
        assert rules is None
        assert exclude is None
        assert has_new is True

    def test_protection_level_invalid(self, tmp_path):
        """Invalid protectionLevel -> strict fallback."""
        (tmp_path / ".rawgentic.json").write_text(json.dumps({
            "protectionLevel": "turbo"
        }))
        level, rules, exclude, has_new = load_protection_config(str(tmp_path))
        assert level == "strict"
        assert rules is None
        assert has_new is True

    def test_explicit_guards_security_list(self, tmp_path):
        """Explicit guards.security list -> custom level with exact rules."""
        (tmp_path / ".rawgentic.json").write_text(json.dumps({
            "guards": {"security": ["eval_injection", "innerHTML_xss"]}
        }))
        level, rules, exclude, has_new = load_protection_config(str(tmp_path))
        assert level == "custom"
        assert rules == {"eval_injection", "innerHTML_xss"}
        assert has_new is True

    def test_guards_security_overrides_protection_level(self, tmp_path):
        """guards.security takes precedence over protectionLevel."""
        (tmp_path / ".rawgentic.json").write_text(json.dumps({
            "protectionLevel": "sandbox",
            "guards": {"security": ["eval_injection"]}
        }))
        level, rules, exclude, has_new = load_protection_config(str(tmp_path))
        assert level == "custom"
        assert rules == {"eval_injection"}

    def test_exclude_paths_extracted(self, tmp_path):
        """securityExcludePaths is extracted from guards."""
        (tmp_path / ".rawgentic.json").write_text(json.dumps({
            "protectionLevel": "standard",
            "guards": {"securityExcludePaths": ["tests/**", "**/*.test.js"]}
        }))
        level, rules, exclude, has_new = load_protection_config(str(tmp_path))
        assert level == "standard"
        assert exclude == ["tests/**", "**/*.test.js"]
        assert has_new is True

    def test_workspace_default_protection_level_fallback(self, tmp_path):
        """Workspace defaultProtectionLevel is used when project has no config."""
        proj_dir = tmp_path / "proj"
        proj_dir.mkdir()
        (proj_dir / ".rawgentic.json").write_text(json.dumps({}))

        ws_dir = tmp_path / "ws"
        ws_dir.mkdir()
        (ws_dir / ".rawgentic_workspace.json").write_text(json.dumps({
            "defaultProtectionLevel": "standard"
        }))

        level, rules, exclude, has_new = load_protection_config(
            str(proj_dir), str(ws_dir)
        )
        assert level == "standard"
        assert isinstance(rules, set)
        assert len(rules) == 6
        assert has_new is False

    def test_workspace_fallback_not_used_when_project_has_config(self, tmp_path):
        """Workspace fallback is skipped when project has protectionLevel."""
        proj_dir = tmp_path / "proj"
        proj_dir.mkdir()
        (proj_dir / ".rawgentic.json").write_text(json.dumps({
            "protectionLevel": "sandbox"
        }))

        ws_dir = tmp_path / "ws"
        ws_dir.mkdir()
        (ws_dir / ".rawgentic_workspace.json").write_text(json.dumps({
            "defaultProtectionLevel": "strict"
        }))

        level, rules, exclude, has_new = load_protection_config(
            str(proj_dir), str(ws_dir)
        )
        assert level == "sandbox"
        assert rules == set()

    def test_workspace_missing_file_falls_through(self, tmp_path):
        """Missing workspace config file -> strict default."""
        proj_dir = tmp_path / "proj"
        proj_dir.mkdir()
        (proj_dir / ".rawgentic.json").write_text(json.dumps({}))

        level, rules, exclude, has_new = load_protection_config(
            str(proj_dir), str(tmp_path / "nonexistent")
        )
        assert level == "strict"
        assert rules is None
        assert has_new is False

    def test_empty_guards_dict_no_protection_level(self, tmp_path):
        """Empty guards dict without protectionLevel -> strict default.

        An empty guards dict is falsy so has_new_config is False.
        """
        (tmp_path / ".rawgentic.json").write_text(json.dumps({
            "guards": {}
        }))
        level, rules, exclude, has_new = load_protection_config(str(tmp_path))
        assert level == "strict"
        assert rules is None
        assert has_new is False


class TestFilterByExcludePaths:
    """Tests for filter_by_exclude_paths()."""

    SAMPLE_MATCHES = [
        {"ruleName": "eval_injection", "reminder": "eval bad."},
        {"ruleName": "innerHTML_xss", "reminder": "innerHTML bad."},
    ]

    def test_none_exclude_paths_returns_unchanged(self):
        """None exclude_paths -> matches unchanged."""
        result = filter_by_exclude_paths(self.SAMPLE_MATCHES, None, "src/app.js")
        assert result == self.SAMPLE_MATCHES

    def test_empty_list_returns_unchanged(self):
        """Empty list -> matches unchanged."""
        result = filter_by_exclude_paths(self.SAMPLE_MATCHES, [], "src/app.js")
        assert result == self.SAMPLE_MATCHES

    def test_matching_pattern_returns_empty(self):
        """File matching an exclude pattern -> empty list."""
        result = filter_by_exclude_paths(
            self.SAMPLE_MATCHES, ["tests/**"], "tests/foo.js"
        )
        assert result == []

    def test_non_matching_pattern_returns_unchanged(self):
        """File not matching any exclude pattern -> matches unchanged."""
        result = filter_by_exclude_paths(
            self.SAMPLE_MATCHES, ["tests/**"], "src/app.js"
        )
        assert result == self.SAMPLE_MATCHES

    def test_multiple_patterns_any_match_excludes(self):
        """If any exclude pattern matches, file is excluded."""
        result = filter_by_exclude_paths(
            self.SAMPLE_MATCHES,
            ["docs/**", "tests/**"],
            "tests/unit/foo.test.js",
        )
        assert result == []

    def test_doublestar_pattern(self):
        """Double-star pattern matches nested files."""
        result = filter_by_exclude_paths(
            self.SAMPLE_MATCHES,
            ["**/*.test.js"],
            "src/utils/helper.test.js",
        )
        assert result == []


class TestSecurityGuardIntegration:
    """Integration tests running security-guard.py as a subprocess.

    Uses run_hook() to test the full flow with different protection levels.
    """

    def _make_write_payload(self, file_path, content):
        """Build a Write tool stdin payload."""
        return {
            "tool_name": "Write",
            "tool_input": {
                "file_path": file_path,
                "content": content,
            },
        }

    def test_sandbox_allows_eval(self, make_workspace):
        """Sandbox level: writing eval( is ALLOWED."""
        ws = make_workspace(
            project_configs={"testproj": {"protectionLevel": "sandbox"}}
        )
        proj_dir = ws.root / "projects" / "testproj"
        file_path = str(proj_dir / "src" / "app.js")
        payload = self._make_write_payload(file_path, "x = eval(code)")

        stdout, stderr, rc = run_hook("security-guard.py", payload, cwd=ws.root)
        output = parse_hook_output(stdout)
        # Sandbox allows everything -> no deny
        assert output is None or "permissionDecision" not in output.get("hookSpecificOutput", {})

    def test_standard_blocks_eval(self, make_workspace):
        """Standard level: writing eval( is BLOCKED."""
        ws = make_workspace(
            project_configs={"testproj": {"protectionLevel": "standard"}}
        )
        proj_dir = ws.root / "projects" / "testproj"
        file_path = str(proj_dir / "src" / "app.js")
        payload = self._make_write_payload(file_path, "x = eval(code)")

        stdout, stderr, rc = run_hook("security-guard.py", payload, cwd=ws.root)
        output = parse_hook_output(stdout)
        assert output is not None
        assert output["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_standard_allows_child_process_exec(self, make_workspace):
        """Standard level: child_process.exec is NOT in standard set -> ALLOWED."""
        ws = make_workspace(
            project_configs={"testproj": {"protectionLevel": "standard"}}
        )
        proj_dir = ws.root / "projects" / "testproj"
        file_path = str(proj_dir / "src" / "app.js")
        payload = self._make_write_payload(file_path, "child_process.exec('ls')")

        stdout, stderr, rc = run_hook("security-guard.py", payload, cwd=ws.root)
        output = parse_hook_output(stdout)
        assert output is None or "permissionDecision" not in output.get("hookSpecificOutput", {})

    def test_strict_blocks_eval(self, make_workspace):
        """Strict level: writing eval( is BLOCKED."""
        ws = make_workspace(
            project_configs={"testproj": {"protectionLevel": "strict"}}
        )
        proj_dir = ws.root / "projects" / "testproj"
        file_path = str(proj_dir / "src" / "app.js")
        payload = self._make_write_payload(file_path, "x = eval(code)")

        stdout, stderr, rc = run_hook("security-guard.py", payload, cwd=ws.root)
        output = parse_hook_output(stdout)
        assert output is not None
        assert output["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_strict_blocks_child_process_exec(self, make_workspace):
        """Strict level: child_process.exec is BLOCKED."""
        ws = make_workspace(
            project_configs={"testproj": {"protectionLevel": "strict"}}
        )
        proj_dir = ws.root / "projects" / "testproj"
        file_path = str(proj_dir / "src" / "app.js")
        payload = self._make_write_payload(file_path, "child_process.exec('ls')")

        stdout, stderr, rc = run_hook("security-guard.py", payload, cwd=ws.root)
        output = parse_hook_output(stdout)
        assert output is not None
        assert output["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_exclude_paths_allows_test_file(self, make_workspace):
        """securityExcludePaths: eval( in tests/foo.js with tests/** exclude -> ALLOWED."""
        ws = make_workspace(
            project_configs={
                "testproj": {
                    "protectionLevel": "strict",
                    "guards": {"securityExcludePaths": ["tests/**"]},
                }
            }
        )
        proj_dir = ws.root / "projects" / "testproj"
        file_path = str(proj_dir / "tests" / "foo.js")
        payload = self._make_write_payload(file_path, "x = eval(code)")

        stdout, stderr, rc = run_hook("security-guard.py", payload, cwd=ws.root)
        output = parse_hook_output(stdout)
        assert output is None or "permissionDecision" not in output.get("hookSpecificOutput", {})

    def test_backward_compat_security_exceptions(self, make_workspace):
        """No protectionLevel: securityExceptions still works."""
        ws = make_workspace(
            project_configs={
                "testproj": {
                    "securityExceptions": [
                        {"rule": "eval_injection", "pathPattern": "**/__tests__/**"}
                    ]
                }
            }
        )
        proj_dir = ws.root / "projects" / "testproj"
        file_path = str(proj_dir / "__tests__" / "foo.test.js")
        payload = self._make_write_payload(file_path, "x = eval(code)")

        stdout, stderr, rc = run_hook("security-guard.py", payload, cwd=ws.root)
        output = parse_hook_output(stdout)
        # Exception matches -> should allow
        assert output is None or "permissionDecision" not in output.get("hookSpecificOutput", {})

    def test_no_config_defaults_to_strict(self, make_workspace):
        """No .rawgentic.json at all -> strict (all blocked)."""
        ws = make_workspace()  # No project_configs -> no .rawgentic.json
        proj_dir = ws.root / "projects" / "testproj"
        # Remove .rawgentic.json if it exists
        config = proj_dir / ".rawgentic.json"
        if config.exists():
            config.unlink()
        # Create a file deep enough that find_project_root won't find config
        src_dir = proj_dir / "src"
        src_dir.mkdir(parents=True, exist_ok=True)
        file_path = str(src_dir / "app.js")
        payload = self._make_write_payload(file_path, "x = eval(code)")

        stdout, stderr, rc = run_hook("security-guard.py", payload, cwd=ws.root)
        output = parse_hook_output(stdout)
        # No config -> strict -> blocks eval
        # Note: find_project_root walks up looking for .rawgentic.json.
        # Without it, project_root is None -> strict default.
        assert output is not None
        assert output["hookSpecificOutput"]["permissionDecision"] == "deny"
