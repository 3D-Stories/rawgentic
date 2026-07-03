# Testing

## Approach

Tests use subprocess black-box testing: hooks are invoked via
`subprocess.run` with JSON on stdin, exactly as Claude Code does at runtime.
Python unit tests import directly from `hooks/` via `sys.path.insert`, so no
package installation is required. Run from the repository root.

## Running Tests

Prerequisites: Python 3.10+, pytest, jq.

```bash
# Run all tests
pytest tests/ -v

# Run a single file
pytest tests/hooks/test_wal_guard.py -v
```

## CI

GitHub Actions runs `pytest tests/ -v` on all pull requests targeting `main`.
The workflow is defined in `.github/workflows/ci.yml` and uses Python 3.12
on `ubuntu-latest`.

Additionally, rawgentic's `.rawgentic.json` includes a `testing` section,
so all SDLC workflow skills (WF2-WF4, WF7-WF12) automatically run the test
suite when working on rawgentic itself.

## Hook Tests

### Shared Fixtures (`tests/hooks/conftest.py`)

Shared pytest fixtures used across all hook test files: `make_workspace`
(temporary workspace with `.rawgentic.json` and registry), `run_hook`
(subprocess invocation helper), `parse_hook_output` (JSON result parser),
and `no_jq_env` (PATH manipulation to simulate missing `jq`).

### Security Guard — Unit Tests (`tests/hooks/test_security_guard.py`)

78 unit tests for the security-guard pattern-matching library. Tests cover
`glob_match`, `normalize_path`, `extract_content`, `check_word_boundary`,
`sanitize_path_for_message`, `suggest_glob`, `match_patterns`,
`filter_exceptions`, and `format_deny`.

### Security Guard — E2E Tests (`tests/hooks/test_security_guard_e2e.py`)

End-to-end tests for the security-guard hook subprocess. Verifies safe
content passes through, dangerous patterns are blocked, `.rawgentic.json`
exceptions work correctly, and malformed/empty stdin does not crash the hook.

### Security Guard Check (`tests/hooks/test_security_guard_check.py`)

4 tests for plugin conflict detection. Validates that the hook detects when
the official Claude Code security-guard plugin is installed alongside
rawgentic's version. Uses HOME env isolation to control the plugin
discovery path.

### WAL Guard (`tests/hooks/test_wal_guard.py`)

36 parametrized tests for production deployment blocking and destructive
command passthrough. Covers production deployment denial (ssh, scp, rsync,
docker, ansible, kubectl, helm, terraform targeting prod), destructive
local command allowance (Claude's built-in safety handles these), safe
command passthrough, and a fail-closed test when `jq` is absent.

### WAL Pre/Post (`tests/hooks/test_wal_pre_post.py`)

7 tests for WAL logging hooks. Validates correct INTENT/DONE/FAIL JSONL
entries, summary extraction from tool output, unbound session skip
behavior, and graceful handling when `jq` is absent.

### WAL Stop (`tests/hooks/test_wal_stop.py`)

6 tests for the session end marker hook. Covers COMPLETE marker writing,
STOP WAL entry, duplicate invocation skip, and registry fallback behavior.

### WAL Bind Guard (`tests/hooks/test_wal_bind_guard.py`)

8 tests for cross-project write blocking. Validates that file writes
outside the bound project directory are blocked, writes inside the
directory are allowed, workspace-level exceptions work, and `jq` absence
triggers fail-open behavior.

### WAL Context (`tests/hooks/test_wal_context.py`)

7 tests for UserPromptSubmit context injection. Covers bound sessions,
unbound sessions, multi-active-session conflict case, no-active-session
case, and auto-bind behavior.

### WAL Lib (`tests/hooks/test_wal_lib.py`)

11 unit tests for the `wal-lib.sh` shared library. Tests `parse_input`,
`extract_summary`, and `find_workspace` functions.

### Session Start (`tests/hooks/test_session_start.py`)

22 tests for the session-start hook. Covers session reconciliation, WAL
recovery, legacy archival removal verification, notes size handler
integration, security pattern staleness detection, context emission,
and claude_docs migration.

### Model Routing (`tests/hooks/test_model_routing.py`)

16 unit tests for `hooks/model_routing_lib.py` (v2.46.0). Covers role
resolution, partial/absent config, invalid values, malformed/invalid-UTF-8
workspace files (fail-open to `inherit`), the review-role opus-floor warning,
and the always-exit-0 CLI contract.

### Model Routing Dispatch Guard (`tests/hooks/test_model_routing_dispatch.py`)

Drift guard asserting every subagent dispatch site in
implement-feature/fix-bug/refactor carries its `<!-- model-routing: role=X -->`
annotation, plus the WF2 Step 8 delegation clean-state-boundary contract.

### Peer Consult Lib (`tests/hooks/test_peer_consult_lib.py`)

12 tests for the consult mode in `hooks/adversarial_review_lib.py` (WF13):
`--key` backward compatibility, `PROPOSAL_SCHEMA` shape, peer-framed prompt
nonce fencing, report paths, and PATH-stubbed CLI runner tests proving the
exit-code contract (0/2/3/4) and the empty-proposal marker on every
non-success path.

### Peer Consult Registration (`tests/hooks/test_peer_consult_registration.py`)

WF13 registration drift guard (mirrors WF5's): skill dir + frontmatter,
marketplace entry, evals stub, the WF2 Step 3 blind-both-ways integration
markers, and the setup Step 2f/2g presence checks.

## Skill Evaluation

Skills are tested via the `/skill-creator` eval pipeline, which produces
`evals.json`, `benchmark.json`, and `review.html` for each skill.

- **Coverage:** 14/14 skills have `evals.json`.
- **When to re-run:** after modifying a `SKILL.md`.
