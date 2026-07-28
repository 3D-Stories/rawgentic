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

The implementation is `hooks/launcher_lib.py`. `perform_handoff` is the wired sequence; `python3 hooks/launcher_lib.py handoff …` is the entry point that drives it from a campaign's driver-state file. It executes the ordered sequence through an **injectable runner** (`subprocess.run(argv, shell=False)` by default), parses each response strictly, reads the verification artifacts itself, and gates teardown internally. Every builder returns a `list[str]` argv; this module never constructs a shell string.

**What ships here and what does not.** The in-repo entry point above is real and tested. The workspace launchers that would call it (`/home/rocky00717/rawgentic/*-resume.sh`) and the `long-run-resume` skill live **outside any git repository**, so wiring them is not part of this PR and no crontab line is installed. See §7.6.

### 7.1 The ordered sequence

1. Record the current size of `session_registry.jsonl` — the **pre-launch offset**. Only evidence appearing after it counts (§7.4).
2. `herdr pane split --pane <anchor> --direction down --cwd <repo>`, then strictly parse the new pane id out of the response. An unparseable response aborts.
3. `herdr agent start <name> --kind claude --pane <new> --timeout <ms>` — **with no goal** (see §7.2).
4. `herdr agent wait <new> --until idle --timeout <ms>` — readiness, before anything is pasted.
5. `herdr pane get <new>` → the successor's session id. Record its transcript's pre-launch offset too.
6. `herdr pane send-text <new> "/goal <condition>"` then `herdr pane send-keys <new> Enter`.
7. **Poll the transcript until the guard is proven armed** — a `goal_status` row with `met: false` whose condition is the one just armed. Failing here aborts *before* the successor is given any work.
8. `herdr pane send-text <new> "<resume prompt>"` then `send-keys Enter`. The prompt is `driver_lib`'s canonical resume wording for the next ready child, never a hand-written string.
9. Poll the registry until the successor's own `/rawgentic:switch` line appears.
10. `herdr pane close <anchor>` — the predecessor, **last**, and only once every check passed.

Step 8 comes after step 7 deliberately. A goal only re-prompts a session that tries to **stop**, so a successor that is armed but never given work sits idle and the run stalls silently — with the predecessor already retired. Equally, work handed to a session whose guard never armed is an **unguarded** run. Both orderings were wrong in earlier revisions; this one is the fix.

### 7.2 Why the goal is NOT armed at birth

An earlier revision passed the goal through `herdr agent start … -- "/goal …"`. A cross-model review rejected that: herdr 0.7.5 refuses a native agent argument containing a **control character** and requires a readiness timeout **greater than 3000 ms**. A real goal condition is multiline, so argv-at-birth fails on precisely the case the requirement exists for. Those upstream line citations came from the review and are **not independently verified here**, which is why `launcher_lib` validates control characters and the timeout floor itself rather than relying on herdr to do it.

The send-text route is used instead. It is independently proven for a 2847-char, 41-newline condition, which arrives as a collapsed bracketed paste and does not submit early.

### 7.3 What "argv-only" does and does not mean

`launcher_lib` never builds a shell string. That is **not** the same as "no shell is involved": herdr strips its `--`, shell-quotes each element, and submits a shell command to the pane's shell. An earlier version of this page claimed no shell ever parses the condition — that was wrong. No injection was found, because herdr's quoting is sound, but the honest residual risks are **argument and authority injection**, not shell injection. Hence:

- The wired path takes a **typed launch mode**, not caller-supplied Claude options. There is exactly one: `fresh`, which passes no options. A `resume` mode was offered and then removed — a resumed successor can already own a registry row and an unmet goal row, so its evidence is only ever temporal, never causally tied to this handoff, and #569's contract is a FRESH successor launched with no `--resume` anyway. Removing it deletes the whole stale-evidence class rather than documenting around it.
- Where Claude options are still accepted at the builder boundary they pass an **allowlist** (`--continue`, `--resume`). An authority-bearing flag such as `--permission-mode` or `--config` is refused, because it would change the successor's authority. `--print` is deliberately absent: it is non-interactive, and `herdr agent start` requires an interactive agent.
- `cwd` is canonicalized and **confined below the project root**, so a caller cannot move the successor's execution out of the project.
- Pane ids and agent names are validated fail-closed, option-shaped values refused, control characters rejected. Every **caller-supplied** field is validated before the split, so a bad argument can never create a pane and then fail. The pane id herdr **returns** is necessarily validated after the split; if it is malformed, or equal to the anchor, or an id that already existed, ownership is not provable and it takes the report-only path below rather than being closed.

**The uncertain-split window — a KNOWN residual leak, reported not fixed.** herdr can create a pane and still fail to describe it: a non-zero exit after server-side creation, rc 0 with truncated JSON, a client timeout after the server acted, or a pane id that parses but fails validation. A pane is closed **only when it is proven ours** — herdr named it, it is not the anchor, and it was absent from the mandatory pre-split inventory. Every other outcome is reported. (The inventory is required, not best-effort: it is the only thing that can show a returned id is genuinely new, so a `pane list` that fails refuses the handoff before anything is created.) When ownership cannot be proven, this runbook's earlier revision closed whichever single pane appeared in a before/after diff — and that was **unsound**. Cardinality is not attribution: if our split created nothing and an unrelated session split concurrently, the diff is exactly one pane and it belongs to a live session. The inventory is server-wide, so it need not even share our workspace.

Proving ownership would need a token the split stamps and a later read verifies. `herdr pane split` accepts `--env`, but herdr 0.7.5's `pane get` and `pane list` expose no environment (verified against the live server on this host), so no such round trip exists. The launcher therefore **reports** what appeared and closes nothing:

```
POSSIBLE ORPHAN: 1 pane(s) appeared during a failed split (w1:pNEW) — NOT closed, because
herdr 0.7.5 offers no way to prove which is ours; check `herdr pane list` and close by hand
```

Killing a colleague's live session is far worse than leaking a pane. If herdr later exposes pane environment or a creation token, this becomes attributable and can close automatically.

**`handoff` accepts only the `herdr` verdict.** `pane_less` means "use the retained `claude --print` path" (`build-fallback`) and `single_session` means "keep the current loop". Running the herdr sequence for either would split a pane on a project that never wanted one and then retire the predecessor after launching by a different mechanism.

**The campaign's own `session_mode` decides whether there is a boundary at all**, and the launcher asserts its own capabilities (`--launcher-armed`, `--fresh-launch-supported`) rather than having them assumed. A driver-state with no `session_mode` means single-session, and `handoff` refuses it.

### 7.4 Verifying the handoff (never scraped pane text)

Checked in this order — which is causal, not alphabetical:

| Step | Artifact that proves it |
|---|---|
| `spawned` | `herdr pane get <pane>` returns a non-empty `agent_session.value` |
| `goal_armed` | the successor transcript, **past the baseline taken just before the goal was pasted**, carries a `goal_status` attachment with `met: false` whose `condition` is the one just armed |
| `project_switched` | `claude_docs/session_registry.jsonl`, **past the baseline taken before the split**, carries a line with the NEW session id |

Pane text is rendered, wrapped, and scrolls away, so it is never the evidence. A step whose artifact is missing, unreadable, or unparseable counts as **failed**, not passed: an unreported check is not evidence of success, and what it gates is irreversible. `goal_armed` specifically requires `met: false` — an already-met goal would not prove the successor is guarded. A live handoff failed on 2026-07-27 precisely because no step was verified and the goal silently never armed.

**Evidence must be launch-bound.** A successor that carried a session id already owning a registry line and an unmet goal row would let a whole-file read authorise teardown on the predecessor's own history. So each artifact gets a baseline and only content past it counts. The two are taken at different moments, necessarily: the **registry** baseline is captured before the split, while the **successor transcript** baseline is captured as soon as its session id is known from `pane get` and before the goal is pasted — the file does not exist any earlier. `goal_armed` additionally matches the row's `condition` against the text actually armed. A capped goal arms the **truncated** text, so that is the form compared — matching the original would fail forever and teardown could never fire.

**A baseline that cannot be established refuses the handoff.** Only `FileNotFoundError` means "empty" — that is the expected state of a successor transcript before the successor exists. Any other read failure on an artifact that *does* exist would otherwise set the offset to zero and let the whole pre-existing file count as this launch's evidence.

**The baseline is a length AND a digest of that prefix.** Length alone cannot see a file replaced at the same or a greater length, and `hooks/registry_prune.py` legitimately rewrites the registry wholesale via `os.replace`. If a prune lands mid-handoff, a purely positional offset points into unrelated content. So the prefix is re-hashed at poll time: an artifact that is no longer an append-only extension of what was measured — shorter, or the same length with different content — voids the evidence instead of being compared against the wrong region.

**What this still does NOT prove.** The evidence is bound to the launch *temporally* (it appeared after a baseline this handoff established, in a file that has not been replaced since). It is not bound *causally* — nothing in a `goal_status` or registry row carries a token identifying this particular handoff. For a `fresh` successor the gap is narrow, which is why `fresh` is the only launch mode. Closing it properly needs a nonce the successor echoes into an artifact; that does not exist yet.

**Reads are polled, bounded, and fail closed.** The artifacts are written by hooks moments after the paste, so a single read races them. `goal_armed` polls up to 12 times at 1.5 s; `project_switched` polls up to 40 times at 3 s, because it needs the successor to run a whole `/rawgentic:switch` turn first. A read error mid-poll is retried, not fatal — a JSONL file being appended to can momentarily fail to read, and a read landing mid-character raises `UnicodeDecodeError`, which is retried alongside OS-level errors. Exhausting either budget fails the handoff and leaves the predecessor alive and guarded.

Truncation is surfaced, never silent: if the condition exceeds the 4000-char cap (which includes the `/goal ` prefix and the truncation note, so a 4000-char condition does not itself fit), the wired path reports it on the step record.

The condition itself is read **verbatim** from the predecessor's own last unmet `goal_status` row (`launcher_lib.py read-goal-condition --transcript <file>`), never retyped or summarised. The **last** unmet row wins, because a run can re-arm its goal and only the most recent row states what is still owed. No unmet row is an explicit refusal, not an invented condition.

### 7.5 Fallback, and what is deliberately NOT a fallback

`select_launch_mode` returns one of three modes, always with a visible reason:

- `pane_less` — the project is not herdr-gated, so the pre-existing pane-less launch is correct. Retained and tested.
- `single_session` — the project **is** herdr-gated but herdr is unavailable, or this launcher does not advertise herdr mode. This deliberately does **not** hand out a pane-less successor: that successor is already known to die at its first build-seat dispatch, and retiring a viable predecessor for it is not what this repo means by fail-open. Keeping the current loop matches `driver_lib.fresh_session_available`'s contract and `docs/multi-issue-driver.md`.
- `herdr` — herdr-gated, herdr available, launcher advertises support.

The third branch is exactly #666's narrowed condition.

### 7.6 What is NOT yet proven (#611 ACs 2, 3, 5)

Honest status, so this section is not read as more than it is. The sequence in §7.1 is implemented, has an in-repo entry point, and is driven end to end in tests through an injected runner. It has **not** been executed against a live herdr server or a real cron firing, and no workspace launcher calls it yet (those scripts are not in any git repo).

- **AC2 (cron to headless-server path) and AC3 (a real resume cycle through cron landing in a pane) are unproven.** Both need an actual cron firing, i.e. a crontab write, which is owner-gated on this host.
- **AC5 (a real build-seat dispatch inside a cron-spawned successor) is blocked by #671.** The build seat resolves to a Claude model while `MUTATING_FS_SANDBOXED` allowlists `codex` alone, so a mutating dispatch is refused at STEP 0 before pane resolution is reached. Pane availability is necessary but not sufficient until #671 is resolved.

### 7.7 Correction to a documented gotcha

The top-level `herdr wait` genuinely does not exist in 0.7.5 (tracked as #659), but **`herdr agent wait <target> --until idle|working|blocked|done|unknown --timeout <ms>` does exist** and is the readiness primitive §7.1 step 3 uses. Scope #659 to the top-level form rather than claiming the capability is absent.
