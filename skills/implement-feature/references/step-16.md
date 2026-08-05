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

1. APPEND WF2 results to session notes.

1a. **Reconcile the campaign queue (#695).** Re-run the terminal-status write-back, whatever
   this run's outcome was:
   ```bash
   python3 hooks/launcher_lib.py record-child-outcome --issue <issue> \
     --status <merged|pr_open|deferred|abandoned> --project-root .
   ```
   Step 14 item 2b already ran this on the merge path; this is the **idempotent
   reconciliation** that catches every other case — a run interrupted between the merge and
   here, an unattended run whose PR is the terminal deliverable (`pr_open`), or a blocked child
   (`deferred`/`abandoned`). Recording a status the child already has is a no-op, so the
   repeat costs nothing.

   Capture the command's output into the run-record: on a fail-open result (no campaign names
   this issue) add its printed reason as a `follow_ups` entry. Fail-open that leaves no trace
   is how a real miss comes to look exactly like a deliberate no-op.

1b. **PR-terminal runs:** when the run had no merge authorization (e.g. an epic-run child
   or any unattended run), Steps 14 and 15 were skipped — the PR is the terminal
   deliverable. Record this for auditability by appending a session-notes marker:
   `### WF2 Step 14/15: SKIPPED (unattended — PR #N is terminal; merge/deploy deferred to human + CI)`
   and set the run-record `outcome.deploy` to `"not_applicable"` with a
   `follow_ups` note `"unattended: merge/deploy deferred to human + CI"`. The
   rendered summary then reflects deploy: not_applicable for the run.

2. **Assemble the run-record** from the workflow so far and write it to
   `/tmp/wf2-run-record-<issue>-<session-id>.json` (use the Write tool, or a `cat > … <<'JSON'`
   heredoc). The path is **session-unique by contract (#511)**: `<issue>` is this run's
   issue number and `<session-id>` is `$CLAUDE_CODE_SESSION_ID` (a bash block may write
   `"${CLAUDE_CODE_SESSION_ID}"` directly) — a fixed shared literal was clobbered live by
   concurrent sessions (sentinel epic #45 vs the #467 run, finding T-2); never substitute
   a shared path. The full schema, the field-presence rules, and the per-gate `status`
   conventions live in **`references/run-record.md`** — read it before assembling.
   Two fields are read from source-of-truth state, never from in-context memory (#512):
   `loop_backs.used` comes from `claude_docs/.wf2-state/<issue>/loopback_counters.json`
   (its `total`; the file survives sessions, so an in-context count is structurally
   wrong on any resumed/multi-session run — a missing file means zero), and each gate's
   `reviewer_kind` is re-derived at assembly time from the gate-defining mechanism per
   run-record.md's merged-gate precedence enumeration (#340) — the additive adversarial
   layer NEVER changes it; read the gate's own session-note markers, not memory.
   **Per-step timing (#506):** compute the run's timing object from the step-state
   history and embed its stdout verbatim as the record's `timing` key:
   ```bash
   python3 hooks/step_state.py timing --project <project> --issue <issue>
   ```
   (run from the workspace root, where `claude_docs/wal/history/` lives; a
   `"status": "absent"` result is embedded as-is or the key omitted — either is
   honest). Never hand-estimate durations into `timing` — the object comes only
   from the CLI over the persisted history (hand-reconstructed wall-clocks
   measured ~2× off, the #506 motivation).
   In short: every documented key must be **present** (a dropped field is a
   telemetry gap, not a `null`), counts are non-negative integers, `resolved` ≤
   `findings`, and `workflow` is `"implement-feature"`. Record
   `"architecture": "inline"` (optional-legacy since the executor retreat;
   `executor`/`legacy` remain valid on historical records only). **Canonical names (#116):** use
   the exact `gates[].name` per step from `work_summary.CANONICAL_GATE_NAMES`
   (`canonical_gate_name("implement-feature", step)`) and record `security_scan.skipped[]` as scanner **kinds**
   from `work_summary.SCANNER_KINDS` (`secrets`/`sca`/`sast`/`iac`), never free text — the
   summarize CLI validates this fail-closed (`strict=True`), so a non-canonical skip
   fails the persist.

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

2d. **dispatches[] (legacy key):** the retreat removed the dispatch machinery, so a current
   run emits no `DISPATCH` lines — OMIT the `dispatches` key entirely (never an empty
   array). The key remains valid on historical records (`references/run-record.md`).

2e. **Gate severity counts (#473):** on each reviewed gate, carry the deduplicated
   `findings_critical` / `findings_high` counts computed at that gate's close
   (optional-additive in `validate_record`, absent on legacy records). The
   seat-Observation sidecar harvest retired with the executor.

3a. **The record may ALREADY be persisted — render only, never summarize twice (#888).** On the
   merge-grant path, Step 14 assembled, persisted, committed and CI-re-verified the record before
   merging (§14, "Merge-grant path"). That run reaches here with its record already in the store,
   so it renders from it with `--no-persist` rather than calling `summarize` a second time:
   ```bash
   python3 hooks/work_summary.py summarize \
     --record-file /tmp/wf2-run-record-<issue>-<session-id>.json \
     --project-root <activeProject.path> --no-persist
   ```
   Check with `python3 hooks/work_summary.py find --issue <issue>` (rc 0 = already there) when
   unsure — a resumed session cannot tell from context alone. Since #888 `persist_record` is
   idempotent, so a second summarize no-ops with a stderr notice and rc 0 instead of duplicating
   the line; that guard is the BACKSTOP, not the plan. Every other run (no merge grant, PR
   terminal) persists here as item 3 describes.

3. **Render + persist.** Carry `activeProject.path` in as a literal (shell vars
   do not persist across Bash tool calls):
   ```bash
   python3 hooks/work_summary.py summarize \
     --record-file /tmp/wf2-run-record-<issue>-<session-id>.json \
     --project-root <activeProject.path> \
     --loopback-counters claude_docs/.wf2-state/<issue>/loopback_counters.json
   rc=$?
   ```
   The `--loopback-counters` flag (#512) makes the tool cross-check the record's
   `loop_backs.used` against the persisted counters file and fail rc 1 on
   divergence — a missing file means zero loop-backs were consumed and still
   validates. Resolve the path relative to the workspace root (where
   `claude_docs/` lives), the same place the counters were written.
   The tool's stdout **is** the completion summary — present it to the user as-is
   (do not re-type it). It also appends the record to
   `<activeProject.path>/docs/measurements/run_records.jsonl` (override with
   `--store` or `$RAWGENTIC_RUN_RECORD_STORE`).

4. **Handle the exit code:**
   - `rc == 0`: record valid and persisted. Done.
   - `rc == 1`: the summary still rendered (the user keeps Step 16 output) but the
     record FAILED validation and was **not** persisted — a telemetry gap. The
     stderr lists exactly which fields are wrong; fix `/tmp/wf2-run-record-<issue>-<session-id>.json`
     and re-run so the substrate stays complete. If it genuinely can't be fixed,
     record the gap in session notes rather than ignoring it.
   - `rc == 2`: usage error / unreadable record file — fix the invocation.

5. **Embedded WF14 self-assessment (opt-in, #338).** After the exit-code handling
   above, gate on the runFeedback key via the generic is-enabled parser:
   ```bash
   python3 hooks/adversarial_review_lib.py is-enabled \
     --workspace .rawgentic_workspace.json --project <name> --skill implement-feature --key runFeedback
   ```
   Non-zero exit (key absent, disabled, or workflow not listed) → skip SILENTLY —
   the peerConsult opt-in pattern, no marker noise. Exit 0 → invoke the
   `/rawgentic:run-feedback` core path (the #337 embed contract — zero interactive
   dependency). When enabled, invoke the run-feedback core path non-interactively
   with explicit `--record /tmp/wf2-run-record-<issue>-<session-id>.json --wf 2 --file-issues --session-notes
   <notes-path>`; an assessment failure never blocks workflow completion — log and
   continue. Run it regardless of item 4's rc — the record FILE exists on rc 1 too,
   and WF14 routes a schema-invalid record to degraded mode (an assessed degraded
   run beats an unassessed one); on rc 2 WF14's own `--record` fail-closed path
   yields a degraded/unscored assessment. The assessment is report-only for the
   plugin SOURCE (it never edits skills/hooks/docs mid-assessment) and
   PR-terminal-safe (it never touches the just-created PR), so it runs in
   unattended runs too — but its outward writes are WF14's own Step 4 actions and
   run autonomously there: the report pair + session-note marker (the only FILE
   writes), up to 3 filed issues against `3D-Stories/rawgentic`, and one mempalace
   memory.

Log a marker in `claude_docs/session_notes.md`:
`### WF2 Step 16: Completion summary + run-record — DONE (#<issue>: persisted: yes/no)`

Do NOT suggest auto-transitioning to WF1 or restarting WF2.

### Output
Standardized completion summary (rendered by `work_summary.py`) + a persisted
run-record. WF2 terminates.
