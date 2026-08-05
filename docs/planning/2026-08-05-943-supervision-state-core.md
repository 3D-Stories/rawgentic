# #943 — Supervision-state core (PART A): one declared signal for who is watching

**Date:** 2026-08-05 · **Issue:** #943 · **Epic:** #871 (M4 wave, child 2 of 7)
**Design authority above this doc:** `docs/planning/2026-08-05-871-m4-session-continuity-away-mode.md` §3.1
**Status:** Step 4 gate CLOSED by owner resolution of the ambiguity breaker (D226) — see §12

```stats
3 | RAWGENTIC_HEADLESS reads replaced
2 | predicates, not one boolean | accent
4 | effective states: attended · away · sleeping · attended-overdue
0 | external APIs used by Part A
```

## 1. The problem, in plain words

Three places in the code ask "is anybody watching this session?" by reading one environment
variable. It only says present-or-absent: it cannot distinguish the owner stepping out for twenty
minutes from the owner asleep until morning, and a session that hits a question with nobody there
has no defined behaviour.

## 2. What exists today — confirmed inventory

Every line read directly at `b7d3f258`.

| # | Site | Current code / prose | Seam |
|---|---|---|---|
| 1 | `hooks/context_meter.py:1439` | `headless = env.get("RAWGENTIC_HEADLESS") == "1"` | feeds `nag_text(..., headless=headless)` at `:1444`; pure |
| 2 | `hooks/scanner_bootstrap.py:269` | `headless = os.environ.get("RAWGENTIC_HEADLESS") == "1"` | feeds pure `decide(*, optout_env, optout_ws, headless, ...)` at `:110`; `headless` FIRST in precedence → `"skip-headless"` |
| 3 | `skills/setup/SKILL.md:149-152` | prose: "When `RAWGENTIC_HEADLESS=1`, do NOT install — just record the gap." | Step 2e |

Constraints established by direct inspection:

- `context_meter.py:869` `find_workspace(cwd)` resolves the workspace root; the `:1439` read is on
  the **emit** path past early returns (`:1402`, `:1416`), and the fast-path comment at `:1324`
  promises "no workspace walk, no config read".
- **`context_meter.py` imports stdlib ONLY:** `argparse`, `glob`, `json`, `os`, `re`, `stat`,
  `sys`, `time`, `datetime` (+ a function-local `subprocess` at `:1059`); `plan_lib` appears only
  in a docstring at `:929`. This measured fact is why the read path is a separate module (§4.1).
- `scanner_bootstrap.py:246` `main()` accepts `--workspace`; imports `atomic_write_lib` at `:41`;
  already registered in `tests/hooks/test_atomic_write_lib.py::TestAllSitesRouted`.
- `tests/test_retirement_tripwire.py:177` asserts the token's active-surface carrier set EQUALS
  `D184_ALLOWED_FILES` (`:168-174`), both directions. `RETIRED_VOCABULARY` (`:26-47`) holds only
  the `_TRIGGER` variant (docstring `:13-16`).
- `tests/hooks/test_headless.py:78` **positively asserts** the literal `RAWGENTIC_HEADLESS=1` and
  "do NOT install — just record the gap" in `skills/setup/SKILL.md`.
- **Driver-state `status` is a CLOSED vocabulary, enforced twice:** `driver_lib.py:48`
  `VALID_STATUSES`, refused by `record_child_outcome` (`:1533-1535`) and `validate_driver_state`
  (`:2372-2375`); related closed sets `_DISPOSED_STATUSES:278`, `TERMINAL_STATUSES:1504`, the
  `pr_open` map `:55`. `additionalProperties: true` permits new **fields**, never new `status`
  **values**.
- **No live producer of the env var:** no settings `env` block sets it, no launcher exports it,
  `tests/test_headless_action_workflow.py` is gone, and this repo's `config-reference.md` no
  longer names it.

## 3. Scope: Part A (this issue) and Part B (next)

Owner decision D226, after three adversarial design rounds exhausted the gate's loop-back budget
with 8 findings still open: **split**. Five of those eight findings lived entirely in machinery
that was already gated off, so splitting lets the specified half ship and moves the hard half to
where its dependencies live.

**PART A — this issue.** The state model and the cutover, which is what the epic queue names
("supervision-state core … replaces the three `RAWGENTIC_HEADLESS` stopgap reads"):
the state file + validator; `effective_state` with expiry and the invalid-≠-absent rule; the two
predicates; the three read replacements; the env-var retirement with the both-direction tripwire;
the three declaration commands; the additive `campaign_wait` field + validator + goal-text clause;
and the `consult_grant` schema field. **Covers issue ACs 1, 4, 5, and the schema half of 7.**

**PART B — the follow-up child, done after Part A merges.** Everything the remaining findings are
about: departure preflight with a transaction/rollback marker (AC 2); blocker routing —
`route_for`, the transport capability gate, the disposition table (AC 3); revision-bound action
claims with atomic execute-once semantics; `authority_permits` **including the absence argument it
is currently missing** (AC 6); and the consult USE path (AC 7).

**Part A ships no routing, no claims, no authority logic and no external calls**, so none of the
Part-B findings can affect it. That is the property that makes the split safe rather than merely
convenient.

## 4. The design (Part A)

### 4.1 Modules

**TWO modules, split by hot-path import cost** — the read path is imported by a hook that runs on
every tool call and today imports stdlib only (§2); `plan_lib` (home of `file_lock`) is large.

```
hooks/supervision_lib.py        # READ + PURE. stdlib imports ONLY (test-enforced).
  evaluate_workspace(record, *, now) -> SupervisionView     # workspace-global; no campaign
  nobody_to_ask(view) -> bool
  installs_forbidden(view) -> bool
  validate_declaration(state, until, now) -> (ok, error)
  validate_campaign_id(value) -> bool
  validate_providers(values) -> (ok, error)
  supervision_path(workspace_root) -> str    # <ws>/claude_docs/.supervision.json
  read_state(workspace_root) -> Loaded       # (record, load_status); never searches for the root
  # CLI: installs-forbidden | nobody-to-ask | effective

hooks/supervision_admin.py      # WRITE. May import plan_lib / atomic_write_lib freely.
  declare(ws, *, state, until, session_id, campaign_ids, consult_providers,
          consult_granted, expected_revision=None) -> dict
  mark_attended(ws, *, session_id, reason, expected_revision) -> dict
  # CLI: declare | mark-attended
```

One shared validator lives in `supervision_lib`, so the split cannot grow competing semantics.
`read_state` takes the workspace root and never walks the tree — `context_meter` already found it
via `find_workspace(cwd)`, `scanner_bootstrap` already has `--workspace`.

`declare` and `mark_attended` each run the locked read → validate → increment → atomic-replace
cycle (the `launcher_lib.py:2914` `_locked_state_update` pattern) and take `expected_revision`, so
a write computed against a stale read aborts rather than clobbering a fresher declaration.

**Predicate-specific CLI, never a bare state word.** A caller deciding "may I install?" must not be
handed `attended-overdue` to interpret:

```bash
python3 hooks/supervision_lib.py installs-forbidden --workspace <root>
#   exit 0 = FORBIDDEN · 1 = allowed · 2 = invalid input (fail-loud diagnostic)
```

Reuse (each verified at the cited line): `atomic_write_lib.py:27` `atomic_write_text(...,
mkdir=True, fsync=True)`; `plan_lib.py:2702` `file_lock` (sidecar); the locked cycle from
`launcher_lib.py:2914`; the capped fail-open read shape from `task_class_lib.py:363`; the
fail-LOUD per-command `_fail` CLI shape from `task_class_lib.py:402-415`.

### 4.2 State file — normative schema

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

- `state` ∈ `attended | away | sleeping`, DECLARED, never inferred.
- `until` — ISO-8601 UTC or `null`. REQUIRED non-null for `sleeping`; MUST be `null` for
  `attended`; **rejected if already in the past at declaration time** (a wake time behind `now`
  would declare an instantly-expired absence).
- `revision` — monotonic; the optimistic-concurrency token, and the absence-window id that
  Part B's decision records will cite.
- `governed_campaign_ids` — campaign NAMES, each validated `^[A-Za-z0-9._-]+$` with path
  separators and `..` rejected: Part B joins these into a state-file path, so an unvalidated value
  would be a path-traversal write primitive. Validated in Part A because the field ships in Part A.
  **Empty list = governs every campaign**; naming campaigns narrows it (consumed by Part B).
- `consult_grant.providers` — validated against the runner's provider vocabulary (`gpt`, `glm`) at
  declaration time, not at send time. The field ships now; its USE is Part B.

Workspace-level: away-ness is a property of the human, not a repo.

### 4.3 Evaluation and the two predicates

```python
@dataclass(frozen=True)
class SupervisionView:
    state: str        # EFFECTIVE: attended | away | sleeping | attended-overdue
    declared: str     # as written, or "invalid"
    until: str | None
    expired: bool
    revision: int
    declared_at: str | None
    load_status: str  # "absent" | "valid" | "invalid"
    consult_providers: tuple[str, ...]
    granted: bool
```

Part A ships ONE evaluator, `evaluate_workspace` — "is the human watching, at all?" — which is the
only question its consumers have, because the two hook sites belong to no campaign.
`governed_campaign_ids` does NOT narrow it: narrowing an install guard by campaign is meaningless.
The campaign-scoped evaluator, and with it any use of the override field, is Part B. This is the
resolution of a defect an earlier draft carried: a single evaluator taking `campaign_id=None` made
the safe reading and a silently-inert reading equally plausible, and an inert reading would have
shipped a feature that does nothing at the very sites it exists for.

Expiry: past `until`, the effective state is `attended-overdue` — attended for new blocker routing
(Part B's concern), with `revision`/`declared_at` retained so an owner-return report can still lead
with what happened during the declared window.

**Two predicates, not one boolean.** The two hook sites need OPPOSITE fail-safe directions:

- `nobody_to_ask(view)` — `True` only for effective `away`/`sleeping`. Consumer: `context_meter`.
  It only changes the wording of advice, so expiry should fail toward "a human is back".
- `installs_forbidden(view)` — `True` whenever an `away`/`sleeping` declaration is in force
  **including after expiry**, and whenever the state is INVALID. Only `/rawgentic:back` clears it.
  Consumers: `scanner_bootstrap`, setup Step 2e. This authorizes a real, outward,
  not-trivially-undone action, so neither a passing clock nor an unparseable file may license it.

A single `is_watched` boolean — proposed by the peer consult — would have collapsed these: the
moment a wake time passed, an unattended host would resume installing packages on the strength of
a clock rather than evidence a human returned.

**Present-but-invalid is NOT absent**, and an unresolvable workspace counts as invalid:

| Condition | `load_status` | `nobody_to_ask` | `installs_forbidden` |
|---|---|---|---|
| no workspace root supplied at all | `absent` | False | False |
| workspace supplied but unresolvable / not a directory | **`invalid`** | False | **True** |
| file missing (`ENOENT`) under a valid root | `absent` | False | False |
| valid, `attended` | `valid` | False | False |
| valid, `away`/`sleeping` | `valid` | **True** | **True** |
| valid, expired → `attended-overdue` | `valid` | False | **True** |
| present but unreadable / oversized / malformed / off-vocab | `invalid` | False | **True** + diagnostic |

Two lines carry the whole safety property and are asserted row by row (§9):

1. `ENOENT` under a valid root is the ONLY *file* failure classed as absence — a corrupt file must
   not silently drop the guard, which is exactly what would happen if the file were damaged
   *during* an away window.
2. **A supplied-but-unresolvable root is invalid, not absent.** Classing it as absence would let a
   path-resolution or caller-misconfiguration failure ALLOW installs while the real workspace held
   an active away declaration — a fail-safe property inverted by a config bug. "No root supplied"
   stays absence, because that genuinely means there is no rawgentic workspace context, which is
   today's behaviour and must not change.

### 4.4 The three read replacements

1. `context_meter.py:1439` → `nobody_to_ask(evaluate_workspace(read_state(workspace), now=...))`
   using the already-discovered workspace; `nag_text` kwarg renamed `headless` → `unattended`.
2. `scanner_bootstrap.py:269` → `installs_forbidden(...)` from the existing `--workspace`;
   `decide()`'s param renamed `installs_forbidden`; sentinel `"skip-headless"` →
   `"skip-unattended"` with its `_OUTCOME` key.
3. `skills/setup/SKILL.md` Step 2e → invokes `installs-forbidden --workspace` with its exit-code
   contract; `tests/hooks/test_headless.py:78` rewritten in the same commit, since it positively
   asserts the old string.

### 4.5 Declaration commands

Three thin skills over one `supervision_admin declare` call:

| Skill | Command | Writes |
|---|---|---|
| `skills/away/SKILL.md` | `/rawgentic:away [until]` | `state=away`, `until` optional |
| `skills/sleeping/SKILL.md` | `/rawgentic:sleeping <wake time>` | `state=sleeping`, `until` REQUIRED |
| `skills/back/SKILL.md` | `/rawgentic:back` | `state=attended`, `until=null`, clears the grant |

Part A's skills declare state and report the result. The campaign-sweeping **departure preflight is
Part B** (it is the subject of an open finding about multi-file write rollback), so Part A's skills
ask no questions and invoke no harness tool — which is why §8 is `none`. Each skill states plainly
that the blocker-routing behaviour arrives with Part B, so a user reading the command does not
over-trust it.

Registration: whitelist entries (`away`, `back` after `adversarial-review`; `sleeping` between
`session-recall` and `setup`); symlinks `../../../skills/<name>`; counts 21 → 24 across
`README.md:14`, `README.md:3` + `:16-18` (workspace management 9 → 12), `README.md:679` evals
`11/24`, both `plugin.json` descriptions (sum 24, byte-identical), the codex
`interface.longDescription`. No `<config-loading>` ⇒ `EXPECTED_CONFIG_LOADING_COUNT` and the sync
MANIFEST untouched.

A pre-merge runtime slash-command discovery spike cannot exist: sessions load skills from
`~/.claude/plugins/cache/rawgentic/rawgentic/<version>/`, so a new skill resolves only after
reinstall AND a new session (repo `CLAUDE.md` §1, mistake #4). Verification is
`hooks/skill_registration_check.py check --skill <name>` plus the packaging/count guards pre-merge,
and D183 plugin-refresh post-merge. **Recorded disagreement:** the peer consult proposed one
`skills/supervision` skill with the mode as an argument; kept at three per the epic design (D224).

### 4.6 Campaign wait field + goal clause (AC 5)

Additive TOP-LEVEL driver-state field — NOT a new `status` value:

```json
"campaign_wait": {"status": "waiting_for_owner", "reason": "...", "blocker_id": "...",
                  "entered_at": "...", "clears_when": "..."}
```

**Why not a `status` value:** the per-issue vocabulary is closed and enforced twice
(`driver_lib.py:48`, refused at `:1533-1535` and `:2372-2375`), with three further closed sets
keyed off it. `additionalProperties: true` covers new FIELDS, not new VALUES — an earlier draft
conflated them. A top-level object is genuinely additive: `validate_driver_state` inspects only
`schema_version`, `campaign`, `issues`, `depends_on` and the single-`in_progress` invariant. No
`schema_version` bump; every existing campaign file still validates. `clears_when` is REQUIRED — a
pause whose exit condition nobody can state is a stall wearing a pause's clothes.

Part A ships **the field, its validator, and the goal clause only.** The scheduling / Stop-release
/ resume / teardown consumers are #927's and #586's, and Part A makes no claim that writing this
field halts anything. `plan_lib.py:2758` `build_goal_text` gains a terminal-for-now clause in the
**campaign** branch only, so a Stop-hook goal loop can read an honest wait instead of nagging
(3 prods measured during a real owner pause); `_GOAL_CAP = 4000` binds and gets a boundary test.

### 4.7 Retirement, kept honest (AC 4)

1. Add the bare token to `RETIRED_VOCABULARY` (`tests/test_retirement_tripwire.py:26`).
2. **KEEP** the exact-set test with `D184_ALLOWED_FILES = set()`, renamed to say it asserts zero
   active-surface carriers — retaining both-direction equality rather than weakening it to a subset
   or a count (the peer consult was right about this).
3. Update the docstring note at `:13-16`, which currently asserts the opposite.
4. Reword `docs/context-meter.md:191`'s row and the README prose above the Changelog (the Changelog
   is excluded from scanning, `:117-121`, so this version's entry may name it as history).

The allowed set goes to empty and STAYS asserted, so after this commit any file naming the token
fails the suite — "retired in prose but honored in code", or the reverse, cannot land.

## 5. Migration

**Clean break, no shim**, justified by §2's confirmed absence of any producer. **Residual, stated
honestly:** a hand-set `RAWGENTIC_HEADLESS=1` afterwards has no effect and no warning, because
warning requires naming it in code; mitigation is the changelog + setup prose. The peer's
coordinated-rollout gate is moot (no producer) and recorded in case one appears.

## 6. Error handling and failure modes

| Failure | Behavior |
|---|---|
| no workspace root supplied | `absent` → `attended`; both predicates False (today's unset-env default) |
| root supplied but unresolvable | `invalid` → **`installs_forbidden` True** + diagnostic |
| file missing under a valid root | `absent` → `attended`; both predicates False |
| present but unreadable / oversized / malformed / off-vocab | `invalid` → `nobody_to_ask` False, **`installs_forbidden` True**, one stderr diagnostic; never raises into a hook |
| `until` in the past at declaration | refused before any write |
| unknown consult provider | refused at declaration time |
| `sleeping` with no wake time | refused before any write |
| declaration WRITE fails | rc 1, loud; the skill reports the owner is NOT recorded as away |
| `expected_revision` mismatch | abort and report the new revision rather than clobber |
| lock contention | `file_lock` serializes; `revision` makes the loser detectable |

READ is fail-open for AVAILABILITY but fail-SAFE for AUTHORITY: a broken file never wedges a
per-tool-call hook, and never unlocks an outward action. WRITE is fail-loud.

## 7. Security implications

- No credentials in the file; `0o600` via `atomic_write_text`'s mkstemp default.
- `consult_grant` is an egress control whose default is un-granted; Part A validates it and stores
  it, and no code in Part A performs egress, so silence cannot authorize a send.
- `governed_campaign_ids` charset-validated, because Part B joins them into paths.
- One-directional trust boundary: the file may relax *advice* and suppress *installs*. **Part A
  contains no authority logic at all** — merge, destructive and outward authority are untouched by
  this issue and remain governed by the epic-run grant and the harness permission system.
  (`authority_permits` and AC 6 are Part B; an earlier draft of this section asserted a merge
  prohibition that contradicted the epic design's rule that merge authority comes from the
  epic-run grant, and would have forbidden the very mode this wave runs under.)
- A corrupt file, or an unresolvable workspace, TIGHTENS the outward-action guard rather than
  loosening it.
- `until` validated (parseable, not past) so a corrupt timestamp degrades to `attended` rather
  than a permanent unsupervised state.

## 8. Platform / external dependencies

```md
platform_apis: none
```

Part A uses no platform, framework or external API: no messaging transport, no consult runner, no
harness tool. It is stdlib (`flock`, `os.replace`, `json`) routed through the existing
`atomic_write_lib` / `plan_lib.file_lock` helpers, plus two hooks and skill prose. Every external
surface the earlier full-scope design declared — the BlueBubbles bridge, the consult runner,
`AskUserQuestion` — belongs to Part B, along with the release gate that must prove live delivery
before AWAY autonomy is trusted.

## 9. Testing strategy

New: `tests/hooks/test_supervision_lib.py`, `tests/hooks/test_supervision_admin.py`. Pure functions
imported via `sys.path.insert`; CLI black-box via subprocess; injected clock; no real waits.

- `evaluate_workspace`: each declared state; expiry exactly AT `until`; `attended-overdue` retains
  `revision`/`declared_at`. **The inert-feature regression:** a bare `/rawgentic:away` with an
  empty `governed_campaign_ids` must still make `installs_forbidden` True — the workspace evaluator
  never narrows by campaign.
- **The load-status table asserted row by row**, with both safety lines explicit: `ENOENT` under a
  valid root is absence, and a supplied-but-unresolvable root is INVALID and forbids installs.
- **The asymmetry test:** expired `away` ⇒ `nobody_to_ask` False AND `installs_forbidden` True —
  the test that catches a future refactor collapsing the two predicates into one boolean.
- **`supervision_lib` imports stdlib only** — asserted by parsing its own import list, because the
  split's entire purpose dies to one convenience import.
- **Setting the retired env var changes nothing**, at both replaced sites.
- `validate_declaration`: `sleeping` without `until`; `attended` with non-null `until`; past
  `until`; unparseable `until`. `validate_campaign_id`: separators, `..`, empty, over-long.
  `validate_providers`: unknown provider rejected.
- `declare`/`mark_attended`: monotonic `revision` under concurrent writer subprocesses, always-valid
  JSON, no stray temp files, loud rc 1 on write failure, `expected_revision` mismatch aborts.
- `campaign_wait`: additive validation, `clears_when` required, a pre-existing campaign file still
  validates unchanged, and `VALID_STATUSES` asserted UNCHANGED (so a future hand adding a wait word
  to the per-issue vocabulary fails loudly).
- Goal text: the clause stays within `_GOAL_CAP = 4000` at the boundary.
- Tripwire: zero active-surface carriers of the token.

Baseline for the regression claim: 5366 passed, exit 0, at `b7d3f258`.

## 10. Out of scope — and where it went

**Part B (the follow-up child):** departure preflight with a transaction/rollback marker (AC 2);
`route_for`, the transport capability gate and the disposition table (AC 3); revision-bound action
claims with atomic execute-once semantics; `authority_permits` with its missing absence argument
(AC 6); the consult USE path (AC 7); and the campaign-scoped evaluator plus the `supervision_override`
lifecycle. **Other children:** #927 (boundary rework, the executable broker, the `campaign_wait`
consumers), #586 (resume, `resets_at`), #944 (revalidate hardening).

## 11. Multi-PR assessment

Single PR for Part A. ~12 implementation files on one seam; splitting the read replacements from
the module feeding them ships a dead module, and splitting the tripwire edit from the code ships a
red suite.

## 12. Gate provenance — how this design was arrived at

**Step 3 peer consult** (blind both ways; drafted and written to disk before the result was
opened). Backend `gpt`, reviewer `gpt-5.6-sol`, `status: success`, `diagnostic: true`.
*Adopted:* the two-module split (the single-module draft was wrong, and §2's stdlib-only import
list is the measured basis); the locked-write pattern; retaining the both-direction exact-set
assertion at empty; `revision` on the view. *Overridden:* a single `is_watched` boolean (§4.3); one
`skills/supervision` skill (§4.5, D224); a coordinated launcher-rollout gate (§5 — moot, no
producer).

**Step 4 — three adversarial rounds, 27 findings, 19 applied.** Rounds returned 10 → 9 → 8
findings; several later findings were caused by earlier rounds' own fixes. Findings that changed
this design materially and survive in Part A:

- present-but-invalid ≠ absent (§4.3) — the strongest finding of the three rounds: an earlier draft
  would have silently dropped the install guard if the state file were damaged during an away
  window, collapsing in the error path the very asymmetry the two predicates exist to preserve;
- `status` is a closed vocabulary enforced twice, so the wait had to become an additive
  `campaign_wait` field and the `additionalProperties` justification was wrong (§4.6);
- an unresolvable workspace must be invalid, not absent (§4.3) — otherwise a config bug inverts the
  fail-safe property;
- the predicate-specific CLI with an exit-code contract, because a bare state word makes
  `attended-overdue` ambiguous at the install site (§4.1);
- `campaign_id=None` overloading, which risked shipping an inert feature — resolved in Part A by
  shipping only the workspace evaluator (§4.3).

Two findings were **refuted with evidence** rather than complied with: nothing can intercept
`AskUserQuestion` (a model-invoked harness tool, not a Python function), and no pre-merge runtime
slash-command discovery spike can exist (skills load from the installed plugin cache after
reinstall + a fresh session). A registration-style static guard IS feasible and belongs to Part B
with the routing it guards.

**How the gate closed.** The `design` loop-back budget was exhausted (2/2, global 2/3) with 8
findings still open, and two of them carried ambiguity markers — which refuses the #798
budget-exhausted close, because that close is only for exhaustion over resolved ground. The
contract therefore required escalation, and the owner resolved it: **split** (D226). This gate
closed by **owner resolution of the ambiguity breaker** — explicitly not by a clean review round
and not by a budget-exhausted close. The 8 open findings were not dropped: 5 transferred wholesale
to Part B, 2 were fixed here (§4.3's unresolvable-root row and §7's corrected authority statement),
and 1 (`AskUserQuestion` capability evidence) transferred with the preflight that needs it.
