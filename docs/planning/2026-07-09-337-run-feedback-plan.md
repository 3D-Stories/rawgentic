# Implementation Plan — #337 WF14 run-feedback (design Rev 4)

Branch: `feature/337-run-feedback-skill` · Version: 3.24.26 → 3.25.0 · Baseline: 2418 passed, 1 skipped

### Task 1: Drift-guard tests (RED) + rubric.md + SKILL.md
- riskLevel: standard
- files: tests/test_run_feedback_clarity.py, skills/run-feedback/SKILL.md, skills/run-feedback/references/rubric.md
- RED: write tests/test_run_feedback_clarity.py pinning the 11 canonical sentences (report-only invariant incl. DONE-marker scope; always-explicit-repo; cap 3 + not-filed-cap; degraded-mode loud + unscored rule; 6-way verdict vocabulary (in BOTH SKILL.md and rubric.md); known-weak-spots named (usage attribution, reviewer_kind, gate-count honesty, store-lag); dispatches[] consume-when-present; mempalace tri-state surface; artifact-publish failure line; marker-acceptance boundaries; rubric version stamp) — confirm they FAIL (files absent).
- GREEN: write skills/run-feedback/SKILL.md (frontmatter name rawgentic:run-feedback, WHEN-description, argument-hint; <config-loading> placeholder tag to be synced in Task 3; steps 1-6 per design Rev 4; embedding contract; report-only invariant) and skills/run-feedback/references/rubric.md (version stamp v1; evidence ledger tags; six dimensions with 1/3/5 anchors + evidence-quota caps; the 5-way FINDING-CLASSIFICATION taxonomy (plugin-defect/friction/environment/orchestrator-error/working-as-designed) — a DIFFERENT axis from the 6-way telemetry VERDICT vocabulary, both present, per design lines 22-23; telemetry-audit section w/ 6-way vocabulary + table schema + known-weak-spot checks; degraded-mode rules; fixed output block; secrets-by-name rule).
- Verification: pytest tests/test_run_feedback_clarity.py -q exit 0.
- Commit: `feat(run-feedback): WF14 skill + rubric + drift guards (#337)`

### Task 2: Peer-consult on rubric draft (AC5) — report committed
- riskLevel: standard
- files: docs/reviews/peer-run-feedback-rubric-2026-07-09.md
- Run `adversarial_review_lib.py consult` against the ACTUAL rubric.md draft (feasibility: this exact command ran successfully in this session — design-phase consult, exit 0; on failure the library writes an explicit empty-proposal marker and the task proceeds with the draft alone, consult noted as unavailable in the report); fold accepted improvements into rubric.md; commit the consult report as design evidence.
- Verification: report file exists; rubric drift guards still green after folds.
- Commit: `docs(run-feedback): rubric peer-consult report + folds (#337)`

### Task 3: Registration surfaces + sync + counts
- riskLevel: standard
- files: .claude-plugin/marketplace.json, .claude-plugin/plugin.json, plugins/rawgentic/.codex-plugin/plugin.json, plugins/rawgentic/skills/run-feedback, scripts/sync_shared_blocks.py, tests/hooks/test_headless.py, tests/hooks/test_adversarial_review_registration.py
- Whitelist entry before ./skills/scan; symlink; version 3.25.0 ×3 (test pin included); descriptions "6 SDLC"→"7 SDLC" (plugin + marketplace); MANIFEST += run-feedback + run sync + --check; EXPECTED_CONFIG_LOADING_COUNT 7→8; "All 7 config-driven"→8 pin.
- Verification: pytest tests/test_v3_removals.py tests/test_codex_plugin_packaging.py tests/hooks/test_adversarial_review_registration.py tests/hooks/test_headless.py tests/test_shared_block_drift.py -q exit 0.
- Commit: `feat(run-feedback): registration surfaces + count guards (#337)`

### Task 4: README + docs
- riskLevel: standard
- files: README.md, docs/skill-development.md
- README: line-3 breakdown 7+6+1+2; "provides 16 skills"; SDLC bullet 7; skills table row; "All 8 config-driven"; evals 9/16 + run-feedback in have-none list; changelog v3.25.0 entry (bold lead (#337), prose, diagram decision "WF14 added, diagram REV 3.25.0", Suite old→new tail — final counts at Step 9). docs/skill-development.md untouched unless the adding-a-skill process itself changed (it did not — no edit).
- Verification: pytest tests/hooks/test_adversarial_review_registration.py -q exit 0 (covers the computed README count strings, evals fraction + have-none membership, description breakdown sum, version pin) AND pytest tests/ -q -k readme_changelog exit 0 (splice guard) AND grep -q "### v3.25.0" README.md.
- Commit: `docs(run-feedback): README counts + changelog + skill docs (#337)`

### Task 5: Workflow diagram wf14 entry + shape guard test
- riskLevel: standard
- files: docs/workflow-diagram.html, tests/test_workflow_diagram.py
- RED: add the DATA-shape guard test (every DATA.order key has versions[revs[0]].steps non-empty; WF14/"Run Feedback" present) — fails (no wf14).
- GREEN: wf14 entry mirroring wf1/wf3/wf5 skeletal shape (versions:{"3.25.0":{superseded:false,steps:[...pending:true...]}}; order append; provenance footer per rev-diagram recipe). Snapshots regenerated if the rev-diagram recipe requires.
- Verification: pytest tests/test_workflow_diagram.py -q exit 0.
- Commit: `feat(diagram): WF14 run-feedback REV 3.25.0 + DATA-shape guard (#337)`

### Task 6: Workspace-side pointer + follow-up issue + design docs commit
- riskLevel: standard
- files: docs/planning/2026-07-09-337-run-feedback-design.md, docs/planning/2026-07-09-337-run-feedback-design.html, docs/planning/2026-07-09-337-run-feedback-plan.md
- Render design .html via render_artifact.py; commit design + plan docs; file the named follow-up issue (WF2 Step 16 / WF3 Step 14 runFeedback invocation wiring — AC6) via `gh issue create --repo 3D-Stories/rawgentic` (gh read-path spike-proven this session; if create fails, commit a local draft `docs/planning/2026-07-09-337-followup-draft.md` with title+body and record the failure — AC6's "named follow-up" is then the draft, filed manually); workspace claude_docs/prompts/wf-run-feedback.md → pointer (workspace-side, NOT staged in this repo's PR).
- Verification: design .html exists; follow-up issue URL captured (or draft committed + failure recorded); workspace pointer file exists outside the PR and names skills/run-feedback as its target.
- Commit: `docs(planning): #337 design + plan + peer-consult evidence (#337)`

No migrations. No multi-PR (single logical phase). No parallel groups (tasks 3-5 all touch registration-adjacent counted surfaces; serial is safest and cheap).

**Intermediate-red note (expected, by design):** the computed cross-surface count guards (whitelist==disk in test_v3_removals; README "provides N skills"/evals/breakdown in test_adversarial_review_registration) are RED from Task 1 (skill dir exists on disk) until Task 4 (README counts land). Per-task verification is therefore the SCOPED guard files listed on each task; the WHOLE suite is green from Task 4 onward and gated at Step 9. This is the add-skill checklist's documented shape (guards verified at the end), not drift.
