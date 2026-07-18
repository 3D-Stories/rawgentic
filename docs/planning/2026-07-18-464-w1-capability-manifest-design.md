# #464 (W1) — Per-seat capability manifest + WIRED_SEATS full set + build-audit path — design

Status: REVISED r3 (Step 3 revision after Step 4 pass-2 breaker — owner applied a 9-finding
package 2026-07-18 on top of pass-1's 11; TWO `design` loop-backs consumed (budget exhausted);
all High dispositions in `claude_docs/.wf2-state/464/dispositions.jsonl` (9 entries).
Author draft written BLIND; then synthesized with (a) a 3-approach brainstorm (sonnet subagent)
and (b) an independent cross-model peer proposal (WF13 consult, backend **gpt**, report:
`docs/reviews/peer-rawgentic-peer-problem-464-2026-07-18.md`). Provenance per decision below.

Doc of record: `docs/planning/2026-07-17-orchestrator-executor-acceptance-criteria.md`
(AC-B1, AC-B3, AC-B4, AC-D3, D-8, U-5, OQ-2). Absorbed: #434 part 2 (owner decision verbatim,
issuecomment-5010034928).

## Approach (selected)

**Manifest-in-table, full 7-seat table, attested build gate.** Per-seat `manifest` object inside
the existing routing table (one source of truth, AC4); table extended to all 7 seats each with its
OWN manifest; structural+semantic validation fail-closed at load; `check_pre` stays a policy
evaluator and gains (i) table-declared enforced-roles checking and (ii) a build-gate
**attestation** requirement with launch-input binding, authenticated at the hooks boundary.

Alternatives weighed and rejected:
- **Separate manifest file / top-level sibling `capabilities` map** (brainstorm approach 3):
  cleanest schema diff, but a second seat-keyed map is a parallel-table shape AC4 forbids in
  spirit, and it leaves seat rows and their governance split across two sections.
- **No design row, candidates borrow manifests from scanned seats** (peer proposal): rejected —
  a design candidate whose lane was scanned from the `build` seat would inherit build's WRITE
  tool grants for a design dispatch (over-grant). Design gets its own row + manifest instead.
- **Loader-defaulted session_policy** (absent → fresh): rejected by author + peer convergently —
  explicit fields are digest-covered and auditable; an enforcement boundary that silently fills
  in policy is the fail-open shape this epic removes.
- **`gate_denied` violation on a non-allow gate decision** (peer): rejected — #429's
  `GateDecision` routes bake-off-vs-single (its fail-closed direction is "force bake-off"),
  it never denies build outright; an "allow" framing misreads its semantics.

## Fork resolutions

### A. Manifest location — inside each routing-table seat (author + peer convergent)

Each of the 7 seats in `phase_executor/src/phase_executor/routing/rawgentic.routing-table.json`
gains a REQUIRED `manifest` object:

```json
"manifest": {
  "session_policy": "fresh",                     // enum: fresh | resume (D-8); EXPLICIT, no defaulting
  "tool_grants": ["read"],                       // broad-but-gated categories (OQ-2): read|edit|bash|net; unique items
  "effort": "high",                              // normalized enum low|medium|high|xhigh|max (U-5); identity value, W2 translates
  "confinement": {"anthropic": "hooks"},         // per-provider declaration map (AC-B3), keys must cover every chain lane provider
  "bounds": {"timeout_s": 1800}                  // AC-B4 compensating bound; required for all seats
}
```

- `session_policy` explicit on EVERY seat. Shipped table: `fresh` everywhere. D-8's "default
  fresh for review/judge" = shipped VALUES + a drift-guard test pinning that every enforced-role
  `review` seat (and `design`) declares `fresh`.
- `resume` opt-in (peer contribution, adopted): schema `if session_policy == "resume" then
  require resume_opt_in: {reason: <non-empty string>}` — resume is legal but never silent.
  Dialect fact (breaker A5; pass-2 P7 evidence bar): the schema declares
  `$schema: draft/2020-12` (routing-table.schema.json:2); the LIVE host validator is
  jsonschema 4.10.3 (executed `import jsonschema` probe this session — not inferred from the
  `>=4.0` range at phase_executor/pyproject.toml:8), and 2020-12 `if`/`then` is implemented
  from jsonschema 4.x. Implementation must ALSO cite the resolved version in
  `phase_executor/uv.lock` and land the executed RED cell (resume without opt-in rejected) as
  the mechanical proof under CI's own environment; if it ever fails to reject, the loader
  semantic pass (§C.2) is the fallback home for the same rule.
- D-8 "judge" mapping (breaker S5): in this table "judge" maps to the `design` competitive-round
  seat. The glm bake-off JUDGE itself is not a routing-table seat (it dispatches via
  `adversarial_review_lib.glm_complete` inside `bakeoff_policy`) — its session behavior is
  adapter-level scope (W2 #465), deliberately NOT governed by this table.
- No singular `provider` manifest field (issue body lists it; disposition: redundant — provider
  is a per-lane fact and chains legitimately span providers; the `confinement` map keyed by
  provider subsumes the declaration, validated to cover every chain provider).
- `additionalProperties: false` on manifest (unknown fields rejected).
- **Normative seat-value matrix (pass-3 R3 — the shipped table's exact manifest values; the
  JSON example above is illustrative only):**

  | seat | role | session_policy | tool_grants | effort | confinement | bounds.timeout_s |
  |---|---|---|---|---|---|---|
  | intake | — | fresh | [read] | medium | anthropic: hooks | 900 |
  | analysis | — | fresh | [read] | medium | anthropic: hooks | 1200 |
  | design | — | fresh | [read] | high | openai: codex-sandbox-readonly · anthropic: hooks | 1800 |
  | plan | — | fresh | [read] | high | anthropic: hooks · openai: codex-sandbox-readonly | 1800 |
  | build | build | fresh | [read, edit, bash] | high | anthropic: hooks · openai: codex-sandbox-pinned | 3600 |
  | review | review | fresh | [read] | high | anthropic: hooks · openai: codex-sandbox-readonly | 1800 |
  | ship | — | fresh | [read] | medium | anthropic: hooks | 900 |

  Confinement value vocabulary (declaration strings; W2 #465 gives them operational meaning):
  `hooks` = claude hook-layer containment (F3, spike #454); `codex-sandbox-readonly` = codex
  default read-only sandbox; `codex-sandbox-pinned` = the Q2 three-override worktree pinning.
  Only `build` carries write grants — every other seat is read-only (no borrowed over-grants,
  §B P3 rule). Each seat's confinement keys exactly cover its chain's lane providers.
- Routing-table `schema_version` stays `"1"`: validator and table ship in lockstep in-package
  (no vendored external copies — unlike the Observation schema, whose #434 policy governs
  cross-repo consumers). The table digest changes — an audited epoch by design; verified no test
  pins a digest literal (`test_shipped_table_digest_stable_and_pools` checks prefix only;
  `test_digest_golden_vector` hashes an inline fixture).

### B. Seat vocabulary — table extends to the full 7; WIRED_SEATS mirrors it (author, over peer)

- `analysis`: real row — primary `claude-sonnet-5`, chain `[claude-opus-4-8, claude-fable-5]`
  (mirrors the owner-approved modelRouting analysis=sonnet signal; W10 bench adjusts later).
  No `role`. Launch feasibility (breaker A4, discarded with citation): every model+lane pair in
  the new rows exists VERBATIM in shipped seats today (sonnet/opus/fable/sol all appear in
  intake/plan/build/review/ship chains, same lanes) and the adapters launch them live
  (#426 review-on-fable live-verified, requested==actual) — working-precedent feasibility,
  no new provider surface.
- `design`: real row — primary `gpt-5.6-sol`, chain `[claude-opus-4-8]` — exactly the #428
  `DESIGN_MODELS` pair (bakeoff_policy.py:29). DISPATCH remains competitive (bake-off owns it);
  the row gives design its OWN manifest (read-only grants — no borrowed over-grants) and declares
  the candidate set in the one table. Its `confinement` map carries BOTH providers
  (`{"openai": …, "anthropic": …}`) — the §C.2 semantic check requires coverage of every chain
  lane provider (this holds for plan/build/review too, whose chains include openai).
  `bakeoff_policy._candidates_for` keeps scanning by model id (behavior preserved; dedupe by
  target identity checked at implementation). Drift-guard (breaker A6): set EQUALITY —
  `set(DESIGN_MODELS) == set(design-row models)` — one-directional subset would let a row-added
  model silently never become a candidate. Provenance note in the table updated (design: static
  row, competitive dispatch). **Candidate-manifest resolution rule (pass-2 P3, normative):** a
  competitive candidate's governing manifest is ALWAYS the REQUESTED seat's (the `design` row),
  never the seat its lane happened to be discovered from — a duplicate model id found via the
  build chain must NOT resolve build's write-granting manifest for a design dispatch. W1 states
  the rule + ships a duplicate-model-id test pinning it; runtime enforcement of manifests is
  W2 #465 (which implements resolution per this rule).
- **Single-dispatch opt-in split (breaker S2):** `WIRED_SEATS` is the enforcement/naming
  VOCABULARY (all 7). The `executorRouting` opt-in gate is narrower:
  `parse_executor_routing`/`classify_seat` REJECT `design` opt-in with a legible error
  (competitive owns its dispatch; single-dispatching it would bypass the bake-off). The other 6
  stay single-dispatchable — build legitimately so, because its single dispatch is exactly the
  gate-attested path (§E) and the #429 gate itself decides bake-off-vs-single.
- `hooks/executor_routing_lib.py:46`: `WIRED_SEATS = frozenset({"intake", "analysis", "design",
  "plan", "build", "review", "ship"})` (AC2 verbatim), documented as the executor seat
  VOCABULARY. `DRIVER_ONLY` unchanged.
- `tests/phase_executor/test_routing.py` `_EXPECTED_SEATS_426` → 7-seat `_464` rewrite;
  `tests/hooks/test_driver_bench.py:109` repointed (build now has an audit path) + a new
  negative (build with NO gate attestation stays fail-closed).

### C. Validation gate placement — fail-closed at LOAD; check_pre stays a policy evaluator (brainstorm + peer convergent, author's dispatch-time manifest check dropped)

1. **Schema (structural):** `routing-table.schema.json` requires `manifest` per seat with full
   shape (enums, required keys, `additionalProperties: false`, resume-opt-in conditional).
   Every production dispatch path loads via `load_routing_table` →
   `contract.validate_routing_table` (routing.py:36; sole entry executor_routing_lib.py:393) —
   missing/malformed manifest = snapshot never constructed = launch refused (AC1).
2. **Semantic (cross-field, jsonschema-unfriendly):** extend the loader's
   `_assert_referential_integrity` pass (routing.py:41): every seat's `confinement` keys must
   cover every provider in its primary+chain lanes; `policy.enforced_roles` well-formed (fork D);
   **name↔role binding lint (breaker A2):** a seat NAMED `build` must declare `role: "build"`,
   a seat named `review` must declare `role: "review"` — fail-closed at load. This closes the
   authoring-error hole (a canonical build seat with role omitted would otherwise bypass the
   attestation gate entirely, since gating keys on the optional role). `check_pre` stays
   role-keyed (renamed-seat portability, the #425 Step-8a lesson, preserved); the lint is a
   table-authoring guard, not an enforcement re-coupling. Shipped-table drift-guard test pins
   both bindings.
3. `RoutingSnapshot.from_table` (no validation, verified routing.py:66-71) remains the
   test-fixture convenience — documented, not a production path. `check_pre` does NOT re-run
   structural manifest checks (saves ~20 existing enforce-test fixtures from churn; convergent
   brainstorm + peer recommendation against the author draft's second layer).

### D. Unrecognized role — fail-closed, keyed on TABLE-declared enforced roles (peer, adopted over author's code constant)

The table gains a top-level `policy` section: `"policy": {"enforced_roles": ["review", "build"]}`.
Precedent: `forbidden_combinations` — routing.py's own docstring: "The engine is policy-free:
invariants … are project-supplied rows in the table, evaluated here as data." A table-declared
set also rides #445's per-project extraction for free and matches the owner's wording
("project-recognized enforced-roles set, not a hard schema enum") literally. Schema requires
`policy.enforced_roles` (unique, non-empty strings).

**Evaluator-registry bound (pass-2 P4):** `enforce.py` exports
`ENFORCEABLE_ROLES: Final[frozenset[str]] = frozenset({"review", "build"})` — the roles the
engine actually has evaluators for. The loader semantic pass rejects any `policy.enforced_roles`
entry outside `ENFORCEABLE_ROLES` (fail-closed at load): a table cannot declare a role
"enforced" that nothing evaluates (appears-enforced-but-isn't). `enforced_roles` therefore ⊆
`ENFORCEABLE_ROLES`; projects may narrow, never widen, until the engine ships a new evaluator.

`check_pre` (reads the set from the snapshot):

```python
role = seat_obj.get("role")
enforced = frozenset(snapshot.table.get("policy", {}).get("enforced_roles", ()))
if role and role not in enforced:
    violations.append(f"unrecognized_role: {role!r} not in {sorted(enforced)}")
```

- `role` absent stays legal (test_enforce.py:515 must keep passing). Empty string (breaker S4):
  `if role and …` treats `""` as absent — matching AC6's "NON-EMPTY" carve-out verbatim — and
  the schema adds `minLength: 1` on `role` so a config can never DECLARE an empty role in the
  first place (the truthiness guard covers only programmatic tables).
- A table with `policy` absent (programmatic legacy fixtures): `enforced` is empty → ANY
  non-empty role fails closed — the safe direction; shipped table always declares it.
- RED cell: `role: "biuld"` → `unrecognized_role` (silently passes on main today).

### E. Build-audit path — deny → bound GateAttestation; authentication at the hooks boundary (peer-strengthened)

The bare "gate_digest is non-None" presence check (author draft) is insufficient — any caller
could hand `check_pre` a junk string (peer finding, adopted). Replacement:

- **`GateAttestation`** frozen dataclass in `enforce.py` (extraction-clean, pure stdlib):
  `{gate_outcome: "bakeoff"|"single", policy_digest: str, input_digest: str}`. A raw dict does
  NOT satisfy it (isinstance check — narrow constructor as the in-process trust boundary; honest
  limit: not cryptographic proof, same trust class as the existing receipt chain).
- **Launch-input binding (anti-replay):** new pure helper in `enforce.py`
  `launch_input_digest(seat, target, correlation_id) -> str` (sha256 over canonical JSON —
  same canonicalization idiom as routing.canonical_bytes). Hooks mint the attestation binding it
  to the exact launch; `check_pre` recomputes from its own args and compares. A gate decision
  replayed against a different launch fails closed. Canonical digest construction lives
  extraction-clean in phase_executor; hooks call it (peer risk item, adopted).
- **`check_pre` role=="build" branch** (replaces enforce.py:121-126):
  - attestation `None` → `gate_missing`
  - not a `GateAttestation` / bad shape / `input_digest` mismatch → `gate_invalid: <detail>`
  - well-formed + bound → no violation; `gate_outcome` + digests recorded in the receipt.
  Docstring keeps E2's honest limit: `check_pre` verifies shape+binding, never the gate's
  AUTHENTICITY (no hooks import) — that is the hooks boundary's job.
- **Hooks boundary** (`executor_routing_lib.py`): `dispatch_seat` gains a gate param (CLI
  `--gate-file`); REQUIRED for seats whose table `role == "build"` (verified: today's signature
  has no gate-evidence param at all, :202-221). It authenticates the #429 `GateDecision` via
  the digest recompute now extracted from `bakeoff_policy._verified_decision:281-294` into a
  public `complexity_gate.verified_decision(gate_decision, expected_context=None)` (bakeoff_policy
  refactored to call it — one helper, one home), then mints the bound `GateAttestation`
  (`gate_outcome` from `decision_from_snapshot`) and calls `check_pre`. Missing/tampered gate →
  exit 4, receipt-only, no launch.
  - **Expected-context cross-check (pass-1 A1, HARDENED by pass-2 P5+SR1 — mandatory on the
    build path):** `verified_decision(gate_decision, expected_context)` compares each canonical
    context key against the GateDecision's `input_snapshot`; ANY mismatch fails verification. On
    the BUILD dispatch path `expected_context` is **MANDATORY and independently sourced**: the
    CLI gains `--plan-context <file>` (distinct from `--gate-file` — deriving context from the
    gate file itself would be circular and detect nothing), carrying the canonical field set
    (the `input_snapshot` keys: task risk/id, issue complexity, files, size estimates) from the
    caller's own plan facts. Missing/empty/partial context → `GateContextError`, dispatch
    refused (exit 2), no attestation minted — omission can never silently disable the defense.
    `verified_decision(ctx=None)` remains legal ONLY for bakeoff_policy's existing internal
    call (which holds no separate plan doc; its gate file IS minted in-process one call
    earlier). A hooks-integration test asserts the REAL dispatch path rejects a stale decision
    minted for different plan inputs. **Named residual (pass-3 R2, owner-declined for W1):** a
    caller submitting a COHERENT stale pair (old gate file + its matching old context file)
    passes the cross-check — caller error under the declared non-hostile model. The closing fix
    (orchestrator-held authoritative plan context minted at dispatch, no second file) is W7
    #470's wiring — on the W7 carry list. Exception-type note: today the tamper path raises
    `bakeoff_policy.JudgeError`; the extracted helper raises `complexity_gate.GateTamperError`
    and `bakeoff_policy` translates at its call site so `run_build_bakeoff`'s "raises on
    tamper" contract holds unchanged.
  - **Gate-outcome routing (pass-2 P1):** a valid attestation does NOT blanket-authorize single
    dispatch. `dispatch_seat` requires `gate_outcome == "single"`; an attestation whose outcome
    is `"bakeoff"` gets a typed refusal (`gate_requires_bakeoff`, enforcement exit 4, receipt
    recorded, no launch) — the gate's routing decision cannot be bypassed by re-presenting its
    own evidence to the single-dispatch path. Competitive routing itself is wired in W7 #470.
  - **Trust boundary, stated honestly (breaker A1):** this is an IN-PROCESS trust chain — the
    digests are unkeyed, so a malicious in-process caller can fabricate a self-consistent
    GateDecision. That is the SAME trust class as the entire existing receipt/audit chain
    (nothing in this repo is cryptographically attested; there is no key infrastructure).
    The controls here defend against authoring errors, stale reuse, and cross-launch replay —
    not against a hostile caller inside the process. HMAC/signed attestations are a named
    follow-up ONLY if kukakuka ever needs cross-process trust.
- **Audit path (AC-D3, scoped — breaker S1):** the receipt→observation→`reconcile_run` spine
  covers the **`dispatch_seat` single-dispatch path** — the sole `check_pre` caller
  (executor_routing_lib.py:245; verified `check_pre` appears nowhere in engine.py). The #428
  competitive bake-off (`run_build_bakeoff` → `run_competitive`, no-bake-off fallback
  `run_seat` at bakeoff_policy.py:327) does NOT mint receipts today and its results persist via
  the caller sink to `bakeoff_results.jsonl` — a different, existing stream. W1 does not claim
  otherwise: a bake-off build is reconciled via `bakeoff_results.jsonl`, and `reconcile_run`'s
  expected-set must simply not enumerate a bake-off-served build call (the expected-set is
  caller-supplied). **Wiring the competitive path into the RoutingAuditLog spine is carried to
  W7 #470 as an explicit constraint** (owner disposition 2026-07-18) — noted on #470 when this
  design is applied. Receipt field mapping (pass-1 A3, HARDENED by pass-2 P2): `PreReceipt`
  gains `role` (from the seat object — check_pre knows it), `gate_outcome`, and
  `gate_input_digest`; the pre-existing `gate_digest` field (enforce.py:64, today always None
  in production) is REPURPOSED with defined semantics: it carries the attestation's
  `policy_digest`. `_validate_record` gains a conditional requirement — a receipt with
  `role == "build"` MUST carry non-null `gate_outcome` + `gate_input_digest` (fail-closed:
  a new build receipt cannot validate ungated) — while receipts WITHOUT a `role` key (all
  historical logs) validate exactly as today. So the audit can PROVE a new build launch was
  gated, and old logs still parse. The binding observation carries nothing new (attestation
  evidence lives on the RECEIPT side; `reconcile_run` binding logic untouched — role-aware
  reconcile requirements land with W7's expected-set extension).
  `driver_bench_lib._score_audit:197` now admits build; the bench build-audit fixture supplies
  a fixture gate decision.

## File changes

| File | Change |
|---|---|
| `phase_executor/src/phase_executor/schemas/routing-table.schema.json` | per-seat required `manifest`; top-level `policy.enforced_roles`; resume-opt-in conditional |
| `phase_executor/src/phase_executor/routing/rawgentic.routing-table.json` | +analysis +design rows; manifest ×7; `policy` section; provenance note |
| `phase_executor/src/phase_executor/enforce.py` | `GateAttestation`, `launch_input_digest`, `ENFORCEABLE_ROLES` export, check_pre surgery (D+E incl. outcome routing), `PreReceipt` +`role`/`gate_outcome`/`gate_input_digest` (gate_digest = policy digest), `_validate_record` build-receipt conditional, docstrings |
| `phase_executor/src/phase_executor/routing.py` | `_assert_referential_integrity`: confinement↔chain-provider cross-check, policy shape |
| `phase_executor/src/phase_executor/__init__.py` | export `GateAttestation`, `launch_input_digest` |
| `hooks/executor_routing_lib.py` | WIRED_SEATS full 7; design opt-in rejected in `parse_executor_routing`/`classify_seat`; `--gate-file` + `--plan-context` params (build path: both MANDATORY); gate-outcome routing (`single` only); build authentication + attestation minting |
| `hooks/complexity_gate.py` | public `verified_decision(gate_decision, expected_context=None)` + `GateTamperError` (extracted from bakeoff_policy) |
| `hooks/bakeoff_policy.py` | call the extracted helper |
| `hooks/driver_bench_lib.py` | build-audit path in `_score_audit` (fixture gate supply), comments |
| tests (enforce/routing/schemas/executor_routing/driver_bench) | red-before-green cells + updated pins (see plan) |
| `README.md` + version ×3 surfaces + AC-doc §7 row | minor bump (feat) 3.51.2 → 3.52.0; changelog entry |

## Error handling / failure modes

- Missing/malformed manifest or policy section: load refused (schema/semantic, fail-closed) — no snapshot, no launch.
- Unknown seat name: unchanged `RoutingError` fail-loud (enforce.py:107).
- Unrecognized non-empty role: fail-closed violation; empty `enforced_roles` fails ALL non-empty roles (safe direction).
- Build without attestation: `gate_missing`; junk/unbound attestation: `gate_invalid`; tampered GateDecision: hooks-side `verified_decision` raises — never dispatches.
- Confinement not covering a chain provider: load refused (semantic pass).

## Security implications

This IS the enforcement surface; every change tightens (fail-open roles → fail-closed;
unconditional build deny → authenticated, launch-bound attestation; no manifest → structurally
validated governance). The one loosening — build seats may dispatch — is gated on the
authenticated #429 gate + unchanged forbidden rules (never-Haiku, cross_model_author). Actual
mutating-launch confinement remains W2/W5 (declared here, enforced there — receipts/docs must
not imply resume or confinement are OPERATIONAL yet; peer risk item, adopted into docs wording).
**Sequencing (pass-2 P6, owner-DECLINED with evidence):** enabling the build path before W2/W5
enforce manifests is safe because the launch surface is DORMANT until W7 — `executorRouting`
defaults to `inherit` (no live cutover; merging changes no workflow), no workflow dispatches
build through the executor before W7 #470's rewiring, and the campaign queue lands W2 #465 and
W5 #468 BEFORE W7 — so by the time any workflow can reach a build dispatch, tool grants,
confinement pinning, and the canary are live. The W1 unlock is owner-ratified epic architecture
(AC-D3; Q1–Q6 decisions 2026-07-18). No secrets touched; `credential_ref` semantics unchanged.

## Platform / external dependencies

platform_apis: none

(pure stdlib + jsonschema — already a runtime dep of the package, `phase_executor/pyproject.toml:8`; both call-site idioms precedented at contract.py:160/:166)

## Multi-PR assessment

Single PR. Coherent enforcement surface; tests dominate line count; splitting schema from
check_pre surgery would ship a half-wired state.

## Test plan (red-before-green anchors)

1. RED: `check_pre(role="build", attestation=<valid bound>)` expecting pass — fails on main (`gate_validation_unavailable`).
2. RED: `check_pre(role="biuld")` expecting `unrecognized_role` — silently passes on main (#434 part 2 cell).
3. RED: shipped-table assertions — manifest ×7, policy section, 7-seat set (main has 5, no manifest).
4. RED: schema rejects a seat without `manifest`; resume without `resume_opt_in` (proves the 2020-12 `if/then` under jsonschema 4.10.3 — breaker A5); confinement missing a chain provider; empty-string `role` (minLength 1); name↔role binding lint — `build` seat without `role: "build"` refused at load (breaker A2).
5. Attestation cells: missing → `gate_missing`; raw dict → `gate_invalid`; input_digest mismatch (cross-launch replay) → `gate_invalid`; `gate_outcome == "bakeoff"` presented to single dispatch → `gate_requires_bakeoff` refusal (pass-2 P1); FORGED-but-self-consistent GateDecision with mismatched `expected_context` → `verified_decision` fails; build dispatch with MISSING/empty/partial `--plan-context` → `GateContextError`, refused, no attestation (pass-2 P5); stale decision (different plan inputs) rejected ON THE REAL DISPATCH PATH (integration, not just unit); valid+bound+outcome-single → pass with receipt carrying `role`, `gate_outcome`, `gate_input_digest`, `gate_digest`=policy digest; new receipt with `role=="build"` but null gate fields FAILS `_validate_record` (pass-2 P2); old role-less receipts still validate.
6. Must-survive: role=None no-requirement (test_enforce.py:515); empty-string role treated as absent in check_pre (breaker S4); off_chain/forbidden/review cells; `test_digest_golden_vector` (inline fixture, untouched); old audit logs (no gate fields) still validate (`_RECEIPT_REQUIRED` unchanged).
7. Rewritten by design: test_enforce.py:101/:109 (deny → attestation semantics); test_driver_bench.py:109 (positive build-audit + fail-closed negative); routing-test fixture migration (breaker S3 + pass-2 SR2): `_table()` helper (test_routing.py:24-43) + every test caller of `load_routing_table`/`validate_routing_table` gains schema-valid manifest+policy AND canonical-named seats gain their matching `role` (the `_table()` review seat currently has none — the name↔role lint would red it) (grep ALL callers, not just enforce fixtures — enforce fixtures use unvalidated `from_table` and survive).
8. Hooks integration: authenticated gate → dispatch → receipt(gate fields) → observation → reconcile ok; tampered gate raises pre-dispatch (`GateTamperError`→`JudgeError` translation holds for bakeoff); missing `--gate-file` on build seat → exit 4; non-build seats need no gate; `executorRouting.seats.design = executor` → `MalformedConfig` exit 2 (breaker S2).
9. Drift guards: enforced-role review seats + design declare `fresh`; `set(DESIGN_MODELS) == set(design-row models)` (EQUALITY — breaker A6); shipped-table name↔role bindings (build/review); `policy.enforced_roles ⊆ ENFORCEABLE_ROLES` loader rejection cell (pass-2 P4); duplicate-model-id manifest-resolution cell — candidate discovered via another seat's chain resolves the DESIGN row's manifest (pass-2 P3, rule-pin; enforcement W2); `verified_decision` single-sourced (bakeoff_policy imports it).
