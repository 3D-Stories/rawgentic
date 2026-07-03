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

### Resolution library

New `hooks/model_routing_lib.py`, CLI mirroring `adversarial_review_lib.py`:

```bash
python3 hooks/model_routing_lib.py resolve \
  --workspace .rawgentic_workspace.json --project <name> --role review
```

- stdout: model name or `inherit`; exit 0 in all degradable cases.
- Unknown model value, malformed block, missing project → stderr warning + `inherit`. **Fail-open by design:** routing is an optimization knob, not a security gate; nothing in this feature may block a workflow run.
- Soft opus floor: a `review` role resolved below `opus` (`sonnet`/`haiku`) resolves as configured but emits a stderr warning ("below recommended opus floor"). Warned, not blocked — per-project judgment stays with the owner.

### Skill changes (3 skills, 7 sites)

The config-loading preamble of implement-feature, fix-bug, and refactor gains one `resolve` call per role the skill uses; resolved values are carried as literals into later steps (fresh-shell rule). Each Agent dispatch adds `model: <resolved>` unless the resolution is `inherit`. Adversarial-review/Codex dispatches are untouched (different mechanism). No registered agents are introduced.

### Step 8 implementation delegation (`implementation` role)

When `implementation` is configured, WF2 Step 8 delegates each plan task to a subagent instead of executing inline:

- **Brief per task:** design doc + the plan task + TDD requirement + project conventions + current test baseline.
- **Serial:** one task-agent at a time (subagent guideline; each task builds on the previous commit). No parallel dispatch in v1.
- **Main loop stays orchestrator:** after each task it re-runs the suite, diffs against the recorded baseline, and proceeds or intervenes. Step 8a high-risk reviews are unchanged.
- **Fail-open:** a task-agent that dies or returns vacuous output → the main loop retries that task once inline, logs the fallback, and continues. Delegation can never block Step 8.
- **Why safe:** the subagent receives a validated plan task (post Step 4 critique + Step 6 drift check), not an open problem — a strong executor model performs well precisely when design and plan are already right, enforced here by workflow position. Proven in-session 2026-07-03 (#123: Opus executed a 24-file removal to a green PR from a main-loop-written brief).
- **Absent role →** Step 8 runs inline, exactly today's behavior.

## What runs where (Fable session, full example config)

| Work | Model |
|---|---|
| Orchestration, issue analysis, design, planning, gates, PR — all inline steps | session model (Fable) |
| Step 4 / 8a / 11 / WF3 / refactor review fleets | `review` (opus) |
| Step 2 gather, Step 10 memorization | `analysis` (sonnet) |
| Step 8 task execution (only when `implementation` set) | `implementation` (opus) |
| WF5 adversarial, peerConsult | Codex (OpenAI) |

## Feature 2: peerConsult

### Config

```json
"peerConsult": { "enabled": true, "workflows": ["implement-feature"] }
```

Same shape and placement as `adversarialReview`. Default-off. v1 wires only `implement-feature`; the `workflows` array leaves fix-bug/refactor config-ready but unwired.

### Behavior (WF2 Step 3 sub-step)

When enabled for the workflow, Codex is engaged as a **peer senior engineer — on par with the reasoning tier, a different perspective; a peer, not a reviewer** (this framing goes verbatim into the prompt file):

1. **Blind both ways.** At Step 3 start, dispatch `codex exec` with the issue body + the Step 2 codebase-analysis summary (the same context Claude designs from), passed as inline content per the known codex sandbox read restriction, requesting Codex's *own design proposal* as structured output: approach, key decisions, risks, sketch. Claude drafts its own design concurrently and does not read Codex's output until both proposals exist. (Claude + Codex concurrency is safe — separate quota pools, no shared session limit.)
2. **Synthesis.** With both proposals in hand, Claude synthesizes best-of-both; the design doc records what the peer contributed (provenance).
3. **Gates unchanged.** Step 4 judges critique the *synthesized* design. WF5 adversarial review keeps its evaluative role and its #121 high-precision tuning — peer consult is generative (design phase), adversarial review is evaluative (gate phase); both may be enabled simultaneously.
4. **Non-blocking.** Any Codex failure → log it, proceed with Claude's design alone (same posture as the WF5 sub-step in WF2).

### Plumbing

Extend `adversarial_review_lib.py is-enabled` with a `--key` parameter: default `adversarialReview` (backward compatible — existing callers unchanged), new value `peerConsult`. One lib, no parallel copy. The peer prompt lives at `skills/implement-feature/references/peer-consult.md`.

## Error handling

`model_routing_lib.py` is the single failure point for routing and always degrades to `inherit` with a stderr warning; skills never parse the workspace JSON themselves for routing. A missing lib file (stale plugin cache) is treated by skills as `inherit`. Peer consult failures never block Step 3. Leftover/unknown config fields in existing workspace files are ignored, never errored.

## Testing

- `tests/hooks/test_model_routing.py`: absent block / partial roles / invalid value / opus-floor warning / malformed JSON / missing project / `inherit` passthrough.
- `adversarial_review_lib` tests extended: `--key` default compatibility + `peerConsult` resolution.
- Skill-lint additions: each of the 7 known dispatch sites must carry its role annotation (drift guard — a new dispatch site cannot silently bypass routing); peer-consult prompt file exists; WF2 Step 3 sub-step documents the non-blocking failure path; Step 8 delegation documents the brief template and the retry-once-inline fallback.
- `model_routing_lib` tests cover the `implementation` role identically to the others.
- Full suite green against the post-#123 baseline (1384 passed, 5 warnings).

## Docs + release

- `docs/config-reference.md`: new `modelRouting` + `peerConsult` sections.
- README: operational sections only (changelog history stays verbatim — see #123 AC8 amendment).
- Version 2.45.0 → 2.46.0 (one minor bump, both features, one PR). Marketplace manifest carries no plugin version (verified during #123) — no drift to sync.
- Implementation proceeds from this spec via an implementation plan; a tracking issue is filed at planning time.

## Out of scope

- Registered/plugin-shipped agents (contradicts WF2's inline-role design; session-load fragility).
- Parallel task dispatch within Step 8 delegation (serial only in v1).
- Effort-level routing (model only in v1).
- Setup-wizard UI for either block (hand-edit JSON in v1).
- Peer consult wired into fix-bug/refactor (config-ready only).
- Any change to WF5's adversarial stance or #121 tuning.

## Decisions log

- **Goal semantics:** absolute model names, cost-optimizing, soft opus floor on `review` (owner choice, 2026-07-03).
- **Approach:** config + inline `model:` at dispatch sites — over registered agents (B) and hardcoded models (C) (owner choice).
- **Peer consult folded into this design** rather than a follow-up issue (owner choice, option b).
- **`implementation` role added at spec review** (owner, 2026-07-03): opt-in Step 8 delegation — serial, fail-open to inline; the `mechanical` reserved tier dropped as redundant.
- **Cross-model gate:** this spec goes to WF5 adversarial review (Codex) before implementation planning.
- **Guided workflows over ad-hoc orchestration:** assessed and chosen before this design; the deep-reasoner/fast-worker agent definitions remain personal `~/.claude/agents` conveniences, out of plugin scope.
