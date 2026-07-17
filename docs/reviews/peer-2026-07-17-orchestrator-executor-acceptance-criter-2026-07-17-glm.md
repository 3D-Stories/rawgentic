# Peer Consult — 2026-07-17-orchestrator-executor-acceptance-criteria.md

- Date: 2026-07-17
- Reviewer: GLM (model glm-5.2, peer designer)

## Approach

Two-component runtime: the Orchestrator is the interactive Claude Code session that owns workflow step logic, gating, and owner interaction; the Executor is phase_executor, which materializes each WF2/WF3 phase seat as its own full agent instance dispatched via headless `claude -p` (or `codex exec`) running inside a dedicated tmux session. The dispatch contract is uniform across seats: input (prompt + context refs) -> headless agentic work (read/edit/test as permitted) -> output (workspace artifacts + structured Observation envelope with work-product references). I keep the existing transport-agnostic spine (engine.run_seat with injected dispatch, enforce.py audit, QuotaCoordinator, routing policy, capture-dir sentinel) and extend rather than replace: adapters gain an agentic invocation profile with explicit model/effort/budget/tool-grant/worktree knobs; a new tmux supervisor module owns spawn/sentinel-wait/timeout-kill/orphan-reap; WIRED_SEATS expands to the full seat set; the Observation schema is versioned and extended for work products. Every seat runs in an isolated git worktree with preserved guardrails (hooks confirmed active in -p), explicit cost guards, and a single audit world (Observation + routing_audit.l + run-end reconcile).

## Key decisions

- Command-as-initial-pane-process tmux launch (`tmux new-session -d -s <name> '<cmd>'`), never send-keys into a TUI; completion detected by process exit + finalized observation. sentinel, not screen scraping.
- Headless `claude -p` / `codex exec` is the transport; tmux is the optional supervisor/hardening layer for survival, observability, and rescue — a refinement of the tmux directive, not a rejection.
- Mutating executor seats run in isolated git worktrees; engine-managed worktree lifecycle preferred for provider uniformity (claude and codex children), with native `--worktree` as a claude-only optimization to probe later.
- Per-seat capability manifest (tool grants, permission mode, worktree policy, budget, effort) config-driven alongside the #448 seat table; tool grants must be COMPLETE because an ungranted tool call aborts the run.
- Preserve guardrails inside children by pinning non-`--bare` behavior explicitly and adding a drift-guard against the documented future default flip; verify live with a red/green hook-firing test rather than assuming.
- Single audit world: only executor result envelopes are accepted for WF2/WF3 phase work; every dispatch produces Observation + routing_audit.l entry; run-end reconcile proves requested==actual throughout.
- Keep run_seat synchronous internally by wrapping tmux-wait in the dispatch callable so existing callers (bakeoff, bench) keep working, while adding async launch/status/collect verbs the orchestrator uses for parallelism.
- Migration via seat-by-seat wiring inside a feature flag, with Agent-tool phase-subagents retained as a declared fallback tier until the proving run reconciles, then hard-cut to avoid an ambiguous half-state.
- Extend Observation with a `work_product` field (changed files / commit SHA / doc paths / test results) and bump schema version per #434 policy.
- Bench (#449) live cells run against the same wired executor path WF2/WF3 use, gated by RUN_LIVE=1, with legible fail-closed skip.

## Risks

- `--bare` default flip or other CLI default drift silently stripping hooks/CLAUDE.md/MCP from executor children, bypassing guardrails without an obvious failure signal.
- Incomplete per-seat tool whitelist causes a headless child to abort mid-run (tool attempt without grant = hard abort), stalling a workflow gate.
- Usage-limit exit-1 is a shared-pool, run-level pause signal; treating it as a per-seat failure could cascade misdiagnosis and bogus fallback attempts across seats.
- Async tmux sessions surviving orchestrator compaction create orphan/reap ambiguity on resume: live sessions found by `tmux ls` may be stale, duplicated, or tied to a different run state.
- Native `claude -p --worktree --tmux` composition is unverified; mixing native worktree ownership with engine-managed lifecycle could split cleanup responsibility and leak worktrees (`-p --worktree` is not auto-cleaned).
- A large-blast-radius big-bang rewire of two workflows across seven seats risks reintroducing the exact half-wired failure mode this architecture exists to fix if migration staging is not strictly gated.
- Cross-model agentic symmetry (codex with workspace-write in its own worktree) is unproven for mutating seats; assuming full symmetry may surface provider-specific permission/quota semantics.
- Schema extension for work products interacts with the owner-gated #434 versioning policy; proceeding before that decision could fragment the contract.
- Shared worktree approvals (`.claude/settings.local.` in main checkout inherited by all worktrees) could over-grant permissions across seats if not scoped per-seat.
- Native `--tmux` flag maturity is unknown; depending on it before probing could conflict with the supervisor module's naming/lifecycle conventions.

## Sketch

Orchestrator (interactive claude session, WF2/WF3 step logic + gates + owner interaction)
  |
  | per phase seat (intake/design/plan/build/review/...): engine.run_seat(model, effort, seat, dispatch=tmux_dispatch)
  v
engine.run_seat (existing): routing policy -> eligible_targets -> fallback chain -> D9/never-Haiku -> QuotaCoordinator lane stamp -> dispatch callable (now tmux_dispatch)
  |
  v
tmux_dispatch (NEW supervisor.py + async verbs):
  - seat capability manifest lookup (tool grants, permission mode, worktree policy, budget, effort)
  - worktree lifecycle acquire (engine-managed `git worktree add` OR native --worktree; uniform across providers)
  - tmux new-session -d -s <run_id>:<seat>:<attempt> '<headless cmd>'   # command-as-initial-pane-process
  - headless cmd = claude -p --model M --effort E --max-budget-usd B --allowedTools T... --permission-mode P --add-dir <worktree> --output-format   (or codex exec -s workspace-write)
  - poll capture dir for finalized observation. sentinel (process exit)
  - on timeout: tmux kill-session + timeout Observation
  - on resume: tmux ls by run-id prefix -> adopt/kill/reap orphans
  - collect Observation + artifacts (work_product: changed files / commit SHA / doc paths / test results)
  v
Observation envelope (versioned, work_product extended) + routing_audit.l entry + capture dir
  |
  v
back to engine.run_seat: verify_post (requested==actual via provider identity) -> fallback if needed -> return Observation
  |
  v
Orchestrator consumes ONLY executor result envelopes for phase work; no third dispatch path
  |
At run end: enforce.reconcile_run proves every expected seat dispatched, audited, requested==actual throughout.

Parallel path: driver-bench (#449) live cells invoke the SAME tmux_dispatch path (RUN_LIVE=1), report per-seat served/fallback/judge/audit + cost line.

Build order: capability manifest + WIRED_SEATS/build-audit fix -> agentic adapter profiles (claude/codex) -> tmux supervisor + async dispatch -> worktree lifecycle -> guardrail live-verification (red/green) -> Observation work-product extension (post #434) -> WF2/WF3 skill rewiring (seat-by-seat under flag) -> proving WF2 run -> #449 live bench on wired path.

---
_Peer proposal (report-only). Synthesize at your discretion._
