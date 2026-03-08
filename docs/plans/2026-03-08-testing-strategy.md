# Testing Strategy — Comprehensive Hook & Skill Test Suite

**Date:** 2026-03-08
**Issue:** #9
**Mode:** Coverage-gap (existing tests for security-guard + wal-guard)
**Scope:** Project-wide

## Current State

- **Tested:** security-guard pattern matching (78 tests: unit + e2e), wal-guard patterns (shell script)
- **Untested:** wal-pre, wal-post, wal-post-fail, wal-stop, wal-context, wal-bind-guard, session-start, security-guard-check
- **Skill evals:** 13/14 skills have evals.json via `/skill-creator`. Only `sync-security-patterns` missing.

## Approach: Subprocess Black-Box Testing

All hook tests invoke hooks as subprocesses via `subprocess.run()`, piping JSON to stdin and asserting on:
- **stdout** — JSON output (deny decisions, context injection) or empty (allow)
- **exit code** — 0 for all hooks except wal-guard (see note below)
- **filesystem side effects** — JSONL entries, session notes, file moves

**Note on error tolerance:** Most hooks are fail-open (exit 0, swallow errors via `2>/dev/null || true`). The exception is **wal-guard**, which is **fail-closed**: if jq is unavailable, it emits a deny decision. This critical security behavior must be tested, not skipped.

This matches the existing pattern in `test_security_guard_e2e.py` and keeps `pytest tests/` as the single entry point.

## Test File Structure

```
tests/
  hooks/
    conftest.py                    (shared fixtures — extended)
    test_security_guard.py         (existing — 78 tests)
    test_security_guard_e2e.py     (existing)
    test_wal_lib.py                (NEW — wal-lib.sh unit tests)
    test_wal_pre_post.py           (NEW — wal-pre, wal-post, wal-post-fail)
    test_wal_stop.py               (NEW)
    test_wal_context.py            (NEW)
    test_wal_bind_guard.py         (NEW)
    test_wal_guard.py              (NEW — pytest port of test_wal_guard.sh)
    test_session_start.py          (NEW)
    test_security_guard_check.py   (NEW)
```

**Migration note:** `tests/test_wal_guard.sh` is superseded by `tests/hooks/test_wal_guard.py`. Keep the shell script with a deprecation comment until the pytest port is validated in CI, then remove it in a follow-up PR.

## Shared Fixtures (conftest.py)

### `make_workspace` — Factory fixture for temp workspace directories

```python
@pytest.fixture
def make_workspace(tmp_path):
    """Factory that creates a temp workspace with configurable state.

    Usage:
        ws = make_workspace(
            projects=[
                {"name": "alpha", "path": "./projects/alpha", "active": True, "configured": True},
                {"name": "beta", "path": "./projects/beta", "active": True, "configured": True},
            ],
            registry_entries=[
                {"session_id": "sess-1", "project": "alpha", "project_path": "./projects/alpha"},
            ],
            wal_entries=[...],          # optional: pre-populate WAL JSONL
            session_notes={"alpha": "# Session Notes\n..."},  # optional
        )
        # ws.root — Path to workspace root
        # ws.workspace_json — Path to .rawgentic_workspace.json
        # ws.registry — Path to session_registry.jsonl
        # ws.wal_dir — Path to claude_docs/wal/
        # ws.notes_dir — Path to claude_docs/session_notes/
    """
```

Creates the full directory scaffold: `.rawgentic_workspace.json`, `claude_docs/session_registry.jsonl`, `claude_docs/wal/`, `claude_docs/session_notes/`, and project directories on disk.

### `run_hook` — Subprocess helper

```python
def run_hook(hook_name, stdin_dict, *, cwd=None, env_override=None, timeout=10):
    """Invoke a hook script via subprocess.

    - Determines interpreter from shebang (python3 for .py, bash for others)
    - Sets cwd in stdin_dict JSON if not already present
    - Passes env_override merged with current env (for PATH/HOME manipulation)
    - Returns (stdout: str, stderr: str, returncode: int)
    - stderr is ALWAYS captured (even though hooks swallow errors, the test
      harness should surface stderr in assertion failure messages for debugging)
    """
```

The `env_override` parameter enables jq-absent testing via PATH manipulation and HOME override for hooks that read `$HOME/.claude/settings.json`.

### `no_jq_env` — PATH manipulation fixture for jq-absent tests

```python
@pytest.fixture
def no_jq_env():
    """Returns env dict with PATH stripped of jq locations.

    Use with run_hook(env_override=no_jq_env) to test hooks' behavior
    when jq is unavailable. Do NOT skip these tests — hooks have explicit
    jq-absent behavior that must be verified:
    - wal-guard: DENY all (fail-closed)
    - wal-pre/post/post-fail: exit 0 silently (fail-open)
    - wal-bind-guard: exit 0 silently (fail-open)
    """
```

## Hook Test Coverage

### test_wal_lib.py — wal-lib.sh function unit tests

Sources wal-lib.sh in a bash subprocess and tests individual functions. Each test uses a shell harness pattern:

```bash
# Example: testing wal_parse_input
source "$SCRIPT_DIR/wal-lib.sh"
echo '{"tool_name":"Bash","session_id":"s1","tool_use_id":"tu1","cwd":"/tmp"}' | {
    WAL_INPUT=$(cat)
    # Re-source parse function logic with WAL_INPUT set
    WAL_TOOL_NAME=$(printf '%s' "$WAL_INPUT" | "$WAL_JQ" -r '.tool_name // "unknown"')
    echo "$WAL_TOOL_NAME"
}
```

Functions tested:
- `wal_parse_input`: correct extraction of tool_name, session_id, tool_use_id, cwd
- `wal_extract_summary`: correct summary for Bash, Edit, Write, NotebookEdit, Task, unknown tools
- `wal_resolve_project`: finds project from registry, returns empty when unbound
- `wal_find_workspace`: traverses up directories, stops at 5 levels, handles missing workspace
- `wal_init_file`: creates WAL dir, sets correct path, skips when no project
- `wal_append_phase`: writes correct JSON structure for INTENT vs DONE/FAIL

### test_wal_pre_post.py — WAL logging hooks

Black-box tests for wal-pre, wal-post, wal-post-fail (shared pattern):
- INTENT entry with summary + cwd on wal-pre
- DONE entry on wal-post
- FAIL entry on wal-post-fail
- Correct fields: ts, phase, session, tool, tool_use_id
- Unbound session writes nothing
- Missing jq gracefully exits 0 with no output (via `no_jq_env`)

### test_wal_stop.py — Session end marker

- Writes COMPLETE marker to session notes
- Writes STOP entry to per-project WAL
- Skips duplicate COMPLETE markers
- Falls back to first active project when registry miss
- Exits silently with missing workspace

### test_wal_context.py — Context injection (UserPromptSubmit)

- Bound session: emits additionalContext JSON with session info
- Unbound + single active: auto-binds and emits context
- Unbound + multi active: emits "use /rawgentic:switch" message
- Unbound + no active: emits "no active projects" message
- Writes .current_session_id file
- Includes recent WAL actions when available
- **Note:** wal-context reads from legacy `claude_docs/wal.jsonl`, not per-project WAL files. Tests should verify this path and document the discrepancy.

### test_wal_bind_guard.py — Cross-project write blocking

- Same-project file: allow (empty stdout)
- Different-project file: deny JSON
- Unbound + multi-active + project file: deny
- Unbound + multi-active + workspace-level file: allow (so /switch works)
- Unbound + single active: allow
- No file path or relative path: allow
- Missing jq: allow (fail-open, via `no_jq_env`)

### test_wal_guard.py — Dangerous command blocking (pytest port)

Migrates test_wal_guard.sh to parametrized pytest:
- Destructive git/rm patterns: deny
- Production deployment patterns (ssh, scp, docker compose prod): deny
- Safe operations (git diff, gh pr, read-only docker compose): allow
- Edge cases (echo with prod, env vars)
- **Missing jq: deny** (fail-closed — wal-guard blocks ALL commands when jq is unavailable, via `no_jq_env`)

### test_session_start.py — Unified SessionStart hook

The most complex hook (330 lines, 4 sections, uses both jq and python3). Tested per section:
- Reconciliation: deactivates projects with missing directories
- WAL recovery: detects incomplete INTENT entries without DONE/FAIL
- WAL rotation: rotates >5000 lines, preserves incomplete + recent
- Session notes archival: archives >600 line files on startup
- Context emission: correct JSON structure with all parts joined

**Fixture note:** Rotation tests need pre-populated WAL files with >5000 lines. Archival tests need session notes with >600 lines. Use `make_workspace` with appropriate `wal_entries` and `session_notes` parameters.

### test_security_guard_check.py — Plugin conflict detection

- Official plugin enabled in settings.json: emits warning
- Not present: silent exit
- Settings file missing: silent exit
- Malformed settings: silent exit

**Env note:** This hook reads `$HOME/.claude/settings.json`. Tests must use `run_hook(env_override={"HOME": str(tmp_path)})` to avoid reading the real settings file.

## Implementation Order

Ordered from simplest to most complex:

1. **conftest.py** — shared fixtures (foundation for everything)
2. **test_security_guard_check.py** — simplest hook, 4 test cases
3. **test_wal_guard.py** — straightforward parametrized port
4. **test_wal_pre_post.py** — three hooks with identical pattern
5. **test_wal_stop.py** — moderate complexity, registry + notes
6. **test_wal_bind_guard.py** — two-gate logic, workspace-level exception
7. **test_wal_context.py** — context assembly, auto-bind logic
8. **test_wal_lib.py** — awkward bash-source-from-python pattern
9. **test_session_start.py** — most complex, 4 sections, large fixtures

## Deliverables

1. **8 new test files** under `tests/hooks/`
2. **Extended conftest.py** with `make_workspace`, `run_hook`, `no_jq_env`
3. **Updated docs/testing.md** — replace "Tests to Be Implemented" with descriptions of all new test files; add "Skill Evaluation" section documenting the `/skill-creator` eval pipeline
4. **evals.json for sync-security-patterns** — fill the one skill eval gap
5. **Deprecation comment in test_wal_guard.sh** — mark as superseded

## Skill Testing

Skills are tested via the `/skill-creator` eval pipeline, which produces:
- `evals/evals.json` — test prompts with assertions
- `*-workspace/iteration-N/` — with_skill vs without_skill comparison runs
- `benchmark.json` — quantitative pass rates, timing, token usage
- `review.html` — interactive HTML viewer for qualitative review

**Coverage:** 13/14 skills have evals.json. `sync-security-patterns` needs one added.

**When to re-run:** After modifying a SKILL.md, invoke `/skill-creator` to validate against existing evals before merging.

## Key Decisions

1. **Subprocess black-box over bats-core** — consistent with existing e2e pattern, no new dependencies, unified `pytest tests/` entry point
2. **Skill testing via /skill-creator** — already established, no custom infrastructure needed
3. **Shared conftest fixtures** — DRY workspace setup, reusable across all hook test files
4. **PATH manipulation over skip markers** — test jq-absent behavior explicitly, especially wal-guard's fail-closed path
5. **Always capture stderr** — hooks swallow errors in production, but test harness surfaces them for debugging
