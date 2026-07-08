# Adversarial Review — .rawgentic-diff-review-303-e466f037.patch

- Date: 2026-07-08
- Artifact type: diff
- Reviewer: Codex (model config-default, reasoning effort high)
- Findings: 2 (Critical 0, High 0, Medium 2, Low 0)

## Summary

The diff adds age-based suppression for WAL recovery INTENT announcements while preserving entries on disk. The main risk is that one documented/runtime visibility path is made vacuous by the existing stderr suppression around the whole WAL operation.

## Findings

### 1. [Medium] correctness · medium confidence — hooks/session-start all-suppressed branch

> +    elif [ "$SUPPRESSED_COUNT" -gt 0 ]; then

When every incomplete INTENT is stale, the code emits only a generic `WAL note` and omits the `WAL RECOVERY` header and recovery instruction. That weakens the stated 'visible suppressed-count line' behavior because a session can have unresolved prior operations but no recovery notice, only a note. The concrete failure is that users or downstream checks looking for WAL recovery context may miss that incomplete operations still exist.

**Recommendation:** Change the all-suppressed branch to use the same `WAL RECOVERY` framing, e.g. `WAL RECOVERY: 0 recent incomplete operation(s); N older incomplete INTENT(s) suppressed...`, so the recovery condition remains explicit even when no entries are shown.

### 2. [Medium] internal-consistency · high confidence — hooks/session-start WAL_RECOVERY_MAX_AGE_DAYS validation

> +            echo "session-start: WAL_RECOVERY_MAX_AGE_DAYS='$MAX_AGE_DAYS' is not a positive integer — using default 7" >&2

The change claims malformed `WAL_RECOVERY_MAX_AGE_DAYS` values warn to stderr, but `_do_wal_ops` is invoked with all stderr discarded, so this warning is never visible to the caller. The concrete failure is that invalid configuration silently falls back despite the new documented warning behavior.

**Recommendation:** Change the `_do_wal_ops` invocation or this validation block so configuration warnings survive the outer stderr suppression, for example by collecting non-fatal warning text into context or by not redirecting `_do_wal_ops` stderr wholesale when emitting explicit user-facing warnings.

---
_Report-only: this review does not edit the artifact. Findings are advisory; incorporate them at your discretion._