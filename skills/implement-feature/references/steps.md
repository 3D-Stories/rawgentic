# WF2 per-step detail

Reading contract: read the matching section (§N) before executing Step N.
The spine (SKILL.md) carries the one-line-per-step overview and the always-run
protocols; this file carries the full per-step instructions plus the
step-semantic blocks they depend on (`<small-standard-lane>`,
`<trivial-work-check>`, `<learning-config>`).

---

<small-standard-lane>
The small-standard lane is a **middle gear** between the `<trivial-work-check>` exit and the
full 16-step spine. It is a **semantic replacement** of the old Step-4-only fast path,
generalized to the whole spine: a 3–5-file UI feature or a 2-file hardening guard genuinely
needs a **code review** but not a **pre-implementation design panel**. The lane is cheaper on
**design ceremony**, never on **review or security**.

**Canonical predicate: `small_standard_lane_eligible`.** It replaces the old `fast_path_eligible`,
which is kept as a **deprecated alias**:

    fast_path_eligible = small_standard_lane_eligible

so every existing "Step-4 self-review vs. critique" reader keeps working unchanged — for that one
decision the lane still selects the self-review, so old Step-4-only callers see identical behavior. New
code reads the canonical name. The flag now controls the **whole lane** (Steps 3/4/5/6/9
collapse), not just Step 4.

**Eligibility — `small_standard_lane_eligible == true` when ALL hold:**
- complexity ∈ {`simple_change`, `standard_feature`} (never `complex_feature` — that is always the full spine), AND
- **changed implementation source files ≤ 7** (`LANE_MAX_IMPL_FILES`) — the SAME counting rule `plan_lib.count_impl_files`/`lane_decision` apply: count non-test, non-doc **source** files the change creates/modifies; **exclude** test files (`test_*`, `*_test.*`, `tests/`), docs (`*.md`, `docs/`), and generated/lockfiles; a rename counts as **1**. Test+doc files are excluded because a small feature legitimately touches several without being "big," AND
- no architecture change, no migration, no new cross-service surface, no new dependency (the signals Step 2 already gathers), AND
- not `trivial_work` (that has its own exit at `<trivial-work-check>`, which takes precedence).

This SUPERSEDES the old two-branch rule (simple_change unconditionally + standard_feature only if
WF1-validated): a non-WF1 standard_feature of bounded size is exactly the field case the lane
exists for. WF1 origin still *strengthens* confidence but is no longer required.

**Decision call (mechanical — mirrors how Step 8 invokes `select_impl_model`).** Pass the Step-2
authoritative complexity, the Step-2 ESTIMATED impl-file count (via `count_impl_files` over the
estimated changed-file list from Step 2 item 1), and the arch/migration/dep/trivial booleans
Step 2 gathered:
```bash
python3 -c "import sys; sys.path.insert(0,'hooks'); from plan_lib import lane_decision, count_impl_files; n=count_impl_files([<estimated changed file list>]); t,r=lane_decision('<complexity>', n, <has_arch_change>, <has_migration>, <has_new_dep>, <is_trivial>); print(t); print(r)"
```
`lane_decision` returns `(tier, reason)` with tier ∈ {`trivial`, `full`, `lane`}. **`tier == "lane"`
→ `small_standard_lane_eligible = true`**; `trivial` defers to `<trivial-work-check>`; `full` runs
the whole spine. Log the tier + reason in session notes.

**Input-source honesty.** `lane_decision` is a pure, unit-tested function, but at Step 2
(pre-implementation) `file_count` is an **estimate** from the Step-2 component map — there is no
diff yet. So lane eligibility is "mechanically decided **given** the Step-2 estimates," not fully
mechanical end-to-end. Guard: **Step 9 cross-checks the actual changed-file count** (see Step 9's
lane cross-check); on a material overshoot it records a `lane-widened` note — it does NOT
retroactively fail. A deterministic pre-diff detector is the AC5 follow-up.

**Surfacing (suggested-never-silent; mirrors `<trivial-work-check>`).** When
`small_standard_lane_eligible` and the lane is not already forced or declined, STOP and present:
```
Step 2 → SMALL-STANDARD detected (<N files, complexity>). Recommend the small-standard lane:
  keeps TDD + code review + security scan + CI; skips the design panel + drift gates.
  (a) Small-standard lane  [recommended]
  (b) Full WF2 (design panel + all gates)
```
This is a **suggestion, never a hard gate** — the orchestrator must NOT silently pick the lane;
continuing the full workflow is always valid. In **headless** mode there is no interactive user,
so AUTO-RESOLVE the lane-vs-full choice: take the lane for eligible changes and the full spine for
`complex_feature`, and log the choice in session notes. (Stated as inline prose, not a bracketed
annotation, to keep the per-skill headless-annotation count stable.)

### Keep / collapse table (the contract)

| Step | Full WF2 | Small-standard lane | Why |
|---|---|---|---|
| 3 Design | inline 1-2 approaches + doc | **brief design note** (file list + failure modes + security), no multi-approach brainstorm | small work has one obvious approach |
| 4 Design critique | **quality-bar rubric** + peer consult + opt-in adversarial-on-design | **quality-bar rubric only** — NO peer consult, NO adversarial-on-design | #190 retired the same-model multi-judge design panel from WF2; cross-model scrutiny is the opt-in adversarial-on-design (full spine) |
| 5 Plan | full task decomposition + drift-ready fields | **checklist plan**: ordered tasks, each with `riskLevel` + a verification line; parallel_group/files optional | keeps TDD + risk tagging; drops ceremony |
| 6 Plan drift | self-review + optional adversarial-on-plan | **SKIP** (folded — the checklist is small enough to eyeball; Step 9 still verifies AC coverage) | a 3-task checklist has no drift surface |
| 8 / 8a | TDD; 8a per high-risk task | **UNCHANGED** — TDD kept; **8a still fires for any `riskLevel: high` task** | security surface never loses per-task review |
| 9 Impl drift | self-review (Part A) + evidence (Part B) | **evidence-only**: run the suite, record the delta, verify each AC has a covering test; skip the alignment self-review | evidence is the real gate |
| 11 Code review | 3-agent (complex) | **≥1 reviewer** (existing minimum for simple/standard) + the opt-in diff adversarial sub-step (#131) still applies | **NON-NEGOTIABLE — this is where the value is** |
| 11.5 Security scan | full | **UNCHANGED** | tool gate never skipped |
| 12/13/14 PR/CI/merge | full | **UNCHANGED** | |
| 16 run-record | full | **UNCHANGED shape**, `complexity` reflects lane; add `lane: "small-standard"` marker | lane runs stay measurable vs full |

**Exact retained vs. removed gates** (no vague "every safety gate"):
- **RETAINED (unchanged):** TDD red-green (Step 8), Step 8a per-task review for any `riskLevel: high` task, Step 11 code review (≥1 reviewer) + the #131 opt-in diff adversarial sub-step, Step 11.5 security scan, CI (Step 13), PR + merge (Steps 12/14), run-record (Step 16).
- **COLLAPSED:** Step 3 (brief note, no multi-approach brainstorm), Step 4 (quality-bar rubric only — no peer consult, no adversarial-on-design; WF2's Step 4 uses the same rubric on the full spine too, so the lane differs only by dropping the opt-in cross-model layers), Step 5 (checklist plan, keeps riskLevel + verification), Step 9 (Part B evidence only — Part A alignment self-review removed).
- **REMOVED entirely:** Step 6 (plan drift).

The RETAINED set is non-negotiable: Step 11 caught 2 Criticals on a run judged "too simple to
review." **Step 11 (code review) and Step 11.5 (security scan) are never traded away in the lane.**
</small-standard-lane>

<trivial-work-check>
Some changes are below even `simple_change` — genuinely **trivial**: a typo, a
comment, a one-line guard, a version/string/constant tweak, a doc-only edit. Running
the full 16-step workflow (and especially the multi-agent reviews) on these costs far
more than the change is worth. This check surfaces that BEFORE the workflow invests in
design, planning, and review.

**Trigger (evaluated in Step 2, after complexity classification):** set
`trivial_work = true` only when ALL hold:
- 1 file (occasionally 2), and roughly ≤ 10 changed lines
- no new logic / control flow / public surface, no new dependency, no migration
- mechanical or cosmetic, low reversal cost (a wrong edit is trivially reverted)
- nothing that warrants its own test *design* (a one-line regression test is fine; the
  change does not need TDD ceremony to get right)

This is a **suggestion, never a hard gate** — the orchestrator must NOT bail on its own,
and continuing the full workflow is always a valid choice.

**When `trivial_work == true` (interactive):** STOP and present, concisely:
```
Step 2 → TRIVIAL detected (<N files, ~M lines, <one-line why>).
The full WF2 (16 steps) is likely overkill for this. Proceed how?
  (a) Do it directly now — quick edit + a targeted test + branch + PR  [recommended]
  (b) Continue the full WF2 workflow
```
Wait for the choice.
- **(a) Do it directly:** LEAVE the workflow. Make the change with the project's
  baseline hygiene only — branch off the default branch, add a targeted test if one is
  warranted, run the suite, bump the version + update docs per the project's pre-PR
  checklist, open a PR — but SKIP the design critique (Step 4), plan + drift gates
  (Steps 5–6, 9), per-task + 3-agent reviews (Steps 8a, 11), and the run-record
  ceremony (Step 16). If you do emit a run-record, set `complexity: "trivial"`.
- **(b) Continue:** proceed to Step 3 as normal (valid when the user wants the full
  audit trail regardless of size).

**[Headless: AUTO-RESOLVE — continue the full workflow. There is no interactive user to
take over a "do it directly" hand-off, and continuing is the conservative default. Log
`### WF2 Step 2 — trivial-work suggestion (auto-continued in headless)`.]**

This is distinct from `<small-standard-lane>`: the lane makes a *non-trivial* change
cheaper (collapses design ceremony, keeps review + security) while staying in the workflow;
the trivial-work check asks whether running the workflow is warranted *at all*.
</trivial-work-check>

<learning-config>
If this workflow discovers new project capabilities during execution (e.g., a new test framework, a previously unknown service), update `.rawgentic.json` before completing:
- Append to arrays (e.g., add new test framework to testing.frameworks[])
- Set fields that are currently null or missing
- Do NOT overwrite existing non-null values without asking the user
- Always read full file, modify in memory, write full file back
</learning-config>

---

## Step 1: Receive Issue Reference and Detect Capabilities

### Instructions

1. **Load project configuration** per `<config-loading>`. The `config` and `capabilities` objects are now available for all subsequent steps. Log all detected capabilities in session notes.

2. Parse the user's input to extract the GitHub issue number. Accept:
   - Bare number: `1`
   - Hash-prefixed: `#1`
   - URL: `https://github.com/<owner>/<repo>/issues/1`

3. Fetch the issue via gh CLI:
   ```bash
   gh issue view <number> --repo ${capabilities.repo} --json number,title,body,labels,state
   ```

4. Validate:
   - Issue exists and is open
   - If closed: ask user if they want to reopen or use a different issue. **[Headless: ERROR — post error comment explaining issue is closed, add rawgentic:ai-error label, exit.]**

5. Check for WF1 origin:
   - If labels include "wf1-created": set `is_wf1_created = true`
   - Extract acceptance criteria, affected components, complexity from the issue body
   - If any are missing (manually created issue): generate them from the description and ask user to confirm. **[Headless: AUTO-RESOLVE for WF1-created issues (accept generated ACs). QUESTION for manual issues — post comment with generated ACs for confirmation, suspend.]**

6. Display to user:
   ```
   ISSUE #NNN: [title]
   State: Open | Labels: [list] | WF1 Origin: [yes/no] | Complexity: [S/M/L/XL]

   Detected Capabilities:
   - Tests: [yes (command) / no]
   - CI: [yes (N workflows) / no]
   - Deploy: [method / no]
   - Infrastructure: [hosts / none]
   - Project type: [type]

   Acceptance Criteria:
   1. [criterion 1]
   ...

   Suggested goal guard — run this so the session can't quit before the ACs are met:
   /goal <plan_lib.build_goal_text(issue_number, ac_lines, variant="wf2") output>

   Confirm this issue and capabilities are correct, or provide corrections. Run the
   /goal command above (or say "skip goal" to decline — declining is fine and never blocks).
   ```

7. Update session notes. Wait for user confirmation (and, in the same round-trip, whether they ran `/goal` or declined — see Step 1b; no second prompt). **[Headless: AUTO-RESOLVE for WF1-created issues (accept and proceed). QUESTION for manual issues — post summary comment for confirmation, suspend.]**

8. **CI-quarantine staleness nag (#137):** if `capabilities.ci_quarantined == true` and `capabilities.ci_quarantined_since` is set, compute `(current local date from the workflow env) − (the YYYY-MM-DD date) > 30 calendar days`; if so, log a "fix or retire CI" advisory in session notes (quarantine is meant to be temporary; this keeps it from silently becoming permanent). Advisory only — never blocks. `ci_quarantined_since` is guaranteed a valid ISO date by `capabilities_lib` (a malformed value already fails the derive), so no parse-guard is needed here. If `ci_quarantined_since` is unset, note that a date should be added so staleness can be tracked.

9. **Branch-protection probe (#139 — advisory, fail-open).** So a passed PR does not overstate its server-side protection, probe once here. **URL-encode the branch** (a default branch like `release/v1` has a `/` that would otherwise hit the wrong endpoint and look like a 404):
   ```bash
   BR_ENC=$(printf %s "${capabilities.default_branch}" | jq -sRr @uri)
   gh api "repos/${capabilities.repo}/branches/${BR_ENC}/protection" -i
   ```
   Capture the HTTP status AND body, then classify with `plan_lib.classify_branch_protection(status, body)` → `(state, details)`. The classifier is strict: only the GitHub "Branch not protected" 404 body → `unprotected`; a 200 that isn't a recognizable protection object, or any 403/401/other → `unknown`. **NEVER fail the run on an API error** — record a probe-command failure (non-zero `gh` exit, network error) as `unknown` in session notes, *distinct* from a confirmed `unprotected`. Record `plan_lib.branch_protection_line(state, details)` in session notes; carry `state` + `details["required_checks"]` forward for Step 12 (PR body) and Step 14 (contradiction check).

### Failure Modes
- Issue does not exist -> ask for correct number
- Issue is closed -> ask if user wants to reopen or use different issue
- Issue lacks acceptance criteria -> generate from description, ask user to confirm

---

## Step 1b: AC-Derived Goal Guard (/goal)

### Instructions

This is an optional guard, not a gate — it never blocks the workflow.

1. **Why.** A skill cannot set a session goal itself; `/goal` is a session command
   (see code.claude.com/docs/en/goal.md), not something a skill body can invoke. So
   this step CONSTRUCTS the goal text and the user (or the headless driver) is the
   one who runs it, giving the session's Stop-hook a concrete condition so the
   workflow can't be silently abandoned before the ACs are met.

2. **Build the text.** Call `plan_lib.build_goal_text(<issue_number>, <the numbered
   AC lines gathered in Step 1>, variant="wf2")`. This is a pure, tested helper:
   the result is guaranteed ≤4000 chars (falling back to "all numbered acceptance
   criteria of issue #<N> as written" if the full AC list would overflow it), and
   it always appends the escape disjunct ("or a blocker is posted to the issue via the ERROR protocol")
   so a legitimately blocked run still clears the goal honestly instead of hanging
   forever. The wording is PR-terminal ("PR open with green CI"), never "merged":
   merge is owner-gated and happens after the workflow ends.

3. **Fold into Step 1's confirmation — no second prompt.** The built text is shown
   inside Step 1 item 6's display block; the user's single confirmation covers both
   the issue/capabilities check and the `/goal` invocation (run it, or decline).
   Declining is always a valid answer and never blocks progress (`goal_guard: skipped`).

4. **ALWAYS emit the constructed `/goal` prompt** (#191). Because `/goal` is a
   session command the skill cannot observe or set, the skill has no reliable way
   to know whether a prior goal is still active — so it must not *suppress*
   emission on the guess that one might be. Emit the built text every run and let
   the user/driver decide whether to run it (declining stays valid). Do NOT skip
   emitting just because a previous run in the same session may have set a goal.

5. **Epic-campaign exception (defer, don't clobber) (#191 AC2).** When this run is
   part of an epic campaign — signaled by the `RAWGENTIC_EPIC_GOAL` environment
   variable being set (to the driving epic's issue number; the driver sets it,
   #192) — an epic-level goal is already in force for the whole campaign. Emitting
   a per-issue `/goal` would clobber it, so **defer**: do NOT emit the per-issue
   prompt, and log the marker as `(deferred: epic #<N> goal active)` (never
   silently skip — the defer must be visible).

6. **Record the marker** (Step 16 reads this to populate the run-record
   `goal_guard` field — `set` when emitted, `deferred` under an epic campaign,
   `skipped` when the user declines / an unlabeled headless run):
   ```
   ### WF2 Step 1b — Goal guard (set|deferred|skipped): <first 80 chars of text | epic #N | decline reason>
   ```

7. `fired` (the Stop-hook actually blocked a quit) is recorded manually only — no
   structured signal reaches the orchestrator when that happens.

**[Headless: AUTO-RESOLVE — when `RAWGENTIC_EPIC_GOAL` is set (epic campaign),
DEFER: the epic-level goal already guards the run, so emit nothing and log
`(deferred: epic #<N> goal active)`. Otherwise, for WF1-created issues, emit the
built goal text verbatim into the headless checkpoint for the driver to set via
`claude -p "/goal …"` (session 1 cannot self-set it — the goal text needs the
fetched issue body, though the driver MAY pre-derive it at launch); for
unlabeled/manual issues, skip the guard and log the marker with (skipped).]**

### Failure Modes
- User neither runs `/goal` nor says "skip" -> treat silence as decline, log `(skipped)`, proceed

---

## Step 2: Analyze Codebase and Classify Complexity

### Instructions

**Execution model — map first, then parallel gather, then synthesize.** Step 2's wall-clock is dominated by its read analyses, but they are NOT all independent: **item 1 (component mapping) must run first**, because item 2 (dependency / blast-radius) and item 5 (existing-test inventory) operate on the mapped artifact list. So run item 1 first, then **fan out the remaining read-only analyses (items 2–6) as concurrent subagents** (Agent tool), passing each the component map from item 1 as **shared input**. One ordering constraint inside the fan-out: item 5 (existing-test inventory) should cover the *full* blast radius from item 2, not just item 1's initial map — so run items 2 → 5 as one **sequential subagent** (dependency analysis, then test inventory over its expanded surface), while items 3, 4, and 6 are fully independent and run concurrently alongside it. This collapses ~5 sequential read passes into roughly one and loses no quality — each subagent goes deeper in its own lane. Items 7–8 (complexity classification, small-standard lane eligibility) are **synthesis** steps that run only after the **gather barrier** (all fan-out subagents returned), over the merged findings; the classification stays authoritative and still overrides any issue label. The issue's complexity hint from Step 1 chooses only the *orchestration cost*, never the workflow path or which gates run — for a trivially small change, skip the subagent spin-up and run items 1–6 inline (the same analyses, in the same order, feeding the same synthesis); otherwise fan out. If a subagent errors, fall back to running that single analysis inline — the per-analysis failure modes below still apply.

<!-- model-routing: role=analysis -->
Dispatch every Step 2 fan-out subagent per the `<model-routing-resolve>` contract for the `analysis` role (generic subagents — no bundled analysis agent; `model: <analysis>` unless `inherit`; effort dual-path, always logged).

1. **Component mapping:** Using Serena MCP (`find_symbol`, `get_symbols_overview`) or Grep/Glob as fallback, identify all files and code that will need to change. Map the issue's "affected components" to actual project artifacts.

2. **Dependency analysis:** Trace relationships from affected components to understand the blast radius. The scope depends on project type:
   - `application`: trace call chains from entry points (routes, handlers, main functions)
   - `infrastructure`: identify dependent containers, networks, volumes, config files
   - `scripts`: identify shared utilities, imports, configuration dependencies
   - `library`: trace public API surface and consumers
   - `docs`: identify cross-references, linked pages, publishing scripts
   - `research`: primarily analysis notebooks, data pipelines, or literature review — testing means validation of results and reproducibility

3. **Live environment probe (infrastructure projects only):** When `capabilities.project_type == "infrastructure"` and target hosts are known (from `config.infrastructure.hosts[]`), SSH to each target host to discover current state. This catches discrepancies between issue specs (which may be outdated) and reality. **[Headless: AUTO-RESOLVE — skip the SSH probes entirely; do local exploration only (file reads, grep, git). A headless run makes no outbound SSH, and `wal-guard` will block it regardless.]**

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
   surfacing block from `<small-standard-lane>` and WAIT for the choice (headless auto-resolves
   per that block). `fast_path_eligible` remains a **deprecated alias**
   (`fast_path_eligible = small_standard_lane_eligible`) so the Step-4 self-review-vs-critique
   readers are unchanged. Trivial changes (item 9) exit via `<trivial-work-check>`, which takes
   precedence over the lane.

9. **Trivial-work check (the one Step 2 step that may surface to the user):** Apply
   `<trivial-work-check>`. If the change is `trivial_work == true`, present the
   "do it directly vs. continue the full workflow" suggestion and WAIT for the user's
   choice before proceeding to Step 3 (headless: auto-continue). The analysis from
   items 1–8 still feeds Step 3 silently; only this suggestion interacts with the user.

10. **Worktree-isolation probe (#136 — parallelism capability).** Probe once so WF2 (and any outer multi-issue orchestrator) knows up front whether worktree-isolated concurrency is possible, instead of attempt-then-fail on an Agent-tool "not in a git repository" error:
    ```bash
    python3 hooks/capabilities_lib.py probe-parallelism --repo-root "$(git rev-parse --show-toplevel)"
    ```
    Prints `worktree` or `serial-only` (the probe is non-mutating — it creates and force-removes a throwaway worktree under the system temp dir — and never fails the run). Carry it as `capabilities.parallelism` and log one session-note line. Step 8 consults it. **Gotcha to encode for parallel-build orchestrators:** `secret-scan --since` full-scans a *linked* worktree — push from the MAIN checkout (existing documented behavior).

### Output
Codebase analysis with complexity classification, small-standard lane eligibility (`small_standard_lane_eligible`), the `parallelism` capability (`worktree`/`serial-only`), and (for infrastructure projects) live environment probe results. Do NOT present to user — feeds into Step 3 (the lane suggestion in item 8 is the one part that may interact with the user).

### Failure Modes
- Serena MCP unavailable: fall back to Grep/Glob
- Issue references components that do not exist: flag discrepancy and ask user. **[Headless: QUESTION — post comment listing missing components with options (skip, create, abort), suspend.]**
- Complexity uncertain: default to `standard_feature`
- SSH to target host fails: log the failure but do not halt — proceed with issue-stated values and flag that live verification was not possible

---

## Step 3: Design Solution Architecture

### Instructions

**Optional peer consult (opt-in, cross-model — blind both ways).** Evaluate up front:
```bash
python3 hooks/adversarial_review_lib.py is-enabled \
  --workspace .rawgentic_workspace.json --project <name> --skill implement-feature --key peerConsult
```
Exit 0 → enabled; non-zero → skip silently (default; no temp file, no subprocess). When enabled:
1. Write the issue body + the Step 2 codebase-analysis summary to a problem file UNDER the project root (e.g. `<root>/.rawgentic-peer-problem-<n>.md` — `resolve_artifact_path` rejects any `--artifact` outside `project_root`, so a `/tmp` path fails closed silently as an empty proposal). Launch the consult as a BACKGROUND process writing structured output to a temp out-file (the out-file may live anywhere; the step gates on exit code, not its location):
   ```bash
   python3 hooks/adversarial_review_lib.py consult \
     --artifact <problem-file> --project-root <root> --out <out-file> --date "$(date -u +%Y-%m-%d)" &
   ```
2. **Blindness rule:** draft your OWN design first and write it to the design doc. You MUST NOT read `<out-file>` before your own draft is on disk.
3. After your draft is written, read `<out-file>`. On timeout/failure the file holds an explicit **empty-proposal marker** (never partial content) — proceed with your design alone. Otherwise synthesize best-of-both and record the peer's contributions (provenance) in the design doc. Delete the problem file now that the consult has completed.
4. **Gate on the background process's EXIT CODE, not just file content** — the empty-proposal write is best-effort, so on an unwritable out-path a non-zero exit can leave the file missing or unreadable entirely. If the exit code is non-zero OR the file is missing/unreadable, treat it identically to an empty proposal and proceed.
5. Codex failure is non-blocking: log and proceed. This sub-step never gates Step 3.

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
     silent gap. An **omitted** declaration is a Step-4 blocker
     (`plan_lib.assert_feasibility_declared(None)` fails), so the omission cannot pass silently.

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
       drafting marker, but `assert_feasibility_declared` rejects it — a dependency assumed
       from the API's mere existence is exactly the #226 failure. Prove it against this
       project's real config before Step 4.
     - **Working-precedent (AC3):** an `existing-call-site` proves feasibility ONLY for the
       **exact** API on the **exact** object kind and target surface (e.g. `window.setSize`
       proven on the main window does NOT prove it on an overlay window whose capability file
       differs). Otherwise cite a `capabilities-file` or run a `spike`.
     - **`docs` is not an accepted evidence kind.** Documentation proves an API *exists*, not
       that THIS project's config *permits* it — accepting docs is the exact #226 failure (docs
       say `setSize` exists; the capability file denies it). `assert_feasibility_declared`
       rejects `verified via docs`; cite the capabilities/manifest file, an exact call site, or
       a spike instead.
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
- All approaches have significant trade-offs: present to user and let them choose. **[Headless: QUESTION — post comment with all approaches, pros/cons, and recommendation, suspend.]**
- Design reveals much larger scope than estimated: flag for user decision. **[Headless: QUESTION — post comment with scope assessment and options (proceed, narrow, abort), suspend.]**

---

## Step 4: Quality Gate — Design Critique

### Instructions

Step 4 applies the in-repo **quality-bar rubric** (`references/quality-bar.md`) to the design for **all** lanes. The same-model
multi-judge design panel (WF2's old full-critique gate) was retired from WF2 (#190): owner
telemetry showed ≈ 0 measured gain from it, and the lean spine shipped 10/10 campaign issues
with 0 loop-backs. High-stakes design scrutiny stays available — cross-model and opt-in —
via the WF5 **adversarial-on-design** sub-step (item 7), which is where genuine design-flaw
catching now lives on the full spine.

**Determine gate shape based on lane eligibility** (`small_standard_lane_eligible`, a.k.a.
the `fast_path_eligible` alias):
- If `fast_path_eligible == true` (lane): apply the quality-bar rubric only — **NO peer consult
  (the Step 3 sub-step) and NO adversarial-on-design (item 7)**. The lane drops all
  design-stage cross-model ceremony.
- If `fast_path_eligible == false` (full spine): apply the quality-bar rubric, **plus** the opt-in
  Step 3 peer consult and the opt-in adversarial-on-design sub-step (item 7) below.

**Design self-review (quality-bar rubric, `references/quality-bar.md`) — all lanes:**

<!-- model-routing: role=review -->
When run as a subagent, dispatch it as a `rawgentic:rawgentic-reviewer` per the
`<model-routing-resolve>` bundled-agent contract (`model: <review>` unless `inherit`; effort
dual-path, always logged). The quality-bar self-review is a single-pass, same-model check over the design:
- Does the design respect existing patterns and project conventions, with appropriate
  dependencies (prefer existing libraries)?
- Are all acceptance criteria addressed, edge cases and failure modes identified, and the
  implementation verifiable (tests if available, otherwise manual checks or scripts)?
- Input validation at boundaries, credential handling (no hardcoded secrets),
  backward-compatibility or a migration plan, acceptable performance?
- **Platform / external-dependency feasibility (#226):** run the mechanical gate —
  `python3 -c "import sys;sys.path.insert(0,'hooks');from plan_lib import parse_feasibility_block,assert_feasibility_declared as A;ok,errs=A(parse_feasibility_block(open('<design-doc>').read()));print(ok,errs)"`.
  A non-`ok` result is a **blocking** finding (Critical/High): an absent `platform_apis:`
  declaration, an `assumed` dependency, weak/uncited evidence, or a `fail-silent` API with no
  `surface:`. Beyond the mechanical check, judge credibility the parser cannot: does the cited
  evidence actually prove the API works under THIS project's real config (does the named
  capability/manifest file truly grant it; is the call site the exact API on the exact object
  kind; did the spike exercise the real surface)? And — the parser cannot see this — does the
  design **use** a platform API it failed to declare (a `platform_apis: none` that is actually
  false)? A used-but-undeclared API is itself the finding.
- For WF1-validated issues: does the design align with the WF1-critiqued spec?

The self-review produces findings in the shape the gate consumes:
   ```
   Finding #N:
   - Severity: Critical | High | Medium | Low
   - Category: architecture | completeness | security | testability | scope_fidelity | migration_safety | performance
   - Description: [what the issue is]
   - Recommendation: [specific action]
   - Ambiguity flag: clear | ambiguous
   - Ambiguity reason: [why, if ambiguous]
   ```

4. **Volume threshold check** (per-tier independent): thresholds per `VOLUME_THRESHOLDS`.

5. **If loop-back triggered:**
   - Check `design_loopback_count` and `global_loopback_total`
   - If within budget: increment counters, apply findings as constraints, return to Step 3
   - If budget exhausted: STOP and escalate to user. **[Headless: ERROR — post error comment with findings summary, add rawgentic:ai-error label, exit.]**
   - **If the adversarial review sub-step (item 7) is enabled and still in flight when this loop-back fires:** do NOT wait for it and do NOT run the ambiguity breaker (thresholds did not pass). **Discard the in-flight adversarial result as stale** — it reviewed a design that is now being revised (this is the documented one-wasted-call tradeoff) — and log `### WF2 Step 4 — Adversarial Review (discarded: superseded by volume loop-back)`. Return to Step 3; the next Step 4 pass dispatches a fresh adversarial review against the revised design.

6. **If thresholds pass:** Apply the ambiguity circuit breaker over the self-review findings — **unless** the adversarial review sub-step (item 7) is enabled for this run. When it is enabled, do NOT run the breaker here; **defer** it to the single merged-findings join barrier in item 7, so the breaker runs **exactly once** over the combined self-review + adversarial findings rather than twice. (The volume/loop-back checks in items 4–5 still run on the self-review findings as soon as the self-review returns; only the breaker is deferred.)

7. **Adversarial review sub-step (opt-in, cross-model — runs concurrently with the self-review).** Evaluate the two gate conditions UP FRONT, before running the self-review, so the cross-model adversarial review of the design document can be dispatched **concurrently with the self-review** rather than serially after it. Both review the same design document, so there is no ordering dependency, and overlapping them removes a serial round-trip from the critical path of every gated run. Gate it on BOTH conditions:
   - `fast_path_eligible == false` (skip cheap-path designs — this is additive to the self-review gate, never a replacement), AND
   - the active project opts in:
     ```bash
     python3 hooks/adversarial_review_lib.py is-enabled \
       --workspace .rawgentic_workspace.json --project <name> --skill implement-feature
     ```
     The command exits `0` when the review is enabled for this skill and `1` (or any non-zero) otherwise. If it exits non-zero, or `fast_path_eligible == true`, **skip silently** — behavior is byte-for-byte unchanged.
   When both gates pass, dispatch `/rawgentic:adversarial-review <design-doc-path>` **in parallel with the self-review** (write the design doc to a temp file under the project first if it only exists in session notes). The adversarial review is **report-only**; bring its findings back into THIS gate at the **join barrier** described next:
   - **Join barrier (single breaker):** once both the self-review and the review have returned, merge adversarial findings with the self-review findings into ONE list, tagging each with `source: self-review | adversarial`. Apply the ambiguity circuit breaker **exactly once** over the merged list — this IS the breaker deferred from item 6; never run a second, self-review-only breaker.
   - If the merged list contains one or more Critical/High design flaws, consume **exactly one** `design` loop-back via `plan_lib.consume_loopback(<counters>, "design")` (the existing counter, NOT a new source) regardless of how many such findings there are, and return to Step 3 once with the unified constraint set. Do not consume per-finding and do not double-count against the self-review loop-back.
   - **Codex failure is non-blocking (the review is additive — the self-review gate already ran).** On ANY non-success from the review (not installed, unauthenticated, timeout, error, parse error — including in headless mode), do NOT trigger the ERROR protocol and do NOT block the workflow: skip the adversarial layer, log the failure loudly in session notes (and, in headless mode, post a STATUS comment noting the review was skipped), and continue with the self-review result. **Because item 6 deferred the breaker when this sub-step is enabled, on any non-success you MUST still run the single ambiguity circuit breaker exactly once over the self-review-only findings before continuing — skipping the adversarial layer must not skip the breaker** (otherwise the breaker would run zero times). Never treat a failed external review as "passed", and never let its absence halt WF2. (Only the standalone `/rawgentic:adversarial-review` skill ERRORs on an unmet Codex prerequisite, because there the review is the entire task.)
   - **Concurrency tradeoff (accepted):** because the review now overlaps the self-review instead of waiting for it, a design that the self-review sends back to Step 3 may have spent one cross-model review call before the loop-back. That is a bounded, accepted cost (at most one such call per loop-back) in exchange for removing the serial wait on every gated run. Do NOT try to "save" the call by serializing — the latency win on the common (no-loopback) path is worth more than the occasional wasted call.
   - Log a marker: `### WF2 Step 4 — Adversarial Review (invoked|skipped): <report path or skip reason>`.

**Breaker decision — run the ambiguity circuit breaker EXACTLY ONCE (items 4–7, summarized).**
The run-count is the most error-prone control flow in this step (it spreads across items
5–7 with no hook to enforce it), so this one table is authoritative for *which* findings
the single breaker runs over. It runs in exactly one row, never twice:

| Volume loop-back fired (item 5)? | Adversarial sub-step (item 7) state | Breaker runs over |
|---|---|---|
| **yes** | (any) | **SKIP** — return to Step 3 now; discard any in-flight adversarial result as stale (item 5). The breaker runs on the *next* Step 4 pass. |
| no | disabled / not opted-in / fast-path | **self-review-only** findings |
| no | enabled AND returned | **merged** self-review + adversarial (the join barrier, item 7) |
| no | enabled BUT non-success (not installed / timeout / error / parse error) | **self-review-only** findings — skipping the adversarial layer must NOT skip the breaker, **else it runs zero times** (item 7) |

The only path on which the breaker does not run is the volume-loop-back row, and that is
because it returns to Step 3 *before* the breaker point — not because the breaker was skipped.

**Lane note:** on the fast path the adversarial review sub-step (item 7) and the Step 3 peer
consult do NOT run — the self-review alone is the gate. The dimensions above are unchanged;
only the opt-in cross-model layers are dropped.

### Output
Amended design document.

### Failure Modes
- Zero findings from the self-review: verify it actually analyzed the design (not a rubber-stamp)
- Ambiguity circuit breaker triggers on >50% of findings: design may be underspecified

---

## Step 5: Create Implementation Plan

### Instructions

**Small-standard lane variant (`<small-standard-lane>`).** In the lane, produce a
**checklist plan** instead of the full decomposition: an ordered list of tasks where each carries a
`- riskLevel: high|standard` line and a one-line verification. `parallel_group`/`files` are
OPTIONAL in the lane. **riskLevel tagging is RETAINED** — the fail-closed `plan_lib.parse_tasks`
contract and the Step 3a risk stratification still apply, because Step 8a fires on any
`riskLevel: high` task. The branch-naming (item 1) and commit-message (item 8) items still apply;
the full task decomposition, drift-ready fields, and multi-PR machinery below are the FULL-spine
form (run them when not in the lane).

1. **Branch naming:**
   - Features: `feature/<issue-number>-<kebab-case-summary>`
   - Bug fixes: `fix/<issue-number>-<kebab-case-summary>`

2. **Task decomposition:** Break the design into ordered tasks, each appropriately sized (aim for 2-10 minutes each). Adapt the task style to the project:

   **If `capabilities.has_tests == true`:** Follow Red-Green-Refactor per task:
   - RED: Write failing test(s), confirm they fail
   - GREEN: Write minimum code to pass
   - REFACTOR: Clean up

   **If `capabilities.has_tests == false`:** Follow Implement-Verify per task:
   - IMPLEMENT: Write the code/config changes
   - VERIFY: Run a verification command (health check, syntax check, dry-run, or manual inspection)
   - Document what "verified" means for this task

3. **Task ordering:** Make dependencies explicit. Parallel-eligible tasks share a `parallel_group` AND each declares the files it touches via a `- files: <comma-separated paths>` line, so disjointness can be proven mechanically:
   - `- parallel_group: <group-id>` — tasks with the same id are candidates to run concurrently.
   - `- files: <comma-separated paths>` — the exact files the task creates/modifies (concrete paths only; globs and directories cannot be proven disjoint).

   After decomposition, call `plan_lib.validate_parallel_groups(tasks)`; it returns `(all_eligible, conflicts)`. A group is parallel-eligible ONLY when every member declares concrete `files` and the members' file sets are pairwise disjoint. Any conflict (overlap, missing files, glob, or directory) means that group is **not** parallel-eligible and **runs sequentially** — an un-provable group degrades to serial execution, never to a concurrent collision. Log conflicts to session notes. (Isolated *concurrent* execution of eligible groups is added in a follow-up; today eligible groups still execute sequentially but are validated so the contract is ready.)

3a. **Risk stratification (P15):** Tag every task with a `riskLevel: high|standard` field. Use **`high`** if ANY of the 8 criteria apply; otherwise `standard`. The 8 criteria (canonical list lives in `hooks/plan_lib.py::RISK_CRITERIA`):

   1. **Security surface** — auth, secrets, sanitization, input validation, crypto, access control
   2. **Module boundary** — introduces or changes a service/module API that other code will import
   3. **Non-trivial error/exception flow** — state machines, retry, fallback branches, discriminated outcomes
   4. **Infra/persistence** — infrastructure, deployment, migrations, schema
   5. **Security middleware** — rate limiting, circuit breakers, request validation
   6. **Deserialization of external data** — JSON/YAML/TOML/binary formats from untrusted sources
   7. **Subprocess construction** — shells out to external commands with dynamic args
   8. **Regex on untrusted input** — ReDoS risk, lookahead in user-controlled input

   **Plan format contract** (enforced by `plan_lib.parse_tasks`):
   - Each task begins with `### Task <id>: <title>` heading.
   - Each task body MUST contain a line `- riskLevel: high|standard`; high-risk tasks include a parenthesized reason: `- riskLevel: high (security surface)`.
   - Tasks lacking a `riskLevel` line **fail closed** (parse error → STOP). **[Headless: ERROR — add `rawgentic:ai-error` label, post comment explaining the plan format contract.]**
   - OPTIONAL: `- parallel_group: <id>` and `- files: <comma-separated paths>` (see Task ordering above). These are purely additive — absent fields just mean the task is not parallel-eligible; they never affect the `riskLevel` fail-closed contract or the pre-P15 migration.

   **Calibration check** — after task decomposition, compute the high-risk ratio and classify it. **Mind the real return shapes (#231 AC3 — each mismatch cost a round-trip):** `plan_lib.compute_risk_ratio(tasks)` returns a **3-tuple `(ratio, high_count, total_count)`**, NOT a bare float — unpack it and pass the SCALAR `ratio` (plus `total`) to `check_ratio_band`, never the whole tuple:

   ```python
   ratio, high, total = plan_lib.compute_risk_ratio(tasks)   # (float, int, int)
   band = plan_lib.check_ratio_band(ratio, total)            # scalar ratio + int total
   ```

   (And `plan_lib.parse_tasks(md)` returns `list[Task]` **objects** — access attributes `t.id`, `t.title`, `t.risk_level`, `t.reason`, `t.parallel_group`, `t.files`, `t.deferral_reason`, NOT dict `t.get("risk_level")`.) Handle the `band` result:

   - `skip` (N<3): silent.
   - `pass` (ratio ≤ WARN_PCT/100, default 30%): silent.
   - `implausible_zero` (ratio == 0 AND N≥5): log an info note: "0% high-risk on a complex feature is implausible — confirm." Continue.
   - `warn` (WARN_PCT/100 < ratio ≤ HALT_PCT/100): log warning to session notes. Continue. **[Headless: AUTO-RESOLVE — log to session notes.]**
   - `halt` (HALT_PCT/100 < ratio < 80%): STOP and ask user. **[Headless: QUESTION — post comment with risk-ratio breakdown + options (proceed-anyway, re-plan, abort), suspend.]**
   - `decompose` (ratio ≥ 80%): STOP and recommend plan decomposition. Treat as halt with a different framing. **[Headless: QUESTION — post comment recommending multi-PR split, suspend.]**

   The 15–30% high-risk ratio is the documented calibration target. Anything above the WARN band signals that the criteria are being over-applied (dilution returns).

   **High-risk path allowlist:** A task touching any file whose path matches the regex allowlist in `plan_lib.DEFAULT_HIGH_RISK_PATH_PATTERNS` (auth, secret, .env, migration, crypto, jwt, session, oauth, csrf, token, credential, passport, middleware, lib/server/auth, security-, hooks/security) is auto-tagged `high` regardless of the agent's manual classification.

4. **Verification strategy per task:** Specify how each task is verified:
   - Test file + test cases (if test framework exists)
   - Shell command that confirms correct behavior
   - Manual inspection criteria
   - Health check URL
   - **Deferred-to-target (#138):** when the dev environment *fundamentally cannot* exercise the artifact (e.g. an NSIS uninstaller with no `makensis`; native Win32 code built from WSL for a Windows target; an OS-native tray menu that cannot render headless), declare a `- verification: deferred-to-target (<reason>)` line on that task. This is NOT a skip: the task still requires its **best local proxy** (compile, typecheck, unit tests of any extractable logic); deferral covers only the *unexercisable remainder*, which will be checked on the target box. `plan_lib.parse_tasks` records the reason (a deferral line with no `(reason)` fails closed). A proxy you can run is never the path you can't — so the gap is named here, carried through Step 9 (listed, never counted as verified), Step 12 (the `## Deferred verification` PR section), Step 16 (the run-record `verification_deferred` list), and enforced by `<completion-gate>`.

5. **Migrations / config changes (if applicable):** Specify files, content, and rollback approach. Use `capabilities.migration_dir` if it exists, otherwise specify where migration files should live.

6. **Documentation tasks:** Identify docs that need updating (CLAUDE.md, README, Confluence pages, inline comments).

7. **Multi-PR decomposition (if applicable):** If design exceeds 500 lines, decompose by logical phase. Each sub-PR follows Steps 8-14 independently.

8. **Commit messages:** Pre-specify conventional commit messages for each task.

### Output
Implementation plan with ordered tasks, verification strategy, branch name, optional multi-PR decomposition.

### Failure Modes
- Too many tasks (>30) -> suggest scope narrowing or multi-PR
- Circular dependencies -> re-order to break cycles
- Plan references nonexistent files -> verify against Step 2 analysis

---

## Step 6: Quality Gate — Plan Drift Check

### Instructions

**Skip condition:** Step 6 is skipped when time-critical **or when running the
small-standard lane** (`small_standard_lane_eligible` — the checklist plan is small enough to
eyeball, and Step 9 still verifies acceptance-criteria coverage). Otherwise run it.

Apply the quality-bar rubric (`references/quality-bar.md`) over these check dimensions:
- **Design-plan alignment:** Does every design component map to at least one task?
- **Verification completeness:** Does every implementation task have a corresponding verification step?
- **Acceptance criteria coverage:** Does the plan, if executed, satisfy all acceptance criteria?
- **Task ordering validity:** Are dependencies correctly ordered?
- **Commit checkpoint adequacy:** Are checkpoints at logical boundaries?

Apply ambiguity circuit breaker on findings. If clear: apply automatically.

**Adversarial review sub-step (opt-in, cross-model).** After the self-review above, optionally run a cross-model adversarial review of the **implementation plan**. Gate on project opt-in only (Step 6 has no fast-path branch):
```bash
python3 hooks/adversarial_review_lib.py is-enabled \
  --workspace .rawgentic_workspace.json --project <name> --skill implement-feature
```
The command exits `0` when enabled and non-zero otherwise; if non-zero, **skip silently**. When enabled, write the plan to a temp file under the project and invoke `/rawgentic:adversarial-review <plan-path> plan`. It is report-only; merge its findings (tagged `source: adversarial`) with the self-review findings and apply the circuit breaker over the **merged** list (do not run two separate breakers). If the merged list contains one or more Critical/High design-level flaws, consume **exactly one** existing `design` loop-back counter and return to Step 3 once with the unified constraints. **Codex failure is non-blocking** (additive review): on any non-success — including headless unmet-prerequisite — skip the adversarial layer, log loudly (headless: STATUS comment), and continue with the self-review result; never ERROR or block WF2. Log: `### WF2 Step 6 — Adversarial Review (invoked|skipped): <report path or skip reason>`.

### Output
Plan drift check result.

### Failure Modes
- Significant drift detected -> add missing tasks
- Scope creep detected -> remove excess tasks or flag for user decision. **[Headless: AUTO-RESOLVE — remove excess tasks, document removed items in session notes.]**

---

## Step 7: Create Feature Branch

### Instructions

1. Ensure working directory is clean:
   ```bash
   git status --porcelain
   ```
   If dirty: stash, create branch, ask user about stash. **[Headless: AUTO-RESOLVE — always stash, log to session notes AND post a brief comment to the issue noting uncommitted changes were stashed (include `git stash list` output for the stash ref).]**

2. Create the feature branch from a **freshly-fetched** default branch — never `git pull` into the current checkout first. `git pull origin <default>` merges the default INTO whatever branch is checked out; if the session still sits on a prior issue's feature branch (a multi-issue campaign, or a headless PR-terminal run that never ran Step 14), that mutates the sibling branch AND bases the new branch on the mixture, silently carrying the sibling's unmerged commits into this PR. Fetch, then branch off `origin/<default>` regardless of the starting checkout:
   ```bash
   git fetch origin ${capabilities.default_branch}
   git checkout -b <branch_name> origin/${capabilities.default_branch}
   ```
   **Base assertion — STOP on mismatch.** Confirm the new branch's base is exactly `origin/<default>` HEAD (nothing foreign rode along):
   ```bash
   [ "$(git merge-base HEAD origin/${capabilities.default_branch})" = "$(git rev-parse origin/${capabilities.default_branch})" ] && echo BASE_OK || echo BASE_MISMATCH
   ```
   `BASE_MISMATCH` → STOP and reconcile before writing any code (do not build on a wrong base). **[Headless: ERROR — post an error comment noting the base mismatch, add the `rawgentic:ai-error` label, exit.]** Because nothing is pulled into the current checkout, no pre-existing branch is mutated as a side effect.

3. Push empty branch to origin:
   ```bash
   git push -u origin <branch_name>
   ```

4. Link branch to issue:
   ```bash
   gh issue comment <issue_number> --repo ${capabilities.repo} --body "Implementation started on branch \`<branch_name>\`"
   ```

### Output
Feature branch created and pushed, issue commented.

### Failure Modes
- Branch already exists: ask user to resume or start fresh. **[Headless: AUTO-RESOLVE — always resume existing branch.]**
- Push fails: continue locally, push later

---

## Step 8: Implementation

### Instructions

Execute the implementation plan task by task.

**For each task in the plan:**

1. **If TDD mode** (`capabilities.has_tests == true`):
   - RED: Write failing test(s). Run test command from `capabilities.test_commands` to confirm failure.
   - GREEN: Write minimum code to pass. Run tests to confirm all pass.
   - REFACTOR: Clean up. Re-run tests.

2. **If Implement-Verify mode** (`capabilities.has_tests == false`):
   - IMPLEMENT: Write the code, config, or infrastructure changes.
   - VERIFY: Run the verification command specified in the plan. Capture output as evidence.
   - If verification fails: debug and fix before proceeding.

3. **Commit:** Create a conventional commit:
   ```bash
   git add <specific_changed_files> && git commit -m "<type>(scope): <description> (#<issue_number>)"
   ```
   Stage ONLY the files modified in this task. Never `git add -A` or `git add .`.

4. **Push regularly:** Push to origin at natural checkpoints (after every 2-3 tasks or every 30 minutes):
   ```bash
   git push origin <branch_name>
   ```

<!-- model-routing: role=implementation -->
**Optional implementation delegation (`implementation` role).** When routing resolved the `implementation` role to a non-`inherit` model, execute each plan task via a subagent instead of inline, subject to a per-task **clean-state boundary**. The resolved `implementation` model is a **CEILING, not a blanket assignment** — pick the cheapest sufficient model per task (issue #132), so a well-specified mechanical task is not built on the ceiling model when a cheaper one suffices. When the resolved `implementation` effort is non-`none`, apply the dual-path effort rule from `<model-routing-resolve>` (pass it only where the dispatch layer supports effort; always log it) — effort is role-wide from `<model-routing-resolve>`, not part of `select_impl_model`'s ceiling logic.

0. **Per-task model selection (ceiling semantics).** For each task, choose its dispatch model with:
   ```bash
   python3 -c "import sys; sys.path.insert(0,'hooks'); from model_routing_lib import select_impl_model; m,r=select_impl_model('<ceiling>','<riskLevel>','<complexity>'); print(m); print(r)"
   ```
   - `<ceiling>` = the resolved `implementation` value from `<model-routing-resolve>`.
   - `<riskLevel>` = this task's `riskLevel` (`high`|`standard`) from the Step 5 plan.
   - `<complexity>` = the **Step 2** authoritative complexity classification (`simple_change`|`standard_feature`|`complex_feature`) carried forward from Step 2 item 7; if it is unavailable/unknown in context, pass `standard_feature` (the conservative middle) and note that in the per-task log.
   `select_impl_model` returns `(model, reason)`: high-risk **or** `complex_feature` → the ceiling; otherwise → `sonnet` (down-routed); a `haiku`/unknown ceiling floors to `sonnet` (rawgentic never routes coding to Haiku). Dispatch this task's subagent with `model: <model>`. **Never dispatch an implementation subagent with `model: haiku`** — if the resolved model is `inherit` and the session model is Haiku, pass `model: sonnet` instead.
   **Log per task** in session notes: `impl task <id>: model <model> (<reason>), effort <effort|none>` — makes over/under-routing auditable.
1. **Before dispatch:** record the pre-task state — current `HEAD` and `git status --porcelain` (the tree must already be clean from the previous task's commit).
2. **Dispatch one `rawgentic:rawgentic-implementer` task-agent** (serial — one at a time; each task builds on the previous commit) with the per-task `model` from item 0 and the brief: the design doc, this plan task, the TDD requirement, project conventions, and the current test baseline. The definition carries the test-first/stage-only-your-files/report-honestly contract; the agent implements the task test-first and commits it. Its `isolation: worktree` frontmatter keeps the mutation off the shared tree (fallback per `<model-routing-resolve>` when worktrees are unavailable).
3. **After it returns — collect, then verify:** when the dispatch ran worktree-isolated, the agent's commit lives in the shared object store but NOT on the feature branch — collect it first (cherry-pick or fast-forward the reported SHA onto the feature branch) and **assert the branch actually advanced** (`git rev-parse HEAD` differs from the recorded pre-task HEAD); a task whose SHA never lands is NOT done, and skipping this check would let the later diff-scoped gates (Step 8a, Step 11, secret scan) run over an empty diff and pass vacuously. The serial chain depends on it too: the next task's worktree is cut only after this commit is on the branch. Apply the staging backstop to the COLLECTED commit (`git show --name-only <sha>` ⊆ the task's declared `files`), not the main tree's staging area. Then re-run the test suite and diff against the recorded baseline. On success (tests green, only expected paths changed, commit landed on the branch) → proceed to the next task.
4. **On failure or vacuous return:** **restore** the pre-task state first — `git reset --hard <recorded HEAD>` and `git clean -fd` to discard the agent's partial edits — then **retry that task once at the CEILING model** (escalate: a down-routed task that struggled gets the configured maximum, dispatched the same clean-state way, or inline if the ceiling is `inherit`). Log the escalation. Because the restore runs first, the retry never operates on a half-mutated tree.
5. Delegation can never block Step 8: a second failure falls through to the normal Step 8 failure handling.

When the `implementation` role is `inherit` (default), Step 8 runs inline exactly as today — no delegation, no behavior change (and if the session model is Haiku, dispatch any implementation subagent with `model: sonnet`).

<!-- whole-issue-delegation: #133 -->
**Optional WHOLE-ISSUE delegated build sub-mode (`wholeIssueDelegation`, default-off).** Per-task delegation above still runs the full per-task ceremony (dispatch, suite re-run, diff) in the orchestrator's own loop, so an orchestrator working a backlog bloats fast. When opted in, Step 8 instead hands ONE build-subagent the whole branch and validates a structured **receipt** — the *typing* is delegated, the *gating* is not. **Trust boundary:** the builder never self-certifies; every gate re-runs in the orchestrator against the real tree, and a receipt claim is a hypothesis until confirmed. Read `references/whole-issue-delegation.md` in full before using this mode — it holds the build-subagent brief template, the receipt schema, and the validation/fallback contract; the block below is only the spine.

1. **Gate.** Only when enabled for this skill:
   ```bash
   python3 hooks/adversarial_review_lib.py is-enabled \
     --workspace .rawgentic_workspace.json --project <name> --skill implement-feature \
     --key wholeIssueDelegation
   ```
   Exit `0` → enabled; non-zero → **skip silently** and run Step 8 exactly as above (per-task or inline). An explicit invocation opt-out also skips.
2. **Pre-flight clean-worktree gate (data-loss guard).** Require `git status --porcelain` empty. If the operator has ANY staged/unstaged/untracked work, do NOT enter this mode — log it and fall back to the normal per-task Step 8. The reject path must be able to discard the builder's output without touching pre-existing operator files.
3. **Record** `branch_base_sha` (`git rev-parse HEAD`) and the pre-build test baseline (`{before:{passed,failed}}`).
4. **Dispatch ONE build-subagent** (`rawgentic:rawgentic-implementer`) with the brief (design, plan w/ riskLevels, TDD requirement, conventions, baseline) + the required receipt schema (both in the reference file). Model: `select_impl_model(<ceiling>, <riskLevel>, <complexity>)` sized to the plan's **highest-risk** task (one agent → size to the hardest task); **never `model: haiku`** (same inherit+Haiku→sonnet rule as above).
4b. **Collect BEFORE validating (worktree runs).** When the build ran worktree-isolated, the receipt's commits exist in the shared object store but the feature branch has NOT advanced — and receipt Rule 4 diffs `base..HEAD` on THIS checkout, so validating first would reject every worktree build (empty diff vs declared files). Land the build on the branch first: fast-forward to the receipt's final sha (or cherry-pick `task_shas` in plan order), then assert the branch actually advanced past `branch_base_sha`. Only then validate. (Non-isolated fallback dispatches leave HEAD already advanced — this step is then a no-op assert.)
5. **Validate the receipt** against the real tree:
   ```bash
   python3 -c "import sys,json; sys.path.insert(0,'hooks'); from plan_lib import validate_build_receipt, parse_tasks; r=json.load(open('<receipt.json>')); tasks=parse_tasks(open('<plan.md>').read()); ok,errs,norm=validate_build_receipt(r,tasks,'.','<branch_base_sha>'); print(ok); print('\n'.join(errs)); print(json.dumps(norm))"
   ```
   **Reject (ok=False)** → **restore then fall back** (delegation can never block Step 8): `git reset --hard <branch_base_sha>` (tracked) then remove ONLY the untracked paths the receipt declared (`union(files_per_task)` filtered to still-untracked); on an unparseable/partial receipt, reset only and WARN that builder untracked files may remain — **never** blanket `git clean -fd` against the operator's checkout. Log the fallback loudly, then run the normal per-task Step 8.
6. **On a valid receipt, run the gates in the orchestrator against the real tree** (not the receipt's word for them):
   - **Step 8a** for every high-risk task (tagged in Step 5 **OR** in `norm["promoted_task_ids"]`) on that task's receipt sha; coverage asserted via `plan_lib.assert_review_coverage(<log>, tasks, receipt["task_shas"])`. **8a is NOT delegated** — the orchestrator owns it.
   - **Step 9** re-run the full suite from the orchestrator (the receipt baseline is a claim; the orchestrator's own run is the gate).
   - **Steps 11 / 11.5** unchanged (full diff review + scan).
7. **Marker** (session notes): `### WF2 Step 8 whole-issue-delegation: <APPLIED receipt-valid | FALLBACK per-task (<reason>) | SKIPPED not-enabled>`.

Interplay with `<small-standard-lane>`: whole-issue delegation is still allowed in the lane — the collapsed gates still run in the orchestrator; the receipt's Step-8a set is just usually empty.

**Parallel task execution (validated, currently serial):** Use the parallel-eligibility result from Step 5's `plan_lib.validate_parallel_groups(tasks)` to know which groups *could* run concurrently, and the `capabilities.parallelism` probe from Step 2 item 10 (#136) to know whether the environment *can* isolate them. When `parallelism == "serial-only"`, state it once ("worktree isolation unavailable → serial execution") rather than attempting concurrent dispatch and hitting an Agent-tool error; when `parallelism == "worktree"`, note that concurrent execution still waits on the execution layer (#85). **Concurrent execution remains OFF by default — execute ALL tasks sequentially in plan order**, including parallel-eligible groups. With the worktree-isolated `rawgentic:rawgentic-implementer` shipped (#164), the collision hazard that forced the serial floor is addressed at the isolation layer, so #85's concurrent Step 8 becomes a **config-gated option** (a future opt-in key, reconciled on issue #85) rather than an unconditional behavior — until that opt-in exists and is validated, sequential execution stays the safe floor, and when `parallelism == "serial-only"` there is no isolation at all, so concurrency must not be attempted (graceful fallback: same agents, strictly serial). **Staging backstop:** the "stage ONLY this task's files, never `git add -A`" rule above applies to every task; when a task additionally declares `files`, that rule becomes machine-checkable — assert the staged set is a subset of the declared `files` and STOP to reconcile if not. (A task in a parallel_group that declared no `files` is already non-eligible and runs sequentially under the same stage-only-this-task's-files rule.)

**Mid-flight risk promotion (P15):** After implementing each task and staging its diff, re-evaluate the task against the 8 risk criteria via two paths:

1. **Mechanical** — call `plan_lib.should_promote(task_id, file_paths, loc_delta)`. It returns `(True, reason)` if any file path matches the high-risk regex allowlist OR `loc_delta >= 200`.
2. **Agent-flagged** — if your implementation work surfaced subjective criteria (e.g., the new error path is non-trivial in a way the path-allowlist couldn't catch), emit a `PROMOTE: <task_id> <reason>` directive in session notes.

Either trigger fires Step 8a on the just-committed commit AND triggers a **retroactive scan** of all prior commits in this branch via `plan_lib.scan_prior_commits_for_trigger(repo, since_sha=<branch_base>, exclude_sha=<current_sha>)`. Any prior SHAs returned by the scan must also receive a Step 8a review **before Step 9**. Log the promotion using `plan_lib.format_promotion_note(task_id, criterion, rationale)`.

Promotion at the last task still triggers Step 8a (and any retroactive scan) before Step 9.

**Mid-flight feasibility check (#226 AC6).** If, while implementing (or iterating on a fix during
UAT), you introduce a **new** platform/framework/external API that the Step-3 `platform_apis:`
declaration did NOT cover — a change that bypasses the design gate — apply the lightweight
feasibility check inline **before committing** that task: prove the API works under this
project's real config (an exact-object-kind precedent or a quick spike, not the API's mere
existence), classify `fail-loud`/`fail-silent`, and add a `surface:` assertion/log if silent.
Record a `platform_apis:` block for it in session notes and fold it into the design doc so
Step 9 and the run-record stay honest. This is AC1/AC3/AC4 in miniature for the exact place the
original failure lived — a mid-UAT fix that never went back through Step 3/4.

**Debugging:** If stuck after 3 manual fix attempts, escalate to systematic debugging.

**Design flaw discovery:** If implementation reveals a fundamental design flaw:
- Check: `tdd_loopback_used == false` AND `global_loopback_total < GLOBAL_LOOPBACK_BUDGET`
- If allowed: loop back to Step 3 with the flaw identified
- If budget exhausted: STOP and escalate to user. **[Headless: ERROR — post error comment with design flaw description + loop-back history, add rawgentic:ai-error label, exit.]**

**Session checkpoint:** Update session notes with progress, verification results, deviations from plan. **[Headless: write a headless checkpoint (format in `references/headless.md`) after every 2-3 tasks to enable fresh-session resumption.]**

---

### Step 8a sub-step: Per-task Review (P15)

**Fires when:** the just-completed task has `riskLevel: high` (either as tagged in Step 5 OR promoted mid-flight in Step 8).

1. **Capture the commit's diff:**
   ```bash
   git show --no-color --format= <sha>
   ```
<!-- model-routing: role=review -->
Dispatch these reviewers as `rawgentic:rawgentic-reviewer` agents per the `<model-routing-resolve>` bundled-agent contract (`model: <review>` unless `inherit`; effort dual-path, always logged).

2. **Dispatch 2 reviewers in parallel** via the Agent tool (`rawgentic:rawgentic-reviewer` + a role brief in the prompt, same pattern as Step 11):
   - **Reviewer 1: Code-level (style + bug/logic)** — naming, imports, hardcoded credentials, off-by-one errors, null/undefined handling, race conditions, type errors. Scope: this commit's diff only.
   - **Reviewer 2: Silent-failure hunt** — catch-block swallows, missing error returns, unchecked async paths, ignored exceptions, fallthrough cases, missing `else` branches that should reject. Scope: this commit's diff only.
3. **Filter findings using the `SEVERITY_BANDED_CONFIDENCE` thresholds** (values in `<constants>`; canonical in `plan_lib.SEVERITY_BANDED_CONFIDENCE`). Count dropped findings.
4. **Triage:**
   - **Critical:** must fix before next task (block).
   - **High:** fix before next task unless deferred-with-rationale. Persist the deferral via `plan_lib.append_deferral(<deferrals_path>, finding)` (the `finding` needs at least `finding_id`, `severity`, `originator_reviewer_slot`) — it **must be re-presented to Step 11** for resolution.
   - **Medium/Low:** advisory; log to review log only.
5. **Ambiguity circuit breaker:** if any finding is ambiguous or two findings conflict, STOP and ask user. **[Headless: QUESTION — post comment with the ambiguous findings, suspend.]**
6. **Design flaw detection:** if the review surfaces a design-level flaw (not a code-level issue), consume a loop-back via `plan_lib.consume_loopback(<counters_path>, "review_design")`. On success, increment counters and return to Step 3. On exhaustion, STOP and escalate. **[Headless: ERROR — post error comment with design flaw + loop-back history, add `rawgentic:ai-error` label, exit.]**
7. **Dispatch failure fallback:** if the Agent tool errors on a reviewer dispatch, retry once after 30s. On second failure, append an entry to the review log with `verdict: "REVIEW_DISPATCH_FAILED"` and **[Headless: QUESTION — post comment with failure details, suspend]**.
8. **Append to the review log** via `plan_lib.append_review_log(<log_path>, entry)` where entry is:
   ```json
   {"task_id": "<id>", "sha": "<commit_sha>", "reviewers": ["R1","R2"],
    "verdict": "applied|deferred|REVIEW_DISPATCH_FAILED",
    "findings": {"crit": N, "high": N, "med": N, "low": N, "dropped": N}}
   ```
9. **Update the review-state pointer** via `plan_lib.write_review_state(repo_root, branch, last_review_log_status)` (path resolved by `plan_lib.review_state_path(repo_root, branch)`). Valid statuses: `"applied"|"suspended"|"dispatch_failed"`. This pointer is **local, git-excluded bookkeeping** (#231 AC2) — `write_review_state` auto-appends `.rawgentic/` to the repo's `.git/info/exclude` so it can never land in the feature PR. **Do NOT stage or commit it** (staging `.rawgentic/` into an app PR is the exact #231 bug); commit only the actual fix files.
10. **Log per-task marker in session notes:** `### WF2 Step 8a [task <id>, sha <abc>]: DONE (<summary>)`.
11. **Headless suspend protection:** when Step 8a suspends (any QUESTION/ERROR path), convert the PR to draft if one exists (`gh pr ready --undo`). On fork PRs or no-perm sessions, post a blocking review comment instead.

### Output
For each high-risk task: an applied|deferred review log entry, review-state pointer updated (local, git-excluded — never staged into the PR), optional fix commits, session-note marker. The branch is not "ready" until the last `last_review_log_status` is `"applied"`.

### Step 8a Failure Modes
- Reviewer cost spike on a plan with many high-risk tasks: confirmed expected behavior (P15 trades cost for early signal).
- A Step 8a-deferred High finding is never re-presented at Step 11: this is what `plan_lib.assert_no_unresolved_high_deferrals` defends against in Step 11's exit check.

---

### Step 8 Failure Modes (main task loop)

These apply to the main Step 8 implementation loop above (Step 8a, the per-task review sub-step, has its own failure modes listed under it).

- Verification fails and cannot be fixed -> flag blocker to user
- Design flaw discovered -> loop back to Step 3 if budget allows
- For TDD: test passes before implementation (test not testing right thing) -> rewrite test

---

## Step 9: Quality Gate — Implementation Drift Check

### Instructions

**Small-standard lane variant (`<small-standard-lane>`).** In the lane, run **Part B (evidence)
only** — i.e. **evidence-only**: run the suite, record the delta, and verify each acceptance
criterion has a covering test. **Part A (the alignment self-review) is removed in the lane** (it adds
little on a checklist plan; the evidence is the real gate). The P15 review-coverage assertion and
the implausibility check below still run.

**Lane cross-check (input-source honesty).** Because Step 2's file_count was an ESTIMATE,
recompute the REAL impl-file count from the actual diff — `git diff --name-only
origin/<default>..HEAD`, applying the same counting rule via `plan_lib.count_impl_files` — and
compare against `LANE_MAX_IMPL_FILES`:
```bash
python3 -c "import sys,subprocess; sys.path.insert(0,'hooks'); from plan_lib import count_impl_files, LANE_MAX_IMPL_FILES; paths=subprocess.run(['git','diff','--name-only','origin/<default>..HEAD'],capture_output=True,text=True).stdout.split(); n=count_impl_files(paths); print(n, n > LANE_MAX_IMPL_FILES)"
```
If the real count materially exceeds `LANE_MAX_IMPL_FILES`, log a **`lane-widened`** note to
session notes AND set a run-record note (the design panel was skipped on a change that turned out
larger than estimated) — do **NOT** retroactively fail: the gates that DID run (Step 11, Step
11.5, Step 8a) are still valid and load-bearing.

**Part A: Drift check (apply the quality-bar rubric, `references/quality-bar.md`):**
- Plan-implementation alignment: does every task have a corresponding implementation?
- Design-implementation alignment: does implementation follow the critiqued design?
- Acceptance criteria verification: for each criterion, identify the test/verification that covers it
- Documentation check: are required docs updated?
- **P15 review coverage (NEW):** invoke `plan_lib.assert_review_coverage(<log_path>, plan_tasks, task_to_sha)`. Every high-risk task (including mid-flight-promoted) must have an `applied` or `deferred` entry in the review log. `REVIEW_DISPATCH_FAILED` entries DO NOT count as coverage.
- **Implausibility check (NEW):** if the plan was tagged `implausible_zero` in Step 5 and the diff touches paths matching `plan_lib.DEFAULT_HIGH_RISK_PATH_PATTERNS`, fail Part A with an explicit message: features touching security-relevant paths must have at least one high-risk task.

**Part B: Evidence enforcement:**

If `capabilities.has_tests`:
- Run full test suite using `capabilities.test_commands`
- Verify new tests actually test new behavior
- Confirm no regressions

If NOT `capabilities.has_tests`:
- Re-run all verification commands from the plan
- Confirm all produce expected results
- Document verification evidence in session notes

**Deferred-to-target tasks (#138):** for every task in `plan_lib.deferred_tasks(tasks)`, list it explicitly with (a) its deferral reason and (b) the **local proxy that WAS run** (compile/typecheck/extractable-unit-tests). A deferred task **never counts as verified** and **never fails the gate by itself** — but a deferred task with NO local-proxy evidence recorded is NOT satisfied (the proxy is still required; deferral is not a pass). It is impossible to silently convert deferred → passed: the deferred surface is tracked separately in the Step 16 run-record `verification_deferred` list, and `<completion-gate>` reconciles the plan's deferred tasks against that list via `plan_lib.assert_deferrals_recorded`.

**Runtime-surface feasibility (#226 AC5).** For changes whose behavior only manifests at
runtime — UI, platform/permission-gated APIs (Tauri capabilities, iOS entitlements, browser
permissions), GPU/audio, native features — a green suite is **not sufficient**: the test env
does not exercise the real surface, so a call the config silently denies still passes every
test. Require EITHER (a) a **real-surface spike** that exercises the exact call on the exact
object kind and records the observed result, OR (b) a `verification: deferred-to-target
(<reason>)` whose recorded entry NAMES, in its `target_check`, the **single feasibility claim
most likely to be wrong** — the on-device claim build #1 must check first. "Deferred,
unspecified" does not satisfy this: naming the likeliest-wrong claim is the whole point, so the
first thing exercised on the target is the thing that silently broke last time. (This reuses
the existing deferral machinery — `deferral_reason` + the run-record `verification_deferred[].target_check` — no new field; a mechanical "is this the *most* likely wrong claim?" check is impossible, so the drift guard pins the requirement and review judges the naming.)

Apply ambiguity circuit breaker on combined findings.

### Output
Implementation drift check with verification evidence.

### Failure Modes
- Drift detected -> fix implementation or update design doc
- Missing verification coverage -> add before proceeding
- Acceptance criteria not met -> implement missing criteria

---

## Step 10: Conditional Memorization (Background)

### Instructions

<!-- model-routing: role=analysis -->
Dispatch the memorization sub-agent per the `<model-routing-resolve>` contract for the `analysis` role (`model: <analysis>` unless `inherit`; effort dual-path, always logged).

**Runs in PARALLEL with Step 11** (dispatch with `run_in_background=true`).

1. Review quality gate findings from Steps 4, 6, and 9.
2. Identify reusable insights — patterns applicable beyond this specific issue.
3. If memorizable insights exist, curate each into memory: if a mempalace MCP
   server is available (`mcp__mempalace__*` tools loaded), store it via
   `mempalace_kg_add` (a fact/decision) or `mempalace_add_drawer` (a note),
   scoped to this project; otherwise — or if the mempalace store call fails —
   check for duplication against CLAUDE.md and MEMORY.md and append if novel.
4. If no reusable patterns: skip entirely.

### Output
Insight stored to mempalace and/or an updated CLAUDE.md (if insights memorized), or no output.

---

## Step 11: Pre-PR Code Review

### Instructions

**Runs in PARALLEL with Step 10** (this is the foreground task).

1. **Generate diff:**
   ```bash
   git diff ${capabilities.default_branch}..HEAD
   ```

   **P15 pre-flight (when Step 8a fired any reviews):** read the review log via `plan_lib.read_review_log(<log_path>)` and read deferrals via `plan_lib.get_deferred_findings(<deferrals_path>)`. Build:
   - `reviewed_shas` — SHAs that already went through Step 8a
   - `deferred_findings` — the verbatim list of deferred-High findings to re-present

   Pass both to each reviewer as context:
   - "Already reviewed at task boundary: <SHA list>. Focus on **cross-cutting concerns**; re-litigate individual files only on **material** findings (the bar is 'this is materially worse than what Step 8a saw,' not 'I might find a smaller issue')."
   - "Previously flagged & deferred: <verbatim finding list>. **RE-EVALUATE each.** A deferred High must end the review as either `applied` or with an independent concurrence from a reviewer slot different from the originator." Record each resolution via `plan_lib.resolve_deferral(<deferrals_path>, <finding_id>, status='applied'` / `add_concurrence=<other_slot>` / `user_ack=True)` — do not edit the deferrals JSON by hand.

1a. **Adversarial diff review sub-step (opt-in, cross-model — runs concurrently with the 3 review agents; issue #131).**
   Mirrors the Step 4 item 7 join-barrier pattern, but over the *diff* instead of the design doc. Report-only; additive to the 3-agent review, never a replacement.

   - **Stale sweep (first thing):** delete any leftover `.rawgentic-diff-review-*.patch` and `.rawgentic-diff-findings-*.json` under the project root before doing anything else. This is crash recovery — a finally-style cleanup-on-exit cannot cover a SIGKILL, so a prior run's stale temp files may still be on disk.
   - **Gate:** enablement via the SAME probe as Step 4 item 7 —
     ```bash
     python3 hooks/adversarial_review_lib.py is-enabled \
       --workspace .rawgentic_workspace.json --project <name> --skill implement-feature
     ```
     exit `0` = enabled, non-zero = skip. Compute `changed_paths` from `git diff --name-only origin/${capabilities.default_branch}..HEAD` — the SAME base ref as the patch below. **If that git command exits non-zero:** log the marker `failed (base ref unavailable: <reason>)`, skip dispatch, and continue (the `failed` marker satisfies the completion gate). Set `has_high_risk_task` = any plan task tagged `riskLevel: high`. Decide via `plan_lib.should_run_diff_review(enabled, changed_paths, has_high_risk_task)` (pure, tested; it raises on str/None inputs, so pass a real list). It returns `(False, <reason>)` → log marker `skipped (<reason>)` and stop; `(True, <reason>)` → dispatch.
   - **Dispatch (concurrent with the 3 review agents):** build the diff **high-risk-first** so that if the artifact is truncated, only the low-risk tail is cut. `any_high_risk_path` returns only the *first* matching path, so do NOT pass it a whole list here — instead **partition** `changed_paths`: `high = [p for p in changed_paths if plan_lib.any_high_risk_path([p])]`, `low = [p for p in changed_paths if p not in high]`. Then `git diff origin/<default>..HEAD -- <high...>` first, then `git diff origin/<default>..HEAD -- <low...>`, concatenated. **Fallback:** if `high` is empty (dispatch was reached via `has_high_risk_task` alone), build the plain full `git diff origin/<default>..HEAD` **once** — do not emit an empty-pathspec diff (which would double the patch). Write the result to `.rawgentic-diff-review-<issue>-<token>.patch` (unique token, mode `0600`) under the project root, with sidecar path `.rawgentic-diff-findings-<issue>-<token>.json`. Run in the background:
     ```bash
     python3 hooks/adversarial_review_lib.py review \
       --artifact <patch> --type diff --project-root <root> --date <date> \
       --findings-json <sidecar>
     ```
     append `--headless` when the run is headless.
   - **Join (before item 3's confidence filter):**
     - Non-zero exit → marker `failed (<reason>)`, loud session-note log, continue with the same-model findings — **never** treat a failed review as passed (and, in headless mode, post a STATUS comment noting the diff review was skipped, mirroring Step 4).
     - Exit `0` but the sidecar is missing / unreadable / invalid JSON → `failed (<reason>)`, never `no_findings`.
     - Sidecar `truncated: true` → `failed (truncated)`.
     - Success → map each finding's confidence enum through `ADV_CONFIDENCE_TO_FLOAT` (from `adversarial_review_lib`), tag each `source: adversarial`, and **merge** them into the finding list BEFORE item 3 so the severity-banded filter processes them identically. The single ambiguity breaker at item 6 runs **once** over the merged list; the design-flaw loop-back at item 7 stays the single `review` source. Marker `findings_present <N>` or `no_findings`; when the sidecar `secrets` list is non-empty, append `; secrets detected: <categories>` to the marker (and to the headless STATUS comment).
   - **Cleanup (finally-style):** delete the patch + sidecar on every handled exit path after the join. The startup stale sweep covers unhandled termination. **Staging backstop:** the temp files land under the *target* project's root (which is usually NOT this plugin repo), so the primary protection is the finally-cleanup + startup sweep, plus the explicit "stage ONLY this task's files, never `git add -A`" rule. As belt-and-suspenders, on first use append the two globs to the target repo's `.git/info/exclude` (local, untracked — does not dirty the target's committed `.gitignore`); the globs added to this plugin repo's own `.gitignore` only protect self-dogfooding runs.
   - **Marker (log exactly one per run):**
     `### WF2 Step 11 — Adversarial Diff Review: findings_present <N>|no_findings|failed (<reason>)|skipped (<reason>) — <report path if any>`

<!-- model-routing: role=review -->
Dispatch the 3 review agents as `rawgentic:rawgentic-reviewer` per the `<model-routing-resolve>` bundled-agent contract (`model: <review>` unless `inherit`; effort dual-path, always logged).

2. **Dispatch 3-agent parallel review.** If any returns 429, retry that agent after 30s.

   **Agent 1: Style & Convention Compliance**
   - Code style rules from project conventions and config.formatting
   - Naming conventions
   - Import ordering
   - No hardcoded credentials or secrets

   **Agent 2: Bug & Logic Detection**
   - Logic errors, edge cases, race conditions
   - Silent failures in catch blocks
   - Null/undefined handling
   - Off-by-one errors, boundary conditions

   **Agent 3: Architecture & History Analysis**
   - Does this change break patterns established by prior commits?
   - Are there related files that should also change?
   - Are there security implications?
   - Is the change backward-compatible?

3. **Filter by confidence:** Apply the severity-banded thresholds from `SEVERITY_BANDED_CONFIDENCE` (values in `<constants>`; canonical in `plan_lib.SEVERITY_BANDED_CONFIDENCE`). The flat 0.80 in `REVIEW_CONFIDENCE_THRESHOLD` is a legacy fallback; the banded values are authoritative. Log dropped-finding counts.

4. **Severity-based fix workflow:**
   - Critical/High: fix before PR
   - Medium/Low: advisory (fix if easy, otherwise note)

5. **Evaluate each finding before fixing:** verify it's real, check YAGNI, push back on unnecessary changes.

6. Apply ambiguity circuit breaker.

7. **Design flaw detection:** If review finds fundamental flaw, consume a loop-back via `plan_lib.consume_loopback(<counters_path>, "review")`. On success, return to Step 3. On exhaustion, escalate. Consume a loop-back **only** for findings with `source: review` (same-model); adversarial-sourced findings (merged in by sub-step 1a with `source: adversarial`) are report-only and MUST NOT consume a design loop-back here — they are advisory input to the fix workflow, not a loop-back trigger.

8. **Deferred-resolution exit gate (P15):** before declaring Step 11 complete, call `plan_lib.assert_no_unresolved_high_deferrals(<deferrals_path>)`. If any deferred Critical/High remains unresolved (not `applied` and lacking independent concurrence from a different reviewer slot), Step 11 cannot complete. A finding with `defer_count >= 2` additionally requires `user_ack: true`.

9. **Update review-state pointer:** after Step 11 passes, call `plan_lib.write_review_state(repo_root, branch, "applied")` (file path resolved via `plan_lib.review_state_path`). It is **local, git-excluded** (auto-added to `.git/info/exclude`); do NOT stage or commit it (#231 AC2).

### Output
Code review result with filtered findings and fixes applied. The local, git-excluded `.rawgentic/review-state/<branch-sanitized>.json` reflects "applied" (not committed — #231 AC2).

### Failure Modes
- Fundamental design flaw -> loop back to Step 3 if budget allows; if budget exhausted: **[Headless: ERROR — post error comment with design flaw description + code review findings + loop-back history, add rawgentic:ai-error label, exit.]**
- Excessive noise (>20 Low findings) -> filter at confidence >= 0.80

---

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
   `gate.errors`, and the top-level `skipped` / `findings`.

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
   STOP and present to the user. **[Headless: if `gate.blocking` is non-empty and
   cannot be auto-resolved, post an error comment listing the blocking findings,
   add the `rawgentic:ai-error` label, and exit. A leaked *real* secret is ALWAYS
   an escalation in headless mode — never auto-handle a live credential.]**

Log a marker in `claude_docs/session_notes.md`:
`### WF2 Step 11.5: Security Scan — DONE (blocking: N resolved, advisory: N, skipped: <kinds>)`

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

## Step 12: Create PR and Push

### Instructions

1. **Wait for join barrier:** Both Step 10 and Step 11 complete.

2. **Include memorization changes:** If Step 10 updated CLAUDE.md, commit it:
   ```bash
   git add CLAUDE.md && git commit -m "docs: update CLAUDE.md with implementation insights (#<issue_number>)"
   ```

2a. **Update README + docs (mandatory decision, not optional):** Before pushing,
   explicitly decide whether this feature changed anything user-facing — new
   commands/flags, changed behavior, new files, or new config. If so, update
   `README.md` and the relevant `docs/` file(s) and commit them so they ship in
   this PR:
   ```bash
   git add README.md docs/ && git commit -m "docs: update README/docs for #<issue_number>"
   ```
   If there is genuinely no user-facing change (pure internal refactor, or
   groundwork that does nothing visible yet), state that explicitly in the PR
   body's Summary rather than silently skipping. Stale or omitted docs are a
   recurring miss — make the call deliberately every time.

2b. **HTML design artifact — create-or-update BEFORE the PR (opt-in, #174).** Same
   slot as the dashboard-before-PR rule. Config-gated — skip silently unless the
   project opts in (`is_enabled_for(..., 'implement-feature', key='designArtifact')`;
   exit 0 = enabled). **Target doc — shared vs per-issue:** read the `designArtifact.sharedDoc` config via
   `design_artifact_shared_doc('.rawgentic_workspace.json', '<name>')`. When it returns
   a path, use **shared-doc mode** — update THAT single rolling doc (the multi-issue /
   campaign model: one program doc updated per slot, like this repo's modernization
   dashboard), refreshing this issue's section, and do NOT create a per-issue file. When
   it returns None (default), use **per-issue** mode:
   `docs/planning/<issue>-<slug>.{md,html}`. Either way, create or update the `.md`+`.html`
   and commit BOTH inside THIS feature PR (one PR per issue; no trailing artifact commits).
   Render with the shared helper —
   never hand-roll HTML — embedding this run's **telemetry** read from the run-record
   structure (Step 16's `/tmp/wf2-run-record.json`; gate findings/resolved, tests +
   suite delta, security-scan, lane, `usage`), never hand-retyped:
   ```bash
   python3 hooks/render_artifact.py --md docs/planning/<issue>-<slug>.md \
     --out docs/planning/<issue>-<slug>.html --title "#<issue> <title>" \
     --telemetry /tmp/wf2-run-record.json --style <style>
   git add docs/planning/<issue>-<slug>.md docs/planning/<issue>-<slug>.html
   ```
   **Style (#199):** resolve `<style>` via
   `adversarial_review_lib.design_artifact_style('.rawgentic_workspace.json', '<name>')`
   → `roadmap` (h2 sections rendered as dashboard-style bubble cards with completion
   chips — for a campaign/roadmap doc) or `plain` (the default document style,
   byte-identical to pre-#199). Pass it as `--style <style>`; omitting the flag also
   yields `plain`, so an unconfigured project is unchanged.
   Fields not knowable pre-PR (PR #, CI, merge SHA) follow the established
   convention: filled by the next slot's pass. Log
   `### WF2 Step 12 — design artifact (updated|skipped)`.

3. **Final push:**
   ```bash
   git push origin <branch_name>
   ```

4. **Pre-PR test gate** (conditional):
   - If `capabilities.has_tests`: run full suite, block PR if tests fail
   - If NOT `capabilities.has_tests`: re-run key verification commands, document results

4a. **P15 review-state gate:** read via `plan_lib.read_review_state(repo_root, branch)`. If the returned state is `None` (missing or branch mismatch) OR `state["last_review_log_status"] != "applied"`, REFUSE to open the PR and surface unresolved review state to the user (or to the issue comment in headless mode). This catches any Step 8a suspend that did not resolve before the PR-creation attempt.

5. **Create PR:**
   ```bash
   gh pr create \
     --repo ${capabilities.repo} \
     --title "<type>(scope): <description> (#<issue_number>)" \
     --body-file /tmp/wf2-pr-body.md
   ```

   PR body template:
   ```
   ## Summary
   [summary of changes]

   Closes #<issue_number>

   ## Design Decisions
   [key choices from Step 3]

   ## Verification
   [test results if available, or verification evidence]

   ## Deferred verification
   [ONLY when plan_lib.deferred_tasks(tasks) is non-empty; OMIT this whole
   section when empty. One bullet per deferred task, generated from the same
   verification_deferred list the run-record carries:
   - <task_id> (<reason>): <the exact manual check to run on the target — command/steps>]

   ## Quality Gate Summary
   - Design critique (Step 4): N findings
   - Plan drift check (Step 6): N findings
   - Implementation drift check (Step 9): N findings
   - Code review (Step 11): N findings (all Critical/High resolved)
   - Security scan (Step 11.5): N blocking resolved, N advisory, skipped: <kinds or "none">
   - <the `plan_lib.branch_protection_line(...)` string from Step 1 item 9 (#139) — states which layer enforces the gates>
   ```
   The `## Deferred verification` heading is the one canonical string (Step 9, Step 16, and `<completion-gate>` all key on it); do not reword it.

### Output
PR URL.

### Failure Modes
- Tests/verifications fail: fix and retry
- Push fails: retry; if persistent, save PR body locally
- Branch conflicts: rebase, resolve, re-push

---

## Step 13: CI Verification (Conditional)

### Instructions

**If `capabilities.has_ci == false`:** Log "No CI configured — skipping Gate 2" in session notes and proceed to Step 14.

**If `capabilities.ci_quarantined == true` (#137 — CI present but human-declared untrustworthy):** the suite is chronically red for reasons unrelated to any diff, so a red run here is noise, not a gate. Still **observe** the run, but record its outcome as a **visible non-gate** — never block, never claim green:
0. **Trust guard (a PR must not disable its own CI gate).** Quarantine only counts when it comes from the TRUSTED base config, not this branch's diff. Load the base config (`git show origin/${capabilities.default_branch}:<config-path>`) and compare via `capabilities_lib.ci_quarantine_change(base_config, head_config)`. If it returns a non-None reason (the branch INTRODUCED or ALTERED the quarantine), **the quarantine does NOT take effect for this run — CI GATES normally** (fall through to the active-CI path below), and surface the change for explicit owner approval. **[Headless: QUESTION — post a comment that the branch changes CI-quarantine config; suspend for approval rather than self-approving a gate bypass.]** Only when the quarantine is unchanged from base do you proceed as a non-gate:
1. Trigger/observe the run the same way (`gh run list ... --json status,conclusion,databaseId`).
2. Record in session notes AND the Step 12 PR body, verbatim: `CI quarantined (<capabilities.ci_quarantine_reason>): run <status/conclusion>, not gating`. Include `since <capabilities.ci_quarantined_since>` when set.
3. Do NOT diagnose/fix/block on a red run, and do NOT report it as passed. Proceed to Step 14 regardless of conclusion. Quarantine is read from config only — WF2 never enters or lifts it (that is a human edit to `config.ci.status`).

**If `capabilities.has_ci == true` (and not quarantined):**

1. Monitor CI:
   ```bash
   gh run list --repo ${capabilities.repo} --branch <branch_name> --limit 1 --json status,conclusion,databaseId
   ```

2. If CI passes: proceed to Step 14.

3. If CI fails: diagnose with `gh run view <id> --log-failed`, fix, push, CI re-runs.

4. If CI times out (> CI_MAX_WAIT_MINUTES): ask user for explicit approval. **[Headless: AUTO-RESOLVE — wait up to 2x CI_MAX_WAIT_MINUTES. If still not done, ERROR — post error comment with CI run URL, add rawgentic:ai-error label, exit.]**

### Output
CI status, quarantine notice, or skip confirmation.

---

## Step 14: Merge PR and Deploy (Adaptive)

### Instructions

**[Headless: AUTO-RESOLVE — SKIP THIS ENTIRE STEP. In headless mode the PR is the terminal deliverable: do NOT merge, do NOT deploy, do NOT SSH anywhere. Proceed directly to Step 16. CI handles deployment when a human merges the PR. (The `ssh`/`script`/`compose` deploy paths below would otherwise run unconditionally — this is the gap that caused the chorestory #309 dev-VM incident.) `wal-guard` also blocks any SSH at the hook layer as a backstop. Append the skip marker per Step 16 item 1b.]**

**P15 pre-merge gate:** re-read via `plan_lib.read_review_state(repo_root, branch)`. If None or `last_review_log_status != "applied"`, refuse to merge. Cleanup of `claude_docs/.wf2-state/<issue>/` AND the branch's `.rawgentic/review-state/<branch-sanitized>.json` happens on merge success.

**Quarantine × protection contradiction check (#139):** before attempting the merge, call `plan_lib.quarantine_protection_contradiction(capabilities.ci_quarantined, <protection state from Step 1 item 9>, <required_checks>)`. A non-None message means CI is quarantined (WF2 non-gating) but branch protection REQUIRES a status check — the squash-merge below would hit a server-side wall. Surface the message to the user and STOP rather than merging into the wall. **[Headless: QUESTION — post the contradiction message, suspend for a human to lift the quarantine, fix CI, or adjust protection.]**

1. **Merge PR (squash merge):**
   ```bash
   gh pr merge <pr_number> --repo ${capabilities.repo} --squash --delete-branch
   ```

2. **Pull main:**
   ```bash
   git checkout ${capabilities.default_branch} && git pull origin ${capabilities.default_branch}
   ```

3. **Deploy (adaptive based on capabilities.deploy_method):**

   **If `deploy_method == "script"`:**
   Run the deploy script from `config.deploy`.

   **If `deploy_method == "ssh"`:**
   SSH to infrastructure hosts from `config.infrastructure.hosts[]` and execute the deployment commands appropriate for the change (docker compose up, service restart, config reload, etc.). Generate commands based on the implementation plan — do NOT use hardcoded commands.

   **If `deploy_method == "compose"`:**
   Run `docker compose up -d` with the relevant compose file.

   **If `deploy_method == null` or `"manual"`:**
   Present deployment instructions to the user:
   ```
   MANUAL DEPLOYMENT REQUIRED
   ==========================
   The following changes need to be deployed:
   [list of changes and where they need to be applied]

   Suggested commands:
   [generated from implementation plan]

   Please deploy and confirm when complete.
   ```
   Wait for user confirmation before proceeding to Step 15. **[Headless: not reachable — the whole step is skipped in headless mode (see the Step 14 header), so this manual-deploy confirmation only ever runs interactively.]**

### Output
Deployed (or manual deployment instructions provided and confirmed).

### Failure Modes
- Merge conflicts: rebase and re-push
- Deploy fails: check logs, rollback if needed
- Manual deploy: user must confirm completion

---

## Step 15: Quality Gate — Post-Deploy Verification (Conditional)

### Instructions

**[Headless: SKIP — no deployment occurred (Step 14 was skipped), so there is nothing to verify. Proceed to Step 16.]**

**If `capabilities.has_deploy == false` AND no deployment was performed:** Skip with note "No deployment target — verification deferred to manual testing."

**If deployment was performed:**

Apply the quality-bar rubric (`references/quality-bar.md`) over check dimensions adapted to what was deployed:

- **Health check verification:** For each affected service, verify it responds correctly. Generate health check commands from the implementation context (not hardcoded URLs).
- **Acceptance criteria spot-check:** For each criterion, verify evidence of correct behavior using the verification commands from the plan.
- **Regression check:** Did any existing functionality break?

Apply ambiguity circuit breaker.

### Output
Post-deploy verification result (or skip confirmation).

### Failure Modes
- Health checks fail -> inspect logs, restart services
- Acceptance criteria not verifiable -> flag as test gap, verify manually if possible

---

## Step 16: Workflow Completion Summary

### Instructions

The completion summary is no longer hand-typed — its shape used to drift run to
run, and nothing about the run was captured for later analysis. Instead, assemble
a structured **run-record** from the data gathered across this workflow and drive
the summary through `hooks/work_summary.py`, which renders the standardized "WF2
COMPLETE" block AND appends the record to a JSONL store. Accumulated across runs
the store is the Tier-2 measurement telemetry substrate (per
`docs/measurements/`), so every gate's findings-caught-vs-resolved becomes a
measurable signal — not just a sentence the user reads once.

1. Update session notes with WF2 results.

1b. **Headless mode:** if `additionalContext` has "HEADLESS MODE active", Steps 14
   and 15 were skipped — the PR is the terminal deliverable. Record this for
   auditability by appending a session-notes marker:
   `### WF2 Step 14/15: SKIPPED (headless — PR #N is terminal; merge/deploy deferred to human + CI)`
   and set the run-record `outcome.deploy` to `"not_applicable"` with a
   `follow_ups` note `"headless: merge/deploy deferred to human + CI"`. The
   rendered summary then reflects deploy: not_applicable for the headless run.

2. **Assemble the run-record** from the workflow so far and write it to
   `/tmp/wf2-run-record.json` (use the Write tool, or a `cat > … <<'JSON'`
   heredoc). The full schema, the field-presence rules, and the per-gate `status`
   conventions live in **`references/run-record.md`** — read it before assembling.
   In short: every documented key must be **present** (a dropped field is a
   telemetry gap, not a `null`), counts are non-negative integers, `resolved` ≤
   `findings`, and `workflow` is `"implement-feature"`.

2b. **Capture usage (#189) — populate the `usage` object with REAL numbers.** #155 added
   the `usage` field but nothing filled it, so it was null in all 24 records and #162's
   yield-per-token gate was incomputable. Before assembling, capture this session's tokens
   from its Claude Code transcript (stdlib-only parse, no network):
   ```bash
   python3 hooks/usage_capture.py capture --session-id "$CLAUDE_CODE_SESSION_ID"
   ```
   It prints a full 5-key `usage` object with `capture_status: "captured"` and real
   per-model `model_mix` + totals, OR just `{"capture_status": "unavailable"}` when the
   session file is missing / mid-write / has no usage. On the `captured` object: set its
   `wall_clock_s` from the orchestrator's own timing, then use it as the record's `usage`.
   On `unavailable`: do NOT merge the bare one-key dict (the `usage` object is present-is-strict
   — all five keys required); instead emit a full object with the five keys null and
   `capture_status: "unavailable"` (null tokens are honest here; do NOT fabricate numbers).
   Because
   `capture_status: "captured"` is fail-closed at the validator (`work_summary.py` REQUIRES
   non-null tokens summing > 0 when captured), you can never persist a captured-but-null
   record — the #155 state is now impossible. `ccusage` is a manual cross-check only, not
   the capture path.

2c. **Lane marker (small-standard lane):** the run-record carries a `lane` field —
   `"small-standard"` when the run took the `<small-standard-lane>`, `"full"` otherwise — so lane
   runs stay measurable against full runs (`complexity` still reflects the Step-2 classification).
   If a Step-9 lane cross-check widened the lane, add the `lane-widened` note to `follow_ups`.
   `lane` is documented as an OPTIONAL field in `references/run-record.md` (#135): do NOT change
   `hooks/work_summary.py` — `validate_record` only checks the keys it knows about and does not
   reject unrecognized top-level keys, so an omitted or present `lane` are both valid.

3. **Render + persist.** Carry `activeProject.path` in as a literal (shell vars
   do not persist across Bash tool calls):
   ```bash
   python3 hooks/work_summary.py summarize \
     --record-file /tmp/wf2-run-record.json \
     --project-root <activeProject.path>
   rc=$?
   ```
   The tool's stdout **is** the completion summary — present it to the user as-is
   (do not re-type it). It also appends the record to
   `<activeProject.path>/docs/measurements/run_records.jsonl` (override with
   `--store` or `$RAWGENTIC_RUN_RECORD_STORE`).

4. **Handle the exit code:**
   - `rc == 0`: record valid and persisted. Done.
   - `rc == 1`: the summary still rendered (the user keeps Step 16 output) but the
     record FAILED validation and was **not** persisted — a telemetry gap. The
     stderr lists exactly which fields are wrong; fix `/tmp/wf2-run-record.json`
     and re-run so the substrate stays complete. If it genuinely can't be fixed,
     record the gap in session notes rather than ignoring it.
   - `rc == 2`: usage error / unreadable record file — fix the invocation.

Log a marker in `claude_docs/session_notes.md`:
`### WF2 Step 16: Completion summary + run-record — DONE (persisted: yes/no)`

Do NOT suggest auto-transitioning to WF1 or restarting WF2.

### Output
Standardized completion summary (rendered by `work_summary.py`) + a persisted
run-record. WF2 terminates.
