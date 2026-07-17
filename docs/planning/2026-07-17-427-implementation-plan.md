# #427 implementation plan — seat-executor wiring (E4)

Branch: `feature/427-seat-executor-wiring` (off fresh origin/main @ 2cd6ce7).
Design: `docs/planning/2026-07-17-427-seat-executor-wiring-design.md` (rev 3).
Has tests → Red-Green-Refactor per task. version bump + README changelog handled in Step 12.

### Task 1: Parameterize model_routing_lib._load_block (shared loader + absent sentinel)
- riskLevel: standard
- RED: add test to tests/hooks/test_model_routing.py asserting `_load_block(ws, proj, key="modelRouting")` still works for existing callers AND `_load_block(..., key="executorRouting", missing=SENTINEL)` returns the SENTINEL when the key is absent but the raw value (e.g. a string) when present-but-non-dict (distinguishing absent from present-non-object). Confirm RED.
- GREEN: add `key="modelRouting"` and `*, missing=_ABSENT` params to `_load_block`; return `missing` only when the key is truly absent; return the raw value (not `{}`) when present-non-dict so the caller can reject it. Preserve every existing `resolve()` caller (default key).
- verify: `pytest tests/hooks/test_model_routing.py -q` green (existing + new).
- files: hooks/model_routing_lib.py, tests/hooks/test_model_routing.py
- commit: `feat(routing): parameterize model_routing _load_block for shared executor-routing loader`

### Task 2: executor_routing_lib core — WIRED_SEATS/DRIVER_ONLY + config parse + resolve_seat
- riskLevel: standard
- RED: tests/hooks/test_executor_routing.py — resolve_seat returns action=inherit for absent block / absent seat; action=executor + primary_model for an opted-in seat; action=driver_only for merge|ci_triage|deploy_verify|step16; exit-2 semantics for unknown seat, present-non-object executorRouting, bad version, unknown seat key, invalid mode. Confirm RED.
- GREEN: implement `WIRED_SEATS={ship,intake,plan}`, `DRIVER_ONLY={merge,ci_triage,deploy_verify,step16}`, `parse_executor_routing(block)` (absent→inherit; present-non-object/malformed→raise a Malformed error the CLI maps to exit 2), and `resolve_seat(seat, workspace, project)` returning the three-way decision (primary_model from `eligible_targets(seat)[0]` for executor mode).
- verify: `pytest tests/hooks/test_executor_routing.py -q -k resolve` green.
- files: hooks/executor_routing_lib.py, tests/hooks/test_executor_routing.py
- commit: `feat(routing): executor_routing resolve_seat + config parse (three-way inherit/executor/driver_only)`

### Task 3: Path derivation + path-safe validation (base = project repo root)
- riskLevel: high (input validation at a filesystem boundary — traversal surface)
- RED: tests asserting derived capture_root = `<repo>/.rawgentic/runs/` (run_id-LESS) and permits = `<repo>/.rawgentic/runtime/permits/<pool-sig>/`, base resolved from the workspace config's project.path (project repo root, NOT workspace root); path-unsafe run_id/project (`/`, `..`, control chars, empty) → exit 2. Confirm RED.
- GREEN: implement `derive_paths(workspace, project, run_id, snapshot)` — resolve project repo root from `project.path`; validate run_id/project path-safe; compute pool-sig = short hash of `snapshot.pool_concurrency()`.
- verify: `pytest tests/hooks/test_executor_routing.py -q -k path` green.
- files: hooks/executor_routing_lib.py, tests/hooks/test_executor_routing.py
- commit: `feat(routing): executor_routing path derivation from project repo root + path-safe guard`

### Task 4: dispatch_seat — per-attempt check_pre wrapper + run_seat + verify_post + audit
- riskLevel: high (subprocess construction via run_seat/adapters; fallback/error + enforcement branches)
- RED: tests (stub the injectable `dispatch`, RECORD the attempted target + populate actual_model): executor-ON returns actual_model == opus-4-8 (intake, plan) / sonnet-5 (ship), verified==True, audit appended; chain-fallback (seat=intake, stub targets[0] availability-fail → returned actual_model == fable-5, check_pre receipt for BOTH attempts, each target_identity == declared chain entry); pre-check-denial → denial receipt only + no Observation + exit 4; identity-breach → pre-receipt + Observation + failed post-check + exit 4; chain-exhaustion → exit 3; audit-write failure → exit 5 carrying correlation_id. Confirm RED.
- GREEN: implement `dispatch_seat(...)` — decorator closing over `eligible_targets`, selecting target by leading `i` in attempt_id, calling `check_pre` + appending receipt per attempt; `run_seat` with the wrapper; `verify_post` once; append observation + post-check; map outcomes to exit codes.
- verify: `pytest tests/hooks/test_executor_routing.py -q -k dispatch` green.
- files: hooks/executor_routing_lib.py, tests/hooks/test_executor_routing.py
- commit: `feat(routing): executor_routing dispatch_seat with per-attempt check_pre + verify_post audit`

### Task 5: main(argv) CLI — guarded import, subcommands, JSON I/O, exit-code mapping
- riskLevel: standard
- RED: CLI-via-subprocess tests (`subprocess.run([sys.executable, CLI, ...])`): resolve-seat + dispatch subcommands emit one JSON object on stdout with the right exit codes; a simulated phase_executor ImportError (monkeypatched sys.path) → exit 5 + `{ok:false,error}` envelope (proving the import is guarded INSIDE main, not module-level). Confirm RED.
- GREEN: implement `main(argv)` — argparse subcommands; guarded `import phase_executor` inside main (try/except ImportError → exit 5 + envelope); read --prompt-file / --context-file; JSON out; map Malformed→2, ChainExhausted/quota/timeout→3, enforcement/identity→4, audit/import→5.
- verify: `pytest tests/hooks/test_executor_routing.py -q -k cli` green.
- files: hooks/executor_routing_lib.py, tests/hooks/test_executor_routing.py
- commit: `feat(routing): executor_routing CLI (resolve-seat/dispatch, guarded import, exit-code taxonomy)`

### Task 6: gitignore + workspace config block + check-ignore test
- riskLevel: standard
- RED: test asserting `git check-ignore` (run in the project repo) matches a REAL derived path (`.rawgentic/runs/<id>/routing-audit.jsonl`, `.rawgentic/runtime/permits/<sig>/x`). Confirm RED (not yet ignored).
- GREEN: add `/.rawgentic/runs/` and `/.rawgentic/runtime/` to `projects/rawgentic/.gitignore`; add the rawgentic `executorRouting` block (all seats `inherit`) to `.rawgentic_workspace.json`.
- verify: check-ignore test green; `python3 hooks/capabilities_lib.py derive --config .rawgentic.json` still rc 0 (workspace edit doesn't touch project config, sanity only).
- files: .gitignore, .rawgentic_workspace.json (workspace root), tests/hooks/test_executor_routing.py
- commit: `feat(routing): ignore executor capture/permit dirs + default-inherit executorRouting config`

### Task 7: @live test + config-reference docs
- riskLevel: standard
- IMPLEMENT: tests/phase_executor/live/test_executor_routing_live.py (`@pytest.mark.live`, skipped unless RUN_LIVE=1) — real ship-seat dispatch asserts reported actual_model == claude-sonnet-5. Document `executorRouting` + the seat↔WF-step mapping in docs/config-reference.md.
- verify: `pytest tests/phase_executor/ -q` collects the live test as skipped (no RUN_LIVE); doc renders.
- files: tests/phase_executor/live/test_executor_routing_live.py, docs/config-reference.md
- commit: `feat(routing): @live ship-seat executor test + executorRouting config-reference docs`

## Notes
- Tasks 2–5 all touch hooks/executor_routing_lib.py → sequential (not parallel-eligible; file overlap). Task 1 is independent-file but a prerequisite for Task 2's config parse.
- Step 8a (per-task review) fires on tasks 3 and 4 (riskLevel: high).
- Full suite `pytest tests/ -q` after each task; delta vs recorded baseline (Step 8 records it).
