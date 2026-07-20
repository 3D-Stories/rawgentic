# Design — fail-closed guardrail canary + --bare drift-guard (#468, W5 of epic #475)

**Blast radius:** HIGH — a security control. Spike #454 (CRITICAL): on a `defaultMode:auto` host
un-granted tools are auto-approved and run with NO gate except the hook layer, so the hook layer +
this canary are the *load-bearing* control (AC-B3, §5b OQ-2). A fail-OPEN check here silently grants
a mutation that should have been refused.

## Scope (what #468 ships vs what #470 wires)

#468 ships the canary **module** + its 5 fail-closed checks + the `canary_result` Observation field +
unit tests (each cell red-before-green) + RUN_LIVE live probes. The **wiring** of `run_canary` into the
production dispatch path (engine/supervisor/pane calling it before every mutating `mod.run`) is **#470
(W7)** — `run_canary`/`build_observation` are consumable now; #470 calls them. This matches the affected
components ("canary module consumed by adapters") and the dependency graph (#470 depends on #468).

## Confirmed source-of-truth
- Refuse idiom: `contract.CompositionError` (the established fail-closed pre-spawn refusal).
- Verdict shape: `enforce.check_pre` → `PreReceipt{verdict, violations: tuple}`, accumulate-all.
- Observation additive precedent: `dispatched_lane`/`effort`/`judge_degraded` (Optional=None,
  emit-when-set, listed in schema `properties`, `additionalProperties:false`, no version bump).
- hooks.json sha256 = `db0c6627…` @ plugin 3.75.0 (spike's `23cd86d2` is stale — churned since #499).
- codex: `codex_cli.validate_mutating_composition` (compose-time) + live seed
  `test_codex_mutating_confinement_live` — reuse both, don't re-implement.

## Design (synthesis of my draft + the cross-model peer consult, `docs/reviews/peer-rawgentic-peer-problem-468-*.md`)

### New module `phase_executor/src/phase_executor/canary.py`

**Two-function split (the peer's key correction — closes the fail-open risk).** The pure evaluator never
raises; the PRODUCTION API raises so a caller cannot silently ignore a refusal:
```python
@dataclass(frozen=True)
class CheckResult:  check_id: str; verdict: str; violation: Optional[str]  # "pass"|"refuse"|"not_applicable"
@dataclass(frozen=True)
class CanaryResult:                  # adversarial M2: identifiers live ON the result, no global-state lookup
    policy_revision: int
    policy_id: str                   # e.g. "claude_mutating"
    provider: str
    profile: str
    verdict: str                     # "pass" | "refuse"
    required_checks: tuple[str, ...] # resolved ordered check ids for this policy
    checks: tuple                    # ordered CheckResult (one per required_checks id)
    violations: tuple[str, ...]      # accumulated, deterministic policy order
    def pass_summary(self) -> dict:  # built from THIS result, EXACTLY the 8 schema keys: {policy_revision,
        ...                          #   policy_id, provider, profile, verdict, required_checks,
        ...                          #   passed_checks=[id for pass], violations=[]}  (== the closed schema set)
# not_applicable is FORBIDDEN for a required check (a required check is pass or refuse); not_applicable
# only marks a check a policy does not require. So passed_checks == required_checks on a dispatched pass.

class CanaryRefused(contract.CompositionError):   # adversarial M1: the refusal-carrying exception
    """Raised by require_canary on any non-pass. Carries the structured result; __str__ leads with the
    stable violation tags + policy_revision so a handler cannot reduce the refusal to a generic error."""
    def __init__(self, result: "CanaryResult"):
        self.result = result
        super().__init__(f"canary refused [{result.policy_id} rev{result.policy_revision}]: "
                         f"{','.join(result.violations)}")

def evaluate_canary(policy, evidence) -> CanaryResult:        # pure, never raises; tests/diagnostics
def require_canary(composition, evidence) -> CanaryResult:    # PRODUCTION: raises CanaryRefused unless verdict=="pass"
```
**The production API takes the immutable final `composition`, NOT a caller-selected policy (adversarial
H2 — a caller could otherwise pass the weaker `codex_mutating` policy for a Claude launch and skip the
hook checks).** `require_canary` derives the policy key internally (`_policy_for(composition)` from
`composition.provider` + `composition.profile.mutating`), asserts the derived provider matches
`evidence.provider` (mismatch ⇒ `canary_provider_mismatch` refuse), and an unresolvable composition ⇒
`canary_policy_unknown`. It is the only API #470's dispatch choke-point calls (exactly once, after the
final composition is known, immediately before spawn). Callers MUST NOT branch on the verdict themselves
— a test proves provider spawn is unreachable after a refusal, and that a Claude composition cannot be
evaluated under `codex_mutating` or any other mismatched policy. `evaluate_canary(policy, evidence)`
stays policy-explicit for hermetic unit tests only.

**Explicit policy matrix keyed by provider/profile** — omission of evidence never means success; every
required check returns `pass` or `refuse` (inapplicability of a REQUIRED check is a refusal, never a pass;
`not_applicable` only marks a check a policy does not require, and such checks are absent from `checks`);
an unknown provider/policy/profile refuses (`canary_policy_unknown`):
```python
POLICY_REVISION = 1
POLICIES = {
  "claude_mutating": ["hooks_digest", "plugin_version", "lane_provisioned", "positive_deny", "bare_absent"],
  "codex_mutating":  ["codex_containment", "bare_absent"],
}
```
Pinned constants (re-pinned per release, drift-guarded — set at implementation to the values live in
THIS PR, i.e. `EXPECTED_PLUGIN_VERSION = "3.76.0"` the version #468 ships, and the registration digest
computed over the current hooks.json + referenced scripts):
```python
EXPECTED_REGISTRATION_DIGEST = "<sha256 over hooks.json + referenced hook scripts, computed at impl>"
EXPECTED_PLUGIN_VERSION = "3.76.0"
```

Check semantics (each maps evidence → a `CheckResult`; a check that cannot evaluate its input refuses
with a stable tag — never a silent pass; an internal exception → `canary_check_error:<id>`):
- `hooks_digest` — missing/unreadable registration artifacts ⇒ `hooks_evidence_missing`;
  `compute_registration_digest()` (hooks.json + referenced scripts) ≠ pin ⇒ `hooks_digest_mismatch`.
- `plugin_version` — reads `.claude-plugin/plugin.json`; ≠ pin ⇒ `plugin_version_mismatch` (its OWN check,
  not folded into the digest — the peer's separation).
- `lane_provisioned` — missing/malformed init event ⇒ `init_evidence_invalid`; `rawgentic@rawgentic` ∉
  `init.plugins[]` ⇒ `lane_unprovisioned`.
- `positive_deny` — **per-mutating-class coverage, correlated + hook-origin** (adversarial-p2 High: one
  probe proves only that ONE tool is denied; a selective hook regression could deny SSH but allow Write).
  The policy declares the mutating tool/matcher classes to probe, **derived from hooks.json's PreToolUse
  matchers** (not invented): `Bash` (→ wal-guard) and `Edit|Write|MultiEdit|NotebookEdit` (→
  security-guard.py). One intrinsically-safe correlated probe per class; **EVERY class must produce a
  hook-origin denial or the check refuses.** Each result must correlate to its issued probe (expected
  tool + payload) AND carry an explicit hook-origin denial marker (the unique guard string). Missing/
  uncorrelated/ambiguous/non-hook ⇒ `positive_deny_unproven:<class>`; a probe actually executing (no
  deny) ⇒ `positive_deny_absent:<class>`; timeout/transport/malformed/provider-rejection all refuse. Each
  probe payload is **intrinsically non-mutating** (an OS-non-writable target) so an OS denial can never
  masquerade as a hook success — hook-origin evidence is required. Coverage is the CURATED set of
  enforcing-deny guards (`_GUARD_DENY_MARKERS`: wal-guard, security-guard) matched against the
  (digest-pinned) hooks.json PreToolUse matchers — NOT auto-derived from every matcher (security-review
  M1: wal-bind-guard/wal-pre are PreToolUse hooks but not deny-enforcers for this threat). A drift-guard
  (`test_pretooluse_guard_set_is_classified`) FAILS if hooks.json gains a PreToolUse guard absent from
  both the enforcer map and the non-enforcing allowlist, forcing a conscious classify — so a NEW
  enforcing-deny guard can never be silently left un-probed. Coverage is IN #468.
- `codex_containment` — reuse `codex_cli.validate_mutating_composition(final_argv, canonical_worktree)`; any
  raise/non-contained ⇒ `codex_containment` (retain sanitized detail, keep the stable tag).
- `bare_absent` — missing/malformed final argv ⇒ `argv_evidence_invalid`; exact `--bare` token anywhere in
  the final direct-exec argv ⇒ `bare_detected` (F3a: no `--no-bare` opt-out; the canary IS the enforcement).

**TOCTOU — enforceable launch contract for #470 (adversarial H4).** An ordering note ("verify then spawn")
does NOT bind the verified bytes to what the child consumes — the installed hooks/plugin-root can change
between verification and the provider opening them. So #470's wiring MUST: (a) stage a **complete minimal plugin tree** into an immutable, access-controlled
snapshot dir — `.claude-plugin/plugin.json` AND `hooks/hooks.json` AND every referenced executable AND
required runtime deps (adversarial-p2 High: omitting `plugin.json` leaves the snapshot an unloadable
plugin root, or forces `plugin_version` to read a mutable location — reopening the TOCTOU gap); (b)
compute BOTH `plugin_version` and the registration digest from THAT snapshot; (c) construct the child
env/argv (`CLAUDE_CONFIG_DIR`/plugin-root) to reference the snapshot and prohibit later resolution to a
mutable root; (d) ship a swap-attempt integration test that replaces a hook script **AND the manifest**
between `require_canary` and provider init and asserts the child cannot consume the replacement.

**Evidence binding — reject replay (adversarial-p2 High).** `CanaryEvidence` is not free-floating: it
carries a `dispatch_nonce`, the `snapshot_digest` (== the registration digest of the staged snapshot),
the `provider`/`profile`, and probe `correlation_id`s. `require_canary(composition, evidence)` refuses
(`evidence_binding_mismatch`) unless `evidence.snapshot_digest` equals the composition's staged-snapshot
digest and `evidence.dispatch_nonce` matches the composition's — so a previously-valid init+deny cannot
be replayed after the config/env changed. #468 defines the `CanaryEvidence` shape + the binding check +
unit tests that reject evidence replayed across nonces/snapshots; #470 wires the trusted collector that
populates the fields from the real dispatch.

### Observation `canary_result` field (additive, no version bump) — PASS summary only
A dispatched launch always passed the canary (a refusal never spawns → produces no Observation), so the
Observation carries only the **pass summary**; refusal data travels on the `CompositionError` (structured
result attached) via the pre-spawn diagnostic path, NOT the Observation.
- `contract.Observation`: `canary_result: Optional[dict] = None`; `to_dict` emits it only when set.
- `schemas/observation.schema.json`: add `canary_result` to `properties`. **The schema object MUST list
  EXACTLY the keys `pass_summary()` emits** (adversarial-p2 Critical: `pass_summary` returns `policy_id`
  but an earlier draft omitted it from the closed schema → every dispatched pass would fail
  `validate_observation`). Keys: `policy_revision` (int), `policy_id` (str), `provider` (str), `profile`
  (str), `verdict` (const `"pass"` — a dispatched Observation's canary is always pass), `required_checks`
  (array of str), `passed_checks` (array of str), `violations` (array, empty for a dispatched launch);
  `additionalProperties:false`; NOT in `required` (absent-tolerant, legacy/non-canary observations omit
  it). `schema_version` stays `"1"` (pure additive per #434). A test asserts `set(pass_summary().keys())`
  == the schema's `canary_result` property set (so the two can never drift).
- `build_observation(..., canary_result: Optional[dict]=None)` stamps `result.pass_summary()`.

### Registration digest = hooks.json + the scripts it references (adversarial H3)
The digest must bind not just the registration (`hooks.json`) but the **enforcing artifacts it references**
— a modified `wal-guard`/`security-guard.py` with an unchanged `hooks.json` would otherwise pass the pin
while no longer denying. `compute_registration_digest()` uses a **canonical framed encoding** (adversarial-p2 High: naive
concatenation permits boundary collisions — bytes moved between two files leave the concatenated hash
unchanged though each file now behaves differently). It hashes an ordered sequence of length-framed
records — `sha256( record(hooks.json) ++ record(script_1) ++ … )` where each `record` =
`u64(len(relative_path)) ++ relative_path ++ u64(len(content)) ++ content` — with: paths normalized
relative to the registration root; records ordered by normalized relative path; **duplicate paths
rejected**; **symlinks rejected**; **any referenced path outside the root rejected** (all fail-closed).
`EXPECTED_REGISTRATION_DIGEST` + `EXPECTED_PLUGIN_VERSION` are pinned constants; the drift-guard test
asserts pin == live (the #271 idiom) — re-pin whenever the registration OR any referenced enforcer
changes; a test redistributes identical concatenated bytes across two scripts and asserts the framed
digest CHANGES. Re-pin cost is one constant per release, caught by the guard.

**Probe coverage (adversarial H3→p2 re-raise, now IN #468):** after the reviewer raised it twice, the
per-mutating-class probe set moved into #468 (see the `positive_deny` check above) — the class set is
derived from the digest-pinned hooks.json PreToolUse matchers, every class must deny, and an unprobed
mutating class refuses. The digest binding above ensures the enforcer SCRIPTS (not just the registration)
are pinned, closing the "selective hook regression" gap the reviewer flagged.

## Platform / external dependencies

The canary's evaluator is pure Python (hashlib/json), but its EVIDENCE comes from provider CLI output
contracts — declared here (adversarial H1: `none` was wrong; the canary depends on these signals, and an
absent/changed signal must fail the canary, never silently pass):

platform_apis:
- api: claude -p --output-format stream-json init.plugins[] + wal-guard hook-deny is_error tool_result on the Claude CLI stream
  feasibility: verified via spike — spike #454 (live, PR #462, docs/planning/2026-07-17-spike-454-guardrail-canary.md:8,46,48): init reports plugins[] (empty fresh / rawgentic present provisioned); a denied Bash/SSH probe returns is_error:true with the unique wal-guard BLOCKED: string (hook-origin proof)
  failure: fail-loud
- api: codex sandbox mutating composition via codex_cli.validate_mutating_composition(final_argv, worktree)
  feasibility: verified via existing-call-site — codex_cli.validate_mutating_composition + live seed tests/phase_executor/live/test_live_seats.py::test_codex_mutating_confinement_live
  failure: fail-loud

**#470 gating prerequisite (adversarial H1):** the exact stream-json envelope parse (event `type` tokens,
the issued-probe↔tool_result correlation fields, the hook-origin marker) is confirmed against the REAL
live output as a NON-skippable spike when #470 wires the canary in — NOT left to an optional RUN_LIVE
test that can skip indefinitely. #468 ships the evaluator against the spike-#454-documented contract; the
RUN_LIVE probes here demonstrate it, and #470's wiring spike is the proving gate.

## Error handling / failure modes
- **Fail-closed everywhere:** any check that cannot evaluate its input returns a violation (never None);
  a provider missing a required input refuses. No silent pass.
- The Q5 cell (ungranted mutating tool under a non-auto profile): unit-tested via the positive-deny
  contract — the assertion is "the hook layer produced the deny"; if a probe result shows the tool ran
  without a deny, refuse. A live host with a non-auto profile is a #472 proving-run cell; #468 pins the
  unit contract + a RUN_LIVE probe that skips absent the host.

## Security implications
This IS the security control. The never-`bypassPermissions` posture (§5b OQ-2) is enforced upstream by
grants; the canary enforces that the hook layer actually fired (positive-deny) and the lane is
provisioned. No secrets, no new external surface. The digest pin binds the exact hook registration.

## Tests (red-before-green)
1. `tests/phase_executor/test_canary.py` (NEW): each check's pass + every refuse path (missing/malformed/
   mismatch/uncorrelated/exception) with its stable tag; `evaluate_canary` accumulates violations in
   deterministic policy order and never raises; the Q5 cell hermetically (fake transport, non-auto profile:
   correlated hook-denial passes; execution/missing/generic-error/malformed/transport-failure all refuse —
   scoped as CONTRACT validation, real-host is a live test).
1b. **require_canary composition-binding (adversarial H2):** a Claude composition CANNOT be evaluated under
   `codex_mutating` (or any mismatched policy) — the derived policy comes from the composition, not a caller
   arg; an unresolvable composition ⇒ `canary_policy_unknown`; provider/evidence mismatch ⇒
   `canary_provider_mismatch`. `require_canary` raises `CanaryRefused` on any non-pass, returns the result on pass.
1c. **CanaryRefused contract (adversarial M1):** the exception carries `.result` and its `str()` leads with
   the stable violation tags + policy_revision (a handler can recover them, not just "an error").
1d. **result data model (adversarial M2):** `pass_summary()` is built from the result alone (no global
   state) — `passed_checks == required_checks` on a dispatched pass; `not_applicable` is rejected for a
   required check.
2. Observation: `canary_result` pass-summary round-trips through `to_dict` + `validate_observation`
   (present + absent); schema `additionalProperties:false` rejects extra keys and a non-"pass" verdict.
3. `tests/phase_executor/test_canary_digest_pin.py` (NEW, drift-guard): `EXPECTED_REGISTRATION_DIGEST` ==
   live `compute_registration_digest()` (hooks.json + referenced scripts — adversarial H3) AND
   `EXPECTED_PLUGIN_VERSION` == `.claude-plugin/plugin.json` version; a mutated referenced script flips the digest.
4. `tests/phase_executor/live/test_canary_live.py` (NEW, RUN_LIVE + `shutil.which` skips): real `claude -p`
   init.plugins[] + a real hook-origin deny; codex out-of-worktree negative control (reuse the seed).
5. Full suite green.

## Adversarial pass 2 dispositions (after the design-loopback revision) — spec-tighten (approach unchanged)
The core design (evaluate/require, policy matrix, registration digest, additive Observation, CanaryRefused)
was STABLE across both passes; pass 2 found 1 Critical + 4 High that are refinements + 2 self-introduced
bugs, all applied here (not an approach change → spec-tighten, verified by an incremental verifier, not a
3rd codex pass which would regress on an exhaustive security reviewer):
- **Critical (policy_id ∉ schema):** FIXED — schema lists exactly the `pass_summary()` keys incl. policy_id;
  a test pins `set(pass_summary)==schema property set`.
- **High (digest boundary collision):** FIXED — canonical length-framed encoding + duplicate/symlink/
  outside-root rejection + a byte-redistribution collision test.
- **High (snapshot omits plugin.json):** FIXED (#470 contract) — stage a complete minimal plugin tree,
  compute plugin_version + digest from it, swap test covers the manifest.
- **High (evidence replay):** FIXED — `CanaryEvidence` binds dispatch_nonce + snapshot_digest; mismatch ⇒
  `evidence_binding_mismatch`; replay-rejection unit tests.
- **High (single-probe coverage):** ADOPTED IN #468 — per-mutating-class probe set from the hooks.json
  matchers; every class must deny; unprobed class refuses.
- **Mediums:** not_applicable clarified (only for non-required checks; `checks` holds only required, so
  pass/refuse only); refusal diagnostic-sink is #470's log wiring (CanaryRefused carries the tags); --bare
  token + snapshot-immutability + correlation-fields are #470 live-spike prereqs (already declared).

## Adversarial-review dispositions (Step 4, gpt/Codex) — one design loop-back consumed (design 1/2)
- **H1 (feasibility, platform_apis:none wrong):** ADOPTED — declared the Claude CLI stream-json + codex
  composition as platform deps with spike-#454 live evidence; #470 live confirmation made a gating prereq.
- **H2 (caller-selectable policy):** ADOPTED — `require_canary(composition)` derives the policy internally;
  test proves a Claude composition can't run `codex_mutating`.
- **H3 (probe/digest coverage):** ADOPTED the digest hardening (registration digest covers hooks.json + the
  enforcing scripts it references); per-tool-class probe coverage was initially scoped as a follow-up but,
  after the pass-2 re-raise, moved IN to #468 (per-mutating-class probe set — see the pass-2 block above).
- **H4 (TOCTOU only ordering):** ADOPTED — replaced the note with an enforceable stage-and-bind launch
  contract for #470 (immutable snapshot + swap-attempt integration test); #468 ships the contract + primitive.
- **M1 (refusal contract):** ADOPTED — `CanaryRefused(CompositionError)` carrying the structured result + tags.
- **M2 (data model):** ADOPTED — `check_id`/`policy_id`/`profile`/`required_checks` on the result;
  `pass_summary` built from the result; `not_applicable` forbidden for required checks.

## Provenance
Synthesized from my draft + the cross-model peer consult (gpt/Codex), then hardened against the Step-4
adversarial review (4 High + 2 Medium, all adopted; one design loop-back). Peer contributions: the
evaluate/require split, the policy matrix, `plugin_version` as its own check, correlated hook-origin
positive-deny with an intrinsically-safe probe, pass-summary-only Observation, hermetic Q5.

## Files touched
- `phase_executor/src/phase_executor/canary.py` (NEW)
- `phase_executor/src/phase_executor/contract.py` (Observation field + to_dict)
- `phase_executor/src/phase_executor/schemas/observation.schema.json` (canary_result property)
- `phase_executor/src/phase_executor/adapters/base.py` (build_observation param)
- `tests/phase_executor/test_canary.py`, `test_canary_digest_pin.py`, `live/test_canary_live.py` (NEW)
- version ×3 → 3.76.0 (minor, feat); README changelog; no diagram REV (no workflow-spine change —
  the canary isn't a WF2/WF3 spine station; #470 wiring will carry the REV).
