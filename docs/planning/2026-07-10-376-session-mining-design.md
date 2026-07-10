# Design: WF17 `/rawgentic:session-mining` (issue #376)

Date: 2026-07-10 · WF2 run (epic #378 child 2/3) · Status: draft for Step 4 gate

## Problem

On-demand, report-only mining of session history for skill/command candidates:
detect (deterministic, no LLM) → durable queue → synthesize (evidence-quoted
candidates) → human gate (route accepted to WF1 as a template-shaped draft).
Mirrors WF14's report-only pattern (`skills/run-feedback` — the write-surface
enumeration, report pair naming, Artifact best-effort, 5-step skeleton).

## Approaches — detector source fork

Sources: (1) FTS5 index #375 (TEXT blocks only — tool payloads deliberately
excluded), (2) `claude_docs/session_notes*` (append-only audit trail), (3) raw
JSONL corpus (tool_use commands / tool_result errors — invisible to the index).

- **A. Full raw-JSONL detector pass.** Real tool-level signal, but a 2.35 GB
  scan per run or a duplicate of #375's incremental machinery. Rejected: scope
  + duplication.
- **B. Bump #375 PARSER_VERSION to index tool payloads.** Forces full rebuild
  for every user; reverses #375's deliberate size/exposure exclusion. Rejected.
- **C. Index+notes only (CHOSEN).** Respects the issue text literally.
  Tool-error and command detectors are **proxy detectors** (errors as restated
  in assistant/user text; commands as quoted in session-notes evidence) and are
  LABELED as such; the report carries the canonical coverage limitation: "v1
  does not inspect raw tool_use/tool_result payloads and cannot conclude that
  command sequences or tool errors are absent." Tool-payload detection is
  deferred to a separately approved design.
- **D. Hybrid (my initial draft — REFUTED by peer consult).** A targeted
  raw-JSONL pass over only already-flagged sessions looks cheap, but samples
  tool evidence exactly where text signals exist — biased recurrence counts
  and misleading negatives — and quietly adds a third substrate the issue
  never declared. Peer-consult reversal recorded (run log D6); provenance:
  the consult report in docs/reviews/.

## `hooks/session_mining_lib.py` (pure core + thin CLI)

Pure functions (unit-tested):
- `detect_friction(rows) -> list[Signal]` — over `session_index search --json`
  result rows for a fixed, versioned phrase list (`FRICTION_PHRASES`: e.g.
  "command not found", "permission denied", "try again", "still failing",
  "same error" — apostrophe-free by rule: unicode61 splits "doesn't" and
  breaks phrase matching). **All phrase queries use `--literal`** (raw FTS5
  would decompose a multiword phrase into AND-of-tokens — precision collapse
  that would inflate the propose-triggering recurrence count). Signal =
  (detector, canonical_pattern, session_id, ts, quote, source_ref).
- **Evidence quote resolution (from the #375 DB, read-only):** the index row's
  `snippet` is a 12-token bm25 fragment with match markup — not AC2's verbatim
  quote. The verbatim text is ALREADY STORED in the #375 DB
  (`messages.text`); the quote is resolved by a read-only DB query
  (`mode=ro` URI, `SELECT m.text FROM messages m JOIN files f ... WHERE
  f.path=? AND m.line_no=?`), extracting the matched phrase ± a bounded
  window. No raw JSONL is ever opened (moots the option-C boundary question
  entirely); `session_index.py` is consumed, not modified (per issue scope) —
  the direct ro read is declared in platform_apis. Verified live 2026-07-10:
  the JOIN returned the full verbatim message containing the phrase.
- `detect_note_commands(notes_text) -> list[Signal]` — fenced ```bash blocks
  and backticked commands appearing in session_notes; canonical_pattern =
  normalized command head. **Session-id resolution for note signals
  (defined):** a UUID-shaped token (`[0-9a-f]{8}-…` regex) appearing in the
  same markdown section (same `##`/`###` block) as the command mention
  resolves as its session id; otherwise the signal is **evidence-only — it
  NEVER counts toward the ≥3 distinct-session threshold** (recurrence is
  distinct-session by definition; a note mention without provenance can
  support a candidate, not gate it).
- `detect_error_proxies(rows) -> list[Signal]` — **proxy** detector over
  indexed text for restated tool errors (versioned `ERROR_PHRASES` rules;
  errors that were never restated in visible text are out of v1 coverage —
  stated, never claimed absent).
- `recurrence(signals) -> dict[pattern_key, RecurrenceAssessment]` —
  **distinct-session** counting (one vote per session, never row count). The
  no-count-API contract from #375 is explicit: callers pass a high `--limit`
  (default 500) and dedupe `session_id` client-side; each assessment records
  **coverage** (`returned_rows`, `requested_limit`, `limit_hit`) — a limit hit
  makes the count a LOWER BOUND: it may establish ≥3, but supports no
  absence/ranking/trend claim. Under-count only delays a proposal, never
  fabricates one. **bm25-crowding caveat (in the coverage contract):** results
  are bm25-ordered, so one verbose session can occupy most of the top-N and
  suppress distinct-session counts for common phrases — errs safe (misses,
  never false-proposes); pagination/diversified queries are a noted deferral
  if propose-rate proves low.
- `candidate_key(detector, scope, canonical_pattern) -> str` — sha256 over
  `identity_version | detector family | normalized scope | canonical pattern`
  (evidence, counts, timestamps excluded). **Accepted v1 behavior:** two
  detectors surfacing the same underlying idea produce two keys — declining
  one does not suppress the other (worst case: one extra human-gated question,
  once per detector family; cross-detector identity merging is a noted
  deferral). **Normalizations defined:**
  `canonical_pattern` = lowercase, whitespace runs collapsed to single space,
  leading/trailing punctuation stripped; for commands, the first two tokens
  only (args stripped). `scope` = the constant `"workspace"` in v1 (mining is
  workspace-global — a constant removes all per-run variance; the field exists
  for future per-project mining). **Pinned invariant (test-guarded):** any
  change to these normalization rules REQUIRES an `identity_version` bump —
  resurrection of a declined candidate is only ever a deliberate, versioned
  reset, never an accidental side effect of a rule tweak. Determinism test:
  same inputs → same key across runs.
- `dedupe_candidates(candidates, reduced_queue, skill_descriptors) ->
  (fresh, suppressed, borderline)` — suppress when (a) the reduced queue holds
  a TERMINAL state (`declined`/`accepted`/`filed`) for the key, or (b) a
  STRONG deterministic match against an existing skill descriptor; a
  borderline match is NOT auto-suppressed — surfaced with the matching skill +
  score for the human gate. **Match algorithm (defined):** token-set Jaccard
  over lowercased alphanumeric tokens of (skill name + frontmatter
  description) vs (candidate title + canonical_pattern + keywords), stopwords
  removed; **strong ≥ 0.6 → suppress; borderline 0.3–0.6 → surface with
  score; < 0.3 → fresh.** Thresholds are named constants, determinism tested. **Descriptor source (defined — a naive cwd-relative
  read would scan the bound project's tree, which holds no rawgentic skills):**
  the running plugin's own skills dir, resolved from the skill's base directory
  (`<skill-base>/../../skills/*/SKILL.md` — the plugin cache), PLUS workspace
  skills at `<workspace-root>/.claude/skills/*/SKILL.md`; descriptor = skill
  name + frontmatter description.
- `redact_evidence(text) -> text` — **core deterministic redaction applied in
  the queue-append path** (not just skill prose): masks a NAMED, versioned
  rule list (v1: long hex/base64 runs ≥ 20 chars; `KEY=value` where KEY
  contains token/secret/key/password/credential; `Bearer <blob>`) to
  `[redacted:<kind>]`. **Honesty bound: this is best-effort pattern masking,
  not a guarantee over arbitrary secret shapes** — the Security section says
  exactly that, and the skill's gate step ADDITIONALLY instructs human
  secrets-by-NAME review before any quote lands in a report or issue.
  **Exposure-class rationale (why pre-gate durable quotes are acceptable):**
  the queue lives in workspace `claude_docs/` beside `session_notes*` and the
  #375 index DB, which already durably hold the SAME session content —
  quoting an excerpt into the queue adds no new exposure class; the redaction
  layer strictly reduces it. AC2's "verbatim" reads: verbatim except values
  masked by the named rule — tested.
- `queue_append(path, event)` / `reduce_queue(path) -> (state, malformed_tail)`
  — the repo's plain JSONL idiom (makedirs + open-"a" + compact json +
  newline; one O_APPEND single-line write). **Torn-tail guard:** before
  writing, `queue_append` checks the file's last byte — if it is not `\n`
  (a prior crash tore the final line), it **truncates the torn fragment**
  (seek to the last `\n`, truncate) before appending. The fragment is
  already-lost data from a crashed write, so removal costs nothing — and,
  unlike prepending a newline, truncation keeps the file all-parseable, so
  the torn-tail case never converts into a fatal mid-file `QueueCorruption`
  (incremental-verifier catch). **Race note:** truncate-then-append is not
  atomic; a concurrent good append landing between the truncate and the write
  could be dropped — bounded and accepted because (a) the window exists only
  in the rare post-crash recovery path, (b) a dropped MACHINE event
  regenerates on the next detect, and (c) a human `disposition` is never
  written concurrently with a crash-recovery append in practice (dispositions
  are interactive); the residual risk is named, not silent. **No queue lock (deliberate):**
  the reducer's human-over-machine rule makes a concurrent duplicate append
  benign (worst case: one candidate proposed once more to a human gate,
  self-healing next run) — plain append matches the issue's stated
  convention. **Corruption policy asymmetric (AC4-load-bearing), expressed in
  the function contract:** `reduce_queue` RAISES `QueueCorruption` on any
  unparseable line that is NOT the final line; it returns normally with
  `malformed_tail=True` only for a torn final line (crash mid-append —
  skipped + warned). `propose`/`disposition` catch `QueueCorruption` → exit 2
  (a lost `declined` event would silently resurrect a declined candidate, and
  the queue holds human dispositions that are NOT rebuildable). `detect` may
  proceed on corruption (append-only, never proposes) — under corruption it
  SKIPS the material-change optimization and appends `detected`
  unconditionally (benign: the reducer reconciles machine events idempotently
  once the queue is repaired). Evidence excerpts
  bounded (≤ 500 chars). **Redaction ordering:** `redact_evidence` +
  normalization are applied BEFORE both storage and the `evidence_updated`
  material-change comparison — redacted-normalized is compared to
  redacted-normalized, so a secret-bearing quote can never cause per-run
  `evidence_updated` churn.

Queue = **event log** (append-only). Reducer rule (test-pinned): **machine
events never override human events; human events override each other in
order.** Machine events = `detected`/`evidence_updated`/`proposed`; human
events = `accepted`/`declined`/`filed`. Once ANY human event exists for a key,
machine events can never change its state ("latest wins" alone would let a
routine re-detect overwrite a human's decline); a LATER human event always
applies — so an accepted-but-never-filed candidate is not stranded: the user
can later `disposition declined` (or `filed` when the WF1 issue lands). A key
with a human state is never re-PROPOSED by the machine (AC4 covers declined;
accepted/filed likewise). **Accepted-unfiled visibility:** the report ALWAYS
lists accepted-but-not-yet-filed candidates in a "pending WF1 action" section
— an accept interrupted before its WF1 handoff is surfaced on every subsequent
run, never silently parked (adversarial p3 catch):
```json
{"schema_version": 1, "ts": "<ISO>", "run_id": "<ISO-invocation-id>",
 "event": "detected|evidence_updated|proposed|accepted|declined|filed",
 "candidate_key": "<sha256>", "detector": "friction|note_commands|error_proxy",
 "canonical_pattern": "<normalized>", "title": "<short>",
 "evidence": [{"session_id": "...", "quote": "<=500 chars", "source": "index|notes"}],
 "distinct_sessions": N, "coverage": {"returned_rows": N, "requested_limit": N, "limit_hit": false},
 "note": "<optional>"}
```
`evidence_updated` is appended only when the normalized evidence set or the
distinct-session count materially changes; otherwise a re-run appends nothing.

CLI subcommands (subprocess-tested): `detect` (runs the three index/notes
detectors, appends `detected`/`evidence_updated` events, prints summary),
`propose` (recurrence ≥ 3 distinct sessions AND not suppressed → prints
candidates with evidence + coverage), `disposition <candidate_key>
<accepted|declined|filed> [--note]` (appends the gate outcome; `accepted` at
the human gate, `filed` once a WF1 issue number exists — both terminal, so an
accepted-but-not-yet-filed candidate is never re-proposed either). No
`.rawgentic.json` dependency; every subcommand accepts `--queue PATH`
(default: workspace `claude_docs/.mining/candidates.jsonl`) so tests and the
live verification run against a throwaway queue — a test disposition must
never write a permanent state into the real one. **NO `<config-loading>`
block** (the WF14
mirror covers the step skeleton and report lifecycle ONLY — the config-loading
canary count, sync_shared_blocks MANIFEST, and the README "8 config-driven
skills" string all stay unchanged); workspace root resolved as in
`session_index.py`.

## Skill: `skills/session-mining/SKILL.md` — WF17

Frontmatter: `name: rawgentic:session-mining`, description prefixed `WF17 —`,
WHEN-triggers ("mine sessions for skill candidates", "what patterns keep
recurring", after campaigns), argument-hint. H1 `# WF17: Session Mining`.
5-step skeleton mirroring WF14: Step 1 Detect (freshness-check the #375 index
first — run `session_index.py index` if stale — then `detect`), Step 2 Dedup +
threshold (`propose`), Step 3 Render report (md+html to
`docs/reviews/session-mining-<date>.md`, `render_artifact.py --style report`,
Artifact best-effort with WF14's exact failure line), Step 4 Gate (present
candidates; per candidate: **accept → run `disposition <key> accepted`
(recorded BEFORE the WF1 handoff)**, then emit a WF1 template-shaped draft
prompt (Description/Acceptance Criteria/Scope/Affected Components/Risk,
conventional title) — **stated explicitly: WF1 has no pre-drafted-body entry
path; the handoff is a prompt WF1 re-drafts from, and WF1's own dedup +
approval still run**; once the WF1 issue exists, `disposition <key> filed
--note "#N"`; decline → `disposition <key> declined`), Step 5 Close
(session-note marker).

Canonical drift-guarded sentences (AC1, AC7 — new
`tests/test_session_mining_clarity.py`, cloning `test_run_feedback_clarity.py`
mechanics: header-index slicing + whitespace normalize + one sentence per pin):
- Write surface: "WF17 is STRICTLY report-only — the ONLY file writes are the
  candidates queue (`claude_docs/.mining/candidates.jsonl`), the report
  `.md`/`.html` pair under `docs/reviews/`, the session-note DONE marker
  append, and — when Step 1 refreshes it — the #375 session-index store (a
  derived cache owned end-to-end by `session_index.py`; WF17 never writes it
  directly)." (The index refresh was initially omitted from this enumeration —
  adversarial catch; an exhaustive write-surface claim that misses a write is
  worse than a longer honest one.)
- Threshold: "A candidate is only PROPOSED for filing at recurrence ≥ 3
  distinct sessions."
- Gate: "Accepted candidates are routed to WF1 as a prepared draft; nothing is
  ever auto-filed — propose-then-approve, always."
- Re-propose rule: "A declined candidate is recorded in the queue and not
  re-proposed."
- Coverage honesty: "v1 does not inspect raw tool_use/tool_result payloads and
  cannot conclude that command sequences or tool errors are absent."

## Registration + diagram

add-skill surfaces: whitelist `./skills/session-mining` between **`scan` and
`session-recall`** (alphabetical: 'm' < 'r'); codex symlink; counts 17→18;
category **SDLC workflows** 7→8 (a numbered WFn workflow, like run-feedback) —
description breakdown `8 SDLC + 7 workspace + 1 planning + 2 security` in
BOTH `.claude-plugin/plugin.json` and `.claude-plugin/marketplace.json` AND
the codex `plugins/rawgentic/.codex-plugin/plugin.json` description (that
third copy is NOT test-guarded — silent drift if missed); **THREE** hardcoded
test literals asserting `"7 SDLC workflow skills"` bump to "8":
`test_adversarial_review_registration.py::test_descriptions_consistent_count`,
`::test_readme_count_strings_updated`, and
`tests/test_interview_skill.py::test_descriptions_account_for_interview_as_planning_skill`
— the third is the EXACT surface both WF14 Step-2 agents missed
(docs/reviews/run-feedback-wf2-337-2026-07-09.md:48); codex longDescription
17→18; README table row + evals fraction 9/18 + have-none list; version ×3 →
**3.34.0**; Changelog entry (exact §2 shape). Category confirmation
(deliberate, not inherited): SDLC because WF-numbered like run-feedback;
sibling session-recall stays workspace-management (no WF number).
**Workflow diagram:** WF17 skeletal registry entry required — `DATA.order` +
`wf17` block (`code:"WF17"`, real phase names from the SKILL, `skeletal:true`)
with non-empty `steps` on its version entry
(`test_every_ordered_workflow_has_nonempty_default_steps` fails loud
otherwise). This is the recorded diagram decision: skeletal entry, no REV
(REV history is WF2-spine-only; WF14 precedent).

## Error handling / failure modes

- `session_index` DB missing → skill runs Step 1's index first (explicit
  invocation); search exit 2 → surface, stop (never fabricate).
- Malformed queue/JSONL lines → skip + count, surfaced in the report (AC3
  spirit carried over).
- Report render failure → non-voiding (WF14 rule): keep the `.md`, record gap.
- Zero candidates → report says so honestly; no padding.

## Security implications

- Report + queue may quote session text → secrets-by-NAME discipline stated in
  the skill (quotes are verbatim EVIDENCE — the skill instructs redacting any
  credential VALUE to its name before the quote lands in queue/report).
- Queue is local, gitignored (`claude_docs/.mining/` added to the workspace
  `.gitignore` beside `.session-index/` — same inert-for-git honesty note).
- No egress. Subprocess surface = exactly two sibling hooks, list-form argv,
  no shell: `session_index.py` (search/index/status) and `render_artifact.py`
  (report render). Plus one direct **read-only** SQLite open of the #375 DB
  (`mode=ro` URI) for verbatim quote resolution — no raw session-file access.

## Platform / external dependencies

platform_apis:
- api: subprocess invocation of hooks/session_index.py (search --literal --limit N --json; index; status) and of render_artifact.py --style report (list-form argv, no shell)
  feasibility: verified via spike — all invocations EXCEPT `--style report` exercised live this session (2026-07-10): search "permission denied" --literal --limit 500 --json returned 264 rows carrying exactly the detector-consumed fields (session_id, ts, project, role, snippet, path, line_no, score); `index` and `status` run end-to-end against the real corpus in the #375 Task-4 live verification; render_artifact executed twice (--style design, --style roadmap) through the shared template registry. `--style report` itself is corroborated (not live-run this session) by WF14's shipped output docs/reviews/run-feedback-wf2-338-2026-07-10.html — it is exercised live at implementation (the skill's own report render is the test)
  failure: fail-loud
- api: read-only SQLite open of the #375 DB (sqlite3 mode=ro URI; SELECT messages.text JOIN files ON path+line_no) for verbatim quote resolution
  feasibility: verified via spike — same 2026-07-10 live run: the JOIN returned the full verbatim message text (3,622 chars) containing the matched phrase
  failure: fail-loud
- api: O_APPEND single-line JSONL writes under workspace claude_docs/.mining/ (no lock — see queue design)
  feasibility: verified via existing-call-site — identical idiom in plan_lib.append_review_log and work_summary.persist_record; same-filesystem convention as session notes
  failure: fail-loud

(No new deps, no network, no daemon, no hook-event registration.)

## Verification map (AC → test)

| AC | Test |
|----|------|
| 1 report-only writes | drift-guard pin on the write-surface sentence + CLI test asserting detect/propose/disposition touch only the queue |
| 2 verbatim evidence + recurrence | unit: Signal carries session_id+quote; propose output includes counts; no candidate without evidence rows |
| 3 dedupe vs skills + queue | unit: dedupe_candidates suppression both branches |
| 4 declined never re-proposed | CLI: disposition declined → subsequent propose omits pattern_key |
| 5 WF1 draft shape | unit: draft emitter output contains the 5 template sections + conventional title |
| 6 detectors unit+CLI tested; registration green | test modules + guard suite |
| 7 threshold + gate drift-guarded | test_session_mining_clarity.py canonical-sentence pins |
