# WF14 run-feedback — WF2 #165 (saystory, PR #178) · 2026-07-10 · rubric v1

**Run under assessment:** WF2 implement-feature, saystory issue #165 (shared
parrot-core-dispatch crate + Linux migration), full spine, merged. **Plugin version
loaded:** 3.29.0 (cache `~/.claude/plugins/cache/rawgentic/rawgentic/3.29.0/`);
current main: 3.32.1 — every filed defect re-verified on main. **Record:** explicit
(`projects/saystory/docs/measurements/run_records.jsonl`, issue-165 line),
`validate_record` (main's validator) → valid, empty error list. **Session notes:**
`projects/saystory/claude_docs/session_notes.md` (21 `### WF2 Step … #165` markers
accepted; all keyed per #341 slots). Assessor = the same session that ran the
workflow — evidence is first-party; the RUN's cross-model layer was gpt-5.6-sol
(design/plan/diff adversarial), but this ASSESSMENT is same-model rubric scoring
(WF14 has no cross-model slot; stated per the owner's gpt-5.6-sol directive).

## Dimension scores

1. **Fidelity 4/5** (confirmed). All 12 mandatory markers present + Step 6 ran +
   Step 15 skip condition met (no deployment). Ding: Step 2 item 10
   (probe-parallelism) was NOT executed — notes say "Parallelism: worktree assumed
   per session precedent" — an asserted, unevidenced condition; and the assumption
   was WRONG at dispatch time (see F1).
2. **Gates 5/5** (confirmed). Real pre-implementation catch, finding text quoted in
   notes: the design note claimed captureShortcut wire tokens "pushToTalk"/
   "handsFree"; BOTH the Step-4 self-review (High, 0.8) and the gpt-5.6-sol
   adversarial pass independently caught that the real tokens are
   "pushToTalkShortcut"/"handsFreeShortcut" (shortcut_capture.rs:41-42) — an
   implementer following the note would have broken every captureShortcut RPC with
   green suites (the arm was unpinned). Peer consult also materially reshaped the
   seam (narrow ServiceInitializer over the drafted PlatformHost).
3. **Clarity 3/5** (confirmed instances). Interpreted sentences: (a) Step 4 item 7
   says dispatch `/rawgentic:adversarial-review <design-doc-path>` — the saystory
   CLAUDE.md precedence note sanctions the raw engine instead; harmonizing prose
   would remove the fork. (b) Step 11 defines dead-return (vacuous) detection but
   no hung-dispatch ELAPSED-TIME threshold — the orchestrator invented "17 min =
   dead" and was wrong (the dispatch returned healthy at 22 min). (c) engine
   `--findings-json` must live under project root — discovered via exit-2 error,
   not prose (one wasted dispatch).
4. **Dispatch 3/5** (confirmed). 14 dispatches; all ultimately resolved or visibly
   substituted. Two blemishes: (a) rawgentic-implementer worktree spawn FAILED
   outright (F1) → documented generic fallback used, serial; (b) the A2
   presumed-dead relaunch — the original returned ok at 22 min, the relaunch was
   TaskStopped; one wasted dispatch (~90k subagent tokens), no vacuous result
   trusted.
5. **Telemetry 3/5** (confirmed). Record valid on first summarize; gates[] counts
   match the persisted at-close dedup notes (#340 rule); loop_backs 1/3 matches the
   counters file; outcome/scan/changes match. Negatives: `dispatches[]` carries 13
   entries vs 14 session DISPATCH lines — the A2 episode was mis-emitted by the
   orchestrator (premature `outcome=dead` for a dispatch that later returned ok,
   plus a `retried` line; the #330 rules prescribe ONE line — orchestrator error,
   owned). `usage` is session-cumulative (standing known-limitation).
6. **Cost 3/5** (capped at 3: usage is session-level). Full spine justified (new
   crate = architecture change; lane correctly ineligible). Heaviest-least-value
   step this run: the A2 relaunch (pure waste — caused by the missing
   dead-threshold guidance, F2).

## Findings

**F1 — PLUGIN DEFECT (filed): probe-parallelism measures the wrong repo for
Agent-tool worktree spawning.** Step 2 item 10 probes
`--repo-root "$(git rev-parse --show-toplevel)"` (the PROJECT repo —
`skills/implement-feature/references/steps.md:454` on main), but the Agent tool
creates the `rawgentic:rawgentic-implementer` worktree (frontmatter
`isolation: worktree`, `agents/rawgentic-implementer.md` on main) against the
SESSION primary working directory. In the plugin's flagship layout — a rawgentic
WORKSPACE root that is NOT a git repo containing project repos — the probe returns
`worktree` while every implementer dispatch dies at spawn. Live error this session,
quoted: "Cannot create agent worktree: not in a git repository and no
WorktreeCreate hooks are configured." The documented `serial-only` graceful
fallback never engages because the probe never says serial-only. Confirmed at
3.29.0 (live) and unchanged on main 3.32.1 (both files verified). Impact: silent
loss of worktree isolation + one failed dispatch per run until the orchestrator
improvises the generic fallback (this run did, logged).

**F5 — PLUGIN DEFECT (filed): WF14's cross-project report path collides with
wal-bind-guard.** WF14 SKILL.md path-binding (main, `skills/run-feedback/SKILL.md:60`:
"reports never land in the assessed project's tree") REQUIRES writing under
`projects/rawgentic/docs/reviews/` from a session bound to the assessed project;
`hooks/wal-bind-guard` (main) denies exactly that write ("File path belongs to a
different project than the bound one" — PreToolUse deny observed live this
session). Two plugin components mandate contradictory behavior; the only
non-bypass resolution is a temporary registry re-bind (used here, then reverted),
which no prose documents. Confirmed at 3.29.0 (live deny) and both surfaces
present on main 3.32.1.

**F2 — PLUGIN FRICTION (memorized, not filed): no hung-dispatch deadline for
reviewer agents.** Step 11 item 2 / Step 8a item 7 define vacuous-return and
error-retry handling, but no elapsed-time threshold for a silent
healthy-but-slow dispatch. Gap sentence (Step 11 item 2): "A reviewer return that
is vacuous (no findings AND no substantive content) is a DEAD dispatch" — nothing
addresses not-yet-returned. Observed cost: one wasted opus relaunch killed after
the original returned at 22 min. Measured this session: opus reviewers running
cargo gates take 12–22 min.

**F3 — ORCHESTRATOR ERROR (owned, no routing): premature `outcome=dead` DISPATCH
line** for A2 (returned ok later) — the #330 single-line retry rule misapplied;
the record's `dispatches[]` inherited the distortion (13 vs 14 lines). The prose
did not invite the misread; no plugin feedback.

**F4 — WORKING AS DESIGNED: adversarial design review raced the peer-consult
synthesis** (reviewed the pre-synthesis draft; its findings against the superseded
PlatformHost section arrived pre-resolved). The Step-4 concurrency tradeoff note
covers the loop-back case; the peer-synthesis race is an adjacent bounded cost of
the same accepted overlap. No action.

## Telemetry audit

| field | recorded | session evidence | verdict | impact | routing |
|---|---|---|---|---|---|
| workflow/version/issue | implement-feature / 3.29.0 / 165 | cache path + markers | match | — | — |
| changes | 12 files, +1614/−493, 5 commits | diff --name-only (12 paths) + commit log | match | — | — |
| tests | 32 added, 339/339 | runner final 339/0; 38 crate tests = 32 authored + 6 relocated | match (relocated ≠ added, judgment noted) | — | — |
| gates[] | 10/10 · 5/5 · 1/1 · 0/0 · 6/6 | at-close dedup notes per #340 | match | — | — |
| security_scan | ran, skipped=[iac] | scan output quoted | match | — | — |
| loop_backs | 1/3 | counters file spec_tighten=1 total=1 | match | — | — |
| outcome | PR 178, merged, ci passed | merge sha + checks output | match | — | — |
| usage | session-cumulative | usage_capture semantics | known-limitation | cost trend only | standing (#155 note) |
| reviewer_kind | inline / hand_rolled_multi | #340 gate-defining precedence | match | — | — |
| dispatches[] | 13 entries | 14 `^DISPATCH issue=165` lines; A2 over-emission | mismatch (minor) | audit fidelity | orchestrator error owned; rules already prohibit it — nothing filed, reason stated |

Verdict counts: match 8 · mismatch 1 · known-limitation 1.

## Routing

- Filed: 2 plugin defects (F1, F5) — links in summary block; dup-checked first.
- Telemetry lane: nothing filed — the one mismatch is an owned orchestrator
  mis-emission the #330 rules already prohibit; dropped with that reason.
- Friction: ONE mempalace memory (F2), project rawgentic.
- Not filed (cap): 0.

Inferred (unconfirmed) claims: none — assessor ran the run; evidence first-party.
