Resolve model routing (optional, fail-open) right after `<config-loading>`, before any subagent dispatch. For each role this skill dispatches (`analysis`, `review`, `implementation`), resolve the configured model:
```bash
python3 hooks/model_routing_lib.py resolve \
  --workspace .rawgentic_workspace.json --project <name> --role <analysis|review|implementation>
```
Run once per role (three invocations total). Exit is always 0; stdout is a model name or `inherit`. If `hooks/model_routing_lib.py` is missing (e.g. a stale plugin cache), the invocation may exit non-zero — treat that, and any non-zero/absent output, as `inherit`. Carry each resolved value as a literal into later steps (fresh-shell rule). When a value is `inherit`, dispatch that role's subagents with NO `model:` parameter (session model). Otherwise pass `model: <value>` on every Agent dispatch for that role. A stderr warning is advisory — never treat it as failure.

For each role also resolve its effort tier with a second invocation appending `--effort`, printing the effort string or `none`; carry both the model and the effort as literals. Effort is role-wide — it does not scale with any per-task down-route (e.g. `select_impl_model`'s ceiling logic in Step 8, `references/steps.md`, is unaffected). When the resolved effort is `none`, dispatch exactly as today. When it is non-`none`: the Agent tool has no per-invocation effort parameter, so effort is carried dual-path — (a) pass it where the dispatch layer supports effort (the Workflow tool's `agent(prompt, {effort: <value>})` option, or a Codex dispatch's reasoning-effort flag), and (b) always record it in the dispatch's session-note/audit line (e.g. `dispatch <role>: model <model>, effort <effort>`) so the resolved tier stays observable where per-invocation delivery is impossible.

**Executor-dispatch contract (#470) — the PRIMARY tier.** Every per-phase model call dispatches through ONE skill-facing entry point — the executor `dispatch` CLI (all seats, one single entry point; no second entry point is ever named in prose, because the sync-vs-supervised split is an internal routing decision keyed on the staged launch profile):
```bash
python3 hooks/executor_routing_lib.py dispatch \
  --seat <seat> --prompt-file <brief-file> --run-id wf2-<issue>-<session> \
  --correlation-id <issue>-<step>-<slug> [--effort <tier>] [--timeout <s>] \
  [--context-file <path> ...] \
  [--gate-file <gate.json> --plan-file <impl-plan.md>] \
  --workspace <workspace-file> --project <name>
```
Build seats require BOTH `--gate-file` and `--plan-file` (the authenticated #429 gate decision plus the live implementation plan the context is minted from). The CLI derives the canonical `REQUIRED_PLAN_CONTEXT_KEYS` internally from `--plan-file` after authenticating the gate — the input is `--plan-file`, NEVER `--plan-context`, so no caller-assembled context object crosses the boundary.

**Exit taxonomy (shipped numbering preserved; 6 is ADDITIVE):** `0` ok · `2` malformed input · `3` availability (chain exhausted / quota timeout) · `4` enforcement denial (incl. `gate_stale_for_plan`) · `5` internal · `6` refused (`EXIT_REFUSED` — canary refusal; NEW, no renumber of the shipped #427/#464 codes; the competitive-only-seat error stays exit 2 as shipped). Exit → DISPATCH `outcome` mapping (normative):

| dispatch exit | DISPATCH `outcome` | condition |
|---|---|---|
| 0 | `ok` (or `retried` when a fallback attempt succeeded) | — |
| 2 malformed | `error` | terminal caller error |
| 3 availability | `error` after the orchestrator stops retrying; `dead` ONLY when it abandons a hung/vacuous supervised job (reap/quarantine) | retry policy is the orchestrator's |
| 4 enforcement (incl. `gate_stale_for_plan`) | `error` | terminal denial |
| 5 internal | `error` | terminal internal fault |
| 6 refused (canary) | `error` | terminal refusal |

**Per-attempt emission rule:** every executor ATTEMPT emits exactly one DISPATCH line once its terminal result is known — refusals and failures included (their line carries `outcome=error`/`dead` per the table). A SUCCESSFUL tmux dispatch's line waits for JobRegistry identity capture + the phase-2 pass before emission; a refused/failed attempt emits immediately at refusal. No attempt ever disappears from the audit trail. The producer is the executor result dict (`type=executor:<seat>`, `model=<actual_model>`, `resolution=primary`) on the primary tier, or the fallback (legacy) Agent-tool dispatch (`resolution=fallback`) as today.

**Mutating-engine allowlist (#470 §2a).** Mutating dispatch is codex-only until an FS-sandbox child ships: the `MUTATING_FS_SANDBOXED` allowlist in `hooks/executor_routing_lib.py` currently holds `{codex}` alone, so a mutating composition on any other engine — a mutating-claude composition — is refused at the supervised STEP 0 with exit 6 (the canary refuses before any process exists). Non-mutating seats are unaffected (canary policy derivation returns None).

**Seat mapping (per-phase → seat; exact seat ids come from the #445 routing table `executor_routing_lib.resolve_table`, cited here as the authority — this prose never restates per-seat models, so there is no second source of truth):**

| WF2 phase | seat | notes |
|---|---|---|
| Step 2 fan-out | `analysis` | concurrent executor dispatches of the `analysis` seat (D-2); ≤3 concurrent preserved |
| Step 3 design generation | bake-off (`competitive`) | competitive-only, never single-dispatch — **AC1 carve-out: this PR DECLARES the bake-off contract; the live competitive wiring + audit-stream production is proven in #472 (the proving run). Until then Step 3 design generation stays on its current mechanism — the one deliberate AC1 carve-out.** |
| Step 4 / 8a / 11 review | `review` / `review_fast` | per the #491 lens map (`<review-lens-routing>`, SKILL.md) — the security lens rides the strong `review` seat |
| Step 8 implementation | `build` | gate authentication + internally minted plan context (`--gate-file` + `--plan-file`, both mandatory) |
| Step 16 | local | no model call |

**Tier selection is per-RUN, at run start, never mixed.** Step 2 probes the executor tier once (import + routing-table resolve — the probe-parallelism idiom); the run declares its tier in session notes (`executor tier: primary` / `fallback (reason)`) and keeps it for the whole run. A MID-RUN executor failure is a handled hard failure surfaced via the ERROR protocol — never an automatic, silent per-dispatch downgrade to the Agent tool. An owner-approved tier-switch TERMINATES the current run — its run_id is finalized with its completed work — and starts a NEW run_id on the other tier, linked to the failed run in session notes; there is no same-run mixed-tier state, so audit and resume stay unambiguous. This extends the #417 no-silent-downgrade rule from chain entries to tiers.

**An executor seat is never a gate bypass — every mandatory gate (Steps 4, 8a, 9, 11, 11.5) runs with identical semantics whichever tier dispatches its model calls, and every EXECUTOR-tier build-seat dispatch requires the authenticated gate decision plus the internally minted plan context.** WF2/WF3 prose runs the complexity-gate step before any fallback-tier build dispatch.

**Bundled agent dispatch (#164) — the FALLBACK (legacy) tier.** The Agent-tool path below is the declared fallback tier: it dispatches with `rawgentic:rawgentic-implementer` / `rawgentic:rawgentic-reviewer` and carries `resolution=fallback` on the DISPATCH line. Until the W12 flip (#474) this tier remains a working, declared fallback — never the primary; do not retire it. The plugin ships two subagent definitions, auto-discovered from the plugin-root `agents/` directory and namespaced on install:
- `rawgentic:rawgentic-implementer` — implementation-task agent (`isolation: worktree`; mutating work runs in an isolated git worktree)
- `rawgentic:rawgentic-reviewer` — quality-gate review agent (read-heavy tools only: Read/Grep/Glob/Bash — no Write/Edit)

Both declare `model: inherit` because routing is per-project config a static definition cannot read: dispatch by passing `subagent_type` plus the resolved role model as the per-invocation `model:` parameter, which OVERRIDES the definition's frontmatter (documented resolution order: env var > per-invocation param > frontmatter > session model). Every per-step dispatch annotation in `references/steps.md` means exactly this contract — `review`-role dispatches use `rawgentic:rawgentic-reviewer`, `implementation`-role dispatches use `rawgentic:rawgentic-implementer`, `analysis`-role dispatches stay generic (no bundled analysis agent). Never-Haiku is enforced twice: in the definitions themselves and by the `select_impl_model` floor in Step 8 (`references/steps.md`). **Graceful fallback (AC4):** when the Step 2 `probe-parallelism` result is `serial-only` (worktree isolation unavailable — e.g. not a git repo), dispatch the same agent types WITHOUT relying on isolation and execute strictly serially; if the agent type itself is unavailable (stale cache, non-plugin install), fall back to the generic inline-prompt dispatch with the same brief — the routed model contract is unchanged in both fallbacks.

**Canonical DISPATCH audit line (#330).** The lowercase start-time line above stays as-is (observability only, never parsed). At the point each dispatch decision COMPLETES — or the orchestrator declares it dead/abandoned — ALSO append one uppercase canonical line carrying the issue number and all six schema fields, fixed key order, single-space-separated, one line:
```
DISPATCH issue=<n> role=<review|implementation|analysis|other> type=<subagent_type> model=<model|null> effort=<effort|null> outcome=<ok|error|retried|dead> resolution=<primary|fallback|generic>
```
Canonical regex (assembly's scoped grep + validator):
```
^DISPATCH issue=(\d+) role=(review|implementation|analysis|other) type=([A-Za-z0-9_.:/-]+) model=(null|[A-Za-z0-9_.:/-]+) effort=(null|[A-Za-z0-9_.:/-]+) outcome=(ok|error|retried|dead) resolution=(primary|fallback|generic)$
```
Emission rules:
- One line per SUBAGENT INVOCATION dispatched (not per attempt) — a multi-reviewer gate emits one line per reviewer (WF2 Step 8a's single accumulated wave of two reviewers = two lines; Step 11's two agents = two lines, #492).
- Write each line flush-left at column 0 as its own physical line — never inside a list item, blockquote, or fenced code block (the assembler greps `^DISPATCH` anchored to line start; an indented or bulleted line is rescued only into the MALFORMED count, never into `dispatches[]`).
- Retry semantics: a single retry of the SAME task/invocation is ONE line — retried-then-succeeded → `outcome=retried`; retried-and-still-failed → `outcome=error` — regardless of any model escalation on the retry. Two lines are written only when the workflow abandons one dispatch PATH for a different one (e.g. delegation abandoned for inline work): the abandoned path's terminal line plus the new path's line.
- A hung/vacuous dispatch abandoned by the orchestrator → `outcome=dead`. A dispatch that errors into a failure handler or a suspend still gets its canonical line (`outcome=error` or `dead`) BEFORE the handler/suspend proceeds — the failed dispatch is exactly the entry the audit most needs.
- `type`/`model`/`effort` values are stable slugs matching `[A-Za-z0-9_.:/-]+` (no spaces, no commas). Write the literal `null` when the role resolved `inherit` (model) or `none` (effort) — never an empty string or "unknown".
- Generic inline-prompt dispatches (no bundled agent type ran) use the stable `subagent_type` token `generic-<role>` (e.g. `generic-analysis`) and carry `resolution=generic`.
- `issue=<n>` is this run's issue number (scoping key — assembly greps the whole session-notes file for `^DISPATCH issue=<n> `). `DISPATCH` is uppercase so it can never collide with the lowercase start-time prose line.

Resolution decision table (maps the dispatch ladder to #329's vocab):

| Dispatch path | resolution |
|---|---|
| Named agent type ran worktree-isolated | `primary` |
| Named agent type ran WITHOUT isolation (`serial-only` degradation) | `primary` — the NAMED type still ran; `fallback` means a SUBSTITUTE type ran, which did not happen |
| Named agent type unavailable AND no bundled substitute tier is declared (or the substitute also fails to resolve) → generic inline-prompt dispatch | `generic` (`subagent_type` = `generic-<role>`) |
| A bundled SUBSTITUTE agent type ran in place of an unavailable named type | `fallback` — no WF2 producer today; WF3 Step 9's declared per-slot chain (#331) produces it at tier 2 |

**Seat fallback chains + circuit breaker (#417).** Each seat's model is a config-declared chain (the routing table's `primary` + `chain[]`, e.g. the interim `review` chain fable → sol → sonnet, #426), tried in order on an AVAILABILITY failure; the skip is **chain-aware** — it drops any entry that would violate the artifact's cross-model invariant, never blindly the literal next entry. **A chain that exhausts its eligible entries is a handled hard failure, never a silent downgrade** — the run surfaces it (the ERROR protocol), it does not quietly proceed on an unrouted model.

**Concurrency ceiling (#417).** Keep **≤ 3 concurrent Claude subagents** (the standing cap); when the driver itself is dispatching Claude work alongside them, reserve one slot for the driver → an **effective working ceiling of 2**. This is a PROSE rule — no programmatic clamp exists — so honor it when fanning out (Step 8a's two reviewers and Step 11's two agents (#492) sit within the cap; a cross-engine candidate on the codex/zhipu pool consumes no Claude slot). `queued_ms` on an Observation records any queue wait so a stall is diagnosable.

**Driver seat — guidance, not enforcement (#417).** opus-4-8 is the recommended session/orchestrator model: the strong-model-on-top reliability floor (weak-model-on-top collapses; the role is unbenchmarked until the driver-bench, #430, reports). This is GUIDANCE only — the harness owns the session model; this block cannot set it, and nothing here fails a run whose session model differs.
