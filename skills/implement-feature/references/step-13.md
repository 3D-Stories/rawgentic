## Step 13: CI Verification (Conditional)

### Instructions

**If `capabilities.has_ci == false`:** Log "No CI configured — skipping Gate 2" in session notes and proceed to Step 14.

**If `capabilities.ci_quarantined == true` (#137 — CI present but human-declared untrustworthy):** the suite is chronically red for reasons unrelated to any diff, so a red run here is noise, not a gate. Still **observe** the run, but record its outcome as a **visible non-gate** — never block, never claim green:
0. **Trust guard (a PR must not disable its own CI gate).** Quarantine only counts when it comes from the TRUSTED base config, not this branch's diff. Load the base config (`git show origin/${capabilities.default_branch}:<config-path>`) and compare via `capabilities_lib.ci_quarantine_change(base_config, head_config)`. If it returns a non-None reason (the branch INTRODUCED or ALTERED the quarantine), **the quarantine does NOT take effect for this run — CI GATES normally** (fall through to the active-CI path below), and surface the change for explicit owner approval. Only when the quarantine is unchanged from base do you proceed as a non-gate:
1. Trigger/observe the run the same way (`gh run list ... --json status,conclusion,databaseId`).
2. Record in session notes AND the Step 12 PR body, verbatim: `CI quarantined (<capabilities.ci_quarantine_reason>): run <status/conclusion>, not gating`. Include `since <capabilities.ci_quarantined_since>` when set.
3. Do NOT diagnose/fix/block on a red run, and do NOT report it as passed. Proceed to Step 14 regardless of conclusion. Quarantine is read from config only — WF2 never enters or lifts it (that is a human edit to `config.ci.status`).

**If `capabilities.has_ci == true` (and not quarantined):**

1. Monitor CI:
   ```bash
   gh run list --repo ${capabilities.repo} --branch <branch_name> --limit 1 --json status,conclusion,databaseId
   ```

1a. **CI structurally unavailable → visible non-gate (#232 AC3).** If, after waiting up to CI_MAX_WAIT_MINUTES, **no run has spawned** for this branch (`gh run list` returns empty) OR a run cannot execute (Actions disabled / minutes exhausted — the platform, not this diff), then "PR open with green CI" is structurally **unsatisfiable** — this is NOT a red run to diagnose and NOT an ERROR condition. Record a **visible non-gate**, exactly like the quarantine path: session notes AND the Step 12 PR body, verbatim: `CI unavailable (no run spawned | Actions unavailable): not gating`. Then proceed to Step 14/16 — never force the ERROR protocol and never claim green. This is the answer to the live-run dead-end where CI simply never ran. (Distinguish from item 4: item 4 is a run that STARTED but hasn't finished; this is a run that never started.)

2. If CI passes: proceed to Step 14.

3. If CI fails: diagnose with `gh run view <id> --log-failed` consumed as a projection
   (#314, see `### Delegated reads`): measure piped (`| wc -c`); over
   `WF2_READ_DELEGATE_BYTES_LOG` grep it to failing job/step + assertion/traceback first
   lines instead of reading the full log (a failing run with an empty grep ⇒ inline —
   projection validation). Fix, push, CI re-runs.

4. If CI times out (> CI_MAX_WAIT_MINUTES) on a run that DID start: ask user for explicit approval.

### Output
CI status, quarantine notice, or skip confirmation.

---

