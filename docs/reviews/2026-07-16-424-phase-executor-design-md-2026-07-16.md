# Adversarial Review — 2026-07-16-424-phase-executor-design.md

- Date: 2026-07-16
- Artifact type: design
- Reviewer: Codex (model config-default, reasoning effort high)
- Findings: 6 (Critical 0, High 3, Medium 3, Low 0)

## Summary

The artifact designs a packaged, multi-provider execution engine with routing, quota control, capture, and competitive execution. Its main risks are an import path that bypasses real packaging, quota enforcement that stops at process boundaries, unresolved provider telemetry contracts, and underspecified public execution behavior.

## Findings

### 1. [High] correctness · high confidence — §9b, binding amendment 2

> **Process-wide `QuotaCoordinator`** (`quota.py`), not a per-`Engine`-instance semaphore — two
>    Engine instances in one process must not jointly exceed the Claude limit.

A process-wide coordinator cannot enforce the stated Claude ceiling across separate hook, worker, or test processes. Two concurrent processes can each grant two Claude permits, producing four child calls and violating the capacity-2 quota.

**Recommendation:** Replace the process-local coordinator with an inter-process permit mechanism keyed by pool and credential/account identity, such as an atomic file-lock/token implementation or an external quota service. Add a test that launches two OS processes and asserts their combined active count never exceeds two.
**Ambiguity:** The artifact does not state that all phase execution is guaranteed to occur in one OS process or that the ceiling is intentionally process-local.

### 2. [High] correctness · high confidence — §4.3, Claude actual-model extraction

> actual = requested if present as a key; else the sole key if exactly one; else **ambiguous →
>   identity failure** (engine rejects). Auxiliary models (e.g. a haiku subagent call) recorded
>   separately, never confused with the seat model.

Claude identity selection uses exact raw-key equality before the later canonical comparison. If the provider reports an aliased or revised ID for the requested model and also reports an auxiliary model, there are multiple keys and no exact requested key, so this rule rejects a valid seat call even though `canonicalize_model_id()` could identify it.

**Recommendation:** Replace the Claude selection rule in §4.3 with: “Canonicalize the requested ID and every `modelUsage` key; require exactly one key whose canonical ID equals the requested canonical ID; preserve that raw key as `actual_model`; record all other keys as auxiliary; zero or multiple canonical matches is identity failure.”

### 3. [High] feasibility · high confidence — §9b, Divergence / Revised layout

> Instead — **src layout**, package
> self-contained under `phase_executor/` (its own `pyproject.toml`+`uv.lock`, extraction-ready),
> imported by the gate via a **localized** `tests/phase_executor/conftest.py` one-line
> `sys.path.insert(0, <repo>/phase_executor/src)` — matches the repo's established
> `sys.path.insert` convention, zero blast radius on existing tests.

The only demonstrated import path is a test-scoped `sys.path` mutation. It bypasses package installation and metadata, so CI can pass even if the package cannot be installed or imported by the E2–E8 production consumers; those consumers will otherwise encounter `ModuleNotFoundError` unless they independently invent an installation or path-injection mechanism.

**Recommendation:** In §4.2 and the revised layout, define the production consumption mechanism and add a packaging smoke gate: create an isolated environment, install `./phase_executor`, and execute `import phase_executor` without the test conftest. Require E2–E8 entry points to use that installed package rather than adding further `sys.path` mutations.

### 4. [Medium] completeness · high confidence — §4.6, `run_competitive`

> `run_competitive(seat, candidates, judge, rubric, *, failure_strategy, sink) -> (winner, losers,
> judge_obs, record)`.

This public primitive has no specified `judge`, `failure_strategy`, or `sink` protocols and no state transition for partial candidate failure, judge failure, sink exception, or cancellation of already-running candidates. E1 therefore cannot produce a stable contract for E5, and independent implementations can return incompatible tuples or leak running work and quota permits.

**Recommendation:** Add a `run_competitive` contract subsection defining typed protocols and return schemas, allowed failure-strategy values, ordering, behavior for every candidate/judge/sink failure, cancellation propagation, capture finalization, and quota release. Cover each transition with engine tests using deterministic stub adapters.
**Ambiguity:** The artifact explicitly defers policy to E5 but does not distinguish deferred winner policy from the execution and failure semantics E1 must implement.

### 5. [Medium] feasibility · high confidence — §4.3 and §9b, binding amendment 7

> **Codex/zhipuai captures are RELEASE GATES, not assumptions** — if `codex exec -o` carries no
>    trustworthy model/usage, or zhipuai streaming omits terminal usage, the adapter **fails closed**
>    and the invocation contract is amended; prompting a model to self-report identity is never
>    evidence.

The provided text contains no successful exact-object-kind capture proving that either current invocation exposes the telemetry required for an `ok` Observation. The cited existing zhipuai call site explicitly consumes only streamed content, and the Codex path returns the requested model, so both adapter contracts remain unverifiable from the provided text. “The invocation contract is amended” does not specify an executable alternative if either release gate fails.

**Recommendation:** Make the Codex and zhipuai telemetry spikes prerequisites to implementation. Add their sanitized captures and exact successful invocation forms to §8, and specify the replacement transport or revised E1 scope for each possible gate failure before adapter work begins.

### 6. [Medium] feasibility · high confidence — §4.2, uv-native package dependencies

> `zhipuai>=2.1.5` + `sniffio` are an **optional
>   extra** (`[project.optional-dependencies] glm`) because the zhipuai adapter shells out via
>   `uv run --with "zhipuai>=2.1.5" --with sniffio …`

The live command resolves an unbounded dependency overlay rather than demonstrating use of the committed lock. A future compatible-version release or unavailable package index/cache can change or prevent execution even while the checked-in `uv.lock` remains unchanged, undermining the claimed pinned, reproducible package.

**Recommendation:** Change §4.2 and the PEP 723 worker contract to execute the locked `glm` environment, for example by syncing the project with the extra under `--locked` and running the worker from that environment. Add an offline-after-sync live-launch smoke test and prohibit unbounded `uv run --with` resolution per model call.

---
_Report-only: this review does not edit the artifact. Findings are advisory; incorporate them at your discretion._