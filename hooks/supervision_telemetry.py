#!/usr/bin/env python3
"""Append-only supervision decision telemetry (#963 AC5).

Every authority decision (permitted/denied + reason) and every claim transition leaves
one line here, so the question that killed the executor — "is this machinery actually
reached?" (D174) — is answered by data rather than by reading code. #871 shipped the
whole authority-and-claims core, and a 2026-08-06 trace found ZERO live callers; this
store is how that stops being invisible.

Location: `<workspace>/claude_docs/supervision-telemetry.jsonl`, beside the supervision
state it describes and a SIBLING of `session_notes/`. That placement is the
`decision_log.py` durability argument: session-start trims `session_notes/*.md`, so a
store the trimmer can reach is not durable across session boundaries. Uncommitted by
design — decisions happen AT merge time, after the PR's content is frozen, so a
committed store would always lag one PR.

FAIL MODE: this module RAISES and lets each caller decide. That is deliberate. The
broker's pre-execution appends are fail-CLOSED (an unmeasurable outward action is the
exact failure this store exists to prevent), while post-execution appends and
`cancel_claims` are fail-loud-and-continue (the merge is already real, and the claims
file — not this one — is the authoritative lifecycle record). A module-level policy
would take that choice away from both.

Why not `atomic_write_lib.atomic_write_text`: it replaces the whole file, so two
concurrent appends would each read the same prefix and the second replace would silently
drop the first line. Appends here are `flock` + `O_APPEND` + one single `write()`, the
`decision_log.append_record` pattern.
"""

import fcntl
import json
import os
import sys
from datetime import datetime, timezone

SCHEMA_VERSION = 1

_REL_PATH = ("claude_docs", "supervision-telemetry.jsonl")

#: `kind` discriminates which fields a reader should expect: `authority` carries
#: decision/reason, `claim` carries transition/claim_id.
KINDS = ("authority", "claim")

#: The claim lifecycle, as the broker drives it. `resumed` is emitted whenever a re-run
#: picks up an existing claim — it is what makes a crash between a claim write and its
#: transition line self-heal at the next touch, since the claims file stays authoritative.
TRANSITIONS = ("minted", "executing", "executed", "cancelled", "reconciled",
               "parked", "reopened", "resumed")


def telemetry_path(workspace_root: str) -> str:
    """The store's path under a workspace root. Never taken from user input."""
    return os.path.join(workspace_root, *_REL_PATH)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _fsync_dir(directory: str) -> None:
    """fsync a directory so a newly created file survives a crash. Best effort: some
    filesystems refuse an O_RDONLY fsync on a directory."""
    try:
        dfd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except OSError:
        pass


def append_event(workspace_root, event: dict) -> str:
    """Append one event as a single line. Concurrency-safe, append-only. RAISES.

    `flock` serialises writers; `O_APPEND` plus one `write()` of a newline-terminated
    line means a record is never interleaved or partially replaced. No path in this
    module truncates or rewrites the file.
    """
    if not workspace_root or not isinstance(workspace_root, str):
        raise ValueError(f"workspace_root must be a non-empty path string: "
                         f"{workspace_root!r}")
    if not isinstance(event, dict):
        raise TypeError(f"event must be a dict, got {type(event).__name__}")
    kind = event.get("kind")
    if kind not in KINDS:
        raise ValueError(f"event kind must be one of {list(KINDS)}, got {kind!r}")

    record = dict(event)
    record["schema_version"] = SCHEMA_VERSION
    record.setdefault("ts", _now_iso())
    # Attribution across session boundaries: a line has to say which run wrote it, and
    # the env var is per-process (the registry file is shared and names the wrong one).
    record.setdefault("session", os.environ.get("CLAUDE_CODE_SESSION_ID") or None)

    path = telemetry_path(workspace_root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    data = (json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
    fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        # If a previous writer died mid-record the file ends without a newline.
        # Appending straight onto it would JOIN this record to the broken one and
        # destroy both — the new line would report success and be unreadable. Close the
        # damaged line first; the reader then skips exactly one record.
        if os.fstat(fd).st_size:
            with open(path, "rb") as probe:
                probe.seek(-1, os.SEEK_END)
                if probe.read(1) != b"\n":
                    os.write(fd, b"\n")
        # os.write may write fewer bytes than asked (ENOSPC boundaries). Ignoring the
        # count would persist a truncated line while telling the caller the decision was
        # recorded — the precise failure this store exists to prevent.
        written = 0
        while written < len(data):
            n = os.write(fd, data[written:])
            if n <= 0:
                raise OSError(f"short write to {path}: {written}/{len(data)} bytes")
            written += n
        os.fsync(fd)
        _fsync_dir(os.path.dirname(path))
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    return path


def emit(workspace_root, event: dict, *, strict: bool) -> bool:
    """Append `event`; return whether it landed. The two fail modes, in one place.

    `strict=True` re-raises — the caller is about to take an outward action and must not
    take it unrecorded. `strict=False` warns on stderr and returns False, for the paths
    where the side effect already happened or where telemetry must never wedge a
    lifecycle operation (`cancel_claims` in the `back` skill).
    """
    try:
        append_event(workspace_root, event)
        return True
    except (OSError, ValueError, TypeError) as exc:
        if strict:
            raise
        try:
            print(f"supervision-telemetry: event not recorded ({exc})", file=sys.stderr)
        except Exception:  # pylint: disable=broad-except
            pass
        return False


def read_events(workspace_root: str, *, last=None) -> list:
    """Read events oldest-first. A corrupt line is skipped, never fatal — the reader is
    tolerant precisely because the writer is not."""
    path = telemetry_path(workspace_root)
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            try:
                rec = json.loads(raw)
            except ValueError:
                continue
            if isinstance(rec, dict):
                out.append(rec)
    if last is not None:
        return out[-last:] if last > 0 else []
    return out
