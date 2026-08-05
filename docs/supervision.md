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
- `governed_campaign_ids` — campaign names, charset-validated because #947 will join them
  into paths. An empty list governs every campaign.
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

**Known gap, deliberately left to #947:** if the state file is *deleted* between two hook
invocations, the next read sees a genuine absence and installs are permitted again.
Distinguishing "never declared" from "declared, then the record was removed" needs a durable
marker outside the file being protected, which is a design change beyond this issue. The
narrower races — a dangling symlink, and a delete between `stat` and `open` — ARE closed
above.

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

## What is NOT here yet (#947)

Deliberately absent, and gated off rather than half-built:

- texting the owner about a blocker and waiting a bounded time for a reply;
- consulting cross-model to break a tie while unsupervised;
- revision-bound action claims and `authority_permits`;
- the departure preflight that sweeps live campaigns for blocking decisions;
- any behavioural consumer of `campaign_wait` — writing it does not by itself halt a run.

Until #947 ships, a run that hits a blocker while the owner is away **parks for a human**
rather than deciding. That is more conservative than the owner's verbal away protocol, on
purpose: the protocol assumes the text actually arrives, and that has not been proven
end-to-end on this host.
