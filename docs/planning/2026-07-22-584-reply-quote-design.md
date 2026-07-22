# #584 — Reply-quote GUID correlation + short numeric fallback token (lane design note)

Small-standard lane brief (simple_change, 4 impl files). Parent arc: #568 (Phase-1 PR #573, Phase-2 PR #574). UAT evidence 2026-07-22: the owner's two natural reply gestures (quote-reply without token; bare token without brackets) both failed the exact-bracketed-token matcher.

## Approach (single obvious path)

Widen `classify_batch` candidate matching from token-only to **quote-first, token-fallback**, and shorten the token. All never-wrong-act invariants (two-store never-lose, token-closure, consumed-GUID dedupe, ambiguity-refuses, temporal scoping) unchanged.

**Match precedence per candidate message (guid-bearing, NUL-free, owner-inbound, dateCreated > sent_ts):**
1. **Quote-match:** `msg.replyToGuid == ask.sent_guid` (exact equality vs stored value; None never matches).
2. **Token-match:** legacy exact bracketed `[RG-…]` (AC7) OR new numeric token at word boundary, brackets optional.

## File changes

- `hooks/hermes_bridge.py`:
  - `mint_token()` → `RG-` + 6-digit numeric suffix (`secrets.randbelow`), collision-checked: existing O_EXCL asks-file create loop retries on collision (already 8 attempts); token rendered as `ref RG-482913`. (Amended — owner decision at the Step-4 breaker.)
  - `ask_owner()` → after 2xx send, **sent-GUID self-query**: via existing `_default_transport`, rows where `isFromMe` AND token in text AND `dateCreated >= sent_ts` (small skew allowance), pick the exact-full-text row first, else **EARLIEST dateCreated** (Amendment 8 tie-break) — the ask precedes any gateway ACK echo (live hazard: Darwin ACKs echo the token back as isFromMe; newest-row pick would capture the ACK, probe guids 8768B0BF vs 54016324). Miss/failure → `sent_guid: null`, logged, ask proceeds (quote path inert, token path = Phase-1 behavior).
  - `classify_batch()` → quote-match arm + widened token-match; ambiguity semantics unchanged (two distinct candidates → ambiguous → deliver nothing).
  - `interpret_reply()` → strip token if present (quote-only replies carry none); numeric option parse unchanged.
  - Message template → ref line LAST and BARE: `Reply to this message — ref RG-482913` / options variant `Reply with the option number — ref RG-482913` (AC6; exact strings in Amendment 3).
- `tests/hooks/test_hermes_bridge.py` → red-first cases per AC (see plan).
- Version surfaces ×N (grep current version at bump time) + README Changelog.

## Failure modes

- **Send propagation delay:** self-query may run before the sent message lands in the store → short bounded retry (≤3 polls, ~2s apart), then `sent_guid: null` (fail-open to token path). Never fails the ask.
- **ACK-echo contamination:** earliest-row rule above; ACKs are also `isFromMe`-filtered OUT of inbound candidate flow (existing owner-inbound filter), so echoes can never false-match as replies.
- **Owner quotes a non-ask message** (observed live: Cell A retry quoted a Darwin chat message): quote arm no-match → token arm → standard dispositions. Degrades, never wrong-acts.
- **Quote-match to consumed/closed ask:** existing token-closure/consumed flow classifies `late` — never double-delivers.
- **6-digit numeric in unrelated owner text:** word-boundary + temporal scoping + open-ask-only + ambiguity-refuses; residual risk accepted (correlation-only token, no auth role).

## Security implications

No new secrets, no new endpoints, send path unchanged (notify.sh reuse). `replyToGuid` used only for equality against a locally-stored GUID (no dereference, no execution). Numeric token lowers entropy but token is correlation-only — inbound already filtered to the owner recipient; never-wrong-act refusal on ambiguity retained. Inbound text remains untrusted DATA (envelope unchanged).

## Platform / external dependencies

platform_apis:
- api: POST /api/v1/message/query returning isFromMe rows with guid/text/dateCreated/replyToGuid on the BlueBubbles server
  feasibility: verified via spike — live probes this session via the exact shipped invocation (`hb._default_transport(chat_guid=..., since_ms=..., limit=...)`): outbound ask row found isFromMe=True guid=54016324 (Cell B ask); replyToGuid populated on all owner inline replies (guids 8034046D/54016324/B75F6CB1/B8B6E537 resolved to their quoted targets)
  failure: fail-loud
  surface: BridgeUnreachable raised on non-2xx/parse/unexpected shape (existing `_query_page` contract — never a silent empty result)

## Amendments (Step 4 spec-tightening pass, owner decisions 2026-07-22)

**Owner decisions (breaker resolution):** token format = `RG-` + 6 digits (`RG-482913`) — accidental all-numeric collision impossible, still phone-typeable; ref rendered on its OWN line with no enclosing punctuation.

1. **(F1, High) Quote-arm null guard — normative:** the quote arm evaluates ONLY when `ask.sent_guid` is truthy AND `msg.replyToGuid` is truthy AND they are equal. Never a bare `==`. Mandatory red-first test: `sent_guid=None` × plain owner message (`replyToGuid` absent) must NOT match — the None==None wrong-delivery case.
2. **(F2, High) Token format:** `mint_token()` → `RG-` + `secrets.randbelow` 6 digits. Match arm: word-boundary `\bRG-\d{6}\b`, brackets optional (`[RG-482913]` also accepted). Entropy note: ~20-bit space is correlation-only; trust boundary remains the owner-handle filter; quote arm is primary. Owner signed off on the residual (prefixed) collision risk explicitly.
3. **(F7) Exact templates (pinned by test — no punctuation after the token):**
   - free_text: last line `Reply to this message — ref RG-482913`
   - options: last line `Reply with the option number — ref RG-482913`
4. **(F3) Enumerated breakages to update in the same tasks:** `TOKEN_RE` (:44) → two-format matcher (legacy `\[RG-[0-9A-F]{12}\]` OR new form); `self_check` token assert (:655); `test_mint_token_shape` (:62); `test_ask_owner_sent_on_rc_2xx` (:78, template text); `test_mint_token_unique` (:67) → deterministic rewrite via stubbed `secrets.randbelow` (200-draw probabilistic test is ~2% flaky in a 1e6 space).
5. **(F4) Phase-1 reversal reconciled:** Phase-1's probe "never observed a populated replyToGuid" because its probe messages were not reply-gestures; the #568 inbound investigation (:23) already noted the field is set only on the iMessage reply gesture, and today's spike resolved 4 reply-gesture guids to their quoted targets. A captured spike row (owner reply-gesture with populated replyToGuid) is pinned as a test fixture to lock the field name against BlueBubbles drift.
6. **(F5) Injectability — normative:** `ask_owner` gains `transport=None, sleep=None` params (the fixed-count retry superseded the drafted clock-based deadline — no `clock` param shipped) (repo convention: all I/O injected); injected paths short-circuit the retry (no real sleeps in tests). Tradeoff recorded: the post-send self-query is racy vs capturing the guid from the send response, chosen to avoid touching sentinel-shared notify.sh (cross-project blast radius); the racy path fail-opens to `sent_guid: null` (token fallback), never wrong-acts.
7. **(F6) Property tests — created, not extended:** NEW property suite over the widened matcher asserting: no owner message resolves `matched` unless it uniquely quote-matches a truthy `sent_guid` or uniquely token-matches; two distinct candidates → deliver nothing; null `sent_guid` → quote arm inert; legacy and numeric token forms never cross-match.
8. **(F8) Plumbing — explicit:** `classify_batch` gains a `sent_guid` parameter; `poll_once` passes `ask_record.get("sent_guid")`. Token-type discrimination: legacy tokens match by exact bracketed substring; new tokens by word-boundary regex. Self-query tie-break on equal `dateCreated`: prefer the row whose text equals the full rendered ask; residual mis-pick degrades the quote path only (never wrong-acts; inbound ACK echoes are isFromMe-filtered).
9. **(Low note) Wording:** collision check is against all persisted ask files (open or closed), not merely open asks.
