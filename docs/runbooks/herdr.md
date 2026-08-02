# herdr runbook

Operational reference for the herdr terminal multiplexer as this workspace uses it.

herdr is load-bearing: `projects/rawgentic` and `projects/thewanderinginn` both route their WF2 **build seat** through it (`executorTerminalBackend: {"build": "herdr"}`), so a broken herdr install takes the build seat with it.

**Scope of this page today (#610).** It covers the **Claude Code integration** — what installing it changes, how to prove the rest of the harness survived, and how to remove it. The binary pin itself lives in `hooks/herdr-pin.json` and its provenance in `docs/reviews/2026-07-27-609-herdr-supply-chain-vet.md`. Section 7 covers the launcher's herdr mode (#611). Sections 9-12 cover the workspace conventions, the attach/detach/remote recipes, the measured rough edges, and a from-scratch walkthrough (#613).

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

**`herdr wait` does not exist in 0.7.5** even though the herdr agent skill documents it. Tracked as #696. (It was previously cited here as #659, which is herdr **version-drift detection** — a different concern, so nothing actually tracked this.)

**Only the `build` seat pops a pane.** Review and analysis seats return `transport: "native"` and no pane, by design. Expecting a pane from a review dispatch means waiting forever.

**On this project a build-seat dispatch is currently refused outright**, so no pane appears from it either. The build seat resolves to a Claude model, but `MUTATING_FS_SANDBOXED` in `hooks/executor_routing_lib.py` allowlists `codex` alone (owner decision 2026-07-20: codex is Landlock-confined, claude has no FS sandbox), so a mutating-claude dispatch returns `EXIT_REFUSED` at STEP 0 with tag `mutating_claude_requires_fs_sandbox` — before any worktree or pane is created. Closing that needs either a declared codex build lane or the FS-sandbox child.

**A pane-less process cannot dispatch the build seat.** `HerdrBackend` dispatches via `herdr pane split --current`, so a cron-spawned process gets `{"error":{"code":"no_current_pane"}}`. This is why arming a durable launcher for a herdr-gated project needs the #611 herdr-aware variant first.

## 7. Unattended resume: the launcher's herdr mode

A herdr-gated project routes its build seat through `herdr pane split --current`. A cron-spawned launcher has no current pane, so a session it starts the ordinary pane-less way dies at its first build-seat dispatch with `{"error":{"code":"no_current_pane"}}`. Without herdr mode, every herdr-gated project loses unattended resumption entirely.

**The mechanism, and the one probed fact it rests on:** `herdr pane split` accepts an **explicit** pane id (`--pane <ID>`), not only `--current`. Splitting from a named **anchor pane** therefore needs no current pane — and the session started in the resulting pane *has* one, so `--current` resolves normally for that session's own dispatches. No `HerdrBackend` change is needed.

The implementation is `hooks/launcher_lib.py`. `perform_handoff` is the wired sequence; `python3 hooks/launcher_lib.py handoff …` is the entry point that drives it from a campaign's driver-state file. It executes the ordered sequence through an **injectable runner** (`subprocess.run(argv, shell=False)` by default), parses each response strictly, reads the verification artifacts itself, and gates teardown internally. Every builder returns a `list[str]` argv; this module never constructs a shell string.

**What ships here and what does not.** The in-repo entry point above is real and tested. The workspace launchers that would call it (`/home/rocky00717/rawgentic/*-resume.sh`) and the `long-run-resume` skill live **outside any git repository**, so wiring them is not part of this PR and no crontab line is installed. See §7.6.

### 7.1 The ordered sequence

1. Record the current size and prefix digest of `session_registry.jsonl` — its **baseline**. Only evidence appearing after it counts (§7.4).
2. Take a full `herdr pane list` inventory. This is **required**: it is the only thing that can later show a returned pane id is genuinely new, so a `pane list` that fails — or that carries a single malformed record — refuses the handoff before anything is created.
3. `herdr pane split --pane <anchor> --direction down --cwd <repo>`, then strictly parse the new pane id out of the response. An unparseable response aborts.
4. `herdr agent start <name> --kind claude --pane <new> --timeout <ms>` — **with no goal** (see §7.2). **A freshly split pane is not yet an available shell**, so this call is retried while — and only while — herdr answers with its own `agent_pane_busy` code (up to 15 attempts, 2 s apart). Any other refusal is terminal, because retrying a malformed name or a dead server would only postpone the abort. A pane that never becomes available still fails closed and still closes the tentative pane. See §7.8 for how this was found.
5. `herdr agent wait <new> --until idle --timeout <ms>` — readiness, before anything is pasted.
6. `herdr pane get <new>` → the successor's session id. Record its transcript's pre-launch offset too.
7. **SEND 1 — the bind, alone.** `herdr pane send-text <new> "/rawgentic:switch <project>"` then `herdr pane send-keys <new> Enter`. The project argument is mandatory: a bare `/rawgentic:switch` enters the switch skill's list mode and waits for a human (§7.1.1).
8. **Poll the registry until the successor's own bind line appears** (`project_switched`). Failing here aborts before any work is handed over, leaving the predecessor alive and still guarded.
9. **SEND 2 — the work.** `herdr pane send-text <new> "<resume prompt>"` then `send-keys Enter`. The prompt is `driver_lib`'s canonical resume wording for the next ready child, never a hand-written string, and it carries **no bind of its own** — send 1 did that.
10. **Poll the transcript for the prompt's marker** (`prompt_landed`), when the caller supplied one. rc 0 on `send-text` proves transport, not arrival.
11. **SEND 3 — the guard, last.** `herdr pane send-text <new> "/goal <condition>"` then `send-keys Enter`.
12. **Poll the transcript until the guard is proven armed** — a `goal_status` row with `met: false` whose condition is the one just armed.
13. `herdr pane close <anchor>` — the predecessor, **last**, and only once every check passed.

Each poll sits immediately after the send whose artifact it reads, so a failure names the send that caused it. Every gate is a **durable artifact the successor itself writes**; none is a timer, and none is pane status (§7.1.1).

#### 7.1.1 Why the bind is its own send and `/goal` goes last (#694)

Two things changed here, and the second one reverses an earlier revision of this page.

**The bind is its own verified turn.** #682 made the resume prompt *open* with `/rawgentic:switch <project>` and checked that as a prefix — which its own validator docstring is honest about being a *proxy* for "first" rather than a proof of it. Sending the bind separately and waiting for its registry row makes the ordering **structural**, so the proxy has nothing left to do. #682 named this design correct and deferred it only because it reorders a ladder. `perform_handoff` now **refuses** a resume prompt that carries a bind at all: send 1 already did it, and a second one makes the successor run the switch skill twice. `driver_lib`'s builders take `include_bind`, defaulting to **True** — the interactive hand-back and the `claude -p` fallback each deliver exactly one prompt and so still need the bind inside it.

**`/goal` goes last, and this is measured.** A `/goal` pasted into a session *actively mid-turn* on 2026-07-29 produced its `goal_status met:false` row **while that turn was still running**, so it needs no idle window. The old ordering armed the guard first on the reasoning that work handed to an unguarded session is an unguarded run. That concern is **answered rather than discarded**: the predecessor is not retired until the last rung passes, so "work begins unguarded" never coincides with "the predecessor is already gone" — which was the actual harm. The residual unguarded window is between send 2 and step 12, bounded by exactly the thing that closes it.

**`agent_status` is NOT a synchronisation signal, and nothing here may gate on it.** An earlier revision of this fix polled `herdr pane get` for `agent_status == "idle"` between the goal and the prompt. It was **falsified by measurement before it shipped**:

| Measured, live, 2026-07-29 | Consequence |
|---|---|
| after a **real unmet** goal was armed, the pane read `working` on consecutive reads while the `goal_status met:false` row was **already present** | an idle gate placed after `goal_armed` refuses **every real handoff** — strictly worse than the bug |
| `/goal` pasted mid-turn produced its row while the turn ran | the guard needs no idle window, so it can go last |
| the value read `idle` right after a prompt was submitted, `done` mid-output, and `working` at an empty input line | the field does not describe input-readiness at all |

`parse_pane_agent_status` is retained for **diagnostics only** — it is what lets a report say *why* a handoff stalled. No control flow branches on it. The 22 tests that went green on the falsified gate proved nothing, because the fake runner returned a canned `agent_status: idle`.

**What #694 turned out NOT to be.** The issue reported the cause as send **order** and asked for switch → prompt → goal. Reproduction refuted the stated *mechanism*: four live runs, and both orders failed identically under back-to-back sends, while a goal-first H7 live handover landed its prompt fine with the bind 25.5 s later (`docs/planning/2026-07-28-667-uat-plan/harness/evidence/682-h7-live-handover-2026-07-28.md`, lines 34-39). The **conclusion** "the goal goes last" is right; the reason in the issue is not. What actually discriminated was whether each send was *gated on evidence* rather than fired back-to-back.

**A permission-blocked successor is a precondition, not something this code can fix.** A prompt pasted into a session blocked on a permission dialog is swallowed outright — but that case was **induced by the test setup** (`--permission-mode default`). The launcher spawns plain `claude`, and `_ALLOWED_CLAUDE_ARGS` deliberately refuses `--permission-mode` as authority-bearing. So a non-blocking permission mode is a **precondition of unattended handoff** that the launcher cannot assert; it fails loudly on a stalled bind instead of building an auto-accepter.

#### 7.1.2 Delivering prompt text to a pane by hand, and the string that looks like a failure (#696)

Everything above is what `perform_handoff` does for you. This section is for the **ad-hoc** case — handing work to a sibling pane outside a campaign handoff — because that is where it was got wrong, at a real cost.

**Use `pane send-text` then a SEPARATE `pane send-keys Enter`. Never `pane run`.**

```bash
herdr pane send-text <PANE_ID> "<the whole prompt, however long>"
herdr pane send-keys <PANE_ID> Enter
```

Two calls, always. A multiline payload arrives as one collapsed bracketed paste and does not submit early, which is why the Enter is a distinct call rather than a trailing newline (#654). `launcher_lib.build_send_text_argv` already emits exactly this pair and records that reason in its docstring — **the shipped code is correct and must not be "fixed"**.

`pane run` is a shell-command runner — its own help reads *"Run a command in a pane"* — and is **never used for prompt text**. That is the sharp form of the diagnosis: it is the wrong tool, not the right tool used badly. A ~1,400-character prompt sent through it did not submit. The precise mechanism by which its Enter fails on long content was **not traced**, and no length threshold is established: the ~1,400-char failure and the ~2,500-char success differ in **method**, not only in length. The honest rule is therefore "never `pane run` for prompt text, at any length", not a byte count.

**`paste again to expand` and `[Pasted text +N lines]` are NOT errors.** They are Claude Code's collapsed-display affordances for long input, and they appear on **successful** submissions — confirmed live on 2026-07-29, where a ~2,500-character multi-line prompt was delivered by the two-call pattern, submitted, and the receiving agent built a task list and began editing files while a `pane read` showed `paste again to expand`. When you see either string, **the buffer is INTACT and must be submitted as-is**: **never retried** and **never truncated**. Both wrong responses have a real cost — a retry risks **double-submission**, and a truncation **silently corrupts the handoff**, which is worse because nothing downstream can tell.

**Submission is verified from the TRANSCRIPT, and never from `agent_status`.** Read the receiving session's own `<session-id>.jsonl` (or any durable artifact the receiver writes, such as its session-registry row) for something only a delivered prompt could produce. A `send-*` exit code proves transport, not arrival. `agent_status` proves nothing at all: measured live it read `idle` immediately after a prompt was submitted, `done` while a turn was still producing output, and `working` for a session sitting at an empty input line.

**`herdr agent wait` takes an agent NAME, not a pane id.** Passing a pane id for a shell-launched `claude` returns `{"error":{"code":"agent_not_found","message":"agent target w1:pD8 not found"}}`, so it is only usable for agents registered through `herdr agent start` — which is why §7.1 step 5 can use it and an ad-hoc sibling pane generally cannot. Combined with the point above, delivering text to a pane that is **already running** a session has **no readiness primitive available**: poll the artifact instead. (This does not apply to `/rawgentic:pane-handoff`, which starts the successor through `herdr agent start` and therefore does have `agent wait` — see the helper note below.)

**A helper IS now shipped — use it (#700).** This section previously said none was, deliberately, and recorded the condition for revisiting: *"Revisit if ad-hoc pane handoffs become routine enough that hand-rolling the poll is a recurring source of error rather than a one-off."* That condition was met — the sequence was hand-driven twice in one day, and a session-history mining pass found seven separate occasions in 36 hours where the handoff was expected and did not happen.

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/launcher_lib.py" ad-hoc-handoff --help
```

The `/rawgentic:pane-handoff` skill is the front end; `ad-hoc-handoff` is a thin adapter that takes the project, prompt, goal condition and anchor pane directly — no driver-state file and no `--launcher-armed`, which is what `_cmd_handoff` requires and why it could not serve this case — and drives `perform_handoff` unchanged.

**Retiring the caller's own pane is the DEFAULT** (owner decision 2026-07-29). #700 shipped it opt-in and OFF, reasoning that an ad-hoc handoff hands off *work* rather than retiring the caller — and the first real handoff refuted that: the phrasings that trigger the skill mean *retire this one*, and the OFF default left a live pane re-prompting itself from an armed `/goal` until the owner intervened. Retirement is gated on every verification passing, on the pane provably hosting the calling session, and on the goal being **confirmed cleared** — an unconfirmed clear leaves the pane open. `--no-teardown` opts out for an additive handoff and reports that the guard is still armed.

**Which of the two you want depends on whether a session already exists in the target pane:**

| Situation | Use |
|---|---|
| Hand work to a FRESH successor | `/rawgentic:pane-handoff` — it launches the pane, so `agent wait` works and all the gates above apply |
| Deliver text to a pane ALREADY running a session | the by-hand recipe in this section — that pane is not a herdr-registered agent, so it has no readiness primitive and none of the gates apply |

The rule the helper exists to enforce: **never assemble the delivery sequence yourself.** `tests/test_pane_handoff_skill.py` fails if the skill body grows a raw terminal-primitive call, because reinstating it is a silent regression whose only symptom — a prompt that looks undelivered — argues for exactly the wrong response.

**One thing the helper does that the recipe above does not describe: a paste can be intact and genuinely UNSUBMITTED, which is a third state.** Found live on 2026-07-29 driving this sequence by hand. `project_switched` proves the bind's registry row *landed*, not that its *turn ended* — the same reasoning error #694 corrected for `goal_status met:false` and left standing here — so the prompt's Enter can be consumed by the still-running bind turn. The pane then shows `[Pasted text #1 +9 lines]` with the content sitting in the input box and `prompt_landed` never appears.

The recovery is **one bare Enter, and nothing else**: no re-paste (double submission) and no truncation (silent corruption). `perform_handoff` now does up to `PROMPT_NUDGE_ROUNDS` rounds of that, each one gated on `pane_shows_unsubmitted_paste` first, because an Enter accepts whatever is on screen and a bounded count is not a bound on privilege. Any unknown pane state — a permission dialog visible, no paste affordance, a failed read — abandons the recovery and fails closed exactly as before. If you are ever doing this by hand, that is the rule to copy: **submit what is already there.**

**A known gap this repo cannot close.** `~/.claude/skills/herdr/SKILL.md` — the herdr agent skill — documented five invocations of a top-level `herdr wait` that does not exist. It has been corrected locally, but that file is user-level and lives **outside any git repository**, so it is neither committed here nor covered by CI. If you are reading a copy of that skill that still shows `herdr wait`, the skill is stale and this runbook is authoritative. The in-repo half is guarded by `tests/test_herdr_runbook_doc.py`.

### 7.2 Why the goal is NOT armed at birth

An earlier revision passed the goal through `herdr agent start … -- "/goal …"`. A cross-model review rejected that: herdr 0.7.5 refuses a native agent argument containing a **control character** and requires a readiness timeout **greater than 3000 ms**. A real goal condition is multiline, so argv-at-birth fails on precisely the case the requirement exists for. Those upstream line citations came from the review and are **not independently verified here**, which is why `launcher_lib` validates control characters and the timeout floor itself rather than relying on herdr to do it.

The send-text route is used instead. It is independently proven for a 2847-char, 41-newline condition, which arrives as a collapsed bracketed paste and does not submit early.

### 7.3 What "argv-only" does and does not mean

`launcher_lib` never builds a shell string. That is **not** the same as "no shell is involved": herdr strips its `--`, shell-quotes each element, and submits a shell command to the pane's shell. An earlier version of this page claimed no shell ever parses the condition — that was wrong. No injection was found, because herdr's quoting is sound, but the honest residual risks are **argument and authority injection**, not shell injection. Hence:

- The wired path takes a **typed launch mode**, not caller-supplied Claude options. There is exactly one: `fresh`, which passes no options. A `resume` mode was offered and then removed — a resumed successor can already own a registry row and an unmet goal row, so its evidence is only ever temporal, never causally tied to this handoff, and #569's contract is a FRESH successor launched with no `--resume` anyway. Removing it deletes the whole stale-evidence class rather than documenting around it.
- Where Claude options are still accepted at the builder boundary they pass an **allowlist** (`--continue`, `--resume`). An authority-bearing flag such as `--permission-mode` or `--config` is refused, because it would change the successor's authority. `--print` is deliberately absent: it is non-interactive, and `herdr agent start` requires an interactive agent.
- `cwd` is canonicalized and **confined below the project root**, so a caller cannot move the successor's execution out of the project.
- Pane ids and agent names are validated fail-closed, option-shaped values refused, control characters rejected. Every **caller-supplied** field is validated before the split — including the transcript directory, which must already exist — so a bad argument can never create a pane and then fail. The pane id herdr **returns** is necessarily validated after the split; if it is malformed, or equal to the anchor, or an id that already existed, ownership is not provable and it takes the report-only path below rather than being closed. The successor **session id** herdr returns is validated the same way, as a bare token, because it is interpolated into a transcript path and an id carrying `..` would read outside the directory.

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
| `project_switched` | `claude_docs/session_registry.jsonl`, **past the baseline taken before the split**, carries a line with the NEW session id |
| `prompt_landed` (mid-child ladder; launch ladder only when a marker is supplied) | the successor transcript, past its baseline, carries the generation-bound marker as a plain substring |
| `goal_armed` | the successor transcript, **past the baseline taken as soon as the session id was known**, carries a `goal_status` attachment with `met: false` whose `condition` is the one just armed |

The order is the send order (§7.1), and #694 reordered it to keep it that way. It is load-bearing rather than cosmetic: `evaluate_verifications` walks the ladder and stops at the **first** failure, so a ladder listing rungs out of send order reports the wrong step as the thing that broke. A reordered or duplicated ladder is refused outright — the sequence must match one of exactly three canonical tuples, so the pre-#694 order cannot be reintroduced by a caller passing `steps=`. The launch ladder has **no** `prompt_landed` rung because `prompt_marker` is optional there, and gating on an absent result fails closed.

Pane text is rendered, wrapped, and scrolls away, so it is never the evidence. A step whose artifact is missing, unreadable, or unparseable counts as **failed**, not passed: an unreported check is not evidence of success, and what it gates is irreversible. `goal_armed` specifically requires `met: false` — an already-met goal would not prove the successor is guarded. A live handoff failed on 2026-07-27 precisely because no step was verified and the goal silently never armed.

**Evidence must be launch-bound.** A successor that carried a session id already owning a registry line and an unmet goal row would let a whole-file read authorise teardown on the predecessor's own history. So each artifact gets a baseline and only content past it counts. The two are taken at different moments, necessarily: the **registry** baseline is captured before the split, while the **successor transcript** baseline is captured as soon as its session id is known from `pane get` and before the goal is pasted — the file does not exist any earlier. `goal_armed` additionally matches the row's `condition` against the text actually armed. A capped goal arms the **truncated** text, so that is the form compared — matching the original would fail forever and teardown could never fire.

**A baseline that cannot be established refuses the handoff.** Only `FileNotFoundError` means "empty" — that is the expected state of a successor transcript before the successor exists. Any other read failure on an artifact that *does* exist would otherwise set the offset to zero and let the whole pre-existing file count as this launch's evidence.

**The baseline is a length AND a digest of that prefix.** Length alone cannot see a file replaced at the same or a greater length, and `hooks/registry_prune.py` legitimately rewrites the registry wholesale via `os.replace`. If a prune lands mid-handoff, a purely positional offset points into unrelated content. So the prefix is re-hashed at poll time: an artifact that is no longer an append-only extension of what was measured — shorter, or the same length with different content — voids the evidence instead of being compared against the wrong region.

**What this still does NOT prove.** The evidence is bound to the launch *temporally* (it appeared after a baseline this handoff established, in a file that has not been replaced since). It is not bound *causally* — nothing in a `goal_status` or registry row carries a token identifying this particular handoff. For a `fresh` successor the gap is narrow, which is why `fresh` is the only launch mode. Closing it properly needs a nonce the successor echoes into an artifact; that does not exist yet.

**Reads are polled, bounded on BOTH axes, and fail closed.** The artifacts are written by hooks moments after the paste, so a single read races them. `goal_armed` and `prompt_landed` poll up to 12 times at 1.5 s; `project_switched` polls up to 40 times at 3 s, because it needs the successor to run a whole `/rawgentic:switch` turn first. A read error mid-poll is retried, not fatal — a JSONL file being appended to can momentarily fail to read, and a read landing mid-character raises `UnicodeDecodeError`, which is retried alongside OS-level errors. Exhausting either budget fails the handoff and leaves the predecessor alive and guarded.

An attempt count alone is **not** a time bound, which a cross-model review caught on #694: every attempt does I/O and a blocked read has no ceiling of its own. Each poll therefore also carries a wall-clock deadline of `POLL_WALL_CLOCK_SLACK` (2×) its nominal `attempts × delay` budget, and the first attempt always runs so a slow clock can never become a verdict. The same bound now applies to the `agent_start` retry of §7.8, where the arithmetic actually bit: 15 attempts against a runner whose timeout is 180 s is a 45-minute ceiling on a condition that resolves itself in about a second.

Truncation is surfaced, never silent: if the condition exceeds the 4000-char cap (which includes the `/goal ` prefix and the truncation note, so a 4000-char condition does not itself fit), the wired path reports it on the step record.

The condition itself is read **verbatim** from the predecessor's own last unmet `goal_status` row (`launcher_lib.py read-goal-condition --transcript <file>`), never retyped or summarised. For the mid-child path that is now ENFORCED rather than merely documented (Step 11): an explicitly supplied `--goal-condition` is checked against the transcript, and a condition that a later `met:true` row has satisfied — or that a replacement guard has superseded — is refused, because arming the successor with it would hand over a guard that is not what is owed and would also re-arm the predecessor wrongly on the partial-success path. The **last** unmet row wins, because a run can re-arm its goal and only the most recent row states what is still owed. No unmet row is an explicit refusal, not an invented condition.

### 7.5 Fallback, and what is deliberately NOT a fallback

`select_launch_mode` returns one of three modes, always with a visible reason:

- `pane_less` — the project is not herdr-gated, so the pre-existing pane-less launch is correct. Retained and tested.
- `single_session` — the project **is** herdr-gated but herdr is unavailable, or this launcher does not advertise herdr mode. This deliberately does **not** hand out a pane-less successor: that successor is already known to die at its first build-seat dispatch, and retiring a viable predecessor for it is not what this repo means by fail-open. Keeping the current loop matches `driver_lib.fresh_session_available`'s contract and `docs/multi-issue-driver.md`.
- `herdr` — herdr-gated, herdr available, launcher advertises support.

The third branch is exactly #666's narrowed condition.

### 7.6 What is NOT yet proven (#611 ACs 2, 3, 5)

Honest status, so this section is not read as more than it is. The sequence in §7.1 is implemented, has an in-repo entry point, and is driven end to end in tests through an injected runner.

**Corrected 2026-07-28 (#673):** an earlier revision of this section said the sequence had never been executed against a live herdr server. That is no longer true — it has, twice, and the first run **failed**. §7.8 records what that cost and what it changed. It still has not run under a real cron firing, and no workspace launcher calls it yet (those scripts are not in any git repo).

- **AC2 (cron to headless-server path), AC3 (a real resume cycle through cron landing in a pane) and AC5 (a real build-seat dispatch inside a cron-spawned successor) were WITHDRAWN into the separate 5-hour-cron service** by owner decision (epic #667 autorun log, D-16), not merely left unproven. All three are cron-framed, and the decision decoupled the cron concern from the handover: the cron service resumes the **already-active** session rather than starting a new one, so it needs none of this machinery. AC2 is worse than unproven — rawgentic carries `headlessEnabled: false`, so it would have proved a path this project does not use.
- **AC5's blocker stands on its own merits and is tracked as #671.** The build seat resolves to a Claude model while `MUTATING_FS_SANDBOXED` allowlists `codex` alone, so a mutating dispatch is refused at STEP 0 before pane resolution is reached. Pane availability is necessary but not sufficient until #671 is resolved. Nothing in the context-driven handover (§8) needs the build seat.

### 7.7 Correction to a documented gotcha

The top-level `herdr wait` genuinely does not exist in 0.7.5 (tracked as #696), but **`herdr agent wait <target> --until idle|working|blocked|done|unknown --timeout <ms>` does exist** and is the readiness primitive §7.1 step 5 uses. Scope the correction to the top-level form rather than claiming the capability is absent — and note that `agent wait` keys on an **agent NAME, not a pane id** (§7.1.2).

### 7.8 The pane-readiness failure, measured (#673)

The first live run of `perform_handoff` on this host aborted at step 4. `herdr pane split` succeeded, then `herdr agent start` refused **instantly** with:

```
{"error":{"code":"agent_pane_busy","message":"agent target pane w1:pBC is not an available shell"}}
```

The same call succeeded on the first attempt about 30 seconds later, so the condition is self-resolving: a pane herdr has just created needs a moment before a shell is available in it. All 145 of #611's tests passed beforehand because an injected runner answers instantly — the gap was between the split and the shell, which only a live run has. §7.6 had declared exactly this exposure ("not been executed against a live herdr server"), and the first live execution found a real defect in the first 30 seconds.

Two things were wrong, both fixed in #673:

- The retry did not exist. It now keys on the machine-readable `error.code`, never on the human message — the message embeds the pane id and is free to change, while the code is the contract.
- **herdr's error payload was thrown away.** The step record said `rc=1` and nothing else, so the one piece of information that identified the condition as self-resolving was invisible and diagnosing it needed a hand reproduction. The payload is now preserved on the step record.

The lesson worth keeping: a sequence whose every effect is injected in tests is verified against the *protocol*, not against the *timing*. Anything with a server on the other end needs one live run before its runbook claims it works.

## 8. Interactive mid-child session handoff (#665)

§7 crosses a **child boundary**: a child reaches a terminal outcome, the session ends, a launcher starts a fresh one, and the successor picks up the next child. This section is the other case — *"I am mid-child, out of context, hand me over and keep going"* — whose trigger is **context exhaustion, never cron** (epic #667, owner decision D-16). The 5-hour cron window is a separate service that resumes the already-active session, so it needs none of this.

Done by hand on 2026-07-27 it half-worked, and the three failures are what this exists to prevent: the goal never armed (an AUTO-MERGE run with no completion guard), the predecessor would not die (its unmet goal blocked Stop nine times, leaving the session alive and idle), and the task list did not transfer. The root cause was not a missing call — it was **no verification between steps**, because the handoff bypassed `driver_lib` entirely.

**Two commands, run by two different sessions**, because the handover and the retirement have different owners:

```bash
# the PREDECESSOR, out of context, hands over
python3 hooks/launcher_lib.py mid-child-handoff \
  --driver-state claude_docs/.driver-state/<campaign>.json \
  --anchor-pane "$HERDR_PANE_ID" --name succ-665 \
  --project-root . --cwd . \
  --registry ../../claude_docs/session_registry.jsonl \
  --transcript-dir ~/.claude/projects/<slug> \
  --issue 665 --step 8 --branch feat/665-mid-child-handoff \
  --test-baseline "5362 passed, 21 skipped, 0 failed, exit 0" \
  --project rawgentic --project-path ./projects/rawgentic \
  --repo-root "$PWD" --goal-condition-from ~/.claude/projects/<slug>/<own-session>.jsonl

# the SUCCESSOR, only after it is actually on the branch with position rebuilt
python3 hooks/launcher_lib.py retire-predecessor \
  --driver-state claude_docs/.driver-state/<campaign>.json \
  --anchor-pane w1:p1 --transcript-dir ~/.claude/projects/<slug> \
  --registry ../../claude_docs/session_registry.jsonl
```

Both default their own session id to `$CLAUDE_CODE_SESSION_ID` and refuse rather than guess when it is unset.

### 8.1 Durable mid-child position

`.driver-state` gains an optional `position` object **inside the existing `handoff_pending` object** `open_handoff` already writes — not a new top-level key and not a second file. Ten required fields: `issue`, `step`, `branch`, `test_baseline`, `predecessor_pane`, `predecessor_session`, `goal_condition`, `project`, `project_path`, `repo_root`. A partial position is worse than none, because the successor rebuilds from it and the teardown gate compares live state against it — a hole there reads as agreement. When no position is supplied the written record is byte-identical to #569's, which is the compatibility proof.

`goal_condition` is recorded **here, at handoff time, by the predecessor** rather than re-derived later. The partial-success recovery in §8.4 re-arms the predecessor's guard, and reading that condition from the *successor's* transcript would arm the predecessor with the successor's guard — silently truncated if the 4000-char cap applied. The predecessor is the only party that can record its own last unmet condition verbatim.

`project_path` joins `project` because a project *label* is not proof of a repository; nothing establishes that labels are globally unique. Both are matched. The honest bound: an exact string comparison against the value the registry's own producer writes, **not** a filesystem canonicalisation — it claims nothing about symlinks.

**A task list is deliberately not the transfer unit.** The harness task tools are session-scoped, and the live predecessor on 2026-07-27 held 30 task subjects spanning three unrelated projects. The successor rebuilds from `.driver-state` plus the position record and re-derives its own list.

### 8.2 The `kind` discriminator is a CLOSED allowlist

`handoff_pending` used to mean exactly one thing: *start the next child*. It now means two, and §7's entry point reads the same file. So the rule is an allowlist, not an equality test:

| `kind` | `handoff` (§7) behaviour |
|---|---|
| absent | legacy child-boundary handoff — proceeds exactly as before |
| `mid_child` | **REFUSED**: a mid-child resume is already in flight, and a second successor would compete for one generation |
| any other value — a misspelling, a different case, a non-string | **REFUSED** as an unrecognised handoff kind |

Equality-only matching would let `MID_CHILD` or `42` fall through to the legacy branch and launch a second successor from a record it does not understand.

An **aborted** handoff sets `cancelled: true`, and both commands refuse a cancelled record. This is not cosmetic: the record is written before the pane is split (a successor cannot claim what was never written), and until a later `open_handoff` bumps the counter the abandoned record **is** the current generation and therefore claimable, so a delayed or stray successor could otherwise take a lease on it. The cancel is monotonic — it wins before the claim is `started` and is refused after, because takeover has already happened.

**Every `.driver-state` read-modify-write in this path goes through one locked helper** holding `plan_lib.file_lock` across read → validate → atomic replace. An advisory lock only serialises writers that participate, so a lock held for the write alone would still let another writer's update land between this one's read and its replace. The lock is on a stable `<path>.lock` **sidecar**, because `flock` follows the opened inode while an atomic replace installs a new inode at the pathname. Known boundary, not implied to be solved: the epic-run skill's prose-driven status writers do **not** take this lock. That is tolerable here because the driver and the predecessor are the same session and its status writes happen at child boundaries, while a mid-child handoff by definition happens between them — one writer at a time by construction. Migrating those writers is filed as a follow-up.

### 8.3 The verification ladder — seven checks, causal order

| # | check | artifact | who proves it |
|---|---|---|---|
| 1 | `queue_revalidated` | `.driver-state`: a `queue_revalidation` receipt whose `validated_head` equals a **freshly observed** `origin/main`, with every eligible child stamped at that head and none carrying a `pending_disposition` (#840) | predecessor |
| 2 | `spawned` | `herdr pane get <new>` yields a non-empty `agent_session.value`, which the predecessor **records** into `handoff_pending.successor` | predecessor |
| 3 | `goal_armed` | successor transcript: a `goal_status` attachment with `met:false` whose condition is the one actually armed | predecessor |
| 4 | `prompt_landed` | successor transcript: the generation-bound handoff marker, matched as a plain **substring** | predecessor |
| 5 | `project_switched` | `session_registry.jsonl`: ONE line carrying the new session id **and** the recorded project **and** project_path | predecessor |
| 6 | `position_rebuilt` | a rebuild **receipt** the successor writes under the state lock, validated against the position record and the claim's own generation and claimant | successor |
| 7 | `state_claimed` | `handoff_claim` with the matching generation and claimant and `started:true` | successor |

**`queue_revalidated` is FIRST, and its producer ships with it.** The queue must be revalidated before a successor is spawned to inherit it — a successor handed a stale queue has already read the wrong issue bodies by the time a later rung could object. Its result is computed by the launcher reading the durable receipt (`produce_queue_revalidated`), never supplied by the caller: an agent asserting its own homework is exactly the vacuous pass the gate exists to prevent. It is produced at BOTH gate sites — `perform_handoff` via an explicit `campaign_context`, and `retire_predecessor`, which recomputes it because another child may have merged since the predecessor reported. A campaign carrying no receipt PASSES the rung with the reason recorded, which is a stated limit: every campaign predating #840 has no receipt, and failing them would refuse every existing mid-child handoff.

Fail-closed is unchanged from §7.4: an unreported step counts as failed — which is why the rung and its producer had to land in one commit rather than the rung alone. #611's three-step launch ladder is untouched — the mid-child ladder is a separate tuple, and each step carries an `owner`, which is load-bearing rather than documentation: a ladder carrying successor-owned checks forces predecessor-side teardown **off**, so a predecessor can never retire itself after only the four checks it can make.

**Why `prompt_landed` is a substring match.** A live probe on 2026-07-28 searched a real transcript for a phrase from a prompt pasted into a pane. It was present verbatim three times — carried in `{"type":"queue-operation","content":…}` and `{"type":"attachment","attachment":{"type":"queued_command","prompt":…}}` rows, and **not** in a `type:"user"` row. A structured match keyed on row shape would have failed every handoff, which is the same class of defect #611 shipped once with its invented `goal_status` shape. So this check asserts nothing about row shape. An empty marker is refused rather than matched, since `"" in anything` is true.

**Why the successor records its own identity check differently.** A session has no way to discover its own pane id — herdr 0.7.5 exposes no pane environment, the same fact that makes orphan reporting report-only. So the predecessor, which observed both values, writes `successor: {pane, session}` under the lock immediately after its `pane get`, and `retire-predecessor` asserts that its **own** `$CLAUDE_CODE_SESSION_ID` equals the recorded one. That is an identity binding, and it is an INTERLOCK rather than an authentication boundary — see the paragraph below on what it does and does not withstand.

**`position_rebuilt` is an ATTESTATION, and this page will not dress it up as more.** Two earlier attempts were weaker than they read. An echo of values the successor had just copied from the record proved nothing. Live `git rev-parse` readings were then **vacuous**, because the shared checkout is *already* on `position.branch` — the predecessor was working there, so a successor that did nothing at all would pass. What ships is a receipt carrying `{generation, claimant, branch_observed, repo_root_observed, step, test_baseline_observed, ts}`, written under the lock and validated **as read back**. No artifact this platform exposes would give independent proof. What the receipt does buy is bounded and real: a stale or foreign receipt fails it (generation, claimant and step are all compared on read-back), and it cannot race the ack. It is NOT proof of work: running the command writes the receipt, so what it rules out is a receipt from another generation or claimant, not a successor that rebuilt nothing. The remaining protection against premature retirement is structural — teardown is successor-driven, a separate explicit command, runnable only by the recorded successor.

`--show-toplevel` is compared **before** the branch, because a same-named branch in a different repository would otherwise satisfy the check and authorise teardown for the wrong working tree.

### 8.4 Successor-driven teardown

Teardown is the successor's, and the reason is asymmetric risk: the predecessor cannot observe whether the successor really took over, so a predecessor that retires itself on its own optimistic report is how "it would not die" becomes "it died holding the only live context". The order:

1. **Locked read, then refuse** on: a foreign `kind`, a cancelled record, an invalid position, a self-predecessor, a caller that is not the recorded successor session, or an `--anchor-pane` disagreeing with durable state. Two independent sources must agree before anything destructive runs.
2. **Claim, idempotently.** A refusal whose cause is that the claim is already **ours** for this generation is an accepted continuation. Probed: `handoff_claim` returns False for a same-claimant re-claim inside the lease *and* after `started`, so without this branch one failed teardown would block its own retry for the whole 1800 s lease. Any other refusal touches nothing.
3. **Verify** checks 1–4 from the successor's own artifacts, then the receipt.
4. **Ack**, then **gate** on all seven. Not allowed returns immediately: the predecessor is left running **and still guarded**.
5. **Prove the target's identity**: `pane get <anchor>` must still return the recorded predecessor session. A pane id is a reusable handle and syntax validation cannot detect a stale or recycled one.
6. **Re-check BOTH guards.** The successor's own guard must still be in force (and not replaced), and — added after Step 11 pass-2 — the PREDECESSOR's newest `goal_status` row must still carry the condition this handoff recorded. Without the second check a predecessor that had been re-prompted and re-armed a different guard would have that new guard cleared by a teardown never authorised to touch it. Check 2 proves a guard existed at some point after the baseline; only this proves the run is guarded *now*. A later `met:true` row fails it, and so does a **replacement** guard — a newest row for a different condition means the handed-over condition is stale, so teardown refuses. Retiring the predecessor while the continuing session is unguarded is the original defect.
7. **Re-validate everything under the lock, then persist `teardown_phase: "clearing"` BEFORE sending anything.** This fence is the one WF2 Step 8a found missing and both reviewers converged on: the entry checks are stale by now, so one locked write re-checks `kind`, `cancelled`, BOTH generations and the claimant, and sets the phase atomically. **Every destructive call re-runs this fence**, not just the first send (Step 11 pass-2 found the original fence covered only up to `send-text`, so a cancellation landing during the Enter, the confirmation poll, or a close retry stopped nothing). A cancel or a superseding generation therefore stops the teardown at whichever step it lands before, and — because every phase write is generation-scoped — the phase can never be stamped onto, or cleared from, a different generation's record. **The honest limit, since an earlier revision of this page overstated it:** a lock cannot be held across a herdr call, so a cancel arriving in the gap between this fence and the send itself is not stopped. Closing that needs a fencing token the destructive call presents — the same mechanism §8.4 lists as out of scope. The ordering is therefore: state fence → identity probe → transcript baseline → send, so the baseline is the LAST read before the transport and the residual gap is between it and the syscall. An earlier revision of this page claimed the gap was "one lock release", which was wrong — a `pane get` subprocess runs after the fence. **A phase write that does not land aborts** — proceeding would open the unguarded window with nothing on disk to find it by, which is the only reason the phase exists. Then `send-text "/goal clear"` + `send-keys Enter` with **both** return codes checked, then poll the predecessor's transcript below a baseline taken immediately before the send for a `met:true, sentinel:true` row **carrying the recorded condition**. Binding it to the condition is not decoration: Step 11 pass-3 reproduced a case where the predecessor had acquired a replacement guard, and clearing THAT produced a `met:true` row which satisfied an unbound confirmation — so the teardown reported success and closed a live pane. A zero return code proves keystrokes were transported, not that the slash command was parsed — without the semantic confirmation a silently ignored `/goal clear` reaches close-before-clear with every other check green. On timeout: `clear_unconfirmed`, and the pane is left **OPEN**.
8. **Re-prove the target, check for a RE-ARM, then close** with two bounded retries, then clear the phase. Each attempt runs, in this order and with nothing between the last two: the state fence, the identity probe, then a re-arm check — if the predecessor's newest `goal_status` row is UNMET it has armed a new guard since the clear, so it is a live guarded session again and the outcome is `predecessor_re_armed` with neither a close nor a re-arm attempted. That check fails CLOSED on an unreadable transcript (a transient read error costs an attempt, not the teardown), because refusing leaves a recoverable stall while proceeding can irreversibly destroy a live context. The identity proof from step 5 is stale by here: the clear succeeded, so the predecessor may have stopped and exited, and a pane id is a REUSABLE handle. If the pane no longer hosts the recorded session the outcome is `target_changed_after_clear` and **nothing** is done — closing would kill whoever holds it now, and re-arming would paste into them. Runner exceptions in this region (a `pane close` timeout, say) count as failed attempts rather than aborting, because aborting here skips the re-arm and leaves the predecessor unguarded while reporting a generic error.
9. **A failed Enter is not a clean abort.** If `send-text` succeeded and only `send-keys` failed, the `/goal clear` is sitting UNSUBMITTED in the predecessor's input: it is guarded now, but a later stray Enter would submit it. That is recorded as `teardown_phase: "clear_staged_unsubmitted"` and named in the reason, rather than resetting the phase and reporting "still guarded".

**The partial-success state, named.** If the clear is confirmed but the close then fails, the predecessor may be alive and **no longer guarded** — strictly worse than either failure alone. The close is retried twice; if it still fails, the predecessor is re-armed from `position.goal_condition` (its **own** recorded condition) with confirmation, reporting `alive_and_re_armed`, or `alive_and_unguarded` if the re-arm or its confirmation also fails. `alive_and_unguarded` is the one state treated as an incident.

**The one window a crash leaves the predecessor unguarded, stated plainly.** Between a *confirmed* clear and a successful close, the predecessor is alive and unguarded, and a successor dying there re-arms nothing. The window is bounded — up to three close attempts, each preceded by its own identity probe and state fence, with the clear already confirmed — and **discoverable in the normal case**, because `teardown_phase` is persisted before the clear is sent. The honest exception, found at Step 11 pass-3: phase writes are generation-scoped, so if a NEWER generation has replaced the record by the time a terminal incident is reached, the write is correctly refused and the incident exists only in the command's returned report — which now says so explicitly rather than claiming it was recorded. "Guaranteed discoverable" would be false. The consequence is a stalled run, not lost work: the branch and the context survive, and recovery is a new handoff generation. A fenced recovery *actor* would close this properly; it needs a fencing token, a claimant-liveness test and crash-injection coverage at four boundaries, and it is filed as a follow-up rather than half-built.

**Teardown authority is an INTERLOCK, not an authentication boundary — and this page previously overstated it.** `$CLAUDE_CODE_SESSION_ID` is authoritative and required, and a `--session-id` contradicting it is refused, so the accidental cases are closed: the predecessor cannot retire itself by following its own resume prompt, and an operator cannot retire the wrong session by passing the wrong id. What it does **not** do is withstand a deliberate impersonation, because a caller controls its own child's environment (`env CLAUDE_CODE_SESSION_ID=<recorded successor> …`). Two Step 11 reviewers flagged the earlier "cannot be asserted" wording as false, and they were right. No stronger claim is available here: any party able to set that variable can equally edit `.driver-state` or run `herdr pane close` directly, so there is no boundary to defend — the honest statement is that this prevents mistakes, not attacks.

**Who may complete a retirement: only the claimant.** `handoff_claim` rejects a foreign claimant inside the lease and rejects one unconditionally once `started`. An earlier revision of the design claimed any later session could finish the job; that was false and is withdrawn. The recovery path for a successor that dies mid-teardown is a **new** handoff, which bumps the generation and supersedes the stale record. Until then the predecessor is alive and still guarded — unless the successor died AFTER a confirmed clear, in which case it is alive and UNGUARDED, which is the window named above — the safe state.

### 8.5 The anti-parallel-path guard (AC7)

`tests/hooks/test_mid_child_handoff.py::TestNoParallelHandoffPath` is a **source-level drift guard**, and the claim is exactly that: it makes a second handoff path fail the suite when someone writes one in the obvious ways. It is not a proof of architectural impossibility — Python offers no such enclosure. It asserts that no `hooks/*.py` other than `launcher_lib.py` builds a herdr argv, imports or reaches the launcher's argv builders, or shells out to `herdr` through a command string; that `launcher_lib` holds exactly one `perform_handoff` and one `retire_predecessor`; that it sources the disposition, generation bump and claim/ack from `driver_lib` and defines none of them itself; and that `handoff_pending` has exactly one writer in `driver_lib`.

Its own negative cases are tested — five synthetic modules exercising each bypass form, every one of which must be flagged — because a guard that has never been shown to bite is not a guard. It is also pinned for **precision**: `herdr` is a legitimate terminal-backend *name* in three real hooks, so the scanner keys on a herdr *command* and on argv whose first element is `herdr`, not on the bare word.

## 9. Workspace, tab and pane conventions (#613)

**What the ACs assumed and what is actually running are different, so here is the live shape first.**
Read from `herdr workspace list` / `tab list` / `pane list` on this host, 2026-07-28:

```
workspace w1  label "rawgentic"   7 tabs, 8 panes, active_tab_id w1:t1
  tab w1:t1   label "rawgentic"          1 pane   (number 1)
  tab w1:t3   label "saystory"           1 pane   (number 3)
  tab w1:tY   label "sysop"              1 pane   (number 30)
  tab w1:t0   label "lumenquire"         1 pane   (number 32)
  tab w1:tA   label "herdr-dashboard"    2 panes  (number 10)
  tab w1:tN   label "model_bench"        1 pane   (number 21)
  tab w1:t14  label "thewanderinginn"    1 pane   (number 36)
```

**The convention in force is ONE workspace for the whole fleet and ONE TAB PER PROJECT** — not one
workspace per project, which is how #613's AC1 phrases it. Panes within a tab are the concurrent
runs on that project (`w1:tA` holds two). The single workspace carries the label of whichever
project is focused, which is why it reads `rawgentic` above and is not a naming rule.

Recommendation, stated as a recommendation rather than smuggled in as fact: **keep one workspace.**
A workspace-per-project layout would put the fleet behind a switch instead of a tab, and the one
operation that matters during a run — glance at every project's `agent_status` at once — is exactly
what `tab list` gives you inside a single workspace. If a second workspace ever appears, it should
mean a different *machine* or a different *epic*, never a different project.

**Per-run convention:** one pane per run, in that project's tab. A mid-child handoff (§8) splits a
second pane in the same tab and closes the first, so the pane count is the number of live runs and
a tab with two panes during a handoff is expected, not a leak.

### 9.1 Tab ids are opaque handles — never compute one

The trap, and it is visible in the table above: a tab's display `number` and its `tab_id` suffix are
**unrelated**. `number 30` is `w1:tY`; `number 10` is `w1:tA`; `number 36` is `w1:t14`. The suffix is
an opaque handle, not a decimal index, and the numbers are non-contiguous besides. Resolve a tab by
`label` or by reading `tab_id` from `tab list`; never build `w1:t<number>`.

Pane labels are **optional and sparse** — 3 of 8 panes carry one here (`lumenquire-s9`,
`Herdr Dashboard`, `claude-twi-4`). Do not rely on a label existing; `pane_id` is the only field
guaranteed present on every `PaneInfo` (that guarantee is what makes #611's fail-closed rule safe).

## 10. Attach, detach, remote (#613)

Every command below is from `herdr --help` and `herdr --default-config` on the pinned 0.7.5.

**Local attach.** `herdr` launches or attaches to the persistent session — the same command for
both, so it is safe to run when you are unsure whether a server is up. `herdr session attach <name>`
attaches a named session. `herdr status` reports both sides and is the first thing to run when
anything looks wrong:

```
client:  version 0.7.5  channel stable  protocol 17
server:  status running  version 0.7.5  protocol 17  compatible yes
         socket /home/rocky00717/.config/herdr/herdr.sock
```

`compatible: yes` is the field that matters — a client/server protocol mismatch is the failure that
looks like a hang.

**Detach** is a keybinding, not a subcommand: **`prefix+q`**. Related bindings from the shipped
default config: `prefix+?` help, `prefix+s` settings, `prefix+shift+r` reload config, `prefix+o`
open notification target. Detaching leaves every agent running — that is the point, and it is what
makes an unattended run survive your ssh session dropping.

**Remote over ssh.** `herdr --remote <ssh-target> [--session <name>]`, with
`--remote-keybindings <local|server>` (**default `local`**, so your local prefix keeps working) and
`--handoff` to opt into a live handoff on update or remote attach.

The `[remote]` config knob is worth understanding before you touch it:

- `manage_ssh_config = true` (default) — herdr runs remote ssh through a **generated** config that
  includes your `~/.ssh/config` **first** and adds `ServerAliveInterval`/`ServerAliveCountMax` as
  *fallbacks*, so keepalive values you set yourself still win. It also uses a private per-attach
  OpenSSH **control socket** to reuse the first authenticated connection.
- Setting it `false` runs plain ssh against your config unchanged. Read the wording carefully: it
  **does not force keepalive or multiplexing off** — it only stops herdr adding its own. If you set
  it false and your ssh config has no keepalive, an idle NAT will drop you.

**Server lifecycle.** `herdr server reload-config` reloads config in the running server — this is the
one to reach for, because it changes nothing about live panes. `herdr server stop` stops the server
and **takes every pane with it**; §6 and #609's vet both record why that is not a casual command on
this host, where the server routinely backs several working agents.

## 11. Rough edges, measured rather than repeated (#613)

**Idle CPU — real, but not what "10% constant" would suggest.** Server PID 8287, measured at
320,331 s elapsed (~89 hours): **cumulative average 10.9% CPU, RSS 26.5 MB**. Three instantaneous
samples two seconds apart while genuinely idle: **10.0%, 0.0%, 0.0%**. So the lifetime average is
real and worth knowing for a long-lived host, but the server does not spin at 11% while idle — the
average is dominated by the hours it spent rendering active agents. Budget for it on a shared box;
do not treat it as a fault.

**Key repeat — I could not confirm a knob exists, and say so rather than invent one.** #613's AC3
lists key repeat as a known rough edge. Grepping the shipped `herdr --default-config` for
`repeat|cpu|poll|interval|fps|tick` returns nothing relevant (only an unrelated comment about named
punctuation and the ssh `ServerAliveInterval`). **Unverified:** whether 0.7.5 exposes any key-repeat
tuning at all. What would settle it: upstream's keybinding documentation, or a live test holding a
key in an attached client and observing whether repeats coalesce. Until then, treat key repeat as an
outer-terminal setting rather than a herdr one.

**Nested herdr is disabled by default.** `[experimental] allow_nested = false` — launching herdr
from inside a herdr-managed pane is refused. This bites agents specifically: a Claude session running
*in* a pane cannot start its own herdr, so anything an agent does cross-pane must go through the
`pane`/`agent` subcommands against the existing server, which is exactly what §7 and §8 do.

**Pane history is not preserved across a server restart.** `pane_history = false` in the shipped
config. A full server restart loses recent pane screen content, so evidence you care about must be on
disk before any restart — the reason §7.4 and §8.3 verify handoffs against files and never against
scraped pane text.

**Two version-surface traps already documented elsewhere, repeated here because they cost time:** the
top-level `herdr wait` does not exist in 0.7.5 (#696) while `herdr agent wait` does (§7.7); and a
freshly split pane is not immediately an available shell, so `agent start` must retry on
`agent_pane_busy` (§7.8, #673).

## 12. Drive a run from scratch (#613 AC4)

The completeness test for this page. Every command is documented above or in §7-§8.

1. **Check the ground.** `herdr status` — require `compatible: yes`. `herdr pane list` to see what is
   already running; `herdr tab list` to find the project's tab.
2. **Attach.** `herdr` (launches or attaches). Detach whenever you like with `prefix+q`; agents keep
   running. From another machine: `herdr --remote <ssh-target>`.
3. **Find or make the project's home.** One tab per project (§9). Its `tab_id` comes from
   `tab list` — never built from the display number (§9.1).
4. **Start the run** in a pane in that tab. For an agent pane the sequence is §7.1's: split from an
   explicit anchor, `agent start` (retrying `agent_pane_busy`), `agent wait --until idle`, then paste
   with `pane send-text` + `pane send-keys Enter`.
5. **Bind the session to its project** — `/rawgentic:switch <project>` inside the new session, before
   it reads anything under `projects/`. An unbound session's `Read` is denied by `wal-bind-guard`
   with no reason text, which reads like a harness fault.
6. **While it runs:** `herdr tab list` shows every project's `agent_status` in one glance;
   `herdr pane list` narrows to panes. `agent_status: blocked` is the one that wants a human.
7. **Out of context mid-task?** That is §8 — `mid-child-handoff` from the predecessor, then
   `retire-predecessor` from the successor, never a hand-rolled pane split.
8. **Finishing up.** Close a pane with `herdr pane close <pane>`. Do not `herdr server stop` to end
   one run: it takes every pane on the host with it (§10).

**What AC4 asks that I cannot self-certify:** whether a *second operator* can drive a run from this
page alone. I wrote it, so I am the worst possible judge of whether it is sufficient for someone who
has not. Every command in it has been executed against this host and the outputs above are real, so
the claims are checkable — but the AC is only genuinely discharged the first time somebody else
follows §12 end to end without asking a question. That is the one thing to report back.
