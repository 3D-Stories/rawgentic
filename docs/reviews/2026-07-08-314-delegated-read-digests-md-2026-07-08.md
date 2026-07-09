# Adversarial Review — 2026-07-08-314-delegated-read-digests.md

- Date: 2026-07-08
- Artifact type: design
- Reviewer: Codex (model config-default, reasoning effort high)
- Findings: 4 (Critical 0, High 1, Medium 3, Low 0)

## Summary

The artifact designs delegated/raw-read reduction for workflow steps using projections and validated LLM-produced indexes. The main risks are unverifiable platform feasibility claims and one silent-failure dependency that is acknowledged but not made fail-loud at runtime.

## Findings

### 1. [High] feasibility · high confidence — Platform / external dependencies

> - api: temp artifact files mode 0600 under project root + stale sweep + .git/info/exclude
>   feasibility: verified via existing-call-site — Step 11 item 1a creates .rawgentic-diff-review-<issue>-<token>.patch with exactly this discipline (steps.md:1129-1153: 0600, stale sweep at step start, finally-clean, .git/info/exclude append). Implementation extends the 1a sweep glob list to .rawgentic-read-* and adds a test proving the new pattern is swept and git-ignored (adversarial finding 5).
>   failure: fail-silent

The platform dependency is explicitly marked fail-silent. If chmod, stale sweep, final cleanup, or .git/info/exclude append silently fails, raw diff/scan artifacts can remain readable or become trackable without blocking the workflow. The following `surface` text only mentions sweep marker/test coverage, not a runtime assertion that 0600 and git exclusion actually succeeded when creating each temp artifact.

**Recommendation:** In `Platform / external dependencies`, change this dependency’s failure mode to fail-loud and require runtime checks: assert file mode is 0600 after creation, assert `.git/info/exclude` contains the `.rawgentic-read-*` pattern or fail, log cleanup failures, and count cleanup/exclude failures in the run-record.

### 2. [Medium] ambiguity · high confidence — A/B experiment

> Quality: mechanical items scored against git ground truth (`--name-only`,
>   `--diff-filter`); judgment items scored by an INDEPENDENT blinded opus judge comparing
>   each arm's answers against the raw diff (judge sees answers unlabeled). Relative
>   pooled score delta |B−A| ≤ 5%, and no single artifact regressing beyond 10%.

The A/B acceptance bar depends on a numeric `pooled score`, but the artifact does not define the scoring rubric, scale, weighting between mechanical and judgment items, or how the independent judge converts answers into percentages. Implementers cannot reproduce or enforce the ±5% and 10% thresholds consistently.

**Recommendation:** In `A/B experiment`, add a scoring schema: list each questionnaire item, assign points or weights, define how partial credit is awarded, define pooled-score calculation, and state how judge disagreements or ambiguous answers are handled.
**Ambiguity:** The artifact names thresholds but leaves the metric that those thresholds apply to undefined.

### 3. [Medium] feasibility · high confidence — Platform / external dependencies

> - api: wc -c on piped command output (bash)
>   feasibility: verified via existing-call-site — coreutils invocations are pervasive in hooks/ bash (e.g. secret-scan.sh, wal-lib.sh pipelines); wc is POSIX coreutils present on this host (used live this session).
>   failure: fail-loud

The claimed proof is not an exact-object-kind in-repo call site for `wc -c` on piped artifact output. It relies on general coreutils usage and an uncited live-session claim. Under the stated platform-feasibility standard, this dependency is assumed rather than proven for the project’s real execution surfaces, so the delegation trigger may be implemented without a verified CI/workflow capability.

**Recommendation:** In `Platform / external dependencies`, replace the feasibility proof with a cited exact call site or add a required spike/test that runs the same `git diff … | wc -c` pipeline in the workflow environment and asserts a numeric byte count plus nonzero command-status surfacing.

### 4. [Medium] feasibility · high confidence — Platform / external dependencies

> - api: grep -F fixed-string matching for evidence verification
>   feasibility: verified via existing-call-site — grep used throughout hooks/ (wal-guard, session-start); -F is POSIX.
>   failure: fail-loud

The feasibility proof cites generic `grep` usage, not an exact in-project call site for `grep -F` over validator-controlled evidence strings and target files. Because fabricated quote rejection depends on this exact fixed-string behavior and exit-status handling, the artifact has not proven the project permits the specific API shape it relies on.

**Recommendation:** In `Platform / external dependencies`, cite an existing exact `grep -F` call site with exit-status checking, or add a validator test/spike that exercises `grep -F` against evidence text containing shell-sensitive characters and asserts rejection on no match.

---
_Report-only: this review does not edit the artifact. Findings are advisory; incorporate them at your discretion._