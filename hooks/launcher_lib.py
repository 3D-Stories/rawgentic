#!/usr/bin/env python3
"""Herdr launch mode for the durable resume launcher (#611, epic #667).

WHY THIS EXISTS
---------------
A herdr-gated project routes its WF2 build seat through `herdr pane split --current`. A
cron-spawned launcher has no current pane, so a session it starts the ordinary way
(`claude --print`, pane-less) dies at its first build-seat dispatch with
`{"error":{"code":"no_current_pane"}}`. Every herdr-gated project therefore loses unattended
resumption entirely.

The fix is launcher-side and hinges on one probed fact: **`herdr pane split` accepts an
EXPLICIT pane id, not only `--current`** (read from the pinned 0.7.5 binary's `--help`).
Splitting from a named *anchor* pane needs no current pane, and the session started in the
resulting pane HAS one — so `--current` resolves normally for that session's own dispatches.
No `HerdrBackend` change is required.

HOW THE GOAL IS ARMED — and why NOT at birth
--------------------------------------------
An earlier revision armed `/goal` through `herdr agent start … -- "<goal>"` (argv at birth).
A cross-model review rejected that with upstream citations: herdr 0.7.5 rejects a native agent
argument containing a **control character** and requires a readiness timeout **> 3000 ms**
(`src/app/agents.rs#L132-L192`). A real goal condition is multiline, so argv-at-birth fails on
exactly the case the requirement exists for. (Those upstream line citations came from the
review and are NOT independently verified here — which is why this module ALSO validates
control characters locally rather than relying on herdr to do it.)

So the wired order is: start the agent WITHOUT a goal → `herdr agent wait --until idle` →
`pane send-text` + `send-keys Enter`. The send-text route is independently proven for a
2847-char / 41-newline condition, which arrives as a collapsed bracketed paste and does not
submit early (#654).

WHAT "no shell" DOES AND DOES NOT MEAN
-------------------------------------
An earlier revision of this docstring claimed "no shell ever parses the condition". That was
FALSE and the review corrected it: herdr strips its `--`, shell-quotes each element, and
submits a shell command to the pane's shell (`src/platform/linux.rs`). No injection was found
— herdr's quoting is sound — but the honest statement is narrower: **this module never builds
a shell string itself**, and the residual risks are argument/authority injection rather than
shell injection. Hence `claude_args` is allowlisted and `cwd` is confined below the project
root: a `--permission-mode` or `--config` smuggled in as a "claude arg" changes the
successor's authority, and an arbitrary cwd moves execution out of the project.

FAIL MODES
----------
**Fail-closed everywhere that matters.** Pane ids, agent names, control characters, timeouts
and cwd containment are validated fail-closed. The AC7 verification ladder treats a missing or
unreadable artifact as FAILURE, never a pass, because what it gates (predecessor teardown) is
irreversible. Mode selection returns `single_session` — matching
`driver_lib.fresh_session_available`'s established contract — rather than launching a
successor already known to be unusable.

Pure core + injected effects (`registry_prune.py` is the house exemplar): every decision
function is pure; the one function that touches the world takes its runner and readers as
parameters, so tests drive it without a herdr server.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time

# Pane ids are OPAQUE stable handles upstream, so this validates the security-relevant
# properties rather than pretending to know the grammar: non-empty, not option-shaped, no
# control characters, no whitespace, bounded length, and a conservative charset. The previous
# `^w\d+:p[0-9A-Za-z]+$` both admitted Unicode digits (`\d` matched `w١:p1`) and rejected
# plausible future handles like `wA:p1` (#611 8a review, Low).
_PANE_ID_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z:_-]{0,63}$")
# herdr 0.7.5 requires a lowercase-letter start, then lowercase/digit/-/_ only, max 32
# (upstream src/app/agents.rs#L9-L14). Mirrored so a bad name fails HERE, before a pane
# has been created, instead of after (#611 Step-11 Medium 6).
_AGENT_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")

GOAL_MAX_CHARS = 4000
_TRUNCATION_NOTE = " […truncated at the 4000-char cap…]"
_GOAL_PREFIX = "/goal "

# herdr's own documented floor is >3000 ms; mirrored locally so a bad value is rejected here
# with a clear message instead of failing inside herdr.
MIN_READINESS_TIMEOUT_MS = 3001
MAX_READINESS_TIMEOUT_MS = 300000

# `claude_args` become real Claude options, so authority-bearing flags are refused rather
# than passed through. Allowlist, not denylist: an unknown flag is refused.
# `--print` is deliberately ABSENT: it is non-interactive, and `herdr agent start`
# requires an interactive agent, so allowing it would pass local validation and then fail
# after the pane exists (#611 Step-11 Medium 6).
_ALLOWED_CLAUDE_ARGS = frozenset({"--continue", "--resume"})

# Typed launch modes replace caller-supplied `claude_args` on the wired path (#611 Step-11
# High 1). `fresh` is the mode `driver_lib.fresh_session_available` gates on: it advertises a
# no-`--resume` launch, so passing `--continue` here would silently defeat AC1.
#
# `resume` was offered here and has been REMOVED (#611 Step-11 pass-4 High 1). A resumed
# successor can carry a session id that already owns a registry row and an unmet goal row, so
# its evidence is only ever temporal — "appeared after the baseline" — never causally tied to
# THIS handoff. Binding it properly needs a nonce the successor echoes into an artifact, which
# does not exist yet. Since #569's whole contract is a FRESH successor launched with NO
# `--resume`, resume mode was never the point; removing it deletes the entire stale-evidence
# class instead of documenting around it.
LAUNCH_MODES: dict[str, tuple[str, ...]] = {"fresh": ()}

# Bounded polling. The second revision read each artifact ONCE, immediately after `send-keys`,
# which races the hooks that write them. The two budgets differ by an order of magnitude
# because the waits are different in kind: `goal_status` is hook-written within seconds of the
# paste, whereas the registry row needs the successor to run a whole `/rawgentic:switch` turn.
GOAL_POLL_ATTEMPTS = 12
GOAL_POLL_DELAY_S = 1.5
SWITCH_POLL_ATTEMPTS = 40
SWITCH_POLL_DELAY_S = 3.0

# Order is CAUSAL. The guard is armed before the successor is given work — a session handed a
# resume prompt before its goal exists is an UNGUARDED run — and the registry row cannot appear
# until the resume prompt has made it run `/rawgentic:switch`. Checking `project_switched`
# before the resume prompt was even sent (the second revision) could only pass on stale evidence.
_VERIFICATION_STEPS: tuple[dict[str, str], ...] = (
    {"step": "spawned",
     "artifact": "herdr pane get <pane> -> a non-empty agent_session.value"},
    {"step": "goal_armed",
     "artifact": "the successor transcript BELOW the pre-launch offset -> a goal_status "
                 "attachment with met:false whose condition is the one we armed"},
    {"step": "project_switched",
     "artifact": "claude_docs/session_registry.jsonl BELOW the pre-launch offset -> a line "
                 "carrying the NEW session id"},
)


class LauncherError(ValueError):
    """Any fail-closed validation refusal."""


# ---------------------------------------------------------------------------
# validation (all fail-closed)
# ---------------------------------------------------------------------------

def _reject_control_chars(value: str, what: str) -> str:
    """herdr refuses a native agent argument containing a control character, and a control
    char in a shell-submitted command is a paste hazard regardless. Checked HERE so the
    failure names the offending field instead of surfacing from inside herdr."""
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in value):
        raise LauncherError(f"{what} contains a control character; not permitted in a herdr "
                            f"agent argument (use the send-text route for multiline text)")
    return value


def validate_pane_id(pane: str) -> str:
    if not isinstance(pane, str) or not pane:
        raise LauncherError("pane id must be a non-empty string")
    if pane.startswith("-"):
        raise LauncherError(f"option-shaped pane id refused: {pane!r}")
    _reject_control_chars(pane, "pane id")
    if not _PANE_ID_RE.fullmatch(pane):
        raise LauncherError(f"malformed pane id {pane!r}")
    return pane


def validate_agent_name(name: str) -> str:
    if not isinstance(name, str) or not name:
        raise LauncherError("agent name must be a non-empty string")
    if name.startswith("-"):
        raise LauncherError(f"option-shaped agent name refused: {name!r}")
    if not _AGENT_NAME_RE.fullmatch(name):
        raise LauncherError(f"malformed agent name {name!r}")
    return name


def validate_readiness_timeout(ms: int) -> int:
    if not isinstance(ms, int) or isinstance(ms, bool):
        raise LauncherError("readiness timeout must be an int")
    if not MIN_READINESS_TIMEOUT_MS <= ms <= MAX_READINESS_TIMEOUT_MS:
        raise LauncherError(
            f"readiness timeout must be in [{MIN_READINESS_TIMEOUT_MS}, "
            f"{MAX_READINESS_TIMEOUT_MS}] ms (herdr's own floor is >3000)")
    return ms


def validate_claude_args(args) -> list[str]:
    """Allowlist. `claude_args` are genuine Claude options, so an authority-bearing flag
    (`--permission-mode`, `--config`, `--dangerously-*`) must not ride through here."""
    if args is None:
        return []
    if not isinstance(args, (list, tuple)):
        raise LauncherError("claude_args must be a list")
    out: list[str] = []
    for a in args:
        if not isinstance(a, str):
            raise LauncherError(f"claude_args elements must be strings, got {type(a).__name__}")
        _reject_control_chars(a, "claude arg")
        if a.startswith("-") and a not in _ALLOWED_CLAUDE_ARGS:
            raise LauncherError(
                f"claude arg {a!r} is not in the allowlist {sorted(_ALLOWED_CLAUDE_ARGS)} — "
                f"authority-bearing flags must not be caller-supplied")
        out.append(a)
    return out


def claude_args_for_launch_mode(mode) -> list[str]:
    """Typed launch mode -> the Claude options it implies.

    The wired path takes a MODE, never raw `claude_args`: an authority-bearing flag smuggled in
    as a "claude arg" changes the successor's authority, and a stray `--continue` on a `fresh`
    handoff silently defeats the very property `fresh_session_available` gates on.
    """
    if not isinstance(mode, str) or mode not in LAUNCH_MODES:
        raise LauncherError(
            f"unknown launch mode {mode!r} — expected one of {sorted(LAUNCH_MODES)}")
    return list(LAUNCH_MODES[mode])


def resolve_cwd(cwd: str, project_root: str) -> str:
    """Canonicalize and confine below `project_root`, so a caller cannot move the successor's
    execution out of the project (the review's argument-injection point)."""
    if not isinstance(cwd, str) or not cwd:
        raise LauncherError("cwd must be a non-empty string")
    root = os.path.realpath(project_root)
    target = os.path.realpath(cwd if os.path.isabs(cwd) else os.path.join(root, cwd))
    if target != root and not target.startswith(root + os.sep):
        raise LauncherError(f"cwd {cwd!r} resolves outside the project root {root!r}")
    return target


# ---------------------------------------------------------------------------
# argv builders — pure, always list[str], never a shell string
# ---------------------------------------------------------------------------

def build_split_argv(*, anchor_pane: str, cwd: str, project_root: str,
                     direction: str = "down") -> list[str]:
    """`herdr pane split` from an EXPLICIT anchor pane — never `--current`, which resolves
    only in a process that already owns a pane and so would break exactly the cron path."""
    validate_pane_id(anchor_pane)
    if direction not in ("down", "right"):
        raise LauncherError(f"direction must be down|right, got {direction!r}")
    return ["herdr", "pane", "split", "--pane", anchor_pane,
            "--direction", direction, "--cwd", resolve_cwd(cwd, project_root)]


def build_agent_start_argv(*, name: str, pane: str, claude_args=None,
                           readiness_timeout_ms: int = 30000) -> list[str]:
    """`herdr agent start <name> --kind claude --pane <id> --timeout <ms> [-- <args>]`.

    Deliberately carries **no goal**: see the module docstring. The goal is armed after
    readiness via send-text, because a multiline condition cannot ride in argv.
    """
    validate_agent_name(name)
    validate_pane_id(pane)
    validate_readiness_timeout(readiness_timeout_ms)
    argv = ["herdr", "agent", "start", name, "--kind", "claude", "--pane", pane,
            "--timeout", str(readiness_timeout_ms)]
    extra = validate_claude_args(claude_args)
    if extra:
        argv += ["--"] + extra
    return argv


def build_agent_wait_argv(*, target: str, until: str = "idle",
                          timeout_ms: int = 120000) -> list[str]:
    """`herdr agent wait` — the readiness gate before the goal is pasted.

    Note the top-level `herdr wait` does NOT exist in 0.7.5 (#659); `herdr agent wait` does.
    """
    validate_pane_id(target)
    if until not in ("idle", "working", "blocked", "done", "unknown"):
        raise LauncherError(f"unsupported wait state {until!r}")
    if not isinstance(timeout_ms, int) or isinstance(timeout_ms, bool) or timeout_ms <= 0:
        raise LauncherError("wait timeout must be a positive int")
    return ["herdr", "agent", "wait", target, "--until", until, "--timeout", str(timeout_ms)]


def goal_text(condition: str) -> tuple[str, bool]:
    """Build `/goal <condition>`. Returns (text, truncated) — callers MUST propagate the flag.

    The condition is read VERBATIM from the predecessor's last unmet `goal_status` row, so a
    condition that fits passes through byte-identical. The cap applies to the whole command
    including the `/goal ` prefix and the note, which is stated because a 4000-char condition
    therefore does NOT itself fit.
    """
    if not isinstance(condition, str):
        raise LauncherError(f"goal condition must be a string, got {type(condition).__name__}")
    if not condition.strip():
        raise LauncherError("goal condition is empty — refusing to arm an empty guard")
    text = f"{_GOAL_PREFIX}{condition}"
    if len(text) <= GOAL_MAX_CHARS:
        return (text, False)
    return (text[:GOAL_MAX_CHARS - len(_TRUNCATION_NOTE)] + _TRUNCATION_NOTE, True)


def armed_condition(condition: str) -> tuple[str, bool]:
    """The condition text as it will ACTUALLY be armed — i.e. after any truncation.

    This is what the successor's `goal_status` row will carry, so it is what the launch-binding
    check must compare against. Matching the caller's original text instead would fail forever
    on any capped goal, and predecessor teardown could never fire (#611 Step-11 Medium 4).
    """
    text, truncated = goal_text(condition)
    return (text[len(_GOAL_PREFIX):], truncated)


def build_send_text_argv(*, pane: str, text: str) -> tuple[list[str], list[str]]:
    """The proven paste route: `send-text` then a separate `send-keys Enter`.

    A multiline payload arrives as one collapsed bracketed paste and does not submit early
    (#654), which is why the Enter is a distinct call rather than a trailing newline.
    """
    validate_pane_id(pane)
    if not isinstance(text, str) or not text.strip():
        raise LauncherError("refusing to send empty text to the successor")
    return (["herdr", "pane", "send-text", pane, text],
            ["herdr", "pane", "send-keys", pane, "Enter"])


def build_send_text_goal_argv(*, pane: str, goal_condition: str) -> tuple[list[str], list[str], bool]:
    """The proven goal-arming route. Returns (send_text_argv, send_keys_argv, truncated).

    `truncated` is returned rather than discarded: a silently shortened goal would guard less
    than the operator supplied, which the earlier revision did and the review caught.
    """
    text, truncated = goal_text(goal_condition)
    send_text, send_keys = build_send_text_argv(pane=pane, text=text)
    return (send_text, send_keys, truncated)


def build_fallback_launch_argv(*, prompt: str, permission_mode: str,
                               wall_timeout: str | None = None) -> list[str]:
    """The retained pane-less launch (AC1 second half, AC4) — what every existing
    `*-resume.sh` already does, kept as a tested first-class builder."""
    if not isinstance(prompt, str) or not prompt:
        raise LauncherError("prompt must be a non-empty string")
    argv = ["claude", "--print", "--permission-mode", permission_mode, prompt]
    if wall_timeout:
        argv = ["timeout", wall_timeout] + argv
    return argv


def build_pane_get_argv(pane: str) -> list[str]:
    validate_pane_id(pane)
    return ["herdr", "pane", "get", pane]


def build_teardown_argv(pane: str) -> list[str]:
    validate_pane_id(pane)
    return ["herdr", "pane", "close", pane]


# ---------------------------------------------------------------------------
# mode selection — aligned with driver_lib.fresh_session_available
# ---------------------------------------------------------------------------

def select_launch_mode(*, terminal_backend: str | None, herdr_available: bool,
                       launcher_supports_herdr: bool) -> tuple[str, str]:
    """Returns ("herdr" | "pane_less" | "single_session", reason).

    The distinction the review forced: a project that is NOT herdr-gated is correctly served
    by a pane-less launch (`pane_less`). A project that IS herdr-gated but cannot get a pane
    must NOT be handed a pane-less successor — that successor is already known to die at its
    first build-seat dispatch, and retiring a viable predecessor for it is not what this repo
    means by fail-open. It returns `single_session`, matching
    `driver_lib.fresh_session_available`'s contract (keep the current loop, visible marker).
    """
    if terminal_backend != "herdr":
        return ("pane_less", f"terminal backend is {terminal_backend!r}, not herdr — a "
                             "pane-less launch is correct here")
    if not herdr_available:
        return ("single_session", "project is herdr-gated but herdr is unavailable — keeping "
                                  "the single-session loop rather than launching a successor "
                                  "that would die at its first build-seat dispatch")
    if not launcher_supports_herdr:
        return ("single_session", "project is herdr-gated but this launcher does not advertise "
                                  "herdr mode — keeping the single-session loop (#666's "
                                  "condition)")
    return ("herdr", "herdr-gated project with herdr available and launcher support")


# ---------------------------------------------------------------------------
# AC7 — real artifact readers
# ---------------------------------------------------------------------------

def parse_pane_agent_session(pane_get_stdout: str) -> str | None:
    """Pull `agent_session.value` out of a `herdr pane get` response. Returns None when
    absent/unparseable — the caller treats that as FAILURE, never a pass."""
    try:
        doc = json.loads(pane_get_stdout)
    except (ValueError, TypeError):
        return None
    node = doc.get("result", doc) if isinstance(doc, dict) else None
    if not isinstance(node, dict):
        return None
    pane = node.get("pane") if isinstance(node.get("pane"), dict) else node
    sess = pane.get("agent_session") if isinstance(pane, dict) else None
    if isinstance(sess, dict):
        value = sess.get("value")
        return value if isinstance(value, str) and value else None
    return None


def registry_has_session(registry_text: str, session_id: str) -> bool:
    """A `claude_docs/session_registry.jsonl` line carrying the NEW session id."""
    if not session_id:
        return False
    for line in registry_text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict) and rec.get("session_id") == session_id:
            return True
    return False


def transcript_has_unmet_goal(transcript_text: str, *,
                              expected_condition: str | None = None) -> bool:
    """A goal_status ATTACHMENT with `met: false` proves the guard is armed and not yet met.

    The shape is the real one, verified by reading live Claude transcripts:

        {"attachment": {"type": "goal_status", "met": false, "sentinel": true,
                        "condition": "..."}}

    An earlier revision looked for `{"goal_status": {"met": false}}` — a shape this codebase
    INVENTED. Its tests passed because they fed that same invented shape back, so the check
    would have returned False against every real transcript, `goal_armed` would never pass, and
    teardown would never fire. The regression fixture
    `tests/fixtures/herdr/goal_status_transcript.jsonl` is real-shaped so this cannot recur.

    `met: true` deliberately does NOT count: an already-satisfied guard does not prove the
    successor is guarded going forward.

    `expected_condition` binds the evidence to THIS launch (#611 Step-11 Medium 4). Without it
    any unmet goal in the file would do — and with `--continue`/`--resume` the successor can
    inherit a transcript that already contains one. Pass the ARMED form (see `armed_condition`),
    because a capped goal arms the truncated text and that is what the row will carry.
    """
    for row in _iter_goal_status(transcript_text):
        if row.get("met") is not False:
            continue
        if expected_condition is None:
            return True
        cond = row.get("condition")
        if isinstance(cond, str) and cond.strip() == expected_condition.strip():
            return True
    return False


def last_unmet_goal_condition(transcript_text: str) -> str | None:
    """The LAST unmet goal condition in a transcript, VERBATIM (#611 AC6).

    The successor's guard is armed from the predecessor's own last unmet `goal_status` row —
    never retyped, never summarised. The LAST one wins because a run can re-arm its goal, and
    only the most recent row states what is still owed. Returns None when there is nothing to
    re-arm; the caller refuses rather than inventing a condition.
    """
    found: str | None = None
    for row in _iter_goal_status(transcript_text):
        cond = row.get("condition")
        if row.get("met") is False and isinstance(cond, str) and cond.strip():
            found = cond
    return found


def _iter_goal_status(transcript_text: str):
    """Yield every `goal_status` object in a JSONL transcript, in file order.

    Line-scoped and lenient: a transcript is append-only JSONL that can end mid-write, so one
    unparseable line must never hide the rows around it.
    """
    for line in transcript_text.splitlines():
        if "goal_status" not in line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        yield from _find_goal_status(rec)


def _find_goal_status(node):
    """Recursive so a nested/reshaped record still matches, but keyed on the REAL contract:
    an object whose `type` is `goal_status`."""
    if isinstance(node, dict):
        if node.get("type") == "goal_status":
            yield node
            return
        for value in node.values():
            yield from _find_goal_status(value)
    elif isinstance(node, list):
        for value in node:
            yield from _find_goal_status(value)


def handoff_verification_steps() -> list[dict[str, str]]:
    return [dict(s) for s in _VERIFICATION_STEPS]


def evaluate_verifications(results: dict[str, bool]) -> tuple[bool, str | None, list[str]]:
    """Walk the ladder in order, stopping at the first failure. FAIL-CLOSED: a step with no
    reported result counts as FAILED — an unreported check is not evidence of success."""
    checked: list[str] = []
    for step in (s["step"] for s in _VERIFICATION_STEPS):
        checked.append(step)
        if results.get(step) is not True:
            return (False, step, checked)
    return (True, None, checked)


def teardown_allowed(results: dict[str, bool]) -> tuple[bool, str]:
    ok, failed, _ = evaluate_verifications(results)
    if not ok:
        return (False, f"refusing teardown: verification {failed!r} has not passed — the "
                       "predecessor stays alive and still guarded")
    return (True, "all handoff verifications passed — predecessor may be retired")


# ---------------------------------------------------------------------------
# the wired entry point
# ---------------------------------------------------------------------------

def _default_runner(argv: list[str], timeout: int = 180):
    return subprocess.run(argv, capture_output=True, text=True, check=False,
                          shell=False, timeout=timeout)


def herdr_available(which=shutil.which) -> bool:
    return which("herdr") is not None


def _poll_for(check, *, attempts: int, delay_s: float, sleeper) -> bool:
    """Bounded wait for an on-disk artifact. Returns False when it never appears.

    A read error mid-poll is swallowed and retried, not fatal: a JSONL file being appended to
    can momentarily fail to read, and treating that as a verdict would abort a handoff that was
    about to succeed. `UnicodeDecodeError` counts as one of those — a read that lands between
    the first and last byte of a multi-byte character raises it, and it is a `ValueError`, so
    catching `OSError` alone let it escape and kill the poll (#611 Step-11 pass-3 Low 5).
    Exhausting the budget still FAILS CLOSED — what this gates (teardown) is irreversible.
    """
    for attempt in range(attempts):
        if attempt:
            sleeper(delay_s)
        try:
            if check():
                return True
        except (OSError, UnicodeDecodeError):
            pass
    return False


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()


def _baseline(read_text, path) -> tuple[int, str] | None:
    """An artifact's state BEFORE the launch: (length, digest of that prefix).

    Returns None when no baseline could be established — the caller must then REFUSE.

    The read-failure distinction was wrong two revisions ago, which mapped every `OSError` to
    offset 0 (#611 Step-11 pass-3 High 1). "Does not exist" is expected and means zero: the
    successor's transcript genuinely does not exist until it is spawned. "Exists but could not
    be read" is entirely different — offset 0 would let the WHOLE pre-existing file count as
    this launch's evidence.

    The DIGEST is the pass-4 addition (High 1). Length alone cannot notice a file replaced at
    the same or a greater length, and the registry is legitimately replaced wholesale via
    `os.replace` by `registry_prune.py`. If a prune rewrites it mid-handoff, a positional
    offset silently points into unrelated content.
    """
    try:
        text = read_text(path)
    except FileNotFoundError:
        return (0, _digest(""))
    except (OSError, UnicodeDecodeError):
        return None
    return (len(text), _digest(text))


def _tail(text: str, baseline: tuple[int, str]) -> str | None:
    """The part of an artifact written AFTER the baseline, or None if the baseline is void.

    Void means the file is no longer an append-only extension of what we measured: shorter than
    its own baseline (truncated or rotated), or the same prefix length but different content
    (replaced). Positional evidence is meaningless then, so this returns None and the check
    fails rather than comparing against the wrong region.
    """
    offset, digest = baseline
    if len(text) < offset:
        return None
    if _digest(text[:offset]) != digest:
        return None
    return text[offset:]


def perform_handoff(*, anchor_pane: str, cwd: str, project_root: str, name: str,
                    goal_condition: str, resume_prompt: str, registry_path: str,
                    transcript_path_for, launch_mode: str = "fresh",
                    readiness_timeout_ms: int = 30000,
                    runner=_default_runner, read_text=None, sleeper=time.sleep,
                    teardown: bool = True) -> dict:
    """Execute the ordered handoff. Effects are injected so tests drive the whole sequence.

    THE ORDER, and why each position is load-bearing:

    1. split from an explicit anchor pane, 2. `agent start` (no goal — see the module
    docstring), 3. `agent wait --until idle`, 4. `pane get` for the successor's session id,
    5. **capture the pre-launch artifact offsets**, 6. paste + submit the goal, 7. **verify the
    guard actually armed**, 8. only then paste + submit the resume prompt, 9. verify the
    successor switched project, 10. retire the predecessor LAST.

    Step 8 after step 7 is the fix for #611 Step-11 High 1: the second revision armed a goal and
    stopped, so the successor sat guarded but idle — a goal only re-prompts a session that tries
    to STOP, so the run stalled silently while the predecessor had already been retired.
    Verifying before sending matters too: work handed to a session whose guard never armed is an
    UNGUARDED run.

    Ownership discipline (#611 Step-11 High 3). Once herdr has NAMED a pane it created, every
    failure path — a failed command, a failed verification, a validation exception, a runner
    timeout — best-effort closes it before returning. Cleanup is skipped only once ownership
    has actually transferred (all verifications passed).

    "Ours" means PROVEN new: herdr named it, it is not the anchor, and it was absent from the
    mandatory pre-split pane inventory. Anything else is reported and left alone, never guessed
    at (see `_report_possible_orphan`). So the honest guarantee is narrower than "every
    post-split failure closes the pane" — it is "every post-split failure either closes a pane
    proven to be ours, or names what appeared so an operator can". A returned id equal to the
    anchor or already present in the inventory takes the reporting path
    (`split_response_not_new`), and an inventory that cannot be taken at all refuses before the
    split (`pane_inventory_unavailable`).

    Every caller-supplied field — including `transcript_path_for`, which is probed — is
    validated before the split, so a bad argument cannot create a pane and then fail. The id
    herdr RETURNS is necessarily validated after it.

    Returns a dict with `ok`, `steps`, `results`, `truncated`, `failed_step`, `new_pane`,
    `session_id`, and `cleanup` (what happened to the tentative pane, if anything).
    """
    if read_text is None:
        def read_text(path):  # pragma: no cover - trivial default
            with open(path, encoding="utf-8") as fh:
                return fh.read()

    out: dict = {"ok": False, "steps": [], "results": {}, "truncated": False,
                 "failed_step": None, "new_pane": None, "session_id": None,
                 "cleanup": None}

    def record(kind, argv, proc=None, note=None):
        out["steps"].append({"kind": kind, "argv": argv,
                             "returncode": getattr(proc, "returncode", None),
                             "note": note})

    # Validate EVERYTHING first: a refusal must never leave a pane behind.
    split_argv = build_split_argv(anchor_pane=anchor_pane, cwd=cwd, project_root=project_root)
    validate_agent_name(name)
    validate_readiness_timeout(readiness_timeout_ms)
    claude_args = claude_args_for_launch_mode(launch_mode)
    goal_text(goal_condition)          # raises on a bad/blank condition before anything runs
    if not isinstance(resume_prompt, str) or not resume_prompt.strip():
        raise LauncherError("resume_prompt is empty — a guarded successor with no work would "
                            "sit idle while the predecessor is retired")
    # Exercised BEFORE the split (#611 Step-11 pass-6 Medium 2): its first real use is after the
    # pane exists, so a broken path builder would otherwise create a pane and then fail.
    probe = transcript_path_for("probe-session-id")
    if not isinstance(probe, str) or not probe:
        raise LauncherError("transcript_path_for must return a non-empty path string")
    if not os.path.isdir(os.path.dirname(probe) or "."):
        raise LauncherError(f"transcript directory {os.path.dirname(probe)!r} does not exist — "
                            "the goal_armed check could never read anything")

    # Captured BEFORE the split so nothing already on disk can be mistaken for this launch's
    # evidence (#611 Step-11 Medium 4). A registry that exists but cannot be read yields no
    # baseline at all, and without a baseline there is no such thing as fresh evidence.
    registry_baseline = _baseline(read_text, registry_path)
    if registry_baseline is None:
        out["failed_step"] = "registry_baseline_unreadable"
        return out

    # Pane inventory before the split. REQUIRED, not best-effort (#611 Step-11 pass-6 High 1):
    # it is the only thing that can later show a returned pane id is genuinely NEW. Without it,
    # a well-formed response naming a pre-existing foreign pane would be claimed as ours and
    # closed on the next failure. Refusing here is also the honest reading of the situation — if
    # `herdr pane list` does not work, herdr is not healthy enough to be splitting panes in.
    panes_before = _pane_inventory(runner)
    if panes_before is None:
        out["failed_step"] = "pane_inventory_unavailable"
        return out

    transferred = False
    split_attempted = False
    try:
        # The split runs INSIDE the ownership state machine (#611 Step-11 pass-4 Medium 4). It
        # used to run before the `try`, so a client timeout after herdr had already created the
        # pane — or a pane id that parsed but failed validation — skipped cleanup entirely.
        split_attempted = True
        proc = runner(split_argv)
        record("split", split_argv, proc)
        if getattr(proc, "returncode", 1) != 0:
            out["failed_step"] = "split"
            return out
        new_pane = _extract_pane_id(getattr(proc, "stdout", "") or "")
        if new_pane is None:
            out["failed_step"] = "split_response_unparseable"
            return out
        validate_pane_id(new_pane)
        # A well-formed response is not by itself proof of ownership (#611 Step-11 pass-5
        # High 1). If herdr ever returns the anchor, or an id that already existed before the
        # split, then claiming it as "ours" would point the cleanup branch at a live pane —
        # possibly the predecessor itself, the exact destructive outcome the report-only path
        # exists to avoid. Ownership stays unknown and the leak is reported instead.
        if new_pane == anchor_pane or new_pane in panes_before:
            out["failed_step"] = "split_response_not_new"
            return out
        out["new_pane"] = new_pane

        start_argv = build_agent_start_argv(name=name, pane=new_pane,
                                            claude_args=claude_args,
                                            readiness_timeout_ms=readiness_timeout_ms)
        proc = runner(start_argv)
        record("agent_start", start_argv, proc)
        if getattr(proc, "returncode", 1) != 0:
            out["failed_step"] = "agent_start"
            return out

        wait_argv = build_agent_wait_argv(target=new_pane, until="idle")
        proc = runner(wait_argv)
        record("agent_wait", wait_argv, proc)
        if getattr(proc, "returncode", 1) != 0:
            out["failed_step"] = "agent_wait"
            return out

        get_argv = build_pane_get_argv(new_pane)
        proc = runner(get_argv)
        record("pane_get", get_argv, proc)
        session_id = parse_pane_agent_session(getattr(proc, "stdout", "") or "")
        out["session_id"] = session_id
        out["results"]["spawned"] = bool(session_id)
        if not session_id:
            out["failed_step"] = "spawned"
            return out

        transcript_path = transcript_path_for(session_id)
        transcript_baseline = _baseline(read_text, transcript_path)
        if transcript_baseline is None:
            out["failed_step"] = "transcript_baseline_unreadable"
            return out

        text_argv, keys_argv, truncated = build_send_text_goal_argv(
            pane=new_pane, goal_condition=goal_condition)
        out["truncated"] = truncated
        for kind, argv in (("send_text", text_argv), ("send_keys", keys_argv)):
            proc = runner(argv)
            record(kind, argv, proc,
                   note="goal TRUNCATED" if truncated and kind == "send_text" else None)
            if getattr(proc, "returncode", 1) != 0:
                out["failed_step"] = kind
                return out

        expected, _ = armed_condition(goal_condition)

        def _goal_is_armed() -> bool:
            tail = _tail(read_text(transcript_path), transcript_baseline)
            return tail is not None and transcript_has_unmet_goal(
                tail, expected_condition=expected)

        out["results"]["goal_armed"] = _poll_for(
            _goal_is_armed,
            attempts=GOAL_POLL_ATTEMPTS, delay_s=GOAL_POLL_DELAY_S, sleeper=sleeper)
        if not out["results"]["goal_armed"]:
            out["failed_step"] = "goal_armed"
            return out

        # Only now — the guard is proven armed — is the successor given work.
        prompt_argv, prompt_keys = build_send_text_argv(pane=new_pane, text=resume_prompt)
        for kind, argv in (("send_resume_prompt", prompt_argv),
                           ("send_resume_keys", prompt_keys)):
            proc = runner(argv)
            record(kind, argv, proc)
            if getattr(proc, "returncode", 1) != 0:
                out["failed_step"] = "send_resume_prompt"
                return out

        def _project_switched() -> bool:
            tail = _tail(read_text(registry_path), registry_baseline)
            return tail is not None and registry_has_session(tail, session_id)

        out["results"]["project_switched"] = _poll_for(
            _project_switched,
            attempts=SWITCH_POLL_ATTEMPTS, delay_s=SWITCH_POLL_DELAY_S, sleeper=sleeper)

        ok, failed, _ = evaluate_verifications(out["results"])
        if not ok:
            out["failed_step"] = failed
            return out

        transferred = True
    except (LauncherError, OSError, subprocess.SubprocessError) as exc:
        out["failed_step"] = out["failed_step"] or f"exception: {exc}"
        return out
    finally:
        if not transferred:
            if out["new_pane"]:
                # Ownership is provable: herdr told us this pane id, so close it.
                out["cleanup"] = _close_tentative_pane(out["new_pane"], runner, record)
            elif split_attempted:
                # A pane may exist that we cannot name. Report it; never guess (see
                # `_report_possible_orphan`).
                out["cleanup"] = _report_possible_orphan(panes_before, runner, anchor_pane)

    # Teardown LAST, only when authorized, and its result is NOT ignored (Step-11 Medium 5).
    allowed, reason = teardown_allowed(out["results"])
    if teardown and allowed:
        td_argv = build_teardown_argv(anchor_pane)
        proc = runner(td_argv)
        record("teardown_predecessor", td_argv, proc, note=reason)
        if getattr(proc, "returncode", 1) != 0:
            out["failed_step"] = "teardown_predecessor"
            out["ok"] = False
            return out
    out["ok"] = True
    return out


def _pane_inventory(runner) -> set[str] | None:
    """Every pane id herdr currently knows about, or None if it could not be listed.

    Taken before the split so an uncertain split response can still be reconciled. None is a
    real answer — "no inventory" — and callers must not treat it as "no panes".
    """
    try:
        proc = runner(["herdr", "pane", "list"])
        if getattr(proc, "returncode", 1) != 0:
            return None
        doc = json.loads(getattr(proc, "stdout", "") or "")
    except (ValueError, TypeError, OSError, subprocess.SubprocessError):
        return None
    node = doc.get("result", doc) if isinstance(doc, dict) else None
    panes = node.get("panes") if isinstance(node, dict) else None
    if not isinstance(panes, list):
        return None
    return {p["pane_id"] for p in panes
            if isinstance(p, dict) and isinstance(p.get("pane_id"), str)}


def _report_possible_orphan(panes_before, runner, anchor_pane: str) -> str | None:
    """REPORT — never close — a pane the split may have created but never told us about.

    A split can create a pane and still fail: a non-zero exit after server-side creation, rc 0
    with truncated JSON, or a client timeout after the server acted. Returning without a word
    would leak one pane per cron fire silently, so this says what it sees.

    **It deliberately does not close anything, because it cannot prove ownership**
    (#611 Step-11 pass-4 High 3). An earlier revision closed the pane when the inventory diff
    contained exactly one addition — but cardinality is not attribution: if our split created
    nothing and an unrelated session split concurrently, that diff is exactly one pane and it
    is someone else's. The inventory is server-wide, so the stray pane need not even share our
    workspace. Attribution would need a token the split could stamp and a later read could
    verify; `herdr pane split` accepts `--env`, but herdr 0.7.5's `pane get`/`pane list` expose
    no environment (verified against the live server), so no such round trip exists. Closing a
    live session's pane is far worse than leaking one, so the leak is surfaced for an operator
    instead of guessed at.
    """
    if panes_before is None:
        return "no pane inventory taken before the split — cannot tell whether one leaked"
    panes_after = _pane_inventory(runner)
    if panes_after is None:
        return "pane inventory unavailable after the split — cannot tell whether one leaked"
    new = sorted(panes_after - panes_before - {anchor_pane})
    if not new:
        return None
    return (f"POSSIBLE ORPHAN: {len(new)} pane(s) appeared during a failed split "
            f"({', '.join(new)}) — NOT closed, because herdr 0.7.5 offers no way to prove "
            "which is ours; check `herdr pane list` and close by hand if orphaned")


def _close_tentative_pane(pane: str, runner, record) -> str:
    """Best-effort close of a successor pane whose ownership never transferred.

    Its own failure is RECORDED distinctly rather than raised: the handoff has already failed,
    and masking that with a cleanup error would lose the original cause.
    """
    try:
        argv = build_teardown_argv(pane)
        proc = runner(argv)
        record("cleanup_tentative_pane", argv, proc)
        if getattr(proc, "returncode", 1) != 0:
            return f"cleanup FAILED for {pane} (rc={getattr(proc, 'returncode', None)})"
        return f"closed tentative pane {pane}"
    except Exception as exc:  # pylint: disable=broad-except
        return f"cleanup errored for {pane}: {exc}"


def _extract_pane_id(stdout: str) -> str | None:
    """Strict parse of the split response. Returns None rather than guessing."""
    try:
        doc = json.loads(stdout)
    except (ValueError, TypeError):
        return None
    node = doc.get("result", doc) if isinstance(doc, dict) else None
    if not isinstance(node, dict):
        return None
    for key in ("pane_id", "id"):
        if isinstance(node.get(key), str) and node[key]:
            return node[key]
    pane = node.get("pane")
    if isinstance(pane, dict) and isinstance(pane.get("pane_id"), str):
        return pane["pane_id"]
    return None


# ---------------------------------------------------------------------------
# the production caller — driver state in, handoff out
# ---------------------------------------------------------------------------

def _driver_lib():
    """Imported lazily so `launcher_lib` stays importable on its own (and so a driver_lib
    import error surfaces at the one subcommand that needs it, not at module load)."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import driver_lib  # pylint: disable=import-outside-toplevel
    return driver_lib


def resume_prompt_for_state(state: dict) -> str | None:
    """The canonical resume prompt for the next ready child, or None when nothing is ready.

    Deliberately delegated to `driver_lib._build_resume_prompt` via `fresh_session_handoff`
    rather than written here: the successor must rebuild from durable state, and two copies of
    that wording would drift. `None` means the campaign is complete or blocked — there is
    nothing to hand off, and the caller must not spawn a successor with no work.
    """
    driver_lib = _driver_lib()
    disposition = driver_lib.fresh_session_handoff(state, mode=driver_lib.FRESH_SESSION_MODE)
    if disposition.get("outcome") != "ready":
        return None
    return disposition["resume_prompt"]


def _cmd_handoff(args) -> int:
    """The non-test caller the Step-11 review found missing.

    The workspace `*-resume.sh` launchers invoke this. They live outside any git repo
    (workspace root is not a repository), so the logic that needs tests lives here and the
    launcher is a thin call — D-11 finding 2.
    """
    driver_lib = _driver_lib()
    with open(args.driver_state, encoding="utf-8") as fh:
        state = json.load(fh)

    # The campaign's OWN mode decides whether there is a process boundary at all. Forcing
    # FRESH_SESSION_MODE here (the previous revision) would hand off for a campaign documented
    # to loop in-session — `fresh_session_handoff` returns `single_session` for exactly that
    # case, and overriding it discards the answer (#611 Step-11 pass-3 High 2).
    mode = state.get("session_mode", "single-session")
    disposition = driver_lib.fresh_session_handoff(state, mode=mode)
    if disposition.get("outcome") != "ready":
        print(f"no handoff: campaign disposition is {disposition.get('outcome')!r} "
              f"(session_mode {mode!r})")
        return 3

    # Probes are DERIVED or asserted by the launcher about itself — never hardcoded True. A
    # launcher that does not pass --launcher-armed/--fresh-launch-supported is telling us it
    # cannot do those things; absence must not read as support (the same lesson as the 8a
    # review's --launcher-herdr default).
    handoff_writable = os.access(os.path.dirname(os.path.abspath(args.driver_state)), os.W_OK)
    available, reason = driver_lib.fresh_session_available(
        state, launcher_armed=args.launcher_armed, handoff_writable=handoff_writable,
        fresh_launch_supported=args.fresh_launch_supported, launch_mode=args.herdr_mode)
    if not available:
        print(f"no handoff: {reason} (launch mode {args.herdr_mode!r})")
        return 3

    condition = args.goal_condition
    if condition is None:
        with open(args.goal_condition_from, encoding="utf-8") as fh:
            condition = last_unmet_goal_condition(fh.read())
        if condition is None:
            print("no unmet goal in the predecessor transcript — refusing to invent a "
                  "condition", file=sys.stderr)
            return 3

    transcript_dir = args.transcript_dir
    out = perform_handoff(
        anchor_pane=args.anchor_pane, cwd=args.cwd, project_root=args.project_root,
        name=args.name, goal_condition=condition,
        resume_prompt=disposition["resume_prompt"],
        registry_path=args.registry,
        transcript_path_for=lambda s: os.path.join(transcript_dir, f"{s}.jsonl"),
        launch_mode=args.launch_mode, teardown=not args.no_teardown)
    print(json.dumps({k: out[k] for k in
                      ("ok", "results", "failed_step", "new_pane", "session_id",
                       "truncated", "cleanup")}, indent=2))
    return 0 if out["ok"] else 4


# ---------------------------------------------------------------------------
# thin CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="herdr launch mode helpers (#611)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_ho = sub.add_parser("handoff", help="run the wired herdr handoff for a campaign")
    p_ho.add_argument("--driver-state", required=True,
                      help="claude_docs/.driver-state/<campaign>.json")
    p_ho.add_argument("--anchor-pane", required=True, help="the PREDECESSOR's pane id")
    p_ho.add_argument("--name", required=True, help="herdr agent name for the successor")
    p_ho.add_argument("--project-root", required=True)
    p_ho.add_argument("--cwd", required=True)
    p_ho.add_argument("--registry", required=True,
                      help="claude_docs/session_registry.jsonl")
    p_ho.add_argument("--transcript-dir", required=True,
                      help="~/.claude/projects/<slug>/ — <session-id>.jsonl lives here")
    cond = p_ho.add_mutually_exclusive_group(required=True)
    cond.add_argument("--goal-condition")
    cond.add_argument("--goal-condition-from", metavar="PREDECESSOR_TRANSCRIPT",
                      help="read the last unmet goal condition VERBATIM (AC6)")
    p_ho.add_argument("--launch-mode", default="fresh", choices=sorted(LAUNCH_MODES))
    # Only `herdr` is accepted. `pane_less` is a real verdict but it means "use the retained
    # claude --print path" (see `build-fallback`) — running the herdr split for it would split
    # a pane on a project that never wanted one, then retire the predecessor after launching
    # by a different mechanism entirely (#611 Step-11 pass-3 Medium 4).
    p_ho.add_argument("--herdr-mode", required=True, choices=("herdr",),
                      help="the verdict from `select-mode`; only 'herdr' runs this sequence")
    p_ho.add_argument("--launcher-armed", action="store_true", default=False,
                      help="the calling launcher asserts it is durably armed")
    p_ho.add_argument("--fresh-launch-supported", action="store_true", default=False,
                      help="the calling launcher asserts it can launch with NO --resume")
    p_ho.add_argument("--no-teardown", action="store_true",
                      help="verify everything but leave the predecessor running")

    p_read = sub.add_parser("read-goal-condition",
                            help="the predecessor's last unmet goal condition, verbatim (AC6)")
    p_read.add_argument("--transcript", required=True)

    # The `pane_less` half of AC1/AC4. Exposed so the non-herdr launch has an in-repo entry
    # point too — a builder with no caller is the disconnected-module smell both reviews caught.
    p_fb = sub.add_parser("build-fallback", help="argv for the retained pane-less launch")
    p_fb.add_argument("--prompt", required=True)
    p_fb.add_argument("--permission-mode", default="bypassPermissions")
    p_fb.add_argument("--wall-timeout", default=None)

    p_mode = sub.add_parser("select-mode")
    p_mode.add_argument("--terminal-backend", default=None)
    p_mode.add_argument("--herdr-available", action="store_true")
    # default FALSE: absence of an advertisement must not read as support (8a review, M-a).
    p_mode.add_argument("--launcher-herdr", dest="launcher_herdr", action="store_true",
                        default=False)

    p_split = sub.add_parser("build-split")
    p_split.add_argument("--anchor-pane", required=True)
    p_split.add_argument("--cwd", required=True)
    p_split.add_argument("--project-root", required=True)
    p_split.add_argument("--direction", default="down", choices=("down", "right"))

    sub.add_parser("verification-steps")

    p_goal = sub.add_parser("goal-text")
    p_goal.add_argument("--condition", required=True)

    args = parser.parse_args(argv)
    try:
        if args.cmd == "handoff":
            return _cmd_handoff(args)
        if args.cmd == "read-goal-condition":
            with open(args.transcript, encoding="utf-8") as fh:
                condition = last_unmet_goal_condition(fh.read())
            if condition is None:
                print("no unmet goal_status row in that transcript", file=sys.stderr)
                return 3
            print(json.dumps({"condition": condition}))
            return 0
        if args.cmd == "select-mode":
            mode, reason = select_launch_mode(
                terminal_backend=args.terminal_backend,
                herdr_available=args.herdr_available,
                launcher_supports_herdr=args.launcher_herdr)
            print(f"{mode}\t{reason}")
            return 0
        if args.cmd == "build-split":
            print(json.dumps(build_split_argv(
                anchor_pane=args.anchor_pane, cwd=args.cwd,
                project_root=args.project_root, direction=args.direction)))
            return 0
        if args.cmd == "verification-steps":
            print(json.dumps(handoff_verification_steps(), indent=2))
            return 0
        if args.cmd == "build-fallback":
            print(json.dumps(build_fallback_launch_argv(
                prompt=args.prompt, permission_mode=args.permission_mode,
                wall_timeout=args.wall_timeout)))
            return 0
        if args.cmd == "goal-text":
            text, truncated = goal_text(args.condition)
            print(json.dumps({"text": text, "truncated": truncated}))
            return 0
    except LauncherError as exc:
        print(f"launcher_lib: {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    sys.exit(main())
