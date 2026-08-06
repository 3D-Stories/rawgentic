# #927 — Epic-run transport rework: probed transport, per-transition effect, one successor

**Date:** 2026-08-05 · **Issue:** #927 (epic #871, M4 wave) · **Status:** design pass 3, for Step 4 re-gate
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
