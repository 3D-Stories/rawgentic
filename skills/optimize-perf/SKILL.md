---
name: rawgentic:optimize-perf
description: DEPRECATED (removal at v3.0.0) — performance work now runs through WF2. Redirects to /rawgentic:implement-feature with a perf-typed issue. Invocable only as a deprecation stub.
argument-hint: none — this stub only redirects
---

# WF10: Performance Optimization — DEPRECATED STUB

This workflow was deprecated in v2.60.0 and will be REMOVED at v3.0.0 (#161).
Evidence for the deprecation (#160 / AC12): 12/12 run-records are WF2-only, zero
session-note traces, and the design docs' mtimes have been frozen since March.

**Do this instead:**

File a perf-typed issue (`/rawgentic:create-issue`, title `perf(scope): ...`)
stating the baseline metric and target, and run
`/rawgentic:implement-feature <issue>` — benchmark-first verification is a
per-task verification line in WF2's plan, not a separate workflow.

**Stub telemetry (do this FIRST, before redirecting):** a firing of this stub is
evidence against the deprecation verdict, so it must be recorded. Append exactly
one line to `claude_docs/session_notes.md`:

    ### STUB FIRED: rawgentic:optimize-perf (deprecated WF10) — <one line: what the user asked for>

Then tell the user the workflow is deprecated, give them the redirect above, and
STOP — this stub performs no other work and asks no questions.
