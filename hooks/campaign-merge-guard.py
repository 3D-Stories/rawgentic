#!/usr/bin/env python3
"""Campaign Merge Guard -- PreToolUse hook that routes campaign merges to the broker.

Hook: PreToolUse (matcher: Bash)
Protocol: JSON stdout with permissionDecision: deny
Policy: SPLIT at the classification boundary (#976 AC3, decision D186)
  - before a command is classified  -> fail-OPEN, with a stderr diagnostic
  - after it is a raw `gh pr merge`  -> fail-CLOSED

Why split, when `wal-guard` fails closed and `security-guard.py` fails open: both are
right, about different paths. This hook runs on EVERY Bash call, so a blanket fail-closed
bug would deny `ls` and `pytest` in every project on the host. But once a command IS a raw
`gh pr merge`, the blast radius of refusing it is exactly that one command -- and the
sanctioned path, `broker-merge`, is not a `gh pr merge` command line, so it stays open.

What this guard is NOT (D187): unbypassable. PreToolUse fires per Claude Code tool call,
not per OS process, so an indirect spawn is invisible to it. It stops the accidental raw
merge, not the deliberate one.

See docs/supervision.md and claude_docs/976-design.md.
"""
import json
import os
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import campaign_merge_guard_lib as guard  # noqa: E402


def _diagnostic(message):
    """The fail-open path must be visible (review finding F4) -- but never blocking.

    stderr, not stdout: stdout is the permission-decision channel, and anything
    non-JSON there would corrupt the protocol.
    """
    try:
        print("campaign-merge-guard: allowing (%s)" % message, file=sys.stderr)
    except (OSError, ValueError):
        pass


def _deny(reason):
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason,
    }}))
    sys.exit(0)


def main():
    deadline = time.monotonic() + guard.INTERNAL_DEADLINE_S

    # ---- before classification: every failure ALLOWS, and says why ----------
    try:
        input_data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, IOError, ValueError):
        _diagnostic("stdin was not valid JSON")
        sys.exit(0)
    if not isinstance(input_data, dict):
        _diagnostic("stdin JSON was not an object")
        sys.exit(0)

    tool_input = input_data.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if not command:
        sys.exit(0)                      # the overwhelmingly common case: not our business

    target = guard.parse_merge_command(command)
    if target is None:
        sys.exit(0)                      # the hot path -- not a raw gh pr merge

    # ---- classified as a raw `gh pr merge`: from here, failures REFUSE ------
    cwd = input_data.get("cwd") or os.getcwd()
    project_root = guard.find_project_root(cwd)
    if project_root is None:
        # No .rawgentic.json above cwd => no campaign can exist. Absence, not failure.
        sys.exit(0)

    active, unevaluable = guard.read_campaigns(project_root, deadline=deadline)
    if time.monotonic() > deadline and not unevaluable:
        unevaluable = ["<internal deadline reached before the state was read>"]

    decision = guard.decide(target, active, unevaluable,
                            guard.configured_repo(project_root))
    if decision["action"] == "deny":
        _deny(guard.format_deny(decision))
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # pylint: disable=broad-except
        # Pre-classification fail-open, as a backstop. A crash here must never wedge every
        # Bash call in every session -- but it is no longer silent.
        _diagnostic("unexpected error: %r" % (exc,))
        sys.exit(0)
