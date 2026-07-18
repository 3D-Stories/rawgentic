# WF2/WF3 ↔ phase_executor integration — seat-placement architecture

**Date:** 2026-07-17
**Status:** DRAFT for owner review (option **C** of the #449 triage fork; unblocks the "**A**" integration work)
**Author:** Claude (Opus 4.8), session 6d7297b7
**Precedes:** the real "#417" integration issue (to be filed after this doc lands)
**Grounds in:** epic #422 (CLOSED), `docs/planning/2026-07-16-per-phase-model-routing.md`, the shipped `phase_executor/` engine, and the WF2/WF3 skills as of plugin v3.51.0

---

## 0. TL;DR — the decision

Epic #422 built a model-seat **execution engine** but never connected WF2/WF3 to it (see §1). Before doing that wiring, one architectural fact settles *which* seats can even go there:

> **The phase_executor is a single-shot, read-only model-dispatch engine — not an agentic, tool-using, worktree runner.**
> Confirmed: `claude --print --model X --output-format json --no-session-persistence`, prompt on stdin, no cwd/worktree/allowed-tools setup (`phase_executor/adapters/claude_cli.py:26`); `codex exec … -s read-only -C <cwd> …` (`codex_cli.py:33`); zhipuai one call. Each adapter is `run(request) -> Observation` — one prompt, one completion.

So the integration is **not** "route everything through the executor." It is:

- **Single completions over provided context** → route through the executor. Gains: real `requested==actual` audit, a *real* effort knob, fallback chains, cross-model-invariant enforcement, competitive/bake-off.
- **Seats that need tools / a worktree / multi-turn iteration** → stay on the **Agent tool** (the executor as built literally cannot edit a file, grep the repo, or iterate).
- **The driver/orchestrator seat is the session itself** → never routed (a session cannot be its own subprocess).

**Recommended placement:**

| Seat (table) | WF2/WF3 phase | Placement | One-line why |
|---|---|---|---|
| orchestrator | the whole run | **Session — never routed** | the driver *is* the session |
| intake | Step 1 AC/triage synthesis | **Executor** | single completion over the issue text |
| analysis | Step 2 codebase map / blast-radius | **Agent tool** | must Read/Grep/Glob the repo |
| design | Step 3 design authoring | **Executor** (competitive) | single completion; already competitive in the table |
| design-critique | Step 4 quality-bar review | **Executor** | single completion over the design doc |
| plan | Step 5 decomposition | **Executor** | single completion over the design |
| build | Step 8 implementation | **Agent tool** (worktree) | edits files, runs tests, iterates — executor is read-only |
| build bake-off | Step 8 gate-flagged | **Hybrid** | executor generates candidate patches + judges; harness applies+tests |
| review | Steps 8a / 11 diff review | **Hybrid** — §4.6 | exploratory review needs grep; a bounded cross-model review pass is executor-routed |
| ship | Step 12+ PR/docs/push | **Session / Agent tool** | needs gh, git, file edits |

Net: **~4 seats move into the executor** (intake, design, design-critique, plan), **2 stay Agent-tool** (analysis, build), **2 are hybrid** (build bake-off, review), **1 is the session** (driver). Two audit worlds coexist by design (§5).

---

## 1. Why this doc exists (the gap)

Epic #422's title: *"route WF2/WF3 model seats … executed through a deterministic phase execution engine."* The engine shipped; **WF2/WF3 were never connected to it.**

Confirmed evidence:
- `git log -S executor_routing -- skills/` and `-S dispatch_seat -- skills/` are **both empty** — no skill has ever referenced the executor.
- `executor_routing_lib.WIRED_SEATS = frozenset({"ship","intake","plan"})` — and its only production consumers are `driver_bench_lib.py` (the bench) and `bakeoff_policy.py`. No workflow invokes it.
- The engine's audit machinery (`RoutingAuditLog`, `verify_post`, `reconcile_run` in `phase_executor/enforce.py`) is invoked only from `executor_routing_lib` (CLI) and `driver_bench_lib`. The one registered `PostToolUse` hook on `Task` dispatch is `wal-post` (WAL logging), not routing enforcement. So **a real WF2/WF3 run produces no `routing_audit.jsonl`** — the epic's acceptance ("routing audit reconciles, requested==actual throughout") is structurally unmeetable on the live path.

Root cause: the single integration child (#417) was narrowed at issue-authoring — the epic row said *"WF2/WF3 prose now just calls the executor per seat"* (`2026-07-16-per-phase-model-routing.md:293`), but issue #417 was filed/closed as *"fallback wiring, concurrency note, driver-seat guidance"* and shipped only prose (PR #441). The engine↔workflow seam became nobody's task.

**This doc settles the prerequisite the epic skipped: which seats belong in the engine at all.** The wiring itself is the follow-on "A" work (§7).

---

## 2. The two execution models (confirmed from code)

| Property | **Agent tool** (in-session subagent) | **phase_executor** (subprocess dispatch) |
|---|---|---|
| Invocation | harness `Agent` tool, `subagent_type` + `model:` | `claude --print` / `codex exec -s read-only` / zhipuai SDK, prompt on stdin |
| Tools available | full per agent type (Read/Edit/Write/Bash/Grep/…) | **none productive** — claude one-shot, codex read-only |
| Worktree isolation | yes (`isolation: worktree`, e.g. `rawgentic-implementer`) | no |
| Multi-turn / iteration | yes (a full agent loop) | no — single prompt→completion |
| Model control | per-call `model:` (real override) | per-seat `-m` (real) |
| **Effort control** | **record-only** — Agent tool has no effort param | **real** — `--effort` (claude), `-c model_reasoning_effort` (codex) |
| `requested==actual` audit | **none** | **yes** — innermost-envelope id (`claude_cli.py:55-60`), `verify_post`, fail-closed on absent identity |
| Fallback chains | none | yes — config chain, chain-aware cross-model skip |
| Cross-model invariant | none | yes — `forbidden_combinations` (never-Haiku, D9) |
| Competitive / bake-off | none | yes — `run_competitive` + `bakeoff_policy` glm judge |
| Context vs orchestrator | shares harness context management | isolated subprocess, own context load |
| Account/quota lanes | single session account | per-lane `CLAUDE_CONFIG_DIR` (#431), per-pool concurrency |
| Cost / latency | in-session, harness-amortized | fresh subprocess + separate context load per call |

**Reading of the table:** the executor's entire value proposition (audit, effort, fallback, cross-model) pays off exactly where a model *decision* must be trustworthy and routed — and where a single completion suffices. The Agent tool's value (tools, worktree, iteration) is mandatory exactly where the seat must *touch the repo* or *iterate*. The two are complements, not substitutes.

---

## 3. The decision axis (one question per seat)

> **Is this seat a single model completion over context I can pack into a prompt — or does it need tools, a worktree, or multiple turns?**

- **Single completion** → executor-eligible, and *should* go there for the audit + effort + fallback + cross-model gains.
- **Needs tools / worktree / iteration** → Agent tool (the executor cannot do it as built).
- **Is the driver** → the session.

Corollary — do **not** force-fit an agentic seat into the executor by pre-serializing the repo into the prompt. That trades the tool's strength (targeted exploration) for the engine's audit, and usually loses (a reviewer that can't grep for callers is a worse reviewer). Where both the audit *and* the exploration matter, use a **hybrid** (§4.6): keep the Agent-tool pass, add an executor-routed pass — the pattern WF5/WF13 already use with Codex.

---

## 4. Per-seat placement, with rationale

### 4.1 orchestrator / driver → **Session (never routed)** — confirmed constraint
The driver seat *is* the session that runs the workflow. A session cannot spawn itself as a subprocess. #422's own table already marks it "session model" and the driver-seat note is guidance-only (the harness owns the session model). **No change; not an executor seat.**

### 4.2 intake (Step 1 AC/triage synthesis) → **Executor**
Turning an issue body into confirmed ACs / a complexity read is a single completion over the issue text. Executor-eligible; gains audit + routing. Low blast. (Already in `WIRED_SEATS`.)

### 4.3 analysis (Step 2 fan-out) → **Agent tool** — confirmed disqualifier
Step 2 maps components, traces blast radius, inventories tests — it *reads the repo* (Serena/Grep/Glob). A read-only single completion cannot do this without serializing the whole repo into a prompt. **Stays Agent tool.** Effort stays record-only here.

### 4.4 design (Step 3 authoring) → **Executor (competitive)**
Design authoring is a single completion over the analysis + issue. The seat table already makes it **competitive** (sol vs opus, glm judge). `run_competitive` + `bakeoff_policy.make_glm_judge` exist for exactly this. Executor. This is the highest-value move — a routed, audited, cross-model-judged design.

### 4.5 design-critique (Step 4 quality-bar) → **Executor**
The quality-bar rubric review is a single completion over the design doc (the doc fits a prompt). Executor-eligible; gains cross-model routing (review seat = fable) + audit. (The opt-in adversarial-on-design already goes cross-model via WF5 — this makes the *base* critique routed too.)

### 4.6 review (Steps 8a / 11 diff review) → **Hybrid** — the judgment call
A code review both (a) reads a bounded diff and (b) *explores* — greps for callers, checks context the diff doesn't show. Pure-executor loses (b). Pure-Agent-tool loses the fable routing + `requested==actual` audit + cross-model guarantee.
**Recommendation:** keep the **Agent-tool exploratory reviewer** (`rawgentic-reviewer`, with tools) as the primary Step 11 gate, and **add an executor-routed bounded-diff review pass** for the cross-model + audited dimension — packing the diff + the reviewer's own gathered context into the prompt. Precedent: WF5 adversarial-review and WF13 peer-consult **already** route to a Codex/GLM subprocess — review-via-subprocess is proven here. This is the least risky way to get the audit without blinding the reviewer.

### 4.7 plan (Step 5 decomposition) → **Executor**
Plan decomposition is a single completion over the design. Executor-eligible; gains audit + routing. (Already in `WIRED_SEATS`.)

### 4.8 build (Step 8 implementation) → **Agent tool (worktree)** — confirmed disqualifier
Implementation edits files, runs the test suite, and iterates red→green. The executor is **read-only** (`codex … -s read-only`; claude `--print` with no cwd). It **cannot** implement. `rawgentic-implementer` runs `isolation: worktree` for exactly this. **Stays Agent tool.**
→ This directly explains the "build seat has no audit path" gap (handoff follow-up #1, `enforce.check_pre` hard-denies `role=="build"`): under this architecture that deny is **correct** — build is not an executor seat. Its audit surface is the `DISPATCH` line (#330) + `run-record.dispatches[]`, not `routing_audit.jsonl`. *(Owner confirm — §6.)*

### 4.9 build bake-off (Step 8 gate-flagged) → **Hybrid**
Confirmed shape (`bakeoff_policy.py:124`): candidates each produce a draft (patch/impl **text** — a single completion), then the harness applies+tests to produce deterministic `build_evidence`, then glm judges on that evidence. So: **executor generates the candidate patches + runs the glm judge; the Agent-tool/harness applies and tests.** Clean split; the winner's patch is applied by the Agent-tool implementer.

### 4.10 ship (Step 12+ PR/docs) → **Session / Agent tool**
Creating the PR, updating README/docs, pushing — all need `gh`, `git`, file edits. Not a single completion. **Session/Agent tool.** (In `WIRED_SEATS` today, but as a *routing-table* entry, not because Step 12 calls the executor — revisit in §6.)

---

## 5. How the two audit worlds coexist (the integration contract)

After wiring, a WF2/WF3 run has **two** dispatch surfaces, each with its own audit — by design, not by accident:

- **Executor seats** (intake, design, design-critique, plan, review-pass, bake-off candidates+judge): dispatched via `executor_routing_lib.dispatch_seat`; each writes a capture dir + a `RoutingAuditLog` record; `verify_post` enforces `requested==actual`; **run-end `reconcile_run`** checks expected-vs-recorded seat calls. This is where the design doc's *"WF2 accepts only executor result envelopes so uninstrumented dispatch can't sneak past the hooks"* (`2026-07-16-…:208-209`) applies — **scoped to executor seats only.**
- **Agent-tool seats** (analysis, build, ship, exploratory-review): audited by the existing `DISPATCH issue=<n> …` canonical line (#330) + `run-record.dispatches[]`.

The reconcile must therefore key off a **per-seat placement manifest** (this table as data), not "every seat must have a routing_audit record" — otherwise it would false-fail on the Agent-tool seats. That manifest is the natural home for §4 and dovetails with epic #448 (per-project phase-seat config).

---

## 6. Open questions / risks (owner input wanted)

1. **`WIRED_SEATS` mismatch.** Today `{ship, intake, plan}`. This doc says the executor-eligible set is `{intake, design, design-critique, plan, review-pass}` (+ bake-off). `ship` is a routing-table entry but Step 12 is not an executor call. → The wiring issue must reconcile `WIRED_SEATS` with §4. **Confirm the target set.**
2. **build-deny is correct-by-design?** §4.8 argues the `enforce.check_pre` `role=="build"` hard-deny is *right* (build isn't an executor seat). Confirm — vs. the handoff's framing of it as a gap to fix.
3. **Review hybrid cost.** §4.6 adds a second (executor) review pass. That's more tokens per PR. Acceptable, or route only high-risk diffs cross-model?
4. **Subprocess cost/latency.** Each executor seat = a fresh `claude -p` context load (no shared cache with the session). Bounded by the ~4–6 seats we route; quantify with #449.
5. **Effort for Agent-tool seats stays record-only** — unchanged, accepted constraint (analysis/build/ship never get a real effort knob).
6. **#449 re-aim.** Under this architecture the driver-bench should bench the **executor seats** (design/plan/critique/review/bake-off) — which is exactly what it dispatches. So #449 stays valuable, re-scoped to "bench the seats this doc places in the executor," and its data answers risk #4. Keep #449 after A, not before.

---

## 7. Follow-on work (the "A" this unblocks)

1. **The real "#417" (new issue / reopen #422):** wire the §4 executor-eligible seats through `dispatch_seat`; emit `routing_audit.jsonl` on a real run; add the run-end `reconcile_run` keyed off the placement manifest (§5). Acceptance = *one real WF2 run where design/plan/critique route through the executor, audit reconciles, `requested==actual`* — the epic AC, now actually meetable.
2. **Reconcile `WIRED_SEATS`** with §4 (add design + design-critique + review-pass; confirm ship/build).
3. **Placement manifest as config** — fold §4 into epic #448's per-project phase-seat table so the reconcile reads it as data.
4. **#449 re-scope** — bench the executor-placed seats; answer risk #4.
5. **Confirm the #429 build-deny** as intended (§6.2).

---

## 8. What is confirmed vs proposed

- **Confirmed (read from code):** the executor is single-shot/read-only (§0, §2); WF2/WF3 never call it (§1); the audit machinery's only callers (§1); build bake-off candidate shape (§4.9); the seat table (#422).
- **Proposed (this doc's decision, owner to ratify):** the per-seat placement (§4), the two-audit-world contract + placement-manifest reconcile (§5), and the follow-on sequencing (§7).
