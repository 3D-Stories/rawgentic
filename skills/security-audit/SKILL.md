---
name: rawgentic:security-audit
description: DEPRECATED (removal at v3.0.0) — semantic security review is the built-in /security-review; the tool-based scanners live on as /rawgentic:scan. Invocable only as a deprecation stub.
argument-hint: none — this stub only redirects
---

# WF9: Security Audit & Remediation — DEPRECATED STUB

This workflow was deprecated in v2.60.0 and will be REMOVED at v3.0.0 (#161).
Evidence for the deprecation (#160 / AC12): 12/12 run-records are WF2-only, zero
session-note traces, and the design docs' mtimes have been frozen since March.

**Do this instead:**

For a semantic security review, run the built-in `/security-review`. For the
tool-based scanners (secrets / SCA / SAST / IaC over the WHOLE tree, not just a
diff), run `/rawgentic:scan` — it wraps the same fail-closed
`hooks/security_scan.py` engine that still gates every WF2/WF3 PR at Step 11.5
(that gate is untouched by this deprecation). Remediation of findings runs
through WF2 with a security-typed issue.

**Stub telemetry (do this FIRST, before redirecting):** a firing of this stub is
evidence against the deprecation verdict, so it must be recorded. Append exactly
one line to `claude_docs/session_notes.md`:

    ### STUB FIRED: rawgentic:security-audit (deprecated WF9) — <one line: what the user asked for>

Then tell the user the workflow is deprecated, give them the redirect above, and
STOP — this stub performs no other work and asks no questions.
