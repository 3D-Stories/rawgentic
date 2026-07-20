# #470 — WF2/WF3 skill rewiring to executor dispatch (W7, epic #475) — Design r5

Date: 2026-07-20 · Author: orchestrator session b2647105 · Status: r5 (post-Step-4 pass-3
spec-tightening, owner override of the mechanical design-fold; dispositions ledger 19 entries;
budgets: design 2/2 EXHAUSTED, spec_tighten 1/2, global 3/3 EXHAUSTED)
r5 amendments (pass-3): §1 provisioning source named (dispatch CLI provisions Supervisor +
worktree via WorktreeManager; resumed runs re-derive from registry); error-handling exit labels
corrected (3=availability, 5=internal, 6=canary refusal); TOCTOU freeze hardened (snapshot
immutable after bind; pane_runner recomputes BOTH digests); phase-2 probes side-effect-free by
construction (disposable credential-free workspace, harmless payloads); compensating-transitions
corrected (refusal creates nothing — only spawn failure releases; probe session holds its own
short-lived permit); `claude -p` init-event evidence expanded (#454 fixture citation).
r4 deltas (R1′–R5′): controls UNIFIED on one path — the `dispatch` CLI is the single skill-facing
entry for ALL seats; a mutating profile routes internally through gate-auth → context-mint →
phase-1 canary → trusted pre-spawn probe session → `require_canary` → `supervisor.launch`.
Phase-2 evidence comes from the probe session (pre-spawn, trusted process), never in-pane.
Context minted from three named sources; gate decision digest-binds the plan and dispatch
refuses on live-plan mismatch. EXIT_REFUSED=6 additive. One DISPATCH line per attempt.
r3 deltas retained: executable black-box gate tests, Step 3 bake-off carve-out, version-pin
enumeration, tier-selection-per-run.
Peer consult: gpt/Codex, blind both ways (r1 on disk before proposal read). Peer contributions
(provenance, adopted into r2): tier-selection-at-run-start (never auto-fallback mid-run),
DISPATCH-after-identity-capture ordering + compensating transitions, canary→spawn TOCTOU
freeze sentence, trusted-evidence-factory-only rule, D-12 CAS serialization. Peer's REV number
(3.76.0) was stale — 3.78.0 stands.
Doc of record: `docs/planning/2026-07-17-orchestrator-executor-acceptance-criteria.md` (AC-F1/F2, D-2, OQ-6, U-3)

## Problem

phase_executor (#464–#469) is built; WF2/WF3 skill prose still dispatches every phase via the
Agent tool. #470 rewires the prose so per-step phase dispatch routes through the executor —
including WF2 Step 2 utility fan-out (D-2) — while ALL mandatory gates keep identical semantics
(AC-F2), and wires the two shipped-but-unwired safety pieces into the dispatch choke-point:
`require_canary` (#468) and orchestrator-minted plan-context (#464 carry).

## Approaches considered

**A. Prose-only rewire (defer canary + plan-context wiring to #472/#474).**
Pros: small, fast, pure-markdown + drift-guard updates. Cons: violates the written contract —
`canary.py` docstring and #468's changelog both assign the choke-point wiring to #470
("#470 wires require_canary/build_observation into the production dispatch choke-point");
#472's proving run would then prove an UN-guarded path. Rejected.

**B. Full wire: prose rewire + dispatch choke-point hardening (canary + minted plan-context) + resume/registry prose + gate-preservation test.** The issue's actual scope. Chosen.

**C. B plus retire Agent-tool prose entirely now.**
Rejected: OQ-6 resolution (AC doc §5b, line 220) keeps the Agent-tool path as the declared
legacy/fallback tier until the W12 flip (#474). Deleting now strands a run on an executor
failure with no sanctioned fallback and pre-empts #474's flip semantics.

## Chosen design (B)

### 1. Skill-prose dispatch contract (AC-F1, D-2)

**Source of truth:** rewrite `shared/blocks/model-routing-resolve.md` (synced only into
implement-feature per MANIFEST) into an **executor-dispatch contract**; re-run
`scripts/sync_shared_blocks.py`. WF3's bespoke inline block in `skills/fix-bug/SKILL.md`
(role=review-narrowed) is rewritten in the same shape independently.

New contract shape (per phase-seat dispatch, replacing "Agent tool + subagent_type + model:"):

```
Primary (executor) tier — dispatch seat <seat> via the SINGLE entry point (all seats):
  python3 hooks/executor_routing_lib.py dispatch \
    --seat <seat> --prompt-file <brief-file> --run-id <wf2-<issue>-<session>> \
    --correlation-id <issue>-<step>-<slug> [--effort <tier>] [--timeout <s>] \
    [--context-file <path> ...] \
    [--gate-file <gate.json> --plan-file <impl-plan.md>]   # build seats: BOTH mandatory
    --workspace <workspace-file> --project <name>
Exit taxonomy (shipped numbering preserved; 6 is ADDITIVE): 0 ok · 2 malformed input ·
3 availability (chain exhausted / quota timeout) · 4 enforcement denial · 5 internal ·
6 refused (canary refusal; NEW — EXIT_REFUSED, no renumber of #427/#464 codes;
competitive-only stays exit 2 as shipped).
Fallback (legacy) tier — Agent tool with rawgentic:rawgentic-implementer /
rawgentic:rawgentic-reviewer: resolution=fallback on the DISPATCH line. Until the
W12 flip (#474) this tier remains a working, declared fallback — never the primary.
```

**Internal routing (R1′ — both controls, one path):** `dispatch` inspects the resolved target's
staged `LaunchProfile`. Non-mutating profiles run the existing synchronous path (`run_seat` /
`dispatch_real`) unchanged. A MUTATING profile routes to the supervised branch INSIDE the same
CLI call, in this exact order, all in the trusted orchestrator-side process:
1. gate authentication (`verified_decision`, incl. the R4′ plan-digest check),
2. internal plan-context mint (§2b),
3. stage-and-bind the immutable snapshot + pane spec (digest, dispatch_nonce),
4. phase-1 canary (local evidence) — refuse before any process exists,
5. trusted pre-spawn probe session (§2a phase 2) — completes runtime evidence,
6. `require_canary(composition, evidence)` — full policy, exactly once,
7. `supervisor.launch()` — pane spawn; identity captured in JobRegistry.

**Provisioning (pass-3 amendment):** the dispatch CLI itself provisions what `launch` requires —
it constructs the `Supervisor` (quota coordinator, registry root, capture root, tmux socket —
the same config surface the CLI already resolves) and provisions the seat's git worktree via
`WorktreeManager` (deriving `WorktreeIdentity` + `WorktreeHandle`); on a resumed run the handle
is re-derived from the registry by run_id+seat instead of re-provisioned. These
`WorktreeManager`/`Supervisor` dependencies ride the file-changes table's
`executor_routing_lib.py` row.
No skill prose ever names a second entry point; the sync-vs-supervised split is an internal
routing decision keyed on the staged profile, so neither control can be skipped by calling
"the other" path — there is no other path.

**Tier selection is per-RUN, at run start, never mixed (peer-adopted).** Step 2 probes the
executor tier once (import + routing-table resolve — the probe-parallelism idiom); the run
declares its tier in session notes (`executor tier: primary` / `fallback (reason)`) and keeps it
for the whole run. A MID-RUN executor failure is a handled hard failure surfaced via the ERROR
protocol — never an automatic, silent per-dispatch downgrade to the Agent tool. **Tier-switch
semantics (R5′, pass-2 aM):** an owner-approved switch TERMINATES the current run — its run_id
is finalized with its completed work — and starts a NEW run_id on the other tier, linked to the
failed run in session notes; there is no same-run mixed-tier state, so audit and resume stay
unambiguous. This extends the #417 no-silent-downgrade rule from chain entries to tiers.

- **Seat mapping table** (prose): Step 2 fan-out → `analysis` seat; Step 3 competitive design →
  bake-off (competitive-only; never single-dispatch) — **explicit AC1 carve-out (F5, owner R6):
  Step 3's prose DECLARES the bake-off contract in this PR, but its live competitive wiring +
  audit-stream production is proven in #472 (the proving run); until then Step 3 design
  generation stays on its current mechanism, named as the one deliberate AC1 carve-out**;
  Step 4/8a/11 review lenses → `review` / `review_fast` seats per #491 lens map; Step 8
  implementation → `build` seat (gate authentication + internally minted plan context, §2b);
  Step 16 stays local (no model call). Exact seat ids from the #445 routing table
  (`executor_routing_lib.resolve_table`) — prose cites the table as authority, never restates
  per-seat models (no second source of truth).
- **DISPATCH audit line (#330): format unchanged.** Producer sentence rewrites: the line is
  emitted when the executor dispatch completes (from the result dict: `type=executor:<seat>`,
  `model=<actual_model>`, `resolution=primary`), or from the fallback tier as today
  (`resolution=fallback`). **Exit→outcome mapping table (aM5, normative):**

  | dispatch exit | DISPATCH `outcome` | condition |
  |---|---|---|
  | 0 | `ok` (or `retried` when a fallback attempt succeeded) | — |
  | 2 malformed | `error` | terminal caller error |
  | 3 availability (chain exhausted / quota timeout — shipped numbering) | `error` after the orchestrator stops retrying; `dead` ONLY when the orchestrator abandons a hung/vacuous supervised job (reap/quarantine) | retry policy is the orchestrator's |
  | 4 enforcement (incl. `gate_stale_for_plan`) | `error` | terminal denial |
  | 5 internal | `error` | terminal internal fault |
  | 6 refused (canary, NEW additive code) | `error` | terminal refusal |

  **Emission rule (R5′, replaces the r3 ordering rule):** every executor ATTEMPT emits exactly
  one DISPATCH line once its terminal result is known — refusals and failures included (their
  line carries `outcome=error`/`dead` per the table). A SUCCESSFUL tmux dispatch's line waits
  for JobRegistry identity capture + phase-2 pass before emission; a refused/failed attempt
  emits immediately at refusal. No attempt ever disappears from the audit trail.
- **Step 2 fan-out (D-2):** the fan-out prose swaps "concurrent subagents (Agent tool)" for
  "concurrent executor dispatches of the `analysis` seat" (background Bash invocations of the
  dispatch CLI, ≤3 concurrent preserved). Same briefs, same synthesis barrier.
- **Concurrency/driver prose** (pinned sentences: "≤ 3 concurrent Claude subagents",
  "effective working ceiling of 2", "strong-model-on-top reliability floor", chain-exhaustion
  hard-failure sentence) carry over verbatim into the new block — the pins keep holding.

### 2. Dispatch choke-point hardening (in `hooks/executor_routing_lib.py` + `phase_executor`)

**2a. `require_canary` wiring (#468 handoff) — REWRITTEN r4 (pass-2 breaker, owner R1′+R2′).**

**Single-path controls (R1′):** the mutating branch lives INSIDE `dispatch` (§1 internal
routing). All canary work happens in the trusted orchestrator-side process, strictly BEFORE the
task pane exists:

- **Phase 1 — local evidence:** after stage-and-bind (immutable snapshot + pane spec, fresh
  `dispatch_nonce`, spec/snapshot digest), evaluate the LOCAL checks (registration_digest over
  the staged snapshot, plugin_version pin, hooks_registration parse, final_argv `--bare`
  absence) via the trusted collector `canary_evidence.py`. Refusal → `CanaryRefused` before any
  process exists.
- **Phase 2 — trusted pre-spawn probe session (R2′):** the supervisor runs a SHORT-LIVED,
  NON-mutating probe session against the SAME staged snapshot (same config root, same hooks,
  same lane): a `claude -p`-class probe invocation whose stream yields the init event
  (`init_plugins`) and whose scripted probe commands exercise each mutating matcher class to
  observe hook-origin denials (`probes`). **Side-effect-freedom by construction (pass-3
  amendment, verifier-corrected):** the probe session runs in a DISPOSABLE, credential-free
  workspace (its own throwaway worktree; no repo write access, no secrets in env), and every
  probe payload follows the shipped `ProbeOutcome` contract (canary.py:88-93): intrinsically
  non-mutating via an OS-NON-WRITABLE target — the write can never succeed, so an OS denial
  can never masquerade as a hook success. Guard-absence therefore surfaces via the
  `positive_deny_unproven` branch (no hook-origin deny observed), and a hook-origin deny is
  the only PASS signal; the workspace disposal is defense-in-depth, not the detection
  mechanism. The probe's evidence is collected by the trusted
  process (the supervisor reads the probe stream directly — spike #454's output contract),
  stamped with the composition's `dispatch_nonce` + `snapshot_digest`, and completes the
  `CanaryEvidence`. Then `require_canary(composition, evidence)` runs — full policy, exactly
  once, strictly before the task spawn. This closes the lane-empty hole (`plugins: []` ⇒ 0
  hooks) and keeps `canary.py`'s "immediately before spawn" contract TRUE as written (no
  docstring weakening needed).
  **Honest ceiling (named):** the probe attests the staged lane/hook CONFIGURATION, not the
  literal task process. What makes that sound is the snapshot binding: probe and task both run
  from the SAME digest-frozen staged snapshot, and the TOCTOU freeze below forbids any change
  between the canary pass and the task spawn. In-pane collection (the rejected alternative)
  would have put the trusted collector inside the mutating pane process — evidence provenance
  would be self-reported by the thing being guarded.
- **Refusal mapping:** `CanaryRefused` (either phase) → `EXIT_REFUSED` (6, additive) with the
  CanaryResult violations in the error payload; the refusal is audited and NEVER proceeds.
  Non-mutating seats: canary not applicable (policy derivation returns None) — documented,
  tested.

**TOCTOU freeze:** between the `require_canary` pass and `supervisor.launch()`, NO route
resolution, mutable file read, or command rewriting may occur. The staged snapshot becomes
IMMUTABLE at stage-and-bind (read-only tree; any post-bind modification is a refusal), and
`pane_runner` re-verifies BOTH digests before executing — the pane-spec digest AND a recomputed
digest of the staged snapshot it is about to run from (pass-3 amendment: binding a snapshot
digest into an unchanged spec does not prove the snapshot's current contents) — **both
pane_runner checks are NEW code in this PR** (today only the supervisor's recovery path
verifies identity), stated per pass-2 F6. Evidence comes ONLY from the internal trusted collector — no
caller-supplied evidence input exists at any CLI boundary.
**Compensating transitions (pass-3 corrected):** at canary-refusal time (either phase) NO task
permit or JobRecord exists yet — both are created inside `launch()` (step 7, after the canary) —
so a refusal creates nothing and has nothing to release; the refusal is audited and the
structured exit returned. Only a SPAWN failure (inside `launch`) releases the just-acquired
permit and finalizes the just-created record. The probe session acquires its OWN short-lived
quota permit (it is a real provider invocation and must respect the pool ceiling), released on
probe completion — probe permit accounting is part of Task 3's tests. Probe-session failure is
a refusal (fail-closed), never a skip.

**2b. Internally-minted plan-context (#464 carry) — REWRITTEN r4 (pass-2 F3/aH3, owner R3′+R4′).**
The dispatch CLI's `--plan-context <ctx.json>` input is REPLACED by `--plan-file <impl-plan.md>`:
`dispatch` derives the canonical REQUIRED_PLAN_CONTEXT_KEYS (`{risk_level, complexity, lines,
file_count}`, `complexity_gate.py:46-47`) internally, after authenticating the gate — no
caller-assembled context object crosses the boundary. **Sources, named per key (R3′ — the plan
file alone cannot mint all four):** `risk_level` + `file_count` from the plan file via
`plan_lib.parse_tasks`; `complexity` from the gate decision's own recorded issue-complexity (the
gate authenticated it — one source of truth, no re-fetch); `lines` from the gate decision's
recorded `plan_est` estimate. The mint is therefore: plan file (live) + gate decision (frozen,
authenticated) — and the cross-check verifies the live plan still matches what the gate froze.
**Enforced freshness binding (R4′ — replaces the r3 "run discipline" honest-limit):** the gate
decision RECORDS the plan-file content digest it was minted against (`complexity_gate` extension,
this PR); `dispatch` recomputes the live plan file's digest and REFUSES on mismatch
(`gate_stale_for_plan`, enforcement class) — a revised plan mechanically forces re-running the
complexity gate. The stale-PAIR residue shrinks to: a stale gate + the byte-identical stale plan
still on disk — i.e. nothing changed, which is exactly the case where the old gate IS current.
Audit trail: dispatch records (gate `policy_digest`, plan digest, run_id, correlation_id)
alongside the receipt.

**2c. #467/D-12 quota-permit rider:** `supervisor.recover()`-adoption re-establishes the quota
permit under the adopting orchestrator pid, with compare-and-swap/serialized ownership-generation
update (peer-adopted: an ownership race during recover-adopt must not over-admit). Contained
change in `supervisor.py`/`quota.py` + regression test (adopted job's permit no longer
stale-reaped; pool ceiling holds across the recovery boundary, including the race).

**2d. Bake-off / RoutingAuditLog reconciliation (#464 carry, first clause):** DECLARE the
two-stream reconciliation formally in `docs/run-records.md` + the audit doc rather than wiring the
competitive path into RoutingAuditLog in this PR (the competitive path has no live consumer until
#472; a doc-declared reconciliation satisfies the carry's "either" arm). Recorded as an explicit
decision in the PR body.

### 3. Gate-preservation invariant test (AC2, #450) — REWRITTEN r3 (breaker aH1/F3, owner R3+R5)

New `tests/test_gate_preservation.py`, two layers:
- **Prose layer:** anchors ONE canonical sentence added to the new shared block: "An executor
  seat is never a gate bypass — every mandatory gate (Steps 4, 8a, 9, 11, 11.5) runs with
  identical semantics whichever tier dispatches its model calls, and every EXECUTOR-tier
  build-seat dispatch requires the authenticated gate decision plus the internally minted
  plan context." (single-sentence anchor, section-sliced, whitespace-normalized — the second
  clause is executor-scoped per F3: the fallback tier's control is the tier-independent PROSE
  gate — a separate anchored sentence asserts WF2/WF3 prose runs the complexity-gate step
  before any fallback-tier build dispatch.) WF3 corpus carries the same sentences (bespoke block).
- **Mechanical layer — executable black-box (R3+R5′):** subprocess invocations of the real CLIs
  where the seam allows, in-process harness with injected collectors where it does not (named
  explicitly per pass-2 F6):
  1. build-seat dispatch WITHOUT a valid gate file → structured refusal BEFORE any provider
     launch (stubbed dispatch_real never called) — subprocess;
  2. build-seat dispatch with a tampered/stale gate → `gate_tampered`; with a live plan whose
     digest differs from the gate's recorded digest → `gate_stale_for_plan` — subprocess;
  3. mutating-profile dispatch routes to the supervised branch and runs gate-auth + mint +
     canary IN ORDER before launch (in-process harness, injected probe-session collector —
     the probe seam is an injected reader exactly like `dispatch_real`);
  4. phase-1 canary refusal → no process created; probe-session (phase-2) refusal → no task
     pane created; NEITHER has a task permit or JobRecord to release (both are created inside
     launch(), after the canary — pass-3 corrected contract; in-process, injected evidence);
  5. SUPERVISED path: successful mutating stub asserts `check_pre` receipt exists before task
     work, the receipt is attached to the job record, and `verify_post` runs on the final
     Observation (pass-2 aH1 — the mutating path gets its own executable assertions);
  6. sync path `check_pre`/`verify_post` — already covered by #427/#464 tests, cross-referenced
     not duplicated.

### 4. Resume protocol (AC3)

- **WF2:** extend `references/state-and-resume.md`: on resume, after the notes-cascade, query the
  executor `JobRegistry` for live jobs keyed by the run's `run_id` (`supervisor.recover(run_id)`:
  identity-matched live jobs are ADOPTED — with the D-12 permit re-establishment — mismatches
  quarantined per `classify_recovery`); prose names tmux session identity as the adoption key.
  `resume_lib.py` gains a `--registry-state` input (present/absent/live-jobs) with new cascade
  cases + tests. Content pin in `test_resume_lib.py` updated.
- **WF3 (asymmetry, scope-honest):** WF3 gets a minimal registry-aware resume note in its
  steps.md branch-resume line (query registry for live jobs before re-dispatching a review seat),
  NOT a new full cascade file — WF3's resume surface today is one line; building a parallel
  state-and-resume.md is #474-adjacent scope creep. Named as an explicit scope decision.

### 5. Diagram REV (AC4)

REV 3.78.0 on top of #447's 3.75.0 seat-routing REV: WF2 stations for Steps 2, 3, 4, 8, 8a, 11
gain the executor-dispatch delta (`rev:{delta, refs:[470]}`, overrides form — steps:null), routing-mode
panel notes flip from "executor-wired (static classification)" to "executor-dispatched (primary
tier)"; provenance footer `@ plugin 3.78.0`; snapshots regenerated full-page light+dark 1440px;
`test_diagram_newest_rev_matches_plugin_version` re-pinned. Per `rev-diagram` recipe.

### 6. Drift-guard updates (AC1 tail)

Every guard the rewire reddens is updated to pin the NEW canonical sentence (never deleted):
`test_model_routing_resolve_prose.py` (new contract sentences incl. carried-verbatim pins),
`test_wf2_parallelism.py` (Step-2 regex → executor-dispatch phrasing),
`test_bundled_agents.py::test_wf2_references_both_agent_types` (thresholds retuned to the
fallback-tier occurrence count; the agent-definition pins unchanged — files stay),
`test_wf2_clarity.py`/`test_wf3_clarity.py` DISPATCH grammar (format unchanged — producer
sentences re-anchored), `test_shared_block_drift.py` (green after sync re-run),
`test_resume_lib.py` (new cascade cases + content pin),
`test_workflow_diagram.py::test_diagram_newest_rev_matches_plugin_version` (REV 3.78.0 linkage,
F6), `test_canary_digest_pin.py` (version + registration-digest re-pins).

## File changes (summary)

| Area | Files |
|---|---|
| Prose | `shared/blocks/model-routing-resolve.md` (+sync into implement-feature SKILL.md), `skills/implement-feature/references/steps.md`, `references/run-record.md`, `references/state-and-resume.md`, `skills/fix-bug/SKILL.md` (bespoke block), `skills/fix-bug/references/steps.md`, `docs/run-records.md` |
| Code | `hooks/executor_routing_lib.py` (supervised internal branch, EXIT_REFUSED=6, --plan-file mint, gate_stale_for_plan), `phase_executor/src/phase_executor/canary_evidence.py` (new: collector + probe-session reader), `phase_executor/src/phase_executor/pane_runner.py` (NEW spec-digest re-verify), `hooks/complexity_gate.py` (plan-digest recording), `supervisor.py`+`quota.py` (D-12 CAS), `hooks/resume_lib.py` |
| Agents | `agents/rawgentic-implementer.md`, `agents/rawgentic-reviewer.md` — frontmatter/prose annotated "legacy fallback tier (OQ-6, retires at W12)" |
| Tests | `tests/test_gate_preservation.py` (new), the §6 guard updates, new unit tests for 2a/2b/2c |
| Diagram | `docs/workflow-diagram.html` REV 3.78.0 + `docs/assets/` snapshots |
| Release | version → 3.78.0 (minor, feat) on the FOUR pinned surfaces (F6 enumeration): `.claude-plugin/plugin.json`, `plugins/rawgentic/.codex-plugin/plugin.json`, `tests/hooks/test_adversarial_review_registration.py::test_plugin_version_bumped`, `phase_executor/src/phase_executor/canary.py::EXPECTED_PLUGIN_VERSION` (guarded by `test_canary_digest_pin.py`; the canary REGISTRATION digest re-pins too if hooks.json/enforcer scripts change) — plus the diagram provenance footer `@ plugin 3.78.0` (fifth string, separately guarded by `test_diagram_newest_rev_matches_plugin_version`, listed in §6). README changelog, docs |

Estimated diff: >500 lines (prose-heavy). **Single PR** — the pieces are not independently
shippable (prose that references unwired code, or wired code no prose invokes, both leave main
misleading); size is acceptable for a prose-majority diff. Flagged per Step-3 rule.

## Error handling / failure modes

- Executor tier availability is decided at the RUN-START probe only (aH3 reconciliation):
  ImportError or a missing routing table AT THE PROBE selects the fallback tier for the whole
  run, recorded with its reason. After a tier is declared, exit 3 (availability /
  chain-exhaustion), exit 5 (internal), and exit 6 (canary refusal) are handled hard failures
  surfaced via the ERROR protocol — never an automatic mid-run tier switch.
- `CanaryRefused` → exit 6 (EXIT_REFUSED) with violations; the run surfaces it via the ERROR
  protocol (a refused mutating dispatch is a blocker, not a retry).
- Plan-context: internally minted per dispatch (§2b); stale-coherent-pair hole closed; honest
  limit stated in §2b (gate freshness is run discipline, re-gate on plan revision).
- Recovery: adopted jobs re-permit under the adopting pid with CAS (D-12); quarantined jobs
  never adopt.

## Security implications

The canary IS the security control being wired: fail-closed hook-layer verification (registration
digest, version pin, lane-provisioned, positive-deny, --bare absence) before every mutating spawn.
Evidence binding (nonce+digest) rejects replay. `check_pre`/`verify_post` per-attempt enforcement
unchanged. No new secrets, no new network surface; dispatch CLI paths canonicalized by existing
`resolve_artifact_path`-class checks.

## Platform / external dependencies

platform_apis:
- api: `claude -p` stream-json `init` event carrying `plugins[]` (evidence for lane_provisioned)
  feasibility: verified via spike — #454's provider CLI output contract: the stream's FIRST
  event is `{"type":"system","subtype":"init",...,"plugins":[...]}` (committed evidence:
  tests/phase_executor/test_canary.py's inline init fixtures pin the event shape and
  `plugins[]` consumption; tests/phase_executor/live/test_canary_live.py RUN_LIVE probes
  shipped by #468 exercise the live stream — `init_plugins` is that event's `plugins[]`
  verbatim, and `probes` outcomes are hook-origin deny strings per matcher class as pinned in
  canary.py's _GUARD_DENY_MARKERS; claude-envelope-*.json fixtures evidence RESULT-envelope
  parsing, a different surface)
  failure: fail-loud
  note: absent field means the owning check refuses (CanaryRefused; fail-closed by design)
- api: `python3 hooks/executor_routing_lib.py dispatch` CLI (the invocation prose ships)
  feasibility: verified via existing-call-site — CLI + `dispatch_seat` shipped #427/#464, black-box
  subprocess tests (tests/hooks/test_executor_routing.py); arg surface confirmed at
  hooks/executor_routing_lib.py:937-950 this session
  failure: fail-loud
  note: structured exit taxonomy, JSON error payload
- api: tmux command-as-pane-process launch (`supervisor.launch`)
  feasibility: verified via existing-call-site — #467 shipped supervisor + registry with tests;
  send-keys race class avoided by design (mempalace decision record)
  failure: fail-loud
  note: JobRegistry states; classify_recovery quarantines mismatches

## What this PR does NOT do (scope cuts, named)

- No live proving run (that is #472); live provider calls stay RUN_LIVE-gated.
- No Agent-tool retirement (that is #474/W12; OQ-6 keeps the fallback tier working).
- No RoutingAuditLog competitive-path wiring (declared reconciliation instead — 2d).
- No WF3 state-and-resume.md file (minimal registry-aware line only — §4).
- No autonomous routing changes, no live-viz (AC groups J/K — later children).
