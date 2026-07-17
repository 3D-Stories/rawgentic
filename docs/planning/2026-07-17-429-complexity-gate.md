# #429 — deterministic complexity gate in plan_lib (E6, epic #422) — lane design+plan

**Small-standard lane** (1 impl file ≤ 7). Plan authority: `docs/planning/2026-07-16-per-phase-model-routing.md` §3.2. depends on #424 (✓). Key-independent.

## Brief design (lane)

Add `hooks/complexity_gate.py` — a NEW module BESIDE `plan_lib` (single-responsibility, like
`model_routing_lib` / `executor_routing_lib`). It is executor-consumed (#428/#430), NOT a WF2-prose
helper, so it deliberately stays out of plan_lib's skill-wired public surface (the
`test_skill_helpers` reverse drift-guard). A **pure, fail-closed** decision function:

```
needs_bakeoff(task, issue, plan_est, cfg) -> GateDecision
```

- **`GateDecision`** (frozen dataclass): `decision: bool`, `reason_codes: tuple[str,...]`,
  `input_snapshot: dict` (the exact inputs the decision was computed from — risk_level, complexity,
  security_surface_hit, lines, file_count, thresholds), `policy_digest: str` (sha256 over the policy
  inputs, reusing the existing `"sha256:"+hashlib` pattern; the executor recomputes it at admission
  so the gate can't be edited between plan and run).
- **Triggers (OR):** `task.risk_level == "high"` · `issue.complexity == "complex"` ·
  `hits_security_surface(plan_est.files)` · `plan_est.lines > cfg BAKEOFF_DIFF_LINES` ·
  `plan_est.file_count > cfg BAKEOFF_FILE_COUNT`. Each firing trigger appends a reason code.
- **Fail-closed (owner directive):** missing/invalid mandatory metadata (unknown risk_level, missing
  complexity, non-int lines/file_count) → `decision=True` + a `fail_closed:<what>` reason code. A gate
  that can't evaluate its inputs bakes off rather than silently passing.
- **`SECURITY_SURFACE_PATTERNS`** — a NEW, narrower repo-owned constant (auth / secrets / payments /
  migrations / ci / crypto) distinct from `DEFAULT_HIGH_RISK_PATH_PATTERNS` (which is broader — jwt,
  session, oauth, middleware, …); `hits_security_surface(files)` matches any file path against it.
  Named limit (plan §3.2): plan-time size estimates can undershoot — the security-glob override is
  the backstop; the glob list is a maintained artifact.
- **Thresholds are config** (`cfg`): `BAKEOFF_DIFF_LINES`, `BAKEOFF_FILE_COUNT`. Read from the passed
  `cfg` (a dict/object); defaults if absent. No new dependency.

Consumers (the executor admission recompute, the WF2/WF3 bake-off dispatch) are LATER children
(#428/#430); #429 ships the pure gate + its tests only. No workflow-spine change.

## Platform / external dependencies

platform_apis: none

(Pure stdlib decision function — `hashlib` for the digest, already imported in plan_lib; no external
or platform API.)

## Plan (lane checklist — TDD)

### Task 1: SECURITY_SURFACE_PATTERNS + hits_security_surface
- riskLevel: standard
- RED: test hits_security_surface on auth/secrets/payments/migrations/ci/crypto paths (hit) + a
  plain path (miss). GREEN: add the constant + anchored matcher (mirror the DEFAULT_HIGH_RISK anchor
  pattern). verify: `pytest tests/hooks/test_plan_lib.py -k security_surface`.

### Task 2: GateDecision + needs_bakeoff (fail-closed)
- riskLevel: standard
- RED: tests — each trigger fires independently (high risk / complex / security-surface / lines>thr /
  files>thr); none fire → decision False; missing/invalid metadata → decision True + fail_closed
  reason; reason_codes accumulate; input_snapshot + policy_digest present + digest stable for same
  inputs. GREEN: implement the dataclass + function. verify: `pytest tests/hooks/test_plan_lib.py -k bakeoff`.

### Task 3: docs + config-reference thresholds note
- riskLevel: standard
- Document `BAKEOFF_DIFF_LINES` / `BAKEOFF_FILE_COUNT` + the security-surface list in the appropriate
  doc; note the gate is code-owned (prose never routes). verify: full suite green.

version 3.45.0 → 3.46.0 (minor, feat). No spine change → no diagram REV.
