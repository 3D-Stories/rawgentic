# Design: FTS5 session index + `/rawgentic:session-recall` (issue #375)

Date: 2026-07-10 · Author: WF2 run (epic #378) · Status: draft for Step 4 gate

## Problem

Full-text search over ~5,130 Claude Code session JSONL files (~2.35 GB, 16 project
dirs under `~/.claude/projects/`). mempalace = curated semantic memory; this = raw
full-text over everything. Derived, rebuildable, never committed. No daemon, no
hook-event registration, no LLM — explicit invocation only (epic #378 hard
constraints).

## Approaches considered

**A. Regular content table + FTS5 external-content table (chosen).**
`messages` regular table holds provenance + text; `messages_fts` is
`fts5(text, content='messages', content_rowid='id')`. Pros: `snippet()` works
(AC2), per-file delete is cheap via a `file_id` index on the regular table,
storage not duplicated (FTS index only), standard SQLite-docs pattern. Cons: two
objects to keep in sync — handled by the AFTER INSERT/DELETE/UPDATE triggers
below (adopted from peer consult; triggers are the sole sync mechanism, no
manual FTS commands in application code, so no double-delete hazard).

**B. Single plain FTS5 table with UNINDEXED provenance columns.** Pros: one
object. Cons: per-file re-index requires deleting rows located only by an
UNINDEXED column — a full virtual-table scan per changed file; at millions of
rows that makes the common incremental path the slow path. Rejected.

**C. Contentless FTS5 (`content=''`).** Smallest disk. Cons: no `snippet()`, no
text reconstruction — fails AC2. Rejected.

## Schema (v1) — post peer-consult synthesis

```sql
PRAGMA journal_mode=WAL;
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
  -- schema_version=1, parser_version=1, last_run=<ISO>
CREATE TABLE files(
  id INTEGER PRIMARY KEY,
  path TEXT UNIQUE NOT NULL,          -- absolute JSONL path
  project TEXT NOT NULL,              -- parent dir basename
  mtime_ns INTEGER NOT NULL, size INTEGER NOT NULL,   -- high-water mark
  indexed_at TEXT NOT NULL,           -- ISO 8601 UTC
  message_count INTEGER NOT NULL,
  malformed_count INTEGER NOT NULL,   -- unparseable JSON lines (AC3)
  ignored_count INTEGER NOT NULL,     -- parseable, non-message line types
  rejected_count INTEGER NOT NULL     -- message-typed lines that failed extraction
                                      -- (no text / missing or unparseable provenance)
);
CREATE TABLE messages(
  id INTEGER PRIMARY KEY,
  file_id INTEGER NOT NULL REFERENCES files(id),
  line_no INTEGER NOT NULL,
  session_id TEXT NOT NULL, ts TEXT NOT NULL,          -- ts verbatim ISO from line
  ts_us INTEGER NOT NULL,                              -- normalized UTC microseconds (filters/ordering)
  project TEXT NOT NULL, role TEXT NOT NULL,
  uuid TEXT, text TEXT NOT NULL,
  UNIQUE(file_id, line_no)
);
CREATE INDEX idx_messages_file ON messages(file_id);
CREATE INDEX idx_messages_project_ts ON messages(project, ts_us);
CREATE VIRTUAL TABLE messages_fts USING fts5(
  text, content='messages', content_rowid='id', tokenize='unicode61'
);
-- FTS sync triggers (the SOLE sync mechanism — application code never issues
-- manual FTS commands, so no double-delete hazard):
CREATE TRIGGER messages_ai AFTER INSERT ON messages BEGIN
  INSERT INTO messages_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER messages_ad AFTER DELETE ON messages BEGIN
  INSERT INTO messages_fts(messages_fts, rowid, text) VALUES('delete', old.id, old.text);
END;
CREATE TRIGGER messages_au AFTER UPDATE ON messages BEGIN
  INSERT INTO messages_fts(messages_fts, rowid, text) VALUES('delete', old.id, old.text);
  INSERT INTO messages_fts(rowid, text) VALUES (new.id, new.text);
END;
```

**Foreign keys: OFF by design** (SQLite default; the `REFERENCES` clause is
documentation). Rationale: cascade-delete would have to fire the FTS delete
triggers via FK-action semantics — provable but subtler than needed. Instead
the single-writer batch code pins the delete order explicitly: `DELETE FROM
messages WHERE file_id=?` first (fires the FTS triggers), then the `files`
row, in the same transaction. A test proves vanished-file content disappears
from `messages_fts`.

Peer-consult provenance: cross-model consult
(`docs/reviews/peer-rawgentic-peer-problem-375-2026-07-10.md`) contributed:
FTS-sync triggers, `line_no` + `UNIQUE(file_id,line_no)` identity, `mtime_ns`,
normalized `ts_us`, malformed/ignored split, stat-recheck-mid-read, flock
writer lock, `parser_version` meta, deterministic result ordering, `--literal`
flag, thinking-block exclusion rationale. Rejected from peer: temp-DB atomic
rebuild swap (WAL-sidecar complexity disproportionate for a rebuildable derived
store — in-place drop/recreate under the writer lock instead).

## Extraction contract (pure function)

`extract_message(obj) -> ExtractedMsg | None` over one parsed JSONL line:
- `type in {"user","assistant"}` only; everything else (mode, attachment,
  file-history-snapshot, system, queue-operation, …) → ignored (counted
  `ignored`, distinct from malformed).
- `message.content` is a **str** (user) → that string.
- `message.content` is a **list of blocks** → join `text` block texts with
  `"\n\n"`. `thinking`, `tool_use`, `tool_result` blocks are NOT indexed in v1
  (index size + accidental exposure of internal/tool payloads — peer-consult
  rationale; revisit = `parser_version` bump + `--rebuild`).
- Classification (calibrated against the REAL corpus during the live Task-4
  run — 77% of message lines are legitimately textless): a `user`/`assistant`
  line with well-formed content but no text-bearing blocks (tool_use-only,
  tool_result, thinking-only, empty string) → **ignored** (expected shape). A
  line with an unexpected `message`/`content` shape (non-dict message, non-
  str/list content) or missing/unparseable provenance (`sessionId`,
  `timestamp`) → **rejected_message** — the true format-drift signal,
  persisted in `files.rejected_count` (see schema DDL).
- **Format-drift warning:** after an `index` run, if run-wide
  `rejected / (indexed + rejected) > 0.5` (indexed = `message_count`; and
  ≥ 100 message lines seen), print
  a loud stderr warning naming sample file:line locations. Exit stays 0
  (fail-soft per AC3) — the warning + `status` counters make the drift visible.
- Provenance per line: `session_id` (top-level `sessionId`), `ts` (`timestamp`
  verbatim) + `ts_us` (normalized UTC microseconds — parsed via
  `datetime.fromisoformat(ts.replace("Z", "+00:00"))`, which handles the
  corpus's real `YYYY-MM-DDTHH:MM:SS.mmmZ` shape on all supported Pythons;
  at least one test fixture line carries that exact shape so a systematic
  parse failure goes red), `role` (= `type`), `uuid`, `line_no`, `project`
  (= FIRST path component under the corpus root — the real corpus nests up to
  `project/session-dir/subagents/*.jsonl`, so the scan is `rglob("*.jsonl")`
  and a parent-dir-basename derivation would mislabel subagent files as
  project "subagents"; live-run catch, Task 4).
- Unparseable JSON line → malformed, counted (AC3), never fatal.

Upstream format is Claude Code-internal and may change without notice (issue
risk note): the extractor is total — any unexpected shape inside a
user/assistant line degrades to "no text extracted", never an exception; format
assumptions documented in the module docstring.

## Incremental strategy (AC1)

**Startup validation (before any scan):** `index` compares the stored
`schema_version` and `parser_version` in `meta` against the code constants;
mismatch → exit 2 with an explicit "run `index --rebuild`" instruction, no file
mutated. Two explicit exemptions: (a) **fresh DB** — when the DB or `meta` does
not yet exist, the first run creates the schema at the current versions and
proceeds (no mismatch possible); (b) **`--rebuild` bypasses the check
entirely** — it is the remedy, so it must never be blocked by the condition it
fixes. Tests cover both: first-run-on-empty succeeds; version-mismatch →
incremental exits 2 → `--rebuild` succeeds. Without this gate, unchanged files
parsed under an old contract would coexist with dirty files parsed under the
new one, silently invalidating the convergence claim below.

Unit = whole file. A file is dirty when `(mtime_ns, size)` differs from the
stored high-water mark (or is absent). **Known v1 limitation (documented, not
defended):** an in-place rewrite that preserves both size and mtime_ns (e.g.
metadata restoration) is undetectable by this mark — accepted for v1 because
the corpus writer (Claude Code) only appends; `--rebuild` is the recovery. Per dirty file, one transaction:
`DELETE FROM messages WHERE file_id=?` (fires the FTS delete triggers), then
delete+recreate the `files` row, re-parse and re-insert deterministically from
file bytes. Stat the file before and after the read; if it changed mid-read
(live session appending), roll back and retry that file up to 2×, else keep the
prior indexed version (stale — visible in `status` freshness). Vanished files →
per vanished path, one transaction: `DELETE FROM messages WHERE file_id=?`
(FTS triggers fire) then the `files` row — else incremental would retain
sessions a clean rebuild removes; a test proves vanished content leaves
`messages_fts`. Because per-file state is always rebuilt from file bytes by the
same pure extractor (pinned by `parser_version`), an incremental pass and a
from-scratch rebuild converge to identical rows → identical search results (AC1
test compares result sets, using the deterministic ordering below).

`--rebuild` executes DROP + CREATE (tables and triggers) + full repopulation
as **one transaction** — SQLite supports transactional DDL, so concurrent
readers see either the old complete index or the new complete index at the
single commit point, never missing/partial tables (verified in the 2026-07-10
exact-schema spike; the AC4 test also exercises search-during-rebuild).
Derived store — a crashed rebuild is recovered by re-running.
**Acknowledged operational caveat:** a single rebuild transaction cannot be
checkpointed until commit, so the WAL transiently grows to roughly the full
new index size (potentially GBs on this corpus) — a deliberate trade against
the peer's temp-DB swap complexity; disk headroom ≈ 2× final DB size is the
documented requirement for `--rebuild`, stated in the module docstring and
README entry. Incremental runs are unaffected (per-file transactions +
periodic passive checkpoints).

## Concurrency (AC4)

WAL mode. `index` is the single writer, enforced by a **non-blocking
`fcntl.flock`** on a sibling lock file (`sessions.db.lock`) acquired before any
mutation — a second concurrent `index` fails immediately, exit 3, "another
index run appears to be in progress" (cleaner than busy-timeout contention;
`PRAGMA busy_timeout=5000` retained as backstop). One transaction per file
keeps the writer lock granular and WAL growth bounded. Readers (`search`,
`status`) open read-only connections (`sqlite3.connect(f"file:{db}?mode=ro",
uri=True)`), never touch the flock — WAL snapshot isolation gives consistent
results during a concurrent write (AC4 test drives this). Search results are
deterministically ordered: bm25 rank, then `ts_us`, `session_id`, `path`,
`line_no` — required for the AC1 result-set comparison.

**WAL growth policy:** per-file transactions bound each write, but a
long-lived reader snapshot can block checkpointing and grow the WAL for the
run's duration. The indexer runs `PRAGMA wal_checkpoint(PASSIVE)` every 50
files; `status` reports the WAL sidecar size so blockage is observable. No
abort threshold in v1 — the store is derived and disk-bounded by the corpus;
documented as an operational caveat.

## CLI contract

`python3 hooks/session_index.py <sub> [flags]` — argparse; pure core + thin
`main(argv)` per repo hook quality bar (exemplar: `registry_prune.py`).

- `index [--projects-dir DIR] [--db PATH] [--rebuild] [--workspace-root DIR]`
  → prints files scanned/indexed/unchanged, messages added, lines skipped. Exit
  0 ok; 3 writer-locked; 2 usage/env error.
- `search QUERY [--project P] [--since YYYY-MM-DD] [--until YYYY-MM-DD]
  [--limit N (default 20)] [--literal] [--json] [--db PATH]`
  → provenance columns + `snippet(messages_fts, 0, '[', ']', '…', 12)`,
  deterministic ordering (bm25, ts_us, session_id, path, line_no). Raw FTS5
  syntax by default; `--literal` converts the query to a safe FTS5 phrase:
  wrap in double quotes AND double any embedded `"` characters (boundary test
  includes a query containing `"`). The skill prefers `--literal` unless the
  user asks for FTS operators. Date filters
  compare against `ts_us` (inclusive `--since`, inclusive date `--until` =
  end-of-day UTC). FTS5 query syntax error (`OperationalError`) → exit 2 with
  the syntax hint, never a traceback. Missing DB → exit 2 "no index yet — run
  index first".
- `status [--db PATH] [--projects-dir DIR]` → schema/parser versions,
  file/message counts, malformed + ignored + rejected line totals, last run,
  DB + WAL sizes, staleness (counts of new/changed/missing files vs the
  high-water marks — live metadata scan, never mutates).

DB default: `<workspace-root>/claude_docs/.session-index/sessions.db`, where
workspace root = upward walk from cwd to the directory containing
`.rawgentic_workspace.json` (pure `resolve_workspace_root(cwd)`); not found and
no `--db` → exit 2 with an explicit message. Projects dir default:
`~/.claude/projects` (injectable for tests).

## Skill: `skills/session-recall/SKILL.md`

Frontmatter `name: rawgentic:session-recall`, trigger-phrased description
("Use when you need to find what a past session did/said/decided …"),
`argument-hint: <query> [--project P] [--since DATE]`. Body: resolve hook as
`<skill-base-dir>/../../hooks/session_index.py`, run `index` first when status
shows staleness (explicit invocation each time — never a hook), then `search`,
present results with secrets-by-NAME discipline (never quote a credential value
found in a session). No `<config-loading>` block (workspace-level tool, no
project config) → config-loading canary count unchanged.

Registration (add-skill surfaces): marketplace whitelist `./skills/session-recall`
between `scan` and `setup`; codex mirror symlink; README count strings
(17 skills; workspace-management 6→7 — including the hand-pinned test literal
`"6 workspace management"` → `"7 workspace management"`); plugin.json ×2
description breakdown `7 SDLC + 7 workspace management + 1 planning + 2 security`;
version ×3 → **3.33.0**; Changelog entry. Category call: **workspace
management** (cross-project workspace tool, like housekeeping/switch). Evals
file: not in v1 (optional surface; follow-up noted) — which still moves the
**computed README evals surfaces**: the fraction denominator ("9/16" →
"9/17") AND `session-recall` inserted into the computed have-none membership
list (README ~line 655), both asserted by
`test_adversarial_review_registration.py::test_readme_count_strings_updated`.

**Workflow-diagram REV decision:** no workflow-spine change (new leaf skill +
hook only; no WF step/gate/loop-back touched) → **no diagram REV entry**;
recorded explicitly here and in the PR body per repo quality bar.

## File changes

New: `hooks/session_index.py`, `tests/hooks/test_session_index.py`,
`skills/session-recall/SKILL.md`, symlink `plugins/rawgentic/skills/session-recall`.
Edits: `.claude-plugin/marketplace.json`, `.claude-plugin/plugin.json`,
`plugins/rawgentic/.codex-plugin/plugin.json`,
`tests/hooks/test_adversarial_review_registration.py` (version pin + workspace-mgmt
literal), `README.md` (counts, skills table, Changelog), workspace root
`.gitignore` (+`claude_docs/.session-index/`), this design doc + rendered HTML.

## Error handling / failure modes

- Malformed JSONL line → count + continue (AC3). Unreadable file (permission,
  vanished mid-scan) → warn to stderr, skip file, continue; recorded as not
  indexed (surfaces in status staleness).
- FTS5 unavailable in the Python build → loud exit 2 naming the missing module
  capability (fail-loud, checked at DB create).
- DB corruption → sqlite errors are not caught broadly; `--rebuild` is the
  documented recovery (derived store, rebuildable by design).
- Search before first index → exit 2 with instruction (no auto-index — explicit
  invocation constraint).

## Security implications

- Index is local-only, under a non-repo workspace dir; no egress, no
  credentials stored by the tool itself. Session content MAY contain secrets —
  the skill instructs secrets-by-NAME discipline for anything quoted into
  reports/issues; the DB never leaves the host.
- **At-rest protection:** the index dir is created `0700` and DB/WAL/SHM/lock
  files `0600` (`os.makedirs(mode=0o700)` + `os.chmod` after create); the CLI
  refuses a `--db` destination that is a symlink (resolve + compare); a test
  asserts the created modes.
- FTS5 `MATCH` uses parameterized queries (no SQL injection); file paths come
  from a directory walk, not user input.
- AC7 honesty note: the workspace root is **not a git repository** (confirmed
  live), so a workspace `.gitignore` entry is inert for git — the real
  guarantee is structural (the DB dir lives outside every repo). We add the
  entry anyway (documentation + defense-in-depth if the workspace ever becomes
  a repo) and add a repo-side test asserting no `.session-index` path exists
  inside the plugin repo. Pre-existing flaw named, not laundered.

## Platform / external dependencies

platform_apis:
- api: SQLite FTS5 external-content tables (fts5 content=/content_rowid=, AFTER INSERT/DELETE/UPDATE sync triggers incl. the 'delete' command with old.text, bm25(), snippet(), parameterized MATCH) via Python stdlib sqlite3
  feasibility: verified via spike — 2026-07-10 exact-schema spike on this host (sqlite 3.45.1): created the v1 schema incl. all three triggers, exercised insert/match/snippet/bm25, trigger delete cleaning FTS, trigger update, all assertions passed
  failure: fail-loud
- api: SQLite WAL mode (PRAGMA journal_mode=WAL) with mode=ro URI readers concurrent with an open write transaction, and transactional DDL (DROP+CREATE+populate in one transaction) for --rebuild
  feasibility: verified via spike — same 2026-07-10 spike: ro-reader saw pre-txn snapshot during an open BEGIN IMMEDIATE, and the transactional rebuild committed atomically
  failure: fail-silent
  surface: PRAGMA journal_mode=WAL is result-bearing (returns the actual mode, does not raise on refusal) — the code asserts the returned value == "wal" and exits 2 naming the actual mode otherwise; unit test covers the assertion
- api: fcntl.flock(LOCK_EX|LOCK_NB) on a sibling lock file for writer exclusion
  feasibility: verified via spike — same 2026-07-10 spike acquired the lock on this host. POSIX-only (Linux target — this workspace); advisory (sufficient: only `index` writes, and only via this code path); NFS explicitly out of scope (DB lives on local ext4)
  failure: fail-loud
- api: filesystem — read traversal of ~/.claude/projects/*/*.jsonl; create workspace claude_docs/.session-index/ (dir 0700, files 0600) incl. WAL/SHM sidecars
  feasibility: verified via existing-call-site — this session's Step 2 probes read the corpus (5,130 files walked); hooks already write under workspace claude_docs (e.g. session_registry.jsonl, wal/); spike created a WAL DB + sidecars under the scratchpad
  failure: fail-loud
- api: symlink creation for the codex mirror (plugins/rawgentic/skills/session-recall)
  feasibility: verified via existing-call-site — every existing plugin skill has the same mirror symlink (e.g. plugins/rawgentic/skills/scan), asserted by the packaging test's is_symlink() check
  failure: fail-loud

(No network, no subprocess, no non-stdlib deps. The sqlite3 CLI binary is
absent on this host — tests use Python sqlite3 only. Environment scope: the
spikes prove THIS host; CI (ubuntu-latest, Python 3.x) is proven by the test
suite itself, which exercises FTS5 + WAL + flock end-to-end — a CI runner
lacking FTS5 fails the suite loudly, never silently.)

## Performance

First full index of 2.35 GB: minutes-scale, acceptable for an explicit batch
command; incremental runs touch only dirty files (typically a handful). One
transaction per file keeps the writer lock granular. bm25 ranking default;
`--limit` caps result payload.

## Verification map (AC → test)

| AC | Test |
|----|------|
| 1 incremental == rebuild | unit+CLI: index, modify one file, index again; compare full search results vs fresh `--rebuild` DB |
| 2 provenance | search result row asserts session id/ts/project/role/snippet |
| 3 malformed skipped+counted | corpus fixture with garbage lines; status shows count; exit 0 |
| 4 WAL concurrent reader | reader connection opened mid-write transaction sees consistent pre-commit snapshot |
| 5 black-box CLI + pure unit | subprocess tests per docs/testing.md + direct-import unit tests |
| 6 registration | existing count-guard suite (test_v3_removals, test_adversarial_review_registration, packaging, headless canary) |
| 7 gitignore/no repo data | workspace .gitignore line present + repo-tree assertion no `.session-index` |
