# #923 — the WF2-lite lane for disposable work, and a loop-back budget that charges for rounds instead of reviews

**Status:** design v4, WF2 Step 3 pass 3 (v1 blind draft → v2 peer synthesis → v3 applied pass-1's
6 High + 2 Medium → v4 applies pass-2's merged 8 High + 4 Medium, incl. owner decision D302) · **Issue:** #923 (epic #906 M2) · **Date:** 2026-08-08
**Head:** `ee38c542` (v3.141.9) · **Baseline:** 6460 passed, 0 failed, exit 0

## Peer consult provenance (WF13, backend `gpt`, reviewer `gpt-5.6-sol`)

Drafted v1 blind first, per the blindness rule; the peer proposal was read only afterwards
(`.rawgentic-peer-result-923.json`, status `success`, `diagnostic: true`, attempts 1).

**Independent convergence** — both designs reached, separately: reservations live in
`loopback_counters.json` (one file, one lock, because the invariant spans counters and
reservations); **open the round first, commit second**, because debit-then-refund fails toward
destroying budget; availability counts committed **plus** outstanding against BOTH caps; no
automatic time-based expiry and no GC; `os.replace` for the write; reconciliation is explicit and
operator-driven.

**Adopted from the peer, replacing my v1:**

1. **A `settled_commits` ledger**, making a retried commit idempotent. v1 §7 named the strict
   unknown-nonce refusal as *the claim I would most expect to be wrong*; the peer independently
   proposed the ledger as its v1. Two designs converging on the problem and one solving it is
   enough evidence to adopt it now rather than defer.
2. **A durable round record as the linearization point** — commit VALIDATES that a round opened
   instead of trusting the caller's assertion. See §2.7: this required a feasibility probe, and the
   peer's own stated weakest claim turned out to be refuted.
3. **Richer reservation identity** (`workflow`, `run_id`, `session_id`, `requested_by`) beyond
   F11's `issue`/`gate`/`run`.
4. **fsync the containing DIRECTORY** after `os.replace` — atomic visibility is not durability;
   also constraint **C2** from #761's D204 fold. **Adopted with a correction (see §2.4):** the
   repo's `atomic_write_lib.atomic_write_text` fsyncs the temp file only, so this ships as a new
   OPTIONAL `fsync_dir=False` parameter on that shared helper rather than as a property that
   already existed.
5. **The `reconciliation_log` lives inside the state file**, not a sibling `.jsonl` — consistent
   with the same one-file-one-lock reasoning both designs used for reservations.
6. **Splitting each step's ARTIFACT and OPT-IN sub-steps into their own matrix rows.** My v1 folded
   them into one COLLAPSED value per step, which blurs exactly the distinction the issue insists on
   ("what a permissive class may drop is an artifact or an opt-in sub-step — never a step").
7. **A drift test that renders the matrix FROM `plan_lib` and compares every documented cell**
   (peer R8). This repo already trusts drift guards over prose; the matrix must be one.
8. **The lens guard inspects the rendered prompt as well as the manifest** (peer R9) — a manifest
   can claim four lenses while the prompt carries one.

**Kept from v1 against the peer:** the three-value core vocabulary stays small (the peer's six
values collapse to three once artifacts and opt-ins are their own rows); the `disposable` class
keeps Step 4 at its collapsed rubric-only form rather than FULL, because the issue's own text names
the adversarial-on-design and peer consult as the droppable opt-ins there.

---

## 0. What the prior work actually left behind — read this first

#923 says to start from `docs/planning/2026-08-04-761-proportionality-contract-design.md` and "not
repeat" it. That doc's handover section (lines 447-452) says the inherited material is "recoverable
from this file's git history and `claude_docs/.wf2-state/761/s4-adv-r4.json`".

**The git-history half is false.** `git log --all --follow` over that path returns exactly ONE
commit — `1a03b9cb` (2026-08-05, PR #926) — and that commit IS Revision 7, the shipped version.
Revisions 1-6 lived in the working tree and were never committed. The per-class ceremony matrix, the
`disposable` definition of done, the reservation state machine, its schema validation, the atomicity
contract, its seven tests, the recovery surface, the cross-version concurrency bound and the D202
author-permission check are **not in git anywhere**.

What survives is `s4-adv-r4.json`: 11 findings that are the **critique** of Revision 4, not the
design. So this design is written fresh. The 11 findings are treated as known-failure constraints,
and the three that govern this issue's two halves are carried explicitly below (F3, F4, F11 for
Part B; F5, F6, F7 for Part A).

This section exists so the next reader does not spend an hour looking for a document that is gone.

---

## 1. The problem, stated as two independent defects

**Defect B — the budget charges for the wrong event.** `_cmd_review_reopen` calls
`consume_loopback` at MINT time (`plan_lib.py:3229`), so asking for permission to open a fix round
spends the same budget as actually opening one. A review that returns zero findings — the case the
budget exists to protect — bills identically to a review that opens a round. With
`GLOBAL_LOOPBACK_BUDGET = 3` and `design` capped at 2, two clean reviews can exhaust a gate's
entire design budget without a single line of code having changed.

**Defect A — the task class has no teeth.** #761 shipped the class (`disposable | internal |
production`), resolved once and snapshotted write-once, but nothing reads it to change what a run
demands. There is no matrix, no definition of done for the cheap lane, and no guard preventing the
cheap lane from quietly becoming a cheap *review*.

The two halves share one property that makes them one issue: **both are places where "less
ceremony" can silently become "less safety"**, and both are fixed by making the reduction
structural rather than documentary.

---

## 2. Part B — reserve, then debit when the round opens

### 2.1 The state machine

A reservation has exactly three states. There is no fourth, and deliberately no timer.

```
                 review-reopen --source S
   (none) ───────────────────────────────────────▶ OUTSTANDING
                 mints nonce, writes reservation      │
                 NO counter moves                     │
                                                      │
       disposition opens a round (§2.8)                │  disposition opens NO round
         1. loopback-open-round --nonce N             │   loopback-release --nonce N --reason R
         2. loopback-commit     --nonce N             │
            (adjacent, nothing between)               │
                    ┌─────────────────────────────────┴──────────────────────────┐
                    ▼                                                            ▼
               COMMITTED                                                     RELEASED
    counters[S] += 1, total recomputed,                       reservation deleted, no counter moves,
    reservation deleted, settled_commits[nonce] set,          empty collections pruned (§2.2b),
    empty collections pruned — ONE locked write               availability restored
```

**Crash analysis, gap by gap.** This is the part F3 was raised against, so every gap is named:

| Crash point | On-disk state | Consequence | How it is cleared |
|---|---|---|---|
| after mint, before disposition | `OUTSTANDING` | budget reserved, not spent; availability correctly reduced | `loopback-reconcile` (§2.5) |
| during `open-round`, mid-lock | unchanged (atomic replace — §2.4) | no round record; nothing charged | re-run; the reservation is still valid |
| after `open-round`, before `commit` | `OPENED_UNCOMMITTED` (derived: reservation + matching `rounds` entry) | **under-charged** — the round is real but unbilled | `loopback-status` classifies it; reconciliation commits it |
| during `commit`, mid-lock | wholly outstanding OR wholly committed, never between | either state is consistent | re-run `commit` with the same nonce |
| after `commit` writes, before its response | `COMMITTED` + `settled_commits` entry | correct | a retry returns `(True, "already_committed")` |
| during `release`, mid-lock | still `OUTSTANDING` | nothing lost | re-run; release is idempotent (§2.3) |

The third row is the deliberate trade, and it is now *detectable* rather than merely accepted. F3
says a crash between debit and round-open must not charge for a round that never happened. **We open
the round first and charge second, always.** Under-charging is visible (`loopback-status` reports
`opened_uncommitted`, and Step 16's completion gate refuses while the current run owns an
outstanding reservation — peer R4); over-charging silently destroys budget the owner paid for.
Given a choice of which way to be wrong, be wrong in the direction an operator can see.

The `OPENED_UNCOMMITTED` state is **derived, not stored** — it is a reservation joined to a matching
`rounds` entry. Storing it would create a fourth write that could itself crash halfway.

### 2.7 The round record — a probe, and a refuted assumption

The peer's design has `commit` validate a **canonical round record** with `state: "open"`, and names
as its own most-likely-wrong claim: *"the existing workflow can define round-open as one durable
canonical record."*

**Probed at `ee38c542`. The assumption is REFUTED — no such record exists today.** What exists:

- `claude_docs/.wf2-state/<issue>/review_log.jsonl`, written by `plan_lib.append_review_log`
  (`:1374`). An entry records that a review *ran* and its verdict:
  `{"task_id":"T1","sha":"e1cb5cda","reviewers":[...],"verdict":"applied","findings":{...},"ts":...}`.
- `claude_docs/.wf2-state/<issue>/dispositions.jsonl` — one record per FINDING
  (`{"schema_version":1,"id":"d-761-5-1-62ee","issue":761,"pass":5,"gate":"4","finding":{...}}`).

Neither is a round-open linearization point. `review_log` is written *after* a verdict exists, and
`dispositions` is per-finding. Building `commit` on either would mean inferring "a round opened"
from an artifact that means something else.

**So #923 creates the record**, because without a durable artifact the AC's ordering requirement —
"the debit must not commit before the round actually opens" — is a convention rather than a
guarantee, and a convention cannot be tested. It is deliberately minimal: one JSON object written
under the SAME lock and the same atomic replace as everything else, in the same state file.

```python
def open_fix_round(path: str, nonce: str, *, actor: str) -> tuple[bool, str, dict]:
    """Durably record that a fix round opened. THE linearization point.
    IDEMPOTENT: a nonce that already has a `rounds` entry returns that SAME
    round_id and writes nothing."""
```

*(A3 — a hole the self-review missed.)* Without that idempotency, a write that succeeds while its
response is lost lets a retry mint a SECOND `round_id` for one nonce; `commit_loopback` then refuses
because more than one round matches, and the real round becomes permanently uncommittable and
unbilled. The rule is therefore: **at most one `rounds` entry per nonce, enforced on write.** A
second call is a lookup, not an insert. Test 12 covers it.

`rounds` is a fourth top-level key mapping `round_id` → `{nonce, issue, workflow, gate, run_id,
session_id, state: "open", opened_at}`. `commit_loopback` then refuses unless exactly one `rounds`
entry matches the nonce AND every immutable identity field agrees (peer R7). The ordering is
enforced by data, not by hope: there is no way to commit a nonce whose round record is absent.

Scope note, stated rather than buried: this is one function, one key, and one validation clause more
than v1 had. It is in scope because it is the only thing that makes the AC's central ordering claim
verifiable.

### 2.2 The data shape

`loopback_counters.json` gains FOUR new top-level keys. No migration, and this is not an assumption —
see §5.

```json
{
  "design": 1, "tdd": 0, "review": 0, "review_design": 0, "spec_tighten": 0, "total": 1,
  "reservations": {
    "e3b0c44298fc1c14": {
      "source": "design", "issue": 923, "workflow": "WF2", "gate": "4",
      "run_id": "2e336392-7c84-4a2e-b8e9-bab4961c11b7",
      "session_id": "2e336392-7c84-4a2e-b8e9-bab4961c11b7",
      "requested_by": "wf2-step-04", "created_at": "2026-08-08T04:41:00Z"
    }
  },
  "rounds": {
    "r-923-4-a91c": {
      "nonce": "e3b0c44298fc1c14", "issue": 923, "workflow": "WF2", "gate": "4",
      "run_id": "2e336392-...", "session_id": "2e336392-...",
      "state": "open", "opened_at": "2026-08-08T04:44:00Z"
    }
  },
  "settled_commits": {
    "d41d8cd98f00b204": {
      "source": "design", "issue": 923, "gate": "4", "round_id": "r-923-4-77b2",
      "run_id": "2e336392-...", "committed_at": "2026-08-08T04:12:00Z"
    }
  },
  "reconciliation_log": [
    {"event_id": "8f14e45fceea167a", "nonce": "abc123...", "action": "release",
     "actor": "operator:chris", "reason": "run 51f74d5b died mid-gate", "round_id": null,
     "at": "2026-08-08T04:50:00Z"}
  ]
}
```

`reservations`, `rounds` and `settled_commits` are maps keyed by nonce / round_id / nonce. Maps, not
lists, because every operation is a lookup by key and a map makes double-insert structurally
impossible.

**Every reservation field is required.** `source`, `issue`, `gate`, `run_id` cover F11's demand —
a record that cannot be attributed cannot be reconciled by anyone — and `workflow`, `session_id`,
`requested_by` come from the peer, so a reservation names the workflow and the actor as well as the
run. `created_at` is recorded for the operator and is **never read by any decision**; that is what
makes automatic time-based expiry impossible by construction rather than by policy. A field nothing
branches on cannot silently restore budget.

### 2.2a The token shape — v2 shipped one the runner REFUSES

*(Self-review S4 — the most serious finding of pass 1.)* v2 adopted the peer's token verbatim:
`{"schema_version": 2, "kind": "review_reopen_reservation", "nonce": ..., "reserved_at": ...}`.
Read at `review_runner.py:184-213`, `load_reopen_token` requires:

```python
if data.get("version") != 1:                      # -> "unsupported version None"
    return None, f"reopen token {path!r}: unsupported version {data.get('version')!r}"
for key in ("source", "nonce", "minted_at"):      # each a non-empty string
    ...
if data.get("consumed_at"):                       # a spent token is refused
    ...
```

The v2 token has no `version` and no `minted_at`, so the runner would refuse it outright — while
this design's own text claimed "the runner's dispatch-time `diagnostic` contract is untouched".
That claim was false as drafted.

**The shipped token keeps every field the runner reads, verbatim, and ADDS the identity fields
alongside them:**

```json
{
  "version": 1,
  "source": "design",
  "nonce": "e3b0c44298fc1c14",
  "minted_at": "2026-08-08T04:41:00Z",
  "issue": 923, "workflow": "WF2", "gate": "4",
  "run_id": "2e336392-...", "session_id": "2e336392-...", "requested_by": "wf2-step-04"
}
```

`version` stays `1` because it names the shape the RUNNER validates, and that shape is unchanged —
additive fields do not bump it. `consumed_at` is still absent at mint and is still what marks a
token spent. Re-probing `load_reopen_token` against a real minted token is **task 2 of the plan**,
so this fix is verified rather than asserted.

### 2.2c The nonce is the key — NOT the token file (owner decision D302)

*(Adversarial A8, and the finding that tripped the ambiguity breaker.)* Verified at `ee38c542`:
`review_runner.consume_reopen_token` (`:214-224`) stamps `consumed_at` on the token after an
actionable success, and `load_reopen_token` (`:184-213`) REFUSES any token carrying `consumed_at`
("already spent"). v3 handed that same token file to `loopback-open-round` and `loopback-commit`
AFTER the runner had run — so with the existing loader those commands would have refused every token
that actually did its job. That function's docstring also encodes the contract this issue overturns:
*"the debit happened at mint time in plan_lib review-reopen"*.

**Resolved by the owner (D302): the new commands take `--nonce` and never re-load the token file.**
The runner's `consumed_at` stamp is then irrelevant to them, `review_runner` keeps its behaviour
byte-for-byte — which is what the AC's "the runner's dispatch-time `diagnostic` contract is
untouched" actually requires — and only that one stale docstring line is corrected in this PR.
Nothing is lost: the reservation record in the state file, not the token, was always the identity
authority. Rejected: a second spent-tolerant loader (two loaders, one file format, guaranteed
drift), and redesigning the token lifecycle (would need the protective AC relaxed).
**Undo:** revert the subcommands to `--token` and add the spent-tolerant loader; no data migration,
because the state file already holds identity.

### 2.2b Empty collections are pruned — AC7's headline test demands it

*(Self-review S5.)* AC7's first test is "a zero-finding review leaving the counters **byte-identical**".
Under v2, `authorize` wrote `"reservations": {...}` and `release` emptied it, so a file that
started with no `reservations` key came back carrying `"reservations": {}` — different bytes, and
the AC's own test would fail against the design meant to satisfy it.

**Invariant:** `release_loopback` and `commit_loopback` DELETE `reservations`, `rounds` and
`settled_commits` from the state dict when they become empty, so an `authorize → release`
round-trip over a pre-existing file is genuinely byte-identical. `reconciliation_log` is the one
exception — it is an audit trail, and an entry there is a deliberate record that something happened,
so a run that required an operator release is *supposed* to differ.

`settled_commits` is what makes a retried `commit` idempotent: a nonce present there returns
`(True, "already_committed")` without incrementing again, so a caller that crashes after a
successful commit and retries gets the truthful answer instead of v1's indistinguishable refusal.
It grows for the life of the issue and is **never garbage-collected automatically** (peer R3) —
`loopback-status` reports its size, and archival happens only when the whole issue state is closed.

The nonce is 16 hex chars from `secrets.token_hex(8)`. It is an identifier, not a capability: the
counters file already lives inside the repo's own state dir, so its job is collision-avoidance and
attribution, not secrecy.

### 2.3 The capacity formula — F4, in one expression

Availability is checked against committed **plus outstanding**, per-source AND global. Both clauses,
every time, at mint:

```python
outstanding_by_source[s] = sum(1 for r in reservations.values() if r["source"] == s)
outstanding_total        = len(reservations)

source_ok = counters[s] + outstanding_by_source[s] <  _LOOPBACK_SOURCE_MAX[s]
global_ok  = counters["total"] + outstanding_total  <  GLOBAL_LOOPBACK_BUDGET
authorize  = source_ok and global_ok
```

`outstanding_total` is `len(reservations)` rather than a sum over sources deliberately: a
reservation whose `source` somehow fell outside `_LOOPBACK_SOURCES` would vanish from a
per-source sum and inflate global availability. Counting the map's length cannot miss one. (Schema
validation refuses such a record at write time; this is the belt to that braces.)

Note the strict `<`. `consume_loopback` today tests `>=` on the post-state; here we are asking
whether one MORE would fit, so the comparison is against the cap directly.

### 2.4 The surface added to `hooks/plan_lib.py`

Three new functions and two new CLI subcommands. `plan_lib.py` stays the single choke point, as the
AC requires, and `review_runner.py`'s dispatch-time `diagnostic` contract is untouched.

```python
def authorize_loopback(path: str, source: str, *, issue: int, workflow: str, gate: str,
                       run_id: str, session_id: str,
                       requested_by: str) -> tuple[bool, str | None, dict]:
    """Reserve one loop-back WITHOUT debiting. Returns (ok, nonce, state)."""

def open_fix_round(path: str, nonce: str, *, actor: str) -> tuple[bool, str, dict]:
    """Durably record that a fix round opened. THE linearization point (§2.7)."""

def commit_loopback(path: str, nonce: str, *, actor: str,
                    reason: str = "disposition-open") -> tuple[bool, str, dict]:
    """Convert an outstanding reservation into a committed count. Refuses unless a
    matching `rounds` entry exists and every identity field agrees. IDEMPOTENT via
    `settled_commits`: a nonce already committed returns (True, "already_committed")."""

def release_loopback(path: str, nonce: str, *, actor: str,
                     reason: str) -> tuple[bool, str, dict]:
    """Discard an outstanding reservation, restoring availability. IDEMPOTENT:
    an unknown nonce returns (True, "already_released", state)."""

def loopback_status(path: str) -> dict:
    """Read-only. Classifies each reservation outstanding | opened_uncommitted,
    and reports settled_commits size (peer R3)."""
```

```
# UNCHANGED required args; the six identity args are all OPTIONAL (self-review S3)
python3 hooks/plan_lib.py review-reopen     --state-file <f> --source <S> --out <token.json> \
        --project-root . [--issue <n>] [--workflow WF2] [--gate <id>] [--run-id <r>] \
        [--session-id <s>] [--requested-by <a>]
# --nonce, never the token file (D302, owner decision — see §2.2c); --project-root on ALL (A7)
python3 hooks/plan_lib.py loopback-open-round --state-file <f> --nonce <n> --actor <a> --project-root .
python3 hooks/plan_lib.py loopback-commit     --state-file <f> --nonce <n> --actor <a> --project-root .
python3 hooks/plan_lib.py loopback-release    --state-file <f> --nonce <n> --actor <a> --reason <t> --project-root .
python3 hooks/plan_lib.py loopback-status     --state-file <f> --project-root .
```

**Backward compatibility is a hard requirement, not a nicety.** *(Self-review S3.)* v2 made the six
identity args required. Read at `plan_lib.py:3537-3546`, today's parser requires only
`--state-file`, `--source`, `--out`, `--project-root` — and the invocation printed in `SKILL.md`'s
`<model-routing-resolve>` block passes exactly those four. Making the new args required would break
every existing call site the moment this merges. They are therefore **optional**, and each has a
defined fallback:

| arg | fallback when omitted |
|---|---|
| `--issue` | parsed from the `--state-file` path (`.wf2-state/<issue>/`), else `null` |
| `--workflow` | `"unknown"` |
| `--gate` | `"unknown"` |
| `--run-id` / `--session-id` | `$CLAUDE_CODE_SESSION_ID` if set, else `"unknown"` |
| `--requested-by` | `"unspecified"` |

A reservation carrying `unknown` fields is still reconcilable by nonce and still counts against
capacity; it is merely less attributable, and `loopback-status` marks it `identity: partial` so the
gap is visible rather than silent. Call sites are updated to pass the real values **in this same
PR** (§2.8), so `unknown` is the compatibility floor, not the expected state.

Every mutating call takes `file_lock(path)` for its whole read-modify-write, exactly as
`consume_loopback` does. Within the lock: read, validate, decide, mutate, **recompute `total` from
the per-source values**, write. The `total` recompute is not optional and not implicit — it is the
same invariant `consume_loopback` maintains (`state["total"] = sum(state[s] for s in
_LOOPBACK_SOURCES)`), and omitting it would let a committed count and its total disagree on disk
until the next read repaired it. *(Self-review S8.)*

**The write routes through `hooks/atomic_write_lib.atomic_write_text(..., fsync=True)`. It does NOT
reimplement tmp+replace.** *(Self-review S1.)* That module's own docstring is explicit: "Every
python hook that atomically writes a file routes through `atomic_write_text`; do not reimplement
mkstemp/os.replace inline (that is exactly the duplication this module removed — nine divergent
copies, three of them weaker)." v2 of this design proposed exactly that reimplementation and would
have made the tenth copy.

**The directory-fsync fork, resolved rather than assumed.** *(Self-review S2.)* v2 adopted a
containing-directory fsync from the peer (also #761 constraint C2). Read at `ee38c542`,
`atomic_write_text` fsyncs the **temp file** (`f.flush(); os.fsync(f.fileno())`) and then
`os.replace` — there is **no** directory fsync, so the property v2 claimed did not exist. Two ways
out; taking the first:

1. **Add an OPTIONAL `fsync_dir=False` parameter to `atomic_write_text`** and pass `True` from the
   loop-back writes only. Additive and default-off, so every one of the module's existing call
   sites keeps byte-identical behaviour, and the blast radius is one new branch guarded by a
   default-false flag.
2. Drop the claim and state the durability bound as "atomic visibility, not crash durability".

(1) wins because the counters file is exactly the artifact where a lost rename costs real budget,
and because (2) would leave this design asserting a weaker guarantee than the constraint it
inherited. The cost is honest and named: one shared helper gains one optional parameter, and its
existing tests must show the default path unchanged.

**Path containment.** *(Self-review S7.)* Every new subcommand applies the same refusal
`_cmd_review_reopen` already applies at `plan_lib.py:3242` — a `--token`/`--out` that resolves
outside `--project-root` is refused before any read or write. The new commands do not relax it and
do not invent a second rule.

`commit` and `release` are called by **disposition**, never by `review_runner`. That is the AC's
"disposition owns the debit, not runner success" made structural: the runner has no CLI path to
either.

**Identity is re-checked on every operation** (peer R7): a token replayed against a different issue,
gate, run or round is refused with a named mismatch rather than silently accepted.

**The release/commit asymmetry, now resolved rather than merely admitted.** Release returns success
for an unknown nonce, exactly as the AC requires. Commit refuses an unknown nonce — if it did not, a
bug that lost a nonce would open a fix round for free, which is the accounting hole this issue
closes. In v1 that made a retried commit-after-success indistinguishable from a genuine bug, and v1
§7 named it as its weakest point. **`settled_commits` removes the ambiguity:** a nonce found there
returns `(True, "already_committed")`; a nonce found nowhere returns `(False, "unknown_nonce")`.
The two cases are now distinct in the data, not left to the caller to infer.

### 2.8 The exact call sites — named, not described

*(Self-review S6; #761's D204 constraint C6 was exactly "cite an exact direct call site, not
prose".)* v2 specified functions and a CLI but never said who calls them or when, which left the
under-charge window (§2.1 row 3) as a sequence any caller could get wrong with nothing naming the
right order.

Every gate that can open a fix round follows the same three-phase shape. The files are the WF2
step references in `skills/implement-feature/references/`:

| Phase | Command | Call site | When |
|---|---|---|---|
| authorize | `review-reopen` | `step-04.md` item 7 · `step-06.md` · `step-08a.md` · `step-09.md` · `step-11.md` | before dispatching a review that MAY open a round |
| open | `loopback-open-round` | the same step's **disposition** branch | the instant the gate decides a fix round opens — before any fix work |
| commit | `loopback-commit` | the same disposition branch, immediately after `open` returns | — |
| release | `loopback-release` | the same step's **no-round** branch | the gate decided no round opens (including a zero-finding review) |

WF3's equivalents (`skills/fix-bug/`) take the same shape and are updated in the same PR.

Two rules that make the ordering hard to get wrong:

1. **`open` and `commit` are adjacent, always.** No review dispatch, no file edit, and no other
   gate logic sits between them. The window in §2.1 row 3 exists because a process can die
   anywhere, not because a caller is expected to do work in the gap.
2. **The no-round branch must call `release`.** A gate that simply returns without releasing leaks
   a reservation, and that leak is exactly what `loopback-status` exists to surface. Step 16's
   completion gate refuses while the current run owns an outstanding reservation, so the leak
   cannot reach a green run silently.

### 2.5 Reconciliation — recoverable, logged, never automatic

```
python3 hooks/plan_lib.py loopback-reconcile --state-file <f> --project-root . \
        [--commit <nonce> --actor <a> --reason <text> | --release <nonce> --actor <a> --reason <text>]
```

*(A1: v3's crash table said reconciliation COMMITS an `opened_uncommitted` round while this CLI
offered only inspect and release — so the only recovery available restored capacity for a round that
really had opened, after which the nonce could never be committed and the round completed unbilled.
`--commit` is the missing half, and it validates the `rounds` entry exactly as `loopback-commit`
does.)*

With no `--release`, it is **read-only**: it prints every outstanding reservation with its
`issue`/`gate`/`run`/`created_at` and the resulting availability, and exits 0. That is the "logged"
half — an operator can always see what is holding budget and who created it.

With `--release <nonce>` it releases, and with `--commit <nonce>` it commits (A1), each under the
lock, appending its audit entry to the **in-file** `reconciliation_log` (§2.2) in the SAME locked
write. *(A6: v3 still named a sibling `reservation_log.jsonl` here, contradicting §2.2's schema.
There is one audit store, inside the state file, so release and its evidence are atomic together.)*

**No timer, no sweep, no `--release-all-older-than`.** The AC prohibits automatic time-based expiry
because it would silently restore spent budget, and the prohibition is enforced by there being no
code path that reads `created_at` to make a decision. An operator releasing a stale reservation is
making a judgment and leaving a record; a cron job doing it is erasing evidence.

### 2.6 The malformed-map contract

A `reservations` value that is not a dict, or any record that fails schema validation, makes
`authorize_loopback` **refuse without rewriting the file**:

```python
ok, nonce, state = authorize_loopback(...)   # -> (False, None, {"error": "malformed_reservations", ...})
```

The refusal is loud (CLI exit 4, message on stderr naming the offending nonce and field) and the
file is left byte-identical for an operator to inspect. This is the direct answer to "must refuse
authorization without rewriting the file, with a real error surface" — a validator that repaired
the file would destroy the evidence of how it got corrupted.

Deliberately NOT extended: `_read_loopback_state`'s existing "corruption resets to 0" behaviour for
the five integer counters. That is pre-existing, is not in this issue's scope, and changing it would
alter behaviour for every run that never touches a reservation.

---

## 3. Part A — the per-class gate matrix

### 3.1 The matrix

Rows are WF2 steps; columns are the three classes. **Every cell is filled.** The vocabulary is
exactly three values and they are defined, not suggestive:

- **FULL** — the step runs as the spine describes it.
- **COLLAPSED** — the step runs with its ceremony reduced: no multi-approach brainstorm at Step 3,
  a checklist rather than a full decomposition at Step 5, evidence-only at Step 9. The gate still
  executes and still returns a verdict. This is the existing small-standard lane's meaning of
  "collapse", reused rather than reinvented.
- **n/a** — the step's own condition is unmet (not a class reduction).

**A step row says nothing about WHERE the step's output lands. The artifact row directly beneath it
says that, and only it does** *(review finding F1, Critical, 2026-08-08)*. The earlier wording
defined COLLAPSED as "produces its output in session notes instead of a separate committed
artifact", which made `internal` unimplementable: its Step 3 read COLLAPSED while its artifact row
read KEEP, and no implementation can satisfy both. The two rows are now strictly orthogonal — the
step row governs ceremony, the artifact row governs the committed file — so every combination of
the two is coherent. `internal` collapses Step 3's ceremony AND keeps its committed design doc,
which is exactly the intended lane and was previously inexpressible.

**Where a COLLAPSED step's output goes when its artifact row reads DROP** *(F2)*: into
`claude_docs/session_notes.md` under that step's own section, closed by the step's ordinary
`### WF2 Step <N>: <Name> — DONE (#<issue>: …)` marker, which is already the load-bearing resume
contract. The consuming gate reads the section between that step's header and its DONE marker. No
new location, schema or parser is introduced, because the marker grammar already exists and is
already tested.

There is no `SKIP` value in the vocabulary. That is the mechanism, not a convention: a class cannot
skip a mandatory step because the matrix has no way to express it.

| Row | disposable | internal | production |
|---|---|---|---|
| 1 Receive issue | FULL | FULL | FULL |
| 2 Analyze codebase | FULL | FULL | FULL |
| 3 Design (the STEP) | COLLAPSED | COLLAPSED | FULL |
| ↳ separate design-doc ARTIFACT | DROP | KEEP | KEEP |
| ↳ peer consult (OPT-IN) | off | on | on |
| ↳ adversarial-on-design (OPT-IN) | off | off | on |
| 4 Design gate (the STEP) | FULL | FULL | FULL |
| ↳ quality-bar rubric | FULL | FULL | FULL |
| 5 Implementation plan (the STEP) | COLLAPSED | COLLAPSED | FULL |
| ↳ separate plan-file ARTIFACT | DROP | DROP | KEEP |
| 6 Plan drift | COLLAPSED | COLLAPSED | FULL |
| 7 Branch | FULL | FULL | FULL |
| 8 Implementation — red-before-green | FULL | FULL | FULL |
| 8a Per-task review (when high-risk) | FULL | FULL | FULL |
| 9 Drift gate | COLLAPSED | FULL | FULL |
| 11 Code review (the STEP) | FULL | FULL | FULL |
| ↳ reviewer COUNT | 1 | 1 | 2 |
| ↳ lens coverage, union of the wave | ALL 4 | ALL 4 | ALL 4 |
| 11.5 Security scan | FULL | FULL | FULL |
| 12 PR | FULL | FULL | FULL |
| 13 CI | FULL | FULL | FULL |
| 16 Completion + run-record | FULL | FULL | FULL |

Deliberately not classified, and why:
- Step 10 — background, never blocks — nothing for a class to scale.
- Step 14 — owner-gated and capability-gated, not class-gated.
- Step 15 — owner-gated and capability-gated, not class-gated.

**Why Steps 10, 14 and 15 carry no row** *(F9, 2026-08-08)*. The matrix classifies only steps a
class could plausibly reduce. Step 10 (memorize) is background and never blocks, so there is
nothing for a class to scale. Steps 14 (merge/deploy) and 15 (post-deploy) are owner-gated and
capability-gated, not class-gated — an unattended run stops at the PR whatever the class. Their
absence is a declared exclusion, not an unclassified surface, and the renderer in §3.1a emits that
exclusion list alongside the table so the drift test can assert it.

*(Self-review S9: v3's provenance claimed this row-splitting as peer adoption 6, but its body still
carried the v1 step-only table — the doc claimed an adoption it had not made. A step row and its
artifact/opt-in rows are now distinct, which is the whole point: a class may drop an ARTIFACT or turn
off an OPT-IN, and can never change a step row. Step 4 reads FULL for every class because the STEP
always runs; what `disposable` drops is the two opt-in sub-rows beneath Step 3, not the gate.)*

The reviewer-COUNT row is the one place a class scales a **demand**, and the coverage row directly
beneath it is why that is safe: **demands scale, lenses never do** (F5's resolution).

**B6, decided (2026-08-08). Coverage is asserted over the UNION of the wave's briefs, never
per-reviewer.** The earlier wording carried both readings — §3.3 said union while the matrix row said
"lenses per reviewer: ALL 4" — and an implementer could not tell which the guard asserts. Union is
the only reading compatible with the shipped spine: `<review-lens-routing>` already assigns Step 11
Reviewer 1 the `mechanical` + `bug_logic` lenses and Reviewer 2 `architecture` + `security` (#492).
A per-reviewer reading would make that shipped split fail its own guard. Under union the row is true
in every column with no second meaning available: `production` covers all four across its two
briefs, and `disposable` and `internal` have a wave of ONE brief, so the union IS that one brief and
their single reviewer must carry all four. The row is therefore named *lens coverage, union of the
wave*. Outside this decision block, which exists to record the rejected reading, no sentence in this
design states a per-reviewer lens requirement — and the drift test in §6 asserts exactly that, so the
ambiguity cannot creep back in prose.

**B9, decided (2026-08-08). Rows are typed, and each kind has its own enum.** v4 claimed a
three-value vocabulary while its own cells also used `DROP`, `KEEP`, `on`, `off`, counts and `ALL 4`.
Untyped rows weakened the no-`SKIP` argument, because a reader could not tell which enum governed a
given cell. The four row kinds:

| Row kind | Enum | Example row |
|---|---|---|
| **step row** | `FULL` · `COLLAPSED` · `n/a` — and nothing else, which is where the no-`SKIP` guarantee lives | `3 Design (the STEP)` |
| **artifact row** | `KEEP` · `DROP` | `↳ separate design-doc ARTIFACT` |
| **opt-in row** | `on` · `off` | `↳ peer consult (OPT-IN)` |
| **count row** | a positive integer, or the literal `ALL 4` for a coverage count | `↳ reviewer COUNT` |

The no-`SKIP` guarantee is a property of the **step-row enum only**. `DROP` and `off` are legal in
their own kinds precisely because neither can appear in a step row, so no class can express skipping
a step. The renderer in §3.1a emits the kind alongside every row, and the drift test compares kind
and value together.

The four bold rows are the **never-reducible** set: Step 8's red-before-green, Step 8a for any
`riskLevel: high` task, Step 11, and Step 11.5. They read FULL in every column.

**What is actually structural, and what is not — stated plainly** *(F6, and §7 carries it as the
claim most likely to be wrong)*. Two different mechanisms hold these four rows, and only one of them
is a runtime guarantee:

- **Structural, enforced at runtime:** the step-row ENUM has no `SKIP` value, so no class can
  *express* skipping a step. The renderer refuses an illegal cell and the drift test refuses a
  documented one. That makes the four rows unreducible **in the matrix**.
- **NOT structural — prose plus a drift test:** whether a given run actually EXECUTES Step 8's
  red-before-green, Step 8a, Step 11 and Step 11.5 is enforced the same way every other WF2
  mandatory step is enforced, by the spine's prose and its guard tests. `assert_lens_coverage`
  narrows exactly one failure inside Step 11 — a reviewer dispatched with lenses missing. It does
  not prove Step 8a ran, and it does not prove Step 11 consumed a verdict.

An earlier revision said §3.3 "makes that structural", which overclaimed: a lens guard cannot make
a different step run. The matrix removes the ability to *declare* a reduction. Making each step's
execution independently provable is a larger piece of work than this issue, and it is named here
rather than implied away.

What a permissive class actually drops is only ever an **artifact** or an **opt-in sub-step**:
Step 3 writes a design note in session notes rather than a committed `docs/planning/*.md`; Step 4
runs the in-repo quality-bar rubric but not the opt-in adversarial-on-design or peer consult. The
gate still runs and can still fail the run. This is exactly the existing small-standard lane's
meaning of "collapse", reused rather than reinvented.

### 3.1a The renderer — the table above is generated, not typed *(F7)*

AC5 requires the matrix to be rendered FROM code with a drift test comparing every documented cell.
The previous revision referenced this section twice without writing it, so nothing defined the
source of truth. It is:

```python
# hooks/plan_lib.py
CLASS_MATRIX_EXCLUDED_STEPS = {"10": "background, never blocks",
                               "14": "owner-gated, not class-gated",
                               "15": "owner-gated, not class-gated"}

# row id -> (kind, label, {class: value})
CLASS_MATRIX: dict[str, tuple[str, str, dict[str, str]]] = {...}

def render_class_matrix() -> str:
    """The markdown table exactly as it appears in the design doc and the skill."""
```

Row `kind` is one of `step`, `artifact`, `opt_in`, `count` — the four kinds §3.1 defines. The
renderer validates each row's value against its kind's enum before emitting, so an illegal cell
fails at render time rather than shipping into prose. `render_class_matrix` emits the table AND the
excluded-step list, so both are generated from one source.

```
python3 hooks/plan_lib.py render-class-matrix --project-root .
```

**The drift test** (`tests/test_class_matrix.py`) slices the table out of BOTH call sites — this
design doc and the skill prose — by header index, whitespace-normalizes, and asserts equality with
`render_class_matrix()`. It anchors on one canonical header per file rather than a whole-corpus
regex, per this repo's drift-guard convention. It also asserts, independently of the text: every
step row's value is in the step enum, so no class can ever express skipping a step.

### 3.2 The `disposable` definition of done

A `disposable` run is DONE when all of:

1. Every red-before-green cycle in the plan has a commit showing the failing test first.
2. Step 11 returned a verdict from a reviewer carrying **all four lenses** (§3.3), and every
   Critical and High finding is resolved or carries a recorded deferral.
3. Step 11.5 ran to completion, and every blocking finding is resolved. **At least one scanner must
   have actually executed** *(F4, and #761's known-failure F6)*. An absent scanner is recorded as a
   visible skip in session notes AND the PR body — but a run where EVERY scanner was absent has
   scanned nothing, and it does not satisfy this clause. It fails the gate and says so, because a
   step that is never-reducible cannot be satisfiable by recording that it did not run. The earlier
   wording allowed exactly that, which is the failure #761's F6 named.

   **"Actually executed" is read off the existing report, not judged** *(F5)*.
   `hooks/security_scan.py` already returns a per-scanner `skipped[]` over the fixed set
   `{secrets, sca, sast, iac}`. The clause is satisfied when at least one of those four is NOT in
   `skipped[]` and the scan returned an exit code — process start alone is not execution, and an
   unreadable result is not a pass. No new criterion or reporting surface is invented.

   **This cannot make DONE unreachable in this repo**, which the review rightly asked to be shown
   rather than asserted: `.github/workflows/ci.yml` installs gitleaks, semgrep and osv-scanner, so
   the secrets, SAST and SCA scanners are present on the CI runner. trivy and pip-audit are
   deliberately omitted and their absence is the ordinary recorded skip. A project that genuinely
   has no scanner at all would fail this clause, and that is the intended reading, not a
   regression — a class may drop an artifact, never a scan.
4. The full suite passes against the baseline recorded at Step 2, with the delta stated.
5. A PR exists, and its body carries the class, the matrix row that applied, and the Step-11.5
   skip list if any.
6. The Step-16 run-record persisted with rc 0.

What is NOT required: a committed design document, a committed implementation-plan file, an
adversarial-on-design pass, a peer consult, or a workflow-diagram REV entry when no spine changed.

Note items 2 and 3 are the same demands `production` makes. That is the point: **the definition of
done differs in artifacts, never in gates.**

### 3.3 The lens guard — what makes it structural

The failure this guards against is measured, not hypothetical: an earlier draft dispatched a lane
reviewer "on the security lens" only, silently dropping `mechanical`, `bug_logic` and
`architecture`.

**B5, decided (2026-08-08). ONE input: the wave's dispatch manifest. Both surfaces are reached
through it.** v4 gave the function brief STRINGS and the CLI only `--brief`, while the prose also
required inspecting a manifest and a rendered prompt. Three inputs were implied and none was
specified, so no implementer could build it. The resolution collapses them to one, because the
manifest is the only artifact that knows how many reviewers the wave has — and a guard fed loose
brief files cannot tell a two-reviewer wave from a one-reviewer wave that lost a brief.

```python
REVIEW_LENSES = ("mechanical", "bug_logic", "security", "architecture")

def assert_lens_coverage(manifest: dict, *, project_root: Path) -> tuple[bool, list[str]]:
    """Every lens must appear across the union of the wave's briefs, on BOTH surfaces.

    Returns (ok, problems). `problems` names each missing lens and the surface it is missing
    from, so a caller never has to guess which half failed.
    """
```

```
python3 hooks/plan_lib.py assert-lens-coverage --manifest <f> --issue <n> --project-root .
```

**The manifest shape**, written by Step 11 BEFORE it dispatches:

```json
{"issue": 1002, "step": "11", "task_class": "production",
 "reviewers": [{"id": "r1", "lenses": ["mechanical", "bug_logic"],
                "prompt_file": "claude_docs/.wf2-state/1002/step11-r1-prompt.md"},
               {"id": "r2", "lenses": ["architecture", "security"],
                "prompt_file": "claude_docs/.wf2-state/1002/step11-r2-prompt.md"}]}
```

**The discovery rule**, one per surface, both mechanical:

- **The manifest** lives at `claude_docs/.wf2-state/<issue>/step11_dispatch.json`. That is the same
  per-issue state directory §3.4 already uses for the class snapshot, so no new location is
  introduced. The CLI takes `--manifest` explicitly rather than deriving the path, because the gate
  must fail loudly on a missing manifest instead of silently resolving a default that is not there.
- **Each rendered prompt** is `reviewers[].prompt_file`, resolved relative to `--project-root`. A
  path that escapes the project root, or that names a file that does not exist, FAILS the guard. It
  is never skipped, because an unreadable surface is exactly the state a silent drop produces.

**The manifest must be THIS wave's manifest** *(F5, 2026-08-08)*. A guard that accepts any
well-formed manifest is defeated by a stale one, another issue's one, or a fabricated one — the
coverage would pass while the actual wave lost a lens. So before either coverage check runs, the
guard binds the manifest to the run on three axes, and any mismatch is exit 2:

- `manifest.issue` equals the `--issue` the caller passed.
- `manifest.task_class` equals the class in the write-once snapshot at
  `claude_docs/.wf2-state/<issue>/task_class.json` (§3.4). The snapshot is the single source of the
  class, so a manifest may not restate it differently.
- `len(manifest.reviewers)` equals the reviewer COUNT the matrix gives that class — 1 for
  `disposable` and `internal`, 2 for `production` — **and every `id` is unique and every
  `prompt_file` is unique** *(F2)*. Length alone does not prove count: two duplicate entries would
  otherwise certify a `production` wave that is really one reviewer.

**Validated bytes are the dispatched bytes** *(F1)*. The three axes above identify the RUN, not the
payload, so on success the guard writes `claude_docs/.wf2-state/<issue>/step11_lens_ok.json` holding
a sha256 of the manifest and of each prompt file. Step 11 dispatches exactly those files, and the
dispatch refuses if any digest no longer matches. Without that, a stale manifest for the same issue
and class could pass while different prompt bytes went out.

**Then the two coverage checks, both required.** (1) The union of every `reviewers[].lenses` covers
all four. (2) For each reviewer, its rendered prompt carries one delimited section per lens that
reviewer claims. Either check failing is a failure, and stdout names the lens and the surface.

**The delimiter grammar, stated so two implementations cannot disagree** *(F6, 2026-08-08)*. A lens
section opens with a line that is exactly `<!-- lens:<name> -->` and closes with a line that is
exactly `<!-- /lens:<name> -->`, where `<name>` is one of the four `REVIEW_LENSES` values. The
rules: the markers must be alone on their line, the open and close names must match, each lens may
appear **at most once** per prompt file (a duplicate is exit 2, not a pass), sections may not nest,
and an unclosed section does not count as coverage. **A section whose body is empty or whitespace
only does not count as coverage either** *(F3)* — four immediately-closed markers would otherwise
certify a prompt that carries no lens instructions at all, which is the exact silent lens-loss this
guard exists to stop. Matching is exact and case-sensitive, so no fuzzy heuristic can drift between
the guard and its drift test. The `<!-- … -->` form is chosen
because the prompt files are markdown and an HTML comment renders as nothing, so the delimiters
cannot leak into what the reviewer reads as instructions.

Check 2 exists because a manifest can claim four lenses while the prompt carries one, and checking
only the manifest would pass exactly that. This is still weaker than proving the reviewer *applied*
a lens — no static check can — and §7 says so plainly. What it prevents is the specific measured
failure: a reviewer dispatched on one lens while the record claimed four.

Exit 0 = every lens covered on both surfaces. Exit 1 = at least one problem, named. Exit 2 = the
manifest is missing or malformed, which is a caller error and never a vacuous pass.

**Wired as a gate, not a helper — and it runs BEFORE the dispatch, not before consuming the result**
*(F3, 2026-08-08)*. The measured failure is a reviewer DISPATCHED with three lenses silently
dropped. A check that runs after dispatch detects that failure after the tokens are spent and the
diff has already gone to a wrongly-configured reviewer, so it prevents nothing. The manifest is
written before dispatch by construction (§3.3's discovery rule), so the guard has everything it
needs at the only moment where refusing is still cheap. Step 11 therefore calls it immediately
after writing the manifest and BEFORE the first reviewer is dispatched. A non-zero exit blocks the
dispatch. A guard nobody calls is documentation, and a guard called too late is a post-mortem.

**The wiring is prose plus a drift test, and an alternate dispatch path is not fenced out** *(F4)*.
This repo has exactly one review entry point, `hooks/review_runner.py` (D179), and Step 11 is
orchestrated by SKILL.md prose the way every other WF2 gate is. So the guard is wired the same way
every gate here is wired, and a drift test pins the prose that calls it. What that does NOT give is
a runtime fence: an orchestrator that ignored the prose and called the runner directly would bypass
the guard, exactly as it could bypass any other WF2 gate today. Closing that would mean moving the
check inside `review_runner.py` itself, which is a larger change than this issue and would couple
the runner to per-issue WF2 state. It is named here as a known limit rather than claimed away.

### 3.4 Who chooses the lane — F7

Nobody chooses it per-gate. The class comes from exactly one place: the write-once snapshot at
`claude_docs/.wf2-state/<issue>/task_class.json`, already shipped by #761 and already immutable for
the life of the issue. Every gate reads the snapshot; no gate takes a class argument, and no config
key can override it mid-run. Reproducible (the same issue always resolves the same way) and
auditable (the snapshot records `provenance` and any `diagnostic`).

---

### 3.5 Acceptance-criteria traceability *(self-review S10)*

| AC | Where it is satisfied |
|---|---|
| AC1 — normative per-class matrix, every value defined, no class skips a mandatory step | §3.1 (the table + the three-value vocabulary with no `SKIP`), pinned by test 11 |
| AC2 — `disposable` definition of done | §3.2 |
| AC3 — lane's single reviewer carries the full lens set, a guard asserts it | §3.1 lenses row, §3.3 `assert_lens_coverage` (manifest + rendered prompt), test 10 |
| AC4 — `review-reopen` authorizes without debiting; debit only when a round opens | §2.1 state machine, §2.4 `authorize_loopback`/`commit_loopback`, §2.8 call sites |
| AC5 — the atomicity contract, all seven clauses | lock §2.4 · capacity §2.3 · disposition-owns-debit §2.8 · no-debit-before-open §2.1+§2.7 · idempotent release §2.4 · recoverable, no auto-expiry §2.5 · malformed refuses without rewriting §2.6 |
| AC7 — the seven tests | §6 tests 1-7; tests 8-13 are additions this design adds beyond the AC |

## 4. Alternatives weighed and rejected

- **Debit at mint, refund on no-round (Part B).** Simpler — no new state, one new "refund" call.
  Rejected: a crash between the review returning and the refund landing leaves the budget
  permanently spent, which is the exact failure mode the AC exists to remove. Reserve-then-commit
  fails toward under-charging; debit-then-refund fails toward over-charging.
- **A separate `reservations.json` file (Part B).** Cleaner separation. Rejected: two files cannot
  be updated under one lock without a second lock and an ordering rule, and the invariant that
  matters — committed + outstanding ≤ cap — spans both. One file, one lock, one invariant.
- **A `SKIP` value in the matrix with a documented never-skip list (Part A).** Rejected: this is
  precisely F6's failure — "visible skips" made it expressible to declare a disposable run done
  without Step 11.5. Removing the vocabulary is stronger than adding a rule about it.
- **Per-class reviewer COUNT reduction (Part A).** Rejected as F5: the previous matrix cut
  disposable review from two agents to one while claiming review is never reducible. This design
  keeps the single-reviewer form for the cheap lane but requires that one reviewer to carry all
  four lenses, enforced by §3.3 — demands scale, lenses do not.

---

## 5. Platform / external dependencies

platform_apis:
- api: an unknown top-level key round-tripping through `plan_lib._read_loopback_state` →
  `_write_loopback_state` without a schema migration
  feasibility: verified via existing-call-site — `_read_loopback_state` (`hooks/plan_lib.py:1976`)
  normalizes ONLY the five names in `_LOOPBACK_SOURCES` plus `total` and returns the decoded dict
  otherwise untouched; `_write_loopback_state` (`:1993`) re-serializes that whole dict with
  `_json.dump(..., sort_keys=True)`. No key filter exists on either side, so a `reservations` key
  survives a read/write cycle. Read at `ee38c542`.
  failure: fail-loud
  surface: AC7's "a zero-finding review leaves the counters byte-identical" test reads the file
  before and after and compares bytes, so a dropped or reordered key fails that test by name.
- api: `os.replace` for the atomic counters write (§2.6 hardening)
  feasibility: verified via existing-call-site — the same temp-then-replace pattern is already used
  in this repo for state writes under lock; `os.replace` is atomic within a filesystem on POSIX.
  failure: fail-loud
  surface: a failed replace raises `OSError` inside `file_lock`, which propagates out of the CLI as
  a non-zero exit; no partial file is ever visible to a reader.
- api: `os.fsync` on a DIRECTORY file descriptor (the `fsync_dir=True` path, §2.4)
  feasibility: verified via spike — REQUIRED before implementation, and it is task 3 of the plan.
  The exact shipped invocation is `fd = os.open(dirname, os.O_RDONLY); os.fsync(fd); os.close(fd)`
  on this host's real filesystem AND in CI (ubuntu-latest, the `test` lane). No in-repo call site
  exists today — `atomic_write_lib` fsyncs the temp FILE only — so an existing-call-site claim would
  be false. *(A10: v3 asserted the durability property with no declaration at all.)*
  failure: fail-loud
  surface: the `OSError` propagates out of `atomic_write_text` inside `file_lock`, so the CLI exits
  non-zero and no partial state is visible. On a filesystem where directory fsync is unsupported,
  that exit IS the surface — the design does not silently degrade to a weaker durability promise.
- api: `secrets.token_hex` for the reservation nonce
  feasibility: verified via existing-call-site — Python standard library, already used in-repo for
  non-secret identifiers.
  failure: fail-loud
  surface: n/a — a failure raises at mint, before any state change.

---

## 6. Tests (AC7's seven, plus three)

Every one is `tests/hooks/test_plan_lib_reservations.py`, black-box through the module under a
`tmp_path` state file, per this repo's testing philosophy.

1. **Zero-finding review leaves counters byte-identical** — authorize, then release; read the file's
   bytes before authorize and after release; assert equality. Covers the headline AC.
2. **Two concurrent authorizes with one slot left, exactly one wins** — real threads against one
   `file_lock`; assert exactly one `ok=True` and that committed+outstanding never exceeds the cap.
3. **Idempotent double-release** — release twice; both return `(True, ...)`, second is
   `"already_released"`.
4. **Refused unknown-nonce debit** — debit a nonce that was never minted → `(False,
   "unknown_nonce")`, counters unchanged.
5. **Crash between mint and disposition leaves the reservation outstanding** — simulate by never
   calling debit/release; assert the reservation is still present, availability is reduced, and
   `reopen-reconcile` reports it.
6. **Release restores availability** — fill to cap with reservations, release one, assert one more
   authorize now succeeds.
7. **Each malformed shape refuses without rewriting** — parametrized over: `reservations` not a
   dict; a record not a dict; each required field missing; `source` not in `_LOOPBACK_SOURCES`.
   Assert refusal, the named error, AND that the file's bytes are unchanged.
8. **Global cap binds across sources (F4)** — outstanding reservations in three different sources
   with the global cap at 3; assert a fourth authorize in a source with per-source room still
   refuses on the global clause.
9. **Commit moves exactly one counter** — authorize `design`, **`open_fix_round`**, THEN commit;
   assert `design` +1, `total` recomputed, reservation gone, `settled_commits` set, other sources
   untouched. *(A5: v3 wrote "authorize, debit" and skipped the round-open entirely — a test that
   contradicted the ordering contract the whole design exists to enforce.)*
10. **Lens guard** — a manifest+prompt naming all four lenses passes; one naming only `security`
    fails and names the three missing (§3.3).
11. **Matrix drift** *(A2)* — render the matrix FROM `plan_lib.CLASS_GATE_MATRIX` and compare every
    documented cell in this doc and in the skill prose; a deliberately altered doc cell fails it.
    v3 promised this pin in its adoption list and never put it in the test list, so the claim that
    every cell was pinned was false.
12. **`open_fix_round` retry is idempotent** *(A3)* — call it twice for one nonce; assert exactly
    ONE `rounds` entry and the same `round_id` returned both times.
13. **Legacy counters file** *(§7 task 1)* — a dict with none of the four new keys reads clean and
    the capacity formula returns numbers, never a `TypeError`.

---

## 7. Risks, and the claim I would most expect to be wrong

**v1's weakest claim is now resolved, so a different one takes its place.** v1 named the strict
unknown-nonce refusal; `settled_commits` (§2.4) removes that ambiguity, and the peer reaching the
same fix independently is what promoted it from "deferred to v2" to shipped.

**The claim I would now most expect to be wrong: that adding four top-level keys to
`loopback_counters.json` is genuinely migration-free for files already on disk.** §5 proves an
unknown key round-trips through `_read_loopback_state` → `_write_loopback_state`, which I read at
`ee38c542` — but every existing counters file in `claude_docs/.wf2-state/*/` predates this schema
and has none of the four keys. My design says "absent means empty collection", which is the peer's
rule too, and that is easy to write and easy to get subtly wrong: a `None` reaching
`len(reservations)` in the capacity formula would raise inside a lock, and a run would then fail at
a gate rather than at a validator. **What would confirm or refute it:** run the new
`loopback_status` and `authorize_loopback` against every existing file under
`claude_docs/.wf2-state/*/loopback_counters.json` (there are several from #761, #855, #880, #927,
#944, #947) before writing any new code, and assert each returns a clean empty-collection reading
rather than an exception. That is a five-minute probe and it is task 1 of the plan.

Secondary risks:

1. **The lens guard checks text, not behaviour.** Inspecting the manifest AND the rendered prompt
   (peer R9) is stronger than v1's brief-text check, but neither proves a reviewer *applied* a lens.
   It prevents the measured failure — a dispatch that names one lens and drops three — and nothing
   more. Stated here rather than left for a reviewer to find.
2. **Under-charging remains possible** (§2.1 row 3): a round that opens without its commit landing
   is unbilled. Deliberate. Now detectable via `opened_uncommitted` and the Step-16 completion
   refusal, where v1 only had reconciliation reporting.
3. **The matrix is only as strong as its readers.** Removing `SKIP` from the vocabulary makes the
   never-reducible rows inexpressible, and the §6 drift test pins every documented cell to the code
   — but a gate that simply never consults the policy is unaffected by either. Step 11's lens guard
   and Step 16's completion guard are the two places non-consultation fails loudly; the rest rely
   on the steps already being mandatory in the spine.
4. **Concurrency is proven for threads, not processes — and A4 says disclosing that is not the
   same as proving it.** The reviewer is right: matching an existing unverified bound does not
   establish feasibility, and this is the design's central invariant. **Therefore task 4 of the plan
   is a real multi-PROCESS probe** — N `python3` processes racing `authorize_loopback` on one state
   file with a single slot left, asserting exactly one wins and committed+outstanding never exceeds
   the cap. If `file_lock` turns out to be process-local or advisory in a way that does not hold,
   that is a blocking finding against this design, not a footnote. The original wording of this
   risk is kept below because it remains the honest bound UNTIL that probe runs.
   Original bound: Test 2 uses real threads against one
   `file_lock`. True multi-process contention — two `claude` processes in an epic run — is not
   exercised. The existing `consume_loopback` has exactly the same bound, so this is not a
   regression, but "two concurrent authorizations cannot both see the same remaining budget" is
   demonstrated at the thread level only.
5. **`settled_commits` grows unboundedly** for the life of an issue (peer R3). Reported by
   `loopback-status`, never auto-collected, because a collector is a code path that can delete
   evidence of a spend.

---

## 8. PR shape

**One PR, `Closes #923`.** The two halves touch one file (`hooks/plan_lib.py`) plus their tests and
the skill prose; splitting would put the matrix in one PR and the guard that enforces it in another.
Estimated change: ~350 lines of implementation and ~400 of tests, under the 500-line multi-PR
threshold for implementation.

Version: **minor** (`feat`) — new CLI subcommands and new behaviour. Three version surfaces plus the
README changelog entry with its diagram decision and suite delta.

**Diagram decision:** the WF2 spine's step sequence, gates and loop-backs are unchanged — this adds
accounting under an existing gate and a matrix describing existing steps. **No workflow-spine
change → no diagram REV.**