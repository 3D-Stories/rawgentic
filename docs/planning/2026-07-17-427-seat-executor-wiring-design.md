# #427 — Ship/intake/plan seats through the executor (E4) — design (rev 3)

**Epic** #422 child 3/10 · supersedes #418 · depends on #426 (✓). Plan authority:
`docs/planning/2026-07-16-per-phase-model-routing.md` §1, §3.3b, §3.4, §4 (E4 row).
Peer proposal (Codex) synthesized; **rev 2** applies the Step-4 design-critique findings (opus
self-review + Codex adversarial) — see "Review findings applied" at the end.

## Problem

`phase_executor/` (E1 #424 + enforce E2 #425 + seat-table E3 #426) exists but is consumed by
**nothing** — `run_seat` is imported nowhere in `hooks/`/`skills/` (grep-confirmed). This child ships
the **first consumer CLI** for the **ship, intake, plan** seats: the glue + CLI + per-seat toggle +
tests. **Scope is mechanism-only** (authorized by the epic's #417 split): #427 adds the CLI and its
tests but edits NO WF step, so **on merge no WF2/WF3 run routes any seat through the executor** — the
mechanism is *verified* here, and the live choke point becomes effective when #417 wires the call
sites and an operator opts a seat in. Default per-seat state is `inherit`, so merge changes no live
behavior.

Out of scope (later children): the WF2/WF3 *prose* call sites (#417); run-end `reconcile_run` over a
whole WF run + telemetry rollups (#420); competitive rounds / bake-off (#428); complexity gate
(#429). The **build** seat stays blocked — `enforce.check_pre` fails build closed
(`gate_validation_unavailable`) until #429; this child touches only ship/intake/plan.

## Confirmed facts (traced this session — evidence)

- `engine.run_seat(seat, prompt, *, snapshot, quota, capture_root, context=(), correlation_id=None,
  author_provider=None, run_id=None, effort=None, timeout=300.0, dispatch=_dispatch_real) ->
  Observation` (`engine.py:65`). The loop (`engine.py:92-113`) calls `dispatch(engine, req,
  run_id=…, attempt_id=…, …)` **exactly once per attempted target** and returns on the first
  non-availability status (`engine.py:111-112`) — so a wrapper on `dispatch` fires on the primary AND
  each real fallback, the exact granularity we want. `attempt_id` is `f"{i}-{uuid…}"` where `i` is the
  0-based index into `eligible_targets` (`engine.py:100`).
- `AdapterRequest` (`adapters/base.py:21-32`) carries `seat, requested_model, transport,
  credential_ref` — **NOT** `provider/auth_mode/pool/participation_mode`. So the dispatch wrapper
  CANNOT reconstruct the full lane from `req`; it must source the target from the chain list (see
  Enforcement wiring).
- `routing.eligible_targets(seat, snap, author_provider=)` returns the ordered
  `[primary, *chain]` target dicts (each `{model, lane:{provider,transport,auth_mode,pool,
  credential_ref,participation_mode?}}`). Live targets (verified): **ship** sonnet-5→opus-4-8→fable-5
  (all `claude`); **intake** opus-4-8→fable-5→sonnet-5; **plan** opus-4-8→fable-5→gpt-5.6-terra
  (terra=`codex`). Pools: `claude:2, codex:4, zhipu:2`.
- `enforce.check_pre(seat, target, snapshot, …) -> PreReceipt` needs the FULL lane; `target_identity`
  (`enforce.py:34-49`) compares it verbatim to the seat's declared chain identities
  (`enforce.py:110-111`). `verify_post(obs) -> PostCheck`; `RoutingAuditLog(capture_root, run_id)`
  re-sanitizes and appends `run_id` as a subdir (`enforce.py:226-234`) — so its `capture_root` must
  NOT already contain `run_id` (see Path derivation).
- `hooks/model_routing_lib.py resolve(workspace, project, role) -> (model, effort)` for roles
  {review, analysis, implementation} — the "prior behavior" INHERIT preserves. Its `_load_block`
  (`model_routing_lib.py:35`) is private and hardcoded to the `"modelRouting"` key.
- **Import feasibility (verified live):** `PYTHONPATH=phase_executor/src python3 -c "import
  phase_executor.engine, .routing, .enforce"` exits 0 under the plain repo interpreter — core modules
  import stdlib + `jsonschema` only; the zhipuai worker is uv-isolated.
- **`.rawgentic/` is NOT ignored repo-wide** — the tracked `.gitignore` only has
  `.rawgentic/review-state/*.json` + `.rawgentic_workspace.json`; the blanket ignore is checkout-local
  (`.git/info/exclude:7`), absent in fresh clones / CI. #427 adds tracked ignores in the PROJECT repo (below).
- **Base = the project repo root, not the workspace root** (finding V1): `<workspace_dir>` (where
  `.rawgentic_workspace.json` lives) is NOT a git repo (confirmed `git -C /home/rocky00717/rawgentic
  rev-parse` rc=128), so dirs there escape every repo's `.gitignore`. `projects/rawgentic` IS a git
  repo. Derived capture/permit dirs therefore go under the resolved project repo root
  (`<workspace_dir>/<project.path>`), where the tracked `.gitignore` covers them.

## Approach (recommended of 3)

**A — thin consumer hook `hooks/executor_routing_lib.py` (RECOMMENDED; I and the Codex peer
converged).** Pure core + thin `main(argv)` CLI in ONE file (repo §5 exemplar `registry_prune.py`);
imports `phase_executor` via a `sys.path` shim **inside `main`** (guarded, see below). Subcommands
`resolve-seat` and `dispatch`. Toggle in the `.rawgentic_workspace.json` project block.
*Pro:* keeps `phase_executor` extraction-clean; "logic in hooks, judgment in skills". *Con:* one more
hook. (Name `executor_routing_lib` aligns with the config key and cannot shadow the `phase_executor`
package on direct execution.) **B** (toggle inside `phase_executor`) rejected — pollutes the
extraction boundary. **C** (extend `model_routing_lib`) rejected — role→model resolution (fail-OPEN)
vs seat execution (subprocess/audit, fail-CLOSED) are different responsibilities.

## File changes

| File | Change |
|---|---|
| `hooks/executor_routing_lib.py` | **NEW.** Pure core (`resolve_seat`, `dispatch_seat`, `WIRED_SEATS`, `DRIVER_ONLY`, config parse, path derivation, per-attempt `check_pre` dispatch wrapper) + thin `main(argv)`. Guarded `phase_executor` import inside `main`. |
| `hooks/model_routing_lib.py` | Parameterize `_load_block(workspace, project, key="modelRouting", *, missing=_ABSENT)` (default preserves callers) so the glue reuses ONE loader AND can tell an ABSENT key from a present-but-non-dict value (finding V3: the old `_load_block` returns `{}` for both, erasing the absent-vs-malformed distinction A2/S7 needs). |
| `projects/rawgentic/.gitignore` | **Add `/.rawgentic/runs/` and `/.rawgentic/runtime/`** (tracked, repo-distributed) so derived capture/permit dirs are ignored in every clone/CI — not the blanket `/.rawgentic/` (that would make the already-tracked `.rawgentic/review-state/README.md` "tracked-yet-ignored"). |
| `tests/hooks/test_executor_routing.py` | **NEW.** Both-path model assertions (stub `dispatch` records dispatched target + actual_model), per-attempt check_pre identity (primary + fallback), chain-fallback (named seat), driver-only, inherit-no-dispatch, absent-vs-malformed config, guarded-import→exit-5, path-safe validation, audit-sequence split, exit-code mapping, CLI-via-subprocess, `git check-ignore` on a representative capture path. |
| `tests/phase_executor/live/test_executor_routing_live.py` | **NEW.** `@pytest.mark.live` real ship-seat `claude --print`; asserts reported `actual_model == claude-sonnet-5` (skipped unless `RUN_LIVE=1`). |
| `.rawgentic_workspace.json` | rawgentic project block gains `executorRouting` (below), all seats `inherit`. |
| `docs/config-reference.md` | Document `executorRouting` + the seat↔WF-step mapping (for #417). |
| `README.md` | Changelog entry (§2 exact shape) + count strings if any. |
| version ×3 surfaces | 3.44.0 → **3.45.0** (minor, feat). |

### `executorRouting` schema (`.rawgentic_workspace.json` project block)

Per-seat mode map (peer-adopted — explicit per-seat state, one-seat-at-a-time cutover):

    "executorRouting": { "version": 1,
      "seats": { "intake": "inherit", "plan": "inherit", "ship": "inherit" } }

**Absence vs invalidity (Step-4 finding A2/S7 — this is an enforcement/verification boundary, so §3
says fail-CLOSED on invalidity, not fail-open):**
- **Absent** block, or a seat **absent** from `seats` → that seat is `inherit` (legitimate
  "not configured yet"; fail-safe).
- **Present but malformed** — the `executorRouting` value present but NOT an object (a string/list —
  finding V3), OR an object with an unsupported `version`, unknown seat key, or a mode other than
  `inherit`/`executor` → `resolve-seat`/`dispatch` return **exit 2** (`{ok:false,error:{code:
  "malformed_config",…}}`). A typo'd `executor` must fail loud, NOT silently run the legacy path
  (that would be a false-cutover: opted-in in intent, off in fact, with only a stderr hint a headless
  run misses). **The glue MUST distinguish absent (→inherit) from present-non-object (→exit 2)** —
  it reads the raw project-block value via `_load_block(..., missing=_ABSENT)` and branches on the
  sentinel, never on an empty-dict collapse.

### CLI contract (three-way tagged; JSON in / one JSON object out; diagnostics to stderr)

- `resolve-seat --seat <s> --workspace <w> --project <p>` → `{"seat","action":"inherit|executor|
  driver_only","primary_model":<str|null>,"reason"}`. `driver_only` for `merge|ci_triage|
  deploy_verify|step16`. `inherit` ⇒ orchestrator uses the pre-existing branch incl. its current
  `model_routing_lib.resolve` call (`primary_model` null — resolve-seat never restates a legacy
  model). `executor` ⇒ `primary_model` = `eligible_targets(seat)[0]["model"]`. **Exit 0** for a valid
  known name; **exit 2** unknown name or malformed config.
- `dispatch --seat <s> --prompt-file <f> --run-id <id> [--context-file <f> …] [--correlation-id <id>]
  [--author-provider <p>] [--effort <e>] [--timeout <s>] --workspace <w> --project <p>` → derive
  capture/permit dirs → build snapshot + `QuotaCoordinator` + `RoutingAuditLog` → `run_seat` with the
  per-attempt `check_pre` wrapper → `verify_post` once → append. stdout `{"ok":true,"action":
  "executor","seat","requested_model","actual_model","parse_status","verified":true,"dispatched_lane",
  "audit_path","observation":{…}}`. Valid ONLY for a seat the config has in `executor` mode
  (dispatch on an inherit/driver_only seat → exit 2).

**Exit-code taxonomy** (peer-adopted): `0` ok · `2` malformed input/config or invalid seat/mode · `3`
chain-exhaustion / quota / timeout / availability (retryable) · `4` enforcement pre-check denial or
requested≠actual identity breach (non-retryable) · `5` audit/capture/internal/import failure
(non-retryable). Every non-zero emits `{"ok":false,"error":{"code","message","retryable"}}`; exit 5
carries the `correlation_id`. **An executor failure NEVER converts to inherit/driver_inline** — it
fails loud so the orchestrator runs the ERROR protocol (owner directive: no driver-inline fallback for
intake/plan; `inherit` is a rollout state decided BEFORE dispatch, not a runtime fallback).

## Enforcement wiring (Step-4 finding S1 — the load-bearing correctness fix)

`dispatch_seat` first computes `targets = routing.eligible_targets(seat, snapshot, author_provider)`
(it needs `targets[0]` for `resolve-seat` anyway). It builds a **dispatch decorator that closes over
`targets`** and, on each call, selects the target by **attempt index** — parsing the leading `i` from
`attempt_id` (`f"{i}-…"`, `engine.py:100`), NOT reconstructing the lane from `req` (which lacks
provider/auth_mode/pool). For target `targets[i]` it calls `check_pre(seat, targets[i], snapshot, …)`,
appends the receipt to `RoutingAuditLog`, and only then calls the real `_dispatch_real`. Because
`run_seat` attempts `targets[i]` in order and stops on first success, the wrapper runs `check_pre`
against the **correct full-lane target** for the primary and every real fallback. After `run_seat`
returns, `verify_post` runs once on the final Observation, and the observation + post-check are
appended. **Deferred to #420:** run-end `reconcile_run` over the whole WF run's expected-seat ledger.

## Audit sequences (Step-4 finding A6 — two distinct contracts)

- **Pre-check denial** (`check_pre` verdict=fail, BEFORE any provider call): append the **denial
  receipt only** (no Observation exists); `run_seat` never dispatches that target; `dispatch` exits 4.
- **Identity breach** (`verify_post` not verified, AFTER a successful dispatch): the pre-receipt +
  the returned Observation + the failed post-check are all appended; exit 4.

Separate tests assert each sequence's audit shape.

## Path derivation (rev 3 — base = PROJECT REPO ROOT; findings V1, V2, A3, S6)

**Base = the project repo root**, resolved from the workspace config's `project.path`
(`<workspace_dir>/projects/rawgentic`) — NOT the workspace root. `<workspace_dir>` is NOT a git repo
(finding V1), so dirs there would escape every repo's `.gitignore`; `projects/rawgentic` IS a git
repo, so repo-local dirs are covered by its tracked `.gitignore` and the check-ignore test is real.

- `capture_root` — passed to BOTH `run_seat` AND `RoutingAuditLog`, **run_id-LESS** (finding V2):
  `<repo>/.rawgentic/runs/`. The adapters prepend `run_id` via `create_capture` (`claude_cli.py:72`
  et al.) and `RoutingAuditLog` appends `sanitize_component(run_id)` (`enforce.py:226-234`), so BOTH
  land under `…/runs/<run_id>/` exactly once — captures at `…/runs/<run_id>/<seat>/<attempt_id>/`, the
  audit file at `…/runs/<run_id>/routing-audit.jsonl` as a sibling. (rev 2 passed a run_id-ful root to
  run_seat → the adapters double-nested run_id; fixed.)
- quota permits: `<repo>/.rawgentic/runtime/permits/<pool-sig>/`, `pool-sig` = short hash of
  `snapshot.pool_concurrency()` (finding A3). All rawgentic WF2 runs share this project-repo permit
  dir → a real `claude:2` ceiling ACROSS concurrent runs that agree on pools; incompatible pool defs
  get separate namespaces (never a silent shared ceiling). rawgentic is the sole consumer of this hook
  (kukakuka consumes `phase_executor` on its own path), so project-repo scope is exactly the
  coordination needed.

`run_id` and `project` are validated path-safe (reject `/`, `..`, control chars, empty) before any
path is built — exit 2 on violation. All dirs live under `<repo>/.rawgentic/`, ignored by the project
repo's tracked `.gitignore`.

## Error handling and failure modes

- **Chain exhausted / quota / timeout:** exit 3 (retryable). No inline fallback.
- **Pre-check denial / identity breach:** exit 4 (non-retryable) — audit sequences above.
- **Audit-write failure AFTER provider execution:** exit 5, non-retryable, error carries
  `correlation_id`; never auto-retry (would duplicate the external call).
- **Malformed executorRouting:** exit 2 (present-malformed); inherit only for absence.
- **phase_executor import failure:** the import runs **inside `main()`** guarded by `try/except
  ImportError` → exit 5 + the structured envelope (finding S3 — a module-level import would abort with
  a bare traceback + exit 1, emitting neither). Fail-closed: a routing boundary that can't load denies.

## Security implications

- Adapter owns the model flag; prompt on stdin (no argv injection) — inherited; glue passes
  `--prompt-file` content as `run_seat`'s `prompt`.
- Capture/permit paths are **derived** from validated `run_id`/`project`, never caller-supplied.
- Derived dirs live under the **project repo root** and are ignored repo-wide via the added tracked
  `projects/rawgentic/.gitignore` entries `/.rawgentic/runs/` + `/.rawgentic/runtime/` (findings
  A4/S2/V1) — a `git check-ignore` test against a REAL runtime path (`.rawgentic/runs/<id>/…`) enforces
  it, so captured prompts/observations can't be committed in a fresh clone/CI. (The rev-2 workspace-root
  base would have escaped every repo's ignore — fixed.)
- Audit log append-only + contained path (`RoutingAuditLog`). No secrets in the glue; `credential_ref`
  is a config-dir NAME (E8), never a secret value.

## Platform / external dependencies

platform_apis:
- api: import phase_executor.{engine,routing,enforce,contract} under the repo CPython via a guarded sys.path.insert inside main()
  feasibility: verified via existing-call-site — tests/phase_executor/conftest.py applies the identical sys.path shim; live import test this session (PYTHONPATH=phase_executor/src python3 -c "import phase_executor.engine, phase_executor.routing, phase_executor.enforce") exited 0; core modules import stdlib + jsonschema only
  failure: fail-loud
  surface: dispatch/resolve exit 5 with the ImportError message + envelope; test_executor_routing simulates the ImportError and asserts exit 5
- api: subprocess claude --print --model <m> --output-format json (and codex/zhipu adapters) via phase_executor.adapters
  feasibility: verified via existing-call-site — phase_executor.adapters.claude_cli, fixture-tested against captured provider output; bench cell-runner lineage (400+ cells) proves the invocation pattern
  failure: fail-loud
  surface: non-zero process exit / non-ok Observation.parse_status → dispatch exits 3/4. Live per-provider execution (creds/permissions in the target env) is proven at OPT-IN time by the @live test (RUN_LIVE=1), not on merge — #427 defaults every seat to inherit, so no unproven live call fires until an operator opts a seat in after running the @live preflight for that provider

## Test strategy — asserts ACTUAL executing model, both paths (AC3)

- **Executor ON (stubbed, no live call):** inject a stub `dispatch` that RECORDS the attempted target
  AND returns a provider-style Observation with `actual_model` = that target's model (never assert
  only the requested model). Assert `dispatch_seat` returns `actual_model == claude-opus-4-8` (intake,
  plan) / `claude-sonnet-5` (ship); `verified==True`; audit line appended.
- **Per-attempt check_pre (finding S1):** assert a `check_pre` receipt whose `target_identity` equals
  the DECLARED chain entry's identity was recorded for the primary AND a fallback attempt (catches the
  target-plumbing bug if the wrapper ever reconstructs from `req`).
- **Chain-fallback (finding S4 — named seat, exact model):** seat = **intake**; stub `targets[0]`
  (opus-4-8) as an availability failure → assert returned `actual_model == claude-fable-5`
  (= `eligible_targets[1]` = intake chain[0]); assert both attempts got a check_pre receipt.
- **Executor OFF / inherit (finding S5 — assert what #427 owns):** `resolve-seat` → `action==inherit`;
  the injected dispatch spy has call-count 0. The `model_routing_lib.resolve` value is asserted only
  as a **no-touch guard** (labeled as such — #427 does not edit that resolution), NOT as a "live WF
  inherit path preserved" proof (there is no call site until #417).
- **Config:** absent block → all inherit; present-non-object `executorRouting` (string/list) → exit 2
  (finding V3 — assert absent≠present-non-object); present-object-malformed (bad version / unknown seat
  / invalid mode) → exit 2.
- **Guards:** driver-only name → `driver_only`; unknown name → exit 2; path-unsafe `run_id`/`project`
  → exit 2; guarded-import failure (monkeypatched sys.path) → exit 5 + envelope.
- **Audit sequences (A6):** pre-check-denial → denial receipt only, no Observation, exit 4;
  identity-breach → pre-receipt + Observation + failed post-check, exit 4.
- **Exit-code mapping:** availability-fail → 3; verify_post breach → 4; audit-append failure → 5
  (carries correlation_id); assert `retryable` flags.
- **`.gitignore` (A4/S2/V1):** `git check-ignore` (run inside `projects/rawgentic`) on a REAL derived
  path (`.rawgentic/runs/<id>/routing-audit.jsonl`, `.rawgentic/runtime/permits/<sig>/…`) resolves via
  the tracked `.gitignore` — the path the runtime actually writes, not a repo-relative decoy.
- **CLI-via-subprocess** (repo convention): both subcommands via `subprocess.run([sys.executable,
  CLI, …])` asserting stdout JSON + exit codes.
- **@live** (`RUN_LIVE=1`, CI-skipped): real ship-seat call → reported `actual_model == claude-sonnet-5`.

## Multi-PR assessment

Single PR. ~450–550 lines (one new hook + a small model_routing_lib tweak + two test files +
config/doc/gitignore/version). Near the 500-line threshold but one cohesive mechanism, no separable
phases.

## Seat ↔ WF-step mapping (documentation for #417 — NOT behavior in #427)

| Seat | WF2 step it will wire (in #417) | Executor-ON model | OFF/inherit prior behavior |
|---|---|---|---|
| intake | Step 2 (analyze codebase) | opus-4-8 | `analysis` role (sonnet) via Agent tool |
| plan | Step 5 (implementation plan) | opus-4-8 | inline / prior role dispatch |
| ship | Step 12 (README/changelog/version×3/docs) | sonnet-5 | orchestrator inline |

Driver-only (NEVER a seat): merge, CI triage, deploy+verify, Step 16.

## Review findings applied (Step-4 design critique — rev 1 → rev 2)

Opus self-review (7) + Codex adversarial (6), merged/deduped:
- **S1 (High, arch)** decorator target-plumbing → Enforcement wiring now closes over `eligible_targets`
  and selects by `attempt_id` index; never reconstructs the lane from `req`. + per-attempt identity test.
- **A2/S7 (High/Med)** malformed-config false cutover → absence→inherit, present-malformed→exit 2
  (fail-closed; it's an enforcement boundary).
- **A3 (High)** permit-dir cross-pool ceiling → permit dir keyed by pool-signature; honest scope.
- **A4/S2 (High, security)** `.git/info/exclude` local-only → add tracked `/.rawgentic/` + check-ignore test.
- **A1/S5 (Crit→Med)** dormant-adapter overclaim → Problem statement rescoped to "first consumer CLI,
  mechanism-only, live choke point lands with #417"; inherit test reframed as a no-touch guard.
- **S3 (Med)** module-level import defeats exit-5 → import guarded inside `main()`.
- **S4 (Med)** chain-fallback test wrong model → named intake, exact `claude-fable-5`.
- **A6 (Med)** pre-check-denial vs identity-breach audit → two distinct sequences + tests.
- **S6 (Low)** capture_root double-nest → RoutingAuditLog gets a root without `run_id`.
- **§3 note** → parameterize `model_routing_lib._load_block(key=…)` to share one loader.
- **A5 (Med, feasibility, deferred-safe)** live per-provider execution unproven → default-off means no
  live call on merge; @live is the per-provider opt-in preflight (surfaced in platform_apis).

### rev 2 → rev 3 (Step-4 re-gate verifier findings)
- **V1 (High, security)** gitignore fix mis-targeted at the non-git workspace root → **base = project
  repo root** (`<workspace_dir>/<project.path>`); tracked `.gitignore` + check-ignore test now cover the
  REAL paths.
- **V2 (Med, arch)** adapter captures double-nested run_id → pass `run_seat` the run_id-LESS
  `capture_root` (same root as RoutingAuditLog); adapters/audit each append run_id exactly once.
- **V3 (Med, completeness)** `_load_block` collapsed absent and present-non-dict to `{}` → parameterize
  it with a `missing` sentinel; glue exits 2 on present-non-object, inherits only on true absence.
- Confirmed resolved by the verifier against the code: S1 (decorator/attempt_id index), A3 (pool-sig),
  A1/S5 (scope claims), S3 (guarded import), S6 (audit de-nest).
