# #796 — prose-to-script batch: design (rev 4, post Step-4 pass 2)

**Issue:** #796 (epic #756) · **Complexity:** standard_feature · **Lane:** eligible, declined
(full spine) · **Base:** `9b37a11d` v3.115.1 · **Baseline:** 6520 passed / 21 skipped / exit 0
· **Loop-backs:** design **2/2 (cap reached)**, global 2/3

## Revision history

- **rev 1** — ship candidates 1+2 folded (checker) and 3.
- **rev 2** — after a blind cross-model peer consult, pivoted candidate 1 to a WRITER
  (`hooks/release_surfaces.py bump`) and dropped candidate 2.
- **rev 3** — Step-4 pass 1: `SHIPPABLE: NO`, 5 High across two reviewers. **Writer dropped**;
  candidate 1 became a CHECK-ONLY verb on an existing module, dissolving the traversal, file-mode
  and destructive-recovery defects outright.
- **rev 4 (this)** — Step-4 pass 2: `SHIPPABLE: NO` again, 5 High. **Both reviewers converged
  independently on the same defect: a consistency checker cannot detect a MISSING bump.** If a PR
  changes nothing, all four surfaces still agree and `release` reports clean — and I confirmed CI
  misses it too, because `test_plugin_version_bumped` asserts a literal that equals whatever
  `plugin.json` currently says. Rev 3 also *removed* epic-run's explicit bump instruction while
  replacing it with a check that cannot enforce it — strictly worse than before. Rev 4 makes the
  verb prove a **transition against a base ref**, keeps the explicit instruction, hardens every
  read, and closes Ship B's vacuous-pass hole.

## The problem, restated

WF2/WF3 carry mechanics as prose. Prose re-derives every run, costs orchestrator tokens, and
drifts silently. The issue lists eight candidate verbs and explicitly permits shipping a subset
(AC3) provided anything not shipped is named with a reason.

**The drift is not hypothetical.** The count of version surfaces is copied by hand into prose in
three places and **two are already stale**: `skills/epic-run/SKILL.md:67` says "version bump ×3
surfaces" and `~/.claude/skills/pr-preflight/SKILL.md:71,134` lists three and says "two of the
three", while `projects/rawgentic/CLAUDE.md` correctly says four. It has been four since #470
added `canary.EXPECTED_PLUGIN_VERSION`.

## What ships

### Ship A — `skill_registration_check.py release` (candidate 1, CHECK-ONLY)

**Home: the existing `hooks/skill_registration_check.py`.** Rev 2 minted a new module and
Step-4 F4 (High, scope_fidelity) correctly called that a violation of the issue's explicit
"extend existing hooks … rather than minting new modules". This module's actual job —
"grep-discovers ALL hand-pinned count copies … so stragglers can't hide" — is precisely the job
for version literals too. Its `Finding` dataclass (`:58-63`), `run_checks` shape (`:348`) and
rc 0/1/2 CLI contract (`:354-381`) are reused unchanged.

It is a **separate `release` subcommand, not a new family inside `sweep_hand_pins`.** That is
deliberate: the peer consult objected that adding a partial pin family to the generic sweep
manufactures false confidence exactly where the scanner's exclusions already hide drift
(`phase_executor/` outside `SWEEP_GLOBS`, `docs/**` excluded by design, **337 canonical-sentence
prose pins** unswept, and the checker is not run in CI). A distinct subcommand claims exactly
what it verifies and nothing more.

```
python3 hooks/skill_registration_check.py release --project-root . --base-ref origin/main
```

Read-only. Reports, and requires:

0. **A release actually happened** (rev 4 — the convergent pass-2 finding). The verb reads the
   base's `.claude-plugin/plugin.json` via `git show <base-ref>:.claude-plugin/plugin.json` and
   requires HEAD's version to be **strictly greater** by semver. Consistency alone is not
   enough: an untouched tree is perfectly consistent, so rev 3's checker would have passed a PR
   that skipped the bump entirely — and so would CI, since
   `tests/hooks/test_adversarial_review_registration.py:44` asserts a literal that equals
   whatever `plugin.json` currently says. Only the base comparison distinguishes "correctly
   bumped" from "never bumped".
   Reading ONE file at the base (not all four) is sufficient: HEAD-side agreement across the
   four plus a strictly-advanced manifest version plus a changelog entry for that version proves
   the whole invariant.
1. All four version surfaces agree with `.claude-plugin/plugin.json` (the source of truth the
   other three already relate to), each located by its **own exact-one anchor** — never a bare
   semver scan, which would match unrelated literals such as
   `tests/test_workflow_diagram.py:50`'s `'"3.10.0"'`:

   | Surface | Anchor |
   |---|---|
   | `.claude-plugin/plugin.json` | JSON key `version` (parsed, not regexed) |
   | `plugins/rawgentic/.codex-plugin/plugin.json` | JSON key `version` |
   | `phase_executor/src/phase_executor/canary.py` | `^EXPECTED_PLUGIN_VERSION = "…"$` |
   | `tests/hooks/test_adversarial_review_registration.py` | `assert plugin["version"] == "…"` |

   Anchoring on `EXPECTED_PLUGIN_VERSION` specifically keeps `EXPECTED_REGISTRATION_DIGEST`
   three lines below (`canary.py:41`) out of scope — it is **not** version-coupled
   (`compute_registration_digest:229-263` reads no version).
2. The README's newest `### vX.Y.Z` changelog entry matches that version.
3. That entry carries **both mandatory tail tokens**.

rc 0 clean / rc 1 stale, naming each stale surface / rc 2 usage error.

**The tail-token grammar — the ambiguity Step 4 flagged, now resolved.** Both reviewers
independently flagged that "a diagram decision" and `Suite <old>-><new>` had no executable
grammar. Derived from the repo's own live corpus (the four newest entries):

Both patterns are **boundary-anchored** and must match **exactly once** in the newest entry's
bullet — pass 2 showed an unanchored "contains" test would accept `no diagram REVISION`,
`diagram REV 1.2.3.4`, `Suite 10->20junk`, and duplicate or conflicting tokens:

- **diagram decision** — exactly one match of
  `(?<![\w.])(?:no diagram REV|diagram REV \d+\.\d+\.\d+)(?![\w.])`
- **suite tail** — exactly one match of `(?<![\w-])Suite \d+->\d+(?![\w-])` (ASCII `->`; every
  live entry uses it)

Verified against the live corpus: all six entries at `README.md:730-746` satisfy both patterns,
including the `diagram REV 3.114.0` entry — the grammar rejects none of them.

**One parser, `parse_newest_changelog_entry(readme_text)`, is shared by the `release`
subcommand and the guard test**, so `check` can never report clean while the hard test fails —
the exact divergence self-review F6 predicted.

**A guard test** asserts the same three properties against the real README, so the invariant
holds even when a human edits by hand. Verified safe against HEAD before relying on it: newest
is `v3.115.1`, `plugin.json` is `3.115.1`, and all four newest entries carry both tokens.

**Why check-only and not a writer.** Rev 2 proposed `bump`. Step 4 found, and I confirmed
against source:
- `--project-root` is user input, so the "targets are not input-derived" claim was false; a
  symlinked parent could redirect `os.replace` outside the repo (F1, High).
- `atomic_write_lib.atomic_write_text:40-47` writes via `tempfile.mkstemp` (mode **0600**) then
  `os.replace`, preserving no mode — all four targets are **0664** today, so every bump would
  silently re-mode four tracked files (F2, High).
- The proposed recovery, `git checkout -- <paths>`, **destroys any pre-existing uncommitted work
  on those files** — the repo's own manual flags this as mistake #20 (F2/A1, High).
All three vanish when nothing is written. The remaining benefit of a writer was saving four
one-line edits; the cost was a first mutating path with three confirmed defects before a line
was written. Candidate 1's observed pain — "one forgotten = red CI" — is fully addressed by an
answer available locally before push, because all four surfaces are already test-guarded so the
failure was never a silent ship. **AC3 records candidate 1 as shipped-as-checker, not as the
writer the row names.**

### Ship B — `plan_lib assert-pr-body` (candidate 3)

`assert_pr_body_has_deferred_section` (`plan_lib.py:345`) and `deferred_tasks` (`:294`) are
pure, tested, and have **no production caller** — they run only when a model reads
`skills/implement-feature/SKILL.md:518-519` inside the END-OF-RUN completion gate. The #781 slip
fired there, after merge (`docs/measurements/2026-08-01-762-retrospective.md:213-222`).

Home: `hooks/plan_lib.py` — a genuine seam. Its docstring already lists "Deferrals tracking +
mechanical resolution gates"; `close-design-gate` (`:2719`) is the CLI house pattern this copies.

```
python3 hooks/plan_lib.py assert-pr-body \
    --pr-body-file /tmp/wf2-pr-body.md \
    --plan-file <the SAME impl-plan.md this run passed to mint-gate/dispatch at Step 8>
```

rc 0 satisfied / rc 1 assertion failed with reasons / rc 2 usage or malformed input.
**Writes nothing.**

**A zero-task plan is malformed, not a vacuous pass (rev 4 — pass-2 self F3 / adv #4).**
`assert_pr_body_has_deferred_section` returns `(True, [])` unconditionally when its task list is
empty (`plan_lib.py:352-353`). That is correct for the function, but at a *gate* it means an
empty, truncated, or simply wrong plan file parses to zero tasks and silently authorises a PR
body that omits real deferrals — the gate would pass hardest exactly when its input is worst.
So the verb **rejects a plan that parses to zero tasks with rc 2** before ever calling the
assertion. Both reviewers flagged this independently; the self-review additionally noted that
rev 3's generic `<f>` placeholders left the verb unbound to the run's real artifacts, hence the
explicit paths above — `/tmp/wf2-pr-body.md` is fixed by `references/steps.md:1518-1524`, and the
plan file is bound to the same one Step 8 already feeds to `mint-gate`/`dispatch`, not a fresh
placeholder.

**No `--project-root` containment — deliberately, and this is the design's most load-bearing
correction.** The peer consult caught that rev 1 gave this verb containment by reflex, copied
from `close-design-gate`. But the PR body lives at `/tmp/wf2-pr-body.md`
(`references/steps.md:1518-1524`) — *outside* the project root by design. Containment would have
rejected the only path the verb is ever handed: fail-closed on every real invocation, while
passing any test that used an in-root fixture. Containment governs **write** targets; this
command has none. **A test passes a `/tmp` body path specifically to prove the exception holds.**

**Input hardening — one open-once helper, used by BOTH verbs (Step-4 pass-1 F5, pass-2 F2/F4).**
Rev 3 hardened only `assert-pr-body` and claimed `release`'s fixed relative paths removed the
concern. Pass 2 showed that was false: `release` still accepts an arbitrary `--project-root` and
reads five paths beneath it, a symlinked `README.md` or manifest can resolve outside that root,
and the module's existing reads use unrestricted `read_text` (`skill_registration_check.py:87-96`).
Pass 2 also showed an `is_file()`-then-`read_text()` shape is racy — the path can be swapped
between the check and the open.

So **every** read in both verbs goes through one helper with an **open-once** contract:
`os.open(path, O_RDONLY | O_NOFOLLOW | O_NONBLOCK)` → `os.fstat` **that descriptor** and require
`stat.S_ISREG` → read at most `CAP + 1` bytes and reject on overflow → decode. Validating the
descriptor rather than the path closes the TOCTOU window; `O_NOFOLLOW` rejects a symlinked final
component; `O_NONBLOCK` means a FIFO cannot hang the open. `CAP` is a named module constant
(1 MiB — two orders of magnitude above the largest real input, `README.md` at ~200 KB).
`S_ISREG` and these flags are stdlib, not POSIX-specific APIs.

**Call site — and it must BLOCK (Step-4 A4).** In
`skills/implement-feature/references/steps.md`, between `read_review_state` (`:1516`) and
`gh pr create` (`:1518`). The prose states explicitly: *run the verb; on a nonzero exit surface
its output and STOP before `gh pr create`.* A4's point was that a call site without a stated
blocking condition is operationally inert — a runner could observe the failure and open the
malformed PR anyway. The drift guard asserts **both** the invocation and the blocking condition.
The end-of-run check stays as defence in depth.

**Not extended to WF3:** it has no deferred-verification concept at all. Adding the verb there
means first adding the feature.

### AC1 — where the skills call the verbs (Step-4 F3)

F3 (High) was right that rev 2 wired Ship A only into `epic-run`, leaving the "every PR" pain
untouched. Investigating it surfaced something sharper: **WF2 and WF3 never instructed the ×4
version bump at all** — Step 12's item 2a covers only "Update README + docs", and the bump lives
in `CLAUDE.md` and the out-of-repo `pr-preflight` skill. So for the release verb there is prose
to *add*, not merely replace:

| Skill | Change |
|---|---|
| `skills/implement-feature/references/steps.md` | Step 12 item 2a: run `release --base-ref origin/main` before pushing; STOP on nonzero. Plus the Ship B call site |
| `skills/fix-bug/references/steps.md` | its pre-PR equivalent: run `release --base-ref origin/main`; STOP on nonzero |
| `skills/epic-run/SKILL.md:67` | **keep** an explicit bump instruction, corrected ×3 → ×4, **and** add the verb as its verification |

**The explicit instruction is kept, not replaced (rev 4).** Rev 3 deleted epic-run's bump
sentence on the theory that the verb subsumed it; pass-2 self F1 correctly called that strictly
worse — a checker that could not detect a missing bump replacing the only prose that asked for
one. With `--base-ref` the verb now *can* detect it, but the instruction still earns its place:
the verb says whether a bump happened, the prose says to do it. AC1 is satisfied by the prose
*calling the verb*, which it now does at all three sites — not by deleting the mechanics.

## What does not ship, and why (AC3)

| # | Candidate | Verdict | Reason |
|---|---|---|---|
| 1 | Version-bump x4 + changelog | **SHIPPED AS A CHECKER, NOT A WRITER** | See Ship A. The `bump` half is declined with cause: three confirmed defects (input-derived write root; `atomic_write_text` not preserving the targets' 0664 mode; a recovery command that destroys pre-existing uncommitted work) against a benefit of four one-line edits |
| 2 | Pin-guard-allowlist derivation | **NOT SHIPPED** | The candidate's stated scope is a derived allowlist over ALL pin classes, and a partial one is worse than none — it manufactures false confidence exactly where the existing scanner's exclusions already hide drift (`phase_executor/` out of `SWEEP_GLOBS`, `docs/**` excluded, **337 canonical-sentence prose pins** unswept, not run in CI). Ship A covers the four VERSION surfaces and claims only that |
| 3 | Step-12 PR-body asserts | **SHIPPED** (deferred-section half only) | See Ship B. The `PENDING-*` half has **no subject** — `grep -rn PENDING skills/` returns nothing. Not invented |
| 4 | Merge-verification bundle | **NOT SHIPPED** | Half is already built (`launcher_lib.classify_issue_state:2102` returns `confirmed_merged`). The missing halves are a squash-SHA-on-main check (no seam) and the epic-checkbox tick — **the first issue-body write in the codebase** (`grep 'gh issue edit' hooks/*.py` → zero), needing concurrency handling and a precise one-way, never-un-tick contract. A first mutating GitHub write deserves its own design gate |
| 5 | ci-wait verb | **NOT SHIPPED** | Working prior art at the workspace tier (`~/rawgentic/.claude/skills/ci-wait/watch.sh`), built after ad-hoc loops died silently. Porting it needs auth, pagination, reruns, check-suite vs check-run terminal states, debounce, timeouts and PAT-limited semantics defined — and WF3's own prose (`skills/fix-bug/references/steps.md:528`) already rules out `gh pr checks` for PAT-limited repos |
| 6 | usage-capture-legs | **NOT SHIPPED** | A real seam exists (`step_state.read_history:363` — `session_registry.jsonl`'s 419 entries carry no issue key, so it cannot correlate), but leg identity, dedup and partial-total semantics are unspecified, and **`usage_capture` returns rc 0 when capture is unavailable** (`:322-328`), so exit status cannot gate a summation. Need arose once (#762) in this epic |
| 7 | D6 sweep table emitter | **NOT SHIPPED** | **No skill defines an executable input/output grammar.** The obligation IS named in docs — `docs/measurements/2026-08-01-owner-notes-executor-economics.md:253` and `:324-325`, and `docs/planning/2026-08-01-epic-756-front-loaded-path.md:180` — but no columns, row shape or output format exist anywhere. `driver_lib` also has no CLI and is documented pure/no-I/O |
| 8 | Marker-writer verb | **NOT SHIPPED** | A grammar migration disguised as a writer: 19 distinct WF2 marker forms, ~24 test-side literal pins, one structural parser with a tie rule (`step_state_post._MARKER_RE:70-73`), and a second unimplemented `DISPATCH` grammar. A generic writer could emit text that looks right but changes resume behaviour — to replace a one-line `cat >>` |

*(Rows 6 and 7 were corrected after Step-4 F7 caught two factual overstatements in rev 2: I had
written "usage_capture always exits 0" — `main` has a `return 2` fallback — and "zero D6 hits in
docs", having grepped only two files.)*

## File changes

| File | Change |
|---|---|
| `hooks/skill_registration_check.py` | `release` subcommand + shared `parse_newest_changelog_entry` |
| `hooks/plan_lib.py` | `assert-pr-body` subparser |
| `tests/hooks/test_skill_registration_check.py` | `release` subprocess CLI tests |
| `tests/hooks/test_plan_lib.py` | `assert-pr-body` subprocess tests incl. the `/tmp` read-only case |
| `tests/hooks/test_adversarial_review_registration.py` | changelog guard (shared parser); version pin bump |
| `skills/implement-feature/references/steps.md` | Step-12 `release` call + Ship B call site, both with STOP-on-nonzero |
| `skills/fix-bug/references/steps.md` | pre-PR `release` call with STOP-on-nonzero |
| `skills/epic-run/SKILL.md` | "version bump x3 surfaces" → call the verb |
| `tests/test_wf2_clarity.py`, `tests/test_wf3_clarity.py` | drift guards for the call sites + blocking condition |
| `README.md` | changelog entry |
| `.claude-plugin/plugin.json`, `plugins/rawgentic/.codex-plugin/plugin.json`, `phase_executor/src/phase_executor/canary.py` | version → 3.116.0 |

## Failure modes

- **A consistency-only checker passes an untouched tree** — the pass-2 defect. Mitigated by the
  `--base-ref` transition check; a test asserts an unchanged tree returns rc 1.
- **A bare semver scan would be fatal** — the test tree holds unrelated semver literals
  (`tests/test_workflow_diagram.py:50`). Mitigated by per-surface exact-one anchors; a test
  asserts `test_workflow_diagram.py` is never reported as a version surface.
- **An unreadable base ref must not read as "clean"** — `release` fails loud (rc 2) rather than
  degrading to a consistency-only pass, or it would silently reopen the pass-2 hole.
- **`check` and the hard test disagreeing.** Prevented structurally by the single shared parser,
  not by keeping two implementations in step.
- **A new hard guard could red an already-green tree.** Verified against HEAD first.
- **The `/tmp` read-only exception could be mistaken for a containment weakening.** Stated in the
  docstring and pinned by a test that passes a `/tmp` path.
- **A blocking call site that does not block** (A4) — the drift guard asserts the STOP condition,
  not just the invocation.
- **Adjacent prose defect, bonus, revertible alone:** `canary.py:36` and
  `tests/phase_executor/test_canary_digest_pin.py:3` say the digest is "re-pinned per release";
  it is not version-coupled, so that invites an action the code does not require. Two comment
  lines. Undo: revert those two lines.

## Security implications

Both verbs are **read-only**: the writer is gone, so there is no write target, no traversal-on-
write surface, no atomicity or file-mode concern, and no recovery contract to honour.

Rev 3's claim that fixed relative paths therefore removed *all* concern was **wrong**, and
pass 2 said so — `--project-root` is still user input, and a symlinked final component under it
can resolve anywhere. Rev 4's mitigation is the open-once helper described under Ship B, applied
to **every** read in both verbs: `O_NOFOLLOW` (no symlinked final component), `fstat` on the
descriptor (not the path, so no TOCTOU), `S_ISREG` only, `O_NONBLOCK` (a FIFO cannot hang the
open), and a 1 MiB cap. `assert-pr-body`'s two inputs are read-only and may legitimately live
outside the project root; `release`'s five are repo-relative but still read through the same
helper rather than trusted.

**One subprocess is constructed**, and it is the only one: `git show <base-ref>:<path>`, run as a
fixed argv list with no shell. `<base-ref>` is operator-supplied, so it is validated against
`^[A-Za-z0-9._/-]+$` and passed after `--` so a ref beginning with `-` cannot be read as a flag.
This is a deliberate change from rev 3, which claimed "no subprocess construction"; that claim
no longer holds and is corrected here rather than left standing.

No network, no deserialization of untrusted data, no regex over untrusted input (the swept
corpus is the repo's own tracked files), no secrets read or written.

## Platform / external dependencies

platform_apis:
- api: git show <ref>:<path> via subprocess.run with a fixed argv list, on the local git CLI
  feasibility: verified via existing-call-site — hooks/plan_lib.py:1745 and :1805 run _git_run(repo, ["show", ...])
  failure: fail-loud

The `fail-loud` classification is exact: a non-zero `git` exit or an unresolvable ref is reported
as rc 2 with stderr surfaced. The verb never silently treats an unreadable base as "no bump
needed" — that would reopen precisely the pass-2 defect it exists to close.

Everything else uses only the Python standard library (`re`, `json`, `os`, `stat`, `pathlib`,
`argparse`) and existing in-repo call sites (`skill_registration_check.Finding`/`run_checks`,
`plan_lib.parse_tasks:201`, `plan_lib.assert_pr_body_has_deferred_section:345`).

AC2's subprocess-driven CLI testing is **precedented in this exact CI job**, not assumed:
21 test files already drive CLIs via `subprocess.run([sys.executable, ...])` — exact-shape
precedent at `tests/hooks/test_context_meter.py:72` — and CI runs `pytest tests/ -v` over all of
them (`.github/workflows/ci.yml:50`). This answers pass-1 A6.

Pass-2 adv #5 asked for an explicit POSIX declaration covering `/tmp` and FIFO semantics. The
`/tmp` path is not introduced by this design — it is fixed by WF2's existing prose
(`references/steps.md:1518-1524`) — and the FIFO concern is handled with `stat.S_ISREG` and
`O_NONBLOCK`, which are stdlib and available on every platform this repo's CI runs
(`ubuntu-latest`). No POSIX-only API is called, so no further declaration is owed.

## Verification

- **AC1** — drift guards asserting each of the three call sites names its verb AND states the
  STOP-on-nonzero condition, each anchored to one canonical sentence in one file.
- **AC2** — both verbs driven via `subprocess.run([sys.executable, CLI, ...])`:
  - `release`: clean tree → rc 0; one surface disagreeing → rc 1 naming it; **version not
    advanced vs base → rc 1** (the pass-2 regression); newest changelog entry missing either tail
    → rc 1; each malformed-tail fixture (`no diagram REVISION`, `diagram REV 1.2.3.4`,
    `Suite 10->20junk`, duplicated tokens) → rc 1; unresolvable `--base-ref` → rc 2; a ref failing
    the charset guard → rc 2; `tests/test_workflow_diagram.py`'s stray semver literals never
    reported as a version surface.
  - `assert-pr-body`: no deferrals + valid plan → rc 0; deferrals + section present → rc 0;
    deferrals + section absent → rc 1; **zero-task plan → rc 2** (not a vacuous pass); missing
    file → rc 2; **a `/tmp` body path → rc 0**, proving the read-only exception.
  - shared read helper: symlinked target → rejected; FIFO → rejected without hanging; oversize
    file → rejected.
- **AC3** — the table above, reproduced in the PR body.

## Known limitation, stated not hidden

Pass-2 adv #2 observed that Ship B's gate is still prose a model must choose to run: the drift
guard proves the skill *says* to run the verb and to stop on failure, not that either happened.
Its proposed fix — a wrapper that runs the assertion and then invokes `gh pr create` itself —
would put a mutating GitHub call into `hooks/`, which is exactly the boundary candidate 4 is
declined for. This repo has already met and accepted this limit: the v3.114.0 changelog records
it verbatim for the #798 design-gate adapter — *"a prose guard can still pass while a model skips
the adapter command."* Recorded here as the same accepted limitation, not claimed as covered.
