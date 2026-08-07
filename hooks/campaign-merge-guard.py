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

The boundary is tracked in `_CLASSIFIED`, not merely documented: the outer handler reads
it, so an unexpected exception AFTER classification denies rather than silently allowing
the very command this hook exists to stop (Step 8a finding F2).

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

#: Flipped the instant the command is known to be a raw `gh pr merge`. Everything after
#: that point fails CLOSED, including an unexpected crash.
_CLASSIFIED = False


def _diagnostic(message):
    """The fail-open path must be visible (Step 4 finding F4) -- but never blocking.

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
    global _CLASSIFIED  # pylint: disable=global-statement
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
        _diagnostic("tool_input.command was missing or empty")
        sys.exit(0)

    target = guard.parse_merge_command(command)
    if target is None:
        sys.exit(0)                      # the hot path -- not a raw gh pr merge

    # ---- classified as a raw `gh pr merge`: from here, failures REFUSE ------
    _CLASSIFIED = True

    hook_cwd = input_data.get("cwd") or os.getcwd()
    # Where the merge segment would ACTUALLY run: `cd /elsewhere && gh pr merge 887`
    # executes in /elsewhere, so resolving state from the hook's cwd read the wrong
    # project (Step 8a finding F3).
    project_root = guard.find_project_root(guard.effective_cwd(target, hook_cwd))
    if project_root is None:
        # No .rawgentic.json above the effective directory => no campaign can exist.
        # Absence, not failure -- but an unresolvable `cd` is failure, and `decide`
        # refuses on it.
        if not target.get("cd_unresolvable"):
            sys.exit(0)

    active, unevaluable = guard.read_campaigns(project_root, deadline=deadline)
    if time.monotonic() > deadline and not unevaluable:
        unevaluable = ["<internal deadline reached before the state was read>"]

    decision = guard.decide(target, active, unevaluable,
                            guard.configured_repo(project_root))
    if decision["action"] == "deny":
        _deny(guard.format_deny(decision, project_root))
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # pylint: disable=broad-except
        if _CLASSIFIED:
            # Post-classification: refuse. Allowing here would silently wave through the
            # exact command this hook exists to stop.
            _deny(
                "BLOCKED: this command is a raw `gh pr merge`, and the campaign merge "
                "guard hit an unexpected error before it could decide whether the "
                "target belongs to an active campaign.\n\n"
                "  Error: %r\n\n"
                "This guard fails closed once a command is identified as a raw merge. "
                "Merge through the supervised broker instead:\n\n"
                "  python3 hooks/launcher_lib.py broker-merge --pr <pr> --issue <issue> "
                "\\\n    --campaign <campaign> --project-root <project root>\n" % (exc,))
        # Pre-classification fail-open. A crash here must never wedge every Bash call in
        # every session -- but it is no longer silent.
        _diagnostic("unexpected error: %r" % (exc,))
        sys.exit(0)
