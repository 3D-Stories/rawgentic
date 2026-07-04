---
name: rawgentic:update-deps
description: DEPRECATED (removal at v3.0.0) — dependency updates now run through WF2. Redirects to /rawgentic:implement-feature with a deps-typed issue. Invocable only as a deprecation stub.
argument-hint: none — this stub only redirects
---

# WF8: Dependency Update — DEPRECATED STUB

This workflow was deprecated in v2.60.0 and will be REMOVED at v3.0.0 (#161).
Evidence for the deprecation (#160 / AC12): 12/12 run-records are WF2-only, zero
session-note traces, and the design docs' mtimes have been frozen since March.

**Do this instead:**

File a deps-typed issue (`/rawgentic:create-issue`, title `chore(deps): ...`) and
run `/rawgentic:implement-feature <issue>` — WF2's Step 11.5 security scan already
runs the dependency-CVE (SCA) scanner on every PR, which was WF8's core gate.

**Stub telemetry (do this FIRST, before redirecting):** a firing of this stub is
evidence against the deprecation verdict, so it must be recorded. Append exactly
one line to `claude_docs/session_notes.md`:

    ### STUB FIRED: rawgentic:update-deps (deprecated WF8) — <one line: what the user asked for>

Then tell the user the workflow is deprecated, give them the redirect above, and
STOP — this stub performs no other work and asks no questions.
