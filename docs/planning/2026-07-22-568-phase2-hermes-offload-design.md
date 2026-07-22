# #568 Phase-2 — Hermes offload seat + numbered-option asks + unattended fallback policy

Design r3 · 2026-07-22 · session 3544db7b · issue #568 (Phase-2 slice) · plugin 3.87.0 → 3.88.0, phase_executor 0.8.0 → 0.9.0
r3 delta (folds 24 Step-4 findings, gpt-5.6-sol self-review + adversarial, all verified vs code):
SCOPE NARROWED — the offload seat ships CONTRACT-CORRECT + CI-tested but ACTIVATION-GATED (preflight
REFUSES an unsandboxed gateway backend, F7); operational WF2-phase wiring + live dispatch defer to
Phase-3 (F6/F17). Contract fixes: manifest.confinement REQUIRED (F1); identity attestation
actual_model="hermes-agent" from /health platform field, no contract change (F2 dissolved-as-overclaim);
use existing USAGE_UNAVAILABLE parse_status not an invented field (F3); map hermes failures to existing
parse_status/AVAILABILITY_FAILURES (F4); effort text de-contradicted (adv-F3). Policy inputs completed
(F5). Runbook firewall order + negative probe + fail-loud credential (F8/F9). §15 D-P2-6.
r2 delta (retained): effort-engine correction; §8 live-spike platform_apis; §10 executed/remaining.
Inputs: exa + context7 research (hermes-agent official docs), gpt-5.6-sol peer proposal
(`docs/reviews/peer-2026-07-22-568-phase2-problem-statement-2026-07-22.md`), problem statement
(`docs/planning/2026-07-22-568-phase2-problem-statement.md`), Phase-1 shipped bridge
(`hooks/hermes_bridge.py`, v3.87.0).

## §0 Closure + conditionality (owner decisions, 2026-07-22)

- **D-P2-1:** ONE WF2 run, ONE PR carrying all three strands. Version bump 3.87.0→3.88.0 (×4
  surfaces) + phase_executor 0.8.0→0.9.0 (pyproject + `__init__`).
- **D-P2-2:** live verification of the offload seat is **DEFERRED** (darwin API-server enablement
  is owner-attended; owner away). The PR ships CI-verified with injected transports + the
  enablement runbook; live cells run owner-attended later. **PR says "Part of #568", never
  "Closes"** — #568 stays OPEN (Phase-3+ enumerated in §14).
- **D-P2-4 (owner, 2026-07-22, supersedes D-P2-2's deferral):** "you have permission to merge and
  do what you need to to smoketest. You can get into darwin through charlie" — merge grant live
  for this resumed run; the darwin enablement (§10) is executed BY THE RUN via the charlie→darwin
  hop and the live smoke runs in-run. D-P2-2's deferral remains the FALLBACK if the hop or
  enablement fails (honest partial, runbook still ships). PR stays "Part of #568".
- Diagram decision: **no WF2-spine change → no diagram REV.** The offload seat is a routing-table
  + engine addition dispatched via the existing `dispatch` CLI; no WF2 step semantics change.

## §1 Problem and goals

Phase-1 (shipped) gave unattended runs a one-way-plus-reply owner channel: ask by text, receive
the tokened reply into a resumable workflow. Three gaps remain (owner-selected scope, all three):

1. **(i) Offload seat** — no cheap always-on lane for small read-only research/lookup subtasks;
   everything burns main-session (or Claude/codex seat) context and quota. The Hermes gateway on
   darwin (10.0.17.204, systemd `hermes-gateway.service`, gpt-5.5 via openai-codex subscription —
   a quota pool the harness does not otherwise touch) is idle capacity.
2. **(ii) Ask ergonomics** — a free-text tokened reply is error-prone on a phone. An ask that
   carries numbered options ("1 = merge, 2 = hold") lets the owner reply `1`.
3. **(iii) Unattended fallback policy** — when may a text substitute for the terminal, and what
   happens when the gateway / BlueBubbles / the bridge is down mid-run? Today: implicit.

**Non-goals:** a second Hermes instance (owner hard constraint); gateway-internal modifications;
runtime SSH to darwin (denied + wal-guard-blocked); mutating offload work; session affinity /
streaming SSE consumption / adaptive concurrency (Phase-3, §14); replacing the sentinel
notify.sh outbound path.

## §2 Verified facts and probes

Confirmed (with evidence):
- Hermes ships an **OpenAI-compatible HTTP API server platform** inside the same gateway process:
  `gateway/platforms/api_server.py`; endpoints `POST /v1/runs` (202 + run_id), `GET /v1/runs/{id}`,
  `GET /v1/runs/{id}/events` (SSE), `POST /v1/runs/{id}/stop`, `POST /v1/chat/completions`,
  `GET /v1/capabilities`, `GET /v1/models`, `GET /health`. Auth `API_SERVER_KEY`; enablement
  `hermes config set API_SERVER_ENABLED true` + `API_SERVER_KEY <name>`; `API_SERVER_HOST/PORT`
  env. Source: official docs site (hermes-agent.nousresearch.com, programmatic-integration +
  api-server pages) + context7 `/nousresearch/hermes-agent` (config.py source quoted).
- darwin reachable from .205 (ping 0.3 ms); **SSH denied** for root/rocky00717/hermes/admin
  (live probe 2026-07-22). Runtime path must be HTTP.
- Gateway relays inbound BlueBubbles to gateway-internal skills only
  (`docs/planning/2026-07-22-568-hermes-inbound-investigation.md`) — harness-sent BB texts are
  `isFromMe=true` (owner's Apple ID), so BlueBubbles-as-RPC is rejected.
- Seat model plug-in surfaces verified in-tree: `ADAPTERS` (`adapters/__init__.py:9`),
  `PROVIDER_ENGINE` (`engine.py:29`), routing table
  `phase_executor/src/phase_executor/routing/rawgentic.routing-table.json`, `WIRED_SEATS`
  (`hooks/executor_routing_lib.py:58`). Read-only seats ride the sync path — no worktree /
  canary / `MUTATING_FS_SANDBOXED` involvement.

**RESOLVED (probed 2026-07-22 via charlie→darwin hop, D-P2-4):** darwin runs **Hermes Agent
v0.18.2 (2026.7.7.2)**, gateway active, and `api_server.py` IS present at
`/usr/local/lib/hermes-agent/gateway/platforms/api_server.py` (ls-confirmed). Remaining
deferred-to-target: the live `/v1/runs` request/response field shapes — probed at enablement
(§10 step 4) before the adapter's live smoke; the adapter defends regardless (§4 preflight +
versioned-fixture parsing). Hop: `ssh charlie` (rocky00717@10.0.17.200) → `ssh root@10.0.17.204`.

## §3 Architecture overview

```
.205 orchestrator                              darwin (10.0.17.204)
  dispatch --seat offload ──HTTP──▶  hermes-gateway.service (EXISTING, unmodified)
    hermes engine adapter                └─ api_server platform (one-time enablement)
      POST /v1/runs (fresh session)      └─ AIAgent core (gpt-5.5, openai-codex quota)
      poll GET /v1/runs/{id}
      best-effort /stop on deadline
      → Observation (usage completeness marked)
      → output wrapped UNTRUSTED_DATA

  hooks/hermes_bridge.py (Phase-1, extended)   BlueBubbles server 10.0.17.148:1234
    ask_owner(..., options=[...]) ──text──▶ owner phone
    poll: strict option/token parse ◀──reply──
  hooks/hermes_policy.py (NEW, pure)
    unattended decision table (iii)
```

## §4 Strand (i) — offload seat detailed design

**Engine `hermes`, provider `nousresearch`, seat `offload`, pool `hermes` (cap 1).**

- **Adapter** `phase_executor/src/phase_executor/adapters/hermes_http.py`: pure `parse_*`
  functions (fixture-tested, no I/O) + `run(AdapterRequest)` with an **injectable HTTP
  transport** (CI never needs a live gateway; mirrors the Phase-1 bridge's injectable
  transport pattern). Prompt in the request body, never argv. Flow:
  1. Resolve credential by NAME (`credential_ref` → env `HERMES_API_SERVER_KEY`); never in
     argv/logs/Observations; auth header redacted from all diagnostics.
  2. Bounded preflight: `GET /health`, then `GET /v1/capabilities` (cached briefly). Missing
     run capability → typed availability/config error, never a malformed-model error.
  3. `POST /v1/runs` with a **fresh session identity per dispatch** (injection containment;
     no session reuse in Phase-2) and a self-contained, bounded, read-only prompt.
  4. Persist `run_id` into capture/audit metadata AS SOON as it exists (traceability).
  5. Poll `GET /v1/runs/{id}` with bounded exponential backoff + jitter until a recognized
     terminal status or deadline (manifest `timeout_s`, default 300). Enforce a max response-body
     read (`HERMES_MAX_RESP_BYTES`, default 256 KiB) and a max output length — a breach returns a
     typed failure, never unbounded memory/capture growth (F14).
  6. On deadline/cancel: ONE best-effort `POST /v1/runs/{id}/stop` with its own short timeout;
     record whether stop was acknowledged; return without waiting further.
  7. Normalize to a `ParsedResult` and let the SHARED `resolve_parse_status` (adapters/base.py)
     classify (F3 fix — no invented field): `actual_model` is attested (see identity below), so a
     completed run with usage → `OK`; a completed run whose `usage` is absent/partial → the EXISTING
     `USAGE_UNAVAILABLE` status with `usage: null` (verify_post VERIFIES a usage_unavailable call
     whose identity matches — enforce.py:256-258). Never a fabricated zero, never a new top-level
     Observation key (the v2 object is closed, `additionalProperties:false`).
- **Identity attestation (F2 fix — no contract change).** The seat routes under model id
  `hermes-agent`; the adapter sets `actual_model = "hermes-agent"` from the gateway's own
  attestation (`GET /health` → `{"platform":"hermes-agent",...}`, corroborated by the run
  object's `"object":"hermes.run"`). `requested==actual` → `verify_post` VERIFIES (enforce.py:272).
  The innermost gpt-5.5 is Hermes's internal model choice, NOT the seat's routed identity — the
  seat contracts on the platform, exactly as a codex seat contracts on `codex`, not on the
  server-side model. (The self-review claimed this needs a versioned contract change; verified
  false — `hermes-agent` is a legitimate attested identity.)
- **Failure taxonomy → EXISTING statuses/exits (F4 fix).** No custom `parse_status` values. Map:
  transport/connect failure + gateway-down + `/health` fail → `NO_RESPONSE`/`LAUNCH_ERROR`
  (∈ `AVAILABILITY_FAILURES` → honest availability, dispatch exit 3); a terminal `failed`
  run (model-layer error) → `HARNESS_ERROR` with the gateway `error` string surfaced; missing
  run-capability at preflight → availability (config) failure, exit 3; `submission_unknown`
  (POST timeout, no run_id) → `NO_RESPONSE` with a `submission_unknown` note in capture metadata,
  NEVER auto-retried (no idempotency key). The custom nuance rides capture metadata, not the
  enum — so `dispatch_seat`/`run_seat` behavior (fallback vs breach) stays contract-correct.
- **ACTIVATION GATE (F7 fix — the load-bearing safety change).** Before any `POST /v1/runs`, the
  adapter probes the gateway's backend disposition (`GET /v1/capabilities` / `/health/detailed`);
  if the gateway reports an **unsandboxed `local` terminal backend**, the seat **REFUSES to
  dispatch** (typed configuration availability failure, exit 3, logged) — because rawgentic's
  `tool_grants:["read"]` is caller-side capability-selection, NOT a gateway sandbox
  (contract.py:311-312), so an unsandboxed gateway could execute injected instructions as the
  darwin host user. The seat becomes live ONLY when the gateway runs a sandboxed backend
  (`terminal.backend: docker`) — an owner Phase-3 step. So this PR ships the seat CI-correct but
  INERT on the current darwin config; that is the honest, safe posture (it is NOT "read-only
  forever" — it is "refuses to run until the gateway can enforce read-only").
- **Submission-ambiguity rule:** (see taxonomy) `submission_unknown` never auto-retries.
- **Registration touchpoints:** `ADAPTERS["hermes"]`; `PROVIDER_ENGINE["nousresearch"] = "hermes"`;
  routing table: pool `hermes: 1`, seat `offload` `{manifest{session_policy: fresh,
  tool_grants: ["read"], effort: "medium", confinement: {"nousresearch": "gateway-api-key +
  activation-gate (unsandboxed-refused)"}, bounds{timeout_s: 300, max_budget_usd: 1.0}},
  primary{model: "hermes-agent", lane{provider: "nousresearch", transport: "http",
  auth_mode: "api_key", credential_ref: "HERMES_API_SERVER_KEY", pool: "hermes"}},
  chain: []}`; `WIRED_SEATS += {"offload"}`. **`manifest.confinement` is REQUIRED by
  `routing-table.schema.json` and `routing.py:63-70` rejects any lane provider it doesn't cover
  (F1)** — the entry above satisfies it (honest string: the confinement is the API-key + the
  activation gate, NOT an OS sandbox). NOT in `_MUTATING_ENGINES`, NOT in `MUTATING_FS_SANDBOXED`,
  no `compose_supervised_argv` entry, no canary policy — this seat is non-mutating on the .205
  side; a future mutating grant re-opens ALL of those gates by design.
- **Effort handling:** the routing schema requires `manifest.effort` and both
  `contract.resolve_effort` (engine.py:143 sync path) and `contract.profile_from_manifest`
  (engine.py:82) fail-closed on engines outside `_EFFORT_ENGINES`. So: add `"hermes"` to
  `_EFFORT_ENGINES` (contract.py:371); add `SUPPORTED_EFFORT["hermes-agent"] = ("medium",)`
  (model_capabilities.py) — this satisfies the `test_registry_covers_shipped_table` superset
  guard. **CAPABILITY_REVISION is NOT bumped** (correction found in Step-8: a new-engine row is
  additive — no existing model's capability semantics change — and `test_engine.py:308-330`
  ASSERT the produced `capability_revision == 1` for unchanged models; the "bump on any edit"
  docstring convention targets capability CHANGES, which this is not). No `ENGINE_NONE_EFFORT`
  entry. Manifest pins `effort: "medium"`. `run_seat` resolves the
  CALLER effort arg, not `manifest.effort` (engine.py:140-150), so with a bare `dispatch` (no
  `--effort`) hermes resolves `None` → identity (passes through); with `--effort medium` it
  resolves medium. EITHER way the adapter ACCEPTS the resolved effort and does NOT forward it to
  the gateway (no effort control) — pinned by a test asserting both the no-`--effort` and
  explicit-`medium` paths dispatch identically. (No `effort-omission tolerance` — hermes IS in
  `_EFFORT_ENGINES`; the earlier draft's contradictory wording is removed, adv-F3.)
- **Live run-object contract (read from the shipped build, darwin v0.18.2 —
  `/usr/local/lib/hermes-agent/gateway/platforms/api_server.py`, live read 2026-07-22):**
  run object = `{"object": "hermes.run", "run_id": "run_<uuid4hex>", "status": <str>,
  "created_at": <epoch s>, "updated_at": <epoch s>, ...}` (`_set_run_status` :4107-4121;
  note the key is `run_id`, NOT `id`); `POST /v1/runs` requires `input` (string or message
  array; optional `instructions`, `conversation_history`, `previous_response_id`), returns
  202 (`_handle_runs` :4168+); `GET /v1/runs/{run_id}` returns the status object verbatim,
  404 `{"error":{"code":"run_not_found",...}}` (`_handle_get_run` :4479-4493); `POST
  /v1/runs/{run_id}/stop` sets status `stopping` (`_handle_stop_run` :4632+). 202 body =
  `{"run_id": ..., "status": "started"}` (:4472-4477). Status vocabulary (writers read at
  :4380-4430): `started`, `running`, `stopping`, terminal `completed` (carries `output` +
  `usage`), `failed` (carries `error`), `cancelled` — the adapter treats the vocabulary as
  OPEN: the recognized-terminal set {completed, failed, cancelled} drives completion,
  anything unrecognized past deadline fails closed. `usage` IS reported on completed runs
  (:4400-4407) — consumed when present, completeness `unknown` otherwise. Errors are
  OpenAI-shape (`invalid_api_key` verified live via HTTP 401). A shared concurrency limit
  (`max_concurrent_runs`) can reject submissions — typed transient availability.
- **Fallback semantics (Step-8 correction).** offload's `chain` is `[claude-sonnet-5 / anthropic]`
  — the analysis lane. This is REQUIRED by the repo invariant `test_..._every_seat_has_fallback_
  chain_426` (every seat has ≥1 fallback) AND is what the peer recommended; the earlier r3 draft's
  `chain: []` violated the invariant. Any AVAILABILITY failure — gateway down, `/health` fail, OR
  the **activation-gate refusal on an unsandboxed backend** — falls back chain-aware to
  claude-sonnet-5. This is NOT silent: `run_seat` records `fallback_reason` + the actual
  `actual_model` on the Observation, and the audit names the lane used. Consequence on the current
  darwin config (unsandboxed): every offload dispatch degrades to the analysis lane until the owner
  sandboxes the gateway (§10-7) — a safe, audited, useful degradation (the same read-only subtask
  runs on the cheap analysis lane), strictly better than a bare seat-skip. Routine offload/fallback
  NEVER texts the owner. The confinement map covers BOTH `nousresearch` and `anthropic` (F1 — every
  chain lane provider must be confined).
- **Cross-model invariant interaction:** `cross_model_author` forbidden-combination logic keys on
  engine/provider; `offload` is not an enforced role (`policy.enforced_roles` stays
  `["review","build"]`) — no floor, no role. Enforcement untouched.
- **Output trust boundary (Step-11 Codex7 correction).** The adapter returns Hermes output as the
  Observation `parsed_payload`, exactly like every other seat's model output — there is no
  special "envelope" in the adapter (the earlier draft overclaimed one). Because the seat has NO
  operational caller in Phase-2 (F6 — offload dispatch isn't wired into any WF2 phase yet), there
  is nothing consuming that output to inject into. The untrusted-data treatment is an ORCHESTRATOR
  prose rule that lands at the Phase-3 wiring point: offload results never alter routing,
  approvals, or gates without independent verification; load-bearing claims get cited or
  re-verified. A structured untrusted-data wrapper + an injection-resistance test belong with that
  wiring (Phase-3), not with an adapter whose output no one reads yet.

## §5 Strand (ii) — numbered-option asks

Extend `hooks/hermes_bridge.py` (backward-compatible; Phase-1 invariants untouched):

- **Ask record additions** (persisted BEFORE send, so parsing never depends on mutable caller
  state): `options: [{id: 1, label: str}, ...]` (ordered, stable integer ids, 1-based),
  `response_mode ∈ {free_text, option_required, option_or_text}` (default `free_text` — an
  optionless ask is bit-for-bit today's behavior). Label-collision detection at ask creation
  (normalized-duplicate labels rejected, fail-closed).
- **Outbound rendering:** question + numbered option lines + the existing token line.
- **Reply interpretation** (strict, after the existing exact-token gate & owner/dedup filters):
  - trimmed digits-only payload → that option id (unknown id → ambiguous);
  - exact normalized label match → that option ONLY if unique;
  - `N: label` / `N - label` → selected only when both resolve to the SAME option;
  - conflicts / duplicate matches → `ambiguous` → deliver NOTHING (Phase-1 never-wrong-act);
  - any other text → `free_text` (valid only when response_mode permits; `option_required` +
    free text → disposition `unmatched_option`, delivered nothing, at most one deduplicated
    clarification text sent when transport is healthy).
- **Inbox doc additions:** `reply: {raw, interpretation ∈ {selected, free_text}, option_id?}`.
  Raw text ALWAYS preserved. Only `selected` may satisfy an `option_required` gate.
- `render_resume_prompt` gains the selected-option line; DATA envelope unchanged.

## §6 Strand (iii) — unattended fallback policy

**New `hooks/hermes_policy.py`** — pure, table-driven, no I/O (decision guide §3 of repo
manual: policy that can't evaluate → fail-closed). Complete typed input contract (F5/adv-F1 —
the earlier draft omitted three predicates the table branches on):
`{ask_criticality ∈ {critical, advisory}, attendance_mode ∈ {attended, unattended},
delivery_state ∈ {unsent, send_failed, delivery_unknown, delivered}, transport_state ∈ {healthy,
bb_read_down, gateway_down}, response_mode ∈ {free_text, option_required, option_or_text},
dispatch_requirement ∈ {optional, hermes_required}, remote_reply_allowed: bool, deadline_state ∈
{within, expired}, has_fallback: bool}` → `{action ∈ {resume, pause_resumable, seat_skip,
fallback_dispatch, error_protocol, wait, clarify_once, continue_safe_branch}, notify ∈ {none,
run_status, blocker}, disposition: str}`. Output values are ENUMERATED (F12) — a fixed
`ACTIONS`/`NOTIFY` tuple the tests pin, so callers cannot invent incompatible strings. Any missing
or contradictory input combination → fail-closed (`pause_resumable`, `notify=run_status`) — never a
silent default. Hooks own transport + durable state; this module owns decisions; skill prose only
DESCRIBES.

Decision table (normative; unit-tested row by row):

| Situation | Unattended action |
| --- | --- |
| Gateway down, offload optional, orchestrator has fallback | audit availability failure; re-dispatch on fallback seat; note in run summary |
| Gateway down, offload optional, no fallback | visible seat-skip; continue |
| Gateway down, dispatch marked hermes-required | stop at dispatch boundary; persist resumable failure; ERROR protocol |
| Ask send fails / delivery unknown | durable pending-delivery record; bounded retry; critical ask ⇒ pause resumably, NO default action |
| Ask delivered, BB read down | never infer a reply; critical ask waits to deadline then pauses resumably |
| Ask delivered, valid reply | consume once (per-consumer GUID+token dedup, single-poller deployment — see F10 note); resume with structured DATA |
| Ask delivered, ambiguous/invalid option | record disposition; deliver nothing; ≤1 deduplicated clarification |
| Non-critical advisory ask unavailable | record degradation; continue only via caller's explicit safe no-response branch |

**Terminal-substitution rule:** a text may stand in for terminal input ONLY when the ask is
explicitly `remote_reply_allowed` AND the run is unattended AND outbound delivery is confirmed
(2xx accepted); never when sending is uncertain, polling is down, the reply is ambiguous, or an
`option_required` ask got free text. Accepted-by-transport ≠ owner-saw-it: critical gates always
wait for a valid reply, never assume visibility. Property test (F5): NO input with
`remote_reply_allowed == false` can yield a `resume` action from a text reply.

**F10 honesty (pre-existing Phase-1 concurrency).** Phase-1's `deliver` is atomic per-inbox-file
(os.link no-clobber) but two CONCURRENT pollers can each read an unanswered ask + empty consumed
ledger and both return the same delivery path — a real double-resume window if two launchers race
one reply. This is a PRE-EXISTING Phase-1 gap, OUT of Phase-2 scope; Phase-2's claim is scoped to
the actual single-poller deployment (the cron launcher is flock single-flight — one poller at a
time). Filed as a follow-up (interprocess per-token CAS claim); NOT papered over as "atomic" here.

## §7 Security

- **Key + endpoint resolution (F9 fix — one explicit path).** The dispatch boundary loads
  `~/.config/rawgentic/hermes.env` (0600) into the process env, from which the adapter reads
  `HERMES_API_URL` (endpoint) + `HERMES_API_SERVER_KEY` (secret, via `credential_ref`). There is
  no dotenv magic in phase_executor; the loader is an explicit, tested helper at the executor
  dispatch boundary (mirrors the Phase-1 bridge's `_read_bb_conf`). Key referenced by NAME
  everywhere; auth headers redacted from every log/traceback/Observation (Phase-1 `redact`
  extended). A missing/empty key or URL → typed configuration availability failure at preflight,
  never a dispatch with an empty Authorization header.
- **Bind scope:** darwin binds the API server to its LAN address, key required, firewall
  restricted to source .205 (EXECUTED live 2026-07-22: iptables ACCEPT from 10.0.17.205 +
  DROP others on tcp/8710 — runtime rules; persistence is an owner runbook step). Plain HTTP
  on the isolated homelab LAN is ACCEPTED risk — same trust level as today's BlueBubbles
  password over LAN HTTP; named residual: passive capture on the LAN segment. TLS via
  owner-managed proxy = Phase-3 option.
- **Unsandboxed gateway execution (the api_server's OWN startup warning, captured live):**
  "API server is network-accessible (10.0.17.204) AND the terminal backend is 'local'
  (unsandboxed). Agent work dispatched through this endpoint runs as the host user with full
  terminal/file access." The offload seat's `tool_grants: ["read"]` is a rawgentic-side
  manifest — the GATEWAY does not enforce it. Mitigations, stated honestly: (a) bearer-key
  auth (verified 401), (b) firewall to .205 only (verified), (c) offload briefs are
  read-only by POLICY and the orchestrator treats results as UNTRUSTED_DATA, (d) darwin is a
  single-purpose homelab VM the gateway already runs on with the same access. **DECISIVE (F7):
  the adapter's ACTIVATION GATE (§4) REFUSES to dispatch while the gateway reports an unsandboxed
  `local` backend — so this PR cannot send a single offload task to the current darwin config.**
  The seat goes live only when the owner sets `terminal.backend: docker` (or equivalent sandbox)
  on the gateway — moved from a Phase-3 aspiration into a Phase-2 hard precondition.
- **Prompt-injection surface:** Hermes output is untrusted data (§4); options/labels in asks are
  caller-owned; reply text remains DATA (Phase-1 envelope).
- **No new attack surface on the gateway beyond the platform Hermes itself ships**; zero
  gateway-code modification.

## §8 Platform / external dependencies (#226)

platform_apis:
- api: BlueBubbles POST /api/v1/message/query (paginated read) and message/text send on the BB server 10.0.17.148:1234
  feasibility: verified via existing-call-site — hooks/hermes_bridge.py:365-420 (_query_page/_default_transport) and sentinel notify.sh path, Phase-1 shipped live v3.87.0
  failure: fail-loud
- api: hermes api_server platform enablement + LAN bind on the existing darwin gateway (v0.18.2, systemd hermes-gateway.service)
  feasibility: verified via spike — enabled live 2026-07-22 via charlie→darwin hop (hermes config set API_SERVER_ENABLED/KEY/HOST/PORT + systemctl restart); ss shows LISTEN 10.0.17.204:8710 pid hermes; journalctl api_server startup line captured
  failure: fail-loud
- api: GET /health on http://10.0.17.204:8710 from .205 (cross-host reachability + firewall path)
  feasibility: verified via spike — live curl from .205 2026-07-22: HTTP 200 {"status":"ok","platform":"hermes-agent","version":"0.18.2"}
  failure: fail-loud
- api: api_server bearer-key auth gate on /v1/* endpoints
  feasibility: verified via spike — unauthenticated GET /v1/capabilities from .205 2026-07-22: HTTP 401 {"error":{"code":"invalid_api_key"}} (auth enforced; OpenAI error shape confirmed live)
  failure: fail-loud
- api: POST /v1/runs submit + GET /v1/runs/{run_id} poll + POST /v1/runs/{run_id}/stop lifecycle field shapes
  feasibility: verified via capabilities-file — /usr/local/lib/hermes-agent/gateway/platforms/api_server.py on the TARGET darwin build, read live 2026-07-22: routes :4805-4809, run object _set_run_status :4107-4121 ({"object":"hermes.run","run_id",...}), input contract _handle_runs :4168+, get/stop :4479/:4632
  failure: fail-silent
  surface: adapter normalizes to ParsedResult → shared resolve_parse_status (existing enum, no new field); fail-closes on any unrecognized response shape or unrecognized-terminal status past deadline (typed availability error, never a silent empty result — the Phase-1 BridgeUnreachable pattern); versioned response fixtures (tests/phase_executor/fixtures/hermes/, transcribed from the shipped darwin build) pin the contract in CI; capability preflight (/health + /v1/capabilities) + the unsandboxed-backend ACTIVATION GATE precede every dispatch; the authenticated end-to-end run from .205 is a deferred owner-attended cell (#138 deferral — key custody stayed on darwin per D-P2-5, AND the activation gate blocks it on the current unsandboxed backend regardless)

## §9 Testing strategy (red-first)

- Adapter: pure `parse_*` over versioned response fixtures (success, failure, cancelled,
  malformed, contradictory-status → fail-closed); `run()` with injected fake transport covering
  the full lifecycle incl. submission_unknown, stop-ack/stop-lost, deadline, usage-absent.
- Routing: table referential integrity, dead-seat assertion, pool cap, provider→engine map,
  effort-omission tolerance for `hermes`, `offload ∈ WIRED_SEATS`, dispatch CLI `resolve-seat offload`.
- Bridge (ii): option schema validation, collision rejection, every parser row of §5, backward
  compat (optionless ask byte-identical behavior), ledger round-trip, `option_required` gating.
- Policy (iii): every decision-table row; property: no input combination yields a
  default-action on a critical ask.
- Live (`@pytest.mark.live`, RUN_LIVE=1, skipped in CI): one end-to-end offload dispatch +
  one live optioned ask — the deferred owner-attended cells.

## §10 Darwin enablement runbook (one-time; ships in PR) — EXECUTED/REMAINING state, 2026-07-22

EXECUTED in-run (D-P2-4, via charlie→darwin hop — `ssh charlie` then `ssh root@10.0.17.204`):
1. ✅ Version check: hermes v0.18.2 (2026.7.7.2), `api_server.py` present. (If ever absent:
   `hermes update` — never `hermes gateway install --force`, user-unit port flap.)
2. ✅ `hermes config set API_SERVER_ENABLED true` + `API_SERVER_KEY <generated on darwin,
   never left the host>` + `API_SERVER_HOST 10.0.17.204` + `API_SERVER_PORT 8710`;
   `systemctl restart hermes-gateway.service` → active, LISTEN 10.0.17.204:8710.
3. ⚠️ Firewall (runtime, EXECUTED but ORDER-UNVERIFIED — F8): `iptables -I INPUT ... -s
   10.0.17.205 -j ACCEPT` + `iptables -A INPUT ... -j DROP`. The `-A` DROP is APPENDED, so any
   earlier broad ACCEPT in the INPUT chain wins first-match — the recorded probes proved positive
   access from .205 + auth-401, NOT rejection from a third host. Owner MUST re-do per step 5.
4. ✅ Verify from .205: `/health` HTTP 200 (no auth needed); `/v1/capabilities` without key
   HTTP 401 invalid_api_key (auth enforced).

REMAINING (owner, one-time — the auto-mode classifier correctly refuses credential transfer
off darwin and darwin-firewall edits mid-run; each is a one-liner):
5. On darwin — install an ORDERED, dedicated chain (F8: insert-before-broad-ACCEPT, not append),
   allow .205 + darwin-self, DROP the port from all else, then a NEGATIVE probe from a third host,
   then persist:
   ```
   iptables -N HERMES8710 2>/dev/null; iptables -F HERMES8710
   iptables -A HERMES8710 -s 10.0.17.205 -j ACCEPT
   iptables -A HERMES8710 -s 10.0.17.204 -j ACCEPT   # darwin-local clients (self-source hit the old DROP)
   iptables -A HERMES8710 -j DROP
   iptables -C INPUT -p tcp --dport 8710 -j HERMES8710 2>/dev/null || iptables -I INPUT 1 -p tcp --dport 8710 -j HERMES8710
   # NEGATIVE probe from a non-.205 host (expect timeout/refused):
   #   ssh <other-lan-host> 'curl -m5 http://10.0.17.204:8710/health'   # must FAIL
   netfilter-persistent save        # or the host's persistence mechanism — runtime rules vanish on reboot
   ```
6. Copy the key to .205 (owner terminal — FAIL-LOUD, adv-F5: pipefail + non-empty assert + atomic
   0600 write + immediate authenticated /v1/capabilities check):
   ```
   set -euo pipefail
   K=$(ssh charlie "ssh root@10.0.17.204 'grep -m1 -oiP \"api_server_key[=: ]+\\K[A-Za-z0-9._-]+\" /root/.hermes/.env /root/.hermes/config.yaml'")
   [ -n "$K" ] || { echo 'ABORT: empty key extracted'; exit 1; }
   install -m700 -d ~/.config/rawgentic
   umask 077; printf 'HERMES_API_URL=http://10.0.17.204:8710\nHERMES_API_SERVER_KEY=%s\n' "$K" > ~/.config/rawgentic/hermes.env
   curl -fsS -m8 -H "Authorization: Bearer $K" http://10.0.17.204:8710/v1/capabilities >/dev/null && echo OK-CAPABILITIES
   ```
7. **Gateway sandbox precondition (F7 — REQUIRED before the seat can dispatch):** set a sandboxed
   backend on the gateway (`hermes config set terminal.backend docker` or equivalent) and restart;
   until then the adapter's activation gate refuses every offload dispatch. Verify:
   `/v1/capabilities` (or `/health/detailed`) reports a non-`local` backend.
8. Then the deferred live cell: `RUN_LIVE=1 pytest tests/phase_executor/test_hermes_adapter.py -m live`
   (authenticated .205-originated submit→poll→complete on the SANDBOXED backend; also surfaces
   whether the gateway's own ChatGPT/codex model auth needs re-login — 401s to
   chatgpt.com/backend-api/codex were observed in its pre-restart journal).

## §11 Acceptance criteria (this PR)

1. `dispatch --seat offload` resolves (confinement satisfied, effort medium, WIRED_SEATS),
   preflights, and (with injected transport) completes the full submit→poll→normalize lifecycle
   producing a schema-valid Observation (`actual_model="hermes-agent"` verified by verify_post;
   usage-absent → `USAGE_UNAVAILABLE`, never a new field); gateway-down / unsandboxed-backend /
   submission-unknown each yield a typed availability failure and a visible skip/fallback decision
   point — never a hang, never a silent downgrade, never a fabricated success.
2. **Activation gate:** with the gateway reporting an unsandboxed `local` backend, the seat REFUSES
   to dispatch (typed config-availability failure) — proven by an injected-capabilities test. The
   seat is live-tested (@live) ONLY against a sandboxed backend; on the current darwin config it is
   inert-by-design (F7).
3. Ask with options: rendered, parsed per §5 rules; all dispositions covered by tests; optionless
   asks regress nothing (existing Phase-1 suite untouched-green); collision fail-closed at creation.
4. Policy module: §6 table fully implemented + tested with enumerated outputs; property test — no
   critical-ask path reaches a default action, and no `remote_reply_allowed==false` path resumes
   from a text reply.
5. Enablement + sandbox-precondition runbook committed (docs/hermes-offload.md); live cells marked
   `@pytest.mark.live` and documented as the deferred owner-attended verification (D-P2-2/§10 5-8).
6. Version ×4 + phase_executor ×2 bumped; README changelog entry (diagram: no REV explicit, +
   DATA.seatRouting digest regen note); suite delta stated vs recorded baseline (4447 base).

## §12 Task decomposition sketch (WF2 Step 5 refines)

T1 adapter parse layer + fixtures (red-first) · T2 adapter run() + injected transport +
submission_unknown/stop semantics · T3 routing table + registration surfaces + dispatch wiring ·
T4 bridge options (schema/render/parse/ledger) · T5 policy module + decision-table tests ·
T6 runbook + docs + versions + changelog. Risk: T2/T3/T4 high (Step 8a wave fires).

## §13 Risks (adopted from peer + own)

LAN plain-HTTP capture (accepted, named §7) · darwin version gap (runbook step 1) · orphaned
remote run on lost /stop (run_id + stop-ack recorded; no auto-resubmit) · injection via offload
output (§4 envelope + no-routing-influence rule) · fallback cost distortion (fallback keeps the
original dispatch budget; audit names actual engine) · option-label collisions (fail-closed at
creation) · gateway throttling (typed transient availability, bounded retry) · gateway
model-backend auth staleness (401s to chatgpt.com/backend-api/codex observed in the
pre-restart journal — a submitted run may fail at the MODEL layer even with a healthy API
server; surfaces as a failed run status, typed provider failure; owner re-auth is §10-7's
live cell) · classifier no-credential-transfer constraint on autonomous runs (D-P2-5).

## §14 Phase-3+ (enumerated, not committed)

Operational wiring of the offload seat into WF2 phases (a caller that actually dispatches
research subtasks — the F6 deferral) · gateway sandboxed backend (`terminal.backend: docker`)
so the activation gate opens · interprocess per-token CAS claim to close the Phase-1
concurrent-poller double-resume window (F10) · session affinity w/ explicit retention rules ·
SSE event streaming · idempotent submission (if API grows a key) · adaptive pool sizing from
measured gateway behavior · TLS termination · usage/cost enrichment when the gateway reports it ·
WebSocket interactive features · texting Darwin to self-reconfigure.

## §14b Step-11 review remediation (2026-07-22, session 3544db7b)

Cross-model Step-11 (Codex gpt-5.6-sol diff review) + two Opus reviewers (mechanical, architecture)
triangulated on the SAME real defects — all fixed in-branch (all latent: seat activation-gated +
unwired, so severity capped at Medium; none Critical, no owner block). Reviewers confirmed CLEAN:
no key leakage, no false-success classification, both policy invariants structurally guaranteed,
Phase-1 backward-compat intact, wiring complete. Fixes:
- **Activation gate → allowlist** (Codex/Opus): `backend_is_sandboxed` now allows ONLY
  `VERIFIED_SANDBOX_BACKENDS = {docker, podman, gvisor}`; every unknown/`local`/`host`/misspelled
  value refuses (was a 2-item denylist that opened on any unrecognized backend).
- **/health identity validated** (Codex6): preflight now requires `status=="ok"` AND
  `platform=="hermes-agent"` before the constant `actual_model` attestation.
- **Failure taxonomy → availability** (Opus F1): transient submit (429/5xx), 404/transient poll,
  and terminal `failed`/`cancelled` all map to AVAILABILITY so `run_seat` falls back to the
  analysis lane (a failed offload degrades, not breaches); only a definite 4xx submit is a breach.
- **No uncaught crashes** (Opus F3): `_coerce_usage` guards every int coercion (bad usage →
  USAGE_UNAVAILABLE); `run()` catches `(ValueError, TypeError, RecursionError)` → availability;
  `_as_dict` catches `RecursionError` on a hostile nested body.
- **Env loader wired** (Codex1): `run()` loads the 0600 `hermes.env` when env is unset.
- **Bounded preflight** (Opus F4): preflight/submit use `_PREFLIGHT_TIMEOUT_S=30`, only the poll
  loop gets the full seat budget.
- **Bridge** (Codex5/Opus-mech F1/Codex8): `ask_owner` rejects `option_required` without options;
  `interpret_reply` uses `isdecimal()` + try/except (a superscript "²" degrades, never crashes);
  `maybe_send_clarification` marks sent ONLY on a 2xx (a failed send stays retryable).
- **Live cell** (Codex3): `test_live_offload_dispatch` is RUN_LIVE-guarded (was an unconditional
  skip that could never run).
- **UNTRUSTED_DATA envelope** (Codex7): corrected — no adapter envelope; orchestrator prose rule at
  the Phase-3 wiring point (§4 above).
+16 remediation tests. Suite delta re-measured at Step-12 full re-run.

## §15 Decision log

- D-P2-1 (owner 2026-07-22): one WF2 run, one PR, all three strands.
- D-P2-2 (owner 2026-07-22): live smoke deferred; PR "Part of #568".
- D-P2-3 (owner 2026-07-22): scope = all three strands (i)+(ii)+(iii).
- D-P2-4 (owner 2026-07-22): merge grant + in-run smoketest via charlie→darwin authorized
  (supersedes D-P2-2 deferral; deferral = fallback).
- D-P2-5 (session 3544db7b, 2026-07-22): the auto-mode permission classifier refused API-key
  transfer off darwin (both directions, two shapes — not re-attempted per policy). Key custody
  stays on darwin; the enablement/bind/auth/reachability spikes completed WITHOUT the key
  leaving the host; the authenticated .205-originated end-to-end run is a #138 deferred cell
  with the owner one-liners in §10 (5-7). Honest partial per D-P2-2 fallback semantics.
- D-P2-6 (session 3544db7b, 2026-07-22, autonomy — owner asleep, 0 Critical so no BlueBubbles
  block): Step-4 folded 24 gpt-5.6-sol findings (self-review + adversarial, all verified vs code).
  SCOPE NARROWED — (ii) options + (iii) policy ship as operational primitives; (i) offload seat
  ships contract-correct + CI-tested but ACTIVATION-GATED (refuses unsandboxed gateway), with live
  dispatch + WF2-phase wiring deferred to Phase-3. Contract fixes adopted: confinement (F1),
  identity-via-platform-field (F2, reviewer overclaim dissolved), USAGE_UNAVAILABLE (F3), taxonomy
  mapping (F4), effort de-contradiction (adv-F3), runbook firewall/credential (F8/F9). F10 named as
  a Phase-1 pre-existing gap (follow-up, not scope). One design loop-back consumed (r2→r3).
- Peer consult: gpt-5.6-sol proposal adopted on transport (/v1/runs), seat shape (new engine +
  new seat, not analysis-chain lane), fresh-session policy, usage-completeness honesty,
  strict option parsing, pure policy module. DIVERGED on: PR decomposition (peer: 3 PRs;
  owner D-P2-1: 1 PR — peer's isolation preserved at TASK level) and in-chain fallback
  (peer allowed configured chain to analysis; this design keeps `chain: []` + orchestrator-
  explicit fallback to avoid silent cross-pool cost semantics — stricter than peer).
