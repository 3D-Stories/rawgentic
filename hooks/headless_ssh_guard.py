#!/usr/bin/env python3
"""Headless blanket SSH-family matcher (issue #47, Layer B).

In headless mode the bot's job ends at PR creation — no merge, no deploy, no
outbound SSH to remote hosts (chorestory #309 incident: a headless WF2 run SSHed
to a live dev VM and corrupted it). `wal-guard` enforces this by DENYING any
command that *invokes* an SSH-family program (ssh/scp/rsync/sftp), unless the
project opts back in via the workspace `headlessAllowSSH` flag.

This module is ONLY the detection — the security-critical, bypass-prone part —
so it is pure and fully unit-tested. The gating (am-I-headless via
`RAWGENTIC_HEADLESS`, and `headlessAllowSSH` resolution) stays in wal-guard /
wal-lib.sh, which already read the env and the resolved project config.

Detection rules (deliberately conservative — this is a safety net, and the
`headlessAllowSSH:true` escape hatch exists for projects that need remote ops):
  * Split the command on shell control operators (`;`, `&&`, `||`, `|`, `&`, and
    newlines) into segments — a blocked program anywhere in the pipeline counts.
  * Per segment, skip leading `VAR=value` env-assignments, then look at the first
    real token's basename. If it is ssh/scp/rsync/sftp -> blocked.
  * If that first token is a known command *wrapper* (sudo/env/timeout/nohup/...),
    a blocked program appearing anywhere later in the segment counts (the wrapper
    is invoking it) — this catches `sudo -u x ssh h`, `timeout 5 ssh h`, etc.
  * Program detection is by **basename**, so `/usr/bin/ssh` matches but `git push`
    (git's own ssh transport), `gh`, and `ssh` used as a mere argument
    (`grep ssh`, `cat /etc/ssh/sshd_config`) do NOT.
"""
import os
import re
import shlex
import sys

# SSH-family programs blocked in headless mode (the issue names ssh/scp/rsync;
# sftp is the same remote-shell family and included for completeness).
BLOCKED_PROGRAMS = frozenset({"ssh", "scp", "rsync", "sftp"})

# Command wrappers that run another program — a blocked program after one of
# these is still an SSH invocation. We do NOT try to parse each wrapper's option
# arity (a rabbit hole); instead, once the segment starts with a wrapper we scan
# the rest of the segment for a blocked program (fail-toward-blocking).
WRAPPERS = frozenset({
    "sudo", "doas", "env", "command", "builtin", "exec", "nohup",
    "time", "timeout", "stdbuf", "xargs", "ionice", "nice", "setsid",
})

_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_SEGMENT_SPLIT = re.compile(r"&&|\|\||[;&|\n]")


def _basename(token: str) -> str:
    """Basename of a program token (so /usr/bin/ssh -> ssh)."""
    return os.path.basename(token)


def _segment_program(segment: str) -> str | None:
    """Return the blocked SSH-family program invoked by one shell segment, else None."""
    try:
        tokens = shlex.split(segment, comments=False, posix=True)
    except ValueError:
        # Unbalanced quotes etc. — fall back to whitespace split (conservative).
        tokens = segment.split()
    if not tokens:
        return None

    # Skip leading env-assignments (FOO=bar ssh ...).
    idx = 0
    while idx < len(tokens) and _ASSIGNMENT.match(tokens[idx]):
        idx += 1
    if idx >= len(tokens):
        return None

    first = _basename(tokens[idx])
    if first in BLOCKED_PROGRAMS:
        return first

    if first in WRAPPERS:
        # Wrapper invokes another program; a blocked program anywhere after it
        # (skipping further env-assignments) counts.
        for tok in tokens[idx + 1:]:
            if _ASSIGNMENT.match(tok):
                continue
            base = _basename(tok)
            if base in BLOCKED_PROGRAMS:
                return base
    return None


def detect_blocked_program(command: str) -> str | None:
    """Return the SSH-family program the command invokes (ssh/scp/rsync/sftp), or None.

    Pure detection: caller decides whether to block based on headless mode and the
    project's `headlessAllowSSH` flag.
    """
    if not command or not command.strip():
        return None
    for segment in _SEGMENT_SPLIT.split(command):
        program = _segment_program(segment)
        if program:
            return program
    return None


def main(argv: list[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="headless_ssh_guard.py",
        description="Detect SSH-family program invocations (headless safety net).",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser(
        "detect",
        help="Read a command from stdin; print the blocked program (exit 0) "
             "if it invokes ssh/scp/rsync/sftp, else print nothing (exit 1).",
    )
    args = parser.parse_args(argv)

    if args.cmd == "detect":
        command = sys.stdin.read()
        program = detect_blocked_program(command)
        if program:
            print(program)
            return 0
        return 1
    return 2  # unreachable (subparser required)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
