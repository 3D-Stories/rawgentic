# Design: first-class deferred-to-target verification state (#138)

Date: 2026-07-04 · Issue #138 · Complexity: standard_feature (M — touches gate honesty) · lean gate

## Problem

WF2's gates assume verification runs locally: TDD mode needs runnable tests, Implement-Verify needs a runnable command. Neither expresses "the dev env fundamentally cannot exercise this artifact" (NSIS uninstaller with no `makensis`; native Win32 paste built from WSL for a Windows target; an OS-native tray menu that can't render headless — all from the SayStory campaign). Today that honest outcome ("compiles; must be verified on the target box") has no legible slot — it degrades into a hand-waved pass or an awkwardly skipped gate. Both are dishonest. Operating principle: **a proxy you can run is never the path you can't** — so the gap must be *named*, never papered over.

## The core invariant (anti-abuse)

Deferral must be **impossible to use as a skip-everything lever**. So: a deferred task STILL requires its best local proxy (compile, typecheck, unit tests of extractable logic); deferral covers only the *unexercisable remainder*, and the deferred surface never counts as verified. The gate is satisfied only when the deferral is *recorded* (reason + what-was-run + the manual target check); an *unrecorded* deferral is a gate failure.

## Approach (additive, fail-open absent / fail-closed malformed)

### 1. Plan contract (`hooks/plan_lib.py`)
A task may declare one line: `- verification: deferred-to-target (<reason>)`. Parsing:
- Add `deferral_reason: str | None = None` to the `Task` dataclass (None = not deferred; str = deferred with that reason). Purely additive, hashable (str), backward-compatible.
- New `_VERIFICATION_DEFERRAL_RE` matches `- verification: deferred-to-target (<reason>)` (case-insensitive on the keyword, reason = the parenthesized text). On match with a **non-empty** reason → set `deferral_reason`.
- **Fail-closed malformed:** a `- verification: deferred-to-target` line with NO parenthesized reason (or an empty one) → `PlanFormatError` (the reason is mandatory for honesty).
- **Free-text verification lines are untouched [Codex F3].** `parse_tasks` today parses only `riskLevel`/`parallel_group`/`files` — it has NO existing `- verification:` handling, so ordinary `- verification: <command>` lines are (and remain) ignored by the parser; they live in the plan prose and are consumed by the orchestrator reading the plan for Implement-Verify mode, not by `parse_tasks`. This change ONLY adds extraction of the `deferred-to-target` marker; it changes nothing about existing verification-command handling.
- Helper `deferred_tasks(tasks) -> list[Task]` returns tasks with `deferral_reason is not None` (used by Steps 9/12).
- **Mechanical gate helper `assert_deferrals_recorded(plan_tasks, recorded_entries) -> (ok, errors)` [Codex F2]** (mirrors `assert_review_coverage`): every deferred plan-task id must appear **exactly once** in `recorded_entries` (the run-record's `verification_deferred` list). Fails on missing, duplicate, or foreign (recorded-but-not-planned) task ids. This is the concrete mechanism the completion gate calls to detect an unrecorded deferral — not a human audit.
- Absent field → `deferral_reason=None`, no error (fail-open). The pre-P15 defaulting path also carries `deferral_reason=None`.

### 2. Step 9 Part B (SKILL.md)
When a task is deferred: list it explicitly with its reason AND the local proxy that WAS run (compile/typecheck/extractable unit tests). Deferred tasks **never count as verified** and **never fail the gate by themselves**. A deferred task with NO local proxy evidence recorded → the gate is not satisfied (deferral is not a pass; the proxy is still required). It is impossible to silently convert deferred → passed: the run-record separates the counts (below).

### 3. Step 12 PR body (SKILL.md)
A dedicated section with the **exact** heading `## Deferred verification` [Codex F4 — one canonical string everywhere; the drift guard asserts this literal], emitted only when `deferred_tasks` is non-empty, enumerating each deferred item + the exact manual check the human must run on the target (command/steps). Empty → section omitted.

### 4. Step 16 run-record (`hooks/work_summary.py` + `references/run-record.md`)
Add optional top-level `verification_deferred` as a **structured list** [Codex F1 — a bare count can't tell WHICH tasks were recorded or whether their evidence exists, so a count could be satisfied while the required evidence is missing]:
```json
"verification_deferred": [{"task_id": "2", "reason": "...", "local_proxy": "...", "target_check": "..."}]
```
Validation (only when present; absent → old records stay valid): a list; each item a dict with non-empty string `task_id`, `reason`, `local_proxy`, `target_check`; task_ids distinct. `render_summary` lists each deferred item ("Verification deferred (must be checked on target): <task_id> — <reason>") when non-empty.

### 5. Completion gate (SKILL.md `<completion-gate>`) — explicit algorithm [Codex F2]
Not a human audit. The gate calls `plan_lib.assert_deferrals_recorded(deferred_tasks(plan), record["verification_deferred"])`: every plan-deferred task id must appear exactly once in the run-record list (and each entry carries reason/local_proxy/target_check per §4 validation). **All deferred tasks recorded → gate satisfied-with-note; any missing/duplicate/foreign → gate failure.** The PR "Deferred verification" section is generated from the same list, so the three surfaces cannot diverge.

## Files
- `hooks/plan_lib.py`: `Task.deferral_reason`, `_VERIFICATION_DEFERRAL_RE`, parse in `parse_tasks`, `deferred_tasks` helper, `assert_deferrals_recorded` gate helper.
- `hooks/work_summary.py`: validate optional structured `verification_deferred` list; render each item.
- `skills/implement-feature/references/run-record.md`: document the field (list shape).
- `skills/implement-feature/SKILL.md`: Step 5 (declare deferral when env can't exercise), Step 9 Part B (list + proxy, never verified), Step 12 (`## Deferred verification` PR section), `<completion-gate>` (call `assert_deferrals_recorded`; recorded=note / unrecorded=fail).
- Tests: `parse_tasks` deferral (match, missing-reason malformed → raise, absent → None, free-text ignored); `deferred_tasks`; `assert_deferrals_recorded` (all-recorded/missing/duplicate/foreign); `validate_record` accepts valid list / rejects malformed item / old record still valid; SKILL drift guards (Step 9, `## Deferred verification` literal, completion-gate helper reference).
- Version bump minor → 2.50.0.

## AC coverage
AC1 plan format + parse (additive/fail-open/fail-closed) → §1 + parse tests. AC2 Step 9 lists deferred w/ proxy, no silent pass → §2 + drift guard. AC3 PR section omitted-when-empty, canonical heading → §3 + drift guard. AC4 run-record structured schema + old records valid → §4 + validate tests. AC5 tests → the matrix. Anti-abuse invariant → `assert_deferrals_recorded` makes "unrecorded deferral = failure" mechanically enforced, not audited.

## Codex adversarial review — findings folded (2026-07-04, 0 Critical / 2 High / 2 Medium)
1. [High] count can't identify which deferred tasks were recorded → run-record is now a structured list `{task_id,reason,local_proxy,target_check}`.
2. [High] no mechanism to detect an unrecorded deferral → `assert_deferrals_recorded` gate helper (exact-once id match), called by `<completion-gate>`.
3. [Medium] free-text verification handling ambiguous → clarified: `parse_tasks` has no existing `- verification:` parsing; free-text lines stay ignored, deferral extraction is purely additive.
4. [Medium] heading casing drift → one canonical `## Deferred verification` everywhere; drift guard asserts the literal.
No Critical/blocker → no design loop-back (lean policy); folded in place.

## Out of scope
Remote-target execution (SSH-to-target verification is a separate feature); WF3.
