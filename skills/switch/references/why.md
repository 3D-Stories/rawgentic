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
