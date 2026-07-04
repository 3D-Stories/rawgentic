# WF2 run-record schema (Step 16)

Assembled at WF2 Step 16 and validated + persisted by `hooks/work_summary.py`. The tool is
the source of truth for the shape: on `rc == 1` it prints to stderr exactly which fields
are wrong (fix `/tmp/wf2-run-record.json` and re-run).

Every key below must be **present**; "nullable" means `null` is an allowed value, NOT that
the key may be omitted (a dropped field is a telemetry gap, not a null). Counts are
non-negative integers and `resolved` may not exceed `findings`:

```json
{
  "workflow": "implement-feature",
  "workflow_version": "<.claude-plugin/plugin.json version>",
  "issue": {"number": <issue # | null>, "type": "feature|bug|chore|other",
            "complexity": "trivial|standard|complex|null"},
  "changes": {"files_changed": N, "insertions": N|null, "deletions": N|null,
              "commits": N},
  "tests": {"added": N, "passing": N|null, "total": N|null},
  "gates": [
    {"step": "4",  "name": "Design Critique",       "findings": N, "resolved": N, "status": "pass|fail|skipped|fast_path"},
    {"step": "6",  "name": "Plan Drift",            "findings": N, "resolved": N, "status": "..."},
    {"step": "9",  "name": "Implementation Drift",  "findings": N, "resolved": N, "status": "..."},
    {"step": "11", "name": "Code Review",           "findings": N, "resolved": N, "status": "..."},
    {"step": "15", "name": "Post-Deploy",           "findings": N, "resolved": N, "status": "..."}
  ],
  "security_scan": {"ran": true|false, "blocking_resolved": N, "advisory": N,
                    "skipped": ["<kind>", ...]},
  "loop_backs": {"used": N, "budget": 3},
  "outcome": {"pr_number": N|null, "pr_url": "<url>"|null, "merged": true|false|null,
              "ci": "passed|failed|not_configured|skipped",
              "deploy": "success|manual|failed|not_applicable"},
  "follow_ups": ["<any item requiring future attention>", ...],
  "lane": "small-standard|full"
}
```

The `gates` array carries whichever gates actually ran (Step 11.5 is captured in
`security_scan`, not as a gate row). Use `status: "fast_path"` for a gate the fast path
replaced, `"skipped"` for one that didn't apply.

**`lane` (OPTIONAL, #135):** `"small-standard"` when the run took the `<small-standard-lane>`,
`"full"` otherwise. Unlike the required keys above, `lane` may be **omitted** — `validate_record`
in `hooks/work_summary.py` only checks the keys it knows about and does not reject unrecognized
top-level keys, so a record without `lane` is exactly as valid as one with it. Existing
run-records recorded before #135 have no `lane` key and remain valid; this is a forward-compatible
addition, not a schema version bump. If a Step-9 lane cross-check widened the lane (see the
small-standard lane design, `docs/design/2026-07-03-small-standard-lane.md`), add a
`"lane-widened"` note to `follow_ups` rather than mutating `lane` after the fact.
