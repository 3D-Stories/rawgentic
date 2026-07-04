# Design: autonomous multi-issue driver (#134)

Date: 2026-07-04 · Issue #134 · Complexity: L program / M design · **design-only** (this issue is the design gate, not the build)

## Problem

WF2 is per-issue by contract: `<termination-rule>` ends the workflow at Step 16 and forbids auto-continuation. Running an autonomous backlog (N issues, push/PR/merge each) therefore means re-invoking the full 16-step skill N times with **no shared state**. This very campaign (#131→#132→#135→#133→#140→#138…, six issues merged) hand-rolled every missing piece:

| Hand-rolled this campaign | Where it lived | Failure risk when improvised |
|---|---|---|
| Ordered queue with per-issue status | an impact-order list in the handoff | drift between "what's next" and reality |
| Rollback anchor per issue | the base SHA captured at branch creation | none captured → can't cleanly revert a bad merge |
| Alignment ledger (status changed mid-loop) | `claude_docs/session_notes/campaign-131-140.handoff.md` | lost across compaction if not written |
| Inter-issue policy (review budget, never-Haiku) | operator memory + standing rules | silently forgotten mid-run |
| DEFER (park an issue, keep going) | *never exercised* — no convention existed | an owner-reserved surface would have stalled the whole loop |

Two real bugs this campaign traces directly to the missing structure: (a) #138 was committed to local `main` because no driver enforced "branch before first commit"; (b) #140 fixed a Step-7 base bug that only bites in a *multi-issue* loop (branching off a stale sibling). Both are exactly the failure modes a driver exists to prevent.

## Decision (AC2): a documented orchestration PATTERN + a small committed state file — NOT a new skill

**Recommendation: documented pattern, not a driver skill.** Rationale, leading with why the alternative loses:

- **A driver skill adds ceremony where the agent is already competent.** The *looping* itself — "run WF2, on success advance, on DEFER park, repeat" — is control flow an orchestrator executes reliably without a skill (this campaign proved it: six issues, zero loop-control errors). A skill wrapping that loop would be mostly prose restating WF2's own contract.
- **The value is in the CONVENTIONS, which are data + docs, not executable logic:** the queue schema, the DEFER taxonomy, the rollback-anchor protocol, and the resumption contract. Those are a documented pattern + one JSON state file, and (optionally) a *thin pure validator* — not a 16-step skill.
- **A skill would be tempted to re-implement WF2's gates** (or worse, weaken them to "go faster across the backlog"). Keeping the driver as a pattern that invokes `/rawgentic:implement-feature` fresh per issue makes it structurally impossible to weaken WF2 — AC3.

**What ships (in the follow-up build issue, not here):**
1. `docs/multi-issue-driver.md` — the pattern: the loop, the policies, the resumption contract.
2. A committed queue state file schema, `claude_docs/.driver-state/<campaign>.json` (per-campaign, so concurrent campaigns don't collide — mirrors the per-branch review-state pointer).
3. OPTIONAL thin `hooks/driver_lib.py` — pure functions `validate_queue(state)`, `next_issue(state)`, `record_outcome(state, issue, outcome)`, `defer_issue(state, issue, reason)` — added ONLY if the state transitions prove error-prone in practice. Default: the pattern doc + hand-maintained JSON is enough (it was this campaign). File as a *second* follow-up, gated on evidence.

## Queue schema (AC1)

`claude_docs/.driver-state/<campaign>.json`:
```json
{
  "schema_version": 1,
  "campaign": "issues-131-140",
  "policy": {"order": "impact", "deploy": "per-issue|batch|none",
             "has_deploy": false, "smoke_gate": null,
             "review_budget": 3, "never_haiku": true},
  "base_default_branch_sha": "<origin/default HEAD when the campaign started>",
  "issues": [
    {"number": 133, "status": "merged", "pr": 145, "merge_sha": "ab650ac",
     "rollback_anchor": "5591174", "deferred_reason": null,
     "deferred_branch": null, "branch_preservation": null},
    {"number": 137, "status": "queued|in_progress|pr_open|merged|deferred|abandoned",
     "pr": null, "merge_sha": null, "rollback_anchor": null,
     "deferred_reason": null, "deferred_branch": null, "branch_preservation": null}
  ]
}
```
**Status transitions [Codex F1 — `pr_open` is a first-class state so headless is representable]:**
```
queued → in_progress → { pr_open | merged | deferred | abandoned }
pr_open → { merged | deferred | abandoned }     # headless stops at pr_open; a human/merge-driver advances it
```
Only one issue `in_progress`/`pr_open` at a time (serial; parallel is out of scope — needs worktrees, #136/#85). `pr_open` is the terminal state for a headless driver (PR-terminal); a non-headless driver passes through it to `merged`.

**`policy.has_deploy` / `policy.smoke_gate` [Codex F4]:** copied from `capabilities` at campaign start into the state file so the driver reads deploy-availability + the required smoke command from committed state, not ambient config. `deploy: "per-issue"` with `has_deploy: false` is a config error the driver rejects at start; `smoke_gate: null` with `deploy != none` means "deploy but no automated smoke" (log the gap, don't silently pass).

## DEFER taxonomy (AC1)

An issue hits a wall mid-build → park it with a typed reason and **continue the loop** (never stall the backlog):

| DEFER type | Trigger | Example this program | Loop action |
|---|---|---|---|
| `owner-decision` | needs a human product/risk call | AskUserQuestion with no durable answer | park, surface in ledger, next issue |
| `owner-reserved` | touches a surface the owner reserved | a run gated behind owner approval | park, note the gate, next issue |
| `cross-repo` | change spans another repo | a fix needing an upstream PR first | park with the blocking dependency |
| `budget` | token/time budget for the campaign exhausted | — | park remaining queued issues, stop cleanly |

**Branch preservation on DEFER [Codex F2 — deterministic, persisted]:** the outcome is recorded in `branch_preservation` + `deferred_branch`, decided by this rule (no ambiguity for resumption):
- **`pushed`** — the branch has ≥1 commit AND the deferral may be resumed later (`owner-decision`, `owner-reserved`, `cross-repo`): push it, set `deferred_branch: <name>`, `branch_preservation: "pushed"`. Resumption knows the work exists.
- **`discarded`** — the branch has commits but the deferral abandons the approach: `git checkout <default> && git branch -D`, set `branch_preservation: "discarded"`, `deferred_branch: null`. Resumption must NOT expect the branch.
- **`none`** — no commits were made before the wall (deferred at design/plan): nothing to preserve, `branch_preservation: "none"`.

`budget` deferrals of not-yet-started issues stay `queued` (no branch, not `deferred`). The ledger records type + reason + these fields so resumption never assumes a branch that was discarded or loses work that was pushed.

## Rollback-anchor protocol (AC1)

Before each issue's branch is created, capture the current `origin/<default>` SHA as that issue's `rollback_anchor` (this campaign captured it implicitly as the branch base — the driver makes it explicit and persisted). On a bad merge/deploy discovered *after* merge: `git revert <merge_sha>` (preferred, preserves history) or reset a not-yet-pushed main to the anchor. The anchor also validates the next issue branches from a *fresh* base (the #140 fix) — driver asserts `new base == current origin/<default>`, not a stale sibling.

## Deploy policy options (AC1)

- **`per-issue`** (deploy-after-each): each merged issue deploys before the next starts. Safest for catching a regression early; slowest. Requires `has_deploy` + a smoke gate per issue.
- **`batch`**: merge all, deploy once at the end. Faster; a late regression is harder to bisect (mitigated by per-issue rollback anchors).
- **`none`**: library/PR-terminal campaigns (this one — rawgentic has no deploy). Queue advances on merge; no deploy step.
Default = `none` for libraries, `per-issue` when `has_deploy` and not headless.

## Interaction with `<termination-rule>` (AC3 — not weakened)

The driver **never** re-enters or extends a WF2 run. Each queue iteration invokes `/rawgentic:implement-feature` **fresh** for one issue; that run terminates at Step 16 exactly as today (all gates, its own completion-gate). The driver observes the *outcome* (merged PR / DEFER) and advances its own queue state — it owns only queue/deferral/anchor/policy, never a WF2 step. WF2's per-issue termination is a precondition the driver relies on, not something it overrides.

## Headless interplay

In headless mode WF2 is PR-terminal (no merge/deploy). The driver therefore advances the queue on **PR creation**, not merge, when headless: status goes `in_progress → pr_open`, and a human (or a separate merge-driver) completes the merge. A non-headless driver advances on merge. This keeps the driver honest about what actually happened — it never marks `merged` on a run that only reached PR.

## Resumption across sessions (AC1)

The committed queue state file IS the resumption substrate (survives compaction/`/clear`, unlike in-context memory — the lesson of this campaign's handoff file). On resume: read `<campaign>.json`, find the single `in_progress`/`pr_open` issue (if any) and reconcile its recorded status against the real git/gh state. **Reconciliation table (precedence top-to-bottom; the observed remote state wins over the stale queue value) [Codex F3 — spelled out, not deferred to `resume_lib`]:**

| Real gh/git state | Queue says | Reconciled action |
|---|---|---|
| PR merged | any | mark `merged` (record `merge_sha`), advance to next issue |
| PR open, CI green | `in_progress`/`pr_open` | non-headless → merge then advance; headless → leave `pr_open`, stop |
| PR open, CI red/pending | any | resume the WF2 run at Step 13 (push CI fixes) |
| no PR, branch has commits | `in_progress` | resume the WF2 run at Step 9/11 per its own `resume_lib` on that branch |
| no PR, branch exists no commits | `in_progress` | resume WF2 at Step 8 |
| no branch | `in_progress` | restart the issue at WF2 Step 7 (branch from fresh `origin/<default>`) |
| `branch_preservation: discarded` | `deferred` | do NOT look for the branch; honor the deferral, next issue |
| `branch_preservation: pushed` | `deferred` | branch exists on origin; re-open per the deferral reason when unblocked |

Within a single issue, the driver delegates the *intra-WF2* resume point to WF2's own `resume_lib` (it already encodes the Step-13/14/16 precedence) — the table above only decides the *driver-level* action (advance / merge / resume / restart / honor-defer). This keeps the driver from re-implementing WF2's step detection while still being fully specified at its own layer.

## Follow-up issues (AC4) — filed from this design once approved
1. **Build the documented pattern + queue state schema** (`docs/multi-issue-driver.md` + `claude_docs/.driver-state/` schema + resumption contract).
2. **(evidence-gated) thin `driver_lib.py` validator** — only if hand-maintained JSON proves error-prone.
3. **Parallel multi-issue execution** — depends on worktree isolation (#136/#85); explicitly deferred.

## Codex adversarial review — findings folded (2026-07-04, 0 Critical / 1 High / 3 Medium)
1. [High] state machine excluded `pr_open` but headless needs it → added `pr_open` status + transitions (headless-terminal).
2. [Medium] DEFER branch preservation ambiguous → `branch_preservation: pushed|discarded|none` + `deferred_branch` fields + deterministic rule.
3. [Medium] resumption reconciliation only referenced, not specified → added the ordered reconciliation table (driver layer) that delegates only intra-WF2 resume to `resume_lib`.
4. [Medium] `has_deploy`/`smoke_gate` referenced but absent from schema → added to `policy`, copied from `capabilities` at campaign start.
No Critical/blocker → no design loop-back (lean policy); folded in place. Report: `docs/reviews/2026-07-04-multi-issue-driver-md-2026-07-04.md`.

## Out of scope
Implementation (follow-ups above); parallel issue execution (#136/#85); changing any WF2 gate.
