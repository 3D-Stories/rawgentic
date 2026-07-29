---
name: pane-handoff
description: Pass this session's work off to a fresh sibling pane — spawn it, bind it, give it the prompt, arm its goal, all gated. Use whenever the user asks to pass off, pass over, hand off or send work to another pane or session, however phrased — "pass off session in new herdr pane", "do the herdr session pane pass off", "passoff", "pass the session/prompt/goal over", "pass everything over", "send all the information over to a new pane", "send this over to a new pain", "hand it over", "hand off", "handoff", "start a new herdr pane and fix the bug", "create a new pane and resume with the prompt and goal", "clear the context into a new session and pass in the prompt and the goal", "use the herder rawgentic skill", "resume in a new pane". Dictated variants are the same request — "herder" means herdr and "pain" means pane. ALSO offer it unprompted when the context-meter reminder reaches its directive tier, because that is when the user expects the pass-off to happen. Requires HERDR_ENV=1.
argument-hint: optional — a resume-prompt file path, or nothing (the skill will ask what to hand over)
---

# Pass this session's work to a fresh pane

<role>
You spawn a guarded successor session in a new pane and hand it work. You do NOT drive herdr's
terminal primitives yourself — one tested command does the whole gated sequence, and driving it by
hand is a documented, costly mistake (`docs/runbooks/herdr.md` §7.1.2). Your job is to assemble
four inputs correctly and read the result honestly.
</role>

## What this does, in one line

Splits a new pane from yours, starts a fresh `claude` in it, then delivers three separately-verified
turns — the project bind, the work prompt, the `/goal` guard — and only reports success when the
successor's own on-disk artifacts prove each one arrived.

**It does not close your pane** unless you explicitly ask. Handing off work is not the same as
retiring yourself.

## When NOT to use it

- **Pasting text into a pane that is already running.** This skill LAUNCHES a successor. A pane that
  already exists is not a herdr-registered agent, so it has no readiness primitive and none of the
  gates below apply — that case is the hand recipe in `docs/runbooks/herdr.md` §7.1.2.
- **No herdr** (`HERDR_ENV` unset). There is no pane to hand anything to. Say so and stop.
- **Mid-campaign handoff inside an epic auto-run.** That is `mid-child-handoff`, which carries the
  driver-state generation and the successor-owned retirement; this command deliberately has neither.

## Step 1: Assemble the four inputs

**The work prompt.** If the user already has a handoff/resume prompt file, use it. If not, that
payload is what `clear-prep` produces — run it first, then come back with its resume-prompt file.
Write the prompt to a file and pass the path; never inline a long prompt.

Two hard rules about the prompt, both of which the command enforces by refusing:

1. **It must NOT contain `/rawgentic:switch`.** The bind is sent as its own verified turn, so a
   prompt that also binds makes the successor run the switch skill twice (#694).
2. **It must contain a marker unique to this handoff** — a short single-line token such as
   `[handoff-700]`, ideally its first line. This is the string that proves the prompt actually
   arrived, so a common word will not do.

**The goal condition.** What the successor still owes, in its own words. If the user has a `/goal`
already, reuse its text verbatim. Multiline is fine — put it in a file and pass the path.

**Where it runs.** Read your own binding rather than guessing:

```bash
printenv HERDR_PANE_ID CLAUDE_CODE_SESSION_ID
grep "$CLAUDE_CODE_SESSION_ID" claude_docs/session_registry.jsonl | tail -1
```

- `--anchor-pane` — `$HERDR_PANE_ID`. If it is unset, find the pane whose
  `agent_session.value` equals `$CLAUDE_CODE_SESSION_ID` in `herdr pane list`.
- `--project` / `--project-path` — the `project` and `project_path` fields of that registry line.
- `--cwd` / `--project-root` — the workspace root and the bound project's path.
- `--registry` — `<workspace root>/claude_docs/session_registry.jsonl`.
- `--transcript-dir` — `~/.claude/projects/<cwd with every "/" replaced by "-">`. For
  `/home/rocky00717/rawgentic` that is `~/.claude/projects/-home-rocky00717-rawgentic`. It must
  already exist.
- `--name` — a short herdr agent label for the successor, e.g. `rawgentic-700`.

## Step 2: Run the one command

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/launcher_lib.py" ad-hoc-handoff \
  --anchor-pane "$HERDR_PANE_ID" \
  --name <label> \
  --project <project> \
  --project-path <./projects/project> \
  --cwd <workspace root> \
  --project-root <project path> \
  --registry <workspace root>/claude_docs/session_registry.jsonl \
  --transcript-dir <transcript dir> \
  --resume-prompt-file <prompt file> \
  --goal-condition-file <goal file> \
  --prompt-marker '<the unique marker>'
```

Add `--teardown-predecessor` **only** when the user has actually asked for their own pane to be
closed. It is off by default, and it only ever fires after every verification has already passed.

Run it in the foreground. It polls real artifacts, so a slow successor can take a couple of minutes;
that is the gating working, not a hang.

## Step 3: Read the result honestly

Exit codes: `0` handed off · `2` a refused input (the message names which) · `4` the sequence ran
and a gate did not pass.

The JSON on stdout carries `results` and `failed_step`. Report what it says, not what you hoped:

| `failed_step` | What it means | What to do |
|---|---|---|
| `split` / `agent_start` / `spawned` | the successor never really came up | check `herdr pane list`; nothing was handed over |
| `project_switched` | the successor never bound the project | most often a permission-blocked successor — it cannot be fixed from here |
| `prompt_landed` | the work never reached it | the recovery already tried; do NOT re-send the text by hand |
| `goal_armed` | the guard never armed | the successor is working but unguarded |
| `send_resume_nudge` | a herdr call failed outright | herdr-side problem, not a timing one |

**On any failure your pane is still alive and still guarded, and the successor pane is cleaned up.**
Say that plainly — it is the most useful sentence in the report. A `cleanup` value naming a
POSSIBLE ORPHAN means a pane may be stranded and needs a human eye.

On success, tell the user the new pane id and session id, and that their own pane was left running
(or closed, if they asked for that).

## The one rule that must not drift

**Never assemble the delivery sequence yourself, in this skill or anywhere else.** The gated order
and the recovery for a paste that arrives intact but unsubmitted live in one tested implementation
(`perform_handoff`, reached only through the command above). Hand-rolling it is what #696 exists to
document and what #700 exists to prevent; a test fails if this file grows a raw terminal-primitive
call. The background is `docs/runbooks/herdr.md` §7.1.2.
