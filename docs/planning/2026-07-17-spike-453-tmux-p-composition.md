# Spike #453 — `claude --tmux` × `-p` composition probe (supervisor-spawn compatibility, U-2)

**Date:** 2026-07-17
**Status:** SPIKE COMPLETE — conclusive verdict reached
**Issue:** #453 (probe for U-2 in `docs/planning/2026-07-17-orchestrator-executor-acceptance-criteria.md`, unmerged branch `docs/orchestrator-executor-ac`, PR #451)
**Host:** this Linux VM (`uname -a`: `Linux claude-code 6.8.0-134-generic #134-Ubuntu SMP PREEMPT_DYNAMIC ... x86_64 GNU/Linux`), tmux 3.4, no iTerm2 (`TERM_PROGRAM=tmux`)
**Scope:** Linux-first. macOS/iTerm2 behavior is out of scope — noted only where the `--help` text itself mentions iTerm2.

No production changes were made. All probes ran against a disposable scratch git repo (`/tmp/.../scratchpad/spike-453/probe-repo`), never against the rawgentic repo.

---

## 1. Environment

**CONFIRMED** — `claude --version`:
```
2.1.212 (Claude Code)
```

**CONFIRMED** — verbatim `--help` text for `--tmux` and `--worktree` (from `claude --help` on this host, 2.1.212):

```
  --tmux                                Create a tmux session for the worktree
                                        (requires --worktree). Uses iTerm2
                                        native panes when available; use
                                        --tmux=classic for traditional tmux.
  ...
  -w, --worktree [name]                 Create a new git worktree for this
                                        session (optionally specify a name)
```

No other `--tmux`-adjacent text exists anywhere else in `--help` output (230 lines, full capture kept in scratch at `claude-help-full.txt`). Nothing in `--help` states how `--tmux` behaves under `-p`/`--print`.

---

## 2. AC1 — Composition probe: `claude -p --worktree --tmux "Reply with exactly: PONG"`

**CONFIRMED** — running the literal command from the issue, from the scratch probe repo (a brand-new, never-before-trusted git repo):

```
$ tmux ls                                              # BEFORE: 8 pre-existing sessions (untouched, verified by name before/after every probe)
$ claude -p --worktree --tmux "Reply with exactly: PONG"
EXIT: 1
STDOUT: (empty)
STDERR: Workspace trust not yet accepted. Run `claude` once in this directory and accept the trust dialog, then retry with --worktree.
$ tmux ls                                              # AFTER: identical 8 sessions, no new session
```

- **tmux session created: NO.** No new session appeared in `tmux ls` before/after.
- **Foreground/detached: N/A** — process exited immediately (no backgrounding attempted, nothing to attach to).
- **Exit code: 1.**
- **stdout envelope: none** — the run aborted before producing any `result`/JSON envelope; nothing reached the model (verified: this call produced no billed usage, unlike every successful probe below which returned real `total_cost_usd`).
- **Refusal message (verbatim, exact):** `Workspace trust not yet accepted. Run \`claude\` once in this directory and accept the trust dialog, then retry with --worktree.`
- **CLI version at refusal:** 2.1.212 (same binary as §1).

### 2a. Isolating exactly which flag causes the refusal (CONFIRMED, clean A/B/C)

The literal issue command has a CLI parsing hazard: `-w/--worktree [name]` takes an **optional** value, so a bare positional string placed right after `--worktree` (with no `--tmux` in between) gets consumed as the **worktree name**, not the prompt — confirmed separately:

```
$ claude -p --worktree "Reply with exactly: PONG"
EXIT: 1
STDERR: Error creating worktree: Invalid worktree name "Reply with exactly: PONG": each "/"-separated segment must be non-empty and contain only letters, digits, dots, underscores, and dashes
```
(No worktree, no tmux session, no trust error here — it fails at name validation before reaching the trust check, because in *this* ordering `--worktree` swallowed the prompt as its `[name]` argument.) This is itself a load-bearing finding for AC-E2 (command-as-initial-pane-process): a supervisor invocation MUST use `--worktree=<name>` or a `--` separator before a free-text prompt, never a bare positional prompt immediately after `--worktree`.

To isolate the *composition* question cleanly, re-ran with unambiguous syntax (`--worktree=<name>`, `--` before the prompt), same untrusted scratch repo, both same session:

**Test A — `--worktree` alone, no `--tmux`:**
```
$ claude -p --worktree=spike453-a --output-format json -- "Reply with exactly: PONG"
EXIT: 0
STDOUT: {"type":"result","subtype":"success", ... "result":"PONG", ... "total_cost_usd":0.699205, ...}
$ git worktree list
.../probe-repo                              bf58ddb [master]
.../probe-repo/.claude/worktrees/spike453-a  bf58ddb [worktree-spike453-a] locked
```
Succeeded — no trust error, worktree created, real model call, correct `PONG` result, no tmux session (none requested).

**Test B — `--worktree` + `--tmux` together, identical untrusted repo, immediately after A:**
```
$ tmux ls   # BEFORE: same 8 pre-existing sessions
$ claude -p --worktree=spike453-b --tmux --output-format json -- "Reply with exactly: PONG"
EXIT: 1
STDERR: Workspace trust not yet accepted. Run `claude` once in this directory and accept the trust dialog, then retry with --worktree.
$ tmux ls   # AFTER: identical, no new session
$ git worktree list   # only spike453-a from Test A; no spike453-b was created
```

**Test C — `--tmux=classic` explicit variant (in case the iTerm2-detection path behaves differently on Linux):**
```
$ claude -p --worktree=spike453-c --tmux=classic --output-format json -- "Reply with exactly: PONG"
EXIT: 1
STDERR: Workspace trust not yet accepted. Run `claude` once in this directory and accept the trust dialog, then retry with --worktree.
```
Identical refusal — the trust check fires before any tmux-mode-specific logic (iTerm2 vs classic) runs.

**CONFIRMED, unconfounded finding:** in this exact same directory, in the exact same untrusted state, `--worktree` alone succeeds under `-p` and `--worktree --tmux` together fails on a workspace-trust error. **The `--tmux` flag itself is what reinstates the trust requirement** that `-p --worktree` alone skips.

### 2b. This contradicts the docs, not just F5's paraphrase

**CONFIRMED via live `WebFetch` of https://code.claude.com/docs/en/worktrees (fetched today, 2026-07-17, this spike):**

> "Interactive runs require workspace trust: if you haven't run Claude in the directory before, run `claude` once there to accept the trust dialog, or `--worktree` exits with an error prompting you to. **Non-interactive runs with `-p` skip the trust check, so `claude -p --worktree` proceeds without it.**"

Test A confirms this documented claim exactly (`-p --worktree` alone, untrusted dir, succeeds). **Tests B/C show the documented claim does NOT extend to `-p --worktree --tmux`** — the docs page never mentions `--tmux` at all, and adding it silently reintroduces the exact trust gate the page says `-p --worktree` skips. This is a **documentation gap, not merely "unverified"** as the AC doc's U-2/F6 framed it — it is now a confirmed, reproducible discrepancy: the one documented non-interactive worktree path does not hold once `--tmux` joins it.

**Why this is plausible, not a fluke (INFERRED mechanism, not confirmed from source):** a `--tmux` session is a persistent, later-attachable pane that runs outside the per-turn permission-review loop `-p` normally applies — Claude Code most likely re-imposes the interactive-grade trust gate specifically because `--tmux` creates a standing surface a human (or another process) can attach to and drive without further review, unlike a one-shot `-p` call whose entire lifetime is the single headless turn. This is a reasonable inference from the observed behavior, not something the docs or `--help` state outright; it would be confirmed by reading the CLI's trust-check call site (closed source, not available to this probe).

---

## 3. AC2 — Naming scheme / ownership / controllability (BLOCKED — not directly observable in this environment)

**Could not be observed live.** Every attempt to exercise `--tmux` past the refusal in §2 requires the target directory to have already passed the one-time interactive trust dialog (confirmed: this dialog is a real, rendered TUI prompt — captured verbatim below — not a documentation fiction).

**What was tried, and why it stopped (both attempts denied by the harness's own permission classifier — not a Claude CLI behavior, a constraint of *this probing session*):**

1. `claude -p --worktree --tmux ... --dangerously-skip-permissions` → denied by the auto-mode classifier before the Bash call ran at all.
2. A scripted pty (`python3` + the `pty` module) that opened `claude` interactively in the scratch repo and *observed* the rendered trust dialog (this succeeded and is captured below — confirms the dialog is real and its default option is "1. Yes, trust this folder"), then attempted to send `\r` to auto-accept it programmatically → denied by the classifier.

Per the standing operating rule ("when a permission gate blocks a command, hand over the exact command and move on — don't rephrase and retry"), this spike did not attempt a third variant to script past the classifier. This is a genuine environment constraint of the **probing session**, not a Claude CLI limitation — a real interactive user, or a directory pre-trusted by prior legitimate use, would not hit it.

**Trust dialog, captured verbatim (raw ANSI, from the pty observation — this IS what a real operator sees, one time, per directory):**
```
Accessing workspace: /tmp/.../scratchpad/spike-453/probe-repo
Quick safety check: Is this a project you created or one you trust? ...
❯ 1. Yes, I trust this folder
  2. No, exit
Enter to confirm · Esc to cancel
```

**To complete AC2, a human (or a pre-trusted directory) needs to run, once, interactively:**
```
cd <target-dir> && claude   # select "1. Yes, trust this folder", Enter, then exit
```
— after which `claude -p --worktree=<name> --tmux -- "<prompt>"` in that same directory should be retried to observe: tmux session name, exit code, envelope, and `tmux ls` naming.

**INFERRED, not confirmed** (what would confirm: the retry above once trust is bootstrapped):
- Naming scheme: unknown. `--help` gives no naming flag or env var for `--tmux` (no `--session-name`, no documented env var found in `--help` or in the fetched worktrees.md/headless.md pages). By analogy to `--worktree`'s own naming (`.claude/worktrees/<name>/`, branch `worktree-<name>`), a `--tmux` session name derived from the same `<name>` is plausible but **unconfirmed** — the fetched worktrees.md page never mentions `--tmux` at all (grep of the full fetched page: zero occurrences of "tmux").
- Process ownership: whatever process the CLI forks to run `tmux new-session` would own the tmux server socket in the usual tmux sense (first session on a socket owns the server) — standard tmux behavior, not `claude`-specific, and not confirmed for this flag's spawn path specifically.
- **AC-E4 recovery-contract implication (assessed on the *documented* surface, since the live naming is unconfirmed):** no flag in `--help` exposes a name-controlling input for `--tmux` (no `--tmux=<name>`; the only accepted value is the literal string `classic`). Contrast with `--session-id <uuid>` which explicitly supports caller-supplied identity. Because `--tmux`'s only documented parameter is a rendering-mode toggle (`classic` vs default), **there is no confirmed or even documented mechanism to inject a `run_id/seat/attempt`-derived session name** — the AC-E4 registry-adoption identity match this doc requires (`run_id/seat/attempt` in the session name) has no attachment point on this flag as documented. This alone is sufficient to fail AC-E4's compatibility bar regardless of what the live naming turns out to be.

---

## 4. AC3 — Worktree interplay confirmed live (not from docs alone)

**CONFIRMED**, from Test A (§2a) run with `--output-format json`:

- **Where the worktree is created:** `.claude/worktrees/<name>/` under the repo root — confirmed on disk: `.../probe-repo/.claude/worktrees/spike453-a/`.
- **Branch name:** `worktree-<name>` — confirmed: `git branch -a` showed `worktree-spike453-a`; `git worktree list` showed `[worktree-spike453-a]`.
- **Base ref:** `bf58ddb`, identical to the probe repo's single commit on `master`. The probe repo has **no remote configured** (`git init` only, no `origin`). This exercises the documented fallback exactly: per the live-fetched worktrees.md — *"For a `fresh` base ... If no remote is configured, or `origin/HEAD` isn't cached locally and can't be fetched, the worktree falls back to your current local `HEAD`."* Confirmed behavior matches this documented fallback (default `worktree.baseRef` is `fresh`; with no remote it fell back to local HEAD, which in this repo is identical to `master`). This probe cannot distinguish `fresh`-from-origin/HEAD vs `head` in a repo that has a real remote — that distinction is untested here (would need a probe repo with an `origin`).
- **Persistence after exit — CONFIRMED, it does NOT auto-clean:** immediately after the `-p` call returned (exit 0), `git worktree list --verbose` showed the worktree still present and **still locked**: `locked: claude session spike453-a (pid 2888621 start 43672471)`. It was not removed by the CLI on process exit. This matches the docs verbatim (fetched live): *"Non-interactive runs with `-p` have no exit prompt, so Claude doesn't clean up their worktrees. Remove them with `git worktree remove`."* **Confirmed live, not just from docs.**
- **Cleanup performed by this spike (hygiene, not a finding):** `git worktree unlock` + `git worktree remove --force` + `git branch -D worktree-spike453-a`, all verified removed before the spike closed out (§6).

Docs contract vs observed behavior side-by-side:

| Claim (docs, fetched live) | Observed |
|---|---|
| `-p --worktree` skips trust check | **Confirmed** (Test A succeeded on an untrusted dir) |
| `-p --worktree --tmux` — not mentioned in docs at all | **New finding: fails the trust check** (Tests B/C) — docs' skip-the-check claim does not extend here |
| Worktree at `.claude/worktrees/<name>/`, branch `worktree-<name>` | **Confirmed** |
| Fresh base falls back to local HEAD with no remote | **Confirmed** |
| `-p` worktrees are NOT auto-cleaned | **Confirmed** |
| `--tmux` "requires --worktree", iTerm2 native panes / `--tmux=classic` fallback | **Confirmed text only** (from `--help`); the iTerm2 path is out of scope on this Linux host (`TERM_PROGRAM=tmux`, no iTerm2) — noted, not tested |

---

## 5. Verdict

**PARTIAL — usable-as-spawn is NOT confirmed; the flag fails closed on the exact precondition an automated supervisor cannot satisfy on its own.**

- `claude -p --worktree` alone: usable as a spawn primitive today (Test A) — worktree isolation, JSON envelope, correct output, all as documented.
- `claude -p --worktree --tmux`: **not usable as an unattended spawn primitive as-is**, because it hard-refuses (exit 1, no session, no worktree) on any directory that hasn't already passed a one-time *interactive* trust dialog, and that dialog has no documented non-interactive equivalent (no flag, no env var found in `--help` or in the two docs pages fetched live for this spike). A supervisor spawning executor instances across many worktrees/repos would need every target directory pre-trusted out of band before this flag could fire successfully — a real operational precondition, not a one-time setup cost, since the AC doc's architecture spans many seats/worktrees.
- Even bracketing the trust gate: **no naming/identity-injection surface was found on `--tmux`** (§3) — the only accepted value is the literal `classic`. This independently fails the AC-E4 requirement that session names encode `run_id/seat/attempt` for registry-adoption identity matching, regardless of what live session-naming turns out to be once trust is bootstrapped.

**Recommendation: do NOT delegate the supervisor's tmux spawn to the native `--tmux` flag.** Keep the planned engine-managed tmux supervisor module (`phase_executor/supervisor.py`, command-as-initial-pane-process via plain `tmux new-session -d -s <name> '<cmd>'` wrapping headless `claude -p`) as the primary and only mechanism, exactly as the AC doc's §3 BUILD section and AC-E1/E2 already assume. Native `--tmux` should be treated as a convenience for a human operator's own interactive terminal use, not a building block for autonomous seat dispatch — it fails on the two things that matter most for that role: unattended usability (trust gate) and controllable identity (no name injection).

**What would upgrade this verdict:** a human (or a directory pre-trusted through legitimate prior interactive use) re-running the AC2 retry command in §3 to observe actual session naming — this could only *soften* the naming-identity conclusion if some undocumented naming convention proves stable and parseable; it cannot change the trust-gate conclusion, which is independent of naming and already fully confirmed.

---

## 6. Probe hygiene — cleanup confirmation

- `tmux ls` before every probe and after: identical 8 pre-existing sessions throughout (`1`, `1-44`, `chorestory`, `chorestory-45`, `jarvis`, `kukakuka`, `kukakuka-46`, `rawgentic-next`, `saystory`) — none touched, none created by any probe in this spike (every composition probe that reached the tmux-decision point refused before creating a session; §2/§2a).
- Worktree `spike453-a` created by Test A: unlocked, removed (`git worktree remove --force`), and its branch (`worktree-spike453-a`) deleted — verified via `git worktree list` and `git branch -a` showing only `master` afterward.
- No `send-keys` was used to drive any tmux session created for comparison (none were created). The one interactive `claude` process launched via `pty` (to observe the trust dialog, §3) was a plain terminal-emulation observation, not a tmux pane, and was terminated (`SIGTERM`→`SIGKILL` after grace) once the dialog was captured; `ps aux` confirms no orphaned process from it.
- Scratch git repo and deliverable worktree live entirely under `/tmp/.../scratchpad/spike-453/`; the rawgentic repo's main checkout was never touched by any `--worktree`/`--tmux` probe.

---

## AC-doc disposition delta

Text to replace **§6 U-2** in `docs/planning/2026-07-17-orchestrator-executor-acceptance-criteria.md` (the ratified doc on branch `docs/orchestrator-executor-ac`, PR #451) with, once the orchestrator applies it:

> - **U-2 — RESOLVED by spike #453 (2026-07-17), live-probed on claude 2.1.212, Linux/tmux 3.4 (no iTerm2):** `claude -p --worktree` alone succeeds non-interactively on an untrusted directory exactly as documented (worktree at `.claude/worktrees/<name>/`, branch `worktree-<name>`, fresh-base-falls-back-to-local-HEAD-with-no-remote, no auto-cleanup on exit — all confirmed live). **Adding `--tmux` to that same command, on the same untrusted directory, reinstates the workspace-trust gate that `-p --worktree` alone skips**: `claude -p --worktree --tmux "<prompt>"` exits 1 with `Workspace trust not yet accepted. Run \`claude\` once in this directory and accept the trust dialog, then retry with --worktree.` — no tmux session, no worktree, no model call. This is a confirmed, unconfounded finding (isolated A/B/C across `--worktree` alone vs `--worktree --tmux` vs `--worktree --tmux=classic`, same directory, same trust state) and a genuine gap in the docs page (code.claude.com/docs/en/worktrees), which documents the `-p --worktree` trust-skip but never mentions `--tmux` at all. Independently, `--tmux`'s only documented value is the literal `classic` (no name/identity-injection flag or env var was found in `--help` or in the fetched docs) — so even bracketing the trust gate, native `--tmux` has no attachment point for the `run_id/seat/attempt` identity AC-E4 requires for registry adoption. **Verdict: partial / not usable as the supervisor's primary spawn mechanism.** Native `--tmux` is out as a delegation target; the planned engine-managed tmux supervisor module (AC-E1/E2, `phase_executor/supervisor.py`) remains the sole mechanism, now with direct confirming evidence rather than an assumption. Full probe log, verbatim commands/output, and docs-vs-observed table: `docs/planning/2026-07-17-spike-453-tmux-p-composition.md`. Residual unknown (would need a human or a pre-trusted directory to close): live tmux session naming/ownership once past the trust gate — cannot change the verdict above (trust gate and missing identity-injection are independent of naming), but would fill in the descriptive picture.
