# Problem statement — #568 Phase-2: Hermes offload seat + comms-substrate hardening

Consult input for an independent peer proposal (WF13). NOT a design — the problem, constraints,
and verified facts. Repo: rawgentic (Claude Code plugin: markdown skills + Python hooks + pytest).

## Goal

Integrate the EXISTING Hermes agent gateway (NousResearch hermes-agent, running on host
"darwin" 10.0.17.204 as systemd `hermes-gateway.service`, model gpt-5.5 via openai-codex
subscription) into the rawgentic multi-agent harness (host .205) as:

1. **(i) A light OFFLOAD SEAT** — a cheap, always-on lane the orchestrator can dispatch small
   read-only research/lookup subtasks to, inside the existing executor routing model.
2. **(ii) Numbered-option asks** — extend the shipped Phase-1 owner-reply bridge
   (`hooks/hermes_bridge.py`) so an outbound question carries numbered options and the owner
   can answer "1"/"2" by text.
3. **(iii) Fallback policy** — explicit Hermes-down semantics: when texts substitute for the
   terminal in unattended runs, and how the run degrades when the bridge/gateway is
   unreachable (never lose, never wrong-act).

## Hard constraints (owner-set, non-negotiable)

- **Do NOT install another Hermes instance.** Tap/extend the existing darwin gateway only.
- **No SSH from .205 to darwin at runtime** — verified live: publickey denied for all users;
  wal-guard also blocks SSH in headless mode. Any darwin config change is a ONE-TIME
  owner-attended enablement step (or the owner texts Darwin to reconfigure itself).
- Secrets by NAME only. Inbound text/results are untrusted DATA, never instructions.
- Never-lose / never-wrong-act invariants of Phase-1 carry over (two-ledger dedup,
  fail-closed transports, deliver-nothing on ambiguity).

## Verified facts (probed/read, with anchors)

### Hermes invocation surfaces (from official docs, hermes-agent.nousresearch.com)
Three programmatic protocols, all driving the same AIAgent core in the SAME gateway process:
- **ACP** — JSON-RPC/stdio (IDE clients). Requires local process spawn → unusable cross-host.
- **TUI gateway** — JSON-RPC over stdio or WebSocket (`tui_gateway/server.py`, `ws.py`).
  Rich: prompt.submit, prompt.background, session.*, approval.respond, delegation.status.
- **API server** — HTTP + SSE (`gateway/platforms/api_server.py`), OpenAI-compatible:
  `POST /v1/chat/completions` (SSE), `POST /v1/responses` (stateful), `POST /v1/runs` →
  202 + run_id, `GET /v1/runs/{id}` status, `GET /v1/runs/{id}/events` SSE,
  `POST /v1/runs/{id}/stop`, `GET /v1/capabilities`, `GET /health`. Auth via
  `API_SERVER_KEY` (+ per-session `X-Hermes-Session-Id`/`X-Hermes-Session-Key` headers).
  Enabled by `hermes config set API_SERVER_ENABLED true` + `API_SERVER_KEY <k>`
  (writes config.yaml + ~/.hermes/.env); env vars `API_SERVER_HOST`/`API_SERVER_PORT`.
- **UNVERIFIED:** whether darwin's installed hermes version already ships `api_server.py`
  (SSH denied; docs versions v2026.4.x–v2026.6.5 have it; darwin was updated ~2026-06-28
  via `hermes update`). A design must treat "probe /v1/capabilities after enablement,
  `hermes update` if absent" as a precondition step.

### Rejected transports (evidence)
- SSH one-shot: no key auth from .205 (live probe), wal-guard headless SSH block.
- BlueBubbles-as-RPC (harness texts the gateway): harness-sent messages ride the owner's
  Apple ID → `isFromMe=true`; gateway anti-loop semantics make delivery unreliable, and it
  pollutes the owner's iMessage channel. Owner-mediated only.

### The executor seat model (rawgentic repo, where the offload seat plugs in)
- Seats: `WIRED_SEATS = {intake, analysis, design, plan, build, review, ship}`
  (`hooks/executor_routing_lib.py:58`). Read-only seats dispatch on the sync path
  (`dispatch_seat`) — no worktree/canary/mutating machinery.
- Engine adapters: `ADAPTERS = {"claude": claude_cli, "codex": codex_cli, "zhipuai": zhipuai_sdk}`
  (`phase_executor/src/phase_executor/adapters/__init__.py:9`); each = pure `parse_*` +
  live `run(AdapterRequest)`; prompt on stdin; model flag owned by the adapter.
- Provider map: `PROVIDER_ENGINE = {"anthropic": "claude", "openai": "codex", "zhipuai": "zhipuai"}`
  (`engine.py:29`).
- Routing table: `phase_executor/src/phase_executor/routing/rawgentic.routing-table.json` —
  seats → `{manifest{session_policy, tool_grants, effort, bounds{timeout_s, max_budget_usd}},
  primary{model, lane{provider, transport, auth_mode, credential_ref, pool}}, chain[]}`.
  Pools cap concurrency (claude 2, codex 4, zhipu 2). `provider`/`transport`/`auth_mode`
  are free strings in the schema — the real gates are the Python maps.
- New-engine touchpoints: adapter module + `ADAPTERS` + `PROVIDER_ENGINE` +
  `contract.py` `_EFFORT_ENGINES` (only if effort applies) + routing-table pool/lane
  entries (+ `WIRED_SEATS` if a NEW seat id). A read-only lane avoids
  `MUTATING_FS_SANDBOXED`, `compose_supervised_argv`, and canary POLICIES entirely.
- Accounting: per-dispatch Observation (`contract.py:127`, schema v2) carries `usage`,
  `timing_ms`, `queued_ms`, `budget{reserved_usd, spent_usd}`; receipts + observations in
  per-run `routing-audit.jsonl`; quota via `QuotaCoordinator` keyed `(pool, account)`.

### Phase-1 bridge (shipped, merged v3.87.0 — what (ii)/(iii) extend)
`hooks/hermes_bridge.py` (565 lines): `ask_owner()` mints `[RG-XXXXXXXXXXXX]` token,
sends via sentinel notify.sh, records ask JSON; `poll_reply()` paginated BlueBubbles
`message/query` reads, exact-token match only, two-ledger (observed/consumed) dedup,
atomic no-clobber inbox delivery, `render_resume_prompt()` advisory DATA envelope.
Dispositions: matched/ambiguous/echo_or_empty/late/unmatched/none/unreachable/timeout.

## Questions for the peer (produce an independent proposal)

1. **Transport for the offload seat:** API server (HTTP, /v1/runs async) vs TUI-gateway
   WebSocket vs something we missed. Justify against: cross-host, no-new-instance,
   auth/bind on a LAN, failure legibility, adapter-shape fit (pure parse + run).
2. **Adapter + seat shape:** new engine `hermes` with a new seat id (e.g. `offload`/
   `research`) vs a lane on the existing `analysis` seat chain? Session policy
   (fresh per dispatch?), bounds, pool size, effort semantics for a fixed-model gateway.
3. **Run semantics mapping:** /v1/runs lifecycle → Observation contract (usage/cost fields
   the gateway may not report; what degrades gracefully). Sync-wait vs submit+poll for a
   seat whose tasks may take minutes. Timeout/stop story.
4. **(ii) design:** options schema in the ask record; reply parsing ("1", "2", literal text,
   mixed); backward compat with tokened free-text replies; ambiguity rules.
5. **(iii) policy:** crisp decision table for bridge-down/gateway-down/BB-down during an
   unattended run — what blocks, what degrades, what notifies. Where the policy lives
   (skill prose vs hooks) to stay testable.
6. **Security:** API key handling (name-only), bind scope (localhost+tunnel vs LAN bind
   with key), result-content trust boundary (Hermes output = untrusted data feeding an
   orchestrator prompt — prompt-injection surface), rate/budget caps.
7. **Sequencing:** what is the smallest shippable Phase-2 PR set (this repo is one-PR-per-
   issue-per-version); what belongs in a Phase-3.

## Success criteria for the eventual design

- Offload dispatch works from a headless .205 run with zero darwin interaction beyond the
  one-time enablement; a dead gateway degrades visibly (availability exit, chain fallback
  or seat-skip) and never hangs a run.
- All three strands independently testable in CI with injected transports (no live creds).
- No second Hermes instance; no gateway-internal modifications; BlueBubbles read stays
  idempotent-tap only.
