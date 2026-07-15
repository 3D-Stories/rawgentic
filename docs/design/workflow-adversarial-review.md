# WF5: Adversarial Review — Design

- **Skill:** `/rawgentic:adversarial-review` (`skills/adversarial-review/SKILL.md`)
- **Engine:** `hooks/adversarial_review_lib.py`
- **Issue:** #77
- **Status:** v1

## 2026-07-03 scope update (#131)

The engine gained a `diff` artifact type (refutation lens — attacks the CHANGE for
fail-open guards, silently-passing error paths, and weakened security checks) plus a
fail-closed `--findings-json` sidecar for structured embedded consumers. WF2 Step 11
is now such a consumer: an opt-in cross-model diff review, gated on `adversarialReview`
covering `implement-feature` AND the diff touching a security surface. See
`docs/design/2026-07-03-diff-stage-adversarial-review.md` for the full design and
`docs/config-reference.md` for the config/data-handling details. The "not a code-diff
reviewer" framing below now describes the *standalone* skill's original scope — diff
review is additive and still report-only.

## Purpose

Provide an **independent, cross-model** adversarial review of a TEXT artifact —
design doc, spec, implementation plan, PRD, ADR, RFC, or README. All existing
rawgentic critique is same-model (Claude reviewing Claude): WF1's 3-judge panel,
WF2/WF3/WF4 reflexion at quality gates. WF5 adds a **different model** (via the
Codex CLI) as a second opinion, catching the correlated blind spots that
same-model self-critique tends to miss.

WF5 is **report-only**: it writes a severity-ranked findings report and never
edits the artifact or auto-applies findings. It is also optionally wired into the
WF2 and WF3 quality gates (per-project opt-in).

This is **not** a code-diff reviewer — use `/code-review` or
`/rawgentic:security-audit` for code. WF5 reviews planning/prose artifacts.

## Architecture

```
SKILL.md (thin orchestrator)         WF2/WF3 gates (thin callers)
        │                                     │
        └──────────────┬──────────────────────┘
                       ▼
        hooks/adversarial_review_lib.py  (all logic; deterministically testable)
                       │
                       ▼
              codex exec --output-schema  (different-model reviewer; egress to OpenAI)
```

All logic lives in the engine library so it is unit-testable with a PATH-stubbed
`codex` (no live calls in CI). The SKILL.md and the WF2/WF3 hooks invoke it via
`python3 -c` import-style calls or the `main()` CLI.

## Configuration

Per-project entry in `.rawgentic_workspace.json` (sibling to `critiqueMethod` /
`headlessEnabled` — workspace-scoped, not committed to the project repo):

```json
"adversarialReview": { "enabled": true, "workflows": ["implement-feature", "fix-bug"] }
```

Default disabled. `workflows` uses bare skill names. Written by
`/rawgentic:setup` Step 2d. Loading is **fail-closed** (any error → disabled).

## Findings schema

Each finding: `severity` (Critical|High|Medium|Low), `category` (correctness,
completeness, feasibility, consistency, internal-consistency, security, scope,
ambiguity), `confidence` (high|medium|low — required since #80; this line
previously omitted it, a doc-rot fix), `description`, `recommendation`,
optional `ambiguity_flag` + `ambiguity_reason`, optional `location`, optional
`loopback_class` (#407: `spec-tightening` | `design-flaw` | null — WF2 Step 4
folds it via `loopback_class_entries`; security-category findings and
absent/off-vocab values always route to the full design path). Codex output is
constrained by a JSON Schema (draft-07) via `codex exec --output-schema`, then
validated and normalized (deduped on the full description; ranked by severity
then category).

## Standalone steps (WF5)

1. **Load config + validate artifact** — resolve active project; the artifact
   must resolve under the project root (traversal rejected); size-capped.
2. **Prerequisite gate** — Codex installed (`command -v codex`) + authenticated
   (`codex login status`). Headless + unauthenticated → ERROR (OAuth is
   interactive-only).
3. **Egress notice (warn-only)** — names any detected secret categories.
4. **Invoke Codex** — `codex exec` with the type-aware prompt on stdin and the
   schema; fail-closed on every error path (status `not_installed`,
   `unauthenticated`, `timeout`, `error`, `parse_error`, `success`).
5. **Present report** — write `<project>/docs/reviews/<slug>-<date>.md`; print a
   summary. Report-only — the artifact is never modified.

## Integration points

- **WF2 (`implement-feature`)** — Step 4 (design) and Step 6 (plan). Additive,
  only when `fast_path_eligible == false`. Findings merge with the in-process
  critique (tagged by source) into one circuit-breaker decision; a Critical/High
  design flaw consumes the **existing `design` loop-back counter** (no new
  budget source). Fail-closed: a failed Codex call never counts as "passed".
- **WF3 (`fix-bug`)** — Step 4. **Default-off** even when WF2 is enabled; must be
  explicitly listed. Preserves WF3's intentional lightweight-reflect path and
  documents the latency tradeoff. Loop-back shares the existing reflect budget.

## Invariants

- **Report-only** — never edits the reviewed artifact.
- **Fail-closed** — any Codex/parse/IO error yields a non-success status; callers
  must check `status == "success"` before consuming findings.
- **Default-disabled opt-in** — projects without the config run WF2/WF3 unchanged.
- **Traversal-safe** — artifact and report paths are validated/sanitized.
- **Warn-only egress, with teeth** — artifact text goes to OpenAI; a secret scan
  names detected categories, and `RAWGENTIC_ADV_REVIEW_BLOCK_SECRETS=1` can block.

## Data handling & cost

Artifact text is transmitted to OpenAI (Codex). Reports stay local in
`docs/reviews/`. Tunables (env, frozen at import):
`RAWGENTIC_ADV_REVIEW_MAX_BYTES` (200000), `_TIMEOUT` (300s), `_MAX_RETRIES` (1),
`_BLOCK_SECRETS` (off). Each embedded invocation is a recurring Codex cost and
adds latency — the reason WF3 is opt-in/default-off and WF2 skips the fast path.

## Testing

`tests/hooks/test_adversarial_review_*.py` cover config (fail-closed parsing),
IO safety (traversal, size cap, secret scan), the Codex contract (every
fail-closed path, PATH-stubbed binary — no live calls), schema validation +
normalization, report rendering + path safety, and the CLI exit-code contract
(0 ok / 2 prereq / 3 codex / 4 parse). Drift guards assert registration, the
skill count, and the WF2/WF3 config-gated invocations.

## Troubleshooting

- **"Codex CLI is not installed"** — `curl -fsSL https://codex.openai.com/install.sh | bash`.
- **"not authenticated"** — `codex login`; for headless, API-key auth.
- **Exit 3 (timeout/error)** — Codex failed; retry or check `codex` status.
- **Exit 4 (parse error)** — Codex returned unexpected output; artifact may be too large.
