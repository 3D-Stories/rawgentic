# Adversarial Review — 2026-07-17-orchestrator-executor-acceptance-criteria.md

- Date: 2026-07-17
- Artifact type: design
- Reviewer: GLM (model glm-5.2, reasoning effort high)
- Findings: 6 (Critical 0, High 1, Medium 4, Low 1)

## Summary

This artifact defines acceptance criteria for routing all WF2/WF3 phase work through a phase_executor that spawns per-seat agent instances (claude -p / codex exec) supervised by tmux. The design is detailed and well-researched, but contains internal contradictions between open questions and their consult resolutions, several platform-feasibility assumptions backed only by documentation rather than project-specific spikes, and an unverifiable claim about pre-launch tool-grant completeness.

## Findings

### 1. [High] internal-consistency · high confidence — §6, U-3

> U-3 — Whether P4's "ONLY phase_executor" retires Agent-tool subagents even for *non-phase* utility dispatch (e.g. WF2 Step 2's parallel read-only analysis fan-out helpers vs the analysis *seat* itself). Assumed: phase seats retire Agent-tool; incidental utility fan-out inside the orchestrator remains allowed. **Assumption — confirm.**

§6 U-3 states utility fan-out 'remains allowed' via Agent-tool, but §5b U-3 resolution says 'ALL model-based dispatch routes through executor jobs — named phase seats AND utility fan-out; pure deterministic orchestration logic stays in-process; no second model-dispatch mechanism remains' and explicitly 'this retires Agent-tool use inside WF2 Step 2 fan-outs too.' An implementer reading §6 would leave a second model-dispatch path alive, directly violating the §5b invariant and AC-A1's 'no third dispatch path.' The concrete failure: a WF2 run with mixed dispatch mechanisms that defeats the audit reconciliation (AC-D1).

**Recommendation:** In §6 U-3, replace the 'Assumption — confirm.' resolution with: 'RESOLVED in §5b: ALL model-based dispatch (including Step 2 utility fan-out) routes through executor jobs; no Agent-tool dispatch mechanism remains after the run-level architecture flip.' Remove the 'remains allowed' language.

### 2. [Medium] feasibility · high confidence — §4, F3

> F3 — Hooks and config surfaces FIRE in `-p` mode — confirmed. "Without [`--bare`], `claude -p` loads the same context an interactive session would"

F3 is labeled 'confirmed' and cited solely from documentation URLs. Per platform-feasibility review standards, docs prove the API exists, not that this project's actual config (settings files, hook definitions, CLAUDE.md, plugin manifests, permission modes) loads and fires correctly inside -p children under the real project layout. The artifact itself hedges in AC-B3 ('This must be verified live (hooks fire in -p mode), not assumed'), yet F3's header claims the fact is 'confirmed,' creating a false confidence gradient. An implementer may treat F3 as proven and deprioritize the live verification build item.

**Recommendation:** Change F3 header from 'confirmed' to 'docs-stated, live-verification pending' and add a note: 'Project-specific hook-loading behavior in -p mode is unspiked; cite the capabilities file or spike result once AC-B3's red/green test is built.'

### 3. [Medium] feasibility · medium confidence — §4, F3b; §5b, OQ-2

> Per-seat tool grants must therefore be COMPLETE for the seat's job, or the seat dies mid-run.

The OQ-2 resolution says 'manifest validated complete BEFORE launch (an incomplete manifest rejects at dispatch, not mid-run).' But an autonomous agent's complete tool set is not knowable a priori — an agent may decide to run an unforeseen bash command, read an unexpected file type, or call a tool the manifest author didn't anticipate. There is no mechanism described for validating completeness short of exhaustively enumerating every possible tool call. The concrete failure: executor seats abort mid-run on legitimate tool calls that weren't pre-granted, and the 'validate before launch' step provides no real protection because it cannot determine completeness.

**Recommendation:** Add to OQ-2 resolution a fallback strategy for mid-run tool denial (e.g., permissive-but-gated categories with hook-layer enforcement), or define a bounded tool taxonomy with explicit 'deny-and-log' vs 'abort' semantics so unexpected calls degrade gracefully instead of killing the seat.
**Ambiguity:** The author may intend a closed, enumerable tool set per seat, but this is not stated and is unlikely to hold for autonomous build/analysis seats.

### 4. [Medium] feasibility · medium confidence — §4, F3a

> The adapter must pin non-bare behavior explicitly and a drift-guard must watch for the default change.

F3a states the adapter must 'pin non-bare behavior explicitly,' but no flag or mechanism for explicitly requesting non-bare mode is cited. The local CLI probe (F1) enumerates many flags but does not list a `--no-bare` or equivalent. If --bare becomes the default and there is no explicit non-bare flag, the 'pin' is impossible — the adapter can only fail to pass --bare and hope the default doesn't flip, which is exactly the scenario the drift-guard is supposed to catch. The concrete failure: a CLI update silently strips all guardrails from executor children with no way to opt back in.

**Recommendation:** In §4 F3a or the BUILD table's guardrail-verification row, specify the exact pinning mechanism (e.g., explicit `--permission-mode` + hook verification at startup), or note as an unverifiable risk if no non-bare flag exists on 2.1.212. State whether such a flag was probed.

### 5. [Medium] internal-consistency · high confidence — §2, AC-B2

> AC-B2 — Every **mutating** executor instance runs in an **isolated git worktree**; concurrent instances can never collide on the shared tree. (Native `claude -p --worktree` exists — see facts F5/F6; engine-managed worktrees are the alternative.

AC-B2 presents native `claude -p --worktree` as the primary mechanism and engine-managed worktrees as merely 'the alternative.' But the §5b OQ-3 consult resolution (adopted) states: 'CONVERGED: engine-managed lifecycle for every mutating provider (claude AND codex get identical isolation, deterministic paths, explicit cleanup, resume-safe cwd). Native `--worktree` demoted to a claude-only optimization to probe later.' An implementer following AC-B2 would build native --worktree integration as primary, contradicting the adopted resolution and creating a claude-only isolation path that leaves codex mutating seats (OQ-7) without worktrees.

**Recommendation:** Rewrite AC-B2 to: 'Every mutating executor instance runs in an isolated engine-managed git worktree (§5b OQ-3 resolution); native `claude -p --worktree` is a claude-only optimization deferred to a later probe, not the primary mechanism.'

### 6. [Low] feasibility · low confidence — §5b, OQ-7; §4, F4

> OQ-7 — Cross-model agentic rights: does a codex design/build candidate get `workspace-write` in its own worktree

OQ-7 is marked 'CONVERGED' in §5b with codex getting workspace-write, but the underlying capability — that `codex exec -s workspace-write` actually functions in a headless, tmux-hosted, engine-managed worktree context under this project's config — is confirmed only via local `--help` (F4), not via a cited spike or capabilities file. The MODIFY table notes the adapter is 'today hardcoded `read-only`' (codex_cli.py:33), meaning no runtime test of workspace-write has occurred. The converged resolution may be based on an unproven platform capability.

**Recommendation:** In §5b OQ-7, add: 'workspace-write path is --help-confirmed only; spike required before the proving run includes a codex mutating cell.' Or mark the convergence as conditional pending the spike.
**Ambiguity:** The doc may intend the proving-run coverage requirement to serve as the spike, but that is not stated.

---
_Report-only: this review does not edit the artifact. Findings are advisory; incorporate them at your discretion._