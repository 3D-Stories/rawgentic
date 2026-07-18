# Adversarial Review — 2026-07-17-orchestrator-executor-acceptance-criteria.md

- Date: 2026-07-17
- Artifact type: design
- Reviewer: Codex (model config-default, reasoning effort high)
- Findings: 10 (Critical 1, High 5, Medium 4, Low 0)

## Summary

The artifact specifies an orchestrator/executor migration using headless model CLIs, tmux supervision, worktrees, routing audit, and promotion. Its quota lifecycle contradicts its process model, while filesystem isolation, cross-provider guardrails, cost enforcement, and several project-specific platform capabilities remain unproven or underspecified.

## Findings

### 1. [Critical] internal-consistency · high confidence — §2 AC-E1; §4 F11; §5b quota-pool pause semantics

> **AC-E1** — Each executor instance runs in its **own tmux session**: survives orchestrator compaction/restart/usage-pause, observable live (`tmux attach`/`capture-pane`), killable/rescuable by name. *(P8)*
> 
> - **F11 — Quota is a shared pool; `-p` fails hard on usage limits**: subscription usage is one rolling 5h+weekly pool across all surfaces and models; on a usage-limit hit `-p` exits code 1, NO retry;

The initial pane process is the headless CLI, and the artifact states that this process exits on a usage-limit event. Therefore the claimed live tmux worker cannot survive that usage pause as written; recovery will find a terminated job rather than preserved live state.

**Recommendation:** Replace AC-E1's usage-pause claim and the quota-pause synthesis with: "On a usage-limit exit, the supervisor records `quota_paused`, persists the provider session ID, worktree, capture directory, and command digest, and later relaunches from the same cwd using the verified session ID; no live tmux process is assumed to survive." Add a resume-identity assertion before work continues.

### 2. [High] correctness · high confidence — §2 AC-E4

> **AC-E4** — Dispatch becomes **async-capable**: launch → poll/wait on sentinel → consume; timeout ⇒ `tmux kill-session` + a timeout Observation; orphan reaping on resume (`tmux ls` by run-id-prefixed session names).

Killing a tmux session is not specified to terminate and verify the entire executor process tree. A child that survives or daemonizes can continue modifying its worktree after the supervisor records a timeout, potentially racing cleanup or promotion.

**Recommendation:** Change AC-E4 to require every pane command to run in a supervisor-owned process group or equivalent containment unit. On timeout, terminate that unit, escalate after a grace period, verify that no recorded PID remains alive, and only then emit the timeout Observation or release the worktree.

### 3. [High] correctness · high confidence — §2 AC-B4; §4 F2/F12/F13

> **AC-B4** — Every executor instance is **cost-guarded**: per-instance `--max-budget-usd` (confirmed flag, F2) and/or per-run budget accounting from Observation `usage`; no unbounded fan-out.

The `and/or` permits post-run Observation accounting as the only guard. Accounting available only after an invocation finishes cannot cap that invocation, and the cited hard-budget flag applies only to Claude; equivalent enforcement for Codex and ZhipuAI is not established. A compliant implementation could therefore exceed the run budget before detecting it.

**Recommendation:** Rewrite AC-B4 to require an atomic pre-launch reservation from the run and quota-pool budgets plus a hard per-invocation limit where the provider supports one. The capability manifest must declare each provider's enforcement and cost-evidence mechanism; reject launches for providers lacking an approved enforceable bound.

### 4. [High] feasibility · high confidence — §2 AC-B3; §5b OQ-7

> **AC-B3** — **Gate preservation:** the workspace guardrails (wal-guard, security-guard, permission classifier, never-Haiku) still fire **inside** executor instances. An executor seat is never a gate bypass ("fan-out is a dispatch mechanism, never a gate bypass" — the #450 invariant, applied here).
> 
> - **OQ-7 (cross-model rights) — CONVERGED:** rights follow **seat capability, not provider identity** — a codex build/design job gets workspace-write only inside its engine-managed worktree under the same gates/budget/audit.

The only hook capability discussed in the artifact is Claude `-p` hook loading. No capability file, exact Codex call site, or live spike proves that wal-guard, security-guard, and the permission classifier execute for `codex exec`. Granting Codex workspace-write under an assumed shared gate layer can create an unguarded mutating path.

**Recommendation:** Add a provider-neutral enforcement layer to AC-B3 with exact supervisor/adapter call sites for every guard, and require a fail-closed canary for each provider, account lane, permission profile, and worktree mode. Until the Codex path passes that spike, set mutating Codex seats to unsupported rather than workspace-write.

### 5. [High] internal-consistency · high confidence — §5b U-3; §6 U-3

> - **U-3 (utility fan-out) — Sol's rule adopted (matches P5 "everything"):** ALL model-based dispatch routes through executor jobs — named phase seats AND utility fan-out; pure deterministic orchestration logic stays in-process; **no second model-dispatch mechanism remains**. *(Owner confirm — this retires Agent-tool use inside WF2 Step 2 fan-outs too.)*
> 
> - **U-3** — Whether P4's "ONLY phase_executor" retires Agent-tool subagents even for *non-phase* utility dispatch (e.g. WF2 Step 2's parallel read-only analysis fan-out helpers vs the analysis *seat* itself). Assumed: phase seats retire Agent-tool; incidental utility fan-out inside the orchestrator remains allowed. **Assumption — confirm.**

The consult synthesis adopts executor-only utility fan-out, while the later uncertainty adopts the opposite assumption. Implementers cannot determine whether WF2 Step 2 Agent-tool dispatch is forbidden or allowed, recreating the dual-dispatch state the design intends to eliminate.

**Recommendation:** Replace §6 U-3 with: "U-3 — Utility fan-out: consult synthesis recommends that every model-based dispatch, including WF2 Step 2 utility fan-out, use executor jobs; this remains pending owner ratification. Each run-level architecture version must declare and enforce exactly one policy."

### 6. [High] security · high confidence — §2 AC-B1–B2; §5b promotion model

> **AC-B1** — Executor instances are **full agents** where the seat requires it: grep/read the repo, edit files in the project workspace, run commands (tests), iterate. Mechanism: headless `claude -p` with tool grants (`--allowedTools`/`--permission-mode`), or `codex exec -s workspace-write` for cross-model seats.
> - **AC-B2** — Every **mutating** executor instance runs in an **isolated git worktree**; concurrent instances can never collide on the shared tree.

A git worktree separates checkouts but does not confine a full agent or its shell commands to that checkout. The design supplies no OS-level write boundary preventing a Claude child from changing the canonical checkout, another worktree, shared configuration, or external state, so the claimed collision isolation and promotion-only integration boundary are not enforced.

**Recommendation:** Extend AC-B2 and the capability manifest to require an OS-enforced per-job sandbox with only that job's worktree and capture directory writable, the canonical checkout and other worktrees read-only, and promotion performed by the orchestrator outside the child sandbox. Reject mutating launch when this confinement cannot be established.

### 7. [Medium] ambiguity · high confidence — §5b New design elements adopted from Sol

> (2) **Expected-dispatch manifest at run start** — reconcile compares expected jobs vs launch records vs Observations vs audit vs promotions; any mismatch blocks completion.

The manifest semantics do not cover runtime-dependent loop-backs, fallback attempts, competitive candidates, retries, or utility fan-out. If it contains exact jobs, legitimate dynamic work blocks reconciliation; if it contains only broad seat expectations, unauthorized extra attempts can escape meaningful comparison.

**Recommendation:** Define the manifest as immutable required job templates plus an append-only authorization ledger. Require every dynamic expansion to be authorized and recorded before launch with parent job, reason, attempt number, fallback edge, and loop-back budget; freeze the ledger before final reconciliation.
**Ambiguity:** The artifact does not define whether expected jobs are exact instances, seat templates, or mutable runtime entries.

### 8. [Medium] completeness · medium confidence — §3 REUSE capture discipline; §4 F9

> `capture/` dir discipline (`create_capture`, finalize) | Becomes the async completion sentinel (AC-E3/E4)
> 
> - **F9 — Existing engine machinery is dispatch-agnostic** (local code read): `run_seat` takes `dispatch=` injected (`engine.py:77`); `run_subprocess` runs children in their own process group with group-kill on timeout (`base.py:61-81`); capture dir finalizes `observation.json` last (`claude_cli.py:95-96`) — a ready-made completion sentinel.

Writing `observation.json` last does not establish that publication is atomic or durable. A poller can observe the path while it is partially written and treat a parse failure as a job failure, or consume data not yet flushed after a crash.

**Recommendation:** Specify in AC-E3 that the writer validates and fsyncs a temporary Observation file, atomically renames it to `observation.json`, then fsyncs the directory. The collector must validate schema and correlation identity before marking completion and must surface malformed sentinels as a distinct audited state.

### 9. [Medium] feasibility · high confidence — §2 AC-E1; §4 F6; §6 U-2

> **AC-E1** — Each executor instance runs in its **own tmux session**: survives orchestrator compaction/restart/usage-pause, observable live (`tmux attach`/`capture-pane`), killable/rescuable by name. *(P8)*

tmux is mandatory for every dispatch, but the text cites neither a project capability/manifest entry nor a spike proving that the tmux executable, server creation, attachment, and detached-session persistence work under the actual host, sandbox, and CI configurations. The native Claude `--tmux` flag does not prove the proposed supervisor's external tmux dependency is permitted.

**Recommendation:** Add a tmux capability preflight to AC-E1 that verifies executable/version, session creation, command execution, capture, kill, and persistence in every supported runtime environment. Define a fail-closed unsupported result or an explicitly approved non-tmux supervisor fallback.

### 10. [Medium] feasibility · high confidence — §4 F3; §3 BUILD guardrail live-verification

> **F3 — Hooks and config surfaces FIRE in `-p` mode — confirmed.** "Without [`--bare`], `claude -p` loads the same context an interactive session would" (CLAUDE.md user+project, settings permissions/hooks, plugins, MCP, skills), and hooks fire on the same lifecycle (`PreToolUse`/`PostToolUse` on every call). Source: https://code.claude.com/docs/en/headless.md, /docs/en/hooks.md. AC-B3's red/green live verification stays (defense against config drift), but the platform contract is confirmed.

The cited documentation proves the general API behavior, not that this project's hook files, plugins, account-specific `CLAUDE_CONFIG_DIR` lanes, permission mode, and sandbox load the required controls. Calling the platform contract confirmed overstates the evidence; the artifact itself postpones the necessary project-specific live verification.

**Recommendation:** Relabel F3 as "API capability confirmed; project enablement unverified" and make the red/green check a mandatory launch gate. Require it to identify and assert each expected guard by stable ID or digest for every account lane and invocation profile, with an audit event on failure.

---
_Report-only: this review does not edit the artifact. Findings are advisory; incorporate them at your discretion._