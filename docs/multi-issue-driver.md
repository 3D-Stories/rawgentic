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
`next_ready_issue`, `validate_driver_state`, `validate_campaign_start`).
Everything else here is procedure the orchestrator executes.

> **Scope.** The driver owns **only** the queue, deferrals, rollback anchors,
> and inter-issue policy. It never re-enters or extends a WF2 run. The fuller
> state-transition validator (`record_outcome` / `defer_issue` / queue mutation)
> is deliberately **not** shipped yet — design #134 follow-up #2, still gated on
> evidence that hand-maintained state transitions prove error-prone.
>
> **Update (#695): that evidence arrived, and the transition layer now exists**, narrowly.
> `driver_lib.record_child_outcome(state, issue, status)` is a pure transition with a
> non-regressible-terminal rule; the queue-mutation and full-validator layer above it is
> still out of scope.

## Who owns the write to `.driver-state` (#695)

**One owner: the `record-child-outcome` command**
(`python3 hooks/launcher_lib.py record-child-outcome --issue <n> --status <s> --project-root .`).
Before #695 the honest answer was *"the driver, except when it isn't"* — and that exception was
the whole defect.

The driver writes this file when **it** sequences children. But a child invoked directly as
`/rawgentic:implement-feature <n>` bypasses that writer entirely, which is exactly what an epic
auto-run does after a fresh-session handoff, and exactly what the resume prompt instructs a
successor to do. So the gap reproduced on **every** non-driver child, silently. Observed live on
epic #684: `claude_docs/.driver-state/epic-684-watcher-fires.json` still read
`{"number": 687, "status": "queued"}` after #687 merged (PR #691) and closed, and the resume
duly offered #687 as the next ready child.

The command is invoked at **each authoritative terminal event**, not at one step:

| Call site | Why |
|---|---|
| **WF2 Step 14, item 2b** — immediately after the merge is confirmed | The write follows the event that makes it true. Step 16 alone is **not atomic** with the merge, so a crash between them reproduces the defect. |
| **WF2 Step 16, item 1a** — idempotent reconciliation | Catches every non-merge terminal outcome (`pr_open` headless, `deferred`, `abandoned`) and any run interrupted before Step 14's write landed. |

Naming the *command* the owner rather than a step is what keeps "one owner" true across two call
sites: one implementation, one lock (`launcher_lib._locked_state_update` — still the only locked
driver-state writer), one transition table. Recording a status a child already has is a no-op, so
the second call costs nothing.

**Discovery, not configuration.** With `--driver-state` omitted the command scans
`claude_docs/.driver-state/*.json` and updates **every** campaign whose queue names the issue —
a single-session run does not know its campaign, and updating only the first match would leave
the others stale. Zero matches is the normal case and is a logged no-op.

**Fail-open, never silent.** No campaign, or no state directory → exit 0, write nothing, print
the reason to stdout **and** stderr. An off-vocabulary status, a terminal regression, or a
corrupt state file → non-zero, file untouched: those are caller or data errors, not states of
the world.

**Belt-and-braces: the resume path also corroborates.** `next_ready_issue` and
`fresh_session_handoff` accept an `issue_state_probe`, and `launcher_lib handoff` supplies one
built from `gh api graphql` (the installed `gh issue view --json` exposes neither `stateReason`
nor `closedByPullRequestsReferences`, so it cannot answer this). A `queued` entry whose real
issue is confirmed closed is never selected, and a confirmed-**merged** prerequisite satisfies
its dependents even while the file still says `queued` — without that second half, an
already-stale campaign reports "no ready child" forever. An unreachable probe leaves the file's
status standing: corroboration must not turn a GitHub outage into a silent campaign stall.
This is what protects the files **already** stale on disk, which the write-back cannot
retroactively repair.

## The loop

For each issue the campaign advances to:

1. **Pick the next issue** — `python3 hooks/launcher_lib.py next-child --driver-state <file>
   --project-root <root> [--project <name>]`, and branch on its exit code (full contract under
   *Selection in the IN-SESSION loop*, below). **Never call `next_ready_issue` directly here.**
   That is the pure function; it cannot fetch, so on a receipt-less campaign it selects without
   ever observing `origin/main` — bypassing the #840 gate — and on an armed one it raises
   `QueueRevalidationRequired` instead of selecting. `next-child` is the caller that observes the
   head first and then selects. rc 0 → build that child; rc 3 → the campaign is done or every
   remaining issue is parked/blocked; rc 6 → stale provenance, cleared by
   `/rawgentic:revalidate-children` (the refusal names the remedy — this is the ONLY reason rc 6
   carries); rc 11 → a live `pending_disposition` on the child that would otherwise be selected,
   cleared only by an owner running `record-child-outcome` (#944, restoring what #840 cut — see
   *Selection in the IN-SESSION loop*, below); rc 5 → stop, the head could not be observed; rc 2 →
   read stdout before deciding (see the table).
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

Only one issue is `in_progress` at a time — one build at a time (serial; parallel
execution needs worktree isolation and is out of scope — #136/#85). `pr_open`
issues may **accumulate** awaiting human merge in a headless stacked-PR campaign
(`deps_satisfied_by: pr_open`); a non-headless driver merges each PR before
advancing, so at most one `pr_open` exists transiently. `validate_driver_state`
enforces exactly this: at most one `in_progress`, unbounded `pr_open`.

### Policy

Campaign-wide policy lives in the state file's `policy` object so a mid-run
compaction can't lose it:

- `order` — `impact` (hand-ordered) or `dependency` (topo-sorted; see below).
- `merge_policy` — `auto-merge-scoped-to-run` or `pr-only`, written from the epic-run
  Step-2 answer. **This is the field the supervision authority gate actually reads**
  (`supervision_route.evaluate_campaign`): during a declared absence, a merge is
  permitted only on the EXACT string `auto-merge-scoped-to-run` and only when no
  tightening override denies it. Any other value, and an absent key, are no grant — so
  `broker-merge` refuses. Documented here since #963, when the gate turned out to be
  reading a key no prose had ever told anyone to write.
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

## The boundary learnings sweep (#769)

**After every merged, deferred, or abandoned child — and whenever `origin/main` moves between
children without a completion — sweep every remaining eligible child against the learnings for
that boundary before selecting or handing off the next child.**

Queue revalidation (below) asks a narrow, machine-checkable question: *do the remaining issue
bodies still describe reality at this head?* The owner's standing order (D181, epic #906,
2026-08-05) asks a wider one — re-assess every remaining child against what the completed child
**learned**: its review findings, its recorded decisions, the issues it filed. A completed child
routinely invalidates a sibling's premise in a way no line-anchor check can see. The five-part
procedure lives in `skills/epic-run/SKILL.md`; this is the durable contract.

**State.** `boundary_sweeps` is an append-only top-level array (declared in
`docs/driver-state/queue.schema.json`). One record per boundary:

| Field | Meaning |
|---|---|
| `swept_at_head` | the 40-char `origin/main` sha the assessment was made against |
| `after_issue` | the disposed child whose learnings drove it, **or `null`** when the head moved with no child completing |
| `learnings` | what the boundary taught — required, non-empty |
| `assessments[]` | one entry per remaining eligible child: `issue`, `outcome`, `note`, plus `ref` when the outcome is not `unaffected` |

Outcomes are `unaffected`, `commented` or `rescoped`. Coverage is **set EQUALITY** against the
children whose status is not `merged`/`deferred`/`abandoned` — a missing child and a foreign one
are equally refused.

**Replay identity is `(swept_at_head, after_issue)`, not the head alone.** A deferred or abandoned
child moves no commit, so two genuine boundaries can share one head. An exact replay (compared
semantically — `observed_at` excluded, assessments order-insensitive) writes nothing; a differing
record at the same identity is refused, so state never holds two contradictory records for one
boundary.

**The gate.** `next-child` and `handoff` both refuse with **rc 8** when the boundary is unswept.
`sweep record` clears a `missing`; an `unreadable` field needs the state repaired first — copy the
file aside, RESET the field to `[]`, confirm it parses, then record again. **Reset it; never
delete the key.** An absent key is the migration marker below, so deleting it would turn a
documented repair into a permanent bypass of the gate.

**What this gate checks, and what it cannot.** Coverage and record integrity ONLY: that a record
exists for this head naming every remaining child with a reason. Whether the judgment behind it
was any good is not observable from state, and nothing here claims otherwise — the same honesty
the revalidation receipt's `depth` field failed at.

**Read-side integrity.** `boundary_sweep_status` re-validates EVERY record in full — head,
provenance, learnings, and each assessment's outcome/note/ref — before it will answer `swept`.
Checking only the issue-number set would let a hand-written record with no evidence in it open
a gate whose whole promise is record integrity.

**Migration.** A campaign whose state carries no `boundary_sweeps` key predates this contract and
is **not** gated; gating it would refuse work over a boundary already past and no longer sweepable.
Campaign creation seeds `[]`, which opts a campaign in; an existing campaign adopts by recording
its first sweep.

## Queue revalidation (#840)

Every merge moves `main` underneath every child that has not started, so their file:line
citations, root-cause claims and acceptance criteria rot silently. The 2026-08-02 audit of epic
#756 found **14 of 23** open children carrying claims that no longer held, three of which had
already caused wasted work. This machinery makes that a gate rather than a habit.

**Per-child provenance.** An `issues[]` entry may carry
`"validated_against": "<40-char sha>"` — the `main` SHA that child's claims were last checked
against. Absent means *never validated*, which is treated as outstanding, never as fine.

**Campaign-level receipt.** `queue_revalidation` records the evidence:

```json
{"queue_revalidation": {
  "version": 1, "extractor_version": 1,
  "validated_head": "<sha>",
  "children": {"840": {
    "body_hash": "<sha256>", "from_sha": "<sha>", "to_sha": "<sha>",
    "extraction": "paths|none|ambiguous", "depth": "deep|quick",
    "outcome": "still_valid|body_corrected",
    "pending_disposition": null,
    "claims": [{"kind": "citation|cause|ac", "quoted_from_body": "…",
                "checked_against": "<path>@<sha>", "evidence": "…",
                "verdict": "holds|broken"}],
    "correction_comment": null, "validated_at": 0}}}}
```

Both keys are additive: `validate_driver_state` ignores them and
`queue.schema.json` sets `additionalProperties: true`, so **no `schema_version` bump is
required** and every pre-#840 campaign file validates unchanged. That permissiveness is also
why the dedicated validators (`validate_validated_against`, `validate_claims`,
`validate_revalidation_child`) RAISE rather than accumulate errors — a malformed stamp would
otherwise pass every existing check in silence.

**The intersection sets DEPTH, never whether to look** (owner ruling 2026-08-02).
`revalidation_worklist(state, observed_head, extractions, changed_by_child)` returns one item
per *effective*-status-`queued` child **not ATTESTED at the observed head** — and attested means a
stamp AND a current receipt entry covering that child, never a stamp alone (round-7 finding 2; the
caller must build its extraction/diff inputs from that same trust-aware candidate set, or the
function raises `no extraction supplied for child #N` on exactly the child it now re-audits).
A child whose cited
files the merge did not touch is still looked at — it is merely `quick` rather than `deep`.
Nothing is auto-cleared. An earlier design did auto-clear such children and was refuted: a
merge can invalidate a root-cause claim through a file the child never cites, which is exactly
how #835's body came to name the wrong cause.

**A stamp requires evidence.** `claims` must be non-empty, and each record names the claim, what
it was checked against, and the quote that settles it. Without that, an agent could mark every
remaining child valid having checked nothing, and the gate would report a fully validated queue.

**`issue_obsolete` is not an `outcome`.** An obsolete child carries
`pending_disposition: "issue_obsolete"` until an owner moves it to `deferred` or `abandoned`.
**That marker now genuinely gates (#944, restoring what #840 cut).** `next_ready_issue` refuses to
hand out a child carrying a live `pending_disposition` — `next-child`/`handoff` surface this as rc
11, naming the write-back remedy. The scan does not stop at the first marked child: it remembers
the first one it sees and keeps looking, raising only when every remaining ready child is
pending-disposition or nothing is ready at all — so one obsolete-marked child never blocks
unrelated, independent work elsewhere in the queue. The child stays STAMPED regardless (its
evidence carries forward into the receipt) — stamping and gating are separate concerns, and
withholding the stamp is what jammed the queue while the gate was cut.

**Corrections are COMMENTS, never body edits.** A child's body is its author's statement of the
problem; the run annotates it, it does not rewrite it underneath them.

### The gate is LIVE (#840 PR 2)

Three layers, in increasing order of authority.

**1. Selection.** `next_ready_issue(state, ..., observed_head=<sha>)` raises
`QueueRevalidationRequired` when the receipt's `validated_head` differs from the observed head, or
when any eligible child's `validated_against` differs from it. (A separate, distinct exception,
`ObsoletePendingChild` — described above — covers the `pending_disposition` case; it is its own
type rather than a third clause here, so the CLI can map it to its own return code, rc 11.)
It **raises** rather than returning `None`, because
`None` already means
"nothing ready" and is reported as *the epic finished* — announcing completion over a stale queue is
strictly worse than refusing. `fresh_session_handoff` surfaces it as an explicit
`revalidation_required` disposition carrying the outstanding worklist; `launcher_lib handoff` exits
**6** for it, distinct from a clean `complete`.

**2. `observed_head` must be FRESHLY OBSERVED.** `launcher_lib.observe_head(repo_root)` runs
`git -C <root> fetch origin` then `git -C <root> rev-parse origin/main`, checks BOTH return codes,
and validates the output is a full 40-character SHA. It is the only permitted source. A cached SHA —
or `validated_head` itself — would satisfy both refusal clauses after `main` had moved, and would
silently defeat the abrupt-death recovery, since a crashed predecessor's stale head compares equal to
itself. `launcher_lib handoff` exits **5** when the head cannot be observed.

**3. The handoff ladder.** `queue_revalidated` is the FIRST rung of the mid-child ladder — the queue
must be revalidated before a successor is spawned to inherit it. Its result is produced by the
launcher reading the durable receipt (`produce_queue_revalidated`), never supplied by a caller: an
agent asserting its own homework is the vacuous pass this whole mechanism exists to prevent. Because
`evaluate_verifications` treats an unreported step as FAILED, a missing or stale receipt means the
handoff produces **no successor** and the predecessor stays alive and guarded.

`handoff_pending.queue` carries the ordered child list plus `validated_head`, and `handoff_claim`
validates the **complete ordered payload** against durable state at claim time — order included,
because order decides which child runs next.

**Recovery from abrupt death.** A session killed abruptly runs neither `perform_handoff` nor
`retire_predecessor`, so the rung never fires for that case. It is covered at layer 1 instead: the
successor's FIRST `next_ready_issue` refuses, because a crashed predecessor left `validated_head`
behind the head it died at.

**What clears it depends on WHY it refused** — a distinction round-5 finding 4 caught this
document getting wrong, having previously said "`/rawgentic:revalidate-children`, and nothing
else":

- **Stale provenance** (the receipt attests an older head, or an eligible child is unstamped) →
  `/rawgentic:revalidate-children`, and nothing else. Re-running it is the whole remedy.
- **A pending disposition is a second, owner-only reason (#944, restoring what #840 cut).** Only
  the owner clears it — `record-child-outcome --issue N --status deferred|abandoned|merged`,
  named verbatim in the rc-11 refusal text. Re-running `/rawgentic:revalidate-children` does NOT
  clear this one: it rediscovers the same marker for ever, because choosing between `deferred` and
  `abandoned` is deliberately not a machine's decision.

Neither bullet covers a CALLER or ENVIRONMENT failure — an absent `campaign_context`, an unreadable
state file, an unobservable head. Those are refusals of the rung's producer rather than of the
queue, and no amount of revalidation repairs them; the message says which one it is.

**Selection in the IN-SESSION loop goes through `launcher_lib next-child`.** The driver's default
mode is `single-session`, which never crosses a process boundary and so never calls `handoff`. Before
#840's review round that loop had no gated way to pick a child — the skill read state itself, and
`fresh_session_handoff` returned `single_session` before selection, so an armed campaign with a stale
receipt advanced anyway. Moving the gate above the mode check was necessary but not sufficient: a
pure function cannot fetch. `next-child` is the caller that observes. Exit codes match `handoff`:

| rc | meaning |
|---|---|
| 0 | a child is ready; `next_issue` on stdout |
| 2 | **caller/data error — parse stdout before deciding** (see below) |
| 3 | nothing ready (`complete` / `blocked`) |
| 5 | the head could not be observed — fail-closed |
| 6 | the queue needs revalidation; the worklist is on stdout |
| 11 | the child that would otherwise be selected carries a live `pending_disposition` — an owner-gated refusal, not self-clearing (#944, restoring what #840 cut) |

**rc 2 covers three situations and the number alone cannot separate them** (round-3 finding 6):
unreadable or invalid state JSON, any `DriverStateError`, and — the one that is not a failure —
a SUCCESSFUL selection whose campaign carries no valid `project`. So an automated caller must
read stdout on rc 2: **a `next_issue` key means selection worked and only the project binding is
missing** (supply `--project` or add the field, then re-run), while no `next_issue` means the
state itself could not be used and the run stops. rc 2 is deliberately NOT rc 3: folding a
config error into "nothing ready" once stopped a live campaign for good, which is round-2
finding 2.

**The gate is UNIVERSAL — a campaign with no receipt is refused, not waved through.** An earlier
revision activated enforcement per campaign, once a receipt existed. The Step-11 cross-model review
called that opt-in theatre and it was right: nothing in the code created the first receipt or produced
the refusal that would prompt anyone to, so every pre-#840 campaign and every new one stayed
ungated. Owner decision 2026-08-02 closed it. A refusal is recoverable by one command; silent
selection is the one failure direction this design forbids.

Consequence, stated rather than discovered: **an existing campaign refuses until
`/rawgentic:revalidate-children` has run against it once.** That is the migration, and it is
deliberate. `handoff_pending` still keeps its exact three-key legacy shape for a campaign with no
receipt, so nothing about the persisted record changes until a campaign is armed.

## DEFER taxonomy

An issue hits a wall mid-build → park it with a typed reason and **continue the
loop**:

| DEFER type | Trigger | Loop action |
|---|---|---|
| `owner-decision` | needs a human product/risk call | park, surface in ledger, next issue |
| `owner-reserved` | touches a surface the owner reserved | park, note the gate, next issue |
| `cross-repo` | change spans another repo | park with the blocking dependency |
| `budget` | campaign token/time budget exhausted | park remaining queued issues, stop cleanly |
| `cross-issue-dependency` | an in-queue dependency was itself deferred/abandoned | dependent stays `queued`, implicitly parked — the advance rule simply skips it; mark it `deferred` with this reason only if the dependency is `abandoned` or the campaign ends with it still blocked (#163) |

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

### The ledger

The ledger **is the state file** — no separate document. It has two parts: the
per-issue `deferred_reason` / `deferred_branch` / `branch_preservation` fields,
and a top-level `notes[]` array of dated free-text strings for everything that
isn't a per-issue field — DEFER surfacing ("2026-07-04: #162 deferred
owner-decision — resume after data review"), rate-limit window-reset times, and
mid-loop status changes discovered during reconciliation. A campaign with an
epic anchor may mirror ledger highlights into an epic comment, but the state
file remains the machine source of truth.

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
   by #N") or a task-list checkbox ("- [ ] #N"). It is a **narrow best-effort
   filter** (not a hard security boundary — it is not markdown-aware, so a phrase
   quoted in a blockquote/code fence is still taken): the phrase is matched at
   word boundaries (so "unblocked by" does not count), negated phrases ("not
   blocked by", "no longer depends on") are ignored, and only the *immediate*
   `#N` list right after the phrase (comma/"and"-separated) is taken — parsing
   stops at a sentence boundary, so a following sentence ("Depends on #10. See
   #20 for context" → `[10]`) does not inject a dep. A bare `#N` in ordinary
   prose is *not* a dependency. Supplement with `gh api` issue relationships
   where available.
2. **Topologically sort** at campaign start with `topo_sort_issues(issues)`
   (Kahn's algorithm; deterministic tie-break = lowest issue number first), then
   **persist that order back into `state["issues"]`** so the advance loop's
   list-order iteration IS the topological order — `next_ready_issue` returns the
   first ready issue *in list order*, so the deterministic dependency order only
   holds if the list is stored sorted.
   **Cycles halt fail-closed:** on a cycle the function raises
   `DependencyCycleError` with the offending cycle printed (e.g.
   `#1 -> #2 -> #1`) — the campaign stops loudly rather than silently
   mis-ordering. External dependencies (not in the queue) impose no ordering
   edge.
3. **Advance rule** — the ordering law `next-child` enforces underneath. `next_ready_issue(state,
   deps_satisfied_by)` returns the
   first `queued` issue whose in-queue dependencies are satisfied. **This states the ordering law,
   not the call to make: never invoke `next_ready_issue` directly — drive it through
   `python3 hooks/launcher_lib.py next-child`** (see *The loop*), which observes `origin/main`
   first. Calling it directly bypasses the #840 gate on a receipt-less campaign and raises
   `QueueRevalidationRequired` on an armed one. A dependency
   counts as satisfied per the `deps_satisfied_by` policy knob:
   `merged` (default) → only `merged`; `pr_open` → `merged` or `pr_open`. A
   dependency that is `deferred`/`abandoned` is **not** satisfied, so its
   dependents are implicitly parked — they stay `queued` and the advance rule
   skips them (`cross-issue-dependency` is recorded only per the DEFER-table
   rule) — while independent issues keep advancing. Dependencies outside the
   queue are external — the offline helper
   cannot verify them, so it treats them as satisfied for ordering/readiness.
   `next_ready_issue` does **not** re-detect cycles — it returns `None` when
   nothing is ready — so the campaign-start `topo_sort_issues` call above is the
   fail-closed cycle gate this advance loop relies on; run it before the loop.

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
  campaign with no epic is a hard error at start, not a silent degrade. Enforced
  by `validate_campaign_start(state, headless=True)`, which errors when `epic` is
  null/missing under headless.
- **Never run `parse_depends_on` on the epic body itself.** The epic's task-list
  children (`- [ ] #N`) use the same syntax a dependency checkbox does, so
  dependency-parsing the epic would misread every child as a dependency. The
  epic body is queue-*derivation* input only; dependencies come from each child
  issue's own body.

## Epic-level goal guard (kickoff)

The `/goal` guard belongs at the **campaign level**, not per-issue: a per-issue
goal lets the session quit after any single slot, and same-session `/goal`
overwrite is documented-unverified (#192). At campaign kickoff the driver sets
ONE goal over the epic's ordered child set:

1. Build the campaign goal text: `driver_lib.campaign_goal_text(state)` — it
   enumerates the `epic` anchor + the topo-ordered child queue via
   `plan_lib.build_goal_text(epic, [], variant="campaign", child_issues=...)`,
   with a **tolerant escape clause** ("a child closed not-planned per its own
   acceptance criteria counts as satisfied, and the owner may pause the campaign
   at any time") so a real campaign outcome clears the goal instead of firing
   relentlessly against a stale condition.
2. **Emit** that text for the owner to run — a skill cannot self-set `/goal`
   (session-level, owner-run); the driver surfaces it at kickoff.
3. **Export `RAWGENTIC_EPIC_GOAL=<epic>`** into the environment of each child
   WF2/WF3 run. Their Step 1b sees it and **defers** (emits no per-issue `/goal`
   that would clobber the campaign goal), logging `(deferred: epic #<N>)`.

The known limitation stands: a skill still cannot set or refresh `/goal`
mid-campaign — the tolerant escape clause is what keeps a stale goal from firing
relentlessly, and the owner may pause at any time.

## Rate limits

On a subscription-auth **rate-limit** lockout, map the current issue to a
`budget` DEFER and note the resume-after-window-reset time in the ledger, then
stop cleanly. The campaign resumes when the window resets — no work is lost, and
the queue records exactly where it stopped.

## Resumption

The disk-persisted `<campaign>.json` is the resumption substrate. On resume:
**validate first** (`validate_driver_state` on every load, not only at campaign
start — the file is hand-maintainable JSON, and the DAG helpers fail closed on
mistyped fields such as string issue numbers in `depends_on`), then read it,
find the `in_progress` issue and any `pr_open` issues, and reconcile their
recorded status against the real git/gh state. Precedence (observed remote
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

## Fresh session per child (#569; the DEFAULT since #927)

By default the whole epic run lives in ONE Claude process (the `/goal` Stop-hook
re-injects into the same session); "fresh WF2 per child" is NOT a fresh *session*, so
context accumulates across children. **The per-child process boundary gives each child its own
process, with continuity carried by durable state alone — and since #927 it is the DEFAULT.**

**How a campaign's transport is decided (#927).** `.driver-state.preferred_transport` is
`pane_chain` or `inline`, and it is **PROBED at campaign creation**, never asked at setup:
`launcher_lib transport resolve-creation` runs a tier-1 herdr capability probe and records the
answer plus the probe's reason. `pane_chain` means a boundary per child; `inline` means the
in-session loop. It is write-once — `transport set pane_chain|inline --reason "<why>"` changes it
later, refused while a child is `in_progress` or a boundary holds a claim, and the NEXT boundary
still probes. `session_mode` survives ONLY as a write-only compatibility projection kept in sync
at the single locked writer, so a build rolled back to a pre-#927 version still behaves
correctly; `preferred_transport` always wins where they disagree. A campaign carrying neither
field is the one genuinely defaulted case and stays `inline`, byte-identical to before.

**Why probed rather than recorded once by hand.** herdr can be present at creation and gone by
boundary 4, so an answer given at setup is stale by construction — that staleness is the defect
#927 exists to remove, which is also why the launcher's caller-asserted `--herdr-available` flag
was deleted rather than defaulted.

**The boundary.** After a child reaches ANY terminal outcome — `merged` or a blocker's
`deferred`/`abandoned` — the session ENDS (a blocked child's context must not bleed into an
independent successor). The driver gets the disposition from `launcher_lib handoff`,
which calls `driver_lib.fresh_session_handoff(state, mode=..., observed_head=...)` with a head it
observed itself (#840 — an armed campaign refuses a disposition computed without one).
It computes an explicit disposition — never a `None` sentinel:

- `ready` → **the command performs the boundary itself**: it splits the pane, launches the
  successor and (unless `--no-teardown`) retires the predecessor, then prints an `ok` report.
  It does **not** return the disposition and does **not** call `open_handoff`, so there is
  nothing for a caller to persist afterwards. *(Corrected at Step-11 round 4, High 2 — this
  document and `skills/epic-run/SKILL.md` both described a two-step "get the disposition, then
  `open_handoff` it" flow that production never implemented. Following it launched the successor
  before anything durable was written and then looked for a `disp` that does not exist.)*
  **The exactly-one-successor fence is HERE now (#845, closed inside #927).** The command opens
  the generation and takes a claim BEFORE it probes, then records a `resolution` event with the
  pre-split pane inventory BEFORE any launch, and marks `split_attempted` before calling the
  split. That ordering is the safety property: `split_attempted: false` PROVES nothing was
  created, so only that state authorises a relaunch, and a `null` successor under
  `split_attempted: true` is resolved by an inventory diff rather than trusted. A second
  invocation for one boundary loses the claim and exits **rc 7** doing no campaign work — it does
  NOT fall back to running the child in its own session, because at a boundary nothing is in
  flight and "continuing" would mean starting the next child beside the holder's successor. The
  claim is released on a DEFINITE terminal outcome and deliberately held on an indeterminate one,
  because that lease is what protects a possibly-live successor until reconciliation runs.
- `complete` (ONLY when every child is `merged`) → run the wrap-up (close the epic).
- `blocked` (unmerged children remain but none is ready — all deferred/abandoned/dep-blocked) →
  leave the epic OPEN with an honest summary and end. **`blocked` is never conflated with
  `complete`** — a blocked-incomplete epic is never closed.

**The launcher contract (`--resume` must be skipped).** The durable `long-run-resume`
launcher is the process-boundary vehicle. Its default relaunch tries `--resume <id>` first,
which reloads the prior session's transcript — that would defeat the whole point. So: **when
`.driver-state.session_mode == "fresh-session"`, the launcher MUST skip the `--resume` attempt
and invoke `claude -p` with NO session id**, giving the successor an empty context. (The
`epic475-resume.sh` / `long-run-resume` template edit that implements this lives outside the
plugin repo — a deferred owner-attended follow-up, mirroring the #568 Phase-1 launcher glue;
until it lands, fresh-session mode's pre-launch check degrades to single-session, so nothing
regresses.)

> **Scope of the two sections below: both boundaries now share this machinery** (#927 closed
> #845). They were written for `mid-child-handoff` (#665) alone, and Round-5 High 3 correctly
> fenced them off because the child boundary genuinely had no claim then. It does now: the child
> boundary reuses this generation/claim/lease/ack machinery UNCHANGED and differs only in its
> precondition — mid-child requires exactly one child `in_progress` matching its position record,
> the child boundary requires the opposite (the next child `queued` and nothing in flight). Where
> a paragraph below says "mid-child", read it as "either boundary" unless it names the position
> record or the retirement, which remain mid-child's alone.

**Generation counter (monotonic).** On a `ready` mid-child disposition the driver persists the
handoff via
`driver_lib.open_handoff(state, disposition, now_ts=)`, which bumps the top-level `generation`
counter AND writes `handoff_pending = {generation, next_issue, written_ts}` atomically — the bump
is required so a later handoff can never reuse a generation (a reused generation would let a stale
claim replay).

**Exactly-one successor + takeover-failure detection (lease/ack).** The successor session, under
the launcher's flock singleton, atomically CLAIMS the pending handoff via
`driver_lib.handoff_claim(state, generation, claimant=, now_ts=)` — accepted ONLY when the pending
generation equals the state's current generation (monotonic, non-negative) AND the handoff is
unclaimed OR its prior claim is reclaimable. After rebuilding durable state and STARTING the child,
the successor calls `driver_lib.handoff_ack_started(state, generation, claimant)` to mark the claim
`started`. A claim that never reaches `started` (the successor crashed between claiming and
starting) is RECLAIMABLE once older than the lease (`driver_lib.handoff_reclaimable`, default
1800s) — so a crashed takeover does not strand the run: the launcher's staleness re-fire reclaims
it. A `started` claim is never reclaimed (the takeover succeeded). After a bounded number of
failed re-fires the launcher notifies the owner and stops (a stranded run surfaces instead of
dying silently). A quota pause is not a failure — the next post-reset fire finds the still-valid
handoff (claimed+started → the successor resumes; claimed-unstarted-past-lease → reclaimed).

**Fail-open (never abort).** `driver_lib.fresh_session_available(state, launcher_armed=,
handoff_writable=, fresh_launch_supported=)` is the pre-launch check. **`fresh_launch_supported`
is load-bearing:** an armed launcher is not enough — it must POSITIVELY advertise that it launches
the successor WITHOUT `--resume` (a resume-first launcher would silently reload the prior context
and defeat the fresh boundary). False on any of the three → the driver degrades to the
single-session loop with the visible marker `### epic-run: fresh-session unavailable —
single-session fallback (<reason>)`. Worst case equals today's behavior. (Until the launcher
template advertises fresh-launch support — the deferred follow-up — this check returns False, so
fresh-session mode stays safely inert.)

**Continuity + gates unchanged.** The queue, topo order, merge policy, per-child record, and
decision log are read from `.driver-state` + `epic-<N>-autorun-log.md` by each new session, not
from in-context memory (`validate_driver_state` gate-checks the loaded state). The harness Task
tools are session-scoped, so each session builds its OWN Step-3b task list from `.driver-state`
(no list crosses the boundary). Each child still runs WF2 FRESH to Step 16 with every gate
intact — the driver still never reaches into a WF2 step.

## Mid-child handoff (#665) — the other boundary

Fresh-session-per-child above crosses a **child boundary**: a child reaches a terminal outcome,
the session ends, the successor starts the NEXT child. It does not cover *"I am mid-child, out
of context, hand me over and keep going"* — the trigger there is **context exhaustion, never
cron** (epic #667, owner decision D-16; the 5-hour cron window is a separate service that
resumes the already-active session and needs none of this machinery).

It **reuses the primitives above rather than adding a second mechanism** — that reuse is the
requirement, not an implementation preference. `driver_lib.mid_child_handoff(state, position=)`
returns a disposition shaped so the SAME `open_handoff` consumes it unchanged, and the successor
claims and acks through the same `handoff_claim` / `handoff_ack_started`. `handoff_claim` itself
is deliberately NOT modified: it is #569's tested primitive, and the new cancelled-record refusal
lives in the new callers, which is where the new state was introduced.

**What is added to the durable record.** `handoff_pending` gains an optional `kind` discriminator
and an optional `position` object (ten required fields: `issue`, `step`, `branch`,
`test_baseline`, `predecessor_pane`, `predecessor_session`, `goal_condition`, `project`,
`project_path`, `repo_root`), plus `successor`, `rebuild_receipt`, `cancelled` and
`teardown_phase` as the handoff progresses. When no position is supplied the written shape is
**byte-identical** to what #569 writes, which is the compatibility proof — and
`docs/driver-state/queue.schema.json` carries `additionalProperties: true` at root, issue and
nested level, so the extended keys validate with no schema change.

**`kind` is a CLOSED allowlist, because `handoff_pending` now has two meanings and the
fresh-session launcher reads the same file.** Absent means the legacy child-boundary handoff;
`mid_child` is REFUSED there (a mid-child resume is already in flight, and a second successor
would compete for one generation); any other value — a misspelling, a different case, a
non-string — is refused as unrecognised. An equality test would let `MID_CHILD` or `42` fall
through to the legacy branch.

**One deliberate difference from `fresh_session_handoff`:** `mid_child_handoff` does NOT gate on
`session_mode == "fresh-session"`. A context-driven handover is cron-free, and gating on that
mode would refuse exactly the in-session campaigns it exists to serve. That is why it is a
sibling function rather than a flag — `fresh_session_handoff`'s `single_session` verdict is
load-bearing for #569 and must keep its meaning.

**The successor rebuilds; nothing is copied.** As above, the harness task tools are
session-scoped (the live predecessor on 2026-07-27 held 30 task subjects across three unrelated
projects), so the successor re-derives position from `.driver-state` plus the position record and
builds its own list. Its resume prompt is built by `driver_lib._build_mid_child_resume_prompt`,
next to #569's `_build_resume_prompt` for the same reason: two copies of that wording would
drift.

**Teardown is successor-driven and verified against seven on-disk artifacts**, and on every refusal BEFORE the clear the
predecessor is left alive AND still guarded. After a confirmed clear the honest statement is narrower: a
refusal leaves it alive but possibly unguarded, and a re-armed predecessor is never closed. `.driver-state` writes on this path go through
one locked read-modify-write helper (`plan_lib.file_lock` on a stable sidecar). The full ladder,
the destructive sequence, the named partial-success states, and the one window where a crash
leaves the predecessor unguarded are documented in `docs/runbooks/herdr.md` §8 rather than
duplicated here.

**Known boundary, stated rather than implied solved:** the epic-run skill's prose-driven status
writers do not take that lock. Acceptable for a context-driven handover because the driver and
the predecessor are the same session and its status writes happen at child boundaries, while a
mid-child handoff by definition happens between them — one writer at a time by construction. A
second driver session on one campaign is already outside #569's model. Migrating those writers
onto the locked helper is a filed follow-up.
