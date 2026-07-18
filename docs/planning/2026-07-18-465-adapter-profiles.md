# #465 — W2: agentic adapter profiles (conditional persistence, codex sandbox pinning, effort gating)

**Issue:** #465 (epic #475 W2; depends on #464, satisfied) · **Date:** 2026-07-18
**Complexity:** standard · **Lane:** full spine

platform_apis:

- api: claude CLI launch flags (--no-session-persistence, --effort, --allowedTools, --max-budget-usd)
  feasibility: verified via spike — spike #455 (docs/planning/2026-07-17-spike-455-resume-mechanics.md, --help verbatim + live persistence probes, CLI 2.1.212) + live `claude --help` probe on this host 2026-07-18 confirming --allowedTools and --max-budget-usd + AC-doc F1/F2
  failure: fail-loud
- api: codex CLI sandbox config (-s workspace-write, sandbox_workspace_write.exclude_slash_tmp/.exclude_tmpdir_env_var/.writable_roots, approval_policy)
  feasibility: verified via spike — spike #452 (docs/planning/2026-07-17-spike-452-codex-containment.md, live Landlock probes incl. the three-override confinement run, codex-cli 0.144.1)
  failure: fail-silent
  surface: the compose-time validator refuses any mutating launch missing an override, and the W5 canary (#468) asserts effective confinement behaviorally — a sandbox silently ignoring options is caught by the canary, never trusted from exit codes
- api: provider effort acceptance (codex model_reasoning_effort, GLM reasoning_effort)
  feasibility: verified via spike — spike #456 (docs/planning/2026-07-17-spike-456-effort-vocabularies.md, live probes: value sets + per-model max gating)
  failure: fail-loud

## Problem (confirmed, spike-grounded)

Three adapter launch behaviors block agentic (mutating/resumable) seats:
1. `adapters/claude_cli.py:26` pins `--no-session-persistence` unconditionally — spike #455
   proved resume is unreachable while it is pinned, and that `claude -p` persists by default
   when the flag is absent (session_id in the envelope, cwd-scoped resume works).
2. `adapters/codex_cli.py:28-33` is read-only-only. Spike #452 (live Landlock probes) proved
   the naive `-s workspace-write` boundary is **/tmp-wide** — a worktree's siblings and the
   canonical checkout are silently writable (`exit 0`, no error). Worktree-only confinement
   requires exactly three overrides, live-verified.
3. Effort is passed verbatim with no per-model gate — spike #456 proved the vocabulary is
   identity across providers but support is **per-model** (`gpt-5.5` rejects `max`; `max` is
   gated to gpt-5.6-sol/terra/luna), and an unsupported value is a live 400 in an unattended
   run — or worse, a silent clamp.

## Approaches weighed

| | Approach | Verdict |
|--|--|--|
| A | Per-adapter ad-hoc params (session flag on claude, profile flag on codex, effort checks inline in each adapter) | Lose — three divergent seams, the exact drift class the shared-contract layer exists to prevent; refusal logic duplicated. |
| B | **LaunchProfile dataclass in `contract.py`** (session_policy, mutating, worktree, grants, budget bounds) derived from the seat manifest by ONE function; adapters consume it; effort gate + stepdown as pure contract functions with a static, spike-confirmed support table | **Win** — one derivation, one refusal point per invariant, pure/testable, extraction-clean (contract stays hooks-free). |
| C | Full manifest→CLI compiler (generic flag templating per engine) | Lose — templating machinery for 2 concrete profiles; YAGNI. |

## Design (approach B)

### B.1 Effort gate + stepdown (`contract.py`, pure)

- `EFFORT_LADDER = ("low", "medium", "high", "xhigh", "max")` (ordinal).
- Capability data lives in a NEW `model_capabilities.py` registry module (peer: contract
  stays lean; data is checked-in + offline — runtime NEVER probes a provider):
  `SUPPORTED_EFFORT: dict[canonical-model-id, tuple]` enumerating **every canonical id the
  shipped table dispatches** (S3): `claude-fable-5`, `claude-opus-4-8`, `claude-sonnet-5`
  → all 5 (spike #456 §0: the claude CLI vocabulary); `gpt-5.6-sol`, `gpt-5.6-terra` (and
  `gpt-5.6-luna`, spike-confirmed) → all 5; `gpt-5.5` → low..xhigh; `glm-5.2` → all 5.
  `CAPABILITY_REVISION: int = 1`. **Drift guard (S3):** a test asserts
  `SUPPORTED_EFFORT.keys() ⊇ {canonicalize(m) for every model in the shipped table}` so a
  table addition without a registry row fails loudly, never a latent runtime refusal.
- `resolve_effort(model, requested, *, engine) -> EffortResolution(requested, native, resolution, capability_revision)` — `engine` is the validated enum {claude, codex, zhipuai} (V7 parity; already in scope at engine.py:94)
  (pure, contract.py; `resolution ∈ {identity, stepdown, adapter_default}`): identity when
  supported; nearest-LOWER ladder level otherwise (`max`→`xhigh` on gpt-5.5). **None
  handling is a per-ENGINE policy resolved in the ONE stamped source (S1 + P2-S1):**
  engine `claude`/`zhipuai` + None → native null (flag omitted; provider default;
  `resolution: "identity"`); engine `codex` + None → native `"high"` with
  `resolution: "adapter_default"` — the policy lives in `model_capabilities.py`
  (`ENGINE_NONE_EFFORT = {"codex": "high"}`), NOT in the adapter:
  the codex adapter keeps a None fallback **SOURCED FROM THE REGISTRY** (S1 pass-3, the
  smallest wire-safe diff): `effort=req.effort or ENGINE_NONE_EFFORT["codex"]` — one
  source of truth (the registry), and the wire stays byte-identical on BOTH dispatch
  paths: `run_seat` (which passes native `"high"` for codex-None anyway) AND
  `run_competitive`/`_run_candidate` (engine.py:179-193 — a PUBLIC path that builds
  requests with effort None and never resolves; the earlier "only production path"
  claim was false against it and is withdrawn). **Unknown model +
  a REQUESTED effort → refuse** (dispatch-configuration error, peer-hardened: never guess
  a capability; the routing table's models are exactly the known set, so an unknown id
  means the registry lacks a row — fail closed, fix the registry). Unknown model + `None`
  effort dispatches fine. A requested value below every supported level → refuse (cannot
  step UP; unreachable today, guarded).
- **Observation records the resolution as an OBJECT** (peer shape; ONE key set — P2-S3,
  dataclass fields == JSON keys, AC3's "requested + native" literal): optional field
  `effort` = `{requested, native, resolution, capability_revision}` on the
  Observation + schema. Optional addition = non-breaking → `schema_version` stays 1
  (bump-on-breaking convention); consumers tolerate absent `effort` on legacy records
  (named mixed-dataset note). `build_observation` threads it; the engine populates it from
  the SAME EffortResolution composition used — recorded value == sent value by
  construction.

### B.2 LaunchProfile (`contract.py`) + manifest derivation

`LaunchProfile(session_policy: str = "fresh", mutating: bool = False,
worktree: str | None = None, tool_grants: tuple = (), effective_grants: tuple = (),
max_budget_usd: float | None = None)` — `effective_grants` is `init=False` (P3-5),
populated ONLY by `profile_from_manifest`; adapter validators additionally assert
`mutating == ("edit" in effective_grants or "bash" in effective_grants)` so a hand-built
inconsistent profile refuses at spawn. **Mutating derivation is restricted to
{claude, codex}** — a zhipuai manifest carrying edit/bash grants REFUSES (P3-6).

`profile_from_manifest(manifest: dict, *, engine, worktree=None) -> LaunchProfile` — ONE
derivation (V7: `engine` is a validated enum {claude, codex, zhipuai} — the caller knows the
target's lane engine): `session_policy` read as REQUIRED and validated against
{fresh, resume} — missing or unknown REFUSES (V2 + S5: #464 made the field explicitly
non-defaultable; the LaunchProfile dataclass default of fresh exists ONLY for the
byte-identical no-profile compat path). `mutating` = `"edit" in tool_grants or "bash" in
tool_grants`; **effective grants apply the closure `bash ⇒ net`, recorded as
`effective_grants` on the profile (V5 — the shipped build manifest is [read, edit, bash]
without net, and claude has no OS sandbox here: grants are capability selection, not a
network boundary; the closure makes the recorded capability honest instead of refusing the
shipped table)**; budget from `bounds.max_budget_usd` when present. **Fail-closed derivations (peer):** a mutating CLAUDE
profile REQUIRES a positive `max_budget_usd` (the enforceable cost bound; the manifest
schema gains the optional `bounds.max_budget_usd` number); a mutating profile REQUIRES a
worktree. `AdapterRequest` gains `profile: LaunchProfile` (default fresh/read-only — every
existing caller unchanged, byte-identical commands). **Compose→validate→spawn (peer):**
each adapter's `run()` composes the argv itself and runs its validator immediately before
spawn — no prebuilt-argv entry point exists, so validation cannot be bypassed; the
validators are separately importable for the W5 canary (#468). Named boundary (peer risk):
tool grants are CAPABILITY SELECTION, not a sandbox — a `bash` grant can reach the network
regardless of `net`; OS-level confinement (codex Landlock; claude has none here) is the
security layer, and the doc/tests never present grants as one. Codex has no provider cost
cap — its compensating bound is the enforced `timeout` (already terminating the process
group), documented as NOT a dollar ceiling.

### B.3 claude profile (`adapters/claude_cli.py`) — AC1

`build_command(model, effort=None, profile=None)`:
- Three-way branch IMMEDIATELY BEFORE SPAWN (P3-2 — profiles are publicly constructible,
  so derivation-time validation alone is bypassable): `profile is None` or
  `session_policy == "fresh"` → flag ADDED; validated `"resume"` → flag OMITTED; ANY other
  value → `CompositionError`. Red-before-green on the conditional. **Mutating claude
  launches spawn with `cwd` set to the canonicalized `profile.worktree`** (P3-1 — claude
  has no OS sandbox here; an ambient cwd must never receive Edit/Write/Bash side effects;
  mutating-without-worktree already refuses at derivation).
- `session_policy == "resume"` additionally records nothing extra here — resume WIRING
  (capturing session_id, issuing `--resume`) is W3/W4 territory (worktrees + supervisor);
  W2's contract is only that the flag no longer forecloses it (the issue's AC1 letter).
- Grants/budget composition: `--allowedTools <mapped>` and `--max-budget-usd <n>` appended
  when the profile carries them. **The wire maps from `effective_grants` — record ==
  command, one truth (P2-S2)**; mapping `read→Read,Grep,Glob`, `edit→Edit,Write`,
  `bash→Bash`, `net→WebFetch,WebSearch`; the shipped build manifest `[read, edit, bash]`
  composes exactly `--allowedTools Read,Grep,Glob,Edit,Write,Bash,WebFetch,WebSearch`
  (closure; deterministic cell — no legacy wire exists, no production grants-present
  caller until W7). Absent grants/budget → no flags (today's command, byte-identical).
  Mapped-tool ACCEPTANCE + budget ENFORCEMENT semantics are asserted by a RUN_LIVE-gated
  live cell (P2-A5 — the probes verified flag EXISTENCE + persistence behavior; the
  semantic half awaits the live cell, and W7's activation gate stands before any
  production use).

### B.4 codex profiles (`adapters/codex_cli.py`) — AC2

- Read-only: `build_command` unchanged byte-for-byte (`-s read-only`).
- Mutating: `build_mutating_command(model, worktree, effort, containment_root)` composes
  `-s workspace-write` plus **exactly these literals (V1, spike #452 §2b :77-82, live-
  verified):**
  `-c sandbox_workspace_write.exclude_slash_tmp=true`
  `-c sandbox_workspace_write.exclude_tmpdir_env_var=true`
  `-c sandbox_workspace_write.writable_roots=["<canonical worktree>"]`
  plus `-c approval_policy=never` (S4 — spike §4/§6 belt-and-suspenders against a user
  config.toml `on-request`; INFERRED-safe, pinned in the command test) and
  **`-C <worktree>` (cwd == worktree, peer-pinned)** — paths canonicalized immediately
  before spawn (no validate/spawn gap). **Containment (V6, partial; propagation P2-A1):**
  `AdapterRequest` gains `containment_root: str | None` (executor-supplied);
  `run()` REQUIRES it whenever `profile.mutating` (missing → CompositionError) and passes
  it explicitly to `build_mutating_command`; the worktree must canonicalize UNDER it and
  must not equal it; proof that the path is a MANAGED worktree (registry) is W3/W4
  territory — named deferral, and no production caller dispatches mutating in W2 (V11). **Compose-time refusal (fail-closed, spike report :153):**
  a dedicated `validate_mutating_composition(cmd, worktree)` asserts all three override
  literals present and `writable_roots` == exactly the canonicalized worktree
  (containment-checked against traversal); `build_mutating_command` runs it before
  returning, and refusal raises `contract.CompositionError` (new, RuntimeError subclass) —
  REFUSED before spawn, never a naive launch. The validator is separately importable so the
  W5 canary (#468) can re-assert the same predicate at runtime.
- The mutating path is selected by `profile.mutating` in `run()` — requires
  `profile.worktree` set (else CompositionError: a mutating launch without a declared
  worktree is exactly the /tmp-wide hazard).

### B.5 What W2 does NOT do (scope)

Engine scope is exactly (S2): `run_seat` resolves effort ONCE per target
(`resolve_effort(target_model, requested)`), passes `native` into `AdapterRequest.effort`,
and stamps the resolution object onto the returned Observation via `replace()` — the
`dispatched_lane` precedent (engine.py:110); recorded == sent from one source. Seats all
declare `session_policy: fresh` today so behavior is byte-identical. NOT in W2: resume
session-id capture (W3/W4); the runtime canary (W5 #468); **and the rollout invariant
(V11 + P2-A2): no production caller dispatches a mutating codex profile in W2, and W7's
wiring MUST gate mutating dispatch on the W5 canary having LAUNCHED the exact composed
command in the target execution environment and asserted BEHAVIORAL confinement (a write
inside the worktree succeeds; a write outside is blocked) — composition validation alone
never unlocks mutating dispatch (codex accepting-but-ignoring sandbox options is the
fail-silent case only behavior catches); W5's behavioral assertions ALSO cover the
observed cwd and approval behavior (P3-7 — `-C` and the spike-INFERRED
`approval_policy=never` are proven there, not assumed from the argv pin). SYMMETRIC
CLAUDE GATE (P3-4): W7 MUST refuse every grants-present or budgeted claude launch until a
RUN_LIVE canary has launched that exact argv in the target environment and asserted
mapped-tool acceptance + budget behavior. SECURITY BLOCKER (Step-11 R2-F1): claude has NO
OS FS sandbox here (only cwd pin + path containment + budget) — W7 MUST NOT wire a
MUTATING-claude dispatch until claude gains a real FS sandbox (bwrap/landlock), or restrict
mutating dispatch to codex; recorded as a binding cross-child constraint. Grants/worktree/
mutating audit-trail on the Observation is W6 #469 (Step-11 R2-F2).** AC3's Observation recording is the `run_seat`
single-dispatch path; competitive candidates carry no effort input today (nothing
requested → nothing misrecorded; P2-S5). The claude
mutating-budget requirement bites only at mutating-profile derivation — the shipped build
manifest carries no `bounds.max_budget_usd`, so deriving its mutating profile REFUSES
until the owner sets the number at wiring time (W7): fail-closed, cost decision deferred
to the human. No live provider calls in the default suite (live cells RUN_LIVE-gated —
AC4).

## Tests (AC1/2/3/4)

- claude: fresh profile/none → command contains `--no-session-persistence` (byte-identical
  to today's list, pinned); resume profile → flag ABSENT; red-before-green on the
  conditional. Grants/budget mapping cells; no-grants = today's exact command.
- codex: read-only unchanged (exact list pin); mutating with all three overrides + worktree
  → composed; each override removed (mutation harness on the validator) → CompositionError;
  worktree traversal/mismatch → CompositionError; `profile.mutating` without worktree →
  CompositionError.
- effort: identity cells per provider; gpt-5.5 `max` → native `xhigh`,
  `resolution == "stepdown"` (V10); unknown model + REQUESTED effort → dispatch-config
  error; unknown model + None → passthrough (V4); codex None → native `high`,
  `resolution == "adapter_default"` (S1); claude None → native null; Observation `effort`
  object round-trips the edited schema (V9: additionalProperties:false made the edit
  mandatory; optional-additive precedent keeps schema_version 1); registry⊇table drift
  guard (S3).
- Full suite green vs 3514/8; live cells RUN_LIVE-gated only.
