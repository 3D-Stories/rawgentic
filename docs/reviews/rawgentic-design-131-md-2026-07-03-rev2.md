# Adversarial Review — .rawgentic-design-131.md

- Date: 2026-07-03-rev2
- Artifact type: design
- Reviewer: Codex (model config-default, reasoning effort high)
- Findings: 4 (Critical 0, High 1, Medium 3, Low 0)

## Summary

The artifact designs an opt-in cross-model diff review for WF2 Step 11 by reusing the existing adversarial review engine with a new `diff` artifact type and a JSON sidecar. Main risks are hidden coupling to git remote state, an underspecified enablement probe, and ambiguous high-risk path pattern reporting that downstream markers/tests rely on.

## Findings

### 1. [High] completeness · high confidence — Section 4, Gate

> compute `changed_paths` from `git diff --name-only origin/<default_branch>..HEAD` — the SAME base ref used for the patch below, so detection and reviewed content are identical [Judge1 L5]

The design makes Step 11 depend on `origin/<default_branch>` being available and valid, but it does not specify what happens if that ref is missing, stale, unfetched, or the command exits non-zero. Implemented as written, the gate can fail before producing the required 4-state marker, which directly conflicts with the completion-gate goal of making “never ran” detectable.

**Recommendation:** In `skills/implement-feature/SKILL.md` Step 11 Gate, define failure handling for the `git diff --name-only origin/<default_branch>..HEAD` command: on non-zero exit, emit `failed (base ref unavailable: <reason>)`, skip dispatch, and satisfy the marker requirement with that failed state.

### 2. [Medium] ambiguity · high confidence — Section 4, Gate

> Call `plan_lib.should_run_diff_review(<is-enabled exit==0>, changed_paths, has_high_risk_task)`.

`<is-enabled exit==0>` is not defined anywhere in the artifact. A reasonable implementer has to infer which command or config check returns this exit code, which can produce inconsistent enablement behavior across Step 11 and the completion gate.

**Recommendation:** In `skills/implement-feature/SKILL.md` Step 11 Gate, replace `<is-enabled exit==0>` with the exact config/API call used to determine whether `adversarialReview.workflows` contains `implement-feature`, including its expected exit/status behavior.
**Ambiguity:** The artifact refers to an exit-code-producing enablement check without naming the command, function, or config reader.

### 3. [Medium] completeness · medium confidence — Section 2, Truth table

> `enabled and any_high_risk_path(changed_paths)` → `(True, "path <p> matches <pattern>")`

`any_high_risk_path` is specified to return only `str | None`, but `should_run_diff_review` is required to report both the matched path and the matched pattern. As written, the function interface does not provide the pattern needed for the documented reason string, so either the implementation must duplicate matcher logic or the reason becomes inaccurate.

**Recommendation:** In `hooks/plan_lib.py`, change `any_high_risk_path(paths, extra_patterns=()) -> str | None` to return a structured match such as `tuple[str, str] | None`, or change the truth-table reason to only require the matched path.

### 4. [Medium] security · high confidence — Section 4, Cleanup

> **Cleanup (always-run, finally-style):** delete patch + sidecar on every exit path, including failures before join [Judge3 M2]; `.gitignore` gains `.rawgentic-diff-review-*.patch` and `.rawgentic-diff-findings-*.json` as the staging backstop (repo currently has no gitignore coverage for these)

The design writes raw source diffs and structured findings under the project root, then relies on later cleanup and `.gitignore` as the backstop. If the process is killed or crashes outside a handled exit path, the diff containing proprietary source or secrets can remain on disk. The artifact names cleanup for “every exit path” but does not cover uncatchable termination or crash recovery.

**Recommendation:** In Section 4 Cleanup, require creation under a dedicated ignored temp directory with restrictive permissions, and add startup cleanup of stale `.rawgentic-diff-review-*` / `.rawgentic-diff-findings-*` files before each run.

---
_Report-only: this review does not edit the artifact. Findings are advisory; incorporate them at your discretion._