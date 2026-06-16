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
  * Recurse into command substitutions `$(...)`, backticks, subshells `(...)`,
    and process substitutions `<(...)`/`>(...)` — ssh hidden there still counts.
  * Recurse into shell interpreter command strings: `bash -c '...'`, `sh -c`,
    `eval '...'`, and `find ... -exec <prog>`.
  * Split the remaining command on shell control operators (`;`, `&&`, `||`,
    `|`, `&`, newlines) into segments. Per segment, skip leading `VAR=value`
    env-assignments, then look at the first real token's basename. ssh/scp/
    rsync/sftp -> blocked.
  * If that first token is a known command *wrapper* (sudo/env/timeout/nohup/...)
    scan the rest of the segment for a blocked program (and recurse into any
    interpreter/find found there). This is intentionally broad — it does NOT
    parse each wrapper's option arity — so `timeout -s KILL 5 ssh` and
    `sudo -u user ssh` are caught with no bypass; the cost is over-blocking the
    rare case where ssh/scp/rsync is a mere ARGUMENT under a wrapper
    (`timeout 5 grep rsync src/`). Fail-toward-blocking is the right default for
    a safety net; the escape hatch covers projects that need otherwise.
  * Program detection is by **basename**, so `/usr/bin/ssh` matches but `git push`
    (git's own ssh transport), `gh`, and `ssh` as a mere argument
    (`grep ssh`, `cat /etc/ssh/sshd_config`) do NOT.

Residual gaps (documented, accepted): ssh embedded in a NON-shell interpreter
string (`python3 -c "...os.system('ssh')"`) is not detected — arbitrary-language
eval is out of scope; WF2's own deploy path is closed by the Layer-A Step-14 skip.
"""
import os
import re
import shlex
import sys

# SSH-family programs blocked in headless mode (the issue names ssh/scp/rsync;
# sftp is the same remote-shell family and included for completeness).
BLOCKED_PROGRAMS = frozenset({"ssh", "scp", "rsync", "sftp"})

# Command wrappers that run another program — a blocked program after one of
# these is still an SSH invocation.
WRAPPERS = frozenset({
    "sudo", "doas", "env", "command", "builtin", "exec", "nohup",
    "time", "timeout", "stdbuf", "xargs", "ionice", "nice", "setsid",
})

# Shell interpreters whose `-c` string (or, for eval, whose args) is itself a
# shell command we must look inside.
INTERPRETERS = frozenset({"bash", "sh", "zsh", "dash", "ash", "ksh", "eval"})

_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_SEGMENT_SPLIT = re.compile(r"&&|\|\||[;&|\n]")
# Command substitution `$(...)`, backticks, and (process-)subshells `(...)`,
# `<(...)`, `>(...)`. Non-nested capture is sufficient for the common forms; the
# recursion depth guard bounds pathological input.
_SUBST = re.compile(r"\$\(([^()]*)\)|`([^`]*)`|(?<![\w$])[<>]?\(([^()]*)\)")

_MAX_DEPTH = 6


def _basename(token: str) -> str:
    return os.path.basename(token)


def _interpreter_inner(tokens: list[str], depth: int) -> str | None:
    """Recurse into a shell interpreter's command string (`bash -c '...'`, `eval ...`)."""
    prog = _basename(tokens[0])
    if prog == "eval":
        return detect_blocked_program(" ".join(tokens[1:]), depth + 1)
    for j in range(1, len(tokens)):
        tok = tokens[j]
        # `-c`, or a combined short-flag cluster carrying c (`-lc`, `-ec`, `-cx`):
        # the following token is the command string. Long flags (`--x`) excluded.
        if (tok.startswith("-") and not tok.startswith("--")
                and "c" in tok and j + 1 < len(tokens)):
            return detect_blocked_program(tokens[j + 1], depth + 1)
    return None


def _find_exec_inner(tokens: list[str], depth: int) -> str | None:
    """Recurse into the command run by `find ... -exec <prog> ... ;`."""
    for j in range(1, len(tokens)):
        if tokens[j] in ("-exec", "-execdir") and j + 1 < len(tokens):
            rest = []
            for tok in tokens[j + 1:]:
                if tok in (";", "+", "\\;"):
                    break
                rest.append(tok)
            return detect_blocked_program(" ".join(rest), depth + 1)
    return None


def _scan_wrapped(rest: list[str], depth: int) -> str | None:
    """Broad scan of a wrapped command's tokens for a blocked program.

    Looks for a blocked basename anywhere (skipping env-assignments), and
    recurses into any interpreter / find encountered. Intentionally does not
    model option arity (see module docstring) — broad coverage, no bypass.
    """
    for k, tok in enumerate(rest):
        if _ASSIGNMENT.match(tok):
            continue
        base = _basename(tok)
        if base in BLOCKED_PROGRAMS:
            return base
        if base in INTERPRETERS:
            hit = _interpreter_inner(rest[k:], depth)
            if hit:
                return hit
        if base == "find":
            hit = _find_exec_inner(rest[k:], depth)
            if hit:
                return hit
    return None


def _segment_program(segment: str, depth: int) -> str | None:
    """Return the blocked SSH-family program invoked by one shell segment, else None."""
    # Blank command-substitutions/subshells (no surrounding space) so a `$(...)`
    # value that contains a space cannot fragment a leading env-assignment — e.g.
    # `TS=$(date +%s) ssh host` must not tokenize to `TS=$(date`, `+%s)`, `ssh`,
    # which would hide the real `ssh`. Their contents are still inspected by the
    # _SUBST recursion in detect_blocked_program, so blanking here loses nothing.
    segment = _SUBST.sub("__SUBST__", segment)
    try:
        tokens = shlex.split(segment, comments=False, posix=True)
    except ValueError:
        # Unbalanced quotes etc. — fall back to whitespace split (conservative).
        tokens = segment.split()
    if not tokens:
        return None

    idx = 0
    while idx < len(tokens) and _ASSIGNMENT.match(tokens[idx]):
        idx += 1
    if idx >= len(tokens):
        return None

    first = _basename(tokens[idx])
    if first in BLOCKED_PROGRAMS:
        return first
    if first in INTERPRETERS:
        return _interpreter_inner(tokens[idx:], depth)
    if first == "find":
        return _find_exec_inner(tokens[idx:], depth)
    if first in WRAPPERS:
        return _scan_wrapped(tokens[idx + 1:], depth)
    return None


def detect_blocked_program(command: str, depth: int = 0) -> str | None:
    """Return the SSH-family program the command invokes (ssh/scp/rsync/sftp), or None.

    Pure detection: caller decides whether to block based on headless mode and the
    project's `headlessAllowSSH` flag.
    """
    if not command or not command.strip() or depth > _MAX_DEPTH:
        return None

    # 1. Recurse into command substitutions / subshells / process substitutions.
    for match in _SUBST.finditer(command):
        inner = match.group(1) or match.group(2) or match.group(3)
        if inner:
            hit = detect_blocked_program(inner, depth + 1)
            if hit:
                return hit

    # 2. Segment scan on the outer command.
    for segment in _SEGMENT_SPLIT.split(command):
        program = _segment_program(segment, depth)
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
