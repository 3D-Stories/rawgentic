# Testing

## Running Tests

Prerequisites: Python 3.10+, pytest.

```bash
pytest tests/
```

Tests use `sys.path.insert` to import directly from `hooks/`, so no package
installation is required. Run from the repository root.

## Security Guard Tests

The security guard is the only hook with tests so far. It has 78 tests split
across unit and end-to-end suites.

### Unit Tests (`tests/hooks/test_security_guard.py`)

These test pure functions exported from `hooks/security_guard_lib.py`:

| Class | What it tests |
|---|---|
| `TestGlobMatch` | `glob_match()` -- `**` patterns including root-level, nested, and non-matching paths |
| `TestNormalizePath` | `normalize_path()` -- stripping project root prefix, handling trailing slashes, paths outside project |
| `TestExtractContent` | `extract_content()` -- extracting writeable content from Write, Edit, MultiEdit, and NotebookEdit tool inputs |
| `TestCheckWordBoundary` | `check_word_boundary()` -- ensuring substring matches respect word boundaries (e.g., `eval` matches standalone but not inside `medieval`) |
| `TestSanitizePathForMessage` | `sanitize_path_for_message()` -- stripping shell-dangerous characters, truncating long paths |
| `TestSuggestGlob` | `suggest_glob()` -- inferring exception glob patterns from file paths (test dirs, workflow files, fallback) |
| `TestMatchPatterns` | `match_patterns()` -- full pattern-matching pipeline: substring rules, path rules, word boundaries, multiple matches, malformed patterns |
| `TestFilterExceptions` | `filter_exceptions()` -- removing matches covered by `.rawgentic.json` exceptions, verifying rule+path specificity |
| `TestFormatDeny` | `format_deny()` -- output structure (JSON shape, `permissionDecision`, aggregated messages, path sanitization) |

### E2E Tests (`tests/hooks/test_security_guard_e2e.py`)

These invoke `hooks/security-guard.py` as a subprocess with JSON on stdin,
exactly as Claude Code does at runtime.

| Class | What it tests |
|---|---|
| `TestE2EAllow` | Safe content passes through; missing `file_path` and unknown tools are allowed |
| `TestE2EDeny` | Dangerous patterns are blocked; word-boundary false positives are not; Edit checks only `new_string` |
| `TestE2EExceptions` | `.rawgentic.json` exceptions allow otherwise-blocked patterns for matching paths, and do not apply to wrong paths or missing keys |
| `TestE2EErrorHandling` | Malformed and empty stdin do not crash the hook (exit 0 in all cases) |

## Tests to Be Implemented

The following hooks have no automated tests yet.

### WAL Hooks

`wal-pre`, `wal-post`, `wal-post-fail`, `wal-stop` -- Verify that each hook
writes the correct JSONL entry (event type, timestamp, tool name, status) to
the active WAL file.

### Session Management

`wal-context` -- Test context injection for bound sessions, unbound sessions,
and the multi-active-session conflict case.

`wal-bind-guard` -- Test that file writes outside the bound project directory
are blocked and that writes inside the directory are allowed.

`wal-guard` -- Test detection of dangerous bash commands (e.g., destructive
operations like forced pushes or recursive deletes) and that safe commands
pass through.

`session-start` -- Test WAL recovery from incomplete entries, log rotation
when the WAL exceeds size thresholds, reconciliation of orphaned sessions,
and archival of completed session logs.

### Security

`security-guard-check` -- Test conflict detection when the official Claude
Code security-guard plugin is installed alongside rawgentic's version.

See [issue #9](https://github.com/3D-Stories/rawgentic/issues/9) for the full test suite implementation plan.
