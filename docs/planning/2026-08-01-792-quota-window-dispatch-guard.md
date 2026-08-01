# Design — pre-dispatch quota-window guard (#792)

**Issue:** #792 · **Epic:** #756 · **Date:** 2026-08-01 · **Complexity:** standard_feature
**Revision:** 3 — **the design budget is now exhausted (design 2/2, global 2/3).**

## Why rev 3 exists: the contract changed and revs 1-2 were written against the stale one

Rev 2's Step-4 gate returned Critical `scope_fidelity`. It was right, and verified:

**#792's scope was widened in a comment** (`IC_kwDORc-e6c8AAAABMvOFwA`, mirrored at
`docs/measurements/2026-08-01-owner-notes-executor-economics.md:351`). I had read the issue *body*
and never its *comments*. Two consequences, both of which invert rev 1-2's central claims:

1. **Spike S1 is already DONE by the owner, and the OAuth endpoint WORKS on this account.** Live
   shape, from the comment:
   ```
   {"five_hour":{"utilization":19.0,"resets_at":"2026-08-01T08:20:00Z","limit_dollars":null,…},
    "seven_day":{"utilization":66.0,"resets_at":"2026-08-06T08:00:00Z",…},
    "extra_usage":{"is_enabled":true,"used_credits":4331.0,"currency":"CAD",…}}
   ```
   The field is **`utilization`** (not `used_percentage`); `resets_at` is an **ISO-8601 string**
   (not a unix epoch); `limit_dollars` is **null on this plan**, so the owner's instruction is
   explicit: *"the guard must key on `utilization`, never on dollars."*
2. **AC2 as filed ("Codex-lane dispatches unaffected") is now WRONG.** Codex moved from 5-hour +
   weekly to **weekly only**, and the owner burned a free reset on it. Codex is a scarce pool with
   its own budget. The guard must cover **three** windows — Claude 5-hour, Claude 7-day, codex
   weekly — and **the refusal must name WHICH pool is walled**, so the orchestrator can route to the
   other provider instead of stalling.

This also **deletes** most of rev 2's complexity: the endpoint is a plain HTTPS call, so it works in
headless/cron. Rev 2's reactive second producer, its two-producer schema ambiguity, and its
statusline render-churn problem all disappear. Rev 3 is simpler than rev 2 while covering three
windows instead of one.

Owner-endorsed prior art (same comment): take usage-guard's **architecture** — poll the endpoint on
a schedule → write a control file → read it at the decision point — and its **threshold semantics**
(wall when *either* window is over), not its code (macOS-only, and merely cooperative where
`executor_routing_lib.py dispatch` is a real hard gate).

## Scope of THIS PR, and what is deliberately deferred

The guard is **pool-generic**. This PR ships the mechanism plus the source that is *confirmed to
work*:

| Pool | Source | This PR |
|---|---|---|
| `claude_5h` | Anthropic OAuth usage endpoint (owner-verified) | **shipped** |
| `claude_7d` | same single read | **shipped** |
| `codex_weekly` | **no verified source exists** | slot shipped, source **not** implemented — that pool reports `inactive(no_source)` |

**This is a real reduction against the owner's stated requirement and is not hidden.** The codex
weekly pool needs its own spike (an OpenAI/codex usage read this session cannot perform — the
permission classifier denied credential access twice). Shipping the Claude half now is strictly
better than shipping nothing, and the codex pool becomes a config + adapter addition with no
redesign, because the pool table is generic from day one. Filed as a follow-up child of #756 and
posted on #792. Per repo convention this PR says **`Part of #792`**, not `Closes`.

## Approach — a per-target eligibility filter at the existing chain-walk seam

`routing.eligible_targets` already implements the needed semantics for `forbidden_combinations`:
*"Primary + chain in order, with every forbidden target skipped (chain-aware skip — never blind
next-entry)"* (`routing.py:205-210`); an empty result raises `ChainExhausted`, already mapped to
exit 3 retryable at `:881-882`.

The wall becomes one more per-target ineligibility reason at the seam every dispatch path walks.

**Rejected — a per-seat refusal (the issue's original sketch).** Chains cross lanes (`build` =
anthropic → anthropic → openai; `review` = openai → anthropic → anthropic), so a per-seat refusal
would kill cross-provider fallback. At target level the right behavior falls out for free — and
now matters *more*, since with codex also walled the orchestrator needs to know which of the two
providers is still open.

**Separate function, not a parameter** (peer consult, kept from rev 2): the guard lives in a new
`dispatch_eligible_targets(...)`. `eligible_targets` stays static-only with no parameter a live
reading could enter through, so **reconcile (`:2372`) and the read verb (`:2625`) cannot be gated**
by a later edit. The invariant is in the type signature, not a convention.

## Pool model

```python
POOLS = {                     # pure data; a pool is (predicate over lane, threshold env, source)
  "claude_5h":     lane.provider == "anthropic" and lane.auth_mode == "subscription_oauth",
  "claude_7d":     same predicate,
  "codex_weekly":  lane.provider == "openai",
}
```

A target is blocked when **any** pool whose predicate matches it is at/over its threshold —
usage-guard's "either window" semantics. The block reason names the pool:
`quota_wall(claude_7d, 96.4% >= 90.0, resets 2026-08-06T08:00:00Z)`.

Thresholds are per-pool and independently tunable: `RAWGENTIC_QUOTA_WALL_PCT_CLAUDE_5H`,
`…_CLAUDE_7D`, `…_CODEX_WEEKLY`, each defaulting to **90.0**, with
`RAWGENTIC_QUOTA_WALL_PCT` as the fallback default for all three.

**Why per-pool thresholds and not one number:** a 7-day window at 66% four days in is healthy,
while a 5-hour window at 66% is nearly spent. One threshold across windows of different length is
the wrong shape, and the owner's own numbers (5h at 19%, 7d at 66%) show the two moving
independently.

## Identifying a Claude-governed target

`provider == "anthropic" AND auth_mode == "subscription_oauth"`. Both independent reviewers reached
this predicate, including both rejections: `pool == "claude"` is an arbitrary concurrency label that
a rename would silently disable; `provider == "anthropic"` alone would wrongly block an `api_key`
lane, which bills an API account rather than the subscription windows.

## Account attribution (rev-2 F5, verified and corrected)

Rev 2 mapped a null `credential_ref` to a `"default"` sentinel. That is wrong:
`adapters/claude_cli.py:_claude_env` returns `None` when `credential_ref` is falsy, and its docstring
says *"env inherited unchanged"* — so the subprocess uses whatever ambient `CLAUDE_CONFIG_DIR` is
set, which is not a stable identity.

**Effective config dir**, resolved identically in the poller and the gate:
explicit `credential_ref` → else ambient `CLAUDE_CONFIG_DIR` → else the platform default
(`~/.claude`). Canonicalized (`os.path.realpath`, `~` expanded, symlinks resolved) and stored as a
**stable non-reversible hash**, so the record carries no path. A governed target with no matching
record fails open with the distinct reason `account_unattributed` — never another account's reading.

Codex pool records key on the effective `CODEX_HOME` by the same rule.

## The refusal must be auditable — and must not poison the audit (rev-2 F6/F7, both verified)

Rev 2 proposed a new `quota_guard_block` audit line. **`enforce.py:_validate_record` raises
`ValueError` on any unknown `kind`**, so that record would have poisoned `records()`, reconciliation,
and collection authorization for the entire run. Rev 2 also promised ordered skip reasons on the
Observation; **`observation-2.json` is `additionalProperties: false`** and its own description states
that such an addition bumps `schema_version`.

Rev 3 therefore:

- **Extends `enforce.py` properly** — adds kind `quota_block` with an explicit required-field set to
  `_validate_record`'s table and to the writer, rather than appending an unknown kind past a
  fail-closed validator. A test asserts `records()` and reconciliation stay healthy over a log
  containing one.
- **Drops the Observation-field promise entirely.** No schema version bump. The blocked-target list
  and per-pool reasons live on the `quota_block` record, which is where multi-target information
  belongs anyway — an Observation describes one attempt, and a total wall produces no attempt.

Record shape:

```json
{"kind": "quota_block", "correlation_id": "...", "seat": "analysis", "reason": "quota_wall",
 "pool": "claude_7d", "utilization": 96.4, "threshold": 90.0,
 "resets_at": "2026-08-06T08:00:00Z", "retry_after_epoch": 1754467200,
 "blocked_targets": [{"model": "...", "lane": {...}}], "source": "oauth_usage",
 "age_s": 42, "account_hash": "sha256:..."}
```

**AC1 honesty:** AC1 says the reason must be "in the receipt". No receipt exists on a total wall —
`enforce.check_pre` runs only per-attempt (`executor_routing_lib.py:853`; `:808` states no receipt is
minted before it), and a wall means no attempt happens. Rev 3 does not pretend an audit record is a
receipt. It satisfies AC1's *intent* (the refusal is durably attributable) and flags the wording as
needing amendment — called out in the PR body, not silently reinterpreted.

## Exit code, reason, and the handler boundary (rev-2 F6/F8)

Exit stays **3 / `EXIT_AVAILABILITY`** — retryable, transient, self-resolving.
Reason `quota_wall`, **qualified by pool**, so an operator (or the orchestrator) can route rather
than stall.

`except routing.ChainExhausted` currently exists at exactly ONE site (`:881`), while supervised
(`:2848`) and resume (`:2964`) have separate structures. The pass-2 review confirmed `_do_dispatch`
(`:3195`) owns all three branches, so `QuotaWallExhausted` handling goes at that shared boundary,
with a CLI test per branch asserting exit 3, the pool-qualified reason, and **no provider launch**.

> Ordering hazard: `except QuotaWallExhausted` must precede `except ChainExhausted`, or the subclass
> is swallowed and reported as `chain_exhausted`. A test pins the reason string.

`retry_after_epoch` (derived from `resets_at`) rides the structured error. The consumer contract is
stated **and wired**: the orchestrator's own retry seam must not re-dispatch a `quota_wall` before
that epoch. Where no compliant consumer exists yet, the field is still emitted — but the design does
not claim a behavior nothing implements.

## Source — poll, cache, read (owner-endorsed architecture)

`hooks/quota_usage.py` (NEW):

- `poll` — `GET https://api.anthropic.com/api/oauth/usage` with `Authorization: Bearer <token>`,
  `anthropic-beta: oauth-2025-04-20`, and `User-Agent: claude-code/<version>` (**the UA is
  load-bearing — without it the request lands in a strict 429 bucket**, per the issue). Writes the
  control file atomically: `umask 077`, `tempfile.mkstemp(dir=…)` → `os.replace`, refuses a symlinked
  path. `~/.claude` is mode 755, so the restrictive mode is required, and the token is **read at
  call time and never persisted**.
- `read` / `verify` — `verify` prints per-pool `active(<pct>%)` or `inactive(<reason>)` and exits
  non-zero when nothing is active; it is the documented acceptance step.
- Pure core (`parse_usage`, `evaluate_pools`, `resolve_thresholds`) with all I/O, clock, and HTTP
  injected — the repo's pure-core/thin-CLI rule.

Polling cadence is the caller's (a cron entry or the session-start hook); the gate only ever reads
the cache, so **a dispatch never blocks on a network call**.

**Staleness → fail OPEN with a stderr warning**, per pool independently: file missing/unreadable/not
JSON; unknown `schema_version`; `captured_at` older than `RAWGENTIC_QUOTA_CACHE_MAX_AGE_S`
(default **300**s); `captured_at` materially future-dated; `utilization` not finite in `[0, 100]`;
`resets_at` unparseable or past; no record for this account; **or the pool has no source**
(`codex_weekly` today).

Threshold parsing: unparseable/non-finite ⇒ default 90.0 + warning; numeric but outside `[1, 100]`
⇒ clamped + warning.

Fail-open is correct per the repo's fail-mode guide (a convenience/routing guard, not a security
boundary), and it is what makes the codex slot safe to ship unsourced.

## The silent-failure surface

A fail-open guard that never fires is invisible — the exact defect class epic #756 exists to kill.

1. `verify` names each pool's state and exits non-zero when none is active.
2. Every dispatch records `quota_guard: <pool>=active(<pct>%)|inactive(<reason>)` **per pool**, so a
   run is auditable for whether the guard was live rather than assumed.
3. `codex_weekly` reports `inactive(no_source)` — the deferred half announces itself on every
   dispatch instead of looking like coverage.

## Platform / external dependencies

platform_apis:
- api: `GET https://api.anthropic.com/api/oauth/usage` with headers `Authorization: Bearer <oauth token>`, `anthropic-beta: oauth-2025-04-20`, `User-Agent: claude-code/<version>`, returning `five_hour.utilization` / `seven_day.utilization` (float 0-100) and `resets_at` (ISO-8601 string)
  feasibility: verified via spike — the OWNER ran this exact request on THIS account on 2026-08-01 and recorded the verbatim response on issue #792 (comment IC_kwDORc-e6c8AAAABMvOFwA, mirrored at docs/measurements/2026-08-01-owner-notes-executor-economics.md:351): five_hour.utilization 19.0, seven_day.utilization 66.0, both with resets_at, and limit_dollars null on this plan — which is why the guard keys on utilization and never on dollars. This is the real invocation the design ships, not a proxy composition.
  failure: fail-loud
  surface: `hooks/quota_usage.py poll` exits non-zero and names the HTTP status on any non-200; `verify` exits non-zero when no pool is active; every dispatch logs per-pool `quota_guard:` state. Asserted by tests injecting a 401, a 429, a malformed body, and a body missing `five_hour`.
**No second platform API is declared, deliberately.** The `codex_weekly` pool ships with **no
source**, so no shipped code path calls any codex usage API — there is no platform dependency to
declare. Listing one would misrepresent an unbuilt integration as a taken dependency. The gap is a
**scope limitation** (§Scope, §Residual limitations), surfaced as `codex_weekly=inactive(no_source)`
on every dispatch, a non-zero `verify`, and test 15 — not a feasibility claim.

## Security implications

- The control file holds utilizations, ISO timestamps, a source name, and a **hashed** account key.
  No credentials, no paths.
- The OAuth token is read at poll time from the platform's own store and **never written** to the
  control file, a log, or an error message. Non-200 handling prints status only, never the body's
  auth-bearing headers.
- `umask 077`, atomic `mkstemp`→`os.replace`, symlink refusal on the control path (`~/.claude` is 755).
- Fixed canonical cache path; no config-supplied path interpolation ⇒ no traversal surface.
- The guard only ever **reduces** the target set, so it cannot escape `forbidden_combinations`
  (never-Haiku, cross-model author).

## Failure modes

| Failure | Behavior |
|---|---|
| control file absent / stale / future-dated / malformed | that pool fails open + warning + `inactive(<reason>)` |
| no record for this account | `inactive(account_unattributed)` — never another account's data |
| pool has no source (`codex_weekly`) | `inactive(no_source)`, never blocks |
| bad threshold env | default or clamp + warning |
| poll gets 401/429/5xx | `poll` exits non-zero, names status; cache untouched; gate keeps last good until stale |
| all targets over threshold | `QuotaWallExhausted` → exit 3, pool-qualified `quota_wall`, `quota_block` audit record |
| some blocked, another provider open | that target runs; skip reasons on the `quota_block` record |
| reconcile / read verbs | never gated — they call static `eligible_targets` |

## Residual limitations (stated, not discovered later)

1. **`codex_weekly` ships unsourced** — a real reduction against the owner's stated scope, announced
   on every dispatch and filed as a follow-up.
2. **Advisory, not a reservation.** A race between read and launch is unavoidable and concurrent
   processes can cross a threshold together. One read per dispatch, not per fallback attempt.
3. **Poll cadence is the operator's.** Between polls the gate uses cached data bounded by the 300s
   staleness rule; a window can move inside that bound.

## Multi-PR assessment

Single PR for the Claude half (~5 impl files, well under 500 lines). `Part of #792`.

## Tests (the evidence)

1. `build` at claude_5h 95% skips both anthropic entries and resolves **gpt-5.6-terra** — AC2 as amended.
2. `analysis` at 95% ⇒ exit 3, reason names **`claude_5h`**; fails if the `except` clauses are reordered.
3. The same on the **supervised** and **resume** branches — exit 3, no provider launch (rev-2 F6).
4. Reconcile with the guard forced to block everything resolves the **same** target — the ungated-path proof both reviewers named as the likely omission.
5. **`claude_7d` over threshold blocks while `claude_5h` is healthy** — the either-window semantics, and the case the owner's own 19%/66% numbers make live.
6. Per-pool thresholds honored independently; global fallback applies when a per-pool var is unset.
7. Missing / stale / future-dated / malformed / out-of-range / unattributed / no-source each fail open with a **distinguishable** reason — AC3.
8. Boundary 89.9 / 90.0 / 90.1; threshold `abc` ⇒ 90.0; threshold `0` ⇒ clamp to 1.
9. An `auth_mode: api_key` anthropic target is **not** blocked at 95%.
10. Account A's record does not block account B's target; ambient `CLAUDE_CONFIG_DIR` changes attribution (rev-2 F5).
11. `poll` parses the owner's **verbatim recorded response** into both pools — the round-trip against real captured bytes, not a hand-invented fixture.
12. `poll` on 401 / 429 / malformed / missing-`five_hour` exits non-zero, names the status, and leaves the cache untouched; the token never appears in output.
13. Writer is atomic (no stray `*.tmp`), mode 0600, refuses a symlinked path.
14. An audit log containing a `quota_block` record still passes `records()` and reconciliation (rev-2 F6 — the poisoning proof).
15. `codex_weekly` always reports `no_source` and never blocks.
16. `run_competitive` at 95% marks only the governed candidates non-ok and still runs an unguarded one.
