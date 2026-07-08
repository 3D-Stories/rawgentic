# Adversarial Review — .rawgentic-rca-310.md

- Date: 2026-07-08
- Artifact type: plan
- Reviewer: Codex (model config-default, reasoning effort high)
- Findings: 3 (Critical 0, High 1, Medium 2, Low 0)

## Summary

The artifact proposes truncating blocked-command text before passing it to jq to prevent argv-limit E2BIG fail-open behavior. The main risk is that the proposed fix narrows one oversized-argv path but leaves the documented empty-output allow behavior as an unaddressed fail-open class.

## Findings

### 1. [High] completeness · high confidence — Symptom / Fix

> A Bash command >~128KiB matching a wal-guard deny pattern is ALLOWED: deny() emits no JSON decision, harness treats empty output as allow.

The fix does not specify changing the harness or deny() contract so empty output from any future jq/exec failure is denied. Implemented as written, the same fail-open outcome remains possible whenever deny() emits no JSON for any reason other than this exact oversized-command path.

**Recommendation:** In `Fix`, add a required fail-closed fallback: if deny() cannot produce a valid JSON decision, emit a minimal deny decision without jq or make the harness treat empty/invalid guard output as deny. Add an acceptance test that forces deny() stdout empty and verifies the command is blocked.

### 2. [Medium] completeness · medium confidence — Parallel paths checked

> Parallel paths checked
> INTENT summary already truncated; wal-context/wal-stop/session-start/wal-bind-guard --arg values are bounded (fixed strings/paths). reason strings are fixed pattern text (small).

The parallel-path check is only a conclusion and does not enumerate the actual `--arg` call sites or their maximum lengths. Since the issue is specifically platform argv feasibility, this leaves the claim unverifiable from the artifact and risks missing another oversized jq argument path.

**Recommendation:** Expand `Parallel paths checked` into a table listing each jq `--arg` call site, the value source, its maximum length under real config, and the test or code constraint proving that bound.

### 3. [Medium] feasibility · medium confidence — Fix

> Both jq sites inherit the bound.

The artifact asserts the two jq invocations are safe after command truncation, but it does not show the resulting maximum size of the full `--arg reason "$msg"` payload after pattern text, JSON framing inputs, and the appended truncation suffix are included. A reader cannot verify from the provided text that the real argv element is always below the platform limit under this project's actual deny patterns.

**Recommendation:** In `Fix`, state the computed upper bound for each jq argv element after truncation, including the largest deny pattern/reason prefix used by wal-guard, and add an acceptance check that validates the longest real deny pattern remains below the argv limit.

---
_Report-only: this review does not edit the artifact. Findings are advisory; incorporate them at your discretion._