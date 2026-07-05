# Adversarial Review — .rawgentic-design-131.md

- Date: 2026-07-03
- Artifact type: design
- Reviewer: Codex (model config-default, reasoning effort high)
- Findings: 5 (Critical 0, High 2, Medium 3, Low 0)

## Summary

The design adds a diff artifact type and wires adversarial diff review into WF2 Step 11, but the Step 11 integration has a few load-bearing contract gaps that can make the new review silently absent or unmergeable.

## Findings

### 1. [High] correctness · high confidence — Error handling / failure modes

> Oversized diff: engine truncates at RAWGENTIC_ADV_REVIEW_MAX_BYTES (default 200KB) with a report warning — documented; reviewer sees the head of the diff.

For a security review, head-only truncation can omit the actual high-risk changed files that caused the gate to run. Implemented as written, a large diff with low-risk files first and auth/security changes later can pass through adversarial review without the reviewer seeing the relevant change, while the workflow still records a valid review outcome.

**Recommendation:** In `Error handling / failure modes`, change oversized diff handling for `diff` artifacts to preserve all high-risk matching file hunks first, then fill remaining budget with the rest of the diff; if any gated high-risk file is omitted, mark the adversarial review as `failed (truncated high-risk diff)` instead of `no_findings` or `findings_present`.

### 2. [High] internal-consistency · high confidence — File changes, item 4, Dispatch / Join

> run `review --artifact <patch> --type diff --project-root <root> --date <date>` in the background

The dispatch command omits the new `--findings-json <path>` option, but the join contract requires reading that sidecar on success. Implemented literally, a successful review can produce only the markdown report, leaving no structured file for Step 11 to merge, so adversarial findings are dropped or the join path fails.

**Recommendation:** In `skills/implement-feature/SKILL.md` Step 11 Dispatch, change the command to include a concrete sidecar path, e.g. `review --artifact <patch> --type diff --project-root <root> --date <date> --findings-json <sidecar>`, and define `<sidecar>` in the same temp-file cleanup contract.

### 3. [Medium] ambiguity · high confidence — File changes, item 4, Gate

> **Gate (evaluate up front, before dispatching the 3 review agents):** project opt-in (`adversarial_review_lib.py is-enabled … --skill implement-feature`) AND security-surface diff: `git diff --name-only origin/<default>..HEAD` has a path matching `plan_lib.any_high_risk_path` OR the plan contains any `riskLevel: high` task. Either gate fails → skip silently.

Operator precedence is ambiguous. Readers can parse this as `(opt-in AND high-risk path) OR high-risk task`, which would bypass the project opt-in for plans containing `riskLevel: high`, or as `opt-in AND (high-risk path OR high-risk task)`. Those produce different dispatch behavior for the same workflow.

**Recommendation:** In `skills/implement-feature/SKILL.md` Step 11 Gate, rewrite the condition explicitly as `project opt-in AND (security-surface diff OR any plan task has riskLevel: high)`.
**Ambiguity:** The prose uses `AND ... OR` without parentheses around the intended grouping.

### 4. [Medium] completeness · high confidence — File changes, item 4, Join / Non-success

> **Join:** on exit 0, read the `--findings-json` sidecar and merge its findings into the Step 11 finding list tagged `source: adversarial` before item 4

The join path only specifies behavior for process exit 0 and for non-zero exits. It does not specify what to do if the process exits 0 but the sidecar is missing, malformed, empty, or fails normalization. That creates an implementation fork: treat it as failed, ignore it, or crash Step 11.

**Recommendation:** In `skills/implement-feature/SKILL.md` Step 11 Join, add that a missing, unreadable, invalid, or schema-invalid sidecar after exit 0 must be recorded as `failed (<reason>)`, logged loudly, and must not be interpreted as `no_findings`.

### 5. [Medium] security · high confidence — File changes, item 4, Dispatch / Cleanup

> write the full diff to `.rawgentic-diff-review-<issue>.patch` under the project root

The temp patch path is predictable and lives under the project root while containing the full source diff, potentially including secrets. The design does not specify restrictive permissions, collision handling, symlink handling, or whether the file is ignored by VCS, so an implementation could expose proprietary code or secrets through the workspace or accidentally include the patch in later diffs.

**Recommendation:** In `skills/implement-feature/SKILL.md` Step 11 Dispatch/Cleanup, specify creation with exclusive mode and restrictive permissions, e.g. `0600` via a safe temp-file API under an ignored `.rawgentic/tmp/` directory, reject symlinks, and always remove both patch and sidecar in a finally-style cleanup path.

---
_Report-only: this review does not edit the artifact. Findings are advisory; incorporate them at your discretion._