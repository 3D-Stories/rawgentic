---
name: rawgentic:session-mining
description: WF17 — mine session history for recurring skill/command candidates (detect → queue → synthesize → gate). Use when the user asks to mine sessions for patterns, "what keeps recurring", "what skills should we build", after a campaign wraps, or on-demand workflow-improvement hunts. Report-only — writes only the candidates queue and a report pair; accepted candidates route to WF1 as prepared drafts, nothing is ever auto-filed. Invoke with /rawgentic:session-mining.
argument-hint: none (optional focus hint, e.g. "look at deploy friction")
---

# WF17: Session Mining

<role>
You mine Claude Code session history for recurring friction patterns and
skill/command candidates, then put them to the human gate. Deterministic
detectors (no LLM in the detect stage), durable append-only queue, evidence-
quoted synthesis, propose-then-approve routing to WF1. You are STRICTLY
report-only — you never edit skills, hooks, or docs mid-run.
</role>

## Write surface (canonical, drift-guarded)

WF17 is STRICTLY report-only — the ONLY file writes are the candidates queue
(`claude_docs/.mining/candidates.jsonl`), the report `.md`/`.html` pair under
`docs/reviews/`, the session-note DONE marker append, and — when Step 1
refreshes it — the #375 session-index store (a derived cache owned end-to-end
by `session_index.py`; WF17 never writes it directly).

## Constraints (epic #378, non-negotiable)

- No LLM in the detect stage; deterministic detectors only.
- Explicit invocation only — never a hook, never a daemon.
- Coverage honesty: v1 does not inspect raw tool_use/tool_result payloads and
  cannot conclude that command sequences or tool errors are absent — say so
  in every report.
- Secrets by NAME: core redaction is best-effort pattern masking; YOU review
  every quote before it lands in a report or issue and mask any credential
  value to its name.

The CLI lives in this plugin: `<skill-base-dir>/../../hooks/session_mining_lib.py`
(and the index CLI beside it, `session_index.py`). Run from the workspace root
(the directory holding `.rawgentic_workspace.json`) so defaults resolve;
otherwise pass `--queue` (and the index's `--db`) explicitly.

## Step 1: Freshness + detect

1. `python3 <plugin-hooks>/session_index.py status` — if stale (new/changed
   files) or exit 2 (no index), run `python3 <plugin-hooks>/session_index.py
   index` (first-ever run walks the whole corpus — minutes; say so).
2. `python3 <plugin-hooks>/session_mining_lib.py detect` — appends
   detected/evidence_updated events to the queue; surface any format-drift or
   corruption warnings verbatim.

## Step 2: Propose (dedup + threshold)

`python3 <plugin-hooks>/session_mining_lib.py propose --json`

A candidate is only PROPOSED for filing at recurrence ≥ 3 distinct sessions.
Dedup runs against existing plugin/workspace skills (strong match suppresses;
borderline surfaces with the matching skill + score) and against the queue's
terminal states. Exit 2 = queue corruption: surface the message, stop — the
queue holds human dispositions and is not rebuildable; never repair silently.

## Step 3: Render the report

Write the report to `docs/reviews/session-mining-<YYYY-MM-DD>.md` (same-day
rerun → `-2`, `-3`, never overwrite): proposed candidates with verbatim
evidence quotes + session ids + recurrence counts + coverage (a `limit_hit`
coverage is a LOWER BOUND — no absence claims), borderline matches with
scores, pending-WF1 accepted candidates, suppressed count, and the coverage-
honesty sentence. Render:

```bash
python3 <plugin-hooks>/render_artifact.py --md <report>.md --out <report>.html \
  --title "WF17 session-mining — <date>" --style report
```

A render failure is non-voiding — keep the `.md`, record the gap. Publish
with the Artifact tool (best-effort on top — the committed pair is the
deliverable): print the URL, or the required failure line "artifact publish
FAILED/unavailable — committed .html is source of truth".

## Step 4: Human gate

Present each proposed/borderline candidate. Per candidate, the user decides:

- **Accept** → record it FIRST:
  `python3 <plugin-hooks>/session_mining_lib.py disposition <key> accepted`
  — then emit the WF1 draft prompt (the lib's `build_wf1_draft` shape:
  conventional title + Description/Acceptance Criteria/Scope/Affected
  Components/Risk) and hand it to `/rawgentic:create-issue`. WF1 has no
  pre-drafted-body entry path — it re-drafts from the prompt, and WF1's own
  dedup + approval still run. Once the WF1 issue exists:
  `... disposition <key> filed --note "#<issue>"`.
- **Decline** →
  `python3 <plugin-hooks>/session_mining_lib.py disposition <key> declined`.
  A declined candidate is recorded in the queue and not re-proposed.

Accepted candidates are routed to WF1 as a prepared draft; nothing is ever
auto-filed — propose-then-approve, always.

## Step 5: Close

APPEND the session-note marker:
`### WF17 Session Mining — DONE (<date>: N proposed, N accepted, N declined, N pending-WF1)`

<termination-rule>
WF17 terminates after the close marker. It never auto-transitions to building
anything it proposed — accepted candidates live in WF1's pipeline, everything
else in the queue and the report.
</termination-rule>

## Failure modes

- No index / stale index → Step 1 refreshes it (the one sanctioned non-queue
  write, via session_index.py).
- Queue corruption on propose/disposition → exit 2, surface, stop.
- Zero candidates at threshold → the report says so honestly; no padding.
- render/Artifact failures → non-voiding per Step 3.
