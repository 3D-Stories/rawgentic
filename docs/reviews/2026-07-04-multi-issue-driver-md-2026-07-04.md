# Adversarial Review — 2026-07-04-multi-issue-driver.md

- Date: 2026-07-04
- Artifact type: design
- Reviewer: Codex (model config-default, reasoning effort high)
- Findings: 4 (Critical 0, High 1, Medium 3, Low 0)

## Summary

The artifact proposes a documented multi-issue orchestration pattern plus a committed queue state file, but several state-machine and resumption details are internally inconsistent or delegated to unspecified future text. The largest risk is that the headless path cannot be represented by the schema as written.

## Findings

### 1. [High] internal-consistency · high confidence — Queue schema (AC1) / Headless interplay

> Status transitions: `queued → in_progress → {merged | deferred | abandoned}`. Only one issue `in_progress` at a time (serial; parallel is out of scope — needs worktrees, #136/#85).

The declared state machine excludes `pr_open`, but the Headless interplay section later requires `in_progress → pr_open`. A headless implementation following the schema cannot persist the required terminal PR state, so validation, resumption, or queue advancement will either reject valid headless runs or mark them incorrectly.

**Recommendation:** In `Queue schema (AC1)`, add `pr_open` to the allowed status values and update the transition rule to include `in_progress → pr_open`, with explicit allowed next states from `pr_open`.

### 2. [Medium] ambiguity · high confidence — DEFER taxonomy (AC1)

> A deferred issue's branch is left pushed (if any commits) or discarded per its rollback anchor; the ledger records the type + reason so resumption knows why.

The DEFER behavior leaves an implementation choice between preserving and discarding a partially built branch, but does not define who decides, when it happens, or how the queue records which path was taken. Resumption can therefore mis-handle a deferred issue by assuming a branch exists when it was discarded, or by losing committed work that was expected to remain available.

**Recommendation:** In `DEFER taxonomy (AC1)`, add explicit fields such as `deferred_branch`, `branch_preservation: pushed|discarded|none`, and a deterministic rule for when each value is used.
**Ambiguity:** The phrase gives two possible branch outcomes without specifying the decision rule or persisted state.

### 3. [Medium] completeness · high confidence — Resumption across sessions (AC1)

> The pattern doc specifies the exact reconciliation, mirroring WF2's own `resume_lib` precedence so a half-done issue resumes at the right point rather than restarting.

The artifact claims exact reconciliation is specified, but this design text does not provide the reconciliation rules and instead depends on an external `resume_lib` precedence that is not included in the provided artifact. An implementer cannot determine from this design how to resolve conflicting states such as local branch exists, PR open, queue says `in_progress`, and remote already merged.

**Recommendation:** In `Resumption across sessions (AC1)`, include the actual ordered reconciliation table for queue status, branch state, PR state, and merge state, instead of deferring to `resume_lib` by reference.

### 4. [Medium] completeness · medium confidence — Deploy policy options (AC1)

> Requires `has_deploy` + a smoke gate per issue.

The deploy policy introduces required capabilities, but neither `has_deploy` nor the smoke gate is present in the queue schema or policy object. A driver implementing `per-issue` cannot tell from the committed state file whether deploy is available or what gate must pass before advancing.

**Recommendation:** In `Queue schema (AC1)`, extend `policy` with explicit `has_deploy` and `smoke_gate` fields, or state that those values are supplied by a named external config and define how missing values are handled.

---
_Report-only: this review does not edit the artifact. Findings are advisory; incorporate them at your discretion._