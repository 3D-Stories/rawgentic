# Why each `switch` rule exists — and what breaks without it

`SKILL.md` is the executable procedure. This file is the reasoning behind it, moved out by
#720 so the operative steps are not buried in prose. **Nothing here is optional reading
before changing a step** — every rule below was written because something broke.

## Why binding does not deactivate other projects

Multiple sessions run concurrently in this workspace, each bound to a different project.
Deactivating others on every bind would unbind live sessions' projects out from under them.
Activation is per-project state, not a global "current project" pointer.

## Step 5 — the session id

### Why `$CLAUDE_CODE_SESSION_ID` and never `claude_docs/.current_session_id`

`.current_session_id` is a **single shared file overwritten by every session on every
prompt**. Under concurrent sessions it can return *another* session's id, which binds the
wrong session to the project — the failure is silent and the registry looks correct
afterwards. `$CLAUDE_CODE_SESSION_ID` is set per Claude Code process, so it is unique and
correct no matter how many sessions run at once.

This is workspace mistake #8. If `printenv` prints nothing you are on a Claude Code old
enough not to set the variable: **stop and ask**, never guess and never invent an id — a
fabricated id produces a registry line that resolves to no real session.

The legacy name `$CLAUDE_SESSION_ID` is **not** set. The correct variable has the `CODE_`
infix.

### Why the append must be expansion-free, in two calls

A Bash command containing `$(...)` or backticks is flagged **"Contains expansion"** by Claude
Code's permission system and **always prompts** — no `permissions.allow` rule can suppress
it. Keeping the append to a leading allowlistable binary (`printf`), literal values, and only
`>>` redirection lets a user's `Bash(printf:*)` / `Bash(date:*)` / `Bash(printenv:*)` rules
auto-approve the bind, so `/rawgentic:switch` stops prompting every time.

That is why it is **two** calls: call 1 reads the id and timestamp, call 2 inlines them as
literals. Doing it in one call would need command substitution and reintroduce the prompt.
Because the env var is per-process, splitting across two calls is still race-free — there is
no shared state between them to corrupt.

Guarded by `tests/test_bind_command_expansion_free.py`, which scans fenced blocks containing
`session_registry.jsonl` in the skill **corpus** (`SKILL.md` + `references/*.md`), so this
prose move keeps the guard live.

### Why the append target must be absolute, and why `<root>` is defined in the step

The failure mode this prevents is a **silent wrong-file write, not an error** (#885, observed
live 2026-08-04 by a sibling session on 3.119.1).

The step reads `.rawgentic_workspace.json` from the primary working directory, so it is tempting
to treat cwd as the workspace root for the write too. Nothing enforces that at the write, and
the Bash tool's cwd **persists across calls within a session** — it drifts with whatever work
happened in between. A session whose cwd had been left at `projects/sysop` ran the bind and
`>>` **created** `projects/sysop/claude_docs/session_registry.jsonl`, wrote the line into it,
and exited 0; `tail -1` echoed the line straight back. Only an adjacent *relative read* failing
loudly (`.rawgentic_workspace.json` → No such file or directory) exposed it.

**The precondition, because it explains why this survived and must not be mistaken for
"unreachable" (measured 2026-08-04).** `>>` creates a missing *file* but never a missing *parent
directory*, so the relative append only misfiles silently when the drifted cwd **already has a
`claude_docs/` directory**; anywhere else it fails loudly with `No such file or directory`. That
is not reassuring — in the real workspace **18 of 49 project directories have one**, including the
`projects/sysop` the live report drifted into. So a reader who probes the old form in a tree
without `claude_docs/`, sees it error, and concludes the fix is unnecessary has tested the wrong
half of the defect.

Three reasons it outranks an ordinary path bug:

1. `>>` **creates** the file, and the step explicitly says to create the registry if absent — so
   a wrong-tree creation is indistinguishable from the documented first-bind case.
2. **Reads fail loudly; this write did not.** Every relative read errors visibly. The one
   relative write just landed elsewhere and reported success.
3. **Binding is the input to everything downstream** — WFn resume, wal-guard, driver state. A
   registry line in the wrong tree makes the bind invisible to anything grepping the real
   registry, while the session believes it is bound.

The fix reuses a value the command **already carried**: both bind templates pass the absolute
workspace root as a printf argument for the JSON `cwd` field, so the redirect target interpolates
that same literal.

Be precise about how much that buys, because it is easy to overstate. A **nonexistent** root, or
one with no `claude_docs/` directory, now fails **loudly** — `>>` cannot create a missing parent.
But an **existing yet wrong** absolute root that happens to contain `claude_docs/` still misfiles
**silently**, and the wrong root recorded in that misplaced line is *not* independent detection: it
is the same wrong value written twice. So the absolute target removes the *cwd-drift* failure mode
and nothing more. Correctness still depends on substituting the resolved directory that holds
`.rawgentic_workspace.json` — which is exactly why Step 5 **defines** `<root>` rather than hinting
at it.

Rejected alternatives, so a rewrite does not reintroduce any of them:

- **`cd <root> && printf …`** — the auto-mode permission classifier blocks cd-prefixed compound
  commands, so a cd-based bind becomes a permission prompt on *every* bind.
- **A prose reminder** ("make sure you are in the workspace root") — that is precisely the
  assumption that already failed silently.
- **A new root-resolving CLI call.** The repo already carries seven walk-up implementations
  (`context_meter.find_workspace`, `session_index.resolve_workspace_root`,
  `session_mining_lib.resolve_workspace_root`, `step_state_post._find_workspace_root`,
  `security-guard.find_workspace_root`, …) and none is exposed as a command. Adding an eighth,
  and a subprocess, to a step whose entire constraint is staying expansion-free would cost more
  than reusing the literal already in hand.

**Already-misfiled records are NOT migrated** — the fix is prospective only. If you find a stray
`claude_docs/session_registry.jsonl` under a project directory, the sessions recorded in it were
never visible to anything reading the workspace registry; the remedy is to re-run the bind for any
session that still matters and delete the stray file. There is deliberately no automatic migration:
moving session records between trees on upgrade is a riskier operation than re-binding, and the one
observed occurrence was remediated by hand at report time.

**`<root>` is therefore DEFINED in Step 5 itself**, not left implicit: an undefined placeholder
is worse than a reminder, because a reader who substitutes the cwd produces
`>> "./claude_docs/session_registry.jsonl"` — byte-for-byte as broken as the original while
looking fixed. Guarded by `test_bind_append_target_is_absolute`, which pins that neither bind
skill carries a bare `>> claude_docs/` append. Note its limit honestly: the guard pins the
template's **shape**: no mechanical check can prove the value substituted at runtime is
absolute, which is why the definition is operative prose in the step.

## Step 5b item 2 — why the universal-field check uses Bash and MUST NOT use `Read`

Item 2 reads the same `.rawgentic.json` that item 3b reads. If it used the `Read` tool, the
harness's `CLAUDE.md` auto-load would fire **at item 2** — before the registry append is
verified and before the bind is reported — letting a project's own prose reach the session
ahead of the deliberate, positioned load at item 3b (and making the two reads collapse into
one, which loses the position guarantees below).

Every other guard in `tests/test_switch_loads_project_manual.py` would still pass in that
unsafe ordering, which is exactly why that file pins the Bash mandate separately. It was
found by a Step 11 review of #721, not by the original design.

### Why the universal-field list is deliberately short

Only `version`, `project`, `repo`, `protectionLevel`, `custom` — fields every project has
regardless of type. Optional sections (`testing`, `database`, `services`, `infrastructure`,
`deploy`, `security`, `ci`, `formatting`, `documentation`) are **not** checked because
projects legitimately omit them, and warning about a legitimately absent section trains the
reader to ignore the advisory. Presence only, never values or nested structure: this is a
staleness check, not a validator.

## Step 5b item 1 — the protection levels

- **`sandbox`** — no guards active. Good for POC / playground projects.
- **`standard`** — blocks destroy + mutate ops on production, plus 6 common security
  patterns.
- **`strict`** — all guards active. Full production projects.

The prompt runs **once**: subsequent binds see the field and skip it.

## Step 5b item 2b — why the staleness nudge is advisory and fail-open

The universal-field check only catches a *malformed or old-shape* config. It does not catch a
project that predates newer setup-requiring features (adversarial review, peer consult,
design artifact) — that project's config is valid, just behind.

`hooks/post_update_reconcile.py`'s SessionStart pass nudges once per plugin version, but an
explicit switch is the moment to surface *that project's own* gap, so this pass has **no
once-per-version gate**. It respects the workspace-level `"setupPrompt": false` opt-out, and
it is **fail-open**: a non-zero exit or empty output means "nothing to nudge", never a
blocked bind. A staleness advisory that can block a bind is worse than no advisory (#234).

## Step 5b item 3b — the bind-time load, which is the whole point of #721

### Why the tool class is load-bearing

The harness auto-loads a `CLAUDE.md` **lazily, on the first use of one of its OWN file tools**
(`Read`/`Edit`) inside a subtree. Shell access does not count: the harness cannot see inside a
bash one-liner. `switch` reads all its config through shell one-liners — deliberately, so the
bind can be allowlisted — so **binding never triggered the load**. A project's manual arrived
whenever a session happened to open a file in that repo, and never at bind.

So swapping the `Read` for `cat`/`head`/`jq` loads **nothing** while reading perfectly
correctly. The bug is invisible at the call site, which is why the canonical sentence pins the
tool class, the target and the Bash prohibition together.

Reading the **config** rather than the manual is what makes projects with no `CLAUDE.md` bind
silently: no existence check, no branch, no message. Doing less is what satisfies that
requirement.

### Why the position is load-bearing, in both directions

- **After the registry append.** The append *is* the bind. Before it the session is unbound
  and `hooks/wal-bind-guard` Gate 1 **denies** a `Read` of any active project's files.
- **Before Confirm Ready.** "Ready" must not be reported while the rules are absent.

### Why item 2's shell read is not duplication

Item 2 reads the file via Bash for field presence; item 3b reads it via the `Read` tool for
the side effect of the injection. Collapsing them either loses the injection (if Bash wins) or
moves the load ahead of the verified bind (if `Read` wins). Both reads are deliberate.

### Why a missing manual is silent

14 of 24 active projects have no `CLAUDE.md`. A warning on every one of those binds is noise
that trains the reader to ignore the skill's output. **Never announce a missing manual.**

### Why a failed read must not report Ready

The registry append has already succeeded, so the session really *is* bound — but its rules
are absent. Saying "Ready" would hide exactly the condition the step exists to guarantee.

## What model context does NOT give you

Loading a project's rules improves the odds a session honours them. It does not **bind** the
session to them: auto-merge permission, protection level and CI gating are policy expressed
as prose, not enforcement. Anything that must be guaranteed belongs in machine-readable config
behind a fail-closed hook. Carried from #721 as a standing caveat.

## Rebinding accumulates manuals

`switch a` then `switch b` leaves **both** projects' rules in context — the harness has no
unload. Owner decision D6 (epic #722): warn and proceed, recommending a fresh session, rather
than refusing the rebind. The hazard predates bind-time loading; #721 raised its frequency,
not its severity. A real fix needs a harness unload primitive or an explicit precedence rule.

## Guards that will catch a bad edit here

- `tests/test_switch_loads_project_manual.py` — a **LOCATION** pin: reads `SKILL.md`
  **directly**, not the corpus, precisely so that moving the operative load step into this
  file as rationale fails the guard. If you are reading this because that test went red, the
  fix is to put the step back in `SKILL.md`, not to relax the test.
- `tests/test_bind_command_expansion_free.py` — corpus-wide; no `$(...)` or backticks in the
  registry-append block.
- `tests/hooks/test_session_binding.py` — `SKILL.md` must name `$CLAUDE_CODE_SESSION_ID` and
  explain the concurrency reason.
- `tests/hooks/test_post_update_reconcile.py` — `SKILL.md` must reference
  `--staleness-project`.
