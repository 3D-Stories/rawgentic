# Peer Consult — .rawgentic-peer-problem-314.md

- Date: 2026-07-08
- Reviewer: Codex (peer designer)

## Approach

sound-with-changes. I would narrow the first implementation to Step 11 branch diff and Step 13 failed CI logs, then add Step 11.5 scan JSON once provenance validation exists. I would not initially delegate Step 8/9 test failure dumps; those are high-decision-density artifacts where the orchestrator often needs exact failure text, stack frames, command context, and sequencing. The core pattern should be: reader subagents may compress raw artifacts into evidence-indexed digests, but the orchestrator keeps all gate, fix, verdict, prioritization, and review judgments. Digests are navigation aids plus quoted evidence, not conclusions.

## Key decisions

- 1. Digest schema: load-bearing fields are `surface`, `artifact_id`, `artifact_fingerprint`, `generated_at`, `command_or_source`, `files_read`, `coverage`, `sections`, `evidence`, `omissions`, `truncated`, and `reader_errors`. I would replace free-form `verbatim_quotes[]` with structured `evidence[]` entries: `{id, file, line_start, line_end, quote, relevance, confidence}`. `sections[]` should reference evidence IDs, not embed unsupported claims. Add `decision_relevance` labels such as `changed_api`, `test_failure`, `security_signal`, `ci_failure`, `review_finding_context`. Remove any field that invites verdicts, like `recommendation`, `severity`, `should_fix`, or `patch`.
- 2. AC5 covering check: validate that every nontrivial digest claim either cites evidence or is explicitly marked as inference. Require `coverage` to include counts such as files seen, hunks seen, failures seen, findings seen, and omitted counts. If the artifact is truncated or coverage is partial, the digest must say what was omitted and why. For decision safety, partial coverage should force targeted reads before any gate or finding evaluation.
- 3. Threshold: use artifact-size heuristics, not token guesses. The least-gameable trigger is deterministic and cheap: bytes plus line count plus surface-specific caps. Example: delegate when `bytes > 64KiB OR lines > 1200 OR file_count > 40 OR failed_log_lines > 500`. Keep thresholds in prose/config with hysteresis: once a surface delegates in a run, subsequent reads of the same artifact use the digest path to avoid flapping.
- 4. Step 11: digest plus targeted verification is sound if the digest is used as an index, not as evidence for final judgment. It nets negative when the diff is small, when most files require judgment anyway, or when the digest triggers broad targeted rereads of more than roughly 30-40% of the original artifact. Add a rule: if targeted rereads exceed a budget, stop pretending this is a digest workflow and read the relevant raw diff inline or dispatch strong-model reviewers as already designed.
- 5. AC3 mechanization: test both prose and helper behavior. Add markdown drift guards for the carve-out: implementation, review, gate verdicts, design decisions, and finding evaluation stay on strong/orchestrator paths. Add `validate_digest()` checks that reject digest payloads containing patch blocks, edit instructions, verdict words in decision fields, severity assignments, approval/rejection language, or `should` recommendations. Add routing tests asserting reader delegation uses `analysis` role only and that `implementation`/`review` mappings remain unchanged.
- 6. Reader quote hallucination: require quote verification against the artifact fingerprint where feasible. The reader should return line spans and quotes; the orchestrator-side helper verifies those exact quoted lines against the current artifact before accepting the digest. If exact verification is impossible for a source, mark evidence unverified and force targeted reads before decisions.
- 7. Stale reads: include an artifact fingerprint from the raw source, preferably hash plus command/source metadata. Before using a digest, recompute or compare the fingerprint. If it changed, discard the digest and regenerate or fall back inline.
- 8. Session-limit death: reader failure must be explicit. Fail-open to inline only for non-gate summarization. For gate-adjacent surfaces, fail-open can erase the token-saving goal and hide broken delegation, so record `reader_errors`, count fallback events, and include them in the before/after measurement.
- 9. Step 8/9: I would keep inline for now unless logs exceed a higher emergency threshold, such as multi-megabyte output. Test failures are often dense with exact causal clues; summarization can drop ordering, repeated failures, environment text, or first-error context. Start with CI logs in Step 13 because they are commonly huge and more naturally reducible to failed jobs, commands, first failing frames, and artifact links.
- 10. Measurement: before/after must compare orchestrator input-token share from `usage.model_mix` for matched workflows, not just absolute tokens. Also track fallback count, delegated artifact bytes, accepted digest bytes, targeted reread bytes, and digest rejection count so a “successful” run does not hide savings lost to verification rereads.

## Risks

- Digest claims can become de facto decisions if the schema permits recommendations or severity labels.
- Exact quote verification may be hard for command output unless the raw artifact is persisted with a stable fingerprint.
- Fail-open inline fallback preserves correctness but can mask delegation regressions unless measured and surfaced.
- Step 11 savings depend heavily on diff shape; broad cross-cutting refactors may require enough targeted reads to erase the benefit.
- Threshold-gated behavior can create inconsistent run records unless thresholds, hysteresis, and artifact identities are stable.
- Security JSON triage is tempting to summarize, but dropping low-confidence or duplicate-looking findings can violate the lossless-for-decisions requirement.

## Sketch

Canonical prose: “Raw artifact reads expected to exceed the delegation threshold MUST be read by an analysis-role reader subagent that returns a bounded evidence digest. Reader subagents summarize and index evidence only. They MUST NOT decide correctness, propose patches, approve/reject gates, rank findings for action, or perform implementation/review work. Orchestrator judgment uses the digest only to choose targeted verification reads; final decisions require verified evidence or explicit targeted reads.”

Digest shape:
```json
{
  "surface": "step11_branch_diff",
  "artifact_id": "branch_diff_before_final_review",
  "artifact_fingerprint": {"kind":"sha256","value":"..."},
  "command_or_source": "git diff --...",
  "generated_at": "ISO-8601",
  "coverage": {"files_total": 18, "files_read": 18, "hunks_total": 44, "hunks_summarized": 44, "omitted_count": 0},
  "sections": [{"title":"API changes", "summary":"...", "evidence_ids":["E1","E2"]}],
  "evidence": [{"id":"E1", "file":"src/x.ts", "line_start":42, "line_end":47, "quote":"exact lines", "relevance":"changed_api", "verified":false}],
  "omissions": [],
  "truncated": false,
  "reader_errors": []
}
```

Validation sketch: parse JSON, require schema fields, reject decision/patch fields, reject fenced diffs, verify artifact fingerprint, verify exact quotes when artifact is available, require every section to cite evidence, require omissions when coverage is partial, and mark digest unusable for decisions until evidence is verified or followed by targeted reads.

Initial rollout: implement Step 11 diff digest with targeted verification reads and Step 13 CI-log digest. Leave Step 8/9 inline except for a very high hard cap. Add run-record metrics for orchestrator token share, digest bytes, raw artifact bytes, targeted reread bytes, fallback count, and rejected digest count.

---
_Peer proposal (report-only). Synthesize at your discretion._
