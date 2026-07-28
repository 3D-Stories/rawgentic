# herdr runbook

Operational reference for the herdr terminal multiplexer as this workspace uses it.

herdr is load-bearing: `projects/rawgentic` and `projects/thewanderinginn` both route their WF2
**build seat** through it (`executorTerminalBackend: {"build": "herdr"}`), so a broken herdr install
takes the build seat with it.

**Scope of this page today (#610).** It covers the **Claude Code integration** — what installing it
changes, how to prove the rest of the harness survived, and how to remove it. The binary pin itself
lives in [`hooks/herdr-pin.json`](../../hooks/herdr-pin.json) and its provenance in
[the #609 supply-chain vet](../reviews/2026-07-27-609-herdr-supply-chain-vet.md). Broader
workspace conventions — pane/workspace layout, the epic-launcher herdr variant, run-driver
conventions — arrive with #613, which extends this file.

---

## 1. What is installed on this host

| Component | State | Source of truth |
|---|---|---|
| herdr binary | `0.7.5` at `~/.local/bin/herdr`, verified bit-for-bit against the published release digest | `hooks/herdr-pin.json` → `pin` |
| Claude Code integration | **installed, v7** | `hooks/herdr-pin.json` → `integrations.claude` |
| Hardened `config.toml` baseline | documented, **not yet applied** (owner-triggered) | `hooks/herdr-pin.json` → `pin.config_baseline` |

Check the integration at any time:

```bash
herdr integration status            # or: --outdated-only
# claude: current (v7) (/home/<user>/.claude/hooks/herdr-agent-state.sh)
```

`status` is the check that matters. It compares the installed script's embedded
`HERDR_INTEGRATION_VERSION` against the binary's own expectation, so it catches the case a version
pin alone misses: the same pinned binary shipping a newer integration script.

---

## 2. What the install changes

`herdr integration install claude` does exactly two things:

1. Writes the hook script to `~/.claude/hooks/herdr-agent-state.sh`.
2. Registers **one** hook entry in `~/.claude/settings.json`.

### 2.1 The settings.json entry — verbatim

This is the entire semantic footprint. Nothing else is added and **nothing is removed**:

```json
{
  "matcher": "*",
  "hooks": [
    {
      "command": "bash '<HOME>/.claude/hooks/herdr-agent-state.sh' session",
      "timeout": 10,
      "type": "command"
    }
  ]
}
```

…appended to `hooks.SessionStart`. `<HOME>` is the absolute home directory at install time.

> **Redaction, stated deliberately.** `3D-Stories/rawgentic` is a **public** repository, and a real
> `~/.claude/settings.json` carries the operator's permission allowlist, absolute home paths, and
> plugin/marketplace inventory. So this page reproduces the **herdr-owned hunk verbatim** — the part
> that is actually the install's diff — and characterizes the rest by shape only, never by content.
> The full unredacted diff is reproducible in seconds with the recipe in §6.

### 2.2 The incidental whole-file reformat

The installer parses and **re-serializes the entire settings.json with alphabetically sorted keys**
(top level *and* nested — e.g. `"repo"` sorts before `"source"` inside every marketplace entry) and
**drops the trailing newline**.

On a hand-maintained settings.json this renders as a **~300-line diff for a one-hook change**. It is
cosmetic: JSON object key order is not semantic, and no value is altered. It is called out here
because a diff-review that does not expect it reads it as the installer rewriting the config.

### 2.3 The hook script

`~/.claude/hooks/herdr-agent-state.sh`, integration v7, `3202` bytes,
sha256 `ffd5a76b7c62f5313040fc1e98fa010ff19a7aa85dd9fe6f325b9729d5f01b46`.

That digest is a **recorded observation of this host**, not a CI-enforced invariant — CI has no
`~/.claude/`. The script's own header declares it herdr-managed and warns that reinstalling or
updating the integration **overwrites** it, so a changed digest normally means herdr replaced it.
**Add custom hooks beside this file; never edit it.**

What it does, read from the script rather than inferred:

- `#!/bin/sh`, `set -eu`. Accepts only the `session` action; any other argument exits 0.
- Hard-gates on `HERDR_ENV=1`, `HERDR_SOCKET_PATH`, `HERDR_PANE_ID` and `python3` being present,
  exiting 0 if any is missing — **outside a herdr pane it is inert.**
- Sends one `pane.report_agent_session` JSON-RPC line over the unix socket with a 0.5 s timeout and
  swallows every exception.
- Ignores any event carrying `agent_id`, so **subagent** events never move the pane state.
- Explicitly refuses `SubagentStop`. The in-script comment records why: older integrations mapped it
  to durable-working, but Claude's recap/away-summary can emit it after the main turn already
  stopped — "never let it revive an idle pane."

Because every failure path exits 0 silently, a broken wiring produces **no error anywhere**. That is
why `herdr integration status` (§1) and the record guards in `tests/hooks/test_herdr_pin.py` are the
only things that reveal it.

---

## 3. What the install does NOT touch

Verified, not assumed:

- **wal-guard is not in `~/.claude/settings.json` at all.** The rawgentic *plugin* registers it, so
  the installer's settings.json rewrite cannot reach it. (`grep -n 'wal-guard\|wal_guard'` over the
  user settings file returns nothing.)
- **mempalace survives byte-for-byte in content:** `PreCompact` → `mempalace-hook-wrapper.sh
  precompact` and `Stop` → `mempalace-hook-wrapper.sh stop`, both `timeout: 210`.
- **The `PreToolUse` chain survives unchanged:** the question-visibility guard, the tmux-kill guard,
  and `rtk hook claude` — same commands, same matchers, same timeouts.

The disjointness half of this claim is enforced as an executable invariant, not just written down
here: `tests/hooks/test_herdr_pin.py` asserts the recorded footprint is exactly `["SessionStart"]`
and disjoint from the events other tools own, so a future edit claiming herdr owns `Stop` or
`PreCompact` fails the suite.

---

## 4. Proving the harness still works

Registration surviving a diff is not the same as the hooks still running. Three live checks:

1. **The herdr hook itself delivered.** Ask the server what it knows about your pane and compare it
   against your own session id:

   ```bash
   printenv CLAUDE_CODE_SESSION_ID
   herdr pane list          # find your $HERDR_PANE_ID
   ```

   The pane's `agent_session` must carry `"source": "herdr:claude"` and a `value` equal to that
   session id. Measured on 2026-07-27 in pane `w1:pAZ`: `agent_session.value` was exactly the
   session's `CLAUDE_CODE_SESSION_ID`, with `source: "herdr:claude"`, `kind: "id"` and
   `agent_status: "working"` — end-to-end proof the SessionStart hook ran and its socket write landed.

2. **The `PreToolUse` chain still fires.** `rtk` rewrites and filters Bash output, so ordinary
   `git`/`grep` calls coming back in rtk's condensed form is itself proof that the PreToolUse chain
   is live.

3. **The rawgentic hook suite is green.** `pytest tests/ -q` from the repo root.

**Honest limit:** mempalace's hooks are `Stop` and `PreCompact`, which fire at turn end and at
compaction, so a mid-turn check cannot observe them running. Their *registration* is verified above;
observing them fire requires ending a turn or triggering a compaction.

---

## 5. Uninstall

```bash
herdr integration uninstall claude
```

Measured behaviour:

- Deletes `~/.claude/hooks/herdr-agent-state.sh`.
- Removes **only its own** `settings.json` entry, matched on the exact command path. A
  differently-pathed sibling entry survives (see the stale-duplicate gotcha below).
- When its entry is the **sole** `SessionStart` member, the whole `SessionStart` key is removed
  rather than left as an empty array. Unrelated events are untouched.
- `herdr integration status` then reports `claude: not installed`.

Uninstall does not restore the pre-install key ordering — the file stays in herdr's normalized,
sorted form. That is cosmetic (§2.2).

---

## 6. Gotchas, each measured

- **There is no dry-run.** `herdr integration install <TARGET>` takes no flags. To see the diff before
  committing to it, run the real installer against a sandboxed `$HOME` seeded with a copy of your
  real file — the exact shipped invocation, your real content as the base, your live file untouched:

  ```bash
  FH=$(mktemp -d); mkdir -p "$FH/.claude"
  cp ~/.claude/settings.json "$FH/.claude/settings.json"
  sha256sum ~/.claude/settings.json                    # record BEFORE
  HOME="$FH" herdr integration install claude
  sha256sum ~/.claude/settings.json                    # MUST be unchanged
  diff -u ~/.claude/settings.json "$FH/.claude/settings.json"
  ```

  The installer honours `$HOME`; confirmed by the sandbox's `integration status` going
  `not installed` → `current (v7)` while the host's own file and script digests stayed identical.

- **Idempotent on an exact-path match, appending otherwise.** Repeating the install a 2nd and 3rd
  time left the `SessionStart` array unchanged — it dedupes on the exact command string. But an
  install whose target path differs from the entry already present **appends a second entry**. So a
  settings.json carried between hosts, users or `$HOME`s accumulates a stale `SessionStart` entry
  pointing at a script that does not exist, and `install` will never clean it up. Remove the stale
  entry by hand.

- **`herdr wait` does not exist in 0.7.5** even though the herdr agent skill documents it — tracked
  as #659.

- **Only the `build` seat pops a pane.** Review and analysis seats return `transport: "native"` and no
  pane, by design. Expecting a pane from a review dispatch means waiting forever.

- **A pane-less process cannot dispatch the build seat.** `HerdrBackend` dispatches via
  `herdr pane split --current`, so a cron-spawned (pane-less) process gets
  `{"error":{"code":"no_current_pane"}}`. This is why arming a durable launcher for a herdr-gated
  project needs the #611 herdr-aware variant first.
