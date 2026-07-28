#!/usr/bin/env python3
"""Context-pressure trigger (#687) — a hook that notices the window filling up.

Design: docs/planning/2026-07-28-687-context-pressure-trigger.md

A session's "my context is getting full, I should hand over" behaviour used to be
pure model judgment, so it fired only sometimes. This reads the session's own
transcript, expresses usage as a FRACTION of the context window, and injects a
nag at two tiers — once each per session per effective window. A hook cannot
forget; that is the whole idea.

Registered on BOTH `UserPromptSubmit` and `PostToolUse`:
  * the 5-TURN arm needs `UserPromptSubmit` (one event per user prompt);
  * the 5-MINUTE arm needs `PostToolUse`, because a long autonomous run gets ONE
    user prompt and then works for hours — a UserPromptSubmit-only meter would be
    silently dead in exactly the runs that need it most.
The two events need DIFFERENT output shapes (both verified live 2026-07-28):
`UserPromptSubmit` takes top-level `additionalContext` (as `hooks/wal-context:43`
does); `PostToolUse` takes it nested under `hookSpecificOutput`.

FAIL MODE: **fail-OPEN.** An absent/unreadable/malformed transcript, an
unwritable state dir, a bad config value, or any unexpected exception means
"emit nothing, exit 0" — never a blocked turn. This is a convenience nag, not a
security boundary (CLAUDE.md §3 decision guide). Fail-open is NOT the same as
invisible, though: the three self-disabling outcomes each emit a once-per-session
stderr diagnostic, so a platform path/format change is findable instead of
manifesting as a meter that quietly never fires.

Pure core + thin CLI (`registry_prune.py` is the exemplar): every function below
`main` is pure or takes its I/O injected.
"""
from __future__ import annotations

import argparse
import glob as _glob
import json
import os
import re
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# The three fields that make up the in-context total. output_tokens is
# deliberately EXCLUDED: #654 verified the identity 2 + 1257 + 652960 == the
# statusline's own `total_input_tokens`, and an assistant's output re-enters as
# cache_creation on the next turn, so counting it would double-count.
IN_CONTEXT_FIELDS = ("input_tokens", "cache_creation_input_tokens",
                     "cache_read_input_tokens")

DEFAULT_WINDOW = 200_000          # conservative floor — see resolve_window
KNOWN_WINDOWS = (200_000, 1_000_000)
DEFAULT_CHECK_IN_PCT = 60         # AC6 — start LOOKING for a break
DEFAULT_ACT_PCT = 70              # AC3 — act now (measured: 1M compacts ≈99.8%)
MIN_TIER_GAP_PCT = 10
DEFAULT_EVERY_TURNS = 5
DEFAULT_EVERY_SECONDS = 300

_BLOCK = 65536
DEFAULT_MAX_BYTES = 4 * 1024 * 1024   # the largest transcript here is 83 MB

_SESSION_RE = re.compile(r"[A-Za-z0-9-]{8,64}\Z")
STATE_DIRNAME = "context-meter"
SWEEP_AGE_S = 7 * 24 * 3600


# ---------------------------------------------------------------------------
# reading the transcript
# ---------------------------------------------------------------------------

def usage_total(usage) -> int:
    """In-context total for one `message.usage` row. Never raises."""
    if not isinstance(usage, dict):
        return 0
    total = 0
    for field in IN_CONTEXT_FIELDS:
        value = usage.get(field)
        if isinstance(value, bool) or not isinstance(value, int):
            continue
        if value > 0:
            total += value
    return total


def _total_from_line(raw) -> int:
    """Parse one transcript line; 0 when it carries no positive usage row."""
    if isinstance(raw, bytes):
        try:
            raw = raw.decode("utf-8", "replace")
        except Exception:                                  # pragma: no cover
            return 0
    raw = raw.strip()
    if not raw or '"usage"' not in raw:
        return 0
    try:
        obj = json.loads(raw)
    except (ValueError, TypeError):
        return 0
    message = obj.get("message") if isinstance(obj, dict) else None
    if not isinstance(message, dict):
        return 0
    return usage_total(message.get("usage"))


def read_used_tokens(path, *, max_bytes=DEFAULT_MAX_BYTES, opener=open):
    """In-context tokens from the LAST NON-ZERO usage row, or None.

    Two things this must get right, both learned the hard way:

    * **Last NON-ZERO, not last.** An interrupted assistant turn writes a usage
      row whose four fields are all zero (confirmed: line 1872 of a real
      transcript whose max total is 809,778). A last-row reader reports 0% on a
      nearly-full session and would never fire.
    * **Last non-zero, not MAXIMUM.** After a compaction the true figure is
      smaller than earlier readings; a max-based reader would report the
      pre-compaction number forever.

    Reads BACKWARD in bounded blocks — the largest transcript on this host is
    82,948,830 bytes, and a forward scan would re-read all of it every five
    minutes in every active session. Returns None once `max_bytes` is exhausted
    rather than reading without bound.
    """
    try:
        with opener(path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            pos = handle.tell()
            tail = b""
            read_total = 0
            while pos > 0 and read_total < max_bytes:
                block = min(_BLOCK, pos, max_bytes - read_total)
                pos -= block
                handle.seek(pos)
                chunk = handle.read(block)
                if not chunk:
                    break
                read_total += len(chunk)
                buf = chunk + tail
                parts = buf.split(b"\n")
                # With bytes still ahead of us, parts[0] may be a partial line;
                # carry it into the next (earlier) block.
                tail = parts[0] if pos > 0 else b""
                complete = parts[1:] if pos > 0 else parts
                for line in reversed(complete):
                    total = _total_from_line(line)
                    if total > 0:
                        return total
    except (OSError, ValueError):
        return None
    return None


def resolve_transcript(session_id, *, payload_path=None,
                       projects_dir=None, glob_fn=_glob.glob):
    """Locate the session transcript, or None.

    `payload_path` (the hook payload's `transcript_path`, present on both events
    — verified live) is the primary route, but only after hardening: the
    basename must be exactly `<validated session id>.jsonl`, the real path must
    be contained under the projects root, and it must be a regular file, not a
    symlink. Anything else falls through to the glob, which is used ONLY when it
    returns exactly one hit — session ids are UUIDs, so two hits is ambiguous and
    must never be resolved by picking one.
    """
    if not isinstance(session_id, str) or not _SESSION_RE.match(session_id):
        return None
    if projects_dir is None:
        projects_dir = os.path.expanduser("~/.claude/projects")

    if isinstance(payload_path, str) and payload_path:
        if _payload_path_ok(session_id, payload_path, projects_dir):
            return payload_path

    try:
        hits = glob_fn(os.path.join(projects_dir, "*", f"{session_id}.jsonl"))
    except Exception:                                      # pragma: no cover
        return None
    # The glob hit gets the SAME hardening as the payload path. Being rooted at
    # projects_dir makes containment likely, not certain — a symlink planted
    # inside the tree would still point out of it.
    if isinstance(hits, list) and len(hits) == 1:
        return hits[0] if _payload_path_ok(session_id, hits[0], projects_dir) \
            else None
    return None


def _payload_path_ok(session_id, payload_path, projects_dir) -> bool:
    try:
        if os.path.basename(payload_path) != f"{session_id}.jsonl":
            return False
        if os.path.islink(payload_path):
            return False
        if not os.path.isfile(payload_path):
            return False
        real = os.path.realpath(payload_path)
        root = os.path.realpath(projects_dir)
        return os.path.commonpath([real, root]) == root
    except (OSError, ValueError):
        return False


# ---------------------------------------------------------------------------
# config: strict parse, clamp, safe default, stderr warning
# ---------------------------------------------------------------------------

def _as_int(value):
    """Strict: a real int or an all-digit string. Rejects bools and floats."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip()
        if text and (text.isdigit() or (text[0] == "-" and text[1:].isdigit())):
            try:
                return int(text)
            except ValueError:                             # pragma: no cover
                return None
    return None


def resolve_window(cfg_value, env_value, observed_tokens, *, warn=None):
    """(window, provenance) — provenance ∈ default|config|env|escalated.

    Default 200,000 rather than 1,000,000 because the two errors are NOT
    symmetric: assuming 1M on a 200k session means the nag never fires (a silent
    failure, the exact class this feature exists to end), while assuming 200k on
    a 1M session fires early — at most two messages, each naming the assumption.
    Fail toward firing.

    ESCALATION: a window a session has already exceeded is provably wrong, so an
    observed total above the resolved window steps up to the next known tier.
    A 1M session therefore self-corrects the moment it passes 200k.
    """
    window, provenance = DEFAULT_WINDOW, "default"
    for value, name in ((env_value, "env"), (cfg_value, "config")):
        if value is None:
            continue
        parsed = _as_int(value)
        if parsed is None or parsed <= 0:
            if warn:
                warn(f"context_meter: ignoring invalid windowSize {value!r} "
                     f"({name}); using {DEFAULT_WINDOW}")
            continue
        window, provenance = parsed, name
        break

    observed = _as_int(observed_tokens) or 0
    if observed > window:
        for candidate in KNOWN_WINDOWS:
            if candidate > observed:
                return candidate, "escalated"
        return observed, "escalated"
    return window, provenance


def _pct(cfg, env, cfg_key, env_key, default):
    for source, value in (("env", env.get(env_key)), ("config", cfg.get(cfg_key))):
        if value is None:
            continue
        parsed = _as_int(value)
        if parsed is None:
            return None, f"invalid {cfg_key} {value!r} ({source})"
        if not 1 <= parsed <= 99:
            return None, f"{cfg_key}={parsed} out of range 1..99 ({source})"
        return parsed, None
    return default, None


def thresholds(cfg, env, *, warn=None):
    """(check_in_pct, act_pct). Both fall back together on any problem.

    `act` must exceed `check_in` by at least MIN_TIER_GAP_PCT — a squeezed pair
    leaves no search band, so it is treated as misconfiguration rather than
    silently honoured.
    """
    cfg = cfg if isinstance(cfg, dict) else {}
    # NOT isinstance(env, dict): os.environ is os._Environ, so a dict check
    # would silently discard every env override in production. Duck-type it.
    env = env if hasattr(env, "get") else {}
    check_in, err1 = _pct(cfg, env, "checkInPercent",
                          "RAWGENTIC_CONTEXT_CHECKIN_PCT", DEFAULT_CHECK_IN_PCT)
    act, err2 = _pct(cfg, env, "actPercent",
                     "RAWGENTIC_CONTEXT_ACT_PCT", DEFAULT_ACT_PCT)
    problem = err1 or err2
    if problem is None and act - check_in < MIN_TIER_GAP_PCT:
        problem = (f"checkInPercent={check_in} must be at least "
                   f"{MIN_TIER_GAP_PCT} below actPercent={act}")
    if problem:
        if warn:
            warn(f"context_meter: {problem}; using defaults "
                 f"{DEFAULT_CHECK_IN_PCT}/{DEFAULT_ACT_PCT}")
        return DEFAULT_CHECK_IN_PCT, DEFAULT_ACT_PCT
    return check_in, act


def cadence(cfg, env, *, warn=None):
    """(every_turns, every_seconds) — AC9's two arms, both configurable."""
    cfg = cfg if isinstance(cfg, dict) else {}
    # NOT isinstance(env, dict): os.environ is os._Environ, so a dict check
    # would silently discard every env override in production. Duck-type it.
    env = env if hasattr(env, "get") else {}
    out = []
    for cfg_key, env_key, default in (
            ("everyTurns", "RAWGENTIC_CONTEXT_EVERY_TURNS", DEFAULT_EVERY_TURNS),
            ("everySeconds", "RAWGENTIC_CONTEXT_EVERY_SECONDS",
             DEFAULT_EVERY_SECONDS)):
        value = env.get(env_key)
        if value is None:
            value = cfg.get(cfg_key)
        parsed = default if value is None else _as_int(value)
        if parsed is None or parsed < 1:
            if warn and value is not None:
                warn(f"context_meter: ignoring invalid {cfg_key} {value!r}; "
                     f"using {default}")
            parsed = default
        out.append(min(parsed, 100_000))
    return out[0], out[1]


def tier_for(fraction, check_in_pct, act_pct) -> str:
    try:
        pct = float(fraction) * 100.0
    except (TypeError, ValueError):
        return "none"
    if pct >= act_pct:
        return "directive"
    if pct >= check_in_pct:
        return "advisory"
    return "none"


# ---------------------------------------------------------------------------
# the seam (AC7)
# ---------------------------------------------------------------------------

def _entered_at(pointer):
    if not isinstance(pointer, dict):
        return None
    value = pointer.get("entered_at")
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def _identity(pointer):
    return (pointer.get("workflow"), pointer.get("step"),
            pointer.get("step_title"), pointer.get("entered_at"))


def seam_verdict(armed, current):
    """(verdict, reason) with verdict ∈ seam | wait | unknown.

    A pointer TRANSITION, not a blessed step list. Reports a CANDIDATE, never
    "safe": a transition proves the recorded step changed; it does NOT prove the
    tree is committed, that no review wave is outstanding, or that the boundary
    was observed before work on the new step began. Only the session can confirm
    those, so the nag asks it to. (A step-number whitelist was rejected: it would
    hardcode WF2/WF3 step semantics into a hook and rot on the next spine change.)

    Three states, not two, and the third one matters: a session with NO workflow
    pointer has no phase to interrupt, so there is nothing to wait for. Folding
    that case into "wait" would have made the advisory tier permanently silent in
    every ordinary session — found by its own test, not by inspection.
    """
    have_armed = isinstance(armed, dict) and _entered_at(armed) is not None
    have_current = isinstance(current, dict) and _entered_at(current) is not None
    if not have_armed and not have_current:
        return "unknown", ("no workflow position recorded — nothing to wait "
                           "for, break whenever suits")
    if not have_armed or not have_current:
        return "unknown", "no comparable step-state pointer"
    if armed.get("project") != current.get("project"):
        return "unknown", "step-state pointer belongs to a different project"
    if _entered_at(current) <= _entered_at(armed):
        return "wait", "step-state pointer has not advanced"
    if _identity(armed) == _identity(current):
        return "wait", "step-state pointer unchanged"
    return "seam", (f"step-state moved to {current.get('workflow')} step "
                    f"{current.get('step')}")


def should_check(state, *, now, every_turns, every_seconds) -> bool:
    """AC9: 5 turns OR 5 minutes, whichever comes first."""
    if not isinstance(state, dict) or not state.get("last_check_ts"):
        return True
    turns = _as_int(state.get("turns")) or 0
    last_turn = _as_int(state.get("last_check_turn")) or 0
    if turns - last_turn >= every_turns:
        return True
    last_ts = _as_int(state.get("last_check_ts")) or 0
    return (now - last_ts) >= every_seconds


# ---------------------------------------------------------------------------
# state
# ---------------------------------------------------------------------------

def state_dir(home):
    return os.path.join(home, ".rawgentic", STATE_DIRNAME)


def state_path(home, session_id):
    if not isinstance(session_id, str) or not _SESSION_RE.match(session_id):
        return None
    return os.path.join(state_dir(home), f"{session_id}.json")


def marker_path(home, session_id, window, tier):
    return os.path.join(state_dir(home),
                        f"{session_id}.{window}.{tier}.emitted")


def load_state(path):
    try:
        with open(path, encoding="utf-8") as handle:
            state = json.load(handle)
        return state if isinstance(state, dict) else {}
    except (OSError, ValueError):
        return {}


def _ensure_dir(home):
    """Create the state dir PRIVATE. `~/.rawgentic` is 0775 on this host, so a
    plain mkdir would leave session state group/world readable; and a symlinked
    state dir would defeat the containment claim entirely."""
    target = state_dir(home)
    if os.path.islink(target):
        raise OSError(f"{target} is a symlink")
    parent = os.path.dirname(target)
    os.makedirs(parent, exist_ok=True)
    if not os.path.isdir(target):
        os.mkdir(target, 0o700)
    os.chmod(target, 0o700)
    real_home = os.path.realpath(home)
    if os.path.commonpath([os.path.realpath(target), real_home]) != real_home:
        raise OSError(f"{target} escapes {home}")
    return target


def save_state(home, session_id, state) -> bool:
    """Atomic, private, best-effort. False when the write did not land — the
    caller MUST NOT emit in that case (record-before-emit)."""
    path = state_path(home, session_id)
    if path is None:
        return False
    try:
        from atomic_write_lib import atomic_write_text
        _ensure_dir(home)
        atomic_write_text(path, json.dumps(state, sort_keys=True) + "\n",
                          prefix=".context-meter-")
        _sweep(home)
        return True
    except Exception:
        return False


def _sweep(home):
    """Bounded growth: one file per session, so drop week-old siblings."""
    try:
        cutoff = time.time() - SWEEP_AGE_S
        target = state_dir(home)
        for name in os.listdir(target):
            full = os.path.join(target, name)
            try:
                if os.path.isfile(full) and os.path.getmtime(full) < cutoff:
                    os.unlink(full)
            except OSError:
                continue
    except OSError:
        return


def has_marker(home, session_id, window, tier) -> bool:
    try:
        return os.path.exists(marker_path(home, session_id, window, tier))
    except OSError:
        return False


def reserve(home, session_id, window, tier) -> bool:
    """Win the right to emit this tier, exactly once, race-free.

    THE MARKER FILE *IS* the once-per-tier record — deliberately the only one.
    An earlier draft also kept an `emitted` list inside the JSON state, which was
    a second source of truth for the same fact and created a permanent
    lost-emission window: create the marker, fail the JSON write, and the tier
    could never fire again. Now the marker is the record, it is created
    immediately before stdout with nothing fallible in between, and the JSON
    state carries only cadence bookkeeping (whose loss costs one late check, not
    a lost warning).

    Parallel tool calls fire CONCURRENT PostToolUse hooks, so two processes can
    both read "not yet emitted". `os.replace` prevents a torn file but not a
    duplicate decision. An O_EXCL create is a filesystem compare-and-swap:
    exactly one process wins, the loser stays silent. No lock needed.
    """
    try:
        _ensure_dir(home)
        fd = os.open(marker_path(home, session_id, window, tier),
                     os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        return True
    except FileExistsError:
        return False
    except OSError:
        return False


def release(home, session_id, window, tier) -> None:
    """Give the reservation back, so a later turn can retry.

    Called on any failure between winning the reservation and delivering the
    message. Without this a post-reservation failure would silence the tier for
    the rest of the session — the exact defect the reservation was added to fix,
    just relocated.
    """
    try:
        os.unlink(marker_path(home, session_id, window, tier))
    except OSError:
        pass


# ---------------------------------------------------------------------------
# resolving the bound project, its config, and the step pointer
# ---------------------------------------------------------------------------

def find_workspace(cwd):
    """Walk up for `.rawgentic_workspace.json` (step_state.find_state_dir idiom)."""
    try:
        current = os.path.realpath(cwd or ".")
    except OSError:
        return None
    while True:
        if os.path.isfile(os.path.join(current, ".rawgentic_workspace.json")):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent


def bound_project(workspace_root, session_id):
    """(name, abs_path) for the LAST registry row matching this session."""
    registry = os.path.join(workspace_root, "claude_docs",
                            "session_registry.jsonl")
    found = None
    try:
        with open(registry, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict) and row.get("session_id") == session_id:
                    found = row
    except OSError:
        return None, None
    if not found:
        return None, None
    name = found.get("project")
    rel = found.get("project_path") or ""
    if not isinstance(name, str) or not name:
        return None, None
    path = os.path.normpath(os.path.join(workspace_root, rel)) if rel else None
    return name, path


def read_meter_config(project_path):
    """The project's own `contextMeter` block, read directly.

    Seven hooks already read `.rawgentic.json` directly for their own key
    (security-guard.py:81-96, security_guard_lib.py:206-223,
    seat_outcomes_lib.py:1237-1247, plan_lib.py:765, …), each fail-open on a
    malformed file. Deliberately NOT `capabilities_lib.py derive`: a subprocess
    on a hook that rides PostToolUse is a per-tool-call cost this has not
    earned, and derive returns a whole capabilities object to answer a
    four-integer question.
    """
    if not project_path:
        return {}
    try:
        with open(os.path.join(project_path, ".rawgentic.json"),
                  encoding="utf-8") as handle:
            cfg = json.load(handle)
    except (OSError, ValueError):
        return {}
    block = cfg.get("contextMeter") if isinstance(cfg, dict) else None
    return block if isinstance(block, dict) else {}


def read_pointer(workspace_root, project, session_id):
    """The step-state pointer, but ONLY if it belongs to THIS session.

    Another session's pointer is not evidence about this one.
    """
    if not workspace_root or not project:
        return None
    path = os.path.join(workspace_root, "claude_docs", "wal",
                        f"{project}.state.json")
    try:
        with open(path, encoding="utf-8") as handle:
            record = json.load(handle)
    except (OSError, ValueError):
        return None
    if not isinstance(record, dict):
        return None
    if record.get("session_id") != session_id:
        return None
    entered = _entered_at(record)
    if entered is None:
        return None
    age_min = (datetime.now(timezone.utc) - entered).total_seconds() / 60.0
    if age_min > 240:                       # step_state.DEFAULT_MAX_AGE_MIN
        return None
    return record


# ---------------------------------------------------------------------------
# the message
# ---------------------------------------------------------------------------

def emit_payload(event, text):
    """Per-event output shape — both proven live on 2026-07-28.

    `UserPromptSubmit` takes the top-level form (hooks/wal-context:43);
    `PostToolUse` takes the nested `hookSpecificOutput` form. Assuming one shape
    worked everywhere was a real defect in the first draft of this design.
    """
    if event == "PostToolUse":
        return {"hookSpecificOutput": {"hookEventName": "PostToolUse",
                                       "additionalContext": text}}
    return {"additionalContext": text}


def nag_text(*, tier, used, window, provenance, seam, seam_reason,
             headless, fresh_handoff_capable):
    """The injected advisory. Contains ONLY integers and the pointer's own
    fields — never any transcript content, which would leak the very context it
    is measuring."""
    pct = used * 100.0 / window if window else 0.0
    lines = [
        f"[rawgentic context meter] This session is using {used:,} tokens of an "
        f"assumed {window:,}-token context window ({pct:.0f}%). "
        f"Window source: {provenance}."
    ]
    if provenance == "default":
        lines.append(
            "That window is the conservative DEFAULT, not a measurement — if "
            "this session's model has a larger window, set "
            "`contextMeter.windowSize` in the project's .rawgentic.json (or "
            "RAWGENTIC_CONTEXT_WINDOW) so this reading is right."
        )

    if tier == "advisory":
        lines.append(
            "Start looking for a safe seam to break at. Do NOT stop mid-phase."
        )
        if seam == "seam":
            lines.append(
                f"A step boundary was just recorded ({seam_reason}). If your "
                "tree is clean and no review wave is outstanding, this is the "
                "moment to break — confirm both yourself; this signal cannot."
            )
        else:
            lines.append(f"Seam status: {seam_reason}.")
    else:
        lines.append(
            "Break NOW, at the next turn, seam or no seam. Accept a mid-phase "
            "seam if that is where you are: capture the branch + commit, the "
            "recorded test baseline, the current step marker, and the loop-back "
            "counters so the successor can resume."
        )

    if headless and fresh_handoff_capable:
        lines.append(
            "Unattended with an armed launcher: checkpoint and hand over via "
            "`launcher_lib.py handoff` — do not wait for a human."
        )
    elif headless:
        lines.append(
            "Unattended, but NO durable launcher is armed, so there is nothing "
            "to relaunch you: run the `clear-prep` skill to write the durable "
            "checkpoint and handoff, then stop cleanly for a manual resume."
        )
    else:
        lines.append(
            "Run the `clear-prep` skill: it writes the mempalace checkpoint, "
            "the durable handoff file, the resume prompt and the /goal text. "
            "Its handoff carries `next actions, in order` — the successor "
            "rebuilds its task list from those via /tasklist."
        )
    return " ".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _warn(message):
    print(message, file=sys.stderr)


def _is_subagent(payload) -> bool:
    """A subagent has its own short-lived context and no authority to hand over
    its parent's session, so the meter does nothing for one.

    Defensive by design: probe 9 captured only a top-level payload, so the exact
    field name a subagent invocation carries is UNVERIFIED. If none of these keys
    is present the branch is simply inert, which is why it cannot break the hook.
    """
    if not isinstance(payload, dict):
        return False
    for key in ("agent_id", "agentId", "subagent_id", "isSidechain",
                "is_sidechain"):
        if payload.get(key):
            return True
    return False


def _diagnose(state, kind, message) -> bool:
    """Once-per-session stderr diagnostic. Returns True when it fired."""
    seen = state.setdefault("diagnostics", [])
    if not isinstance(seen, list):
        seen = state["diagnostics"] = []
    if kind in seen:
        return False
    seen.append(kind)
    _warn(f"context_meter: {message}")
    return True


def cmd_hook(argv) -> int:
    try:
        payload = json.load(sys.stdin)
    except (ValueError, OSError):
        return 0
    if not isinstance(payload, dict):
        return 0
    if _is_subagent(payload):
        return 0

    session_id = payload.get("session_id")
    if not isinstance(session_id, str) or not _SESSION_RE.match(session_id):
        return 0

    home = os.path.expanduser("~")
    path = state_path(home, session_id)
    if path is None:
        return 0
    state = load_state(path)
    state.setdefault("schema_version", 1)
    state["session_id"] = session_id

    event = payload.get("hook_event_name")
    if event == "UserPromptSubmit":
        state["turns"] = (_as_int(state.get("turns")) or 0) + 1

    env = os.environ
    now = int(time.time())
    # The throttle uses cadence CACHED in state, so the cheap path costs one
    # small JSON read and two integer compares — no workspace walk, no config
    # read, no transcript open. It rides every tool call, so this matters.
    every_turns = _as_int(state.get("every_turns")) or DEFAULT_EVERY_TURNS
    every_seconds = (_as_int(state.get("every_seconds"))
                     or DEFAULT_EVERY_SECONDS)
    if not should_check(state, now=now, every_turns=every_turns,
                        every_seconds=every_seconds):
        save_state(home, session_id, state)
        return 0

    workspace = find_workspace(payload.get("cwd"))
    project, project_path = (bound_project(workspace, session_id)
                             if workspace else (None, None))
    cfg = read_meter_config(project_path)
    every_turns, every_seconds = cadence(cfg, env, warn=_warn)
    state["every_turns"], state["every_seconds"] = every_turns, every_seconds
    check_in_pct, act_pct = thresholds(cfg, env, warn=_warn)

    transcript = resolve_transcript(session_id,
                                    payload_path=payload.get("transcript_path"))
    used = read_used_tokens(transcript) if transcript else None

    state["last_check_turn"] = _as_int(state.get("turns")) or 0
    state["last_check_ts"] = now

    if transcript is None:
        _diagnose(state, "transcript_unresolved",
                  f"could not resolve a transcript for session {session_id} "
                  "— the meter is inactive for this session")
        save_state(home, session_id, state)
        return 0
    if used is None:
        _diagnose(state, "no_usage_row",
                  f"no parseable message.usage row in {transcript} "
                  "— the meter is inactive for this session")
        save_state(home, session_id, state)
        return 0

    window, provenance = resolve_window(cfg.get("windowSize"),
                                        env.get("RAWGENTIC_CONTEXT_WINDOW"),
                                        used, warn=_warn)
    state["assumed_window"] = window
    state["window_provenance"] = provenance
    tier = tier_for(used / window, check_in_pct, act_pct)

    if tier == "none":
        save_state(home, session_id, state)
        return 0
    # The reservation MARKERS are the once-per-tier record, and they are keyed by
    # EFFECTIVE WINDOW: escalation changes the denominator, so a tier recorded
    # against a window the session has outgrown cannot suppress the real warning
    # later. (A flat list in the JSON state would have silenced the true 600k
    # advisory because of a premature one computed against an assumed 200k
    # window — and being a second source of truth, it also created a permanent
    # lost-emission window when the JSON write failed.)
    #
    # Monotonic: a directive satisfies the advisory for the same window, so no
    # stale advisory can follow it.
    if has_marker(home, session_id, window, tier) or (
            tier == "advisory"
            and has_marker(home, session_id, window, "directive")):
        save_state(home, session_id, state)
        return 0

    pointer = read_pointer(workspace, project, session_id)
    armed = state.get("seam_search")
    if not isinstance(armed, dict) or armed.get("window") != window:
        # Escalation resets the search: a seam armed under a wrong denominator
        # was armed for the wrong reason.
        armed = {"armed_at": now, "window": window, "pointer": pointer}
        state["seam_search"] = armed
    seam, seam_reason = seam_verdict(armed.get("pointer"), pointer)

    # The advisory tier waits for a seam ONLY when there is a tracked phase to
    # wait for. The directive tier never waits.
    if tier == "advisory" and seam == "wait":
        save_state(home, session_id, state)
        return 0

    # Build the message BEFORE reserving, so nothing fallible sits between
    # winning the reservation and delivering it.
    headless = env.get("RAWGENTIC_HEADLESS") == "1"
    fresh_handoff_capable = (env.get("RAWGENTIC_LAUNCHER_ARMED") == "1"
                             and env.get("RAWGENTIC_FRESH_LAUNCH_SUPPORTED") == "1")
    try:
        text = nag_text(tier=tier, used=used, window=window,
                        provenance=provenance, seam=seam,
                        seam_reason=seam_reason, headless=headless,
                        fresh_handoff_capable=fresh_handoff_capable)
        payload_out = json.dumps(emit_payload(event, text))
    except Exception:
        save_state(home, session_id, state)
        return 0

    # RECORD BEFORE EMIT (security-guard-check.sh:49-55). The marker create IS
    # the record: it lands first, and the very next statement delivers. If
    # delivery still fails, RELEASE the reservation — holding it would silence
    # this tier for the rest of the session, which is the defect the reservation
    # exists to prevent, merely relocated.
    if not reserve(home, session_id, window, tier):
        save_state(home, session_id, state)
        return 0
    try:
        print(payload_out)
        sys.stdout.flush()
    except Exception:
        release(home, session_id, window, tier)
        return 0
    save_state(home, session_id, state)
    return 0


def cmd_read(args) -> int:
    transcript = args.transcript or resolve_transcript(args.session_id)
    used = read_used_tokens(transcript) if transcript else None
    if used is None:
        print(json.dumps({"used": None, "transcript": transcript,
                          "error": "no parseable usage row"}))
        return 3
    window, provenance = resolve_window(
        None, os.environ.get("RAWGENTIC_CONTEXT_WINDOW"), used)
    check_in_pct, act_pct = thresholds({}, os.environ)
    print(json.dumps({"used": used, "window": window,
                      "fraction": round(used / window, 6),
                      "tier": tier_for(used / window, check_in_pct, act_pct),
                      "provenance": provenance, "transcript": transcript},
                     sort_keys=True))
    return 0


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("hook", help="read a hook payload as JSON on stdin")
    p_read = sub.add_parser("read", help="print the current reading as JSON")
    p_read.add_argument("--session-id", default=None)
    p_read.add_argument("--transcript", default=None)
    if not argv:
        argv = ["hook"]
    args = parser.parse_args(argv)
    if args.cmd == "read":
        return cmd_read(args)
    return cmd_hook(args)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:          # fail-OPEN: never block a turn
        sys.exit(0)
