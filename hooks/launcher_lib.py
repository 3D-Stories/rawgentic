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
import json
import os
import re
import shutil
import subprocess
import sys

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

_VERIFICATION_STEPS: tuple[dict[str, str], ...] = (
    {"step": "spawned",
     "artifact": "herdr pane get <pane> -> a non-empty agent_session.value"},
    {"step": "project_switched",
     "artifact": "claude_docs/session_registry.jsonl -> a line carrying the NEW session id"},
    {"step": "goal_armed",
     "artifact": "the successor transcript -> a goal_status attachment with met:false"},
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
    text = f"/goal {condition}"
    if len(text) <= GOAL_MAX_CHARS:
        return (text, False)
    return (text[:GOAL_MAX_CHARS - len(_TRUNCATION_NOTE)] + _TRUNCATION_NOTE, True)


def build_send_text_goal_argv(*, pane: str, goal_condition: str) -> tuple[list[str], list[str], bool]:
    """The proven goal-arming route. Returns (send_text_argv, send_keys_argv, truncated).

    `truncated` is returned rather than discarded: a silently shortened goal would guard less
    than the operator supplied, which the earlier revision did and the review caught.
    """
    validate_pane_id(pane)
    text, truncated = goal_text(goal_condition)
    return (["herdr", "pane", "send-text", pane, text],
            ["herdr", "pane", "send-keys", pane, "Enter"],
            truncated)


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


def transcript_has_unmet_goal(transcript_text: str) -> bool:
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
    """
    for line in transcript_text.splitlines():
        if "goal_status" not in line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if _find_unmet_goal(rec):
            return True
    return False


def _find_unmet_goal(node) -> bool:
    """Recursive so a nested/reshaped record still matches, but keyed on the REAL contract:
    an object whose `type` is `goal_status` and whose `met` is exactly False."""
    if isinstance(node, dict):
        if node.get("type") == "goal_status" and node.get("met") is False:
            return True
        return any(_find_unmet_goal(v) for v in node.values())
    if isinstance(node, list):
        return any(_find_unmet_goal(v) for v in node)
    return False


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


def perform_handoff(*, anchor_pane: str, cwd: str, project_root: str, name: str,
                    goal_condition: str, registry_path: str, transcript_path_for,
                    claude_args=None, readiness_timeout_ms: int = 30000,
                    runner=_default_runner, read_text=None,
                    teardown: bool = True) -> dict:
    """Execute the ordered handoff. Effects are injected so tests drive the whole sequence.

    Ownership discipline (#611 Step-11 High 3): once the split has created a tentative
    successor pane, EVERY failure path — a failed command, a failed verification, a validation
    exception, a runner timeout — best-effort closes that pane before returning. Otherwise
    repeated cron fires accumulate orphan panes, and a wait/send failure would leave a started
    but UNGUARDED Claude session running. Cleanup is only skipped once ownership has actually
    transferred (all verifications passed).

    Every caller-supplied field is validated BEFORE the split, so a bad argument cannot create
    a pane and then fail.

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
    validate_claude_args(claude_args)
    goal_text(goal_condition)          # raises on a bad/blank condition before anything runs

    proc = runner(split_argv)
    record("split", split_argv, proc)
    if getattr(proc, "returncode", 1) != 0:
        out["failed_step"] = "split"
        return out
    new_pane = _extract_pane_id(getattr(proc, "stdout", "") or "")
    if new_pane is None:
        out["failed_step"] = "split_response_unparseable"
        return out

    transferred = False
    try:
        validate_pane_id(new_pane)
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

        get_argv = build_pane_get_argv(new_pane)
        proc = runner(get_argv)
        record("pane_get", get_argv, proc)
        session_id = parse_pane_agent_session(getattr(proc, "stdout", "") or "")
        out["session_id"] = session_id
        out["results"]["spawned"] = bool(session_id)

        if session_id:
            try:
                out["results"]["project_switched"] = registry_has_session(
                    read_text(registry_path), session_id)
            except OSError:
                out["results"]["project_switched"] = False
            try:
                out["results"]["goal_armed"] = transcript_has_unmet_goal(
                    read_text(transcript_path_for(session_id)))
            except OSError:
                out["results"]["goal_armed"] = False

        ok, failed, _ = evaluate_verifications(out["results"])
        if not ok:
            out["failed_step"] = failed
            return out

        transferred = True
    except (LauncherError, OSError, subprocess.SubprocessError) as exc:
        out["failed_step"] = out["failed_step"] or f"exception: {exc}"
        return out
    finally:
        if not transferred and out["new_pane"]:
            out["cleanup"] = _close_tentative_pane(out["new_pane"], runner, record)

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
# thin CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="herdr launch mode helpers (#611)")
    sub = parser.add_subparsers(dest="cmd", required=True)

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
