# herdr runbook

Operational reference for the herdr terminal multiplexer as this workspace uses it.

herdr is load-bearing: `projects/rawgentic` and `projects/thewanderinginn` both route their WF2 **build seat** through it (`executorTerminalBackend: {"build": "herdr"}`), so a broken herdr install takes the build seat with it.

**Scope of this page today (#610).** It covers the **Claude Code integration** — what installing it changes, how to prove the rest of the harness survived, and how to remove it. The binary pin itself lives in `hooks/herdr-pin.json` and its provenance in `docs/reviews/2026-07-27-609-herdr-supply-chain-vet.md`. Section 7 covers the launcher's herdr mode (#611). Broader workspace conventions — pane/workspace layout and run-driver conventions — arrive with #613, which extends this file.

## 1. What is installed on this host

| Component | State | Source of truth |
|---|---|---|
| herdr binary | `0.7.5` at `~/.local/bin/herdr`, verified bit-for-bit against the published release digest | `hooks/herdr-pin.json` → `pin` |
| Claude Code integration | **installed, v7** | `hooks/herdr-pin.json` → `integrations.claude` |
| Hardened `config.toml` baseline | documented, **not yet applied** (owner-triggered) | `hooks/herdr-pin.json` → `pin.config_baseline` |

Check the integration at any time:

```bash
herdr integration status            # or: --outdated-only
# claude: current (v7) (<HOME>/.claude/hooks/herdr-agent-state.sh)
```

`status` is the check that matters. It compares the installed script's embedded `HERDR_INTEGRATION_VERSION` against the binary's own expectation, so it catches the case a version pin alone misses: the same pinned binary shipping a newer integration script.

## 2. What the install changes

`herdr integration install claude` does exactly two things:

1. Writes the hook script to `~/.claude/hooks/herdr-agent-state.sh`.
2. Registers **one** hook entry in `~/.claude/settings.json`.

### 2.1 Evidence classes — read this before trusting any artifact below

A real `~/.claude/settings.json` carries the operator's permission allowlist, absolute home paths, and plugin inventory, and this repo is **public**. So the committed evidence is deliberately of three different strengths, and each is labelled:

| Class | What it means | Where |
|---|---|---|
| **byte-exact** | Committed bytes, asserted by digest or regenerated diff in CI | the v7 hook script fixture; `install.diff` |
| **redacted** | Real installer output against a *synthetic* settings file, with the sandbox `$HOME` normalized to the literal `<HOME>` | `settings.before.json` / `settings.after.json` |
| **semantic** | A claim about the live host, verified by reading, not committed verbatim | §3 unperturbed claims; §4 measurements |

Nothing here is a verbatim capture of a live host settings file, and no artifact should be read as one.

### 2.2 The exact diff — byte-exact, on a sanitized fixture

`tests/fixtures/herdr/install.diff` is the real unified diff produced by running the actual installer against a sandboxed `$HOME` seeded with `tests/fixtures/herdr/settings.before.json` — a synthetic file carrying the same *shape* as a real one (mempalace on `Stop` + `PreCompact`, three `PreToolUse` entries, one nested object to expose key sorting) and none of its content.

`tests/hooks/test_herdr_pin.py` regenerates that diff with the same `diff -u` invocation and asserts it matches, so the artifact cannot drift from the fixture pair it documents.

The herdr-owned addition, in full:

```json
    "SessionStart": [
      {
        "hooks": [
          {
            "command": "bash '<HOME>/.claude/hooks/herdr-agent-state.sh' session",
            "timeout": 10,
            "type": "command"
          }
        ],
        "matcher": "*"
      }
    ],
```

That is the entire semantic footprint. Nothing else is added and **nothing is removed** — asserted against the fixtures by `test_install_adds_only_the_sessionstart_entry`.

### 2.3 The incidental whole-file reformat

The installer parses and **re-serializes the entire settings.json with alphabetically sorted keys** (top level and nested — `"repo"` sorts before `"source"`, `"type"` after `"command"`, `"matcher"` after `"hooks"`) and **drops the trailing newline**.

`install.diff` shows this plainly: on a 67-line synthetic fixture, a one-hook change produces a 79-line rewrite ending in `\ No newline at end of file`. On a real hand-maintained settings.json it renders as a ~300-line diff. It is cosmetic — JSON object key order is not semantic and no value changes — but a diff-review that does not expect it reads it as the installer rewriting the config.

### 2.4 The hook script — byte-exact

`tests/fixtures/herdr/herdr-agent-state.v7.sh` is the installed script, committed verbatim: integration v7, `3202` bytes, sha256 `ffd5a76b7c62f5313040fc1e98fa010ff19a7aa85dd9fe6f325b9729d5f01b46`.

Committing it is safe and useful for one measured reason: the script is **generic**. It takes its pane and socket identity from the environment and embeds no host path, so its bytes are identical on every machine — the fixture's digest was measured equal to this host's installed script. That is also what makes the digest CI-verifiable (`test_committed_script_fixture_matches_the_pinned_digest`) rather than an observation nobody can check.

The file header declares it herdr-managed and warns that reinstalling or updating the integration **overwrites** it. **Add custom hooks beside this file; never edit it.**

What it does, read from the script rather than inferred:

- `#!/bin/sh`, `set -eu`. Accepts only the `session` action; any other argument exits 0.
- Hard-gates on `HERDR_ENV=1`, `HERDR_SOCKET_PATH`, `HERDR_PANE_ID` and `python3` being present, exiting 0 if any is missing, so outside a herdr pane it is inert.
- Sends one `pane.report_agent_session` JSON-RPC line over the unix socket with a 0.5 s timeout and swallows every exception.
- Ignores any event carrying `agent_id`, so **subagent** events never move the pane state.
- Explicitly refuses `SubagentStop`. The in-script comment records why: older integrations mapped it to durable-working, but Claude's recap/away-summary can emit it after the main turn already stopped — "never let it revive an idle pane."

Because every failure path exits 0 silently, a broken wiring produces **no error anywhere**. That is why `herdr integration status` (§1) is the operator check.

## 3. What the install does NOT touch

Verified, not assumed:

- **wal-guard is not in `~/.claude/settings.json` at all.** The rawgentic **plugin** registers it, so the installer's settings.json rewrite cannot reach it. Confirmed by grepping the user settings file for `wal-guard` and `wal_guard`: no matches.
- **mempalace survives:** its `PreCompact` and `Stop` entries (`mempalace-hook-wrapper.sh`, `timeout: 210`) come through with identical commands, matchers and timeouts.
- **The `PreToolUse` chain survives unchanged:** the question-visibility guard, the tmux-kill guard, and `rtk hook claude`.

For the fixture pair this is an enforced invariant, not prose: `test_install_leaves_every_other_hook_owner_value_identical` asserts every pre-existing hook event is value-identical after the install. Compared by value rather than bytes on purpose — the whole-file key sort (§2.3) would fail a byte comparison while telling you nothing about semantics.

For the **live host** the same claim is `semantic` class per §2.1: established by reading the live file, not by a committed capture.

## 4. Proving the harness still works

Registration surviving a diff is not the same as the hooks still running. Three live checks:

1. **The herdr hook itself delivered.** Compare what the server knows about your pane against your own session id. Run `printenv CLAUDE_CODE_SESSION_ID`, then `herdr pane list` and find your `$HERDR_PANE_ID`. That pane's `agent_session` must carry `"source": "herdr:claude"` and a `value` equal to the session id. Measured 2026-07-27 on this host: `agent_session.value` was exactly the running session's `CLAUDE_CODE_SESSION_ID`, with `source: "herdr:claude"`, `kind: "id"` and `agent_status: "working"` — end-to-end proof the SessionStart hook ran and its socket write landed.
2. **The `PreToolUse` chain still fires.** `rtk` rewrites and filters Bash output, so ordinary `git` and `grep` calls coming back in rtk's condensed form is itself proof the chain is live.
3. **The rawgentic hook suite is green.** `pytest tests/ -q` from the repo root.

**Honest limit:** mempalace's hooks are `Stop` and `PreCompact`, which fire at turn end and at compaction, so a mid-turn check verifies their registration but cannot observe them running.

## 5. Uninstall

```bash
herdr integration uninstall claude
```

Measured behaviour:

- Deletes `~/.claude/hooks/herdr-agent-state.sh`.
- Removes **only its own** `settings.json` entry, matched on the exact command path. A differently-pathed sibling entry survives — see the stale-duplicate gotcha in §6.
- When its entry is the **sole** `SessionStart` member, the whole `SessionStart` key is removed rather than left as an empty array. Unrelated events are untouched.
- `herdr integration status` then reports `claude: not installed`.

Uninstall does not restore the pre-install key ordering; the file stays in herdr's normalized sorted form. That is cosmetic (§2.3).

## 6. Gotchas, each measured

**There is no dry-run.** `herdr integration install <TARGET>` takes no flags. To preview the change, run the real installer against a sandboxed `$HOME`. The recipe below is deliberately **redaction-safe**: it prints only the JSON *paths* that changed, never their values, so it is safe to run inside an agent session or paste into a ticket.

```bash
set -eu
umask 077
FH=$(mktemp -d)
trap 'rm -rf "$FH"' EXIT INT TERM        # the copy holds your real settings — always clean it up
mkdir -p "$FH/.claude"
cp ~/.claude/settings.json "$FH/.claude/settings.json"
before=$(sha256sum < ~/.claude/settings.json)
HOME="$FH" herdr integration install claude
after=$(sha256sum < ~/.claude/settings.json)
[ "$before" = "$after" ] && echo "live settings.json unchanged: OK" || echo "LIVE FILE CHANGED - STOP"
python3 - "$FH" <<'PY'
import json, sys, pathlib
def paths(o, p=""):
    if isinstance(o, dict):
        for k, v in o.items(): yield from paths(v, f"{p}.{k}")
    elif isinstance(o, list):
        for i, v in enumerate(o): yield from paths(v, f"{p}[{i}]")
    else: yield p
fh = pathlib.Path(sys.argv[1])
a = set(paths(json.loads(pathlib.Path.home().joinpath(".claude/settings.json").read_text())))
b = set(paths(json.loads((fh / ".claude/settings.json").read_text())))
for k in sorted(b - a): print("ADDED  ", k)
for k in sorted(a - b): print("REMOVED", k)
print("(paths only, no values printed)")
PY
```

Expect exactly the `.hooks.SessionStart[...]` paths added and nothing removed.

**If you need the full unredacted diff**, run it only in a trusted local terminal — never in an agent session or CI log, where the transcript would capture your permission allowlist. Write it to a mode-`0600` file rather than stdout:

```bash
( umask 077; diff -u ~/.claude/settings.json "$FH/.claude/settings.json" > ~/herdr-install.diff )
```

Delete it when you are done. For a shareable artifact use the committed sanitized pair in §2.2 instead.

**Idempotent on an exact-path match, appending otherwise.** Repeating the install a 2nd and 3rd time left the `SessionStart` array unchanged — it dedupes on the exact command string. But an install whose target path differs from the entry already present **appends a second entry**. So a settings.json carried between hosts, users or `$HOME`s accumulates a stale `SessionStart` entry pointing at a script that does not exist, and `install` will never clean it up. Remove the stale entry by hand.

**`herdr wait` does not exist in 0.7.5** even though the herdr agent skill documents it. Tracked as #659.

**Only the `build` seat pops a pane.** Review and analysis seats return `transport: "native"` and no pane, by design. Expecting a pane from a review dispatch means waiting forever.

**On this project a build-seat dispatch is currently refused outright**, so no pane appears from it either. The build seat resolves to a Claude model, but `MUTATING_FS_SANDBOXED` in `hooks/executor_routing_lib.py` allowlists `codex` alone (owner decision 2026-07-20: codex is Landlock-confined, claude has no FS sandbox), so a mutating-claude dispatch returns `EXIT_REFUSED` at STEP 0 with tag `mutating_claude_requires_fs_sandbox` — before any worktree or pane is created. Closing that needs either a declared codex build lane or the FS-sandbox child.

**A pane-less process cannot dispatch the build seat.** `HerdrBackend` dispatches via `herdr pane split --current`, so a cron-spawned process gets `{"error":{"code":"no_current_pane"}}`. This is why arming a durable launcher for a herdr-gated project needs the #611 herdr-aware variant first.

## 7. Unattended resume: the launcher's herdr mode

A herdr-gated project routes its build seat through `herdr pane split --current`. A cron-spawned launcher has no current pane, so a session it starts the ordinary pane-less way dies at its first build-seat dispatch with `{"error":{"code":"no_current_pane"}}`. Without herdr mode, every herdr-gated project loses unattended resumption entirely.

**The mechanism, and the one probed fact it rests on:** `herdr pane split` accepts an **explicit** pane id (`--pane <ID>`), not only `--current`. Splitting from a named **anchor pane** therefore needs no current pane — and the session started in the resulting pane *has* one, so `--current` resolves normally for that session's own dispatches. No `HerdrBackend` change is needed.

The implementation is `hooks/launcher_lib.py`. `perform_handoff` is the wired entry point: it executes the ordered sequence through an **injectable runner** (`subprocess.run(argv, shell=False)` by default), parses each response strictly, reads the verification artifacts itself, and gates teardown internally. Every builder returns a `list[str]` argv; this module never constructs a shell string.

### 7.1 The ordered sequence

1. `herdr pane split --pane <anchor> --direction down --cwd <repo>`, then strictly parse the new pane id out of the response. An unparseable response aborts.
2. `herdr agent start <name> --kind claude --pane <new> --timeout <ms>` — **with no goal** (see §7.2).
3. `herdr agent wait <new> --until idle --timeout <ms>` — readiness, before anything is pasted.
4. `herdr pane send-text <new> "/goal <condition>"` then `herdr pane send-keys <new> Enter`.
5. Verify each step against its on-disk artifact, in order, aborting at the first failure.
6. `herdr pane close <anchor>` — the predecessor, **last**, and only once every check passed.

### 7.2 Why the goal is NOT armed at birth

An earlier revision passed the goal through `herdr agent start … -- "/goal …"`. A cross-model review rejected that: herdr 0.7.5 refuses a native agent argument containing a **control character** and requires a readiness timeout **greater than 3000 ms**. A real goal condition is multiline, so argv-at-birth fails on precisely the case the requirement exists for. Those upstream line citations came from the review and are **not independently verified here**, which is why `launcher_lib` validates control characters and the timeout floor itself rather than relying on herdr to do it.

The send-text route is used instead. It is independently proven for a 2847-char, 41-newline condition, which arrives as a collapsed bracketed paste and does not submit early.

### 7.3 What "argv-only" does and does not mean

`launcher_lib` never builds a shell string. That is **not** the same as "no shell is involved": herdr strips its `--`, shell-quotes each element, and submits a shell command to the pane's shell. An earlier version of this page claimed no shell ever parses the condition — that was wrong. No injection was found, because herdr's quoting is sound, but the honest residual risks are **argument and authority injection**, not shell injection. Hence:

- `claude_args` is an **allowlist** (`--print`, `--continue`, `--resume`). An authority-bearing flag such as `--permission-mode` or `--config` is refused, because it would change the successor's authority.
- `cwd` is canonicalized and **confined below the project root**, so a caller cannot move the successor's execution out of the project.
- Pane ids and agent names are validated fail-closed, option-shaped values refused, control characters rejected.

### 7.4 Verifying the handoff (never scraped pane text)

| Step | Artifact that proves it |
|---|---|
| `spawned` | `herdr pane get <pane>` returns a non-empty `agent_session.value` |
| `project_switched` | `claude_docs/session_registry.jsonl` carries a line with the NEW session id |
| `goal_armed` | the successor transcript carries a `goal_status` attachment with `met: false` |

Pane text is rendered, wrapped, and scrolls away, so it is never the evidence. A step whose artifact is missing, unreadable, or unparseable counts as **failed**, not passed: an unreported check is not evidence of success, and what it gates is irreversible. `goal_armed` specifically requires `met: false` — an already-met goal would not prove the successor is guarded. A live handoff failed on 2026-07-27 precisely because no step was verified and the goal silently never armed.

Truncation is surfaced, never silent: if the condition exceeds the 4000-char cap (which includes the `/goal ` prefix and the truncation note, so a 4000-char condition does not itself fit), the wired path reports it on the step record.

### 7.5 Fallback, and what is deliberately NOT a fallback

`select_launch_mode` returns one of three modes, always with a visible reason:

- `pane_less` — the project is not herdr-gated, so the pre-existing pane-less launch is correct. Retained and tested.
- `single_session` — the project **is** herdr-gated but herdr is unavailable, or this launcher does not advertise herdr mode. This deliberately does **not** hand out a pane-less successor: that successor is already known to die at its first build-seat dispatch, and retiring a viable predecessor for it is not what this repo means by fail-open. Keeping the current loop matches `driver_lib.fresh_session_available`'s contract and `docs/multi-issue-driver.md`.
- `herdr` — herdr-gated, herdr available, launcher advertises support.

The third branch is exactly #666's narrowed condition.

### 7.6 What is NOT yet proven (#611 ACs 2, 3, 5)

Honest status, so this section is not read as more than it is. The sequence in §7.1 is implemented and driven end to end in tests through an injected runner. It has **not** been executed against a live herdr server or a real cron firing.

- **AC2 (cron to headless-server path) and AC3 (a real resume cycle through cron landing in a pane) are unproven.** Both need an actual cron firing, i.e. a crontab write, which is owner-gated on this host.
- **AC5 (a real build-seat dispatch inside a cron-spawned successor) is blocked by #671.** The build seat resolves to a Claude model while `MUTATING_FS_SANDBOXED` allowlists `codex` alone, so a mutating dispatch is refused at STEP 0 before pane resolution is reached. Pane availability is necessary but not sufficient until #671 is resolved.

### 7.7 Correction to a documented gotcha

The top-level `herdr wait` genuinely does not exist in 0.7.5 (tracked as #659), but **`herdr agent wait <target> --until idle|working|blocked|done|unknown --timeout <ms>` does exist** and is the readiness primitive §7.1 step 3 uses. Scope #659 to the top-level form rather than claiming the capability is absent.
