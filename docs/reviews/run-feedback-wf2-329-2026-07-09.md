# WF14 run-feedback — WF2 #329 (dispatches[] schema + aggregate rollup)

Assessed under **rubric v1** (2026-07-09, #337). Mode: **full**. Record:
`--record` (scratchpad copy of the persisted store line; store line confirmed appended —
`docs/measurements/run_records.jsonl` last line, issue 329, rode PR #349 per the
store-lag convention). Run's loaded plugin cache: **3.25.0**; current main: **3.27.1**
(PR #347 merged 839f5a2 → v3.26.0 was this run's own release).

## Evidence ledger (all load-bearing claims)

- `confirmed` — all 12 mandatory-step markers present in the `## WF2 run: rawgentic #329`
  session-notes section, each quoted below with its slice line; suite delta from runner
  final output ("2435 passed, 1 skipped" baseline → "2503 passed, 1 skipped", exit 0);
  PR/merge from `gh pr view 347` (MERGED 839f5a2); record schema-VALID via
  `work_summary.validate_record` → `[]`.
- `confirmed` — FOREIGN-SESSION marker contamination inside the #329 section: slice
  lines 46/55/56 carry `### WF2 Step 8: Implementation — DONE (4 commits, swift 55/0)`,
  a Step 9 "lane holds at 6 files", and a Step 10 "dispatched" marker from a DIFFERENT
  project's run (a Swift suite — saystory) appended by a concurrent session into the
  shared notes file. This run's own Step 8/9/10 markers appear later (lines 103/112/114)
  with matching evidence. Attribution survives only because the assessor can
  distinguish by content ("swift 55/0" vs "2503p/1s") — mechanically it is ambiguous.
- `inferred` — Step 4's reviewer was a dispatched `rawgentic:rawgentic-reviewer`
  subagent (opus); the record's `reviewer_kind: inline` follows run-record.md's mapping
  guidance ("design self-review → inline") but the actual mechanism was a dispatched
  agent. What would confirm the right value: #340's reviewer_kind vocabulary decision.

## Dimension scores

| Dimension | Score | Evidence (one line) |
|---|---|---|
| Step fidelity | 5/5 | Every mandatory marker present; every skip quotes its condition ("SKIPPED (small-standard lane)", "SKIPPED (no deployment target)"). |
| Gate value | 3/5 | Gates ran, findings dispositioned (Step 4: 2 Low resolved-in-gate; Step 11: 2 adversarial Medium dropped-by-band + refuted) — no named real defect caught pre-merge this run. |
| Prose clarity | 3/5 | Two interpretations needed: §1 item 9 "classify with `plan_lib.classify_branch_protection(status, body)`" doesn't say `body` must be a PARSED DICT (string body silently classifies `unknown` — cost 2 retries); the §4 mechanical feasibility gate needs a real `platform_apis:` LINE, which a bullet-embedded declaration fails (cost 1 rewrite). |
| Dispatch reliability | 5/5 | All 10 dispatches resolved first-try, non-vacuous, models as routed (opus high-risk / sonnet down-routed per `select_impl_model`). |
| Telemetry honesty | 4/5 | Valid on first summarize (rc=0); most fields `match`; `reviewer_kind` `unverifiable` pending #340's vocabulary; usage attribution session-level (`known-limitation`). |
| Cost sanity | 4/5 | Lane election skipped the design panel for a 3-impl-file change; heaviest-least-value step: the Step 11 codex adversarial diff review (2 findings, both dropped/refuted — value ≈ 0 this run; it is opt-in config, not skill waste). |

## Telemetry audit

| field | recorded_value | session_evidence | verdict | impact | routing |
|---|---|---|---|---|---|
| tests | 68/2503/2504 | runner final "2503 passed, 1 skipped"; baseline 2435 | match | — | — |
| gates[4] | 2/2 pass inline | "2 Low resolved-in-gate, 0 loop-backs" | match (kind: see reviewer_kind) | — | — |
| gates[11] | 2/2 pass hand_rolled_multi | "findings_present 2 (both dropped by confidence band; refuted)" | known-limitation | dropped-by-band vs "resolved" counting is exactly #340's multi-pass question | dup → #340 (already filed, epic #333) |
| loop_backs | 0/3 | no consume_loopback in section | match | — | — |
| outcome | PR 347 merged, ci passed | gh + CI checks all pass | match | — | — |
| security_scan | ran, 0/0, skip iac+sca | Step 11.5 marker | match | — | — |
| usage | captured 51.8M/208k | whole-session attribution | known-limitation | per-run cost claims impossible | standing weak spot (rubric) — not refiled |
| reviewer_kind | inline (step 4) | dispatched opus reviewer subagent | unverifiable | vocabulary can't express "dispatched single quality-bar reviewer" | dup → #340 (already filed) |
| dispatches[] | 10 entries | 10 dispatch events evidenced in section (1+2+4+2+1) | match | DOGFOOD: first record carrying the field this run itself shipped | — |

**Marker attribution (behavioral, no field):** foreign-session markers inside this run's
section (evidence ledger) — mechanical marker-to-run attribution is impossible in a
shared notes file. Verdict: known gap; routing: **dup → #341 (issue-keyed step markers,
already filed, epic #333 queue)** — this run is a live reproduction, noted on no new issue.

## Findings and classification

1. **PLUGIN FRICTION** — §1 item 9's `classify_branch_protection(status, body)` sentence
   does not state `body` must be JSON-parsed to a dict; a raw string body returns
   `unknown` silently (fail-open masking a real `unprotected`). Evidence: 2 failed
   classify attempts this run before reading `plan_lib.py:1867`. Quoted sentence:
   "Capture the HTTP status AND body, then classify with
   `plan_lib.classify_branch_protection(status, body)`".
2. **PLUGIN FRICTION** — §4's mechanical gate requires a literal `platform_apis:` line;
   a design note carrying the declaration inside a bullet fails
   `parse_feasibility_block` with no hint about line-format. Quoted requirement (§3):
   "```md\nplatform_apis: none\n```".
3. **ENVIRONMENT** — gitleaks pre-push new-branch full-history scan re-flags 2 known
   historical fixture FPs; SECRET_SCAN_SKIP denied by the auto-mode classifier.
   Resolved durably this run via committed `.gitleaksignore` (now the runbook).
   Host-level hook, not plugin machinery.
4. **WORKING AS DESIGNED** — Step 11 adversarial layer produced only dropped/refuted
   findings this run; opt-in cost accepted by config.

## Routing

- Defects filed: **none** — both telemetry-adjacent gaps are exact dups of already-filed
  epic children (#340 counting/reviewer_kind, #341 marker attribution); linked above,
  never refiled. Findings beyond nothing — cap unused (0/3).
- Telemetry improvements: none beyond the #340/#341 dups (dropped-with-reason: filed).
- Friction: ONE mempalace memory for the run (items 1+2) — result printed in the
  routing line of the summary block.

## Intentionally not claimed

- Per-run token cost (usage is session-level — known-limitation).
- Whether `reviewer_kind: inline` is "wrong" (vocabulary decision belongs to #340).
