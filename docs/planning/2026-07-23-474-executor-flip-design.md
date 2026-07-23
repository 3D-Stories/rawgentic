# Design — #474 executor migration flip (W12, closes epic #475) — r6 (FINAL, gate closed)

**Issue:** #474 `feat(executor): migration flip + legacy retirement (W12, closes epic)`
**Date:** 2026-07-23 · **Session:** 0ef46985 · **Base:** origin/main `081fd16` (v3.92.4, phase_executor 0.10.0)
**Blast:** HIGH — changes the default dispatch architecture for every project and seat.
**Owner ratification:** 2026-07-22 (the U-3 ratification; verbatim in mempalace `rawgentic/decisions`):
flip the default to executor, run-level enforced, **no runtime auto-fallback**, rollback = a
deliberate JOINT config change; **keep the legacy Agent-tool path + bundled agent definitions
in-tree as the rollback target** (the one deviation from the issue's AC3 "removed").
**Pre-flip rollback anchor:** main `081fd163e7041220452fb3c219b01f219a2867a4`.
**Revision:** r6 FINAL — owner descope decision (2026-07-23, in-session AskUserQuestion after
the incremental verifier returned NOT VERIFIED on the guard/binding subsystem): the PreToolUse
guard + session→run binding subsystem is DESCOPED to **#606** (which carries r5 §2.2b/§2.2c and
the verifier findings as its starting spec). #474 ships the core flip, verified clean since
pass 3. Fold ledgers: §9-§12; descope ledger §13. Counters:
`claude_docs/.wf2-state/474/loopback_counters.json`.

## 0. Scope contract (what "flip" means here)

1. **AC1 — run-level architecture version, enforced.** A run is 100% executor or 100% legacy,
   never mixed. EXECUTOR runs are declared mechanically at run start (`begin-run` ledger pin,
   §2.3) and enforced at every executor entry point (dispatch/recover/close refuse mixed or
   undeclared runs). The AGENT-TOOL side is controlled in #474 by three interim layers —
   architecture-conditioned skill prose + corpus drift pins (§2.4), a workspace self-check
   inside both bundled agent definitions (§2.2b), and a detective run-record warn (§2.4) —
   with the MECHANICAL Agent-tool interceptor descoped to **#606** (owner decision 2026-07-23,
   §13). Stated honestly: until #606 ships, Agent-side enforcement is prose- and
   detection-level, not a harness interceptor.
2. **AC2 — default flips to executor for ALL projects/seats.** Absent config now means executor.
   The prior per-project `executorRouting` opt-in stops selecting the tier.
3. **No runtime auto-fallback.** An executor dispatch failure fails loud via the ERROR protocol
   (exit taxonomy unchanged); it never routes to the Agent-tool path mid-run. Extends #417's
   no-silent-downgrade to an absolute.
4. **Rollback = deliberate joint config change.** `defaultArchitecture: "legacy"` at the
   workspace top level routes every seat back to the legacy Agent-tool path (kept in-tree:
   `agents/rawgentic-implementer.md`, `agents/rawgentic-reviewer.md` — retained as the rollback
   target, gaining ONLY an additive architecture self-check paragraph, §2.2b). Documented and
   tested; never automatic, never per-run.
5. **#450 re-evaluated post-flip (noted, NOT actioned):** #450 proposes ultracode/Workflow-tool
   interop for the Agent-tool fan-out. Post-flip that premise is legacy-architecture-only; an
   interop design would have to route through executor dispatch (or be legacy-scoped). Stays open,
   unchanged, re-scoped by the owner separately.

## 1. Approaches considered

- **A (chosen) — workspace-level architecture key + run-start ledger pin + Agent-tool guard.**
  One new top-level workspace key selects the architecture; a mandatory run-start declaration
  pins it in the per-run expected-call ledger; every executor entry point (dispatch, recover,
  close) enforces the pin; a PreToolUse hook gates the legacy Agent-tool entry. (r2: run-start
  declaration and the hook were added by the pass-1 fold — three independent cross-model voices
  converged on declare-at-run-start, and the ungated Agent-tool entry was pass-1's Critical.)
- **B (rejected) — per-project `architecture` override on each workspace entry.** Finer-grained
  rollback, but contradicts the ratified rollback model (ONE joint lever); multiplies mixed-config
  states for granularity nobody asked for.
- **C (rejected) — encode the architecture in the phase-executor routing table.** Wrong layer:
  the table maps seats to models/chains (#445); tier selection is a workspace policy.

## 2. Design (approach A, r6)

### 2.1 Config surface — `defaultArchitecture` (workspace top level)

New OPTIONAL top-level key in `.rawgentic_workspace.json` (precedent for a top-level key read:
`defaultProtectionLevel`, `hooks/security_guard_lib.py:252`):

```json
{ "defaultArchitecture": "executor" }
```

New pure function in `hooks/executor_routing_lib.py`:

```python
resolve_architecture(workspace_path: str) -> str            # "executor" | "legacy"
resolve_architecture_from_snapshot(ws: dict) -> str          # same rules, pre-loaded snapshot (§2.3 TOCTOU)
```

- Key ABSENT → `"executor"` — **this is the flip** (AC2). No config anywhere = executor.
- `"executor"` / `"legacy"` (exact strings) → as declared.
- Any other value/type → `MalformedConfig` (CLI exit 2) — a typo'd architecture must never
  silently pick a side (the `parse_executor_routing` false-cutover discipline, both directions).
- Unreadable/corrupt workspace → `MalformedConfig` (fail-CLOSED, matching `resolve_seat_action`'s
  `strict_read=True` rationale: an enforcement boundary that cannot evaluate DENIES).

### 2.2 `resolve_seat_action` rewrite — architecture selects the tier

1. `classify_seat` unchanged (driver_only / competitive-only refusal unchanged).
2. **One workspace snapshot per resolution (r2, S3-TOCTOU):** the workspace file is `json.load`ed
   ONCE per CLI invocation; `resolve_architecture_from_snapshot`, the project entry, and the
   `executorRouting` block are all read from that single in-memory snapshot — the load is the
   configuration linearization point. (`resolve_repo_root` participates in the same snapshot at
   the dispatch call site.)
3. `executorRouting` still validated (`parse_executor_routing` unchanged); its seat modes NO
   LONGER select the tier. **Conflict refusal (config-level third of AC1):** an explicit seat
   mode contradicting the architecture — `"inherit"` under executor, `"executor"` under legacy —
   raises `MalformedConfig` ("mixed-architecture config refused"), the message NAMING the
   offending seat and mode. Agreeing modes stay valid as a redundant no-op (the live rawgentic
   all-executor block).
4. executor → `("executor", "executor architecture (default since #474)")`.
5. legacy → `("inherit", "legacy architecture (defaultArchitecture: legacy — manual rollback, #474)")`.
   Action vocabulary unchanged (`inherit` = the legacy Agent-tool path).

### 2.2b Bundled-agent self-check (interim Agent-side control; guard descoped to #606)

The r2-r5 PreToolUse guard + session→run binding subsystem is NOT part of #474 (owner descope,
§13; design history preserved in §9-§12 and in #606's body). #474 ships one cheap, stateless
Agent-side control: **both bundled agent definitions (`agents/rawgentic-implementer.md`,
`agents/rawgentic-reviewer.md`) gain an additive first-instruction SELF-CHECK** — the agent
walks up from its cwd to find `.rawgentic_workspace.json` and REFUSES with a structured error
unless `defaultArchitecture` is `"legacy"` (absent key = executor = refuse). Properties:
- In-band: lives inside the dispatched agent's own instructions — no hook execution required,
  no session identity, no state files; works in worktree isolation (cwd-walk).
- Fail direction: cannot find the workspace → refuse (these agents are meaningless outside a
  rawgentic workspace); corrupt/unreadable → refuse.
- A corpus drift pin asserts the self-check sentence exists in both definitions.
- Honest bound: an instruction-level control — an agent that ignores its own system prompt is
  the same trust class as an orchestrator that ignores its prose (the named residual, accepted
  by the descope decision; #606 replaces this class with a harness interceptor).

### 2.3 Run-start architecture declaration + ledger pin (r6 — executor-side, binding-free)

**Executor runs declare at run START via a new CLI verb (never a lazy seed):**

```bash
python3 hooks/executor_routing_lib.py begin-run --run-id <id> \
  --workspace <ws> --project <name>
```

- Resolves the architecture from ONE workspace snapshot (§2.2), resolves the routing table, and
  atomically writes the ledger `initial` record — `append_initial(config_digest,
  architecture="executor")` — under the existing ledger flock. Under a LEGACY workspace
  architecture, begin-run REFUSES (exit 4, "legacy architecture — executor run machinery
  unused"): legacy runs have no ledger; their declaration is the session-note line plus the
  run-record `architecture` field (§2.4). (The r4/r5 both-architecture session-binding
  declaration moved to #606 with the binding itself.)
- **Idempotence keyed on BOTH fields:** a duplicate begin-run is benign-noop ONLY when the
  existing initial matches the architecture AND the current `config_digest`; any mismatch
  refuses under the ledger lock (a routing-table change mid-run is a declared-state conflict,
  never a silent re-pin).
- WF2/WF3 prose: Step 2's executor tier probe is followed by `begin-run` — executor runs are
  declared mechanically at run start, zero-dispatch runs included.
- `ledger.py` changes: `LedgerState.architecture: Optional[str]`;
  `append_initial(initial_digest, architecture)` (required kwarg, validated
  `in {"executor","legacy"}` — the vocabulary stays two-valued for #606 forward-compat even
  though #474 only ever writes "executor"); `_parse` reads/validates (absent → `None` compat;
  present-but-invalid → `LedgerError`); `append_expected(expected_architecture=...)` asserts
  under the flock (no read-then-append window). **BREAKING signature** (callers, grep-verified:
  executor_routing_lib.py dispatch :2250 + close-run :2537, tests, ledger.py itself; kukakuka
  consumes the contract SCHEMA, not ledger.py's Python API). phase_executor 0.10.0 → 0.11.0.
- **Executor entry points CONSUME the declaration (never seed):**
  - `dispatch`: requires an existing `initial` — absent → `run_not_declared` (exit 4, message
    names begin-run). Ledger architecture `"legacy"` → `mixed_architecture_run_refused`
    (exit 4). The architecture assertion lives INSIDE the flock-held `append_expected`
    precheck.
  - `recover-run`: before supervisor construction, assert workspace architecture == executor
    AND ledger architecture ∈ {"executor", None-compat}; `None` accepted ONLY on an existing
    VALID initial (non-empty `initial_digest`); absent/deleted/corrupt/symlinked ledger refuses
    BEFORE any launch.
  - `close-run`: requires the declaration exactly like dispatch — NO initial →
    `run_not_declared`; `architecture: None` initial (pre-flip in-flight) → proceed + advisory;
    `architecture: legacy` in a ledger → refuse (unreachable state defended). `reconcile`
    consumes the record unchanged.
- **Mid-run rollback edit — refuse-loud:** after the joint edit to legacy, every executor entry
  point (dispatch, recover) refuses at its next call; the run cannot continue mixed; the
  operator restarts fresh legacy runs.
- **Compat (`architecture: None` ledgers) — bounded window with a PROVEN invariant:**
  `append_initial` was called ONLY from the two executor CLI paths (dispatch :2250, close-run
  :2537) plus tests in ≤3.92; the legacy path writes no ledger. Every pre-3.93 ledger is an
  executor run's BY CONSTRUCTION. `None` → executor with an advisory, bounded to the 3.93.x
  line. In-flight pre-flip runs keep dispatching; `run_not_declared` applies only to runs with
  NO initial.

### 2.4 Fail-loud prose + audit legibility (r2 additions)

- `shared/blocks/model-routing-resolve.md` (SYNCED → `skills/implement-feature/SKILL.md`; edit
  source, run `scripts/sync_shared_blocks.py`): the "FALLBACK (legacy) tier" framing is
  rewritten to **"LEGACY architecture — manual joint-config rollback target"**; "Until the W12
  flip (#474)…" becomes "Since the W12 flip (#474) the executor IS the architecture everywhere by
  default; the Agent-tool path remains in-tree ONLY as the legacy architecture, reachable solely
  via the deliberate joint rollback (`defaultArchitecture: "legacy"`), NEVER via runtime
  fallback — an executor failure is surfaced via the ERROR protocol."
- **Architecture-conditioned dispatch prose (r2, S2):** the legacy dispatch instructions in both
  skills (implement-feature `references/steps.md` — the Agent-tool/`rawgentic:rawgentic-implementer`
  /reviewer dispatch sentences at ~:498/:995/:1016/:1092 — and fix-bug `references/steps.md:394`,
  plus both SKILL.md blocks) are made explicitly conditional: each names the declared-legacy
  branch as its ONLY entry ("Under the LEGACY architecture (declared, `defaultArchitecture:
  legacy`) dispatch via …"), and the executor branch names only executor entry points. A corpus
  drift-guard (anchored single-sentence pins per file, per the repo's guard conventions — no
  whole-corpus regex) asserts the legacy-branch conditioning sentence exists and the
  unconditional forms are gone.
- `skills/fix-bug/SKILL.md` bespoke copy hand-edited to match.
- **Run-record architecture field (r2, S4; r3 hardened, SR2-7):** `work_summary.py` schema gains
  `architecture: "executor" | "legacy"` — **REQUIRED when the record's `workflow_version` is
  ≥ 3.93.0**, optional/lenient below (records carry `workflow_version`, so compatibility and
  new-record enforcement are mechanically distinguishable; a new record omitting it is REJECTED,
  keeping the durable-ground-truth claim honest). **Version comparison (r5, SR4-9):** strict
  grammar `^\d+\.\d+\.\d+$` parsed to an int 3-tuple and tuple-compared — never lexical; a
  malformed `workflow_version` is treated as NEW (architecture required), failing toward the
  requirement, with the malformed value named in the validator message. Validator + `docs/run-records.md` §fields
  updated; WF2/WF3 Step-16 prose populates it from the run's declaration. Both architectures now
  leave a durable machine-readable per-run record (executor: ledger + run-record; legacy:
  run-record).
- **Step-16 detective surface (r6 — V4-widened):** `work_summary.py` validation WARNS (visible,
  non-fatal) when a run-record carries `architecture: "executor"` while ANY of its DISPATCH
  lines carries a NON-`primary` resolution (`fallback` OR `generic`) — post-hoc detection of
  Agent-tool dispatch inside an executor run, independent of any hook. Advisory by design: the
  DISPATCH stream has no run-ID join key, so sequential legacy+executor runs of one issue can
  false-positive (named; run-keyed telemetry is #606 AC3). Rides the T6 run-record work.
- **DISPATCH audit vocab UNCHANGED (r2 disposition of A2 — declined, root cause closed
  elsewhere):** with the PreToolUse gate (§2.2b) a `resolution=fallback` line is UNPRODUCIBLE in
  an executor-architecture run (the dispatch is denied before the agent exists), and the
  run-record `architecture` field gives consumers the per-run ground truth. Adding a seventh
  DISPATCH field would churn the pinned regex, validator, and every consumer for a state the
  guard makes unreachable.

### 2.5 Rollback procedure (guard c) — documented + tested

Documented in `docs/config-reference.md` (new `defaultArchitecture` entry) and the PR body:

1. Owner + operator together set `"defaultArchitecture": "legacy"` at the top level of
   `.rawgentic_workspace.json`.
2. Remove (or set to `"inherit"`) any per-project `executorRouting` seat modes that say
   `"executor"` — a contradicting explicit mode is refused (§2.2), by design. Operationally:
   CLOSE (or recover + close) any in-flight executor runs first — they stop loudly at their
   next dispatch/recovery anyway (§2.3), but an orderly close keeps ledgers reconciled.
   **Migration preflight (r2, A6):** at flip time the LIVE workspace holds exactly one `executorRouting`
   block (rawgentic, all-executor — verified 2026-07-23) and zero explicit `"inherit"` modes, so
   no deployed config becomes malformed at upgrade; the config-reference entry carries a
   one-line preflight (`grep -n executorRouting .rawgentic_workspace.json` + fix guidance) for
   any future workspace.
3. In-flight executor runs stop loudly at their next dispatch/recovery; restart as fresh legacy
   runs. The bundled agents and legacy dispatch prose are in-tree and functional.
4. Roll-forward is the same edit in reverse. Rollback anchor: pre-flip main
   `081fd163e7041220452fb3c219b01f219a2867a4` (v3.92.4) — reverting the #474 PR restores
   pre-flip defaults wholesale.

### 2.6 Out of scope / exempt

- `hooks/driver_bench_lib.py` (:520) consumes `resolve_table` (seat→model), which carries no
  architecture gate — nothing to exempt (r2: peer's `context=bench` param declined on this
  ground).
- Epic driver `hooks/driver_lib.py`: zero executor/Agent-tool surface (Step 2 confirmed).
- `skills/setup` Step 2i seat table (`phaseExecutorTable`): different config surface, untouched.
- The bake-off (`competitive`) seat AC1 carve-out from #470 unchanged.

## 3. File changes (r2)

| # | File | Change |
|---|------|--------|
| 1 | `hooks/executor_routing_lib.py` | `resolve_architecture` (+`_from_snapshot`); `resolve_seat_action` rewrite; `begin-run` verb (executor ledger declaration, (arch,digest) idempotence, refuses under legacy); dispatch consume+refuse (`run_not_declared`, `mixed_architecture_run_refused`); recover-run gate (valid-initial predicate); close-run consume (no seed); single-snapshot workspace read |
| 2 | `phase_executor/src/phase_executor/ledger.py` | `LedgerState.architecture`; `append_initial(..., architecture)` (BREAKING signature — callers enumerated §2.3); `append_expected(expected_architecture=...)` flock-held assertion; tolerant parse |
| 3 | `phase_executor/pyproject.toml` + `src/phase_executor/__init__.py` | 0.10.0 → 0.11.0 |
| 4 | `agents/rawgentic-implementer.md` + `agents/rawgentic-reviewer.md` | additive architecture SELF-CHECK first-instruction paragraph (interim Agent-side control, §2.2b; the PreToolUse guard itself is #606) |
| 5 | `tests/hooks/test_executor_routing.py` | red-first flips (:194,:198,:450) + guards (a)–(d) + conflict/mid-run/recover/None-compat/begin-run tests |
| 6 | `tests/phase_executor/test_ledger.py` | signature, architecture validation, None-compat, flock-held assertion |
| 8 | `tests/test_model_routing_resolve_prose.py` + `tests/test_wf3_clarity.py` | post-flip prose pins + legacy-branch conditioning pins |
| 9 | `shared/blocks/model-routing-resolve.md` + synced `skills/implement-feature/SKILL.md` | §2.4 rewrite (source + sync) |
| 10 | `skills/fix-bug/SKILL.md` + both skills' `references/steps.md` | bespoke prose flip + architecture-conditioned dispatch sentences |
| 11 | `hooks/work_summary.py` + `docs/run-records.md` + its tests | `architecture` run-record field — REQUIRED at `workflow_version` ≥ 3.93.0, lenient below (§2.4) |
| 12 | `docs/config-reference.md` | `defaultArchitecture` entry + rollback procedure + preflight |
| 13 | `docs/planning/2026-07-17-orchestrator-executor-acceptance-criteria.md` | U-3 marked owner-ratified 2026-07-22 (keep-legacy deviation named) |
| 14 | `README.md` | changelog entry (diagram decision + Suite old→new) |
| 15 | `docs/workflow-diagram.html` + `docs/assets/` | REV 3.93.0 (dispatch-tier station delta) |
| 16 | version ×4 | plugin surfaces → 3.93.0 |
| 17 | this design doc `.md` + `.html` | committed on the branch |

## 4. Test plan (red first, r6)

**Break set:** `test_resolve_absent_block_is_inherit` (:194), `test_resolve_seat_not_in_config_is_inherit`
(:198), `test_resolve_absent_workspace_is_inherit_not_error` (:450 — absent workspace file stays
non-error and resolves EXECUTOR; corrupt stays fail-closed deny), prose pins.

**The four contract guards:**
- (a) `test_no_config_defaults_to_executor` — no `defaultArchitecture`, no `executorRouting` →
  executor (lib + CLI).
- (b) `test_executor_failure_never_reaches_agent_tool` — failing dispatch (availability stub /
  malformed / enforcement) → non-zero exit, NO `inherit` in output, `resolve_seat_action`
  unchanged after; prose pin: the failure path names the ERROR protocol and contains no
  route-to-Agent-tool instruction; corpus pin: both agent self-check sentences present.
- (c) `test_default_architecture_legacy_routes_back` — legacy workspace → `inherit` + legacy
  reason; dispatch/begin-run refuse under legacy; agent definitions present, frontmatter-valid,
  self-check paragraph present (packaged-cache layout via existing packaging tests). Honest
  bound: live harness Agent invocation is not CI-exercisable — formal deferral (below).
- (d) `test_mixed_architecture_refused` — config conflict both directions (exit 2, seat named);
  ledger `architecture: legacy` + executor dispatch → exit 4; ledger present-but-invalid value →
  LedgerError; `architecture: None` (pre-flip, valid initial) → dispatch proceeds + advisory;
  NO initial → `run_not_declared` exit 4.

**Executor-side enforcement:** begin-run idempotence on (architecture, config_digest) — same
both → noop, digest mismatch → refuse under lock; begin-run refuses under legacy workspace;
mid-run flip (executor ledger + workspace edited legacy) → dispatch AND recover-run refuse
exit 4; recover-run: absent/deleted/corrupt/symlinked ledger refuse pre-launch, `None` only
with valid non-empty `initial_digest`; close-run: no initial → `run_not_declared`,
None-initial → proceed + advisory; flock-held `append_expected` architecture assertion
(deterministic seam test); concurrent first-begin-run cannot double-seed (flock).

**Prose/telemetry:** corpus-derived Agent-producer pins — every Agent-tool dispatch instruction
in both skills' corpora carries the legacy-branch condition (per-file anchored sentences, no
whole-corpus regex); "Until the W12 flip" gone everywhere; work_summary `architecture` REQUIRED
at `workflow_version` ≥ 3.93.0 (strict 3-tuple semver compare; malformed → treated as new),
lenient below, off-vocab rejected at any version; detective WARN on any non-`primary` DISPATCH
resolution in an executor run-record (fallback AND generic cases tested; advisory); DISPATCH
regex/vocab tests unchanged-by-assertion.

**Formal deferred-to-target verification (#138; carried through Step 9 list, Step 12
`## Deferred verification` PR section, Step 16 run-record):**
- `deferred-1` live harness invocation of the bundled legacy agents under a legacy-configured
  workspace (incl. the self-check allowing execution). local_proxy: frontmatter validity +
  packaging tests + self-check corpus pin. target_check: owner-attended rollback drill
  (scratch workspace set legacy, dispatch `rawgentic-reviewer` once, observe it run).

**Full gate:** whole suite vs baseline **4799 passed / 17 skipped / 0 failed** (Step 2, main @
081fd16); both pylint lanes; scoped iteration per `<test-run-discipline>`.

## 5. Error handling / security / platform

**Error handling:** structured CLI errors on the existing exit taxonomy (2 malformed/conflict;
4 enforcement: `run_not_declared`, `mixed_architecture_run_refused`, recover-gate refusals).
Fail modes per repo convention: corrupt workspace denies; absent workspace defaults executor
(AC2); the bundled-agent self-check refuses unless a readable workspace positively declares
`legacy` (§2.2b)
(non-rawgentic projects), deny on corrupt (§2.2b).

**Security implications:** no new hook or state file ships in #474 (the interceptor is #606).
New surfaces: the `defaultArchitecture` key (strictly validated, fail-closed on corrupt);
the ledger `architecture` field (validated under the existing flock/O_NOFOLLOW hardening);
the agent self-check (instruction-level, refuse-by-default). Single-snapshot reads close the
config TOCTOU (S3); the flock-held architecture assertion closes the ledger TOCTOU. Interim
Agent-side residual (no mechanical interceptor until #606) is owner-ratified (§13).

## Platform / external dependencies
platform_apis:
- api: fcntl.flock exclusive lock + os.open(O_NOFOLLOW|O_CREAT) on the run-ledger leaf
  feasibility: verified via existing-call-site — phase_executor/src/phase_executor/ledger.py:89 (O_NOFOLLOW read), :181 (O_NOFOLLOW|O_CREAT append), :185 (flock LOCK_EX), exercised by tests/phase_executor/test_ledger.py on this project's CI (ubuntu) and locally
  failure: fail-loud

(The PreToolUse-hook platform block from r3-r5 — incl. the LIVE Task|Agent WAL-probe evidence of
2026-07-23 — moved to #606 with the guard; no hook ships in #474.)

## 6. Diagram decision (Step 12 input)

Dispatch-tier spine changes (primary/fallback tiers → single default architecture + manual legacy
rollback + run-start declaration) → **workflow-diagram REV required** at plugin 3.93.0
(dispatch/tier station delta), via the `rev-diagram` recipe.

## 7. Multi-PR assessment

Single PR (atomic flip — a partial flip IS the mixed state AC1 forbids). PR says **"Closes
#475"** and carries the rollback anchor + procedure.

## 8. Peer-consult provenance (backend gpt / gpt-5.6-sol, blind both ways)

Own draft first; proposal read after. Convergent: top-level key, absent→executor, fail-closed
invalids; run pinned at declaration; legacy → `inherit` + dispatch refusal; no auto-fallback;
agents kept; DISPATCH vocab unchanged; conflict refusal. Adopted: seat-naming diagnostics;
mid-run-flip semantics made explicit; bounded None-compat; tests (e)–(g). **r2 update:** the
peer's declare-at-run-start (initially declined as lazy-seed-sufficient) was ADOPTED after both
pass-1 reviewers independently demanded it — begin-run is the r2 §2.3 rework. Still declined:
versioned key values (owner contract pins the names); separate `architecture.jsonl` (the ledger
initial record is the single authority); exit-6 remap (canary semantics); `context=bench`
param (no gate on `resolve_table` to exempt). Divergence retained: refuse-loud on mid-run
rollback vs peer's pinned continuation (§2.3 rationale).

## 9. Revision ledger — pass-1 volume loop-back fold (r1 → r2)

Merged pass-1 findings: self-review (executor review seat, gpt-5.6-sol) 4 High + 1 Medium;
adversarial (gpt-5.6-sol) 1 Critical + 4 High + 3 Medium. Volume loop-back (High 8 ≥ 5), full
design source consumed (design 1/2, global 1/3). All Critical/High applied as ONE constraint
set:

| # | Pass-1 finding | Disposition in r2 |
|---|---|---|
| A1 | **Critical** — Agent-tool entry ungated; mixed run reachable | ADOPTED §2.2b: PreToolUse Task-matcher guard hook (precedent verified: wal-pre matches Task) |
| A2 | High — `resolution=fallback` ambiguity in executor runs | DECLINED with mechanism: §2.2b guard makes the state unreachable + §2.4 run-record architecture field gives ground truth; schema churn avoided |
| A3 | High (amb) — None-ledger invariant unproven | ADOPTED §2.3: exhaustive producer inventory cited (grep-verified: dispatch :2250 + close-run :2537 + tests only) |
| A4 | High (amb) — rollback invocability unproven | ADOPTED-reduced §4(c): frontmatter-validity + packaging + prose-token proxies committed; live-harness remainder named as the honest bound |
| A5 | High — lazy seeding ≠ run start | ADOPTED §2.3: begin-run run-start declaration; entry points consume, never seed |
| A6 | Medium (amb) — explicit-inherit migration | ADOPTED-cheap §2.5: live-workspace fact (zero inherit modes) + preflight line + seat-naming errors |
| A7 | Medium — append_initial breaking mislabeled | ADOPTED §2.3: relabeled BREAKING, callers enumerated, kukakuka scope clarified (schema consumer, not Python API) |
| A8 | Medium (amb) — flock/O_NOFOLLOW undeclared | ADOPTED §5: platform_apis blocks with existing-call-site evidence |
| S1 | High — recover-run bypasses the gate (verified :2318-2363) | ADOPTED §2.3: recover-run architecture gate, exit 4 |
| S2 | High — prose still directs Agent-tool unconditionally | ADOPTED §2.4: architecture-conditioned legacy branch + corpus pins; §2.2b closes it mechanically |
| S3 | High — TOCTOU (multi-read snapshot; check outside flock) | ADOPTED §2.2/§2.3: single-snapshot linearization + flock-held `append_expected` architecture assertion |
| S4 | High — zero-dispatch runs undeclared; run-record lacks architecture | ADOPTED §2.3 (begin-run) + §2.4 (run-record field) |
| S5 | Medium (amb) — kukakuka compatibility unknown | ADOPTED §2.3: kukakuka consumes the contract schema (Rust producer), not ledger.py — no downstream Python caller; release-noted |

Ambiguity flags (A3, A4, A6, A8, S5) were evidence-gaps, each resolved by verifiable in-repo
facts recorded above — no product/risk fork required owner adjudication; the pass-2 gate
re-reviews the resolutions.

## 10. Revision ledger — pass-2 volume loop-back fold (r2 → r3)

Pass-2 merged findings: adversarial 2 Critical + 3 High + 2 Medium (all REOPENS with legible
deltas); self-review 2 Critical + 2 High + 3 Medium. Volume loop-back #2 (High 5 ≥ 5), design
source consumed (design 2/2 EXHAUSTED, global 2/3). All applied as ONE constraint set:

| # | Pass-2 finding | Disposition in r3 |
|---|---|---|
| P2-1 | **Crit** (REOPENS A1) — guard consults mutable workspace, not the run pin | ADOPTED §2.2b/§2.2c: session→run binding written by begin-run governs the guard; the pin outlives a mid-run flip |
| P2-2 | **Crit** (REOPENS A1) — fail-open guard error bypasses the interceptor | ADOPTED §2.2b: fully fail-CLOSED for the two gated types; only a positive legacy determination allows; type check precedes all I/O so other types cannot be bricked |
| P2-3 | High (REOPENS A4) — proxies ≠ harness invocability | ADOPTED §4: formal #138 deferral (deferred-1, owner rollback drill) with named local proxies |
| P2-4 | High — subprocess test ≠ live matcher registration | ADOPTED §4/§5: hooks.json registration pin + dual matcher + live WAL probe + deferred-2 post-reinstall check |
| P2-5 | High (REOPENS S4) — close-run does not enforce the pin | ADOPTED §2.3: close-run requires the declaration; r2 compat-seed REMOVED |
| P2-6 | Med (amb) — idempotence ignores config_digest | ADOPTED §2.3: (architecture, config_digest) both must match; mismatch refuses under lock |
| P2-7 | Med (REOPENS S4) — legacy begin-run contradiction | ADOPTED §2.3 (r3 form: executor-only — SUPERSEDED r4: begin-run declares both architectures; legacy = binding-only, still never a ledger) |
| SR2-1 | **Crit** — guard scope excludes generic/toolkit producers | ADOPTED-scoped §2.2b: gating ALL Agent use would brick legitimate non-workflow dispatch; producers closed by conditioned prose + NEW corpus-derived Agent-producer pins + DISPATCH audit; prose-violating orchestrator = same trust as mislabeled type (named threat model) |
| SR2-2 | **Crit** — mutable-workspace evaluation + discovery divergence | ADOPTED §2.2c: trusted binding carries canonical workspace identity; cleared at run terminal; unbound/corrupt states deny |
| SR2-3 | High — Task vs Agent matcher unproven | ADOPTED §5: live spike evidence (Agent events through a Task matcher, last 2026-07-20) + dual matcher + deferred-2 |
| SR2-4 | High — recover conflates absent ledger with pre-flip initial | ADOPTED §2.3: valid non-empty initial required; absent/deleted/corrupt/symlinked refuse pre-launch |
| SR2-5 | Med — close-run seeding contradiction | ADOPTED §2.3 (with P2-5) |
| SR2-6 | Med — idempotence digest | ADOPTED §2.3 (with P2-6) |
| SR2-7 | Med — run-record field optional forever | ADOPTED §2.4: REQUIRED at workflow_version ≥ 3.93.0, lenient below |

Ambiguity flags: P2-6 only — resolved by the (arch, digest) spec above (a wording/spec fix, no
product fork). Budget note: design source exhausted; a pass-3 Critical/High escalates to the
owner via the Hermes bridge per the ERROR protocol.

## 11. Revision ledger — pass-3 fold under the owner's +1 grant (r3 → r4)

Pass-3 merged: adversarial 1 Critical + 5 High; self-review 3 Critical + 3 High + 1 Medium +
1 Low. Volume threshold met with the design source exhausted → owner escalation (issue comment
5054205521 + Hermes RG-809678); owner granted +1 design loop-back in-session 2026-07-23
(counters file carries the `budget_override` annotation). All findings applied as ONE set:

| # | Pass-3 finding | Disposition in r4 |
|---|---|---|
| P3-1/SR3-1 | **Crit** — legacy runs lack a mechanical run-start pin; same-run-id re-declaration reachable | ADOPTED §2.3: begin-run declares BOTH architectures (legacy = binding-only pin); re-declaration under another architecture refused |
| SR3-2 | **Crit** (REOPENS SR2-1) — generic/toolkit producers pass during executor runs | ADOPTED §2.2b: deny ALL Agent-tool dispatch while an executor run is open (exactly U-3); structural classification = open binding, not allow-by-type |
| P3-2/SR3-3 | **Crit/High** — binding atomicity/serialization unspecified | ADOPTED §2.2c: per-session flock transactions; pending→open commit order; duplicate begin-run verifies+repairs; crash-injection tests |
| SR3-4 | High (REOPENS P2-2/P2-4) — platform hook fail-open window | ADOPTED §2.2b/§0: claim reworded to "mechanically gated with named failure windows"; agent-definition self-check backstop added; residual window named |
| P3-3/SR3-5 | High — binding root/identity lookup contract | ADOPTED §2.2c: fixed XDG state root, zero guard-side workspace discovery; REQUIRED --session-id from $CLAUDE_CODE_SESSION_ID (switch-skill printenv precedent); identity spike |
| SR3-6 | High — binding read hardening absent | ADOPTED §2.2c: ledger-grade hardening (validator, O_NOFOLLOW, regular-file, caps, per-field validation) |
| P3-4 | High — session-id source unproven | ADOPTED with P3-3/SR3-5 (same mechanism) |
| P3-5/SR3-7 | High/Med — stale §5 fail-open sentence | ADOPTED §5: replaced with the reviewer-worded fail-closed rule |
| P3-6 | High — review-constraining header sentence | ADOPTED header: neutral budget facts only; brief template fixed (neutral framing, no classification pressure) |
| SR3-8 | Low — stale table row (optional architecture field) | ADOPTED §3: version-gated requirement stated |

## 12. Revision ledger — pass-4 fold under owner grant #2 (r4 → r5)

Pass-4 merged: adversarial 1 Critical + 5 High + 2 Medium; self-review 7 High + 3 Medium.
Zero REOPENS of the core flip — every finding targets the guard/binding subsystem added at
r2-r4 or doc self-consistency. Owner grant #2 (in-session AskUserQuestion, option A): fold +
ONE incremental verifier over the changed sections (owner-sanctioned close; not a fifth full
wave). All applied:

| # | Pass-4 finding | Disposition in r5 |
|---|---|---|
| P4-1/SR4-1 | **Crit**/High — stale r3 "executor-only" leftovers contradict r4 §2.3 | ADOPTED: three stale lines swept; §10 P2-7 row annotated superseded |
| P4-2/SR4-2 | High — flock on a replaced inode does not serialize | ADOPTED §2.2c: separate never-replaced `<session_id>.lock` held across read-validate-replace |
| P4-3 | High — re-declaration refusal session-scoped, not run-scoped | ADOPTED §2.3: run-id/session contract enforced (run_id must embed --session-id); cross-session reuse refuses |
| P4-4/SR4-7 | High — one session mixing executor+legacy open runs | ADOPTED §2.3: one architecture per session at a time; §2.5 rollback gains close-before-legacy step |
| P4-5/SR4-5 | High — §5 stale two-type boundary / pass-before-I/O contradiction | ADOPTED §5 + §2.2b: deny-all semantics stated; every invocation reads the binding; unreadable store denies all |
| P4-6/SR4-6 | High — hook silent non-execution undetected post-deploy | ADOPTED §2.4: Step-16 detective warn (executor run-record + any fallback-resolution DISPATCH line), hook-independent |
| P4-7/SR4-3 | Med/High — terminal verbs lack session lookup | ADOPTED §2.3: --session-id on close-run/reconcile, default derived per the run-id contract |
| P4-8 | Med — session-id source evidence | ADOPTED §2.2c: live call-site cited (this session's registry bind, 2026-07-23T03:09:22Z) |
| SR4-4 | High — terminal cross-file commit order | ADOPTED §2.3: run_closed FIRST, binding removal second; crash direction safe; orphan repair idempotent |
| SR4-6 | High — XDG_STATE_HOME spoof blinds the guard | ADOPTED §2.2c: root pinned via pwd.getpwuid, env deliberately not honored; missing-binding residual named |
| SR4-8 | Med — parent-dir symlink verification | ADOPTED §2.2c: each parent component 0700 + lstat non-symlink + uid-owned |
| SR4-9 | Med — semver comparison grammar | ADOPTED §2.4: strict 3-tuple grammar; malformed → treated as new (fails toward the requirement) |
| SR4-10 | Med — agent self-check assumes parent-session access | ADOPTED §2.2b: scope reduced to workspace defaultArchitecture (cwd-walk); run-pin stays with hook + entry points |

Close protocol per owner grant #2: one incremental verifier over the changed sections
(§0/§2.2b/§2.2c/§2.3/§2.4/§2.5/§5), then the gate closes. A new Critical/High from the
verifier escalates back to the owner — no further self-granted passes.

## 13. Descope ledger — gate close (r5 → r6 FINAL)

The owner-sanctioned incremental verifier over r5's changed sections returned **NOT VERIFIED**:
5 §12 rows resolved, 8 findings remaining (6 High, no new Critical) — every one inside the
PreToolUse-guard + session→run-binding subsystem, which the gate itself introduced at r2 and
which diverged under four review rounds (each fold surfaced a deeper hardening layer). Full
verdict: `claude_docs/.wf2-state/474/step4-verifier.md`.

**Owner decision (AskUserQuestion, 2026-07-23, session 0ef46985): DESCOPE the guard.** The
subsystem moved to **#606** carrying r5 §2.2b/§2.2c + all verifier findings as its starting
spec. Dispositions 474-v-1..8 in the ledger: V4 ADOPTED here (detective warn widened to any
non-primary resolution); V3 DISSOLVED (contradictory sections removed with the subsystem);
V1/V2/V5/V6/V7/V8 DEFERRED-to-#606.

**What #474 ships (verified clean since pass 3):** defaultArchitecture flip (absent=executor,
fail-closed invalids), begin-run ledger declaration + (arch,digest) idempotence, entry-point
enforcement (dispatch/recover/close), fail-loud with no runtime fallback, conflict refusal
naming the seat, architecture-conditioned prose + corpus pins, bundled-agent self-check,
run-record architecture field (version-gated, strict semver) + widened detective warn,
documented+tested rollback, None-ledger bounded compat.

**Owner-ratified interim residual:** until #606, Agent-side mixed-run control is prose +
self-check + detection — no harness interceptor. Gate CLOSED on this decision; the breaker ran
each pass; loop-back counters final: design 4 (2 base + 2 owner grants), global 4.
