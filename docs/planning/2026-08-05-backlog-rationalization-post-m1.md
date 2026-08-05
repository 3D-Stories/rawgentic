**Date:** 2026-08-05 · **Author:** Fable 5, with gpt-5.6-sol as consultant (runner `consult` verb, repo-verified)
**Follows:** the 2026-08-03 rationalization roadmap (`2026-08-03-756-rationalization-roadmap.md`). M0 and M1 are merged; this doc dispositions everything that remains.

Of the 116 open issues, **46 closed on evidence** (owner rulings that were never executed, work that already shipped, subjects the M0 retreat deleted, superseded tracking epics, and six gray-zone calls the owner approved), **1 was transferred** to the repo its code moved to after a retest, **3 duplicates folded into survivors**, and **69 stay open** — reordered into seven milestones plus a LATER shelf, most impactful first. This document was written as the evidence table BEFORE execution and, after owner approval, updated into the execution record — §8 lists exactly what ran and the one correction made along the way.

```chips
116 open issues, all dispositioned | note
46 closed, with citing comments | ok
1 transferred after retest (#670) | note
3 duplicates folded into survivors | note
69 stay open across 7 milestones | note
3 decision tickets filed (#931-#933) | warn
```

```callout
info | How to read this
Every row names its evidence. "Ruled" = you already decided it (D174/D178/roadmap §3) and the closure was simply never executed. "Verified" = I checked the code on main (ce9eba67) today. A closed issue is one click to reopen — nothing here is destructive.
```

## 1. Where we are

M0 (the executor retreat, PRs #867/#868/#870/#872) and M1 (prose ceilings + lite lane: #856, #761, #822, epic #875) are **merged, verified via the GitHub API today**. The roadmap's §3 issue cleanup, however, was only partly executed: 14 of the 28 ruled closures happened; the rest are still open. On top of that, the retreat deleted the subject matter of ~17 older issues nobody re-triaged, and five things shipped without their issues being closed. That is most of what "close today" is.

M2 epic **#906** exists and is sound. One update: its item 1 listed #731+#800+#835, but **#800 was fixed and closed by PR #912** — item 1 is now #731+#835 only.

## 2. The 40 closures, with evidence

### Group 1 — you already ruled these (7)

| Issue | Ruling | Note |
|---|---|---|
| #749 | D178 kill | headless mode itself was deleted in M0d — the subject no longer exists |
| #760 | D178 kill | stop-hook circuit breaker, killed by name |
| #763 | D178 kill | its one surviving residual (visible both-projects confirmation) already rides #906 item 4 |
| #764 | D178 kill | different-model done-verifier, killed by name |
| #775 | D174 retreat | Step-3 bake-off hardening; the bake-off is gone (zero matches in skills/ or hooks/) |
| #815 | roadmap §7 | closed-parked with #777 "until a producer boundary exists again" — the closing comment will carry that reopen trigger verbatim |
| #861 | D174/M0d | the `--gate-file` binding it reports left with `complexity_gate` (plan_lib.py:3271 records the removal) |

### Group 2 — shipped or superseded, verified in code (5)

| Issue | Verified evidence |
|---|---|
| #855 | the reopen-token choke point IS the M0a runner: `review_runner.py` requires `--reopen-token`, minted by `plan_lib review-reopen` |
| #793 | `NOISE_STRIP_PATHS` + refuse-oversize + bounded truncation retry live at `review_runner.py:59-63,116` |
| #357 | the runner always pins an explicit `-m` (review_runner.py:327-339); model choice is per-dispatch prose now; codex CLI 0.146.0 installed |
| #654 | the spike's ask ("nothing can see context fullness") shipped: `context_meter.py` + the #687 trigger (closed). The unattended-restart remainder is named into #871 by comment |
| #362 | superseded by #874's step-local files — marker discipline now travels inside each step file. Refile with fresh post-M1 evidence if drops recur |

### Group 3 — the M0 retreat deleted their subject (17)

| Issues | Deleted subject |
|---|---|
| #616 #617 #618 #619 #620 #621 | HD2 "herdr executor runtime" epic + children — `phase_executor/` is gone |
| #624 #681 | driver-bench cell / driver-bench anomaly — `driver_bench_lib` deleted (#681 additionally: severity-low, never reproduced) |
| #484 | bake-off candidate config — bake-off retired |
| #549 | per-seat response-token budgets — seats deleted |
| #659 | executor AC1 herdr-drift gate — gone; `herdr-pin.json` is self-authoritative and test-guarded now |
| #666 #671 | build-seat impossibility issues — the build seat is deleted |
| #371 | implementer worktree spawn collision — the `rawgentic-implementer` agent is deleted; `probe-parallelism` is no longer called by any prose |
| #661 | `park_and_reset` at the design loop-back seam — the primitive itself no longer exists in hooks/ |
| #450 | ultracode interop as written targets the deleted model-routing + Agent-tool dispatch; the idea moves to the LATER shelf with a trigger criterion |
| #599 | executor-economics epic — every child above |

### Group 4 — tracking epics the plan supersedes (8)

Per gpt-5.6-sol's condition: each closes **only with every live child's destination named in the closing comment**. Destinations below.

| Epic | Live children → new home |
|---|---|
| #590 telemetry | #355→fold #888 · #356, #363→M2.5 · #361→M6 · #588→fold #888 |
| #595 autonomy-safety | #364, #370, #379→M5 · #365→M3 · #362, #371 close above |
| #596 concurrency | #345, #346, #593, #594, #372→M5 |
| #597 unattended | #380→M6 · #586→M4 · #568→LATER (rescoped) |
| #598 skill hygiene | #350, #390, #391, #399, #400, #534, #536, #537→M7 |
| #685 UAT backlog | #390→M7 · #670→transfer · #654, #659, #666(#671), #681 close above · #680→gray zone |
| #722 context diet | #738→LATER (parked by its own text) · #745→gray zone |
| #756 the origin epic | closes with a final honest summary; #906 + this plan are the tracking surface (roadmap §8 sanctioned this) |

### Merges — true duplicates fold into survivors (3)

| Folds | Into | Why one issue |
|---|---|---|
| #660 | **#363** | the same defect (run-record usage is session-cumulative), two evidence sets; one attribution fix satisfies both |
| #588, #355 | **#888** | one transactional persistence contract: cannot-be-dropped (#588), cannot-be-duplicated (#355), codified pre-merge path (#888) |

## 3. The six gray-zone calls

My recommendation is **close all six**; gpt-5.6-sol dissents on two (marked).

| Issue | Recommendation | The call |
|---|---|---|
| #622 | close | plugin-native herdr skill — a user-level copy exists at `~/.claude/skills/herdr` (HERDR_ENV-gated). Codex dissent: a user-local install isn't a product decision; closing must SAY it's a non-product decision, which the comment will |
| #745 | close | "review rawgentic's own CLAUDE.md" — the 2026-08-04 doctor-plus audit + #908 (§5 trim, cross-tier dedup) did this work; the foreign-project names its body flags (kukakuka, sentinel) are already gone from the manual. Codex dissent: map each AC before closing |
| #623 | close | stall-triage console recipes — tiny docs chore from the dissolved console epic; goes to the LATER shelf as prose |
| #625 | close | dashboard polish placeholder whose ACs literally read "TBD" |
| #626 | close | herdr Phase C epic — every child is done, dead, or closing (#622, #623, #624, #625) |
| #680 | close | herdr detection-manifest bug reported against an old herdr; we now pin 0.8.0, and the defect belongs upstream (herdrdev/herdr). Comment: retest on 0.8.0, file upstream if it persists |

## 4. The transfer — and the correction it took

**#670** (render_artifact.py mangles inline links/rules/wrapped list items): that script left this repo in #807; the render engine lives in the **claude-skills** repo's design-doc-publish add-on.

**Outcome (corrected during review):** a first probe checked links, `---` rules and code-span pairing — all fixed by claude-skills#16 — and the issue was closed as fixed-in-successor. The cross-model diff review then flagged fragmented lists in this PR's own rendered HTML; a sharper probe confirmed **the wrapped-list-item defect (the issue's "damaging one") persists** in the successor engine, for `-` bullets and numbered lists alike. The record was corrected on the issue, and it was **reopened and transferred** to 3D-Stories/claude-skills, where its code lives — the owner's originally-preferred disposition, now with its relevance proven.

## 5. What stays open — M2 and beyond, most impactful first

Ordering logic (mine + codex's two corrections, adopted): **evals baseline early** (cheapest guard over the whole skill surface), **a minimum telemetry-truth layer before away mode** (unattended runs you cannot reconstruct are worse than none), and **away mode as a bounded slice, not a wholesale deferral**.

### M2 — the pane-handoff chain, made boring (epic #906, order unchanged) + spillover

| Item | Issues | Why first |
|---|---|---|
| Quick wins, do-anytime | #916 (redeploy stale UAT page) · **#928** (wire our 9 existing `evals.json` files into Anthropic's now-shipped skill-creator eval harness — trigger regressions are silent today) | both cheap; #928 guards every later skill edit |
| 1. Launcher robustness | #731 #835 (#800 shipped via PR #912) | the observed flakiness lives here |
| 2. Meter rework (D177) | #797 · #729-residual (ack-tracking) · #734 | two-threshold pane-handoff driving |
| 3. In-flight-work gate | #726 | refuse handoff over live background work + durable-path check |
| 4. Epic-run rework (D176) | #927 (fresh-session default) · #769 (boundary sweep) · explicit close-or-fold owed to #845 #846 #848 #849 #850 #851 | children continue via pane-handoff; receipts retire |
| 5. Trusted goal reader | #864 #772 #878 | one origin-bound reader behind both destructive paths |
| 6. Goal cap | #806 | cap from the constant + exact-text display |
| M1 spillover | #923 (WF2-lite lane) · #899 (word budget) | #923 unlocks cheap disposable-class work |

### M2.5 — minimum telemetry truth (gates away mode)

| Issues | What it buys |
|---|---|
| #888 (+#588 +#355 folded) | run-records that land exactly once, even when the run merges its own PR |
| #363 (+#660 folded) | per-run usage attribution instead of session-cumulative guesses |
| #356 | dispatch entries that survive session seams |

### M3 — trust the new machinery (runner + review loop + config)

Kept as **linked siblings, not merged** (codex: different trust boundaries need independent tests): #876 (trusted `--brief`) · #894 (death/OOM evidence) · #365 (contaminated reviewer returns) · #893 (Step 8a engagement evidence) · #891 (retire fail-open riskLevel default) · #895 (blindness guard) · #889 (reopen-token refund) · #892 (re-litigation flag) · #884 + #883 (config-surface pair) · plus roadmap M3: #860 (consult-on-exhaustion) · #808 (WF3 budget-exhausted close) · #750 (registry append helper) · #759 (deferral registry, lite).

### M4 — away mode, as a bounded slice

#871 (the epic; a narrow first slice with explicit duration/cancel/recovery limits) + #586 (resume launcher survives /clear). Gated on M2 reliability + M2.5 records, per consult.

### M5 — project identity & concurrency + WF hygiene

#345 + #346 (one keying design, two migration tickets) · #593 + #594 (grouped siblings: canonical notes home; auto-worktree on second bind) · #372 (WF14 vs wal-bind-guard) · #364 (doc-only resume state) · #370 + #379 (ambiguity-breaker pair) · #395 (authored-blind checklist) · #658 (call-site inventory) · #890 (consumer-project hook invocation — unblocked now #874 shipped).

### M6 — telemetry & hygiene tail

#361 (Step-16 cross-checks) · #380 (wal-context internal deadline).

### M7 — skills & tooling

#400 (WF17 usage auditor — pairs conceptually with #928) · #390 (workspace-doctor) · #391 (session-index v2) · #534 (retire epic-run-analysis — actionable now #508 shipped) · #536 (deploy-verify skill) · #537 (security-vet skill) · #399 (runner-group inference) · #350 (CodeGuard rules).

### LATER — with reopen triggers, not vibes

#792 (quota preflight — still statusline-based; **no programmatic usage API exists**, verified) · #358 #359 #360 (WF15/16 codex design workflows) · #738 (parked by its own text until projects reactivate) · #568 (Hermes — phases 1–2 merged, offload seat died with the executor; keep, rescoped to the surviving bridge/policy substrate, comment says so).

## 6. What the external research changed (verified today)

| Finding | Effect |
|---|---|
| **Anthropic shipped a skill-evals harness** (skill-creator 2.0, official blog 2026-03-03 + code.claude.com docs): `evals/evals.json` schema — which our 9 eval files already match — plus benchmark mode and trigger/description tuning, CI-integrable | #928 shrinks from "build a harness" to "wire ours in"; moved to the front of the queue |
| Claude Code **native Bash sandboxing** shipped (`/sandbox`); **sandbox-runtime** shipped (npm, wraps arbitrary CLIs); **prompt/agent-type hooks** shipped (LLM-judged Stop gates, official hooks docs) | three watch-list triggers fired → three decision tickets proposed (owner call, D179) |
| Subagent silent-death (claude-code#47936) still **open, unfixed** | the artifact-gate machinery stays; no simplification available |
| No programmatic usage/quota API (only `/usage` + OTel export) | #792 stays statusline-based, stays LATER |
| Codex CLI: `UserPromptSubmit` hook exists, PostToolUse "soon"; `--output-schema` mature incl. resume | runner architecture unaffected; watch item stays |

## 7. Consult provenance

gpt-5.6-sol reviewed the full disposition table via `review_runner.py consult` (result JSON in this session's `docs/reviews/` sink, gitignored by design). Adopted from its verdict: don't collapse the sibling pairs (#593/#594, #345/#346, #370/#379, #876/#894/#365, #884/#883, #889/#892) — group, don't merge; #928 to the earliest tranche; minimum telemetry before away mode; away mode as a bounded slice; every epic closure names child destinations; #815/#450 closures carry machine-searchable reopen triggers; #568 kept-rescoped rather than closed. Its two gray-zone dissents (#622, #745) are shown in §3 rather than resolved silently.

## 8. Execution record (2026-08-05, owner-approved)

This section replaced the pre-execution "what has NOT been done" note once the owner answered the four questions (all approved; #670 routed to "verify relevance first").

- **46 issues closed** with citing comments; **3 duplicates folded** (#660→#363, #588+#355→#888, absorption comments posted on the survivors); **5 survivor comments** posted (#871, #363, #888, #568-rescope, #906-amendments); **3 decision tickets filed** (#931 sandboxing, #932 LLM-judged hooks, #933 sandbox-runtime). Open issues: **116 → 72**.
- **One correction, made in the open:** #670 was first closed as fixed-in-successor on a probe that checked links/rules/code-spans. The cross-model diff review of this very PR surfaced fragmented lists; the sharper re-probe proved the wrapped-list-item defect persists in the claude-skills engine. The closing comment was corrected, the issue reopened and **transferred to 3D-Stories/claude-skills**. No other disposition changed.
- No issue **bodies** were edited anywhere — all context rides comments, per convention.
- The roadmap doc was updated (phases block, M2 amendments, new M2.5–M7 sections, §6 statuses, §11) and redeployed to its existing URL; this doc and the roadmap ship together in one docs PR.

```provenance
Code state | main @ ce9eba67, checked 2026-08-05
Issue states | GitHub API, live 2026-08-05
Cross-model consult | review_runner.py consult, gpt-5.6-sol, result in docs/reviews/ (gitignored sink)
External docs | code.claude.com docs + claude.com/blog (skill-creator 2.0) + anthropics/skills schema + exa cross-check
Author | Claude Fable 5, session e6a60533
```
