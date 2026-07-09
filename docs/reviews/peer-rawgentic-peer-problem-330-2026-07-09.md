# Peer Consult — .rawgentic-peer-problem-330.md

- Date: 2026-07-09
- Reviewer: Codex (peer designer)

## Approach

Use a single canonical terminal audit line emitted after each workflow-prescribed dispatch attempt reaches its final state. Avoid start/completion pairs because the orchestrator is prose-driven and has no stable ID system unless this issue invents one; a terminal-only line naturally contains all six fields, including outcome, and removes correlation failure as a normal path.

## Key decisions

- Canonical line syntax: `dispatch <role>: subagent_type <subagent_type>, model <model|null>, effort <effort|null>, outcome <outcome>, resolution <resolution>`
- Exact regex: `^dispatch (?<role>review|implementation|analysis|other): subagent_type (?<subagent_type>[A-Za-z0-9_.:/-]+), model (?<model>null|[A-Za-z0-9_.:/-]+), effort (?<effort>null|[A-Za-z0-9_.:/-]+), outcome (?<outcome>ok|error|retried|dead), resolution (?<resolution>primary|fallback|generic)$`
- Emit one line per final attempt, not per retry chain. If an attempted dispatch is retried internally and the final usable attempt succeeds, emit one `outcome ok` line for that final attempt. If all attempts are exhausted, emit one terminal line for the last attempt with `outcome error`, `dead`, or `retried` as appropriate. This keeps `dispatches[]` aligned with terminal dispatch records rather than transient routing noise.
- `outcome retried` means this particular final attempted dispatch did not produce the accepted work product because the workflow moved to another attempt/path. If the retry later succeeds, that later final attempt gets its own line. Thus a retry sequence may produce multiple entries only when the workflow meaningfully changes attempt/path and both terminal outcomes matter.
- Emit only from prescribed workflow dispatch sites: WF2 Steps 2/8/8a/11, WF3 Step 9, and documented delegation sub-modes. Do not capture ad-hoc prose dispatches.
- Ordering in `dispatches[]` is the order valid canonical lines appear in session notes. The assembler does not reorder by role, step, or inferred chronology.
- Duplicate handling is append-only and conservative: exact duplicate canonical lines are retained unless they are adjacent and clearly caused by immediate restatement during record assembly. Non-adjacent duplicates are preserved because two equivalent dispatches can be real.
- Zero valid dispatch lines means omit `dispatches` entirely, rather than emitting an empty array.
- Malformed lines, unsupported enum values, missing fields, or non-token field values are skipped. Assembly records a best-effort note in `extra.dispatch_capture_notes`, for example `skipped 2 malformed dispatch audit lines`. The run-record must still be emitted if the rest of the schema is valid.
- There is no unmatched-line handling for the normal path because there are no paired lines. If legacy start-style lines such as `dispatch review: model ..., effort ...` appear, they are ignored as malformed for `dispatches[]` and counted in capture notes.
- For unavailable named agents using a bundled substitute, keep the requested/selected logical `subagent_type` token and set `resolution fallback`. For inline prompt delegation, use `resolution generic` and a stable `subagent_type` token such as `generic-review`, `generic-implementation`, or `generic-analysis`.
- Use literal `null` for unknown model or effort, never empty strings, `unknown`, or quoted JSON strings inside the markdown line.

## Risks

- A terminal-only line loses explicit dispatch start timing. That is acceptable for this schema because `dispatches[]` stores terminal result metadata, not latency or in-progress state.
- Regex-friendly tokens disallow spaces and commas in `subagent_type`, `model`, and `effort`. Existing workflow prose must normalize these to stable slugs before writing audit lines.
- Because the same LLM emits and assembles the record, drift remains possible. The mitigation is one canonical sentence copied from one shared block into every prescribed dispatch site, plus a drift test that checks the exact sentence and regex examples.
- `outcome retried` can be ambiguous unless the workflow text defines it as terminal for an abandoned attempt, not as a success state. The skill prose should include one example for a successful retry chain.

## Sketch

Canonical examples:

`dispatch review: subagent_type code-reviewer, model gpt-5, effort high, outcome ok, resolution primary`

`dispatch implementation: subagent_type implementer, model null, effort medium, outcome error, resolution fallback`

`dispatch analysis: subagent_type generic-analysis, model gpt-5-mini, effort null, outcome dead, resolution generic`

Assembly algorithm in prose:

1. Scan session notes from top to bottom.
2. For each line matching the canonical regex exactly, append one object to `dispatches[]` with parsed fields, converting literal `null` in `model` and `effort` to JSON null.
3. For any line that appears to be a dispatch audit line but fails the canonical regex, skip it and increment a skipped counter.
4. If skipped counter > 0, add/update `extra.dispatch_capture_notes` with the count and short reason category.
5. If no valid entries were captured, omit `dispatches`.
6. Never fail run-record assembly solely because dispatch capture was partial or malformed.

---
_Peer proposal (report-only). Synthesize at your discretion._
