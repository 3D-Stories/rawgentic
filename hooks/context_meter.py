#!/usr/bin/env python3
"""Context-pressure trigger (#687) — a hook that notices the window filling up.

Design: docs/planning/2026-07-28-687-context-pressure-trigger.md

A session's "my context is getting full, I should hand over" behaviour used to be
pure model judgment, so it fired only sometimes. This reads the session's own
transcript, expresses usage as a FRACTION of the context window, and injects a
nag at two tiers — once each per session per effective window. A hook cannot
forget; that is the whole idea.

Registered on THREE events (#713 added the third):
  * the 5-TURN arm needs `UserPromptSubmit` (one event per user prompt);
  * the 5-MINUTE arm needs `PostToolUse`, because a long autonomous run gets ONE
    user prompt and then works for hours — a UserPromptSubmit-only meter would be
    silently dead in exactly the runs that need it most.
  * `Stop` is where `/goal` decides whether to re-prompt, so it is the only moment a
    "hand over now" reading can arrive while it still decides anything. Without it the
    meter spoke mid-turn and went stale before the decision — a real run reached ~98% of
    a 1M window with ten Stop firings and never handed off (#713).

ONE output shape on every event: `additionalContext` NESTED under `hookSpecificOutput`
with `hookEventName` set to the firing event. An earlier version of this module sent the
TOP-LEVEL form on `UserPromptSubmit`, recorded as verified live 2026-07-28; probes 14/14b
(docs/planning/2026-07-29-713-probes/) measured that shape being SILENTLY IGNORED on
Claude Code 2.1.220, so that arm had never delivered anything. Nested is confirmed
delivered on all three events.

`Stop` is DIFFERENT in three ways, all of them consequences of one fact — at `Stop` every
channel that reaches the model also CONTINUES the turn, so silence is the only way to let
a turn end:
  * it emits only when `stop_hook_active` is true (a hook-driven loop is already
    continuing, so speaking costs no turn it was not already taking);
  * it emits only the DIRECTIVE tier (at the check-in tier a forced turn buys nothing);
  * it is exempt from the cadence throttle, because it fires once per turn and being
    throttled at the decision point is the exact mistiming #713 is about.

FAIL MODE: **fail-OPEN.** An absent/unreadable/malformed transcript, an
unwritable state dir, a bad config value, or any unexpected exception means
"emit nothing, exit 0" — never a blocked turn. This is a convenience nag, not a
security boundary (CLAUDE.md §3 decision guide). Fail-open is NOT the same as
invisible, though: every self-disabling outcome emits a stderr diagnostic, so a
platform path/format change is findable instead of manifesting as a meter that
quietly never fires. Three are once-per-session (unresolvable transcript,
ambiguous match, no parseable usage row). The FOURTH — an unusable state
directory — deliberately REPEATS, because its once-per-session record would have
to live in the very store that is broken, and a store that cannot remember
cannot deduplicate. Named here rather than pretending the guarantee is uniform.

Pure core + thin CLI (`registry_prune.py` is the exemplar): every function below
`main` is pure or takes its I/O injected.
"""
from __future__ import annotations

import argparse
import glob as _glob
import json
import os
import re
import stat
import sys
import time
from datetime import datetime, timezone

import supervision_lib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# The three fields that make up the in-context total. output_tokens is
# deliberately EXCLUDED: #654 verified the identity 2 + 1257 + 652960 == the
# statusline's own `total_input_tokens`, and an assistant's output re-enters as
# cache_creation on the next turn, so counting it would double-count.
IN_CONTEXT_FIELDS = ("input_tokens", "cache_creation_input_tokens",
                     "cache_read_input_tokens")

DEFAULT_WINDOW = 200_000          # conservative floor — see resolve_window
KNOWN_WINDOWS = (200_000, 1_000_000)
# Owner decision 2026-07-29 (#716): 60/70 -> 35/50. The old pair had ~30 points of margin
# against measured auto-compaction (~99.8% on a 1M window), so it was safe — and still
# too late to be USEFUL. Two real runs proved the gap: one rode a 1M window to ~98%
# because the directive arrived with no room left to act well on it, and the quality
# gradient across its passes tracked the pressure. Margin against compaction was never the
# binding constraint; room to write a good handoff is. At 35% of a 1M window a session has
# ~650k tokens in hand to finish its phase and hand over properly; at 70% it has 300k and
# is already choosing what to drop.
DEFAULT_CHECK_IN_PCT = 35         # AC6 — start LOOKING for a break
DEFAULT_ACT_PCT = 50              # AC3 — act now (gap 15, above MIN_TIER_GAP_PCT)
MIN_TIER_GAP_PCT = 10
DEFAULT_EVERY_TURNS = 5
DEFAULT_EVERY_SECONDS = 300

_BLOCK = 65536
DEFAULT_MAX_BYTES = 4 * 1024 * 1024   # the largest transcript here is 83 MB

_SESSION_RE = re.compile(r"[A-Za-z0-9-]{8,64}\Z")
# A project name reaches a FILENAME (the step-state pointer) — same bare-name contract
# step_state.sanitize_project enforces, but rejecting rather than sanitizing.
_PROJECT_RE = re.compile(r"[A-Za-z0-9._-]{1,64}\Z")
# Anything echoed into the model's context is allowlisted to this shape. The step-state
# pointer is a project-controlled FILE, so its strings are untrusted input, and
# additionalContext is read by the model as if it were trustworthy (#687 Step-11 review,
# Critical: a hostile pointer could smuggle instruction text into a session's next turn).
_ECHO_SAFE_RE = re.compile(r"[A-Za-z0-9._-]{1,32}\Z")
STATE_DIRNAME = "context-meter"
SWEEP_AGE_S = 7 * 24 * 3600
# Every file this hook reads is attacker-influenceable and it runs on every tool call under a
# 5 s timeout, so each read is byte-capped and refuses anything that is not a regular file.
MAX_JSON_BYTES = 256 * 1024
MAX_REGISTRY_BYTES = 8 * 1024 * 1024   # ~55k rows; see _find_registry_row
MAX_STDIN_BYTES = 4 * 1024 * 1024


def _echo_safe(value, fallback="?"):
    """Allowlist a value before it can reach the model's context."""
    text = str(value) if value is not None else ""
    return text if _ECHO_SAFE_RE.match(text) else fallback


def _read_capped(path, limit, *, tail=False):
    """Read at most `limit` bytes from a REGULAR file, else None.

    Refuses symlinks and non-regular files (a FIFO would otherwise block until the hook's
    timeout) and never follows a symlink between the check and the open — the descriptor is
    opened with O_NOFOLLOW and stat'ed through itself.
    """
    fd = None
    try:
        # O_NONBLOCK is load-bearing, not belt-and-braces: opening a FIFO for reading
        # BLOCKS until a writer appears, so without it the hook would hang at open() —
        # before any fstat could reject the file — and burn its whole timeout. Found by
        # this module's own FIFO test, which hung the suite until this flag was added.
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK
                     | getattr(os, "O_NOFOLLOW", 0))
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            return None
        if tail and info.st_size > limit:
            os.lseek(fd, info.st_size - limit, os.SEEK_SET)
        data = os.read(fd, limit)
        return data.decode("utf-8", "replace")
    except (OSError, ValueError):
        return None
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass


def _load_json_capped(path, limit=MAX_JSON_BYTES):
    text = _read_capped(path, limit)
    if text is None:
        return None
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return None


def _find_registry_row(path, session_id, *, max_bytes=MAX_REGISTRY_BYTES):
    """Most recent registry row for `session_id`, scanning backward. None if not found.

    Bounded like the transcript reader, and for the same reason: the file can be large
    and only its end is interesting. Returns as soon as a match is seen, so the common
    case reads one block.
    """
    fd = None
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK
                     | getattr(os, "O_NOFOLLOW", 0))
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        pos = os.lseek(fd, 0, os.SEEK_END)
        tail = b""
        read_total = 0
        while pos > 0 and read_total < max_bytes:
            block = min(_BLOCK, pos, max_bytes - read_total)
            pos -= block
            os.lseek(fd, pos, os.SEEK_SET)
            chunk = os.read(fd, block)
            if not chunk:
                break
            read_total += len(chunk)
            parts = (chunk + tail).split(b"\n")
            tail = parts[0] if pos > 0 else b""
            for line in reversed(parts[1:] if pos > 0 else parts):
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line.decode("utf-8", "replace"))
                except (ValueError, TypeError):
                    continue
                if isinstance(row, dict) and row.get("session_id") == session_id:
                    return row
    except (OSError, ValueError):
        return None
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
    return None


def _contained(child, parent) -> bool:
    try:
        child_real = os.path.realpath(child)
        parent_real = os.path.realpath(parent)
        return os.path.commonpath([child_real, parent_real]) == parent_real
    except (OSError, ValueError):
        return False


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
        # Past the largest KNOWN tier, PIN to that tier. Returning the observed count
        # would make the effective window change on every reading, and the window is the
        # key of the once-per-tier marker — so each check would open a fresh namespace and
        # re-deliver the directive forever (#687 Step-11 review, found by both reviewers).
        return KNOWN_WINDOWS[-1], "escalated"
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


# Every key the `contextMeter` block documents (docs/config-reference.md). Named here as ONE
# constant because `validate_setup_block` uses it as an ALLOWLIST: a hand-copied list inside the
# function would drift from the documented block the moment a sixth key is added.
SETUP_BLOCK_KEYS = ("windowSize", "checkInPercent", "actPercent", "everyTurns", "everySeconds",
                    "insertPrompt")


def validate_setup_block(block) -> list[str]:
    """Every reason `block` would NOT be honoured as written, for `/rawgentic:setup` (#701).

    Lives here, beside the constants, because the rules it enforces are the HOOK's: the 1..99 range
    is `_pct`'s and the tier gap is `MIN_TIER_GAP_PCT`. A validator anywhere else would have to copy
    both, and a copy is the drift this exists to prevent — setup would accept a pair `thresholds()`
    then discards, leaving the user's tuned values inert and the meter silently back on its
    defaults, which is indistinguishable from the bug #701 was filed about.

    It adds nothing to the reading, the thresholds or the nag behaviour #687 settled; it only reads.

    Deliberately PURE and total: no I/O, and every input shape is answered with a message rather
    than an exception. `context_meter.py`'s `__main__` fails OPEN by design (a PostToolUse hook must
    never block a turn), so a validator that raised would be reported as exit 0 — a validation gate
    that passes on error is worse than none.

    An EMPTY block is valid: absence means documented defaults, and #701 AC5 requires a declined
    prompt to leave the section absent rather than restate them.
    """
    if not isinstance(block, dict):
        return [f"contextMeter must be a JSON object, got {type(block).__name__}"]

    errors: list[str] = []

    # Unknown keys are REFUSED, not ignored (#701 Step-11 diff review, High). Checking only the keys
    # it knows about made the validator say "ok" to `{"windowSzie": 1000000}`: setup would stage it,
    # the hook would ignore the misspelled field, and the meter would keep using the 200,000-token
    # fallback — the exact failure this whole feature exists to prevent, reproduced by the fix for it.
    for key in block:
        if key not in SETUP_BLOCK_KEYS:
            errors.append(
                f"unknown contextMeter key {key!r} — it would be silently ignored and the meter "
                f"would keep its default. Expected one of: {', '.join(SETUP_BLOCK_KEYS)}")

    def _int(value):
        # `bool` is an `int` subclass, so a bare isinstance check would accept `true` and mean 1.
        return None if isinstance(value, bool) or not isinstance(value, int) else value

    pcts: dict[str, int] = {}
    for key, default in (("checkInPercent", DEFAULT_CHECK_IN_PCT),
                         ("actPercent", DEFAULT_ACT_PCT)):
        if key not in block:
            pcts[key] = default
            continue
        parsed = _int(block[key])
        if parsed is None:
            errors.append(f"{key} must be a whole number, got {block[key]!r}")
        elif not 1 <= parsed <= 99:
            errors.append(f"{key}={parsed} is out of range 1..99")
        else:
            pcts[key] = parsed

    # Checked against the DEFAULT for whichever half was omitted, because that is the pair
    # `thresholds()` will actually evaluate.
    if len(pcts) == 2:
        gap = pcts["actPercent"] - pcts["checkInPercent"]
        if gap < MIN_TIER_GAP_PCT:
            errors.append(
                f"checkInPercent={pcts['checkInPercent']} must be at least {MIN_TIER_GAP_PCT} "
                f"below actPercent={pcts['actPercent']} (gap is {gap}) — a squeezed or inverted "
                "pair leaves no band in which to look for a seam, so the advisory tier is "
                "unreachable and the session goes straight to 'break now'")

    if "windowSize" in block:
        window = _int(block["windowSize"])
        if window is None:
            errors.append(f"windowSize must be a whole number of tokens, got "
                          f"{block['windowSize']!r}")
        elif window <= 0:
            errors.append(f"windowSize={window} must be a positive number of tokens")

    # #718 kill switch. STRICTLY a bool, because `insert_enabled` compares with `is True`: a
    # truthy-looking `"yes"` or `1` would be staged by setup and then read as OFF by the hook,
    # which is the "setup accepts a block the hook discards" defect #701 exists to prevent.
    if "insertPrompt" in block and not isinstance(block["insertPrompt"], bool):
        errors.append(f"insertPrompt must be true or false, got {block['insertPrompt']!r} — the "
                      "hook accepts only a real boolean, so any other value reads as OFF")

    return errors


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
    """Tier for a used/window fraction. Prefer `tier_for_tokens` — float arithmetic
    puts an exact boundary a hair under itself (116000/200000*100 computes as
    57.99999999999999, so an exact 58% configured boundary would not fire)."""
    try:
        pct = float(fraction) * 100.0
    except (TypeError, ValueError):
        return "none"
    if pct >= act_pct:
        return "directive"
    if pct >= check_in_pct:
        return "advisory"
    return "none"


def tier_for_tokens(used, window, check_in_pct, act_pct) -> str:
    """Integer-exact tier: compares `used * 100` against `pct * window`, so a
    configured boundary is honoured exactly rather than approximately."""
    used_i, window_i = _as_int(used), _as_int(window)
    if used_i is None or not window_i or window_i <= 0:
        return "none"
    scaled = used_i * 100
    if scaled >= act_pct * window_i:
        return "directive"
    if scaled >= check_in_pct * window_i:
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
    # NOTHING from the pointer is echoed. The pointer is a project-controlled FILE, so its
    # strings are untrusted, and this reason is injected into the model's next turn — the
    # Step-11 review rated verbatim interpolation CRITICAL. An allowlist was the first fix,
    # and the verification review then showed it insufficient: `IGNORE-PRIOR-INSTRUCTIONS`
    # satisfies any identifier pattern. So the channel is REMOVED rather than narrowed. The
    # session already knows which step it is on; the meter does not need to tell it.
    return "seam", "a workflow step boundary was just recorded"


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


def channel_for(event) -> str:
    """Which delivery CHANNEL an event belongs to (#713).

    `Stop` is its own channel because it is a different moment, not a different event:
    it is where `/goal` decides whether to re-prompt. The two mid-turn events share one
    channel, so #687's once-per-tier guarantee is preserved rather than tripled.
    """
    return "stop" if event == "Stop" else "midturn"


def marker_path(home, session_id, window, tier, channel):
    """The once-per-tier record, keyed by CHANNEL as well (#713).

    Without the channel the mid-turn arm's 70% delivery would consume the only marker and
    silence the Stop arm for the rest of the session — reproducing the very bug #713
    reports (a directive that never reaches the re-prompt decision) through its own fix.
    """
    return os.path.join(state_dir(home),
                        f"{session_id}.{window}.{channel}.{tier}.emitted")


def load_state(path):
    """Read the per-session state, capped and no-follow.

    This one was MISSED by the first bounded-read pass and the verification review
    caught it: a FIFO planted at the state path hung every hook invocation until the
    timeout, and a huge state file was read unbounded — on a hook that runs per tool
    call. It goes through the same capped reader as every other input now.
    """
    state = _load_json_capped(path)
    return state if isinstance(state, dict) else {}


def _ensure_dir(home):
    """Create the state dir PRIVATE, validating BEFORE mutating anything.

    `~/.rawgentic` is 0775 on this host, so a plain mkdir would leave session state
    group/world readable. Two ordering bugs the Step-11 review found, both fixed here:
    the containment check ran AFTER mkdir+chmod (so a path that escapes $HOME was
    created and chmod'ed before being refused), and only the final component was
    symlink-checked (so a symlinked `~/.rawgentic` passed a realpath containment test
    against $HOME while redirecting every write).
    """
    parent = os.path.join(home, ".rawgentic")
    target = state_dir(home)
    # Validate every component we did not create, before touching the filesystem.
    for path in (parent, target):
        if os.path.islink(path):
            raise OSError(f"{path} is a symlink")
    if os.path.exists(parent) and not _contained(parent, home):
        raise OSError(f"{parent} escapes {home}")
    os.makedirs(parent, exist_ok=True)
    if not os.path.isdir(target):
        os.mkdir(target, 0o700)
    if not _contained(target, home):
        raise OSError(f"{target} escapes {home}")
    info = os.lstat(target)
    if not stat.S_ISDIR(info.st_mode):
        raise OSError(f"{target} is not a directory")
    os.chmod(target, 0o700)
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
    """Bounded growth: one file per session, so drop week-old siblings.

    A session's marker files are dropped ONLY when that session's own state file is
    also stale. Sweeping markers by their own mtime would let a session alive for
    more than seven days lose its emission record and get nagged a second time for a
    tier it already delivered (#687 Step-11 review) — the marker is the once-per-tier
    record, so deleting it under a live session breaks the guarantee.
    """
    try:
        cutoff = time.time() - SWEEP_AGE_S
        target = state_dir(home)
        names = os.listdir(target)
    except OSError:
        return
    fresh = set()
    for name in names:
        if not name.endswith(".json"):
            continue
        try:
            if os.path.getmtime(os.path.join(target, name)) >= cutoff:
                fresh.add(name[: -len(".json")])
        except OSError:
            fresh.add(name[: -len(".json")])       # unknown age => treat as live
    for name in names:
        full = os.path.join(target, name)
        session = name.split(".", 1)[0]
        if session in fresh:
            continue
        try:
            if os.path.isfile(full) and os.path.getmtime(full) < cutoff:
                os.unlink(full)
        except OSError:
            continue


def has_marker(home, session_id, window, tier, channel) -> bool:
    """Has this tier already been delivered on this channel?

    Honours the PRE-#713 unchannelled marker for the mid-turn channel (Step-11 diff review,
    Medium). A session that crossed a tier under <= 3.107.1 and then picked up this version
    would otherwise find no marker and re-deliver the tier it had already been given — and
    since the directive tier now leads to an automatic handoff, a duplicate is not merely
    noise. The legacy marker deliberately does NOT satisfy the `stop` channel: that channel
    never delivered anything before this version, so treating an old record as covering it
    would leave the new arm born silenced in exactly the long-running sessions it is for.
    """
    try:
        if os.path.exists(marker_path(home, session_id, window, tier, channel)):
            return True
        if channel == "midturn":
            return os.path.exists(os.path.join(
                state_dir(home), f"{session_id}.{window}.{tier}.emitted"))
        return False
    except OSError:
        return False


def reserve(home, session_id, window, tier, channel) -> bool:
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
    path = marker_path(home, session_id, window, tier, channel)
    created = False
    try:
        _ensure_dir(home)
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        created = True
        os.close(fd)
        return True
    except FileExistsError:
        return False
    except OSError:
        # If the create SUCCEEDED and a later step (os.close) failed, the marker must not
        # survive a False return — it would suppress every retry for the rest of the
        # session while nothing was ever delivered (#687 Step-11 review).
        if created:
            try:
                os.unlink(path)
            except OSError:
                pass
        return False


def release(home, session_id, window, tier, channel) -> None:
    """Give the reservation back, so a later turn can retry.

    Called on any failure between winning the reservation and delivering the
    message. Without this a post-reservation failure would silence the tier for
    the rest of the session — the exact defect the reservation was added to fix,
    just relocated.
    """
    try:
        os.unlink(marker_path(home, session_id, window, tier, channel))
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
    # Scan BACKWARD in bounded blocks, stopping at the first (i.e. most recent) matching
    # row. The registry is append-only and a workspace can commit a huge hostile prefix, so
    # reading all of it on every due check is a self-inflicted timeout on a hook that runs
    # per tool call (#687 Step-11 review). A fixed TAIL slice was the first fix, and the
    # verification review showed it wrong in the other direction: >2 MiB appended AFTER a
    # live session's row made the session look unbound, losing its config and producing a
    # premature default-window nag. Backward-from-the-end is both bounded and correct for
    # the case that matters, because a session's own row is written when it binds.
    found = _find_registry_row(registry, session_id)
    if not found:
        # Either the session genuinely is not bound, or its row lies beyond the scan
        # bound. Those are indistinguishable from here, and the consequence of guessing
        # wrong is a premature default-window nag — so the caller reports the ambiguity
        # instead of swallowing it (verification review).
        return None, None
    name = found.get("project")
    rel = found.get("project_path") or ""
    # The project NAME becomes a filename (the step-state pointer) and the PATH is read
    # from — both come from a file a repo controls, so both are validated rather than
    # trusted: a name like "/tmp/payload" or a path like "../../victim" would otherwise
    # redirect the reads (#687 Step-11 review, High).
    if not isinstance(name, str) or not _PROJECT_RE.match(name):
        return None, None
    if not isinstance(rel, str) or not rel or os.path.isabs(rel):
        return name, None
    path = os.path.normpath(os.path.join(workspace_root, rel))
    # A symlinked component inside the workspace would let one project's registry row
    # point at another project's config and pointer, so the declared path must be its
    # own realpath (verification review: `claimed` symlinked elsewhere was accepted).
    if os.path.realpath(path) != os.path.abspath(path):
        return name, None
    if not _contained(path, workspace_root) or not os.path.isdir(path):
        return name, None
    return name, path


def read_meter_config(project_path):
    """The project's own `contextMeter` block, read directly.

    Seven hooks already read `.rawgentic.json` directly for their own key
    (security-guard.py:81-96, security_guard_lib.py:206-223,
    plan_lib.py:765, …), each fail-open on a
    malformed file. Deliberately NOT `capabilities_lib.py derive`: a subprocess
    on a hook that rides PostToolUse is a per-tool-call cost this has not
    earned, and derive returns a whole capabilities object to answer a
    four-integer question.
    """
    if not project_path:
        return {}
    cfg = _load_json_capped(os.path.join(project_path, ".rawgentic.json"))
    block = cfg.get("contextMeter") if isinstance(cfg, dict) else None
    return block if isinstance(block, dict) else {}


# ---------------------------------------------------------------------------
# #718 — inserting a real PROMPT at the act tier
# ---------------------------------------------------------------------------

# The insert gets its OWN channel in the existing marker dimension (`midturn`, `stop`), NOT a share
# of the emit's reservation. Sharing it would spend the session's single attempt on a transient
# herdr failure and leave only the emit channel — the channel probe 12 showed a model may refuse as
# possible prompt injection. With its own channel the insert can be released and retried at the next
# Stop while the nag stays delivered exactly once.
INSERT_CHANNEL = "stop-insert"

# Whole-subprocess budget. `launcher_lib insert-prompt` sleeps INSERT_SUBMIT_DELAY_S (1.5 s, the
# measured minimum that actually submits — #718 §5b) and makes three quick herdr calls, so 12 s is
# margin rather than an expectation. It IS latency added to one `Stop`, once per session per window;
# that is the stated price of a submit that lands instead of a paste nobody submits.
INSERT_TIMEOUT_S = 12


def meter_config_readable(project_path) -> bool:
    """Did the project's own `.rawgentic.json` actually exist and parse?

    `read_meter_config` fails OPEN and returns `{}` for BOTH a healthy config carrying no
    `contextMeter` block and a missing, oversized or malformed file. That collapse is right for
    thresholds — the documented defaults are the correct answer either way — and WRONG for the #718
    kill switch (Step-11 diff review, High): a config that would not parse may well contain
    `insertPrompt: false`, and reading that as "absent, therefore on" ignores the operator's switch
    while claiming to be fail-closed.

    `_load_json_capped` returns None for missing/unreadable/malformed and a dict for valid JSON, so
    the distinction is available without a second read of the file's bytes.
    """
    if not project_path:
        return False
    return isinstance(_load_json_capped(os.path.join(project_path, ".rawgentic.json")), dict)


def insert_enabled(cfg, project_path, config_readable=True) -> bool:
    """The #718 kill switch, and the fail-CLOSED guard that makes it reachable.

    Keyed on `project_path`, deliberately NOT on the block being non-empty: most projects carry no
    `contextMeter` block at all (this repo included, verified 2026-07-29), so an empty-block test
    would silently disable the feature everywhere it matters.

    No resolved project → REFUSE. The switch lives in a project's `.rawgentic.json`, so outside a
    project it cannot be reached — and default-on there would auto-type an authoritative imperative
    into unrelated herdr sessions (#718 WF5 review, H2). A boundary that cannot evaluate its own
    guard refuses; that is this repo's rule for exactly this shape.
    """
    if not project_path:
        return False
    if not config_readable:
        return False
    if isinstance(cfg, dict) and "insertPrompt" in cfg:
        return cfg["insertPrompt"] is True
    return True


def insert_prose(used, window) -> str:
    """The inserted text. PROSE that ASKS for the skill — never a bare slash command.

    Measured #718: a bare `/pane-handoff` sat queued through five goal-driven turns and was taken
    up only once the goal was met, while prose was acted on in 17 seconds. `launcher_lib`'s
    `validate_inserted_prompt` refuses anything starting with `/`, so this string is checked again
    on the way through rather than trusted.

    It is imperative on purpose. As INJECTED hook text an imperative gets refused (probe 12); as
    inserted USER input it is the authoritative channel, which is the entire point of #718.
    """
    pct = int(round(100.0 * used / window)) if window else 0
    return (f"Context is at {pct}% of the window ({used:,} of {window:,} tokens). "
            "Please run the rawgentic pane-handoff skill now to pass this work to a fresh pane. "
            "Run it — do not ask first. If you are mid-task, finish the smallest safe unit, "
            "then hand off.")


def try_insert_prompt(*, home, session_id, window, used, cfg, project_path, env,
                      runner=None) -> str:
    """Best-effort prompt insertion. Returns a short outcome string, for tests and diagnostics.

    FAIL-OPEN in every direction — it is called from a `Stop` hook and must never raise, block a
    turn, or make the meter's own emit conditional on herdr being healthy. Every refusal is a quiet
    return, because the emit has already delivered the same information by the old channel.
    """
    if not insert_enabled(cfg, project_path, meter_config_readable(project_path)):
        return "skipped: disabled, unreadable config, or no resolved project"
    if env.get("HERDR_ENV") != "1":
        return "skipped: not inside herdr"
    pane = env.get("HERDR_PANE_ID")
    if not pane:
        return "skipped: no HERDR_PANE_ID"
    # RECORD BEFORE ACT, then RELEASE on failure — the same discipline the emit path uses at the
    # reservation above, for the same reason: a held reservation after a failed delivery would
    # silence this channel for the rest of the window.
    if not reserve(home, session_id, window, "directive", INSERT_CHANNEL):
        return "skipped: already inserted for this window"
    argv = [sys.executable,
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "launcher_lib.py"),
            "insert-prompt", "--pane", pane, "--text", insert_prose(used, window)]
    try:
        run = runner or _insert_runner
        completed = run(argv, INSERT_TIMEOUT_S)
        code = getattr(completed, "returncode", 1)
    except Exception as exc:                      # pylint: disable=broad-except
        release(home, session_id, window, "directive", INSERT_CHANNEL)
        return f"failed: {type(exc).__name__}"
    if code != 0:
        release(home, session_id, window, "directive", INSERT_CHANNEL)
        return f"failed: launcher exit {code}"
    return "inserted"


def _insert_runner(argv, timeout):
    """The one place this module shells out. Isolated so tests never need herdr.

    The module docstring's older claim that it deliberately uses no subprocess is narrowed by #718,
    not forgotten: one call, on the `Stop` path, at the directive tier, once per session per window.
    """
    import subprocess                            # pylint: disable=import-outside-toplevel
    return subprocess.run(argv, capture_output=True, text=True, check=False,
                          shell=False, timeout=timeout)


def read_pointer(workspace_root, project, session_id):
    """The step-state pointer, but ONLY if it belongs to THIS session.

    Another session's pointer is not evidence about this one.
    """
    if not workspace_root or not project or not _PROJECT_RE.match(str(project)):
        return None
    path = os.path.join(workspace_root, "claude_docs", "wal",
                        f"{project}.state.json")
    record = _load_json_capped(path)
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
    """ONE shape for every event: nested, with the firing event's own name (#713).

    The earlier per-event branch sent the TOP-LEVEL form on `UserPromptSubmit`, recorded as
    verified live 2026-07-28. Probes 14 and 14b (docs/planning/2026-07-29-713-probes/)
    measured the opposite on Claude Code 2.1.220: registered ALONE, with no sibling hook to
    confound the merge, a top-level `additionalContext` on that event is **silently
    ignored** — the probing model replied `NONE`. The official guide says so outright ("if
    you place it at the top level of the JSON, Claude Code silently ignores it"). So that
    arm had never delivered anything since #687, and the meter had been running on
    `PostToolUse` alone.

    The nested form is CONFIRMED delivered on all three events the meter uses:
    `PostToolUse` (#687 probe 9), `UserPromptSubmit` (probe 14) and `Stop` (probes 11, 12).
    """
    return {"hookSpecificOutput": {"hookEventName": event,
                                   "additionalContext": text}}


def nag_text(*, tier, used, window, provenance, seam, seam_reason,
             unattended, fresh_handoff_capable, herdr_available=False):
    """The injected advisory. Contains ONLY integers and the pointer's own
    fields — never any transcript content, which would leak the very context it
    is measuring. Pure: env is read at the caller (#732 `herdr_available`
    follows the existing unattended/fresh_handoff_capable pattern).

    `unattended` comes from the DECLARED supervision state since #943 — it was
    `headless`, read from an env var that said only present-or-absent."""
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

    # AC4 (#713) — the line that was missing when a real run read "LOOP until DONE" as "do
    # not hand off" and burned its whole window. It is phrased as a FACT about how goals are
    # judged, not as an order, because probe 12 measured what happens to orders injected
    # here: the model named the injected imperative as a possible prompt injection and
    # declined to act on it, correctly, while still reporting it. State survives that
    # defence; commands do not. And the fact is true — probe 15 armed a real /goal whose
    # condition said a recorded handoff satisfies it, and the evaluator returned exactly
    # that verdict, ending the loop instead of demanding another work turn.
    lines.append(
        "A handoff SATISFIES a LOOP goal: the work continues in a FRESH session with a "
        "full window, so handing off does not stop the work — it relocates it. "
        "\"Do not stop\" is not \"do not hand off\"."
    )

    if tier == "advisory":
        # AC5 — the old wording ("start looking for a safe seam") named no deliverable, so a
        # session searched and wrote nothing. The point of this tier is that there is still
        # ROOM to write a good handoff; at 98% there is not.
        lines.append(
            "Write the resume prompt NOW, while there is room to write a good one, and "
            "verify the delivery gates. Do NOT stop mid-phase — but do not put off the "
            "writing either: this tier exists because the room to do it well disappears."
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

    if unattended and fresh_handoff_capable:
        lines.append(
            "Unattended with an armed launcher: checkpoint and hand over via "
            "`launcher_lib.py handoff` — do not wait for a human."
        )
    elif unattended and herdr_available:
        # AC4 (#732) — "stop cleanly for a manual resume" means stop with nobody to
        # continue (the overnight failure #713 documented). With a herdr pane available
        # there IS a successor to spawn into, so prefer the route that actually hands over.
        lines.append(
            "Unattended with NO durable launcher armed, but a herdr pane is "
            "available: run `/rawgentic:pane-handoff` — it wraps `clear-prep` and "
            "then actually hands over, spawning and binding the successor. "
            "`clear-prep` ALONE leaves no successor."
        )
    elif unattended:
        lines.append(
            "Unattended, but NO durable launcher is armed, so there is nothing "
            "to relaunch you: run the `clear-prep` skill to write the durable "
            "checkpoint and handoff, then stop cleanly for a manual resume."
        )
    elif tier == "directive":
        # AC3 + AC6 (#713) — name the route that actually HANDS OFF. `clear-prep` produces
        # the payload but neither clears this session's guard nor starts a successor, so a
        # session that obeyed the old text perfectly still stopped with nobody to continue:
        # that is exactly what a real overnight run did, writing its handoff and then waiting
        # for a human. State the chain, and name its end.
        lines.append(
            "Run `/rawgentic:pane-handoff`: it wraps `clear-prep` (the mempalace "
            "checkpoint, the durable handoff file, the resume prompt and the /goal text) "
            "and then actually hands over — it spawns the successor, binds it, delivers the "
            "prompt, arms its goal, and clears this session's guard. `clear-prep` ALONE "
            "leaves no successor. The handoff carries `next actions, in order` — the "
            "successor rebuilds its task list from those via /tasklist."
        )
    else:
        # AC1-AC3 (#732) — the advisory tier said "run clear-prep", and a session that
        # obeyed it literally built every artifact and stopped with no successor: that is
        # compliance, not misreading. #713 fixed the directive branch above and left this
        # one carrying the bug it had just diagnosed. Same route as directive, softer
        # TIMING ("at the next clean seam" vs "Break NOW") — the tiers decide WHEN to
        # hand off, never WHETHER.
        lines.append(
            "Run `/rawgentic:pane-handoff` at the next clean seam: it wraps "
            "`clear-prep` (the mempalace checkpoint, the durable handoff file, the "
            "resume prompt and the /goal text) and then actually hands over — it "
            "spawns the successor, binds it, delivers the prompt, arms its goal, and "
            "clears this session's guard. `clear-prep` ALONE leaves no successor."
        )
    return " ".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _warn(message):
    # Control characters are stripped: a hostile directory component could otherwise
    # forge log lines or drive terminal escapes through captured hook stderr
    # (#687 Step-11 review, Low).
    print("".join(ch for ch in str(message) if ch == " " or ch.isprintable()),
          file=sys.stderr)


def _is_subagent(payload) -> bool:
    """A subagent has its own short-lived context and no authority to hand over
    its parent's session, so the meter does nothing for one.

    VERIFIED (probe 10, 2026-07-28) — this was a documented guess until a real
    subagent was driven under a payload-dumping hook. A subagent's `PostToolUse`
    payload carries **`agent_id`** ("a8827c81c4105e67c") and **`agent_type`**
    ("general-purpose"); the parent's own `Agent` tool call carries NEITHER. And the
    subagent's `session_id` is IDENTICAL to the parent's — which is exactly why the
    guard is needed: without it a subagent's tool calls would read the parent's
    transcript and advance the parent's cadence.

    The extra key names are harmless belt-and-braces for other Claude Code versions;
    absent all of them the branch is inert, so it cannot break the hook.
    """
    if not isinstance(payload, dict):
        return False
    for key in ("agent_id", "agent_type", "agentId", "subagent_id",
                "isSidechain", "is_sidechain"):
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
        # Byte-capped: a huge tool_response should not be able to make a hook that runs on
        # every tool call chew its 5 s timeout (#687 Step-11 review).
        payload = json.loads(sys.stdin.read(MAX_STDIN_BYTES))
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
    channel = channel_for(event)

    # THE STOP GATE (#713). At `Stop` there is NO read-only channel to the model: both
    # `decision: block` and `additionalContext` continue the conversation (hooks.md:2271,
    # measured by probe 11 — the hook fired twice and the session took two assistant turns
    # for one prompt). So emitting when nothing else is continuing would force a turn the
    # user never asked for, turning this convenience nag into a turn-blocker.
    #
    # `stop_hook_active: true` means a Stop hook already continued this turn — in practice a
    # `/goal` loop, which is exactly the situation this arm exists for, and where the turn
    # was continuing anyway. Probe 12 confirmed it is true from the second Stop onward in a
    # real goal loop, driven by /goal's own continuations.
    #
    # Deliberately `is not True`, not a truthy test: staying silent is the safe direction, so
    # an unexpected shape (a string, a missing key) declines to speak rather than guessing.
    #
    # NOTE the documented idiom (hooks-guide.md:955-964) checks this same field and exits
    # early when it is TRUE. That is for gates whose job is to FORCE convergence and which
    # must not loop. This is the opposite shape — a once-per-tier nag that must not START a
    # loop — so it exits early when the field is FALSE. It cannot loop either way: the
    # marker bounds it to one emission per tier per window per channel.
    if event == "Stop" and payload.get("stop_hook_active") is not True:
        return 0

    if event == "UserPromptSubmit":
        state["turns"] = (_as_int(state.get("turns")) or 0) + 1

    env = os.environ
    now = int(time.time())
    # The throttle uses cadence CACHED in state, so the cheap path costs one small JSON
    # read and two integer compares — no workspace walk, no config read, no transcript
    # open. It rides every tool call, so this matters.
    every_turns = _as_int(state.get("every_turns")) or DEFAULT_EVERY_TURNS
    every_seconds = (_as_int(state.get("every_seconds"))
                     or DEFAULT_EVERY_SECONDS)
    # `Stop` is EXEMPT from the cadence throttle (#713). It fires once per turn, so it is
    # already low-frequency — unlike PostToolUse, which rides every tool call and is what the
    # throttle exists to tame. Throttling it would mean a PostToolUse check moments earlier
    # could suppress the meter at the one moment the whole arm exists to speak at, which is
    # the mistiming this issue is about.
    if event != "Stop" and not should_check(state, now=now, every_turns=every_turns,
                                            every_seconds=every_seconds):
        # Write ONLY when there is something to record. A throttled PostToolUse changes no
        # state, so writing would mean an atomic rewrite + rename + chmod + a directory
        # sweep on every covered tool call — which is not the cheap path this claims to be
        # (#687 Step-11 review). UserPromptSubmit did bump `turns`, so it still persists.
        if event == "UserPromptSubmit":
            save_state(home, session_id, state)
        return 0

    workspace = find_workspace(payload.get("cwd"))
    project, project_path = (bound_project(workspace, session_id)
                             if workspace else (None, None))
    if workspace and project is None:
        # Visible degradation, not a silent one: without the bound project there is no
        # config, so the window falls back to the conservative default and the nag may
        # fire early. Say so once rather than letting it look like a real reading.
        _diagnose(state, "session_unbound",
                  "this session is not bound to a project in "
                  "claude_docs/session_registry.jsonl (or its row lies beyond the scan "
                  "bound), so contextMeter config is unavailable and the conservative "
                  f"default {DEFAULT_WINDOW:,}-token window is assumed")
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
                  f"no parseable message.usage row in {session_id}.jsonl "
                  "— the meter is inactive for this session")
        save_state(home, session_id, state)
        return 0

    window, provenance = resolve_window(cfg.get("windowSize"),
                                        env.get("RAWGENTIC_CONTEXT_WINDOW"),
                                        used, warn=_warn)
    state["assumed_window"] = window
    state["window_provenance"] = provenance
    tier = tier_for_tokens(used, window, check_in_pct, act_pct)

    if tier == "none":
        # Pressure fell back below the advisory band, so DROP any armed seam search: a
        # snapshot taken at the previous crossing would otherwise be compared against a
        # much later pointer and report a long-past transition as "the current seam"
        # (#687 Step-11 review).
        state.pop("seam_search", None)
        save_state(home, session_id, state)
        return 0

    # DIRECTIVE TIER ONLY at `Stop` (#713). Emitting there costs at most one extra turn (see
    # the gate above), and the two tiers do not deserve that cost equally: at the check-in
    # tier an extra forced turn buys nothing, while at the act tier the extra turn IS the
    # handoff this issue exists to produce. The advisory tier stays mid-turn, where it is
    # free.
    if event == "Stop" and tier != "directive":
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
    if has_marker(home, session_id, window, tier, channel) or (
            tier == "advisory"
            and has_marker(home, session_id, window, "directive", channel)):
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
    # #943: the DECLARED supervision state, not an env var. `workspace` was already
    # resolved above, so this costs one capped read and no second walk — and it lands
    # here, on the emit path, so the common no-nag tool call still pays nothing.
    unattended = supervision_lib.nobody_to_ask(
        supervision_lib.evaluate_workspace(
            supervision_lib.read_state(workspace),
            now=datetime.now(timezone.utc)))
    fresh_handoff_capable = (env.get("RAWGENTIC_LAUNCHER_ARMED") == "1"
                             and env.get("RAWGENTIC_FRESH_LAUNCH_SUPPORTED") == "1")
    herdr_available = env.get("HERDR_ENV") == "1"
    try:
        text = nag_text(tier=tier, used=used, window=window,
                        provenance=provenance, seam=seam,
                        seam_reason=seam_reason, unattended=unattended,
                        fresh_handoff_capable=fresh_handoff_capable,
                        herdr_available=herdr_available)
        payload_out = json.dumps(emit_payload(event, text))
    except Exception:
        save_state(home, session_id, state)
        return 0

    # RECORD BEFORE EMIT (security-guard-check.sh:49-55). The marker create IS
    # the record: it lands first, and the very next statement delivers. If
    # delivery still fails, RELEASE the reservation — holding it would silence
    # this tier for the rest of the session, which is the defect the reservation
    # exists to prevent, merely relocated.
    if not reserve(home, session_id, window, tier, channel):
        # A reservation can fail two ways: another process won the race (correct, stay
        # silent) or the store is unwritable. The second is a SELF-DISABLING failure and
        # the module contract says those are never invisible — so it warns. This is the
        # ONE diagnostic that may repeat: its once-per-session record lives in the very
        # store that is broken, and a store that cannot remember cannot deduplicate.
        # Stated in the docstring rather than quietly excepted (adversarial diff review).
        if not os.path.isdir(state_dir(home)):
            _warn("context_meter: state directory is unusable, so the meter cannot "
                  "record what it has already said — no nag will be emitted. This "
                  "warning repeats until storage recovers.")
        save_state(home, session_id, state)
        return 0
    try:
        print(payload_out)
        sys.stdout.flush()
    except Exception:
        release(home, session_id, window, tier, channel)
        return 0

    # #718 — INSERT A PROMPT, in ADDITION to the emit above and only after it succeeded. Injected
    # hook text is DATA the model may decline (probe 12: it named the directive as possible prompt
    # injection and refused its imperative while faithfully reporting it); an inserted prompt
    # arrives as USER input, the one channel treated as authoritative. Additive on purpose: if the
    # insert fails, the text has still been delivered the old way.
    #
    # `Stop` only. Never mid-turn, and never ESC — at `Stop` the turn has ended and nothing is in
    # flight, whereas an ESC mid-turn can kill a running suite or a half-finished commit.
    if event == "Stop" and tier == "directive":
        outcome = try_insert_prompt(home=home, session_id=session_id, window=window, used=used,
                                    cfg=cfg, project_path=project_path, env=env)
        # NEVER SILENT about not having acted (Step-11 diff review, Medium). This module's contract
        # is fail-open *but* visible, and discarding the outcome made "disabled", "herdr wedged" and
        # "inserted" indistinguishable — leaving an operator unable to tell why only the refusable
        # text channel fired. `inserted` stays quiet: that one is self-evidencing, because the
        # prompt itself shows up in the session.
        if not outcome.startswith("inserted"):
            _warn(f"context_meter: prompt insertion did not happen — {outcome}. The directive was "
                  "still delivered as injected text, which a model may decline to act on.")

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
                      "tier": tier_for_tokens(used, window, check_in_pct, act_pct),
                      "provenance": provenance, "transcript": transcript},
                     sort_keys=True))
    return 0


def cmd_validate_config(args) -> int:
    """Exit 0 and print `ok`, or print every reason on stderr and exit 2 (#701).

    Fails CLOSED, unlike the hook paths in this module: what it gates is whether setup stages a
    block, and staging one the hook will discard is the failure mode #701 exists to remove.
    """
    # BROAD except, and it is load-bearing rather than lazy (#701 Step-11 diff review, High).
    # `__main__` in this module deliberately swallows every exception and exits 0, because a
    # PostToolUse hook must never block a turn — so ANY exception escaping here is reported to setup
    # as SUCCESS, and setup stages a block nothing validated. Catching only `ValueError` left that
    # open: `json.loads` raises RecursionError (a RuntimeError, not a ValueError) on deeply nested
    # input, verified live at 100k nesting. Whatever goes wrong, the answer is a refusal.
    try:
        block = json.loads(args.json_block)
        errors = validate_setup_block(block)
    except Exception as exc:  # pylint: disable=broad-except
        print(f"validate-config: could not validate the block ({type(exc).__name__}: {exc})",
              file=sys.stderr)
        return 2
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 2
    print("ok")
    return 0


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("hook", help="read a hook payload as JSON on stdin")
    p_read = sub.add_parser("read", help="print the current reading as JSON")
    p_read.add_argument("--session-id", default=None)
    p_read.add_argument("--transcript", default=None)
    # #701 — the validate-config shape retired setup blocks also used:
    # exit 0 = stage it, non-zero = show stderr and
    # re-offer. Never stage a block this refuses.
    p_val = sub.add_parser("validate-config",
                           help="validate a contextMeter block for setup (#701)")
    p_val.add_argument("--json", dest="json_block", required=True)
    if not argv:
        argv = ["hook"]
    args = parser.parse_args(argv)
    if args.cmd == "validate-config":
        return cmd_validate_config(args)
    if args.cmd == "read":
        return cmd_read(args)
    return cmd_hook(args)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:          # fail-OPEN: never block a turn
        sys.exit(0)
