# Multi-issue driver

Run an autonomous backlog: implement N GitHub issues in one campaign, each as a
full WF2 (`/rawgentic:implement-feature`) run, advancing a durable queue between
them. This is a **documented orchestration pattern**, not a skill — the loop is
control flow an orchestrator already runs reliably; the value is in the
*conventions* below (the queue schema, the DEFER taxonomy, the rollback-anchor
protocol, the dependency ordering, and the resumption contract). Design of
record: `docs/design/2026-07-04-multi-issue-driver.md` (#134); built by #148
(the pattern + v1 queue) and #163 (the v2 dependency DAG + epic anchor).

The small pieces whose behavior is worth testing rather than describing live in
`hooks/driver_lib.py` (`parse_depends_on`, `topo_sort_issues`,
`next_ready_issue`, `validate_driver_state`). Everything else here is procedure
the orchestrator executes.

> **Scope.** The driver owns **only** the queue, deferrals, rollback anchors,
> and inter-issue policy. It never re-enters or extends a WF2 run. The fuller
> state-transition validator (`record_outcome` / `defer_issue` / queue mutation)
> is deliberately **not** shipped yet — design #134 follow-up #2, still gated on
> evidence that hand-maintained state transitions prove error-prone.

## The loop

For each issue the campaign advances to:

1. **Pick the next issue** — `next_ready_issue(state, deps_satisfied_by)` (see
   *Dependency ordering*). If it returns `None`, the campaign is done or every
   remaining issue is parked/blocked.
2. **Run WF2 fresh** — invoke `/rawgentic:implement-feature <number>` as a brand
   new run. It goes through all 16 steps and **terminates at Step 16** exactly as
   it does standalone. The driver observes the *outcome*; it never reaches inside
   the run.
3. **Advance the queue** on the outcome:
   - **merged** (non-headless) → status `merged`, record `pr` + `merge_sha`.
   - **pr_open** (headless — WF2 is PR-terminal) → status `pr_open`; a human or a
     separate merge-driver completes the merge. The driver **advances on
     `pr_open`, not `merged`, when headless** so it never claims a merge that did
     not happen.
   - **DEFER** → park with a typed reason and **continue** to the next issue (see
     *DEFER taxonomy*). A wall on one issue never stalls the whole backlog.

Only one issue is `in_progress`/`pr_open` at a time (serial; parallel execution
needs worktree isolation and is out of scope — #136/#85).

### Policy

Campaign-wide policy lives in the state file's `policy` object so a mid-run
compaction can't lose it:

- `order` — `impact` (hand-ordered) or `dependency` (topo-sorted; see below).
- `deploy` — `per-issue` (deploy after each merge), `batch` (deploy once at the
  end), or `none` (library / PR-terminal campaigns — rawgentic itself). Default
  `none` for libraries, `per-issue` when `has_deploy` and not headless.
- `has_deploy` / `smoke_gate` — copied from `capabilities` at campaign start so
  the driver reads deploy-availability + the required smoke command from
  committed state, not ambient config. `deploy: per-issue` with
  `has_deploy: false` is a config error the driver rejects at start.
- **review budget** (`review_budget`, default 3) — the WF2 loop-back budget per
  issue; the driver never lowers a WF2 gate to "go faster across the backlog."
- **never-Haiku** (`never_haiku: true`) — coding is never routed to Haiku;
  enforced independently by `select_impl_model`'s floor and the bundled agent
  definitions. The driver restates it so a campaign operator can't forget it.

## Queue state schema

The **live** per-campaign state is written at runtime to
`claude_docs/.driver-state/<campaign>.json`. That directory lives under the
gitignored `claude_docs/` tree (runtime working state, like `.wf2-state/`), so
the live file is **disk-persisted, not git-committed** — which is exactly what
resumption needs: it survives compaction / `/clear` (unlike in-context memory)
without polluting the repo with in-flight campaign state. When the design calls
the state "committed," read it as *durably persisted to disk*.

The **git-tracked contract** — the schema and worked examples — lives in
`docs/driver-state/`:
- `docs/driver-state/queue.schema.json` — the JSON Schema (draft-07).
- `docs/driver-state/example-v2.campaign.json` — a dependency-DAG campaign.
- `docs/driver-state/example-v1.campaign.json` — a v1 campaign (no
  `depends_on`), proving v1 files still validate under the v2 schema.

> The driver loads a **named** `<campaign>.json` file, so the committed
> `example-*.campaign.json` references are never mistaken for a live campaign.

Shape (see the schema for the authoritative definition):

```json
{
  "schema_version": 2,
  "campaign": "issues-201-205",
  "policy": {"order": "dependency", "deploy": "none", "has_deploy": false,
             "smoke_gate": null, "review_budget": 3, "never_haiku": true,
             "deps_satisfied_by": "merged"},
  "base_default_branch_sha": "<origin/default HEAD when the campaign started>",
  "epic": 200,
  "issues": [
    {"number": 201, "status": "merged", "depends_on": [], "pr": 301,
     "merge_sha": "a1b2c3d", "rollback_anchor": "0000000",
     "deferred_reason": null, "deferred_branch": null,
     "branch_preservation": null}
  ]
}
```

**Status machine.** `status` is one of `queued`, `in_progress`, `pr_open`,
`merged`, `deferred`, `abandoned`:

```
queued → in_progress → { pr_open | merged | deferred | abandoned }
pr_open → { merged | deferred | abandoned }   # headless stops at pr_open
```

`validate_driver_state(state)` is a stdlib-only readability check (no jsonschema
dependency) for both schema versions; the committed `queue.schema.json` is the
fuller contract, validated against the examples in the test suite.

## DEFER taxonomy

An issue hits a wall mid-build → park it with a typed reason and **continue the
loop**:

| DEFER type | Trigger | Loop action |
|---|---|---|
| `owner-decision` | needs a human product/risk call | park, surface in ledger, next issue |
| `owner-reserved` | touches a surface the owner reserved | park, note the gate, next issue |
| `cross-repo` | change spans another repo | park with the blocking dependency |
| `budget` | campaign token/time budget exhausted | park remaining queued issues, stop cleanly |
| `cross-issue-dependency` | an in-queue dependency was itself deferred/abandoned | park the dependent; independent issues keep going (#163) |

### Branch preservation on DEFER

The outcome is recorded deterministically in `branch_preservation` +
`deferred_branch`, so resumption never guesses:

- **`pushed`** — the branch has ≥1 commit AND the deferral may be resumed later
  (`owner-decision`, `owner-reserved`, `cross-repo`): push it, set
  `deferred_branch: <name>`, `branch_preservation: "pushed"`.
- **`discarded`** — commits exist but the approach is abandoned:
  `git checkout <default> && git branch -D`, `deferred_branch: null`,
  `branch_preservation: "discarded"`.
- **`none`** — no commits before the wall (deferred at design/plan). Nothing to
  preserve.

`budget` deferrals of not-yet-started issues stay `queued` (no branch), not
`deferred`.

## Rollback-anchor protocol

Before each issue's branch is created, capture the current `origin/<default>`
HEAD as that issue's `rollback_anchor`; `base_default_branch_sha` records the
campaign's starting point. On a bad merge discovered *after* merge:
`git revert <merge_sha>` (preferred, preserves history) or reset a not-yet-pushed
default branch to the anchor. The anchor also validates that the next issue
branches from a **fresh** base (the #140 fix) — assert `new base == current
origin/<default>`, never a stale sibling.

## Dependency ordering (schema v2)

When `order: dependency`, the queue is a DAG.

1. **Parse dependencies** with `parse_depends_on(body)` — it extracts issue
   numbers only from a recognized dependency phrase ("depends on #N", "blocked
   by #N") or a task-list checkbox ("- [ ] #N"). It is **prompt-injection-safe**:
   a bare `#N` in ordinary prose (e.g. "see #999 for context", "does not depend
   on anything") is *not* taken as a dependency. Supplement with
   `gh api` issue relationships where available.
2. **Topologically sort** at campaign start with `topo_sort_issues(issues)`
   (Kahn's algorithm; deterministic tie-break = lowest issue number first).
   **Cycles halt fail-closed:** on a cycle the function raises
   `DependencyCycleError` with the offending cycle printed (e.g.
   `#1 -> #2 -> #1`) — the campaign stops loudly rather than silently
   mis-ordering. External dependencies (not in the queue) impose no ordering
   edge.
3. **Advance rule** — `next_ready_issue(state, deps_satisfied_by)` returns the
   first `queued` issue whose in-queue dependencies are satisfied. A dependency
   counts as satisfied per the `deps_satisfied_by` policy knob:
   `merged` (default) → only `merged`; `pr_open` → `merged` or `pr_open`. A
   dependency that is `deferred`/`abandoned` is **not** satisfied, so its
   dependents are parked (`cross-issue-dependency`) while independent issues keep
   advancing. Dependencies outside the queue are external — the offline helper
   cannot verify them, so it treats them as satisfied for ordering/readiness.

### v1 compatibility

`schema_version: 1` files predate the DAG and omit `depends_on`. A v2 reader
must accept them: `validate_driver_state` treats a missing `depends_on` as `[]`,
and `topo_sort_issues` on issues with no dependencies degrades to the
ascending-number order. So a v1 campaign runs unchanged under v2 tooling.

## Epic anchor

A campaign may be anchored to an **epic** issue (`epic: <number>`) instead of an
inline list:

- The queue is derived from the epic's task list (its `- [ ] #N` children); an
  inline list is also accepted, in which case the driver offers to create the
  epic with one `gh` call.
- Epic checkboxes are mirrored **one-way**: the state file → the epic. The state
  file remains the sole machine source of truth; a human ticking an epic box
  never writes back into state, so a tampered or hand-edited epic cannot corrupt
  the machine queue.
- **Headless runs refuse to start without an epic.** In headless mode the epic is
  the STATUS/QUESTION channel (the driver has no terminal), so a headless
  campaign with no epic is a hard error at start, not a silent degrade.

## Rate limits

On a subscription-auth **rate-limit** lockout, map the current issue to a
`budget` DEFER and note the resume-after-window-reset time in the ledger, then
stop cleanly. The campaign resumes when the window resets — no work is lost, and
the queue records exactly where it stopped.

## Resumption

The disk-persisted `<campaign>.json` is the resumption substrate. On resume:
read it, find the single `in_progress`/`pr_open` issue (if any), and reconcile
its recorded status against the real git/gh state. Precedence (observed remote
state wins over a stale queue value):

| Real gh/git state | Reconciled driver action |
|---|---|
| PR merged | mark `merged` (record `merge_sha`), advance |
| PR open, CI green | non-headless → merge then advance; headless → leave `pr_open`, stop |
| PR open, CI red/pending | resume the WF2 run (it re-enters at its own Step 13) |
| no PR, branch has commits | resume the WF2 run on that branch |
| no PR, branch exists, no commits | resume the WF2 run at its build step |
| no branch | restart the issue (WF2 branches from fresh `origin/<default>`) |
| `branch_preservation: discarded` | do NOT look for the branch; honor the deferral, next issue |
| `branch_preservation: pushed` | branch exists on origin; re-open per the deferral reason when unblocked |

The reconciliation table decides only the **driver-level** action (advance /
merge / resume / restart / honor-defer). The **intra-WF2** resume point (which
WF2 step to re-enter) is delegated to WF2's own `resume_lib` — the driver never
re-implements step detection.

## Interaction with WF2 (not weakened)

Each queue iteration invokes `/rawgentic:implement-feature` **fresh** for one
issue; that run terminates at **Step 16** with all its gates intact (design
critique, TDD, per-task review, code review, security scan). The driver observes
only the outcome and updates its own queue — it owns queue/deferral/anchor/policy
and **never** a WF2 step, so it is structurally impossible for the driver to
**weaken** WF2. WF2's per-issue termination (`<termination-rule>`) is a
precondition the driver relies on, not something it overrides.
