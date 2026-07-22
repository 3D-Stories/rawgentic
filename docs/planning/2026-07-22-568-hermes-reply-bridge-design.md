# Design — #568 Phase 1: two-way owner-reply bridge (Hermes comms substrate)

- **Issue:** #568 · **Scope:** **Phase 1 only** — the two-way owner-reply bridge. Phase 2+ (Hermes offload seat, full comms substrate) is deferred to a future design-doc/WF1 epic (owner comment).
- **Date:** 2026-07-22 · **Session:** ed47a344 · **Complexity:** standard_feature · full spine
- **Rev 2** — revised after Step-4 pass-1 critique (Codex adversarial + opus self-review): token-only correlation, two-store crash model, single canonical inbox, `atomic_write_lib`, advisory-envelope. See "Review synthesis".

## Problem
Unattended/overnight harness runs (`claude -p --resume` sessions) can text the owner (outbound, `sentinel/bin/notify.sh`) but cannot receive the **reply**. Phase 1 closes the inbound-CAPTURE half: it captures, correlates, and records the owner's reply in a durable inbox + exposes `render_resume_prompt`. The final step that feeds that inbox into `claude -p --resume` lives in a workspace launcher OUTSIDE this repo and is a documented follow-up (see AC-deferral) — so Phase-1 is not, by itself, an end-to-end unattended resume.

## Investigation result (AC1 — tap, don't rebuild)
The Hermes gateway on darwin **already relays inbound** (BlueBubbles → gateway **webhook** `webhook_port: 8645` → `channel_skill_bindings` → gateway-internal Hermes skill; proven by sentinel reboot-by-text). **But it routes to gateway-internal skills, not to any sink a separate `claude -p` harness session can read.** Intercepting it needs a darwin skill deploy + cross-host sink — owner-gated, blocked while owner away. **Decision:** the harness **taps the same BlueBubbles message store via its read API** (idempotent query — reads don't consume), reusing the existing credential; no gateway rebuild, no conflict (handle-once is per-consumer). Full note: `docs/planning/2026-07-22-568-hermes-inbound-investigation.md`.

## Approaches
- **A — harness-side read-only poller against `message/query`  ✅ CHOSEN.** No darwin deploy; reuses `bluebubbles.env` + `notify.sh`; idempotent (no gateway conflict); unit-testable with a fake transport; lands entirely in rawgentic. Con: poll not push (~15 s).
- **B — deploy a Hermes gateway skill on darwin → shared sink  ❌ Phase-2+.** Push, reuses gateway routing, but needs owner-gated darwin deploy + cross-host sink; couples harness to gateway internals.

## Architecture (Approach A) — module `hooks/hermes_bridge.py`
All transport (HTTP query, notify send) is injected for CI (no live creds), mirroring notify.sh's `NOTIFY_CURL`/`NOTIFY_PYTHON` override idiom.

**1. Outbound ask** — `ask_owner(question, run_id) -> ask_record`
- Mint a high-entropy bracketed token `token = "[RG-" + secrets.token_hex(6).upper() + "]"` (48 bits, exact-match; routing is NEVER derived from free reply text). Create the ask record **create-if-absent** via `os.open(path, O_CREAT|O_EXCL|O_WRONLY)` (collision → re-mint + retry) since the token is also the record key. This write-once identity file uses O_EXCL, NOT `atomic_write_text` — the helper's `os.replace` is an unconditional overwrite (documented carve-out; see State store). A later `status` transition (prepared→sent) DOES overwrite via `atomic_write_text`.
- Ask record `{token, run_id, question, sent_ts_ms, status}`. Outbound states `prepared` → `sent` (notify rc 0 / HTTP 2xx) → or `delivery_unknown` (send timed out AFTER submission). **A `delivery_unknown` ask is NEVER auto-resent with a new token** (would duplicate the question) — preserved for reconciliation.
- Send via existing `sentinel/bin/notify.sh` (reuse **unchanged** — one outbound voice), embedding the token: `"<question>\n(reply to this message, keep ref <token>)"`.

**2. Inbound poll** — `poll_reply(ask_record, timeout_s, interval_s=15) -> Reply | Status`
- Query `POST {BB_URL}/api/v1/message/query?password=PW` — **password in a curl `-K` file** (the read path reuses notify.sh's send-path `-K` idiom; never argv) — for the owner DM, `sort:"DESC"`, small `limit`, `dateCreated > sent_ts_ms`.
- **Direction/owner filter:** keep only `handle.address == BB_RECIPIENT` AND `isFromMe == false`. For a 1:1 owner DM the handle filter is belt-and-suspenders; `isFromMe:false` is the load-bearing direction control.
- **Correlation — TOKEN-ONLY, EXACT (Phase 1):** a reply matches iff the exact bracketed `token` appears in `text`. **`replyToGuid` correlation is CUT from Phase 1** — `notify.sh` returns only the HTTP code (`-o /dev/null -w %{http_code}`), so the sent GUID is not capturable without modifying it, and the live probe never observed a populated `replyToGuid`. (Phase-2: capture the sent GUID + probe `replyToGuid` to add the reply-gesture path.) No positional/time-window fallback (would weaken wrong-question/wrong-recipient safety); an untokened message is `unmatched`.
- **Per-GUID lifecycle + two stores (fixes never-lose):** `observed.jsonl` records each fetched message once — its dedup means only "do not re-append an identical observation" and **does NOT gate delivery**. A SEPARATE `consumed` store marks a guid delivered; **delivery reprocessing is gated ONLY on the consumed store, written AFTER the inbox file.** A crash between observe-append and inbox-write leaves the guid observed-but-not-consumed → next poll re-classifies and delivers it. A guid is a terminal no-op only once its inbox delivery is durable.
- **Reply classifications** (each delivers nothing except `matched`): `matched` (exactly one message carries the open token) · `unmatched` (no token) · `echo_or_empty` (only the token, or reproduces the question with no answer) · `ambiguous` (**≥2 distinct messages each carrying the open token** → NO_ACTION, owner disambiguates) · `late` (token already consumed → reconciliation notice, answer unchanged). **Zero matches = `UNMATCHED`, never `AMBIGUOUS`.**
- Returns `Reply{guid,text,dateCreated}` only on `matched`; else `Status` ∈ `{NO_REPLY_YET, TIMEOUT, UNREACHABLE, AMBIGUOUS, UNMATCHED}`.

**3. Deliver into workflow** — reuse the existing `claude -p --resume` launcher pattern (AC3, no bespoke injection)
- **Single canonical inbox:** on `matched`, write `claude_docs/.hermes-bridge/inbox/<run_id>/<delivery_id>.json` (deterministic `delivery_id = sha1(guid)[:12]`) **create-if-absent** (`os.open(O_CREAT|O_EXCL)`, `mkdir` the per-run dir first): if the `delivery_id` file already exists, **SKIP the re-write**. Gating the re-write on file-existence (not only the consumed ledger) means a crash-replay never reverts a file the launcher has already advanced (`ready`→`claimed`) — closes the never-double-act window. Schema `{delivery_id, run_id, token, guid, dateCreated, reply_text, state:"ready"}`. Mark the guid consumed ONLY after the inbox file exists. **The Markdown session-notes path is dropped** — `session_notes.md` stays the append-only human resume log, not a delivery sink.
- **Resume-glue (home named):** the consumer that turns the inbox file into a resume prompt lives in the **workspace launcher** (`epic475-resume.sh` at workspace root; `sentinel/bin/provision_hermes_reboot.sh`) — OUTSIDE the rawgentic repo. Phase-1 rawgentic deliverable = the inbox contract + a unit-tested `render_resume_prompt(inbox_json) -> str` helper the launcher calls. The launcher atomically renames `ready`→`claimed` before resuming, retains the claimed file until success, and a **stale `claimed` is explicit human recovery, never auto-replayed** (never double-act). Wiring the launcher + the genuine-human round-trip are documented-deferred (owner away) — see AC-deferral.

## State store (`claude_docs/.hermes-bridge/`, git-excluded via `.gitignore:25`)
- `observed.jsonl` — append-only observation ledger (persist-before-classify; dedup = don't-re-append-observation ONLY).
- `consumed.jsonl` — delivered-guid ledger (the ONLY delivery gate; written after the inbox file).
- `asks/<token>.json` — ask record + outbound state (atomic create-if-absent).
- `inbox/<run_id>/<delivery_id>.json` — canonical delivery outbox (atomic via `atomic_write_lib`, deterministic id).
- **Write discipline:** write-once IDENTITY files (`asks/<token>.json` initial create, `inbox/.../<delivery_id>.json`) use `os.open(O_CREAT|O_EXCL)` create-if-absent (collision-safe + crash-replay-safe: skip on exists). `atomic_write_lib.atomic_write_text` (mkstemp + `os.replace`, unconditional overwrite, symlink-safe, temp-unlinked-on-error) is used for full-file OVERWRITES (e.g. an ask record's `status` transition) — CLAUDE.md §5 mistake #12 (reuse the helper, never hand-roll `os.rename`). Ledgers (`observed.jsonl`, `consumed.jsonl`) are append-only (`open('a')`). Rationale for JSON/JSONL over the peer's SQLite: repo has no SQLite precedent and consistently uses JSON state (`run_records.jsonl`, `dispositions.jsonl`, `.wf2-state/`); same guarantees hold (append persist-before-classify, unique-by-guid dedup via ledger scan, atomic delivery). SQLite is a future upgrade if question volume ever needs indexed queries.

## Fail-safe semantics (AC6 — never lose / never wrong-act)
- **Never lose:** reads are idempotent; delivery is gated ONLY on the `consumed` ledger, written AFTER the durable inbox file (write-inbox-before-mark-consumed). A crash anywhere before consume → next poll re-delivers (the guid is observed but not consumed). The observed-ledger never suppresses an undelivered reply.
- **Never wrong-act:** `ambiguous` (≥2 tokened matches) and `unmatched` (0 matches) both deliver NOTHING → degrade to "owner comes to the session". `UNREACHABLE` (HTTP error / conn fail) is distinct from `NO_REPLY_YET` — the run must NOT proceed as if the owner declined. `TIMEOUT` delivers nothing (never means yes/no/cancel/continue). `delivery_unknown` outbound never auto-resends.
- **Untrusted input:** reply `text` is NEVER executed — reject NULs, bound size, preserve original, serialize as JSON; written as labeled DATA. (Enforcement boundary: see Security.)

## Security implications
- Secrets by NAME: `BLUEBUBBLES_PASSWORD` via the curl `-K` file on BOTH send (notify.sh) and the new read path — never argv, never logs; `?password=` redacted from ALL logs AND exception/traceback strings. Reuse `~/.config/vm-update-monitor/bluebubbles.env`.
- Owner-only: strict `BB_RECIPIENT` + `isFromMe:false`; group chats / other handles ignored.
- **Untrusted-input enforcement (advisory envelope is NOT the boundary):** the "treat as DATA" banner prepended to the resume prompt is **advisory framing**, not an enforcement control — an LLM can be steered past a banner. The ENFORCED bound: (1) inbox content populates ONLY the recorded question's answer; (2) an owner text reply must NOT, by itself, unlock auto-approval of any destructive/new-scope action — the harness permission classifier / owner gate stays active on a reply-triggered resume; (3) embedded directives in a reply are surfaced, never executed. Since the source chat is owner-only, third-party injection is not the threat; the residual risk is the owner's own reply being over-interpreted as broad authority — the permission gate is what actually bounds it.
- **Transport (accepted LAN exception — confidentiality, integrity AND authentication):** `message/query`/`message/text` go over plaintext HTTP on the homelab LAN (`.205`↔`.148`, same-L2). An on-path LAN actor can (a) read the password + message contents (confidentiality), (b) alter a query response (integrity), and (c) — having read the outbound token off the plaintext channel — forge a message with the owner handle carrying that token (authentication), which token-only correlation would match. **The residual is bounded, not eliminated:** a matched reply is delivered as untrusted DATA answering ONLY the recorded question, and the harness permission gate stays active on the reply-triggered resume — so a forged answer cannot itself unlock a destructive/new-scope action (that still hits the gate). Accepted threat-model exception, consistent with the whole homelab's plaintext-LAN BlueBubbles bridge (reboot-by-text mitigates the same channel with TOTP); HTTPS/tunnel is out of Phase-1 scope (touches the shared server config). Future hardening: sign/verify the correlation token or move to an authenticated channel.

## Platform / external dependencies
platform_apis:
- api: POST /api/v1/message/query on the BlueBubbles Server REST API (BB_URL=http://10.0.17.148:1234)
  feasibility: verified via spike — live probe 2026-07-22 from host .205 (session-notes Step 2 "LIVE PROBE"): POST {BB_URL}/api/v1/message/query?password=PW body {"chatGuid":"iMessage;-;+14036189135","limit":3,"offset":0,"with":["chat","handle"],"sort":"DESC"} → HTTP 200, data[] of message objects carrying guid, text, dateCreated (epoch ms), isFromMe, handle.address — the EXACT fields the token-only correlation + dedup use. (replyToGuid is NOT relied on — cut from Phase 1.)
  failure: fail-silent
  surface: the poller returns a distinct UNREACHABLE status on any non-2xx / connection failure (never "no reply"); logged (password redacted), run degrades to owner-attended. Test #1 exercises UNREACHABLE via the fake transport.
- api: POST /api/v1/message/text on the BlueBubbles Server REST API (outbound send, via notify.sh, unchanged)
  feasibility: verified via existing-call-site — projects/sentinel/bin/notify.sh (production; owner completion-note pattern). Only the HTTP code is consumed (rc 0 = 2xx); the sent GUID is intentionally NOT needed (token-only correlation).
  failure: fail-loud
  surface: notify.sh returns the HTTP code; non-2xx → send-failure → ask recorded `delivery_unknown`, not `sent`.

## File changes
- NEW `hooks/hermes_bridge.py` — `ask_owner` / `poll_reply` / `deliver` / `render_resume_prompt` + `--self-check`; injectable transport (query fn) + notify fn.
- NEW `tests/hooks/test_hermes_bridge.py` — fake transport + fake notify + tmp state dir (no live creds). Cover: request shape + `-K` secret handling; owner/direction filter; observed-vs-consumed two-store; **crash-replay re-delivers exactly once**; **token-only exact correlation + `unmatched` when token absent** (NO serialized-fallback); `echo_or_empty`; `ambiguous` (≥2 tokened) → no delivery; zero-match = UNMATCHED; `UNREACHABLE`≠`NO_REPLY_YET`; since-ts; `delivery_unknown` no-resend; write-inbox-before-consume ordering; NUL-reject + size-bound; `render_resume_prompt` envelope shape + embedded-directive surfaced (not executed); password redacted in logs+tracebacks.
- NEW `docs/planning/2026-07-22-568-hermes-inbound-investigation.md` (AC1).
- NEW `docs/planning/2026-07-22-568-hermes-reply-bridge-design.{md,html}` (this).
- MOD `README.md` (Changelog + feature + suite tail); version bump surfaces (feat→**minor**): `.claude-plugin/plugin.json`, `plugins/rawgentic/.codex-plugin/plugin.json`, the `test_plugin_version_bumped` pin, AND `canary.py:38 EXPECTED_PLUGIN_VERSION` (HARD lane `test_canary_evidence.py:76` — confirmed present at 3.86.0). NO `phase_executor` bump (untouched). VERIFY the full live surface list via pr-preflight. Workflow-diagram no-spine-change decision recorded.

## Multi-PR: single PR (< 500 lines, self-contained).

## Owner-gated / deferred (owner away) — explicit
- **AC4 genuine-human round-trip** (owner texts back) needs the owner → documented-deferred; a **simulated** owner reply (inject a tokened test message via message/text, or a fixture) demonstrates the full path mechanically (inbox written + `render_resume_prompt` output shown).
- **Launcher resume-glue wiring** (`epic475-resume.sh` reading the inbox → resume prompt) lives OUTSIDE the rawgentic repo → the "acted-on" half of AC4 is Phase-1-documented + demo-only, not wired in this PR. Phase-1 rawgentic ships the inbox contract + `render_resume_prompt` (unit-tested); the launcher edit is a separate follow-up.

## Verification strategy
- `pytest tests/hooks/test_hermes_bridge.py` (fake transport) — red-before-green per task.
- `hooks/hermes_bridge.py --self-check` — assert-based, no live bridge.
- FULL suite once at Step 9 (baseline diff). Mechanical live demo (simulated owner reply) for AC4; genuine-human + launcher wiring deferred.

## Review synthesis (provenance)
- Peer consult (Codex, blind): `docs/reviews/peer-rawgentic-peer-problem-568-2026-07-22.md`.
- Step-4 pass-1 adversarial (Codex): `docs/reviews/2026-07-22-568-hermes-reply-bridge-design-md-2026-07-22.md` (1 Crit, 3 High, 4 Med) + opus self-review (2 High, 4 Med, 1 Low). Rev-2 changes made in response: never-lose two-store fix (Crit), token-only correlation (High — replyToGuid unbuildable), single canonical inbox (High), AMBIGUOUS single definition + serialized-fallback removed (High), `atomic_write_lib` reuse (Med), advisory-envelope + enforced permission-gate (Med), token entropy+collision-check (Med), plaintext-LAN exception documented (Med), resume-glue home named + demo-only (Low). Cut from Phase 1 (both reviewers concur): gateway/webhook changes, message bus, session injection, NLP matching, multi-owner, dashboards, auto command execution.
