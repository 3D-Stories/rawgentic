# Peer Consult — .rawgentic-peer-problem-424.md

- Date: 2026-07-16
- Reviewer: Codex (peer designer)

## Approach

Build E1 as a flat, repository-root Python package with an immutable configuration snapshot and a small synchronous orchestration core. Separate provider-independent domain logic, schema validation, routing, eligibility, quota coordination, hashing, and record construction from provider-specific command execution and SDK access. Each adapter composes three independently testable pieces: a pure request/command builder, a live transport, and a strict pure parser fed by captured provider envelopes. `run_seat` pins one routing epoch, walks eligible targets in chain order, acquires the selected target's shared quota, executes in an isolated attempt directory, parses evidence without inference, validates the Observation, and appends an audit record. `run_competitive` submits candidates concurrently, relying on the same quota coordinator, then invokes the caller-supplied judge policy and results sink after candidate completion. CI runs schemas, fixtures, parser failures, routing, concurrency, and audit tests without importing provider SDKs or requiring `uv`; separately marked local integration tests perform the three authenticated calls and the timed bake-off.

## Key decisions

- Use repository-root `pyproject.toml` and `uv.lock` with a flat `phase_executor/` package. Running `pytest tests/ -q` from the repository root therefore imports `phase_executor` through Python's normal root-path behavior, while the same directory is explicitly included by the build backend. Do not add a `sys.path` shim, duplicate package, or nested project.
- Lay out the package as `contract.py` for schema-backed DTO construction and validation; `models.py` for immutable request/result types; `engine.py` for orchestration; `routing/{loader,snapshot,eligibility}.py`; `quota.py`; `capture.py`; `audit.py`; `hashing.py`; `adapters/{claude_cli,codex_cli,zhipuai_sdk}.py`; `transports/subprocess.py`; and `parsers/{claude,codex,zhipuai}.py`. Keep JSON Schemas under `phase_executor/schemas/` and real sanitized captures under `tests/fixtures/providers/`.
- Represent every route entry as a complete target containing both `requested_model` and a lane object. A lane alone cannot support never-Haiku checks, cross-model eligibility, command construction, or useful quota selection. Give each lane an explicit `pool` key so lanes sharing the same credential or provider quota share one semaphore without relying on fragile derived identity.
- Keep adapters synchronous but dependency-injected: each receives a subprocess runner, clock, capture store, credential resolver, and parser. Pure builders return argv, stdin bytes, environment requirements, and expected artifact paths; they never use a shell. The zhipuai adapter launches a PEP 723 worker through the prescribed `uv run` command, so importing and fixture-testing the package never imports `zhipuai` or requires `uv`.
- Make parsers accept captured bytes or decoded objects and return a provider-evidence structure before Observation construction. Preserve the original envelope as a capture, reject missing or ambiguous identity and usage, and never substitute the requested model, SDK argument, command-line flag, or zero token counts for provider evidence.
- Define explicit failure semantics in the Observation schema: all keys remain structurally required, but `actual_model` and `usage` may be null only for a non-success `parse_status`; an `ok` observation conditionally requires a nonempty provider-reported model and provider-reported usage. This avoids both fabricating evidence and making pre-envelope timeouts impossible to record.
- For Claude, derive identity only from `modelUsage`. Accept a single unambiguous model key; if a real fixture proves multiple keys are legitimate, add a documented deterministic rule based on stronger inner-envelope evidence rather than choosing the requested model. Sum cache-read and cache-creation counts into `usage.cached` only if the schema defines that meaning explicitly, while retaining the raw envelope for audit.
- Treat Codex and ZhipuAI live captures as release gates, not implementation assumptions. Promote an authenticated capture into a sanitized fixture only after confirming where the innermost model ID and final provider usage actually appear. If `codex exec -o` contains only the model-generated payload, or ZhipuAI streaming omits final usage, the adapter must fail closed and the invocation contract must be amended; prompting the model to report its own identity is not evidence.
- Implement quota accounting with an injected process-wide `QuotaCoordinator` keyed by `pool`. Rawgentic's Claude child pool has capacity two; the Claude driver is external to that semaphore and consumes the separately reserved third working slot. Every attempt acquires exactly once immediately before live execution and releases in `finally`, including timeout, parser failure, and cancellation paths.
- Resolve chain eligibility before quota acquisition. Traverse primary followed by chain entries, skip every target prohibited by project-owned rules, and select the first eligible target with no index arithmetic. Cross-model checks compare normalized engine families against caller-supplied author provenance; never-Haiku checks use target model metadata. Record skipped targets and reasons in the attempt record so a later fallback is auditable.
- Pin an immutable routing snapshot at the start of each seat or competitive run. Compute its digest from schema-validated canonical JSON bytes, excluding resolved secret values. An explicit atomic reload validates and constructs a new snapshot, swaps it under a lock, and appends one epoch event only when the digest changes. In-flight runs retain their old digest; new runs use the new one, and neither is treated as a mismatch.
- Run competitive candidates with a fixed-size thread executor and submit all candidates before awaiting any result. Each attempt gets a unique capture directory and output filename derived from run ID, candidate ID, and attempt ID. Use an empty/read-only per-attempt working directory and pass required context as input; E1 does not permit candidates to mutate a shared checkout.
- Define AC4 over candidate execution only, before judging and sink latency. The fixture must be schedulable within pool limits—for example, no more than two Claude candidates with the remainder on Codex or ZhipuAI. A three-Claude bake-off cannot satisfy the bound under a capacity-two pool and should fail a feasibility preflight rather than advertise impossible parallelism.
- Keep audit output append-only JSON Lines with a per-process lock, one validated record per write, and capture paths relative to a configured capture root. The caller-supplied results sink receives the completed competitive record; sink or judge failures follow caller policy but never erase candidate Observations or leak quota permits.
- Cut hot file watching, async APIs, distributed or cross-process quota coordination, mutable worktree management, CCR support, direct provider HTTP, generalized plugin discovery, cost normalization, automatic credential provisioning, detached execution, and speculative parsers from E1. Retain only explicit reload, sequential chain fallback, in-process quotas, deterministic captures, strict evidence parsing, and concurrent competitive execution.

## Risks

- The specified Codex `-o` artifact may contain only the schema-constrained assistant result and no trustworthy model or token metadata. If live verification confirms that, AC3 and the mandatory evidence contract are incompatible with the fixed command and must block release.
- ZhipuAI streaming may expose model identity on chunks but omit usage unless a provider-specific stream option or terminal chunk behavior is enabled. Aggregating partial chunks, double-counting usage, or taking identity from the request would silently corrupt observations.
- Claude `modelUsage` can become ambiguous if a single invocation bills more than one model. Choosing the first dictionary key would be nondeterministic and could conceal fallback or internal routing.
- Requested and actual model identifiers may differ only by aliases, dated revisions, or provider prefixes. AC3 needs a documented comparison policy: preserve exact provider evidence in `actual_model`, then compare through a separately tested canonicalization function rather than rewriting the evidence.
- A semaphore scoped to one Engine instance would allow multiple engines in the same process to exceed the Claude limit. Conversely, counting the external driver inside the capacity-two child semaphore would unnecessarily reduce candidate capacity to one.
- Cancellation and timeout handling can leak permits or leave subprocesses running. The live runner must terminate the process group, wait for cleanup, finalize capture metadata, and release quota in a `finally` path.
- The 1.3× target is impossible when three candidates contend for a capacity-two pool, and can also be distorted by first-time `uv` dependency resolution. The benchmark needs a feasibility check, prewarmed dependencies, synchronized submission, and explicit measurement boundaries.
- Shared repository working directories invite generated files, config discovery, and output collisions even when intended operations are read-only. Isolation must be enforced by attempt-specific directories and transport sandboxing, not naming conventions alone.
- Eligibility rules can be applied against the requested seat instead of each concrete fallback target, causing a forbidden author/reviewer pairing after fallback. Rule evaluation must run for every target using normalized target metadata.
- Reload races can mix a new routing table with an old digest if components read global configuration independently. All routing, rules, pool definitions, and digest values for a run must come from the same pinned snapshot.
- Canonicalization drift between Python and Rust could produce different routing digests for equivalent documents. Publish canonicalization rules and cross-language golden vectors with the schemas.
- Raw captures may contain prompts, responses, credential-adjacent headers, or provider metadata. Captures require restrictive permissions, deterministic redaction boundaries, and no secrets in argv, config digests, audit events, or fixtures.

## Sketch

Repository root
├── pyproject.toml
├── uv.lock
├── phase_executor/
│   ├── __init__.py
│   ├── engine.py                 # synchronous orchestration only
│   ├── contract.py               # schema loading, validation, Observation factory
│   ├── models.py                 # frozen requests, targets, evidence, records
│   ├── quota.py                  # shared pool semaphore coordinator
│   ├── capture.py                # isolated attempt directories and raw artifacts
│   ├── audit.py                  # append-only observations and epoch events
│   ├── hashing.py                # prompt/context hashes and canonical config digest
│   ├── routing/
│   │   ├── loader.py             # parse and schema-validate project data
│   │   ├── snapshot.py           # immutable epoch and atomic reload
│   │   └── eligibility.py        # pure chain traversal and forbidden rules
│   ├── adapters/
│   │   ├── claude_cli.py         # builder + runner + parser composition
│   │   ├── codex_cli.py
│   │   └── zhipuai_sdk.py
│   ├── parsers/
│   │   ├── claude.py             # pure envelope-to-evidence functions
│   │   ├── codex.py
│   │   └── zhipuai.py
│   ├── transports/
│   │   └── subprocess.py         # timeout and process-group lifecycle
│   ├── workers/
│   │   └── zhipuai_call.py       # PEP 723, live dependency boundary
│   └── schemas/
│       ├── observation.schema.json
│       └── routing-table.schema.json
└── tests/
    ├── fixtures/providers/       # sanitized real envelopes plus provenance notes
    ├── test_contract.py          # includes kukakuka CCR-shaped document
    ├── test_parsers.py
    ├── test_routing.py
    ├── test_quota.py
    ├── test_epochs.py
    ├── test_competitive.py
    └── live/                     # explicitly selected authenticated AC3/AC4 tests

run_seat:
pin snapshot → hash inputs → iterate primary+chain → pure eligibility check → acquire target pool → create isolated attempt → live adapter → strict parser → Observation schema validation → capture/audit append → release pool

run_competitive:
pin one snapshot → feasibility check → submit all candidate run_seat calls → per-pool semaphores govern only constrained lanes → collect immutable observations → caller judge policy → caller results sink → return winner, losers, judge observation, and record

---
_Peer proposal (report-only). Synthesize at your discretion._
