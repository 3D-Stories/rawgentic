# WF2 extraction effort — impact measurement (Tier 1)

**Date:** 2026-06-15
**Range:** `fcd22b2` (pre-#83 baseline) .. `86fbbf7` (#89 merge)
**PRs:** #83 (cleanup), #84 (parallelism), #86 (parallel_group validator), #87/#88/#89 (CLI extraction: headless / resume / capabilities)

Reproduce the deterministic numbers with:

```bash
python3 scripts/wf2_impact_metrics.py              # markdown
python3 scripts/wf2_impact_metrics.py --json       # machine-readable
```

## Scope of this report (Tier 1 only)

The effort splits into two measurement regimes:

| PRs | Lever | Claim | Measurable how |
|-----|-------|-------|----------------|
| #84, #86 | parallelism | *faster* | **Tier 2** — end-to-end A/B runs, high variance (not done here) |
| #87, #88, #89 | deterministic code extraction | *more correct / safer / consistent* | **Tier 1** — provable from tests + git, no runs |

This report covers **Tier 1**: the cheap, deterministic, run-free metrics. **Speed and output-quality are deliberately NOT claimed here** — they require the Tier-2 end-to-end harness (fixed issue corpus, baseline-via-`git worktree`, N replicates, pinned model). What follows is what we can prove without paying for ~100 full workflow runs.

## 1. Reliability / correctness

### Cross-model review bug ledger
Cross-model (Codex) review caught these concrete defects in the *prose / first-draft* of each PR **before merge** — i.e. bugs the old prose versions had latent:

| PR | Defects surfaced + fixed pre-merge |
|----|------|
| #87 (headless) | shell-vars don't persist across separate Bash tool calls → empty comment/suspend; `write-suspend` accepted empty `--question-id`/`--comment-url` (argparse `required` only checks presence); missing non-empty comment-URL guard; step validation holes (`""`/`0`/`-1`/whitespace/`>16`/`"5\n"`) |
| #88 (resume) | cross-Bash-call shell-vars (recurrence) + `step` never extracted; `clarification_round` null≠0 contract bug; completion-gate rule not in the deterministic invocation; `parse-reply` single-quote injection via CLI literal; `cmd; echo $?` erased the fail-closed exit code; out-of-range reply fail-open; `gh`-fetch-failure conflated with "no reply" |
| #89 (capabilities) | null-vs-absent fail-open (present-null silently defaulted); `deploy.method` accepted any string (no enum) |

**~14 concrete correctness defects** caught and fixed before shipping, each now pinned by a regression test.

### Fail-closed coverage
**69** fail-closed / adversarial test markers (grep matches — a proxy/lower-bound, *not* a literal `assert` count) across the three new CLI test files (`test_headless.py`, `test_resume_lib.py`, `test_capabilities_lib.py`) — covering malformed configs, corrupt suspend files, out-of-range steps/replies, and injection-shaped inputs, each asserting the code fails *safe* rather than producing usable-looking-but-wrong output.

### Test suite growth
- Test functions: **482 → 596** (+114, +24%)
- Parametrize blocks: 32 → 52
- Collected cases: ~672 → ~895

## 2. Consistency / determinism (proven by construction)

The interpretation work — headless suspend/resume, the resumption cascade, reply parsing, and the config→capabilities derivation — is now **pure deterministic code**, so identical input yields identical output by construction. The old prose drove an LLM to re-derive a 9-rule cascade or reconstruct a nested `python3 -c` block on every run, which varies run-to-run. This is the cleanest win of the extraction and is self-evident from the code being deterministic.

## 3. Maintainability / drift-resistance (countable)

- Config→capabilities derivation: **12 copies → 1** (11 byte-identical SKILL.md blocks + 1 docs table → `hooks/capabilities_lib.py`).
- Inline fragile `python3 -c` / `from … import` blocks in implement-feature: **4 → 1**.
- **4 drift-guard test classes** added (`TestHeadlessCLISkillWiring`, `TestResumeSkillWiring`, `TestCapabilitiesSkillWiring`, `TestDocsTableDriftGuard`) so the dedup can't silently re-diverge.
- Diff volume: skills/ `+267 / -274` (interpretation removed from 11 files), hooks/*.py `+896 / -7` (tested code added), tests/ `+1374`.

## 4. Speed (MODELED, not measured)

Not run end-to-end. Static critical-path model for #84:
- **Step 2:** old = *N* sequential component analyses on the critical path → new = fan-out + 1 synthesize ≈ 2 hops regardless of *N*.
- **Step 4:** old = critique *then* reflexion (2 sequential gate phases) → new = concurrent ≈ 1 phase.

This is a latency *model*, not a measurement. Proving the wall-clock win requires the Tier-2 harness (or a focused micro-benchmark of Steps 2/4 in isolation).

## Honest caveats

- The extraction PRs' value is overwhelmingly **reliability + consistency + maintainability**, NOT speed; trying to prove their wall-clock impact end-to-end would mostly measure noise.
- "Quality" of produced features is only weakly proxied here (gate findings, CI-pass) — true output quality needs judge-graded A/B.
- Tier-2 (the real speed/quality proof) needs a fixed corpus, `git worktree` baselines, N≥5 replicates, a pinned model, and ~100+ full workflow runs. The natural telemetry substrate for it is a per-run structured run-record (see the work-summary feature).
