"""Task class (`disposable | internal | production`) — resolve it, snapshot it, read it back (#761).

The field gives proportionality a seat at the gates: eventually reviews scale their DEMANDS to
the class. This module ships the field only — resolved, snapshotted, and rendered into prompts —
with nothing yet instructed to reduce anything. The demand-scaling half and the WF2-lite lane
ship with #923.

Three jobs, and the boundaries between them are load-bearing:

- `resolve_class` reads an issue BODY (untrusted text) and a config default, and returns the
  class, where it came from, and a diagnostic when it had to fail closed.
- `write_snapshot` / `read_snapshot` persist that decision ONCE per issue so no later gate can
  observe it change mid-run.
- `format_surface_line` is the one place the operator-visible line is shaped.

**The diagnostic never reaches a prompt.** It is derived from issue-body text, and the class line
in a reviewer prompt sits OUTSIDE the nonce fence — routing body text there would put
attacker-controlled text unfenced in front of a reviewer, which is the exact threat the
prompt-injection guards exist for. Its only destinations are the snapshot, stderr and session
notes. What reaches a prompt is one of three validated literals, never text.

Config reading follows the narrow in-repo exception for a hook that needs its OWN single key
(`projects/rawgentic/CLAUDE.md` §1): read only that key, cap the read, fail open, validate the
value. The exact precedent call site is `hooks/security_guard_lib.py:223`
(`config_path = os.path.join(project_root, ".rawgentic.json")` inside its own config loader),
not the prose rule.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys
import tempfile

TASK_CLASSES = ("disposable", "internal", "production")
DEFAULT_CLASS = "production"
PROVENANCES = ("issue_body", "config", "default")
CONFIG_KEY = "defaultTaskClass"

# Reading our own single config key: cap the read so a pathological file cannot stall a hook.
_CONFIG_READ_CAP_BYTES = 512 * 1024

# The canonical line. `**Task class:** <value>` — nothing else on the line.
#
# Candidates are collected by PREFIX and only then validated. A whole-line match alone meant a
# line with trailing junk matched NOTHING, so it counted as "zero candidates" and silently took
# the config default — contradicting the promise that trailing text is malformed rather than
# accepted, and letting one valid line plus one malformed duplicate resolve to the valid class.
_PREFIX = "**task class:**"
# IGNORECASE on BOTH halves. The prefix scan was case-insensitive while this was not, so
# `**TASK CLASS:** disposable` was collected and then rejected as malformed — the spec
# contradicted itself (pass-6 finding, adopted under D204).
_FULL_RE = re.compile(r"^[ ]{0,3}\*\*Task class:\*\*[ \t]+(\S+)[ \t]*$", re.IGNORECASE)

_MAX_FENCE_INDENT = 3          # CommonMark: 4+ spaces is an indented code block, not a fence
_INDENTED_CODE_INDENT = 4


class TaskClassError(Exception):
    """A fail-loud task-class failure: the run must not proceed on an unread class."""


# ======================================================================================
# Extraction boundary
# ======================================================================================

def _fence_delim(stripped: str):
    """`(char, run_length, info_string)` when the line is a fence delimiter, else None."""
    for ch in ("`", "~"):
        if stripped.startswith(ch * 3):
            run = len(stripped) - len(stripped.lstrip(ch))
            return ch, run, stripped[run:].strip()
    return None


def _classify_lines(body: str):
    """Split the body into `(eligible, excluded)` line lists, both 1-indexed.

    A precise line-state walk rather than prose, because "not inside a code fence" is not
    implementable as prose: longer fences, tilde fences, info strings, indentation and an
    unclosed fence all have to be decided the same way twice. Excluded lines are RETAINED (with
    a reason) so a candidate-looking line that got excluded can still be reported — silent
    exclusion is the same defect as a silent fallback.
    """
    eligible: list[tuple[int, str]] = []
    excluded: list[tuple[int, str, str]] = []
    open_fence = None
    for n, raw in enumerate(body.splitlines(), start=1):
        expanded = raw.expandtabs(4)
        stripped = expanded.strip()
        indent = len(expanded) - len(expanded.lstrip(" "))
        delim = _fence_delim(stripped) if indent <= _MAX_FENCE_INDENT else None

        if open_fence is None:
            if delim is not None:
                open_fence = (delim[0], delim[1])
                excluded.append((n, raw, "fence delimiter"))
                continue
        else:
            # Inside a fence every line is excluded; it may also CLOSE the fence. A closer must
            # use the same character, a run at least as long as the opener, and carry no info
            # string.
            if delim is not None and delim[0] == open_fence[0] \
                    and delim[1] >= open_fence[1] and not delim[2]:
                open_fence = None
            excluded.append((n, raw, "fenced"))
            continue

        if indent >= _INDENTED_CODE_INDENT:
            excluded.append((n, raw, "indented (4+ spaces reads as code)"))
            continue
        if stripped.startswith(">"):
            excluded.append((n, raw, "block quote"))
            continue
        eligible.append((n, raw))
    # An unclosed fence excludes everything to the end of the body, which the walk above already
    # did — fail-closed by construction: an unterminated fence can only exclude more, never less.
    return eligible, excluded


def _looks_like_candidate(raw: str) -> bool:
    return raw.expandtabs(4).strip().lower().startswith(_PREFIX)


# ======================================================================================
# Resolution
# ======================================================================================

def _config_outcome(config_default):
    """`(class, provenance, diagnostic)` for the no-valid-body-line path."""
    if config_default is None:
        return DEFAULT_CLASS, "default", None
    value = str(config_default).strip().casefold()
    if value in TASK_CLASSES:
        return value, "config", None
    return (DEFAULT_CLASS, "default",
            f"invalid {CONFIG_KEY} {config_default!r} in .rawgentic.json — "
            f"expected one of {', '.join(TASK_CLASSES)}; using {DEFAULT_CLASS}")


def resolve_class(body: str, config_default: str | None = None):
    """Resolve the task class from an issue body.

    Returns `(task_class, provenance, diagnostic)`. `diagnostic` is None on the clean paths and a
    single human-readable string when something had to fail closed or was silently excluded.

    Precedence: a single valid body line > `defaultTaskClass` > `production`. On a MALFORMED or
    duplicated body line the config default is bypassed entirely — a body that tried to set the
    class and got it wrong must not silently inherit a permissive project default.
    """
    if not isinstance(body, str):
        body = ""
    eligible, excluded = _classify_lines(body)

    candidates = [(n, raw) for n, raw in eligible if _looks_like_candidate(raw)]
    if not candidates:
        cls, prov, diag = _config_outcome(config_default)
        hidden = [(n, why) for n, raw, why in excluded if _looks_like_candidate(raw)]
        if hidden and diag is None:
            where = "; ".join(f"line {n} ({why})" for n, why in hidden)
            diag = (f"no task-class line found, but a candidate-looking line was EXCLUDED: {where}. "
                    f"The line must sit at indent 0-3, outside code fences and block quotes. "
                    f"Using {cls}.")
        return cls, prov, diag

    problems: list[str] = []
    valid: list[tuple[int, str]] = []
    for n, raw in candidates:
        m = _FULL_RE.match(raw.expandtabs(4).rstrip("\n"))
        if m is None:
            problems.append(f"line {n}: malformed — the line must be exactly "
                            f"`**Task class:** <value>` with nothing after the value")
            continue
        value = m.group(1).strip().casefold()
        if value not in TASK_CLASSES:
            problems.append(f"line {n}: unrecognised value {m.group(1)!r} — "
                            f"expected one of {', '.join(TASK_CLASSES)}")
            continue
        valid.append((n, value))

    if len(candidates) == 1 and len(valid) == 1 and not problems:
        return valid[0][1], "issue_body", None

    if len(candidates) > 1:
        lines = ", ".join(str(n) for n, _ in candidates)
        detail = f"{len(candidates)} task-class lines found (lines {lines}); expected exactly one"
        if problems:
            detail += ". " + " ".join(problems)
        return DEFAULT_CLASS, "default", (
            f"{detail}. Failing closed to {DEFAULT_CLASS}; the project default is NOT consulted.")

    return DEFAULT_CLASS, "default", (
        f"{problems[0]}. Failing closed to {DEFAULT_CLASS}; "
        f"the project default is NOT consulted.")


# ======================================================================================
# Snapshot
# ======================================================================================

def _schema_error(record, issue: int) -> str | None:
    if not isinstance(record, dict):
        return "snapshot is not a JSON object"
    cls = record.get("task_class")
    if not isinstance(cls, str) or cls not in TASK_CLASSES:
        return f"task_class {cls!r} is not one of {', '.join(TASK_CLASSES)}"
    prov = record.get("provenance")
    if not isinstance(prov, str) or prov not in PROVENANCES:
        return f"provenance {prov!r} is not one of {', '.join(PROVENANCES)}"
    got = record.get("issue")
    if not isinstance(got, int) or isinstance(got, bool):
        return f"issue {got!r} is not an integer"
    if got != issue:
        return (f"snapshot belongs to issue {got}, not {issue} — a snapshot for a different "
                f"issue must never be adopted")
    at = record.get("resolved_at")
    if not isinstance(at, str) or not at.strip():
        return "resolved_at is missing or empty"
    diag = record.get("diagnostic")
    if diag is not None and (not isinstance(diag, str) or not diag.strip()):
        return "diagnostic is present but not a non-empty string"
    return None


def write_snapshot(path: str, payload: dict) -> str:
    """Persist the class WRITE-ONCE. Returns `"created"` or `"adopted"`.

    `os.link` is the atomicity primitive: it raises `FileExistsError` when the target exists, so
    the loser of a race ADOPTS the winner's snapshot instead of overwriting it. For a given issue
    the class is therefore decided exactly once and is immutable for every run and every gate —
    no run can observe it change. That needs no lock, so it adds no second lock order against
    `plan_lib.file_lock` and no deadlock surface.

    Keyed by ISSUE, deliberately not by run or session: WF2 explicitly spans sessions, so a
    session-keyed snapshot would vanish on resume and the resumed run would re-read a
    possibly-rotted body. The escape is explicit and logged, never automatic — delete the file to
    force a re-resolve, and do it only when no run on that issue is live, since deleting under a
    run that has already adopted lets two gates observe different classes.

    The containing DIRECTORY is fsynced after the link. `os.link` gives atomic VISIBILITY, not
    durable persistence of the new directory entry, so without this a host crash could lose a
    snapshot a completed run had already used and the next run would re-resolve a mutated body.
    """
    directory = os.path.dirname(path) or "."
    pathlib.Path(directory).mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".task_class-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        try:
            os.link(tmp, path)
            outcome = "created"
        except FileExistsError:
            outcome = "adopted"
        except OSError as exc:
            # Named because the hard-link path is only PROVEN on this host's filesystem; a
            # filesystem without hard-link support fails here rather than silently degrading.
            raise TaskClassError(
                f"could not create the task-class snapshot at {path}: "
                f"{exc.strerror} (errno {exc.errno}). The filesystem may not support hard links."
            ) from exc
        dfd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
        return outcome
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def read_snapshot(path: str, issue: int) -> dict:
    """Read and VALIDATE a snapshot. Fail-loud on anything unusable.

    Parseability is not enough: a parseable snapshot carrying a valid class for the wrong issue
    would otherwise be adopted and injected. Never silently re-resolve on a bad snapshot — that
    would defeat write-once. The remedy is named in the message so an operator can act on it.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            record = json.load(fh)
    except FileNotFoundError as exc:
        raise TaskClassError(f"no task-class snapshot at {path}") from exc
    except (ValueError, OSError) as exc:
        raise TaskClassError(
            f"task-class snapshot at {path} is unreadable ({exc}). Delete the file to force a "
            f"re-resolve; it is never re-resolved silently."
        ) from exc
    problem = _schema_error(record, issue)
    if problem is not None:
        raise TaskClassError(
            f"task-class snapshot at {path} is invalid: {problem}. Delete the file to force a "
            f"re-resolve; it is never re-resolved silently."
        )
    return record


# ======================================================================================
# Operator surface
# ======================================================================================

def format_surface_line(task_class: str, provenance: str, snapshot: str,
                        diagnostic: str | None) -> str:
    """The one operator-visible line, emitted once per resolution — including on ADOPT.

    Re-emitting on adopt matters: without it a later run silently inherits an earlier run's
    fail-closed fallback and nobody ever sees why scrutiny is at `production`.
    """
    line = f"task-class: {task_class} (provenance={provenance}, snapshot={snapshot})"
    if diagnostic:
        line += f" DIAGNOSTIC: {diagnostic}"
    return line


def read_config_default(project_root: str) -> str | None:
    """The project's `defaultTaskClass`, or None. Fail-OPEN: an unreadable config is not fatal.

    Only this one key is read. Value validation lives in `_config_outcome`, so an invalid value
    surfaces as a diagnostic on the normal path rather than as an exception here.
    """
    path = os.path.join(project_root, ".rawgentic.json")
    try:
        if os.path.getsize(path) > _CONFIG_READ_CAP_BYTES:
            print(f"task_class_lib: {path} exceeds the read cap; ignoring {CONFIG_KEY}",
                  file=sys.stderr)
            return None
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    value = data.get(CONFIG_KEY)
    return value if isinstance(value, str) else None


# ======================================================================================
# CLI
# ======================================================================================

def _iso_now() -> str:
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _cmd_resolve(args) -> int:
    body = pathlib.Path(args.body_file).read_text(encoding="utf-8") if args.body_file else ""
    cls, prov, diag = resolve_class(body, read_config_default(args.project_root))
    payload = {"task_class": cls, "provenance": prov, "issue": args.issue,
               "resolved_at": _iso_now()}
    if diag:
        payload["diagnostic"] = diag
    try:
        outcome = write_snapshot(args.out, payload)
        if outcome == "adopted":
            existing = read_snapshot(args.out, issue=args.issue)
            cls, prov, diag = existing["task_class"], existing["provenance"], \
                existing.get("diagnostic")
    except TaskClassError as exc:
        print(f"task-class: FAILED — {exc}", file=sys.stderr)
        return 1
    print(format_surface_line(cls, prov, outcome, diag), file=sys.stderr)
    print(json.dumps({"task_class": cls, "provenance": prov, "snapshot": outcome,
                      "diagnostic": diag}))
    return 0


def _cmd_read(args) -> int:
    """The class for a review/consult invocation.

    `--issue` is REQUIRED whenever an issue is in scope. Without it this returns the project
    default, which is correct ONLY for a standalone artifact review with no issue — an
    accidental omission on an issue-scoped call would otherwise silently inject the project
    default instead of the class the run snapshotted, with no failure and no diagnostic.
    """
    if args.issue is None:
        cls, prov, diag = _config_outcome(read_config_default(args.project_root))
        print(json.dumps({"task_class": cls, "provenance": prov, "diagnostic": diag,
                          "issue_scoped": False}))
        return 0
    path = args.snapshot or os.path.join(
        args.project_root, "claude_docs", ".wf2-state", str(args.issue), "task_class.json")
    try:
        rec = read_snapshot(path, issue=args.issue)
    except TaskClassError as exc:
        print(f"task-class: FAILED — {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"task_class": rec["task_class"], "provenance": rec["provenance"],
                      "diagnostic": rec.get("diagnostic"), "issue_scoped": True}))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="task_class_lib",
                                     description="resolve, snapshot and read the task class")
    subs = parser.add_subparsers(dest="cmd", required=True)

    r = subs.add_parser("resolve", help="resolve from an issue body and snapshot write-once")
    r.add_argument("--issue", type=int, required=True)
    r.add_argument("--body-file", required=True)
    r.add_argument("--out", required=True)
    r.add_argument("--project-root", default=".")
    r.set_defaults(func=_cmd_resolve)

    d = subs.add_parser("read", help="the class for a review/consult invocation")
    d.add_argument("--issue", type=int, default=None,
                   help="REQUIRED when an issue is in scope; omit only for a standalone review")
    d.add_argument("--snapshot", default=None)
    d.add_argument("--project-root", default=".")
    d.set_defaults(func=_cmd_read)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
