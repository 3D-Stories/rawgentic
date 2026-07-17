# #431 — multi-account Claude lanes via CLAUDE_CONFIG_DIR (E8, epic #422) — lane design+plan

**Small-standard lane** (2 impl files ≤ 7). Plan §3.5b. depends on #424 (✓). Key-independent.

## Brief design (lane)

The quota-per-account coordination is ALREADY wired (engine.py:102 passes `account=lane.credential_ref`
to `quota.acquire`, so each account gets its own permit namespace / ceiling). #431 adds the missing
piece: the **claude adapter sets `CLAUDE_CONFIG_DIR=<credential_ref>` in the subprocess env** so a
lane's `credential_ref` selects an isolated Claude config tree (= an independent quota pool;
CLAUDE_CONFIG_DIR isolation confirmed live 2026-07-16 per the issue).

Two focused changes to `phase_executor/src/phase_executor/adapters/`:

1. **`base.py::run_subprocess(cmd, stdin, timeout, *, env=None)`** — new optional `env` param of
   ADDITIONS. When given, launch with `{**os.environ, **env}` (MERGE, not replace — the child keeps
   PATH/HOME/etc. and gains the additions). `env=None` → `Popen(env=None)` inherits, byte-identical to
   today (codex/zhipu adapters unchanged).
2. **`claude_cli.py`** — a pure `_claude_env(credential_ref) -> dict | None` returning
   `{"CLAUDE_CONFIG_DIR": credential_ref}` when `credential_ref` is a non-empty string, else `None`;
   `run()` passes it as `run_subprocess(..., env=_claude_env(req.credential_ref))`. Only the CLAUDE
   adapter does this (Claude multi-account); codex has its own `CODEX_HOME`, zhipu its own key — out of
   scope for #431.

Setup runbook (docs): per-account one-time — `CLAUDE_CONFIG_DIR=<dir> claude` → `/login`, install the
rawgentic plugin, settings; the cron launcher / executor pins the env var per invocation. ToS note
(owner-acknowledged): per-account limits are by design; legitimately-owned seats are the owner's call;
`ANTHROPIC_API_KEY` (API billing) is the sanctioned no-window alternative.

## Platform / external dependencies

platform_apis:
- api: CLAUDE_CONFIG_DIR env var selecting an isolated Claude Code config tree for the `claude --print` subprocess
  feasibility: verified via spike — owner live probe 2026-07-16 (a fresh CLAUDE_CONFIG_DIR scaffolds a full config tree and reports "Not logged in", per the issue); the `claude --print` subprocess itself is the existing phase_executor.adapters.claude_cli call site (bench lineage)
  failure: fail-loud
  surface: a wrong/unauthenticated CLAUDE_CONFIG_DIR → the claude CLI errors (non-zero exit / "Not logged in") → non-ok Observation.parse_status (availability failure) → run_seat falls back / reconcile flags it; the @live multi-account test (RUN_LIVE + a second logged-in dir) is the opt-in preflight

## Plan (lane checklist — TDD)

### Task 1: run_subprocess env param (merge, not replace)
- riskLevel: standard
- RED: test run_subprocess with `env={"PE_TEST_VAR":"x"}` running `python3 -c "import os;print(os.environ.get('PE_TEST_VAR'), os.environ.get('PATH') is not None)"` → asserts the addition is present AND PATH is still inherited (merge); `env=None` → addition absent. GREEN: add `*, env=None` + `{**os.environ, **env}` when env truthy.
- files: phase_executor/src/phase_executor/adapters/base.py, tests/phase_executor/test_adapter_env.py

### Task 2: claude adapter sets CLAUDE_CONFIG_DIR from credential_ref
- riskLevel: standard
- RED: test `_claude_env("/home/x/.claude-acct2")` → `{"CLAUDE_CONFIG_DIR": "/home/x/.claude-acct2"}`; `_claude_env(None)`/`_claude_env("")` → None. GREEN: add `_claude_env` + wire into `run()`.
- files: phase_executor/src/phase_executor/adapters/claude_cli.py, tests/phase_executor/test_adapter_env.py

### Task 3: setup runbook docs
- riskLevel: standard
- Document the per-account runbook + ToS note in docs (config-reference or a runbook doc). verify: full suite green.

version 3.46.0 → 3.47.0 (minor, feat). No spine change → no diagram REV.
