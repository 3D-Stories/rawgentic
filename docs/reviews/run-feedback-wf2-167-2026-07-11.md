# WF14 run-feedback — WF2 #167 (saystory P3 adapter, PRs #182+#183) · rubric v2

## At a glance
**Verdict: the machinery handled its hardest run yet — first loop-back cap hit, first decompose-band split, owner pause/resume mid-run — and every gate caught something real, but adversarial-pass re-litigation and four 40–60-min CI fix loops (two preventable) made this the most expensive WF2 run on record.**

- Fidelity **4/5** — all mandatory markers across BOTH PRs; owner-pause handled via the documented blocker protocol; one deduction: the plan's Task-1 file list under-enumerated (adapters.rs edit was in-scope but unlisted; reviewer-flagged).
- Gates **4/5** — real catches at every tier (below); deduction: design passes 2–3 spent budget re-litigating owner-settled dispositions.
- Clarity **3/5** — executable throughout, but ≥2 areas needed interpretation (quoted): the #340 merged-gate `reviewer_kind` rule ("the gate-DEFINING mechanism") applied to a rubric-self-review + adversarial gate; and Step 16's "it appends the record" vs the orchestrator's manual-append instinct.
- Dispatch **3/5** — 32 dispatches; 8a-T4's BOTH reviewers died vacuous on the account session limit and were re-dispatched only after owner /login; substitution visible + logged (`outcome=dead` ×2, `outcome=retried`), matching anchor 3.
- Telemetry **3/5** — `dispatches[]` 32=32 exact; deductions: `changes` was persisted from estimates then corrected post-persist (honest but out-of-band), usage `unavailable`.
- Cost **2/5** — 33 design findings over 3 full adversarial passes (pass-3 adversarial re-raised a decline it was explicitly told was settled); 4 mac-lane CI loops ≈ 3h wall, of which loops 2–3 were preventable by pre-push whole-surface sweeps (owner said so mid-run; the corrected process then proved loop-4's fix locally 30/30 before green). Heaviest-least-value step: adversarial design pass 3.

**Best catch:** design pass-1 self-review F3 — the design's `NSSound(named:)` would have silently played nothing; the shipping `SoundFeedback` path was reused instead (file:line-cited catch). Runner-up: PR-B adversarial's fail-closed paste-target pid (garbled id would have pasted into the wrong app).
**Worst friction:** adversarial passes receive no prior-pass disposition ledger — re-litigation consumed loop-back budget twice (also observed in the #69 run: "adversarial diff review lacks prior-gate dispositions → re-litigated the resolved coarse-id finding", session notes, 2 runs total).
**Routed:** 3 issues filed (see routing), 1 friction memory, worktree evidence commented on #371.

## Evidence ledger (summary)
- Run-record: jsonl tail (schema-valid, summarize rc=0; `changes` corrected post-persist to real diff numbers — flagged in audit) — **confirmed**.
- Markers: full spine ×2 PRs quoted in `projects/saystory/claude_docs/session_notes.md`; loop-back counters file `claude_docs/.wf2-state/167/loopback_counters.json` = `{design:2, spec_tighten:1, total:3}` — **confirmed**.
- Owner decisions (breaker r1/r2, escalation, 2-PR split, pause, process correction) — **confirmed** (session notes, D-numbered decision log in the run handoff).
- Plugin version loaded: **3.32.2**; main at assessment: **3.35.0**. Defects below verified conceptually against 3.35.0 skill text (no disposition-ledger mechanism; no reviewer-liveness protocol; no authored-blind pre-push checklist).
- Worktree isolation: 4/4 implementer dispatches ran isolated and merged clean — **confirmed**, CONTRADICTS open #371's "every implementer dispatch dies at spawn in workspace layout".

## Telemetry audit
| field | recorded | session evidence | verdict | impact | routing |
|---|---|---|---|---|---|
| tests | 49 added, 389/389 | ws final output 389/0 (baseline 365/0) | match | — | — |
| gates[] | 4:33/33, 8a:6/6, 11:17/17 | markers: 11+13+9 design; 8a 0+0+1M4L+5L→6 unique above-band… counted per #340 unique-across-passes | match (within #340 counting) | — | — |
| loop_backs | 3/3 | counters file total=3 | match | — | — |
| changes | 63 files +4274/−646, 13 commits | initially persisted as ESTIMATES (68/5800/600), corrected post-persist from real `git diff --shortstat` | mismatch (transient; self-corrected) | record briefly wrong on disk | pooled into telemetry note (not filed — cap) |
| outcome | PR #183 merged ci passed | merge → 2ce0078, 4/4 lanes | match | — | — |
| usage | unavailable | session re-logged mid-run | known-limitation | — | standing weak spot |
| dispatches[] | 32 (incl 2 dead + 1 retried) | 32 `^DISPATCH issue=167` lines incl the dead pair | match | — | — |
| reviewer_kind (4/8a/11) | hand_rolled_multi | multi-reviewer + adversarial layers | match | — | — |

## Findings
1. **PLUGIN DEFECT — no disposition ledger across adversarial passes.** Pass-2 adversarial re-attacked the owner-settled unbounded-FIFO decision; pass-3 re-raised the settled entitlements-gate decline despite an explicit settled-scope instruction in its brief. Cost: breaker round-trips + loop-back budget. Recurrence: 2 runs (this + #69's memorized identical class). FILED.
2. **PLUGIN DEFECT — no reviewer-liveness/vacuous-result protocol.** Both 8a-T4 reviewers died on the account session limit; detection + re-dispatch relied on orchestrator memory of the vacuous-result pattern, not skill guidance. Recurrence: "hit your session limit" appears in 36 distinct sessions (index query, literal). FILED.
3. **PLUGIN FRICTION — deferral contract lacks an authored-blind pre-push proxy checklist.** CI loops 2 (uniffi `*Impl` decl collision — a bindings SIGNATURE diff had been done but not a DECL-NAME sweep) and 3 (5 test files missing imports — sources swept exhaustively, tests sampled) were both catchable offline; loop 4's fix was then proven locally (rust-harness 30/30) under the corrected process. Triggered checklist (no-target-toolchain / generated-interfaces / deferred-lane) per consult risk note — never a blanket mandate. FILED (feat).
4. **ORCHESTRATOR ERROR** (owned) — estimates persisted into `changes`; duplicate-append instinct on #166; bindings check stopped at signatures. The prose invited none of these.
5. **ENVIRONMENT** — account session limit killed the T4 reviewers; owner /login restored.
6. **WORKING AS DESIGNED** — loop-back cap escalation to owner; decompose-band 2-PR split; preemptable-shutdown design catching the parked-callback class before it shipped; worktree isolation (evidence against #371).

## Routing
- Filed (cap 3, shared pool): rawgentic#393 (disposition ledger, defect), rawgentic#394 (reviewer liveness/vacuous protocol, defect), rawgentic#395 (authored-blind pre-push checklist, friction→feat).
- Telemetry improvements not filed (cap): merged-gate reviewer_kind guidance example; changes-from-estimates guard (summarize could recompute `changes` from `git diff` itself — noted for #333's queue).
- Friction memory: SAVED + verified — drawer_rawgentic_process-conventions_afffd44a377ef2ec8ccd9efd.
- #371: contradicting evidence commented (issuecomment-4941285577), not refiled.

## Fixed output block
```
WF RUN ASSESSMENT — WF2 @ v3.32.2 (issue #167, PRs #182+#183, lane full ×2) · rubric v2
Fidelity 4/5 · Gates 4/5 · Clarity 3/5 · Dispatch 3/5 · Telemetry 3/5 · Cost 2/5
Mode: full · Record: docs/measurements/run_records.jsonl (tail)
Best catch: design F3 — NSSound(named:) would have silently played nothing; shipping SoundFeedback reused
Worst friction: adversarial passes carry no prior-pass disposition ledger (re-litigation ×2 runs)
Telemetry verdicts: match 6 / mismatch 1 / missing-in-record 0 / missing-in-session 0 / unverifiable 0 / known-limitation 1
Defects filed: rawgentic#393 (disposition ledger) #394 (reviewer liveness) #395 (authored-blind checklist) | Telemetry filed: none (cap) | Not filed (cap): 2 | Friction memorized: drawer_rawgentic_process-conventions_afffd44a | Clean run: no
Report: docs/reviews/run-feedback-wf2-167-2026-07-11.md · Artifact: https://claude.ai/code/artifact/fdb2ab72-0bad-464c-8bc0-c5e063c87156
Inferred (unconfirmed) claims: defect presence on main v3.35.0 verified against skill text, not a live 3.35.0 run
```
