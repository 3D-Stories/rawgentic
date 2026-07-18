# Spike #455 — headless session resume mechanics (session_id capture, resume-from-cwd, worktree cell, abnormal-exit residue)

**Date:** 2026-07-17
**Status:** SPIKE COMPLETE — report only, no production changes
**Purpose:** Prove (or refute) the mechanics that AC-E1's quota-pause recovery design (docs/planning
2026-07-17-orchestrator-executor-acceptance-criteria.md, unmerged PR #451) depends on. That doc is
**not read/edited here** beyond a read-only `git show` of the branch for context; this report's
verdict is a standalone input the doc's owner folds in later.
**Host:** claude CLI **2.1.212** (confirmed: `claude --version` → `2.1.212 (Claude Code)`), matching
the version the AC doc's F1–F14 probes were run against.
**All probes run from throwaway scratch dirs** under
`/tmp/claude-1000/-home-rocky00717-rawgentic/11d19ee3-2abf-42e3-be34-6e45299fbc5a/scratchpad/spike-455/`
— never inside this repo. Codex resume is explicitly OUT of scope (accepted: the codex adapter is
deliberately ephemeral per the AC doc's OQ-7 disposition).

## Verdict

**CONFIRMED, no CRITICAL blockers.** Session-id capture, cwd-scoped resume, and worktree-scoped
resume all work exactly as the supervisor design needs. Abnormal exit (SIGKILL) of an established,
already-persisted session leaves the session file byte-for-byte untouched and fully resumable
afterward — the interrupted turn is simply absent, not corrupting. One **doc correction** to AC
doc's F10 (not a design blocker, see below): the documented "wrong cwd silently returns a fresh
session" gotcha did NOT reproduce for `--resume <id>` — it hard-errors (exit 1, explicit stderr)
instead, which is actually a *safer* failure mode for the supervisor to detect than a silent one.
One **must-fix implementation fact**, already implied by AC-E1 but now confirmed live and by
reading the only relevant code: `phase_executor/src/phase_executor/adapters/claude_cli.py:26`
unconditionally passes `--no-session-persistence`, which — confirmed live below — makes ANY
resume categorically impossible regardless of supervisor bookkeeping. This flag must be dropped
for any seat with `session_policy: resume` before AC-E1's relaunch mechanism can work at all.

---

## 1. Adapter interaction: `--no-session-persistence` must be dropped for resumable seats

`phase_executor/src/phase_executor/adapters/claude_cli.py:26`:

```python
cmd = ["claude", "--print", "--model", model, "--output-format", "json", "--no-session-persistence"]
```

This is the **only** place in `phase_executor/src/` that touches session persistence or `--resume`
(confirmed: `grep -rn "session_policy\|no-session-persistence\|--resume\|session_id"
phase_executor/src/` matches only this one line). There is no existing `session_policy` plumbing
anywhere in the engine to build on or work around — the flag is unconditional for every dispatch
through this adapter today.

**`claude --help` (CONFIRMED, verbatim):**
```
--no-session-persistence   Disable session persistence - sessions
                            will not be saved to disk and cannot be
                            resumed (only works with --print)
```

**Live confirmation** (not taking the docstring on faith): ran
`claude --print --model sonnet --output-format json --no-session-persistence "Reply with exactly: OK"`
from a fresh scratch cwd (`probe_adapter_flag/`). Result:
- The JSON envelope still reports a `session_id` (`d9f6f8af-edb6-4fd6-bd89-0983c5211c23`).
- **No session directory was created at all** for that cwd under `~/.claude/projects/` — checked
  the exact dash-encoded slug path, confirmed absent (`ls: cannot access ... No such file or
  directory`).
- Attempting `claude -p --resume d9f6f8af-... --output-format json "test"` from the *same* cwd
  immediately after: `No conversation found with session ID: d9f6f8af-edb6-4fd6-bd89-0983c5211c23`,
  exit code 1 — identical failure signature to the cross-cwd negative control in §3.

**Interaction confirmed:** as long as `claude_cli.py:26` hardcodes `--no-session-persistence`, no
executor dispatch through this adapter can ever produce a resumable session, no matter how well the
supervisor persists `session_id`/worktree/capture-dir/command-digest per AC-E1. The flag must be
made conditional — omitted whenever the dispatched seat's capability manifest declares
`session_policy: resume` (AC-B1) — for AC-E1's `--resume` relaunch path to be reachable at all.
This is a scope-bounded code change for the wiring epic, not done here (spike is report-only).

**Does `claude -p` persist sessions by default (flag absent)?** **CONFIRMED YES.** Every probe
below that omitted `--no-session-persistence` (all of §2–§4) produced a `<session_id>.jsonl` file
under a `~/.claude/projects/<cwd-slug>/` directory without any extra flag — persistence is the
default; `--no-session-persistence` is an explicit opt-out, not something that needs opting into.

---

## 2. AC #1 — Session-id capture

Ran from `probe1_2/`:

```
$ claude -p --output-format json "Remember the codeword: sardine-42. Reply with exactly: OK"
```

Verbatim envelope (trimmed to load-bearing fields):
```json
{"type":"result","subtype":"success","is_error":false,"result":"OK",
 "session_id":"5942adc5-48ef-4ef9-b0ca-69e7b88a2efe", ...}
```
Exit code 0.

Programmatic capture (the documented `jq` pattern, shown live):
```
$ SID=$(jq -r '.session_id' cap1_out.json)
$ echo "SESSION_ID=$SID"
SESSION_ID=5942adc5-48ef-4ef9-b0ca-69e7b88a2efe
```

**CONFIRMED**: `session_id` is present in every `--output-format json` envelope (success and,
per F12 in the AC doc, error) and extractable with a one-line `jq -r '.session_id'`.

---

## 3. AC #2 — Resume-from-cwd + negative control

**Positive case**, same directory (`probe1_2/`) as capture:
```
$ claude -p --resume 5942adc5-48ef-4ef9-b0ca-69e7b88a2efe --output-format json \
    "What is the codeword? Reply with just the codeword."
```
Result: `"result":"sardine-42"`, `"session_id":"5942adc5-48ef-4ef9-b0ca-69e7b88a2efe"` (unchanged),
exit code 0.

**CONFIRMED**: the child demonstrably retained the codeword across the resumed process boundary;
`session_id` in the envelope is stable across the resume.

**Negative control — resume from a DIFFERENT cwd** (`probe2_negctrl/`, same session id):
```
$ claude -p --resume 5942adc5-48ef-4ef9-b0ca-69e7b88a2efe --output-format json \
    "What is the codeword? Reply with just the codeword, or say I DO NOT KNOW if you don't know."
EXIT_CODE=1
STDOUT: (empty)
STDERR: No conversation found with session ID: 5942adc5-48ef-4ef9-b0ca-69e7b88a2efe
```

**CONFIRMED, and this corrects a documented claim.** The AC doc's F10 states (citing
agent-sdk/sessions.md): "Resuming from the wrong cwd silently returns a FRESH session." That did
**not** reproduce for the exact invocation shape AC-E1's supervisor would use
(`claude -p --resume <id> ...`): the wrong-cwd resume **hard-errored** — exit 1, explicit
"No conversation found" on stderr, no silent fresh envelope, no output at all. This is *better*
for the supervisor's per-worktree resume discipline (F10) than the documented gotcha implied: a
loud, detectable failure is easier to build a retry/alert path around than a silent identity swap.
(Scope note: this probe tests `--resume <id>`, the mechanism AC-E1/F10 actually specify. The cited
docs source may describe a different invocation shape, e.g. `--continue`'s "most recent session in
this cwd" semantics, which was not tested here — flagging as what would need checking if F10's
exact docs source is revisited.)

Underlying mechanism (see §5): the session file lives under a directory keyed to the cwd's own
slug; a different cwd's slug directory doesn't contain that session id's `.jsonl` file at all, so
the CLI can't find it — consistent with a hard lookup-miss rather than a silent new-session
fallback.

---

## 4. AC #3 — Worktree cwd cell

Built a throwaway git repo + worktree (`probe3_repo/` init+commit, then
`git worktree add probe3_wt/ -b spike-455-wt-branch`), then ran the full capture/resume cycle
**inside the worktree**:

```
$ cd probe3_wt && claude -p --output-format json "Remember the codeword: pelican-77. Reply with exactly: OK"
→ session_id = 9a0796aa-864f-49fc-87b8-2af831c2d1ea
```

**Session file located and named** (CONFIRMED via `find ~/.claude/projects -name
"9a0796aa-....jsonl"`):
```
/home/rocky00717/.claude/projects/-tmp-claude-1000--home-rocky00717-rawgentic-11d19ee3-2abf-42e3-be34-6e45299fbc5a-scratchpad-spike-455-probe3-wt/9a0796aa-864f-49fc-87b8-2af831c2d1ea.jsonl
```
The directory slug is the worktree's own absolute path with every non-alphanumeric character
(`/` **and** `_`) replaced by `-` — distinct from the main scratch repo's own slug
(`...-probe3-repo`), confirming the worktree gets its **own** session-storage cell keyed to its
own cwd, not the main checkout's.

Resume from inside the worktree:
```
$ claude -p --resume 9a0796aa-864f-49fc-87b8-2af831c2d1ea --output-format json \
    "What is the codeword? Reply with just the codeword."
→ "result":"pelican-77", session_id unchanged, exit 0
```

**CONFIRMED**: worktree cwds get their own resume-scoped session cell exactly like any other cwd;
"a per-seat resume must execute from that seat's worktree path" (F10) is directly supported —
each engine-managed worktree (per OQ-3's converged design) is naturally its own resume cell with
no extra bookkeeping needed beyond "launch/resume this seat's dispatch from its own worktree path."

**Aside (not load-bearing, but named for completeness):** a second, differently-encoded directory
(`...-probe1_2`, underscore preserved, containing only a stray `learnings-queue.json`) was also
observed alongside the real session-storage directory for the same cwd. That is a **different**
subsystem (the claude-reflect plugin's per-project learnings queue, which slugifies differently —
preserves `_`) and is unrelated to session storage; confirmed by content (it holds
`learnings-queue.json`, never a `<uuid>.jsonl`) and is called out only so a future reader isn't
confused by two similarly-named directories per cwd.

---

## 5. AC #4 — Abnormal-exit residue (SIGKILL)

Three trials, escalating realism:

**5a. Kill a first-turn call at t=2s** (`probe4/`, prompt intentionally verbose/slow):
launched `claude -p --output-format json "Count slowly from 1 to 30..."` in the background,
captured its PID (`ps --forest` confirmed a single process, no forked children to miss),
`kill -9` at t=2s. Result: process confirmed dead (`ps -p $PID` → not found), `wait` exit status
137 (128+SIGKILL). `out.json` and `err.log` both **0 bytes**. **No session directory was created
at all** for that cwd (`find ~/.claude/projects -iname "*probe4*" -type d` → no match).

**5b. Kill a first-turn call at t=4.5s** (`probe4b/`, right before typical ~5s completion latency):
same result — 0-byte stdout/stderr, **still no session directory whatsoever**. (stderr did carry
an unrelated stdin-timing warning from not redirecting stdin — not a resume-mechanics finding.)

**CONFIRMED**: for a **first-ever turn** of a session, the session `.jsonl` file is written only
at/after the process's own completion (or at least not observably before it, at either 2s or
4.5s into a ~5s call) — SIGKILL before that point leaves **zero** residue: no partial file, no
directory, no `session_id` the supervisor could even have learned to persist. This is the one
genuinely irrecoverable case: **if the very first turn of a dispatch is killed before it prints
its JSON envelope, there is nothing to resume — the supervisor's only correct move is to treat it
as a fresh-dispatch retry, not a resume**, because no session_id was ever observable.

**5c. Kill a SUBSEQUENT resume-turn mid-flight** (the realistic quota-pause shape — a session
that's already 2 turns deep gets interrupted on turn 3), using the worktree session from §4
(`9a0796aa-...`, session file 110037 bytes / 31 lines before):
```
$ wc -c / wc -l <session-file>   # BEFORE: 110037 bytes, 31 lines
$ claude -p --resume 9a0796aa-... --output-format json "Count slowly from 1 to 30..." &
$ sleep 2; kill -9 $CPID
$ wc -c / wc -l <session-file>   # AFTER:  110037 bytes, 31 lines — byte-for-byte identical
```
`out3.json`/`err3.log` both 0 bytes (same "no output before completion" behavior as 5a/5b).

**Then re-resumed the same session** to check for corruption:
```
$ claude -p --resume 9a0796aa-... --output-format json "What is the codeword? Reply with just the codeword."
→ "result":"pelican-77", session_id unchanged, exit 0
```

**CONFIRMED**: an abnormal exit mid-resume-turn leaves the **prior, already-committed turns fully
intact and resumable** — the killed turn simply never got appended (no truncation, no corruption,
no partial/malformed line). The supervisor's persisted "session_id + worktree + capture dir +
command digest" (AC-E1) is sufficient to safely relaunch via `--resume` after any abnormal exit
**of a session that had already completed at least one turn** — the interrupted turn is retried
as if it never happened.

**What could NOT be reproduced — genuine usage-limit exit-1 (F11):** SIGKILL is a supervisor-side
signal and is not the same failure as a real provider-side usage-limit rejection. This spike did
not (and, without deliberately burning the shared quota pool, safely cannot) force an actual
`claude -p` usage-limit exit to observe its residue characteristics directly — F11's claim that
usage-limit exit is "exit code 1, no retry" is inferred from the AC doc's own cited docs
(costs.md/errors.md), not independently reproduced here.

**Residual risk (named explicitly):** it is UNCONFIRMED whether a genuine usage-limit exit-1
leaves the same "clean, unmodified session file" residue this spike observed for SIGKILL, or
whether the provider-side rejection happens at a different point in the request lifecycle (e.g.
after some partial server-side work is attributed) that could leave different residue. What would
confirm it: an intentionally-forced usage-limit condition (e.g. throttling a dedicated low-quota
test account to exhaustion) with the same before/after session-file byte-diff this spike ran for
SIGKILL — out of scope for this spike given cost/quota impact.

---

## 6. Summary of confirmed facts vs the AC doc

| AC doc claim | This spike's finding |
|---|---|
| F1 (session flags exist on 2.1.212) | CONFIRMED — `--resume`, `--session-id`, `--no-session-persistence` all present in `claude --help` on this host's 2.1.212 |
| F10 (cwd-scoped resume; wrong cwd ⇒ "silently returns a FRESH session") | Cwd-scoping CONFIRMED. "Silently fresh" **not reproduced** for `--resume <id>` — observed hard error (exit 1, explicit stderr) instead. **Doc correction, not a design blocker** — a loud failure is easier to build the supervisor's resume-identity assertion around than a silent one. |
| F11 (usage-limit exit code 1, no retry) | Not independently reproduced (see §5 residual risk); taken as inferred from AC doc's cited docs, unchanged by this spike |
| AC-E1 (quota_paused persists session-id/worktree/capture-dir/command-digest, relaunches via `--resume` from the same cwd) | Mechanically CONFIRMED workable — session-id capture, cwd-scoped resume, and worktree-scoped resume all behave as required, and an abnormal exit of an established session does not corrupt or lose prior turns. **Blocked today only by `claude_cli.py:26`'s unconditional `--no-session-persistence`** (§1) — a wiring-epic fix, not a design flaw. |

No CRITICAL findings — the mechanics AC-E1 depends on hold up under live probing.

---

## Evidence index (scratch, not committed)

All raw command transcripts for this spike live under
`/tmp/claude-1000/-home-rocky00717-rawgentic/11d19ee3-2abf-42e3-be34-6e45299fbc5a/scratchpad/spike-455/`
(`probe1_2/`, `probe2_negctrl/`, `probe3_repo/` + `probe3_wt/`, `probe4/`, `probe4b/`,
`probe_adapter_flag/`) and are cleaned up after this PR is opened, per the spike's own scratch
discipline — this report's inline quotes are the durable record.
