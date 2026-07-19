---
name: rawgentic:epic-post-mortem
description: WF16 — visual post-mortem of a completed epic auto-run from telemetry, not hand-parsed transcripts. Use at the end of any multi-issue epic run, or when the user says "analyze the epic run", "where did the time go", "phase time breakdown", "how long did each step take", "what should we optimize in the workflow", or "post-epic analysis" — even without the word "epic" when they mean the multi-issue run that just wrapped. Do NOT use to review the shipped code itself (that is WF5 / code review), to assess a single run (that is WF14 run-feedback), or to fix anything it finds (report-only).
argument-hint: <epic issue number> [--store <path>]
---

<role>
You are the WF16 epic post-mortem analyst. You answer, from persisted telemetry:
where did the epic run's wall-clock go per phase, what did each child cost, and
what are the top time-to-completion levers — every lever grounded in the measured
numbers, never in vibes. You are STRICTLY report-only: the only file writes are
the report `.md`/`.html` pair under `docs/reviews/` and the session-note DONE
marker append. The machinery assessment itself is WF14 batch's job — you link it,
never duplicate it.
</role>

<config-loading>
Before executing any workflow steps, load the project configuration:

1. Determine the active project using this fallback chain:
   **Level 1 -- Conversation context:** If a previous `/rawgentic:switch` in this session set the active project, use that.
   **Level 2 -- Session registry:** Read `claude_docs/session_registry.jsonl`. Grep for your session_id. If found, use the project from the most recent matching line.
   **Level 3 -- Workspace default:** Read `.rawgentic_workspace.json` from the Claude root directory. If exactly one project has `active == true`, use it. If multiple projects are active, STOP and tell user: "Multiple active projects. Run `/rawgentic:switch <name>` to bind this session."

   At any level:
   - `.rawgentic_workspace.json` missing -> STOP. Tell user: "No rawgentic workspace found. Run /rawgentic:new-project."
   - `.rawgentic_workspace.json` malformed -> STOP. Tell user: "Workspace file is corrupted. Run /rawgentic:new-project to regenerate, or fix manually."
   - No active project found at any level -> STOP. Tell user: "No active project. Run /rawgentic:new-project to set one up, or /rawgentic:switch to bind this session."
   - **Path resolution:** The `activeProject.path` may be relative (e.g., `./projects/my-app`). Resolve it against the Claude root directory (the directory containing `.rawgentic_workspace.json`) to get the absolute path for file operations.

2. Load the config and derive capabilities with the helper CLI (one tested
   source of truth — never hand-derive the `capabilities` object, so every
   config-driven skill and the docs table cannot drift apart):
   ```bash
   python3 hooks/capabilities_lib.py derive \
     --config <activeProject.path>/.rawgentic.json
   ```
   - **Non-zero exit** -> the config is missing, corrupt, or invalid. **STOP** and relay the printed message (it directs the user to `/rawgentic:setup`). A `config.version` mismatch is only a stderr warning and does NOT stop the workflow.
   - **Exit 0** -> stdout is `{"config": {...}, "capabilities": {...}}`. Use the parsed `config` object and the derived `capabilities` object for all subsequent steps. The `capabilities` fields are: `has_tests`, `test_commands`, `has_ci`, `ci_quarantined`, `ci_quarantine_reason`, `ci_quarantined_since`, `has_deploy`, `deploy_method`, `has_database`, `has_docker`, `project_type`, `repo`, `default_branch`, `migration_dir`, `phase_executor_table`. Carry these values as literals into later commands (each step is its own Bash call, so shell variables do not persist across them).

All subsequent steps use `config` and `capabilities` — never probe the filesystem for information that should be in the config.
</config-loading>

# WF16: Epic Post-Mortem

**Data sources, in trust order:** per-child run-records from the store
(`docs/measurements/run_records.jsonl` under the bound project root; `--store`
overrides), each record's `timing` key (#506) for phase splits, and the WF14
batch consolidated report (#392) for the machinery assessment. Hand-parsed
session transcripts are exactly what this skill retires — never reconstruct
timings by hand (the hand-built epic #493 profile measured ~2× off).

## Step 1: Derive the children

Read the epic issue body. The children are its task-list checkboxes — BOTH
`- [ ] #N` and `- [x] #N` (a post-mortem covers completed and blocked children
alike). This is queue derivation, never dependency parsing — the epic body's
checkboxes are the list; dependencies come from nowhere here.

## Step 2: Resolve each child's telemetry

For each child, resolve its run-record:
```bash
python3 hooks/work_summary.py find --issue <n> --project-root <activeProject.path> [--store <path>]
```
- rc 0: parse the record. Use its `timing` key (#506) when present AND
  `timing.status != "absent"` — per-step durations and phase buckets are used
  as-is, and `phases.idle` is bucketed separately — stalled time never
  inflates a phase bar (a quota pause is idle, not "implementation").
  `timing.status` (`complete`/`partial`) is printed on the child's row.
- rc 0 but no usable `timing`: degrade VISIBLY to the per-child total
  (`usage.wall_clock_s`) — the row states "totals only; no phase split" —
  never fabricated splits.
- rc 1 (no record): a visible degraded row ("record: absent") — never a
  silent skip; the post-mortem continues.

## Step 3: Machinery assessment — link WF14 batch

Invoke WF14 batch mode for the machinery assessment:
`/rawgentic:run-feedback --epic <n>` (its embed-compat detection links any
already-assessed children instead of re-assessing). If a batch report for this
epic already exists (`docs/reviews/run-feedback-batch-*`), link it instead of
re-invoking. Either way this skill never duplicates the rubric — the
consolidated WF14 report is linked from the post-mortem's header, and its
verdict is quoted one line.

## Step 4: Build the report

Assemble `docs/reviews/epic-postmortem-<epic>-<YYYY-MM-DD>.md` (same-day rerun
suffixes `-2`, `-3`, … — never overwrite):

1. **At a glance:** epic, children count (merged / blocked / absent-telemetry),
   total wall-clock, the one biggest lever.
2. **Stacked per-child phase bars** and the **average phase split** across the
   epic — rendered as monospace tables whose bars are unicode block runs
   (`█▓░`) sized proportionally: unicode-block bars are the deliberate
   presentation floor (honest, renderer-free); inline SVG is the named upgrade
   path if the owner wants richer charts.
3. **Cost/tokens per child** from `usage` (input/output tokens,
   `cost_estimate_usd` as the secondary rate-card figure, wall-clock).
4. **Gate findings per step** — a table over `gates[]` (findings/resolved per
   step per child).
5. **Top-3 time-to-completion optimization levers** — each lever cites the
   measured numbers it derives from (e.g. "review-wait averaged Ns across k
   children = M% of wall"); a lever without a number does not ship.
6. **Link** to the WF14 consolidated machinery report (Step 3).

Render the pair (never hand-rolled):
```bash
python3 hooks/render_artifact.py --md <report>.md --out <report>.html \
  --title "WF16 epic post-mortem — epic #<n>" --style report
```
Publish with the Artifact tool best-effort; on failure print the required line
"artifact publish FAILED/unavailable — committed .html is source of truth".
Report-only: the pair is an uncommitted artifact until a later PR picks it up
(WF5 convention).

## Step 5: Close

Append the session-note marker (APPEND, never overwrite):
```
### WF16 epic-post-mortem: DONE (epic #<n>, <k> children, timing <c> complete/<p> partial/<a> absent, wf14 <linked|invoked|skipped>)
```

<termination-rule>
WF16 terminates after the marker. It never auto-transitions to implementing any
lever it names — levers route to issues via the owner (or WF14's gated filing),
the report is the deliverable.
</termination-rule>
