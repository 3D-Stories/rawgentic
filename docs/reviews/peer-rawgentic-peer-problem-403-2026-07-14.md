# Peer Consult — .rawgentic-peer-problem-403.md

- Date: 2026-07-14
- Reviewer: Codex (peer designer)

## Approach

Keep the existing Codex execution functions and result contract intact, then add GLM as a parallel adapter behind a thin backend orchestrator. Introduce a normalized Backend enum (`gpt`, `glm`, `both`) and coerce config/CLI values at the boundary. Implement `run_glm_review` and `run_glm_consult` using injected-client-first APIs; when no client is injected, lazily import `zhipuai`, resolve subscription credentials/base URL, and construct the SDK client. Share only provider-neutral preparation and completion handling: artifact reading, secret scanning, nonce-fenced prompt creation, retry classification, JSON extraction, validation/normalization, and report rendering inputs. Preserve Codex subprocess code and its tests without refactoring its internals unless a helper can be extracted with behavior-equivalence tests. Model a command as independent backend jobs; `both` expands into ordered jobs `[gpt, glm]`, executes each despite sibling failure, writes a report only for each successful validated result, and returns an aggregate outcome containing every backend status and path. Default `gpt` should continue through the existing single-backend path so filenames, output, exit codes, and side effects remain unchanged.

## Key decisions

- Use a small provider-neutral result envelope containing backend, provider, model, status, validated payload, error category, attempts, and optional usage metadata. Retain the existing Codex result/status types at their current public boundary and adapt them into this envelope only inside orchestration.
- Represent backend selection as a closed enum. Config absence yields `gpt`; invalid values or non-string shapes emit one stderr warning and yield `gpt`. An explicit CLI `--backend` is parsed strictly and overrides the coerced config value.
- Give GLM functions an optional `client` parameter (or client factory) so CI exercises the exact streaming and parsing path without importing the SDK or touching the network. Production client creation is deferred until a GLM job actually runs.
- Resolve GLM auth in precedence order `ZHIPUAI_API_KEY`, `ZHIPU_API_KEY`, `GLM_API_KEY`; resolve base URL from `ZHIPUAI_BASE_URL`, then `GLM_JUDGE_BASE_URL`, then `https://api.z.ai/api/coding/paas/v4`. Pass both explicitly to `ZhipuAI`. Default model is `glm-5.2`, with a documented GLM-specific model override.
- Keep streaming inside a dedicated `_collect_glm_stream` helper that concatenates textual deltas, tolerates usage-only/final chunks, and rejects no-content responses. Invoke the verified SDK shape with JSON-object response format, thinking enabled, reasoning effort in `extra_body`, and `stream=True`. Never fall back to non-streaming.
- Parse GLM output as exactly one JSON object, then reuse the existing semantic validators: review uses `validate_findings` and `normalize_findings`; consult uses `_parse_codex_proposal` only if that parser is genuinely provider-neutral, otherwise rename it to `_parse_proposal` while preserving a compatibility alias for existing tests/importers.
- Treat missing SDK, missing key, timeout, transport exhaustion, malformed stream, empty content, JSON failure, and schema/semantic validation failure as explicit non-success statuses. No report or sidecar is written from an invalid payload.
- Run secret scanning before either provider call. The existing hard-block environment flag applies identically. Egress notices are generated from backend metadata: OpenAI for GPT and `z.ai / Zhipu` for GLM, including the jurisdiction distinction. In `both`, show both notices before execution.
- Use backend-aware path builders with an optional suffix. GPT continues to use existing names; GLM inserts `-glm` immediately before the date. Centralize collision rules so review and consult cannot diverge.
- Define `--findings-json` as a base path in `both` mode: GPT writes the exact requested path for backward compatibility and GLM writes a sibling with `-glm` before the extension. In GLM-only mode, write the exact requested path. Sidecars are written only for successful validated review results; consult does not acquire findings sidecars.
- Aggregate `both` outcomes without short-circuiting. Present a deterministic per-backend summary and all successful report paths. Use success when at least one backend succeeds, partial-success when one succeeds and one fails, and failure when none succeeds; map partial success to a distinct existing-compatible nonzero exit code only if callers can handle it, otherwise return success while printing a prominent partial-failure warning. Document the chosen contract and test it.
- Make prerequisites compositional: `prereq_status('gpt')` runs the unchanged Codex installed/authenticated checks; `prereq_status('glm')` checks lazy importability plus presence of any accepted key; `both` returns both named results rather than collapsing diagnostic detail. The CLI exits nonzero if any selected prerequisite fails and preserves headless guidance per backend.
- Renderers receive reviewer identity as data rather than inferring `Codex`. Supply display values such as `Codex (model …)` and `GLM (model glm-5.2 …)`. Keep all other GPT report text unchanged by default.
- Test in layers: config coercion table including bad shapes; GLM fake streaming chunks, empty/malformed streams, transport retry, timeout, validation failure, auth/base-URL precedence, request arguments, and reviewer label; prerequisite matrices; egress wording; path/sidecar naming; CLI override precedence; and both-mode success/failure permutations. Finish with the full suite and state the delta from the recorded baseline.
- Update the two skills symmetrically: read the workflow-specific config default, allow an explicit skill argument to override it, invoke backend-aware prereq and egress steps, pass `--backend`, and present one or two reports. Record the workflow-diagram decision as no topology change unless backend branching is represented in that diagram’s abstraction level.

## Risks

- A broad refactor of the mature Codex path could break byte-for-byte compatibility or PATH-stubbed subprocess tests. Mitigation: leave Codex execution intact and introduce adapters around it; extract helpers incrementally with characterization tests.
- SDK stream chunk shapes may vary across `zhipuai` versions, especially missing choices, object-versus-dict fields, and usage-only terminal chunks. The collector should accept the documented variants but fail closed on ambiguity, with fake fixtures for each supported shape.
- Retrying after a partially received stream may duplicate provider work or cost. Discard the partial attempt completely, retry only classified transient failures, cap attempts with the shared retry policy, and never combine chunks across attempts.
- Shared timeout settings may not map cleanly between subprocess execution and SDK transport/read timeouts. Define whether the GLM timeout is per attempt or whole operation, enforce it explicitly, and document the semantics.
- Returning success for a partial `both` run can hide degraded review coverage, while returning failure can make successful reports inconvenient to consume. The aggregate exit-code contract must be explicit, stable, and reflected in both skills.
- Environment-variable aliases and custom base URLs can accidentally route a subscription token to the wrong endpoint. Log the selected endpoint/provider without exposing credentials and test precedence.
- JSON-object response format is weaker than Codex’s strict output schema. Semantic validation remains the authority; tests must prove extra, missing, empty, and wrong-typed fields cannot produce a report.
- Config fallback to GPT is intentionally fail-safe but could unexpectedly cause OpenAI egress after a typo intended to select GLM. The stderr warning and pre-call egress notice must be conspicuous; consider including the invalid value’s type but never secrets.
- Two reports created at different times could observe mutable artifacts differently if preparation is repeated. Read and scan the artifact once, build immutable shared input, then give identical content to both backend jobs.
- The optional dependency may be present but incompatible at runtime. Prerequisites can check importability, while execution must still catch constructor/call-shape failures and report a closed failure with actionable version guidance.

## Sketch

Backend selection and orchestration:

`coerce_backend(raw) -> Backend`
`resolve_backend(cli_override, config) -> Backend`
`expand_backend(gpt|glm|both) -> [GPT] | [GLM] | [GPT, GLM]`

`prepare_review(artifact, options)`
`  -> read once -> secret scan/hard-block -> nonce-fenced prompt -> PreparedRequest`

`execute_review(prepared, backend)`
`  GPT -> existing run_codex_review(...) -> adapt_result(...)`
`  GLM -> run_glm_review(..., client=None) -> collect stream -> parse -> validate -> normalize`

`execute_consult` mirrors review and uses proposal validation.

`run_selected(kind, selection, prepared)` loops over expanded jobs, catches failures per job, and returns `AggregateResult(results=[...])`. `persist_successes` writes each validated payload using `review_report_path(..., backend)` or `consult_report_path(..., backend)` plus the defined review sidecar rule. `present_aggregate` prints every backend status and successful path.

CLI shape:
`prereq --backend {gpt,glm,both}`
`review ... --backend {gpt,glm,both} [--findings-json PATH]`
`consult ... --backend {gpt,glm,both}`

GLM production call:
`client.chat.completions.create(model=glm_model, messages=[system,user], response_format={'type':'json_object'}, thinking={'type':'enabled'}, extra_body={'reasoning_effort': effort}, stream=True, ...)`

Naming:
GPT review: `<slug>-<date>.md`; GLM review: `<slug>-glm-<date>.md`
GPT consult: `peer-<slug>-<date>.md`; GLM consult: `peer-<slug>-glm-<date>.md`
Both findings sidecars: requested GPT path plus a `-glm` sibling before the extension.

---
_Peer proposal (report-only). Synthesize at your discretion._
