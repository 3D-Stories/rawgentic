## Step 11.5: Tool-Based Security Scan (Pre-PR Gate)

### Instructions

Step 11's review is the LLM *reasoning* about the diff; this step runs the
*actual* scanners — secrets, dependency CVEs, SAST, and (for Docker projects)
IaC misconfig — via the shared `hooks/security_scan.py` lib. The
`/rawgentic:scan` utility (WF9's surviving tooling — the security-audit
workflow itself was removed at v3.0.0, #161) calls the **same** lib, so
the tool-based scanning can never drift between the two callers. Scanners catch concrete, known-pattern
problems that reasoning misses (a leaked token, a CVE'd transitive dependency);
they do **not** replace the review or WF9's STRIDE/authorization analysis — those
find logic and authz flaws that no scanner can. Run this AFTER Step 11 has applied
its fixes and committed, and BEFORE pushing in Step 12.

1. Run the scan. Carry `capabilities` values in as **literals** (each step is its
   own Bash call, so shell variables from earlier steps do not persist). Append
   `--has-docker` only when `capabilities.has_docker` is true:
   ```bash
   python3 hooks/security_scan.py scan \
     --project-root <activeProject.path> \
     --project-type <capabilities.project_type> \
     --base-ref origin/<capabilities.default_branch> \
     --json
   rc=$?
   ```
   The JSON `gate` object is authoritative (exit code mirrors it: `0` PASS, `1`
   BLOCKED, `2` usage error). Read `gate.blocking`, `gate.advisory`,
   `gate.errors`, and the top-level `skipped` / `findings`. **This IS the #314
   projection for this surface** (see `### Delegated reads`): consume the gate object +
   each finding's compact id/loc/title dict; finding IDs are never dropped; the raw
   scanner stdout never enters context. Projection validation: a command-failed or
   empty-on-BLOCKED result falls back to inline.

2. **Blocking findings (`gate.blocking`) — fix before the PR, exactly like a
   Step 11 Critical/High:**
   - `secrets`: the secret is in the branch's new commits. Remove it, and if it
     is a *real* credential it must be **rotated** — a deleted-but-already-
     committed secret is still leaked in history. Surface this to the user; do
     not silently delete-and-continue.
   - `sca` (dependency CVE): bump to the fixed version; if none exists, evaluate
     the exposure and either pin/replace or get explicit user acknowledgement.
   - `sast` / `iac`: fix the flagged pattern. Only if it is a *verified* false
     positive, suppress it at the tool's rule level with a justifying comment —
     never by removing the scan.
   Commit the fix and re-run the scan until `gate.blocking` is empty.

3. **Scanner errors (`gate.errors`) — fail closed:** an *installed* scanner
   produced unparseable output. Do NOT treat this as clean. Investigate (often a
   missing lockfile or a tool-version mismatch), fix the cause and re-run, or
   escalate. "I couldn't tell" is never "secure."

4. **Advisory findings (`gate.advisory`):** Medium/Low — note them in the PR body
   and fix if easy.

5. **Skipped scanners (`skipped`):** a tool wasn't installed, or wasn't
   applicable to this project. This is **NOT a pass** — record the skips in
   session notes and the PR body so the gap stays visible. If a scanner the
   project *should* run was skipped for "tool not installed," recommend
   `/rawgentic:setup` (which installs the scanners).

6. **Ambiguity circuit breaker:** if a finding's validity or severity is unclear,
   STOP and present to the user.

Log a marker in `claude_docs/session_notes.md`:
`### WF2 Step 11.5: Security Scan — DONE (#<issue>: blocking: N resolved, advisory: N, skipped: <kinds>)`

### Output
Security scan gate PASS with all blocking findings resolved; skips and advisories
recorded for the PR body and session notes.

### Failure Modes
- Blocking finding is out of WF2 scope to fix → create a follow-up issue and get
  user sign-off before proceeding; a Critical secret/CVE never ships silently.
- All scanners skipped (no tools installed) → the gate is effectively a no-op for
  this run; warn the user and recommend `/rawgentic:setup` to install them.
- Scanner slow on a large repo → secrets/SAST are diff-scoped already; for
  SCA/IaC accept the one-time cost or narrow scope with the user.

---

