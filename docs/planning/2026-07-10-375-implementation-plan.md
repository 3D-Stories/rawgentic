# Implementation plan: issue #375 (FTS5 session index + session-recall)

Branch: `feature/375-fts5-session-index`
Design: `docs/planning/2026-07-10-375-fts5-session-index-design.md`
Mode: TDD (red-green-refactor per task). Execution: sequential (tasks share
`hooks/session_index.py` / `tests/hooks/test_session_index.py` — no parallel
groups declared). Single PR (deliverables are one atomic feature: the skill
wraps the CLI and the count guards force simultaneous registration; >500-line
total acknowledged and accepted — phases are not separable without shipping
dead code).

### Task 1: Pure core — extraction, timestamps, planning, quoting
- riskLevel: high (deserialization of external data)
- files: hooks/session_index.py, tests/hooks/test_session_index.py
- RED: unit tests (direct import) for `extract_message` (user str content;
  assistant block list joining text blocks only; thinking/tool_use excluded;
  rejected on missing sessionId/timestamp/empty text; ignored on non-message
  types), `parse_ts_us` (real `YYYY-MM-DDTHH:MM:SS.mmmZ` fixture shape,
  naive/invalid → None), `literal_quote` (embedded `"` doubling boundary),
  `resolve_workspace_root` (found/not-found), `plan_changes` (new/changed/
  unchanged/vanished from (mtime_ns,size) marks), format-drift predicate.
- GREEN: implement pure functions; module docstring documents JSONL format
  assumptions + rebuild disk-headroom caveat.
- Verify: `pytest tests/hooks/test_session_index.py -q` green; full suite green.
- Commit: `feat(hooks): session_index pure core — extraction, planning, quoting (#375)`

### Task 2: DB layer + index subcommand
- riskLevel: high (infra/persistence; non-trivial error flow)
- files: hooks/session_index.py, tests/hooks/test_session_index.py
- RED: tests for schema create (triggers present; WAL asserted == "wal"; dir
  0700/file 0600 modes; symlink-dest refusal), startup validation (fresh-DB
  proceeds; version mismatch → exit 2; mismatch → `--rebuild` succeeds),
  incremental behavior (only dirty files reprocessed; vanished-file rows leave
  messages_fts; AC1: incremental-after-modify == fresh `--rebuild` result
  sets), malformed/ignored/rejected counting (AC3, garbage-line fixture),
  flock exclusion (second writer exit 3), AC4 WAL concurrent reader
  (ro-reader mid-write-txn sees consistent snapshot), transactional rebuild
  visibility, run-level format-drift warning surfaced on stderr (fixture
  crossing the >50% threshold), checkpoint call presence (PASSIVE every 50
  files — asserted via injected counter or code inspection note). CLI
  black-box via subprocess per docs/testing.md.
- GREEN: implement `cmd_index` + DB helpers.
- Verify: module tests + full suite green.
- Commit: `feat(hooks): session_index index subcommand — FTS5 store, WAL, flock (#375)`

### Task 3: search + status subcommands
- riskLevel: standard
- files: hooks/session_index.py, tests/hooks/test_session_index.py
- RED: CLI tests — search provenance columns (AC2: session id, ts, project,
  role, snippet), deterministic ordering, `--project`/`--since`/`--until`
  filters, `--literal` boundary (query containing `"`), `--json` shape, FTS5
  syntax error → exit 2 no traceback, missing DB → exit 2 hint; status fields
  (versions, counts incl rejected, DB+WAL sizes, staleness new/changed/missing).
- GREEN: implement `cmd_search`, `cmd_status`.
- Verify: module tests + full suite green.
- Commit: `feat(hooks): session_index search + status subcommands (#375)`

### Task 4: session-recall skill + registration surfaces
- riskLevel: standard
- files: skills/session-recall/SKILL.md, plugins/rawgentic/skills/session-recall, .claude-plugin/marketplace.json, .claude-plugin/plugin.json, plugins/rawgentic/.codex-plugin/plugin.json, tests/hooks/test_adversarial_review_registration.py, README.md
- RED: registration guard suite currently green at 16 skills — after adding
  skill dir the computed guards (whitelist==disk, README counts, evals
  fraction/membership, packaging symlink) go red until every surface updates;
  version pin test red until 3.33.0 ×3.
- GREEN: SKILL.md (trigger-phrased description, --literal default, secrets-by-
  NAME discipline, staleness-check-then-search flow); symlink; whitelist entry
  between scan and setup; plugin.json ×2 version 3.33.0 + description
  "7 workspace management"; README: 17 skills + fixed line-14 prose breakdown
  (pre-existing rot: 6+7+1=14 — bonus fix, one line), skill table row,
  "7 workspace management" literal (+ test literal), evals fraction 9/17 +
  have-none list + config-loading count untouched (no <config-loading> block),
  Changelog v3.33.0 entry; version-pin test → "3.33.0".
- Verify: `pytest tests/test_v3_removals.py tests/test_codex_plugin_packaging.py tests/hooks/test_adversarial_review_registration.py tests/hooks/test_headless.py -q` green; full suite green; **live skill-command execution** (new-skill quality bar): run every command the SKILL.md instructs — status, index (incremental), search with --literal — once against the real corpus from a non-repo cwd, exactly as the skill words them, and confirm exit codes/output shapes match the skill's claims.
- Commit: `feat(skills): session-recall skill + registration surfaces, v3.33.0 (#375)`

### Task 5: gitignore guard, docs, design artifact
- riskLevel: standard
- files: ../../.gitignore, tests/test_wf2_clarity.py, docs/planning/2026-07-10-375-fts5-session-index-design.md, docs/planning/2026-07-10-375-fts5-session-index-design.html, docs/planning/2026-07-10-375-implementation-plan.md, README.md
- RED: new repo-side guard test asserting no `.session-index` path exists in
  the repo tree (AC7) — red if any index artifact sneaks in (test asserts on
  tree state; red-proof via temporary fixture during dev).
- GREEN: workspace .gitignore `claude_docs/.session-index/` line (documented
  inert-for-git honesty note in design); README usage entry for the CLI incl
  rebuild disk-headroom caveat; render design doc HTML via
  `python3 hooks/render_artifact.py --md <design>.md --out <design>.html`;
  commit design + plan docs.
- Verify: full suite green; HTML renders.
- Commit: `docs(planning): #375 design + plan, gitignore guard, README usage (#375)`

## Verification strategy summary
Every task: full suite `pytest tests/ -q` after task, delta vs baseline stated
(baseline recorded at Step 8 start per WF2 contract).
AC→task map: AC1→T2, AC2→T3, AC3→T2, AC4→T2, AC5→T1+T2+T3 (unit + subprocess
black-box), AC6→T4, AC7→T5. Full AC→test detail in the design doc, which also
pins the deterministic ordering keys (bm25, ts_us, session_id, path, line_no),
inclusive --since/--until date semantics, the Z-suffix timestamp parse, and the
platform_apis feasibility evidence — the plan does not restate them. No
deferred-to-target verification (all paths exercisable locally).

## Documentation tasks
README (counts, table, changelog, usage), design doc committed md+html,
workflow-diagram: explicit NO-REV decision (no spine change).
