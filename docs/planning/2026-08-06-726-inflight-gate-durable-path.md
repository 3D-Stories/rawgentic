# #726 — In-flight-work declaration gate + session-scoped-path check for pane handoff

**Issue:** [#726](https://github.com/3D-Stories/rawgentic/issues/726) (bug, severity: high) ·
**Epic:** #871 (M4 wave, child 5 of 8) · **Date:** 2026-08-06 · **Complexity:** standard_feature
**Design context:** `docs/planning/2026-08-05-871-m4-session-continuity-away-mode.md` §4 — "#726
scope as filed".
**Revision 4 (final)** — two `design` loop-backs consumed (the source cap), so the Step-4 gate
closes budget-exhausted per #798 after applying this pass's findings. §8 records every finding from
one peer consult, THREE adversarial design passes and three self-reviews, with its disposition.

---

## 1. The defect, and exactly what this change does about it

`perform_handoff` gates on the **successor's** readiness — split, agent start, project bind, prompt
landed, goal armed. Every one of those checks looks FORWARD. Nothing looks BACKWARD at the
predecessor's own unfinished work. Observed live 2026-07-30: a handoff ran while a design re-gate
was still dispatched; its 15 KB verdict (8 findings, 3 High) landed two minutes later in
`/tmp/claude-1000/<slug>/<PREDECESSOR-SESSION-UUID>/scratchpad/step4-regate2.out`. The predecessor
tried to preserve the artifact by copying it into the repo, but the file was still 0 bytes at copy
time, so what survived was an empty file.

**The two holes are named precisely, because an earlier revision of this document overclaimed and
the adversarial review was right to say so:**

- **H1 — nothing REQUIRES the question "is anything of mine still running?" to be asked.** This
  change makes the answer a mandatory, recorded input. It does **not** discover running work
  (§2 explains why that is not available), so it is an **attestation gate**, not a detector. A
  caller that answers falsely still passes.
- **H2 — nothing rejects a successor reference that is scoped to the predecessor's session.** This
  change rejects exactly that path shape. It does **not** prove durability, and it does **not**
  catch the empty-file half of the 2026-07-30 incident. Both limits are restated in §8 and in the
  PR body.

Together they close the *procedural* failure (nobody looked) and the *addressing* failure (an
unusable reference), which is the whole of what the issue's ACs can mechanically buy.

## 2. What the hook can and cannot see — probed, not assumed

The question "can a subprocess enumerate the orchestrator's background work?" was **probed live in
this session on 2026-08-06** (`date -u` → `2026-08-06T05:45:52Z`, quoted because pass 2 of the
review believed the date was 2026-08-05 and rejected the evidence on that basis):

- **Harness background bash tasks DO leave one artifact:** `<scratch-root>/tasks/<task-id>.output`.
- **It carries only the command's stdout — no status, no exit code, no completion marker, and no
  sibling file.** Measured on the SAME task at three points, which is what the review asked for:
  task `biiftwaky` read 14 bytes mid-run (`tick 1 / tick 2`), 49 bytes still mid-run, and 69 bytes
  once the harness reported it complete — with `ls` showing exactly one matching file every time. A
  running task and a finished one are therefore **indistinguishable** by this surface.
- **`Monitor` watches leave nothing on disk at all** — they are harness-internal, not OS processes.
- The session scratch root contains exactly two subdirectories: `tasks/` and `scratchpad/`.

So a probe exists and **cannot answer the only question this gate asks**. That is why the design
declares. It also settles the apparent conflict with #951, which deleted a caller-asserted
`--herdr-available` flag in favour of a probe: the rule is *"do not assert what you can probe"*, and
the probe here was attempted and cannot carry the load.

**The honest bound, repeated in the PR body:** no hook can prove the enumeration was truthful. A
caller that types `--inflight-none` reflexively passes. What is mechanical is that the question
cannot be *skipped*, that every answer is *reported in the result payload*, and that an override
is *visible* there. The payload is stdout plus the WF2 run-record the orchestrator writes; this
change adds no new durable store of its own, and §8 records that bound.

## 3. Approaches considered

```options
Process-tree probe | Needs no declaration at all | Blind to Monitor watches; cannot tell a stranded waiter from a deliberate pytest run; zero ps//proc precedent in hooks/ | rejected
On-disk probe of dispatch artifacts | Would be authoritative if it existed | The executor surface AC 1 names was deleted in #866 M0d; review_runner writes its receipt only at the end | rejected
Producer-owned completion receipts | Would satisfy both holes properly | The producers are harness tasks this repo cannot instrument; a separate design | rejected
Mandatory caller declaration | Converts 'nobody looked' into a hard stop; every answer recorded | Cannot catch a caller that answers falsely | chosen
```

**A — Process-tree probe.** *Rejected.* No `ps`/`/proc` precedent anywhere in `hooks/` (grep: zero).
It cannot see `Monitor` watches. It cannot separate the handoff's own subprocess chain, or a
deliberate pytest run, from a stranded waiter. A probe that is frequently wrong teaches operators to
pass the override reflexively — which is how AC 3 becomes a rubber stamp.

**B — On-disk probe of rawgentic's own dispatch artifacts.** *Rejected, reason recorded.* AC 1 names
"executor dispatches ... `routing-audit.jsonl` / `executor_routing_lib.py status`". That surface **no
longer exists**: the M0 executor retreat (#866 M0d, D174) deleted the executor phase package, and a
retirement tripwire test fails on those identifiers. Its successor `hooks/review_runner.py` writes
its `--out` receipt only at the END and *removes a stale one at the start*
(`review_runner.py:545-548`), so "out file absent" is indistinguishable from "never dispatched".
**This is the largest way the issue body is stale, and the PR body says so.**

**C — Producer-owned completion receipts the hook verifies** (pass-2 review's preferred fix for both
H1 and H2). *Out of scope, recorded as declined.* The producers are harness tasks and external
review backends; this repo cannot instrument them. A receipt contract is a separate design, and
"scope as filed" (epic design §4) settles it for this child.

**D — Mandatory caller declaration. CHOSEN**, with the claims narrowed to what it actually delivers
(§1). Precedent in the same function: `perform_handoff` already REQUIRES an explicit
`expected_predecessor_goal` snapshot under `strict_goal_binding`, because `None` is itself a valid
state and "an omitted snapshot is indistinguishable from an unvalidated one"
(`launcher_lib.py:1993-1997`).

## 4. The design

### 4.1 Pure functions

```python
INFLIGHT_KINDS  = ("bash", "dispatch", "watch", "other")
INFLIGHT_STATES = ("running", "completed", "abandoned")
_INFLIGHT_IDENT_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._-]{0,63}$")
_UNSET_INFLIGHT = object()

def parse_inflight_item(raw: str) -> dict              # "<kind>:<ident>:<state>:<detail>"
def inflight_decision(declaration: dict) -> (ok, reason, record)
def abandoned_work_block(items) -> str                 # "" when nothing was abandoned
def session_scoped_paths(text, *, session_id=None) -> list[dict]
```

**The declaration is a closed object with exactly one alternative selected**, because
"unspecified" is how a mandatory question gets bypassed. It is
`{"items": [...], "attested_none": bool, "override": bool}`, and it must satisfy **exactly one** of
`attested_none is True` (with `items` empty) or a **non-empty** `items` list. A `None`, a missing
key, an unknown key, a non-list `items`, both alternatives, or neither is refused. So a future
caller cannot satisfy the call-site guard by handing over a bare `[]`.

### 4.2 The declaration and its decision table (ACs 1–3)

Each item carries `kind`, a charset-restricted `ident`, a `state`, and a free-text `detail`. The
state makes AC 4's wait auditable: an operator who waited declares the item `completed` rather than
declaring nothing, so the record still shows the work existed.

**`abandoned` is a DISPOSITION, not an execution state.** Declaring it does not and cannot stop the
task — a hook cannot terminate a harness job (§2). It means: *the successor will neither receive nor
wait for this result.*

| declaration | `--allow-inflight` | outcome |
|---|---|---|
| omitted at the library boundary (`_UNSET`) | any | **`LauncherError`** — caller-contract violation, refused before anything runs |
| `--inflight-none` (explicit empty) | no | **PASS**, recorded as `attested_none` |
| all items `completed` | no | **PASS**, items recorded (the waited case) |
| any item `running` | any | **REFUSE**, every running item named |
| ≥1 `abandoned`, none `running` | no | **REFUSE** — abandonment needs the flag |
| ≥1 `abandoned`, none `running` | yes | **PASS**, override recorded |
| no `abandoned` item | yes | **REFUSE** — a vacuous override is a false audit record |
| `--inflight-none` with `--inflight` | any | **REFUSE** at argparse and again in the function |

Row 4 is stricter than the issue's `--allow-inflight` sketch: an item declared `running` cannot be
admitted by the flag. To proceed, the operator re-declares it `abandoned` — a positive statement
about that job rather than a blanket "let me past". Row 7 mirrors an existing refusal exactly:
`--goal-rewrite-approved` with `--no-teardown` is refused because "an approval flag there could only
ever mint a FALSE audit record" (`launcher_lib.py:5272-5276`).

```callout decision
decision | Enforce on all three CLIs, and place the campaign check early
Revision 3 enforced only where the caller shipped in this PR. Pass 3 objected that two
unenforced paths leave the defect live, and this campaign had already measured the
counter-example: #769's rc-8 sweep gate shipped a mandatory refusal against the same frozen
plugin cache, and the next boundary read its printed remedy and cleared it. For an LLM-driven
caller a self-explaining refusal IS migration. The liveness half is kept structurally instead,
by refusing before `mark_split_attempted` rather than inside `perform_handoff`.
```

**Enforced on all three handoff CLIs — the rollout split from revision 3 is DROPPED.** Revision 3
enforced only on `ad-hoc-handoff`, on the argument that the campaign CLIs are driven by a cached
`epic-run` skill (D230 bars a plugin refresh this campaign) and that "refusal text is not caller
migration". Pass 3 objected that two unenforced paths leave the defect live, and pass 3 is right,
because **this campaign already measured the counter-example**: #769 shipped the rc-8 boundary-sweep
gate as a mandatory refusal against the same frozen cache, and the next boundary met it, read the
remedy the refusal printed, and cleared it. For an LLM-driven caller a self-explaining refusal IS
migration.

The liveness half of the earlier concern is kept structurally rather than by weakening the gate: on
the campaign paths the check runs EARLY, before `mark_split_attempted` (§4.5), so a refusal can
never park a campaign — it is re-runnable the moment the flag is added. Every refusal prints the
exact flag that clears it, and `skills/epic-run/SKILL.md` is updated for sessions that load the new
build.

### 4.3 AC 6 is written by the HOOK — and carries NO caller text at all

When any item is `abandoned`, `abandoned_work_block(items)` builds a fenced block and
`perform_handoff` **appends it to the resume prompt**:

```
--- ABANDONED WORK (predecessor handoff) ---
2 background items were abandoned at this handoff (kinds: dispatch, watch). They may still finish
in the predecessor session, but their results will NOT reach you and must NOT be waited for.
Re-dispatch anything you need.
--- END ABANDONED WORK ---
```

**Every byte of that block is hook-authored.** The only variables are a COUNT and the allowlisted
`kind` values. Revision 3 also carried the operator's `ident`, and pass 3 was right that this was
not good enough: `_INFLIGHT_IDENT_RE` happily admits `ignore_previous_instructions`, so an
identifier is semantic text reaching a model. `ident` and `detail` now live ONLY in the audit
payload. The successor loses nothing it needs — it is not correlating the predecessor's internal
job ids; it needs to know that something was abandoned and that waiting is pointless.

This is the invariant `driver_lib.with_boundary_clause` already states for the boundary clause:
"nothing here is interpolated from a probe or an issue body — every value is generated by this
process from durable state, which is what keeps untrusted text out of a successor's prompt."
Exclusion, not fencing: fencing prose as data does not stop a model reading it as instruction.

Ordering inside `perform_handoff` is load-bearing: the marker check and the bind-directive refusal
run on the ORIGINAL prompt and are unaffected by an append; `validate_inserted_prompt` refuses text
*starting* with `/`, which an append cannot change. The path scan (§4.4) runs on the **augmented**
prompt.

Caller fields are still validated even though they never reach the prompt — `kind` and `state`
against their vocabularies, `ident` against `_INFLIGHT_IDENT_RE`, `detail` length-capped, control
characters and `/rawgentic:switch` refused anywhere. A malformed declaration should fail at the
boundary, not merely be filtered later.

### 4.4 `session_scoped_paths` — the exact contract (AC 5)

**Tokenizer, stated precisely because "token" was ambiguous in revision 2.** A candidate is the
longest run matching `/[A-Za-z0-9._\-/]+` — an absolute path of unreserved characters. Trailing
`.`/`,`/`)`/`]` are stripped. This deliberately does **not** attempt quoted paths with spaces,
percent-encoding, or environment-variable aliases; those are stated non-goals, not oversights, and
§8 records them as an evasion bound. `file:///tmp/claude-…` needs no decoder: it contains the plain
path as a substring, so the same scan matches it.

Each candidate is normalized (lexical `.`/`..` collapse, no filesystem access) and **flagged** when:

1. any path component equals the predecessor's session id (when known); **or**
2. the path lies under `/tmp/claude-*/` **and** carries a component matching the UUID shape
   `[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}`.

Rule 2 exists because rule 1 alone fails where it matters most: a `--no-teardown` ad-hoc handoff
passes no predecessor session.

**Test vectors (all shipped as cases).** Positive: the canonical incident path; the same path inside
a Markdown link; the same as a `file://` URL; a `/tmp/claude-1000/<slug>/<uuid>/` directory with no
file part; a path with `/./` and `/../` segments that normalizes onto one. Negative: an in-repo
`docs/planning/...` path; `/tmp/claude-1000/<slug>` with no UUID component; a UUID appearing in
ordinary prose with no leading `/`; a non-`/tmp` absolute path carrying a UUID.

**Why such a path is a defect — corrected against a live probe, because the issue body's stated
reason is wrong.** The issue says the successor "could not have read the file even knowing the
path". Probed 2026-08-06: `/tmp/claude-1000` is `drwx------` owned by the single host user, every
Claude session on this host runs as that user, and 207 sibling session directories (oldest
2026-08-01) were listable from this session. A successor **can** read a predecessor scratchpad. The
real reasons are:

- it is untracked per-session temp state with no durability guarantee, outside the repo;
- it is scoped to a session the default handoff is **about to retire**; and
- the successor is never given the predecessor's session id, so the path is usable only if pasted
  verbatim — exactly the fragile hand-off this refuses.

**No override.** Revision 1 added `--allow-session-path` for the case where AC 6 forces the operator
to paste a stranded path; §4.3 dissolved that by generating the notice. What remains is an ordinary
false positive whose fix is always available and is named in the refusal: copy the artifact
somewhere durable, or drop the reference.

**ReDoS:** one bounded character class, one quantifier, no nesting or alternation — linear.

### 4.5 Where the checks run — early at the CLI, backstopped in `perform_handoff`

`_cmd_handoff` durably records `mark_split_attempted` at `launcher_lib.py:5135` **before** calling
`perform_handoff`, and `_classify_launch` (`:4763`) maps an unrecognised `failed_step` to "append no
terminal event". A new refusal *inside* `perform_handoff` on the campaign path would therefore leave
`split_attempted: true` with no terminal outcome — a permanently parked campaign. That is why:

- **`_cmd_handoff` and `_cmd_mid_child_handoff` run the checks EARLY**, beside the existing rc-6
  revalidation and rc-8 sweep gates (both of which already sit before the claim and before
  `mark_split_attempted`), and pass the resulting declaration down for the record.
- **`perform_handoff` keeps both checks as a backstop**, positioned after the `queue_revalidated`
  rung and before the #731 name preflight — purely local work, so refusing there costs no `herdr`
  subprocess and creates nothing to clean up.
- **`_classify_launch` additionally maps `inflight` and `durable_path` to `no_split_attempted`**, so
  even a backstop refusal on the campaign path is classified as "nothing was created" rather than
  left indeterminate.

Both follow the #731 template: recorded in `out["steps"]` via `record(...)` on every path,
structured `failed_step` + `failure_detail`, refusal before anything exists.

### 4.6 Fail-mode — CLOSED on both, deliberately opposite to the #731 preflight

The name preflight fails **open** because a second gate exists downstream: `agent start` still
refuses a taken name. **Neither new check has a downstream gate.** So both fail CLOSED:

- `inflight_decision`: an absent declaration raises; a blocking declaration refuses, on every CLI.
  A caller-contract violation is not an unavailable probe fact, so the fail-open idiom does not apply.
- `session_scoped_paths`: an unexpected exception **refuses** with `failed_step="durable_path"` and a
  reason naming the exception class. Revision 1 had this failing open, contradicting this design's
  own doctrine. Refusing strands nothing: no pane exists yet.

This matches `projects/rawgentic/CLAUDE.md` §3 — a data-loss boundary that cannot evaluate fails closed.

### 4.7 The gate cannot be dropped by a future caller

`perform_handoff(inflight=_UNSET_INFLIGHT)`; **a sentinel that reaches the function raises**, in the
same class as an empty `resume_prompt`. Measured cost of doing this properly: the three heavy test
modules each build kwargs through ONE `_handoff(...)` helper (`test_launcher_lib.py:164`,
`test_adhoc_pane_handoff.py:161`, `test_adhoc_teardown_guard.py:136`), so it is one edit per file.

Belt and braces, because a new caller is born in source: a guard test enumerates every
`perform_handoff(` call site under `hooks/` and asserts each passes `inflight=`. There are three
today — `_cmd_handoff` (`:5138`), `_cmd_ad_hoc_handoff` (`:5372`), `_cmd_mid_child_handoff` (`:5607`).

### 4.8 CLI surface, and what is NOT covered

Added to all three handoff subcommands:

```
--inflight '<kind>:<ident>:<state>:<detail>'   repeatable
--inflight-none                                affirmative attestation: nothing is running
--allow-inflight                               proceed with abandoned items (recorded)
```

New standalone subcommand:

```
python3 hooks/launcher_lib.py check-handoff-prompt --prompt-file <f> [--session-id <id>]
```
rc `0` clean · `3` offending paths (JSON naming each path and why) · `2` caller error.

**`clear-prep` is NOT covered, and this PR therefore says `Part of #726`, not `Closes`.** The
issue's scope names it, but it lives at `~/.claude/skills/clear-prep`, outside this repository.
Editing the owner's personal skill directory from inside a repo PR would be an unreviewed
out-of-band change to their environment, so this PR gives `clear-prep` something to call and
nothing more. **#726 stays OPEN** with a comment enumerating what landed and what remains; the
epic #871 checkbox is ticked because the wave's scheduled work on this child is delivered — the
same shape D226 set in this wave for #943 Part A (merged, box ticked, issue deliberately open).
No follow-up ISSUE is filed: D179 requires owner confirmation, so the residual rides the
run-record `follow_ups` and is led with when the owner returns (D247).

### 4.9 Result payload

`out["inflight"] = {"declared": [...], "attested_none": bool, "override": bool}` and
`out["session_paths"] = {"flagged": [...]}` — both surfaced by the CLI payload, so an override stays
visible even when a later delivery step fails (AC 3).

**What "recorded" means here, stated exactly.** The payload is stdout, plus whatever the caller
persists — for a WF2 run that is the run-record. This change adds **no durable store of its own**,
so a caller that discards stdout and then dies loses the declaration. Building one would mean a new
persistence surface, which the epic design's §7 ruling ("no new enforcement state machines")
forbids. The bound is recorded in §8 rather than papered over.

### 4.10 AC 4 is NOT satisfied by this change

AC 4 asks that "when the operator waits, the skill polls to completion and then proceeds
automatically". Revision 3 called this "prose-satisfied"; pass 3 rejected that, correctly. There is
nothing to poll: §2's probe shows the harness exposes no task status surface, so neither the hook
nor a skill can observe a terminal state, and an instruction to "wait for your notification" has no
assertion behind it and fails silently if the notification never arrives.

So AC 4 is recorded as **UNSATISFIED**. The refusal names the blocking items and the operator
re-runs after the work lands, re-declaring each item `completed` so the wait stays in the record —
that is a manual loop, not an automatic one, and the PR body says so under what this change does
not do. Satisfying AC 4 needs the producer-owned receipt surface declined in §3 C.

## 5. File changes

| File | Change |
|---|---|
| `hooks/launcher_lib.py` | 4 pure functions, 2 backstop preflights, early CLI gates, flags on 3 subcommands, 1 new subcommand, `_classify_launch` rows, payload fields |
| `skills/pane-handoff/SKILL.md` | enumeration prose (AC 1), the flags, the wait-and-re-declare instruction (AC 4), new `failed_step` rows |
| `skills/epic-run/SKILL.md` | one boundary line: the boundary handoff must pass a declaration, and what the refusal looks like |
| `tests/hooks/test_inflight_handoff_gate.py` | new — pure-function, vector, preflight, CLI and call-site-completeness tests |
| `tests/hooks/test_launcher_lib.py`, `test_adhoc_pane_handoff.py`, `test_adhoc_teardown_guard.py` | one `_handoff(...)` helper edit each |
| `README.md` | changelog entry (both tail tokens) |
| `.claude-plugin/plugin.json`, `plugins/rawgentic/.codex-plugin/plugin.json`, `tests/hooks/test_adversarial_review_registration.py` | version bump, all three surfaces |
| `docs/planning/campaign-log.md` | this child's roadmap slot |

`plan_lib.lane_decision` returned `lane`; the lane was **declined** (D245) because the deliverable is
a refusing gate on the most safety-critical function in the module — and the cross-model layer the
lane drops then found nine High defects across two passes.

## 6. Error handling and failure modes

| Failure | Behaviour |
|---|---|
| `inflight` omitted at the library boundary | `LauncherError` before anything runs |
| No declaration flags on any handoff CLI | `LauncherError` naming the three flags |
| Any item `running` | REFUSE, every running item named |
| `abandoned` without the override | REFUSE |
| Override with no `abandoned` item | REFUSE — a false audit record |
| Any of the above on `handoff` / `mid-child-handoff` | Same REFUSAL, raised EARLY — before `mark_split_attempted`, so nothing is parked and the re-run is clean |
| `--inflight-none` plus `--inflight` | REFUSE at argparse and in the function |
| Bad kind/state/ident, control chars, over-length, bind directive in a field | `LauncherError` before anything runs |
| Session-scoped path in the augmented prompt | REFUSE `durable_path`, each path named with its reason and the two fixes |
| Path scanner raises unexpectedly | REFUSE `durable_path`, reason names the exception class |

## 7. Security implications

Read-only regex over operator-supplied text; nothing is executed, opened, or resolved on the
filesystem. No new egress, no new file writes, no new credential surface. Three security-relevant
properties: the ReDoS bound (§4.4); **no free-text reaches the successor's prompt** (§4.3, the
strongest available answer to prompt injection — exclusion, not fencing); and both new gates fail
CLOSED (§4.6).

## 8. Review provenance and dispositions

**Peer consult (gpt-5.6-sol) — adopted:** the hook writes the abandoned-work block; item `state`
with the stricter override rule; dropping `--allow-session-path`; the `_UNSET` sentinel; treating
declared text as untrusted; path normalization. **Declined:** a separate artifact manifest (a second
declaration burden, no measured failure behind it); a versioned JSON schema (capped repeatable flags
carry the same fields).

**Adversarial pass 1 (gpt-5.6-sol) — 4 High, 1 Medium, all applied.** `abandoned` recast as a
disposition; the sentinel now raises; §2 backed by a probe; the scanner fails closed; the
readability claim probed and corrected.

**Adversarial pass 2 (gpt-5.6-sol) — 5 High, 2 Medium.**

| # | Sev | Finding | Disposition |
|---|---|---|---|
| F1 | High | `clear-prep` is claimed in scope but not integrated | **applied** — §4.8 defers it explicitly; the PR body states it is not covered |
| F2 | High | The gate attests, it does not detect — stop calling H1 solved | **applied** — §1 restates H1 as an attestation gate and names what is not bought. The recommended producer registry is **declined** (§3 C): the producers cannot be instrumented from this repo |
| F3 | High | The path check proves neither durability nor completeness | **applied** — §1 restates H2 to exactly "rejects predecessor-session-scoped references". Receipts **declined** as a separate contract |
| F4 | High | The 2026-08-06 probe post-dates the review's context, so it is not yet evidence | **refuted** — the reviewer's clock is stale. `date -u` in the probing session printed `2026-08-06T05:45:52Z`. Its fair sub-request WAS honoured: §2 now reports the same task observed mid-run and after completion |
| F5 | High | Mandatory enforcement ships while the cached caller cannot be updated | **applied, then SUPERSEDED by P3-F1** — revision 3 split the rollout; revision 4 drops the split and keeps the safety requirement structurally instead (early placement, self-explaining refusal, the measured rc-8 precedent) |
| F6 | Medium | "Token" and "Markdown punctuation" have no defined grammar | **applied** — §4.4 states the exact pattern, the normalization, the non-goals, and ten test vectors |
| F7 | Medium | Fencing does not isolate untrusted text from a model | **applied** — §4.3 removes free text from the prompt entirely |

**Self-reviews (security lens) — 4 High across two passes, all applied:** the frozen-cache liveness
trap (converged with F5); untrusted text in the successor prompt (converged with F7); the
`mark_split_attempted` parking hazard on the campaign path (§4.5 — found by reading shipped code,
not raised by either reviewer); and the missing `_classify_launch` rows for the new failed steps.

**Adversarial pass 3 (gpt-5.6-sol) — 4 High, 2 Medium, all applied.** The gate then CLOSED
budget-exhausted at the `design` source cap (#798); no third loop-back was consumed.

| # | Sev | Finding | Disposition |
|---|---|---|---|
| P3-F1 | High | The rollout split leaves two commands able to retire a predecessor with work pending | **applied** — the split is dropped; all three enforce. Pass 2's liveness concern is met structurally (early placement, §4.5) rather than by weakening the gate, and this campaign's own rc-8 sweep gate is the measured evidence that a self-explaining refusal migrates an LLM caller (D247) |
| P3-F2 | High | AC 4 was claimed prose-satisfied while nothing polls | **applied** — §4.10 records AC 4 as UNSATISFIED |
| P3-F3 | High | `clear-prep` is in scope and unintegrated, so the issue cannot close | **applied** — the PR says `Part of #726`, the issue stays OPEN with a residual comment (D247) |
| P3-F4 | High | `ident` is caller text in the prompt; the regex admits `ignore_previous_instructions` | **applied** — §4.3's block is now 100% hook-authored: a count and allowlisted kinds only |
| P3-F5 | Medium | The declaration contract does not define `items=[]` vs `None` vs omission | **applied** — §4.1 makes it a closed object with exactly one alternative selected; `None`, bare `[]`, both and neither all refuse |
| P3-F6 | Medium | The payload has no durable sink, so "every answer is recorded" overclaims | **applied** — §2 and §4.9 narrow the claim to the result payload plus the caller's run-record; a new store is declined under the epic design's no-new-state-machines ruling |

**Known bounds, stated not fixed:** a caller can attest falsely; a non-session `/tmp` path or an
empty in-repo file passes; the tokenizer does not handle quoted, percent-encoded or
variable-aliased paths; AC 5 only sees paths that appear in the prompt; `clear-prep` is uncovered;
AC 4 is unsatisfied; and the declaration has no durable sink beyond the caller's own record.

## Platform / external dependencies

```
platform_apis:
- api: harness session scratch root — /tmp/claude-<uid>/<cwd-slug>/<session-uuid>/
  feasibility: verified via spike — probed live 2026-08-06. `ls -d
    /tmp/claude-1000/-home-rocky00717-rawgentic/f4545f82-f0cb-47d5-abbf-de07d4b29911/scratchpad`
    succeeded; the 4th component is byte-equal to `printenv CLAUDE_CODE_SESSION_ID`. That is the
    exact shape rule 2 of §4.4 matches on.
  failure: fail-loud
- api: harness background-task artifacts — <scratch-root>/tasks/<task-id>.output
  feasibility: verified via spike — the SAME task observed twice on 2026-08-06. Mid-run, task
    `biiftwaky` read 14 bytes of its own stdout with no sibling file; after its later ticks the
    same path read 49 bytes, still with no sibling, no status and no exit code. Three earlier
    tasks matched. NEGATIVE result, load-bearing: it is why §2 declares rather than probes.
  failure: fail-loud
- api: cross-session readability of /tmp/claude-<uid>
  feasibility: verified via spike — 2026-08-06 `stat` reported `drwx------ rocky00717:rocky00717`
    on `/tmp/claude-1000`, and 207 sibling session directories (oldest 2026-08-01) were listable
    from this session. REFUTES the issue body's stated reason; §4.4 carries the corrected one.
  failure: fail-loud
```

No dependency, migration, or new cross-service surface is introduced.

## 9. Multi-PR assessment

Single PR. One module, two skill files, one new test file, three one-line test-helper edits.
