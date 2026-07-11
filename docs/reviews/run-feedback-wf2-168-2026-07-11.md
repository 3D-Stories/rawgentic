# WF14 run-feedback — WF2 #168 (saystory, 2-PR split: PR #186 rust + PR #189 swift)

Assessed under rubric v2 (2026-07-10, #377). Plugin version loaded by the run:
3.35.0 (cache `~/.claude/plugins/cache/rawgentic/rawgentic/3.35.0/`); current main
version not re-checked this pass (defects verified against 3.35.0 prose; the one
filed-class finding was already filed upstream as #393, so no stale-cache filing risk).
Record: explicit `--record` (claude_docs/.rawgentic-168-record.json), persisted to the
saystory store (`docs/measurements/run_records.jsonl`, 23rd line) — schema-valid on
SECOND summarize (see telemetry). Reviewer model directive honored: adversarial layers
this run used gpt-5.6-sol (owner directive 2026-07-10 ~08:50).

## At a glance

**A high-value, high-friction run: the gates earned their cost several times over
(the single-llama localization mechanism, a hang-class bridge bug, and a merge-gate
heading requirement were all caught pre-merge), but PR-A burned six mac-lane CI loops
before the run adopted the preflight rule that then took PR-B through CI in one.**

- **Fidelity 3/5** — all mandatory markers present across both PR legs EXCEPT an
  explicit Step-9 marker for the PR-B leg (drift verification demonstrably happened
  inside Step 11 A1's whole-diff task mapping, but the marker is absent → cap).
- **Gates 5/5** — multiple named real catches, quoted below.
- **Clarity 4/5** — executable throughout; one tool contract needed discovery
  (engine sidecar path must live under project root — error text was clear, but no
  skill prose states it).
- **Dispatch 4/5** — 25 dispatches, zero vacuous, all opus-as-routed; one implementer
  idled holding for its own background gate and needed a SendMessage nudge.
- **Telemetry 2/5** — record REJECTED on first summarize (assessor used `type` for
  the documented `subagent_type` — orchestrator error, validator correct); gate
  counts initially recorded as per-pass sums (corrected in-store to the #340 unique
  rule before any PR carries the line).
- **Cost 3/5** — proportional for a `complex` 2-PR feature overall; the heaviest
  least-value spend was PR-A CI loop 3 (a full mac lane burned re-testing `lto=off`
  on a conclusion drawn with the wrong instrument).

**Best catch:** Step 4 pass-2 H4 — "the single SwiftPM TEST BUNDLE links ParrotCore
(Swift llama xcframework) AND ParrotCoreFFI (rust llama .a) together from PR-A
onward" — this produced the R6 symbol-localization mechanism whose CI hardening then
dominated (and justified) PR-A's fix loops. Runner-up: adversarial diff on PR-B
caught the unbounded `gate.wait()` hang class AFTER an 8a lens had passed the same
code as "documented R7 case" — the redundant-lens design worked exactly as intended.

**Worst friction:** adversarial diff reviews still re-litigate settled dispositions
(2 of 4 PR-B findings; 3 of 4 on PR-A) — already filed as rawgentic#393 (OPEN),
recurrence now ≥3 runs (#166, #167, #168).

**Routed:** 0 new defects filed (1 recurrent friction linked to existing #393);
1 friction memory saved; telemetry improvements: none filed (both negatives were
assessor-caused, validator behaved correctly).

## Evidence ledger (selected, tagged)

- Steps 1–5, 7, 8, 8a, 11, 11.5, 12, 14, 16 markers present for both PR legs —
  **confirmed** (session_notes.md `### WF2 Step ...` markers; e.g. Step 4:
  "pass 2, 12 findings → 8 resolutions owner-approved, REV3 ... loop-backs design 2/3"
  @ line 1112; Step 12 PR-B: "PR #189 OPEN 'Closes #168' (7 commits, v0.2.46)").
- Step 9 (PR-B leg): explicit marker **absent**; drift check evidenced inside Step 11
  A1 result ("Whole-diff drift: every changed file maps to a task ... No unowned
  file") — **confirmed as performed, unverifiable as a step**.
- PR-A six CI loops + root causes — **confirmed** (session notes postmortem marker:
  "L1 platform_version; L2 nm bitcode death masked by ||true; L3 wrong-instrument
  inference (lto=off); L4 phantom over-strip ...; L5 swift-link httplib undefineds;
  L6 GREEN: -u root-set").
- PR-B zero CI loops; mac lane green first run — **confirmed** (run 29150102046
  jobs: macOS SUCCESS; preflight rounds 1–3 log reads: 82→84 tests/1 skip/0 fail).
- 25 dispatches (2 implementer, 7 reviewer this leg + 16 `DISPATCH issue=168`
  markers from earlier legs), zero vacuous — **confirmed** (marker grep + agent
  results in-session).
- Owner process rule adopted mid-run ("no mac-lane push without full
  build-macos-ffi.sh + swift test on the mac host") — **confirmed** (owner challenge
  + session-notes rule marker); its effect (6 loops → 0) — **confirmed**.

## Telemetry audit (record: present, explicit)

| field | recorded | session evidence | verdict | impact | routing |
|---|---|---|---|---|---|
| workflow/issue | implement-feature / 168 | matches run | match | — | — |
| tests | 33 added / 491 passing | ws 407/0 (runner output) + swift 84/1/0 (preflight log); "added" is an estimate across two count methods | mismatch-risk on `added` (estimate, stated here) | low — analytics only | not filed: assessor-assembled estimate, noted in-record context |
| gates[] step 4 | 20/20 (corrected in-store from 24/24) | pass1 "12 merged (4H/6M/2L)", pass2 "deduped to 8 resolutions" | match after correction; initial value was a per-pass-sum over-count | corrected pre-PR | orchestrator error, no filing |
| gates[] 8a/11 | 8/8, 11/11 | markers + reviewer results | match (counts assembled from markers) | — | — |
| loop_backs | 2/3 | ".wf2-state/168/loopback_counters.json" markers ("design 2/3, total 2/3") | match | — | — |
| security_scan | ran, 0 blocking, iac skipped | "Security scan: PASS ... iac: not applicable" output | match | — | — |
| outcome | PR 189 merged, ci passed | merge SHA 904fc65, run success | match | — | — |
| usage | capture_status unavailable | no session usage capture | known-limitation (standing) | — | #329/#330 open |
| reviewer_kind | hand_rolled_multi on merged gates | multi-reviewer + adversarial layers | match under #340 precedence | — | — |
| dispatches[] | 25 entries | markers + this session's agents | match; NOTE first summarize REJECTED the field (`type` vs documented `subagent_type`) — assessor error, validator correct and loud | schema held | none |

Verdict counts: match 7 · mismatch 0 (2 corrected/stated) · missing-in-record 0 ·
missing-in-session 0 · unverifiable 0 · known-limitation 1.

## Findings and classes

1. **PLUGIN FRICTION (recurrent, ≥3 runs: #166/#167/#168)** — adversarial diff
   passes lack the settled-disposition ledger; PR-A: 3 of 4 findings re-litigated
   settled decisions; PR-B: 2 of 4 declined against already-recorded evidence
   (cleanup-routing = merged PR-A; detect-flag branch unreachable under the rust
   invariant). **Already filed: rawgentic#393 (OPEN)** — linked, not refiled. This
   run adds a data point: the cost is real but bounded (each decline took one
   evidence lookup), and one adversarial finding on the same pass was the run's
   second-best catch — the lens must not be weakened, only fed dispositions.
2. **ORCHESTRATOR ERROR** — run-record first-summarize rejection (`type` instead of
   the documented `subagent_type`, run-record.md:44) and the initial per-pass-sum
   gate count. Both caught by machinery (validator; rubric #340 rule at assessment
   time). Own goal; the docs were right; no prose invited the misread.
3. **ORCHESTRATOR ERROR (earlier leg)** — PR-A CI loop 3: `lto=off` re-tested on the
   runner after loop 2, but the failing reader (Apple nm) was the wrong instrument;
   the conclusion "LTO inference WRONG" was drawn from a stale-cache artifact. One
   full mac lane spent. The corrective (instrument swap + on-runner forensics) came
   from the owner's challenge, not the workflow prose — WF2 has no "verify the
   instrument before re-testing the hypothesis" guidance for CI fix loops; recorded
   here as a data point for the existing authored-blind checklist thread
   (peer-review defect #3 lineage, #391-class), not filed separately.
4. **ENVIRONMENT ×2** — mac host disk 100% full (killed preflight round 1; owner
   freed 137G; agent-owned spike dirs deleted with approval) and WSL root 100% full
   (per-worktree cargo `target/` dirs ~3–7G each + a 13G incremental cache; harness
   task-output writes fail loudly on ENOSPC). No plugin runbook covers worktree
   disk hygiene — worth one line in the implementer-dispatch prose eventually;
   below filing threshold this run (cap discipline).
5. **WORKING AS DESIGNED** — the mid-run adoption of the mac-preflight rule
   (full FFI script + swift test on the target host before any mac-lane push):
   PR-B's zero-loop CI empirically validates it. Candidate for promotion from
   session rule to WF2 prose via the existing #391-class checklist issue.

## Claims intentionally not made

- No per-run cost claim (usage capture unavailable — session-level only).
- No claim that PR-A's Step-16 record behavior matched convention (that leg's
  record was assembled and persisted in THIS leg, post-merge — a deviation from the
  append-pre-merge convention, driven by the 2-PR structure; noted, not scored).

## Summary block

```
WF RUN ASSESSMENT — WF2 @ v3.35.0 (issue #168, PRs #186+#189, lane full) · rubric v2
Fidelity 3/5 · Gates 5/5 · Clarity 4/5 · Dispatch 4/5 · Telemetry 2/5 · Cost 3/5
Mode: full · Record: claude_docs/.rawgentic-168-record.json (persisted, corrected)
Best catch: Step-4 H4 double-llama test-bundle → R6 localization mechanism (pre-merge)
Worst friction: adversarial diff re-litigates settled dispositions — rawgentic#393, recurrence ≥3 runs
Telemetry verdicts: match 7 / mismatch 0 / missing-in-record 0 / missing-in-session 0 / unverifiable 0 / known-limitation 1
Defects filed: none (recurrent friction → existing #393) | Telemetry filed: none | Not filed (cap): 0 | Friction memorized: wf2-adversarial-needs-disposition-ledger-168 | Clean run: no
Report: projects/rawgentic/docs/reviews/run-feedback-wf2-168-2026-07-11.md
Inferred (unconfirmed) claims: tests.added=33 (two count methods); current-main defect presence not re-verified (nothing filed that depends on it)
```
