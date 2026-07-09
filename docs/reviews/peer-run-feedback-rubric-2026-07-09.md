# Peer-Consult Report — WF14 run-feedback rubric v1 (#337, AC5)

Date: 2026-07-09 · Consult path: `hooks/adversarial_review_lib.py consult` (WF13
engine, cross-model via Codex — per the `docs/codex-reliability.md` routing rule) ·
Artifact consulted: `skills/run-feedback/references/rubric.md` (v1 draft) · Exit: 0
(structured proposal returned; no empty-proposal fallback needed).

This is the AC5 evidence artifact: the rubric was enhanced via the sanctioned consult
path during the implementing WF2 run, and this report is committed with the PR. An
earlier design-phase consult (blind both ways) shaped the design itself — its
provenance lives in `docs/planning/2026-07-09-337-run-feedback-design.md`.

## Peer proposal (summary)

The peer modeled WF14 as a deterministic assessment pipeline over explicit inputs
with an evidence-ledger-first design: ingest (redact secrets) → ledger (tagged
entries with source/line/quote) → mode resolver (full | degraded | unscored) →
version verifier → six independent dimension scorers with declarative caps →
telemetry auditor → finding classifier → filing planner (fingerprint dedupe, shared
3-cap) → report emitter generating the fixed block from structured values.

Core invariants proposed: no score without evidence or an explicit unscored state; no
plugin defect without current-main confirmation; no telemetry negative without both a
verdict and a classification; no report emission containing raw secrets; no inferred
claim omitted from the final inferred-claims list.

## Disposition of peer contributions

| # | Peer point | Disposition |
|---|---|---|
| 1 | Evidence-ledger-first; scores cite ledger entries | Already in rubric v1 (evidence tags + per-dimension evidence lines) — independent agreement |
| 2 | Mode state machine full/degraded/unscored | Already in rubric v1 (degraded-mode rules + unscored) — independent agreement |
| 3 | Declarative per-dimension caps, cap precedence tested | Already in rubric v1 (caps on dims 1/2/5/6); "tested" = the drift guards pin the cap sentences |
| 4 | Verdict vocabulary separate from finding classification | Already in rubric v1 (explicit two-axes note) — independent agreement |
| 5 | Known weak spots as mandatory every-run checks | Already in rubric v1 — independent agreement |
| 6 | **Current-main verification unavailable → assess but refuse to file reproducibility-dependent defects** | **FOLDED** into rubric v1 scope rules (offline/detached-cache downgrade line) |
| 7 | Filing fingerprints (class + canonical anchor + version) | Equivalent already in SKILL.md (normalized title keys: WF id + classification + failure signature) — no change |
| 8 | Machine-readable JSON alongside the human report | **DROPPED with reason:** report-only skill, no consumer exists today; adds a schema surface to drift-guard with no AC behind it. Possible follow-up if an embedder needs structured output |
| 9 | Fixed block generated from structured results, not hand-written | Prose-level hint; the rubric's fixed block is already a rigid template — no change |
| 10 | Adapter layer per evidence source (marker-format variance) | Covered by the marker-acceptance boundaries + `unverifiable`-over-guessing rule — no change |

One fold (row 6), one explicit drop (row 8), eight independent agreements/equivalents
— the consult confirms the rubric's architecture and tightened its offline honesty.
