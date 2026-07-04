---
name: rawgentic:create-tests
description: DEPRECATED (removal at v3.0.0) — test-suite work now uses the superpowers TDD skills plus WF2. Redirects there. Invocable only as a deprecation stub.
argument-hint: none — this stub only redirects
---

# WF12: Test Suite Creation — DEPRECATED STUB

This workflow was deprecated in v2.60.0 and will be REMOVED at v3.0.0 (#161).
Evidence for the deprecation (#160 / AC12): 12/12 run-records are WF2-only, zero
session-note traces, and the design docs' mtimes have been frozen since March.

**Do this instead:**

Use the superpowers plugin's TDD skills (`/superpowers:brainstorming` for test
strategy, its test-driven skills for the loop) and run the implementation through
`/rawgentic:implement-feature` with a test-typed issue — WF2's TDD red-green per
task is the same discipline WF12 wrapped in 14 steps.

**Stub telemetry (do this FIRST, before redirecting):** a firing of this stub is
evidence against the deprecation verdict, so it must be recorded. Append exactly
one line to `claude_docs/session_notes.md`:

    ### STUB FIRED: rawgentic:create-tests (deprecated WF12) — <one line: what the user asked for>

Then tell the user the workflow is deprecated, give them the redirect above, and
STOP — this stub performs no other work and asks no questions.
