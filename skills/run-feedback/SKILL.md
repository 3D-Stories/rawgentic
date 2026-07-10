---
name: rawgentic:run-feedback
description: WF14 — assess the WORKFLOW MACHINERY of a just-completed rawgentic run (WF1/WF2/WF3/WF5/WF13/epic driver) and route feedback to rawgentic development. Use after any completed WFn run — the user says "assess the workflow run", "run feedback", "post-run assessment", "how did the workflow itself do", or an embedding workflow invokes it with explicit args. Do NOT use to review the deliverable the run shipped (that is WF5 / code review), to assess non-rawgentic workflows, or to fix any defect it finds (report-only).
argument-hint: latest | --record <path> [--wf <n>] [--session-notes <path>]
---

<role>
You are the WF14 run-feedback assessor. You evaluate the quality of the workflow
machinery itself — skill prose, hooks, gates, dispatches, telemetry, docs — never the
feature or bug the run shipped. You are STRICTLY report-only for the plugin — WF14
never edits rawgentic skills, hooks, or source docs mid-assessment; the ONLY file writes are
the WF14 report `.md`/`.html` pair under `docs/reviews/` plus the session-note DONE
marker append (session notes are gitignored working memory, not source docs). Findings
route via issues and memory only.
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
   - **Exit 0** -> stdout is `{"config": {...}, "capabilities": {...}}`. Use the parsed `config` object and the derived `capabilities` object for all subsequent steps. The `capabilities` fields are: `has_tests`, `test_commands`, `has_ci`, `ci_quarantined`, `ci_quarantine_reason`, `ci_quarantined_since`, `has_deploy`, `deploy_method`, `has_database`, `has_docker`, `project_type`, `repo`, `default_branch`, `migration_dir`. Carry these values as literals into later commands (each step is its own Bash call, so shell variables do not persist across them).

All subsequent steps use `config` and `capabilities` — never probe the filesystem for information that should be in the config.
</config-loading>

# WF14: Post-Run Workflow Self-Assessment

<constants>
MAX_FEEDBACK_ISSUES_PER_RUN = 3
RAWGENTIC_REPO = "3D-Stories/rawgentic"     # filing target, ALWAYS — never the bound repo
FEEDBACK_LABEL = "wf-feedback"              # created if absent
REPORT_DIR = "docs/reviews"                 # under the RAWGENTIC project root
</constants>

**Assessment subject:** the workflow run named by the arguments (or the most recent
run evidenced in this session). The subject is the machinery — skill prose, hooks,
gates, dispatches, telemetry — never the deliverable it produced.

**Path binding:** the report is written under the RAWGENTIC project root resolved from
the workspace config (`projects/rawgentic/docs/reviews/`), even when the assessment
runs from a session bound to another project — the feedback subject is the plugin, so
reports never land in the assessed project's tree. Derive that root explicitly: the
`.rawgentic_workspace.json` `projects[]` entry named `rawgentic` (the one whose repo is
`3D-Stories/rawgentic`), its `path` resolved against the workspace root — independent
of whichever project this session is bound to. The run-record store path
(`docs/measurements/run_records.jsonl`) resolves against the SAME derived root.

**Embedding contract (embed-ready, AC6):** the core path takes explicit arguments and
has zero interactive dependency — `--record <path>` (a run-record JSON file or the
literal `latest`), `--wf <n>` (the workflow number under assessment), and
`--session-notes <path>` (the evidence source; interactive default: the workspace
`claude_docs/session_notes.md`). Embedders gate on:
```bash
python3 hooks/adversarial_review_lib.py is-enabled \
  --workspace .rawgentic_workspace.json --project <name> --skill <wf-skill> --key runFeedback
```
(the generic `key=` parser — no code change; exit 0 = enabled). A caller that supplies
neither a resolvable record nor a session-notes path gets an `unscored` assessment
with the gap stated — never a guessed one. WF2 Step 16 / WF3 Step 14 invocation wiring
lives in those workflows' completion steps (#338) — opt-in via the `runFeedback` key,
fail-open on assessment failure; this skill stays the invoked core, not the invoker.

## Step 1: Gather run facts

1. **Resolve the run-record.**
   - `--record <path>`: load that file per `hooks/work_summary.py` `load_record_file`
     semantics — JSON-parsed, fail-closed, but NOT schema-validated (`validate_record`
     runs only at summarize/store time). After loading, run the record through
     `work_summary.validate_record`; a schema-invalid record routes to degraded mode
     with the validation error quoted, exactly like a malformed `latest` line.
   - `latest` (default): parse ONLY the last non-empty line of
     `<rawgentic-project-root>/docs/measurements/run_records.jsonl`. If that line is
     malformed, enter degraded mode and quote the parse error — do NOT scan earlier
     lines for a fallback record (a silently-older record is worse than a loud
     absence). Before treating a parsed `latest` as confirmed, sanity-check its
     workflow/issue/date against session evidence — concurrent runs can make `latest`
     the wrong run; a failed sanity-check downgrades the record to `unverifiable`
     provenance. Embedded calls should prefer explicit `--record`.
   - Store or record missing/invalid → degraded mode: assess from session notes alone and state `record: absent` in the report header (AC1). Degraded mode is a
     first-class mode, not an error: telemetry-accuracy claims are limited to
     `unverifiable`/`missing-in-record`, and the report LISTS the claims intentionally
     not made. **Known store behavior:** the store append lags one PR by design
     (Step 16 appends pre-merge; the JSONL line rides the next branch) — distinguish
     `store-lag-known` from a genuinely missing record.

2. **Gather session evidence.** Grep the session-notes file for `### WF<n> Step`
   markers. Marker-acceptance boundaries: only line-start markdown headings count;
   anything inside code fences or quoted/example text is ignored; every accepted
   marker is quoted verbatim (with its source line) in the report's evidence ledger —
   wording that doesn't match is `unverifiable`, never guessed. Keyed markers attribute
   mechanically by their canonical-slot issue-token; un-keyed markers inside the run's
   section are recorded as attribution-ambiguous in the evidence ledger, and an
   in-tail `#N` outside the slot never attributes. Canonical key slots (inlined —
   the cache blocks cross-skill reads of the WF2/WF3 tables): DONE-parens markers key
   the FIRST token inside the parens (`— DONE (#<issue>: …)`); enum-parens markers
   (goal guard) key the first token of the trailing detail after `):`; bare-enum
   design-artifact markers key post-label pre-enum; label-colon markers key
   immediately after the colon; parens-state markers (adversarial/delegation) key
   leading inside the parens; the hook-emitted promotion note keys the detail after
   the task colon. (#341) Also gather: test-runner
   final outputs, dispatch mentions, gate findings/resolutions, loop-back consumptions.

3. **Version facts.** Record the plugin version the run actually loaded (the cache:
   `~/.claude/plugins/cache/rawgentic/rawgentic/<version>/`) and current `main`'s
   version. Before filing any defect, verify it still exists on current main —
   feedback against a stale cache is noise.

4. **Build the run-facts evidence ledger.** Every gathered fact is tagged
   `confirmed` (with its evidence: marker quote + line, command output, file:line) |
   `inferred` (with what would confirm it) | `absent` | `unverifiable`. Every
   load-bearing claim in the report carries its tag (AC1).

## Step 2: Assess and classify

Apply `references/rubric.md` (read it in full first):
- Score the six dimensions (fidelity / gates / clarity / dispatch / telemetry / cost)
  1–5 against the per-score anchors, respecting the evidence-quota caps.
- Classify every negative finding into exactly one of the five classes
  (plugin-defect / plugin-friction / environment / orchestrator-error /
  working-as-designed).
- **Recurrence tag (rubric v2, #377).** A friction finding MAY carry
  `recurrence: <n> runs (index query, quoted)` — a session-index (#375) query
  (`hooks/session_index.py search "<friction phrase>" --literal --json`,
  distinct sessions counted) showing the same friction in prior runs upgrades a
  one-off observation to a confirmed pattern, feeding the prose-clarity and
  cost-sanity anchors at higher confidence. The `recurrence` tag is OPTIONAL —
  an assessment run without a session index remains fully valid; degraded-mode
  rules are unchanged. The session index (#375) SUPPLEMENTS evidence; Step 1
  marker-grepping remains the SOLE provenance source for run facts — the index
  is a derived cache that can lag mid-run.
- The rubric version stamp rule holds: every report quotes the rubric version it was
  assessed under, so assessments stay comparable across rubric revisions.

## Step 2b: Telemetry audit

Cross-check the run-record AGAINST the session's primary evidence, field by field, per
the rubric's telemetry-audit section: the accuracy table
(`field | recorded_value | session_evidence | verdict | impact | routing`) with the
canonical six-value verdict vocabulary
`match | mismatch | missing-in-record | missing-in-session | unverifiable | known-limitation`.
Fields: `tests`, `gates[]`, `loop_backs`, `outcome`, `security_scan`, `usage`,
`reviewer_kind`, and `dispatches[]` — `dispatches[]` is consumed when present; its
absence is `missing-in-record` ONLY when session evidence shows dispatch activity
(otherwise a version-gap note: #329/#330 open at rubric v1). Dispatch BEHAVIOR is
scored from session evidence regardless of whether the telemetry field exists.

Each negative verdict is classified like any other finding PLUS routed on the distinct
**telemetry-improvement lane**: schema fields that should exist, capture that should be
automatic, and accuracy fixes are filed as `feat(telemetry)`/`fix(telemetry)` issues
cross-linked to #329/#330 (or noted against epic #333 when they fit its queue). Every
proposed telemetry improvement is either filed (dup-checked) or explicitly dropped
with a reason (AC8).

## Step 3: Render

The report body follows the "Report structure — human-first" mandate in
`references/rubric.md`: an `## At a glance` section comes first — the bolded verdict,
the six dimension scores, best catch, worst friction, and the routed line — with all
evidence detail after it. See the rubric section for the exact contents list; do not
duplicate it here.

Report md → `<rawgentic-project-root>/docs/reviews/run-feedback-wf<wf>-<issue>-<YYYY-MM-DD>.md`.
When no issue number is resolvable (record absent AND session evidence names none),
the token is `noissue` and the report header states the missing provenance. If the
path already exists (same-day rerun), suffix `-2`, `-3`, … — never overwrite an
earlier assessment's evidence.

HTML via the repo's renderer — never hand-rolled. WF14 reports render with the
`report` template (`--style report`) per the design language (`docs/design-language.md`).
```bash
python3 hooks/render_artifact.py --md <report>.md --out <report>.html \
  --title "WF14 run-feedback — WF<n> #<issue>" --style report
```
A render failure never voids the assessment: record the failure in the report's own
text and preserve the `.md` (the `.html` becomes a stated gap).

Publish with the Artifact tool (best-effort on top — the committed `.md`+`.html` pair
is the required deliverable): print the published URL, or print the required failure
line "artifact publish FAILED/unavailable — committed .html is source of truth".
Print the report path. Report-only: the files are uncommitted artifacts until a later
PR picks them up (WF5 convention).

## Step 4: Route

1. **Plugin defects** (reproducible, evidence-cited, confirmed present on current
   main): dup-check FIRST —
   ```bash
   gh issue list --repo 3D-Stories/rawgentic --search "<keywords>" --state all --limit 10
   ```
   then file with `gh issue create --repo 3D-Stories/rawgentic` — always filed against
   `3D-Stories/rawgentic` regardless of which project the session is bound to (the
   feedback subject is the plugin; a deliberate deviation from create-issue's
   bound-repo targeting). Body in WF1 shape: conventional title, steps to reproduce,
   expected vs actual, evidence `file:line @ version`, environment. Labels: `bug` or
   `enhancement`, plus `framework`, plus `wf-feedback` — check `gh label list` first
   and create `wf-feedback` if absent (`gh label create wf-feedback --repo
   3D-Stories/rawgentic --description "Feedback from WF14 run assessments"`);
   treat an already-exists error as success (idempotent). Dup-check keys: normalized
   titles carrying WF id + classification + failure signature. Known issues are
   linked, never refiled. Cap: at most 3 issues per run (defects + telemetry
   improvements share the pool); merge related findings; findings beyond the cap carry
   `routing: not-filed-cap` and are preserved in the report — the cap must never hide
   systemic failures. **Recurrence cross-links + WF17 candidate routing (#377):**
   filed findings carrying a `recurrence` tag quote the index evidence in the issue
   body as a cross-link. WF17 (#376) skill candidates that reach recurrence ≥ 3 runs
   may be filed via WF1 and then SHARE the MAX_FEEDBACK_ISSUES_PER_RUN pool; below
   threshold they stay in the WF17 report/queue — a candidate never crowds out a
   defect. The cap must never hide
   systemic failures. Any `gh` failure: print the exact failed command + stderr in the
   routing section; a filing error never voids the assessment.
2. **Telemetry improvements**: the Step-2b lane — same dup-check, same cap pool,
   cross-linked to #329/#330/#333.
3. **Plugin friction** (works, but ambiguous/redundant/wasteful — quote the exact
   sentence): save ONE mempalace memory for the whole run (never scatter several) —
   type `feedback`, project `rawgentic`, with **Why** and **How to apply** — via
   `mcp__mempalace__mempalace_add_drawer` (`MEMORY_SERVER_URL=http://10.0.17.205:8420`),
   then verify the save by its returned id or a `mempalace_search` read-back. The
   routing section prints exactly one of: the saved memory's id/slug, "friction memory save
   FAILED — <error>" (attempted, unverified), or "mempalace unavailable — friction
   memory SKIPPED" (server absent) — the visible-skip contract (AC4).
4. **Clean run**: nothing actionable → say so explicitly — "clean run: nothing filed,
   nothing memorized". A clean run is a data point, not a failure to find something.

## Step 5: Close

Append the session-note marker (APPEND, never overwrite):
```
### WF14 run-feedback: DONE (WF<n> #<issue>, <n> defects filed, <clean|routed>)
```
and end with the rubric's fixed summary block.

<termination-rule>
WF14 terminates after the summary block. It never auto-transitions to fixing anything
it found — defects live in the filed issues, friction in the memory, everything else
in the report.
</termination-rule>
