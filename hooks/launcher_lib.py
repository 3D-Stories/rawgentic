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
EXPLICIT pane id, not only `--current`** (read from herdr 0.7.5's `--help`; still true on the
now-pinned 0.8.0, where `pane split` takes `[PANE_ID]` and `send-text`/`send-keys` take it as a
mandatory positional — re-probed live under #886).
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

**There are THREE sends, each gated on a durable artifact the successor itself writes (#694).**
In order: `/rawgentic:switch <project>` alone, gated on the session-registry row
(`project_switched`); then the resume prompt, gated on its marker reaching the transcript
(`prompt_landed`); then `/goal` LAST, gated on the `goal_status met:false` row (`goal_armed`).
The predecessor is retired only after all of them pass.

Nothing here gates on `agent_status`, and nothing may: measured live on 2026-07-29 it read `idle`
immediately after a prompt was submitted, `done` while a turn was still producing output, and
`working` for a session sitting at an empty input line. An earlier revision of this fix gated the
resume-prompt paste on `agent_status == "idle"` and was falsified before it shipped — after a real
UNMET goal was armed the pane reported `working` across consecutive reads while the `goal_status`
row was ALREADY present, so that gate would have refused every real handoff. `/goal` needs no idle
window at all: pasted into a session actively mid-turn it still produced its row while that turn
ran, which is what lets it go last. `parse_pane_agent_status` survives for DIAGNOSTICS only.

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
# #700 — the unsubmitted-Enter recovery. `project_switched` proves the bind's ROW landed, not that
# its TURN ended, so the resume prompt's Enter can be eaten by the still-running bind turn and the
# paste then sits in the input box unsubmitted. Measured live 2026-07-29: the row appeared at ~24 s
# while the pane was still cooking a ~50 s turn, so ONE nudge after the first ~18 s poll would still
# have landed inside that turn. Four rounds of {bare Enter, re-poll} is ~90 s worst case, past a
# first turn of the observed length, and costs nothing on a handoff that submits normally.
PROMPT_NUDGE_ROUNDS = 4
# A marker must be distinctive, not merely present in the prompt (#700 design review, High 1):
# `transcript_has_marker` is a plain substring scan, so a short common word would match unrelated
# tail content and pass `prompt_landed` before the prompt ever submitted. A length floor is a
# heuristic and nothing more — the skill's own rule is a token unique to the handoff.
PROMPT_MARKER_MIN_LEN = 8
GOAL_POLL_DELAY_S = 1.5
SWITCH_POLL_ATTEMPTS = 40
SWITCH_POLL_DELAY_S = 3.0

# Every bounded wait here is bounded on wall clock as well as on attempt count (#694 cross-model
# review). An attempts cap alone is not a time bound: each attempt does I/O, a blocked read has no
# ceiling of its own, and the arithmetic is unforgiving — a 15-attempt retry whose call can each
# take the runner's 180 s timeout is a 45-minute wait dressed up as a short one. Multiplying the
# NOMINAL budget (attempts x delay) rather than naming an absolute per-site number keeps the two
# numbers from drifting apart when a budget is retuned; 2x leaves a healthy-but-slow poll room to
# finish while refusing to let a stalled one run away.
POLL_WALL_CLOCK_SLACK = 2.0

# `agent_status` is NOT a synchronisation signal, and nothing here may treat it as one (#694).
#
# An earlier revision of this fix gated the resume-prompt paste on `agent_status == "idle"`. It was
# falsified by measurement on 2026-07-29, on this host, before it shipped:
#   - after arming a real UNMET goal, the pane reported `working` across consecutive reads while the
#     `goal_status met:false` row was already present — so an idle gate placed after `goal_armed`
#     would have refused EVERY real handoff;
#   - the value read `idle` immediately after a prompt was submitted, and `done` while a turn was
#     still producing output, and `working` for a session sitting at an empty input line.
#
# So the sequence is gated on DURABLE ARTIFACTS the successor itself writes — the session-registry
# row and the `goal_status` row — and never on pane status or a fixed timer. `parse_pane_agent_status`
# is retained for diagnostics only (it is what makes a report say WHY a handoff stalled); no control
# flow may branch on it.

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

# #731 — the OTHER instant refusal, and the one that must NOT be retried: a bound agent name
# stays bound (the live 2026-07-30 failure had it bound to the predecessor's own pane), so a
# same-name retry is structurally guaranteed to fail. It gets a name-specific `failed_step`
# instead, on both the pre-split preflight and the start-time race path.
NAME_TAKEN_ERROR_CODE = "agent_name_taken"
# The preflight list's own runner timeout (seconds): an optional check must not hold every
# handoff for the 180 s default runner bound when herdr hangs (#731 Step-11).
NAME_PREFLIGHT_TIMEOUT_S = 5

# Order is CAUSAL, and #694 REORDERED it: `spawned -> project_switched -> goal_armed`, matching the
# order the sends now happen in. Each rung is the durable artifact produced by the send before it.
#
# The previous order armed the guard FIRST (`spawned -> goal_armed -> project_switched`) on the
# reasoning that a session handed work before its goal exists is an UNGUARDED run. That reasoning is
# answered rather than discarded: the predecessor is not retired until the LAST rung passes, so
# "work begins unguarded" never coincides with "the predecessor is already gone" — which was the
# actual harm. What the old order cost was real, because the bind had to ride inside the resume
# prompt as a prefix, and a prefix check can only ever proxy for "first" (#682's own docstring says
# so). Binding in its own verified send makes the ordering structural instead.
#
# Two earlier revisions got this wrong in the opposite direction, so the constraint is worth
# stating: `project_switched` must not be checked before the bind has actually been SENT, or it can
# only pass on stale evidence.
_VERIFICATION_STEPS: tuple[dict[str, str], ...] = (
    {"step": "spawned",
     "artifact": "herdr pane get <pane> -> a non-empty agent_session.value"},
    {"step": "project_switched",
     "artifact": "claude_docs/session_registry.jsonl BELOW the pre-launch offset -> a line "
                 "carrying the NEW session id"},
    {"step": "goal_armed",
     "artifact": "the successor transcript BELOW the pre-launch offset -> a goal_status "
                 "attachment with met:false whose condition is the one we armed"},
)

# The mid-child ladder (#665, extended by #840). SEVEN checks, still CAUSAL, and every artifact is
# a file on disk or live git state — none of them reads scraped terminal output, which is why a
# handoff can be verified at all.
#
# `owner` records which side can produce each piece of evidence, and it is load-bearing rather
# than documentation: the predecessor can prove the first FIVE about the successor it just
# launched — four before #840, which inserted the predecessor-owned `queue_revalidated` at
# position 1 — but the last two are the SUCCESSOR's own (a rebuild receipt and its claim). A
# predecessor-side gate that demanded all seven could never pass, and — worse — a full ladder
# handed to `teardown_allowed` on the predecessor side would authorise a predecessor to retire
# ITSELF after the five checks it owns, which is precisely the ownership inversion approach C
# was rejected
# for (design §2).
#
# #694 reordered the four predecessor-owned rungs to `project_switched -> prompt_landed ->
# goal_armed`, so the ladder again lists its rungs in the order the sends produce them (the bind is
# now its own send, and `/goal` goes last). Order here is not cosmetic: `evaluate_verifications`
# walks it and stops at the FIRST failure, so a ladder listing rungs out of send order reports the
# wrong step as the thing that broke.
#
# #840 puts `queue_revalidated` FIRST. The queue must be revalidated BEFORE a successor is spawned
# to inherit it — a successor handed a stale queue has already read the wrong issue bodies by the
# time any later rung could object. It is predecessor-owned because the predecessor is what just
# merged the child that moved `main`.
#
# **The rung and its producer must ship together.** A rung with no producer is not inert: it is a
# landmine. `perform_handoff` gates on `_predecessor_steps(ladder)` at `:1699` and `:1722`,
# `retire_predecessor` gates at `:2501`, and `evaluate_verifications` treats an UNREPORTED step as
# failed (`:1139`) — so adding the rung alone fail-closes every mid-child handoff and every
# teardown the moment it lands.
_MID_CHILD_VERIFICATION_STEPS: tuple[dict[str, str], ...] = (
    {"step": "queue_revalidated", "owner": "predecessor",
     "artifact": ".driver-state -> a queue_revalidation receipt whose validated_head equals a "
                 "FRESHLY observed origin/main (launcher_lib.observe_head), with every eligible "
                 "child stamped at that head and no DURABLY-UNDISPOSED child carrying a "
                 "pending_disposition (the marker is checked wider than eligibility, because a "
                 "pr_open child cannot be selected but can still satisfy a dependency). Produced by "
                 "the launcher reading the receipt — never satisfied by a caller-supplied rung "
                 "result, because an agent asserting its own homework is the vacuous pass this "
                 "whole issue exists to eliminate"},
    {"step": "spawned", "owner": "predecessor",
     "artifact": "herdr pane get <new> -> a non-empty agent_session.value, recorded into "
                 "handoff_pending.successor so the successor can later bind its own session id "
                 "to it (a session cannot discover its own pane id, so it cannot re-derive this)"},
    {"step": "project_switched", "owner": "predecessor",
     "artifact": "claude_docs/session_registry.jsonl BELOW the offset -> ONE line carrying the "
                 "NEW session id AND the recorded project AND the recorded project_path"},
    {"step": "prompt_landed", "owner": "predecessor",
     "artifact": "the successor transcript BELOW the offset -> the generation-bound handoff "
                 "marker, matched as a plain SUBSTRING: a live probe found pasted prompts "
                 "persisted in queue-operation / attachment rows, not a type:user row"},
    {"step": "goal_armed", "owner": "predecessor",
     "artifact": "the successor transcript BELOW the pre-launch offset -> a goal_status "
                 "attachment with met:false whose condition is the one actually armed"},
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


def build_agent_list_argv() -> list[str]:
    """`herdr agent list` — every agent herdr knows, with an OPTIONAL `name` per entry.

    Verified live against herdr 0.8.0 (2026-08-05): the JSON is
    `{"id": "cli:agent:list", "result": {"agents": [...], "type": "agent_list"}}` and only
    named agents carry a `name` key. Used by the #731 pre-split name preflight.
    """
    return ["herdr", "agent", "list"]


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


def build_send_enter_argv(pane: str) -> list[str]:
    """A BARE Enter — the #700 recovery for a paste that is intact but unsubmitted.

    Deliberately not `build_send_text_argv`'s second element: that pair always re-sends the text
    first, and re-sending is the one thing this recovery must never do. #696's rule is that a
    collapsed paste is neither retried (double submission) nor truncated (silent corruption); the
    correct action is to submit what is already there.
    """
    validate_pane_id(pane)
    return ["herdr", "pane", "send-keys", pane, "Enter"]


def build_pane_read_argv(pane: str) -> list[str]:
    """Read the pane's VISIBLE viewport — used only to decide whether a nudge is safe.

    This is not a gate on progress and must never become one: nothing in this module verifies a
    handoff from scraped terminal output, because the artifacts a successor writes are the only
    durable evidence. Its single job is the inverse — refusing an action when the screen shows
    something an Enter must not touch.
    """
    validate_pane_id(pane)
    return ["herdr", "pane", "read", pane, "--source", "visible", "--format", "text"]


# Claude Code's own collapsed-input affordances, both confirmed live and documented in
# docs/runbooks/herdr.md §7.1.2. They appear on SUCCESSFUL submissions too, so their presence does
# NOT prove the buffer is unsubmitted — see `pane_shows_unsubmitted_paste` for what is actually
# being claimed.
_PASTE_AFFORDANCES: tuple[str, ...] = ("[Pasted text", "paste again to expand")
# A permission prompt VETOES the nudge. These are user-visible strings rather than a machine
# contract, so a future reword would cost this half of the check — which is why the positive
# affordance requirement carries it too, and why every unknown resolves to "do not nudge".
_PERMISSION_DIALOG_SIGNATURES: tuple[str, ...] = (
    "Do you want to", "and don't ask again", "No, and tell Claude")


def pane_shows_unsubmitted_paste(pane_read_stdout) -> tuple[bool, str]:
    """Is it SAFE to send a bare Enter to this pane? Returns (safe, reason).

    The honest claim is narrower than the name suggests, and #700's review is the reason it is
    stated here rather than implied: this does not prove the buffer is unsubmitted. §7.1.2 records
    that the same affordance shows on a successful submission. What it proves is that the pane is
    displaying an input-box paste affordance and is NOT sitting on a permission dialog — which is
    the question that matters, because an Enter accepts whatever is on screen and a bounded nudge
    count is not a bound on privilege.

    Fail-safe in every direction: an empty read, an unrecognised screen, a non-string, or any
    dialog signature all return False. The cost of a false negative is a handoff that fails closed
    exactly as it did before #700; the cost of a false positive is accepting a dialog nobody
    authorised. The dialog check runs FIRST because a scrollback paste marker can still be visible
    above a live dialog.
    """
    text = pane_read_stdout if isinstance(pane_read_stdout, str) else ""
    for signature in _PERMISSION_DIALOG_SIGNATURES:
        if signature in text:
            return (False, f"a permission dialog is on screen ({signature!r}) — an Enter would "
                           "accept it")
    for affordance in _PASTE_AFFORDANCES:
        if affordance in text:
            return (True, f"the input box shows a collapsed paste ({affordance!r})")
    return (False, "no collapsed-paste affordance on screen — the pane's state is unknown")


def build_teardown_argv(pane: str) -> list[str]:
    validate_pane_id(pane)
    return ["herdr", "pane", "close", pane]


# ---------------------------------------------------------------------------
# #718 — inserting a PROSE prompt into a pane (the context meter's act tier)
# ---------------------------------------------------------------------------

# Measured live 2026-07-29 (docs/planning/2026-07-29-718-meter-inserts-prompt.md §5b). An `Enter`
# sent immediately after the paste returns rc 0 and submits NOTHING: the text sat in the input box
# through two further goal-driven turns and past goal completion, until a later Enter arrived. With
# 1.5 s between the paste and the Enter — both still inside a Stop hook — it submits.
#
# The value is EVIDENCED, NOT TUNED: 1.5 s is the only delay measured. Shorter values are untested,
# so lowering it needs its own measurement rather than an argument. The hypothesis that produced
# this experiment ("Claude Code cannot read input while a hook runs, so no in-hook delay can work")
# was REFUTED by round 2 — the round-1 failure was a race with the bracketed paste, not a barrier.
INSERT_SUBMIT_DELAY_S = 1.5


def validate_inserted_prompt(text) -> None:
    """Refuse anything that is not PROSE. Raises `LauncherError`; returns None when acceptable.

    THE finding this whole subcommand exists to encode, measured live on three panes and then
    re-measured under a controlled goal loop (#718 §2): a **bare slash command is inert**.
    Inserted into a session with an unmet `/goal`, `/tasklist` sat queued through five
    goal-driven turns and was taken up only after the goal was achieved — whereas prose inserted
    the same way was acted on in 17 seconds.

    So the discriminator is the leading character, not a token count: the client treats a message
    beginning with `/` as a command for its own input box rather than as a turn. Prose that merely
    CONTAINS a slash command (`okay, please run /rawgentic:pane-handoff`) is the intended shape and
    stays legal — that exact form is what was measured executing a skill in ~60s.

    Fail-CLOSED, unlike most of this module's env handling: what it guards is a delivery that
    silently achieves nothing, and a silent no-op is precisely the defect #718 was filed about.
    """
    if not isinstance(text, str) or not text.strip():
        raise LauncherError("refusing to insert an empty prompt")
    if text.strip().startswith("/"):
        raise LauncherError(
            "refusing to insert a bare slash command — it is INERT inside a /goal loop (measured "
            "#718: queued through five goal-driven turns, consumed only once the goal was met). "
            "Insert PROSE that asks for the skill instead, e.g. "
            "'please run the rawgentic pane-handoff skill now'")


def insert_prompt(*, pane: str, text: str, runner=None,
                  sleep=time.sleep) -> tuple[bool, str]:
    """Paste PROSE into `pane` and submit it. Returns (delivered, reason).

    The sequence is read → paste → delay → Enter, and every element of that order is load-bearing:

    * The **read comes first** because an `Enter` accepts whatever is on screen. If a permission
      dialog is up, typing then submitting would answer somebody's dialog rather than start a turn.
      That check is fail-CLOSED (an unreadable pane refuses) because the harm is irreversible,
      while the cost of refusing is one missed insertion the caller can retry.
    * The **delay sits between the paste and the Enter** — see `INSERT_SUBMIT_DELAY_S`. A delay
      before the paste would not help; that is why the ordering is asserted by a test.

    Returns `delivered`, never `submitted`: rc 0 on `send-keys` proves the keystroke was
    transported, not that a turn began (#718 §5b — the round-1 failure returned rc 0 twice and
    submitted nothing). Nothing here scrapes the screen to claim otherwise, because the one
    affordance available (`pane_shows_unsubmitted_paste`) requires a COLLAPSED paste marker that
    short prose never produces, and its own contract says that marker appears on successful
    submissions too. An honest "delivered" beats a verification that cannot discriminate.
    """
    validate_inserted_prompt(text)
    send_text, send_keys = build_send_text_argv(pane=pane, text=text)
    runner = _default_runner if runner is None else runner

    def dialog_veto():
        """None when it is safe to type/submit, else the reason it is not."""
        read = runner(build_pane_read_argv(pane))
        if getattr(read, "returncode", 1) != 0:
            return "the pane read failed, so its state is unknown"
        screen = read.stdout if isinstance(getattr(read, "stdout", None), str) else ""
        for signature in _PERMISSION_DIALOG_SIGNATURES:
            if signature in screen:
                return f"a permission dialog is on screen ({signature!r}) — an Enter would accept it"
        return None

    blocked = dialog_veto()
    if blocked:
        return (False, f"refusing to type: {blocked}")

    if getattr(runner(send_text), "returncode", 1) != 0:
        return (False, "herdr pane send-text failed — nothing was pasted")
    sleep(INSERT_SUBMIT_DELAY_S)

    # CHECK AGAIN, immediately before the Enter (#718 Step-11 diff review, High). One pre-paste
    # snapshot is not enough: the delay is a 1.5 s window in which a permission dialog can appear,
    # and an Enter fired into it accepts the dialog instead of submitting a turn. Re-reading shrinks
    # that window to the round trip. The prose stays pasted-but-unsubmitted, which `Stop`'s next
    # firing can recover with a bare Enter rather than a re-paste (`build_send_enter_argv`).
    blocked = dialog_veto()
    if blocked:
        return (False, f"refusing to submit: {blocked}. The prose is pasted but UNSUBMITTED")

    if getattr(runner(send_keys), "returncode", 1) != 0:
        return (False, "herdr pane send-keys failed — the prose is pasted but unsubmitted; a bare "
                       "Enter is the recovery (see build_send_enter_argv)")
    return (True, f"delivered: pasted, waited {INSERT_SUBMIT_DELAY_S}s, sent Enter. Submission is "
                  "not independently verifiable from an exit code (#718 §5b)")


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


def parse_pane_agent_status(pane_get_stdout: str) -> str | None:
    """Pull `agent_status` out of a `herdr pane get` response (#694).

    Returns None when absent, empty, non-string, or unparseable. The caller treats None as
    NOT SETTLED, never as a pass: a status that cannot be read is not evidence that the
    successor can receive a paste, and what this gates (handing over work, then retiring the
    predecessor) is irreversible.

    Deliberately a sibling of `parse_pane_agent_session` over the same response rather than an
    extra return value from it — the two are read at different moments (identity once, readiness
    repeatedly on a poll) and every existing caller of that function keeps its exact contract.
    """
    try:
        doc = json.loads(pane_get_stdout)
    except (ValueError, TypeError):
        return None
    node = doc.get("result", doc) if isinstance(doc, dict) else None
    if not isinstance(node, dict):
        return None
    pane = node.get("pane") if isinstance(node.get("pane"), dict) else node
    if not isinstance(pane, dict):
        return None
    status = pane.get("agent_status")
    return status if isinstance(status, str) and status else None


def _path_key(value, base: str | None) -> tuple[bool, tuple[str, ...]] | None:
    """`value` as `(is_absolute, normalized components)`, resolved against `base` if relative.

    Lexical only — `os.path.normpath`, never `realpath`. Returns None for anything that is not a
    usable path string.

    **`is_absolute` is part of the key, and dropping it was a real false accept** (found by
    `test_the_false_accept_the_guard_prevents` while this function was being written): the
    component split discards the empty leading field of an absolute path, so a bare component
    comparison made `/projects/rawgentic` equal to `./projects/rawgentic` — two different
    directories, matched with no base in sight. Keeping absoluteness in the key forces that pair
    through the base-resolution branch, where it correctly fails.

    **A `..` component is REFUSED outright** (Step-8a cross-model review, High): lexical
    normalization and the filesystem disagree the moment `..` follows a symlink — with `/ws/link`
    → `/other/dir`, `link/../project` normalizes to `/ws/project` while traversal reaches
    `/other/project`, so the gate could accept a wrong directory and authorize a teardown. No real
    producer writes `..` here (the switch skill writes `./projects/<name>` or its absolute form),
    so refusing removes the entire class instead of reasoning about which symlink layouts are safe.
    A **relative or `..`-bearing base is not used** for the same reason: it cannot establish which
    directory either side names.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    if os.pardir in value.split(os.sep):
        return None
    if not os.path.isabs(value) and isinstance(base, str) and base.strip() \
            and os.path.isabs(base) and os.pardir not in base.split(os.sep):
        value = os.path.join(base, value)
    norm = os.path.normpath(value)
    return (os.path.isabs(norm),
            tuple(p for p in norm.split(os.sep) if p not in ("", ".")))


def project_paths_equivalent(recorded, expected, project_root: str | None = None) -> bool:
    """Do two `project_path` spellings denote the SAME directory? PURE — no filesystem access.

    #800: the registry's `project_path` is written by a MODEL following the switch skill, and it
    does not reliably pick one representation. Measured on two consecutive `ad-hoc-handoff` runs
    with identical inputs (2026-08-01): one successor wrote `./projects/claude-skills`, the next
    wrote `/home/rocky00717/rawgentic/projects/claude-skills`. A bare `!=` therefore refused a bind
    that had actually succeeded, and no caller-side value fixes it — the variance is on the
    producer's side.

    The rule, in order:

    1. Either side not a usable non-empty string → False (a row with no `project_path` reaches
       here as None and must never become a match).
    2. Equal as `(is_absolute, normalized components)` → True. This alone settles `./a/b` vs `a/b`
       vs `a/b/`. Absoluteness is part of that key deliberately — see `_path_key`.
    3. `project_root` given → resolve each RELATIVE side against it and compare again. This is the
       repo's established convention for exactly this value: `context_meter.py`'s `bound_project`
       uses `os.path.normpath(os.path.join(workspace_root, rel))` and `wal-lib.sh` the shell
       equivalent. `os.path.join` leaves an already-absolute side untouched, so a base can never
       corrupt an absolute row, and because BOTH sides share one base a wrong base can only fail
       to complete a mixed pair — never manufacture a match between two different directories.
    4. Otherwise False — the pre-#800 exact comparison. A caller that supplies no base loses the
       fix, never its safety.

    **Deliberately NOT a component-suffix match**, which is the obvious base-free alternative and
    was refuted by probe: `projects/rawgentic` is a suffix of `/other/ws/projects/rawgentic`, and
    `rawgentic` is a suffix of `/home/x/rawgentic/projects/rawgentic`. Both would be false ACCEPTS
    on a gate whose whole job is refusing the wrong project.

    **Deliberately lexical.** This function is pure and its callers compare paths that need not
    exist, so the module's honest bound stands: it claims nothing about symlinks.
    """
    a = _path_key(recorded, None)
    b = _path_key(expected, None)
    if a is None or b is None:
        return False
    if a == b:
        return True
    if not (isinstance(project_root, str) and project_root.strip()):
        return False
    ra = _path_key(recorded, project_root)
    rb = _path_key(expected, project_root)
    return ra is not None and ra == rb


def registry_has_session(registry_text: str, session_id: str, *,
                         expected_project: str | None = None,
                         expected_project_path: str | None = None,
                         project_root: str | None = None) -> bool:
    """A `claude_docs/session_registry.jsonl` line carrying the NEW session id.

    `expected_project` / `expected_project_path` are #665 additions and both default to None, so
    every #611 caller keeps its exact behaviour. When supplied, all three fields must appear on
    the SAME line: matching the session id alone let a successor bound to the WRONG project pass
    this check, claim the handoff, and retire a healthy predecessor before continuing in the
    wrong repository (design §5, pass-2 finding 2).

    The project pair is matched, not just the label, because nothing here establishes that
    project labels are globally unique. The LABEL is compared exactly — that is what makes a
    successor bound to a different project fail. The PATH is compared path-equivalently via
    `project_paths_equivalent` (#800), because the producer is a model and does not reliably
    choose between the workspace-relative `./projects/<name>` and its absolute spelling; pass
    `project_root` (the workspace root those relative paths are relative to) to enable that.
    With no `project_root` the path comparison stays exact, so the fix is opt-in per caller and
    fail-closed. Still lexical, never a filesystem canonicalisation — it claims nothing about
    symlinks.
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
        if expected_project_path is not None and not project_paths_equivalent(
                rec.get("project_path"), expected_project_path, project_root):
            continue
        return True
    return False


_REGISTRY_CONTRACT_TAIL = ("claude_docs", "session_registry.jsonl")


def _workspace_root_of_registry(registry_path: str) -> str | None:
    """The workspace root a registry file implies, or None when the path is off-contract.

    #800: `retire_predecessor` compares against `position["project_path"]` — possibly the
    workspace-relative spelling — but holds no workspace root of its own. The switch skill writes
    the registry at `<workspace root>/claude_docs/session_registry.jsonl`, the SAME contract that
    makes `project_path` workspace-relative, so the directory two levels up is that root.

    The tail check is load-bearing, not decoration (Step-4 F1): deriving unconditionally would turn
    `/x/registry.jsonl` into the base `/`, and a row claiming `/projects/rawgentic` would then
    compare equal to an expectation of `./projects/rawgentic` — a false ACCEPT manufactured by a
    wrong base. Off-contract → None → the path comparison stays exact, which is fail-closed.

    **Resolved, not lexical (Step-11 cross-model review, High).** An earlier revision derived the
    root from `abspath`, so a registry reached through a SYMLINKED `claude_docs` — or via
    `symlink/../claude_docs/...` — vouched for the workspace it merely LOOKED like it was in, while
    the bytes came from somewhere else entirely. Reproduced live before fixing: with
    `<ws>/claude_docs` symlinked to `/foreign/claude_docs`, the derivation returned `<ws>`. It now
    refuses a `..` component outright and derives from `os.path.realpath`, so the root it reports is
    the root of the file it will actually read. `--registry` is passed through unvalidated at all
    four CLI call sites (verified), so this helper cannot assume a caller confined it.

    **What this does NOT fix, stated because a narrowed hole is not a closed one:** a foreign
    registry whose row spells `project_path` RELATIVELY still satisfies the exact comparison, which
    is the pre-#800 code path — verified by execution on the same fixture (the exact comparison
    matched it too). Closing that needs the gate to REFUSE a registry resolving outside the
    predecessor's own workspace, which in turn needs the retire-site fixtures rebuilt to model the
    real repo_root/project_path relationship. Out of #800's scope, filed as a follow-up.
    """
    if not isinstance(registry_path, str) or not registry_path.strip():
        return None
    if os.pardir in registry_path.split(os.sep):
        return None
    parts = os.path.realpath(registry_path).split(os.sep)
    if tuple(parts[-2:]) != _REGISTRY_CONTRACT_TAIL:
        return None
    root = os.sep.join(parts[:-2])
    return root or os.sep


def _trusted_registry_base(registry_path: str, project_path, repo_root) -> str | None:
    """The registry's implied workspace root, but ONLY when already-validated state agrees.

    Step-8a cross-model review, High/security: `_workspace_root_of_registry` treats ANY path ending
    in the contract tail as proof of its workspace root, so a registry handed in from a DIFFERENT
    workspace — carrying a row for the same session id and the same project label — would have its
    foreign root used to resolve a relative expectation, and could then match its own absolute row
    and authorize an irreversible teardown.

    `retire_predecessor` already holds independently validated evidence of where it is:
    `position["repo_root"]` was confined below `--project-root` by `resolve_cwd` before anything was
    written. So the derived base is trusted only when resolving `position["project_path"]` against
    it lands on that same repo root. A foreign registry fails that, the base is dropped, and the
    path comparison falls back to exact equality — fail-closed.

    The realpath fallback exists because `resolve_cwd` canonicalises: on a symlinked workspace the
    lexical comparison can disagree with a perfectly valid layout. This site already touches the
    filesystem, so resolving here costs nothing that is not already paid.
    """
    base = _workspace_root_of_registry(registry_path)
    if base is None or not isinstance(repo_root, str) or not repo_root.strip():
        return None
    if project_paths_equivalent(project_path, repo_root, base):
        return base
    if not isinstance(project_path, str) or not project_path.strip():
        return None
    candidate = project_path if os.path.isabs(project_path) \
        else os.path.join(base, project_path)
    try:
        if os.path.realpath(candidate) == os.path.realpath(repo_root):
            return base
    except OSError:
        return None
    return None


def registry_match_diagnosis(registry_text: str, session_id: str, *,
                             expected_project: str | None = None,
                             expected_project_path: str | None = None,
                             project_root: str | None = None) -> str:
    """WHY `registry_has_session` did not match — one line, for the failure report.

    #800's second half. A never-matching comparison and a plain poll timeout produced
    BYTE-IDENTICAL output, so a gate that could never pass was indistinguishable from a successor
    that was merely slow. That cost the #875 campaign a published wrong diagnosis. This says which
    field disagreed, and names the base, because the base is what decides a path comparison.

    Reports on the LAST row carrying the session id: on a poll the newest row is the successor's
    own. Never raises — a diagnosis that throws while explaining a failure is worse than no
    diagnosis, so unparseable lines are skipped exactly as the matcher skips them, a missing
    registry text is treated as empty, and an empty session id is named rather than reported as a
    row that could not be found.
    """
    text = registry_text or ""
    if not session_id:
        return ("no session id was supplied to look up, so the registry could not be checked at "
                "all — this is a caller defect, not a successor that failed to bind")
    if registry_has_session(
            text, session_id, expected_project=expected_project,
            expected_project_path=expected_project_path, project_root=project_root):
        return "matched"
    found = None
    malformed = 0
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            malformed += 1
            continue
        if not isinstance(rec, dict):
            # Step-11 inline finding IF-2: a line that PARSES but is not an object was skipped
            # without being counted, so it too was reported as "the successor never wrote its
            # bind" — the narrower form of the same misdirection.
            malformed += 1
            continue
        if rec.get("session_id") == session_id:
            found = rec
    if found is None:
        # Step-8a cross-model review, Medium: skipping malformed lines and then reporting "the
        # successor never wrote its bind" sends the operator to permission prompts when the real
        # story is a truncated row — the same wrong-diagnosis class this function exists to remove.
        if malformed:
            return (f"no parseable registry row carries session {session_id!r}, and {malformed} "
                    f"malformed line(s) were skipped — the registry may be corrupt (a torn or "
                    f"interleaved write), which is a different failure from a successor that "
                    f"never bound")
        return (f"no registry row carries session {session_id!r} — the successor never wrote its "
                f"bind (it may have been blocked at a permission prompt, or never ran the switch)")
    if expected_project is not None and found.get("project") != expected_project:
        return (f"the registry row for session {session_id!r} records project "
                f"{found.get('project')!r}, but this handoff expects {expected_project!r} — the "
                f"successor bound the WRONG project")
    base = project_root if (isinstance(project_root, str) and project_root.strip()) else None
    return (f"the registry row for session {session_id!r} records project_path "
            f"{found.get('project_path')!r}, which is not the same directory as "
            f"{expected_project_path!r} (resolved against project_root {base!r})")


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


def _corroborates(row: dict, cond, last: dict | None) -> bool:
    """#782 — is this sentinel-less trusted-origin row CORROBORATION of the goal we already trust?

    True only when every one of these holds, which is what keeps both #758 forgery directions
    dead while unblocking the ordinary post-evaluation teardown:

    - `met` is literally `False` — a sentinel-less `met: true` is NOT corroboration; since #880
      it is handled by the separate `_retires` predicate below, whose own preconditions keep the
      forgery directions dead (`test_a_sentinelless_met_true_after_a_trusted_arm_retires_the_goal`).
    - the condition is a non-blank string AND **byte-equal to the condition of the last row we
      already trust** — so a sentinel-less row can never inject a goal of its own choosing
      (`test_a_forged_sentinelless_unmet_row_cannot_inject_a_phantom_goal`, where no trusted row
      exists at all, so `last` is None and this returns False).

    Measured basis (2026-08-01, 25 most recent transcripts): of 122 trusted-origin goal_status
    rows, 85 are exactly this shape — unstamped, `met: false`, carrying the armed condition
    verbatim — because that is what the Stop hook's own evaluation writes. The repo contains no
    writer of these rows (`grep -rln goal_status hooks/` is reader-only), so the shape cannot be
    fixed at the source from here.
    """
    return (row.get("met") is False
            and isinstance(cond, str) and bool(cond.strip())
            and last is not None and cond == last.get("condition"))


def _retires(row: dict, cond, last: dict | None) -> bool:
    """#880 Defect D (owner decision 2026-08-04, narrow accept) — is this
    sentinel-less row the Stop hook's SATISFIED evaluation of the goal we
    already trust?

    When a goal is MET the harness auto-clears it and the `met: true`
    evaluation row IS the record of that retirement — no separate clear row is
    ever written (sessions f5411321 / 160a9114: two goal rows total, arm +
    met:true evaluation). Before this predicate, strict mode refused that
    state, so the retire path was permanently unavailable after ANY successful
    run, and the refusal's own REMEDY (/goal clear) was a no-op on the gone
    goal (measured: two retries, byte-identical refusal).

    Why this opens no forgery direction (#758 posture, owner-ratified over
    four review rounds): the `sentinel` field is NOT an authentication token —
    any writer able to place top-level transcript rows can already set
    `sentinel: true` and be fully trusted today. The real provenance boundary
    is top-level `attachment` position (nested rows in user/tool content are
    never read by this reader), which is unchanged. On top of that this
    predicate requires a prior genuinely-TRUSTED row to have established the
    exact condition, byte-equal — so an accepted row can never introduce a
    condition of its own, and every chain stays anchored to the last trusted
    condition. A different condition, a blank condition, no prior trusted row,
    and torn tails all still refuse.
    """
    return (row.get("met") is True
            and isinstance(cond, str) and bool(cond.strip())
            and last is not None and cond == last.get("condition"))


def live_owner_goal(transcript_text: str, *, strict: bool = False) -> str | None:
    """The predecessor's LIVE owner goal, from sentinel-bearing rows only (#758).

    Trust boundary: `_find_goal_status` is deliberately recursive, so structured user or
    tool content embedded in a transcript line can carry a forged `type: goal_status`
    object. Only the harness's own attachments carry `sentinel: true`, so this reader
    keys on the sentinel — a forged sentinel-less row can neither inject a phantom goal
    nor spoof "already cleared" (a forged `met: true` carrying the genuine condition was
    exactly the #758 pass-2 bypass through `goal_currently_unmet`, which scans without a
    sentinel check).

    Origin-bound, not merely sentinel-keyed (#758 Step-8a wave): `sentinel: true` is a
    field any forged object can carry, so trust requires the row to sit at the REAL
    harness attachment location — the record's TOP-LEVEL `attachment` object — never a
    recursively discovered nested one (`_iter_goal_status`'s recursion exists for other,
    non-destructive readers). And `met` must be a literal boolean: a row whose `met` is
    missing or malformed is not trusted at all — a corrupt row must not read as "cleared".

    Liveness is decided by the LAST trusted row with a non-blank condition:
    `met` False → its condition is live (returned VERBATIM); `met` True → the guard was
    cleared → None; no trusted rows → None. (`last_unmet_goal_condition` is historical —
    it returns a met:false row even after a later clear — so it is deliberately not used.)

    Strict mode (#758 Step-11 wave): with `strict=True`, ABSENCE of trustworthy evidence
    is not the same verdict as a proven "no goal". A transcript is append-only, so only
    the TAIL can be torn — when the newest goal-bearing evidence is an unparseable
    goal_status line or a trusted-origin row that fails validation, and no newer VALID
    trusted row supersedes it, strict mode raises `LauncherError` instead of silently
    falling back to an older row (stale guard) or to None (phantom "already cleared").
    Lenient mode (the default) keeps the historical skip-and-continue behavior for
    non-destructive readers.
    """
    last: dict | None = None
    suspicious: str | None = None
    for line in transcript_text.splitlines():
        if "goal_status" not in line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            suspicious = "an unparseable goal_status-bearing line (torn write?)"
            continue
        if not isinstance(rec, dict):
            continue
        row = rec.get("attachment")
        if not isinstance(row, dict) or row.get("type") != "goal_status":
            continue
        cond = row.get("condition")
        valid = (row.get("sentinel") is True and isinstance(row.get("met"), bool)
                 and isinstance(cond, str) and bool(cond.strip()))
        if valid:
            last = row
            suspicious = None
        elif _corroborates(row, cond, last):
            # #782 — a Stop-hook EVALUATION row is trusted-origin and sentinel-LESS, so the
            # sentinel test alone classed it "fails validation" and strict mode refused every
            # post-evaluation teardown (#802, hit three consecutive sessions). But an unstamped
            # met:false row carrying the SAME condition as the row we already trust AGREES with
            # the state we already hold: it is corroboration, not ambiguity. Neither forgery
            # direction is opened — it cannot inject a phantom goal (the condition is one a
            # sentinel row already established) and it cannot spoof a clear (met is False, so
            # the goal stays live). Deliberately does NOT clear `suspicious`: corroboration is
            # neutral, so a torn line earlier in the tail still refuses.
            pass
        elif _retires(row, cond, last):
            # #880 — the SATISFIED evaluation (see _retires's contract). Setting `last` is what
            # flips the final verdict to "no live goal": the verdict logic below is unchanged.
            # Like corroboration, this does NOT clear `suspicious` — a torn line earlier in the
            # tail still refuses.
            last = row
        else:
            suspicious = "a trusted-origin goal_status row that fails validation"
    if strict and suspicious is not None:
        raise LauncherError(
            f"the newest goal evidence in the transcript is {suspicious} — refusing to "
            "derive a live-goal verdict from ambiguous evidence on a destructive path "
            "(#758): a torn or malformed newest row must not read as 'no goal' or fall "
            "back to a stale one. REMEDY: since #782 the ORDINARY case — an armed goal "
            "whose newest row is a sentinel-less Stop-hook evaluation agreeing with it — "
            "no longer reaches here, so this refusal now means the tail is genuinely "
            "unreadable: a torn write, a malformed 'met', or a row proposing a DIFFERENT "
            "condition. Inspect the last few goal_status lines of this transcript before "
            "assuming otherwise. REMEDY, conditional (#880): if a goal is still armed in this "
            "pane, running '/goal clear' and retrying appends a trusted row and lets teardown "
            "proceed — but if '/goal clear' reports no goal is set, it writes NOTHING this "
            "validator reads, so do not retry in a loop; inspect the transcript tail by hand "
            "or hand off WITHOUT retiring this pane by re-running with '--no-teardown' and "
            "relaying the manual retirement steps yourself")
    if last is None or last.get("met") is not False:
        return None
    return last["condition"]


def validate_goal_carry(successor_goal: str, predecessor_live_goal: str | None, *,
                        approved_answer: str | None = None) -> tuple[bool, str, bool]:
    """#758 — the successor's goal must be the predecessor's owner-authored goal VERBATIM.

    Comparison is on ARMED forms (`armed_condition`) after ONE documented normalization:
    a single trailing newline is stripped from the successor text, because a
    goal-condition FILE ends with a newline while the armed row never carries one. No
    other normalization — `strip()` equality was rejected at the design gate (pass-1 F4):
    byte-identical or refused keeps the rule legible.

    `approved_answer` is the owner's verbatim yes/no answer approving a DIFFERENT goal
    text (the `--goal-rewrite-approved` value). It is a caller assertion — no crypto root
    of trust exists, so the enforceable layer is the skill prose gating it on an explicit
    owner question plus the audit record the CLI writes (AC3 permits "rejects or flags":
    unapproved difference → rejected; approved → flagged).

    `predecessor_live_goal is None` means no live guard existed at validation — nothing
    to carry, nothing to validate.

    A mismatch reason carries ONLY lengths and the numeric first-divergence offset —
    never goal content (an owner goal must not leak into error logs; pass-1 F8).

    Returns `(ok, reason, used_override)` — `used_override` is True ONLY when the goals
    actually differed and an affirmative owner answer authorized the rewrite (Step-11
    wave: the audit field must mean "an override was consumed", never merely "the flag
    was present"). An approval answer counts ONLY when it reads affirmative — the skill
    asks a yes/no question, so the answer must start with "yes" or "approved"
    (case-insensitive): an owner's "no, do not change it" passed through the flag must
    never authorize the rewrite.
    """
    if predecessor_live_goal is None:
        return (True, "no live predecessor goal — nothing to validate", False)
    succ = successor_goal[:-1] if successor_goal.endswith("\n") else successor_goal
    armed_succ, succ_trunc = armed_condition(succ)
    armed_pred, pred_trunc = armed_condition(predecessor_live_goal)
    if armed_succ == armed_pred and not (
            (succ_trunc or pred_trunc) and succ != predecessor_live_goal):
        # Truncation guard (Step-11 wave): two over-cap texts sharing the truncated
        # armed prefix are NOT a verbatim carry — when either side truncates, the RAW
        # texts must match too, else the armed prefix vouches for suffixes it never saw.
        return (True, "verbatim carry confirmed (armed forms identical)", False)
    answer = approved_answer.strip() if isinstance(approved_answer, str) else ""
    if answer and answer.lower().startswith(("yes", "approved")):
        return (True, f"goal rewrite approved by owner: {approved_answer!r} "
                      "(flagged in the audit output)", True)
    if answer:
        return (False,
                f"--goal-rewrite-approved was given a NON-AFFIRMATIVE answer "
                f"({len(answer)} chars, does not start with yes/approved) — an owner "
                f"answer that is not a yes never authorizes a goal rewrite (#758)", False)
    offset = next((i for i, (a, b) in enumerate(zip(armed_succ, armed_pred)) if a != b),
                  min(len(armed_succ), len(armed_pred)))
    return (False,
            f"successor goal differs from the predecessor's owner-authored goal "
            f"(#758 verbatim-carry rule): successor {len(armed_succ)} chars, "
            f"predecessor {len(armed_pred)} chars, first divergence at offset {offset}. "
            f"Goals are owner-authored and carried verbatim — read the live goal with "
            f"read-goal-condition, never retype or extend it. A genuine goal change "
            f"needs an explicit owner yes/no first (then pass "
            f"--goal-rewrite-approved '<the answer>'), or use --no-teardown for an "
            f"additive helper handoff", False)


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
    """The seven-step mid-child ladder (#665, `queue_revalidated` added by #840). A SEPARATE tuple,
    not a mutation of #611's three: that contract is pinned by its own test and a launch handoff
    still has exactly three checks to make."""
    return [dict(s) for s in _MID_CHILD_VERIFICATION_STEPS]


QUEUE_REVALIDATED_STEP = "queue_revalidated"


# "Nobody passed one, so derive the production probe" — distinct from an explicit `None`, which
# means "run WITHOUT corroboration". A plain `None` default cannot tell those apart, and the
# difference decides whether a stale-file sibling jams the rung. Defined above its use because a
# default argument is evaluated at definition time (the same trap `runner` documents below).
# Canonical decimal ASCII, matching the receipt validator. `"01"`, `"001"` and the Unicode
# digit `"١"` all pass `str.isdigit()` and all `int()` to the same number.
_CANONICAL_ISSUE_KEY_RE = re.compile(r"\A(?:0|[1-9][0-9]*)\Z")


_DERIVE_PROBE = object()


def produce_queue_revalidated(campaign_context, *, runner=None,
                              issue_state_probe=_DERIVE_PROBE) -> tuple[bool, str]:
    """The `queue_revalidated` rung's PRODUCER. Reads the receipt; returns ``(passed, reason)``.

    This is deliberately launcher-owned rather than caller-supplied, which was the peer consult's
    sharpest point: *agent-provided verification JSON must not satisfy this rung.* The launcher
    reads the durable receipt and compares it against a head it observes itself, so the evidence
    cannot be the assertion of the thing being checked.

    ``campaign_context`` is ``{driver_state_path, repo_root}`` (``generation`` is accepted and
    ignored here — it is the caller's own bookkeeping). ``None`` means "not a campaign", which is
    the ad-hoc handoff: that path uses the three-rung launch ladder and never carries this rung at
    all, so there is nothing to produce. If the rung were somehow in the ladder with no context,
    the result stays UNREPORTED and `evaluate_verifications` fail-closes — safe by construction.

    **A campaign with no receipt FAILS** (Step-11 finding 1, owner decision 2026-08-02). An earlier
    revision of this function passed it with the reason recorded, on a compatibility argument: every
    campaign predating #840 has no receipt, so failing them refuses every existing mid-child
    handoff. The reviewer refuted that and the refutation is decisive — a refusal is RECOVERABLE by
    running `revalidate-children`, whereas silent passage is the one failure direction this design
    forbids, and nothing in the code would ever have created that first receipt or produced the
    refusal that prompts someone to. Arming a campaign is one command; a gate that never fires is
    not a gate.

    Fail-CLOSED on everything it cannot read: an unobservable head, an unreadable state file, or a
    malformed receipt all refuse. What this gates is an irreversible teardown.
    """
    # `runner` defaults inside rather than in the signature: this function is defined next to the
    # ladder it serves, which is ABOVE `_default_runner`, and a default argument is evaluated at
    # definition time.
    runner = _default_runner if runner is None else runner
    if not isinstance(campaign_context, dict):
        return (False, "no campaign context supplied, so the receipt could not be read")
    state_path = campaign_context.get("driver_state_path")
    repo_root = campaign_context.get("repo_root")
    if not state_path or not repo_root:
        return (False, "campaign context needs both driver_state_path and repo_root; got "
                       f"{campaign_context!r}")
    driver_lib = _driver_lib()
    try:
        state = _locked_state_read(state_path)
    except (OSError, ValueError) as exc:
        return (False, f"could not read {state_path}: {type(exc).__name__}: {exc}")
    if not isinstance(state, dict):
        return (False, f"{state_path} does not hold a JSON object")
    # #840 Step-11 finding 1 (Critical, reproduced): this used to return `(True, "enforcement is
    # OFF")` for a campaign with no receipt, which made all three layers opt-in — nothing in the
    # code created the first receipt or produced the refusal that would prompt anyone to. Owner
    # decision 2026-08-02 closed it: a campaign with no receipt now FAILS the rung, with an
    # actionable reason. A refusal is recoverable by the skill this ships; silent passage is not.
    try:
        head = observe_head(repo_root, runner=runner)
    except LauncherError as exc:
        return (False, str(exc))
    # #840 round-8 High 1. The rung and the selection it gates must see the SAME queue. Without
    # the probe this call disagreed with `fresh_session_handoff` about which children are still
    # open: a durably `queued` sibling the probe confirms merged is eligible here and not there,
    # so the rung refused `#N: never revalidated` on a child nobody can revalidate — the skill's
    # worklist correctly returns nothing for it. Sixth unrecoverable jam, and the first one caused
    # by two callers of the same function being given different evidence.
    #
    # DERIVED here rather than required from callers: #695's own finding is that an optional
    # corroboration nobody threads in ships dead, and this producer has two production call sites
    # (`perform_handoff`, `retire_predecessor`) that would each have to remember. The parameter
    # exists for tests, which cannot reach a real `gh`; the default is the production path.
    probe = (_issue_state_probe_for(repo_root)
             if issue_state_probe is _DERIVE_PROBE else issue_state_probe)
    try:
        driver_lib.next_ready_issue(state, observed_head=head, issue_state_probe=probe)
    except driver_lib.QueueRevalidationRequired as exc:
        return (False, str(exc))
    except driver_lib.DriverStateError as exc:
        return (False, f"driver state refused the freshness check: {exc}")
    return (True, f"the queue is revalidated against {head}")


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


#: #927 — bounded reads and a bounded wall clock for the transport probe. The timeout exists
#: for a HUNG daemon, not for latency: the round trip is a local socket. 5 s rather than the
#: peer consult's proposed 2 s because a false negative costs one visible, self-healing
#: `inline` transition, and this host routinely runs several agent panes plus a test suite at
#: once — systematic spurious degradation is the failure #927 exists to end.
PROBE_MAX_BYTES = 64 * 1024
PROBE_TIMEOUT_S = 5


class _BoundedProc:
    """What `_bounded_probe_runner` hands back — the shape `_read` expects."""

    __slots__ = ("returncode", "stdout", "stderr")

    def __init__(self, returncode, stdout, stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _bounded_probe_runner(argv, timeout=PROBE_TIMEOUT_S):
    """Run a probe command, reading AT MOST `PROBE_MAX_BYTES` from each stream.

    `subprocess.run(capture_output=True)` buffers the whole of stdout before returning, so a
    size check afterwards prevents nothing — a chatty or hostile daemon can exhaust memory
    before the check ever executes (Step-11 finding 8). This stops reading at the cap, kills the
    child, and reaps it.

    Returns one byte OVER the cap when exceeded, so the caller's backstop check still trips.
    """
    # stderr is DEVNULL rather than a second PIPE, and that is load-bearing: two SEQUENTIAL
    # bounded reads on two pipes DEADLOCK. The child blocks writing whichever stream we are not
    # reading yet, so it never exits and our read never returns. Measured — the first version of
    # this function hung its own test. The probe never reads stderr, and an unread pipe is also
    # an unbounded buffer, so not creating it fixes both problems at once.
    with subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                          text=True, shell=False) as proc:
        try:
            # `read(n)` stops at n; the child is then KILLED rather than drained, so an
            # unbounded producer cannot keep us reading.
            out = proc.stdout.read(PROBE_MAX_BYTES + 1) if proc.stdout else ""
            if len(out) > PROBE_MAX_BYTES:
                proc.kill()
                proc.wait(timeout=timeout)
                return _BoundedProc(0, out)
            proc.wait(timeout=timeout)
            return _BoundedProc(proc.returncode, out)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            raise


def transport_probe(*, pane_ref, runner=None):
    """Is a pane-chain transport available RIGHT NOW? Returns ``(capability_ok, pane_ok, reason)``.

    Two tiers, reported SEPARATELY and never collapsed into one verdict (#927). Campaign
    creation legitimately has no pane reference, and it must still be able to record
    ``pane_chain`` from ``capability_ok`` alone. A single collapsed verdict would report
    ``inline`` there and silently preserve the very default this issue exists to invert — the
    design's own AC-1 regression, caught in review before it shipped.

    Tier 1 (capability) reuses the ``herdr pane list`` shape that `_pane_inventory` already
    hardens; it needs no pane reference, which is the point — `--current` is exactly what fails
    in a pane-less session (see this module's header). Tier 2 (liveness) asks about ONE pane and
    requires the answer to be about that pane: an rc 0 response describing a different pane is
    not evidence about this one.

    ``HERDR_ENV`` / ``HERDR_PANE_ID`` are a HINT that supplies a candidate id, never proof — the
    round trip is the proof. Fail-open in every direction: this can degrade a boundary to
    ``inline``, never raise into it.
    """
    runner = runner or _bounded_probe_runner

    def _read(argv):
        """(parsed doc, error token, returncode). Never raises."""
        proc = runner(argv, timeout=PROBE_TIMEOUT_S)
        rc = getattr(proc, "returncode", 1)
        out = getattr(proc, "stdout", "") or ""
        # The size check is a BACKSTOP. The real bound is in `_bounded_probe_runner`, which
        # stops reading and reaps the child at the cap — a post-hoc `len()` on an
        # already-buffered string prevents nothing (Step-11 finding 8). It is still checked here
        # because the runner is injectable and a caller may supply an unbounded one.
        if len(out) > PROBE_MAX_BYTES:
            return None, "oversized", rc
        if rc != 0:
            return None, "rc", rc
        try:
            return json.loads(out), None, rc
        except (ValueError, TypeError):
            return None, "unparseable", rc

    try:
        doc, err, _rc = _read(["herdr", "pane", "list"])
        if err == "oversized":
            return (False, False, "probe_oversized")
        if err == "unparseable":
            return (False, False, "probe_unparseable")
        if doc is None:
            return (False, False, "herdr_unreachable")
        node = doc.get("result", doc) if isinstance(doc, dict) else None
        if not isinstance(node, dict) or not isinstance(node.get("panes"), list):
            return (False, False, "probe_unparseable")

        # Capability is proven from here on; only tier 2 can still fail.
        if not pane_ref:
            return (True, False, "no_pane_ref")
        try:
            validate_pane_id(pane_ref)
        except LauncherError:
            # A hostile or mistyped $HERDR_PANE_ID never reaches a subprocess.
            return (True, False, "invalid_pane_ref")

        doc2, err2, rc2 = _read(["herdr", "pane", "get", pane_ref])
        if err2 == "oversized":
            return (True, False, "probe_oversized")
        if err2 == "unparseable":
            return (True, False, "probe_unparseable")
        if doc2 is None:
            # rc 2 is OUR bug (a malformed invocation), rc 1 is herdr saying the pane is gone.
            # Collapsing them would present an implementation error as an absent pane and lose
            # the loud signal the design promised (Step-11 finding 10).
            if rc2 == 2:
                return (True, False, "probe_usage_error")
            return (True, False, "pane_not_found")
        node2 = doc2.get("result", doc2) if isinstance(doc2, dict) else None
        pane = node2.get("pane") if isinstance(node2, dict) else None
        if not isinstance(pane, dict):
            return (True, False, "probe_unparseable")
        if pane.get("pane_id") != pane_ref:
            return (True, False, "probe_identity_mismatch")
        # The workspace is the pane id's prefix — asserted live against herdr 0.8.0 rather than
        # assumed (`w1:pKS` -> `w1`).
        if pane.get("workspace_id") != pane_ref.split(":")[0]:
            return (True, False, "probe_identity_mismatch")
        return (True, True, "probe_ok")
    except FileNotFoundError:
        return (False, False, "herdr_absent")
    except subprocess.TimeoutExpired:
        return (False, False, "probe_timeout")
    except Exception as exc:  # pylint: disable=broad-except
        # Fail-open is the whole contract: an unpredicted error degrades the transport, it does
        # not take the boundary down with it.
        return (False, False, f"probe_error:{type(exc).__name__}")


def resolve_creation_transport(*, runner=None) -> tuple[str, str]:
    """The `preferred_transport` a NEW campaign should record. Returns ``(transport, reason)``.

    #927 AC 1: the preference is DERIVED by probing, not asked at setup and not defaulted. This
    deliberately consults TIER 1 ONLY — a campaign being created has no pane reference of its
    own, and requiring one would fail closed on every new campaign while herdr was perfectly
    healthy. That failure mode is why `transport_probe` reports the tiers separately.

    Note what this does NOT do: it never *upgrades* anything. It is the creation seam only. An
    existing campaign's recorded preference is changed exclusively by the sanctioned
    `transport set` command.
    """
    # Lazy import, matching this module's existing convention (see :4072).
    import driver_lib  # pylint: disable=import-outside-toplevel

    capability_ok, _pane_ok, reason = transport_probe(pane_ref=None, runner=runner)
    if capability_ok:
        # Tier 1 alone answers creation, so `no_pane_ref` is the expected reason here and is
        # NOT a degradation — report the capability verdict instead of the tier-2 skip.
        return (driver_lib.PANE_CHAIN_TRANSPORT,
                "probe_ok" if reason == "no_pane_ref" else reason)
    return (driver_lib.INLINE_TRANSPORT, reason)


# #840 — the ONLY permitted source of `observed_head`.
_OBSERVED_HEAD_RE = re.compile(r"\A[0-9a-f]{40}\Z")


def observe_head(repo_root: str, *, runner=_default_runner,
                 remote_ref: str = "origin/main") -> str:
    """Freshly observe the campaign's tracking head. The ONE source of `observed_head`.

    It lives HERE and not in `driver_lib` because `driver_lib` promises no I/O, and that promise
    is enforced by a source grep for `import subprocess`/`subprocess.run`
    (`tests/hooks/test_driver_state_write_back.py:295-301`). The design named the wrapper
    `driver_state.observe_head`; there is no such module, and putting it in `driver_lib` would
    fail that grep. `launcher_lib` already owns the I/O and the injected-`runner` pattern.

    **Why a wrapper at all, rather than letting callers pass a SHA.** Both pass-3 reviewers
    found independently that requiring the argument without binding it to a live observation is
    no guard: a caller could pass a cached SHA — or `validated_head` itself — and satisfy both
    refusal clauses after `main` had moved. That also silently defeated the abrupt-death
    recovery, since a crashed predecessor's stale head compares equal to itself.

    **Both return codes are checked, and `-C <repo_root>` is on BOTH commands.** r2 omitted
    `-C` on the fetch; a reviewer correctly flagged that as able to update a different checkout,
    or to fail outside a repository, while leaving the target stale. A rc-0 `rev-parse` whose
    stdout is not a full 40-char SHA is also refused — provenance that cannot be read is not
    provenance.

    Fail-CLOSED, deliberately against this module's usual convenience-fails-open rule (§3 of the
    repo manual): what this feeds is a gate on handing out work, so a fetch outage must refuse
    rather than return a head it could not confirm. The caller reports the refusal; nothing
    proceeds on a guess.
    """
    if not isinstance(repo_root, str) or not repo_root.strip():
        raise LauncherError("observe_head needs a repository root; got "
                            f"{repo_root!r}")
    # #840 round-13: the fetch MUST name the refspec it intends to update. A bare
    # `git fetch origin` obeys the repo's configured `remote.<name>.fetch`; a narrow or
    # filtered configuration that excludes this branch returns rc 0 WITHOUT advancing the
    # tracking ref, and the rev-parse below then hands back a STALE sha that satisfies the
    # freshness clause this function exists to enforce. rc 0 is only freshness evidence when
    # the command was told exactly which ref to advance. Found by the neutral-brief review
    # probe; the three adversarial lenses had all cleared this clause.
    remote, _, branch = (remote_ref or "").partition("/")
    if not remote or not branch or "/" in branch:
        raise LauncherError(
            f"refusing to observe the head: remote_ref {remote_ref!r} does not split into "
            "exactly <remote>/<branch>, so no explicit refspec can be derived — and guessing "
            "one would silently restore the unqualified fetch this refuses")
    refspec = f"+refs/heads/{branch}:refs/remotes/{remote}/{branch}"
    fetch = runner(["git", "-C", repo_root, "fetch", remote, refspec])
    if getattr(fetch, "returncode", 1) != 0:
        raise LauncherError(
            f"refusing to observe the head: `git -C {repo_root} fetch {remote} {refspec}` "
            f"exited {getattr(fetch, 'returncode', 'unknown')} — a stale head would make every "
            "freshness comparison compare equal to itself and open the gate on a moved main: "
            f"{(getattr(fetch, 'stderr', '') or '').strip()[:400]}")
    rev = runner(["git", "-C", repo_root, "rev-parse", remote_ref])
    if getattr(rev, "returncode", 1) != 0:
        raise LauncherError(
            f"refusing to observe the head: `git -C {repo_root} rev-parse {remote_ref}` exited "
            f"{getattr(rev, 'returncode', 'unknown')}: "
            f"{(getattr(rev, 'stderr', '') or '').strip()[:400]}")
    head = (getattr(rev, "stdout", "") or "").strip()
    if not _OBSERVED_HEAD_RE.match(head):
        raise LauncherError(
            f"refusing to observe the head: `rev-parse {remote_ref}` succeeded but printed "
            f"{head!r}, which is not a full 40-character lowercase SHA")
    return head


def _poll_for(check, *, attempts: int, delay_s: float, sleeper,
              max_wall_s: float | None = None, now=time.monotonic) -> bool:
    """Bounded wait for an on-disk artifact. Returns False when it never appears.

    A read error mid-poll is swallowed and retried, not fatal: a JSONL file being appended to
    can momentarily fail to read, and treating that as a verdict would abort a handoff that was
    about to succeed. `UnicodeDecodeError` counts as one of those — a read that lands between
    the first and last byte of a multi-byte character raises it, and it is a `ValueError`, so
    catching `OSError` alone let it escape and kill the poll (#611 Step-11 pass-3 Low 5).
    Exhausting the budget still FAILS CLOSED — what this gates (teardown) is irreversible.

    The budget is bounded on BOTH axes (#694 cross-model review). An attempt count alone bounds
    how many times `check` runs, not how long the poll can take: `check` does I/O, and nothing
    caps how long one read may block. The nominal budget is `attempts x delay_s`; the wall clock
    gets `POLL_WALL_CLOCK_SLACK` times that, so a healthy-but-slow poll still finishes while a
    stalled one cannot outlive its own budget by an order of magnitude. The first attempt ALWAYS
    runs — a deadline that could refuse before checking once would turn a slow clock into a
    verdict.
    """
    if max_wall_s is None:
        max_wall_s = attempts * delay_s * POLL_WALL_CLOCK_SLACK
    deadline = now() + max_wall_s
    for attempt in range(attempts):
        if attempt:
            if now() >= deadline:
                return False
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


# #758 Step-11 wave (F-F): the valid cleared-state snapshot is None, so omission needs its
# own sentinel — strict binding must be able to tell "validated: no live goal" from "the
# caller never validated anything".
_UNSET_GOAL_SNAPSHOT = object()


def perform_handoff(*, anchor_pane: str, cwd: str, project_root: str, name: str,
                    goal_condition: str, resume_prompt: str, registry_path: str,
                    transcript_dir: str, launch_mode: str = "fresh",
                    readiness_timeout_ms: int = 30000,
                    runner=_default_runner, read_text=None, sleeper=time.sleep,
                    teardown: bool, prompt_marker: str | None = None,
                    expected_project: str | None = None,
                    expected_project_path: str | None = None, steps=None,
                    on_successor=None, now=time.monotonic,
                    predecessor_session: str | None = None,
                    predecessor_goal_condition: str | None = None,
                    strict_goal_binding: bool = False,
                    expected_predecessor_goal=_UNSET_GOAL_SNAPSHOT,
                    campaign_context=None) -> dict:
    """Execute the ordered handoff. Effects are injected so tests drive the whole sequence.

    THE ORDER, and why each position is load-bearing:

    1. split from an explicit anchor pane, 2. `agent start` (no goal — see the module
    docstring), 3. `agent wait --until idle`, 4. `pane get` for the successor's session id,
    5. **capture the pre-launch artifact offsets**, 6. SEND 1 — `/rawgentic:switch <project>`
    alone, 7. **verify the registry row appeared** (`project_switched`), 8. SEND 2 — the resume
    prompt, 9. **verify it actually landed** (`prompt_landed`, when a marker was supplied),
    10. SEND 3 — `/goal`, LAST, 11. **verify the guard armed** (`goal_armed`), 12. retire the
    predecessor LAST.

    Each verify sits immediately after the send whose artifact it reads, so a failure names the
    send that caused it. Sending the bind as its OWN turn (#694) is what makes "the bind happens
    first" structural rather than a property of prompt wording; the prompt must therefore NOT
    carry a bind, which is checked below.

    `/goal` going last is measured, not assumed: pasted into a session actively mid-turn it still
    produced its `goal_status` row while that turn was running, so it needs no idle window. The
    older order armed the guard first to avoid handing work to an unguarded session; that concern
    is met by step 12 instead — the predecessor is not retired until `goal_armed` passes, so an
    unguarded successor never costs the run its predecessor. The residual unguarded window is
    between send 2 and step 11, and it is bounded by exactly the thing that closes it.

    Step 11's own history is why it is not merely "send and hope" (#611 Step-11 High 1): a
    revision that armed a goal and stopped left the successor guarded but idle, because a goal
    only re-prompts a session that tries to STOP.

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

    Step 9 carries the #700 recovery: if the marker never appears, the paste may be intact but
    UNSUBMITTED because the bind's turn ate its Enter, so up to `PROMPT_NUDGE_ROUNDS` bare Enters
    are sent — each one gated on `pane_shows_unsubmitted_paste`, never re-sending the text, and
    every unknown pane state abandoning the recovery. It changes no order and no gate.

    #665 additions, all defaulting to the #611 behaviour when omitted: `prompt_marker` adds the
    `prompt_landed` check (the resume prompt is verified to have ARRIVED, not merely to have
    been transported with rc 0); `expected_project`/`expected_project_path` bind
    `project_switched` to the right repository; `steps` selects the ladder to gate on.

    A ladder carrying successor-owned checks forces `teardown` OFF. For a mid-child handoff the
    predecessor is the thing being retired, and retirement is the SUCCESSOR's call — letting the
    predecessor close its own pane after the five checks it can make is the ownership inversion
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

    # #758 Step-11 wave (F-F): strict binding must never silently provide no binding.
    # The strict check lives on the teardown-clear path, which only runs with a
    # predecessor session — so an uncoupled request is a caller bug, refused BEFORE any
    # pane exists rather than granted as imaginary protection. The snapshot must be
    # EXPLICIT because None is itself a valid state ("validated: no live goal").
    if strict_goal_binding:
        if not teardown or predecessor_session is None:
            raise LauncherError(
                "strict_goal_binding=True requires teardown AND a predecessor_session — "
                "the binding guards the destructive clear, which only runs there; "
                "without them the caller would be requesting protection that cannot "
                "attach (#758)")
        if expected_predecessor_goal is _UNSET_GOAL_SNAPSHOT:
            raise LauncherError(
                "strict_goal_binding=True requires an EXPLICIT expected_predecessor_goal "
                "snapshot (pass None only to assert 'validated: no live goal') — an "
                "omitted snapshot is indistinguishable from an unvalidated one (#758)")
    expected_goal_snapshot = (None if expected_predecessor_goal is _UNSET_GOAL_SNAPSHOT
                              else expected_predecessor_goal)

    out: dict = {"ok": False, "steps": [], "results": {}, "truncated": False,
                 "failed_step": None, "new_pane": None, "session_id": None,
                 "cleanup": None, "teardown_skipped": None, "predecessor_guard": None,
                 # #927 PR 2: the herdr `error.code` of the failing step, machine-readable.
                 # ADDITIVE — no branch here reads it. `_cmd_handoff` needs the code rather than
                 # the human `note` because section 16.4's downgrade triggers on an ENUMERATED
                 # spiked refusal code (`CREATION_REFUSAL_CODES`), and parsing a prose note to
                 # recover a code it already had would be exactly the kind of guess that finding
                 # forbids.
                 "failure_code": None,
                 "failure_detail": None, "pane_capture": None}

    ladder = _VERIFICATION_STEPS if steps is None else steps
    gate_steps = _predecessor_steps(ladder)
    if teardown and len(gate_steps) != len(list(ladder)):
        teardown = False
        out["teardown_skipped"] = (
            "ladder carries successor-owned checks — retirement belongs to the successor "
            "(`retire-predecessor`), not to the session being retired")

    def record(kind, argv, proc=None, note=None):
        if note is None and proc is not None and getattr(proc, "returncode", 1) != 0:
            # #731 — one choke point: a failed step's herdr error body becomes its note
            # automatically, so no failure record is ever bare.
            note = _error_note(f"{getattr(proc, 'stdout', '') or ''}"
                               f"{getattr(proc, 'stderr', '') or ''}")
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
    # #694 — the bind is SEND 1, so the resume prompt must NOT carry it.
    #
    # This is the exact INVERSE of the #682 precondition it replaces, and the reversal is the point
    # rather than a regression. #682 required the prompt to OPEN with `/rawgentic:switch <project>`
    # and checked that as a prefix, which its own docstring is honest about being a proxy for
    # "first" and not a proof of it. Sending the bind as its own turn — gated on the registry row
    # the successor itself writes — makes the ordering STRUCTURAL, so the proxy has nothing left to
    # do. #682 named this design correct and deferred it only because it reorders a ladder.
    #
    # A prompt that still opens with the bind is a caller that has not been updated. Sending it
    # anyway would re-enter the switch skill in a session already bound, which at best wastes the
    # successor's first turn and at worst sits in list mode. So it is refused loudly.
    #
    # `expected_project` stays REQUIRED — send 1 is built from it, and `project_switched` binds the
    # registry row to it. An absent project would make the bind a bare directive, which is the
    # #682 defect: the switch skill enters LIST MODE and waits for a human that no unattended
    # successor has. Checked HERE with the other caller-mismatch validations, before the split, so
    # a refusal never leaves a pane behind.
    driver = _driver_lib()
    if not driver.valid_project_name(expected_project):
        raise LauncherError(
            f"expected_project {expected_project!r} is not a valid project name — the bind is sent "
            "as its own turn and is built from it, and a bare `/rawgentic:switch` enters the "
            "switch skill's LIST MODE and waits for a human (#682)")
    if driver.BIND_DIRECTIVE in resume_prompt:
        raise LauncherError(
            f"resume_prompt carries {driver.BIND_DIRECTIVE!r} — the launcher sends the bind as "
            "SEND 1 of its own, gated on the session-registry row, so a prompt that also binds "
            "would make the successor run the switch skill twice. Build it with "
            "include_bind=False (#694)")
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

    # #840 — the FIRST rung, produced here: after every caller-mismatch validation (so a bad
    # argument still RAISES rather than being masked by a queue refusal) and before any pane,
    # session or state effect exists. A successor handed a stale queue has already read the wrong
    # issue bodies by the time a later rung could object, so this cannot wait for the post-launch
    # checks.
    #
    # `campaign_context` is an EXPLICIT parameter rather than something inferred, because
    # `perform_handoff` is deliberately shared with `_cmd_ad_hoc_handoff`, which has no campaign
    # state at all. Present => the check runs, fail-closed. Absent => the ad-hoc case, whose
    # three-rung launch ladder carries no such rung, so there is nothing to produce.
    if any(s.get("step") == QUEUE_REVALIDATED_STEP for s in ladder):
        revalidated, revalidation_reason = produce_queue_revalidated(campaign_context,
                                                                     runner=runner)
        out["results"][QUEUE_REVALIDATED_STEP] = revalidated
        record("queue_revalidated", ["<driver-state receipt>"], note=revalidation_reason)
        if not revalidated:
            out["failed_step"] = QUEUE_REVALIDATED_STEP
            out["reason"] = revalidation_reason
            out["failure_detail"] = revalidation_reason
            return out

    # #731 — pre-split name preflight. A bound agent name makes `agent start` fail AFTER a
    # pane exists, and a same-name retry is structurally impossible (the name stays bound) —
    # so refuse BEFORE anything is created. FAIL-OPEN when the list cannot be read: the
    # start-time path still refuses a taken name, now with its cause, and a broken
    # `agent list` must not become a new way for handoffs to fail.
    list_argv = build_agent_list_argv()
    try:
        try:
            proc = runner(list_argv, timeout=NAME_PREFLIGHT_TIMEOUT_S)
        except TypeError:
            # A legacy caller-supplied runner without a timeout parameter — the preflight
            # matters more than its bound (#731 Step-11).
            proc = runner(list_argv)
    except (OSError, subprocess.SubprocessError, LauncherError) as exc:
        # Fail-open on transport trouble too: this runs BEFORE the ownership try/finally, so
        # an uncaught raise here would crash the whole handoff over an optional preflight.
        proc = None
        record("agent_name_preflight", list_argv, None,
               note=f"preflight FAIL-OPEN: `herdr agent list` raised {exc} — proceeding; a "
                    "taken name is still refused at agent start")
    if proc is None:
        pass
    elif getattr(proc, "returncode", 1) != 0:
        record("agent_name_preflight", list_argv, proc,
               note="preflight FAIL-OPEN: `herdr agent list` failed — proceeding; a taken "
                    "name is still refused at agent start, with its cause")
    else:
        parsed, holder = _agent_name_holder(getattr(proc, "stdout", "") or "", name)
        if not parsed:
            record("agent_name_preflight", list_argv, proc,
                   note="preflight FAIL-OPEN: `herdr agent list` output was unusable — "
                        "proceeding; a taken name is still refused at agent start")
        elif holder is not None:
            detail = (f"agent name {name!r} is already bound to pane {holder} — refused "
                      f"before any split, so nothing was created and nothing needs cleanup. "
                      f"Check `herdr agent list` and pick a fresh --name: a same-name retry "
                      f"cannot succeed while the name stays bound")
            record("agent_name_preflight", list_argv, proc, note=detail)
            out["failed_step"] = "name_taken"
            out["failure_detail"] = detail
            return out
        else:
            record("agent_name_preflight", list_argv, proc, note=f"name {name!r} is free")

    # Captured BEFORE the split so nothing already on disk can be mistaken for this launch's
    # evidence (#611 Step-11 Medium 4). A registry that exists but cannot be read yields no
    # baseline at all, and without a baseline there is no such thing as fresh evidence.
    registry_baseline = _baseline(read_text, registry_path)
    if registry_baseline is None:
        out["failed_step"] = "registry_baseline_unreadable"
        out["failure_detail"] = (
            "the session registry exists but could not be read for a pre-launch baseline — "
            "without one, no later row can count as fresh evidence")
        return out

    # Pane inventory before the split. REQUIRED, not best-effort (#611 Step-11 pass-6 High 1):
    # it is the only thing that can later show a returned pane id is genuinely NEW. Without it,
    # a well-formed response naming a pre-existing foreign pane would be claimed as ours and
    # closed on the next failure. Refusing here is also the honest reading of the situation — if
    # `herdr pane list` does not work, herdr is not healthy enough to be splitting panes in.
    panes_before = _pane_inventory(runner)
    if panes_before is None:
        out["failed_step"] = "pane_inventory_unavailable"
        out["failure_detail"] = (
            "`herdr pane list` failed or returned an unusable inventory — a new pane could "
            "not be proven ours, so no split was attempted")
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
            out["failure_code"] = _error_code(
                f"{getattr(proc, 'stdout', '') or ''}{getattr(proc, 'stderr', '') or ''}")
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
        # Bounded on wall clock as well as on attempts (see POLL_WALL_CLOCK_SLACK). This loop is
        # where the arithmetic actually bites: `runner` is `_default_runner`, whose timeout is
        # 180 s, so 15 attempts is a 45-minute ceiling on what the comment above calls "a
        # condition that resolves itself in about a second".
        started = False
        start_deadline = now() + PANE_READY_ATTEMPTS * PANE_READY_DELAY_S * POLL_WALL_CLOCK_SLACK
        for attempt in range(PANE_READY_ATTEMPTS):
            if attempt:
                if now() >= start_deadline:
                    break
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
                # #731 — the preflight race: the name was free at the list and taken by start
                # time. Same name-specific refusal as the preflight, never a bare agent_start,
                # and never a retry (only agent_pane_busy is self-resolving).
                if _error_code(body) == NAME_TAKEN_ERROR_CODE:
                    out["failed_step"] = "name_taken"
                    out["failure_detail"] = _error_note(body)
                    return out
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

        # SEND 1 of 3 — the BIND, as its own verified turn (#694).
        #
        # This is the design #682 identified as correct and deferred: "the launcher should send the
        # bind as its own verified turn — it already sends `/goal` that way — which removes the
        # dependence on prose ordering entirely". It is no longer deferred, because the ordering it
        # replaces was measured wrong. A separate send makes "the bind happens first" STRUCTURAL
        # instead of a property of prompt wording that a prefix check could only ever proxy.
        bind_argv, bind_keys = build_send_text_argv(
            pane=new_pane, text=f"{_driver_lib().BIND_DIRECTIVE} {expected_project}")
        for kind, argv in (("send_bind", bind_argv), ("send_bind_keys", bind_keys)):
            proc = runner(argv)
            record(kind, argv, proc)
            if getattr(proc, "returncode", 1) != 0:
                out["failed_step"] = "send_bind"
                return out

        # Gated on the REGISTRY ROW, never on a timer and never on `agent_status`. Measured live
        # 2026-07-29: `agent_status` is not a usable synchronisation signal — it read `idle`
        # immediately after a prompt was submitted, `done` while a turn was still running, and
        # `working` on a session sitting at an empty input line. The registry row is a durable
        # artifact the successor itself writes, which is why it is the gate.
        # #800 — the tail the gate last JUDGED, kept so the failure branch can explain itself
        # without reading the registry again. Step-11 inline finding IF-1: this function's default
        # `read_text` is a bare `open()` with no error handling (unlike `retire_predecessor`'s
        # `read_or_empty`), so a second read would have added a new exception surface to the exact
        # branch whose job is to report a failure legibly. It also makes the diagnosis provably
        # about the same bytes that were rejected, rather than a fresh read that may differ.
        judged_tail: list[str | None] = [None]

        def _project_switched() -> bool:
            tail = _tail(read_text(registry_path), registry_baseline)
            judged_tail[0] = tail
            # #800 — `project_root` IS the workspace root those relative `./projects/<name>` paths
            # are relative to (the pane-handoff skill passes `--project-root <workspace root>`),
            # so the comparison accepts either spelling the successor may have written.
            return tail is not None and registry_has_session(
                tail, session_id, expected_project=expected_project,
                expected_project_path=expected_project_path, project_root=project_root)

        out["results"]["project_switched"] = _poll_for(
            _project_switched,
            attempts=SWITCH_POLL_ATTEMPTS, delay_s=SWITCH_POLL_DELAY_S, sleeper=sleeper)
        if not out["results"]["project_switched"]:
            # #800 — say WHICH field disagreed. Without this, a comparison that could never match
            # looked exactly like a slow successor, and the #875 campaign published a wrong
            # diagnosis off that ambiguity. Diagnosed over the SAME post-baseline tail the gate
            # judged — not a fresh read — so a row that predates the launch can neither satisfy nor
            # explain this check (#611 Step-11 Medium 4). A void baseline means the registry stopped
            # being an append-only extension of what we measured, which is its own explanation.
            tail = judged_tail[0]
            record("project_switched", ["<registry poll>"],
                   note=(registry_match_diagnosis(
                       tail, session_id, expected_project=expected_project,
                       expected_project_path=expected_project_path,
                       project_root=project_root) if tail is not None else
                         f"the registry {registry_path!r} is no longer an append-only extension "
                         f"of its pre-launch baseline (truncated, rotated or replaced), so no "
                         f"positional evidence about session {session_id!r} is trustworthy"))
            # A successor that never binds is also the shape a permission-BLOCKED successor takes,
            # so the failure names that possibility rather than leaving an operator guessing. The
            # launcher cannot fix it: `--permission-mode` is refused by `_ALLOWED_CLAUDE_ARGS` as
            # authority-bearing, so a non-blocking permission mode is a PRECONDITION of unattended
            # handoff, not something this code can assert. Failing loudly here is the honest
            # alternative to silently burning the rest of the sequence.
            out["failed_step"] = "project_switched"
            return out

        # SEND 2 of 3 — the work. The prompt no longer has to carry the bind, because send 1 did.
        prompt_argv, prompt_keys = build_send_text_argv(pane=new_pane, text=resume_prompt)
        for kind, argv in (("send_resume_prompt", prompt_argv),
                           ("send_resume_keys", prompt_keys)):
            proc = runner(argv)
            record(kind, argv, proc)
            if getattr(proc, "returncode", 1) != 0:
                out["failed_step"] = "send_resume_prompt"
                return out

        # `prompt_landed` (#665) — rc 0 on send-text proves TRANSPORT, not arrival.
        if prompt_marker is not None:
            def _prompt_landed() -> bool:
                tail = _tail(read_text(transcript_path), transcript_baseline)
                return tail is not None and transcript_has_marker(tail, prompt_marker)

            landed = _poll_for(_prompt_landed, attempts=GOAL_POLL_ATTEMPTS,
                               delay_s=GOAL_POLL_DELAY_S, sleeper=sleeper)

            # #700 — the paste may be INTACT BUT UNSUBMITTED, which is a third state distinct from
            # both success and transport failure. `project_switched` proves the bind's row landed,
            # not that its turn ended, so the prompt's own Enter can be consumed by that turn.
            # Found live on 2026-07-29 driving this sequence by hand; a single bare Enter recovered
            # it with exactly one occurrence of the marker in the transcript and no double
            # submission.
            #
            # This is recovery inside send 2, NOT a change to the send order or to any gate:
            # `prompt_landed` still has to pass on the same artifact, and a nudge can only let a
            # gate pass where the buffer was intact all along. Never a re-paste and never a
            # truncation (#696).
            for _ in range(PROMPT_NUDGE_ROUNDS):
                if landed:
                    break
                # An Enter accepts whatever is on screen, so the pane's state is checked FIRST and
                # anything other than a clear "safe" abandons the recovery — which leaves exactly
                # the pre-#700 behaviour.
                read_argv = build_pane_read_argv(new_pane)
                proc = runner(read_argv)
                if getattr(proc, "returncode", 1) != 0:
                    record("pane_read", read_argv, proc,
                           note="nudge SKIPPED: pane read failed, so the pane's state is unknown")
                    break
                safe, why = pane_shows_unsubmitted_paste(getattr(proc, "stdout", "") or "")
                record("pane_read", read_argv, proc,
                       note=f"nudge {'PERMITTED' if safe else 'SKIPPED'}: {why}")
                if not safe:
                    break
                nudge_argv = build_send_enter_argv(new_pane)
                proc = runner(nudge_argv)
                record("send_resume_nudge", nudge_argv, proc,
                       note="bare Enter — submit the intact paste, never re-send it (#700)")
                if getattr(proc, "returncode", 1) != 0:
                    # Named distinctly (design review): reporting this as `prompt_landed` would
                    # blame the successor's timing for what is a failed herdr call.
                    out["failed_step"] = "send_resume_nudge"
                    return out
                landed = _poll_for(_prompt_landed, attempts=GOAL_POLL_ATTEMPTS,
                                   delay_s=GOAL_POLL_DELAY_S, sleeper=sleeper)

            out["results"]["prompt_landed"] = landed
            if not landed:
                out["failed_step"] = "prompt_landed"
                out["failure_detail"] = (
                    "the resume prompt's marker never appeared in the successor transcript "
                    "within the poll budget — transport rc 0 proves delivery, not arrival")
                return out

        # SEND 3 of 3 — the GUARD, LAST, deliberately while the successor is already working.
        #
        # This is what the reordering rests on, and it is measured rather than assumed: on
        # 2026-07-29 a `/goal` pasted into a session actively mid-turn (counting, `agent_status`
        # `working`) produced its `goal_status met:false` row
        # while that turn was still running. `/goal` therefore needs no idle window, which is why
        # it can go last — and why the previous ordering's whole premise (arm first, because the
        # guard must exist before work) bought nothing that mattered and cost the prompt.
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
            out["failure_detail"] = (
                "no unmet goal_status row for the armed condition appeared in the successor "
                "transcript within the poll budget")
            return out

        # The unguarded window is now BETWEEN send 2 and this row, and it is bounded by exactly the
        # thing that closes it: the predecessor is not retired until `goal_armed` has passed below,
        # so a successor that never arms its guard never costs the run its predecessor. That is the
        # honest answer to the objection the old ordering existed for — the harm was never "work
        # begins unguarded", it was "work begins unguarded AND the predecessor is already gone".
        ok, failed, _ = evaluate_verifications(out["results"], steps=gate_steps)
        if not ok:
            out["failed_step"] = failed
            out["failure_detail"] = (
                f"verification {failed!r} did not pass — fail-closed: an unreported check "
                "counts as failed")
            return out

        transferred = True
    except (LauncherError, OSError, subprocess.SubprocessError) as exc:
        out["failed_step"] = out["failed_step"] or f"exception: {exc}"
        out["failure_detail"] = out["failure_detail"] or str(exc)
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
        # #731 — no try-body exit returns a bare failed_step: derive the cause from the failing
        # step's own recorded note when no site set it explicitly. Idempotent (explicit wins),
        # and the pane capture recorded during cleanup is lifted the same way.
        if out.get("failed_step") and not out.get("failure_detail"):
            out["failure_detail"] = failure_detail(out)
        if out.get("pane_capture") is None:
            out["pane_capture"] = pane_capture(out)

    def _finalize():
        # #731 Step-11 (merged Medium): teardown-phase exits fill the derived fields at the
        # LIBRARY level too — not just the CLI serialization. `predecessor_guard`, when set,
        # IS this phase's failure narrative, so it wins over a step note that would name the
        # step without its cause (e.g. teardown_predecessor's note is the ALLOWED reason).
        if out.get("failed_step") and not out.get("failure_detail"):
            guard = out.get("predecessor_guard")
            out["failure_detail"] = (guard if isinstance(guard, str) and guard
                                     else failure_detail(out))
        return out

    # Teardown LAST, only when authorized, and its result is NOT ignored (Step-11 Medium 5).
    allowed, reason = teardown_allowed(out["results"], steps=gate_steps)
    if teardown and allowed:
        # #700 field defect: this used to close the pane WITHOUT clearing its goal, while the
        # successor-owned `retire-predecessor` path (#665) correctly clears, confirms, then closes.
        # Found on a real handoff: the predecessor was left alive with its guard armed and looped
        # its Stop hook four times. Closing a guarded pane is usually masked because the session
        # dies with it — but when the close fails or is skipped, the owner is left with a session
        # that can never stop.
        #
        # The clear is CONFIRMED before the close, and an unconfirmed clear keeps the pane: a guard
        # that may still be armed is recoverable, a wrongly-closed pane is not.
        if predecessor_session is not None:
            pred_transcript = os.path.join(transcript_dir, f"{predecessor_session}.jsonl")
            pred_baseline = _baseline(read_text, pred_transcript)
            if pred_baseline is None:
                out["failed_step"] = "predecessor_goal_clear"
                out["predecessor_guard"] = (
                    f"the predecessor pane {anchor_pane} is ALIVE and STILL GUARDED — its "
                    f"transcript could not be baselined, so {_CLEAR_COMMAND!r} was never sent. Run "
                    f"{_CLEAR_COMMAND!r} in it by hand, then close it")
                return _finalize()
            # #707 — THREE states, not two. The first revision sent the clear unconditionally and
            # then required a NEW receipt row, so "no guard was ever armed" produced no receipt and
            # was reported as "the clear may not have landed": the pane was stranded with an
            # AMBIGUOUS verdict and exit 4 when it was simply already safe to close. That is the
            # COMMON path, not an edge case — the skill tells the operator to run `clear-prep` first
            # and `clear-prep` step 6 clears the guard, so the documented happy path produces it.
            #
            # Liveness is read from the WHOLE transcript, not the post-baseline tail: the guard was
            # armed earlier in the session, long before this baseline.
            #
            # It is keyed on the NEWEST goal_status row and NOT on `predecessor_goal_condition`,
            # which is the hazard in this fix. A caller whose supplied condition has been superseded
            # would otherwise read as "already clear" while a replacement guard is live, and the pane
            # would be closed over it — the same trap #665 Step-11 pass-3 documented for the confirm
            # side. So the transcript is the single authority, and the supplied condition is at most
            # an assertion recorded below.
            try:
                pred_text = read_text(pred_transcript)
            except (OSError, UnicodeDecodeError) as exc:
                out["failed_step"] = "predecessor_goal_clear"
                out["predecessor_guard"] = (
                    f"the predecessor pane {anchor_pane} is ALIVE and may still be GUARDED — its "
                    f"transcript could not be read ({exc}), and an unreadable transcript is not "
                    f"evidence that nothing is armed. Run {_CLEAR_COMMAND!r} in it by hand")
                return _finalize()

            # #758 strict goal binding (D18). The validated snapshot — the sentinel-only live
            # goal the caller's verbatim-carry check ran against, INCLUDING the explicit
            # "no live goal" state (None) — must still hold at the destructive step. ANY
            # divergence refuses the clear and keeps the pane: a goal that changed, appeared,
            # or disappeared mid-handoff means an instruction this close could destroy. The
            # residual race between this read and the clear send is a platform ceiling
            # (`/goal clear` has no compare-and-clear form); mitigation: a goal cannot arm
            # mid-turn in a busy pane — arming requires a Stop evaluation.
            if strict_goal_binding:
                try:
                    live_now = live_owner_goal(pred_text, strict=True)
                except LauncherError as exc:
                    # Ambiguous newest evidence (torn/malformed tail) — same refusal as a
                    # divergence: never clear or close over evidence that cannot be read.
                    out["failed_step"] = "predecessor_goal_binding"
                    record("predecessor_goal_binding", [], None,
                           note=f"strict binding REFUSED the clear (#758): {exc}")
                    out["predecessor_guard"] = (
                        f"the predecessor pane {anchor_pane} is LEFT OPEN and its guard "
                        f"untouched — {exc}")
                    return _finalize()
                if live_now != expected_goal_snapshot:
                    def _shape(v):
                        return "none" if v is None else f"{len(v)} chars"
                    detail = (f"validated snapshot {_shape(expected_goal_snapshot)}, "
                              f"live now {_shape(live_now)}")
                    if isinstance(live_now, str) and isinstance(expected_goal_snapshot, str):
                        off = next((i for i, (a, b) in
                                    enumerate(zip(live_now, expected_goal_snapshot))
                                    if a != b),
                                   min(len(live_now), len(expected_goal_snapshot)))
                        detail += f", first divergence at offset {off}"
                    out["failed_step"] = "predecessor_goal_binding"
                    record("predecessor_goal_binding", [], None,
                           note=f"strict binding REFUSED the clear (#758): {detail}")
                    out["predecessor_guard"] = (
                        f"the predecessor pane {anchor_pane} is LEFT OPEN and its guard "
                        f"untouched — the goal state changed between validation and teardown "
                        f"({detail}), and closing a pane over an instruction that changed "
                        f"underneath the handoff is exactly what #758 forbids. The successor "
                        f"is armed with the VALIDATED goal and keeps running. Inspect the "
                        f"pane; clear and close it by hand only after reading its goal")
                    return _finalize()

            if strict_goal_binding:
                # Step-8a wave Critical (#758): under strict binding the #707 three-state
                # classification must derive from the SAME trusted verdict the binding just
                # validated — the sentinel-insensitive helpers below would let a forged
                # sentinel-less met:true row for the UNCHANGED condition read as
                # already_clear, skipping `/goal clear` and closing a still-guarded pane.
                # The validated snapshot IS the state: it equals `live_owner_goal(pred_text)`
                # (the binding check above just proved it), so no second read is needed.
                live_condition = expected_goal_snapshot
                armed = expected_goal_snapshot is not None
            else:
                live_condition = latest_goal_status_condition(pred_text)
                armed = (live_condition is not None
                         and goal_currently_unmet(pred_text, live_condition))
            if not armed:
                # Nothing to clear, so nothing to confirm. Recorded as its own result value rather
                # than a silent skip: "already_clear" is a materially different fact from "cleared
                # by us", and the step record is what an operator reads afterwards.
                out["results"]["predecessor_goal_clear"] = "already_clear"
                record("clear_predecessor_goal", [], None,
                       note=("skipped: no goal is currently armed in the predecessor "
                             + (f"(newest guard {live_condition!r} is already met)"
                                if live_condition is not None
                                else "(no goal_status row at all)")
                             + f" — nothing for {_CLEAR_COMMAND!r} to clear (#707)"))
                if (predecessor_goal_condition is not None
                        and live_condition is not None
                        and predecessor_goal_condition.strip() != live_condition.strip()):
                    record("clear_predecessor_goal", [], None,
                           # #730: flag-neutral — the value may have arrived via either
                           # --predecessor-goal-condition or --predecessor-goal-condition-file,
                           # and naming one of them would be wrong half the time.
                           note=(f"note: the supplied predecessor goal condition "
                                 f"{predecessor_goal_condition!r} is not the newest guard "
                                 f"({live_condition!r}); the transcript won"))

            sends = ()
            if armed:
                clear_text, clear_keys = build_send_text_argv(pane=anchor_pane,
                                                              text=_CLEAR_COMMAND)
                sends = (("clear_predecessor_goal", clear_text),
                         ("clear_predecessor_goal_keys", clear_keys))
            for kind, argv in sends:
                proc = runner(argv)
                record(kind, argv, proc, note=f"{_CLEAR_COMMAND} before closing (#700)")
                if getattr(proc, "returncode", 1) != 0:
                    out["failed_step"] = kind
                    out["predecessor_guard"] = (
                        f"the predecessor pane {anchor_pane} is ALIVE and may still be GUARDED — "
                        f"{kind} failed, so aborting BEFORE the close. If the text landed but the "
                        f"Enter did not, {_CLEAR_COMMAND!r} is staged unsubmitted in its input. "
                        f"Check the pane and run {_CLEAR_COMMAND!r} by hand")
                    return _finalize()

            # Bound to `live_condition` — the guard actually in force — and NOT to the caller's
            # supplied one. #665 Step-11 pass-3 proved the binding is necessary at all: with ANY
            # met:true row accepted, a row belonging to a different guard confirms our clear and a
            # live pane gets closed on it. #707 then showed WHICH condition it must be: we clear
            # whatever is armed, so the receipt carries that condition, and binding to a stale
            # caller-supplied string would wait for a receipt nothing will ever write.
            def _pred_cleared() -> bool:
                tail = _tail(read_text(pred_transcript), pred_baseline)
                return tail is not None and transcript_has_cleared_goal(
                    tail, expected_condition=live_condition)

            if armed and not _poll_for(_pred_cleared, attempts=GOAL_POLL_ATTEMPTS,
                                       delay_s=GOAL_POLL_DELAY_S, sleeper=sleeper):
                out["failed_step"] = "predecessor_goal_clear"
                out["predecessor_guard"] = (
                    f"the predecessor pane {anchor_pane} is LEFT OPEN because "
                    f"{_CLEAR_COMMAND!r} was transported but never confirmed by a met:true "
                    f"sentinel row. Its guard state is AMBIGUOUS — verify the pane and run "
                    f"{_CLEAR_COMMAND!r} by hand before closing it")
                return _finalize()
        else:
            # The campaign launcher does not know the predecessor's session id, so the clear could
            # not be confirmed even if it were sent. Its behaviour is therefore UNCHANGED —
            # close-only — and the gap is recorded rather than silently assumed to be harmless.
            record("teardown_predecessor_guard", [], None,
                   note=("predecessor goal NOT cleared before close: no predecessor_session was "
                         "supplied, so the clear could not be confirmed. The session normally dies "
                         "with the pane; if the close fails, its guard is still armed"))

        td_argv = build_teardown_argv(anchor_pane)
        proc = runner(td_argv)
        record("teardown_predecessor", td_argv, proc, note=reason)
        if getattr(proc, "returncode", 1) != 0:
            out["failed_step"] = "teardown_predecessor"
            out["ok"] = False
            out["predecessor_guard"] = (
                f"the predecessor pane {anchor_pane} could NOT be closed (rc="
                f"{getattr(proc, 'returncode', None)}). If its goal was cleared it will stop "
                f"normally; otherwise run {_CLEAR_COMMAND!r} in it by hand")
            return _finalize()
    elif not teardown:
        # #700 field defect 3, and the one that actually bit: the default path left the guard armed
        # and said NOTHING, so the owner met a pane that looped its Stop hook with no idea why.
        # #880 AC-D(iii): assert armedness only where it was actually checked. When the
        # predecessor's transcript is available, read it (lenient, advisory — never raises);
        # a retired/absent goal must not be reported "STILL ARMED" (a false alarm sent the
        # owner to fix a non-problem). Where no transcript exists (the CLI --no-teardown path
        # passes no predecessor_session), the wording is conditional, never assertive.
        armed_hint: bool | None = None
        # Defense-in-depth (#880 Step 11): only a grammar-valid session id is
        # ever joined into a transcript path here — a traversal-shaped id
        # stays indeterminate (conditional wording) instead of reading outside
        # transcript_dir. Mirrors the CLI's _SESSION_ID_RE gate at build time.
        if predecessor_session is not None and _SESSION_ID_RE.fullmatch(predecessor_session):
            try:
                pred_text = read_text(
                    os.path.join(transcript_dir, f"{predecessor_session}.jsonl"))
                # STRICT read (#880 Step 8a wave finding): leniently, a torn
                # tail is skipped and a retired-then-torn transcript would
                # print a definitive "NO live goal" for an indeterminate pane.
                # Strict raises LauncherError there -> armed_hint stays None ->
                # the conditional wording below.
                armed_hint = live_owner_goal(pred_text, strict=True) is not None
            except (LauncherError, OSError, UnicodeDecodeError, TypeError, ValueError):
                armed_hint = None
        if armed_hint is False:
            out["predecessor_guard"] = (
                f"the predecessor pane {anchor_pane} is still running; its transcript shows NO "
                f"live goal (#880), so it will not re-prompt itself — nothing for "
                f"{_CLEAR_COMMAND!r} to clear. Close the pane whenever you are done with it")
        elif armed_hint is True:
            out["predecessor_guard"] = (
                f"the predecessor pane {anchor_pane} is still running and its /goal is STILL "
                f"ARMED — it will keep re-prompting itself at every Stop until you run "
                f"{_CLEAR_COMMAND!r} in it. That is deliberate (teardown is opt-in), but it is "
                "not automatic")
        else:
            out["predecessor_guard"] = (
                f"the predecessor pane {anchor_pane} is still running and its /goal MAY still "
                f"be armed (this path has no transcript to check) — if it is, it will keep "
                f"re-prompting itself at every Stop until you run {_CLEAR_COMMAND!r} in it. "
                "That is deliberate (teardown is opt-in), but it is not automatic")
    out["ok"] = True
    return _finalize()


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


def failure_detail(out) -> str | None:
    """The human-readable cause behind out['failed_step'], or None.

    Precedence: an explicitly-set out['failure_detail'] → the LAST recorded note of the step
    kind that failed → out['reason'] → out['predecessor_guard'] (only for the teardown-phase
    predecessor_goal_* failures, whose whole cause lives there) → None. Deliberately NO
    cross-kind note fallback: borrowing an unrelated step's note would attribute the failure
    to the wrong cause, which is worse than admitting no detail was captured (#731).
    """
    if not isinstance(out, dict) or not out.get("failed_step"):
        return None
    explicit = out.get("failure_detail")
    if isinstance(explicit, str) and explicit:
        return explicit
    failed = out["failed_step"]
    steps = out.get("steps")
    if isinstance(steps, list):
        for step in reversed(steps):
            if isinstance(step, dict) and step.get("kind") == failed:
                note = step.get("note")
                if isinstance(note, str) and note:
                    return note
    reason = out.get("reason")
    if isinstance(reason, str) and reason:
        return reason
    if isinstance(failed, str) and failed.startswith("predecessor_goal"):
        guard = out.get("predecessor_guard")
        if isinstance(guard, str) and guard:
            return guard
    return None


def pane_capture(out) -> str | None:
    """The tentative pane's captured output (the 'cleanup_pane_capture' step note), or None.

    Lifted from the step record so the CLI payload can carry it without shipping the whole
    ladder — the successor's own output is the single most informative artifact of a failed
    handoff, and cleanup used to destroy it unread (#731 AC2).
    """
    if not isinstance(out, dict):
        return None
    steps = out.get("steps")
    if isinstance(steps, list):
        for step in reversed(steps):
            if isinstance(step, dict) and step.get("kind") == "cleanup_pane_capture" \
                    and step.get("returncode") == 0:
                note = step.get("note")
                if isinstance(note, str) and note:
                    return note
    return None


_PANE_CAPTURE_CAP = 2000
# The cleanup capture's own runner timeout (seconds). The default runner bound is 180 s
# (`_default_runner`) — acceptable for launch steps, far too long to hold a best-effort
# read during cleanup (#731 Step-8a).
PANE_CAPTURE_TIMEOUT_S = 5


def _capped_tail(text: str, cap: int = _PANE_CAPTURE_CAP) -> str:
    """The LAST `cap` chars — tail-biased, because a failing pane's error is at the end,
    and a megabyte of scrollback must not ride a JSON report."""
    if len(text) <= cap:
        return text
    return f"[capture truncated to the last {cap} chars]\n" + text[-cap:]


def _agent_name_holder(agent_list_stdout, name) -> tuple[bool, str | None]:
    """(parsed, holder_pane_id) from `herdr agent list` output.

    parsed=False means the output was unusable — the preflight then FAILS OPEN (unlike
    `_pane_inventory`, which fails closed, because *its* consumers close panes; refusing a
    handoff over a garbled list would be a new failure mode, while proceeding just moves the
    refusal to `agent start`, which now reports its cause). Entries carry an OPTIONAL `name`
    key (herdr 0.8.0, verified live 2026-08-05) — unnamed and malformed entries are skipped.
    """
    try:
        doc = json.loads(agent_list_stdout or "")
    except (ValueError, TypeError):
        return (False, None)
    node = doc.get("result", doc) if isinstance(doc, dict) else None
    agents = node.get("agents") if isinstance(node, dict) else None
    if not isinstance(agents, list):
        return (False, None)
    for entry in agents:
        if isinstance(entry, dict) and entry.get("name") == name:
            pane = entry.get("pane_id")
            return (True, pane if isinstance(pane, str) and pane else "<unknown pane>")
    return (True, None)


def _error_code(body) -> str | None:
    """The `error.code` of a herdr error payload, or None when there is no such thing.

    The sibling of `_is_pane_busy` for callers that need the code itself rather than one
    yes/no: #731 branches `agent_name_taken` to a name-specific `failed_step`. Same guarded
    parse — any non-JSON, non-dict, or code-less shape is None, never an exception.
    """
    try:
        doc = json.loads(body or "")
    except (ValueError, TypeError):
        return None
    err = doc.get("error") if isinstance(doc, dict) else None
    code = err.get("code") if isinstance(err, dict) else None
    return code if isinstance(code, str) and code else None


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
                # #731 Step-8a High (security): a refused probe means the handle may have
                # been REUSED — reading it would disclose another session's output into the
                # report. No ownership, no read; the skip stays visible.
                record("cleanup_pane_capture", [], None,
                       note="capture SKIPPED: the pane no longer provably hosts our session "
                            "— reading it could disclose another session's output")
                return (f"NOT closed {pane}: it no longer provably hosts {expected_session!r} "
                        f"(saw {live!r}) — the handle may have been reused, and closing it could "
                        "kill an unrelated session; check `herdr pane list`")

        # #731 AC2 — capture the pane's visible output BEFORE the close destroys it: the
        # successor's own words are the single most informative artifact of a failed handoff.
        # This runs AFTER the ownership check above, on exactly the close's own ownership
        # basis (probe passed, or the early-failure case where no session was ever
        # established and the close proceeds on the pre-split-inventory bound). Best-effort
        # in every direction: no capture failure may block the close, and the read carries
        # its own short timeout — the default runner bound is 180 s, far too long to hold a
        # cleanup for a best-effort read.
        read_argv = build_pane_read_argv(pane)
        try:
            try:
                read_proc = runner(read_argv, timeout=PANE_CAPTURE_TIMEOUT_S)
            except TypeError:
                # A legacy caller-supplied runner without a timeout parameter (#731 Step-11:
                # this module never passed a runner kwarg before — losing the capture AND the
                # close to a TypeError would orphan the pane).
                read_proc = runner(read_argv)
        except (OSError, subprocess.SubprocessError, LauncherError) as exc:
            read_proc = None
            record("cleanup_pane_capture", read_argv, None,
                   note=f"pane read raised {exc} — capture skipped")
        if read_proc is not None:
            if getattr(read_proc, "returncode", 1) == 0:
                text = (getattr(read_proc, "stdout", "") or "").strip()
                record("cleanup_pane_capture", read_argv, read_proc,
                       note=_capped_tail(text) if text
                       else "pane read succeeded but the viewport was empty")
            else:
                record("cleanup_pane_capture", read_argv, read_proc)
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


def _no_duplicate_keys(pairs):
    """Refuse a JSON object carrying the same property twice.

    `json.load` keeps the LAST of two identical properties and silently discards the first.
    For a campaign receipt that means two contradictory records for one child collapse to
    whichever happened to be written last, and the gate then opens on the survivor at rc 0
    with the other's evidence — a correction, a broken claim — simply gone.

    Round 12 installed this on the `--audited` decode only. Round 13 (all six reviewers,
    adversarial and neutral, each by executed reproduction) found the four DURABLE driver-state
    decodes still on plain `json.load`, which is where selection and teardown actually read.
    One hardened path out of five is not a guard. This is now the single definition."""
    seen = set()
    for key, _value in pairs:
        if key in seen:
            raise ValueError(f"duplicate property {key!r} — two records cannot both be "
                             "the evidence for one child")
        seen.add(key)
    return dict(pairs)


def _load_state_strict(fh, *, source: str = "driver state") -> dict:
    """The ONE decoder for driver state and audit evidence. Duplicate properties and a
    non-object document are both DATA refusals (`ValueError`), never a traceback: every
    caller already maps `ValueError` to its documented rc-2 refusal."""
    doc = json.load(fh, object_pairs_hook=_no_duplicate_keys)
    if not isinstance(doc, dict):
        raise ValueError(
            f"{source} must be a JSON object mapping the campaign's fields; got "
            f"{type(doc).__name__}")
    return doc


def _locked_state_read(path: str) -> dict:
    """Read driver state while holding the same lock every writer here takes."""
    with _plan_lib().file_lock(path):
        with open(path, encoding="utf-8") as fh:
            return _load_state_strict(fh)


def _project_legacy_session_mode(state) -> None:
    """Keep the write-only `session_mode` projection true, in place (#927).

    Called from `_locked_state_update` — the module's ONLY driver-state writer — so the
    invariant holds by construction rather than by every future mutation path remembering it.
    Review finding A6: stated as a convention, the first path that forgets leaves `session_mode`
    stale and a rolled-back build then executes the OPPOSITE transport, silently.

    Two deliberate non-actions:

    - No canonical field ⇒ the legacy field is left ALONE. A pre-#927 campaign must not have a
      projection invented for it; `campaign_transport` migrates it on read instead.
    - An unrecognised canonical value ⇒ no projection is written. Guessing a legacy value from
      an unknown transport is how a rollback would silently pick the wrong one.
    """
    if not isinstance(state, dict):
        return
    transport = state.get("preferred_transport")
    if transport is None:
        return
    # Lazy import, matching this module's existing convention (see :4072).
    import driver_lib  # pylint: disable=import-outside-toplevel

    legacy = driver_lib.legacy_session_mode(transport)
    if legacy is not None:
        state["session_mode"] = legacy
    else:
        # An UNRECOGNISED canonical value must not leave a stale projection standing
        # (Step-11 finding 6). `preferred_transport="teleport"` with a leftover
        # `session_mode="fresh-session"` runs inline on this build and pane-chain after a
        # rollback — the exact opposite-transport regression the projection exists to prevent.
        # No projection is better than a false one.
        state.pop("session_mode", None)


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
            state = _load_state_strict(fh)
        new = mutate(state)
        if new is None:
            return None
        _project_legacy_session_mode(new)
        _atomic_write(path, json.dumps(new, indent=2) + "\n")
        return new


# ---------------------------------------------------------------------------
# #695 — the terminal-status write-back, and the issue-state probe behind AC2
# ---------------------------------------------------------------------------

DRIVER_STATE_DIRNAME = os.path.join("claude_docs", ".driver-state")

# An owner/repo segment. Interpolated into a GraphQL query string, so it is validated as a bare
# token rather than trusted from `git remote`.
_PROJECT_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")

# The GraphQL query behind AC2's corroboration. `gh issue view --json` CANNOT answer this on the
# installed CLI — its field list offers `state` and `closed` but neither `stateReason` nor
# `closedByPullRequestsReferences` — so the probe would have been unbuildable through it. This
# form was verified live on this host against the actual regression: issue #687 returns
# state CLOSED, stateReason COMPLETED, and one closing PR #691 with merged:true (#226).
_ISSUE_STATE_QUERY = """
{
  repository(owner: "%s", name: "%s") {
    issue(number: %d) {
      number state stateReason
      closedByPullRequestsReferences(first: 10, includeClosedPrs: true) {
        nodes { number merged }
      }
    }
  }
}
"""


def classify_issue_state(payload) -> str:
    """Map a `gh api graphql` issue payload to a probe verdict. PURE.

    Verdicts, per the #695 design: `confirmed_merged` (closed AND some closing PR merged),
    `confirmed_abandoned` (closed with no merged closing PR — e.g. `NOT_PLANNED`),
    `confirmed_open`, or `unknown`.

    `unknown` is returned for ANY shape that cannot be read confidently, and the caller must
    treat it as "no opinion" rather than as evidence — a probe that cannot answer must not veto
    a candidate, or a GitHub outage becomes a silent campaign stall.
    """
    try:
        issue = payload["data"]["repository"]["issue"]
        state = issue["state"]
    except (KeyError, TypeError):
        return "unknown"
    if not isinstance(state, str):
        return "unknown"
    if state.upper() == "OPEN":
        return "confirmed_open"
    if state.upper() != "CLOSED":
        return "unknown"
    nodes = (issue.get("closedByPullRequestsReferences") or {}).get("nodes")
    if not isinstance(nodes, list):
        return "confirmed_abandoned"
    if any(isinstance(n, dict) and n.get("merged") is True for n in nodes):
        return "confirmed_merged"
    return "confirmed_abandoned"


def repo_from_git(project_root: str, runner=None) -> str | None:
    """`owner/name` from the project's `origin` remote, or None.

    Derived rather than passed as a flag: #695's H1 finding was that an optional parameter
    nobody supplies ships the feature dead, and a `--repo` flag on one CLI would have repeated
    that. Returns None on any failure, which degrades AC2's corroboration to "file wins" rather
    than failing the handoff.
    """
    if runner is None:
        runner = _default_runner
    try:
        proc = runner(["git", "-C", project_root, "remote", "get-url", "origin"], 30)
    except (OSError, subprocess.SubprocessError, TypeError):
        return None
    if getattr(proc, "returncode", 1) != 0:
        return None
    url = (getattr(proc, "stdout", "") or "").strip()
    if not url:
        return None
    if url.endswith(".git"):
        url = url[:-4]
    # git@host:owner/name and https://host/owner/name both reduce to the last two segments.
    parts = [p for p in url.replace(":", "/").split("/") if p]
    if len(parts) < 2:
        return None
    owner, name = parts[-2], parts[-1]
    if not _PROJECT_TOKEN_RE.fullmatch(owner) or not _PROJECT_TOKEN_RE.fullmatch(name):
        return None
    return f"{owner}/{name}"


def build_issue_state_probe(repo: str, runner=None, timeout: int = 30):
    """A callable `issue_state_probe(number) -> verdict` for `driver_lib.next_ready_issue`.

    Lives HERE rather than in `driver_lib` because it does I/O: `driver_lib` promises "no I/O and
    no side effects" and the docs run it from a `python3 -c` one-liner, so the probe is injected
    across that boundary instead of imported through it (#695).

    Never raises: every failure — a malformed repo, a non-zero `gh`, a timeout, unparseable
    JSON — becomes `unknown`, because the caller's contract is that an unreachable probe leaves
    the file's own status standing.
    """
    if runner is None:
        runner = _default_runner
    try:
        owner, name = repo.split("/", 1)
    except (AttributeError, ValueError):
        return lambda _number: "unknown"
    if not owner or not name:
        return lambda _number: "unknown"

    def probe(number) -> str:
        # An issue number is interpolated into the query, so it must be exactly digits.
        if isinstance(number, bool) or not str(number).isdigit():
            return "unknown"
        argv = ["gh", "api", "graphql", "-f",
                "query=" + (_ISSUE_STATE_QUERY % (owner, name, int(number)))]
        try:
            proc = runner(argv, timeout)
        except (OSError, subprocess.SubprocessError, TypeError):
            return "unknown"
        if getattr(proc, "returncode", 1) != 0:
            return "unknown"
        try:
            return classify_issue_state(json.loads(getattr(proc, "stdout", "") or ""))
        except (ValueError, TypeError):
            return "unknown"
    return probe


ISSUE_PROBE_ENV = "RAWGENTIC_ISSUE_STATE_PROBE"
_PROBE_OFF_VALUES = frozenset({"0", "off", "false", "no"})


def issue_probe_enabled(env=None) -> bool:
    """Is AC2's issue-state corroboration on? **Defaults to ON.**

    The default direction is load-bearing (#695 review finding H1): corroboration that has to be
    switched on is corroboration nobody switches on, and an optional probe no caller supplies is
    exactly how AC2 nearly shipped dead. So this is an opt-OUT.

    The opt-out exists because the probe makes a live `gh` call, and a test that drives the
    handoff CLI with a synthetic campaign would otherwise depend on the real state of whatever
    issue numbers its fixture happens to reuse — several of this repo's own fixtures use numbers
    that really are merged here, which turned a fake queue into a "complete" campaign. Tests set
    it to `0`; production never does.
    """
    if env is None:
        env = os.environ
    return str(env.get(ISSUE_PROBE_ENV, "1")).strip().lower() not in _PROBE_OFF_VALUES


def _issue_state_probe_for(project_root: str):
    """The AC2 probe for a project, or None when it is unavailable or switched off."""
    if not issue_probe_enabled():
        return None
    repo = repo_from_git(project_root)
    return build_issue_state_probe(repo) if repo else None


def discover_driver_states(project_root: str, issue: int, listdir=os.listdir,
                           read_text=None) -> list[str]:
    """Every driver-state file whose queue names `issue`, in sorted filename order.

    A single-session WF2 run does not know which campaign (if any) owns its issue, so passing a
    path only moved the problem — the command discovers it instead (#695).

    **Cardinality is deliberate.** Zero matches is the NORMAL case (a run outside any campaign)
    and the caller treats it as a logged no-op. More than one match means several campaigns
    genuinely name this child, and EVERY one of them is updated: writing only the first would
    leave the others stale, which is the defect this exists to fix.

    Unreadable or unparseable files are skipped rather than fatal — one corrupt campaign file
    must not stop an unrelated campaign's bookkeeping.
    """
    if read_text is None:
        def read_text(path):
            with open(path, encoding="utf-8") as fh:
                return fh.read()
    root = os.path.join(project_root, DRIVER_STATE_DIRNAME)
    try:
        names = sorted(n for n in listdir(root) if n.endswith(".json"))
    except OSError:
        return []
    hits = []
    for name in names:
        path = os.path.join(root, name)
        try:
            state = json.loads(read_text(path))
            issues = state.get("issues", [])
        except (OSError, ValueError, AttributeError, UnicodeDecodeError):
            continue
        if not isinstance(issues, list):
            continue
        if any(isinstance(e, dict) and e.get("number") == issue for e in issues):
            hits.append(path)
    return hits


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
    5-6. ack, then gate on all seven. Not allowed returns now, predecessor alive AND still guarded.
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
            # #840 Step-11 rounds 3 and 4: the COMPLETE refusal reason, decided from this one
            # locked snapshot. Round 3 asked only about the caller's own generation and re-read
            # the state afterwards for the queue half; round 4 showed both were wrong. A live
            # claimant on a SUPERSEDED generation was invisible, and the second read could
            # observe a claim that appeared or expired in between. Both halves now come from `s`.
            claim_state["live_claim"] = driver_lib.handoff_claim_is_live(
                s, now_ts=now, lease_s=lease_s)
            claim_state["queue_current"] = driver_lib.handoff_queue_is_current(s)
            # Round 7, High 1: a STARTED claim on another generation is ambiguous, not absent —
            # completion is inferred rather than recorded (#846), so durable state cannot tell a
            # finished successor from a running one.
            claim_state["unprovable"] = driver_lib.handoff_claim_completion_unprovable(s)
            return None

        _locked_state_update(driver_state_path, _claim)
        if claim_state.get("verdict") == "refused":
            out["outcome"] = "claim_refused"
            # #840 Step-11 finding 4: a queue mismatch and a foreign claim are different
            # situations with different remedies, and reporting both as "a foreign or live claim"
            # sent the operator looking for a competing session that does not exist.
            #
            # Rounds 3 and 4, High 3: the payload diagnostic may speak ONLY when no live claim
            # caused the refusal — on ANY generation, not merely this caller's. `handoff_claim`
            # tests the claim before the payload, and a live claimant on a SUPERSEDED generation
            # is still somebody working. "No claim was ever created, open a new generation" is
            # the one instruction that can put a competitor beside them, so it is gated on the
            # strongest available evidence that nobody is in there.
            #
            # Round 7, High 1: "no live claim" is not the same as "nobody is in there". A
            # STARTED claim on another generation is AMBIGUOUS, because completion is inferred
            # and never recorded (#846) — durable state cannot distinguish a successor that
            # finished from one still running. The lifecycle gap predates this PR, but the
            # instruction below is emitted by code this PR added, so the PR owns making it safe:
            # an unprovable claim gets its own honest verdict rather than either confident one.
            if claim_state.get("unprovable") and not claim_state.get("live_claim"):
                out["claim_state_ambiguous"] = True
                out["reason"] = (
                    f"could not claim generation {generation}, and the campaign's state is "
                    "AMBIGUOUS: a claim on an earlier generation is marked started, and this "
                    "system records when a claim BEGINS but not when it ends (#846) — so it "
                    "cannot be told apart from a successor that is still working. The "
                    "predecessor stays alive and guarded and nothing has been torn down. "
                    "RECONCILE BY HAND before doing anything else: confirm whether that earlier "
                    "successor is still running (its pane, its transcript). Only once it is "
                    "proven finished may a new generation be opened — doing so blind can put a "
                    "competitor beside a live claimant")
            elif not claim_state.get("live_claim") and not claim_state.get("queue_current", True):
                out["queue_changed"] = True
                out["reason"] = (
                    f"could not claim generation {generation} — the campaign queue changed after "
                    "this handoff was written (a child's status moved), so the recorded payload no "
                    "longer matches durable state. Nothing is wrong with this session: the "
                    "predecessor stays alive and guarded. Re-run the handoff so a new generation "
                    "is opened from current state; retrying THIS generation can never succeed and "
                    "the claim lease does not apply, because no claim was ever created")
            else:
                out["reason"] = ("could not claim generation "
                                 f"{generation} — a live or foreign claim holds this campaign "
                                 "(possibly on a later generation than the one this session "
                                 "captured); the run continues in place and the predecessor "
                                 "stays alive and guarded. Do NOT open another generation — "
                                 "a claimant may be working right now")
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
        # #800 Step-11 review (High, raised INDEPENDENTLY by both cross-model passes): a trusted
        # base is REQUIRED here, not merely used when available. Dropping an untrusted base and
        # falling back to the exact comparison does not refuse a foreign registry at all — a row
        # from another workspace that spells `project_path` RELATIVELY (`./projects/rawgentic`)
        # compares equal to the expectation before any base is consulted, so the gate passed and
        # could authorize closing the predecessor pane. Verified by execution on a symlinked
        # fixture, including that the PRE-#800 exact comparison accepted it too — so this closes a
        # hole older than #800, deliberately, because this is the gate that guards a teardown.
        # `--registry` is passed through unvalidated at all four CLI call sites (verified), so the
        # registry's provenance has to be established HERE.
        trusted_base = _trusted_registry_base(
            registry_path, position["project_path"], position.get("repo_root"))
        out["results"]["project_switched"] = trusted_base is not None and registry_has_session(
            read_or_empty(registry_path), session_id,
            expected_project=position["project"],
            expected_project_path=position["project_path"],
            project_root=trusted_base)
        if trusted_base is None:
            record("project_switched", ["<registry provenance>"],
                   note=(f"refusing the registry {registry_path!r}: it does not resolve to "
                         f"{position['project_path']!r} inside the workspace holding the recorded "
                         f"repo_root {position.get('repo_root')!r}, so a row in it proves nothing "
                         f"about THIS workspace's successor"))
        # #840 — the same launcher-owned producer as `perform_handoff`, recomputed here rather
        # than trusted from the predecessor's run. This is the LAST gate before an irreversible
        # teardown, and the predecessor may have merged another child since it reported. Both
        # inputs are already in scope, so there is no signature change.
        revalidated, revalidation_reason = produce_queue_revalidated(
            {"driver_state_path": driver_state_path, "repo_root": position["repo_root"]},
            runner=runner)
        out["results"][QUEUE_REVALIDATED_STEP] = revalidated
        record("queue_revalidated", ["<driver-state receipt>"], note=revalidation_reason)

        launch_ladder = _predecessor_steps(mid_child_verification_steps())
        ok_early, failed_early, _ = evaluate_verifications(out["results"], steps=launch_ladder)
        if not ok_early:
            out["outcome"] = "teardown_refused"
            out["failed_step"] = failed_early
            out["reason"] = (f"refusing teardown: verification {failed_early!r} has not passed — "
                             "the predecessor stays alive and still guarded, and no claim is "
                             "acked so a later generation can still take over cleanly")
            # **Carry the ACTIONABLE reason into the caller-visible field (round-9 Medium 2).**
            # `produce_queue_revalidated` returns a reason that names the exact remedy, and it
            # was being filed only into `steps[].note` — which `_cmd_retire_predecessor` does not
            # project into its JSON. So the operator saw `queue_revalidated: false` and no way to
            # clear it, and retrying reproduced the same opaque refusal. The predecessor was
            # never in danger; the person holding it just had nothing to do.
            if failed_early == QUEUE_REVALIDATED_STEP and revalidation_reason:
                out["reason"] += f". {revalidation_reason}"
                out["revalidation_reason"] = revalidation_reason
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

        # --- 6. gate on all seven ---
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


def resume_prompt_for_state(state: dict, project: str | None = None, *,
                            repo_root: str | None = None) -> dict:
    """The canonical resume decision for the next ready child, as a RESULT OBJECT.

    Returns ``{"outcome": <disposition outcome>, "prompt": str | None, ...}``. `prompt` is
    non-None only for ``ready``.

    **#840 changed the return type, and the reason is the whole point of the gate.** This used to
    return ``str | None`` and collapse EVERY non-ready disposition into ``None``, with a docstring
    saying `None` meant "complete or blocked". A revalidation refusal returning `None` would
    therefore have been reported to the operator as *the epic finished* while the queue was stale
    — announcing completion is strictly worse than refusing, so `revalidation_required` has to be
    representable here rather than flattened.

    ``repo_root`` (#840) is what makes the head FRESHLY OBSERVED rather than merely supplied:
    `observe_head` runs the fetch and the rev-parse itself. A campaign carrying a
    `queue_revalidation` receipt REFUSES without it — an optional enforcement input is a bypass.

    The prompt wording stays delegated to `driver_lib._build_resume_prompt` via
    `fresh_session_handoff`: the successor must rebuild from durable state, and two copies of that
    wording would drift.
    """
    driver_lib = _driver_lib()
    observed_head = None
    if repo_root is not None:
        # Module-global lookup on purpose — the dataflow test patches `observe_head` to a sentinel
        # and proves the sentinel is what reaches the comparison, which a locally-bound reference
        # would defeat.
        observed_head = observe_head(repo_root)
    elif state.get("queue_revalidation") is not None:
        raise LauncherError(
            "this campaign carries a queue_revalidation receipt, so a resume decision needs a "
            "repo_root to freshly observe the head; refusing to decide without one")
    disposition = driver_lib.fresh_session_handoff(
        state, mode=driver_lib.FRESH_SESSION_MODE, project=project,
        observed_head=observed_head)
    outcome = disposition.get("outcome")
    result: dict = {"outcome": outcome,
                    "prompt": disposition.get("resume_prompt") if outcome == "ready" else None}
    if outcome == "revalidation_required":
        result["worklist"] = disposition.get("worklist", [])
        result["observed_head"] = disposition.get("observed_head")
        result["validated_head"] = disposition.get("validated_head")
        result["reason"] = disposition.get("reason")
    return result


#: #769 — the sweep gate's own return code, shared by `next-child` and `handoff`.
#:
#: 8 rather than 7 deliberately: `handoff` already spends 7 on the one-successor fence loser, and
#: one return code must not mean two different things across the two commands that share this
#: boundary. 9 is the compare-and-record refusal, which only `sweep record` can produce.
SWEEP_REQUIRED_RC = 8
SWEEP_HEAD_MOVED_RC = 9


def _sweep_gate(state, observed_head) -> "tuple[int, dict] | None":
    """The rc-8 boundary-sweep gate, in ONE place both callers use.

    Returned as (rc, payload) when the run must stop, or None when it may proceed. Implemented
    once rather than inlined twice so `next-child` and `handoff` cannot drift on the ordering —
    both consult it AFTER their revalidation check and AFTER their not-ready check, so a finished
    campaign reports "nothing ready" instead of being asked to sweep an empty queue.
    """
    status = _driver_lib().boundary_sweep_status(state, observed_head)
    if status in ("not_due", "swept"):
        return None
    return (SWEEP_REQUIRED_RC, {"outcome": "sweep_required", "status": status,
                                "observed_head": observed_head})


def _sweep_refusal_text(status: str, driver_state: str) -> str:
    """What an operator should DO about it — which differs by status, so the message must too.

    `missing` is self-clearable by doing the ordered work. `unreadable` is not: this PR ships no
    repair API, and telling a run to re-record over a corrupt field would loop it. Naming the
    wrong remedy is worse than naming none.
    """
    if status == "unreadable":
        return ("refusing: the campaign's `boundary_sweeps` field is unreadable, so whether the "
                "boundary was swept cannot be determined — and a fence must never read that as "
                f"'it was done'. REPAIR the state file ({driver_state}): copy it aside, RESET the "
                'malformed field to an empty list (`"boundary_sweeps": []`), check the file '
                "parses, then record the sweep again. RESET it — do not DELETE the key: an "
                "absent key means 'campaign predates this contract' and disarms the gate "
                "permanently, so deleting it would turn a repair into a silent bypass")
    return ("refusing: the remaining queue has not been swept against this boundary's learnings "
            "(the owner's standing order — see docs/multi-issue-driver.md). Do the sweep, then "
            "record it:\n"
            "  HEAD=$(python3 hooks/launcher_lib.py sweep begin --project-root . "
            "| python3 -c 'import json,sys; print(json.load(sys.stdin)[\"head\"])')\n"
            f"  python3 hooks/launcher_lib.py sweep record --driver-state {driver_state} "
            "--expected-head \"$HEAD\" --after-issue <the child that just finished> "
            "--learnings '<what it taught>' --assess '{\"issue\":<n>,\"outcome\":\"unaffected\","
            "\"note\":\"<why it is unaffected>\"}' --project-root .")


def _cmd_sweep(args) -> int:
    """#769 — `sweep begin | record | status`.

    `begin` prints the head the assessment is about to be made against; `record` re-observes it
    under the state lock and refuses if it moved (compare-and-record — observing only at WRITE
    time would stamp stale assessments with a fresh head, which is the staleness the gate exists
    to catch); `status` answers the successor's question.

    Machine output on stdout, operator text on stderr — `next-child` publishes a data contract on
    stdout and two tests already broke on that boundary once.
    """
    driver_lib = _driver_lib()
    project_root = getattr(args, "project_root", None) or "."
    try:
        observed_head = observe_head(project_root)
    except LauncherError as exc:
        print(f"refusing: {exc}", file=sys.stderr)
        return 5
    if args.verb == "begin":
        print(json.dumps({"head": observed_head}))
        return 0

    try:
        with open(args.driver_state, encoding="utf-8") as fh:
            state = _load_state_strict(fh, source=args.driver_state)
    except (OSError, ValueError) as exc:
        print(f"refusing: cannot read {args.driver_state}: {exc}", file=sys.stderr)
        return 2

    if args.verb == "status":
        status = driver_lib.boundary_sweep_status(state, observed_head)
        print(json.dumps({"status": status, "observed_head": observed_head}, indent=2))
        return 0 if status in ("not_due", "swept") else 3

    # verb == "record". The expected-head comparison happens INSIDE the lock below, not here:
    # comparing before acquiring it leaves a window in which the head moves and the record is
    # appended anyway, which is precisely the staleness the compare-and-record rule promises to
    # refuse (Step-11 finding). This early check is only a cheap reject of an obviously stale
    # caller; the authoritative one is under the lock.
    if args.expected_head != observed_head:
        print(json.dumps({"outcome": "head_moved", "expected_head": args.expected_head,
                          "observed_head": observed_head}, indent=2))
        print("refusing: origin/main moved between the sweep and this record, so the assessments "
              "describe a head that is no longer current. Re-run `sweep begin`, re-assess against "
              "the new head, and record again", file=sys.stderr)
        return SWEEP_HEAD_MOVED_RC
    # `--after-issue` is OPTIONAL and OMISSION is how the no-completion head move is expressed.
    # The literal text "null" is REJECTED rather than treated as that case: accepting it would
    # make a typo indistinguishable from the intended value. Validated here rather than by
    # `type=int` so the caller gets a return code and a sentence, not an argparse SystemExit.
    after_issue = getattr(args, "after_issue", None)
    if after_issue is not None:
        try:
            after_issue = int(after_issue)
        except (TypeError, ValueError):
            print(f"refusing: --after-issue must be an issue NUMBER, got {after_issue!r}. To "
                  "record a boundary where origin/main moved with no child completing, OMIT the "
                  "flag entirely — the literal text 'null' is not accepted, because a typo must "
                  "not pass for that case", file=sys.stderr)
            return 2
    assessments = []
    for raw in (args.assess or []):
        try:
            entry = json.loads(raw)
        except ValueError as exc:
            print(f"refusing: --assess must be one JSON object, got {raw!r}: {exc}",
                  file=sys.stderr)
            return 2
        if not isinstance(entry, dict):
            print(f"refusing: --assess must be a JSON OBJECT, got {type(entry).__name__}",
                  file=sys.stderr)
            return 2
        assessments.append(entry)

    outcome = {}

    def _record(state):
        # RE-OBSERVE under the lock. This is the authoritative half of compare-and-record: the
        # pre-lock check above can go stale between its comparison and this mutation, and
        # appending then would stamp assessments with a head that had already moved.
        locked_head = observe_head(project_root)
        if locked_head != args.expected_head:
            outcome["head_moved"] = locked_head
            return None                      # None ⇒ _locked_state_update writes nothing
        new = driver_lib.record_boundary_sweep(
            state, after_issue=after_issue, swept_at_head=locked_head,
            learnings=args.learnings, assessments=assessments, now_ts=int(time.time()))
        outcome["result"] = "replayed" if new is None else "recorded"
        return new

    try:
        _locked_state_update(args.driver_state, _record)
    except driver_lib.DriverStateError as exc:
        print(f"refusing: {exc}", file=sys.stderr)
        return 2
    except LauncherError as exc:
        print(f"refusing: {exc}", file=sys.stderr)
        return 5
    if "head_moved" in outcome:
        print(json.dumps({"outcome": "head_moved", "expected_head": args.expected_head,
                          "observed_head": outcome["head_moved"]}, indent=2))
        print("refusing: origin/main moved while the record was being written, so nothing was "
              "written. Re-run `sweep begin`, re-assess, and record again", file=sys.stderr)
        return SWEEP_HEAD_MOVED_RC
    print(json.dumps({"result": outcome.get("result"), "head": observed_head,
                      "after_issue": after_issue}, indent=2))
    return 0


def _cmd_next_child(args) -> int:
    """#840 Step-11 finding 2 — the gated selection path for the IN-SESSION loop.

    The epic driver's default mode is `single-session`: it loops child-by-child in one session and
    never crosses a process boundary, so it never calls `handoff`. Before this command existed the
    in-session loop had no gated way to pick the next child — the skill read driver state itself —
    and `fresh_session_handoff` returned `single_session` before selection, so an armed campaign
    with a STALE receipt advanced anyway. That was the main path, not a corner.

    Moving the gate above the mode check in `driver_lib` was necessary but not sufficient: the loop
    still needed a caller that makes a REAL observation, because a pure function cannot fetch. This
    is that caller. Exit codes match `handoff`'s so the two surfaces cannot drift:

    - **0** — a child is ready; its number is on stdout as JSON.
    - **3** — nothing ready (`complete` / `blocked`), with the outcome named.
    - **5** — the head could not be observed (fail-closed).
    - **6** — the queue needs revalidation; the worklist is on stdout.
    """
    driver_lib = _driver_lib()
    try:
        with open(args.driver_state, encoding="utf-8") as fh:
            state = _load_state_strict(fh, source=args.driver_state)
    except (OSError, ValueError) as exc:
        print(f"refusing: cannot read {args.driver_state}: {exc}", file=sys.stderr)
        return 2
    try:
        observed_head = observe_head(getattr(args, "project_root", None) or ".")
    except LauncherError as exc:
        print(f"refusing: {exc}", file=sys.stderr)
        return 5
    probe = _issue_state_probe_for(getattr(args, "project_root", ".")) \
        if not getattr(args, "no_probe", False) else None
    try:
        # `mode=FRESH_SESSION_MODE` is passed deliberately even for an in-session loop: it is what
        # makes the disposition compute `complete`/`ready`/`blocked` instead of short-circuiting to
        # `single_session`. This command answers "which child may I start", not "should I hand off",
        # and the caller does not act on the process-boundary part of the verdict.
        disposition = driver_lib.fresh_session_handoff(
            state, mode=driver_lib.FRESH_SESSION_MODE,
            project=getattr(args, "project", None), issue_state_probe=probe,
            observed_head=observed_head)
    except driver_lib.DriverStateError as exc:
        print(f"refusing: {exc}", file=sys.stderr)
        return 2
    outcome = disposition.get("outcome")
    if outcome == "revalidation_required":
        print(json.dumps({"outcome": outcome, "observed_head": observed_head,
                          "validated_head": disposition.get("validated_head"),
                          "worklist": disposition.get("worklist", []),
                          "reason": disposition.get("reason")}, indent=2))
        return 6
    if outcome == "no_project":
        # #840 Step-11 round 2, finding 2 (High, reproduced): this used to fold into rc 3, which
        # this command documents as "nothing ready" — so a perfectly ready campaign whose state
        # omits the optional `project` field reported as complete-or-blocked and the loop could
        # stop. `project` is not required by queue.schema.json, and a default single-session
        # campaign historically never needed one, because only a successor BIND needs it.
        #
        # It is a configuration error, not a queue verdict, so it gets its own rc and says what to
        # do. Selection itself already succeeded: the gate passed and a child is ready.
        print(json.dumps({"outcome": outcome, "observed_head": observed_head,
                          "next_issue": disposition.get("next_issue"),
                          "errors": disposition.get("errors", [])}, indent=2))
        print("refusing: the queue is fresh and a child IS ready, but this campaign has no valid "
              "`project` for the resume-prompt bind. Pass --project, or add `project` to the "
              "driver-state file. This is a config error, NOT 'nothing ready' (#840)",
              file=sys.stderr)
        return 2
    if outcome != "ready":
        print(json.dumps({"outcome": outcome, "observed_head": observed_head}, indent=2))
        return 3
    # #769 — the boundary-sweep gate, AFTER the not-ready check so a finished campaign is never
    # asked to sweep an empty queue.
    gate = _sweep_gate(state, observed_head)
    if gate is not None:
        rc, payload = gate
        print(json.dumps(payload, indent=2))
        print(_sweep_refusal_text(payload["status"], args.driver_state), file=sys.stderr)
        return rc
    # #927 AC 4, the in-session half. A `ready` here under an `inline` campaign means the run is
    # about to take the next child IN THIS SESSION — a choice the campaign recorded, not a
    # degradation. `boundary_advisory_line` cannot speak for it (preferred and effective agree),
    # so the operator would learn it only from silence. Advisory only: it never changes this
    # command's exit code, and the at-most-once claim keeps `handoff` and `next-child` from both
    # emitting for the same boundary.
    preferred, provenance = driver_lib.campaign_transport(state)
    line = driver_lib.inline_mode_advisory_line(preferred=preferred, provenance=provenance,
                                                next_issue=disposition["next_issue"])
    if line is not None:
        _emit_advisory_once(args.driver_state,
                            transition_id=f"bnd:{state.get('campaign', 'campaign')}:"
                                          f"{disposition['next_issue']}",
                            line=line)
    print(json.dumps({"outcome": "ready", "next_issue": disposition["next_issue"],
                      "observed_head": observed_head}, indent=2))
    return 0


def _cmd_rebuild_receipt(args) -> int:
    """Persist a rebuilt `queue_revalidation` receipt, under the lock, against a FRESH head.

    Round-9 High 4 and B3. `driver_lib.rebuild_receipt` is pure and returns a new state; the skill
    documented `new_state = rebuild_receipt(state, head, audited)` and then never said how to
    write it, so the prescribed remedy could not be followed to the end — the file was untouched,
    the skill's own validation command validated the unchanged (receipt-less) file and printed
    `receipt OK`, and selection still refused. Worse, the obvious way to write it — rebuild from
    the snapshot read at step 1, then replace the file — is a lost update: a concurrent
    `record-child-outcome` committed during the audit is erased by the atomic replace, and the
    lock cannot repair a read taken before it.

    So the rebuild happens INSIDE `_locked_state_update`, against the state read under that lock,
    not against whatever the caller last saw. The caller supplies EVIDENCE (`--audited`, a JSON
    object of `{issue number: record}`), never a replacement state.

    The head is observed here too, for the reason the whole gate exists: a caller-supplied head
    can be stale, and a receipt attesting a head nobody confirmed is the vacuous pass this
    feature was built to remove.
    """
    driver = _driver_lib()
    try:
        head = observe_head(getattr(args, "project_root", None) or ".")
    except LauncherError as exc:
        print(f"refusing: {exc}", file=sys.stderr)
        return 5
    audited: dict = {}
    if args.audited:
        # **Duplicate PROPERTIES are refused at decode time (round 12).** The canonical-key check
        # below catches `"1"` versus `"01"`, but `json.load` silently keeps the LAST of two
        # identical `"1"` properties before any check can see either — so evidence was discarded
        # while the command reported success and opened the gate. A Python dict cannot express
        # that shape, which is exactly why the round-11 test missed it.
        # The duplicate-property and non-object checks are the module-level
        # `_load_state_strict` (#840 round 13) — this used to carry its own nested copy, which
        # is how four other decode paths were left unprotected.
        try:
            with open(args.audited, encoding="utf-8") as fh:
                raw = _load_state_strict(fh, source=args.audited)
        except (OSError, ValueError) as exc:
            print(f"refusing: cannot read {args.audited}: {exc}", file=sys.stderr)
            return 2
        # **Canonical keys, and collisions REFUSED (round-11).** `{int(key): value}` mapped
        # `"1"` and `"01"` to the same integer, so one record silently won, the command returned
        # rc 0, and the gate then selected that child on evidence the operator never intended to
        # supply. Losing audit evidence while REPORTING SUCCESS is the worst failure mode this
        # command has, and it is the same non-canonical-key defect the receipt validator already
        # refuses — this path simply had its own conversion.
        audited = {}
        for key, value in raw.items():
            if not (isinstance(key, str) and _CANONICAL_ISSUE_KEY_RE.match(key)):
                print(f"refusing: {args.audited} key {key!r} is not a canonical issue number "
                      "(ASCII decimal, no leading zeros)", file=sys.stderr)
                return 2
            number = int(key)
            if number in audited:
                print(f"refusing: {args.audited} names issue #{number} more than once; two "
                      "records cannot both be the evidence for one child", file=sys.stderr)
                return 2
            audited[number] = value

    def _mutate(state):
        return driver.rebuild_receipt(state, head, audited)

    try:
        _locked_state_update(args.driver_state, _mutate)
    except driver.DriverStateError as exc:
        print(f"refusing: {exc}", file=sys.stderr)
        return 6
    except (OSError, ValueError) as exc:
        print(f"refusing: cannot update {args.driver_state}: {exc}", file=sys.stderr)
        return 2
    # Re-read from DISK and validate what actually landed. "It returned a good object" is not
    # "the file on disk is good", and this command exists precisely because that gap was real.
    try:
        driver.validate_queue_revalidation(_locked_state_read(args.driver_state))
    except driver.DriverStateError as exc:
        print(f"refusing: the persisted receipt does not validate: {exc}", file=sys.stderr)
        return 6
    print(json.dumps({"validated_head": head, "audited": sorted(audited)}))
    return 0


def _cmd_record_child_outcome(args) -> int:
    """#695 — write a child's terminal status back to every campaign queue that names it.

    Fail modes are split deliberately, because they are different KINDS of thing:

    - **Fail-OPEN, exit 0, but never silent:** no campaign file names this issue (the normal
      case — a single-session run outside any campaign), or the state directory is absent. The
      reason goes to BOTH stdout and stderr so a wrapper that discards one still records it, and
      WF2 Step 16 captures it into the run-record. Silent fail-open is how a real miss comes to
      look exactly like a deliberate no-op.
    - **Fail-CLOSED, non-zero, file untouched:** a status outside the vocabulary, a terminal
      regression, or a corrupt/unreadable campaign file. Those are caller or data errors, not
      states of the world, and swallowing them would corrupt the state the resume path treats as
      authoritative.
    """
    driver = _driver_lib()
    if args.driver_state:
        targets = [args.driver_state] if os.path.isfile(args.driver_state) else []
        if not targets:
            _say(f"no driver-state file at {args.driver_state!r} — wrote nothing (#695 "
                 "fail-open: a run outside any campaign is the normal case)")
            return 0
    else:
        targets = discover_driver_states(args.project_root, args.issue)
        if not targets:
            _say(f"no campaign under {args.project_root}/{DRIVER_STATE_DIRNAME} names issue "
                 f"#{args.issue} — wrote nothing (#695 fail-open)")
            return 0

    wrote, skipped = [], []
    for path in targets:
        def _mutate(state, _p=path):
            return driver.record_child_outcome(state, args.issue, args.status)
        try:
            result = _locked_state_update(path, _mutate)
        except (OSError, ValueError, driver.DriverStateError) as exc:
            # Loud, and it does NOT continue: a corrupt or refusing campaign file must not be
            # reported alongside a successful sibling write as though the run were clean.
            print(f"refusing: {path}: {exc}", file=sys.stderr)
            return 1
        if result is None:
            skipped.append(path)
        else:
            wrote.append(path)
    for path in wrote:
        _say(f"recorded issue #{args.issue} as {args.status!r} in {path}")
    for path in skipped:
        _say(f"issue #{args.issue} already {args.status!r} (or absent) in {path} — no write")
    return 0


def _say(message: str) -> None:
    """Print to BOTH streams (#695 M2). A fail-open reason that only reaches stdout is lost to
    any wrapper that captures one stream, and then a real miss is indistinguishable from a
    deliberate no-op."""
    print(message)
    print(message, file=sys.stderr)


def _cmd_transport(args) -> int:
    """The `transport` command group — AC 2, and the creation seam AC 1 depends on.

    All three verbs share one rule: the guard is evaluated INSIDE the same
    `_locked_state_update` that writes, against the state read under that lock (pass-2 finding
    F7). A pre-lock check permits a child to start between the check and the write, which is the
    mid-child mode flip `transport_set_blocked` exists to refuse.
    """
    driver_lib = _driver_lib()
    verb = args.verb
    outcome: dict = {}

    if verb == "resolve-creation":
        # Section 16.6. Write-once: a second call is a caller error, not a silent re-probe.
        transport, reason = resolve_creation_transport()

        def _create(s):
            if s.get("preferred_transport") is not None:
                outcome["refused"] = ("already_recorded", s["preferred_transport"])
                return None
            new = dict(s)
            new["preferred_transport"] = transport
            projected = driver_lib.legacy_session_mode(transport)
            if projected is not None:
                new["session_mode"] = projected
            # #769 — SEED the sweep contract here, at the one production point where a campaign
            # is created. `boundary_sweep_status` grandfathers a campaign with no
            # `boundary_sweeps` key, so without this seeding every NEW campaign would inherit the
            # migration exemption meant for campaigns already in flight, and the gate would never
            # fire for anyone (Step-11 finding: the only seeding in the first draft was in a test
            # helper). Empty list = "gated, nothing swept yet".
            new.setdefault(driver_lib.BOUNDARY_SWEEPS_KEY, [])
            now = int(time.time())
            rid = driver_lib.append_resolution(
                new, transition_id=f"c:{new.get('campaign', 'campaign')}:0",
                generation=new.get("generation", 0), trigger="creation", kind="creation",
                preferred=transport, effective=transport, probe_reason=reason, probe_ms=None,
                pane_ref=None, panes_before=None, now_ts=now)
            driver_lib.append_terminal_outcome(new, resolution_id=rid, outcome="created",
                                               now_ts=now)
            outcome["transport"] = transport
            return new

        _locked_state_update(args.driver_state, _create)
        if "refused" in outcome:
            _why, recorded = outcome["refused"]
            print(f"refusing: this campaign already records preferred_transport "
                  f"{recorded!r} — creation is write-once. Use `transport set` to change it.",
                  file=sys.stderr)
            return 2
        print(json.dumps({"preferred_transport": outcome["transport"], "reason": reason},
                         indent=2))
        return 0

    if verb == "set":
        # Self-review S1: an unvalidated value would become `preferred_transport`, which
        # `campaign_transport` then reports as `unrecognized` and degrades to inline for ever.
        if args.value not in driver_lib.TRANSPORTS:
            print(f"refusing: {args.value!r} is not a transport; expected one of "
                  f"{sorted(driver_lib.TRANSPORTS)}", file=sys.stderr)
            return 2
        try:
            reason = driver_lib.validate_operator_note(args.reason, what="--reason")
            operator = driver_lib.validate_operator_note(args.operator, what="--operator")
        except driver_lib.DriverStateError as exc:
            print(f"refusing: {exc}", file=sys.stderr)
            return 2

        def _set(s):
            blocked, why = driver_lib.transport_set_blocked(s, now_ts=int(time.time()))
            if blocked:
                outcome["blocked"] = why
                return None                 # no write at all
            new = dict(s)
            new["preferred_transport"] = args.value
            projected = driver_lib.legacy_session_mode(args.value)
            if projected is not None:
                new["session_mode"] = projected
            new["transport_audit"] = list(new.get("transport_audit") or []) + [
                {"transport": args.value, "operator": operator, "reason": reason,
                 "observed_at": int(time.time())}]
            return new

        _locked_state_update(args.driver_state, _set)
        if "blocked" in outcome:
            print(f"refusing: {outcome['blocked']} — the recorded transport may not change while "
                  "a child is in flight or a boundary holds a claim (that is the "
                  "`mid-child-handoff` case, not this one)", file=sys.stderr)
            return 3
        print(json.dumps({"preferred_transport": args.value, "reason": reason}, indent=2))
        return 0

    # verb == "unpark"
    try:
        reason = driver_lib.validate_operator_note(args.reason, what="--reason")
        operator = driver_lib.validate_operator_note(args.operator, what="--operator")
        if args.adopt is not None:
            validate_pane_id(args.adopt)
    except (driver_lib.DriverStateError, LauncherError) as exc:
        print(f"refusing: {exc}", file=sys.stderr)
        return 2
    resolved = "successor_acked" if args.adopt is not None else "reconciled_no_action"

    def _unpark(s):
        blocked, why = driver_lib.unpark_blocked(s, resolution_id=args.resolution_id)
        if blocked:
            outcome["blocked"] = why
            return None
        new = dict(s)
        new["transitions"] = list(new.get("transitions") or [])
        if args.adopt is not None:
            driver_lib.record_successor_pane(new, resolution_id=args.resolution_id,
                                             pane=args.adopt)
        driver_lib.append_unpark(new, resolution_id=args.resolution_id, outcome=resolved,
                                 operator=operator, reason=reason, now_ts=int(time.time()))
        return new

    _locked_state_update(args.driver_state, _unpark)
    if "blocked" in outcome:
        print(f"refusing: {outcome['blocked']} — only a `parked_unreconcilable` resolution may be "
              "unparked, and only once", file=sys.stderr)
        return 3
    print(json.dumps({"resolution_id": args.resolution_id, "outcome": resolved,
                      "operator": operator}, indent=2))
    return 0


def _claimant_id(args) -> str:
    """Who holds the boundary claim. Per-process, so concurrent sessions cannot collide.

    `$CLAUDE_CODE_SESSION_ID` is the same identity the session registry uses; the agent name is the
    fallback for a cron-spawned launcher that has no session of its own. Never
    `claude_docs/.current_session_id` — a shared file every session overwrites.
    """
    raw = os.environ.get("CLAUDE_CODE_SESSION_ID") or f"launcher:{getattr(args, 'name', '?')}"
    # Step-11 F6: this lands in durable state AND is interpolated into the successor's generated
    # prompt, so it must be an opaque identifier. A hostile or merely odd environment value
    # carrying newlines could otherwise reshape those instructions.
    return _driver_lib().validate_claimant_id(raw)


def _classify_launch(out: dict, *, panes_before) -> tuple[str | None, bool]:
    """`perform_handoff`'s result → `(terminal_outcome_or_None, refusal_was_measured)`.

    Design §16.3. `None` means **append no terminal event**: the resolution stays the crash
    signature §4.4 reconciles. That is the whole point of pass-2 finding F5 — a terminal event
    CLOSES a resolution, and reconciliation only examines unterminated ones, so recording
    `launch_failed` on a shape that MIGHT have created a pane removes that pane from
    reconciliation's reach for good.

    The second return value is `True` only for the ONE shape a live probe measured end-to-end
    (non-zero split, an enumerated refusal code, and an empty post-failure inventory diff). It is
    the sole thing §16.4 may downgrade a campaign's preference on.
    """
    if out.get("ok"):
        return ("successor_acked", False)
    failed = out.get("failed_step")
    if failed == "pane_inventory_unavailable":
        # herdr could not even be listed, so no split was attempted. Distinct from a refusal:
        # an OBSERVATION failure must never downgrade a healthy campaign (F4).
        return ("no_split_attempted", False)
    if failed == "split":
        panes_after = _pane_inventory(_default_runner)
        if panes_after is None or panes_before is None:
            return (None, False)            # cannot prove either way → indeterminate
        if set(panes_after) - set(panes_before):
            return (None, False)            # something appeared → a pane may exist
        code = out.get("failure_code")
        return ("launch_failed", code in _driver_lib().CREATION_REFUSAL_CODES)
    if failed in ("split_response_unparseable", "split_response_not_new"):
        # The RESPONSE was unreadable or unclaimable — that is not evidence about what exists.
        return (None, False)
    if failed in ("agent_start", "name_taken") and out.get("new_pane"):
        return ("start_failed", False)
    return (None, False)


def _emit_boundary_advisory(state_path: str, *, transition_id: str, resolution_id: str | None,
                            preferred: str, effective: str, reason: str) -> None:
    """Print the degradation advisory at most once per transition, and record what happened.

    Design §16.7 / AC 4. The claim is persisted BEFORE the print, so a competing surface
    (`next-child`) cannot also emit it. Advisory-only: nothing here changes an exit code, and a
    failure to print becomes a durable `failed` delivery rather than silence.
    """
    driver_lib = _driver_lib()
    line = driver_lib.boundary_advisory_line(preferred=preferred, effective=effective,
                                            reason=reason)
    if line is None:
        return
    _emit_advisory_once(state_path, transition_id=transition_id, line=line)


def _emit_advisory_once(state_path: str, *, transition_id: str, line: str) -> None:
    """Claim, print, record — the ONE advisory path both surfaces use (#927 AC 4).

    Two independent emitters exist (`handoff` at a boundary, `next-child` in the in-session loop),
    which is why the claim is durable and taken BEFORE the print: pass-2 finding F4 showed a pure
    predicate over a caller-supplied set lets both of them speak for the same transition.
    """
    driver_lib = _driver_lib()
    claimed: dict = {}

    def _claim(s):
        got, after = driver_lib.claim_advisory(s, transition_id, now_ts=int(time.time()))
        claimed["got"] = got
        return after if got else None

    try:
        _locked_state_update(state_path, _claim)
    except (OSError, ValueError, LauncherError):
        # A campaign whose state cannot be written still deserves the line; the durable record is
        # the backstop, not a precondition for speaking.
        print(line, file=sys.stderr)
        return
    if not claimed.get("got"):
        return
    delivered = True
    try:
        # STDERR, not stdout. Both emitting surfaces publish a machine-readable JSON document on
        # stdout that skills parse (`next-child`'s payload is read with `json.loads`), so an
        # advisory line there breaks the data contract — caught by two pre-existing
        # `test_revalidation_gate.py` cases on the FULL suite, after the area suite passed. The
        # advisory is operator-facing text; the JSON is the API.
        print(line, file=sys.stderr)
    except OSError:
        delivered = False
    try:
        _locked_state_update(state_path, lambda s: driver_lib.record_advisory_delivery(
            s, transition_id=transition_id, delivered=delivered, now_ts=int(time.time())))
    except (OSError, ValueError, LauncherError):
        pass                                # `undelivered_advisories` still shows the pending


def _cmd_handoff(args) -> int:
    """The non-test caller the Step-11 review found missing.

    The workspace `*-resume.sh` launchers invoke this. They live outside any git repo
    (workspace root is not a repository), so the logic that needs tests lives here and the
    launcher is a thin call — D-11 finding 2.
    """
    driver_lib = _driver_lib()
    with open(args.driver_state, encoding="utf-8") as fh:
        state = _load_state_strict(fh, source=args.driver_state)

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
    #
    # #927 PR 2: the recorded answer is now read through `campaign_transport`, which prefers the
    # canonical `preferred_transport` and MIGRATES a legacy `session_mode` on read. That is still
    # deference, not forcing — the launcher asks the campaign what it chose and obeys it; all that
    # changed is which field carries the answer. A campaign carrying neither field is the one
    # genuinely defaulted case and stays `inline`/single-session, byte-identical to before.
    # Step-11 F1: read the recorded answer under the LOCK. This used to use the unlocked snapshot
    # taken at the top of the command, so a `transport set pane_chain` committing in between was
    # ignored and the boundary returned rc 3 ("continue in-session") against a campaign that had
    # just asked for the opposite. `_locked_state_read` is already this function's idiom for
    # re-reading before a decision that matters.
    try:
        _locked = _locked_state_read(args.driver_state)
    except (OSError, ValueError, LauncherError):
        _locked = state                      # fail to the unlocked snapshot rather than refusing
    preferred, _provenance = driver_lib.campaign_transport(_locked)
    mode = driver_lib.legacy_session_mode(preferred) or "single-session"
    # include_bind=False: this disposition feeds `perform_handoff`, which sends the bind as SEND 1
    # of its own (#694). `resume_prompt_for_state` above keeps the default True — it serves the
    # interactive hand-back and the `claude -p` fallback, which each deliver one prompt and so have
    # nowhere else to put the bind.
    #
    # #695 AC2: the issue-state probe is supplied HERE, at the one production selection site. The
    # design review's sharpest finding was that an optional probe no caller passes ships the
    # corroboration dead, so the repo is derived from the project's own `origin` remote rather than
    # taken as a flag someone must remember. A repo that cannot be derived degrades to "the file
    # wins" — the handoff still runs.
    probe = _issue_state_probe_for(getattr(args, "project_root", "."))
    if probe is None:
        print("note: issue-state corroboration is OFF — the driver-state file's own status is "
              "being trusted (#695)", file=sys.stderr)
    # #840 — the freshly observed head, supplied HERE for the same reason #695's probe is: an
    # enforcement input that no caller threads in ships dead.
    #
    # Observed UNCONDITIONALLY (Step-11 finding 1, owner decision 2026-08-02). An earlier revision
    # observed only when the state already carried a receipt, which made the whole gate opt-in: a
    # never-armed campaign skipped the observation, then `next_ready_issue` saw neither an
    # observation nor a receipt and selected work normally. Observing always means an un-armed
    # campaign is REFUSED with an actionable reason instead of quietly advanced.
    try:
        observed_head = observe_head(getattr(args, "project_root", None) or ".")
    except LauncherError as exc:
        # Fail-CLOSED. Without a confirmed head every freshness comparison would compare a stale
        # value against itself and open the gate on a moved main.
        print(f"refusing: {exc}", file=sys.stderr)
        return 5
    disposition = driver_lib.fresh_session_handoff(state, mode=mode,
                                                  project=getattr(args, "project", None),
                                                  include_bind=False,
                                                  issue_state_probe=probe,
                                                  observed_head=observed_head)
    if disposition.get("outcome") == "revalidation_required":
        # Its OWN rc, distinct from a clean `complete` (design §6). A refusal reported through the
        # generic "no handoff" branch below would be indistinguishable from a finished epic, which
        # is the single failure this gate exists to prevent.
        print(json.dumps({"outcome": "revalidation_required",
                          "observed_head": disposition.get("observed_head"),
                          "validated_head": disposition.get("validated_head"),
                          "worklist": disposition.get("worklist", []),
                          "reason": disposition.get("reason")}, indent=2))
        return 6
    if disposition.get("outcome") != "ready":
        print(f"no handoff: campaign disposition is {disposition.get('outcome')!r} "
              f"(session_mode {mode!r})")
        return 3
    # #769 — the same gate `next-child` consults, in the same position, from the one helper. A
    # boundary handoff that skipped the learnings sweep is exactly the case this exists to catch.
    gate = _sweep_gate(state, observed_head)
    if gate is not None:
        rc, payload = gate
        print(json.dumps(payload, indent=2))
        print(_sweep_refusal_text(payload["status"], args.driver_state), file=sys.stderr)
        return rc

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

    # --- #927 PR 2, design §16.2: the boundary fence, in ONE locked mutation -------------------
    #
    # Step 1. The snapshot, the precondition, the generation and the claim all land inside a single
    # `_locked_state_update`. Pass-2 finding F3 is why: reading `preferred_transport` and evaluating
    # the precondition before taking the claim leaves both stale by the time the claim exists — a
    # concurrent `transport set inline` could commit and be ignored by this boundary, and a child
    # could move to `in_progress` between the check and the claim, letting a stale attempt launch a
    # second worker.
    next_issue = disposition["next_issue"]
    gate: dict = {}

    def _open_and_claim(s):
        locked_preferred, _prov = driver_lib.campaign_transport(s)
        gate["preferred"] = locked_preferred
        ok, why = driver_lib.child_boundary_precondition(s, next_issue)
        if not ok:
            gate["verdict"] = "precondition"
            gate["reason"] = why
            return None                     # no write at all
        opened = driver_lib.open_handoff(s, disposition, now_ts=int(time.time()))
        claimed, after = driver_lib.handoff_claim(
            opened, disposition["generation"], claimant=_claimant_id(args),
            now_ts=int(time.time()))
        if not claimed:
            held = opened.get("handoff_claim")
            gate["verdict"] = "claim_refused"
            gate["holder"] = held.get("claimant") if isinstance(held, dict) else None
            return None                     # no write at all
        gate["verdict"] = "claimed"
        return after

    _locked_state_update(args.driver_state, _open_and_claim)
    if gate.get("verdict") == "precondition":
        print(f"no handoff: {gate['reason']} — the child-boundary precondition needs the next "
              f"child queued and nothing in flight (a child in flight is the `mid-child-handoff` "
              f"case, not this one)")
        return 3
    if gate.get("verdict") == "claim_refused":
        # Pass-1 Critical (F1). The mid-child path tells a losing contender the run "continues in
        # place", and that is right THERE — it has a child genuinely in flight to keep working on.
        # At a boundary the precondition is that nothing is in flight, so continuing could only
        # mean starting the next child beside the claim holder's successor: the two-workers
        # condition this fence exists to prevent. Distinct rc, because rc 3 means "nobody is
        # working this boundary, proceed in-session" and rc 7 means the opposite.
        print(f"refusing: generation {disposition['generation']} is claimed by "
              f"{gate.get('holder')!r} — that holder owns this boundary. This contender is doing "
              "NO campaign work and is not starting the next child; exiting so exactly one "
              "successor exists.", file=sys.stderr)
        return 7

    claimant = _claimant_id(args)
    generation = disposition["generation"]
    transition_id = f"b:{state.get('campaign', 'campaign')}:{generation}"

    # Step 2. The probe runs AFTER the claim — only the holder of a valid claim probes, so
    # exactly-one-successor never depends on the probe being deterministic. It runs even under an
    # `inline` preference so every transition carries fresh capability evidence (recorded, never
    # acted on to upgrade).
    # Step-11 F5: the advisory claim is keyed on the BOUNDARY (campaign + next child), not on the
    # generation. Keyed on the generation, `handoff` and `next-child` used different ids for the
    # same boundary, so both could speak — a degradation line from one and an in-session line from
    # the other, for one situation.
    advisory_key = f"bnd:{state.get('campaign', 'campaign')}:{next_issue}"
    probe_started = time.time()
    capability_ok, pane_ok, probe_reason = transport_probe(pane_ref=args.anchor_pane)
    probe_ms = int((time.time() - probe_started) * 1000)
    preferred = gate.get("preferred") or preferred
    effective = (driver_lib.PANE_CHAIN_TRANSPORT
                 if (preferred == driver_lib.PANE_CHAIN_TRANSPORT and capability_ok and pane_ok)
                 else driver_lib.INLINE_TRANSPORT)

    # Step 3. The pre-split inventory. Captured HERE even though `perform_handoff` captures its own,
    # because the resolution must carry the baseline BEFORE the split (the C1 Critical) and
    # threading `perform_handoff`'s copy out would change a tested signature on the shipped launch
    # path. The two captures can differ only if a pane appears in between, which widens the
    # "appeared" set and makes reconciliation PARK — it fails toward safety.
    panes_before = _pane_inventory(_default_runner)

    # Step 4. The resolution lands before any launch, with `successor_pane` null and
    # `split_attempted` false. A crash here therefore PROVES nothing was launched.
    recorded: dict = {}

    def _append(s):
        recorded["id"] = driver_lib.append_resolution(
            s, transition_id=transition_id, generation=generation, trigger="child_boundary",
            kind="child_boundary", preferred=preferred, effective=effective,
            probe_reason=probe_reason, probe_ms=probe_ms, pane_ref=args.anchor_pane,
            panes_before=sorted(panes_before) if panes_before is not None else None,
            now_ts=int(time.time()))
        return s

    _locked_state_update(args.driver_state, _append)
    resolution_id = recorded["id"]

    if effective == driver_lib.INLINE_TRANSPORT:
        # Step 5a. Nothing is launched, so the claim is released in the same mutation that closes
        # the transition (F1 — nothing else in the codebase releases a claim, so returning here
        # would hold it for the full 1800 s lease and refuse every later contender).
        def _close_inline(s):
            driver_lib.append_terminal_outcome(s, resolution_id=resolution_id,
                                               outcome="inline_continued", now_ts=int(time.time()))
            _released, after = driver_lib.handoff_claim_release(s, generation, claimant=claimant)
            return after

        _locked_state_update(args.driver_state, _close_inline)
        _emit_boundary_advisory(args.driver_state, transition_id=advisory_key,
                                resolution_id=resolution_id, preferred=preferred,
                                effective=effective, reason=probe_reason)
        print(json.dumps({"outcome": "inline_continued", "transport": effective,
                          "preferred": preferred, "reason": probe_reason,
                          "resolution_id": resolution_id, "next_issue": next_issue}, indent=2))
        return 0

    # Step 5b. `split_attempted` is durable BEFORE the split — with the marker landing first,
    # `split_attempted: false` PROVES nothing was created, and only that state authorises a
    # relaunch. If it landed after, a crash in between would be indistinguishable from "never
    # started" and could put a second successor beside a live one.
    _locked_state_update(args.driver_state, lambda s: (
        driver_lib.mark_split_attempted(s, resolution_id=resolution_id) or s))

    out = perform_handoff(
        anchor_pane=args.anchor_pane, cwd=args.cwd, project_root=args.project_root,
        name=args.name, goal_condition=condition,
        resume_prompt=driver_lib.with_boundary_clause(
            disposition["resume_prompt"], generation=generation, claimant=claimant,
            kind="child_boundary", resolution_id=resolution_id),
        registry_path=args.registry, transcript_dir=args.transcript_dir,
        launch_mode=args.launch_mode, expected_project=getattr(args, "project", None),
        teardown=not args.no_teardown)

    outcome, downgrade = _classify_launch(out, panes_before=panes_before)

    def _close_launch(s):
        if out.get("new_pane"):
            driver_lib.record_successor_pane(s, resolution_id=resolution_id,
                                             pane=out["new_pane"])
        if outcome is None:
            # Deliberately NO terminal event: this is the indeterminate crash signature §4.4
            # reconciles, and its lease is what protects a possibly-live successor. Closing it here
            # would remove it from reconciliation's reach for good (pass-2 finding F5).
            return s
        driver_lib.append_terminal_outcome(s, resolution_id=resolution_id, outcome=outcome,
                                           now_ts=int(time.time()))
        if outcome == "successor_acked":
            # Step-11 F2. The claim is released below, and the child stays `queued` until the
            # SUCCESSOR marks it `in_progress` — a window in which a second invocation would pass
            # every check and launch again. `child_boundary_precondition` refuses on this marker.
            s["boundary_consumed"] = {"issue": next_issue, "generation": generation,
                                      "resolution_id": resolution_id,
                                      "observed_at": int(time.time())}
        # Step-11 F3: design section 16.4 restricts the downgrade to a campaign where
        # `successor_acked` has NEVER occurred, and the first implementation dropped that half —
        # so a campaign that had been chaining panes successfully for six children would be
        # durably switched to inline by one `pane_not_found`. `_classify_launch` answers "was the
        # refusal measured"; only state can answer "has this ever worked here".
        ever_acked = any(e.get("outcome") == "successor_acked"
                         for e in driver_lib.transition_events(s))
        if downgrade and not ever_acked:
            # One transaction with the terminal event (F2): a crash between them would leave a
            # terminal resolution without the promised downgrade, and a delayed downgrade could
            # overwrite a concurrent operator `transport set`.
            s["preferred_transport"] = driver_lib.INLINE_TRANSPORT
            projected = driver_lib.legacy_session_mode(driver_lib.INLINE_TRANSPORT)
            if projected is not None:
                s["session_mode"] = projected
            s.setdefault("transport_audit", []).append(
                {"transport": driver_lib.INLINE_TRANSPORT, "operator": "automatic",
                 "reason": "creation_refused", "observed_at": int(time.time())})
        _released, after = driver_lib.handoff_claim_release(s, generation, claimant=claimant)
        return after

    _locked_state_update(args.driver_state, _close_launch)
    # Guarded: this runs AFTER a launch that may have succeeded, so an unreadable state file here
    # must not turn rc 0 into a traceback (inline self-review, bug_logic lens). The advisory is the
    # only thing that depends on it, and an advisory never changes an exit code.
    try:
        _post = _locked_state_read(args.driver_state)
    except (OSError, ValueError, LauncherError):
        _post = {}
    downgraded = bool(driver_lib.campaign_transport(_post)[0] == driver_lib.INLINE_TRANSPORT
                      and preferred == driver_lib.PANE_CHAIN_TRANSPORT
                      and _post)
    if downgraded or effective != preferred:
        _emit_boundary_advisory(args.driver_state, transition_id=advisory_key,
                               resolution_id=resolution_id, preferred=preferred,
                               effective=(driver_lib.INLINE_TRANSPORT if downgraded else effective),
                               reason="creation_refused" if downgraded else probe_reason)
    print(json.dumps({k: out.get(k) for k in
                      ("ok", "results", "failed_step", "new_pane", "session_id",
                       "truncated", "cleanup")} | {"resolution_id": resolution_id,
                                                   "terminal_outcome": outcome}, indent=2))
    return 0 if out["ok"] else 4


def _read_text_arg(inline: str | None, path: str | None, what: str) -> str:
    """A text input that may arrive inline or as a file (#700).

    Both a resume prompt and a real goal condition are routinely multiline — §7.2 of the herdr
    runbook cites a 2847-char, 41-newline condition — and shell-quoting that from a skill body is
    the hazard this repo already answers with `git commit -F`. Read VERBATIM: stripping could move
    a marker that a caller put at the end of its prompt.
    """
    if inline is not None:
        return inline
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except OSError as exc:
        raise LauncherError(f"cannot read the {what} from {path!r}: {exc}") from exc


def _read_optional_text_arg(inline: str | None, path: str | None, what: str) -> str | None:
    """`_read_text_arg` for an input that may legitimately be absent entirely (#730).

    The goal/prompt pairs are `required=True`, so exactly one of their two forms is always present.
    The predecessor condition is optional — it is only meaningful when tearing down — so the
    both-absent case must return None rather than reaching `open(None)`. Delegates to
    `_read_text_arg` so there is ONE file reader, and an unreadable path still fails loudly naming
    the path.

    A SUPPLIED-but-blank value is refused. Not for the reason an earlier draft of this claimed:
    it does NOT hand the successor an empty goal — the successor's guard is the separate required
    `--goal-condition` pair, blank guards are already refused by `build_goal_command`, and the
    clear receipt binds to the transcript-derived `live_condition`, never to this string. This
    value is assertion-only. Blank is refused because asserting "my armed guard is <nothing>" is
    a caller mistake — almost always an empty file the caller meant to fill — and silently
    accepting it would emit a misleading "not the newest guard" diagnostic instead of naming the
    real problem.
    """
    if inline is None and path is None:
        return None
    text = _read_text_arg(inline, path, what)
    if not text.strip():
        source = f"file {path!r}" if path is not None else "the inline value"
        raise LauncherError(f"the {what} from {source} is blank — refusing a blank assertion")
    return text


def _cmd_ad_hoc_handoff(args) -> int:
    """#700 — the ad-hoc handoff: `perform_handoff` with no campaign behind it.

    `_cmd_handoff` cannot serve this case. It opens a driver-state file as its first act, derives
    the resume prompt from a campaign disposition, and gates on `fresh_session_available(...,
    launcher_armed=...)`; an ad-hoc caller has none of those, so all three refuse before a pane is
    ever split. This is therefore an argument adapter and nothing more — no sequencing, no gate and
    no send of its own, which is what keeps #696's hand-rolled-send failure mode out of the skill
    that calls it.
    """
    resume_prompt = _read_text_arg(args.resume_prompt, args.resume_prompt_file, "resume prompt")
    condition = _read_text_arg(args.goal_condition, args.goal_condition_file, "goal condition")

    # #758 Step-11 wave: --no-teardown skips the verbatim-carry validation entirely, so an
    # approval flag there could only ever mint a FALSE audit record ("override consumed"
    # where nothing was validated). Refuse the combination outright.
    if args.no_teardown and args.goal_rewrite_approved is not None:
        raise LauncherError(
            "--goal-rewrite-approved is meaningless with --no-teardown: an additive "
            "helper handoff never validates the goal carry, so the flag would emit a "
            "false audit record (#758). Drop one of the two")

    marker = args.prompt_marker
    # A literal newline is stored ESCAPED in the JSONL transcript, so a marker carrying one can
    # never match `transcript_has_marker`'s substring scan — `prompt_landed` would burn its whole
    # poll budget and fail closed after a pane, a session and an armed guard already existed.
    # Checked here rather than reusing `_reject_control_chars`, whose message is about herdr argv.
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in marker):
        raise LauncherError(
            f"prompt marker {marker!r} contains a control character — a transcript stores a "
            "newline escaped, so a multiline marker can never match the substring scan and "
            "prompt_landed could not pass")
    if len(marker.strip()) < PROMPT_MARKER_MIN_LEN:
        raise LauncherError(
            f"prompt marker {marker!r} is shorter than {PROMPT_MARKER_MIN_LEN} characters — the "
            "marker is matched as a plain substring, so a short common word would also match "
            "unrelated transcript content and pass prompt_landed before the prompt ever "
            "submitted. Use a token unique to this handoff, e.g. '[handoff-700]'")
    # Step-11 diff review: the length floor alone does not deliver what it aims at, since an
    # 8-character PHRASE is still ordinary prose. Requiring a single token narrows it much further
    # — the bind turn's own transcript output is prose and tool JSON, which does not contain
    # `[handoff-700]`-shaped tokens. Still a heuristic, not a proof of uniqueness; the guarantee
    # remains the caller's choice of token, which is why the skill states the rule too.
    if marker.strip() != marker or any(c.isspace() for c in marker):
        raise LauncherError(
            f"prompt marker {marker!r} contains whitespace — a marker must be a single token so it "
            "cannot match ordinary prose in the successor's transcript. Use e.g. '[handoff-700]'")

    # Step-11 diff review (Medium): nothing bound the anchor pane to the CALLING session, and
    # teardown CLOSES that pane — so a stale or mistyped `$HERDR_PANE_ID` would split from, and then
    # close, a stranger's pane. `retire_predecessor` already holds the rule this mirrors: a
    # destructive target must prove it hosts the session claiming authority over it.
    #
    # Scoped to the destructive request on purpose. Without teardown a wrong anchor is a pane split
    # in the wrong place — recoverable, and not worth requiring a live herdr probe (and therefore a
    # herdr server) for every ordinary handoff.
    teardown = not args.no_teardown
    own = ""
    if teardown:
        own = os.environ.get("CLAUDE_CODE_SESSION_ID", "")
        if not own:
            raise LauncherError(
                "$CLAUDE_CODE_SESSION_ID is unset or empty — refusing to retire the anchor pane. "
                "Closing a pane is irreversible, and a session that cannot prove its own identity "
                "cannot prove the pane it is asking to close is its own. Pass --no-teardown to hand off without retiring this pane")
        probe = _default_runner(build_pane_get_argv(args.anchor_pane))
        if getattr(probe, "returncode", 1) != 0:
            raise LauncherError(
                f"`herdr pane get {args.anchor_pane}` returned "
                f"rc={getattr(probe, 'returncode', None)} — refusing to retire the anchor pane "
                "because the pane cannot be proven to be yours right now")
        live = parse_pane_agent_session(getattr(probe, "stdout", "") or "")
        if live != own:
            raise LauncherError(
                f"pane {args.anchor_pane} hosts session {live!r}, not this session ({own!r}) — "
                "refusing to retire it. Closing it could kill an unrelated session; "
                "check $HERDR_PANE_ID")

    # #758 — verbatim goal carry (retirement path only). The successor of a RETIREMENT handoff
    # continues THIS session's work, so its goal must be this session's own live owner goal,
    # byte-for-byte — model-drafted STATE/MODE text belongs in the handoff file, never in the
    # goal. An additive helper (--no-teardown) legitimately gets different work, so it is
    # exempt. Fail CLOSED on missing/unreadable provenance (pass-1 F3): this runs before any
    # pane exists, so refusing strands nothing, and evidence denial must not become a bypass.
    strict_binding = False
    expected_goal: str | None = None
    used_override = False
    if teardown:
        if not _SESSION_ID_RE.fullmatch(own):
            raise LauncherError(
                f"$CLAUDE_CODE_SESSION_ID {own!r} is not a valid session id — refusing to "
                "build a transcript path from it (#758)")
        own_transcript = os.path.join(args.transcript_dir, f"{own}.jsonl")
        real_dir = os.path.realpath(args.transcript_dir)
        if os.path.dirname(os.path.realpath(own_transcript)) != real_dir:
            raise LauncherError(
                f"transcript path {own_transcript!r} escapes {args.transcript_dir!r} — "
                "refusing (#758)")
        try:
            with open(own_transcript, encoding="utf-8") as fh:
                own_text = fh.read()
        except (OSError, UnicodeDecodeError) as exc:
            raise LauncherError(
                f"cannot read this session's own transcript {own_transcript!r} ({exc}) — "
                "refusing the retirement handoff: without provenance the verbatim goal carry "
                "cannot be validated (#758), and an unreadable transcript must not become a "
                "bypass. Pass --no-teardown to hand off without retiring this pane") from exc
        expected_goal = live_owner_goal(own_text, strict=True)
        ok, reason, used_override = validate_goal_carry(
            condition, expected_goal, approved_answer=args.goal_rewrite_approved)
        if not ok:
            raise LauncherError(reason)
        strict_binding = True

    # `steps` is deliberately NOT passed: the canonical launch ladder is the only legal one for a
    # launch handoff (`evaluate_verifications` refuses anything else), and defaulting says so.
    out = perform_handoff(
        anchor_pane=args.anchor_pane, cwd=args.cwd, project_root=args.project_root,
        name=args.name, goal_condition=condition, resume_prompt=resume_prompt,
        registry_path=args.registry, transcript_dir=args.transcript_dir,
        prompt_marker=marker, expected_project=args.project,
        expected_project_path=args.project_path,
        # AC4 — an ad-hoc handoff hands off work, not the caller's own session, so teardown is
        # opt-in. `perform_handoff` no longer carries a default of its own (#700 field defect 1:
        # the library defaulted ON while this CLI defaulted OFF, so the two surfaces disagreed
        # about one operation), which is why it is always passed here.
        teardown=teardown,
        # Only meaningful when tearing down, and it is what lets the goal be CLEARED and the clear
        # CONFIRMED before the pane is closed. `own` is this session's own id, already required
        # above for the ownership proof.
        predecessor_session=(own if teardown else None),
        predecessor_goal_condition=_read_optional_text_arg(
            args.predecessor_goal_condition, args.predecessor_goal_condition_file,
            "predecessor goal condition"),
        strict_goal_binding=strict_binding,
        **({"expected_predecessor_goal": expected_goal} if strict_binding else {}))
    payload = {k: out[k] for k in
               ("ok", "results", "failed_step", "new_pane", "session_id",
                "truncated", "cleanup", "teardown_skipped", "predecessor_guard")}
    # #731 — the cause and the captured pane output ride the payload; `steps` stays out.
    # Via the helpers, not out[...]: they tolerate results that predate these keys, and they
    # derive from the step records for exits (teardown-phase) the library did not pre-fill.
    payload["failure_detail"] = failure_detail(out)
    if payload["failed_step"] and not payload["failure_detail"]:
        # #731 Step-8a Medium: the contract is a detail for EVERY failed step. An uncovered
        # or legacy result shape still names the step rather than shipping null.
        payload["failure_detail"] = (f"step {payload['failed_step']!r} failed with no "
                                     "recorded detail")
    payload["pane_capture"] = pane_capture(out)
    if used_override:
        # #758 — the audit record rides the output ONLY when an override was actually
        # consumed (goals differed AND an affirmative owner answer authorized it);
        # emitting it for an identical-goal run would fake an override that never was.
        payload["goal_rewrite_approved"] = args.goal_rewrite_approved
    print(json.dumps(payload, indent=2))
    # On BOTH streams, and in plain words: the JSON is easy to skim past, and this is the sentence
    # whose absence let a stranded guarded pane loop its Stop hook four times unnoticed (#700).
    if out.get("predecessor_guard"):
        print(f"\nPREDECESSOR: {out['predecessor_guard']}", file=sys.stderr)
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

    # #840 Step-11 finding 5 (Medium): the queue check used to run only inside `perform_handoff`,
    # i.e. AFTER `open_handoff` had bumped `generation` and written `handoff_pending`, and after the
    # cancelling `try/finally` had been entered. An abrupt death between that write and the block
    # left an UNCANCELLED generation for a queue nobody had checked. Checking before anything
    # durable is written means a stale queue costs no generation at all. `perform_handoff` still
    # checks: defence in depth, and it is the only check the `retire-predecessor` side gets.
    #
    # It runs on an UNLOCKED pre-read, deliberately, for two reasons. Ordering: a bad position or
    # "no active child" is a caller error and must keep its own exit code rather than being masked
    # by a queue refusal, so the plausibility check has to come first. And the freshness probe does
    # a `git fetch` — holding the campaign lock across a network call would block every concurrent
    # reader for its duration. The locked `_open` below stays authoritative; this only decides
    # whether to get that far.
    pre_disposition = driver_lib.mid_child_handoff(
        _locked_state_read(args.driver_state), position=position, include_bind=False)
    if pre_disposition.get("outcome") == "ready":
        revalidated, revalidation_reason = produce_queue_revalidated(
            {"driver_state_path": args.driver_state, "repo_root": position["repo_root"]})
        if not revalidated:
            print(json.dumps({"ok": False, "failed_step": QUEUE_REVALIDATED_STEP,
                              "reason": revalidation_reason}, indent=2))
            print(f"refusing mid-child handoff: {revalidation_reason}", file=sys.stderr)
            return 6

    # The disposition is computed INSIDE the lock so the generation it bumps is derived from the
    # state actually being written, not from a copy read earlier.
    held: dict = {}

    def _open(state):
        # include_bind=False — see `_cmd_handoff`: this prompt is delivered by `perform_handoff`,
        # which binds in its own send.
        disposition = driver_lib.mid_child_handoff(state, position=position, include_bind=False)
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
            on_successor=_record_successor,
            # #840 — MANDATORY on this call site. The mid-child ladder carries the
            # `queue_revalidated` rung, and `evaluate_verifications` fail-closes on an unreported
            # step, so omitting this would refuse every mid-child handoff. Pinned by a
            # source-level call-site test.
            campaign_context={"driver_state_path": args.driver_state,
                              "repo_root": position["repo_root"],
                              "generation": generation})
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
    # #682: REQUIRED. The resume prompt has to open with `/rawgentic:switch <project>`, and a bare
    # bind enters the switch skill's list mode and waits for a human — so a handoff with no project
    # name cannot produce a workable prompt and must refuse at the CLI rather than spawn a doomed
    # successor. Step-11 finding: this argument did not exist, so the documented child-boundary path
    # could never have produced a valid bind at all.
    p_ho.add_argument("--project", required=True,
                      help="the rawgentic project NAME the successor must bind (not its path)")
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

    # #700 — the ad-hoc handoff. Everything comes in DIRECTLY: no driver-state file to read, no
    # campaign disposition to be `ready`, and no `--launcher-armed` to assert, because an ad-hoc
    # caller is not a launcher. The skill `pane-handoff` is its only intended caller.
    p_ah = sub.add_parser("ad-hoc-handoff",
                          help="hand work to a fresh guarded successor pane, outside any "
                               "campaign (#700)")
    p_ah.add_argument("--anchor-pane", required=True,
                      help="the pane to split FROM — the caller's own ($HERDR_PANE_ID)")
    p_ah.add_argument("--name", required=True, help="herdr agent name for the successor")
    p_ah.add_argument("--project", required=True,
                      help="the rawgentic project NAME the successor must bind (not its path)")
    # Required, unlike the campaign path's optional equivalent (#700 design review): it binds
    # `project_switched` to the registry's own project_path as well as the name, and the caller
    # reads both off its own registry row anyway, so optionality bought nothing.
    p_ah.add_argument("--project-path", required=True,
                      help="as the registry records it, e.g. ./projects/rawgentic")
    p_ah.add_argument("--cwd", required=True)
    p_ah.add_argument("--project-root", required=True)
    p_ah.add_argument("--registry", required=True, help="claude_docs/session_registry.jsonl")
    p_ah.add_argument("--transcript-dir", required=True,
                      help="~/.claude/projects/<slug>/ — <session-id>.jsonl lives here")
    ah_prompt = p_ah.add_mutually_exclusive_group(required=True)
    ah_prompt.add_argument("--resume-prompt", help="the work, inline")
    ah_prompt.add_argument("--resume-prompt-file",
                           help="the work, read verbatim from a file (preferred: a real prompt "
                                "is multiline)")
    ah_goal = p_ah.add_mutually_exclusive_group(required=True)
    ah_goal.add_argument("--goal-condition", help="the successor's guard, inline")
    ah_goal.add_argument("--goal-condition-file",
                         help="the successor's guard, read verbatim from a file")
    p_ah.add_argument("--prompt-marker", required=True,
                      help="a token UNIQUE to this handoff that appears in the prompt; it is what "
                           "prompt_landed matches. Required because the check is skipped without "
                           "one, and a skipped check is not a gate")
    # Default ON (owner decision, 2026-07-29), reversing #700 AC4. AC4 reasoned that an ad-hoc
    # handoff hands off work rather than retiring the caller — but the first real handoff showed the
    # opposite in practice: the phrasings that trigger this ("pass off", "clear the context into a
    # new session") mean RETIRE THIS ONE, and the OFF default left a live pane re-prompting itself
    # from an armed goal. Retirement is gated on every verification passing and on the goal being
    # provably cleared, so the safe-by-construction path is also the expected one.
    p_ah.add_argument("--no-teardown", action="store_true", default=False,
                      help="keep the anchor pane alive after a successful handoff. Your /goal stays "
                           "ARMED and the output says so — use this only for an additive handoff "
                           "where you keep working. Named to match the `handoff` subcommand")
    # #730: the third condition flag was the only one WITHOUT a `-file` twin, while
    # `--goal-condition` and `--resume-prompt` both have one. The asymmetry was invisible until
    # argparse rejected the call, and it cost a real hand-off a wasted invocation at the moment it
    # had the least context to spend. Not `required=True` — unlike the goal/prompt pairs this whole
    # input is optional (it is only meaningful when tearing down).
    ah_pred = p_ah.add_mutually_exclusive_group(required=False)
    ah_pred.add_argument("--predecessor-goal-condition", default=None,
                         help="your OWN currently-armed goal condition, used only with "
                              "the default retirement to bind the clear receipt to the guard "
                              "actually cleared. Read it with `read-goal-condition --transcript "
                              "<own>.jsonl`")
    ah_pred.add_argument("--predecessor-goal-condition-file", default=None,
                         help="the same condition, read verbatim from a file (preferred: a real "
                              "condition routinely carries backticks and $(...), which the repo "
                              "answers with a file rather than a command line)")
    # #758 — a caller ASSERTION that the owner explicitly approved a goal text differing from
    # the predecessor's live goal (the verbatim-carry escape hatch). Takes the owner's verbatim
    # yes/no answer, never a bare boolean: the answer is recorded in the output JSON so the
    # audit trail carries the claimed approval text. No crypto root of trust exists — the
    # enforceable layer is the skill prose gating this on an explicit owner question.
    p_ah.add_argument("--goal-rewrite-approved", default=None, metavar="OWNER_ANSWER",
                      help="the owner's verbatim answer approving a DIFFERENT successor goal "
                           "(#758). Without it, a retirement handoff whose goal differs from "
                           "this session's live goal is refused")

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

    # #927 AC 2 + the creation seam. PR 1 shipped the pure guards and no caller, so the whole
    # feature was unreachable; these three verbs are the callers.
    p_tr = sub.add_parser("transport",
                          help="record, change or unpark a campaign's transport (#927)")
    tr_sub = p_tr.add_subparsers(dest="verb", required=True)
    p_tr_rc = tr_sub.add_parser("resolve-creation",
                                help="probe once and record a NEW campaign's preference (AC 1)")
    p_tr_rc.add_argument("--driver-state", required=True)
    p_tr_rc.add_argument("--project-root", default=".")
    p_tr_set = tr_sub.add_parser("set", help="change an in-flight campaign's preference (AC 2)")
    p_tr_set.add_argument("value", help="pane_chain | inline")
    p_tr_set.add_argument("--driver-state", required=True)
    p_tr_set.add_argument("--reason", required=True, help="why, for the audit record")
    p_tr_set.add_argument("--operator", default="operator")
    p_tr_up = tr_sub.add_parser("unpark",
                                help="clear a parked boundary transition — an operator decision")
    p_tr_up.add_argument("resolution_id")
    p_tr_up.add_argument("--driver-state", required=True)
    p_tr_up.add_argument("--reason", required=True)
    p_tr_up.add_argument("--operator", default="operator")
    adopt = p_tr_up.add_mutually_exclusive_group(required=True)
    adopt.add_argument("--adopt", metavar="PANE",
                       help="a surviving successor pane to adopt; validated before it is recorded")
    adopt.add_argument("--discard", action="store_true",
                       help="nothing survives worth adopting")

    # #769 — the child-boundary learnings sweep. `begin` captures the head the assessment will be
    # about; `record` re-compares it under the state lock; `status` answers the successor.
    p_sw = sub.add_parser("sweep",
                          help="record or query the child-boundary learnings sweep (#769)")
    sw_sub = p_sw.add_subparsers(dest="verb", required=True)
    p_sw_b = sw_sub.add_parser("begin",
                               help="print the head to assess against, as {\"head\": <sha>}")
    p_sw_b.add_argument("--project-root", default=".")
    p_sw_r = sw_sub.add_parser("record", help="record one boundary sweep (coverage-validated)")
    p_sw_r.add_argument("--driver-state", required=True)
    p_sw_r.add_argument("--project-root", default=".")
    p_sw_r.add_argument("--expected-head", required=True,
                        help="the head from `sweep begin`; a COMPARISON token, never an "
                             "authoritative assertion — it is re-observed under the lock")
    p_sw_r.add_argument("--after-issue", default=None,
                        help="the disposed child whose learnings drove this sweep. OMIT it when "
                             "origin/main moved with no child completing; the literal text "
                             "'null' is rejected so a typo cannot pass for that case")
    p_sw_r.add_argument("--learnings", required=True, help="what this boundary taught")
    p_sw_r.add_argument("--assess", action="append", default=[], metavar="JSON",
                        help="one JSON object per remaining eligible child: "
                             "{\"issue\":N,\"outcome\":\"unaffected|commented|rescoped\","
                             "\"note\":\"…\"[,\"ref\":\"https://…\"]}. JSON rather than a "
                             "delimited string because a ref contains '://'")
    p_sw_s = sw_sub.add_parser("status", help="has this boundary been swept? (rc 0 yes, 3 no)")
    p_sw_s.add_argument("--driver-state", required=True)
    p_sw_s.add_argument("--project-root", default=".")

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

    # #695 — the ONE owner of a child's terminal status write-back. Invoked at each authoritative
    # terminal event: WF2 Step 14 right after the merge is confirmed, and Step 16 as idempotent
    # reconciliation. Step 16 alone was the first design and it is NOT atomic with the merge.
    # #840 — the gated selection path for the in-session loop (Step-11 finding 2). Without this the
    # default single-session mode had no way to select a child through a freshly observed head.
    p_nc = sub.add_parser("next-child",
                          help="which child may be started now, gated on a freshly observed "
                               "origin/main (rc 0 ready, 2 caller/data error — PARSE STDOUT: a "
                               "`next_issue` key means selection succeeded and only `project` is "
                               "missing, otherwise the state is unusable; 3 nothing ready, "
                               "5 head unobservable, 6 revalidation required)")
    p_nc.add_argument("--driver-state", required=True)
    p_nc.add_argument("--project-root", default=".",
                      help="repository root; the head is observed with `git -C <root>`")
    p_nc.add_argument("--project", help="project name, for the resume-prompt bind")
    p_nc.add_argument("--no-probe", action="store_true",
                      help="skip the GitHub issue-state corroboration (#695); the driver-state "
                           "file's own statuses are then trusted")

    p_rr = sub.add_parser("rebuild-receipt",
                          help="rebuild and PERSIST the queue_revalidation receipt under the "
                               "state lock, against a freshly observed origin/main")
    p_rr.add_argument("--driver-state", required=True)
    p_rr.add_argument("--project-root", default=".",
                      help="repo whose origin/main is observed for the head")
    p_rr.add_argument("--audited",
                      help="JSON file holding {issue number: record} for the children audited "
                           "this pass; omit when nothing needed auditing")

    p_rco = sub.add_parser("record-child-outcome",
                           help="write a child's terminal status back to its campaign queue")
    p_rco.add_argument("--issue", type=int, required=True)
    p_rco.add_argument("--status", required=True,
                       help="one of the driver-state statuses (merged, deferred, abandoned, ...)")
    p_rco.add_argument("--driver-state",
                       help="a specific campaign file; omit to DISCOVER every campaign whose "
                            "queue names this issue under claude_docs/.driver-state/")
    p_rco.add_argument("--project-root", default=".",
                       help="root to discover claude_docs/.driver-state/ beneath")

    # The `pane_less` half of AC1/AC4. Exposed so the non-herdr launch has an in-repo entry
    # point too — a builder with no caller is the disconnected-module smell both reviews caught.
    p_fb = sub.add_parser("build-fallback", help="argv for the retained pane-less launch")
    p_fb.add_argument("--prompt", required=True)
    p_fb.add_argument("--permission-mode", default="bypassPermissions")
    p_fb.add_argument("--wall-timeout", default=None)

    p_mode = sub.add_parser("select-mode")
    p_mode.add_argument("--terminal-backend", default=None)
    # #927 PR 2 (Step-4 pass-1 finding S2): `--herdr-available` is GONE. It was a
    # CALLER-ASSERTED capability claim — a skill remembering to pass a flag — for something this
    # module can determine for itself, and the whole point of #927 is that a recorded capability
    # goes stale while the real one moves. `herdr_available()` is derived here instead. The flag's
    # removal waited for this PR because `skills/epic-run/SKILL.md` documented it, and deleting a
    # flag while its prose still prescribes it breaks the skill.
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

    # #718 — the context meter's act tier calls this. PROSE only; a bare slash command is refused
    # because it is inert inside a /goal loop (see `validate_inserted_prompt`).
    p_ins = sub.add_parser("insert-prompt",
                           help="insert a PROSE prompt into a pane and submit it (#718)")
    p_ins.add_argument("--pane", required=True,
                       help="the target pane — the caller's own $HERDR_PANE_ID")
    p_ins.add_argument("--text", required=True, help="PROSE; must not begin with '/'")

    args = parser.parse_args(argv)
    try:
        if args.cmd == "handoff":
            return _cmd_handoff(args)
        if args.cmd == "ad-hoc-handoff":
            return _cmd_ad_hoc_handoff(args)
        if args.cmd == "transport":
            return _cmd_transport(args)
        if args.cmd == "sweep":
            return _cmd_sweep(args)
        if args.cmd == "mid-child-handoff":
            return _cmd_mid_child_handoff(args)
        if args.cmd == "retire-predecessor":
            return _cmd_retire_predecessor(args)
        if args.cmd == "next-child":
            return _cmd_next_child(args)
        if args.cmd == "rebuild-receipt":
            return _cmd_rebuild_receipt(args)
        if args.cmd == "record-child-outcome":
            return _cmd_record_child_outcome(args)
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
                herdr_available=herdr_available(),
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
        if args.cmd == "insert-prompt":
            delivered, reason = insert_prompt(pane=args.pane, text=args.text)
            print(json.dumps({"delivered": delivered, "reason": reason}))
            return 0 if delivered else 1
    except LauncherError as exc:
        print(f"launcher_lib: {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    sys.exit(main())
