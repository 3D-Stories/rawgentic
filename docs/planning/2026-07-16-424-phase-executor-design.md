# Design — #424 (E1): `phase_executor/` package

**Date:** 2026-07-16 · **Issue:** #424 (epic #422) · **Complexity:** complex_feature (full WF2 spine)
**Design source of truth:** `docs/planning/2026-07-16-per-phase-model-routing.md` §3 (rev 3, merged).
This doc is the concrete E1 realization of that architecture. Every load-bearing claim is
**[C]onfirmed** (evidence) or **[I]nferred** (what would confirm).

## 1. Scope (what E1 is, and is NOT)

**Is:** the deterministic execution engine that will replace prose-subagent dispatch for
routed model seats — extracted from the proven bench cell-runner. Ships the normative JSON
Schemas, three engine adapters, the engine primitives (`run_seat`, `run_competitive`), and the
declarative routing config + loader, with tests.

**Is NOT (deferred to later children — do not build here):**
- routing-enforcement hooks (`routing_audit.jsonl`, Pre/PostToolUse digest+identity checks) → **E2 (#425)**
- seat-table cutover of WF2/WF3 prose, review→fable flip → **E3 (#426)**
- ship/intake/plan seat wiring, delegation boundaries → **E4 (#427)**
- competitive design rounds + build bake-off {sonnet,opus,terra} + glm judge + hybrid
  judge-failure policy + `bakeoff_results.jsonl` → **E5 (#428)**
- deterministic complexity gate `needs_bakeoff` in plan_lib → **E6 (#429)**
- driver-bench → **E7 (#430)**; multi-account Claude lanes → **E8 (#431)**

E1 provides the *primitives* E2–E8 consume. `run_competitive` takes the judge, the
judge-failure strategy, and the results sink as **caller-supplied parameters** — E1 implements
none of those policies (that is E5). The engine is policy-free (kukakuka conformance §3.5).

## 2. Approaches considered

- **A — clean-room a fresh executor.** Rejected. Plan §3 is explicit "do not clean-room"; the
  bench cell-runner's per-engine invocations are proven by 400+ cells [C: bench #14 lineage,
  `rawgentic-next/hooks/model_bench_lib.py`]. Clean-room throws away that evidence.
- **B — copy `model_bench_lib.py` and strip.** Rejected. It is 3617 lines welded to
  bench-only scaffolding (approval interlocks, bwrap repo-blind isolation, golden/campaign
  freeze) [C: model_bench_lib.py:304-1255]. Copying drags all of it in.
- **C (chosen) — extract the reusable core into a new small package.** Take the exact proven
  invocations + capture discipline + parse logic; leave the bench scaffolding behind. Adopt the
  richer Observation contract from plan §3.1 (renamed/extended vs the bench `observation.json`).
  Adapters split **pure parser** (fixture-tested, no I/O) from **live `run()`** (subprocess/SDK).

## 3. Package layout

```
phase_executor/                     # top-level, in-repo now (extracted when kukakuka consumes)
  __init__.py                       # public API re-exports
  contract.py                       # Observation dataclass + producer + to_dict(); schema-validate helper
  schemas/
    observation.schema.json         # normative, versioned (schema_version)
    routing-table.schema.json       # normative, versioned
  adapters/
    __init__.py
    base.py                         # AdapterRequest, capture-dir discipline (.incomplete + atomic write), shared outcome vocab
    claude_cli.py                   # parse_claude(raw_json)->Parsed  [PURE] + run(req)->Observation  [live]
    codex_cli.py                    # parse_codex(out_json, stderr)->Parsed  [PURE] + run  [live]
    zhipuai_sdk.py                  # parse_zhipuai(resp)->Parsed  [PURE] + run  [live, via uv run --with]
  engine.py                         # run_seat(), run_competitive(); chain/never-Haiku/cross-model/ceiling enforcement
  routing.py                        # load_routing_table(), config digest, lane/forbidden-combo lookup, epoch note
  pyproject.toml                    # uv-native package metadata + deps
  uv.lock                           # pinned lock

tests/phase_executor/               # runs in the existing `pytest tests/` gate
  conftest.py                       # sys.path.insert(0, REPO_ROOT) so `import phase_executor` works
  fixtures/
    claude-envelope.json            # REAL captured claude --output-format json (modelUsage+usage+total_cost_usd)
    codex-out.json                  # REAL codex exec -o JSON (captured live in AC3)
    codex-stderr.txt                # REAL codex stderr (header model: + token trailer) — fallback-parse fixture
    zhipuai-response.json           # REAL zhipuai response shape (captured live in AC3)
    kukakuka-observation.json       # hand-written kukakuka-shaped Observation (AC2)
    routing-table.json              # sample rawgentic seat table (also the shipped default)
  test_contract_schema.py           # AC2: schema-validate kukakuka-shaped + rawgentic observations
  test_claude_adapter.py            # AC1: parse_claude vs real fixture
  test_codex_adapter.py             # AC1
  test_zhipuai_adapter.py           # AC1
  test_engine.py                    # chain membership, never-Haiku, cross-model chain-aware skip, ceiling-2 accounting, digest/epoch — mocked adapters
  test_routing.py                   # load table, forbidden_combinations, per-lane concurrency, digest determinism
  test_live_seats.py                # AC3 (one seat/adapter, requested==actual) + AC4 (3-candidate parallel bake-off ≤1.3×) — @pytest.mark.live, skipped in CI
```

Shipped default routing table also lives at `phase_executor/routing/rawgentic.routing-table.json` (data, validated against the schema at load).

## 4. Key decisions

### 4.1 Pure/live split — the testability keystone
Each adapter is a **pure parse function** `parse(raw_bytes...) -> Parsed` (deterministic, no I/O,
fixture-tested → **AC1 runs in CI with no auth/tools**) plus a thin `run(request) -> Observation`
that does the subprocess/SDK call, writes the capture dir, calls `parse`, and assembles the
Observation. **AC3/AC4** exercise `run()` live and are `@pytest.mark.live` — skipped when the
tool/auth/SDK is absent (always in CI; I run them locally for the ACs). [C: CI has no
claude/codex/zhipuai — `ci.yml:47` installs only `pytest jsonschema pyyaml`.]

### 4.2 Import + uv-native reconciliation
- **Gate import:** `tests/phase_executor/conftest.py` does `sys.path.insert(0, REPO_ROOT)`, so
  `import phase_executor` resolves with zero install — matching the repo's `sys.path.insert`
  convention [C: `tests/hooks/test_driver_lib.py:20`]. **CI needs no change.**
- **uv-native package (owner decision):** `pyproject.toml` + `uv.lock` declare the package and
  its deps so it is `uv`/`pip`-installable for the kukakuka extraction later. `jsonschema` is a
  hard dep (already in CI [C: `ci.yml:47`]); `zhipuai>=2.1.5` + `sniffio` are an **optional
  extra** (`[project.optional-dependencies] glm`) because the zhipuai adapter shells out via
  `uv run --with "zhipuai>=2.1.5" --with sniffio …` (owner-validated invocation; 2.1.5 metadata
  bug omits sniffio) rather than importing zhipuai into the test process.
- **No CCR lane** in rawgentic's shipped routing table (owner ruling). The *schema* still
  admits `transport: ccr` (kukakuka needs it, AC2) — rawgentic just ships no such row.

### 4.3 actual_model extraction (the highest-risk correctness surface)
`actual_model` = provider-reported id from the INNERMOST envelope; **absent identity is a
failure, not a success** (plan §3.1). Per engine:
- **claude** [C: proven in model_bench_lib.py:2529-2559]: `modelUsage` dict keyed by model id.
  actual = requested if present as a key; else the sole key if exactly one; else **ambiguous →
  identity failure** (engine rejects). Auxiliary models (e.g. a haiku subagent call) recorded
  separately, never confused with the seat model. `usage`: `input_tokens`, `output_tokens`,
  `cache_read_input_tokens` (→ cached); cost from `total_cost_usd`.
- **codex**: parse from the `-o` JSON envelope. **[I — needs live verification]:** the in-repo
  codex path (`adversarial_review_lib.py:1411`) returns the *requested* model and never parses
  the actual id/usage from codex output. AC3 captures a real `codex exec -o` JSON envelope; the
  pure parser is written against that real capture. Fallback signal: stderr header `model:` line
  + `tokens used` trailer [C: model_bench_lib.py:2614, real capture]. If the `-o` envelope
  carries neither model nor usage, that is a named limitation surfaced in the Observation
  (`parse_status`), not a silent pass.
- **zhipuai**: **[I — build-new + live-verify]:** the in-repo glm path
  (`adversarial_review_lib.py:1484-1533`) reads only `chunk.choices[0].delta.content` — no model
  id, no usage. AC3 captures the real response object; parser reads model + usage from it
  (streaming may expose a terminal usage chunk — SDK-dependent, verified live).

### 4.4 Engine enforcement (`engine.py`)
- **chain membership:** requested model ∈ `seat.primary ∪ seat.chain`; else reject.
- **never-Haiku + cross-model author invariant:** evaluated as `forbidden_combinations` rows
  (data, not code) — never-Haiku is a rawgentic row; the cross-model reviewer-≠-author invariant
  is applied as a **chain-aware skip** (skip a chain entry that shares the author engine, advance
  to the next eligible — not blind next-entry). A chain exhausting eligible entries is a
  **handled hard failure**, never a silent downgrade.
- **Claude working-ceiling 2:** per-pool concurrency keyed by lane (`claude: 2`), enforced by a
  bounded executor/semaphore. Cross-pool candidates (codex/zhipu) never consume Claude slots.
  Queue wait recorded in `queued_ms`.
- **capture + digest:** every `run_seat` writes a capture dir (below) and stamps
  `routing_config_digest`. An **injectable sink** callable receives each Observation (E2 wires
  `routing_audit.jsonl`; E1 ships a no-op default). Engine stays policy-free.

### 4.5 Capture-dir discipline [C: adopted from model_bench_lib.py:1867-2013]
Per call: `mkdir(exist_ok=False)` → write `.incomplete` marker FIRST → `input.md` (prompt) →
run → `transport.stdout.txt` / `output.md` / `stderr.txt` / `observation.json` → unlink
`.incomplete` on success. All writes atomic (`mkstemp`+`fsync`+`os.replace`) so a crash never
leaves a half-written file. `raw_capture_path` in the Observation points at the dir.

### 4.6 `run_competitive` + parallel bake-off (AC4)
`run_competitive(seat, candidates, judge, rubric, *, failure_strategy, sink) -> (winner, losers,
judge_obs, record)`. Candidates launch **concurrently** across quota pools via
`concurrent.futures.ThreadPoolExecutor` (calls are I/O-bound subprocess/SDK waits — GIL
released), bounded by the per-lane concurrency limits (Claude ≤2). AC4's demonstration uses **3
candidates across the 3 pools** (claude + codex + zhipu) → genuinely parallel, no Claude-ceiling
contention, wall-clock ≈ slowest single candidate. Each candidate has its **own capture dir** —
these are model *calls* producing text, so no shared-tree collision; worktree isolation is the
mechanism for *file-mutating build* candidates (E5), noted but not required for E1's text demo.
`judge` and `failure_strategy` are caller-supplied (E1 tests pass a stub judge).

## 5. File changes
- **New:** the `phase_executor/` package + `tests/phase_executor/` (§3).
- **Version ×3 surfaces** [C: CLAUDE.md §2]: `.claude-plugin/plugin.json`,
  `plugins/rawgentic/.codex-plugin/plugin.json`, `test_plugin_version_bumped` — **minor** bump
  (feat).
- **README:** Changelog entry (§2 exact shape) + any count strings that move (this adds a
  package, not a skill — skill counts unchanged; verify no "provides N skills" drift).
- **CLAUDE.md §1:** add `phase_executor/` as a fourth structural layer note (new package, its
  own uv packaging, sys.path test import) — architecture-changed ⇒ CLAUDE.md update
  (completion-gate item 8).
- **`.rawgentic.json`:** fold the one-line `project.description` count refresh already dirty in
  the tree (staged by name) — per handoff.
- **Diagram decision:** no workflow-spine change → **no diagram REV** (E1 is machinery behind the
  existing prose path; the spine cuts over in E3/E4).
- **CI:** no change (jsonschema present; live tests skip).

## 6. Error handling / failure modes
- Adapter subprocess: nonzero exit / timeout / launch error → Observation with `process.exit_code`,
  `process.timed_out`, `parse_status`, `fallback_reason`; `.incomplete` left as crash marker.
- Ambiguous/absent `actual_model` → **identity failure** (not a success) — engine rejects the call.
- Routing table invalid (schema-invalid, unknown lane, cycle) → **fail-closed** load error.
- Forbidden-combination match → reject the call with the row's reason.
- Chain exhausted → handled hard failure (surfaced), never silent downgrade.
- zhipuai SDK / uv absent → the *live* path fails loud; the *pure* parser + AC1 are unaffected.

## 7. Security implications
- Adapters **own the model flag** — no user/caller-supplied string reaches the command line as a
  model or arbitrary flag (plan §3.1). Prompt goes on **stdin**, never as an argv (no injection
  via argv) [C: model_bench_lib.py:1918 `input=prompt`; adversarial_review_lib.py:1377].
- codex runs `-s read-only` + `--ephemeral` + `project_doc_max_bytes=0` [C: adversarial_review_lib.py:1364-1372] — no writes, no session-history persistence of prompts, no project-doc steering.
- Capture dirs written under a caller-controlled root; path components sanitized (no traversal
  from seat/model names). Atomic writes only.
- Secrets by name: `credential_ref` in lane objects names a config dir / env key, never a value.
- No secret is logged; raw captures may contain model output — captured under the run dir, not
  emitted to chat.

## 8. Platform / external dependencies
platform_apis:
- api: `claude --print --model <m> --output-format json` (Claude Code CLI)
  feasibility: verified via existing-call-site — `rawgentic-next/hooks/model_bench_lib.py` builds this exact invocation, proven across 400+ bench cells; a live AC3 seat call re-confirms on this host (`claude 2.1.211` on PATH)
  failure: fail-loud
- api: `codex exec --output-schema <s> -o <f> -c model_reasoning_effort=<e> --ephemeral --color never -c project_doc_max_bytes=0 -s read-only -C <cwd> --skip-git-repo-check -` (Codex CLI)
  feasibility: verified via existing-call-site — `hooks/adversarial_review_lib.py:1351-1374`, proven in WF5/WF13 (108 cells per owner); `codex-cli 0.144.1` on PATH, `codex login status` = Logged in
  failure: fail-loud
- api: zhipuai SDK `ZhipuAI(...).chat.completions.create(model,messages,response_format,thinking,extra_body,stream=True)` invoked via `uv run --with "zhipuai>=2.1.5" --with sniffio`
  feasibility: verified via existing-call-site — `hooks/adversarial_review_lib.py:1466-1533` (glm backend) + owner-validated `uv run --with` invocation on both hosts; `uv 0.10.12` on PATH
  failure: fail-loud
- api: `jsonschema` Draft validation of Observation/routing docs against the committed schemas
  feasibility: verified via capabilities-file — `ci.yml:47` installs `jsonschema`; available in the gate
  failure: fail-loud

No `fail-silent` external calls → no silent-failure `surface:` needed (every external call
raises/rejects on failure and is asserted in tests).

## 9b. Peer-consult synthesis (gpt-5.6-sol, blind) — BINDING amendments

An independent peer proposal (`docs/reviews/peer-rawgentic-peer-problem-424-2026-07-16.md`,
backend gpt) converged on the same core (pure/live split, caller-supplied judge+sink,
chain-aware skip, ceiling-2 with driver external, cut CCR/detached/async/HTTP). Adopted deltas,
which **override** the sections above where they conflict:

1. **`pool` key on every lane object** (schema §4.2 / routing-table.schema.json). Concurrency
   semaphores key on explicit `pool`, never on derived provider/credential identity — lanes that
   share a quota declare the same `pool`. (`claude` pool = capacity 2.)
2. **Process-wide `QuotaCoordinator`** (`quota.py`), not a per-`Engine`-instance semaphore — two
   Engine instances in one process must not jointly exceed the Claude limit. Acquire exactly once
   immediately before live exec; release in `finally` on **every** path (success, timeout,
   parse-fail, cancel). The external driver consumes the reserved 3rd slot and is NOT counted in
   the capacity-2 child pool (else candidate capacity collapses to 1).
3. **Conditional Observation schema** — all keys structurally present, but `actual_model` and
   `usage` may be **null only when `parse_status != "ok"`**; an `ok` Observation conditionally
   requires a nonempty provider-reported `actual_model` + provider-reported `usage` (JSON Schema
   `if/then`). This records a pre-envelope timeout honestly without ever fabricating evidence, and
   still makes absent identity on a *successful* call a failure. Supersedes §4.3's flat "absent =
   failure".
4. **`canonicalize_model_id()`** — a separately-tested pure function. AC3 compares
   requested==actual *through* canonicalization (aliases / dated revisions / provider prefixes,
   e.g. `claude-opus-4-8` vs `claude-opus-4-8[1m]`); the raw provider id is preserved verbatim in
   `actual_model`. Never rewrite the evidence to force a match.
5. **AC4 feasibility preflight** — `run_competitive` (and the AC4 test) preflight the candidate
   set against pool capacities: a set that cannot meet the 1.3× bound under the limits (e.g. 3
   Claude candidates under `claude:2`) **fails the preflight** rather than advertising impossible
   parallelism. AC4's real demo = candidates spread across the 3 pools (claude+codex+zhipu).
   Measurement boundary = candidate execution only (before judge/sink latency); prewarm uv deps so
   first-run resolution doesn't distort timing.
6. **Process-group lifecycle** — live subprocesses started in their own session
   (`start_new_session=True`); on timeout, terminate the **process group**, wait for cleanup,
   finalize capture metadata, release quota — no leaked permits or orphan processes.
7. **Codex/zhipuai captures are RELEASE GATES, not assumptions** — if `codex exec -o` carries no
   trustworthy model/usage, or zhipuai streaming omits terminal usage, the adapter **fails closed**
   and the invocation contract is amended; prompting a model to self-report identity is never
   evidence. (Sharpens §4.3's [I] items.)
8. **Sanitized fixtures + capture hygiene** — real captures sanitized (strip session ids / any
   credential-adjacent headers) before becoming committed fixtures; capture dirs written with
   restrictive perms; no secret in argv, config digest, audit line, or fixture. Canonical-JSON
   digest rule (sorted keys, no whitespace, secrets excluded) documented + a golden digest vector
   committed for Python↔Rust (kukakuka) parity.

**Divergence (recorded):** the peer proposed a **repo-root `pyproject.toml`** so `pytest tests/`
imports `phase_executor` with no shim. Rejected for E1: this repo has **zero root packaging** and
introducing repo-wide packaging (root pyproject + root conftest affecting all 2989 tests'
collection) is blast-radius the child doesn't warrant. Instead — **src layout**, package
self-contained under `phase_executor/` (its own `pyproject.toml`+`uv.lock`, extraction-ready),
imported by the gate via a **localized** `tests/phase_executor/conftest.py` one-line
`sys.path.insert(0, <repo>/phase_executor/src)` — matches the repo's established
`sys.path.insert` convention, zero blast radius on existing tests.

### Revised layout (supersedes §3 tree)
```
phase_executor/                       # project + extraction unit
  pyproject.toml  uv.lock  README.md
  src/phase_executor/
    __init__.py
    contract.py        # Observation + schema-validate + canonicalize_model_id()
    engine.py          # run_seat(), run_competitive() + AC4 feasibility preflight
    routing.py         # load + immutable snapshot + digest/epoch + eligibility (chain-aware skip)
    quota.py           # process-wide QuotaCoordinator keyed by pool
    capture.py         # capture-dir discipline (.incomplete + atomic write) + prompt/context hashing
    adapters/
      __init__.py
      base.py          # AdapterRequest, injected subprocess runner + process-group lifecycle, outcome vocab
      claude_cli.py    # parse_claude() [pure] + run() [live]
      codex_cli.py     # parse_codex() [pure] + run()
      zhipuai_sdk.py   # parse_zhipuai() [pure] + run() [live via uv run --with; PEP 723 worker]
    schemas/
      observation.schema.json
      routing-table.schema.json
    routing/rawgentic.routing-table.json   # shipped default (data, schema-validated at load)
tests/phase_executor/                 # collected by `pytest tests/`
  conftest.py          # sys.path.insert(0, <repo>/phase_executor/src)
  fixtures/            # sanitized real captures + kukakuka-observation.json (AC2) + golden digest vector
  test_contract.py test_parsers.py test_routing.py test_quota.py test_epochs.py test_engine.py
  live/test_live_seats.py   # AC3 + AC4, @pytest.mark.live (skipped in CI)
```
Consolidated vs the peer's ~15 modules (kept `quota`/`capture`/`routing` as focused modules; kept
parsers as pure module-level functions inside each adapter, and the subprocess transport injected
via `base.py`) — the pure/live boundary and per-piece testability are preserved without
gold-plating the file count.

## 9c. Step-4 gate resolution — applied amendments (BINDING, override above)

Design gate consumed one `design` loop-back (Critical/High fold). Owner resolved the two
ambiguous/owner-decision findings; all six adversarial findings + the self-review are applied:

1. **[High f1 → owner: build now] Inter-process quota lock.** `quota.py` enforces the pool
   ceiling via an **inter-process atomic file-lock/token** keyed by `pool` + account
   (`credential_ref`), not a process-local semaphore — so a 2nd session / hook / worker cannot
   jointly exceed `claude:2`. Lock dir under a config'd runtime root; each attempt acquires one
   token immediately before live exec, releases in `finally` on **every** path (success, timeout,
   parse-fail, cancel), stale-token reclaim on dead holder (pid liveness + mtime). **Test: launch
   two OS processes, assert combined active Claude permits never exceed 2.** The driver's reserved
   3rd slot is outside this pool.
2. **[High f2 → adopt] Canonical-first Claude identity.** `parse_claude`: canonicalize the
   requested id AND every `modelUsage` key; require **exactly one** key whose canonical id equals
   the requested canonical id → preserve that RAW key as `actual_model`; all other keys =
   auxiliary; **zero or >1 canonical matches = identity failure**. Supersedes §4.3's raw-key-exact
   rule. (Fixes: aliased/dated id + an auxiliary model no longer wrongly rejects a valid call.)
3. **[High f3 → adopt] Packaging smoke gate + production consumption.** Add a test that, in an
   **isolated env with no conftest**, `uv pip install ./phase_executor` (or `uv build` + install
   the wheel) then `import phase_executor` succeeds — proving the pyproject is really installable,
   not just conftest-importable. **Production consumers (E2–E8) import the INSTALLED package**, not
   a sys.path shim; the conftest shim is the *test-gate* convenience only. (This test is
   `@pytest.mark.live`/`slow`-guarded if it needs network for build isolation; a no-network
   `python -m build`/`uv build` + import-from-wheel variant runs in CI.)
4. **[Medium f4 → adopt] `run_competitive` execution contract (E1 owes E5).** Add a contract
   subsection pinning, independent of the deferred *winner policy*: typed `Judge`/`Sink`/
   `FailureStrategy` protocol shapes; the return schema `(winner, losers, judge_obs, record)`;
   and the state transition for **every** failure — partial candidate failure (losers carry the
   failed Observation; winner selection proceeds among successes per strategy), judge failure
   (after one retry → `failure_strategy` callback; `judge_degraded` flag), sink exception
   (logged, never erases candidate Observations, never leaks a permit), and **cancellation of
   still-running candidates** (terminate process groups, finalize captures, release permits).
   Covered by engine tests with deterministic stub adapters.
5. **[Medium f5 → adopt] codex/zhipuai telemetry spikes are PREREQUISITES.** Before writing the
   codex/zhipuai `parse_*` + `run()`: run a live spike, confirm exactly where the innermost model
   id + provider usage appear, commit the **sanitized** capture as the fixture, and record the
   exact successful invocation in §8. **If a gate fails** (codex `-o` lacks model/usage; zhipuai
   stream omits terminal usage): the adapter emits `parse_status != ok` (fails closed, never
   self-reported identity), and the fallback is decided before adapter work — codex fallback =
   parse the stderr header `model:` + `--json` event stream if `-o` is insufficient; zhipuai
   fallback = enable the SDK's usage option / read the terminal chunk, else record usage absent +
   `parse_status=usage_unavailable` (a named, tested degraded state, not a silent zero).
6. **[Medium f6 → owner: locked env] uv reproducibility.** The zhipuai worker runs from a
   **locked** env: `uv sync --locked --extra glm` (or `uv run --locked --extra glm`) against the
   committed `uv.lock`; the unbounded `uv run --with "zhipuai>=2.1.5" --with sniffio` (owner's
   validated form) is retained ONLY as a documented interim fallback when a locked sync is
   unavailable. `pyproject.toml` declares `zhipuai>=2.1.5` + `sniffio` under
   `[project.optional-dependencies] glm`; `uv.lock` pins them. Live smoke: sync once, then a
   worker launch (offline-after-sync) succeeds.

Self-review Lows (noted, non-blocking): `capture.py` reimplements atomic-write (justified —
extraction-ready, no `hooks/` dependency); AC4 measured over candidate-exec only, deps prewarmed.

**Re-gate:** feasibility declaration unchanged in shape (uv api now cites the locked-sync form,
same existing-call-site evidence) → mechanical gate stays ok=True; amendments introduce no NEW
Critical/High. Gate PASSES → Step 5.

## 9. Multi-PR assessment
The four ACs are cross-cutting (AC4 needs contract + all three adapters + engine + routing all
present and working together), so E1 is a **single cohesive PR**. Estimated > 500 lines but not
separable without shipping a non-functional half. If the build overruns materially, the natural
seam is (schemas+contract+adapters, PR 1 `Part of #424`) then (engine+routing+live ACs, PR 2
`Closes #424`) — decide at Step 5 only if size demands it; default is one PR.
