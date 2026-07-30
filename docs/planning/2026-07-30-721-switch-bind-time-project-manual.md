# #721 — `switch` loads the bound project's `CLAUDE.md` at bind time

**Issue:** [#721](https://github.com/3D-Stories/rawgentic/issues/721) · epic
[#722](https://github.com/3D-Stories/rawgentic/issues/722) child 1 of 3
**Date:** 2026-07-30 · **Complexity:** `standard_feature` (Step 2, authoritative)
**Revision:** v5 — stripped back to the plain mechanism on owner instruction (D11). v1–v4 are
preserved under `claude_docs/md-backups/epic-722/`.

---

## 1. The problem

`/rawgentic:switch <p>` binds a session to a project but does not put that project's `CLAUDE.md`
into context. Rules that gate a *decision* — may this project's sessions auto-merge, which CI lanes
are enforced, what the protection level is — are absent exactly when they are needed.

It is a **timing** gap, not an absence. The harness auto-loads a `CLAUDE.md` by directory walk at
session start, **plus lazily on the first use of a harness file tool (`Read`/`Edit`) inside a
subtree**. Shell file access does not count — the harness cannot see inside a bash one-liner. And
`switch` reads all its config through shell one-liners (deliberately, so a `Bash(printf:*)`-style
allow-rule can auto-approve the bind without a permission prompt). So binding has never loaded the
project tier.

### Evidence (CONFIRMED)

| # | Observation | Where |
|---|---|---|
| 1 | Bind to `sysop` (all shell) → `projects/sysop/CLAUDE.md` absent from `/context` | issue #721, 2026-07-29 |
| 2 | **`Read` of `projects/sysop/.rawgentic.json` → `projects/sysop/CLAUDE.md` injected in full** | issue #721, 2026-07-29 |
| 3 | Reproduced independently: a `Read` of `tests/test_bind_command_expansion_free.py` injected all 26,569 chars of `projects/rawgentic/CLAUDE.md`, while the same session's earlier all-shell bind had not | this run, 2026-07-30 |
| 4 | A later `Read` of `hooks/wal-bind-guard` did **not** re-inject. Narrow observation; "fires once per session" is the natural reading but is **INFERRED** | this run, 2026-07-30 |
| 5 | A post-bind `Read` of the bound project's own file is allowed by `wal-bind-guard` | this run, 2026-07-30 (probe) |

**Row 2 is the exact invocation this design ships.**

### The one hard constraint

`hooks/hooks.json:40` registers `hooks/wal-bind-guard` on PreToolUse for
`Edit|Write|MultiEdit|NotebookEdit|Read`. While the session is **unbound** and >1 project is active
(24 are), Gate 1 (`wal-bind-guard:100-133`) **denies** a `Read` of any file under an active project.
Once bound, Gate 2 (`:159-170`) allows files under the bound project.

⇒ the pre-bind config reads stay shell one-liners, and the load goes **after** the registry append,
which is what performs the bind.

---

## 2. The mechanism

**After the fail-closed Headless Access Check and before "Ready", use the `Read` tool on
`<project path>/.rawgentic.json`.**

That one tool call is a file-tool access inside the bound project, which is the harness's lazy-load
trigger, so the project's `CLAUDE.md` is injected in full as a side effect. The injected copy is the
one that matters — it carries memory-file authority, where a `Read` result is merely data the model
has seen. The skill already reads this same file via shell for the universal-field check; **the
second read is deliberate, and is the mechanism.**

### Why this and not a direct `Read` of `CLAUDE.md`

Only because of the 14 of 24 active projects that have no `CLAUDE.md`. Reading a file that does not
exist produces a visible error on every one of those binds. Reading `.rawgentic.json` — which every
configured project has — needs **no existence check at all**: if the project has no manual, the
harness injects nothing and `switch` says nothing. AC2 is satisfied structurally, by doing less.

### No shell command, therefore no shell risks

This step issues **no shell command**. It is a `Read` tool call with a path parameter. There is
consequently no command injection, no word-splitting, no glob, no metacharacter handling, and no
new allowlisted binary.

**This is the whole lesson of v1–v4, recorded so it is not repeated.** Earlier revisions added a
shell `test -f` guard purely to avoid the cosmetic "file not found" above. That guard — not the
feature — produced every security finding across four review passes: command injection through a
project path containing `$(...)` (verified live: `"a$(id -u)b"` → `a1000b`), a symlink escape, and a
check-then-use window between the guard and the read. Each "fix" was more shell, so each fix grew
the surface. Removing the guard removes all of it. **The plain version is strictly safer than the
elaborate one.**

### The residual risk, stated plainly

If a project's `CLAUDE.md` were a symlink pointing outside the project, the harness would inject
whatever it points at. That is **unchanged by this PR**: the harness already does exactly that
today, unprompted, the first time any session opens any file in that project. This change alters
*when* the injection happens, never *whether* or *what*. `wal-bind-guard`'s own-project allowance is
a lexical prefix check (`:159-170`; the realpath hardening at `:178-205` covers only the
cross-project allowlist branch) — a real gap, pre-existing, filed as a follow-up in §7, and
correctly fixed in the guard rather than in every caller.

### Placement, and why it is after the headless check

`switch`'s **fail-closed** Headless Access Check is at `skills/switch/SKILL.md:197`. Loading a
project's own prose *before* the check that decides whether this session may operate on that project
headlessly would let project-controlled text influence its own authorization. So the load goes after
it, immediately before "Confirm Ready".

Everything that runs before the load is either a workspace-level default (`defaultProtectionLevel` —
a *workspace* setting, so a project manual is not its input) or advisory mechanics with no policy
effect (the universal-field check, the staleness nudge).

**Known boundary:** `switch` skips Step 5b wholesale when `configured` is `false`, and an
unconfigured project has no `.rawgentic.json` to read anyway (`skills/switch/SKILL.md:119-121`) — so
those binds get no load and are told to run `/rawgentic:setup`. Zero of the 24 currently active
projects are unconfigured (checked 2026-07-30). Stated, not hidden.

---

## 3. File changes

### 3.1 `skills/switch/SKILL.md` — one new item

Inserted between the Headless Access Check (item 3) and Confirm Ready (item 4):

```md
### 3b. Load the project's operating rules

**Use the `Read` tool on `<project path>/.rawgentic.json`. Never Bash (`cat`/`head`/`jq`).**

That one call is the whole mechanism: the harness's `CLAUDE.md` auto-load fires on its own file
tools and cannot see inside a bash one-liner, so this is what puts the project's rules in context
with memory-file authority. Item 2 already read this file via shell — the second read is
deliberate, not duplication. Do not collapse them, and do not move this step: before the registry
append `wal-bind-guard` Gate 1 denies it, and before the headless verdict it would let project
prose influence a fail-closed authorization check.

Projects with no `CLAUDE.md` need no handling — the harness injects nothing, so say nothing.
Never announce a missing manual.

If the `Read` fails, do not report Ready. Say: bound, but the project's rules did not load.
```

~830 chars, so sibling #720 keeps room under its 3,000-char cap for the whole file.

### 3.2 `tests/test_switch_loads_project_manual.py` — new drift guard

Reads `skills/switch/SKILL.md` **directly** (a *location* pin, not the corpus — repo manual §1),
which is what stops #720 sweeping the step into `references/why.md`. Section isolated by
header-index slicing, whitespace-normalised (`tests/test_wf2_clarity.py:440-454` pattern).

One canonical operative sentence carries the contract:

> Use the `Read` tool on `<project path>/.rawgentic.json`. Never Bash (`cat`/`head`/`jq`).

1. `test_canonical_load_sentence_present` — that sentence, whitespace-normalised, appears exactly
   once in the isolated section. Carries tool class, target file and the Bash prohibition together,
   so scattered prose cannot satisfy it by accident.
2. `test_load_is_after_the_registry_append` — the append is the platform gate that makes the `Read`
   permissible (Gate 1); without this anchor, moving the append down leaves the suite green while
   the read is denied.
3. `test_load_is_after_the_headless_verdict` — the security ordering.
4. `test_load_is_before_confirm_ready`.
5. `test_no_manual_case_is_silent` — AC2's mandatory silence, the line #720 is most likely to trim
   as "obvious".
6. `test_failed_read_does_not_report_ready`.
7. `test_anchors_are_non_vacuous` — all three ordering anchors are found, so 2–4 cannot pass by
   matching nothing.

There is deliberately **no** test asserting the injection occurred — see §4.

### 3.3 Version ×4, README, diagram, artifacts

`feat` ⇒ **minor**: 3.108.0 → **3.109.0** across `.claude-plugin/plugin.json`,
`plugins/rawgentic/.codex-plugin/plugin.json`,
`tests/hooks/test_adversarial_review_registration.py`'s pinned assert, and
`phase_executor/src/phase_executor/canary.py` `EXPECTED_PLUGIN_VERSION`.

README `## Changelog` entry in the exact repo-manual §2 shape, with both mandatory tail tokens.

**Diagram decision: no REV.** `switch` is not a WFn spine, has no station in
`docs/workflow-diagram.html`, and this adds no step, gate or loop-back to WF1/2/3/5.

This doc ships as an md+html pair (`hooks/render_artifact.py`), with the peer-consult and
adversarial-review reports under `docs/reviews/`.

---

## 4. Platform / external dependencies

platform_apis:
- api: Claude Code harness lazy `CLAUDE.md` injection, triggered by a `Read` inside the project
  feasibility: verified via spike — the EXACT shipped invocation: issue #721 records a `Read` of `projects/sysop/.rawgentic.json` as the first project access injecting `projects/sysop/CLAUDE.md` in full (2026-07-29); independently reproduced 2026-07-30 with a descendant-file `Read` injecting all 26,569 chars of `projects/rawgentic/CLAUDE.md`
  failure: fail-silent
  surface: verification DEFERRED TO TARGET (#138) — local proxy: the drift guard pins the canonical operative sentence and its position, and the `Read` tool result proves the trigger fired; target check: the owner-attended `/context` bind check in §6, the only place the injection itself is observable
- api: `hooks/wal-bind-guard` PreToolUse gating of the `Read` tool
  feasibility: verified via existing-call-site — `hooks/wal-bind-guard:100-133` Gate 1 unbound-deny and `:159-170` Gate 2 bound-project-allow, read at source and probed live post-bind this run
  failure: fail-silent
  surface: the guard is fail-OPEN by its own contract (`hooks/wal-bind-guard:7` — missing `jq` or any error allows the operation), so a guard malfunction permits the read rather than blocking it; this step reads a file INSIDE the bound project, which both a working and a failed-open guard allow, so a guard failure cannot change its outcome

**On the deferral, honestly.** Earlier revisions claimed no pytest could verify the injection. That
was **false**: `claude --help` lists `--plugin-dir <path>`, and this repo already has `RUN_LIVE`-gated
live-CLI tests (`tests/hooks/test_bakeoff_policy.py`, `tests/phase_executor/test_canary_dispatch.py`,
…). The truthful statement is *possible, live-only, never in CI, and not built here* — a live
behavioural test with real flake risk, for a dependency whose failure mode is "behaves exactly as it
does today". Filed as a follow-up (§7), and recorded as deferred-to-target rather than dressed up as
untestable.

---

## 5. Failure modes

| Failure | Behaviour |
|---|---|
| Project has no `CLAUDE.md` (14 of 24) | Nothing happens, nothing is said. Structural — the step never looks for the file |
| Project is unconfigured | No load; Step 5b is already skipped and the user is told to run `/rawgentic:setup`. Stated boundary (§2) |
| The `Read` fails | Do not report Ready; say "bound, but the project's rules did not load" |
| Harness stops auto-injecting | Nothing loads and nothing errors — i.e. today's behaviour. Declared `fail-silent`; §6 is the only way to notice |
| Step moved before the registry append | Gate 1 denies the `Read`; guard 2 fails in CI first |
| Step moved before the headless verdict | Project prose could influence a fail-closed authorization; guard 3 fails |
| `Read` swapped for `cat` in #720's trim | Feature silently dies — shell reads do not trigger the auto-load; guard 1 fails |
| The second `.rawgentic.json` read removed as "duplication" | Feature silently dies; guard 1 fails |
| **Rebinding** (`switch a` → `switch b`) | Both manuals stay in context; the harness cannot unload the first. **Not addressed here** — pre-existing (it already happens whenever a session opens a file in A then rebinds), and the owner's ruling was warn-not-refuse (D6). Follow-up §7 |

## 6. AC3 — measured cost, and the check only the owner can run

Cost of the bind-time load = the size of that project's `CLAUDE.md` (CONFIRMED, `wc -c`, 2026-07-30):
chorestory 37,250 (~9.3k tok) · rawgentic 26,569 (~6.6k) · rawgentic-next 24,029 (~6.0k) ·
saystory 17,948 (~4.5k) · sysop 4,780 (~1.2k) · herdr-dashboard 1,141 (~0.3k). Plus the trigger read
itself: `.rawgentic.json` is ~1–3 KB.

**The live check (after merge, plugin reinstall, and a fresh session — the repo is not the running
plugin):** bind `sysop`, run `/context`, confirm `projects/sysop/CLAUDE.md` is now listed; repeat for
`chorestory` and note the delta; bind a project with no manual and confirm silence. This is the
target check for §4's deferred dependency.

## 7. Follow-ups (not solved here)

1. **Per-project `CLAUDE.md` cleanup** — 10 files, several with stale/cross-project content
   (`projects/sysop/CLAUDE.md` carries chorestory's Playwright settings and an `ssh root@…` path).
   One issue per project, each in its own session (owner, D4/D7). **Blocking prerequisite for
   closing epic #722.**
2. **Rebind accumulates manuals.** Warned, not prevented (D6). A real fix needs a harness unload or
   an explicit precedence rule.
3. **`RUN_LIVE` injection canary** — `claude --plugin-dir` against a fixture project whose manual
   carries a unique token. Possible, live-only, real flake risk.
4. **`wal-bind-guard` own-project symlink containment** — Gate 2 is a lexical prefix check; the
   realpath hardening covers only the cross-project branch. Pre-existing; belongs in the guard, not
   in callers.
5. **Headless verdict is skipped for unconfigured projects** — `skills/switch/SKILL.md:197` sits
   inside the `configured`-only Step 5b, so a headless bind to an unconfigured project never reaches
   the fail-closed check. Found during this design; pre-existing; out of scope here.
6. **Model context is not enforcement.** Auto-merge, protection level and CI gating are policy;
   loading them improves the odds a session honours them, it does not bind it.

## 8. Acceptance criteria (post-rescope, owner 2026-07-30)

| AC | Satisfied by |
|---|---|
| AC1 bind-time load | §3.1. **Honest restatement:** "before any file in the project is touched" is unsatisfiable by any design — the load is *caused by* the first project file access. What ships: the bind-owned `Read` **is** that first access, and the rules are in context before "Ready". Boundary: unconfigured projects (§2) |
| AC2 silent for the 14 | Structural — no branch, no message, no check |
| AC3 cost as file size | §6, reported in the PR body |
| AC4 / AC5 / AC6 | cancelled / moved to #719 / split to per-project issues |
| AC7 guarantees unbroken | `$CLAUDE_CODE_SESSION_ID` untouched; the expansion-free append untouched (this step issues no shell command at all); the fail-closed headless verdict untouched and now strictly precedes the load; the fail-open staleness nudge untouched |

## 9. Multi-PR

No. Single PR, well under 500 lines.
