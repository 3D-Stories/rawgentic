# #769 — the child-boundary learnings sweep, mechanized

**Issue:** [#769](https://github.com/3D-Stories/rawgentic/issues/769) ·
**Epic:** [#871](https://github.com/3D-Stories/rawgentic/issues/871) (M4 wave, child 4 of 8) ·
**Design authority:** `docs/planning/2026-08-05-871-m4-session-continuity-away-mode.md` §3.2 ·
**Head at design:** `224ddace` · **Author model:** `claude-opus-5`

---

## 1. The problem in one paragraph

Between two children of an epic campaign the driver already runs a boundary sequence: verify the
merge SHA, record the child's outcome, revalidate the remaining children against the new
`origin/main`, hand off to a successor. Revalidation asks a narrow, machine-checkable question —
*do the remaining issue bodies still describe reality at this head?* The owner's standing order
asks a wider one, verbatim from epic #906 D181 (2026-08-05):

> "in between each issue, make sure you revalidate future issues in the epic based on learnings"

That is a **learnings sweep**: take what the completed child *learned* — its review findings, its
recorded decisions, the systemic discoveries it made — and re-assess every remaining child against
them. A completed child routinely invalidates a sibling's premise in a way no line-anchor check
can see. Today the sweep is done by hand and done well, but **nothing names it and nothing records
it**, so a fresh-session successor cannot tell whether the sweep for child N already ran. It
either redoes it worse (it lacks the predecessor's context) or drops it silently.

## 2. What exists today, verified at `224ddace`

| Surface | State |
|---|---|
| `skills/epic-run/SKILL.md` Step 4 boundary section (`:125-230`) | No sweep step. Grep for `reassess`/`learnings sweep`/`boundary sweep` → **zero** |
| `docs/multi-issue-driver.md` | Same: zero |
| `hooks/driver_lib.py` | No sweep state of any kind |
| The by-hand procedure | `claude_docs/session_notes/epic-906-autorun-log.md` (D181 sweeps, three boundaries) and this wave's `epic-871-m4-wave-log.md` |

**Three append-only per-transition records already exist and are the template, not an analogy:**
`transitions` (a resolution event before the action, a terminal event after, correlated by
`resolution_id`), `advisory_deliveries` (`driver_lib.py:1914-1988` — a key constant, a pure reader
that never raises on a malformed state, a claim/close pair, and an `undelivered_advisories`
visibility backstop), and `transport_audit`. Each is declared in `docs/driver-state/queue.schema.json`
and **none of them bumped `schema_version`** — the schema sets `additionalProperties: true` and
`campaign_wait`'s own description (`:162`) states that an additive top-level field needs no bump.

## 3. The one decision that shapes everything: gate, or record?

This is the fork worth arguing, because getting it wrong in either direction is expensive.

**The sweep's substance is judgment no validator can verify.** Whether an engineer genuinely
re-read five issues against a finding is not observable from state. A gate on the *substance* can
therefore only check that *something was written* — attestation, not proof. This repo already has
the scar: the revalidation receipt stamps an audit `depth` that its own docstring calls "an
instruction to the auditor, not a property the validator checks", and closing that hole is a
separate issue (#944) in this same wave.

**But one property IS mechanically checkable: COVERAGE.** A sweep that names three of the five
remaining children is incomplete, and that is verifiable without any judgment at all. Coverage is
the honest middle: it cannot prove the thinking happened, but it can refuse a record that
demonstrably did not look at everything.

**Decision: a coverage-validated RECORD, and a fail-closed GATE on handing out the next child.**

**This reverses my own first draft, and the reversal is the most load-bearing thing in this
document.** I drafted record-plus-advisory (no hard gate) and argued three reasons for it; the
independent peer proposal (`gpt-5.6-sol`, §9) argued the opposite, and two of my three reasons do
not survive contact with it.

| My original reason | Why it does not hold |
|---|---|
| "A second hard gate doubles the wedge surface" | **The common refusal is self-clearable.** `revalidation_required` can need corrections posted before it opens; a `missing` sweep is always writable by the run itself, by doing the work it was ordered to do, and the refusal names that command. **Narrowed twice by Step-4 findings:** this is true of `missing` ONLY. `unreadable` needs state repair, for which this PR ships no API (§5), so it DOES stop an unattended run — honestly, and §4.3 now names the concrete repair rather than gesturing at "existing procedures". (A third status, `conflict`, existed in the pass-2 draft and was deleted at pass 3: it was both unreachable by the designed writer and unrepairable if reached.) The first draft claimed self-clearability for every rc-8 refusal, which was false. |
| "An attestation gate trains the empty attestation" | Partly true, and it is why coverage plus a per-child one-line reason is required rather than a bare boolean. Naming every remaining child with a reason is real friction against a bogus record — not proof, but not a checkbox either. |
| "The design authority only asks for state" | It asks for a minimum, and does not forbid a gate. |

What decided it is the **asymmetry of the two failure modes**. If the gate fires wrongly, the cost
is that the run does the work the owner ordered. If the advisory is ignored, the standing order
lapses silently — which is precisely the defect this issue exists to fix, and an autonomous run
skipping a step under context pressure is this repo's own catalogued mistake #8. There is also a
consistency argument: revalidation, the *weaker* check, already gates. Mechanizing the owner's
*stronger* order more weakly than the weaker one is backwards.

**What the gate honestly checks, stated in the docs so nobody over-reads it:** coverage and record
integrity — that a record exists for this head and names every remaining child with a reason. It
does **not** and cannot verify that the judgment behind it was any good. That limitation is written
into the contract prose deliberately, because this repo already has one audit stamp whose docstring
admits "depth is an instruction to the auditor, not a property the validator checks" (#944 in this
same wave exists to close that hole). Claiming more than coverage here would repeat it.

## 4. Design

### 4.1 State: one new append-only top-level field, `boundary_sweeps`

```jsonc
{
  "boundary_sweeps": [
    {
      "swept_at_head": "224ddace…",   // 40-char sha; with after_issue, the replay identity
      "after_issue": 927,             // the disposed child whose learnings drove this sweep,
                                      //   or null when the head moved with no child completing
      "learnings": "…what child #927 taught, non-empty…",
      "assessments": [                // EVERY remaining eligible child, exactly once
        {"issue": 769, "outcome": "commented",  "note": "…", "ref": "https://…"},
        {"issue": 726, "outcome": "unaffected", "note": "…"}
      ],
      "observed_at": 1754450000
    }
  ]
}
```

**The GATE keys on the head, not on the completed child** — the correction the peer proposal's
reasoning forced, and it makes the whole design smaller. What matters is not "did somebody sweep
after #927" but "has the remaining queue been assessed against the world as it is *now*". Head
identity gives that directly, needs no "which child completed last" derivation (driver-state does
not timestamp merges, so that derivation would have been guesswork), and makes the sweep the exact
sibling of the revalidation receipt, which already keys on `validated_head`.

**Replay identity is `(swept_at_head, after_issue)`, which is a different question from what the
gate asks.** Keeping the two apart is what closes the pass-2 defect below: the gate asks "is the
current world assessed?", replay asks "is this the same write twice?", and one field cannot answer
both.

**A head alone is NOT a boundary identity — pass-2 adversarial High, `correctness`, CONFIRMED
against my own two rules.** The reviewer showed that head-as-sole-key and refuse-differing-replay
combine into a silent skip. Worked through:

1. At head `H`, child A is **deferred**. A deferral moves no commit, so the head stays `H`. A sweep
   is recorded at `H` covering the then-eligible set `{B, C, D}`. The gate passes.
2. Later, still at head `H`, child B is **deferred** too. That is a genuine new boundary with its
   own learnings.
3. Under the draft: the gate finds a valid record at `H` and reports `swept`, so B's boundary is
   **silently skipped** — and an attempt to record it would be refused as a conflicting replay at
   the same key. Both halves fail at once.

This is real because §4.3 makes `deferred`/`abandoned` boundaries, and those dispositions do not
have to advance `origin/main`. Two changes fix it, and neither needs a new identifier:

- **Replay/conflict identity is `(swept_at_head, after_issue)`, not the head alone.** Two sweeps at
  one head with different `after_issue` are different boundaries and both may exist.
- **The gate compares coverage against the CURRENT eligible set, not merely against the recorded
  one.** A record is `swept` only when `swept_at_head == observed_head` AND its assessment
  issue-set equals `sweep_eligible_children(state)` **as it is now**. In the scenario above,
  disposing B shrinks the eligible set to `{C, D}` while the stored record covers `{B, C, D}` — the
  mismatch makes the gate report `missing`, exactly as it should.

**Why not the reviewer's `boundary_id` counter.** Its recommendation was a monotonically ordered
boundary id minted whenever a child is disposed or the head moves. That works, but it introduces a
second identifier, a minting site, and an ordering invariant to keep — and §9 already rejected a
composite id as a second representation of one fact. The coverage comparison above is not a new
mechanism at all: it is the property this design already validates at write time, now also read at
gate time. Same defect closed, nothing new to keep consistent.

**Replay policy, one rule, stated once (pass-1 adversarial High, `internal-consistency`).** The
first draft was self-contradictory: §4.1 said a second call at the same head returns `None`, while
§9 permitted a second, differing record at that key. That buys two contradictory authoritative
records with no defined winner and a gate that passes anyway. The rule:

- **Exact payload match at the same `(head, after_issue)` → `None`**, write nothing — the
  idempotent replay `record_child_outcome` already models.
- **Differing payload at the same `(head, after_issue)` → refuse the WRITE** (`DriverStateError`,
  CLI rc 2, nothing persisted). State therefore never holds two contradictory records for one
  replay identity: the contradiction is **prevented, not recorded**.

**`conflict` was a status in the pass-2 draft and is DELETED — pass-3 High,
`internal-consistency`.** The reviewer showed the two halves could not both be true: if a differing
replay writes nothing, no durable evidence of it exists, so `boundary_sweep_status` could never
return `conflict`. The table promised a state unreachable by the designed writer. Two repairs were
available — append a non-authoritative conflict marker so the status becomes reachable, or drop the
status and rely on the refusal. **Dropping is correct**, and a second pass-3 finding (High,
`feasibility`) is why: a durable `conflict` is a state that cannot self-clear, and this PR ships no
repair API for it, so making it reachable would manufacture an unrecoverable wedge to solve a
problem the writer already prevents. Refusing the write keeps state single-valued by construction.

**"Exact payload" is defined SEMANTICALLY, not bytewise (pass-2 Medium, flagged ambiguous).** A
bytewise rule would refuse an honest operational retry, because `observed_at` differs on the second
call and an equivalent assessment list may be ordered differently. Equality therefore: exclude
`observed_at`; sort `assessments` by `issue`; compare the remaining fields exactly. The FIRST
stored record keeps its timestamp — a retry never rewrites it.

**This reverses §9's "not adopted" line on conflicting replays, and the reversal is correct.** I
rejected refusal because it hands an operator a jam with no in-PR repair path. The objection was
real but mis-weighted: the alternative is not "no jam", it is a silently wrong authoritative
record, which is worse and much harder to notice.

### 4.2 Outcome vocabulary — closed, THREE words

| Outcome | Meaning | Extra requirement |
|---|---|---|
| `unaffected` | Re-read against the completed child's learnings; nothing changes for it | — |
| `commented` | A correction or rescope comment was posted on it | `ref` required |
| `rescoped` | Its scope or approach materially changed | `ref` required |

Closed, because every consumer in this module compares status words exactly; free text would make
the record unreadable. `note` is REQUIRED on every entry including `unaffected` — that is what
keeps the negative result honest while staying cheap ("does not touch the transport probe" is a
complete, truthful `unaffected` note). A `ref` is required for anything other than `unaffected`:
a claim that a child *changed* must point at the artifact.

**`blocked` was in the draft and is DELETED — Step-4 finding (adversarial, High, `correctness`).**
The reviewer showed that nothing consumed it: a sweep could record a child as needing a decision
first, and `next-child` would then hand out that very child. Shipping a vocabulary word no caller
honors is exactly the unwired-machinery defect D233 recorded against #927 PR 1. There were two
honest repairs — teach `next-child` to exclude `blocked` children, or remove the word — and
removal is correct here, because **a child that must not be handed out already has a mechanism**:
`pending_disposition` and the obsolete-child owner gate, which is #944's scope in this same wave.
A second, weaker blocking channel would collide with it and give two answers to one question. A
sweep that discovers a genuine blocker records `rescoped` with the `ref`, and the blocking itself
goes through #944's gate.

### 4.3 Pure functions in `hooks/driver_lib.py`

```python
BOUNDARY_SWEEPS_KEY = "boundary_sweeps"
SWEEP_OUTCOMES = frozenset({"unaffected", "commented", "rescoped"})

def boundary_sweeps(state) -> list                    # never raises on a malformed state
def sweep_eligible_children(state) -> list[int]       # status not in _DISPOSED_STATUSES
def record_boundary_sweep(state, *, after_issue, swept_at_head, learnings,
                          assessments, now_ts) -> dict | None   # None ⇒ write nothing
def boundary_sweep_status(state, observed_head) -> str
```

`record_boundary_sweep` **raises `DriverStateError`** — these are caller bugs, not states of the
world — on: an outcome outside `SWEEP_OUTCOMES`; an assessment for a child not in the queue
(foreign); a **missing** eligible child or a **duplicate** entry (the coverage rule is set
EQUALITY, both directions); an empty `learnings` or an empty `note`; a missing `ref` on a
non-`unaffected` outcome; a malformed `swept_at_head` (reuses `validate_validated_against`); any
over-long or control-character operator string — `learnings`, `note` **and `ref`** (reuses
`validate_operator_note`; the draft guarded only `note`, which was a self-review High: `ref` is
equally operator-supplied and equally rendered back on later reads). `now_ts` is injected — this
module takes no clock.

**`ref` is grammar-checked, not merely non-empty (pass-2 Medium, `correctness`).** The draft
required the field and then applied only length and control-character checks, so `"done"` would
have satisfied a rule whose entire stated purpose is "point at the artifact" — the contract
claimed more than the validator delivered, which is the same over-claiming this design criticizes
elsewhere. `ref` must now match ONE of: an `https://` URL, or a repository-relative path to a
durable record (`docs/…`, `claude_docs/…`). Anything else is refused before the write. The
alternative the reviewer offered — weaken §4.2 to call `ref` an unchecked annotation — was
rejected: the field exists precisely to carry evidence, so the honest repair is to check it.

**`after_issue` is validated, and it is nullable.** The draft required it and validated it
nowhere, which an adversarial finding showed lets an arbitrary or foreign number stand as
provenance while the gate still opens. It is now: **either `null`, or an int naming a child in
this queue whose status is in `_DISPOSED_STATUSES`** — never absent, never anything else.
`null` is a real and necessary value, not a loophole (self-review High): `origin/main` can move
with NO child completing, which happened in this very wave when PR #949 landed an unplanned
blocker fix between children. Without a nullable `after_issue` the gate would demand a record
that could not be written truthfully, and the run would wedge with no honest way out. A `null`
record's `learnings` must say why the head moved.

`boundary_sweep_status(state, observed_head)` is the successor's read path:

| Status | When | Consequence |
|---|---|---|
| `not_due` | No child has reached a disposed status yet — no boundary has happened | Proceed |
| `swept` | A valid record exists with `swept_at_head == observed_head` **whose assessment issue-set equals the CURRENT eligible set** | Proceed |
| `missing` | No such record — none at this head, or the newest one's coverage no longer matches the current eligible set | **Gate refuses** — self-clearable by `sweep record` |
| `unreadable` | `boundary_sweeps` is present but malformed | **Gate refuses** — needs the state repair named below |

`not_due` uses `_DISPOSED_STATUSES` (`merged`/`deferred`/`abandoned`), the SAME rule as
eligibility. The draft said "terminal status" in one place and used `_DISPOSED_STATUSES` in
another — two incompatible definitions in one document (self-review Medium), and `TERMINAL_STATUSES`
excludes `deferred`. A deferred child is a real boundary that produced real learnings; that is
usually *why* it was deferred.

`unreadable` is deliberately distinct from `missing`. Both fail toward doing the work, matching the
`boundary_consumed` fence — "I cannot read it" must never be reported as "it was done" — but they
need different remedies, and §3's self-clearable claim is true of `missing` ALONE.

**The `unreadable` repair, named concretely rather than gestured at (pass-3 High, `feasibility`).**
The draft said "the existing state-repair procedure" and never said what it was, which the reviewer
correctly called an unverifiable dependency underneath a hard gate. The concrete procedure, and it
needs no new command: `claude_docs/.driver-state/<campaign>.json` is **local, gitignored working
state, not a shared artifact** — the git-tracked contract-of-record is the schema, not the
instance. So repair is (1) copy the file aside, (2) delete the malformed `boundary_sweeps` key with
an editor or `jq`, (3) validate with `python3 -c "import json;json.load(open(...))"`, (4) re-run
`sweep record`, which rebuilds the record from evidence. Removing the key is safe precisely because
the field is additive and its absence reads as `missing`, never as `swept` — the fence direction is
what makes deletion a legitimate repair rather than a data loss. This is the same
rebuild-from-evidence posture `rebuild_receipt` takes toward unreadable revalidation records.

### 4.4 CLI in `hooks/launcher_lib.py`, and the gate

```bash
# 1. BEFORE assessing anything, capture the head the assessment will be about.
#    `sweep begin` prints JSON, so the sha must be extracted — assigning the whole
#    stdout would pass `{"head": …}` to --expected-head and refuse every record.
HEAD=$(python3 hooks/launcher_lib.py sweep begin --project-root . \
       | python3 -c 'import json,sys; print(json.load(sys.stdin)["head"])')

# 2. …do the five-part sweep against that head…

# 3. Record it, passing the captured head as a COMPARISON token:
python3 hooks/launcher_lib.py sweep record --driver-state <f> --expected-head "$HEAD" \
  --after-issue 927 --learnings "…" \
  --assess '{"issue":769,"outcome":"commented","note":"…","ref":"https://…"}' \
  --assess '{"issue":726,"outcome":"unaffected","note":"…"}' --project-root .

python3 hooks/launcher_lib.py sweep status --driver-state <f> --project-root .
```

**`--assess` takes ONE JSON object, not a colon-delimited string.** The draft wrote
`769=commented:"…":<url>`, and an adversarial finding (Medium, flagged ambiguous) pointed out that
a `ref` is normally an `https://` URL, which contains the delimiter — so a conforming input parses
into the wrong fields or is rejected. Notes contain colons too. JSON removes the grammar question
entirely rather than inventing an escaping rule.

**Compare-and-record, not observe-at-write (adversarial High, `correctness`).** The draft had
`sweep record` observe the head itself, reasoning by analogy with `--herdr-available`. The reviewer
showed the analogy fails: observing at *write* time does not bind the record to the head the
*analysis* was performed against. If main moves after the children are assessed but before the
command runs, the old assessments get stamped with the new head and the gate accepts them as
current — the exact staleness this gate exists to catch. So `--expected-head` is passed in and
treated as a **comparison token, never an authoritative assertion**: `sweep record` re-observes
the head under the state lock and refuses without writing unless it still equals `--expected-head`.
A caller cannot widen the check by lying, only fail it.

**Stdout contract for all THREE subcommands, specified rather than implied (pass-2 Medium, flagged
ambiguous).** The draft named three subcommands but gave an output contract for none of them, so
implementation and tests could disagree about what is machine-readable and whether a replay is
distinguishable from a new write:

| Subcommand | stdout JSON | rc |
|---|---|---|
| `sweep begin` | `{"head": "<40-char sha>"}` | 0; 5 if the head cannot be observed |
| `sweep record` | `{"result": "recorded" \| "replayed", "head": "…", "after_issue": <int\|null>}` | 0 recorded or replayed; 2 caller/data error or refused validation (including a differing replay); 5 head unobservable; 9 `--expected-head` no longer matches |
| `sweep status` | `{"status": "not_due\|swept\|missing\|unreadable", "observed_head": "…"}` | 0 `not_due`/`swept`; 3 otherwise |

**`--after-issue` is OPTIONAL, and omitting it means JSON `null` (pass-3 Medium, flagged
ambiguous).** The state contract makes `after_issue: null` load-bearing for a no-completion head
move, but the draft's only grammar showed an integer, leaving three incompatible guesses (omit,
the string `"null"`, or a separate flag) for a case an operator must be able to express. So:
omission maps to `null`; the literal text `null` is REJECTED, because accepting it would make a
typo indistinguishable from the intended value. The no-completion example:

```bash
python3 hooks/launcher_lib.py sweep record --driver-state <f> --expected-head "$HEAD" \
  --learnings "origin/main moved via PR #949 (test-clock fix); no child completed." \
  --assess '{"issue":726,"outcome":"unaffected","note":"unrelated to the test clock"}' \
  --project-root .
```

`replayed` is deliberately distinguishable from `recorded`: a caller that cannot tell them apart
cannot tell a no-op retry from a fresh boundary. Every operator line goes to **stderr** (the
previous child broke two tests by ignoring that split).

**The gate: `next-child` and `handoff` both refuse with a NEW `rc 8` when the sweep is
`missing` or `unreadable`.** rc 8 rather than 7, because `handoff` already spends 7 on
the one-successor fence loser, and one return code must not mean two different things across the
two commands that share this boundary. Implemented ONCE as a shared helper both commands call
(self-review Low: two copies could drift on ordering), positioned after the `revalidation_required`
check (rc 6) **and after the not-ready check (rc 3)** — so a fully merged campaign reports
"nothing ready" and is never asked to sweep an empty queue. It performs no state write.

**The refusal message differs by status, because the remedies differ:** `missing` names the exact
`sweep record` command; `unreadable` names the state-repair procedure of §4.3 and says that a
sweep must be recorded again afterwards.

**Any `observe_head` failure — launch, non-zero exit, or unparseable output — produces a non-zero
rc, an actionable stderr diagnostic, and NO state write** (adversarial Medium). It is never
allowed to degrade into an empty or malformed observation.

### 4.5 Prose (the two ACs that are documentation)

`skills/epic-run/SKILL.md` boundary section and `docs/multi-issue-driver.md` both gain the step
with its five parts, anchored on ONE canonical sentence pinned by a drift guard:

> **After every merged, deferred, or abandoned child — and whenever `origin/main` moves between
> children without a completion — sweep every remaining eligible child against the learnings for
> that boundary before selecting or handing off the next child.**

**The draft's sentence said "after every merged child", and that was a pass-2 High
(`consistency`).** The executable design treats `deferred` and `abandoned` as boundaries too, and
requires a sweep after a no-completion head move (the `after_issue: null` case). An operator
following the short sentence would omit sweeps the gate demands, and meet rc 8 with no idea why.
The canonical sentence now covers exactly the conditions the code enforces — which is the only
version worth pinning with a drift guard.

Both carry the 5-part procedure (list the completed child's findings/decisions/filed issues → sweep
every remaining child's CURRENT body against them → comment or rescope any child whose scope,
approach or deps changed → log a decision entry → only then start the next child) and cite the
field precedent: the D181 by-hand sweeps in `claude_docs/session_notes/epic-906-autorun-log.md`
plus this wave's log — **not** the issue body's `epic-756-autorun-log.md` D6 pointer, which its own
correction comment retires as unrecoverable (that log was trimmed 2026-08-02).

### 4.6 Known limitation shipped deliberately: a body edited after the sweep

**Pass-3 adversarial High, `correctness` — CONFIRMED, and NOT closed.** The gate keys on the head
and the eligible issue-number set, but the things assessed are mutable GitHub issue bodies. Editing
a remaining child's body without moving `origin/main` and without changing queue membership leaves
the stored record satisfying the gate, so a stale assessment can authorize the next child even
though the five-part procedure says each child's CURRENT body was read.

**What ships: nothing for this, deliberately.** The pass-3 draft added a `body_hash` per assessment
as partial evidence. **The Step-11 review deleted it**, and was right to: nothing computed it, no
CLI example supplied it, and the schema made it optional — so it would have been a field that is
always absent, advertising evidence that never exists. An optional-and-never-populated field is
worse than no field, because the design's own prose then over-claims.

**What a real fix needs, and why it is not here:** `boundary_sweep_status` would have to compare a
recorded hash against the LIVE issue body, which means GitHub reads inside a pure state function.
`driver_lib` promises no I/O and that promise is test-enforced by a source grep for `subprocess`
(`tests/hooks/test_driver_state_write_back.py:295-301`). That is a capability decision deserving
its own issue, not a side effect of this one.

**The hole is SHARED with the gate this one mirrors, not introduced by it** — verified rather than
assumed: `build_revalidation_record` stores a `body_hash`, and `_receipt_covers_child`
(`driver_lib.py:639`) compares only `to_sha == observed_head`, never the hash against a live body.
A post-revalidation body edit already evades the existing gate the same way. Residual recorded as a
run-record follow-up, not filed as an issue (D179).

## 5. What this deliberately does NOT build

- **No auto-derived learnings.** Nothing reads run-records to decide what a finding implies for a
  sibling. That inference IS the judgment the owner's order is about; a machine guess would be
  worse than the human one and would launder the requirement.
- **No LLM judge of sweep quality**, no issue-body semantic differ, no GitHub comment crawler, no
  body-snapshot archive (all four named and rejected in the peer proposal too).
- **No pending/terminal two-event protocol.** `transitions` needs one because a launch can die
  mid-flight and leave an indeterminate successor. A sweep record is written after the work is
  done, in one atomic locked append — there is no indeterminate window to model.
- **No `schema_version` bump.** Additive top-level field; three precedents.
- **No edit-in-place repair API.** A malformed record is repaired by the project's existing state
  procedures, not by a new mutation path in this PR — a second writer for a fence's own record is
  the parallel-mechanism trap #665 was rewritten to avoid.
- **No separate sweep log file.** The driver-state file is already the campaign ledger.
- **No sweep of DISPOSED children.** Nobody will be handed a merged or abandoned child.

## 6. Failure modes

| Failure | Behavior |
|---|---|
| `boundary_sweeps` malformed | Reader returns `[]`; status returns `unreadable`; the gate refuses (rc 8) naming the state-repair procedure, NOT the do-the-sweep command |
| A differing record at one `(head, after_issue)` | The WRITE is refused (rc 2); state stays single-valued. No durable `conflict` state exists to repair |
| Coverage incomplete or duplicated | `record_boundary_sweep` raises before any write; `_locked_state_update` writes nothing |
| `after_issue` names a non-existent, foreign, or still-active child | Refused before any write. `null` is the only non-child value accepted, and it means "the head moved with no child completing" |
| Two processes record the same boundary | Exact match is idempotent (`None`, no write); a differing payload is refused. The state-file lock serializes them |
| Head moves after a sweep | The old record no longer matches `observed_head`, so the gate refuses again — deliberately: the world changed under the assessment |
| **Head moves between assessing and recording** | `--expected-head` is re-compared under the lock and the write is REFUSED. This is the one the draft got wrong: observing at write time would have stamped stale assessments with a fresh head |
| `observe_head` fails (launch, exit code, or parse) | Non-zero rc, actionable stderr diagnostic, no state write. Never degrades to an empty observation |
| Campaign fully merged | `next-child` returns rc 3 (nothing ready) before the sweep gate is consulted, so a finished campaign is never asked to sweep an empty queue |
| Gate fires in an unattended run | `missing` is self-clearable — the refusal names the exact `sweep record` command. `unreadable` stops the run honestly and names the §4.3 repair procedure |

## 7. Security implications

The new attacker-adjacent surface is **every operator-supplied string** in the record — `learnings`,
each assessment's `note`, and each assessment's `ref`. All three land in durable state and are
rendered back on later reads, so all three go through `validate_operator_note` (200-char cap, no
control characters) — the same guard `transport unpark` uses, and for the same reason: an escape
sequence in an audit record would reach a console that never asked for it.

**The draft guarded `note` and silently omitted `ref`**, which my own self-review caught as a High:
the security section reasoned the hazard through for one field and then left an equally exposed
sibling unguarded. A `ref` is the field most likely to be pasted from elsewhere, which makes the
omission worse than arbitrary.

`after_issue` and every assessment `issue` are validated as canonical integers naming children of
this queue before they become keys, so no body-derived text becomes a state key. No new egress and
no new file path.

## Platform / external dependencies

platform_apis:
- api: refreshing remote-tracking head observation via `launcher_lib.observe_head` —
    `git -C <root> fetch <remote> +refs/heads/<branch>:refs/remotes/<remote>/<branch>`
    followed by `git rev-parse`, BOTH return codes checked
  feasibility: verified via existing-call-site — `hooks/launcher_lib.py:1743`, with the explicit
    refspec at `:1786-1787` and the fail-closed fetch check at `:1788-1792`; already invoked on
    this exact object kind and surface by `_cmd_next_child` (`launcher_lib.py:4249`) and
    `_cmd_handoff` (`launcher_lib.py:4782`); `sweep record` and `sweep begin` call the SAME
    function with the same argument shape
  failure: fail-loud
  surface: any launch failure, non-zero exit, or unparseable output raises `LauncherError`, which
    the CLI turns into a non-zero rc plus a `refusing: …` stderr diagnostic and NO state write —
    asserted by test, mirroring the existing rc-5 path
- api: advisory cross-process file locking on the driver-state file via
  `launcher_lib._locked_state_update`
  feasibility: verified via existing-call-site — `hooks/launcher_lib.py:3128`, the module's ONLY
    driver-state writer, already used for the receipt write (`:3672`), the claim (`:3527`) and the
    transport writes (`:4531`, `:4565`); `sweep record` adds a caller, not a mechanism
  failure: fail-loud
  surface: the mutate function returns `None` to write nothing, and every refusal path in
    `record_boundary_sweep` uses it — asserted by test that a refused record leaves the file
    byte-identical

**Why this replaced a bare `platform_apis: none` (adversarial Medium, `feasibility`).** The draft
declared `none` on the reasoning that both calls are already precedented, and the #226
working-precedent rule does exempt an exact call site. The reviewer's point stands anyway: the
design *relies* on git execution and on cross-process locking, and an omitted declaration hides
which surface fails and how. Naming them with their exact call sites costs six lines and makes the
`fail-loud` contract checkable. **Not adopted** from that finding: a new CI spike for lock
contention. `_locked_state_update` is the module's single existing writer with its own tests; a
spike proving a shipped, tested mechanism still works would be ceremony, and `<probe-before-design>`
requires a spike where a spike is the *evidence*, not where an exact call site already is.

## 8. Multi-PR assessment

**One PR.** Estimated ~250-350 changed lines across two hooks, two prose files, one schema and
four test files — well under the 500-line multi-PR threshold, and the prose contract and the state
it describes must land together or the drift guards pin a sentence describing machinery that does
not exist.

## 9. Peer consult provenance (WF13, cross-model, blind both ways)

An independent peer proposal was obtained through `hooks/review_runner.py consult`
(backend `gpt`, reviewer **`gpt-5.6-sol`**, author `claude-opus-5`, status `success`,
`diagnostic: true` — a consult never authorizes a fix round). My own draft was written to disk
BEFORE the result file was opened, per the Step-3 blindness rule.

**Independently converged** (both designs, neither having seen the other): one append-only
top-level field; the key binds the sweep to a head SHA, not to the completed child alone;
validated coverage over exactly the remaining set; a closed disposition vocabulary in which the
negative result stays cheap; a reader that never raises with `unreadable` kept distinct from
`missing` and both failing closed; idempotent replay returning "write nothing"; CLI subcommands on
the existing launcher with JSON on stdout and diagnostics on stderr; one canonical sentence pinned
by a whitespace-normalizing drift guard; tests first; and the same four rejected over-engineerings
(LLM quality judge, semantic body differ, comment crawler, schema-version bump).

**Adopted FROM the peer, against my own first draft:**

1. **The fail-closed gate** (§3). Its argument that a self-clearable attestation gate enforces
   ordering and gives the successor a durable answer beat my wedge-risk objection, because the
   wedge is one command deep.
2. **A `ref` is required whenever a child is reported as changed.** A "this child changed" claim
   with nothing to point at is the weakest possible evidence, and my draft would have accepted it.
3. **An explicit statement in the contract prose that the validator checks coverage and record
   integrity only** — never the quality of the judgment. Writing the limitation down is what stops
   the next reader over-trusting the stamp, which is exactly how the `depth` hole (#944) happened.
4. **`learnings` as a required non-empty field.** My draft recorded what was swept but not what it
   was swept *against*, which makes the record far less useful to a successor.

**Not adopted, with reasons:**

- **`revalidation_receipt_id` as a record field.** There is no receipt *id* in this schema to bind
  to; the receipt is keyed by `validated_head`. Keying the sweep on the same head achieves the
  intended coupling with no new identifier.
- **`boundary_id` as a derived `issue-<N>@<sha>` string.** A composite string alongside the
  authoritative component fields is a second representation of one fact, and the peer's own note
  concedes the components stay authoritative. Dropped as redundant.
- **A `learnings` sub-object of `summary`/`findings`/`decisions`/`filed_issues`.** Four fields where
  the honest content is one paragraph. A required non-empty string carries the same evidence at a
  fraction of the ceremony, and the campaign's D-entries and run-records already hold the
  structured versions.
- **`disposition: unchanged|changed`.** Two words lose which action was taken. The FINAL vocabulary
  is the three words `unaffected` / `commented` / `rescoped` (§4.2), which keep the action visible.
  **Corrected at pass 2 (Medium, `internal-consistency`):** this bullet previously still advertised a
  four-word vocabulary including `blocked`, contradicting §4.2's explicit deletion of it — an
  implementer could not tell which contract the design authorized. Blocking a child is represented
  ONLY through `pending_disposition` and the #944 obsolete-child owner gate, never through a sweep
  outcome.
- ~~**Refusing a conflicting replay at the same key.**~~ **REVERSED at the Step-4 gate — the peer
  was right and I was wrong.** I had kept idempotence on an exact match but tolerated a second,
  differing record at the same head, reasoning that refusing hands an operator a jam with no in-PR
  repair path. The adversarial design review showed what tolerance actually buys: two contradictory
  authoritative records at one head, no defined winner, and a gate that passes anyway. A jam an
  operator can see beats a wrong answer nobody can. §4.1 now carries the single executable rule, and
  the reader reports `conflict` as a fail-closed status. **Superseded at pass 3:** the refusal stands, but the `conflict` STATUS was deleted — a refused write leaves nothing durable to report, so the contradiction is prevented rather than recorded (§4.1).

## 10. Step-4 design gate — pass 1 dispositions

Pass 1 merged an inline self-review (security lens, 5 findings) with a cross-model adversarial
review of this document (`review_runner.py review-artifact --type design`, reviewer
`gpt-5.6-sol`, author `claude-opus-5`, `status: success`, 7 findings). Merged severity:
**7 High, 4 Medium, 1 Low** — over the `High: 5` volume threshold, so one `design` loop-back was
consumed (`design` 1/2, global 1/3) and the design was revised rather than waved through.

**Dispatch honesty:** the first adversarial dispatch DIED with no result file and no `END` line.
Under the vacuous-result gate that is a FAILED dispatch, not a slow one, so it was retried once in
the foreground per `<model-routing-resolve>`; the retry returned rc 0. Its `input_sha256` was
checked against this file on disk before any finding was consumed.

| # | Source | Sev | Disposition |
|---|---|---|---|
| 1 | adversarial | High | **Applied** — `after_issue` is now validated (a disposed child of this queue, or `null`), §4.3 |
| 2 | adversarial | High | **Applied, by DELETING `blocked`** rather than by adding a scheduling rule — §4.2 |
| 3 | adversarial | High | **Applied** — compare-and-record via `--expected-head`, re-observed under the lock, §4.4 |
| 4 | adversarial | High | **Applied** — §3's self-clearable claim narrowed to `missing` (pass 3 narrowed it again; `conflict` was later deleted) |
| 5 | adversarial | High | **Applied** — one executable replay rule. Reverses §9. (The `conflict` STATUS introduced here was deleted at pass 3; the write refusal it protected remains) |
| 6 | adversarial | Medium (ambiguous) | **Applied** — `--assess` takes one JSON object; the colon grammar is gone |
| 7 | adversarial | Medium | **Applied in part** — git + locking declared with exact call sites and fail-loud surfaces. The extra CI spike is declined, with the reason recorded in the Platform section |
| S1 | self-review | High | **Applied** — `after_issue` nullable (folded into #1) |
| S2 | self-review | High | **Applied** — `ref` guarded by `validate_operator_note`, §7 |
| S3 | self-review | Medium | **Applied** — `_DISPOSED_STATUSES` used for both eligibility and `not_due` |
| S4 | self-review | Medium | **Applied** — self-application called out below and destined for the PR body |
| S5 | self-review | Low | **Applied** — the gate is ONE shared helper both commands call |

**Ambiguity circuit breaker: fired once, on finding 6, and resolved WITHOUT escalation.** The
reviewer flagged it ambiguous because it could not tell how the colon grammar was meant to parse.
Reading the draft's own text answers it — `--assess 769=commented:"…":<url>` genuinely breaks on
the `://` in any URL — so the finding is correct and the remedy is a syntax choice with one
sensible answer, not an owner decision. This follows the D238/D240 pattern established earlier in
this wave: when a reviewer flags an ambiguity, read the code or text it is about.

### Self-application: this change gates the campaign that ships it

`next-child` is how the #871 M4 wave selects every remaining child. The moment this merges, the
wave's own driver-state — which has no `boundary_sweeps` — makes the next `next-child` call refuse
with **rc 8**. That is correct dogfooding and the first real exercise of the gate, but a successor
pane that has not read this document would reasonably read the refusal as a regression in the tool
it depends on. It is therefore called out here, in the boundary prose, and in the PR body, and the
rc-8 message names the exact command that clears it.

## 11. Step-4 design gate — pass 2 dispositions

Pass 2 ran a fresh cross-model review against the REVISED document (reviewer `gpt-5.6-sol`,
`status: success`, `input_sha256` re-checked against the file on disk). **6 findings: 2 High,
4 Medium** — under the `High: 5` volume threshold, so no volume loop-back; the Critical/High fold
returned `design` (one `design-flaw` + one `spec-tightening`), consuming the second and last
`design` loop-back (**`design` 2/2, global 2/3**).

| # | Sev | Disposition |
|---|---|---|
| 1 | High `consistency` | **Applied** — the canonical sentence covered only *merged* children while the code makes `deferred`/`abandoned`/no-completion-head-moves boundaries too. Rewritten to match what the gate enforces (§4.5) |
| 2 | High `correctness` | **Applied, by a different mechanism than recommended** — see below |
| 3 | Medium (ambiguous) | **Applied** — "exact payload" now defined semantically: exclude `observed_at`, sort assessments by issue, first record keeps its timestamp |
| 4 | Medium (ambiguous) | **Applied** — an explicit stdout JSON contract and rc table for all three subcommands; `replayed` distinguishable from `recorded`; §9's stale "two CLI verbs" corrected |
| 5 | Medium `correctness` | **Applied** — `ref` is grammar-checked (`https://` URL or repo-relative durable path), not merely non-empty. The offered alternative (weaken the contract to call `ref` an unchecked annotation) was rejected |
| 6 | Medium `internal-consistency` | **Applied** — §9 still advertised the four-word vocabulary including `blocked` that §4.2 had deleted |

**Finding 2 is the one worth reading.** It proved that head-as-sole-key plus refuse-differing-replay
combine into a silent skip: a `deferred` child moves no commit, so a second deferral at the same
head is a real boundary the gate would report as already swept, while the record for it would be
refused as a conflicting replay. Confirmed by walking my own two rules, not taken on trust.

**Adopted the defect, not the remedy.** The reviewer proposed a monotonically ordered `boundary_id`
minted at every disposal or head transition. The fix shipped instead is smaller and adds no new
identifier: replay identity becomes `(swept_at_head, after_issue)`, and the gate compares the
record's coverage against the **current** eligible set rather than only the recorded one. Disposing
another child shrinks that set, so a stale record stops satisfying the gate automatically. This
reuses a property the design already validates at write time instead of introducing a counter with
its own ordering invariant to maintain.

**Ambiguity circuit breaker: fired on findings 3 and 4, resolved WITHOUT escalation** — both were
under-specification in my own text (what "exact payload" means; what the subcommands print), each
with one sensible answer readable from the document itself. Same D238/D240 pattern as pass 1. That
makes six ambiguity-breaker firings across this wave, all resolved from code or text, none
escalated to the owner.

## 12. Step-4 design gate — pass 3, and the budget-exhausted CLOSE

Pass 3 reviewed the twice-revised document (reviewer `gpt-5.6-sol`, `status: success`,
`input_sha256` re-checked). **6 findings: 1 Critical, 3 High, 2 Medium.** The Critical/High fold
returned `design`, whose cap was already spent (2/2) while the global cap was not (2/3), the
ambiguity breaker completed `clear`, and every Critical/High finding carries a terminal
disposition — so the gate **CLOSED budget-exhausted** per the #798 carve-out rather than escalating
to the owner. Recorded with
`plan_lib.py close-design-gate --issue 769 --gate 4 … --breaker-result clear` (rc 0); the ledger is
`claude_docs/.wf2-state/769/dispositions.jsonl` and the run-record `extra` row is
`design_gate_close`. **This is a legitimate close, not an ERROR.**

| # | Sev | Disposition | Substance |
|---|---|---|---|
| 1 | **Critical** `correctness` | applied | The §4.4 example assigned `sweep begin`'s whole stdout to `$HEAD`, but that stdout is `{"head": …}` — so every documented `--expected-head` would have been JSON and every record refused, leaving the rc-8 gate permanently uncleared. A real bug in a copy-pasteable command. Fixed, and the three-command sequence is now an end-to-end test |
| 2 | High `correctness` | **deferred** | Issue bodies are mutable: a body edited without moving the head or changing queue membership leaves a stale record satisfying the gate. §4.6 |
| 3 | High `feasibility` | applied | `conflict`/`unreadable` were hard-gate states with no named repair. `conflict` is deleted entirely; `unreadable`'s repair is now spelled out concretely |
| 4 | High `internal-consistency` | applied | `conflict` was unreachable by the designed writer — a refused write leaves no evidence to report it. Deleted rather than made reachable |
| 5 | Medium `ambiguity` | applied | `--after-issue` omission maps to `null`; the literal string `null` is rejected; a full no-completion example added |
| 6 | Medium `feasibility` | **REFUTED** | See below |

**Finding 6 is refuted with evidence, not waved off.** It claimed the design proves only local
`rev-parse` execution and never establishes that the remote-tracking ref is refreshed, so a stale
local ref could let an old sweep authorize selection. Read at source, that is wrong about the
shipped code: `observe_head` builds an explicit refspec (`launcher_lib.py:1786-1787`) and runs
`git -C <root> fetch <remote> +refs/heads/<branch>:refs/remotes/<remote>/<branch>` BEFORE the
`rev-parse`, checking BOTH return codes and failing closed on the fetch (`:1788-1792`). Its own
docstring records that #840 round-13 introduced exactly that refspec to kill exactly this
stale-ref hazard. The grain of truth — my Platform block described the call as "rev-parse-based",
understating it — IS applied: the declaration now names the fetch, the refspec, and the
fail-closed check with file:line.

**Ambiguity breaker across the whole gate: fired on 6 findings over three passes and resolved every
one from code or text, none escalated.** Pass 3's three flagged items were an unnamed repair path
(resolved by deleting the unrepairable state and naming the remaining one's procedure), the
`--after-issue` null grammar (one sensible answer), and the fetch-freshness claim (settled by
reading `observe_head`). Consistent with D238/D240, established earlier in this wave.

**Gate outcome:** design ACCEPTED as amended, with one documented deferred limitation (§4.6). Three
adversarial passes produced **1 Critical + 7 High + 8 Medium + 1 Low across 18 findings; 17 applied
(one partly), 1 refuted.**

## 13. Step-11 pre-PR review — dispositions

One cross-model pass over the committed diff (`review_runner.py review-code --base origin/main`,
reviewer `gpt-5.6-sol`, author `claude-opus-5`, `status: success`, `diagnostic: false`, `head_sha`
verified equal to HEAD before any finding was consumed). **6 findings: 4 High, 2 Medium — all six
CONFIRMED, two of them by running the code, and all six fixed.**

| # | Sev | Finding | Fix |
|---|---|---|---|
| 1 | High `completeness` | The prose claimed campaign creation seeds `boundary_sweeps: []`, but the only seeding anywhere was **in a test helper** — so every NEW campaign would have inherited the migration exemption and the gate would never have fired for anyone | Seed it in the real creation seam, `transport resolve-creation`, with a test that drives the production path |
| 2 | High `correctness` | `boundary_sweep_status`, documented as never raising, threw `TypeError` on an unhashable `issue` value — a corrupt file became a `next-child` OUTAGE instead of an rc-8 refusal. **Reproduced before fixing** | Total validation; every failure becomes `unreadable` |
| 3 | High `security` | Key absence is the grandfathering marker, and **my own documented repair told operators to DELETE the key** — turning the repair into a permanent bypass of the gate | Repair now RESETS the field to `[]`; both the CLI refusal text and the two prose surfaces say so, with the reason attached |
| 4 | High `security` | The reader checked head + issue-number set and nothing else, so a hand-written record with no `learnings`, no outcomes and no notes read as `swept`. **Reproduced: it returned `swept`** | `sweep_record_is_intact`, shared with the write path; one corrupt record makes the whole field `unreadable` |
| 5 | Medium `correctness` | `observe_head` and the `--expected-head` comparison ran BEFORE the lock, so the head could move between the check and the append — the exact staleness compare-and-record promises to refuse | The authoritative comparison moved INSIDE `_locked_state_update`; a move there returns `None`, so nothing is written |
| 6 | Medium `internal-consistency` | `body_hash` was optional in code and "always recorded" in the design; no caller computed it | The field is **removed** and §4.6 rewritten to say plainly that nothing ships for that limitation |

**Note on findings 1, 3 and 6: all three are the same class of defect — prose that claimed more
than the code delivered.** Seeding that existed only in a test, a repair procedure that disarmed
the thing it repaired, and a field advertised as evidence that nothing populated. That is worth
recording as the lesson of this review, because each individually reads as a small slip and
together they are a pattern: the design was written before the code, and three claims were never
re-checked against what actually shipped.

**Ambiguity breaker: fired once (finding 1) and resolved from the code** — the question was whether
a production creation path existed to seed; `transport resolve-creation` is it. Not escalated,
consistent with D238/D240.

**Loop-back budget after this round: `design` 2/2, `review` 1/1, global 3/3 — fully spent.** The
Step-11 reopen token was minted before the review (which is what debited `review`), so this fix
round is authorized; there is no budget for another. Any further finding must be dispositioned
without a loop-back.
