# Design: modelRouting + peerConsult (v2.46.0)

**Date:** 2026-07-03
**Status:** Approved (brainstorming session, owner-approved section by section)
**Companion:** `2026-07-03-model-routing-and-peer-consult-design.html` (visual artifact, same content)

## Problem

Rawgentic workflow skills dispatch subagents at seven sites today — and every one inherits the session model. When the session runs a premium model (Fable), the heavy review fleets (3 design judges + 2 per-task reviewers + 3 pre-PR reviewers per WF2 run) burn premium tokens on work a cheaper strong model handles well. Separately, Codex participates only as an adversarial *reviewer* (WF5); there is no way to engage it as a *peer designer* — same problem, blind, own proposal — even though cross-model perspective is proven value in this workspace.

An alternative was considered and rejected: ad-hoc orchestration (a standing "you are the orchestrator" prompt routing work to registered agents). Rejected because dispatch knowledge would live in per-run improvisation instead of versioned skill text, registered agent definitions load only at session start (fragile), and WF2 deliberately uses inline-defined prompt roles ("NOT registered subagents", Step 8a).

## Empirical basis (merged main @ 8f36a38)

Dispatch inventory — no dispatch specifies a model today:

| Site | Agents | Role |
|---|---|---|
| WF2 Step 2 (codebase gather) | parallel readers | `analysis` |
| WF2 Step 4 (design critique) | 3 judges | `review` |
| WF2 Step 8a (per-task, high-risk) | 2 reviewers | `review` |
| WF2 Step 10 (memorization, background) | 1 curation agent | `analysis` |
| WF2 Step 11 (pre-PR) | 3 reviewers | `review` |
| WF3 (bug-fix review) | 2 reviewers | `review` |
| Refactor (post-refactor review) | 4 reviewers | `review` |

~90% of dispatch is review fleets. Implementation runs inline in the main loop today — it becomes routable via the `implementation` role's opt-in Step 8 delegation (below), the largest token block of a WF2 run.

## Feature 1: modelRouting

### Config

Optional block in the project's entry in `.rawgentic_workspace.json` (same placement as `adversarialReview`, `critiqueMethod`, `headlessEnabled`):

```json
"modelRouting": { "review": "opus", "analysis": "sonnet", "implementation": "opus" }
```

- **Roles:** `review` (all judge/reviewer fleets), `analysis` (WF2 Step 2 gather + Step 10 background memorization), `implementation` (WF2 Step 8 task delegation — see below).
- **Values:** `opus` | `sonnet` | `haiku` | `fable` | `inherit`.
- **Defaults:** absent block, absent role, or `inherit` → subagents inherit the session model. Byte-identical behavior to v2.45.0 unless configured (default-off, like `adversarialReview`).
- Roles are partial: `{"review": "opus"}` alone is valid.

### Setup integration

`/rawgentic:setup` gains a modelRouting step alongside the existing per-project steps (headless, adversarialReview): it offers the three roles with suggested defaults (`review: opus`, `analysis: sonnet`, `implementation: opus`) and skip-per-role (= inherit), plus a peerConsult enable/workflows step mirroring the adversarialReview one. Both are applied in the same single finalize read-modify-write as the other per-project fields so no step clobbers another.

### Resolution library

New `hooks/model_routing_lib.py`, CLI mirroring `adversarial_review_lib.py`:

```bash
python3 hooks/model_routing_lib.py resolve \
  --workspace .rawgentic_workspace.json --project <name> --role review
```

- stdout: model name or `inherit`; exit 0 in all degradable cases.
- Unknown model value, malformed block, missing project → stderr warning + `inherit`. **Fail-open by design:** routing is an optimization knob, not a security gate; nothing in this feature may block a workflow run.
- Soft opus floor: a `review` role resolved below `opus` (`sonnet`/`haiku`) resolves as configured but emits a stderr warning ("below recommended opus floor"). Warned, not blocked — per-project judgment stays with the owner. The warning fires only on explicit `sonnet`/`haiku` values; `inherit` and `fable` never warn (no cross-model ordering defined or needed).

### Skill changes (3 skills, 7 sites)

The config-loading preamble of implement-feature, fix-bug, and refactor gains one `resolve` call per role the skill uses; resolved values are carried as literals into later steps (fresh-shell rule). Each Agent dispatch adds `model: <resolved>` unless the resolution is `inherit`. Adversarial-review/Codex dispatches are untouched (different mechanism). No registered agents are introduced.

### Step 8 implementation delegation (`implementation` role)

When `implementation` is configured, WF2 Step 8 delegates each plan task to a subagent instead of executing inline:

- **Brief per task:** design doc + the plan task + TDD requirement + project conventions + current test baseline.
- **Serial:** one task-agent at a time (subagent guideline; each task builds on the previous commit). No parallel dispatch in v1.
- **Main loop stays orchestrator:** after each task it re-runs the suite, diffs against the recorded baseline, and proceeds or intervenes. Step 8a high-risk reviews are unchanged.
- **Fail-open with a clean-state boundary:** before each dispatch the main loop records the pre-task state (HEAD + `git status --porcelain`). A task-agent that dies or returns vacuous output → the main loop **restores the pre-task state first** (discarding the agent's partial edits after confirming only expected paths changed), then retries that task once inline, logs the fallback, and continues. A successful delegated task must end committed, so every task starts from a clean tree. Delegation can never block Step 8, and an inline retry never runs on a half-mutated tree.
- **Why safe:** the subagent receives a validated plan task (post Step 4 critique + Step 6 drift check), not an open problem — a strong executor model performs well precisely when design and plan are already right, enforced here by workflow position. Proven in-session 2026-07-03 (#123: Opus executed a 24-file removal to a green PR from a main-loop-written brief).
- **Absent role →** Step 8 runs inline, exactly today's behavior.

## What runs where (Fable session, full example config)

| Work | Model |
|---|---|
| Orchestration, issue analysis, design, planning, gates, PR — all inline steps | session model (Fable) |
| Step 4 / 8a / 11 / WF3 / refactor review fleets | `review` (opus) |
| Step 2 gather, Step 10 memorization | `analysis` (sonnet) |
| Step 8 task execution (only when `implementation` set) | `implementation` (opus) |
| WF5 adversarial, WF13 peer consult | Codex (OpenAI) |

## Feature 2: peerConsult (WF13 — standalone skill + WF2 integration)

### Shape — the WF5 pattern

A standalone skill `/rawgentic:peer-consult` registered as **WF13** (WF6 stays reserved per `docs/consolidation.md`; highest used was WF12), plus an opt-in WF2 Step 3 integration — exactly how WF5 pairs `/rawgentic:adversarial-review` with its opt-in gate wiring. `docs/consolidation.md` and the README workflow table are updated to register WF13.

### Standalone invocation

`/rawgentic:peer-consult <problem-artifact-path>` — takes a problem statement (issue export, brief, or design-input doc), dispatches Codex as peer designer, and writes the peer proposal to `<project>/docs/reviews/peer-<slug>-<date>.md`. Report-only, same output/egress conventions as WF5. Standalone invocation always works; config governs only the WF2 integration (same as WF5).

### Config

```json
"peerConsult": { "enabled": true, "workflows": ["implement-feature"] }
```

Same shape and placement as `adversarialReview`. Default-off. v1 wires only `implement-feature`; the `workflows` array leaves fix-bug/refactor config-ready but unwired.

### Behavior (WF2 Step 3 sub-step)

When enabled for the workflow, Codex is engaged as a **peer senior engineer — on par with the reasoning tier, a different perspective; a peer, not a reviewer** (this framing goes verbatim into the prompt file):

1. **Blind both ways — mechanism.** At Step 3 start, WF2 launches the WF13 engine as a background `codex exec` process writing structured output to a temp file (`-o <path>`); the prompt carries the issue body + the Step 2 codebase-analysis summary (the same context Claude designs from), inlined per the known codex sandbox read restriction, requesting Codex's *own design proposal*: approach, key decisions, risks, sketch. The main loop records the output path but **must not read it until Claude's own draft design is written to the design-doc file**. Completion detection = process exit; on timeout/failure the engine writes an explicit empty-proposal marker (never partial content). (Claude + Codex concurrency is safe — separate quota pools, no shared session limit.)
2. **Synthesis.** After Claude's draft is on disk, read the peer proposal, synthesize best-of-both; the design doc records what the peer contributed (provenance).
3. **Gates unchanged.** Step 4 judges critique the *synthesized* design. WF5 adversarial review keeps its evaluative role and its #121 high-precision tuning — peer consult is generative (design phase), adversarial review is evaluative (gate phase); both may be enabled simultaneously.
4. **Non-blocking.** Any Codex failure → log it, proceed with Claude's design alone (same posture as the WF5 sub-step in WF2).

### Plumbing

Extend `adversarial_review_lib.py` with a consult mode reusing the shared codex invocation, prereq, and egress code (different prompt + output schema: a proposal, not findings). `is-enabled` gains the `--key` parameter: default `adversarialReview` (backward compatible), new value `peerConsult`. One lib, no parallel copy. The peer prompt lives in `skills/peer-consult/` (the WF13 skill's own directory).

## Error handling

`model_routing_lib.py` is the single failure point for routing and always degrades to `inherit` with a stderr warning; skills never parse the workspace JSON themselves for routing. A missing lib file (stale plugin cache) is treated by skills as `inherit`. Peer consult failures never block Step 3. Leftover/unknown config fields in existing workspace files are ignored, never errored.

## Testing

- `tests/hooks/test_model_routing.py`: absent block / partial roles / invalid value / opus-floor warning / malformed JSON / missing project / `inherit` passthrough.
- `adversarial_review_lib` tests extended: `--key` default compatibility + `peerConsult` resolution.
- Skill-lint additions: each of the 7 known dispatch sites must carry its role annotation (drift guard — a new dispatch site cannot silently bypass routing); peer-consult prompt file exists; WF2 Step 3 sub-step documents the non-blocking failure path; Step 8 delegation documents the brief template and the retry-once-inline fallback.
- `model_routing_lib` tests cover the `implementation` role identically to the others.
- WF13 registration test (mirroring `test_adversarial_review_registration`); consult-mode lib tests (schema, empty-proposal marker on timeout); setup-step lint for the new modelRouting/peerConsult prompts.
- Acceptance criterion (future, verified at implementation): full suite green against the recorded pre-implementation baseline — expected 1384 passed / 5 warnings as of spec time (post-#123).

## Docs + release

- `docs/config-reference.md`: new `modelRouting` + `peerConsult` sections.
- README: operational sections only (changelog history stays verbatim — see #123 AC8 amendment).
- Version 2.45.0 → 2.46.0 (one minor bump, both features, one PR). Marketplace manifest carries no plugin version (verified during #123) — no drift to sync.
- Implementation proceeds from this spec via an implementation plan; a tracking issue is filed at planning time.

## Out of scope

- Registered/plugin-shipped agents (contradicts WF2's inline-role design; session-load fragility).
- Parallel task dispatch within Step 8 delegation (serial only in v1).
- Effort-level routing (model only in v1).
- Peer consult wired into fix-bug/refactor (config-ready only).
- Any change to WF5's adversarial stance or #121 tuning.

## Decisions log

- **Goal semantics:** absolute model names, cost-optimizing, soft opus floor on `review` (owner choice, 2026-07-03).
- **Approach:** config + inline `model:` at dispatch sites — over registered agents (B) and hardcoded models (C) (owner choice).
- **Peer consult folded into this design** rather than a follow-up issue (owner choice, option b).
- **`implementation` role added at spec review** (owner, 2026-07-03): opt-in Step 8 delegation — serial, fail-open to inline; the `mechanical` reserved tier dropped as redundant.
- **Cross-model gate:** this spec went to WF5 adversarial review (Codex) — 5 findings (0C/1H/3M/1L), all accepted and applied in rev 3 (`docs/reviews/2026-07-03-model-routing-and-peer-consult-design-m-2026-07-03.md`).
- **Owner review (2026-07-03):** setup gains modelRouting + peerConsult steps; peer consult promoted to standalone **WF13** with WF2 integration (WF6 remains reserved).
- **Guided workflows over ad-hoc orchestration:** assessed and chosen before this design; the deep-reasoner/fast-worker agent definitions remain personal `~/.claude/agents` conveniences, out of plugin scope.
