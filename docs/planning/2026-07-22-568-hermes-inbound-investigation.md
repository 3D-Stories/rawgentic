# Investigation — #568 AC1: does hermes_agent already relay inbound BlueBubbles?

**Verdict: YES — the Hermes gateway on darwin already relays inbound. But it routes to gateway-internal Hermes skills, not to any sink a separate `claude -p` harness session can read. → the harness TAPS the BlueBubbles read API (idempotent), it does not rebuild a poller and does not intercept the gateway.**

Date 2026-07-22 · session ed47a344 · confirmed by config read + live probe.

## Evidence — the existing inbound relay
- **`projects/hermes_agent/config.template.yaml` → `platforms.bluebubbles`** (confirmed by read):
  `enabled: true`; `extra.server_url` + `extra.password` (redacted); **`extra.webhook_host` / `extra.webhook_port: 8645` / `extra.webhook_path`** — the BlueBubbles server PUSHES new-message events to the gateway webhook; and `extra.channel_skill_bindings` maps a chat GUID → a skill set.
- **Routing mechanism:** the gateway's `bluebubbles.py` adapter resolves `channel_skill_bindings` server-side (keys on `chat_guid or chat_identifier`) and loads the bound skill for that channel. Cited in `projects/sentinel/skills/homelab-reboot/SKILL.md` ("the BlueBubbles adapter keys the session on `chat_guid or chat_identifier` (`bluebubbles.py`)"; owner DM `iMessage;-;+14036189135` and bare `+14036189135` bound to `homelab-reboot`; family group bound to `media-request`).
- **Proof two-way owner comms already work through Hermes:** sentinel's reboot-by-text / status-by-text / shutdown-start-by-text (`projects/sentinel/skills/homelab-{reboot,status}`, design docs `2026-07-08-reboot-by-text-design.md`, `2026-07-09-25-shutdown-start-by-text-design.md`). The owner texts → gateway → skill runs a multi-turn TOTP flow, session surviving a ~5-minute wait for the reply.

## Why "tap the gateway" is NOT the Phase-1 path
The gateway delivers inbound messages to **gateway-internal Hermes skills** (running in the gateway's own agent process on darwin). A rawgentic WF2/epic run is a **separate headless `claude -p --resume` session** the gateway does not route to. Making the gateway hand replies to the harness would require:
- deploying a NEW Hermes skill to darwin (`/root/.hermes/skills/…`) — owner-gated infra, blocked while the owner is away; AND
- a cross-host shared sink (.205 ↔ darwin) for the captured reply.
That is heavyweight and enumerated as Phase-2+ design territory (issue "Phase 2+").

## The chosen tap (endpoint cited, live-probed)
The harness reads the **same BlueBubbles message store** the gateway is fed from, via its read API — reusing the existing credential `~/.config/vm-update-monitor/bluebubbles.env` (`BB_URL=http://10.0.17.148:1234`, `BB_RECIPIENT=+14036189135`, `BLUEBUBBLES_PASSWORD`):

- `GET  {BB_URL}/api/v1/ping?password=PW` → `200 {"data":"pong"}` (server up, credential valid).
- `POST {BB_URL}/api/v1/message/query?password=PW` body `{"chatGuid":"iMessage;-;+14036189135","limit":N,"offset":0,"with":["chat","handle"],"sort":"DESC"}` → `200`, `data[]` of messages. Per-message: **`guid`** (dedup), `text`, `dateCreated` (epoch ms), `isFromMe` (owner reply = `false`; our outbound = `true`), `handle.address` (= owner), `replyToGuid` (set when the owner uses the iMessage reply gesture).

**Why the tap does not conflict with the gateway:** BlueBubbles `message/query` is a READ — it does not consume or delete. Two consumers (the gateway webhook and the harness reader) both see every message; handle-once is per-consumer via GUID dedup. The gateway keeps working unchanged; the harness never touches gateway internals.

## Outbound (reused unchanged)
`projects/sentinel/bin/notify.sh` — `POST {BB_URL}/api/v1/message/text?password=PW` (JSON chatGuid/tempGuid/message/method apple-script), password in a curl `-K` file (never argv), rc 0 = HTTP 2xx. Owner completion-note pattern: `echo "msg" | bash projects/sentinel/bin/notify.sh`.
