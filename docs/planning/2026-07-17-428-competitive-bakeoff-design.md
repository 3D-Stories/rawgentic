# #428 — Competitive rounds + build bake-off + glm-5.2 judge (epic #422, E5) — design

**Lane:** full spine (Large feature, `feat`). Depends on #424 (phase_executor engine — ✓ merged),
#429 (`needs_bakeoff` gate — ✓ merged). Live glm-5.2 confirmed working on this host
(`ZHIPUAI_API_KEY` in `~/.config/rawgentic/glm-judge.env`, `zhipuai 2.1.5` in `.venv-bench`).

## 1. What already exists (do NOT rebuild)

`phase_executor.run_competitive` (engine.py:196) already implements the **execution + failure
semantics**: runs candidates concurrently across quota pools, validates pools +
`forbidden_combinations` + parallel feasibility, isolates a raising candidate as a `harness_error`
Observation, and applies a **caller-supplied** `judge` / `failure_strategy` / `sink`, returning
`(winner, losers, judge_obs, record)`. The module docstring names this issue: *"the winner policy
is the caller's (E5)."*

So #428 is the **rawgentic caller-policy layer** — NOT new engine code. The engine is
extraction-clean (imports no `hooks/`); the policy is rawgentic-specific and lives in `hooks/`.

## 2. Confirmed contracts (evidence)

- **judge(results, rubric) -> dict** (engine.py:199,270-288): required `winner_index:int`; optional
  `scores`, `judge_obs`, `degraded`. `record` keys: `run_id, winner_index, n_candidates,
  judge_degraded, candidates[], scores, sink_error?`.
- **failure_strategy(results, exc) -> dict** (engine.py:201,271-275): same shape as judge; taking
  this path forces `degraded=True`. `None` ⇒ the judge exception **propagates** (= interactive
  stop-and-ask).
- **sink(record) -> None** (engine.py:202,289-293): return ignored; a raised exception is swallowed
  and sets `record["sink_error"]=True` — never aborts the round.
- **glm call reuse** (adversarial_review_lib.py): public gates `glm_sdk_available()` (624),
  `glm_api_key()` (629), `glm_base_url()` (642), `GLM_MODEL="glm-5.2"` (147). Raw text-completion is
  private: `_load_glm_client(timeout)` (1466) + `_glm_attempts(client, prompt, timeout, *, model,
  effort) -> (payload|None, last_error)` (1506). **We add ONE public wrapper** `glm_complete(...)`
  that single-sources those two — the existing review path is left unchanged (no churn).
- **bench-14 rubric** = v2-blueprint phase rubrics (`rawgentic-next
  docs/measurements/model-bench/fixtures/v2-blueprint/rubrics/{design,build,...}.md`). Design
  round uses `design.md`; build bake-off uses `build.md`. These live in ANOTHER repo → **vendored
  verbatim** into this repo so #428 is self-contained + version-controlled.

## 3. Design

### 3.1 New public wrapper in `hooks/adversarial_review_lib.py`
```
def glm_complete(prompt, *, model=GLM_MODEL, effort=REASONING_EFFORT, timeout=...) -> tuple[str|None, str]
```
Thin: gate on `glm_sdk_available()` + `glm_api_key()` (fail → `(None, reason)`, no raise), then
`_load_glm_client(timeout)` + `_glm_attempts(...)`. Returns `(payload_text_or_None, error_detail)`.
Single source for a raw glm text completion; a test asserts it wires the two privates (stubbed).

### 3.2 New module `hooks/bakeoff_policy.py` (rawgentic policy)
Imports `phase_executor` (read-only: `run_competitive`, `Candidate`, `Observation`) and
`adversarial_review_lib` (`glm_complete`, `GLM_MODEL`, credential gates).

- `RUBRIC_DIR = hooks/data/bakeoff_rubrics/`; `load_rubric(phase) -> str` — reads the vendored
  `{phase}.md`; **fail-closed** (missing/empty ⇒ `RubricUnavailable`, so we never judge blind).
- `anonymize_and_shuffle(results, *, seed) -> (drafts, order)` — extract each candidate's output
  text (`Observation.parsed_payload` / raw), strip model identity, label `Draft 1..N` in a
  **seeded** shuffle; return the anonymized draft blobs + `order[shuffled_pos] = original_index`.
  Seed is a parameter (deterministic tests); production passes a per-round nonce.
- `make_glm_judge(rubric_text, *, phase, seed, complete_fn=glm_complete, model=GLM_MODEL)` returns
  the `judge(results, rubric)` callable: anonymize+shuffle → build judging prompt (rubric + drafts +
  "respond JSON `{winner_draft:int, scores:{criterion:0-100}, confidence:0-1}`") → `complete_fn` →
  parse JSON → **map `winner_draft` back through `order` to the original candidate index** → return
  `{winner_index, scores, confidence, judge_obs:None}`. glm `None`/unparseable/out-of-range winner ⇒
  raise `JudgeError` (⇒ failure_strategy fires). Build bake-off variant appends the candidates'
  deterministic **test-evidence** to each draft (judged on evidence + anonymized patch, not vibes).
- `hybrid_failure_strategy(*, headless, incumbent_index)`:
  - headless ⇒ returns a callable `(results, exc) -> {winner_index: incumbent_index, degraded: True,
    scores: None}` (incumbent = the opus lane's candidate index) — excluded from telemetry via the
    `judge_degraded` flag on the record; surfaced in the morning report.
  - interactive ⇒ returns **`None`** (the judge exception propagates = stop and ask). One retry is
    handled inside the judge (`make_glm_judge(..., retries=1)`), matching §3.3 "after one retry".
- `bakeoff_sink(path) -> sink(record)` — append one JSON line to `bakeoff_results.jsonl`
  (`O_APPEND|O_CREAT`, single `os.write` of one line = atomic for line-sized payloads across
  concurrent bake-offs); path injectable (default `docs/measurements/bakeoff_results.jsonl`,
  parallel to `run_records.jsonl`; #420 feeds these into run records).
- `run_design_round(prompt, *, snapshot, quota, capture_root, headless, seed, sink_path)` — build
  the sol + opus `Candidate`s (lanes from the routing snapshot), call `run_competitive` with the
  design-rubric glm judge + failure strategy + sink. Winner's bytes are the phase artifact.
- `run_build_bakeoff(prompt, *, gate_decision, snapshot, quota, ..., default_seat_runner=run_seat)`
  — if `gate_decision.needs_bakeoff` (from #429) ⇒ {sonnet, opus, terra} bake-off on the build
  rubric; else ⇒ the single default build seat (no bake-off). The gate decision is the #429
  `GateDecision` object, not re-derived here.
- `reviewer_backend_for_winner(winner) -> str` — **D9**: winner engine `codex`(gpt) ⇒ `"claude"`
  (never gpt reviews gpt); else the configured default. Relational rule; inert when engine unknown.

### 3.3 Vendored rubrics
`hooks/data/bakeoff_rubrics/design.md` + `build.md` — copied verbatim from the v2-blueprint
fixture, with a one-line provenance header (`# Vendored from rawgentic-next … bench #14 (frozen v2
8dba1b62). Do not edit here; update at source + re-vendor.`). A test asserts the files are
non-empty and carry the hard-gate + Completeness/Quality section headers (drift guard on the vendor).

## 4. Failure modes (fail-closed where it matters)
- No rubric file ⇒ `RubricUnavailable` (never judge blind).
- glm unavailable / unparseable / winner out of range ⇒ `JudgeError` ⇒ hybrid strategy
  (headless: incumbent+degraded; interactive: propagate/ask).
- Unknown/unset candidate pool ⇒ engine already raises `ValueError` (ceiling bypass) — we pass
  pools straight from the routing snapshot, so this can't silently pass.
- Sink write error ⇒ engine swallows + flags `sink_error` (candidate Observations never lost).

## 5. Tests (TDD; CI = stubbed, live = @pytest.mark.live / RUN_LIVE=1)
`tests/hooks/test_bakeoff_policy.py` (stub `complete_fn` + stub `dispatch`, no network):
1. `load_rubric` returns text; missing phase ⇒ `RubricUnavailable`.
2. `anonymize_and_shuffle` deterministic per seed; `order` maps back correctly; model identity absent.
3. judge parses a stub verdict and maps the shuffled winner to the RIGHT original index; unparseable
   / out-of-range ⇒ `JudgeError`; retry consumes exactly one extra attempt.
4. `hybrid_failure_strategy`: headless ⇒ incumbent index + `degraded=True`; interactive ⇒ `None`.
5. `bakeoff_sink` appends valid JSONL; two bake-offs accumulate two lines.
6. `reviewer_backend_for_winner`: gpt winner ⇒ non-gpt; claude winner ⇒ default.
7. end-to-end `run_design_round` with a stub dispatch + stub judge ⇒ correct winner + one sink record;
   **wall-clock AC** proven with two stub authors that `sleep`, asserting total ≤1.3× the slower
   (parallel, not summed) — deterministic, no live calls.
`tests/hooks/test_glm_complete.py`: `glm_complete` wires the two privates (stubbed client);
credential-absent ⇒ `(None, reason)` no raise.
**Live demonstration** (`@pytest.mark.live`, run once manually with the sourced key): a real glm-5.2
judge over two canned anonymized drafts returns a `winner_index` + per-criterion `scores`. Recorded
in the PR body (CI never runs live).

## 6. Versioning / docs / diagram
`feat` ⇒ minor: 3.49.0 → **3.50.0** ×3 surfaces + `test_plugin_version_bumped`. README changelog
entry (diagram decision: **no workflow-spine change → no diagram REV** — this is executor policy, not
a WF2 station). Update `docs/model-routing.md` with a short "competitive rounds / bake-off" subsection
pointing at `bakeoff_policy`. Step-16 run-record.

## rev 1 → rev 2 (Step-4 adversarial findings — all verified against code, all adopted)

- **C1 (Critical) — secret-egress bypass.** `glm_complete` must NOT call the raw privates directly:
  that skips the A3 secret scan + `validate_glm_base_url` that every GLM caller gets via
  `_glm_prepare` (adversarial_review_lib.py:1567-1610). The build bake-off ships candidate PATCHES to
  Zhipu — bypassing the scan is a real leak. **Fix:** `glm_complete(prompt, …)` delegates to
  `_glm_prepare(artifact_text=(prompt, False), backend="glm", …)` (scans the outgoing prompt, validates
  the endpoint, constructs the client in try/except), then `_glm_attempts(client, prompt, timeout,
  model=, effort=)`. This also fixes **L3** (client-construction failure now returns `(None, reason)`).
- **H1 — wrong attribute.** The #429 verdict is `gate_decision.decision` (bool), NOT
  `.needs_bakeoff` (that's the function). `run_build_bakeoff` branches on `.decision`.
- **H2 — a failed candidate can win.** `_harness_observation` (engine.py:152) leaves a `HARNESS_ERROR`
  Observation with `parsed_payload=None` in `results`. **Fix:** `anonymize_and_shuffle` includes ONLY
  Observations with `parse_status == ok` and a non-None `parsed_payload`; it builds `order` over the
  survivors and returns their original indices. `<2` valid drafts ⇒ raise `JudgeError` (never judge a
  degenerate set). `hybrid_failure_strategy` validates `incumbent_index` points at an `ok`
  Observation; if not, it too fails closed (interactive-propagate).
- **H3 — identity leak via raw capture.** Extract ONLY `parsed_payload` text; NEVER read
  `raw_capture_path` (the provider envelope carries the model id — codex_cli.py:67, zhipuai_sdk.py:40).
  No `parsed_payload` ⇒ excluded (H2), not a raw fallback. Build-variant test-evidence is
  engine-name-scrubbed before it enters a draft. (Out of scope, named: an LLM judge can still infer
  authorship from writing style — the shuffle removes positional bias only.)
- **M1 — off-by-one.** Drafts are labelled 1-based ("Draft 1..N"); `order` is 0-indexed by position;
  `winner_index = order[winner_draft - 1]`, pinned explicitly. Test uses a NON-identity permutation and
  a NON-first winner (an identity shuffle or Draft-1 winner would pass a buggy impl).
- **M2 — interactive discards work + no telemetry.** `JudgeError` carries the `results`. Interactive
  path passes a `failure_strategy` that **persists a degraded record via the sink, then re-raises**
  (engine.py:274 is outside the try, so a raising strategy propagates = stop) — so there is always a
  trace AND the completed drafts are recoverable, without re-spending quota.
- **M3 — AC test must exercise the real ceiling.** The wall-clock test instantiates the real
  `QuotaCoordinator` with production pool limits and assigns the sleeping stubs to the SAME pools as the
  real candidates — the build set puts sonnet+opus BOTH on `claude` (limit 2), so the test covers the
  saturating boundary, not a trivial two-unconstrained-pool fan-out.
- **M4 — D9 vocabulary.** Adversarial-review backends are `{gpt, glm, both}` (adversarial_review_lib
  BACKENDS:151) — never `"claude"`. `reviewer_backend_for_winner` returns a valid backend: a
  gpt(codex)-engine winner ⇒ `"glm"` (never gpt-reviews-gpt); any non-gpt winner ⇒ the configured
  default. (There is no "claude" adversarial backend, so a claude winner reviewed by gpt/glm is already
  cross-engine — M4's symmetric case dissolves.)
- **M5 — sink append.** Records embed full candidate payloads (tens of KB) — O_APPEND size-atomicity is
  invalid. Reuse the established single-writer append (plain `open(a)`, like
  `work_summary.persist_record`); the driver runs one bake-off at a time.
  `# ponytail: single-writer append; add flock only if concurrent bake-offs ever become real`.
- **M6 — design-round lane source.** No `design` seat exists (routing-table.json:6). Named source:
  the sol competitor lifts the `review`-chain `gpt-5.6-sol` lane (provider openai / codex pool); the
  opus competitor lifts the `build`/`intake` opus lane (anthropic / claude pool). Both validated
  against `snapshot.pool_concurrency()` before dispatch.
- **M7 — gate anti-tamper.** `run_build_bakeoff` recomputes `policy_digest` from the passed
  `GateDecision.input_snapshot` and compares to `.policy_digest`; mismatch ⇒ raise (fail-closed) — the
  #429 guard is honoured, not inert.
- **L1 — confidence persisted.** The judge folds `confidence` into the `scores` dict
  (`scores["_confidence"]`) so `run_competitive`'s record retains it.
- **L2 — vendor guard is a STRUCTURE guard** (cross-repo freshness is undetectable from here). The
  provenance header records the source commit `8dba1b62` + a sha256 of the vendored bytes; the test
  asserts the header + section structure and the recorded hash matches the file.
- **L4 (no change) —** the judge closes over `rubric_text`; the `judge(results, rubric)` second arg is
  intentionally unused (run_competitive passes `rubric=None`). Harmless.

## Platform / external dependencies

platform_apis:
- api: zhipuai SDK glm-5.2 chat.completions.create (JSON verdict), reused behind a new public glm_complete() wrapper
  feasibility: verified via existing-call-site — hooks/adversarial_review_lib.py:1506-1534 (_glm_attempts already calls client.chat.completions.create(...) live for adversarial review); a direct glm-5.2 ping this session (2026-07-17) returned a valid response with the key sourced from ~/.config/rawgentic/glm-judge.env and zhipuai 2.1.5 in .venv-bench. No NEW external API surface — same SDK call, single-sourced.
  failure: fail-loud
  surface: glm_complete() returns (None, reason) on credential/SDK absence; make_glm_judge raises JudgeError on None/unparseable/out-of-range, which run_competitive routes to the hybrid failure_strategy (headless: degrade to incumbent + judge_degraded flag, excluded from telemetry; interactive: propagate = stop and ask). The live judge is proven only under the @pytest.mark.live / RUN_LIVE=1 test, never on merge
