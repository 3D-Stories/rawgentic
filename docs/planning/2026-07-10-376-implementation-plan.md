# Implementation plan: issue #376 (WF17 session-mining)

Branch: `feature/376-session-mining`
Design: `docs/planning/2026-07-10-376-session-mining-design.md` (rev 2 + verifier fixes)
Mode: TDD. Execution: sequential (shared files). Single PR ("Closes #376").
NOTE: loop-back budget for this issue is fully consumed at Step 4 (3/3) — any
design-flaw discovery in Steps 8/8a/11 escalates via the ERROR protocol, no
loop-back remains.

### Task 1: Pure core — detectors, identity, queue, redaction, dedupe
- riskLevel: high (deserialization of external data)
- files: hooks/session_mining_lib.py, tests/hooks/test_session_mining_lib.py
- RED: unit tests (direct import) for build_wf1_draft (candidate → WF1
  template-shaped draft: conventional title + the 5 sections — AC5's unit
  surface lives HERE, in the lib, not the skill prose), detect_friction
  (phrase rules over search-JSON rows), detect_note_commands (fenced/backtick
  commands; same-section UUID session-id resolution; evidence-only fallback),
  detect_error_proxies, canonical normalization + candidate_key determinism +
  identity_version-bump invariant, recurrence (distinct-session, coverage
  lower-bound/limit-hit), redact_evidence (hex/base64 ≥20, KEY=value,
  Bearer; [redacted:kind]), dedupe_candidates (terminal suppress; Jaccard
  0.6/0.3 strong/borderline/fresh), reducer (human-over-machine; later human
  event wins; machine never overrides human), queue_append torn-tail truncate
  (crash fixture: torn fragment removed, file all-parseable), reduce_queue
  QueueCorruption raise on non-tail corruption + malformed_tail on torn tail.
- Verify: module tests green; full suite green.
- Commit: `feat(hooks): session_mining_lib pure core — detectors, event-log queue, identity (#376)`

### Task 2: CLI — detect / propose / disposition + quote resolution
- riskLevel: high (persistence + module boundary)
- files: hooks/session_mining_lib.py, tests/hooks/test_session_mining_lib.py
- RED: subprocess CLI tests — detect (invokes session_index search --literal
  --json via injected runner in unit tests + real subprocess in CLI tests over
  a tmp corpus indexed by session_index; appends detected/evidence_updated;
  unconditional detected under corruption), propose (≥3 distinct sessions;
  suppressed/borderline/fresh; coverage printed; exit 2 on QueueCorruption;
  accepted-unfiled listed as pending), disposition (accepted/declined/filed;
  declined never re-proposed — AC4 end-to-end), ro-DB verbatim quote
  resolution (mode=ro JOIN path+line_no; matched-phrase window), exit codes.
- Verify: module tests green; full suite green.
- Commit: `feat(hooks): session_mining CLI — detect/propose/disposition (#376)`

### Task 3: WF17 skill + clarity drift guards
- riskLevel: standard
- files: skills/session-mining/SKILL.md, tests/test_session_mining_clarity.py
- RED: test_session_mining_clarity.py pins (header-index slice + whitespace
  normalize, one sentence per pin): write-surface enumeration (4 surfaces incl
  index refresh), recurrence ≥ 3 sentence, propose-then-approve sentence,
  declined-not-re-proposed sentence, coverage-honesty sentence, WF17 H1.
- GREEN: SKILL.md — frontmatter (WF17-prefixed trigger description,
  argument-hint), 5-step WF14-mirror skeleton (Step 1 freshness+detect, Step 2
  propose, Step 3 report render --style report + Artifact best-effort with
  WF14's exact failure line, Step 4 gate: disposition accepted → WF1
  template-shaped draft prompt → disposition filed; declined path, Step 5
  close marker), NO config-loading block, secrets-by-NAME gate review.
- Verify: clarity tests green; full suite green.
- Commit: `feat(skills): WF17 session-mining skill + clarity guards (#376)`

### Task 4: Registration surfaces + WF17 diagram entry + version
- riskLevel: standard
- files: .claude-plugin/marketplace.json, .claude-plugin/plugin.json, plugins/rawgentic/.codex-plugin/plugin.json, plugins/rawgentic/skills/session-mining, tests/hooks/test_adversarial_review_registration.py, tests/test_interview_skill.py, README.md, docs/workflow-diagram.html, tests/test_workflow_diagram.py
- RED: guard suite red after skill dir lands (whitelist==disk, counts, evals
  fraction, packaging); THREE "7 SDLC workflow skills" literals; version pin.
- GREEN: whitelist between scan and session-recall; symlink; descriptions
  8 SDLC ×3 copies (plugin.json, marketplace.json, codex description); codex
  longDescription 17→18; version 3.34.0 ×3; README (18 skills, SDLC bullet,
  table row in SDLC section, evals 9/18 + have-none, Changelog v3.34.0 exact
  shape incl diagram decision + Suite old→new); diagram: wf17 skeletal
  registry entry in DATA.order + block with real phase names + non-empty
  steps on revs[0] (+ registry test mirroring test_wf14_registry_entry_present).
- Verify: guard suite + test_workflow_diagram green; full suite green.
- Commit: `feat(skills): session-mining registration + WF17 diagram entry, v3.34.0 (#376)`

### Task 5: Docs, artifact, gitignore, live execution
- riskLevel: standard
- files: ../../.gitignore, docs/planning/2026-07-10-376-session-mining-design.md, docs/planning/2026-07-10-376-session-mining-design.html, docs/planning/2026-07-10-376-implementation-plan.md
- GREEN: workspace .gitignore `claude_docs/.mining/` line; render design html
  (--style design); commit design+plan docs; **live skill-command execution**
  (new-skill bar) — against the REAL corpus/index but an EXPLICIT TEMP QUEUE
  (`--queue <tmp>` — a test disposition must never pollute the real
  candidates queue with a permanent declined state; adversarial-plan catch):
  detect → propose → disposition declined → re-propose (AC4 live) → report
  render --style report.
- Verify: full suite green; live checks are CONCRETE: each CLI exits 0 (except
  documented exit-2 paths), propose output carries evidence quotes + session
  ids + recurrence + coverage line, declined key absent from second propose,
  report .md+.html pair exists and html renders the report template.
- Commit: `docs(planning): #376 design+plan docs, mining gitignore (#376)`

## Verification strategy summary
Every task: full suite after, delta vs baseline (recorded at Step 8 start).
AC→task: AC1→T3 (drift pin)+T2 (CLI write-surface), AC2→T1+T2, AC3→T1,
AC4→T1+T2 (+T5 live), AC5→T3 (draft emitter section test in T1), AC6→T1+T2+T4,
AC7→T3. Ordering/date/coverage semantics pinned in design; plan doesn't
restate. No deferred-to-target items.
