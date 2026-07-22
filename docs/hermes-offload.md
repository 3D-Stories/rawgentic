# Hermes offload seat + owner-comms primitives (#568 Phase-2)

Operator doc for the `offload` executor seat and the Phase-2 bridge primitives. Full design:
`docs/planning/2026-07-22-568-phase2-hermes-offload-design.md`.

## What shipped (v3.88.0)

- **`offload` executor seat** (`hooks/executor_routing_lib.py` WIRED_SEATS; routing table
  `phase_executor/src/phase_executor/routing/rawgentic.routing-table.json`): a read-only seat on
  the new `hermes` engine (`adapters/hermes_http.py`), talking to the EXISTING darwin Hermes
  gateway's OpenAI-compatible `/v1/runs` HTTP API. **No second Hermes instance** — it taps the
  gateway already running on darwin (10.0.17.204, systemd `hermes-gateway.service`).
- **Numbered-option owner asks** (`hooks/hermes_bridge.py`): `ask_owner(options=, response_mode=)`
  so an unattended run can text a question with numbered choices and the owner replies "1".
- **Unattended fallback policy** (`hooks/hermes_policy.py`): a pure decision table for
  gateway-down / delivery-uncertain / reply-ambiguous / terminal-substitution.

## The activation gate (why the seat is inert today)

The seat's adapter probes the gateway backend before every dispatch and **REFUSES** unless the
gateway reports a confirmably **sandboxed** terminal backend. rawgentic's `tool_grants:["read"]`
is caller-side capability-selection — it does NOT constrain the gateway, which runs agent work
with full host access when `terminal.backend: local`. Fail-closed: an absent/unknown/`local`
backend refuses. On refusal the seat degrades chain-aware to the `analysis` lane
(claude-sonnet-5), recorded on the Observation (`fallback_reason`) — never silent.

**Consequence:** until the gateway is sandboxed, `offload` behaves as an audited alias for the
analysis lane. This is the intended safe posture.

## Enablement runbook (owner, one-time)

The API server was enabled live 2026-07-22 (config set + restart + firewall + reachability/auth
verified from .205). The steps below are the REMAINING owner-only actions (the auto-mode
classifier correctly refuses credential transfer off darwin and mid-run firewall edits).

1. **Firewall (ordered, dedicated chain — insert BEFORE any broad ACCEPT, then a NEGATIVE probe):**
   ```
   iptables -N HERMES8710 2>/dev/null; iptables -F HERMES8710
   iptables -A HERMES8710 -s 10.0.17.205 -j ACCEPT
   iptables -A HERMES8710 -s 10.0.17.204 -j ACCEPT
   iptables -A HERMES8710 -j DROP
   iptables -C INPUT -p tcp --dport 8710 -j HERMES8710 2>/dev/null || iptables -I INPUT 1 -p tcp --dport 8710 -j HERMES8710
   # from a NON-.205 host (expect timeout/refused): curl -m5 http://10.0.17.204:8710/health
   netfilter-persistent save
   ```
2. **Key + endpoint to .205 (fail-loud):**
   ```
   set -euo pipefail
   K=$(ssh charlie "ssh root@10.0.17.204 'grep -m1 -oiP \"api_server_key[=: ]+\\K[A-Za-z0-9._-]+\" /root/.hermes/.env /root/.hermes/config.yaml'")
   [ -n "$K" ] || { echo 'ABORT: empty key'; exit 1; }
   install -m700 -d ~/.config/rawgentic
   umask 077; printf 'HERMES_API_URL=http://10.0.17.204:8710\nHERMES_API_SERVER_KEY=%s\n' "$K" > ~/.config/rawgentic/hermes.env
   curl -fsS -m8 -H "Authorization: Bearer $K" http://10.0.17.204:8710/v1/capabilities >/dev/null && echo OK
   ```
3. **Sandbox precondition (REQUIRED before the seat dispatches):** set a sandboxed gateway backend
   (`hermes config set terminal.backend docker` or equivalent) and restart the gateway. Verify
   `/v1/capabilities` reports a non-`local` backend.
4. **Live cell:** `RUN_LIVE=1 pytest tests/phase_executor/test_hermes_adapter.py -m live`
   (authenticated .205→gateway submit→poll→complete; also surfaces gateway model-backend auth
   staleness — 401s to `chatgpt.com/backend-api/codex` were seen in the pre-restart journal).

## Deferred to Phase-3

Operational wiring of the seat into WF2 phases (a caller that dispatches research subtasks);
gateway sandboxed backend; interprocess per-token CAS for the Phase-1 concurrent-poller window;
session affinity; SSE streaming; TLS; usage/cost enrichment. See design §14.
