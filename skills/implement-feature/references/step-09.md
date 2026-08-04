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
compare against `LANE_MAX_IMPL_FILES`. Pass the SAME `impl_extensions` the entry decision used
(`lane_impl_extensions(cfg)`, #143) so a markdown-is-product repo reconciles on the same basis:
```bash
python3 -c "import sys,json,subprocess; sys.path.insert(0,'hooks'); from plan_lib import count_impl_files, lane_impl_extensions, LANE_MAX_IMPL_FILES; exts=lane_impl_extensions(json.load(open('<activeProject.path>/.rawgentic.json'))); paths=subprocess.run(['git','diff','--name-only','origin/<default>..HEAD'],capture_output=True,text=True).stdout.split(); n=count_impl_files(paths, impl_extensions=exts); print(n, n > LANE_MAX_IMPL_FILES)"
```
If the real count materially exceeds the run's **comparison figure** — the **sanctioned
elected count** logged by a #225 secondary-signal/override lane election when one exists,
else `LANE_MAX_IMPL_FILES` — log a **`lane-widened`** note to
session notes AND set a run-record note (the design panel was skipped on a change that turned out
larger than estimated, or beyond the sanctioned elected count) — do **NOT** retroactively
fail: the gates that DID run (Step 11, Step 11.5, Step 8a) are still valid and load-bearing.

**Part A: Drift check (apply the quality-bar rubric, `references/quality-bar.md`):**
- Plan-implementation alignment: does every task have a corresponding implementation?
- Design-implementation alignment: does implementation follow the critiqued design?
- Acceptance criteria verification: for each criterion, identify the test/verification that covers it
- Documentation check: are required docs updated?
- **P15 review coverage:** run `plan_lib.assert_review_coverage` with `log_path = claude_docs/.wf2-state/<issue>/review_log.jsonl` — the JSONL Step 8a item 8 appends, NEVER the session notes (#880: the markdown as log_path made the gate unpassable behind an 8,878-line stderr flood, and a missing log passed vacuously). Verdicts `applied` or `deferred` count; `REVIEW_DISPATCH_FAILED` does not — an uncovered task re-presents to the wave before this gate passes. A `ReviewLogError` (missing or wrong file) is a structural STOP; echo its missing-log diagnostic into session notes.
- **Implausibility check:** if the plan tagged ZERO tasks high-risk and the diff touches paths matching `plan_lib.DEFAULT_HIGH_RISK_PATH_PATTERNS`, fail Part A with an explicit message: features touching security-relevant paths must have at least one high-risk task.

**Part B: Evidence enforcement:**

If `capabilities.has_tests`:
- Run full test suite using `capabilities.test_commands` — the second of the exactly-two full-suite runs (per `<test-run-discipline>`, SKILL.md) — consume it as a projection
  (#314): the runner's final-summary tail + delta vs the recorded baseline; exit code is
  the verdict; never a full log dump into context (empty projection on failure ⇒ inline)
- Verify new tests actually test new behavior
- Confirm no regressions

If NOT `capabilities.has_tests`:
- Re-run all verification commands from the plan
- Confirm all produce expected results
- APPEND verification evidence to session notes

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

