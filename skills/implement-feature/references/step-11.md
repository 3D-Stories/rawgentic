## Step 11: Pre-PR Code Review

### Instructions

**Runs in PARALLEL with Step 10** (this is the foreground task).

1. **Generate diff:**
   ```bash
   git diff ${capabilities.default_branch}..HEAD
   ```

   **P15 pre-flight (when Step 8a fired any reviews):** collect the covered SHAs from the Step 8a session-note coverage markers and read deferrals via `plan_lib.get_deferred_findings(<deferrals_path>)`. Build:
   - `reviewed_shas` — SHAs that already went through Step 8a (from the markers)
   - `deferred_findings` — the verbatim list of deferred-High findings to re-present

   Pass both to each reviewer as context:
   - "Already reviewed at task boundary: <SHA list>. Focus on **cross-cutting concerns**; re-litigate individual files only on **material** findings (the bar is 'this is materially worse than what Step 8a saw,' not 'I might find a smaller issue')."
   - "Previously flagged & deferred: <verbatim finding list>. **RE-EVALUATE each.** A deferred High must end the review as either `applied` or with an independent concurrence from a reviewer slot different from the originator." Record each resolution via `plan_lib.resolve_deferral(<deferrals_path>, <finding_id>, status='applied'` / `add_concurrence=<other_slot>` / `user_ack=True)` — do not edit the deferrals JSON by hand.

1a. **Adversarial diff review sub-step (opt-in, cross-model — runs concurrently with the 2 review passes; issue #131).**
   Mirrors the Step 4 item 7 join-barrier pattern, but over the *diff* instead of the design doc. Report-only; additive to the 2-pass review, never a replacement.

   - **Stale sweep (first thing):** delete any leftover `.rawgentic-diff-review-*` temp/result files under the project root before doing anything else. This is crash recovery — a finally-style cleanup-on-exit cannot cover a SIGKILL, so a prior run's stale temp files may still be on disk.
   - **Gate:** enablement via the SAME probe as Step 4 item 7 —
     ```bash
     python3 hooks/adversarial_review_lib.py is-enabled \
       --workspace .rawgentic_workspace.json --project <name> --skill implement-feature
     ```
     exit `0` = enabled, non-zero = skip. Compute `changed_paths` from `git diff --name-only origin/${capabilities.default_branch}..HEAD` — the SAME base ref as the patch below. **If that git command exits non-zero:** log the marker `failed (base ref unavailable: <reason>)`, skip dispatch, and continue (the `failed` marker satisfies the completion gate). Set `has_high_risk_task` = any plan task tagged `riskLevel: high`. **Resolve the diff-review mode BEFORE deciding (#879)** — it is an INPUT to the decision, so it resolves here rather than at dispatch where the backend does:
     ```bash
     python3 hooks/adversarial_review_lib.py diff-review-mode \
       --workspace .rawgentic_workspace.json --project <name> --key adversarialReview
     ```
     exit `0` is the ONLY success path → stdout is `auto` (the default, and what an absent field resolves to) or `always`; **exit 2 (invalid config value) → abort this diff-review layer loudly (marker `failed (invalid diffReviewMode config)`), never default to `auto`** — a silent default would restore the very heuristic the config opted out of; **any OTHER non-zero exit** (an unreadable workspace file, a loader crash) → marker `failed (diffReviewMode resolution error: <reason>)` and abort the layer WITHOUT calling the gate — an undefined exit must never reach the decision as an assumed `auto`. Branch on the exit code, never on an empty stdout capture. **When the resolved mode is `always` and the diff cannot be established at all** (the base-ref failure above), the mandated every-diff review did NOT run: record it in the Step 12 PR body as well as the marker — a `failed` marker alone satisfies the completion gate, so without the PR-body line the unmet promise would be visible only in session notes. Decide via `plan_lib.should_run_diff_review(enabled, changed_paths, has_high_risk_task, mode=<resolved mode>)` (pure, tested; it raises on str/None `changed_paths` and on an unrecognized `mode`, so pass a real list and a resolved mode). Under `always` every NON-EMPTY diff elects the review — an empty diff still skips, and the returned reason names the mode. It returns `(False, <reason>)` → log marker `skipped (<reason>)` and stop; `(True, <reason>)` → dispatch.
   - **Dispatch (concurrent with the 2 review passes):** **resolve the review backend first (#403):** `python3 hooks/adversarial_review_lib.py backend --workspace .rawgentic_workspace.json --project <name> --key adversarialReview` — exit 0 → stdout is the backend; **exit 2 (invalid config value) → abort this diff-review layer loudly (marker `failed (invalid backend config)`), never default to gpt.** Then, from a read-only harness subagent, run the runner (TOKENLESS — this layer is report-only/diagnostic by design and MUST NOT drive a loop-back):
     ```bash
     python3 hooks/review_runner.py review-code --base origin/<default> --brief <brief.md> \
       --author-model <your model id> --reviewer <default per the contract> \
       [--backend <resolved backend>] --out .rawgentic-diff-review-<issue>-<token>.json --project-root .
     ```
     The runner composes the diff itself from `--base` and REFUSES oversize input instead of truncating (#834) — on an oversize refusal, either split the change or raise `RAWGENTIC_ADV_REVIEW_MAX_BYTES` deliberately and say so in the PR body; never trim the diff by hand to sneak it under. The brief names the changed paths **high-risk-first** (partition via `high = [p for p in changed_paths if plan_lib.any_high_risk_path([p])]`) plus the plan's high-risk tasks. Under `both`, dispatch two runner invocations (one per backend, separate `--out` files). On a pass-N dispatch, apply the Step 4 item 7 disposition-ledger fold (#393) at the join — fold, backstop, gate-close persistence.
   - **Join (before item 3's confidence filter):**
     - Runner exit `2`/`3`/`4` (after the contract's one retry) → marker `failed (<reason>)`, loud session-note log, continue with the same-model findings — **never** treat a failed review as passed (and, in an unattended run, post a STATUS comment noting the diff review was skipped, mirroring Step 4).
     - **Under `both`, one backend failed and one succeeded:** consume the successful result, log a loud warning naming the failed backend. Success-with-warning, never `failed`.
     - Exit `0` but the `--out` file is missing / empty / invalid JSON / stale (`head_sha` no longer matches the current HEAD) → treat that backend as failed (`failed (<reason>)` when none is left), never `no_findings` — the vacuous-result gate from `<model-routing-resolve>`.
     - Success → under `both`, read BOTH results and merge deterministically: provenance from file identity; dedupe key (evidence, location, category); on collision keep the higher severity, tie → higher confidence, tie → the gpt record; stable sort (severity rank, backend, original order). Findings arrive with a numeric `confidence` plus a `confidence_source` provenance flag — since #902 the runner enforces the numeric schema and applies the one word→float map (`ADV_CONFIDENCE_TO_FLOAT`, in `adversarial_review_lib`) itself as the flagged fallback; verify `confidence_source` and log the mapped count (a `confidence_mapped: true` result marks a legacy-word round — note it, never a blocker). Tag each `source: adversarial` and **merge** them into the finding list BEFORE item 3 so the severity-banded filter processes them identically. The single ambiguity breaker at item 6 runs **once** over the merged list; the design-flaw loop-back at item 7 stays the single `review` source. Marker `findings_present <N>` or `no_findings`; when any finding reports a leaked secret, append `; secrets detected: <categories>` to the marker (and to the unattended STATUS comment).
   - **Cleanup (finally-style):** delete the `--out` result file(s) on every handled exit path after the join. The startup stale sweep covers unhandled termination. **Staging backstop:** the temp files land under the *target* project's root (which is usually NOT this plugin repo), so the primary protection is the finally-cleanup + startup sweep, plus the explicit "stage ONLY this task's files, never `git add -A`" rule. As belt-and-suspenders, on first use append the `.rawgentic-diff-review-*` glob to the target repo's `.git/info/exclude` (local, untracked — does not dirty the target's committed `.gitignore`); the globs added to this plugin repo's own `.gitignore` only protect self-dogfooding runs.
   - **Marker (log exactly one per run):**
     `### WF2 Step 11 — Adversarial Diff Review: #<issue> findings_present <N>|no_findings|failed (<reason>)|skipped (<reason>) — <report path if any>`

<!-- model-routing: role=review -->
Run the review per the `<model-routing-resolve>` contract: the cross-model pass dispatches
`python3 hooks/review_runner.py review-code --base origin/<default> --brief <brief.md> --author-model <your model id> --reviewer <default per the contract> [--reopen-token <token.json>] --out <result.json> --project-root .`
from a read-only harness subagent, IN PARALLEL with your own INLINE self-review of the same diff (two independent passes, never merged). **The PR diff is a REQUIRED input: the runner composes it itself from `--base` — the artifact-delivery guarantee that `--requires-context` used to provide (#826) is now structural (a route that cannot carry the bytes cannot be called), so a review of nothing can never return a verdict this gate would read as a pass.** Mint the reopen token FIRST: `python3 hooks/plan_lib.py review-reopen --state-file claude_docs/.wf2-state/<issue>/loopback_counters.json --source review --out <token.json> --project-root .` — the mint debits the loop-back budget; on exhaustion (exit 3) dispatch TOKENLESS and note the round is diagnostic (a fundamental-flaw finding then escalates instead of looping — item 7). Per `<review-lens-routing>` (SKILL.md): Reviewer 1 (the inline self-review) → `mechanical` + `bug_logic`; Reviewer 2 (the runner pass) → `architecture` + `security` (never the one dropped). Every reviewer brief MUST restate the read-only execution clause (#510): Bash is for read-heavy inspection only — never execute the target project's entry-point scripts, deploy paths, or anything that mutates state or sends outward; the only sanctioned executions are the verification commands this brief names (from the project's `.rawgentic.json` testing config); an entry script invoked in an unexpected form may fall through to a live path — do not experiment with invocation forms; when a command's read-only-ness is uncertain, don't run it — report the uncertainty as part of the review.

2. **Run the 2-agent parallel review** (#492 trimmed 3→2 — the mechanical and bug/logic briefs fold into the inline self-review; the two "agents" are the inline pass and the runner pass). The runner owns transport retries (#857) — never wrap it in your own retry loop. **Dead-return detection:** a reviewer return that is vacuous (no findings AND no substantive content) is a DEAD dispatch, not a clean pass — relaunch that pass once; on a second death treat that slot as a dispatch failure (retry-once-then-REVIEW_DISPATCH_FAILED per Step 8a item 7's pattern) rather than counting it as a clean review.

   While the passes run, pipeline per `<review-pipelining>` (SKILL.md): draft the PR body and the version/changelog edits (non-committing); the confidence filter (item 3), fixes, and the exit gate still wait for the wave.

   **Reviewer 1: Mechanical + Bug & Logic** (the old Agents 1+2, merged by #492)
   - Code style rules from project conventions and config.formatting; naming; import ordering
   - No hardcoded credentials or secrets
   - Logic errors, edge cases, race conditions
   - Silent failures in catch blocks; null/undefined handling
   - Off-by-one errors, boundary conditions

   **Reviewer 2: Architecture, History & Security** (the strong pass — the security lens is never the one dropped, #492)
   - Does this change break patterns established by prior commits?
   - Are there related files that should also change?
   - Are there security implications? (this lens caught the ReDoS + FIFO-DoS on #466)
   - Is the change backward-compatible?

3. **Filter by confidence:** Apply the severity-banded thresholds from `SEVERITY_BANDED_CONFIDENCE` (values in `<constants>`; canonical in `plan_lib.SEVERITY_BANDED_CONFIDENCE`). The flat 0.80 in `REVIEW_CONFIDENCE_THRESHOLD` is a legacy fallback; the banded values are authoritative. Log dropped-finding counts.

4. **Severity-based fix workflow:**
   - Critical/High: fix before PR
   - Medium/Low: advisory (fix if easy, otherwise note)

5. **Evaluate each finding before fixing:** verify it's real, check YAGNI, push back on unnecessary changes.

6. Apply ambiguity circuit breaker.

7. **Design flaw detection:** a successful pre-dispatch mint (`--source review`) authorizes ONE gate-wide fix round for a fundamental flaw found by EITHER pass — inline or runner (the debit already happened at mint; never consume a second time). Consult the result's `diagnostic` field only when validating the runner receipt. On a refused mint (budget exhausted): NEITHER pass may open the round — the disposition step MUST refuse and escalate. A loop-back fires **only** for findings from the 2-pass review itself; adversarial-sourced findings (merged in by sub-step 1a with `source: adversarial`, always diagnostic) are report-only and MUST NOT drive a loop-back here — they are advisory input to the fix workflow, not a loop-back trigger.

8. **Deferred-resolution exit gate (P15):** before declaring Step 11 complete, call `plan_lib.assert_no_unresolved_high_deferrals(<deferrals_path>)`. If any deferred Critical/High remains unresolved (not `applied` and lacking independent concurrence from a different reviewer slot), Step 11 cannot complete. A finding with `defer_count >= 2` additionally requires `user_ack: true`.

9. **Gate-close persistence:** at this gate's close, persist each Critical/High finding's terminal disposition via `plan_lib.append_disposition` (Step 4's gate-close persistence sentence is canonical; deferrals stay in `deferrals.json`).

### Output
Code review result with filtered findings and fixes applied.

### Failure Modes
- Fundamental design flaw -> loop back to Step 3 if budget allows; if budget exhausted:
- Excessive noise (>20 Low findings) -> filter at confidence >= 0.80

---

