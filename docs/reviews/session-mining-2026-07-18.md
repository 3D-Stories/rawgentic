# WF17 session-mining — 2026-07-18

**Run:** session 11d19ee3, plugin v3.51.1, index refreshed this run (203 files indexed, 3760
unchanged, 1535 vanished — 30-day retention churn; 7872 messages added; corpus now ~5300 files /
~91k messages). Detect: 1058 signals across 220 patterns, 19 queue events appended. Propose:
**2 proposed, 0 borderline, 0 suppressed, 0 pending-WF1.**

## Coverage honesty

v1 does not inspect raw `tool_use`/`tool_result` payloads and therefore CANNOT conclude that
command sequences or tool errors are absent — the detectors see assistant/user text only. Neither
candidate below hit its row limit (`limit_hit: false` on both), so their recurrence counts are
complete over the indexed text, not lower bounds. The 1535 vanished files are sessions deleted by
Claude Code's 30-day retention — patterns that lived only in those sessions are unmineable now.

## Proposed candidates

### 1. `same error` — friction detector, recurrence 3 (key 267c4b3b…)

Coverage: 4 rows / limit 500, limit not hit.

Miner assessment: **false positive — recommend decline.** The three distinct sessions match the
generic bigram "same error" inside unrelated prose, not a recurring friction about one actual
error: (a) a classifier-outage status line ("Still down. Same error, config-derive still blocked
by the auto-classifier outage"), (b) a code-review note about error-message hygiene ("…terser than
`provision()`'s … for the same error code"), (c) a review comment ("The test repeats the same
error (lines 7-8)"). A fourth quote does not contain the phrase at all (index-snippet adjacency).
No shared root cause, no automatable workflow.

Evidence (verbatim, redaction-reviewed — no secret values present):

- `e6117a98-737a-4d47-8871-93c40c8a905a`: "Still down. Same error, config-derive still blocked by
  the auto-classifier outage. Options to move now (both bypass the down classifier)…"
- `0640df90-a7d3-4a0e-91b4-c7103870324f`: "…`decommission()` BAD_NAME message (line 215) is terser
  than `provision()`'s (line 125) — minor error-message-hygiene inconsistency for the same error
  code…"
- `a6ad888c-180d-4350-bb18-d34ab722f68d`: "…The test repeats the same error (lines 7-8). This is a
  NEW comment introduced by this PR."

### 2. `traceback most recent call` — error_proxy detector, recurrence 3 (key 5ebf954c…)

Coverage: 3 rows / limit 500, limit not hit.

Miner assessment: **false positive — recommend decline.** None of the three quotes is an actual
recurring traceback: two are index-snippets of review verdict prose with no visible traceback
content, and the third is **self-referential** — a session discussing THIS detector's own redaction
rule ("the friction/error detectors specifically mine phrases like … 'traceback most recent call'").
The detector matched documentation of itself.

Evidence (verbatim, redaction-reviewed):

- `ef6d060f-00c7-402c-b03c-c1f2ef8f4202`: spec-compliance review prose (snippet adjacency; no
  traceback text in the quote).
- `b3994fee-bf43-4b30-bad8-56f34e3bf454`: slopslap crash-analysis verdict prose (snippet adjacency).
- `01b89659-ae14-4db1-9bac-2c648921676e`: "…the friction/error detectors specifically mine phrases
  like 'no such file or directory' and 'traceback most recent call' — whose actionable evidence is
  exactly the path/command/traceback. This rule masks that content…"

## Borderline matches

None.

## Pending-WF1 accepted candidates (prior runs)

None.

## Dispositions this run

Recorded at the human gate; see the queue (`claude_docs/.mining/candidates.jsonl`) for the
authoritative record.

## Detector-quality observation (for the WF17 backlog, not actioned here)

Both proposals this run are generic-phrase matches inside REVIEW PROSE — sessions in this workspace
quote error phrases constantly while reviewing code that handles errors. A future detector
refinement (report-only note): weight down matches whose surrounding text is review/verdict prose,
and exclude self-referential matches originating from session-mining's own design/test sessions.
