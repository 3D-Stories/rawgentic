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
# herdr hands back the successor's Claude session id and it is interpolated into a transcript
# path, so it is constrained to a bare token: no separators, no `..`, no control characters.
# Real ids are UUIDs; this is deliberately a little wider without admitting traversal.
_SESSION_ID_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z_-]{0,63}$")

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

# A freshly split pane is NOT immediately an available shell. Found live on 2026-07-28 (epic
# #667): the split succeeded, `agent start` refused instantly with
# `{"error":{"code":"agent_pane_busy","message":"agent target pane <id> is not an available
# shell"}}`, and the whole handoff aborted on a condition that resolves itself in about a
# second. Every test passed beforehand because an injected runner answers instantly — the gap
# was between the split and the shell, which only a live run has.
#
# So `agent start` is retried while herdr reports exactly this code, and only this code: a
# different refusal (a malformed name, a dead server) is terminal and retrying it would just
# postpone the abort. Bounded, because what follows creates a session and arms a guard.
PANE_READY_ERROR_CODE = "agent_pane_busy"
PANE_READY_ATTEMPTS = 15
PANE_READY_DELAY_S = 2.0

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

# The mid-child ladder (#665). Six checks, still CAUSAL, and every artifact is a file on disk or
# live git state — none of them reads scraped terminal output, which is why a handoff can be
# verified at all.
#
# `owner` records which side can produce each piece of evidence, and it is load-bearing rather
# than documentation: the predecessor can prove the first four about the successor it just
# launched, but the last two are the SUCCESSOR's own (a rebuild receipt and its claim). A
# predecessor-side gate that demanded all six could never pass, and — worse — a six-step ladder
# handed to `teardown_allowed` on the predecessor side would authorise a predecessor to retire
# ITSELF after four checks, which is precisely the ownership inversion approach C was rejected
# for (design §2).
_MID_CHILD_VERIFICATION_STEPS: tuple[dict[str, str], ...] = (
    {"step": "spawned", "owner": "predecessor",
     "artifact": "herdr pane get <new> -> a non-empty agent_session.value, recorded into "
                 "handoff_pending.successor so the successor can later bind its own session id "
                 "to it (a session cannot discover its own pane id, so it cannot re-derive this)"},
    {"step": "goal_armed", "owner": "predecessor",
     "artifact": "the successor transcript BELOW the pre-launch offset -> a goal_status "
                 "attachment with met:false whose condition is the one actually armed"},
    {"step": "prompt_landed", "owner": "predecessor",
     "artifact": "the successor transcript BELOW the offset -> the generation-bound handoff "
                 "marker, matched as a plain SUBSTRING: a live probe found pasted prompts "
                 "persisted in queue-operation / attachment rows, not a type:user row"},
    {"step": "project_switched", "owner": "predecessor",
     "artifact": "claude_docs/session_registry.jsonl BELOW the offset -> ONE line carrying the "
                 "NEW session id AND the recorded project AND the recorded project_path"},
    {"step": "position_rebuilt", "owner": "successor",
     "artifact": ".driver-state -> a rebuild receipt the successor writes under the state lock "
                 "carrying {generation, claimant, branch_observed, repo_root_observed, step, "
                 "ts}, validated against handoff_pending.position and against the claim's own "
                 "generation and claimant"},
    {"step": "state_claimed", "owner": "successor",
     "artifact": ".driver-state -> handoff_claim with the matching generation and claimant and "
                 "started:true"},
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


def registry_has_session(registry_text: str, session_id: str, *,
                         expected_project: str | None = None,
                         expected_project_path: str | None = None) -> bool:
    """A `claude_docs/session_registry.jsonl` line carrying the NEW session id.

    `expected_project` / `expected_project_path` are #665 additions and both default to None, so
    every #611 caller keeps its exact behaviour. When supplied, all three fields must appear on
    the SAME line: matching the session id alone let a successor bound to the WRONG project pass
    this check, claim the handoff, and retire a healthy predecessor before continuing in the
    wrong repository (design §5, pass-2 finding 2).

    The project pair is matched, not just the label, because nothing here establishes that
    project labels are globally unique. The honest bound: this is an exact string comparison
    against the same producer's representation (the switch skill writes a workspace-relative
    `./projects/<name>`), NOT a filesystem canonicalisation — it claims nothing about symlinks.
    """
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
        if not isinstance(rec, dict) or rec.get("session_id") != session_id:
            continue
        if expected_project is not None and rec.get("project") != expected_project:
            continue
        if expected_project_path is not None \
                and rec.get("project_path") != expected_project_path:
            continue
        return True
    return False


def transcript_has_marker(transcript_text: str, marker: str) -> bool:
    """The handoff marker, matched as a plain SUBSTRING over the transcript tail.

    Deliberately shape-independent, and this is the one check where that is the WHOLE point. A
    live probe on 2026-07-28 searched a real transcript for a phrase from a prompt pasted into a
    pane: present verbatim 3 times, but carried in `{"type":"queue-operation",...,"content":…}`
    and `{"type":"attachment","attachment":{"type":"queued_command","prompt":…}}` rows — never a
    `type:"user"` row with `message.content`. A structured match keyed on the row shape would
    therefore have failed EVERY handoff, which is exactly the class of defect #611 shipped once
    with its invented `goal_status` shape (its tests passed by feeding the invention back).

    So this asserts nothing about row shape. An empty marker is REFUSED rather than treated as
    absent: `"" in anything` is True, so it would silently pass on every transcript ever.
    """
    if not isinstance(marker, str) or not marker.strip():
        raise LauncherError("refusing to match an empty handoff marker — an empty substring is "
                            "present in every transcript, so it would pass unconditionally")
    return marker in (transcript_text or "")


def transcript_has_cleared_goal(transcript_text: str,
                                expected_condition: str | None = None) -> bool:
    """A `met:true, sentinel:true` goal_status row — the SEMANTIC confirmation that a
    cross-pane `/goal clear` was actually parsed and acted on.

    A zero return code from `send-text`/`send-keys` proves only that keystrokes were
    transported, not that the slash command was parsed or that the guard changed state. Without
    this reader a silently ignored `/goal clear` reaches the close-before-clear outcome the
    design forbids with every other check green (design §6 step 9, feasibility §8).

    `sentinel` is required as well as `met`: it is what distinguishes a goal-guard row from any
    other record that happens to carry a met flag.

    `expected_condition` binds the confirmation to the guard we were authorised to clear, and
    Step 11 pass-3 showed why it is not optional in the destructive path. BOTH reviewers
    reproduced the same hole: with any `met:true` row accepted, a row belonging to a DIFFERENT
    guard confirmed our clear. One walked it end to end — the predecessor acquired a replacement
    guard, clearing THAT produced its own `met:true` row, `retire_predecessor` took it as proof,
    returned `retired` and closed a live pane. The other showed the timing variant: a row landing
    between the baseline and the send confirms a clear herdr never executed. Matching the
    condition closes both, because the row must belong to the guard the handoff recorded.
    """
    for row in _iter_goal_status(transcript_text):
        if row.get("met") is not True or row.get("sentinel") is not True:
            continue
        if expected_condition is None:
            return True
        cond = row.get("condition")
        if isinstance(cond, str) and cond.strip() == expected_condition.strip():
            return True
    return False


def goal_currently_unmet(transcript_text: str, condition: str) -> bool:
    """Is the guard for `condition` STILL in force at read time?

    `transcript_has_unmet_goal` answers a strictly weaker question — "was a guard armed and
    unmet at some point after the baseline" — and the design (§5) is explicit that treating that
    as sufficient reintroduces the artifact's original failure mode: a later clear can exist
    while the historical check still passes, so the predecessor could be retired while the
    continuing session has no live guard. This reads the LAST row and answers "now".

    Scope, stated because the design doc contradicts itself here and the tests pin this side:
    "latest" is scoped to rows matching `condition`, so a row for a DIFFERENT condition does not
    decide this answer. Design §5's stricter reading — any later row, whatever its condition,
    fails — is about a *replacement* guard, which is a different question from whether THIS
    condition is still owed. That refusal therefore lives at the destructive gate in
    `retire_predecessor`, where a replacement guard means the armed condition is stale, rather
    than being folded into this predicate where it would conflate the two.
    """
    latest: dict | None = None
    for row in _iter_goal_status(transcript_text):
        cond = row.get("condition")
        if isinstance(cond, str) and cond.strip() == (condition or "").strip():
            latest = row
    return latest is not None and latest.get("met") is False


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


def _step_names(steps) -> tuple[str, ...]:
    return tuple(s["step"] for s in steps)


# The ONLY ladders that may gate anything. The third is the predecessor-owned prefix of the
# mid-child ladder — what a predecessor can legitimately prove about the successor it launched.
_PERMITTED_LADDERS: frozenset[tuple[str, ...]] = frozenset({
    _step_names(_VERIFICATION_STEPS),
    _step_names(_MID_CHILD_VERIFICATION_STEPS),
    _step_names([s for s in _MID_CHILD_VERIFICATION_STEPS if s.get("owner") != "successor"]),
})


def handoff_verification_steps() -> list[dict[str, str]]:
    return [dict(s) for s in _VERIFICATION_STEPS]


def mid_child_verification_steps() -> list[dict[str, str]]:
    """The six-step mid-child ladder (#665). A SEPARATE tuple, not a mutation of #611's three:
    that contract is pinned by its own test and a launch handoff still has exactly three checks
    to make."""
    return [dict(s) for s in _MID_CHILD_VERIFICATION_STEPS]


def _predecessor_steps(steps) -> list[dict[str, str]]:
    """The subset of a ladder the PREDECESSOR can actually produce evidence for.

    An absent `owner` means predecessor, so #611's three-step tuple is unaffected.
    """
    return [dict(s) for s in steps if s.get("owner") != "successor"]


def evaluate_verifications(results: dict[str, bool],
                           steps=None) -> tuple[bool, str | None, list[str]]:
    """Walk the ladder in order, stopping at the first failure. FAIL-CLOSED: a step with no
    reported result counts as FAILED — an unreported check is not evidence of success.

    `steps` defaults to #611's three-step launch ladder, so every existing caller and its pinned
    contract are untouched; the mid-child path passes `mid_child_verification_steps()`. The
    ladder LOGIC stays single-sourced here rather than being copied per ladder.

    **The ladder must BE one of the canonical ladders (8a correctness 4, tightened after Step 11).**
    It was first accepted as complete authority, which made an empty ladder authorise teardown with
    nothing proven. The 8a fix only required non-empty and known names — and Step 11 refuted that
    with a probe: `teardown_allowed({"spawned": True}, steps=[{"step": "spawned"}])` returned True,
    so a caller could close the predecessor having proved only that a session spawned. Names are not
    the invariant; the WHOLE ladder is. So the step sequence must match one of exactly three
    permitted tuples, which also makes a reordered or duplicated ladder impossible.
    """
    ladder = list(_VERIFICATION_STEPS if steps is None else steps)
    names = tuple(s.get("step") for s in ladder)
    if names not in _PERMITTED_LADDERS:
        raise LauncherError(
            f"refusing a non-canonical verification ladder {names!r} — a ladder must be exactly "
            "handoff_verification_steps(), mid_child_verification_steps(), or its "
            "predecessor-owned prefix. A subset, a reordering or a duplicate would authorise an "
            "irreversible teardown on less than the ladder proves")
    checked: list[str] = []
    for step in (s["step"] for s in ladder):
        checked.append(step)
        if results.get(step) is not True:
            return (False, step, checked)
    return (True, None, checked)


def teardown_allowed(results: dict[str, bool], steps=None) -> tuple[bool, str]:
    ok, failed, _ = evaluate_verifications(results, steps=steps)
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
                    transcript_dir: str, launch_mode: str = "fresh",
                    readiness_timeout_ms: int = 30000,
                    runner=_default_runner, read_text=None, sleeper=time.sleep,
                    teardown: bool = True, prompt_marker: str | None = None,
                    expected_project: str | None = None,
                    expected_project_path: str | None = None, steps=None,
                    on_successor=None) -> dict:
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

    Every caller-supplied field — including `transcript_dir`, which must already exist — is
    validated before the split, so a bad argument cannot create a pane and then fail. The pane
    id and session id herdr RETURNS are necessarily validated after it.

    `transcript_dir` is where `<session-id>.jsonl` lives (`~/.claude/projects/<slug>/`).

    #665 additions, all defaulting to the #611 behaviour when omitted: `prompt_marker` adds the
    `prompt_landed` check (the resume prompt is verified to have ARRIVED, not merely to have
    been transported with rc 0); `expected_project`/`expected_project_path` bind
    `project_switched` to the right repository; `steps` selects the ladder to gate on.

    A ladder carrying successor-owned checks forces `teardown` OFF. For a mid-child handoff the
    predecessor is the thing being retired, and retirement is the SUCCESSOR's call — letting the
    predecessor close its own pane after the four checks it can make is the ownership inversion
    approach C was rejected for, and it is how "the predecessor cannot observe whether the
    successor really took over" becomes unrecoverable.

    Returns a dict with `ok`, `steps`, `results`, `truncated`, `failed_step`, `new_pane`,
    `session_id`, `cleanup` (what happened to the tentative pane, if anything), and
    `teardown_skipped`.
    """
    if read_text is None:
        def read_text(path):  # pragma: no cover - trivial default
            with open(path, encoding="utf-8") as fh:
                return fh.read()

    out: dict = {"ok": False, "steps": [], "results": {}, "truncated": False,
                 "failed_step": None, "new_pane": None, "session_id": None,
                 "cleanup": None, "teardown_skipped": None}

    ladder = _VERIFICATION_STEPS if steps is None else steps
    gate_steps = _predecessor_steps(ladder)
    if teardown and len(gate_steps) != len(list(ladder)):
        teardown = False
        out["teardown_skipped"] = (
            "ladder carries successor-owned checks — retirement belongs to the successor "
            "(`retire-predecessor`), not to the session being retired")

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
    # #682: the bind must be the successor's FIRST act, and that is now a precondition of the
    # handoff rather than a wording convention. `project_switched` allows 120 s
    # (SWITCH_POLL_ATTEMPTS x SWITCH_POLL_DELAY_S) for the registry row to appear and then CLOSES
    # THE PANE, so a prompt that asks the successor to verify anything before binding buys a silent,
    # expensive failure: a clean-looking `failed_step`, a closed pane, and the successor's completed
    # work lost. Checked HERE with the other caller-mismatch validations, before the split, so a
    # refusal never leaves a pane behind.
    binds_first, why_not = _driver_lib().resume_prompt_binds_first(
        resume_prompt, project=expected_project)
    if not binds_first:
        raise LauncherError(f"resume_prompt does not bind first: {why_not}")
    if prompt_marker is not None:
        if not isinstance(prompt_marker, str) or not prompt_marker.strip():
            raise LauncherError("prompt_marker must be a non-empty string when supplied")
        # Checked BEFORE the split, because the failure is a caller mismatch and not a runtime
        # condition: a marker that is not in the prompt can never appear in the successor's
        # transcript, so `prompt_landed` would burn its whole poll budget and fail closed after
        # a pane, a session and an armed guard already existed.
        if prompt_marker not in resume_prompt:
            raise LauncherError(
                f"prompt_marker {prompt_marker!r} does not appear in the resume prompt — "
                "prompt_landed could never pass")
    # Validated BEFORE the split: its first real use is after the pane exists, so a bad
    # directory would otherwise create a pane and then fail. An earlier revision took a
    # `transcript_path_for` CALLBACK and probed it with an invented session id, which both
    # rejected any callback that validated its input and proved nothing about the real id
    # (#611 Step-11 pass-7 Medium 2). Taking the directory and building the path here removes
    # the callback, the sentinel, and the gap between them.
    if not isinstance(transcript_dir, str) or not transcript_dir:
        raise LauncherError("transcript_dir must be a non-empty string")
    if not os.path.isdir(transcript_dir):
        raise LauncherError(f"transcript directory {transcript_dir!r} does not exist — the "
                            "goal_armed check could never read anything")

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
        # The pane herdr just created needs a moment before it is an available shell (see
        # PANE_READY_* above — found live, not in tests). Wait for exactly that condition.
        started = False
        for attempt in range(PANE_READY_ATTEMPTS):
            if attempt:
                sleeper(PANE_READY_DELAY_S)
            proc = runner(start_argv)
            body = f"{getattr(proc, 'stdout', '') or ''}{getattr(proc, 'stderr', '') or ''}"
            busy = _is_pane_busy(proc, body)
            if getattr(proc, "returncode", 1) == 0:
                record("agent_start", start_argv, proc)
                started = True
                break
            record("agent_start", start_argv, proc,
                   note=(f"herdr refused: {PANE_READY_ERROR_CODE} — waiting for the pane's "
                         f"shell (attempt {attempt + 1}/{PANE_READY_ATTEMPTS})") if busy
                   else _error_note(body))
            if not busy:
                break
        if not started:
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

        # The session id comes from herdr and is interpolated into a path, so it is validated
        # as a bare token first — an id carrying `..` or a separator would escape the directory.
        if not _SESSION_ID_RE.fullmatch(session_id):
            out["failed_step"] = "session_id_malformed"
            return out

        # #665 — hand the observed (pane, session) pair to the caller HERE, immediately after
        # `pane get`, exactly as design §3 R4 specifies. The predecessor is the only party that
        # can see both values (a session cannot discover its own pane id), and recording them now
        # rather than after the whole sequence removes the window in which a successor that had
        # already switched project could not yet prove its own identity to `retire_predecessor`.
        # Its verdict is NOT ignored (Step 11 fence 7). If the successor's identity cannot be
        # recorded — because a concurrent handoff already superseded this generation — then this
        # successor can never prove itself to `retire_predecessor` and is unusable. Continuing
        # would arm and prompt a second session on the same child while only the other one could
        # retire the predecessor. So an unrecordable successor is a FAILED launch, and the
        # tentative pane is closed by the ownership machinery below.
        if on_successor is not None and on_successor(new_pane, session_id) is False:
            out["failed_step"] = "successor_not_recorded"
            return out
        transcript_path = os.path.join(transcript_dir, f"{session_id}.jsonl")
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

        # `prompt_landed` (#665) — rc 0 on send-text proves TRANSPORT, not arrival. Checked
        # before `project_switched` because the registry row is a downstream consequence of this
        # prompt: verifying the consequence while the cause is unproven can only pass on stale
        # evidence, which is the ordering defect #611's own ladder was rewritten to fix.
        if prompt_marker is not None:
            def _prompt_landed() -> bool:
                tail = _tail(read_text(transcript_path), transcript_baseline)
                return tail is not None and transcript_has_marker(tail, prompt_marker)

            out["results"]["prompt_landed"] = _poll_for(
                _prompt_landed,
                attempts=GOAL_POLL_ATTEMPTS, delay_s=GOAL_POLL_DELAY_S, sleeper=sleeper)
            if not out["results"]["prompt_landed"]:
                out["failed_step"] = "prompt_landed"
                return out

        def _project_switched() -> bool:
            tail = _tail(read_text(registry_path), registry_baseline)
            return tail is not None and registry_has_session(
                tail, session_id, expected_project=expected_project,
                expected_project_path=expected_project_path)

        out["results"]["project_switched"] = _poll_for(
            _project_switched,
            attempts=SWITCH_POLL_ATTEMPTS, delay_s=SWITCH_POLL_DELAY_S, sleeper=sleeper)

        ok, failed, _ = evaluate_verifications(out["results"], steps=gate_steps)
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
                # Ownership was provable at split time; `expected_session` re-checks that it still
                # is, when we got far enough to learn one.
                out["cleanup"] = _close_tentative_pane(
                    out["new_pane"], runner, record,
                    expected_session=out.get("session_id"))
            elif split_attempted:
                # A pane may exist that we cannot name. Report it; never guess (see
                # `_report_possible_orphan`).
                out["cleanup"] = _report_possible_orphan(panes_before, runner, anchor_pane)

    # Teardown LAST, only when authorized, and its result is NOT ignored (Step-11 Medium 5).
    allowed, reason = teardown_allowed(out["results"], steps=gate_steps)
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


def _is_pane_busy(proc, body: str) -> bool:
    """True only when herdr refused with its OWN `agent_pane_busy` code.

    Keyed on the machine-readable code, never on a substring of the human message: the message
    embeds the pane id and is free to change, while the code is the contract. Anything else —
    including an unparseable body — is NOT this condition, so it stays terminal.
    """
    if getattr(proc, "returncode", 1) == 0:
        return False
    try:
        doc = json.loads(body)
    except (ValueError, TypeError):
        return False
    err = doc.get("error") if isinstance(doc, dict) else None
    return isinstance(err, dict) and err.get("code") == PANE_READY_ERROR_CODE


def _error_note(body: str) -> str | None:
    """herdr's error payload, preserved on the step record.

    The live 2026-07-28 failure cost a hand reproduction to diagnose purely because this was
    thrown away: the step said `rc=1` and nothing else, so the actual code — the one thing that
    identified the condition as self-resolving — was invisible.
    """
    body = (body or "").strip()
    if not body:
        return None
    try:
        doc = json.loads(body)
        err = doc.get("error") if isinstance(doc, dict) else None
        if isinstance(err, dict):
            return f"herdr error: {err.get('code')} — {err.get('message')}"
    except (ValueError, TypeError):
        pass
    return f"herdr said: {body[:200]}"


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
    # Fail CLOSED on a malformed MEMBER, not just malformed whole output (#611 Step-11 pass-7
    # High 1). Silently dropping an unparseable record yields a short inventory that still looks
    # authoritative — and a pane missing from it reads as "new", which is exactly the licence to
    # close a foreign pane. A partial inventory is not an inventory.
    out: set[str] = set()
    for pane in panes:
        if not isinstance(pane, dict) or not isinstance(pane.get("pane_id"), str) \
                or not pane["pane_id"]:
            return None
        out.add(pane["pane_id"])
    return out


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


def _close_tentative_pane(pane: str, runner, record, expected_session: str | None = None) -> str:
    """Best-effort close of a successor pane whose ownership never transferred.

    Its own failure is RECORDED distinctly rather than raised: the handoff has already failed,
    and masking that with a cleanup error would lose the original cause.

    `expected_session` (#665, Step 11 pass-2 adversarial 3) is checked when known: proof that the
    id was NEW at split time is not proof that it is still ours at cleanup time, because this
    module treats pane handles as reusable everywhere else. If a fresh `pane get` shows a different
    session, the pane is reported rather than closed. When the failure happened before any session
    was established there is nothing to compare against, and the honest bound is the pre-split
    inventory alone — herdr 0.7.5 exposes no creation token that would close that gap.
    """
    try:
        if expected_session is not None:
            probe = build_pane_get_argv(pane)
            proc = runner(probe)
            record("cleanup_identity_probe", probe, proc)
            live = parse_pane_agent_session(getattr(proc, "stdout", "") or "")
            if getattr(proc, "returncode", 1) != 0 or live != expected_session:
                return (f"NOT closed {pane}: it no longer provably hosts {expected_session!r} "
                        f"(saw {live!r}) — the handle may have been reused, and closing it could "
                        "kill an unrelated session; check `herdr pane list`")
        argv = build_teardown_argv(pane)
        proc = runner(argv)
        record("cleanup_tentative_pane", argv, proc)
        if getattr(proc, "returncode", 1) != 0:
            return f"cleanup FAILED for {pane} (rc={getattr(proc, 'returncode', None)})"
        return f"closed tentative pane {pane}"
    except Exception as exc:  # pylint: disable=broad-except
        return f"cleanup errored for {pane}: {exc}"


def latest_goal_status_condition(transcript_text: str) -> str | None:
    """The condition of the LAST goal_status row, whatever that condition is.

    This is the half of design §5 that `goal_currently_unmet` deliberately does not carry: if
    the newest guard in the transcript belongs to a DIFFERENT condition, then the condition we
    armed has been replaced, and a replaced guard is a retired one. Teardown must refuse, because
    the run is no longer guarded by what was handed over. Returns None when there is no
    goal_status row at all.
    """
    found: str | None = None
    for row in _iter_goal_status(transcript_text):
        cond = row.get("condition")
        if isinstance(cond, str) and cond.strip():
            found = cond
    return found


# ---------------------------------------------------------------------------
# #665 — the ONE locked driver-state writer
# ---------------------------------------------------------------------------

def _plan_lib():
    """Lazy, same direction as `_driver_lib`, so `launcher_lib` stays importable alone."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import plan_lib  # pylint: disable=import-outside-toplevel
    return plan_lib


def _atomic_write(path: str, text: str) -> None:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import atomic_write_lib  # pylint: disable=import-outside-toplevel
    atomic_write_lib.atomic_write_text(path, text)


def _locked_state_read(path: str) -> dict:
    """Read driver state while holding the same lock every writer here takes."""
    with _plan_lib().file_lock(path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)


def _locked_state_update(path: str, mutate):
    """The ONE locked read -> validate -> atomic-replace cycle for driver state (#665).

    `mutate(state)` returns the new state, or None to abort the write. The lock is held across
    the WHOLE cycle, which is the point: an advisory lock only serialises writers that
    participate, so a lock held for the write alone would still let another writer's update land
    between this one's read and its replace, silently erasing a claim, an ack, a generation bump
    or the position record.

    `plan_lib.file_lock` locks a stable SIDECAR (`<path>.lock`) rather than the state file, and
    that is load-bearing here: `flock` follows the opened inode while `atomic_write_text`
    installs a NEW inode at the pathname, so a lock taken on the target itself would let two
    waiters hold locks on different inodes and interleave anyway.
    """
    with _plan_lib().file_lock(path):
        with open(path, encoding="utf-8") as fh:
            state = json.load(fh)
        new = mutate(state)
        if new is None:
            return None
        _atomic_write(path, json.dumps(new, indent=2) + "\n")
        return new


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
# #665 — successor-driven predecessor teardown
# ---------------------------------------------------------------------------

HANDOFF_LEASE_S = 1800
CLOSE_ATTEMPTS = 3          # the first close plus two bounded retries
CLOSE_RETRY_DELAY_S = 2.0
_CLEAR_COMMAND = "/goal clear"


def retire_predecessor(*, driver_state_path: str, session_id: str, anchor_pane: str,
                       transcript_dir: str, registry_path: str,
                       runner=_default_runner, read_text=None, sleeper=time.sleep,
                       now_ts: int | None = None, lease_s: int = HANDOFF_LEASE_S) -> dict:
    """Retire the predecessor, run BY THE SUCCESSOR. The only destructive path in #665.

    Teardown is successor-driven for an asymmetric-risk reason (design §2, approach C): the
    predecessor cannot observe whether the successor really took over, so a predecessor that
    retires itself on its own optimistic report is how "the predecessor would not die" becomes
    "the predecessor died while holding the only live context".

    The order is fixed and every position in it is load-bearing:

    1-2. locked read, then refuse on `kind`, `cancelled`, an invalid position, a self-predecessor,
         a caller that is not the RECORDED successor session, or an `--anchor-pane` that disagrees
         with durable state. Two independent sources must agree before anything destructive runs.
    3.   claim, idempotently — a refusal whose cause is that the claim is already OURS for this
         generation is a continuation, not a failure. Probed live: `handoff_claim` returns False
         for a same-claimant re-claim inside the lease AND after `started`, so without this branch
         one failed teardown would block its own retry for the whole 1800 s lease.
    4.   verify the four launch checks from the successor's OWN artifacts (never a report handed
         to it), then `position_rebuilt` from live git state in the recorded repository.
    5-6. ack, then gate on all six. Not allowed returns now, predecessor alive AND still guarded.
    7.   prove the target's identity: `pane get` must still return the recorded predecessor
         session. A pane id is a reusable handle and syntax validation cannot detect a recycled one.
    8.   re-check that OUR guard is still in force. Step 4's `goal_armed` proves a guard existed at
         some point; only this proves the run is guarded NOW, and retiring the predecessor while
         the continuing session is unguarded is the original defect.
    9.   persist `teardown_phase` BEFORE sending anything, then clear, then CONFIRM semantically.
         A zero return code proves keystrokes were transported, not that the command was parsed.
    10.  close, bounded retries, then re-arm from the predecessor's OWN recorded condition if the
         close never succeeds.

    `outcome` is one of: `retired`, `refused`, `claim_refused`, `teardown_refused`,
    `clear_failed`, `clear_unconfirmed`, `alive_and_re_armed`, `alive_and_unguarded`, `error`.
    Only `retired` sets `ok`.
    """
    if read_text is None:
        def read_text(path):  # pragma: no cover - trivial default
            with open(path, encoding="utf-8") as fh:
                return fh.read()

    now = int(time.time()) if now_ts is None else now_ts
    out: dict = {"ok": False, "outcome": None, "reason": "", "results": {}, "steps": [],
                 "failed_step": None, "generation": None}

    def record(kind, argv, proc=None, note=None):
        out["steps"].append({"kind": kind, "argv": argv,
                             "returncode": getattr(proc, "returncode", None), "note": note})

    def refuse(reason: str) -> dict:
        out["outcome"] = "refused"
        out["reason"] = reason
        return out

    def read_or_empty(path: str) -> str:
        """Fail CLOSED: an unreadable artifact yields no evidence, so its check fails."""
        try:
            return read_text(path)
        except (OSError, UnicodeDecodeError):
            return ""

    def _run(argv, kind, note=None):
        """Run one command in the destructive region, converting a runner EXCEPTION into a failed
        attempt rather than an abort (8a destructive 4).

        `_default_runner` can raise `subprocess.TimeoutExpired`. Letting that propagate meant a
        close that timed out AFTER a confirmed clear jumped straight to the outer handler, skipping
        the remaining bounded retries AND the entire re-arm — leaving the predecessor alive and
        unguarded while reporting a generic `error` rather than `alive_and_unguarded`. A timeout is
        also genuinely ambiguous about whether the server acted, so it must be treated as "this
        attempt did not demonstrably succeed", never as "stop".
        """
        try:
            proc = runner(argv)
        except (OSError, subprocess.SubprocessError) as exc:
            record(kind, argv, None, note=f"runner raised {type(exc).__name__}: {exc}")
            return None
        record(kind, argv, proc, note=note)
        return proc

    try:
        driver_lib = _driver_lib()
        state = _locked_state_read(driver_state_path)

        # --- 1-2. derive and sanity-check, before anything can be claimed or destroyed ---
        pend = state.get("handoff_pending")
        if not isinstance(pend, dict):
            return refuse("no handoff_pending record in driver state — nothing to retire")
        if pend.get("kind") != driver_lib.MID_CHILD_HANDOFF_KIND:
            return refuse(f"handoff_pending.kind is {pend.get('kind')!r}, not "
                          f"{driver_lib.MID_CHILD_HANDOFF_KIND!r} — this command retires a "
                          "mid-child predecessor only")
        if pend.get("cancelled") is True:
            return refuse("handoff_pending is cancelled — the handoff was aborted, so no guard "
                          "may be cleared and no pane may be closed")
        position = pend.get("position")
        ok_pos, errors = driver_lib.validate_mid_child_position(position)
        if not ok_pos:
            return refuse("invalid position record: " + "; ".join(errors))
        if position["predecessor_session"] == session_id:
            return refuse("refusing: this session is recorded as its own predecessor — a session "
                          "is never its own predecessor")
        successor = pend.get("successor")
        recorded_session = successor.get("session") if isinstance(successor, dict) else None
        if not isinstance(recorded_session, str) or recorded_session != session_id:
            return refuse(f"this session ({session_id!r}) is not the recorded successor "
                          f"({recorded_session!r}) — only the intended successor may retire the "
                          "predecessor")
        if anchor_pane != position["predecessor_pane"]:
            return refuse(f"--anchor-pane {anchor_pane!r} disagrees with the recorded "
                          f"predecessor_pane {position['predecessor_pane']!r} — two independent "
                          "sources must agree before anything destructive happens")
        validate_pane_id(anchor_pane)
        generation = pend.get("generation")
        if not isinstance(generation, int) or isinstance(generation, bool):
            return refuse("handoff_pending.generation is not an int — refusing to claim")
        out["generation"] = generation

        # --- 3. claim, idempotently, with the decision taken INSIDE the lock ---
        claim_state: dict = {}

        def _claim(s):
            claimed, new = driver_lib.handoff_claim(
                s, generation, claimant=session_id, now_ts=now, lease_s=lease_s)
            if claimed:
                claim_state["verdict"] = "claimed"
                return new
            held = s.get("handoff_claim")
            if isinstance(held, dict) and held.get("generation") == generation \
                    and held.get("claimant") == session_id:
                claim_state["verdict"] = "already_ours"
                return None
            claim_state["verdict"] = "refused"
            return None

        _locked_state_update(driver_state_path, _claim)
        if claim_state.get("verdict") == "refused":
            out["outcome"] = "claim_refused"
            out["reason"] = ("could not claim generation "
                             f"{generation} — a foreign or live claim holds it; the run continues "
                             "in place and the predecessor stays alive and guarded")
            return out

        # --- 4. verify from the SUCCESSOR's own artifacts ---
        armed, _ = armed_condition(position["goal_condition"])
        own_transcript = os.path.join(transcript_dir, f"{session_id}.jsonl")
        # `spawned` is the R4 identity binding, already established above: the predecessor
        # observed both values and recorded them, because a session cannot discover its own pane.
        out["results"]["spawned"] = True
        own_text = read_or_empty(own_transcript)
        out["results"]["goal_armed"] = transcript_has_unmet_goal(
            own_text, expected_condition=armed)
        marker = driver_lib.mid_child_marker(position["issue"], generation)
        out["results"]["prompt_landed"] = transcript_has_marker(own_text, marker)
        out["results"]["project_switched"] = registry_has_session(
            read_or_empty(registry_path), session_id,
            expected_project=position["project"],
            expected_project_path=position["project_path"])

        launch_ladder = _predecessor_steps(mid_child_verification_steps())
        ok_early, failed_early, _ = evaluate_verifications(out["results"], steps=launch_ladder)
        if not ok_early:
            out["outcome"] = "teardown_refused"
            out["failed_step"] = failed_early
            out["reason"] = (f"refusing teardown: verification {failed_early!r} has not passed — "
                             "the predecessor stays alive and still guarded, and no claim is "
                             "acked so a later generation can still take over cleanly")
            return out

        def _git(*rev_args) -> str | None:
            argv = ["git", "-C", position["repo_root"], "rev-parse", *rev_args]
            proc = runner(argv)
            record("git", argv, proc)
            if getattr(proc, "returncode", 1) != 0:
                return None
            return ((getattr(proc, "stdout", "") or "").strip() or None)

        # `--show-toplevel` is compared BEFORE the branch: a same-named branch in a DIFFERENT
        # repository would otherwise satisfy this check and authorise teardown for the wrong tree.
        toplevel = _git("--show-toplevel")
        branch = _git("--abbrev-ref", "HEAD")
        rebuilt = bool(toplevel and branch
                       and toplevel == position["repo_root"]
                       and branch == position["branch"])
        if toplevel and branch:
            receipt = {"generation": generation, "claimant": session_id,
                       "branch_observed": branch, "repo_root_observed": toplevel,
                       "step": position["step"],
                       # #665's AC4 addendum on the issue says the receipt echoes the WF2 step AND
                       # the recorded test baseline. `step` was carried; the baseline was not, so
                       # the acceptance record and the code disagreed (Step 11 pass-3, gate 3).
                       "test_baseline_observed": position["test_baseline"], "ts": now}

            def _write_receipt(s):
                p = s.get("handoff_pending")
                if not isinstance(p, dict) or p.get("generation") != generation:
                    return None
                new = dict(s)
                new["handoff_pending"] = {**p, "rebuild_receipt": receipt}
                return new

            _locked_state_update(driver_state_path, _write_receipt)
            # Validate the receipt as READ BACK, not as written: that is what makes a stale or
            # foreign receipt (an earlier generation's, another claimant's) fail rather than
            # being taken on trust. The attestation is honest about its limit — it proves
            # something was written by THIS claimant for THIS generation, not that a rebuild
            # happened; §5 states that plainly.
            back = _locked_state_read(driver_state_path).get("handoff_pending", {})
            got = back.get("rebuild_receipt") if isinstance(back, dict) else None
            rebuilt = rebuilt and isinstance(got, dict) \
                and got.get("generation") == generation \
                and got.get("claimant") == session_id \
                and got.get("branch_observed") == position["branch"] \
                and got.get("repo_root_observed") == position["repo_root"] \
                and got.get("step") == position["step"] \
                and got.get("test_baseline_observed") == position["test_baseline"]
        out["results"]["position_rebuilt"] = rebuilt

        # --- 5. ack ---
        # 8a correctness 2: gate the PREDECESSOR-owned checks BEFORE persisting a receipt or
        # acking a claim. A claim marked `started` is never reclaimable, so acking on evidence
        # that already failed strands the takeover until someone opens a new generation by hand.
        def _ack(s):
            acked, new = driver_lib.handoff_ack_started(s, generation, session_id)
            return new if acked else None

        out["results"]["state_claimed"] = _locked_state_update(driver_state_path,
                                                               _ack) is not None

        # --- 6. gate on all six ---
        allowed, reason = teardown_allowed(out["results"],
                                          steps=mid_child_verification_steps())
        if not allowed:
            _, failed, _ = evaluate_verifications(out["results"],
                                                  steps=mid_child_verification_steps())
            out["outcome"] = "teardown_refused"
            out["failed_step"] = failed
            out["reason"] = reason
            return out

        # --- 7. prove the target's identity before touching it ---
        # 8a destructive 2: the return code is checked as well as the payload. A parseable stdout
        # from a FAILED command was previously enough to satisfy this, and this is the check that
        # stands between us and closing a stranger's pane.
        def _anchor_hosts_predecessor() -> tuple[bool, str]:
            # Routed through `_run` (Step 11 fence 5): this probe also runs AFTER the clear, and a
            # `TimeoutExpired` raised here used to escape to the outer handler — skipping the close
            # AND the re-arm entirely and reporting a generic `error` while the predecessor was
            # alive and unguarded. An unprovable target is a refusal, never an abort.
            # Tri-state, not a bool (Step 11 pass-2): a TRANSIENT probe failure and a PROVEN
            # mismatch demand opposite responses. Collapsing them meant one timed-out probe after a
            # confirmed clear abandoned every remaining close retry and the re-arm, leaving a live
            # predecessor unguarded when the next probe would have succeeded.
            get_argv = build_pane_get_argv(anchor_pane)
            proc = _run(get_argv, "pane_get_anchor")
            if proc is None:
                return ("unavailable", f"`herdr pane get {anchor_pane}` did not complete (the "
                                       "runner raised), so the target is unproven right now")
            if getattr(proc, "returncode", 1) != 0:
                return ("unavailable", f"`herdr pane get {anchor_pane}` returned "
                                       f"rc={getattr(proc, 'returncode', None)} — the target is "
                                       "unreadable right now")
            live = parse_pane_agent_session(getattr(proc, "stdout", "") or "")
            if live != position["predecessor_session"]:
                return ("mismatch", f"pane {anchor_pane} hosts {live!r}, not the recorded "
                                    f"predecessor session {position['predecessor_session']!r}")
            return ("ok", "")

        def _state_fence() -> tuple[bool, str, str]:
            """Re-read driver state under the lock and confirm this teardown is still authorised.

            Step 11 pass-2, both reviewers (Critical): the single fence before `send-text` was far
            too narrow. Every later destructive call — the Enter, and each close attempt — re-proved
            only the PANE, so a cancellation or a superseding generation landing after the clear was
            staged, or during the confirmation poll, stopped nothing: the old generation went on to
            clear and close a predecessor the NEW handoff was already relying on. Checking state
            before every destructive call is the fix; the residual is now just the gap between this
            check and the syscall, which no amount of checking can close.
            """
            # Tri-state like the identity probe (Step 11 pass-3): a TRANSIENT read failure is not
            # proof that authority was revoked. Collapsing them meant one unreadable state file
            # after a confirmed clear abandoned the remaining close attempts AND the re-arm,
            # leaving a live predecessor unguarded on the strength of a momentary I/O error.
            try:
                snapshot = _locked_state_read(driver_state_path)
            except (OSError, ValueError) as exc:
                return (False, f"driver state could not be re-read ({exc})", "unreadable")
            pend = snapshot.get("handoff_pending")
            if not isinstance(pend, dict):
                return (False, "handoff_pending has disappeared from driver state", "changed")
            if pend.get("kind") != driver_lib.MID_CHILD_HANDOFF_KIND:
                return (False, f"handoff_pending.kind is now {pend.get('kind')!r}", "changed")
            if pend.get("cancelled") is True:
                return (False, "the handoff has been CANCELLED", "changed")
            if snapshot.get("generation") != generation or pend.get("generation") != generation:
                return (False, f"generation moved on: ours is {generation}, state now carries "
                               f"{snapshot.get('generation')!r}/{pend.get('generation')!r}",
                        "changed")
            claim = snapshot.get("handoff_claim")
            if not (isinstance(claim, dict) and claim.get("generation") == generation
                    and claim.get("claimant") == session_id):
                return (False, "the claim is no longer ours", "changed")
            return (True, "", "ok")

        def _destructive_call(argv, kind, note=None):
            """State-fence AND re-prove the target, THEN issue one destructive command.

            Returns `(proc_or_None, refusal_reason_or_None, refusal_kind)` where `refusal_kind` is
            `None`, `"unavailable"` (transient — the caller may retry) or `"mismatch"`/`"state"`
            (terminal — stop).
            """
            fence_ok, fence_why, fence_kind = _state_fence()
            if not fence_ok:
                # "unreadable" is retryable by the caller; "changed" is proven and terminal.
                return (None, f"state fence REFUSED: {fence_why}",
                        "unavailable" if fence_kind == "unreadable" else "state")
            status, why_now = _anchor_hosts_predecessor()
            if status != "ok":
                return (None, f"identity check failed: {why_now}", status)
            return (_run(argv, kind, note=note), None, None)

        status, why = _anchor_hosts_predecessor()
        if status != "ok":
            return refuse(f"pre-teardown identity check FAILED: {why} — refusing both "
                          "destructive steps")

        # --- 8. is OUR guard still in force? ---
        own_text = read_or_empty(own_transcript)
        newest = latest_goal_status_condition(own_text)
        if newest is not None and newest.strip() != armed.strip():
            return refuse(
                "refusing: the newest goal_status row in this session carries a DIFFERENT "
                "condition, so the handed-over guard has been replaced — a replacement guard "
                "means the armed condition is stale and the predecessor must not be retired")
        if not goal_currently_unmet(own_text, armed):
            return refuse(
                "refusing: this session's guard for the armed condition is no longer unmet, so "
                "the continuing run is already unguarded — retiring the predecessor here is the "
                "original defect this workflow exists to prevent")

        # --- 9. FENCE, record the phase, clear, then CONFIRM the clear ---
        #
        # 8a correctness 1 / destructive 3, both reviewers converging: `cancelled` and the claim
        # were validated only at ENTRY, and the phase write validated nothing and ignored its own
        # failure. So a cancel landing before the clear, or a newer generation installed by
        # `open_handoff`, could not stop a teardown already in flight — and the phase could be
        # written into a DIFFERENT generation's record. Design §3 R4 requires this re-validation
        # under the lock immediately before the destructive step; it was missing.
        def _phase(value, *, fenced: bool):
            """Set `teardown_phase` under the lock, optionally re-validating the whole record.

            Returns (ok, reason). When `fenced`, the write only lands if this is still OUR
            un-cancelled generation and OUR claim — the check and the write share one lock hold,
            so nothing can slip between them.
            """
            verdict: dict = {}

            def _mutate(s):
                pend = s.get("handoff_pending")
                if not isinstance(pend, dict):
                    verdict["reason"] = "handoff_pending has disappeared from driver state"
                    return None
                # Generation-scoped on EVERY write, fenced or not (Step 11 prose 3 / fence 2): an
                # unfenced phase write could otherwise stamp — or clear — `teardown_phase` on a
                # NEWER generation's record, hiding that generation's own unguarded window.
                if pend.get("generation") != generation:
                    verdict["reason"] = (
                        f"refusing to write teardown_phase: the pending record is generation "
                        f"{pend.get('generation')!r}, not ours ({generation})")
                    return None
                if fenced:
                    if pend.get("kind") != driver_lib.MID_CHILD_HANDOFF_KIND:
                        verdict["reason"] = (f"handoff_pending.kind changed to "
                                             f"{pend.get('kind')!r} while this teardown was in "
                                             "flight")
                        return None
                    if pend.get("cancelled") is True:
                        verdict["reason"] = ("the handoff was CANCELLED after this teardown "
                                             "began — refusing to clear a guard or close a pane")
                        return None
                    if s.get("generation") != generation:
                        verdict["reason"] = (
                            f"generation moved on: this claim is {generation}, driver state now "
                            f"carries {s.get('generation')!r} — a newer handoff supersedes this one")
                        return None
                    claim = s.get("handoff_claim")
                    if not (isinstance(claim, dict)
                            and claim.get("generation") == generation
                            and claim.get("claimant") == session_id):
                        verdict["reason"] = ("the claim is no longer ours — another claimant or "
                                             "generation holds it")
                        return None
                new = dict(s)
                new["handoff_pending"] = {**pend, "teardown_phase": value}
                return new

            landed = _locked_state_update(driver_state_path, _mutate) is not None
            return (landed, verdict.get("reason", "the driver-state write did not land"))

        pred_transcript = os.path.join(transcript_dir,
                                       f"{position['predecessor_session']}.jsonl")

        # --- 8b. is the PREDECESSOR's guard still the one we are about to clear? ---
        # Step 11 pass-2 (adversarial, High): the gate re-checked only the SUCCESSOR's guard, then
        # sent an unconditional `/goal clear`. If the predecessor was re-prompted and re-armed a
        # DIFFERENT guard while the successor was rebuilding, this cleared that replacement guard
        # and closed a live session anyway. What we are authorised to clear is the condition the
        # handoff recorded, so that is what must still be in force on the target.
        # FAIL-CLOSED (Step 11 pass-3): the first version refused only when a non-None condition
        # DIFFERED, so an absent row, an unreadable transcript, and a newest row that was already
        # `met:true` all sailed through — the check "failed open" on exactly the evidence it exists
        # to demand. It must positively establish that the guard we are authorised to clear is the
        # one currently in force on the target.
        pred_text = read_or_empty(pred_transcript)
        pred_rows = [r for r in _iter_goal_status(pred_text)]
        pred_newest = pred_rows[-1] if pred_rows else None
        if pred_newest is None:
            return refuse(
                "refusing: the predecessor's transcript carries no goal_status row at all (or "
                "could not be read), so there is no evidence of the guard this teardown would "
                "clear — absence of evidence is not evidence of a guard")
        pred_cond = pred_newest.get("condition")
        if not isinstance(pred_cond, str) \
                or pred_cond.strip() != position["goal_condition"].strip():
            return refuse(
                "refusing: the predecessor's newest goal_status row carries a DIFFERENT condition "
                f"({pred_cond!r}) than the one this handoff recorded, so it has re-armed since the "
                "handover — clearing it would remove a guard this teardown was never authorised "
                "to touch")
        if pred_newest.get("met") is not False:
            return refuse(
                "refusing: the predecessor's newest goal_status row for the recorded condition is "
                "not unmet, so its guard is already gone — there is nothing to clear, and closing "
                "an already-unguarded session is not this command's decision to make")

        # Persisted BEFORE the send, so the one window where a crash leaves the predecessor
        # alive and UNGUARDED is discoverable on disk instead of invisible (§6 step 9). A write
        # that does NOT land aborts: proceeding would open that window with nothing on disk to
        # find it by, which is the whole reason the phase exists.
        fenced_ok, fence_reason = _phase("clearing", fenced=True)
        if not fenced_ok:
            return refuse(f"refusing at the pre-teardown fence: {fence_reason}")

        clear_text, clear_keys = build_send_text_argv(pane=anchor_pane, text=_CLEAR_COMMAND)

        # The baseline is captured as the LAST thing before the first transport (Step 11 pass-3):
        # the fence and identity probe are herdr/disk round-trips, so a baseline taken before them
        # leaves a window in which a `met:true` row can land and later "confirm" a clear that was
        # never executed. Fence and probe FIRST, baseline, then send — so the only remaining window
        # is between the baseline and the syscall itself. (That last sliver, and the equivalent one
        # for the state fence, is the residual design §10 item 1 declares out of scope: closing it
        # needs a fencing token herdr does not offer.)
        pre_ok, pre_why, pre_kind = _state_fence()
        if not pre_ok:
            _phase(None, fenced=False)
            return refuse(f"state fence REFUSED before the clear: {pre_why} ({pre_kind})")
        status, why = _anchor_hosts_predecessor()
        if status != "ok":
            _phase(None, fenced=False)
            return refuse(f"identity check failed before the clear: {why}")
        pred_baseline = _baseline(read_text, pred_transcript)
        if pred_baseline is None:
            _phase(None, fenced=False)
            return refuse("cannot baseline the predecessor transcript — without a baseline the "
                          "clear could never be confirmed, so nothing is sent")

        text_landed = False
        for kind, argv in (("clear_text", clear_text), ("clear_keys", clear_keys)):
            if kind == "clear_text":
                # `_run` DIRECTLY, not `_destructive_call` (Step 11 pass-4). The fence and the
                # identity probe already ran immediately above, and `_destructive_call` would run
                # them AGAIN — a state-file round-trip plus a whole `herdr pane get` subprocess —
                # between the baseline and the transport. A reviewer showed that reopened the hole
                # pass 3 was meant to close: a `met:true` row for the RECORDED condition landing
                # during those duplicated reads sits below the baseline, so it confirms a clear that
                # herdr never executed. Now nothing at all happens between the baseline and this
                # send, which is what the docs claim.
                proc, refusal_why, _refusal_kind = (_run(argv, kind), None, None)
            else:
                proc, refusal_why, _refusal_kind = _destructive_call(argv, kind)
            if refusal_why is not None:
                # Step 11 pass-2 (fixes 3): if the TEXT already landed, a refusal here must not
                # erase the staged-command warning — the `/goal clear` is sitting unsubmitted and a
                # later Enter would submit it. Only a refusal before anything was transported may
                # reset the phase to idle.
                phase_ok, phase_why = _phase(
                    "clear_staged_unsubmitted" if text_landed else None, fenced=False)
                out["outcome"] = "clear_failed"
                out["failed_step"] = kind
                if text_landed and not phase_ok:
                    out["reason"] = (
                        f"{refusal_why} — refusing to continue {_CLEAR_COMMAND!r}. The clear text "
                        "was already transported and is STAGED unsubmitted in the predecessor's "
                        f"input, and this could NOT be persisted to teardown_phase ({phase_why}), "
                        "so nothing on disk records it.")
                    return out
                out["reason"] = (
                    f"{refusal_why} — refusing to continue {_CLEAR_COMMAND!r}."
                    + (" The clear text was already transported and is STAGED unsubmitted in the "
                       "predecessor's input; teardown_phase records this." if text_landed else
                       " Nothing was transported."))
                return out
            if proc is not None and getattr(proc, "returncode", 1) == 0 and kind == "clear_text":
                text_landed = True
            if proc is None or getattr(proc, "returncode", 1) != 0:
                # 8a destructive 5: these two failures are NOT equivalent. If `send-text`
                # succeeded and only the Enter failed, the `/goal clear` is sitting UNSUBMITTED in
                # the predecessor's input, and a later stray Enter submits it — leaving the
                # predecessor unguarded long after this function returned "STILL guarded". That
                # state is recorded discoverably instead of being reset to idle.
                #
                # Step 11 (fence 6) extends this: a `send-text` that RAISED is ambiguous, not
                # proof that nothing was transported — herdr may have accepted the text before the
                # client timed out. Only a definite non-zero return code means "not transported".
                staged = kind == "clear_keys" or proc is None
                _phase("clear_staged_unsubmitted" if staged else None, fenced=False)
                out["outcome"] = "clear_failed"
                out["failed_step"] = kind
                out["reason"] = (
                    f"{kind} for {_CLEAR_COMMAND!r} failed — aborting BEFORE pane close. "
                    + ("The clear may be STAGED in the predecessor's input (transported but not "
                       "submitted, or the call was ambiguous): it is guarded now, but a later "
                       "Enter would submit the clear. teardown_phase records this."
                       if staged else
                       "The call returned a definite failure, so nothing was transported and the "
                       "predecessor is alive and STILL guarded."))
                return out

        def _cleared() -> bool:
            tail = _tail(read_text(pred_transcript), pred_baseline)
            return tail is not None and transcript_has_cleared_goal(
                tail, expected_condition=position["goal_condition"])

        if not _poll_for(_cleared, attempts=GOAL_POLL_ATTEMPTS, delay_s=GOAL_POLL_DELAY_S,
                         sleeper=sleeper):
            # Deliberately NOT reset to None: the clear may have landed while its confirmation
            # was unreadable, so the state stays discoverable rather than claiming to be idle.
            phase_ok, phase_why = _phase("clear_unconfirmed", fenced=False)
            out["outcome"] = "clear_unconfirmed"
            out["reason"] = (
                "the clear was transported but never confirmed by a met:true sentinel row for the "
                "recorded condition — leaving the pane OPEN. The guard state is AMBIGUOUS: the "
                "clear may have been parsed with its confirmation unreadable or late."
                + ("" if phase_ok else
                   f" WARNING: this could NOT be persisted to teardown_phase ({phase_why}), so "
                   "nothing on disk records it."))
            return out

        # --- 10. close, re-proving the target before EVERY attempt ---
        # The identity proof from step 7 is stale by here: the clear was confirmed, so the
        # predecessor may have stopped and exited — and a pane id is a REUSABLE handle, so it can
        # already belong to an unrelated session. Closing on an old proof would kill that session
        # and report `retired`. Re-arming would paste into it too, so when the target stops being
        # provably the predecessor's, nothing further is done.
        def _predecessor_re_armed() -> tuple[str | None, str]:
            """Has the predecessor become GUARDED AGAIN since we cleared it?

            Step 11 pass-4 (Critical, reproduced): the close path re-checked driver state and pane
            identity but never guard state. So a predecessor that armed a NEW guard between the
            confirmed clear and the close — it is a live session and can be prompted — was closed
            anyway, destroying a guarded working context. The declared clear-to-close window assumes
            the predecessor stays unguarded; this is the case where it does not.

            Returns `(reason_or_None, kind)` with kind `"ok"`, `"re_armed"` or `"unreadable"`.

            FAIL-CLOSED on an unreadable transcript (Step 11 pass-5). The first version failed safe
            to "not re-armed" to avoid stranding a teardown behind a transient I/O error — and a
            reviewer promptly hid a REAL re-arm behind one read error and got `retired` plus a
            `pane close`. The asymmetry decides it: refusing on an unreadable read leaves the
            predecessor alive and recoverable, while proceeding can irreversibly destroy a live
            guarded session. `"unreadable"` is retryable by the caller, so a genuinely transient
            error costs an attempt rather than the whole teardown.
            """
            try:
                text = read_text(pred_transcript)
            except (OSError, UnicodeDecodeError) as exc:
                return (f"the predecessor's transcript could not be read ({exc}), so a re-arm "
                        "cannot be ruled out", "unreadable")
            rows = [r for r in _iter_goal_status(text)]
            if not rows:
                return (None, "ok")
            newest = rows[-1]
            if newest.get("met") is False:
                return (f"the predecessor has ARMED A NEW GUARD since the clear was confirmed "
                        f"(newest condition {newest.get('condition')!r} is unmet) — it is a live "
                        "guarded session again, so closing it would destroy a working context this "
                        "teardown was never authorised to touch", "re_armed")
            return (None, "ok")

        close_argv = build_teardown_argv(anchor_pane)
        closed = False
        target_lost: str | None = None
        for attempt in range(CLOSE_ATTEMPTS):
            if attempt:
                sleeper(CLOSE_RETRY_DELAY_S)
            # Explicit ordering, NOT `_destructive_call` (Step 11 pass-5, reproduced): that wrapper
            # would run the fence and a `pane get` subprocess AFTER the re-arm check, and a reviewer
            # injected a new guard during exactly that window and watched a guarded predecessor get
            # closed. The re-arm check must be the LAST read before the syscall, so the order is
            # fence -> identity -> re-arm -> close, with nothing in between the last two.
            fence_ok, fence_why, fence_kind = _state_fence()
            if not fence_ok:
                if fence_kind == "unreadable":
                    continue                        # transient: costs an attempt, not the teardown
                target_lost = f"state fence REFUSED: {fence_why}"
                break
            status, identity_why = _anchor_hosts_predecessor()
            if status == "unavailable":
                continue
            if status == "mismatch":
                target_lost = f"identity check failed: {identity_why}"
                break
            re_armed, re_arm_kind = _predecessor_re_armed()
            if re_arm_kind == "unreadable":
                continue                            # fail-closed, but retryable
            if re_arm_kind == "re_armed":
                phase_ok, phase_why = _phase("predecessor_re_armed", fenced=False)
                out["outcome"] = "predecessor_re_armed"
                out["reason"] = (
                    re_armed + ". Refusing to close or re-arm."
                    + ("" if phase_ok else
                       f" WARNING: not persisted to teardown_phase ({phase_why})."))
                return out
            proc = _run(close_argv, "pane_close",
                        note=f"attempt {attempt + 1}/{CLOSE_ATTEMPTS}")
            if proc is not None and getattr(proc, "returncode", 1) == 0:
                closed = True
                break
        if target_lost is not None:
            phase_ok, phase_why = _phase("target_changed_after_clear", fenced=False)
            out["outcome"] = "target_changed_after_clear"
            out["reason"] = (
                f"{target_lost}. The guard was already cleared, so the predecessor is "
                "alive-and-unguarded OR has exited on its own — this cannot distinguish them. "
                "Refusing to close or re-arm, because both would act on a pane that is no longer "
                "provably the predecessor's."
                + (" teardown_phase records this for an operator."
                   if phase_ok else
                   f" WARNING: the incident could NOT be persisted to teardown_phase "
                   f"({phase_why}), so this state is visible only in this report."))
            return out
        if closed:
            _phase(None, fenced=False)
            out["outcome"] = "retired"
            out["ok"] = True
            out["reason"] = "guard cleared and confirmed, pane closed"
            return out

        # The partial-success state: the guard is confirmed CLEARED but the pane would not
        # close, so the predecessor may be alive and no longer guarded — strictly worse than
        # either failure alone. Re-arm from the predecessor's OWN recorded condition (§3: reading
        # it from the successor's transcript would arm the predecessor with the wrong guard, and
        # a capped one silently truncated).
        rearm_baseline = _baseline(read_text, pred_transcript)
        rearm_text, rearm_keys, truncated = build_send_text_goal_argv(
            pane=anchor_pane, goal_condition=position["goal_condition"])
        rearm_sent = True
        for kind, argv in (("rearm_text", rearm_text), ("rearm_keys", rearm_keys)):
            proc, refusal_why, _kind = _destructive_call(
                argv, kind, note="goal TRUNCATED" if truncated else None)
            if refusal_why is not None or proc is None \
                    or getattr(proc, "returncode", 1) != 0:
                rearm_sent = False
                break

        def _rearmed() -> bool:
            tail = _tail(read_text(pred_transcript), rearm_baseline)
            return tail is not None and transcript_has_unmet_goal(
                tail, expected_condition=armed)

        confirmed = bool(rearm_sent and rearm_baseline is not None
                         and _poll_for(_rearmed, attempts=GOAL_POLL_ATTEMPTS,
                                       delay_s=GOAL_POLL_DELAY_S, sleeper=sleeper))
        out["outcome"] = "alive_and_re_armed" if confirmed else "alive_and_unguarded"
        out["reason"] = (
            "pane close failed after a CONFIRMED clear; predecessor re-armed from its own "
            "recorded condition" if confirmed else
            "pane close failed after a CONFIRMED clear AND the re-arm could not be confirmed — "
            "the predecessor may be alive and UNGUARDED; this is an incident")
        # A terminal INCIDENT record that cannot be persisted must be said out loud (Step 11
        # pass-2, fixes 6): a silently-refused write here means the only trace of an unguarded
        # predecessor is this return value.
        phase_ok, phase_why = _phase(out["outcome"], fenced=False)
        if not phase_ok:
            out["reason"] += (f" WARNING: this outcome could NOT be persisted to teardown_phase "
                              f"({phase_why}), so nothing on disk records it.")
        return out
    except (LauncherError, OSError, subprocess.SubprocessError, ValueError) as exc:
        out["outcome"] = out["outcome"] or "error"
        out["reason"] = out["reason"] or f"exception: {exc}"
        return out


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

    # #665 — the `kind` discriminator is a CLOSED allowlist, checked FIRST.
    #
    # `handoff_pending` used to have exactly one meaning: start the next child. It now has two,
    # and this entry point reads the same file. The rule is an allowlist rather than an equality
    # test because equality-only matching lets `"MID_CHILD"`, `"mid-child"` or `42` fall through
    # to the legacy branch and launch a second successor from a record it does not understand —
    # two successors competing for one generation. Absent is the only accepted value here; every
    # present value is refused, naming which case it is.
    def _refuse_foreign_kind(state_snapshot) -> int | None:
        """The kind/cancelled allowlist, factored out so it can run TWICE (8a correctness 3).

        This entry point reads driver state WITHOUT the lock (as #611 always has), so checking
        once is a time-of-check/time-of-use window: a concurrent `mid-child-handoff` can install
        `kind: "mid_child"` after this read and both commands then launch a successor — exactly
        the two-successors-on-one-generation condition the discriminator exists to prevent. It is
        re-run against a LOCKED re-read immediately before the launch, which narrows the window to
        the launch call itself rather than the whole capability-probe sequence.
        """
        pend_now = state_snapshot.get("handoff_pending")
        if not isinstance(pend_now, dict):
            return None
        if "kind" in pend_now:
            if pend_now["kind"] == driver_lib.MID_CHILD_HANDOFF_KIND:
                print(f"refusing: handoff_pending.kind is "
                      f"{driver_lib.MID_CHILD_HANDOFF_KIND!r} — a mid-child resume is already in "
                      "flight; building a child-boundary handoff from it would put a second "
                      "successor on one generation", file=sys.stderr)
            else:
                print(f"refusing: unrecognised handoff_pending.kind {pend_now['kind']!r} — this "
                      "entry point handles only the legacy child-boundary handoff, which carries "
                      "no kind at all", file=sys.stderr)
            return 3
        if pend_now.get("cancelled") is True:
            # An aborted handoff cancels its own record, and until a later `open_handoff` bumps
            # the counter that record IS the current generation and therefore claimable. Refusing
            # it here is what stops a stray successor taking a lease on an abandoned handoff.
            print("refusing: handoff_pending is cancelled — an aborted handoff record must not "
                  "be claimed", file=sys.stderr)
            return 3
        return None

    refused = _refuse_foreign_kind(state)
    if refused is not None:
        return refused

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

    # Re-check against a LOCKED re-read immediately before launching. Everything above — the
    # disposition, the capability probes, the goal-condition read — takes time during which a
    # concurrent `mid-child-handoff` can install its own record.
    refused = _refuse_foreign_kind(_locked_state_read(args.driver_state))
    if refused is not None:
        return refused

    out = perform_handoff(
        anchor_pane=args.anchor_pane, cwd=args.cwd, project_root=args.project_root,
        name=args.name, goal_condition=condition,
        resume_prompt=disposition["resume_prompt"],
        registry_path=args.registry, transcript_dir=args.transcript_dir,
        launch_mode=args.launch_mode, teardown=not args.no_teardown)
    print(json.dumps({k: out[k] for k in
                      ("ok", "results", "failed_step", "new_pane", "session_id",
                       "truncated", "cleanup")}, indent=2))
    return 0 if out["ok"] else 4


def _own_session_id(explicit: str | None, *, require_env: bool = False) -> str:
    """The caller's OWN Claude session id — the environment is authoritative.

    8a destructive 1 (Critical): this used to return `explicit or env`, so a caller-supplied
    `--session-id` OVERRODE the real identity. Since `retire_predecessor`'s whole authority is
    "my own session id equals the recorded successor", any session — the predecessor included —
    could read `handoff_pending.successor.session`, pass it back, and authorise the teardown. That
    recreates exactly the predecessor-driven path approach C was rejected for, without touching
    `.driver-state` at all.

    So `$CLAUDE_CODE_SESSION_ID` wins whenever it is set, and an explicit value that CONTRADICTS
    it is refused rather than silently preferred. The flag survives only as an assertion (useful
    in tests and for an explicit operator invocation) and can no longer be an impersonation.

    Step 11 then REFUTED that first attempt with a probe: it returned `env or explicit`, so
    `env -u CLAUDE_CODE_SESSION_ID ... --session-id <recorded successor>` restored the whole
    impersonation. Narrowing the override was not enough — the environment must be REQUIRED
    wherever the value carries destructive authority (`require_env`), because "no environment at
    all" is not a state a real Claude successor session can be in.
    """
    env = os.environ.get("CLAUDE_CODE_SESSION_ID", "")
    if env and explicit and explicit != env:
        raise LauncherError(
            f"--session-id {explicit!r} contradicts $CLAUDE_CODE_SESSION_ID {env!r} — teardown "
            "authority is THIS session's own identity, so an override is refused")
    if require_env and not env:
        raise LauncherError(
            "$CLAUDE_CODE_SESSION_ID is unset or empty — refusing to take a caller-supplied "
            "identity for a destructive teardown. Only the recorded successor session may retire "
            "the predecessor, and a session that cannot prove its own identity is not it")
    return env or explicit or ""


def _cmd_mid_child_handoff(args) -> int:
    """The PREDECESSOR's side: capture position, persist it through `open_handoff`, launch the
    successor with the mid-child ladder, and record the successor's identity. It deliberately
    does NOT retire anything — that is the successor's command."""
    driver_lib = _driver_lib()
    predecessor_session = _own_session_id(args.predecessor_session)
    if not predecessor_session:
        print("no --predecessor-session and CLAUDE_CODE_SESSION_ID is unset — refusing to guess "
              "which session is handing over", file=sys.stderr)
        return 2

    # The condition must be the predecessor's OWN live guard, and Step 11 (prose 5) showed two
    # ways that was not enforced: an explicit `--goal-condition` was accepted with no provenance
    # check at all, and `last_unmet_goal_condition` happily returns a condition that a LATER
    # `met:true` row has since satisfied. Either would arm the successor with a guard that is not
    # what is actually owed — and the same wrong value would then be used to re-arm the
    # predecessor on the partial-success path. So whichever way it arrives, it is validated
    # against the transcript as still-unmet.
    # Step 11 pass-2, BOTH reviewers (High): the previous attempt validated the condition only
    # when a transcript was supplied — while the parser made `--goal-condition` and
    # `--goal-condition-from` MUTUALLY EXCLUSIVE, so a production invocation using an explicit
    # condition could never supply the transcript and was therefore never checked at all. The
    # regression test had fabricated an argparse state the CLI cannot produce, which is exactly the
    # test-asserts-the-implementation defect this epic keeps hitting. The transcript is now
    # REQUIRED for this command, and `--goal-condition` is an optional assertion checked against it.
    with open(args.goal_condition_from, encoding="utf-8") as fh:
        transcript_text = fh.read()
    condition = args.goal_condition
    if condition is None:
        condition = last_unmet_goal_condition(transcript_text)
        if condition is None:
            print("no unmet goal in this session's transcript — refusing to invent a condition "
                  "for the successor", file=sys.stderr)
            return 3
    # Two separate questions, and Step 11 pass-3 (verify 4) showed that only asking the first lets
    # a REPLACED guard through: `goal_currently_unmet` deliberately examines only rows matching the
    # condition, so with history `A/met:false` then `B/met:false`, an explicit `--goal-condition A`
    # was accepted even though B is the live guard. So the condition must be BOTH still-unmet AND
    # the newest guard in the transcript.
    if not goal_currently_unmet(transcript_text, condition):
        print("refusing: the supplied/derived goal condition is not the guard currently in force "
              "in this session's transcript (it has been met) — arming the successor with it would "
              "hand over a guard that is not what is owed", file=sys.stderr)
        return 3
    newest_condition = latest_goal_status_condition(transcript_text)
    if newest_condition is None or newest_condition.strip() != condition.strip():
        print(f"refusing: the goal condition is not the NEWEST guard in this session's transcript "
              f"(newest is {newest_condition!r}) — it has been replaced, so handing it over would "
              "arm the successor with a guard that has already been retired", file=sys.stderr)
        return 3

    # 8a destructive 6: `--repo-root` and `--project-root` were independent inputs, and nothing
    # bound the recorded repo to the project the successor proves it switched to. So
    # `project_switched` could prove rawgentic while `position_rebuilt` proved an unrelated
    # repository that happened to carry the same branch name — an authority-binding failure, not
    # an injection. Confining it to the project root binds the two proofs to one tree.
    try:
        repo_root = resolve_cwd(args.repo_root, args.project_root)
    except LauncherError as exc:
        print(f"refusing: --repo-root is not inside --project-root ({exc}) — the recorded repo "
              "must be the project the successor proves it switched to, or project_switched and "
              "position_rebuilt can prove two different trees", file=sys.stderr)
        return 2

    position = {"issue": args.issue, "step": args.step, "branch": args.branch,
                "test_baseline": args.test_baseline, "predecessor_pane": args.anchor_pane,
                "predecessor_session": predecessor_session, "goal_condition": condition,
                "project": args.project, "project_path": args.project_path,
                "repo_root": repo_root}

    # The disposition is computed INSIDE the lock so the generation it bumps is derived from the
    # state actually being written, not from a copy read earlier.
    held: dict = {}

    def _open(state):
        disposition = driver_lib.mid_child_handoff(state, position=position)
        held["disposition"] = disposition
        if disposition.get("outcome") != "ready":
            return None
        return driver_lib.open_handoff(state, disposition, now_ts=int(time.time()))

    _locked_state_update(args.driver_state, _open)
    disposition = held.get("disposition") or {}
    if disposition.get("outcome") != "ready":
        print(f"no mid-child handoff: {disposition.get('outcome')!r} "
              f"{'; '.join(disposition.get('errors') or [])}", file=sys.stderr)
        return 3

    generation = disposition["generation"]

    def _record_successor(pane, session):
        """Returns False when the record could not be written — see `perform_handoff`'s
        `on_successor` contract. A silent no-op here used to let a superseded generation keep
        launching a successor nobody could ever retire."""
        def _mutate(state):
            pend = state.get("handoff_pending")
            if not isinstance(pend, dict) or pend.get("generation") != generation:
                return None
            new = dict(state)
            new["handoff_pending"] = {**pend, "successor": {"pane": pane, "session": session}}
            return new
        return _locked_state_update(args.driver_state, _mutate) is not None

    # 8a correctness 5: `perform_handoff` validates the pane id, agent name and transcript
    # directory AFTER this record is persisted, and it RAISES rather than returning a result. So a
    # malformed argument used to bump the generation and leave an uncancelled `mid_child` record
    # that the legacy launcher refuses and no successor can ever satisfy. Every exit from here
    # cancels, not just the ones that return.
    # An aborted handoff CANCELS its own record. Until a later `open_handoff` bumps the counter the
    # abandoned record IS the current generation and therefore claimable, so a delayed or stray
    # successor could otherwise take a lease on it. Monotonic: once the claim is `started` the
    # takeover has happened and a cancel is refused instead.
    def _cancel(state):
        pend = state.get("handoff_pending")
        if not isinstance(pend, dict) or pend.get("generation") != generation:
            return None
        claim = state.get("handoff_claim")
        if isinstance(claim, dict) and claim.get("generation") == generation \
                and claim.get("started"):
            return None
        new = dict(state)
        new["handoff_pending"] = {**pend, "cancelled": True}
        return new

    # `finally`, not `except` (Step 11 pass-2, fixes 8). "Every exit cancels" was still false for
    # `KeyboardInterrupt` and `SystemExit`, which do not derive from `Exception` — a Ctrl-C during
    # an interactive handoff left the persisted generation uncancelled and claimable. A `finally`
    # is the only construct that actually makes the claim true.
    out = None
    try:
        out = perform_handoff(
            anchor_pane=args.anchor_pane, cwd=args.cwd, project_root=args.project_root,
            name=args.name, goal_condition=condition,
            resume_prompt=disposition["resume_prompt"], registry_path=args.registry,
            transcript_dir=args.transcript_dir, launch_mode=args.launch_mode,
            prompt_marker=driver_lib.mid_child_marker(position["issue"], generation),
            expected_project=args.project, expected_project_path=args.project_path,
            steps=mid_child_verification_steps(), teardown=False,
            on_successor=_record_successor)
    except Exception as exc:  # pylint: disable=broad-except
        # Broad on purpose: a narrow tuple let `UnicodeDecodeError` from
        # `subprocess.run(text=True)` and `JSONDecodeError` from a state read escape. Reported,
        # never swallowed silently.
        print(f"mid-child handoff aborted: {type(exc).__name__}: {exc}", file=sys.stderr)
    finally:
        if out is None or not out.get("ok"):
            _locked_state_update(args.driver_state, _cancel)

    if out is None:
        print(json.dumps({"generation": generation, "ok": False,
                          "failed_step": "exception_before_result",
                          "cancelled": True}, indent=2))
        return 4
    print(json.dumps({"generation": generation,
                      **{k: out[k] for k in ("ok", "results", "failed_step", "new_pane",
                                             "session_id", "truncated", "cleanup",
                                             "teardown_skipped")}}, indent=2))
    return 0 if out["ok"] else 4


def _cmd_retire_predecessor(args) -> int:
    """The SUCCESSOR's side: the only command that clears a live guard and closes a pane."""
    session_id = _own_session_id(args.session_id, require_env=True)
    out = retire_predecessor(
        driver_state_path=args.driver_state, session_id=session_id,
        anchor_pane=args.anchor_pane, transcript_dir=args.transcript_dir,
        registry_path=args.registry)
    print(json.dumps({k: out[k] for k in ("ok", "outcome", "reason", "results", "failed_step",
                                          "generation")}, indent=2))
    if out["ok"]:
        return 0
    return 3 if out["outcome"] in ("refused", "claim_refused", "teardown_refused") else 4


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

    # #665 — the mid-child pair. Two commands, run by two different sessions, because the
    # handover and the retirement have different owners: only the successor may retire.
    p_mc = sub.add_parser("mid-child-handoff",
                          help="hand this mid-child session over to a fresh successor (#665)")
    p_mc.add_argument("--driver-state", required=True)
    p_mc.add_argument("--anchor-pane", required=True, help="THIS session's pane id")
    p_mc.add_argument("--name", required=True, help="herdr agent name for the successor")
    p_mc.add_argument("--project-root", required=True)
    p_mc.add_argument("--cwd", required=True)
    p_mc.add_argument("--registry", required=True)
    p_mc.add_argument("--transcript-dir", required=True)
    p_mc.add_argument("--issue", required=True, type=int, help="the in-progress child")
    p_mc.add_argument("--step", required=True, help="the WF2 step being interrupted")
    p_mc.add_argument("--branch", required=True)
    p_mc.add_argument("--test-baseline", required=True,
                      help="the RECORDED baseline, verbatim — the successor must not re-measure")
    p_mc.add_argument("--project", required=True)
    p_mc.add_argument("--project-path", required=True,
                      help="as the registry records it, e.g. ./projects/rawgentic")
    p_mc.add_argument("--repo-root", required=True)
    p_mc.add_argument("--predecessor-session", default=None,
                      help="defaults to $CLAUDE_CODE_SESSION_ID")
    p_mc.add_argument("--launch-mode", default="fresh", choices=sorted(LAUNCH_MODES))
    # NOT mutually exclusive (Step 11 pass-2): the transcript is the provenance for the condition,
    # so it is always required, and an explicit `--goal-condition` is an assertion validated
    # against it rather than a way to bypass it.
    p_mc.add_argument("--goal-condition-from", metavar="OWN_TRANSCRIPT", required=True,
                      help="THIS session's transcript — the provenance for the goal condition")
    p_mc.add_argument("--goal-condition", default=None,
                      help="optional assertion; must equal the guard currently in force")

    p_rp = sub.add_parser("retire-predecessor",
                          help="retire the predecessor after a mid-child handoff (#665) — the "
                               "successor runs this, and only after its position is rebuilt")
    p_rp.add_argument("--driver-state", required=True)
    p_rp.add_argument("--session-id", default=None,
                      help="THIS session's id; defaults to $CLAUDE_CODE_SESSION_ID")
    p_rp.add_argument("--anchor-pane", required=True,
                      help="the PREDECESSOR's pane; must equal the recorded predecessor_pane")
    p_rp.add_argument("--transcript-dir", required=True)
    p_rp.add_argument("--registry", required=True)

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
        if args.cmd == "mid-child-handoff":
            return _cmd_mid_child_handoff(args)
        if args.cmd == "retire-predecessor":
            return _cmd_retire_predecessor(args)
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
