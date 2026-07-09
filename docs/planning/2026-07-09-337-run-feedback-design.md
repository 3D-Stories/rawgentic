# Design — #337 WF14 run-feedback: post-run workflow self-assessment skill

Date: 2026-07-09 · Issue: [#337](https://github.com/3D-Stories/rawgentic/issues/337) · Complexity: standard_feature (Step-2 authoritative; full spine elected — AC5 requires peer consult + adversarial design gate) · Version: 3.24.26 → **3.25.0** (minor, new skill) · **Rev 2** (Step-4 loop-back: 8 merged findings applied — wf14 entry shape + guard test, 6-way verdict vocabulary unified, mempalace/Artifact spike evidence, latest-line semantics, report-path binding, evals wording) · **Rev 3** (Step-4 pass-2 loop-back: rubric-section vocab remnant fixed, report-only invariant scoped to allow the report's own write, `--session-notes` degraded-mode input contract + unscored rule, mempalace save-verification tri-state surface, same-day filename collision suffix) · **Rev 4** (Step-4 pass-3 spec-tighten: invariant explicitly covers the session-note DONE marker append (gitignored working memory), mempalace write tool named (`mempalace_add_drawer` + read-back verify) and Step 5 aligned to the tri-state, `noissue` filename fallback, marker-acceptance boundaries for prose-grep, gh spike citation + write-permission honesty, render-failure preserves the .md)

## Approaches considered

**A — single SKILL.md (WF5 style, everything inline).** Pro: fewest files, matches adversarial-review precedent. Con: the rubric is the drift-guarded, separately-evolving artifact — inlining it makes the SKILL.md huge, makes the workspace-prompt pointer awkward, and couples orchestration prose to rubric prose. Rejected.

**B — thin orchestrator SKILL.md + `references/rubric.md` (CHOSEN, issue-prescribed).** The rubric moves into the plugin as a reference file (house style: references/ scales with complexity — implement-feature precedent). SKILL.md carries steps, config contract, routing rules; rubric.md carries the assessment content. Drift guards pin canonical sentences in both.

## Skill shape

`skills/run-feedback/SKILL.md`:
- Frontmatter: `name: rawgentic:run-feedback`, description = WHEN to trigger (after any completed WFn run; "assess the workflow run", "post-run feedback"; Do NOT use to assess the deliverable itself or non-rawgentic workflows), `argument-hint: latest | --record <path> [--wf <n>]`. Near-collision check (strip `:`/`-`): `runfeedback` — no collision with existing 15 names.
- Carries the synced `<config-loading>` block (MANIFEST entry; precedent: peer-consult, adversarial-review — both report-only and synced). Needed: project root (run-record store path, docs/reviews output), project name (runFeedback key).
- **Report-only invariant (WF5 wording, scoped):** never edits rawgentic skills, hooks, or source docs mid-assessment; the ONLY file writes are the WF14 report `.md`/`.html` pair under `docs/reviews/` (uncommitted artifacts) plus the Step-6 session-note DONE marker append — session notes are gitignored working memory (confirmed: 0 tracked files under `claude_docs/`), not source docs, and the DONE marker is the universal WFn close protocol. Findings route via issues/memory only.
- **Embedding contract (AC6):** core steps take explicit args — `--record <path>`, `--wf <n>`, and `--session-notes <path>` (the evidence source; interactive default: the workspace `claude_docs/session_notes.md`) — with zero interactive dependency; embedders gate on `python3 hooks/adversarial_review_lib.py is-enabled --workspace .rawgentic_workspace.json --project <name> --skill <wf-skill> --key runFeedback` (generic `key=` confirmed at hooks/adversarial_review_lib.py:230-266 — **no code change**). Degraded-mode input rule: when the run-record is absent, session notes are the ONLY evidence source, so an embedded caller that supplies neither a resolvable record nor a session-notes path gets an `unscored` assessment with the gap stated — never a guessed one. WF2 Step 16 / WF3 Step 14 wiring = named follow-up issue (filed at PR time).

### Steps (WF14)

1. **Gather run facts.** Resolve the run-record: `--record <path>` → `work_summary.load_record_file`; `latest` (default) → last line of `<project-root>/docs/measurements/run_records.jsonl` (no latest-helper exists; `tail -1` + validate via `load_record_file` semantics). Missing/invalid store → **degraded mode**: assess from session notes alone and say so loudly in the report header (AC1). Grep session notes for `### WF<n> Step` markers (no hook parser exists — prose-grep, same as the resume protocol). Marker-acceptance boundaries: only line-start markdown headings count; anything inside code fences or quoted/example text is ignored; every accepted marker is quoted verbatim (with its source line) in the report's evidence ledger — wording that doesn't match is `unverifiable`, never guessed. Record loaded plugin cache version (`~/.claude/plugins/cache/rawgentic/rawgentic/<version>/`) vs current main.
2. **Assess + classify** per `references/rubric.md`: six 1–5 dimensions with per-score anchors, every load-bearing claim confirmed (evidence) or inferred (what would confirm), 5-way classification per negative finding.
3. **(2b) Telemetry audit** per the rubric's audit section: field-by-field table with columns `field | recorded_value | session_evidence | verdict | impact | routing` and the SIX-value verdict vocabulary `match | mismatch | missing-in-record | missing-in-session | unverifiable | known-limitation` (single canonical set — used identically in SKILL.md step prose, rubric, and drift guards; `mismatch`, `missing-in-record`, and `missing-in-session` count as negative findings; `known-limitation` and `unverifiable` never do). Fields: `tests` (vs runner final output), `gates[]` (findings/resolved vs session-note markers), `loop_backs` (vs counters file), `outcome`, `security_scan`, `usage` (standing weak spot: whole-SESSION token attribution → `known-limitation` unless internally impossible), `dispatches[]` **consume-when-present** (#329/#330 NOT landed — absence is `missing-in-record` ONLY when session evidence shows dispatch activity; otherwise a version-gap note), `reviewer_kind` fidelity, gate-count honesty. Each negative verdict classified like any finding PLUS the telemetry-improvement routing lane.
4. **Render.** Report md → `<rawgentic-project-root>/docs/reviews/run-feedback-wf<wf>-<issue>-<YYYY-MM-DD>.md`; when no issue number is resolvable (record absent AND session evidence names none), the token is `noissue` and the report header states the missing provenance; if the path already exists (same-day rerun), suffix `-2`, `-3`, … — never overwrite an earlier assessment's evidence. A render_artifact.py failure never voids the assessment: record the failure in the report's own text, preserve the `.md` (the `.html` becomes a stated gap). The output path is ALWAYS bound to the rawgentic project root resolved from the workspace config, even when the assessment runs from a session bound to another project (the feedback subject is the plugin; reports never land in the assessed project's tree). HTML via `python3 hooks/render_artifact.py --md <md> --out <html> --title "..."` (never hand-rolled) — the committed-later `.md`+`.html` pair is the REQUIRED deliverable; Artifact publication is best-effort on top: publish with the Artifact tool and print the URL, or print the required failure line "artifact publish FAILED/unavailable — committed .html is source of truth". Print the report path (AC2). Report-only: files are uncommitted artifacts until a later PR picks them up (WF5 convention).
5. **Route.**
   - Plugin defects: dup-check first (`gh issue list --repo 3D-Stories/rawgentic --search`), then `gh issue create --repo 3D-Stories/rawgentic` **always explicit-repo** (deliberate deviation from create-issue's bound-repo targeting; assessments run from chorestory/saystory sessions too). Simplification vs the seed prompt: ALWAYS direct `gh issue create` (never route through /rawgentic:create-issue even when bound to rawgentic) — deterministic, embed-ready, one code path. Labels: `bug`/`enhancement` + `framework` + `wf-feedback` (create-if-absent: `gh label list` check then `gh label create`; label confirmed ABSENT today). Cap 3/run; merge related; known issues linked never refiled; verify each defect still exists on current main before filing.
   - Telemetry improvements: filed as `feat(telemetry)`/`fix(telemetry)` cross-linked to #329/#330, or appended to epic #333 when they fit its queue (same dup-check + cap pool).
   - Friction: ONE mempalace memory (type `feedback`, project `rawgentic`, Why + How-to-apply) via `mcp__mempalace__mempalace_add_drawer` (the write tool; `mempalace_search` read-back verifies). Outcome is the platform_apis tri-state, exactly one surface line: saved id/slug | "friction memory save FAILED — <error>" | "mempalace unavailable — friction memory SKIPPED" (AC4).
   - Clean run: explicit "nothing filed, nothing memorized — clean run" outcome.
6. **Close.** Session-note marker `### WF14 run-feedback: DONE (<wf> #<issue>, <n> defects filed, <clean|routed>)` + the rubric's fixed summary block.

## Rubric (`references/rubric.md`)

Seeded from `claude_docs/prompts/wf-run-feedback.md` (workspace file becomes a pointer — workspace-side edit, not in this repo's PR). Enhancements over the seed (AC5: enhanced via peer consult during this run; consult report committed as design evidence):
- **Per-score anchors** for each of the six dimensions (what a 2 vs a 4 looks like) so scores are reproducible across assessor models.
- **Telemetry-audit section** (new, issue item 2b): the field-by-field check list above, the canonical six-value verdict vocabulary `match | mismatch | missing-in-record | missing-in-session | unverifiable | known-limitation` (identical to Steps step 3 — one set, three surfaces), the accuracy-table format, and the standing known-weak spots NAMED as checks: usage attribution (whole-session tokens), `reviewer_kind` fidelity, gate-count honesty, store-append-lags-one-PR (Step 16 appends pre-merge; the JSONL line rides the next branch).
- Degraded-mode rules: which claims are permitted from session notes alone; everything else marked inferred.
- Security: reference secrets by name only; no tokens/log dumps in filed issues or the published artifact.

## File changes

| # | File | Change |
|---|---|---|
| 1 | `skills/run-feedback/SKILL.md` | NEW |
| 2 | `skills/run-feedback/references/rubric.md` | NEW |
| 3 | `tests/test_run_feedback_clarity.py` | NEW — drift guards, test_wf3_clarity.py pattern (section-sliced, whitespace-normalized, ONE canonical sentence per guard): report-only invariant; always-explicit-repo; cap 3; degraded-mode loud; verdict vocabulary; known-weak-spots named; dispatches[] consume-when-present; mempalace tri-state surface line; artifact-publish failure line |
| 4 | `.claude-plugin/marketplace.json` | whitelist `./skills/run-feedback` before `./skills/scan`; description "6 SDLC workflow skills" → "7 SDLC workflow skills" |
| 5 | `.claude-plugin/plugin.json` | version 3.25.0; description 6→7 SDLC (breakdown regex must sum to n_skills=16: 7+6+1+2 ✓) |
| 6 | `plugins/rawgentic/.codex-plugin/plugin.json` | version 3.25.0 (+ description if it carries the breakdown) |
| 7 | `plugins/rawgentic/skills/run-feedback` | NEW symlink `../../../skills/run-feedback` |
| 8 | `scripts/sync_shared_blocks.py` | MANIFEST += run-feedback; run sync; verify `--check` |
| 9 | `tests/hooks/test_headless.py` | `EXPECTED_CONFIG_LOADING_COUNT` 7 → 8 |
| 10 | `tests/hooks/test_adversarial_review_registration.py` | version pin 3.25.0; "6 SDLC workflow skills" literals → 7; "All 7 config-driven" → 8 |
| 11 | `README.md` | line-3 breakdown 7+6+1+2; "provides 16 skills"; SDLC bullet (7 skills); skills table row; "All 8 config-driven"; evals "9/16" + `run-feedback` added to have-none backtick list; changelog entry |
| 12 | `docs/workflow-diagram.html` | `wf14` entry mirroring the wf1/wf3/wf5 skeletal SHAPE exactly: `skeletal:true` **plus a full `versions:{"3.25.0":{superseded:false, steps:[...]}}` block with real pending steps** (the WF14 step list, `pending:true`) + `order` append. "Skeletal" = pending-steps, NEVER empty/`steps:null` — the SPA renderer dereferences `versions[revs[0]].steps` unconditionally and an empty entry crashes the WF14 tab on click. Version-linkage test unaffected (reads first revs array = wf2's, verified tests/test_workflow_diagram.py:181). Follow the `rev-diagram` workspace skill recipe (provenance footer, snapshots) |
| 12b | `tests/test_workflow_diagram.py` | NEW guard: every entry in `DATA.order` has a `versions[revs[0]]` block whose `steps` is a non-empty array (catches the empty-skeletal crash class for wf14 and any future workflow) + WF14/"Run Feedback" presence check alongside the existing extensible-registry test |
| 13 | docs/reviews/peer-consult report (rubric) | committed as design evidence (AC5) |
| — | `claude_docs/prompts/wf-run-feedback.md` | workspace-side: replaced with pointer to the plugin skill (NOT in repo PR) |

Evals: one explicit statement — README eval coverage changes from **9/15 to 9/16**; NO new eval artifact is added; `run-feedback` is added to the README have-none backtick list (computed membership guard).

## Configuration changes

Workspace `.rawgentic_workspace.json` gains optional per-project `runFeedback: {"enabled": bool, "workflows": [...]}` — parsed by the EXISTING generic `load_adversarial_review_config(key="runFeedback")`; no schema/code change. Documented in SKILL.md embedding contract + README row.

## Error handling / failure modes

- Run-record store missing/invalid → degraded mode, loud header, claims bounded per rubric (never a silent pass).
- `latest` resolution, exact semantics: parse ONLY the last non-empty line of the store; if that line is malformed, enter degraded mode and quote the parse error — do NOT scan earlier lines for a fallback record (a silently-older record is worse than a loud absence). Before treating a parsed `latest` as confirmed, sanity-check its workflow/issue/date against session evidence (concurrent runs can make `latest` the wrong run); a failed sanity-check downgrades the record to `unverifiable` provenance.
- `dispatches[]` absent (today's reality) → "not captured at this version (#329/#330 open)" — a gap note, never a mismatch verdict.
- gh filing failure → report already written; log the failure in the report's routing section, continue (a filing error never voids the assessment).
- mempalace server down/absent → the mandatory `surface:` visible-skip line (AC4; see platform_apis — one contract).
- Label create race → check-then-create; treat already-exists as success.
- Cap enforcement: >3 defect candidates → merge related, file top 3 by severity, list the rest in the report as unfiled-with-reason (AC8's "explicitly dropped with a reason").

## Security implications

- Filed issues + published artifact are outward-facing: secrets by NAME only, no token values, no raw log dumps (rubric rule + drift guard).
- `gh issue create` on an explicit foreign repo from any session — write is to our own repo, intended; dup-check bounds spam; cap 3 bounds volume.
- No new hooks, no new subprocess surfaces in Python (skill is prose; all commands precedented).

## Platform / external dependencies

platform_apis:
- api: gh CLI (issue list/create, label list/create) on the GitHub repo 3D-Stories/rawgentic
  feasibility: verified via spike — `gh issue view 337 --repo 3D-Stories/rawgentic`, `gh label list --repo 3D-Stories/rawgentic`, and `gh api repos/3D-Stories/rawgentic/...` all executed live in THIS design session (2026-07-09, authenticated, scopes incl. repo per the API response headers); create-and-add label prior art at references/headless.md. Write permission not spike-proven (no test issue filed) — the routing step therefore prints the exact failed command + stderr on any gh failure, and a filing error never voids the assessment (existing error-handling rule)
  failure: fail-loud
- api: mempalace MCP tools (mempalace_search / memory save) on MEMORY_SERVER_URL=http://10.0.17.205:8420
  feasibility: verified via spike — mcp__mempalace__mempalace_search called live in THIS design session (2026-07-09, returned 8 results from http://10.0.17.205:8420) proves server reachability + tool loading; the WRITE path (memory save) is best-effort by design — the spike does not prove it, so the skill MUST verify each save by its returned id (or a read-back search) and print a save-failed line when verification fails
  failure: fail-silent
  surface: the report's routing section MUST print exactly one of: the saved memory's id/slug (verified write), "friction memory save FAILED — <error>" (attempted, unverified), or "mempalace unavailable — friction memory SKIPPED" (server absent). All three drift-guarded; the mandatory surface line IS the AC4 visible skip — `fail-silent` describes only the RAW API. Implementers ship the surface line, never the raw silent skip.
- api: Artifact tool (harness publish) on the Claude Code session
  feasibility: verified via spike — the Artifact tool is loaded and schema-resolved in THIS design session (2026-07-09); prior-art call-site at skills/create-issue/SKILL.md:240-243 (publish after render_artifact.py). Availability varies per session/harness, hence best-effort classification below
  failure: fail-silent
  surface: report prints the published URL or the REQUIRED failure line "artifact publish FAILED/unavailable — committed .html is source of truth" (drift-guarded; mirrors workspace mistake #18). The committed .md+.html pair is the required deliverable; publication is best-effort on top.

## Multi-PR assessment

~600–800 new prose lines + registration edits; one logical phase; single PR (precedent: every prior skill-add PR).

## Peer-consult provenance (Step 3 sub-step, blind both ways)

Cross-model consult (Codex via `adversarial_review_lib.py consult`, exit 0) returned after this draft was on disk. Adopted into the design (peer contributions):

1. **Expanded verdict vocabulary** — `match | mismatch | missing-in-record | missing-in-session | unverifiable | known-limitation` (superset of AC8's minimum three; absent data has different remediation paths). Table columns: `field | recorded_value | session_evidence | verdict | impact | routing`.
2. **Run-facts evidence ledger** — every gathered fact tagged `confirmed | inferred | absent | unverifiable` before scoring; gate verdicts must QUOTE the exact marker/evidence used (guards against prose-grep false negatives — `unverifiable` over guessing).
3. **Score anchors + evidence quotas** — each dimension defines 1/3/5 anchors as observable evidence patterns (not adjectives) plus minimum-evidence caps: e.g. telemetry dimension capped at 2 with no run-record; `unscored` allowed when a dimension cannot honestly be assessed.
4. **Degraded mode as first-class mode** — `record: absent` in the summary; telemetry claims limited to `unverifiable`/`missing-record`; the report LISTS claims intentionally not made.
5. **`latest` sanity check** — before treating the tail record as confirmed, cross-check its workflow/issue/date against session evidence (concurrent/multi-branch work can make `latest` the wrong run; embedded calls should prefer explicit `--record`).
6. **usage = `known-limitation`** unless values are internally impossible — never file a defect solely because whole-session attribution is broad. **`store-lag-known`** distinct from genuinely-missing record.
7. **Cap overflow routing** — findings beyond the 3-issue cap carry `routing: not-filed-cap` and are preserved in the artifact (the cap must not hide systemic failures).
8. **dispatches[] refinement** — absence verdicts: `missing-in-record` only when session evidence SHOWS dispatch activity; otherwise a version-gap note (#329/#330 open), and dispatch BEHAVIOR is scored from session evidence regardless.
9. **Rubric version stamp** — rubric.md carries a version; every report quotes it so assessments stay comparable.
10. **Dup-check keys** — normalized title keys (WF id + classification + failure signature) before label creation and filing; label create idempotent (already-exists = success).

Peer confirmed (independent agreement, no change): thin-orchestrator + rubric split; always-explicit-repo filing; report-only invariant; clean-run still emits marker + full summary block. Rubric-draft consult per AC5 runs in Step 8 against the actual rubric.md; report committed to docs/reviews/.
