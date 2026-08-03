# #856 — implementation plan (PR 1 of 3)

**Design:** `docs/planning/2026-08-03-856-wf2-prose-to-guards.md`
**Branch:** `chore/856-wf2-prose-obligation-budget`
**Scope:** PR 1 only — AC1 (generated inventory), AC4 (measured before + CI budget), AC5
(characterization freeze + loader hardening). No workflow prose is edited.

Baseline to record after the branch is cut (Step 7), per `<test-run-discipline>`.

## Multi-PR decomposition

| PR | ACs | gate |
|---|---|---|
| **1 (this)** | 1, 4, 5 | none — additive only |
| 2 | 2 | after #855 lands |
| 3 | 3 | after #855 lands |

## Tasks

### Task 1: Fixture + RED test for metrics over the REUSED corpus reader
- riskLevel: standard
- files: tests/test_skill_prose_budget.py
- RED: assert `skill_prose_budget.corpus_metrics(...)` reports the measured totals, and that the
  production corpus reader (`skill_registration_check.skill_corpus`) is byte-identical to
  `tests.corpus.skill_corpus("implement-feature")`. Fails with `ModuleNotFoundError` first.
- verification: `pytest tests/test_skill_prose_budget.py -q` — red, named reason
- commit: `test(856): red — prose metrics + corpus-reader drift guard`

### Task 2: `corpus_metrics` over the imported helpers
- riskLevel: standard
- files: hooks/skill_prose_budget.py
- GREEN for Task 1. **Imports** `skill_corpus` from `hooks/skill_registration_check.py` and
  `atomic_write_text` from `hooks/atomic_write_lib.py` — no third copy of either (design §3.3).
  `corpus_metrics` returns total AND per-file bytes/lines. Fail-closed docstring.
- verification: Task 1's test passes; metrics equal the measured 301,635 B / 3,529 lines
- commit: `feat(856): skill prose size metrics reusing the existing corpus + atomic-write helpers`

### Task 3: `extract_obligations` + `classify_clause`
- riskLevel: high (module boundary — every later PR depends on this extractor contract)
- files: hooks/skill_prose_budget.py, tests/test_skill_prose_budget.py
- RED then GREEN. Clause split on sentence terminators (not line breaks, so rewrapping is
  invisible); section resolved by nearest enclosing heading **tracking heading level** so
  `### Instructions` does not shadow its `## Step N` ancestor; `classify_clause` separates
  obligation from reassurance (`never` + blocks/gates/fails).
- verification: fixture-based unit tests — rewrap-invariance, heading-hierarchy, reassurance
  classification, boundary (empty file, no headings, XML-ish block open), malformed (undecodable)
- commit: `feat(856): obligation extraction with heading-hierarchy sections and reassurance split`

### Task 4: `inventory_digest` + `render_inventory`
- riskLevel: standard
- files: hooks/skill_prose_budget.py, tests/test_skill_prose_budget.py
- Digest = sha256 over ordered `(section_identity, normalized_clause)` **records**, case preserved
  inside code spans (design §3.1). IDs are **content fingerprints**, never document order. Inventory
  renders `id`/`clause`/`occurrences`/`section`/`class`/`guard`/`invocation`/`choke_point`, with
  `class` including `unclassified`; occurrences map many source lines to one id.
- verification: MUTATION TESTS are acceptance criteria — (a) a pure rewrap must PRESERVE the digest;
  (b) `## Step 4`->`## Step 5` must CHANGE it; (c) `DISPATCH`->`dispatch` must CHANGE it. Plus:
  digest changes on deletion; every source occurrence maps to some id; inserting an obligation does
  not renumber existing ids.
- commit: `feat(856): order-preserving obligation digest + generated inventory renderer`

### Task 5: CLI (`measure` / `inventory` / `check`)
- riskLevel: high (subprocess-exercised CLI plus `--out` path containment against traversal)
- files: hooks/skill_prose_budget.py, tests/test_skill_prose_budget.py
- Thin CLI over the pure core (`registry_prune.py` shape): `measure`, `inventory --out`,
  `inventory --check`, `check --policy <json>`. The policy carries `corpus_ceiling_bytes`,
  `per_file_ceiling_bytes`, `obligation_set_digest` and `baseline_inventory` (design §3.4) — a loose
  `--ceiling`/`--digest` pair cannot express per-file limits nor produce an added/removed diff.
  `check` cross-checks the baseline inventory's hash against the pinned digest, then diffs records.
  rc 1 on breach, rc 2 caller error. Fail-CLOSED on an unreadable corpus. `--out` canonicalized +
  containment-checked; writes go through `atomic_write_lib.atomic_write_text`.
- verification: `subprocess.run([sys.executable, CLI, ...])` for each verb; a traversal `--out`
  refused rc 2; an unpinned corpus file is a breach; a baseline whose hash != the pinned digest is
  refused; `inventory --check` fails on a stale committed artifact
- commit: `feat(856): skill_prose_budget CLI — measure, inventory, check (fail-closed)`

### Task 6: Generate and commit the inventory
- riskLevel: standard
- files: docs/planning/2026-08-03-856-wf2-directive-inventory.md
- Run `python3 hooks/skill_prose_budget.py inventory --skill implement-feature --out <path>`.
- verification: the committed file is byte-identical to a fresh regeneration (a test asserts this,
  so the doc cannot rot)
- commit: `docs(856): generated WF2 obligation inventory (AC1)`

### Task 7: The budget guard — ceilings + digest pin
- riskLevel: standard
- files: tests/test_skill_prose_budget.py
- Total ceiling AND per-file ceiling map at today's measured sizes; `OBLIGATION_SET_DIGEST` pinned;
  a test asserting the committed inventory equals a fresh render. Failure messages say "moved or
  deleted", and name actual-vs-ceiling per file.
- verification: full-suite green; EACH guard asserted to FAIL under an induced breach (a ceiling test
  that cannot fail is the fake-green pattern this design exists to avoid)
- commit: `test(856): assert the WF2 prose budget and obligation-set digest (AC4, AC5)`

### Task 8: Characterization hardening — remove the module-level prose read
- riskLevel: standard
- files: tests/test_feasibility_gate.py
- Move the module-level `steps.md` read (`:264`) inside the four tests that use it, so a later split
  cannot take 25 unrelated `plan_lib` unit tests down with a collection error.
- verification: `pytest tests/test_feasibility_gate.py -q` — all 29 still pass; simulate a missing
  `steps.md` and confirm only the 4 prose tests fail, not collection
- commit: `test(856): scope test_feasibility_gate's steps.md read to the tests that use it`

### Task 9: Version, changelog, docs
- riskLevel: standard
- files: .claude-plugin/plugin.json, plugins/rawgentic/.codex-plugin/plugin.json, phase_executor/src/phase_executor/canary.py, tests/hooks/test_adversarial_review_registration.py, README.md
- Minor bump (feat). README changelog entry in the exact repo shape, including the diagram decision
  and `Suite old→new`. Diagram decision: **no workflow-spine change → no diagram REV** (PR 1 edits
  no step, gate, or loop-back).
- verification: `pytest tests/hooks/test_adversarial_review_registration.py tests/phase_executor/test_canary_digest_pin.py tests/phase_executor/test_canary_evidence.py -q`
- commit: `chore(856): release <version> — WF2 prose obligation budget`

## Risk calibration

9 tasks, 2 high → 22.2%, inside the documented 15–30% target band.

## Verification strategy

Per task above. No `deferred-to-target` verification: everything here runs locally in CI's own
environment (pure Python + pytest, no platform surface). Step 9 re-runs the full suite and diffs
against the Step-7 baseline.
