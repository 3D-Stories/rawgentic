# Orchestrator/Executor architecture — acceptance criteria + gap analysis

**Date:** 2026-07-17
**Status:** **RATIFIED** (owner, 2026-07-17) — consulted (WF13) + adversarially reviewed (WF5) by GPT Sol AND GLM 5.2, all 16 findings folded (§5c); then owner-ratified: D-1…D-8 approved, U-3/U-4/U-5 confirmed. D-4 and U-2 get conclusive answers via probe issues **#452** (codex containment) and **#453** (--tmux × -p). This document is now the target architecture the wiring epic is written against. **Spike verdicts folded in (2026-07-18, issues #452–#456, PRs #458–#462):** D-4 conditional pass (§6b note), U-2 resolved-negative (§6), U-5 resolved (§6), F3/F3a verified live + F3b revised (§4), F10 corrected + F11 annotated (§4), OQ-2 sharpened incl. the lane CRITICAL, OQ-7 spike condition discharged (§5b). **EXECUTION STATE (2026-07-18): wiring epic #475 FILED** — children #464–#474 in dependency order + #449 re-scoped as W10, #445/#446/#447 absorbed, #448 closed superseded; owner decisions Q1–Q6 recorded (#457 comments) and baked into child ACs; **#434 decided (b) and closed** (schema_version bumps on breaking-for-old-copies changes — v2 ships with W6 #469); adversarial-review xhigh effort fix shipped v3.51.1 (#463). This document is now in execution.
**Author:** Claude (Fable 5), session 6d7297b7
**Supersedes:** `2026-07-17-wf2-wf3-executor-seat-placement.md` (built on a wrong premise — see §3 "throwaway")
**Grounds:** all owner prompts this session, epic #422 artifacts, `phase_executor/` code, live CLI probes, external research (citations inline)

---

## 1. What the owner asked for (requirement trace)

Every requirement below traces to a specific owner statement this session (P-numbers = prompt order):

| # | Owner statement (paraphrase) | Requirement extracted |
|---|---|---|
| P1 | Handoff: "#449 top priority — need bench tooling so we can adjust routing off real data" | Live bench remains a deliverable; it must measure the path workflows actually use |
| P3 | "when rawgentic routes to a phase agent, does it set the model and effort?" | Model AND effort must be real, per-dispatch knobs |
| P4 | "Didn't we update rawgentic to ONLY use the phase-executor engine — WF2 orchestrator Agent-tool subagents no longer needed?" | phase_executor is THE dispatch mechanism; Agent-tool phase-subagents retire |
| P5 | "the entire purpose of the overnight epic was… everything in WF2 and WF3 routed through their own individual agent calls" | Every WF2/WF3 phase = its own individual executor agent call |
| P6 | "let's do C then A — write the design doc" | Architecture settled before integration work |
| P7 | "2 components: 1) orchestrator 2) executor. Analysis and build ALSO run through phase_executor with its own access to grep, edit files… executor: (1) takes input (prompts or docs), (2) does thinking/designing/building/whatever, (3) provides output (docs or returned value)" | **The core architecture.** Two components; executor instances are full agents; simple input→work→output contract |
| P8 | "consider using tmux to spin up each executor instance" | tmux as the executor process model (evaluate honestly — see research) |
| P9 | "complete analysis… full AC list… gap analysis… adversarial review with GPT Soul and GLM 5.2… thought partners… research with EXA/context7/web — facts not assumptions" | This document + the consult/review pipeline |
| P10 | "visual artifact with a proper VDL that draws my eyes to where it needs my attention" | Decision-focused artifact deliverable |
| P11 | "get answers out of the Soul and GLM consults; document uncertainties for my review" | Open questions route to consults; residual uncertainty logged for owner |

Standing context (epic #422, owner-approved rev 3): the seat table (orchestrator=opus interim · intake=opus · design=COMPETITIVE sol-vs-opus, glm judge · plan=opus · build=sonnet + gated bake-off · review=fable · ship=sonnet), fallback chains, never-Haiku, cross-model invariant (D9), `requested==actual` audit, multi-account lanes, routing telemetry.

---

## 2. Acceptance criteria

### A. Architecture (two components)

- **AC-A1** — Exactly two runtime components. **Orchestrator**: the interactive Claude Code session that runs WF2/WF3 step logic, quality gates, and owner interaction. **Executor**: `phase_executor`, which spawns each phase seat as its own agent instance. Once wired, WF2/WF3 phase work has **no third dispatch path** — harness Agent-tool phase-subagents (`rawgentic-implementer`/`rawgentic-reviewer`/generic-analysis dispatch) retire from the workflows. *(P4, P5, P7)*
- **AC-A2** — Executor contract: **input** = prompt + optional docs/context references; **work** = whatever the seat requires (thinking, designing, building, reviewing); **output** = docs/files produced in the workspace + a structured returned value (the Observation envelope, extended for work products — see AC-D4). *(P7 verbatim)*
- **AC-A3** — Every WF2/WF3 phase seat (intake, analysis, design, plan, build, review — and ship-support work where it is a model call) dispatches as its **own individual executor agent call**. The orchestrator seat is the session itself and is never dispatched. *(P5; structural)*

### B. Agentic capability

- **AC-B1** — Executor instances are **full agents** where the seat requires it: grep/read the repo, edit files in the project workspace, run commands (tests), iterate. Mechanism: headless `claude -p` with tool grants (`--allowedTools`/`--permission-mode`), or `codex exec -s workspace-write` for cross-model seats. Pure-completion seats (e.g. the glm judge) may stay one-shot. *(P7)* Every seat declares a `session_policy: fresh | resume` in its capability manifest — **default `fresh` for all review/judge seats** (fresh eyes over self-consistency; a reviewer that remembers its last verdict anchors on it). Cross-review context travels via the curated fenced-carry mechanism (the #393 dispositions-ledger pattern, generalized), never via session memory — keeping `context_hashes` a true record of what the model saw. Distinct from AC-E1's `--resume`, which is same-dispatch quota recovery, not cross-dispatch memory. *(OQ-10 / D-8)*
- **AC-B2** — Every **mutating** executor instance runs in an isolated **engine-managed git worktree** (§5b OQ-3 resolution: uniform across claude AND codex children; native `claude -p --worktree` is a claude-only optimization deferred to a later probe, never the primary mechanism). A worktree isolates the *checkout*, not the *agent* (a Bash-capable child can write outside it — gpt review #6): mutating launches additionally require **provider-native write confinement** — claude: permission-layer file scoping + sandbox settings; codex: its OS-level sandbox (`workspace-write` restricts writes to the workspace). A mutating launch whose confinement cannot be established is **rejected**. Promotion into the canonical branch is performed by the orchestrator, outside the child's boundary.
- **AC-B3** — **Gate preservation, provider-neutral:** an executor seat is never a gate bypass ("fan-out is a dispatch mechanism, never a gate bypass" — the #450 invariant). For **claude** children the workspace guardrails (wal-guard, security-guard, permission classifier, never-Haiku) are hook-layer and load in `-p` (F3 — verified live, spike #454). For **codex** children NO claude-hook layer exists (gpt review #4): containment comes from the codex sandbox + supervisor-level checks, declared per-provider in the capability manifest. A **fail-closed guardrail canary** runs per provider × lane × permission profile before any mutation is allowed; **codex mutating seats are unsupported until their canary/spike passes**.
- **AC-B4** — Every executor instance is **cost-guarded by pre-launch reservation**: an atomic reservation against the run + quota-pool budgets BEFORE launch, plus a hard per-invocation cap where the provider supports one (claude: `--max-budget-usd`, F2). Providers without a hard cap (codex, zhipu) must declare a compensating enforceable bound in the manifest (timeout + max-token/turn limits) — post-hoc Observation accounting alone is NOT a guard (gpt review #3). No unbounded fan-out.

### C. Routing, model, effort

- **AC-C1** — Every seat dispatch sets **model and effort explicitly** from the seat table; `requested==actual` verified from provider-reported identity (existing `verify_post`). *(P3)*
- **AC-C2** — Fallback chains (chain-aware cross-model skip), never-Haiku, and the D9 cross-model-author invariant are preserved unchanged (existing `engine.run_seat` + `routing/`).
- **AC-C3** — The seat table stays config-driven and per-project (epic #448 alignment: #445 config table → #446 setup → #447 diagram).

### D. Enforcement, audit, output contract

- **AC-D1** — Every executor dispatch produces an Observation (schema-versioned) + capture dir + `routing_audit.jsonl` entry; the **run-end reconcile** proves every expected seat call happened, audited, with no uninstrumented dispatch. This makes epic #422's acceptance ("routing audit reconciles, requested==actual throughout") actually meetable on a real run.
- **AC-D2** — WF2/WF3 accept **only executor result envelopes** for phase work (the design doc's sol orphan-work rule, now enforceable).
- **AC-D3** — The **build seat gets an audit path**: `enforce.check_pre`'s `role=="build"` hard-deny is removed/rewired and `WIRED_SEATS` extends to the full seat set.
- **AC-D4** — The Observation contract is **extended for agentic work products**: an agentic seat's output is not just completion text — it needs the produced artifact references (changed files / commit SHA / doc paths / test results). Schema change ⇒ versioning policy decision (#434 is the open owner-gated issue; OQ-4).

### E. Process model (tmux + headless)

- **AC-E1** — Each executor instance runs in its **own tmux session**: survives **orchestrator** compaction/restart (the tmux server owns the process, not the harness), observable live (`tmux attach`/`capture-pane`), killable/rescuable by name. *(P8)* **A provider usage-limit exit is NOT survivable** — the headless child exits code 1 (F11; gpt review #1): on that exit the supervisor records `quota_paused`, persists the provider session ID + worktree + capture dir + command digest, and later **relaunches via `--resume <session-id>` from the same cwd** (F10) with a resume-identity assertion before work continues. A **tmux capability preflight** (executable/version, create/exec/capture/kill/persist) runs once per environment; unsupported ⇒ fail-closed or an explicitly approved non-tmux supervisor fallback (gpt review #9). **Spike #455 verdict (PR #460): the recovery mechanics hold.** Session-id capture (`--output-format json` envelope), cwd-scoped `--resume`, and worktree-scoped `--resume` all behave as specified on claude 2.1.212; a SIGKILL mid-turn on an established (≥1 completed turn) session leaves the session file byte-for-byte intact and fully resumable — the interrupted turn is retried, not corrupted. No-recovery case: a first-ever turn killed before its JSON envelope prints leaves no session_id and no session file — treat as fresh-dispatch retry, not resume. Confirmed implementation blocker: `claude_cli.py:26` unconditionally passes `--no-session-persistence`, making `--resume` unreachable today — must become conditional on the seat's `session_policy: resume` (wiring-epic task, not a design change). A genuine usage-limit exit-1 was NOT reproduced (spike declined to burn the shared pool) — residual risk its residue differs from SIGKILL's.
- **AC-E2** — Launch pattern is **command-as-initial-pane-process** (`tmux new-session -d -s <name> '<cmd>'`) — **never** `send-keys`-typed prompts into a TUI. Evidence: Anthropic's own Agent-Teams tmux backend has a reproducible failure class doing exactly that (F7).
- **AC-E3** — Inside tmux the instance is **headless** (`claude -p` / `codex exec`): completion = process exit + finalized `observation.json` sentinel, never TUI screen-scraping. *(F8)* Sentinel publication is **atomic and durable**: validate + fsync a temp file, `os.replace` to `observation.json`, fsync the directory; the collector validates schema + correlation identity before marking completion, and a malformed sentinel is a **distinct audited state**, not a silent job failure (gpt review #8; the repo's `atomic_write_lib` pattern).
- **AC-E4** — Dispatch becomes **async-capable**: launch → poll/wait on sentinel → consume. The pane command runs in a **supervisor-owned process group**; timeout ⇒ terminate the group, escalate after a grace period, **verify no recorded PID survives**, and only then emit the timeout Observation / release the worktree — `tmux kill-session` alone does not kill a daemonized child (gpt review #2). Orphan handling on resume via the durable job registry (§5b OQ-8): adopt only on full identity match, else quarantine. Session names encode `run_id/seat/attempt` (the existing `correlation_id` scheme).
- **AC-E5** — Concurrency ceiling + per-pool quota lanes enforced **at launch** (existing `QuotaCoordinator`; `CLAUDE_CONFIG_DIR` multi-account lanes, #431).
- **AC-E6** — **Orphan cleanup (owner-directed 2026-07-17): no tmux-session graveyard.** Three tiers: (1) **normal path leaves zero residue** — the supervisor kills the session immediately after the Observation is collected (collect ⇒ kill, same transaction); (2) **sweep for the abnormal** — a reaper verb (run at run-end and orchestrator session-start, the `registry_prune.py` precedent) inventories `tmux ls` against the job registry: sessions with a finalized sentinel are killed; quarantined/unknown sessions are listed owner-visibly and auto-killed past an age threshold ONLY when their worktree is clean (mirrors Claude Code's own worktree-sweep only-if-clean rule); a session whose registry entry is live with a fresh heartbeat is NEVER swept; (3) **companion residue** — the same sweep applies the retention policy to engine worktrees (dirty ⇒ retained-for-diagnosis with retention limit + secret-redaction, per the Sol risk) and capture dirs (age-bounded). The AC-J status surface and AC-I4 orphan count expose the inventory so a leak is visible before it is a pile.

### F. WF2/WF3 integration

- **AC-F1** — WF2/WF3 skill prose rewired: per-step phase dispatches route through the executor (the real "#417" that never shipped). The `DISPATCH` audit line (#330) stays; its producer becomes the executor path.
- **AC-F2** — All WF2/WF3 mandatory gates preserved (Steps 4, 8a, 9, 11, 11.5): same gates, executor-dispatched where they are model calls. The lane/trivial checks, loop-back budgets, and resume protocol are unchanged in semantics.
- **AC-F3** — One **real WF2 run** post-wiring passes: review on fable, audit reconciles, `requested==actual` throughout — the #422 acceptance, demonstrated not proxied.
- **AC-F4** — Migration strategy decided explicitly (hard cutover vs seat-by-seat; OQ-1) — never an ambiguous half-state like the one this doc exists to fix.

### G. Bench (#449, re-aimed)

- **AC-G1** — driver-bench live cells run against the **same executor path WF2/WF3 use** (not a bench-only dispatch): real model calls, report to `docs/measurements/driver-bench/`, per-seat outcome (served/fallback/judge/audit), explicit cost line, `RUN_LIVE=1` gate, fail-closed legible skip on missing creds. (#449's issue ACs, now pointed at the wired path.)

### H. Meta (this analysis)

- **AC-H1** — Adversarially reviewed by GPT Sol AND GLM 5.2; both also consulted as thought partners; findings dispositioned (accept/discard with reason). *(P9)*
- **AC-H2** — Load-bearing claims grounded in cited primary sources (docs pages, GitHub issues, local CLI/code probes) — confirmed vs inferred marked. *(P9)*
- **AC-H3** — Final deliverable includes a decision-focused visual artifact (attention-directing VDL: owner decisions first, assumptions flagged confirm/deny). Residual uncertainties documented for owner review. *(P10, P11)*

### I. Telemetry (owner-directed 2026-07-17 — what gets captured, by consumer)

- **AC-I1** — **Per-dispatch capture** (the Observation, extended): everything the envelope already carries (seat, engine, transport, requested/actual model, `prompt_hash`, `context_hashes`, usage {input, output, cached, cost_proxy}, `timing_ms`, `queued_ms`, exit/timeout, `parse_status`, `fallback_reason`, capture path) **plus the new-architecture fields**: requested + native effort (the U-5 dual record), `session_policy` used (D-8), worktree id, tmux session name, canary result, budget reserved vs spent, quota pool + lane, hook **denial events** (deny-and-log counts from OQ-2), and `work_product` refs (D-5). One envelope = the atomic telemetry unit; nothing about a dispatch is knowable only from logs.
- **AC-I2** — **Per-run capture** (orchestrator level, extends the existing run-record): run_id, architecture_version (D-3), workflow + issue, **phase transitions with timestamps** (seat begin/end per WF step), gate outcomes + loop-back consumption per source, **review findings counts by severity per pass**, the expected-dispatch ledger reconciliation result, cost/usage rollup by model × seat, wall-clock per phase, human-interaction points (questions asked, wait durations), CI outcome, PR number.
- **AC-I3** — **Cross-run history** (the self-improvement substrate, feeds AC-K): per-seat × per-model outcome rows joined against bench baselines — findings caught, gate pass rates, fallback frequency, canary failures, quota pauses, cost per merged PR, wall-time percentiles. Home: extend the existing Tier-2 store (`docs/measurements/run_records.jsonl` via `work_summary.py`) + a seat-outcomes sidecar; never a new parallel telemetry system.
- **AC-I4** — **Operational/monitoring signals** (live, derived — never separately instrumented): per-seat liveness (tmux session alive + last capture write age), queue depth + pool saturation, stuck-seat detection (no sentinel past estimate), orphan count, capture-dir disk usage, error-taxonomy counts. Read from the job registry + capture dirs; a monitoring failure can never affect a run.

| Consumer | Reads |
|---|---|
| Monitoring | I4 (liveness, stuck, orphans, saturation) |
| Operational | I1/I2 (cost, budgets, quota pauses, denials) |
| Tracking/audit | I1 + ledger reconcile (provenance, requested==actual) |
| Visualization | I1/I2/I4 (the AC-J feeds) |
| Self-improvement | I3 (+ bench reports, bakeoff sink) |

### J. Live run visualization (owner-directed 2026-07-17)

- **AC-J1** — Every orchestrator session can show, per active run: **(a)** which agents are running (seat, tmux session name, worktree), **(b)** model + effort — requested, and actual once known, **(c)** ETA per seat — honest estimate from AC-I3 wall-time percentiles for that seat × model, "no estimate" before history exists (never a fabricated number), **(d)** elapsed since seat start and since run start, **(e)** current WF phase/step, **(f)** what it is doing — correlation_id → step/task label + the last activity line (latest stream-json event / capture write).
- **AC-J2** — **Data-first contract**: the feed is a supervisor/CLI verb (`status --run <id>`) returning JSON derived from the job registry + capture dirs; presentation layers (terminal statusline, tmux status bar, rendered HTML dashboard via the existing `render_artifact` dashboard style) consume that JSON — the JSON is the contract, presentation is swappable.
- **AC-J3** — **Read-only invariant**: visualization reads registry/audit/capture only, never mutates run state; a dead or wedged viz cannot affect a run.

### K. Self-improvement reflection — data + alerts now, learning later (owner-directed 2026-07-17)

- **AC-K1** — **Data foundation first**: every run writes the AC-I3 outcome rows sufficient to later support per-seat model comparison, autocorrect proposals, and head-to-head experiments — captured NOW even though the learning loop ships later. A field not captured is an experiment that can't run.
- **AC-K2** — **Baselines**: rolling per-seat × per-model baselines (p50/p90, robust to small n) computed from history; driver-bench stubbed baseline + live reports (#449) are the reference anchors for "expected" behavior.
- **AC-K3** — **Alerts (ship now)**: threshold rules evaluated at run end (and at gate time where cheap) — examples: review Critical+High count > baseline p90 → "investigate: reviewer anomaly or genuinely risky change"; fallback fired > expected; canary failure; requested≠actual occurrence; seat cost or wall-time > p90; quota-pause frequency rising. Alerts are **advisory** — surfaced in the completion summary, session notes, and optionally an issue comment. **An alert is never a gate** — it asks the owner to look, it does not block.
- **AC-K4** — **Experiment stub (schema only now)**: outcome rows reserve `experiment_id` + `arm` so future randomized head-to-head cells (starting with D-8's fresh-vs-resume review cell) record into the same store. **Explicitly out of scope now**: any autonomous routing change — every "autocorrect" is a *proposal* routed to the owner (a drafted WF1 issue), consistent with the user-in-the-loop principle (P11).
- **AC-K5** — **Consumers wired, not duplicated**: WF14 (run-feedback) and WF17 (session-mining) read this store; alert thresholds become per-project config surfaced through setup (#446's natural home).

---

## 3. Gap analysis — reuse / modify / throwaway / build

### REUSE as-is (transport-agnostic; survives the architecture shift)

| Asset | Why it survives |
|---|---|
| `contract.py` + `observation.schema.json` / `routing-table.schema.json` | The envelope is dispatch-agnostic; extend (AC-D4), don't replace |
| `enforce.py` — `check_pre`/`verify_post`/`RoutingAuditLog`/`reconcile_run` | The audit spine; needs seat-set extension only (AC-D3) |
| `routing/` — seat table, `eligible_targets`, chains, D9, never-Haiku | Pure policy; unchanged |
| `engine.run_seat` fallback loop + `dispatched_lane` stamping (`engine.py:65-117`) | Chain/quota/audit logic wraps *any* dispatch callable (it already takes `dispatch=` injected) |
| `QuotaCoordinator` + #431 `CLAUDE_CONFIG_DIR` lanes | Concurrency + account isolation apply identically to tmux children |
| `capture/` dir discipline (`create_capture`, finalize) | Becomes the async completion sentinel (AC-E3/E4) |
| `bakeoff_policy.py` + `run_competitive` | Judge + anonymization + fail-closed policy unchanged; candidates become agentic instances |
| `model_routing_lib` role→model/effort config | Feeds the seat table; #448 extends it |
| `driver_bench_lib` scorer + 12 fixtures + stubbed baseline | The 7-dim scorer + fixtures stay; live path re-aims at the wired dispatch (AC-G1) |
| WF2 `DISPATCH` line contract (#330) + run-record `dispatches[]` | Format stays; producer changes |
| gap-forensics in the superseded doc (§1: how the miss happened) | Valid history; referenced by the accountability record |

### MODIFY (reuse with surgery)

| Asset | Surgery |
|---|---|
| `adapters/claude_cli.py` | Add **agentic invocation profile**: `--allowedTools`/`--permission-mode`/worktree cwd/`--add-dir`/`--effort`/`--max-budget-usd`; keep one-shot mode for pure completions |
| `adapters/codex_cli.py` | Add `-s workspace-write` profile for build-capable cross-model seats (today hardcoded `read-only`, `codex_cli.py:33`) |
| `adapters/base.py run_subprocess` | Blocking mode stays (`base.py:61-81`, own-process-group + group-kill); ADD detached tmux-launch + sentinel-poll path alongside |
| `executor_routing_lib` | Extend `WIRED_SEATS` to all seats; add async verbs (launch/status/collect); keep the fail-closed taxonomy |
| WF2/WF3 `<model-routing-resolve>` + steps.md dispatch annotations | Rewrite to executor dispatch (the real #417); Agent-tool contract text retires (OQ-6: keep as fallback tier or delete) |

### THROWAWAY

| Asset | Why |
|---|---|
| `2026-07-17-wf2-wf3-executor-seat-placement.md` §§0/2/4 — the "executor is single-shot/read-only so analysis+build can't route through it" placement argument | **Wrong premise.** Read the v1 adapter *configuration* as a *platform limit*. `claude -p` is a full headless agent (F1); codex has `workspace-write` (F4). Superseded by this doc |
| Its "hybrid review" (Agent-tool explorer + executor bounded pass) | Moot — an executor review seat can grep/read itself (AC-B1) |
| Its "two audit worlds coexist" contract (§5) | Collapses — with all phase work executor-routed there is ONE audit world (AC-D1/D2); the DISPATCH line remains only as the orchestrator-level record |
| Agent-tool phase-dispatch as the *primary* mechanism (bundled `rawgentic-implementer`/`rawgentic-reviewer` contracts in skill prose) | Retired by AC-A1 (disposition of the agent definitions themselves = OQ-6) |

### BUILD (new work)

| Item | ACs served |
|---|---|
| **Seat capability manifest**: per-seat tool grants, permission mode, worktree policy, budget (config, alongside #448's seat table) | B1, B2, B4, C3 |
| **tmux supervisor module** (`phase_executor/supervisor.py`): spawn command-as-pane-arg, sentinel wait, timeout kill, run-id session naming, **collect⇒kill + reaper sweep (sessions/worktrees/capture dirs, only-if-clean, owner-visible quarantine)** | E1–E4, **E6** |
| **Async dispatch API**: engine launch/await split (or blocking `run_seat` wrapping tmux-wait — OQ-5 settles the shape) | E4 |
| **Worktree lifecycle** for executor instances (native `--worktree` vs engine-managed — OQ-3) | B2 |
| **Guardrail live-verification** harness: prove hooks fire inside `-p` children (red/green test, not prose) + a `--bare`-default drift-guard (F3a) | B3 |
| **Observation work-product extension** (+ schema version bump per #434 policy) | D4 |
| **build-seat audit path** (`enforce` rewire + `WIRED_SEATS` full set) | D3 |
| **WF2/WF3 skill rewiring** (the real #417): per-step executor dispatch prose + resume-protocol integration with tmux sessions | F1, F2 |
| **The proving run**: one real WF2 run, audit reconciled | F3 |
| **#449 live cells** on the wired path + report | G1 |
| **Telemetry extension**: Observation new-architecture fields + per-run phase/gate capture + seat-outcomes sidecar on the Tier-2 store | I1–I3 |
| **Status surface**: supervisor `status` verb (JSON contract) + terminal/HTML presentation | J1–J3 |
| **Baselines + alert rules**: rolling per-seat×model p50/p90 + advisory alert evaluation at run end (thresholds → per-project config) | K2, K3, K5 |

---

## 4. Research facts (confirmed; citations)

- **F1 — `claude -p` is a full headless agent, not a completion API.** Flags confirmed live on claude 2.1.212 (this host): `--allowedTools`, `--disallowedTools`, `--permission-mode`, `--add-dir`, `--dangerously-skip-permissions`, `--output-format json|stream-json`, `--input-format stream-json`, `--session-id`, `--resume`, `--fork-session`, `--no-session-persistence`. Headless children can edit files and run Bash when granted.
- **F2 — Per-instance cost + effort knobs exist**: `--effort <level>` and `--max-budget-usd <amount>` confirmed on 2.1.212 (local `--help` probe).
- **F3 — Hooks and config surfaces in `-p` mode: VERIFIED LIVE (spike #454, PR #462 — claude 2.1.212, rawgentic 3.51.0).** A non-bare `claude -p` child launched from this repo fired the project hook layer: 9 SessionStart hooks + a wal-guard PreToolUse deny on a probe Bash call, captured verbatim (`BLOCKED: SSH (ssh) is disabled in headless mode …`, `is_error:true` tool_result); installed plugin `hooks.json` == repo copy, byte-identical (sha256 `23cd86d2…`). Docs baseline (previously the only evidence — gpt #10, glm #2): "Without [`--bare`], `claude -p` loads the same context an interactive session would" (headless.md, hooks.md). AC-B3's red/green canary stays a **mandatory launch gate** — registration presence is host-state, not a per-launch guarantee, and the lane cell shows how it silently vanishes (see the §5b OQ-2 lane note).
- **F3a — `--bare` landmine:** `--bare` skips hooks/plugins/CLAUDE.md/MCP discovery, and docs state it is "the recommended mode for scripted and SDK calls, and **will become the default for `-p` in a future release**." **No explicit `--no-bare` opt-out flag was found in the 2.1.212 probe** (glm #4) — so "pinning" cannot be a flag: the enforcement IS the AC-B3 startup canary (a child whose hooks didn't fire fails closed before mutation) plus a CLI-version drift-guard. (headless.md) **Spike #454: CONFIRMED LIVE** — `--bare` fired 0 hooks (vs 9 non-bare) and stripped the plugin toolset to 3 built-ins; on this host it also failed auth (`apiKeySource:none`, "Not logged in") even with the default config dir. Canary digests pinned: `hooks.json` sha256 `23cd86d2…`, PreToolUse block `8c6c390e…`, bound to plugin 3.51.0.
- **F3b — `-p` behavior when a tool is NOT granted: REVISED — the "run ABORTS" claim did NOT reproduce (spike #454).** Docs say "otherwise the run aborts when one is attempted" (headless.md), but across four permission modes (`default` no-grant, `default`+`--allowedTools Read`, `default`+`--disallowedTools Bash`, `dontAsk`) on claude 2.1.212 the `-p` run always completed, never aborted, `permission_denials:[]`. Cause: this host's `settings.json permissions.defaultMode:"auto"` auto-approves tool calls (`--allowedTools` is additive, not exclusive) and read-only commands auto-classify safe. **Per-seat tool grants are therefore NOT a containment boundary on an auto-mode host — the hook layer is.** Residual (inferred only): the abort may still occur for an un-granted mutating tool under a non-auto profile — untested. Reinforces AC-B3: the canary must prove hooks fire, because grants alone don't gate. Permission modes: `default|acceptEdits|plan|auto|dontAsk|bypassPermissions` (permission-modes.md).
- **F3c — Worktree-shared approvals (v2.1.211+):** "don't ask again" approvals save to the MAIN checkout's `.claude/settings.local.json` and apply in every worktree of the repo — pre-approve a seat's toolset once, all workers inherit. (worktrees.md)
- **F4 — codex sandbox modes**: `codex exec -s read-only|workspace-write|danger-full-access` confirmed via local `--help`; v1 adapter *chose* read-only — a config choice, not a platform limit (`codex_cli.py:33`).
- **F5 — Native worktree support**: `claude --worktree [name]`; `claude -p --worktree` skips the trust check; subagent `isolation: worktree`; worktree locking while an agent runs; periodic sweep cleanup (age > `cleanupPeriodDays`, only if clean); `-p --worktree` worktrees are NOT auto-cleaned; `.worktreeinclude` copies untracked files (e.g. `.env`); `WorktreeCreate`/`WorktreeRemove` hooks can replace git logic; `worktree.baseRef: fresh|head`. Source: https://code.claude.com/docs/en/worktrees
- **F6 — Native `--tmux` flag exists** (2.1.212): "Create a tmux session for the worktree (requires `--worktree`)". Native CLI support for exactly P8's shape. Maturity/headless interplay unverified (U-2).
- **F7 — The send-keys failure class is real and reproducible** in Anthropic's own Agent-Teams tmux backend: shell-init race loses the command (issues [#23513](https://github.com/anthropics/claude-code/issues/23513), [#25315](https://github.com/anthropics/claude-code/issues/25315)); silent exit-1 before debug init, "manual run of the identical command succeeds" ([#46349](https://github.com/anthropics/claude-code/issues/46349)); prompts never delivered / inbox never read ([#23477](https://github.com/anthropics/claude-code/issues/23477)); lifecycle-state loss → orphaned/duplicate teammates at scale (~30/400 runs, [#44701](https://github.com/anthropics/claude-code/issues/44701)). The documented robust fix in every thread: **pass the command as the pane's initial process**, never send-keys.
- **F8 — The ecosystem converged on "headless workers, tmux as optional supervisor".** overstory [#85](https://github.com/jayminwest/overstory/issues/85) is a full postmortem of TUI-in-tmux agents (zombies, ~15s spawn overhead, beacon loss, trust dialogs) migrating to `claude -p --output-format stream-json` with process-exit lifecycle; maintainer verdict: "workers mostly headless; coordinator/monitor interactive; tmux isolation is the hardening layer, not the transport." Prior art: twaldin/tmux-orchestrator (spawn/kill/message/watch scripts, worktree isolation, CLAUDE.md injection — but interactive agents needing an auto-approval watcher), claude-squad, primeline-ai/claude-tmux-orchestration, jeffdhooton/orch, omux, taco.
- **F9 — Existing engine machinery is dispatch-agnostic** (local code read): `run_seat` takes `dispatch=` injected (`engine.py:77`); `run_subprocess` runs children in their own process group with group-kill on timeout (`base.py:61-81`); capture dir finalizes `observation.json` last (`claude_cli.py:95-96`) — a ready-made completion sentinel.
- **F10 — Session resume is cwd-scoped**: `--resume <id>` must run from the SAME directory (or its worktrees) — session files live under `~/.claude/projects/<encoded-cwd>/<id>.jsonl` (or `$CLAUDE_CONFIG_DIR/projects/...`). Wrong-cwd resume was live-corrected by spike #455: for the `--resume <id>` shape this design uses it is a HARD FAILURE (exit 1, stderr `No conversation found with session ID: <id>`, no JSON output) — not the docs-implied silent fresh session (that wording may describe `--continue`'s most-recent-in-cwd fallback, untested). A loud, machine-detectable failure is better for the supervisor's resume-identity assertion than a silent identity swap would have been. A per-seat resume must execute from that seat's worktree path. (agent-sdk/sessions.md)
- **F11 — Quota is a shared pool; `-p` fails hard on usage limits**: subscription usage is one rolling 5h+weekly pool across all surfaces and models; on a usage-limit hit `-p` exits code 1, NO retry; 429s retry with backoff up to `CLAUDE_CODE_MAX_RETRIES` (default 10); `CLAUDE_CODE_RETRY_WATCHDOG=1` allows indefinite 429/529 retries for unattended runs. One worker's limit hit usually means the whole pool is dry — the supervisor must treat exit-1-usage-limit as a RUN-level pause signal, not a per-seat failure. (costs.md, errors.md) **Spike #455:** the usage-limit exit itself was not reproduced (spike declined to drain the shared pool); SIGKILL — a different abnormal-exit path — confirmed an interrupted turn leaves the session file unmodified and resumable, consistent with but not proof of the same for a usage-limit rejection.
- **F12 — Cost fields**: `--output-format json` always carries `total_cost_usd` (client-side ESTIMATE, both success and error) + `modelUsage` per-model breakdown that INCLUDES subagent activity (`usage` undercounts on nesting — use `modelUsage`, which the v1 adapter already parses). No cross-call session total — the executor accumulates. (agent-sdk/cost-tracking.md)
- **F13 — Agent SDK (python/TS) is the docs-recommended substrate for production automation** ("CI/CD: SDK; production automation: SDK; one-off tasks: CLI") with programmatic permission callbacks (`can_use_tool`), native hook callbacks, per-call model+effort, streaming. BUT it is claude-only: codex/zhipuai seats still need CLI/SDK-of-their-own subprocess dispatch, and the existing multi-provider adapter symmetry is CLI-shaped. (agent-sdk/overview.md)
- **F14 — `--effort` values**: `low|medium|high|xhigh|max` (+`ultracode` v2.1.203+); peer flag to `--model`; not marked "print mode only" (inferred applicable to `-p`; `/effort <v>` prompt-embedded works in `-p` on v2.1.205+). (cli-reference.md, headless.md)

**Interpretation of F7+F8 for P8 (the tmux directive):** tmux earns its place as the **supervisor** (survival, observability, rescue) — but the *transport* must be headless `-p` with the command as the pane's initial process and completion by process exit + sentinel file. tmux-as-typed-TUI is the documented failure mode. This is a **refinement** of P8, not a rejection.

---

## 5. Open questions — routed to the Sol + GLM consults (owner is out; P11)

- **OQ-1 — Migration shape:** hard cutover (P4 says Agent-tool "no longer needed") vs seat-by-seat wiring with Agent-tool as a temporary fallback tier. Risk: a half-wired state is exactly the #422 failure mode; but a big-bang rewire of 2 workflows × 7 seats is a large blast radius.
- **OQ-2 — Child permission model:** per-seat `--allowedTools` whitelist (must be COMPLETE — an un-granted tool call ABORTS the run, F3b) vs `--permission-mode acceptEdits`/`dontAsk` vs `bypassPermissions` (trusting the hook layer, which is confirmed active in `-p`, F3). Worktree-shared approvals (F3c) offer a fourth path: pre-approve seat toolsets once in the main checkout. What is the right risk posture for autonomous headless children in a repo with wal-guard/security-guard hooks?
- **OQ-3 — Worktree ownership:** native `claude -p --worktree` (CLI owns creation; NOT auto-cleaned in -p; sweep rules apply) vs engine-managed `git worktree add/lock/remove` (executor owns lifecycle, uniform across providers — codex children get worktrees too). Which owns the lifecycle?
- **OQ-4 — Observation work-product extension:** how should agentic outputs (changed files, commit SHA, test results, doc paths) ride the envelope — a `work_product` field vs `parsed_payload` conventions? Interacts with #434 (schema versioning policy, owner-gated).
- **OQ-5 — Engine API shape:** keep `run_seat` synchronous (tmux-wait inside dispatch) vs a new launch/await split API. Sync keeps every existing caller (bakeoff, bench) working; async is what the orchestrator actually needs for parallel seats.
- **OQ-6 — Bundled Agent-tool agents** (`rawgentic-implementer`/`rawgentic-reviewer`): retire entirely, or keep as a declared fallback tier when tmux/executor is unavailable (the resolution ladder's `fallback` slot)?
- **OQ-7 — Cross-model agentic rights:** does a codex design/build candidate get `workspace-write` in its own worktree (full symmetry with claude children), or are mutating seats claude-only with codex kept read-only?
- **OQ-8 — Orchestrator polling discipline:** sentinel-poll cadence, per-seat timeout defaults, and how the WF2 resume protocol reconciles live tmux sessions found on resume (adopt? kill? re-attach?).
- **OQ-9 — Claude-adapter substrate:** stay on CLI subprocess (`claude -p`, symmetric with codex/zhipu adapters, tmux-hostable) vs migrate the claude adapter to the Agent SDK (docs-recommended for production automation, F13 — programmatic permission callbacks + native hook callbacks + streaming, but claude-only, python-package dependency, and harder to host in a user-attachable tmux session).
- **OQ-10 — Review session policy (owner-raised, 2026-07-17):** fresh-per-dispatch vs resumed sessions for sequential reviews (Step 8a per-task, Step 11, #393 pass-N). Recommendation: `fresh` default — (1) anchoring: a reviewer that remembers its prior verdict is biased to confirm it; (2) audit contract: a resumed session's real input is the invisible prior transcript, so `prompt_hash`/`context_hashes` no longer prove what the reviewer saw; (3) fallback-chain portability: fable→sol→sonnet can't resume across providers; (4) unfenced accumulated history widens the injection surface; (5) the wanted benefit (next review knows the last) is already served auditable by the dispositions-ledger pattern, generalized. **Benchable:** #449 adds one A/B cell — re-review quality fresh vs resumed — so the default gets settled by data. Decision: D-8.

## 5b. Consult synthesis (WF13, GPT Sol + GLM 5.2 — 2026-07-17; owner to ratify)

Both peers produced independent proposals (reports in `docs/reviews/peer-2026-07-17-orchestrator-executor-acceptance-criter-2026-07-17{,-glm}.md`). Resolutions below are **consult-derived, owner-ratification pending**:

- **OQ-1 (migration) — RESOLVED by synthesis of the fork.** Sol: atomic run-level cutover, never mix architectures within a run, Agent-tool is not a runtime fallback. GLM: seat-by-seat under a flag, fallback tier until the proving run, then hard cut. **Synthesis: a run-level architecture version — any single run is 100% executor or 100% legacy, never mixed (Sol's invariant); staging happens ACROSS runs (GLM's caution): prove seats in shadow/integration runs, then flip the default.** Bounded legacy window; refuse legacy resumes after archival.
- **OQ-2 (permissions) — CONVERGED, refined by glm review #3:** least-privilege per-seat grants + non-interactive denial, NEVER `bypassPermissions`; manifest validated for structural completeness BEFORE launch. **But an autonomous agent's full tool set is not enumerable a priori** — omitted grants hard-abort the run (F3b). Mechanism: grant **broad-but-gated tool categories** and enforce restrictions via **hook-layer deny-and-log** (a PreToolUse DENY returns feedback the agent can adapt to; an ungranted tool aborts). Unexpected calls degrade gracefully — deny-and-log by default, abort reserved for the forbidden-combinations class. Startup guardrail canary before any mutation; non-bare enforced by the canary (no opt-out flag exists — F3a). **Spike #454 (PR #462): CONFIRMED and sharpened.** Deny-and-log verified live: granted Bash + wal-guard PreToolUse deny → child received the denial as feedback, adapted, `result:success`. But the companion premise "an un-granted tool call aborts" is NOT reliable — under this host's `defaultMode:auto` un-granted tools are auto-approved and run with no gate except hooks (F3b revised). Net: enforcement rests on the **hook layer**, not grant completeness; the startup canary (F3a, AC-B3) is the load-bearing control, per provider × lane × permission profile, before any mutation. **Lane note (CRITICAL, #431 interaction):** a `CLAUDE_CONFIG_DIR` lane child loads plugin hooks ONLY if the lane dir is provisioned (plugin cache + `enabledPlugins`) — a fresh lane emits `plugins:[]` and fires 0 hooks, i.e. the whole guard layer silently absent; provisioning restores all 9 (both cells live, spike #454). Lane provisioning + the canary's `init.plugins[]` assertion are mandatory for lane seats.
- **OQ-3 (worktrees) — CONVERGED: engine-managed lifecycle** for every mutating provider (claude AND codex get identical isolation, deterministic paths, explicit cleanup, resume-safe cwd). Native `--worktree` demoted to a claude-only optimization to probe later. Sol adds: retain failed/dirty worktrees for diagnosis (with retention limits + secret-redaction policy); worktree population (.env etc.) needs an allowlist.
- **OQ-4 (work product) — CONVERGED:** typed `work_product` object (kind, worktree_path, base/head SHA, changed_paths[], documents[], tests[{command_digest,status,exit_code,report_ref}], promotion status), never overloaded into `parsed_payload`; provider-native output preserved separately; **the executor independently derives git changes/commit identity/exit evidence — never trusts the agent's self-report.** Schema version bump rides #434.
- **OQ-5 (API shape) — CONVERGED:** async at core (`launch/status/await/cancel/recover`), synchronous `run_seat` compatibility wrapper so bakeoff/bench/sequential callers keep working.
- **OQ-6 (bundled agents) — Sol: retire from runtime** (not a fallback); GLM: keep as declared fallback tier only until the proving run. Under the OQ-1 synthesis these agree: legacy runs use them until the flip; after archival they retire. Disposition of the agent definition files themselves = cleanup child.
- **OQ-7 (cross-model rights) — CONVERGED, CONDITIONAL:** rights follow **seat capability, not provider identity** — a codex build/design job gets workspace-write only inside its engine-managed worktree under the same gates/budget/audit. **Conditional on a spike** (glm #6: `codex exec -s workspace-write` is `--help`-confirmed only; the adapter has never run it — hardcoded read-only today) and on the per-provider containment canary (gpt #4: codex loads NO claude hooks — its guard layer is the codex sandbox + supervisor checks). **Codex mutating seats stay unsupported until both pass**; the proving run must include a codex mutating cell. **glm-#6 spike condition — DISCHARGED with a caveat (spike #452, PR #458).** `codex exec -s workspace-write` has now actually run: headless, subscription auth, no approval hang, parse-compatible with `parse_codex` (usage split, `agent_message`, process exit code identical to read-only; only additive `command_execution` items, which the parser ignores). Containment is real — Landlock (fs) + seccomp (net) via `codex-linux-sandbox`, NOT bwrap, so the `docs/codex-reliability.md` §3 userns/AppArmor issue does not apply — but **default-wide**: default writable roots are `{cwd} ∪ {all of /tmp} ∪ {$TMPDIR}`, so `/tmp`-resident engine worktrees are NOT isolated from each other by default (see the D-4 resolution note, §6b). The gpt-#4 canary condition is UNCHANGED and now has a concrete spec (spike report §5): it MUST include an out-of-worktree negative-control write that fails — the `-s workspace-write` flag alone does not prove worktree confinement.
- **OQ-8 (recovery) — Sol's criteria adopted:** durable job registry + run-prefixed tmux names; recovery ADOPTS a session only when identity, command digest, worktree, and capture dir all match; otherwise QUARANTINE for explicit cancellation. Supervisor distinguishes running / exited-without-sentinel / quota-paused / timed-out / orphaned.
- **OQ-9 (substrate) — CONVERGED:** CLI initially (provider symmetry, tmux-transparent), behind an adapter interface so an SDK implementation can swap in without changing workflow/audit contracts.
- **U-3 (utility fan-out) — Sol's rule adopted (matches P5 "everything"):** ALL model-based dispatch routes through executor jobs — named phase seats AND utility fan-out; pure deterministic orchestration logic stays in-process; **no second model-dispatch mechanism remains**. *(Owner confirm — this retires Agent-tool use inside WF2 Step 2 fan-outs too.)*
- **New design elements adopted from Sol:** (1) **Promotion model** — executor output is descriptive, never self-integrating; the orchestrator promotes the approved commit/patch into the canonical branch with base-SHA validation, clean-worktree requirement, explicit conflict result. (2) **Expected-dispatch manifest at run start** — refined per gpt #7: **immutable required job templates + an append-only authorization ledger** for dynamic work (fallback attempts, competitive candidates, loop-backs, retries, utility fan-out) — every dynamic expansion is authorized and recorded BEFORE launch with parent job, reason, attempt number, and budget linkage; the ledger freezes before final reconciliation; reconcile compares templates+ledger vs launch records vs Observations vs audit vs promotions; any mismatch blocks completion. (3) **Quota-pool pause semantics** — subscription exhaustion pauses the pool (preserving live state, resumable), never consumed as model-failure fallback attempts. (4) **Effort mapping recorded twice** — normalized policy value + requested provider-native value on every Observation.

## 5c. Adversarial-review disposition (WF5, both backends — all 16 ACCEPTED, folded in)

Reports: `docs/reviews/2026-07-17-orchestrator-executor-acceptance-criter-2026-07-17{,-glm}.md`. gpt: 10 (C1/H5/M4) · glm: 6 (H1/M4/L1); two independent duplicate catches (U-3 contradiction; F3 overstatement) — treated as high-signal convergence. No finding discarded; no reviewer conflict; the ambiguity flags (gpt #7, glm #3) were resolved by adopting the recommended mechanisms rather than deferring.

| # | Finding | Fix landed in |
|---|---|---|
| gpt 1 (Critical) | AC-E1 "survives usage-pause" contradicted F11 (child exits 1) | AC-E1 rewritten: quota_paused + persist + `--resume` relaunch |
| gpt 2 (High) | `tmux kill-session` ≠ process-tree kill | AC-E4: supervisor-owned process group, verify-dead, then Observation |
| gpt 3 (High) | "and/or" allowed post-hoc-only cost accounting; codex/zhipu capless | AC-B4: pre-launch atomic reservation + per-provider declared bound |
| gpt 4 (High) | codex loads no claude hooks — assumed shared gate layer | AC-B3 provider-neutral; codex mutating unsupported until canary/spike |
| gpt 5 + glm 1 (High) | §5b vs §6 U-3 contradiction (dual-dispatch ambiguity) | §6 U-3 rewritten, single policy per architecture version |
| gpt 6 (High) | worktree isolates checkout, not agent (no OS write boundary) | AC-B2: provider-native confinement required or launch rejected |
| gpt 7 (Medium) | expected-dispatch manifest vs dynamic loop-backs/fallbacks | §5b(2): job templates + append-only authorization ledger |
| gpt 8 (Medium) | observation.json publication not atomic/durable | AC-E3: tmp+fsync+rename+dir-fsync; malformed sentinel = audited state |
| gpt 9 (Medium) | no tmux capability preflight | AC-E1: preflight per environment, fail-closed |
| gpt 10 + glm 2 (Medium) | F3 "confirmed" overstated (platform ≠ project enablement) | F3 relabeled docs-stated/unverified; canary = mandatory launch gate |
| glm 3 (Medium) | tool-grant completeness unknowable a priori; aborts mid-run | OQ-2: broad-but-gated grants + hook deny-and-log (feedback, not abort) |
| glm 4 (Medium) | "pin non-bare" impossible — no opt-out flag exists | F3a: enforcement = startup canary + version drift-guard, not a flag |
| glm 5 (Medium) | AC-B2 wording contradicted OQ-3 resolution (native-first) | AC-B2 rewritten: engine-managed primary |
| glm 6 (Low) | OQ-7 workspace-write `--help`-confirmed only | OQ-7 marked CONDITIONAL on spike + canary |

## 6. Uncertainties for owner review (P11 — documented, not blocking)

- **U-1** — ~~Hooks in `-p`~~ RESOLVED → F3 (confirmed from docs). Residual: the `--bare` future-default flip (F3a) needs a drift-guard — new build item, not an uncertainty.
- **U-2** — **RESOLVED by spike #453 (PR #459 — claude 2.1.212, Linux/tmux 3.4): partial / NOT usable as the supervisor's spawn mechanism.** `claude -p --worktree` alone works exactly as documented (worktree at `.claude/worktrees/<name>/`, branch `worktree-<name>`, local-HEAD base fallback with no remote, no auto-cleanup on `-p` exit — all live-confirmed). Adding `--tmux` **reinstates the workspace-trust gate** that `-p --worktree` alone skips: exit 1, `Workspace trust not yet accepted…`, no tmux session, no worktree, no model call (unconfounded A/B/C isolation incl. `--tmux=classic`; a genuine docs gap — the worktrees page documents the `-p` trust-skip and never mentions `--tmux`). Independently, `--tmux` exposes no name/identity-injection surface (only accepted value: literal `classic`) — failing AC-E4's `run_id/seat/attempt` adoption requirement regardless of the trust gate. The engine-managed tmux supervisor module (AC-E1/E2) is the sole spawn path, now evidence-backed. Residual: live session naming past the trust gate unobserved (cannot change the verdict). Full log: `docs/planning/2026-07-17-spike-453-tmux-p-composition.md`.
- **U-3** — Utility fan-out: **RESOLVED in §5b (supersedes the earlier assumption here — both reviewers flagged the stale text, gpt #5 / glm #1):** ALL model-based dispatch — named phase seats AND WF2 Step 2-style utility fan-out — routes through executor jobs; pure deterministic orchestration logic stays in-process; after the run-level architecture flip **no second model-dispatch mechanism remains**. Each run's architecture version declares and enforces exactly one policy. *Owner ratification still pending (it retires Agent-tool use inside Step 2 fan-outs too).*
- **U-4** — Whether the interim seat table (review=fable etc.) stays authoritative for the wired path until #449 data lands. Assumed yes. **Assumption — confirm.**
- **U-5** — **RESOLVED via spike #456 (PR #461), CONFIRMED live: no cross-provider naming mismatch.** claude `low|medium|high|xhigh|max` (given, CLI 2.1.212). codex `model_reasoning_effort` accepts `none|minimal|low|medium|high|xhigh|max` (codex-cli 0.144.1 — live 400s naming the enum, `codex debug models` catalog, official config docs). GLM `reasoning_effort` (extra_body, zhipuai 2.1.5.20250725, glm-5.2, the live judge endpoint) accepts the **identical** 7-value enum (live 400 + `low`/`max` round-trips). All 5 normalized levels map 1:1 by string onto both providers; the real gap is **per-model support within codex** (gpt-5.5 rejects `max`, accepts `xhigh`; gpt-5.6 family accepts both) — GLM per-model variance unconfirmed (only glm-5.2 probed). Build rule: identity-map; gate the native value against the resolved model's supported set at dispatch; on unsupported, step down `max→xhigh→high→medium→low`; record BOTH normalized and native values per Observation (§5b OQ-4) — never a silent pass-through. Pre-existing live bug surfaced: `hooks/adversarial_review_lib.py` `_EFFORT_ALLOWED={low,medium,high}` excludes `xhigh` on a now-falsified premise (gpt-5.5's own 400 names `xhigh` accepted) — `RAWGENTIC_ADV_REVIEW_EFFORT=xhigh` silently clamps to `high` today; fast-follow recommended. Evidence: `docs/planning/2026-07-17-spike-456-effort-vocabularies.md`.

---

## 6b. Owner decision points (concentrated — everything that needs YOUR answer)

| # | Decision | Recommendation on the table | Source |
|---|---|---|---|
| **D-1** | Ratify the AC list (§2) as the target architecture | Ratify — traces 1:1 to your prompts; survived 2-model review | §1–§2 |
| **D-2** | ALL model dispatch through executor — including WF2 Step 2 utility fan-out (retires Agent-tool there too) | Adopt (Sol's rule; matches your P5 "everything") | §5b U-3 |
| **D-3** | Migration: run-level architecture version — a run is never mixed; staging happens across runs; bounded legacy window | Adopt (synthesis of Sol atomic × GLM staged) | §5b OQ-1 |
| **D-4** | Codex mutating seats: unsupported until containment spike + canary pass (claude-only mutation until then) — **spike #452: CONDITIONAL PASS, resolution note below** | Adopt (gpt #4 + glm #6 — codex has no claude-hook layer) | §5c |
| **D-5** | Observation `work_product` schema extension rides the #434 versioning decision (which is already owner-gated) — **RESOLVED 2026-07-18: #434 decided (b)** (schema_version bumps on breaking-for-old-copies changes; v1 frozen; consumers validate by declared version) and closed; `work_product` + AC-I1 fields ship as `schema_version: "2"` with W6 (#469); the #434 role fail-closed fix rides W1 (#464) | Decide #434 first or together | AC-D4 |
| **D-6** | Interim seat table (review=fable etc.) stays authoritative until #449 live data lands | Confirm (assumption U-4) | §6 |
| **D-7** | Sequencing: new epic for the wiring; #449 re-scoped to bench the wired path AFTER it exists; #448 children ride alongside — **SATISFIED 2026-07-18: epic #475 filed** (spike verdicts first, per this decision); #449 retitled as W10; #448 closed superseded | Confirm §7 order | §7 |
| **D-8** | Review seats: `session_policy: fresh` default (cross-review context via fenced dispositions-carry, not session memory); resume stays opt-in per seat | Adopt fresh default + one #449 A/B cell to settle by data | OQ-10, AC-B1 |

**D-4 spike result (#452, PR #458 — 2026-07-18): CONDITIONAL PASS.** `codex exec -s workspace-write` runs headless on this host with no approval hang and IS OS-enforced — Landlock (filesystem) + seccomp (network) via `codex-linux-sandbox`, NOT bwrap (the `docs/codex-reliability.md` §3 userns/AppArmor issue does not apply). **But the default sandbox is NOT worktree-confined:** default writable roots are `{workspace/cwd} ∪ {all of /tmp} ∪ {$TMPDIR}` — a mutating child in a `/tmp`-resident engine worktree wrote into the sibling worktree and the canonical checkout (live-verified; `$HOME` blocked). Worktree-only confinement was live-verified ONLY with three explicit overrides: `sandbox_workspace_write.exclude_slash_tmp=true`, `sandbox_workspace_write.exclude_tmpdir_env_var=true`, `sandbox_workspace_write.writable_roots=[<worktree>]`. Net: codex mutating seats stay claude-only until (a) the adapter launch composition pins those three overrides for every mutating seat (or worktrees relocate outside `/tmp`/`$TMPDIR`), AND (b) the AC-B3 containment canary (spike report §5: positive control + an out-of-worktree negative-control write that MUST fail) is implemented and gates each launch fail-closed. The spike is satisfied; the D-4 gate now rests on canary implementation, not further probing.

## 6c. Owner decisions 2026-07-18 (post-spike, pre-epic — recorded on #457)

| Q | Decision | Lands in |
|---|---|---|
| Q1 | GO — wiring epic filed after decomposition approval | epic #475 |
| Q2 | BOTH — codex confinement = 3 `sandbox_workspace_write.*` overrides pinned per mutating launch AND engine worktrees relocated outside `/tmp`/`$TMPDIR` | W2 #465 + W3 #466, proven per launch by W5 #468 |
| Q3 | SPLIT — #431 owns lane provisioning; the canary child owns the fail-closed per-lane `init.plugins[]` assertion | W5 #468 |
| Q4 | FILE+FIX NOW — sysop secret-scan worktree `.git`-file bug | shipped: sysop#24 / sysop#25 MERGED; fix live host-wide (canonical checkout updated + live-verified) |
| Q5 | Hooks + canary = the load-bearing control (F3b: grants don't gate on auto-mode hosts); add the un-granted-mutating-tool-under-non-auto-profile cell | W5 #468 |
| Q6 | SIGKILL residue proxy accepted for design; ONE controlled usage-limit probe | W9 #472 (timed near a pool reset) |
| #434 | Option (b) — `schema_version` bumps when a change breaks older vendored copies; v1 frozen | W6 #469 (v2 + normative policy text); role fail-closed part in W1 #464 |

## 7. Sequencing sketch (post-ratification)

1. **Epic:** orchestrator/executor wiring (the real #417, resurrected as its own epic — references this doc + the accountability trace in the superseded doc §1).
2. Children (dependency order): capability manifest + WIRED_SEATS/build-audit (D3) → agentic adapter profiles (B1/B4) → tmux supervisor + async dispatch (E1–E5) → worktree lifecycle (B2) → guardrail live-verification (B3) → Observation work-product + telemetry extension (D4/I1, after #434 decision) → WF2/WF3 skill rewiring (F1/F2) → **status surface (J1–J3, needs registry+capture live)** → proving run (F3, exercises the status surface too) → #449 live bench on the wired path (G1) → **baselines + alerts (K2/K3, needs I3 history + bench anchors)** → #448 config/setup/diagram children ride alongside (alert thresholds join #446).
3. **Realized 2026-07-18 as epic #475.** The filed children, dependency order (deps live in each child's body for the epic driver; epic checkboxes = queue):

| Issue | W | Title | Depends on |
|---|---|---|---|
| #464 | W1 | capability manifest + WIRED_SEATS full set + build-audit path (absorbs #434 part 2: unrecognized `role` fail-closed in `check_pre`) — **SHIPPED v3.52.0** (manifest-in-table ×7, attested build gate w/ launch-input binding, design single-dispatch refused; bake-off audit-spine wiring + authoritative plan-context carried to W7 #470) | — |
| #465 | W2 | agentic adapter profiles — conditional `--no-session-persistence`, codex sandbox-override pinning (Q2), effort gating | #464 |
| #466 | W3 | engine-managed worktree lifecycle outside `/tmp` + orchestrator-side promotion (Q2) | #464 |
| #467 | W4 | tmux supervisor, async dispatch, durable job registry, orphan reaper | #464 |
| #468 | W5 | fail-closed guardrail canary + `--bare` drift-guard (lane assertion per Q3; Q5 non-auto mutating cell) | #465 |
| #469 | W6 | Observation `work_product` + telemetry I1–I3, `schema_version: "2"` per #434(b) | #434 (decided + closed) |
| #470 | W7 | WF2/WF3 skill rewiring — the real #417 (**diagram REV expected**) | #465, #467, #468 |
| #471 | W8 | live run status surface | #467, #469 |
| #472 | W9 | proving run — real WF2, codex mutating cell, Q6 controlled quota probe | #470, #468, #466 |
| #449 | W10 | driver-bench on the wired path + `session_policy` fresh/resume A/B (re-scoped, retitled) | #472 |
| #473 | W11 | baselines + alerts from telemetry history + bench anchors | #469, #449 |
| #474 | W12 | migration flip + legacy retirement — **its PR closes the epic** | #472, #449 |

   Absorbed from closed epic #448, riding alongside: #445 (config seat table, aligns W1) · #446 (setup integration + alert thresholds, joins W11) · #447 (diagram child, coordinates with W7's REV).
4. #450 (ultracode) re-evaluated after wiring — its "gate-preserving fan-out" may collapse into the executor's parallel-seat capability.
