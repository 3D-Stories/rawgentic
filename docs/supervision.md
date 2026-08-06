# Supervision state — who is watching this session

Supervision is **declared**, never inferred. A session cannot tell whether a human is at
the keyboard, so it is told, and the answer lives in one workspace-level file that every
hook can read.

This replaces the bare unattended-session environment variable retired in #943 (the last
remnant of the headless orchestration deleted in #866). That variable could only say
present-or-absent: it could not distinguish the owner stepping out for twenty minutes from
the owner asleep until morning, and a *session* could not clear it, because a process cannot
un-export its parent's environment. It is named nowhere in the active tree now — the
retirement tripwire fails the suite if it reappears, which is how this very sentence got
reworded.

## The three commands

| Command | Meaning | Wake time |
|---|---|---|
| `/rawgentic:away [until]` | absent, still reachable by phone | optional |
| `/rawgentic:sleeping <wake time>` | unreachable until a stated time | **required** |
| `/rawgentic:back` | watching again | cleared |

`declare` deliberately **cannot** set `attended`: its revision fence is optional, while
`mark_attended`'s is mandatory, so allowing it there offered an unfenced way to clear a newer
absence.

`/rawgentic:back` is the **only** thing that lifts the unattended guards. That is
deliberate — see the expiry rule below.

## The state file

`<workspace-root>/claude_docs/.supervision.json`:

```json
{
  "schema_version": 1,
  "revision": 7,
  "state": "away",
  "until": "2026-08-05T22:30:00Z",
  "declared_at": "2026-08-05T20:10:00Z",
  "declared_by_session": "f0411833-...",
  "governed_campaign_ids": ["epic-871-m4-wave"],
  "consult_grant": {"providers": ["gpt"], "granted": true}
}
```

- `state` — `attended | away | sleeping`, as declared. `attended-overdue` is an
  *evaluated* state and is never written.
- `until` — ISO-8601 UTC or `null`. Required for `sleeping`; must be `null` for
  `attended`; **rejected if already in the past**, since that would declare an
  instantly-expired absence.
- `revision` — monotonic. It is the optimistic-concurrency token (`--expected-revision`)
  and the absence-window identifier a later report can cite.
- `governed_campaign_ids` — campaign names, charset-validated (the same
  `validate_campaign_id` #947 Part B reuses for claims/preflight/driver-state paths).
  An empty list governs every campaign.
- `consult_grant` — permission for a cross-model consult to send repo text off this host.
  Default un-granted, so silence never authorizes egress.

## Two predicates, and why not one flag

The two consumers need **opposite** safe defaults, so there are two predicates rather than
one `is_watched` boolean:

```bash
python3 hooks/supervision_lib.py nobody-to-ask      --workspace <root>   # 0 = nobody
python3 hooks/supervision_lib.py installs-forbidden --workspace <root>   # 0 = FORBIDDEN
```

- **`nobody-to-ask`** decides whether a context-pressure nag says "checkpoint and hand
  over" or "tell me". Consumer: `hooks/context_meter.py`. An expired declaration
  **relaxes** it: past a stated return time, assume the owner is back, because the only
  consequence is which advice gets printed.
- **`installs-forbidden`** decides whether missing security scanners may be
  auto-installed. Consumers: `hooks/scanner_bootstrap.py`, the `setup` skill's Step 2e. An
  expired declaration does **not** relax it. Installing packages is an outward act, and a
  clock passing a timestamp is not evidence anybody came back.

A single flag would have to pick one of those defaults and would silently be wrong for the
other consumer. That is the whole reason for the asymmetry, and a test pins it: an expired
`away` declaration yields `nobody_to_ask` **False** and `installs_forbidden` **True`.

## Absent, valid, invalid

`read_state` classifies what it found, and invalidity is **not** absence:

| Condition | `load_status` | `nobody-to-ask` | `installs-forbidden` |
|---|---|---|---|
| no workspace root supplied | `absent` | allowed | allowed |
| root supplied but unresolvable | **`invalid`** | allowed | **FORBIDDEN** |
| file missing under a valid root | `absent` | allowed | allowed |
| valid, `attended` | `valid` | allowed | allowed |
| valid, `away` / `sleeping` | `valid` | **nobody** | **FORBIDDEN** |
| valid, expired (`attended-overdue`) | `valid` | allowed | **FORBIDDEN** |
| unreadable / oversized / malformed / off-vocabulary | `invalid` | allowed | **FORBIDDEN** |
| not valid UTF-8 | `invalid` | allowed | **FORBIDDEN** |
| a FIFO, device or directory at the path | `invalid` | allowed | **FORBIDDEN** |
| a dangling symlink | `invalid` | allowed | **FORBIDDEN** |
| present but missing declared schema fields | `invalid` | allowed | **FORBIDDEN** |
| vanished between `stat` and `open` | `invalid` | allowed | **FORBIDDEN** |
| workspace root is not a path string | `invalid` | allowed | **FORBIDDEN** |

Two lines carry the safety property:

1. **`ENOENT` under a valid root is the only *file* failure treated as absence.** A corrupt
   file must not silently drop the guard — which is precisely what would happen if the file
   were damaged *during* an away window.
2. **A supplied-but-unresolvable root is invalid, not absent.** Otherwise a path-resolution
   or caller-misconfiguration bug would ALLOW installs while the real workspace held an
   active away declaration, inverting the fail-safe property via a config error.

Non-regular files are refused **before** opening, because `open()` on a FIFO with no writer
blocks — and this read rides a hook that fires on every tool call, so one bad filesystem
entry would hang the session rather than degrade it.

**Known gap, still open (#947 Part B did not address it):** if the state file is *deleted*
between two hook invocations, the next read sees a genuine absence and installs are
permitted again. Distinguishing "never declared" from "declared, then the record was
removed" needs a durable marker outside the file being protected, which is a design change
beyond either #943 or #947 — no issue currently owns it. The narrower races — a dangling
symlink, and a delete between `stat` and `open` — ARE closed above.

## Two modules, and why they are separate

- `hooks/supervision_lib.py` — read + pure. **Standard library imports only**, enforced by
  a test. `hooks/context_meter.py` consumes it on a hook that runs on *every tool call*
  and imports stdlib only; `plan_lib` (home of `file_lock`) is large, so pulling it in to
  answer "is anyone watching" would tax every call.
- `hooks/supervision_admin.py` — write. Free to import `plan_lib` and `atomic_write_lib`.
  Holds `plan_lib.file_lock` across the whole read-validate-increment-write cycle, then
  lands via `atomic_write_text(..., fsync=True)`.

Fail modes are deliberately opposite: **READ is fail-open for availability** (a broken file
must never wedge a per-tool-call hook) but **fail-safe for authority** (a broken file never
unlocks an outward action). **WRITE is fail-loud** — a silently-failed declaration would
leave a session believing the owner is recorded as away when nothing landed.

## Terminal-for-now campaign state

`waiting_for_owner` and `waiting_for_reset` are **campaign**-level, recorded in an additive
top-level `campaign_wait` object in driver-state — not as values of the per-issue `status`,
which is a closed vocabulary enforced in two places. `clears_when` is required: a pause
whose exit condition nobody can state is a stall wearing a pause's clothes.
`plan_lib.build_goal_text`'s campaign variant references both states so a Stop-hook goal
loop reads an honest pause instead of nagging one.

## What #947 (Part B) shipped

`hooks/supervision_route.py` — the campaign-scoped decision layer, gated behind a single
`CampaignView`, itself only ever produced by `evaluate_campaign` (no combination of
inputs can pair one campaign's view with a foreign campaign's grant, override, or
workspace state):

- `route_for` — the blocker-routing decision (owner-only exemption, sleeping decides
  immediately, away gates on `transport_verified` and a typed `AskAttempt`);
- `authority_permits` — bounded autonomous authority; `merge` is the only action_kind
  absence can ever permit, and only under a grant an override hasn't denied;
- `consult_permitted` — the outward-egress gate for a cross-model consult while
  unsupervised, wired into this repo's three existing `review_runner.py consult` call
  sites via `consult_check` (implement-feature Step 3, peer-consult/WF13);
- `hooks/supervision_claims.py` — revision-bound action claims, execute-once via
  reconcile-before-retry (no idempotency key exists for a GitHub merge);
- `hooks/supervision_preflight.py` + `supervision_admin.declare(preflight_token=...)` —
  the departure preflight, now wired into `/rawgentic:away` and `/rawgentic:sleeping`
  before their existing `declare` call, and `/rawgentic:back` now cancels every pending
  claim on return;
- `supervision_admin.mark_transport_verified` — owner-attended, Hermes-cross-checked
  transport verification, the trust signal `route_for` reads before it will ever wait
  on an ask;
- `driver_lib.set_supervision_override` — the ONE writer of the tighten-only
  `supervision_override` field (a per-campaign restriction stacked on top of this
  workspace-global state);
- `tests/test_askuserquestion_registration.py` — a repo-wide guard that a new
  `AskUserQuestion` site in `skills/**/*.md` names its own routing.

Deliberately still NOT here, per the design's own scope boundary (§1a): wiring
`authority_permits`/the claims lifecycle into the epic-run driver's OWN merge step
(`hooks/launcher_lib.py`, WF2 Step 14) — that executable broker belongs to a future
issue, so this shipped the gate, not a retrofit of every action call site in the repo.
Until that broker exists, a run that hits a blocker still **parks for a human** on that
one call site rather than deciding, even though the decision layer above can now answer
the question correctly if asked.
