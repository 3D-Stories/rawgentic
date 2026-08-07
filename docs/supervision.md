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

## The supervised merge (#963)

`python3 hooks/launcher_lib.py broker-merge --pr <n> --issue <n> --campaign <id>` is the
executable broker the section above was waiting for — the ONE live caller of the
authority core, and what makes a declared absence actually gate a merge instead of
merely describing one. WF2 Step 14 and the epic-run boundary invoke it whenever a
campaign is active (guard-tested prose); a non-campaign merge still runs the raw command.

It binds authorization to the target before reading authority (the repo must be the
project's own, the issue a child of the campaign, the PR must reference the issue), mints
an execute-once claim, re-checks that nothing moved, merges, and confirms the SHA by
probing the PR — `gh pr merge` exiting 0 is never treated as evidence on its own. rc `0`
merged, `12` refused with nothing merged, `13` parked for a human. Re-running the
identical command is always safe: executed is terminal, executing or parked reconciles
from real evidence, pending continues.

**The grant it reads must be written.** During an absence a merge is permitted only when
the campaign's driver state carries `policy.merge_policy = "auto-merge-scoped-to-run"`
and no tightening override denies it. The epic-run Step-2 answer records that key —
before #963 the gate read a field no prose produced.

**Decision telemetry.** Every authority decision and claim transition appends one line to
`<workspace>/claude_docs/supervision-telemetry.jsonl`, so whether this machinery is
reached is answered by data rather than by reading code — the question that killed the
executor (D174) and that #871 could not answer for its own core.

**One-time migration.** A workspace that declared BEFORE #963 shipped has no declaration
marker. Run `python3 hooks/supervision_admin.py bootstrap-marker --workspace <root>` once
after upgrading; `broker-merge` and every `declare`/`mark_attended` also self-heal it.

## The merge guard (#976)

`hooks/campaign-merge-guard.py` is the executable half of the sentence above. It is a
`PreToolUse` hook on the `Bash` matcher: when a command is a raw `gh pr merge` whose PR is
a child of an **active** campaign, the hook refuses it and names the `broker-merge` command
to run instead. Before #976, what routed a campaign merge into the broker was prose pinned
by guard tests, so a prose-violating session reached the raw command and skipped authority
evaluation, target binding, the execute-once claim and the decision telemetry in one step.

**"Active" is read from durable state, never session context.** A campaign file under
`claude_docs/.driver-state/` is active when at least one entry in its top-level `issues[]`
has a status outside `{merged, deferred, abandoned}`. Binding is on `{repo, pr}`: an
explicit `--repo` that is not this project's own repo is allowed through, because PR
numbers are repository-scoped.

**The fail mode is split at the classification boundary**, and the split is the whole
design (decision D186):

| Path | Mode | Why |
|---|---|---|
| stdin unparseable, no command field, startup exception | **ALLOW**, with a stderr diagnostic | this hook runs on every Bash call; a bug here would otherwise deny `ls` and `pytest` in every project |
| no `.rawgentic.json` above cwd, or no `.driver-state/` | **ALLOW** | absence, not failure — the same rule this document states for supervision state, that `ENOENT` under a valid root is the only file failure treated as absence |
| classified as a raw `gh pr merge`, but campaign state is corrupt, oversized or unreadable | **DENY** | the blast radius is exactly one refused raw command, and `broker-merge` is not a `gh pr merge` command line, so the sanctioned path stays open |

The two existing `PreToolUse` siblings are deliberately opposite — `wal-guard` fails closed
(`wal-guard:14-17`), `security-guard.py` fails open (`security-guard.py:6`). This hook is
both, because the two are right about different paths.

**What it does not do, stated plainly (decision D187).** It stops an *accidental* raw
merge — a session that drifted from the prose. It does **not** stop a deliberate bypass:
`PreToolUse` fires per Claude Code tool call, not per OS process, so
`python3 -c "subprocess.run(['gh','pr','merge',…])"` is invisible to it. That same property
is what makes the broker's own internal merge pass untouched with no spoofable signal —
the distinction is a process boundary, not a token nobody may forge. Closing the deliberate
bypass would need the merge credential to live somewhere a session cannot reach, or
GitHub-side merge-queue permissions; both are larger changes than this guard, and neither
is shipped. The threat model here is caller confusion and prose drift, which is the same
one the broker states for its own target binding (`launcher_lib.py:5300-5303`).

The guard-tested prose in WF2 Step 14 and the epic-run boundary stays exactly as it was:
the hook hardens that route, it does not replace it.
