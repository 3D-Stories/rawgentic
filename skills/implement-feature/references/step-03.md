## Step 3: Design Solution Architecture

### Instructions

**Optional peer consult (opt-in, cross-model — blind both ways).** Evaluate up front:
```bash
python3 hooks/adversarial_review_lib.py is-enabled \
  --workspace .rawgentic_workspace.json --project <name> --skill implement-feature --key peerConsult
```
Exit 0 → enabled; non-zero → skip silently (default; no temp file, no subprocess). When enabled:
1. **Resolve the consult backend (#403):**
   ```bash
   python3 hooks/adversarial_review_lib.py backend \
     --workspace .rawgentic_workspace.json --project <name> --key peerConsult
   ```
   Exit 0 → stdout is the backend (`gpt`|`glm`|`both`; absent config → `gpt`). **Exit 2 → the config carries an invalid backend value: abort THIS consult sub-step loudly (log the stderr message; the sub-step is skipped, never defaulted to gpt)** — non-blocking for Step 3, which proceeds with your own design alone. Never default an empty stdout capture to gpt; branch on the exit code.
1b. Write the issue body + the Step 2 codebase-analysis summary to a problem file UNDER the project root (e.g. `<root>/.rawgentic-peer-problem-<n>.md` — the runner's path containment rejects any `--artifact` or `--out` outside `project_root`, refusing before egress). Launch the consult through the runner as a BACKGROUND process (or a read-only dispatch subagent), one invocation per resolved backend — under `both`, TWO independent invocations with distinct `--out` paths (`--backend gpt` with `--reviewer gpt-5.6-sol`; `--backend glm` omitting `--reviewer`):
   ```bash
   python3 hooks/review_runner.py consult \
     --artifact <problem-file> --author-model <your model id, verbatim> \
     --backend <resolved backend> [--reviewer <peer>] \
     --out <root>/.rawgentic-peer-result-<n>[-<backend>].json --project-root <root> &
   ```
   Consults are always `diagnostic: true` — a proposal never authorizes a fix round, so no reopen token is minted here.
2. **Blindness rule:** draft your OWN design first and write it to the design doc. You MUST NOT read any consult result file before your own draft is on disk.
3. After your draft is written, collect each invocation **by its EXIT CODE — never by whether the out file exists**: `0` = the result JSON's `proposal` is valid; `2`/`3`/`4` = refused / terminal backend failure / empty-invalid output — that backend produced NO proposal (never partial content), and the runner already applied its own transport policy, so add no retry loop. Under `both`, one success + one failure is a PARTIAL: use the successful backend's proposal and log the failed backend (success-with-warning, not a failure). Synthesize best-of-all successful proposals and record each peer's contributions (provenance, backend named) in the design doc. Delete the problem file now that the consult has completed.
4. Backend failure is non-blocking: log and proceed with your own design alone. This sub-step never gates Step 3.

1. **Design approach:** For complex features, use the Agent tool with a brainstorming prompt to generate 2-3 implementation approaches. For standard features, design inline with 1-2 approaches.

2. **Each approach includes:**
   - Name and description
   - Pros and cons
   - Estimated effort
   - Risk assessment

3. **Select approach** based on complexity classification and acceptance criteria. Recommend one with rationale.

4. **Design document** — adapt structure to project type:

   **For all project types:**
   - File changes (which files, what modifications)
   - Configuration changes (env vars, YAML, Docker compose)
   - Error handling and failure modes
   - Security implications
   - **Platform / external dependencies (`platform_apis:` — MANDATORY, #226).** Every design
     MUST carry this declaration, exactly like "Security implications" is required on every
     design regardless of whether there is a concern. It is one line when there are no
     material platform APIs — so this is NOT feasibility-proof-for-everything over-gating; it
     only forces the *risk to be named*. A design can commit to a platform/framework API the
     project's own config does not permit and still pass every test-centric gate (the real
     call is silently denied on a surface CI never exercises) — this declaration closes that
     silent gap. An **omitted** declaration is a Step-4 blocker, so the omission cannot
     pass silently.

     ```md
     ## Platform / external dependencies
     platform_apis: none        # the whole declaration when no material platform/external API is used
     ```
     or, per **material** platform/framework/external API **not already proven in-repo the same
     way** (an already-precedented exact call site needs no block), one block each:
     ```md
     platform_apis:
     - api: <exact API> on <exact object/runtime surface>
       feasibility: verified via <capabilities-file|existing-call-site|spike> — <citation>
       failure: fail-loud | fail-silent
       surface: <assertion|log|observable check> — <where>   # REQUIRED when failure: fail-silent
     ```
     Rules (this is the **canonical** contract; WF3 and the WF5 lens point here, they do not
     re-state it):
     - **`assumed` is Step-4-blocking.** `feasibility: assumed` may appear as an interim
       drafting marker, but the Step-4 gate rejects it — a dependency assumed from the
       API's mere existence is exactly the #226 failure. Prove it against this project's
       real config before Step 4.
     - **Working-precedent (AC3):** an `existing-call-site` proves feasibility ONLY for the
       **exact** API on the **exact** object kind and target surface (e.g. `window.setSize`
       proven on the main window does NOT prove it on an overlay window whose capability file
       differs). Otherwise cite a `capabilities-file` or run a `spike`.
     - **`docs` is not an accepted evidence kind.** Documentation proves an API *exists*, not
       that THIS project's config *permits* it — accepting docs is the exact #226 failure (docs
       say `setSize` exists; the capability file denies it). The Step-4 gate rejects
       `verified via docs`; cite the capabilities/manifest file, an exact call site, or
       a spike instead.
     - **Probe-before-claim (#490):** per `<probe-before-design>` (SKILL.md), a `spike` cited
       here must have exercised the EXACT invocation the design will ship, live, and the block
       cites that probe's real result — a proxy composition is not evidence.
     - **Silent-failure gate (AC4):** classify each external call `fail-loud` vs `fail-silent`
       on the target. A `fail-silent` call (denied/failed with the error only in a console CI
       never sees) MUST carry a `surface:` assertion/log that makes build #1 reveal the
       failure — not UAT cycle #3.

   **Additional for `application` projects:**
   - Data flow changes (routes, queries, message flows)
   - Database migrations (with rollback strategy)

   **Additional for `infrastructure` projects:**
   - Container/service changes (images, ports, networks, volumes)
   - Resource allocation (CPU, memory, storage)
   - Dependency ordering (what must start before what)
   - Rollback strategy (how to revert to previous state)
   - Init script design: when using database Docker images (postgres, mysql, etc.), note that `/docker-entrypoint-initdb.d/` scripts behave differently by file type — `.sql` files do NOT support shell environment variable substitution, while `.sh` scripts do. If credentials must come from env vars (e.g., `.env` file), use a `.sh` init script with heredoc, not raw `.sql`.
   - Upstream image capabilities: incorporate findings from Step 2's image inspection (e.g., if the image ships native migration files for your target database, reference those rather than assuming they don't exist)

   **Additional for `scripts`/`docs` projects:**
   - Script interface changes (arguments, outputs)
   - Documentation updates needed

5. **Multi-PR assessment:** If the design suggests more than 500 lines of change or has clearly separable phases, flag for multi-PR decomposition in Step 5.

### Output
Design document. NOT presented to user — goes to Step 4 for critique.

### Failure Modes
- All approaches have significant trade-offs: present to user and let them choose.
- Design reveals much larger scope than estimated: flag for user decision.

---

