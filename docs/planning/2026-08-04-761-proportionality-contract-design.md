# #761 — Task-class field: defined, snapshotted, injected inert

**Issue:** #761 (Part of epic #756; shipped under the epic #875 M1 campaign) · **Date:** 2026-08-05
**Base:** `origin/main` = `1d834296` (post-#886) · **Baseline:** 5080 passed / 0 failed, exit 0
**Complexity:** `complex_feature` — Step 2's authoritative classification, deliberately unchanged. The
rescope makes the *work* smaller (one new hook module, three prose surfaces, no gate-behaviour
change), but the lane stays FULL: `fast_path_eligible` remains **false**, so no design-stage ceremony
is dropped and the cross-model adversarial-on-design sub-step still runs.

> **RESCOPED by the owner, 2026-08-05.** Authority: `#761 issuecomment-5187270371`. This issue
> delivers **AC1**, **AC5**, and the **injection half of AC2**. **AC4** (the WF2-lite lane) and
> **AC6** (authorize-without-charging + atomicity) moved to **#923**. See "What #923 inherits".
>
> **Revision 6** folded all 11 findings from Step-4 pass 5 (7 cross-model + 4 self-review) under an
> owner-granted design pass past the exhausted `design` source (**D203**; counters left honest at
> `design 2/2`, `total 2/3`).
>
> **Revision 7 — the D204 fold.** Pass 6 returned **13** findings and the `design` source refused at
> 2/2 again with the ambiguity breaker unclear, so the owner **closed the Step-4 gate by override**
> (**D204**) — implement-with-constraints, rather than a third design pass, another split, or folding
> into #923. The 13 findings are now **implementation constraints C1–C13**, all folded into the design
> below in one pass per the override's own condition: the doc must not ship carrying known gaps. The
> 7 High ones hold terminal ADOPTED dispositions in
> `claude_docs/.wf2-state/761/dispositions.jsonl`. Constraint map: the provenance appendix.

## The problem, verified not paraphrased

`docs/reviews/session-mining-2026-07-30.md:98` reads verbatim: *"…no actor at any gate owning the
question 'should this exist at this size at all?' … That is the gap: proportionality has no seat at
any gate."* The issue's evidence: 28,884 LOC / 15 PRs / 42 review rounds in 3 days for a run-once
scrape the owner priced at "a couple of hours".

A task class (`disposable | internal | production`) is the field that eventually lets reviews scale
their **demands**. This issue builds the field, wires it to the prompt surfaces, and makes it
visible. Nothing yet reduces any demand.

## Settled spec readings

1. **AC1's "issue template" surface does not exist.** There is no `.github/ISSUE_TEMPLATE/` in this
   repo, so "issue template" means the **WF1 create-issue body shape** — how issues are actually
   filed here. No GitHub template file is created.
2. **AC3 is struck through in the body; AC5's halt clause is moot by supersession.** #798 shipped the
   opposite of AC3 (`hooks/plan_lib.py` `budget_exhausted_close`). AC5 therefore scopes to its first
   clause only: prompt-injection presence guards.
3. **AC2 splits cleanly.** Its two verbs are separable: *inject the class* (here) and *instruct
   reviewers to scale demands to it* (#923, with the lane). Inert-first sequencing means scrutiny can
   never drop before the lane that justifies it exists.

## What ships, and what deliberately does not

| Ships here | Deferred, and to where |
|---|---|
| The three-value field, its canonical issue-body line, and its exact grammar | Demand-scaling instructions → **#923** |
| A `.rawgentic.json` top-level `defaultTaskClass` project default | The WF2-lite lane + definition of done → **#923** |
| Resolution + a run-stable snapshot at WF2 Step 1, with a validated schema | Authorize-without-charging / debit-at-first-use + atomicity → **#923** |
| The resolved class **wired through named call sites** into the WF1 / WF5 / WF13 prompt surfaces, inert | Author-permission check on a permissive class → **#923** (**D202**) |
| Enum validation inside the prompt builders themselves, plus presence and injection-safety guards (AC5) | |

**Why the author-permission check is deferred (D202).** Revision 4 honored `disposable`/`internal`
only when the issue author held `write`/`admin`. While the field is inert, **that check protects
nothing** — no demand is reduced by any class — yet it costs a `gh api` call per run and a second
platform dependency with its own failure path. It becomes load-bearing in the same change that makes
a permissive class actually reduce scrutiny, so it ships there. AC1 asks only for a conservative
default, which is unchanged and, below, strengthened.

---

# The design

## The grammar (resolves **F3** and **F1** of the grammar's own contradiction)

**One canonical line**, anywhere in the issue body:

```
**Task class:** disposable
```

**Candidate collection is by PREFIX, then validated — not by whole-line match.** Pass 5 found the
bug this fixes: a whole-line regex means a line with trailing junk matches *nothing*, so it counts as
"zero candidates" and silently takes the config default, contradicting this document's own promise
that trailing text is malformed rather than accepted. Worse, one valid line plus one malformed
duplicate would have accepted the valid class instead of failing closed. The rule is therefore:

1. **Collect** every non-excluded line whose whitespace-trimmed, case-insensitively-compared prefix
   is `**Task class:**`. This catches malformed lines too — that is the point.
2. **Zero candidates** → the config default, else `production`. No diagnostic; this is the normal case.
3. **Exactly one candidate that fully matches the grammar with a recognized value** → that value.
   Full grammar: `^\s{0,3}\*\*Task class:\*\*[ \t]+(\S+)[ \t]*$`, **matched case-INSENSITIVELY
   (`re.IGNORECASE`) — C9**, value normalized with `strip().casefold()` and required to be exactly one
   of `disposable`, `internal`, `production`. The full match must agree with step 1's
   case-insensitive prefix rule: if it did not, `**TASK CLASS:** disposable` would be *collected* as a
   candidate and then *rejected* as malformed, so a correct line in the wrong case would fail closed
   to `production` with a diagnostic instead of simply resolving. Two rules over one string must read
   the case question the same way.
4. **Anything else** — a malformed candidate, an unrecognized value, or two or more candidates →
   **`production`**, and **the config default is NOT consulted**, and a diagnostic is recorded
   listing every relevant **original** line number.

Two or more candidates fail closed **even when they agree**. An "agreeing duplicates are fine" clause
is one more branch for an implementer to read differently, which is the defect this rule removes.

### Extraction boundary — a precise line-state algorithm (resolves **F4**)

"Not inside a code fence" is not implementable as prose, so the algorithm is stated exactly. Walk the
body **line by line**, tracking one piece of state: the open fence (`None`, or a `(char, run_length)`
pair). For each line, with `indent` = count of leading spaces (tabs expanded to 4):

- **Fence delimiter:** `indent <= 3` and the trimmed line begins with a run of **3 or more** `` ` ``
  or **3 or more** `~`. Let `(char, run)` be that character and run length.
- **No fence open** and the line is a fence delimiter → open a fence with `(char, run)`. Any trailing
  info string is allowed and ignored. The delimiter line itself is **excluded**.
- **A fence is open** → the line is **excluded**. It closes the fence only if it is a fence delimiter
  with the **same** `char`, a run **>= the opening run**, and **no** info string after it.
- **`indent >= 4`** → **excluded** (an indented code block; a candidate line may carry at most 3
  leading spaces, matching the grammar above).
- **A line beginning `>`** after trimming → **excluded** (block quote).
- **An unclosed fence excludes everything to the end of the body.** Fail-closed by construction: an
  unterminated fence can only exclude more, never less.

**Original line numbers are preserved throughout**, because diagnostics report them.

Without this, an issue that *documents* the field — this very issue does — would set it.

**An EXCLUDED candidate-looking line emits an informational diagnostic (C10).** Exclusion is silent
otherwise, and silence here is indistinguishable from "the author never wrote a class line" — so an
author who put the line inside a fenced example, a block quote, or at 4+ spaces of indent would get
`production` by the *normal* path, with no signal that the line they wrote was ignored. The
diagnostic names the original line number and which rule excluded it (`fenced` | `quoted` |
`indented`). It is informational, not a fail-closed trigger: an excluded line still leaves **zero
candidates**, so resolution proceeds to the config default exactly as rule 2 says. Documenting the
field must stay free; being ignored silently must not.

## The config default

Top level of `.rawgentic.json`, mirroring the workspace file's `defaultProtectionLevel`:

```json
"defaultTaskClass": "internal"
```

Absent → `production`. Present but not one of the three values → `production` **with a diagnostic**
(strict parse, safe default, visible warning — the established hook-config discipline in
`projects/rawgentic/CLAUDE.md` §1, whose narrow exception also licenses reading **only this one key**
directly rather than through `capabilities_lib derive`).

**The exact precedent, cited as code rather than as prose (C6).** A rule quoted only from `CLAUDE.md`
is unverifiable at review time, so here is a live call site doing exactly this: `read_meter_config`
at **`hooks/context_meter.py:924-939`**, whose own docstring states the pattern and whose read is
`_load_json_capped(os.path.join(project_path, ".rawgentic.json"))` at **`:937`** — one key
(`contextMeter`), a capped read, fail-open on a malformed file. `task_class_lib.read_config_default`
follows that shape exactly: one key (`defaultTaskClass`), a 512 KiB cap, `return None` on any
`OSError`/`ValueError`. Verified present at this branch's base. The same docstring names three more
(`security-guard.py:81-96`, `security_guard_lib.py:206-223`, `plan_lib.py:765`), so this is the
established pattern rather than a new exception.

**Precedence:** a valid single issue-body line > `defaultTaskClass` > `production`. Note rule 4
above: on a malformed body line the config default is bypassed entirely.

## The snapshot (resolves **F2** of pass 4, plus **F5** and **F6** of pass 5)

Resolved **once** at WF2 Step 1 into `claude_docs/.wf2-state/<issue>/task_class.json`; every later
surface reads the snapshot, never the live issue. The body is mutable and this campaign has measured
body-rot as a live hazard — the #875 revalidation gate exists because 14 of 23 bodies had rotted.

**Write-once-adopt, not a run-keyed path.** The writer is **atomic create-if-absent**, and the
shipped sequence is stated in full — pass 5 correctly caught that an earlier draft omitted the
directory creation the probe actually performed:

```
mkdir(parents=True, exist_ok=True)  ->  mkstemp(dir=<same dir>)  ->  write  ->  flush  ->
fsync(file)  ->  os.link(tmp, final)  ->  fsync(DIRECTORY)  ->  unlink(tmp)
```

**The directory fsync is load-bearing, not belt-and-braces (C2).** `os.link` gives atomic
**visibility** — the name either exists or it does not — but it does not make the new *directory
entry* durable. Without `fsync` on the containing directory, a host crash after a run has already
read and acted on the snapshot can lose that entry, and the next run then re-resolves against a
possibly-mutated issue body: the class silently changes for an issue that had already decided it,
which is precisely the immutability this design rests on. Visibility and durability are different
guarantees and only one of them comes free. The implementation opens the directory `O_RDONLY`,
`fsync`es that descriptor, and closes it in a `finally`.

`os.link` raises `FileExistsError` when `final` exists, so the loser of a race **adopts** the
winner's snapshot instead of overwriting it. Consequently, for a given issue the class is decided
**exactly once** and is immutable for every run and every gate — no run can observe it change, which
is the guarantee this design rests on.

- **Why not key the file by run/session id:** WF2 explicitly spans sessions
  (`references/state-and-resume.md` is a whole resumption protocol), so a session-keyed snapshot
  would vanish on resume and the resumed run would re-read a possibly-rotted body. That trades an
  overwrite hazard for a worse one.
- **Deliberate bound:** a genuinely fresh run on the same issue *before* merge adopts the earlier
  class. The escape is explicit and logged, never automatic: delete
  `claude_docs/.wf2-state/<issue>/task_class.json` to force a re-resolve.
- **The escape carries a QUIESCENCE RULE (C4): delete only when no run on that issue is live.**
  Deleting under a run that has already *adopted* the snapshot is the one way two gates in the same
  run can observe **different** classes — the earlier gate saw the old value, the re-resolve writes a
  new one, and the later gate reads that. Write-once makes the class immutable *for as long as the
  file exists*; it cannot defend against the file being removed mid-run. The rule is therefore part
  of the contract, not operator folklore, and it is stated at every surface that mentions the escape
  (the `write_snapshot` docstring, WF2 Step 1's prose, and `docs/config-reference.md`).
- **Post-merge cleanup, cited exactly (C7):** `skills/implement-feature/references/step-14.md:5` —
  "on merge success, clean up `claude_docs/.wf2-state/<issue>/`". Coverage of `task_class.json` is
  **structural**: the snapshot lives inside that directory, so the existing directory-wide delete
  removes it with no new cleanup step and nothing to keep in sync. A post-merge re-run therefore
  starts clean. Because that coverage depends on the cleanup staying directory-wide, a test pins
  Step 14 to that path, so narrowing it to a file list later fails loudly instead of silently
  stranding snapshots.
- **Bounded temp residue:** a kill between `mkstemp` and `os.link` leaves one `.task_class-*.tmp` in
  the state directory. Bounded, never read by anything, and swept by Step 14's directory delete.

### The snapshot schema, and identity (resolves **F5**)

JSON-parseability is not enough — a *parseable* snapshot with the wrong contents could otherwise be
adopted and injected. Required, and validated on every read:

| Field | Type / constraint |
|---|---|
| `task_class` | string, exactly one of `disposable` / `internal` / `production` |
| `provenance` | string, exactly one of `issue_body` / `config` / `default` |
| `issue` | integer, and **equal to the issue being resolved** |
| `resolved_at` | non-empty string. **Producer format: ISO-8601 UTC, `%Y-%m-%dT%H:%M:%SZ` (C12).** The *validator* deliberately checks only non-empty-string, so a snapshot written by a future version with a different timestamp precision is still adoptable — tightening the read would turn a cosmetic difference into a fail-loud refusal on a field nothing computes with. Stated rather than left implicit so nobody adds a parse. |
| `diagnostic` | absent, or a non-empty string |

Any missing field, wrong type, off-enum value, **or an `issue` that does not equal the requested
issue** is **fail-loud**: refuse, naming the offending field and the delete-to-re-resolve remedy.
Never silently re-resolve — that would defeat write-once. Unknown extra keys are ignored (forward
compatibility), never a refusal.

### Feasibility, probed against the clean path (resolves **F6**)

Pass 5 asked whether the sequence works when the issue directory does **not** yet exist. Probed live,
2026-08-05, the literal shipped sequence above, with the directory verifiably absent first:

```
precondition: issue dir absent : True
1st outcome (clean path)       : created
dir created by the writer      : True
2nd outcome (overlapping run)  : adopted
first writer's value preserved : True
temp residue                   : none
```

The writer creates the directory itself, so no ordering guarantee from any other Step-1 component is
required. Evidence scope, stated honestly: this proves the path on **this host's filesystem** (ext4).
It does not prove portability — see the `os.link` failure row below.

**The support bound, named (C5).** The atomicity primitive is `os.link`, so this design requires a
filesystem that supports **hard links**. That holds on ext4/xfs/btrfs/APFS and on any POSIX
filesystem in normal use, and it does **not** hold on FAT/exFAT, on some network and container
overlay mounts, and on filesystems mounted with hard links disabled. The probe above is evidence for
one host's ext4 and nothing wider; the claim is a *requirement* stated up front, not a measurement
generalized past its scope. The consequence is deliberately a loud one: an `OSError` from `os.link`
that is not `FileExistsError` **aborts Step 1**, naming the path and the underlying `errno`, rather
than degrading to a non-atomic `os.replace` — a silent downgrade would trade a clear
unsupported-filesystem error for a race that decides an issue's class twice.

No lock is needed: `os.link` is the atomicity primitive, so this introduces no second lock order
against `plan_lib.file_lock` and no deadlock surface.

## The failure contract

| Event | Behaviour |
|---|---|
| **Issue fetch fails** (`gh issue view` non-zero) | **Fail loud, abort Step 1.** Not a class concern: Step 1 already cannot proceed without the body (`references/step-01.md:14` fetches `number,title,body,labels,state`). There is no run to assign a class to. |
| Fetch succeeds, no candidate line | `defaultTaskClass`, else `production`. Proceed. Normal, no diagnostic. |
| Fetch succeeds, candidate malformed / unrecognized / duplicated | `production` **with a diagnostic**, config default bypassed. Proceed. |
| Invalid `defaultTaskClass` in config | `production` **with a diagnostic**. Proceed. |
| Snapshot **write** fails | **Fail loud, abort Step 1** — later gates must never read an absent snapshot. |
| `os.link` raises an `OSError` that is **not** `FileExistsError` (no hard-link support on the filesystem) | **Fail loud, abort Step 1**, naming the path and the underlying errno. Named because the probe's evidence is host-filesystem-scoped; this is the portability boundary, not a silent one. |
| Snapshot exists but fails to parse, or fails the schema/identity check | **Fail loud, refuse**, naming the offending field and the delete-to-re-resolve remedy. |

So: **"never proceeds on an unread class" applies to an unreadable issue, an unwritable or invalid
snapshot, and an unsupported filesystem; `production` with a diagnostic applies to a readable body
that simply does not set a valid class.** One rule each, no overlap.

**Where a failed WRITE is logged (C8).** Every other surface in this design reports through the
Step-1 session-note marker — but a write failure happens *before* that marker exists, so naming the
marker as its destination would name a place the message can never reach, and the failure would be
visible only as a non-zero exit code. The destination is therefore **stderr, written by
`task_class_lib resolve` itself, before it returns rc 1**, in the form
`task-class: FAILED — <reason>` where `<reason>` names the path and, for an `OSError`, the
`strerror` and `errno`. The orchestrator surfaces that line when it aborts Step 1. Ordering stated
explicitly because it is the whole point: **emit, then exit** — never exit and rely on a marker that
was never written.

## Diagnostic surfacing (resolves **F2**)

Pass 5 was right that a "visible note" promised but never defined can be silent. Defined:

- **Exact shape, on stderr, once per Step-1 resolution**, after create *or* adopt:
  - normal: `task-class: <class> (provenance=<issue_body|config|default>, snapshot=<created|adopted>)`
  - fallback: the same line plus ` DIAGNOSTIC: <reason>`, where `<reason>` names the cause and, for a
    body-line failure, every offending original line number.
- **Emitting call site:** `hooks/task_class_lib.py resolve`, invoked by WF2 Step 1. One emission per
  resolution, including on **adopt** — an adopted snapshot that carries a `diagnostic` re-surfaces it,
  so a later run never inherits a silent fallback.
- **Session-note surface:** the Step-1 marker carries the tail
  `task_class=<class> provenance=<p>[ diagnostic=<reason>]`.
- **Tests capture both surfaces** for: invalid config value, malformed body line, duplicate lines, and
  adoption of a snapshot that already contains a diagnostic.

**The diagnostic is NEVER passed to a prompt builder.** It is body-derived text, and the class line
sits outside the nonce fence; routing it there would put attacker-controlled text unfenced into a
reviewer prompt — the exact threat AC5 exists to guard (self-review S1, and pass 5's F7 from the
other direction). Its only destinations are the snapshot, stderr, and session notes.

## Injection points (AC2, injection half) — with the call sites named (resolves **F1**)

Defining the receiving functions is not enough: if no caller supplies the value, every builder test
passes while real prompts carry no class. The call sites, named and required:

| # | Surface | Change |
|---|---|---|
| 1 | `skills/create-issue/SKILL.md` Step 2 (WF1) | The drafted body carries the canonical line, `production` unless the user chooses otherwise, with the three values documented. This is the surface AC1 calls the "issue template". |
| 2 | `hooks/adversarial_review_lib.py` `build_prompt` (`:1111`) | New `task_class` argument, rendered as ONE line **outside** the nonce fence, stating the class and that no demand is scaled by it yet. |
| 3 | `hooks/adversarial_review_lib.py` `build_consult_prompt` (`:1476`) | The same line, same placement. |
| 4 | `hooks/review_runner.py` (`:610`, `:628`) | New validated `--task-class` flag threaded into both builder call sites, plus `--issue`. An out-of-enum value is **refused** (exit 2, `invalid_input`, no egress), and so is `--issue` without `--task-class` (C3, below). |
| 5 | `skills/adversarial-review/SKILL.md` (WF5) | Its `review_runner.py` invocation **must** resolve the class via `task_class_lib.py read` and pass `--task-class` — plus `--issue` whenever an issue is in scope. Required on this path, not optional. |
| 6 | `skills/peer-consult/SKILL.md` (WF13) | Same requirement on its `consult` invocation. |

`task_class_lib.py read` takes an **optional** `--issue`: with one, it reads that issue's snapshot;
without one (WF5/WF13 can review an artifact with no issue in scope), it returns the config default,
else `production`. So the required paths always have a value to pass.

**An omitted issue number must be DETECTABLE, not silently the config default (C3).** The two cases
that `read` handles by design — issue-scoped, and legitimately issue-less — are indistinguishable at
the *runner* if the caller simply forgets to pass anything. That forgetting is the realistic failure:
the review still runs, still succeeds, and renders the project default, so the reviewer is shown a
class the issue never set with **no failure and no diagnostic** anywhere. Silent-and-wrong is worse
here than loud-and-stopped, so the runner refuses:

| Flags at the runner | Behaviour |
|---|---|
| `--task-class X --issue N` | The issue-scoped path. Renders `X`. Both recorded on the result. |
| neither flag | The issue-less path. Renders `production` (strictest) and records `issue: null`. |
| `--issue N`, no `--task-class` | **REFUSED** — exit 2, `invalid_input`, zero backend calls. The message names `task_class_lib.py read --issue N` as the fix. |
| `--task-class` out of enum | **REFUSED** — exit 2, `invalid_input`, before any read or egress. |

The asymmetry is deliberate: `--task-class` alone is legal (an issue-less review still has a class,
from the config default), while `--issue` alone is not, because that is exactly the shape of a
forgotten resolve. Both refusals are placed after the `--out` preflight so the refusal lands in a
**receipt the orchestrator can read** — argparse `choices` would exit 2 with no receipt at all, which
is why the enum is validated in the function rather than declared to argparse.

**Integration test, not just builder unit tests:** seed a snapshot, drive the runner's own
prompt-construction path with the resolved class, and assert **both** generated prompts contain the
snapshot's value. Non-vacuity comes from making the snapshot's class and the config default
**different**, so any wiring that quietly fell back to the default fails the assertion rather than
passing on a coincidence. This stops below egress — an injected runner captures the composed prompt
and returns a canned body — so no backend call is needed.

**The test must also cover the PROSE handoff (C1).** Rows 5 and 6 are markdown, and no Python test
can execute them, so the integration test above would stay green if the WF5/WF13 instruction were
reworded away or dropped — leaving real prompts class-less while every machine-side assertion still
passed. That is the same vacuity F1 identified, one layer up. So the test file additionally **pins
the four prose surfaces**: WF5 and WF13 must each contain `task_class_lib.py read`, `--task-class`
and `--issue`; WF2 Step 1 must contain `task_class_lib.py resolve` and the snapshot path; and the WF1
draft contract must name the canonical line and all three values. A pin on prose is weaker than an
executed path and is not pretended otherwise — it catches deletion and rewording, not
misinterpretation. It is the strongest available check on a handoff whose executor is a language
model.

### Enum validation lives in the builders too (resolves **F7** and self-review **S1**)

Validating only the CLI flag makes the safety of direct or future library callers unverifiable. So
**`build_prompt` and `build_consult_prompt` each validate `task_class` against the enum themselves
and refuse (raise) on anything else** — the flag validation stays, as defence in depth. What is
interpolated is therefore always one of three literals, chosen and re-checked; raw issue text cannot
reach the prompt through this path at all, so there is nothing to escape. Untrusted artifact text
keeps its existing nonce-fence treatment, untouched.

**The builders DEFAULT to `production` and ALWAYS render the line (C11).** `task_class` is a keyword
argument defaulting to `production`, and the line is emitted unconditionally. Both halves matter, and
the failure they prevent is the same one twice: a default of `None` (or a conditional that skips the
line when no class was passed) would let a caller that forgets the argument produce a **class-less
prompt** — the exact vacuity this whole section exists to close, reintroduced at the last hop. And
when a class must be assumed, the assumption is the *strictest* one: degrading to `production` can
only over-state durability, never under-state it. So there is no code path through either builder
that yields a prompt without a class line.

Tests prove newline-bearing and instruction-bearing values are **rejected rather than rendered**.
`build_prompt`'s output is **byte-pinned** by
`tests/hooks/test_adversarial_review_codex.py:165 test_build_prompt_byte_identical_to_golden`; the
golden is re-captured in the same commit, so the pin *is* the presence guard.

### Blast radius, named and accepted (resolves self-review **S3**)

Rendering the line unconditionally means **every** WF5 review and WF13 consult in **every** project
that installs this plugin gains one prompt line, defaulting to `production`. That reach is real and is
accepted. The alternative — omit the line when the class is the default — was considered and
rejected: an absent line cannot distinguish "no class set" from "production", which is exactly the
ambiguity the field exists to remove. The change remains inert in *effect*: no demand scales.

## Where the code lives

A new **`hooks/task_class_lib.py`** (`resolve` · `read`, with a CLI, like every other hook lib). Not
`plan_lib.py`: it is already 3,570 lines and owns the gate/loop-back budget, a different concern. Not
`capabilities_lib.py`: `derive` is config-only and returns the whole capabilities object, whereas this
takes an issue body as input.

## Platform / external dependencies

```
platform_apis:
- api: issue-body retrieval for the class line
  feasibility: verified via existing-call-site — WF2 Step 1 already runs exactly this call, at
    `skills/implement-feature/references/step-01.md:14`:
    `gh issue view <number> --repo <repo> --json number,title,body,labels,state`.
    Same object kind, same surface, every WF2 run; no new field is requested.
  failure: fail-loud — a non-zero fetch aborts Step 1 (see the failure contract)
  surface: the Step-1 session-note marker
- api: atomic create-if-absent snapshot write via os.link into claude_docs/.wf2-state/<issue>/
  requires: a filesystem supporting HARD LINKS (C5). Holds on ext4/xfs/btrfs/APFS and POSIX
    filesystems in normal use; does NOT hold on FAT/exFAT, some network and overlay mounts, or a
    mount with hard links disabled. A non-FileExistsError OSError aborts Step 1 naming path and
    errno rather than degrading to a non-atomic os.replace.
  feasibility: verified via spike of the EXACT shipped sequence (mkdir -> mkstemp -> write -> flush
    -> fsync(file) -> os.link -> fsync(DIRECTORY) -> unlink), run live 2026-08-05 with the issue
    directory verifiably ABSENT
    beforehand: precondition "issue dir absent: True", first write "created", writer created the
    directory itself, second write "adopted" on FileExistsError, first writer's content preserved,
    zero temp residue. Probe output quoted verbatim above. Evidence scope: this host's ext4
    filesystem; portability is NOT claimed and its failure row is in the failure contract.
  failure: fail-loud — a failed write, or an OSError other than FileExistsError, aborts Step 1
  surface: session notes + the snapshot's own `provenance` / `diagnostic` fields, and the stderr line
```

## Tests

1. grammar: each of the three values resolves; label case-insensitive **in the full match as well as the prefix, so `**TASK CLASS:** disposable` resolves rather than being collected-then-rejected (C9)**; a candidate with trailing text ⇒ `production` + diagnostic (**not** the config default);
2. grammar: one valid line + one malformed candidate ⇒ `production` + diagnostic naming both line numbers;
3. duplicates: two agreeing valid lines ⇒ `production` + diagnostic;
4. fences: triple-backtick, longer-than-triple backtick, tilde, unclosed fence, 4-space-indented line, and `>` quote — each excludes its candidate; original line numbers preserved; **each excluded candidate-looking line emits an informational diagnostic naming the line number and the rule that excluded it (C10)**;
5. precedence: valid body line beats `defaultTaskClass` beats `production`; invalid config value ⇒ `production` + diagnostic;
6. snapshot: clean path from an absent directory ⇒ `created`; second write ⇒ `adopted`, content unchanged, no residue; **the containing DIRECTORY is fsynced after `os.link`, asserted by spying the fsync targets so visibility-without-durability cannot regress (C2)**;
7. snapshot schema: each of missing field / wrong type / off-enum `task_class` / off-enum `provenance` / **mismatched `issue` — a snapshot for another issue must never be adopted (C13)** ⇒ fail loud with the remedy named; unknown extra key ⇒ accepted;
8. diagnostic surfacing: stderr line and session-note tail captured for invalid config, malformed line, duplicates, and adoption of a diagnostic-carrying snapshot; **a failed WRITE emits `task-class: FAILED — …` on stderr before rc 1, i.e. before any session-note marker exists (C8)**;
9. builders: the class line is present in `build_prompt` and `build_consult_prompt`; each **rejects** a newline-bearing or instruction-bearing value; **omitting the argument renders the line at `production` rather than yielding a class-less prompt (C11)**; the golden byte-pin is re-captured;
10. runner: `--task-class bogus` refused (exit 2) before egress; absence of the flag leaves behaviour unchanged; **`--issue` without `--task-class` refused (exit 2, `invalid_input`, zero backend calls) so an issue-scoped review that forgot to resolve is distinguishable from a legitimately issue-less one (C3)**;
11. integration (AC2 non-vacuity): from a seeded snapshot through the runner's prompt-construction path, both prompts contain the snapshot's class **and not the config default, the two being deliberately different**;
12. prose pins **(C1 — the handoff no Python test can execute)**: the WF1 draft contract names the canonical line and all three values; WF2 Step 1 resolves and snapshots write-once; WF5 and WF13 each pass `--task-class` **AND** `--issue`;
13. cleanup coverage **(C7)**: Step 14 still names the whole `claude_docs/.wf2-state/<issue>/` directory, so the snapshot's post-merge removal stays structural rather than a file list that can drift.

## What #923 inherits from Revision 4

Not deleted — handed over, recoverable from this file's git history and
`claude_docs/.wf2-state/761/s4-adv-r4.json`: the per-class ceremony matrix and its never-reducible
rows (Step 8 TDD, Step 8a, Step 11, Step 11.5 hold for every class); the `disposable` definition of
done; the reservation state machine, its schema validation, the atomicity contract and its seven
tests; the reservation-recovery/reconciliation surface; the cross-version concurrency bound; and the
author-permission check (**D202**). #923 must also compose with `plan_lib.severe_findings_are_disposed`
(`hooks/plan_lib.py:3019`), which already gates `close-design-gate` — exhaustion alone no longer
closes the design gate, exhaustion over resolved ground does.

## PR shape

**One PR, `Closes #761`.** The Revision-4 two-PR split existed to sequence the field ahead of the
lane; the split into #923 now does that, so a second PR here has nothing left to carry.

# Provenance appendix (compressed — superseded prose is in this file's git history)

| Round | What it was | Outcome |
|---|---|---|
| **Rev 1** (Step 3) | Deliberately short draft; named what Step 4 must decide rather than deciding it | Pass 1: 5 findings (3 High / 2 Medium, 4 ambiguous). Under-specifying the hardest calls made the artifact unreviewable. One `design` loop-back. |
| **Rev 2** | Class→demands table, issue-body-authoritative + Step-1 snapshot, mint mechanics, inert-first sequencing, probed feasibility | Pass 2: 7 findings incl. a **Critical** — the mint mechanics delivered AC6's intent while conceding its literal wording. Breaker 6/7 ambiguous → blocker `issuecomment-5178563046`. No loop-back (a spec decision). |
| **Owner amendment** | `issuecomment-5180899385` | Adopted pass 2's rejected substance as an owner decision, promoting the atomicity objection to a hard requirement. |
| **Rev 3** | Folded the amendment; re-anchored citations at `8edb8f4c` | Pass 3: 12 findings (6 High / 6 Medium) incl. a real bug — the debit was tied to runner success, not to disposition opening a round. The **last** automatic `design` loop-back. |
| **Rev 4** | Resolved all 12; consolidated to one canonical design | Pass 4: 8 findings; `design` source EXHAUSTED (2/2) with the ambiguity breaker unresolved → blocker `issuecomment-5186962900`. |
| **Owner: split** | `issuecomment-5187270371` | AC4 + AC6 → #923. Four pass-4 findings touch the field and stayed: grammar, snapshot scope, write feasibility, failure contract. |
| **Rev 5** | Rescoped to AC1 + AC5 + AC2-injection; resolved those four | Pass 5: 11 merged findings (7 cross-model + 4 self-review; 4 High, 6 Medium, 1 Low). Fold → `design`, refused at 2/2; breaker NOT clear (3 ambiguous) so the #798 carve-out did not apply → escalated to the owner. |
| **Owner: one more design pass** | **D203** | Granted over splitting again / overriding the breaker / parking the issue. Counters left honest at `design 2/2`, `total 2/3`; the grant is recorded, not counted. |
| **Rev 6** | Resolves all 11: prefix-then-validate candidate rule, a precise fence algorithm, snapshot schema + identity, the clean-path probe, named call sites + an integration test, builder-side enum validation, defined diagnostic surfacing, the `os.link` portability row, the named blast radius, and the temp-residue bound | Pass 6: **13 merged findings** (3 High design-flaw). Breaker ambiguous on F5/F6/F7; `design` refused at 2/2 again → escalated. |
| **Owner: close by override** | **D204** | The Step-4 design gate was closed **by owner override** rather than by a further design pass, a further split, or folding into #923. The 13 findings became **implementation constraints C1–C13**; the 7 High ones carry terminal ADOPTED dispositions in `claude_docs/.wf2-state/761/dispositions.jsonl`. The override's condition: the design doc must not ship carrying known gaps. |
| **Rev 7 — this one** | The D204 fold: every one of C1–C13 written into the design above in one pass, so the shipped doc and the shipped code agree. See the constraint map below. | — |

### The D204 constraint fold (C1–C13), and where each one landed

Recorded as a map rather than prose so a reader can check the doc against the dispositions ledger
without reconstructing which finding became which paragraph. "Doc gap" means the constraint was
already satisfied in *code* by an earlier task but the design text did not say so — D204's condition
is about the text.

| # | Constraint | Where it now lives in this doc |
|---|---|---|
| C1 | The integration test must cover the **prose handoff**, not just the path after resolution | "The test must also cover the PROSE handoff", plus test 12 |
| C2 | **fsync the containing DIRECTORY** after `os.link` — atomic visibility is not durability | "The directory fsync is load-bearing", the shipped sequence, test 6 |
| C3 | An omitted issue number must be **detectable**, not silently the config default | The runner-flag matrix under "Injection points", plus test 10 |
| C4 | **Quiescence rule** for the delete-to-re-resolve escape | The third snapshot bullet |
| C5 | State the **hard-link support bound**; the probe's evidence is host-scoped | "The support bound, named", plus the platform block's `requires:` |
| C6 | Cite an **exact direct-config-read call site**, not `CLAUDE.md` prose | "The exact precedent, cited as code" (`context_meter.py:924-939`) |
| C7 | Cite the **exact post-merge deletion call site** and cover `task_class.json` | The fourth snapshot bullet (`step-14.md:5`), plus test 13 |
| C8 | Define the **log destination when the WRITE itself fails**, before any marker exists | "Where a failed WRITE is logged", plus test 8 |
| C9 | Make the **full-match regex case-insensitive** so it agrees with the prefix rule | Grammar rule 3, plus test 1 |
| C10 | Emit a diagnostic when a candidate-looking line was **EXCLUDED** | "An EXCLUDED candidate-looking line…", plus test 4 |
| C11 | Builders **default to `production` and always render** | "The builders DEFAULT to `production`…", plus test 9 |
| C12 | State the `resolved_at` **format** (or declare it unconstrained) | The schema table's `resolved_at` row |
| C13 | **Schema + identity** validation on every read | "The snapshot schema, and identity" (already present), plus test 7 |

**Stale citations, recorded so nobody re-follows them:** Revisions 1–2 cite `steps.md:1261`, `:1290`,
`:1069`, `:1087`, `:115`, `plan_lib.py:1602`, `:2836`, `review_runner.py:550`. All are wrong at
`fb4952db` (#874 split `steps.md` into `step-NN.md`; #902/#903 shifted `plan_lib.py`). The anchors in
the body above were re-verified at `fb4952db`.
