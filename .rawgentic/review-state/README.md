# P15 review-state pointers

Per-branch committed status pointers for the WF2 tiered code review (P15).
Each in-flight feature branch writes its own file here:

```
.rawgentic/review-state/<branch-sanitized>.json
```

where `<branch-sanitized>` is the branch name with `/` and other path-unsafe
characters replaced by `-`. For example:

```
.rawgentic/review-state/feature-73-tiered-code-review.json
```

## Schema (v1)

```json
{
  "schema_version": 1,
  "branch": "feature/73-tiered-code-review",
  "last_review_log_status": "applied | suspended | dispatch_failed",
  "ts": "ISO-8601 UTC"
}
```

## Why per-branch and not a singleton?

A singleton file would conflict on every multi-PR workflow and would couple
two unrelated branches' review states. Per-branch files merge cleanly. The
Step 12/14 gate reads `<branch-sanitized>.json` and additionally verifies
`state.branch == current_branch` before trusting `last_review_log_status` —
a safety check against a misnamed file from another branch.

## Lifecycle

Step 14 (merge) deletes the branch's pointer file as part of merge cleanup
along with `claude_docs/.wf2-state/<issue>/`.
