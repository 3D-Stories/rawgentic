## Step 2: Analyze Codebase and Classify Complexity

### Instructions

**Execution model — map first, then parallel gather, then synthesize (D182).** Step 2's wall-clock is dominated by its read analyses, but they are NOT all independent: **item 1 (component mapping) must run first**, because item 2 (dependency / blast-radius) and item 5 (existing-test inventory) operate on the mapped artifact list. Run item 1 first, then — when the gather is BROAD — fan out the remaining read-only analyses (items 2–6) as concurrent **read-only harness subagents** (Agent tool, Explore-style; ≤3 concurrent) per the `<model-routing-resolve>` contract, passing each the component map from item 1 as **shared input**; a narrow gather runs items 1–6 inline in the same order (judgment by breadth — the point of the fan-out is keeping file dumps out of the main window, not ceremony). One ordering constraint inside the fan-out: item 5 (existing-test inventory) should cover the *full* blast radius from item 2, not just item 1's initial map — so run items 2 → 5 as one sequential subagent (dependency analysis, then test inventory over its expanded surface), while items 3, 4, and 6 are fully independent and run concurrently alongside it. Items 7–8 (complexity classification, small-standard lane eligibility) are **synthesis** steps that run only after the **gather barrier** (all fan-out subagents returned), over the merged findings; the classification stays authoritative and still overrides any issue label. Every subagent return gets the vacuous-result gate from `<model-routing-resolve>` (non-empty, shape parses, load-bearing claims spot-verified) before it is consumed. If a gather subagent dies or returns vacuous: re-run that single analysis inline (the per-analysis failure modes below still apply) — the gather is read-only, so inline is always a safe fallback.

1. **Component mapping:** Using Serena MCP (`find_symbol`, `get_symbols_overview`) or Grep/Glob as fallback, identify all files and code that will need to change. Map the issue's "affected components" to actual project artifacts.

2. **Dependency analysis:** Trace relationships from affected components to understand the blast radius. The scope depends on project type:
   - `application`: trace call chains from entry points (routes, handlers, main functions)
   - `infrastructure`: identify dependent containers, networks, volumes, config files
   - `scripts`: identify shared utilities, imports, configuration dependencies
   - `library`: trace public API surface and consumers
   - `docs`: identify cross-references, linked pages, publishing scripts
   - `research`: primarily analysis notebooks, data pipelines, or literature review — testing means validation of results and reproducibility

3. **Live environment probe (infrastructure projects only):** When `capabilities.project_type == "infrastructure"` and target hosts are known (from `config.infrastructure.hosts[]`), SSH to each target host to discover current state. This catches discrepancies between issue specs (which may be outdated) and reality. **In an unattended run, skip the SSH probes entirely — local exploration only (file reads, grep, git); an unattended run makes no outbound SSH.**

   Probe for:
   - **Server capacity:** `nproc` (CPU count), `free -g` (RAM), `df -h` (disk) — compare against issue requirements
   - **Running containers:** `docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"` — discover what's actually running vs what the issue assumes
   - **Docker Compose version:** `docker compose version` — determines syntax choices (e.g., `deploy.resources` vs deprecated `mem_limit`)
   - **Port usage:** `ss -tlnp` — verify target ports are actually free
   - **Existing configs:** check relevant compose files and `.env` files on the host for patterns to follow
   - **Docker images:** inspect target images for capabilities (e.g., `docker run --rm <image> ls /path/` to check for migration files, installed packages)

   Log probe results in session notes. Flag any discrepancies between the issue spec and actual server state — these often reveal outdated assumptions that would cause deployment failures.

4. **Memory search (Layer 3 — proactive recall).** If a mempalace MCP server is available (`mcp__mempalace__*` tools loaded), call `mempalace_search` with the feature topic and `mempalace_kg_query` for entity-specific facts. Surface prior architectural decisions, known gotchas, and related implementations in this area. Reference findings explicitly when designing the implementation. If no mempalace MCP server is configured, skip silently.

5. **Existing test/verification inventory:** Identify any existing tests, verification scripts, or validation mechanisms that cover the affected code. Note gaps.

6. **Library and image research:** If the feature uses libraries in new ways, use Context7 MCP to fetch current documentation. For infrastructure projects, inspect Docker images that will be used — check for built-in migration files, supported database drivers, pre-installed packages (e.g., `psycopg2` in a Python image), and default configurations. This prevents designing around incorrect assumptions about image capabilities.

7. **Complexity classification:**
   - `simple_change`: 1-3 files, no architecture change, no migration, no new deps
   - `standard_feature`: 4-15 files, contained scope, may need configuration changes
   - `complex_feature`: 15+ files, cross-service changes, multiple configuration changes, new deps

   This classification is AUTHORITATIVE — it overrides any complexity label from the GitHub issue.

8. **Small-standard lane eligibility:** Decide the execution tier via `plan_lib.lane_decision`
   per `<small-standard-lane>`. Estimate the changed-file count with `plan_lib.count_impl_files`
   over the item-1 component map, then call the decision (see `<small-standard-lane>` for the
   exact `python3 -c` invocation): `tier == "lane"` → `small_standard_lane_eligible = true`, else
   `false`. When eligible and not already forced/declined, present the suggested-never-silent
   surfacing block from `<small-standard-lane>` and WAIT for the choice (unattended runs auto-resolve
   per that block). `fast_path_eligible` remains a **deprecated alias**
   (`fast_path_eligible = small_standard_lane_eligible`) so the Step-4 self-review-vs-critique
   readers are unchanged. Trivial changes (item 9) exit via `<trivial-work-check>`, which takes
   precedence over the lane.

9. **Trivial-work check (the one Step 2 step that WAITS for a user decision):** Apply
   `<trivial-work-check>`. If the change is `trivial_work == true`, present the
   "do it directly vs. continue the full workflow" suggestion and WAIT for the user's
   choice before proceeding to Step 3 (unattended runs auto-continue). The analysis from
   items 1–8 still feeds Step 3 silently; only this suggestion (and the item-8 lane
   surfacing) waits for input.

10. **Worktree-isolation probe (#136 — parallelism capability).** Probe once so WF2 (and any outer multi-issue orchestrator) knows up front whether worktree-isolated concurrency is possible, instead of attempt-then-fail on an Agent-tool "not in a git repository" error:
    ```bash
    python3 hooks/capabilities_lib.py probe-parallelism --repo-root "$(git rev-parse --show-toplevel)"
    ```
    Prints `worktree` or `serial-only` (the probe is non-mutating — it creates and force-removes a throwaway worktree under the system temp dir — and never fails the run). Carry it as `capabilities.parallelism` and log one session-note line. Step 8 consults it. **Gotcha to encode for parallel-build orchestrators:** `secret-scan --since` full-scans a *linked* worktree — push from the MAIN checkout (existing documented behavior).

**Baseline record (per `<test-run-discipline>`, SKILL.md):** when `capabilities.has_tests`, run the FULL suite once now and record the baseline from the runner's final output (pass/fail/skip counts + failing test names) in session notes. This is the first of the exactly-two full-suite runs; Step 9's final gate diffs against it. If the current checkout is not the branch base that Step 7 will cut (e.g. a prior issue's feature branch), re-record after Step 7 — a baseline measured on foreign content is invalid. A baseline already recorded on content whose git tree hash equals the branch base carries as-is.

### Output
Codebase analysis with complexity classification, small-standard lane eligibility (`small_standard_lane_eligible`), the `parallelism` capability (`worktree`/`serial-only`), and (for infrastructure projects) live environment probe results. Do NOT present to user — feeds into Step 3. User-visible surfaces: the item-8 lane suggestion (waits for input) and the item-9 trivial-work suggestion (waits for input).

### Failure Modes
- Serena MCP unavailable: fall back to Grep/Glob
- Issue references components that do not exist: flag discrepancy and ask user.
- Complexity uncertain: default to `standard_feature`
- SSH to target host fails: log the failure but do not halt — proceed with issue-stated values and flag that live verification was not possible

---

