# #927 — Epic-run transport rework: probed transport, per-transition effect, one successor

**Date:** 2026-08-05 (§15–§17 added 2026-08-06 for PR 2) · **Issue:** #927 (epic #871, M4 wave) ·
**Status:** PR 1 MERGED (`19925711`, v3.132.0 — machinery, no caller); PR 2 designed in §16
**Authority:** #927's M4 rescope comment ([issuecomment-5195655685]) and its anchor correction
([issuecomment-5198313987]); wave design
`docs/planning/2026-08-05-871-m4-session-continuity-away-mode.md` §3.2. The issue BODY is stale
where it disagrees.
**Base:** `e759ee04` · **Baseline:** 5488 passed, `pytest tests/ -q` exit 0

[issuecomment-5195655685]: https://github.com/3D-Stories/rawgentic/issues/927#issuecomment-5195655685
[issuecomment-5198313987]: https://github.com/3D-Stories/rawgentic/issues/927#issuecomment-5198313987

> **Pass 3.** Pass 1 drew 7 High (rewrite; §13). Pass 2 drew 7 more High — 6 adversarial + 1
> self — so the gate looped back again (`design` source, counters now **`design=2` at cap,
> `total=2` of 3**). Dispositions in §14.
>
> **Pass 2's central refutation, and why pass 3 is smaller than pass 2.** Pass 2 invented a
> `launch_token` for reconciliation that `herdr api snapshot` was never shown to expose (P4), and
> left successor CREATION undeclared in `platform_apis` (P5). Both were symptoms of one mistake:
> designing an exactly-once protocol on a launch path I had not spiked, when the repo already had
> a hardened one. Pass 3 DELETES the invented token and reuses `_pane_inventory` +
> `_extract_pane_id` + the `#611` ownership check (§4.4). Reuse over invention — the ladder's rule,
> applied late.
>
> **The design source is now at cap.** A further volume loop-back cannot buy another design pass;
> per #798 the gate would close budget-exhausted over resolved ground, or escalate if anything is
> ambiguous or global-capped.

## 1. What is actually wrong

An epic-run campaign records `session_mode` — `fresh-session` or `single-session`, defaulting to
single — by hand, once, at creation. Three defects follow:

1. **The default fights the owner's standing intent.** Measured on epic #875: after a child
   merged, the run continued in a session that had already spent 12 commits, four review passes,
   three full-suite runs and a merge. No trigger fired, and none was due.
2. **`single-session` has no sanctioned boundary handoff at all.** The boundary command is gated
   on `fresh-session`; `mid-child-handoff` refuses at a boundary (`no_active_child`); ad-hoc
   `pane-handoff` excludes mid-campaign epic runs by its own text.
3. **The recorded answer is permanent, the capability is not.** herdr can be present at creation
   and absent at boundary 4.

**The surface is far smaller than the issue implies.** `session_mode` has exactly TWO production
reads — `driver_lib.py:2026` (`fresh_session_available`) and `launcher_lib.py:4286`
(`_cmd_handoff`) — and **nothing in the code writes it**. That is what makes this affordable.

## 2. What already exists (measured at `e759ee04`)

- **No herdr probe.** `launcher_lib.py:1565` `herdr_available()` is `shutil.which("herdr")`, and
  it is not wired to `select-mode`, which takes a caller-asserted `--herdr-available` flag
  (`:5031`, consumed `:5082`). Neither hook module reads `HERDR_ENV`.
- **A one-successor fence exists — on the mid-child path only.** `open_handoff` (`:1950`) bumps
  `generation` and writes `handoff_pending`; `handoff_claim` (`:2130`) compares the pending queue
  payload to the revalidated payload; `handoff_ack_started` (`:2205`); `handoff_reclaimable`
  (`:2036`, 1800 s lease). `_cmd_handoff` has none of it — that IS #845.
- **A pane-id validator already exists:** `launcher_lib.py:306` `validate_pane_id`. Reuse it.
- **A pane-id-free herdr call already exists:** `pane_watch_lib.read_snapshot` runs
  `herdr api snapshot` and raises `WatchError` on non-zero. Precedent for §4.1's tier 1.
- **The pane-reference problem is already documented in-repo:** `launcher_lib.py:6-10` records
  that a cron-spawned, pane-less session dies with `{"error":{"code":"no_current_pane"}}`.
- Schema `docs/driver-state/queue.schema.json` has `additionalProperties: true` at both levels;
  `session_mode` is undeclared; `validate_driver_state` (`:2370`) has no unknown-key branch.

## 3. Approaches considered

### A — `preferred_transport` durable, effect recorded as an append-only TWO-EVENT log *(SELECTED)*

`preferred_transport` is top-level and durable. The effect is never a mutable field. Each
transition appends **two immutable events**:

```
resolution      {resolution_id, transition_id, generation, trigger, kind,
                 preferred_snapshot, effective, probe_reason, probe_ms,
                 pane_ref, successor_pane, observed_at}
terminal_outcome{resolution_id, transition_id, claim_attempt, outcome, observed_at}
```

`resolution_id` is `<transition_id>#<attempt>` and is the ONLY correlation key — a terminal
outcome closes exactly the resolution naming it. (Pass 2 caught that the terminal event referenced
a field the resolution did not carry; §14 P6.)

`successor_pane` is written by an **amendment** to the resolution the moment `pane split` returns
an ownership-verified new pane id, BEFORE `agent start` is attempted. That is the one permitted
in-place write, and it is confined to a field that is `null` until the split returns — see §4.4,
where it is what makes reconciliation possible at all.

Effort: M. Risk: low.

**Why two events, not one.** A single record carrying `outcome` must be written before the action
that determines the outcome — so it would have to be invented early or mutated later, and an
"append-only" record you mutate is neither. Splitting them keeps both immutable: the resolution is
appended before acting, the terminal outcome after the action is classified. **A resolution with
no terminal event is the crash signature**, and §4.4 defines recovery from exactly that.

### B — both fields top-level, `effective_transport` overwritten each boundary

Effort: S. Risk: medium-high. A value from boundary 3 sits in the file during boundary 4 and every
reader must remember it is stale — the same defect `session_mode` already causes, at a new name.
Rejected.

### C — never persist the effect; recompute on demand

Effort: S. Risk: medium. No stale value, but no audit trail and nothing for the
visible-degradation requirement to attach to. Rejected.

## 4. The design

### 4.1 The probe — two tiers, because one of them needs no pane id

The reviewer's strongest point (§13 A3) was that a probe requiring `HERDR_PANE_ID` fails closed at
exactly the two moments that variable is least likely to exist — campaign creation and resume —
turning a healthy herdr into a permanent-feeling `inline`. Resolved by splitting the probe:

**Tier 1 — capability (always available, needs NO pane reference).**
`herdr api snapshot`, bounded, 5 s.

**Tier 2 — pane liveness (only when a validated pane reference exists).**
`herdr pane get <pane_ref>`, bounded, 5 s.

**The probe returns the two tiers SEPARATELY** — `(capability_ok, pane_ok, reason)` — never one
collapsed verdict. Creation consumes `capability_ok` alone; a boundary requires
`capability_ok and pane_ok`. Collapsing them was a self-review finding on the pass-2 draft (§14
S8): with one verdict, "no pane reference" returned `inline`, so campaign creation — which needs
no pane reference — would have recorded `inline` and silently re-broken AC 1, the very defect
pass-2 finding A4 had just fixed.

| condition | verdict | reason |
|---|---|---|
| tier 1 rc 0, parses, carries `result.snapshot` | `capability_ok = True`; continue to tier 2 | — |
| tier 1 non-zero / timeout / unparseable / `FileNotFoundError` | `inline` | `herdr_unreachable` / `probe_timeout` / `probe_unparseable` / `herdr_absent` |
| no pane reference available | `inline` | `no_pane_ref` — **capability proven, pane unknown**; tier 2 is skipped, not failed |
| tier 2 rc 0, parses, `result.pane.pane_id` == requested, `workspace_id` == `pane_id.split(":")[0]` | `pane_chain` | `probe_ok` |
| tier 2 rc 0 but identity mismatch | `inline` | `probe_identity_mismatch` |
| tier 2 rc 1 | `inline` | `pane_not_found` |
| tier 2 rc 2 | `inline` | `probe_usage_error` — our bug, logged loudly |

**Pane reference provenance, resolved (no longer deferred).** It is CALLER-SUPPLIED, exactly as
the two existing CLI flags document it (`launcher_lib.py:4874`, `:5052` — "the caller's own
`$HERDR_PANE_ID`"). There is no internal current-pane resolver, and `--current` is precisely what
fails pane-less (`launcher_lib.py:6-10`). So: the reference is an explicit parameter, validated
with the existing `validate_pane_id` (`:306`); `HERDR_PANE_ID` is only the default the CLI reads.
**Absence degrades tier 2 alone** — it can never make tier 1 look like a herdr failure.

**Bounded I/O, not a post-hoc length check.** stdout and stderr are read through bounded readers
that terminate and reap the child the moment either exceeds 64 KiB. A `len(stdout)` test after
capture would buffer the unbounded data first and prevent nothing (§13 A8).

**Never echo probe output.** The advisory and the transition record carry only a fixed reason
token from the table above. Raw stdout/stderr is never printed to an operator's terminal and never
interpolated into a generated prompt.

**Argv is a list, never a shell string.** `HERDR_ENV`/`HERDR_PANE_ID` are a hint that supplies a
candidate id, never proof — the round-trip is the proof, which is the rescope's "never `HERDR_ENV`
alone".

**Timeout 5 s — a recorded disagreement with the peer** (which proposed 2.0 s). A false negative
costs one visible, self-healing `inline` transition; on a host running several agent panes and a
test suite at once, 2 s risks systematic spurious degradation, which is the failure this issue
exists to end. `probe_ms` is recorded on every resolution so the value is tuned on data.

### 4.2 Transport resolution, creation, and migration

`campaign_transport(state) -> (preferred, provenance)`, pure:

| state | result | provenance |
|---|---|---|
| `preferred_transport` present | that value | `recorded` |
| absent, legacy `session_mode == "fresh-session"` | `pane_chain` | `migrated` |
| absent, legacy `session_mode == "single-session"` | `inline` | `migrated` |
| absent, legacy value unrecognized | `inline` + **loud diagnostic** | `unrecognized` |
| neither present | `inline` | `legacy_default` |

**The creation contract, stated explicitly (§13 A4 — its absence would have failed AC 1).**
A NEW campaign does not fall through to the table above. Creation runs the probe and writes
`preferred_transport = pane_chain` when tier 1 succeeds, `inline` otherwise. The `inline` row
marked `legacy_default` applies ONLY to a pre-existing campaign carrying neither field — never to
a new one. Without this, a healthy new campaign would inherit `inline` and preserve exactly the
default this issue exists to invert.

A successful probe **never upgrades** an explicit `inline` preference. Evidence is recorded; the
preference is obeyed.

**Migration materializes on the next locked state mutation, never during a read**, so no read path
writes.

**Rollback safety — one enforced chokepoint (§13 A6).** `session_mode` becomes a **write-only
compatibility projection** derived from `preferred_transport`. It is enforced in
`_locked_state_update` (`launcher_lib.py:2914`) — the single locked write path — rather than by a
convention every future writer must remember. A test enumerates the production mutation entry
points and asserts the canonical field and its projection stay synchronized. New code never
consults the projection when the canonical field exists; on disagreement the canonical field wins
and the disagreement is reported. Removed in a later cleanup issue.

**Schema.** The new fields are additive and need no `schema_version` bump, but they ARE declared
in `docs/driver-state/queue.schema.json` rather than riding `additionalProperties: true`
undocumented. Precedent: `campaign_wait`.

### 4.3 Transition lifecycle — all four triggers

Pass 1 named four triggers and specified one (§13 A1). All four:

| trigger | caller | transition_id | generation | lock / claim | probe | action | terminal outcome |
|---|---|---|---|---|---|---|---|
| `creation` | campaign setup | `c:<campaign>:0` | not bumped (none exists) | locked write, no claim | tier 1 only | write `preferred_transport` | `created` |
| `child_boundary` | `_cmd_handoff` | `b:<campaign>:<gen>` | bumped by `open_handoff`, **idempotent** — a competing call gets the SAME generation | claim required | tiers 1+2, **after** the claim | launch successor, or continue inline | `successor_acked` / `inline_continued` / `launch_indeterminate` |
| `mid_child_resume` | `_cmd_mid_child_handoff` | `m:<campaign>:<gen>` | existing mid-child behavior, unchanged | existing claim, unchanged | tiers 1+2 after claim | unchanged | unchanged |
| `boundary_resume` | resume path reclaiming an expired lease | `r:<campaign>:<gen>:<attempt>` | NOT bumped — reuses the reclaimed generation | reclaim under the 1800 s lease | tiers 1+2 after reclaim | **only after reconciliation** (§4.4) | `successor_acked` / `reconciled_no_action` / **`parked_unreconcilable`** |

`parked_unreconcilable` is a real terminal value, not a narrative state (§14 P3): an intentional
park must be distinguishable from the crash signature of a resolution with no terminal event, or
automated reclaim cannot tell "a human must look at this" from "recover me". A parked transition
is never auto-reclaimed.

**Terminal vs reclaimable, disambiguated (§15 C5).** `launch_indeterminate` was listed as a
terminal outcome while other sections required the same transition to stay reclaimable — an
implementation treating terminals as closed would strand it forever. It is therefore **not** a
terminal outcome. The closed set is exactly:

```
successor_acked · inline_continued · launch_failed · start_failed
reconciled_no_action · created · parked_unreconcilable
```

An indeterminate launch is the ABSENCE of a terminal event on a resolution with
`split_attempted: true` — the crash signature §4.4 reconciles. There is no `launch_indeterminate`
event.

**Unparking is a command, not manual state editing (§15 C2).**
`transport unpark <resolution_id> [--adopt <pane>|--discard]`, under the driver-state lock:
it refuses unless that resolution's terminal outcome is `parked_unreconcilable`; it requires the
operator to say whether the surviving pane is adopted or discarded; it appends a new
`terminal_outcome` (`reconciled_no_action` or `successor_acked`) rather than rewriting the parked
one; and it records who, when and why. Without it, "only an operator clears it" would mean
hand-editing driver-state — which this design otherwise forbids.

**Claim before probe, never the reverse.** Only the holder of a valid claim probes, so
exactly-one-successor does not depend on the probe being deterministic.

**The exact durable ordering** (pass 2 found the earlier text contradicted itself about whether the
resolution lands before or after the launch — §14 P8):

```
1 claim            (locked)
2 probe            (tiers per §4.1)
3 append resolution   successor_pane = null      <- lands BEFORE any launch
4 act:  pane split  ->  amend resolution.successor_pane = <owned id>   <- the residual window
        agent start ->  ack
5 append terminal_outcome
```

The resolution ALWAYS precedes the launch. That is what lets recovery distinguish "never started"
(`successor_pane` null) from "started, ack lost" (`successor_pane` set).

**Recovery for a resolution with no terminal event, per trigger** (§14 P2 — pass 2 correctly noted
only the boundary case was defined):

| trigger | crash signature | recovery |
|---|---|---|
| `creation` | resolution, no terminal | idempotent: re-probe and rewrite `preferred_transport`; no external side effect exists to reconcile |
| `child_boundary` | resolution, no terminal | §4.4 reconciliation on `successor_pane` |
| `inline continuation` | resolution with `effective=inline`, no terminal | idempotent: nothing was launched; append `inline_continued` and proceed |
| `mid_child_resume` | unchanged | existing #665 behavior, untouched |
| `boundary_resume` | resolution, no terminal | re-reconcile against the ORIGINAL boundary resolution; never against its own |

**AC 3 — the launcher still defers to the recorded answer.** `_cmd_handoff`
(`launcher_lib.py:4286`) reads the campaign's own preference through `campaign_transport` and never
forces a mode. #611 Step-11 pass-3 High 2 reverted a revision that overrode it; that must not
return. Pinned by a test (§9). *(Restored — the pass-1→2 rewrite dropped this from the body and
left it only in the test strategy.)*

**The probe runs even under an `inline` preference**, so every transition carries fresh capability
evidence — recorded, never acted on to upgrade.

**Advisory.** When a transition resolves `inline`, emit exactly one line naming the fixed reason
token: `### epic-run: transport=inline preferred=<p> reason=<token> — re-probing next transition`.
Suppression is keyed on `transition_id` (not `generation`), because `creation` and
`boundary_resume` do not bump a generation and would otherwise share a key. Advisory only: it
never blocks and never changes an exit code.

**If the advisory itself fails to emit, that is recorded, not swallowed** (§14 P7). Silently
catching the output error would contradict §5's promise that every non-success verdict is visible.
So the resolution event carries `advisory_emitted: true|false`, and a `false` is what the
degradation-visibility test in §9 asserts against — the durable record is the backstop surface when
the terminal one fails.

### 4.4 The child-boundary fence, and what happens when a launch fails

Reuse the mid-child machinery, changing only the **precondition**:

| | mid-child (`mid_child`) | child boundary (`child_boundary`, NEW) |
|---|---|---|
| precondition | exactly one `in_progress` child matching `position` | next child `queued`, none `in_progress` |
| generation, claim, ack, lease, payload equality | — | identical, reused unchanged |

`MID_CHILD_HANDOFF_KIND` (`driver_lib.py:2228`) and `_refuse_foreign_kind`
(`launcher_lib.py:4245`) already discriminate, so adding `CHILD_BOUNDARY_HANDOFF_KIND` keeps the
paths separate by construction. **The mid-child path is not modified** — its existing tests passing
unchanged is the regression signal.

**Launch-failure classification, rebuilt on machinery that already exists.** Pass 2 (§14 P4)
correctly refuted the pass-2 design: it invented a `launch_token` that `herdr api snapshot` was
never shown to expose. **There is no token.** The repo already identifies a successor pane, and by
a mechanism hardened by this same review lineage:

| step | existing code |
|---|---|
| inventory panes before splitting | `_pane_inventory(runner)` — `launcher_lib.py:1997` |
| strict-parse the split response | `_extract_pane_id` — `launcher_lib.py:3136` (returns None rather than guessing) |
| **prove ownership** of the returned id | `new_pane != anchor_pane and new_pane not in panes_before` — `launcher_lib.py:2027`, added by **#611 Step-11 pass-5 High 1** |
| report a possible orphan | `_report_possible_orphan(panes_before, runner, anchor_pane)` — `launcher_lib.py:2740` |

So the design records the ownership-verified `new_pane` into the resolution (§3) the instant the
split returns, and reconciliation asks one proven question: **is that recorded pane id still in a
fresh inventory?**

| classification | evidence | behavior |
|---|---|---|
| **definite failure** | split returned non-zero, OR `_extract_pane_id` returned None, OR the ownership check rejected the id — in every case `successor_pane` is still `null` | append `launch_failed`; retryable |
| **indeterminate** | the split call timed out or was killed, so `successor_pane` may or may not have been set | append `launch_indeterminate`; **do NOT continue inline, do NOT relaunch** |
| **started, ack lost** | `successor_pane` is set but no ack arrived | append `launch_indeterminate`; reconciliation below decides |

**`null` must never be read as "nothing was created" (§15 C1 — CRITICAL, and it was against my own
text).** Pass 3's earlier draft accepted a crash between `pane split` returning and the amendment
landing, and let reconciliation treat `successor_pane: null` as proof no successor existed —
authorizing a relaunch beside a live pane. That contradicts the property this fence exists to
provide, so the ambiguity is removed rather than documented:

The resolution records **`split_attempted` and `panes_before`** *before* the split is called.
`panes_before` is already captured by `_pane_inventory` (`launcher_lib.py:1997`) — it is written
down, not newly computed. That makes `null` unambiguous:

| `split_attempted` | `successor_pane` | meaning | action |
|---|---|---|---|
| `false` | `null` | the split was never called — proven, because the marker lands first | relaunch permitted |
| `true` | `null` | **indeterminate**: a pane may exist | reconcile by INVENTORY DIFF (below); never relaunch on the `null` alone |
| `true` | set | a pane was created and owned | reconcile on that id |

**Reconciliation at lease expiry.** The reclaimer reads the ORIGINAL boundary resolution
(`b:<campaign>:<gen>#<attempt>`), never its own `r:` id (§14 P1):

1. Take a fresh inventory. Unreadable (`_pane_inventory` → `None`) ⇒ **refuse to relaunch**, append
   `parked_unreconcilable`.
2. **Inventory diff:** `fresh − panes_before − {anchor}` is the set of panes this transition could
   have created. Non-empty ⇒ a successor (or an orphan) exists ⇒ **never relaunch**; adopt or park.
   Empty AND the inventory is fresh ⇒ proven nothing survives ⇒ relaunch permitted.
3. **A pane is not a running agent (§15 C3).** `pane split` succeeding does not mean
   `agent start` did; an empty pane would otherwise be acked as a live successor and stall the
   campaign forever. The snapshot distinguishes them — my spike showed `result.snapshot.agents[]`
   entries carry their own `pane_id`, so "a pane with no agent entry" is directly observable.
   Pane present + agent present + ack ⇒ `successor_acked`. Pane present + NO agent entry ⇒
   `start_failed`: retire that empty pane, then relaunch is permitted (nothing is running in it).
   Pane present + agent present + no ack ⇒ park; a live agent is never displaced.

**The honest limit, restated correctly.** The design no longer permits a misclassification. What
remains is narrower and is a LIVENESS cost, never a safety one: if the inventory diff is non-empty
but the pane turns out to be unrelated debris, the run parks instead of relaunching. Parking
requires an operator; two successors do not announce themselves. The trade is deliberate, and
§4.3's unpark command is what bounds it.

### 4.5 Setup, the mode-change command, and generated prompts

- **Step 2 asks exactly two questions** — merge policy, and arming the resume launcher. The
  session-mode question dies (`skills/epic-run/SKILL.md:48-56`). Transport is derived per §4.2.
- **`transport set pane_chain|inline` (AC 2 — pass 1 omitted this entirely, §13 S1).** Under the
  driver-state lock, refused when a child is `in_progress` OR a `handoff_claim` is active (that is
  the `mid-child-handoff` case, not this one), with a distinct exit code. Otherwise it updates
  `preferred_transport` and its projection immediately; the NEXT boundary still probes. It records
  who/when/reason as audit metadata and introduces no pending-override state.
- **Generated successor prompts.** `resume_prompt_for_state` (`launcher_lib.py:3961`) already
  builds one and forces the mode at `:3995`. It becomes the generator: campaign identity,
  generation, claim id, boundary kind, next issue, **`resolution_id`**, and the task-list-rebuild
  instruction. The successor RELOADS driver-state rather than trusting an embedded snapshot. No
  hand-authored prompt is accepted on this path, and no probe output or issue-body text is
  interpolated into it.
  *(§15 C6: this said `launch_token`, a field pass 3 had already deleted — an implementer would
  have had to resurrect the rejected protocol or invent an undefined field. `resolution_id` is the
  sole correlation key, per §3.)*

## 5. Platform / external dependencies

```
platform_apis:
- api: `herdr api snapshot` (herdr CLI 0.8.0) — tier-1 capability probe, needs no pane id
  feasibility: verified via spike — run on this host 2026-08-05, the exact shipped invocation:
    rc 0, 10418 bytes, JSON carrying result.snapshot.agents[0].pane_id == "w1:pKS" and
    workspace_id == "w1". In-repo precedent: pane_watch_lib.read_snapshot runs the same argv.
  failure: fail-loud
  surface: non-zero rc / timeout observed directly; every non-success verdict is recorded on the
    resolution event AND printed as the §4.3 advisory line.
- api: `herdr pane get <pane_id>` (herdr CLI 0.8.0) — tier-2 pane-liveness probe
  feasibility: verified via spike — same host, same day, exact shipped invocation:
    `herdr pane get w1:pKS` rc 0 returning result.pane.pane_id == "w1:pKS" AND
    result.pane.workspace_id == "w1"; `pane get` with no arg rc 2; `pane get w9:NOPE` rc 1;
    `herdr --version` rc 0 "herdr 0.8.0". The identity rule is spike-confirmed, not assumed:
    pane_id.split(":")[0] == workspace_id was asserted live and held.
  failure: fail-loud
  surface: as above.
- api: `herdr pane split --pane <anchor> …` — successor CREATION (load-bearing; pass 2 §14 P5
    correctly found this undeclared)
  feasibility: verified via existing-call-site — the EXACT invocation is built by
    `build_split_argv` (`hooks/launcher_lib.py:386-393`) and executed inside `perform_handoff`
    (`:1855`), which is this repo's shipped, tested successor-launch path on the same object kind
    (a herdr pane) and the same runtime (herdr 0.8.0, re-probed live under #886 per the module
    docstring `:12-18`). This design adds no new call — it records the result of the existing one.
  failure: fail-loud
  surface: `failed_step` is set on every branch (`split`, `split_response_unparseable`,
    `split_response_not_new`), and `_report_possible_orphan` (`:2740`) surfaces a leaked pane.
- api: `herdr agent start --pane <new pane> …` — successor process start
  feasibility: verified via existing-call-site — `build_agent_start_argv`, executed at
    `hooks/launcher_lib.py:2032` in the same ladder.
  failure: fail-loud
  surface: as above, plus the ack rung.
- api: `shutil.which` for binary presence
  feasibility: verified via existing-call-site — `hooks/launcher_lib.py:1565`
  failure: fail-loud
```

**The negative case is not spiked, so the DEFAULT FLIP does not rest on it (§15 C4, C7).** No spike
proves what `pane split` does when creation is REFUSED (workspace full, quota, permissions), and
tier 1 is a READ. Rather than ship a default that assumes read access implies create permission,
creation is **self-correcting**:

- Creation records `preferred_transport = pane_chain` on tier-1 success, as before.
- If the FIRST boundary of a campaign fails at `pane split` with a refusal-class error, that is not
  merely a retry: the campaign **downgrades `preferred_transport` to `inline`** once, with a
  recorded reason (`creation_refused`) and the §4.3 advisory. The run continues inline instead of
  parking, and the operator can raise it again with `transport set`.

**One contract for a refused split, replacing the earlier contradiction.** A non-zero split is
`launch_failed` and retryable (§4.4) — it is NOT a park. Parking is reserved for an unreadable
inventory or a live-but-unacked agent. The earlier text said creation refusal parks the campaign,
which contradicted §4.4; that is deleted.

Step 8 still owes the one negative probe (§11), but the design is now correct whichever way it
resolves: if creation can be refused while reads succeed, the first boundary downgrades cleanly
instead of stranding.

**Caveat, stated not buried:** no spike exercises a HUNG daemon, so the timeout branch is reasoned,
not measured. Step 8 covers it with an injected fake process, not a live hang.

**Separately measured, and it constrains this design:** herdr's scraped `tokens` field is STALE on
this host — three probes minutes apart returned identical values with a frozen `revision: 7` while
the true values had moved far. **This design reads only `pane_id` and `workspace_id`, never
`tokens`, and must not start.** Full entry: `claude_docs/session_notes/epic-871-m4-wave-log.md`.

## 6. Error handling and failure modes

| failure | behavior |
|---|---|
| tier-1 probe fails | `inline` + reason + advisory; run continues |
| pane reference absent | `inline`, reason `no_pane_ref`; tier 1 still recorded as healthy |
| probe raises anything unexpected | caught, `inline`, reason `probe_error:<type>`; never propagates |
| legacy `session_mode` unrecognized | `inline` + loud diagnostic; never guessed |
| canonical and projection disagree | canonical wins; disagreement reported |
| `transport set` while a child is in flight or a claim is active | refused, distinct exit code |
| two boundary calls, one transition | second gets the SAME generation (idempotent open); one claim holder |
| launch definitely failed | retryable |
| launch indeterminate | parked, visible; reconciliation at the next reclaim decides |
| snapshot unreadable during reconciliation | refuse to relaunch; park |

## 7. Security implications

No new network surface: both probes are local CLI round-trips to a daemon already trusted to launch
panes. Probe stdout is parsed as JSON and read for two fields only — never `eval`'d, never
interpolated into a shell command, a terminal advisory, or a generated prompt. Pane references are
validated by the existing `validate_pane_id` (`launcher_lib.py:306`) before being used, recorded,
or rendered, so a hostile `HERDR_PANE_ID` cannot inject through argv or into durable state.
Generated prompts are built from driver-state only; issue-body text and probe output are excluded.
Bounded readers cap both streams, so a chatty or hostile daemon cannot exhaust memory.

## 8. Scope: two PRs, both landing tonight (D230)

- **PR 1 — machinery.** Probe (both tiers), `campaign_transport`, creation contract, migration +
  projection chokepoint, two-event transition log, `transport set`, the child-boundary fence with
  reconciliation, the advisory, schema declarations. **No prose surface.** `Part of #927`.
- **PR 2 — contract.** Generated successor prompts, `skills/epic-run/SKILL.md` Step 2 + boundary
  rewrite, `docs/multi-issue-driver.md`, and the four drift guards those break. `Closes #927`.

The fence ships WITH the default flip in PR 1: the issue's Risk section forbids shipping the
default alone. **Per D230, `plugin-refresh` is NOT run after PR 2**, so this wave keeps executing
the cached skill and the rewrite takes effect in a later session.

## 9. Test strategy

TDD, red before green. Scoped suite during iteration; the FULL suite once at Step 9.

Known drift guards that MUST be updated in the same commit as the PR-2 prose (not surprises):
`tests/test_epic_run_clarity.py:85,:91,:98`; `tests/hooks/test_multi_issue_driver.py:129`.
`tests/hooks/test_driver_lib.py:652` (validator tolerates additive fields) is EXTENDED, not
replaced.

New coverage: every probe branch in the §4.1 table with an injected runner (no live herdr);
**one idempotency test and one degradation-visibility test per trigger** in the §4.3 table;
migration in all five rows of §4.2 including the unrecognized-value diagnostic; the creation
contract resolving a healthy new campaign to `pane_chain`; projection sync across every production
mutation entry point; the advisory firing exactly once per `transition_id`; the fence refusing a
second successor; **crash-before-ack and ambiguous-launch reconciliation**, including
snapshot-unreadable → refuse-to-relaunch; a bounded-reader test with a fake process emitting past
the cap; and the #611 pin that the launcher still defers to the recorded answer.

## 10. Also owed by this issue (comments, not code)

Close-or-fold dispositions recorded on **#846, #849, #850, #851** (#848→#944 and #845→#927 are
settled). Owned by an explicit plan task so it cannot be dropped.

## 11. The claim most likely to be wrong

**That `herdr api snapshot` — a READ — is a sound proxy for "a successor pane can be CREATED
here".** Tier 1 proves the daemon answers a read. It does not prove `pane split` will succeed; the
daemon could serve reads while refusing creation (workspace full, quota, permissions). If that gap
is real, creation records `pane_chain` optimistically and the first boundary parks instead of
degrading cleanly — converting a clean `inline` into the far more disruptive
`launch_indeterminate` / `parked_unreconcilable` path.

Survived two review passes unrefuted, and pass 2 independently sharpened it into finding P5, which
is why §5 now declares the split/start APIs explicitly.

**Why it is bounded rather than blocking:** the reconciliation in §4.4 contains the damage (no
double-launch; the park is visible and operator-clearable), so this is a LIVENESS risk, not a
safety one. **Step 8 owes exactly one negative probe** — a split attempted against an invalid or
exhausted workspace. If creation really can be refused while reads succeed, tier 1 gains a cheap
create-capability assertion instead of trusting a read.

## 12. Peer consult outcomes (gpt-5.6-sol, pass 1)

Blind both ways; my draft was on disk before the proposal was read. `diagnostic: true`, so it
authorized no fix round. **Adopted:** append-only transition records over `handoff_pending`;
claim-before-probe; probe identity check and stdout cap; probe-under-inline without upgrading;
idempotent boundary-record creation; no inline fallback after an ambiguous launch; the write-only
projection framing; declaring the new fields in the schema; and refusing `transport set` while a
claim is active. **Rejected:** the 2.0 s timeout (§4.1 states the reason and the tuning plan); and
making the pure validator reject unrecognized transport values — `validate_driver_state` is
deliberately permissive with no unknown-key branch (`driver_lib.py:272-275`), so an unrecognized
value degrades to `inline` with a loud diagnostic instead of hard-failing a live campaign.

## 13. Step 4 pass-1 findings and dispositions

Self-review (security lens) — 2 High, 3 Medium, 1 Low. Adversarial (gpt-5.6-sol, `diagnostic:
true`) — 5 High, 3 Medium, 0 Critical, confidence 0.93–0.99. Merged 7 High ≥ the volume threshold
of 5, so the gate looped back on the `design` source (counters `design=1 total=1`). All 8
adversarial findings were verified against the design and the cited code before being accepted;
none was taken on the reviewer's authority.

| id | sev | finding | disposition |
|---|---|---|---|
| A1 | High | four triggers named, one specified | **applied** — §4.3 lifecycle table, all four rows; §9 per-trigger tests |
| A2 | High | "proven nothing started" undefined; reclaim could double-launch | **applied** — §4.4 classifier + `launch_token` reconciliation; the residual window is stated |
| A3 | High | pane reference left unresolved, deferred to Step 8 | **applied** — resolved before this gate: caller-supplied, `validate_pane_id`, and tier 1 needs no pane id at all |
| A4 | High | creation contract never stated ⇒ a new campaign stays `inline` | **applied** — §4.2 creation contract; would have failed AC 1 |
| A5 | High | `outcome` required on a record appended before the action | **applied** — §3 two-event contract |
| A6 | Medium | "every write" invariant with no named writer | **applied** — enforced in `_locked_state_update`; §9 test over every entry point |
| A7 | Medium | spike proved `pane_id` only, but the verdict now needs `workspace_id` | **applied** — re-spiked live; the prefix identity rule is asserted in §5 |
| A8 | Medium | post-capture `len(stdout)` caps nothing | **applied** — bounded readers on both streams, terminate and reap |
| S1 | High | AC 2 (mode-change command) had no section or task | **applied** — §4.5 |
| S2 | High | deleting `--herdr-available` breaks `SKILL.md:186` + two CLI tests | **applied** — the flag's removal moves to PR 2, which owns that prose surface |
| S3 | Medium | `pane_ref` from env written to state and prompts unvalidated | **applied** — existing `validate_pane_id` reused (§7) |
| S4 | Medium | advisory could echo probe stdout | **applied** — fixed reason tokens only (§4.1) |
| S5 | Medium | #846/#849/#850/#851 dispositions unowned | **applied** — §10 plus an explicit plan task |
| S6 | Low | transition-log growth unbounded | **deferred** — two events per transition on a queue of ≤ 10 children is a few KB; pruning would need a rule that must never touch the active transition, which is more risk than the growth. Re-raise if a campaign ever exceeds ~200 transitions. |

## 14. Step 4 pass-2 findings and dispositions

Adversarial (gpt-5.6-sol, `diagnostic: true`, rc 0, freshness verified — the result's
`input_sha256` matched the artifact byte-for-byte): **6 High, 2 Medium, 0 Critical**, confidence
0.93–0.99. Self-review added 1 High + 1 Medium. Merged **7 High ≥ the volume threshold of 5** ⇒
loop-back on `design`; counters `design=2` (at cap), `total=2`. Breaker: no ambiguous flags, no
conflicts ⇒ clear. All 8 adversarial findings were verified against the design text and the cited
code before acceptance.

| id | sev | finding | disposition |
|---|---|---|---|
| P1 | High | `launch_token` = `transition_id`, but the reclaimer's id (`r:`) differs from the original (`b:`) — which does it search? | **applied** — the token is deleted entirely; reconciliation reads the ORIGINAL boundary resolution's `successor_pane` (§4.4) |
| P2 | High | recovery defined only for the child-boundary launch; creation, inline continuation and `mid_child_resume` undefined | **applied** — per-trigger recovery table in §4.3 |
| P3 | High | the promised "terminal-for-now" park had no terminal value, so an intentional park was indistinguishable from a crash | **applied** — `parked_unreconcilable` is a real outcome, never auto-reclaimed (§4.3) |
| P4 | High | reconciliation assumed the snapshot exposes a `launch_token`; the spike proved only `pane_id`/`workspace_id` | **applied** — rebuilt on `_pane_inventory` + `_extract_pane_id` + the `#611` ownership check, all existing call sites (§4.4) |
| P5 | High | successor CREATION load-bearing but absent from `platform_apis`; the negative case deferred to Step 8 | **applied** — `pane split` and `agent start` declared with existing-call-site evidence; the unspiked negative case is now an explicit bounded risk in §5 and §11 |
| P6 | High | `terminal_outcome` references `resolution_id`, which the resolution event did not carry | **applied** — `resolution_id = <transition_id>#<attempt>` added as the sole correlation key (§3) |
| P7 | Medium | no surface when the advisory itself fails to emit, contradicting §5 | **applied** — `advisory_emitted` recorded on the resolution; asserted by the §9 test |
| P8 | Medium | §4.3 ordering (resolution → act) contradicted §4.4's crash description (act → resolution) | **applied** — one explicit 5-step ordering block in §4.3; the resolution ALWAYS precedes the launch |
| S7 | Medium | AC 3's #611 deference survived only in the test strategy after the pass-2 rewrite | **applied** — restored to §4.3 body |
| S8 | High | the probe collapsed two tiers into one verdict, so creation (which needs no pane ref) would have recorded `inline` and re-broken AC 1 | **applied** — probe returns `(capability_ok, pane_ok, reason)`; creation consumes tier 1 only (§4.1) |

**Net effect of pass 3: the design got SMALLER.** One invented protocol removed, four existing
functions reused, two contradictions deleted. The remaining unproven claim is named in §11 and owes
exactly one negative probe in Step 8.

## 15. Step 4 pass-3 findings and dispositions

Recorded 2026-08-06 during PR 2's Step 2 read. **These dispositions were applied in the body by
pass 3 and referenced as `§15 C1`…`§15 C7` from seven places, but the table itself was never
written** — every one of those references was dangling. Reconstructed from the primary evidence,
not from memory: the review result file `.rawgentic-927-s4-adv-pass3.json` (`status: success`,
`diagnostic: true`, `head_sha e759ee04`, `input_sha256
4a318b626bb48e8a7829d6f4fa6506f7f9701679b933ed76acf89308af047be9`, 7 findings — **1 Critical,
5 High, 1 Medium**, confidence 0.94–0.99), read together with the body text that cites each id.

| id | sev | conf | finding (reviewer's own words, condensed) | disposition |
|---|---|---|---|---|
| C1 | **Critical** | 0.99 | §4.4 permitted a crash state misclassified as proof no successor exists, so reclaim could launch a second pane beside a live first one | **applied** — the pre-split inventory is recorded on the resolution BEFORE `pane split`; `null` is never read as "nothing created"; §4.4's diff rule and `reconcile_boundary` enforce it |
| C2 | High | 0.96 | a parked transition's only recovery was "an operator clears it", with no command, lock, validation or audit defined | **applied in design** — `transport unpark <resolution_id> [--adopt\|--discard]` (§4.3). **Code status: the pure guards `unpark_blocked` / `append_unpark` shipped in PR 1; the COMMAND is PR 2's (§16 D).** |
| C3 | High | 0.98 | a pane is not a running agent — an empty pane could be acked as a live successor and stall the campaign forever | **applied** — §4.4 distinguishes pane-present/agent-absent as `start_failed`; `reconcile_boundary` returns `park` on `agent_state_unknown` rather than coercing it |
| C4 | High | 0.99 | the default flip rested on a READ probe standing in for CREATE permission; the negative case was deferred | **applied, and now closed** — §5 made creation self-correcting via a one-time `creation_refused` downgrade, and PR 2 **ran the owed negative probe live** (§17). **Code status: the downgrade is PR 2's (§16 F) — it existed nowhere in the code after PR 1.** |
| C5 | High | 0.94 | `launch_indeterminate` was listed as terminal while other sections needed the transition reclaimable — an implementation would strand it forever | **applied** — §4.3 removes it from the terminal set; indeterminate is the ABSENCE of a terminal event on `split_attempted: true` |
| C6 | High | 0.99 | the generated-prompt contract still required the deleted `launch_token` | **applied** — §4.5 carries `resolution_id`, the sole correlation key |
| C7 | Medium | 0.99 | §5/§11 said a creation refusal parks, contradicting §4.4 where a non-zero split is retryable `launch_failed` | **applied** — one contract: non-zero split ⇒ `launch_failed`, retryable; parking is reserved for an unreadable inventory or a live-but-unacked agent |

**Honest limit of this table.** PR 2's author did not run pass 3 and does not claim to have. The
severities, confidences and finding text above are quoted from that result file; the dispositions
are read off the body text that cites each id. Where the body's disposition and the SHIPPED CODE
disagree — C2 and C4 — the disagreement is stated rather than smoothed over, because that gap is
exactly what §16 exists to close.

## 16. PR 2 — the implementation design (2026-08-06)

**Base:** `19925711` (PR 1 merged, v3.132.0). **Baseline:** 5584 passed, `pytest tests/ -q` exit 0,
measured first-hand on this tree.

### 16.0 What PR 1 actually left behind, measured

PR 1 was honestly scoped as unwired machinery (D233). The measurement at `19925711` is starker than
"the fence has no caller":

- **Twelve** PR-1 symbols appear ONLY in `hooks/driver_lib.py`, where they are defined:
  `append_resolution`, `mark_split_attempted`, `record_successor_pane`, `append_terminal_outcome`,
  `unterminated_resolutions`, `transport_set_blocked`, `unpark_blocked`, `append_unpark`,
  `boundary_advisory_line`, `advisory_due`, `reconcile_boundary`, `child_boundary_precondition`.
- `resolve_creation_transport` (`launcher_lib.py:1715`) has **no caller**.
- There is **no `transport` CLI subcommand** of any kind.
- `skills/epic-run/SKILL.md` contains the string `transport` **zero times**.
- The only live behaviour is the `session_mode` ⇄ `transport` projection inside
  `_locked_state_update`, and `campaign_transport` / `legacy_session_mode` which serve it.

So **AC 1, AC 2 and AC 4 have no live path at all** — not merely an unwired fence. PR 2 ships the
whole user-visible feature and carries `Closes #927` (D237: one PR, because shipping another
caller-less command is the very defect D233 recorded).

### 16.1 The eight deliverables

| | deliverable | AC / authority |
|---|---|---|
| **A** | **Creation seam** — `transport resolve-creation`, the full contract in §16.6 | AC 1, §4.2 |
| **B** | **Boundary wiring** in `_cmd_handoff` — claim → probe → resolution → split → record → terminal, plus the inline-continue path | AC 1, #845 fence, §4.3/§4.4 |
| **C** | **`transport set pane_chain\|inline`** over PR 1's `transport_set_blocked` guard | AC 2, §4.5 |
| **D** | **`transport unpark <resolution_id> --adopt\|--discard`** over `unpark_blocked` / `append_unpark` | §4.3, C2 |
| **E** | **Advisory emission** — one line per `transition_id`, at the boundary and when `next-child` returns `ready` under an `inline` effect | AC 4, §4.3 |
| **F** | **`creation_refused` one-time downgrade** on a definite launch failure before pane_chain has ever worked for this campaign | §5, C4 |
| **G** | **Generated successor prompt** carrying generation, claim, boundary kind, `resolution_id`, and the task-list-rebuild instruction | §4.5, C6 |
| **H** | Prose: `skills/epic-run/SKILL.md` (Step 2 → two questions; boundary rewrite; the `--herdr-available` line), `docs/multi-issue-driver.md`, and the removal of `--herdr-available` itself | AC 1, S2 |
| **I** | **Tests, as a named deliverable** (§18 F8): the creation probe→value mapping incl. every failure token; the atomic step-1 snapshot (a `transport set` committing between an unlocked read and the claim must not be ignored; a child moving to `in_progress` must refuse); claim refusal returning rc 7 with NO campaign work and NO write; claim RELEASE on each definite terminal path and its deliberate ABSENCE on an indeterminate one; two concurrent advisory claimants where exactly one prints; a print that raises leaving a `pending` delivery event; `transport set` refused mid-child with no write; `transport unpark` refusing a non-parked and an already-unparked resolution; the launcher's continued deference to the recorded answer (the #611 pin); the downgrade firing on `pane_not_found` and NOT firing on an unclassified non-zero split; and the mid-child suite passing unchanged | AC 5 |

### 16.2 The exact ordering inside `_cmd_handoff`

Unchanged prologue: strict state read → `_refuse_foreign_kind` → `campaign_transport` for the
recorded preference (never forced — AC 3, the #611 deference at `:4488` stays) → `observe_head`
(fail-closed, rc 5) → `fresh_session_handoff` → `revalidation_required` rc 6 → non-`ready` rc 3 →
`fresh_session_available` → goal condition.

New, after that and before any launch:

```
1  LOCKED (one mutation, §18 F3):
     re-read preferred_transport via campaign_transport   -> the SNAPSHOT everything below uses
     re-evaluate child_boundary_precondition(state, next_issue)
       -> not ok: no write at all; rc 3 naming the reason
     open_handoff(...)                                   -> idempotent; a competing call gets the
                                                            SAME generation
     handoff_claim(...)                                  -> claim BEFORE probe
       -> refused: no write; rc 7 (§16.2 "claim-refused STOPS")
2  transport_probe(pane_ref=anchor_pane)                  -> tiers 1+2, AFTER the claim
3  panes_before = _pane_inventory(runner)                 -> the baseline C1 requires
4  LOCKED: append_resolution(preferred=<step-1 snapshot>, effective=..., panes_before=...)
                                                          -> lands BEFORE any launch; successor_pane
                                                             null, split_attempted false
5a effective == inline:
     LOCKED (one mutation): append_terminal_outcome(inline_continued)
                            + handoff_claim_release(...)  -> §18 F1
     then the advisory per §16.7, then return the inline disposition (rc 0)
5b effective == pane_chain:
     LOCKED: mark_split_attempted()                       -> the marker lands FIRST (C1)
     perform_handoff(...)                                 -> split, ownership check, agent start, ack
     LOCKED (ONE mutation, §18 F2): record_successor_pane(new_pane) when out["new_pane"] is set
                            + append_terminal_outcome(<§16.3>)  [or deliberately none]
                            + the §16.4 downgrade when it qualifies, with its projection and audit
                            + handoff_claim_release(...) on a DEFINITE terminal only
     then print anything the transaction authorised
```

**Why steps 1–2 collapsed into one lock (§18 F3, High 0.98).** The first draft read
`preferred_transport` and evaluated the precondition BEFORE taking the claim. Both are then stale by
the time the claim lands: a concurrent `transport set inline` could commit and be ignored by this
boundary, and a child could move to `in_progress` between the check and the claim, letting a stale
attempt launch a second worker. The re-read inside the claim lock is what makes the snapshot binding.

**Why the failure path is ONE mutation (§18 F2, High 0.90).** The terminal event, the §16.4
downgrade, its `legacy_session_mode` projection, the audit metadata and the claim release are all
committed together, and nothing prints until that transaction lands. Separate mutations leave a crash
window where the resolution is terminal but the promised downgrade is absent, and let a delayed
automatic downgrade overwrite a concurrent operator `transport set`.

**Claim release, because nothing else releases it (§18 F1, High 0.87 — CONFIRMED against the code).**
No function in `driver_lib` clears `handoff_claim`; `handoff_ack_started` only sets `started: True`,
and the module's own comment at `:2467` records that completion is never written (the #846 gap). So an
inline continuation that simply returned would leave the claim live for its full 1800 s lease —
blocking `transport set` and handing every later contender rc 7 for half an hour after the boundary
had finished. A new pure `handoff_claim_release(state, generation, claimant) -> (released, new_state)`
clears ONLY a claim matching that generation and claimant whose resolution already carries a terminal
event. It is additive and boundary-only: the shared `handoff_claim_is_live` predicate and the
mid-child path are untouched, so mid-child's tests passing unchanged remains the regression signal.
An INDETERMINATE launch deliberately does NOT release — the lease is what protects a possibly-live
successor until reconciliation runs.

**Claim-refused STOPS this contender. It does not continue in place** (§18 F1 — Critical, and it
was against my own first draft). The first version copied the mid-child path's wording: "the run
continues in place and reports it". That is right for mid-child, where the loser has a child
genuinely in flight to keep working on. At a **boundary** it is the opposite: the precondition is
that NOTHING is in flight, so "continue in place" can only mean *start the next child in this
session* — while the claim holder is launching a fresh session for that same child. Two workers on
one child is precisely what the fence exists to prevent, so:

> Only the claim holder may launch a successor OR choose inline continuation. A contender that
> fails to claim terminates its boundary attempt, performs no campaign work, and exits with its own
> code (**rc 7**, distinct from rc 3's "no handoff is due") naming the holder.

`rc 7` is new rather than reusing rc 3 because the two demand opposite operator responses: rc 3
means nobody is working this boundary and the run may proceed in-session; rc 7 means somebody is,
and proceeding is the defect.

**`panes_before` is captured here even though `perform_handoff` captures its own** (`:2167`). The
resolution must carry the baseline BEFORE the split, and threading `perform_handoff`'s copy out
would change a tested signature on the shipped launch path. The extra `herdr pane list` is one
local read. The two captures can differ only if a pane appears in between, which widens the
"appeared" set and makes reconciliation PARK — it fails toward safety.

### 16.3 `perform_handoff` result → terminal outcome (the complete map)

`perform_handoff`'s failure taxonomy is read off its own code, not assumed:

| result | terminal outcome | `successor_pane` | why |
|---|---|---|---|
| `ok: True` | `successor_acked` | the owned `new_pane` | the ack rung passed |
| `failed_step: pane_inventory_unavailable` (`:2169`) | `no_split_attempted` | null | herdr could not even be listed, so **no split was attempted**. Distinct from a refusal — see §16.4 |
| `failed_step: split` (`:2185`), stderr `error.code == pane_not_found`, **and** the post-failure diff is empty | `launch_failed`, **and this row alone is downgrade-eligible** | null | the one shape a live probe measured end to end (§17) |
| `failed_step: split` (`:2185`), any other/absent code, **and** the post-failure diff is empty | `launch_failed`, **never downgrade-eligible** | null | nothing survives to reconcile, so closing it is safe — but an unclassified failure is not evidence that creation was REFUSED (§18 F5) |
| `failed_step: split` (`:2185`), **and** the diff is non-empty or unreadable | **no terminal event** | null | an unspiked shape may have created a pane before failing. Left unterminated so §4.4 reconciles by diff — never terminalized on an unmeasured shape |
| `failed_step: split_response_unparseable` (`:2189`) | **no terminal event** | null | `_extract_pane_id` → None means the RESPONSE was unreadable, not that creation failed. Indeterminate by construction |
| `failed_step: split_response_not_new` (`:2198`) | **no terminal event** | null | the #611 ownership check rejected the id — herdr named a pane we cannot claim, so what exists is unknown |
| `failed_step: agent_start` / `name_taken` (`:2234`,`:2239`) with `new_pane` set | `start_failed` | the owned pane | a pane exists, no agent runs in it (C3) |
| any other failure, or an exception, with the split attempted | **no terminal event** | whatever landed | the indeterminate crash signature §4.4 reconciles. Appending a terminal here is the C5 mistake |

**Why three former `launch_failed` rows became "no terminal event" (§18 F5, High 0.90).** A terminal
event CLOSES a resolution, and §4.4's reconciliation only ever examines UNTERMINATED ones. So
recording `launch_failed` on a shape that might have created a pane does not merely mislabel it — it
removes the pane from reconciliation's reach for good. Only the one shape a live probe measured
(rc non-zero, empty stdout, structured `pane_not_found`, inventory unchanged) is treated as proven.
The post-failure inventory diff is the same one call `_pane_inventory` already makes, compared
against the `panes_before` the resolution already carries.

**Terminal status is the LATEST terminal event by append order, not the first** (`_terminal_for`,
`driver_lib.py:1814`). That rule is load-bearing — it is what makes `transport unpark` a one-shot —
and until now it lived only in a docstring, so it is stated here as contract (§18 F7).

**`no_split_attempted` is a NEW terminal value** and must be added to `TERMINAL_OUTCOMES`
(`driver_lib.py`) with the schema declaration alongside it. It exists so that "herdr was unlistable"
stops sharing a name with "creation was refused" — §16.4 depends on telling them apart.

### 16.4 F — the `creation_refused` downgrade, and why it is not code-based

§5 promises a one-time downgrade so the default flip does not rest on an unproven CREATE
permission. §17's probe shows a refused split answers with a **structured `error.code` on stderr**
— but it can only produce `pane_not_found`, never a quota or permission refusal, so **an allowlist
of refusal codes would be guessing.** The trigger is therefore the OUTCOME, not the code:

> On the ONE downgrade-eligible row of §16.3 — `failed_step: split` with stderr
> `error.code == pane_not_found` **and** an empty post-failure inventory diff — when no
> `successor_acked` has ever been recorded for this campaign, set `preferred_transport = inline` once,
> with reason `creation_refused`, in the same locked mutation as the terminal event, and emit the
> §16.7 advisory afterwards. The transition stays retryable; the NEXT boundary continues inline.

**The trigger is an ENUMERATED spiked refusal code, not a shape (§18 F5, High 0.98).** An empty
inventory diff proves only that no successor pane survived the observation — a transient service,
transport or internal error produces the same shape, and durably downgrading a healthy campaign on
one of those is a worse outcome than the stranding this rule exists to prevent. The enumerated set is
exactly `{pane_not_found}` today, because that is the only code a live probe has measured (§17). It is
a data constant, so adding a code later is a one-line change WITH a spike, never an inference.

**What must NOT trigger it (§18 F4, High 0.99).** The first draft triggered on the whole
`launch_failed` class, which §16.3 then also assigned to an unreadable pane inventory, an unparseable
split response, and an ownership rejection. Every one of those is an OBSERVATION failure, not a
refusal — and the inventory case never even attempted a split — so a transient herdr hiccup would
have permanently downgraded a healthy campaign's preference. After §16.3 those shapes carry
`no_split_attempted` or no terminal event at all, so the trigger is now exactly the proven-refusal
row and nothing else. Explicitly retryable WITHOUT touching `preferred_transport`:
`no_split_attempted`, unparseable responses, ownership rejections, and every indeterminate result.

"Has pane_chain ever worked here" is answered purely from the transitions log, so the rule needs no
new state. An operator raises it again with `transport set` (deliverable C) — which is why C and F
must ship together.

### 16.6 A — the creation seam, in full (§18 F3, High 0.94)

The first draft left this as one table row: "a CLI that resolves and records `preferred_transport`".
That is the same shape of under-specification that produced PR 1's caller-less machinery, so it is
written out.

**Command.** `python3 hooks/launcher_lib.py transport resolve-creation --driver-state <path>
--project-root <dir>`

**Inputs.** Nothing from the environment. `HERDR_ENV` / `HERDR_PANE_ID` are never read here —
creation has no pane of its own, and tier 1 needs none (§4.1).

**Behaviour.** One `_locked_state_update`, doing all of this inside that single lock:

1. Refuse (**rc 2**, no write) when `preferred_transport` is ALREADY recorded. Creation is
   write-once; changing it afterwards is `transport set` (deliverable C). This makes the command
   idempotent-safe rather than idempotent: a second call is a caller error, not a silent re-probe.
2. `resolve_creation_transport()` → `(transport, reason)`, tier 1 only.
3. Write `preferred_transport = transport` AND its `legacy_session_mode` projection together.
4. `append_resolution(trigger="creation", transition_id=f"c:{campaign}:0", generation=<current or 0>,
   kind="creation", preferred=transport, effective=transport, probe_reason=reason, pane_ref=None,
   panes_before=None)` then `append_terminal_outcome(outcome="created")`. Both events land in the
   same lock as the write, so a crash cannot leave a recorded preference with no provenance.

**Probe result → recorded value** (the mapping the reviewer correctly found absent):

| tier-1 verdict | `preferred_transport` | `probe_reason` |
|---|---|---|
| `capability_ok` True | `pane_chain` | `probe_ok` |
| `herdr_absent` / `herdr_unreachable` | `inline` | that token |
| `probe_oversized` / `probe_unparseable` / `probe_timeout` | `inline` | that token |
| `probe_error:<Type>` | `inline` | that token |

**Failure surface.** Every non-`pane_chain` resolution emits the §16.7 advisory and records
`probe_reason` durably, so a campaign that starts `inline` says why on both a terminal and a durable
surface. Persistence failure is NOT swallowed: `_locked_state_update` raising propagates as a
non-zero exit, because a creation that cannot record its own answer must not report success.

**Call site.** `skills/epic-run/SKILL.md` Step 2, immediately after the driver-state file is written
and before the first child is handed out (deliverable H). Its two remaining questions — merge policy
and arming the resume launcher — are unaffected.

### 16.7 E — advisory emission, with an atomic claim (§18 F2, High 0.92)

AC 4 requires the advisory to fire **exactly once per boundary**, and two independent surfaces emit
it (`_cmd_handoff` at the boundary; `next-child` when it returns `ready` under an `inline` effect).
PR 1 shipped `advisory_due(transition_id, already_emitted)` as a PURE predicate over a caller-supplied
set — and nothing persists that set, so two surfaces could each pass their own empty set and both
print, or one could mark it emitted while its own write to the terminal failed.

**Contract.** A new locked operation, `claim_advisory(state, transition_id) -> bool`:

- It appends an advisory DELIVERY event `{transition_id, state: pending, at}` and returns True, or
  returns False when an event for that `transition_id` already exists. Both happen inside ONE
  `_locked_state_update`, so the check and the record cannot be separated.
- **Only a True claimant prints.** After the print it appends `{transition_id, state: emitted|failed,
  at}`. `advisory_due` keeps its place as the pure predicate the claim is built on; it is no longer
  the gate.
- **AC 4 is satisfied as AT-MOST-ONCE printing plus an authoritative durable event — not as
  exactly-once stdout delivery (§18 F4, High 0.99).** The reviewer is right that exactly-once
  delivery is unobtainable here: the claim is durable and the print is not transactional, so
  recording first can suppress an advisory that never appeared, and printing first can duplicate it
  after a crash. Recording first is the correct side to fail on, because the durable event is the
  authoritative surface and a `pending` that never became `emitted` is itself the visible defect —
  a duplicate line, by contrast, is indistinguishable from a second real degradation.
- A `pending` with no terminal delivery state is therefore a first-class signal, and it is what §9's
  degradation-visibility test asserts against. The resolution's `advisory_emitted` field mirrors the
  outcome for the boundary path; `next-child` has no resolution, so the delivery event is the whole
  record there.
- The command's exit code is never affected — advisory-only, per AC 4.

`next-child` has no resolution to annotate, so on that path the claim alone is the record.

### 16.8 C — `transport set`, guard INSIDE the lock (§18 F7, Medium 0.88)

`transport_set_blocked(state, now_ts=...)` is evaluated **inside the same
`_locked_state_update`** that writes the new value, against the state read under that lock — never
before acquiring it. A pre-lock check permits a child to move to `in_progress` between check and
write, which is the mid-child mode flip the guard exists to refuse.

On a blocked result the mutation performs **no write at all** and the command exits **rc 3** with the
blocking reason (`child_in_flight`, `live_claim`, `state_unreadable`, `issues_unreadable`) named on
stderr. On a clear result it writes `preferred_transport` and its `legacy_session_mode` projection
together, and records who/when/reason as audit metadata. It introduces no pending-override state:
the NEXT boundary still probes.

*(This finding carried `ambiguity_flag: true`, which mechanically fires WF2's ambiguity circuit
breaker. It was resolved from the settled design rather than escalated — §4.5 already said "under the
driver-state lock" — and the reasoning, plus how to overturn it, is decision **D238**.)*

### 16.5 Risk, and the one claim most likely to be wrong

**Highest risk: B.** It adds locked writes on the shipped boundary path, and its failure mode is
the two-successors condition the fence exists to prevent. Mitigations: every write goes through
`_locked_state_update` (the module's only writer); the claim precedes the probe; the resolution
precedes the launch; `mark_split_attempted` precedes the split; and the mid-child path is not
touched, so its tests passing unchanged is the regression signal. Tagged `riskLevel: high` so
Step 8a reviews it.

**The claim most likely to be wrong:** that mapping `agent_start` / `name_taken` to `start_failed`
is safe *without* re-verifying the pane. `perform_handoff` sets `new_pane` from an
ownership-verified id, and its own cleanup may already have closed that pane on the failure path —
so a `start_failed` record could name a pane that no longer exists. That is benign for
reconciliation (`successor_gone` ⇒ `relaunch_permitted`) but it means `start_failed` must never be
read as "an empty pane is waiting for you". Stated here rather than discovered later.

## 17. PR 2's platform declaration and the owed negative probe

```
platform_apis:
- api: `herdr pane split --pane <anchor> --direction down --cwd <dir>` — the NEGATIVE case
  feasibility: verified via spike — the owed §11 probe, RUN LIVE on this host 2026-08-06 with the
    EXACT shipped argv, built by `build_split_argv(anchor_pane=..., cwd=..., project_root=...)`:
      argv: herdr pane split --pane w9:NOPE --direction down --cwd /home/rocky00717/rawgentic/projects/rawgentic
      rc: 1 · stdout: EMPTY · stderr: {"error":{"code":"pane_not_found","message":"pane not found"},"id":"cli:pane:split"}
      _extract_pane_id(stdout) -> None
      pane inventory identical before and after (7 panes, unchanged) -> NOTHING was created
    herdr 0.8.0. So a refused split is a DEFINITE failure: rc non-zero, no id to parse, no pane
    leaked. §16.3's `launch_failed` row and §4.4's "definite failure" class are now measured, not
    reasoned.
  failure: fail-loud
  surface: `failed_step: split`; the resolution carries `split_attempted: true` with
    `successor_pane: null`, which §4.4 reconciles by inventory diff.
- api: `herdr pane list` — tier-1 capability, and the pre/post-split inventory
  feasibility: verified via existing-call-site — `_pane_inventory` (`hooks/launcher_lib.py:2880`),
    called on the shipped launch path at `:2167` and `:2930`, and by `transport_probe` (`:1662`).
    Same object kind (a herdr pane), same runtime (herdr 0.8.0). Re-observed live this session:
    rc 0, JSON carrying `result.panes[]`, 7 panes.
  failure: fail-loud
  surface: `_pane_inventory` returns None on any unusable inventory; `perform_handoff` refuses with
    `failed_step: pane_inventory_unavailable` (`:2169`) and §16.3 records `no_split_attempted`.
- api: `herdr pane get <pane_id>` — tier-2 pane liveness
  feasibility: verified via spike — §5's live spike on this host (`herdr pane get w1:pKS` rc 0 with
    `pane_id`/`workspace_id` agreeing; no-arg rc 2; `w9:NOPE` rc 1). PR 2 adds no new invocation.
  failure: fail-loud
  surface: `transport_probe` returns `(True, False, <token>)` with `pane_not_found`,
    `probe_usage_error`, `probe_identity_mismatch` or `probe_unparseable` — every token distinct and
    recorded on the resolution as `probe_reason`.
- api: `herdr pane split --pane <anchor> …` — successor creation, POSITIVE case
  feasibility: verified via existing-call-site — argv built by `build_split_argv`
    (`hooks/launcher_lib.py:386-393`), executed inside `perform_handoff` (`:2182`). This is the
    repo's shipped, tested successor-launch path; PR 2 records its result rather than adding a call.
  failure: fail-loud
  surface: `failed_step` ∈ {`split`, `split_response_unparseable`, `split_response_not_new`}, each
    mapped to a DIFFERENT §16.3 row; `_report_possible_orphan` (`:2740`) surfaces a leaked pane.
- api: `herdr agent start --pane <new pane> …` — successor process start
  feasibility: verified via existing-call-site — `build_agent_start_argv`, executed at `:2218` in the
    same ladder, with the `agent_pane_busy` retry and the #731 `name_taken` refusal already tested.
  failure: fail-loud
  surface: `failed_step` ∈ {`agent_start`, `name_taken`} with `new_pane` set ⇒ §16.3 records
    `start_failed`; the ack rung is what promotes it to `successor_acked`.
```

**Why this is now one row per operation (§18 F8, Medium 0.97).** The first draft bundled all four
behind "unchanged from §5", which cited a section the review artifact did not contain and named no
assertion. Each row above carries its own call site, its own evidence class, and the concrete
`failed_step` token that surfaces its failure.

## 18. PR 2 Step-4 pass-1 findings and dispositions

Cross-model adversarial review of §16–§17 (`gpt-5.6-sol` via `hooks/review_runner.py review-artifact`,
`status: success`, **`diagnostic: false`** — a reopen token was minted first, so this pass could
authorize a fix round; freshness verified: the result's `input_sha256` matched the artifact
byte-for-byte at disposition time). **1 Critical, 5 High, 2 Medium**, confidence 0.88–0.99. Merged
High = 5, at the volume threshold of 5 ⇒ loop-back on the `design` source (counters `design=1`,
`total=1` of 3 — a FRESH budget, D235). Every finding was checked against the design text and the
cited code before acceptance; none was taken on the reviewer's authority.

| id | sev | conf | finding | disposition |
|---|---|---|---|---|
| F1 | **Critical** | 0.98 | a claim-refused contender was told to "continue in place", which at a boundary means starting the next child beside the claim holder's successor | **applied** — §16.2: only the claim holder may launch or continue; a loser exits rc 7 doing no campaign work. Verified real against `child_boundary_precondition`: at a boundary nothing is in flight, so "continue" can only mean taking the next child |
| F2 | High | 0.92 | two surfaces emit the advisory with no atomic claim, and no defined behaviour when the print itself fails | **applied** — §16.7: a locked `claim_advisory(transition_id)`; only a True claimant prints; a failed print records `advisory_emitted: false`. Verified: `advisory_due` is pure over a caller-supplied set and nothing persisted that set |
| F3 | High | 0.94 | the creation seam — the deliverable AC 1 depends on — was one table row with no command, call site, mapping or failure behaviour | **applied** — §16.6 written out in full. The finding is exactly right about the risk: that is the shape of under-specification that produced PR 1's caller-less machinery |
| F4 | High | 0.99 | `creation_refused` triggered on the whole `launch_failed` class, which also covered an unreadable inventory, an unparseable response and an ownership rejection — none of them refusals | **applied** — §16.3 splits those shapes out (`no_split_attempted` / no terminal event) and §16.4 triggers only on the proven-refusal row |
| F5 | High | 0.90 | only the `pane_not_found` shape is spiked, so terminalizing every non-zero split as definite could close a resolution that had created a pane, removing it from reconciliation's reach | **applied** — §16.3: `launch_failed` requires non-zero rc **and** an empty post-failure inventory diff; every other shape stays unterminated for §4.4 |
| F6 | High | 0.99 | the review artifact's own header told the reviewer that sections 1–15 were "NOT under review", which suppresses findings about contracts §16 imports | **applied** — the artifact generator no longer says what is settled; it states the normative dependencies and inlines the contracts §16 relies on. This repo's own review-brief rule bans exactly that sentence (`SKILL.md <review-severity>`), so the finding is a hit on the author's process, not on the design |
| F7 | Medium | 0.88 | `transport set` did not state that its guard is evaluated inside the same locked mutation as the write | **applied** — §16.8. Carried `ambiguity_flag: true`, which mechanically fires the ambiguity breaker; resolved from the settled §4.5 rather than escalated to an away owner — reasoning and undo in **D238** |
| F8 | Medium | 0.97 | §17 bundled four herdr operations behind "unchanged from §5", citing an absent section and naming no assertion | **applied** — one row per operation, each with its call site and its `failed_step` token |

**What the probe does NOT prove, stated plainly:** it exercises a nonexistent workspace, which is
the only refusal a session can produce without exhausting a real one. It does not show what rc or
`error.code` a QUOTA or PERMISSION refusal returns. §16.4 is designed so that gap cannot matter —
the downgrade triggers on the outcome, never on a code.

**The `tokens` caveat from §5 still binds:** PR 2 reads only `pane_id` and `workspace_id`.

## 19. PR 2 Step-4 pass-2 findings and dispositions — the gate CLOSES here

Same runner, same reviewer, same freshness check (`input_sha256` matched byte-for-byte at
disposition). **6 High, 2 Medium, 0 Critical** — pass 1's Critical is fixed and no new one appeared.
Merged High = 6 ≥ the volume threshold of 5, so volume alone calls for another design pass, but the
`design` source is at its cap (`design=2`, `total=2` of 3; the GLOBAL cap is not reached). Per #798
the gate therefore **closes budget-exhausted** rather than escalating over resolved ground.

**The breaker, and why it did not stop the run.** Two findings carried `ambiguity_flag: true`, which
mechanically blocks that carve-out. Each was checked against the shipped code instead of being
escalated to an away owner — one was CONFIRMED and fixed, one was REFUTED with file:line evidence.
Neither was a fork only an owner could settle. Reasoning and undo: **D239**.

| id | sev | conf | finding | disposition |
|---|---|---|---|---|
| F1 | High | 0.87 | the inline branch takes a claim and never releases it, so it blocks `transport set` and hands later contenders rc 7 for the whole 1800 s lease | **applied** — §16.2 adds a boundary-only `handoff_claim_release` on every DEFINITE terminal path (never on an indeterminate one). **CONFIRMED against the code:** nothing in `driver_lib` clears `handoff_claim`, and `:2467` already records that completion is never written (#846) |
| F2 | High | 0.90 | the terminal event, the downgrade, the projection and the audit metadata were separate mutations, so a crash could leave a terminal resolution without its promised downgrade, and a delayed downgrade could overwrite a concurrent operator change | **applied** — §16.2 step 5b commits all of it in ONE `_locked_state_update`; nothing prints until that transaction lands |
| F3 | High | 0.98 | `preferred_transport` and the boundary precondition were read BEFORE the claim lock and never revalidated inside it | **applied** — §16.2 step 1 re-reads both inside the claim mutation and returns that snapshot for the probe and the resolution |
| F4 | High | 0.99 | a durable claim plus a non-transactional print cannot deliver exactly-once; recording first can suppress a line that never appeared, printing first can duplicate it | **applied** — §16.7 becomes an append-only delivery record (`pending`/`emitted`/`failed`) and AC 4 is restated honestly as at-most-once printing with an authoritative durable event. A `pending` with no terminal state IS the visible defect |
| F5 | High | 0.98 | an empty inventory diff proves no pane survived, NOT that creation was refused, so a transient error could durably downgrade a healthy campaign | **applied** — §16.4's trigger is now an enumerated spiked refusal code (`{pane_not_found}`), and §16.3 splits the unclassified non-zero split into its own non-downgrade-eligible row |
| F6 | High | 0.99 | the artifact again addressed the reviewer directly ("say so if a contract makes it unsound"), a prompt-injection path from untrusted artifact text into review scope | **applied** — the sentence is deleted and the generator carries no reviewer-directed text at all. Second occurrence of this defect from the same author in one gate; the rule is in this repo's own `<review-severity>` block |
| F7 | Medium | 0.93 | the unpark outcomes are undefined and a guard could permit repeated unparks | **REFUTED in part, applied in part.** `_terminal_for` (`driver_lib.py:1814`) returns the LATEST terminal event, and its docstring records that this exact repeated-unpark case was PR 1's Step-11 finding 7, fixed for that reason — so `unpark_blocked` returns `not_parked` after an unpark. No new outcome vocabulary is needed: `append_unpark` writes an existing terminal value plus `operator`/`reason`. **Applied:** §16.3 now states the latest-wins rule as contract rather than leaving it in a docstring |
| F8 | Medium | 0.92 | testing is AC 5 but the "complete" deliverable list contained no test deliverable and no concrete race cases | **applied** — deliverable **I** in §16.1, enumerating the races and failure paths, including the ones these two passes surfaced |

**Residual risk, stated rather than buried.** These eight dispositions ship WITHOUT a verifying
design pass, because no design budget remains. The mitigation is that the CODE is what ships and it
is reviewed twice more — Step 8a on the high-risk wiring task and Step 11's two reviewers — and both
briefs name this section so the reviewers know which contracts arrived unverified.

## 20. PR 2 Step-4 inline self-review (security lens) — my own findings

Run by the author, and labelled as such. Two findings, both at a trust boundary the new CLI
surfaces create, both applied into §16.6/§16.8.

| id | sev | finding | disposition |
|---|---|---|---|
| S1 | Medium | `transport set <value>` and `transport unpark --adopt <pane>` take operator-supplied strings that land in DURABLE state. The design named no validation for either, so an arbitrary string could become `preferred_transport` — which `campaign_transport` then reports and `legacy_session_mode` maps to `None`, degrading every later boundary to `inline` with a diagnostic nobody asked for | **applied** — `transport set` validates its value against the closed `TRANSPORTS` set and refuses rc 2 otherwise; `--adopt` runs the existing `validate_pane_id` before the value is recorded or rendered, exactly as `transport_probe` already does for `HERDR_PANE_ID` |
| S2 | Low | the `operator` and `reason` strings `append_unpark` records are unbounded, and they are rendered back on later reads | **applied** — both are capped at 200 characters and control characters are rejected. Cheap, and it keeps a pasted transcript out of a durable audit record |

Neither is exploitable by anything but the operator running the command, which is why both are
Medium/Low rather than High: the trust boundary is real but the caller is already trusted to edit
the campaign. They are fixed because input validation at a trust boundary is one of the things this
repo's build discipline says must never be simplified away.

**Platform-feasibility check (#226): PASS.** §17 declares every herdr operation PR 2 touches, each
with an existing shipped call site or a live spike; PR 2 adds no new external call. The one claim
that rested on reasoning rather than measurement — what a refused `pane split` does — was measured
this session (§17) and the design was then narrowed to fit what the measurement actually proves.

## 21. PR 2 Step-11 code-review findings and dispositions

Cross-model diff review of the committed branch (`gpt-5.6-sol` via
`hooks/review_runner.py review-code --base origin/main`, `status: success`, `diagnostic: false`,
freshness verified — the result's `head_sha` matched `HEAD` at disposition time). **4 High,
2 Medium, 0 Critical**, confidence 0.83–0.99. Merged High = 4, BELOW the volume threshold of 5, so
no loop-back was triggered; every finding was applied anyway. Each was verified against the code
before acceptance.

**The headline: the fence as reviewed did not deliver exactly-one-successor.** F2 and F4 are the
same hole seen from two directions, and the code says so plainly —
`handoff_claim_blocked_by_live_claim` returns False whenever the held claim's generation differs
from the one being claimed, `handoff_claim_is_live` is scoped to the CURRENT generation, and
`open_handoff` has never consulted the claim at all (that last fact is recorded in
`handoff_claim_is_live`'s own docstring as the #846 limit). So the claim protects only two
invocations that derived the SAME generation from the same snapshot; one that reads state after the
other has claimed derives `generation + 1`, opens it, and claims it unopposed.

| id | sev | conf | finding | disposition |
|---|---|---|---|---|
| F1 | High | 0.94 | the transport read and the mode-dependent rc 3 refusal came from an UNLOCKED snapshot taken before the locked step 1, so a `transport set pane_chain` committing in between was ignored | **applied** — the decision now reads through `_locked_state_read`, this function's existing idiom, and falls back to the unlocked snapshot only if the locked read itself fails |
| F2 | High | 0.84 | after `successor_acked` the claim is released while the child is still `queued`, so a replay in that window passes every check and launches again | **applied** — `_close_launch` records a `boundary_consumed` marker and `child_boundary_precondition` refuses on it (`boundary_already_consumed`), fail-CLOSED on an unreadable marker. Carried `ambiguity_flag: true`; resolved from the code, not escalated (D240) |
| F3 | High | 0.99 | the downgrade fired on the failure classification alone, dropping §16.4's "only when `successor_acked` has NEVER occurred" — so a campaign that had been chaining panes for six children would be durably switched to inline by one `pane_not_found` | **applied** — the guard now consults the transitions log. This was my omission, not a design gap: §16.4 always said it |
| F4 | High | 0.87 | `_claimant_id` is not per-process, so the fence's identity is not what it claims | **applied, via F2's mechanism.** The identity concern is real but secondary: the actual hole is the generation bump, which no claimant id can close. `child_boundary_precondition` now refuses while ANY boundary resolution is unterminated (`boundary_in_flight`), using `unterminated_resolutions` — a function PR 1 shipped and nothing called. Also `ambiguity_flag: true`; resolved from the code (D240) |
| F5 | Medium | 0.96 | `handoff` keyed the advisory claim on the generation and `next-child` on the issue, so the two surfaces never contended and both could speak for one boundary | **applied** — one canonical key, `bnd:<campaign>:<issue>`, on both surfaces |
| F6 | Medium | 0.83 | the claimant comes from an environment variable, lands in durable state, and is interpolated into the successor's generated prompt unvalidated | **applied** — `validate_claimant_id` enforces a bounded identifier grammar (letters, digits, dot, underscore, colon, hyphen; ≤128), so a newline or instruction-shaped value is refused rather than reshaping a prompt |

**Suite after the fix round: 5636 passed, exit 0** (baseline 5584, +52 tests). Lint 10.00/10.

## 22. Known limitation: teardown can kill the writer of the boundary's own terminal state

Found by the author's inline bug-logic pass at Step 11, and stated here rather than left for
somebody to hit.

`_cmd_handoff` runs INSIDE the predecessor session, and with the default verification ladder
`perform_handoff(teardown=True)` retires that predecessor — it closes the predecessor's pane, which
kills this process. Everything §16.2 puts after the launch (`record_successor_pane`, the terminal
outcome, the transport downgrade, the claim release, the `boundary_consumed` marker) is therefore
written by a process that may not survive to write it.

**Why this is bounded rather than broken.** The kill happens INSIDE `perform_handoff`, so there are
only two outcomes, and the design already models both:

- The call RETURNS ⇒ the process is alive ⇒ every post-launch write lands in the two locked
  mutations that immediately follow. This is the normal path.
- The process dies inside the call ⇒ the resolution stays unterminated with `split_attempted: true`
  and `successor_pane: null` ⇒ that is precisely the crash signature §4.4 reconciles.

**The honest cost, and it is a LIVENESS cost.** In that second case reconciliation takes a fresh
inventory, finds a pane that appeared (the successor really did start), and cannot prove it is ours
because `successor_pane` was never recorded — so `reconcile_boundary` returns **`park`**
(`indeterminate_pane_appeared`) rather than `adopt_successor`. A successful handoff can therefore
land the campaign in a park that needs an operator. It never launches a second successor, which is
the property that matters.

**The remedy already ships in this PR:** `transport unpark <resolution_id> --adopt <pane>`, which is
exactly the operator decision this case needs. And `--no-teardown` avoids the window altogether, at
the cost of leaving the predecessor pane alive for the successor to retire — which is this repo's own
stated rule for a ladder carrying successor-owned checks ("retirement belongs to the successor").

**Not fixed here, deliberately.** Making the boundary retire-by-successor is a change to who owns
teardown, which is #665's design rather than this issue's, and #927 is already carrying #845. The
narrow follow-up is: have `_cmd_handoff` default to `--no-teardown` and let `retire-predecessor`
close the predecessor once the successor has acked. Recorded here rather than filed, per the D179
issue throttle.
