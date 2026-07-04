---
name: rawgentic:refactor
description: DEPRECATED (removal at v3.0.0) — refactoring now runs through WF2. Redirects to /rawgentic:implement-feature with a refactor-typed issue. Invocable only as a deprecation stub.
argument-hint: none — this stub only redirects
---

# WF4: Refactoring — DEPRECATED STUB

This workflow was deprecated in v2.60.0 and will be REMOVED at v3.0.0 (#161).
Evidence for the deprecation (#160 / AC12): 12/12 run-records are WF2-only, zero
session-note traces, and the design docs' mtimes have been frozen since March.

**Do this instead:**

File a refactor-typed issue (`/rawgentic:create-issue`, title `refactor(scope): ...`)
and run `/rawgentic:implement-feature <issue>` — WF2's small-standard lane plus its
characterization-test discipline (TDD red-green over existing behavior) covers what
WF4 did, with the full review + security gates WF4 lacked telemetry for.

**Stub telemetry (do this FIRST, before redirecting):** a firing of this stub is
evidence against the deprecation verdict, so it must be recorded. Append exactly
one line to `claude_docs/session_notes.md`:

    ### STUB FIRED: rawgentic:refactor (deprecated WF4) — <one line: what the user asked for>

Then tell the user the workflow is deprecated, give them the redirect above, and
STOP — this stub performs no other work and asks no questions.
