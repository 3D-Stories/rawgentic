"""#638 Step-11 pass 7 — the TRI-STATE seam (`Liveness`).

Seven review passes each found the same class of bug: an OPERATIONAL failure read as a
definitive answer. Root cause was structural — every probe returned a `CompletedProcess`
whose `returncode` conflates "confirmed gone" with "couldn't tell", so each call site
guessed, and each guess-site fixed in isolation created a new wrong guess nearby.

Every tmux stderr string asserted here was OBSERVED from the real pinned binary (pass-7 live
probe), not inferred. The sharp edge is `error connecting to <sock>`: it splits on its
PARENTHETICAL -- `(No such file or directory)` means the socket does not exist, so no server
exists, so the session verifiably does not exist; `(Permission denied)` means we could not
look at all. Same prefix, OPPOSITE verdicts.
"""
import subprocess

import pytest

from phase_executor.terminal_backend import Liveness, TmuxBackend, classify_tmux_result


def _cp(rc, stderr="", stdout=""):
    return subprocess.CompletedProcess(["tmux"], rc, stdout, stderr)


# ---- the classifier, against REAL observed tmux output ----------------------

@pytest.mark.parametrize("stderr,expected", [
    # server up, session absent -> verifiably gone
    ("can't find session: rg-x", Liveness.CONFIRMED_GONE),
    # socket present, no tmux server -> no server means no session
    ("no server running on /tmp/x.sock", Liveness.CONFIRMED_GONE),
    # socket absent -> no server -> no session
    ("error connecting to /tmp/x.sock (No such file or directory)", Liveness.CONFIRMED_GONE),
    # THE trap: same prefix, but we could not look -> indeterminate
    ("error connecting to /tmp/x.sock (Permission denied)", Liveness.INDETERMINATE),
    # unrecognised / protocol / empty -> fail SAFE, never "dead"
    ("protocol version mismatch", Liveness.INDETERMINATE),
    ("", Liveness.INDETERMINATE),
])
def test_classify_tmux_nonzero(stderr, expected):
    assert classify_tmux_result(_cp(1, stderr)) is expected


def test_classify_tmux_zero_is_alive():
    assert classify_tmux_result(_cp(0)) is Liveness.CONFIRMED_ALIVE


# ---- TmuxBackend's tri-state, exercised through the REAL backend ------------
# Pass-7 finding: the earlier permit tests only used a StubBackend forced to return success,
# so they missed the real tmux case entirely.

def _tmux(responses):
    """`responses` maps the tmux verb -> CompletedProcess."""
    def run(cmd, *, env=None, timeout=30):
        verb = cmd[3] if len(cmd) > 3 else cmd[-1]
        return responses[verb]
    return TmuxBackend(run=run)


def test_close_session_absent_session_is_confirmed_gone():
    # THE regression (High, pass 7): a real `kill-session` against an already-absent
    # session/server exits NONZERO with an absence message. Reading that as an unconfirmed
    # teardown made every ORDINARY spawn refusal pin a quota permit until the process exited
    # -- two refusals could exhaust the Claude pool.
    be = _tmux({"kill-session": _cp(1, "can't find session: rg-x")})
    assert be.close_session("/tmp/s.sock", "rg-x") is Liveness.CONFIRMED_GONE


def test_close_session_no_server_is_confirmed_gone():
    be = _tmux({"kill-session": _cp(1, "no server running on /tmp/s.sock")})
    assert be.close_session("/tmp/s.sock", "rg-x") is Liveness.CONFIRMED_GONE


def test_close_session_operational_error_is_indeterminate():
    # ...but a genuine operational failure must NOT count as teardown.
    be = _tmux({"kill-session": _cp(1, "error connecting to /tmp/s.sock (Permission denied)")})
    assert be.close_session("/tmp/s.sock", "rg-x") is Liveness.INDETERMINATE


def test_close_session_success_is_confirmed_gone():
    be = _tmux({"kill-session": _cp(0)})
    assert be.close_session("/tmp/s.sock", "rg-x") is Liveness.CONFIRMED_GONE


def test_probe_session_permission_error_is_not_death():
    # THE third finding: a LIVE tmux server whose socket returns an operational error used to
    # reduce to "dead" -- releasing the permit without kill verification, or dropping the
    # record out of reap()'s live_names so its still-live process entered kill_tree.
    be = _tmux({"has-session": _cp(1, "error connecting to /tmp/s.sock (Permission denied)")})
    assert be.probe_session("/tmp/s.sock", "rg-x") is Liveness.INDETERMINATE


def test_probe_session_absent_is_confirmed_gone():
    be = _tmux({"has-session": _cp(1, "can't find session: rg-x")})
    assert be.probe_session("/tmp/s.sock", "rg-x") is Liveness.CONFIRMED_GONE


def test_probe_session_alive():
    be = _tmux({"has-session": _cp(0)})
    assert be.probe_session("/tmp/s.sock", "rg-x") is Liveness.CONFIRMED_ALIVE


def test_enumerate_confirmed_empty_is_reliable_not_excluded():
    # tmux's routine idle-socket answer is a RELIABLE empty enumeration -- those records must
    # keep flowing through the ordinary dead-job sweep, never be excluded from it.
    be = _tmux({"list-sessions": _cp(1, "no server running on /tmp/s.sock")})
    verdict, names = be.enumerate_sessions("/tmp/s.sock")
    assert verdict is Liveness.CONFIRMED_GONE and names == []


def test_enumerate_operational_failure_is_indeterminate():
    be = _tmux({"list-sessions": _cp(1, "error connecting to /tmp/s.sock (Permission denied)")})
    verdict, names = be.enumerate_sessions("/tmp/s.sock")
    assert verdict is Liveness.INDETERMINATE and names == []


def test_enumerate_alive_returns_names():
    be = _tmux({"list-sessions": _cp(0, stdout="a\nb\n")})
    verdict, names = be.enumerate_sessions("/tmp/s.sock")
    assert verdict is Liveness.CONFIRMED_ALIVE and names == ["a", "b"]


def test_raising_runner_is_always_indeterminate():
    def boom(cmd, *, env=None, timeout=30):
        raise TimeoutError("stub: tmux timed out")

    be = TmuxBackend(run=boom)
    assert be.probe_session("/tmp/s.sock", "x") is Liveness.INDETERMINATE
    assert be.close_session("/tmp/s.sock", "x") is Liveness.INDETERMINATE
    assert be.enumerate_sessions("/tmp/s.sock")[0] is Liveness.INDETERMINATE


# ---- Step-11 pass 8: the `no sessions` absence diagnostic --------------------

def test_no_sessions_is_confirmed_gone():
    # tmux 3.4's exact diagnostic for an EMPTY but still-running server (confirmed present in
    # the installed binary via `strings`), reachable when a user's config sets `exit-empty off`.
    # Missing it classified an ORDINARY absent target as INDETERMINATE, which holds permits and
    # excludes records from the sweep.
    assert classify_tmux_result(_cp(1, "no sessions")) is Liveness.CONFIRMED_GONE
    assert classify_tmux_result(_cp(1, "no sessions\n")) is Liveness.CONFIRMED_GONE


def test_no_sessions_pattern_is_anchored():
    # ...but it must not swallow a longer, unrelated message.
    assert classify_tmux_result(
        _cp(1, "no sessions available for the widget")) is Liveness.INDETERMINATE


def test_no_sessions_through_the_backend_surfaces():
    be = _tmux({"list-sessions": _cp(1, "no sessions")})
    verdict, names = be.enumerate_sessions("/tmp/s.sock")
    assert verdict is Liveness.CONFIRMED_GONE and names == []
    be2 = _tmux({"has-session": _cp(1, "no sessions")})
    assert be2.probe_session("/tmp/s.sock", "x") is Liveness.CONFIRMED_GONE
    be3 = _tmux({"kill-session": _cp(1, "no sessions")})
    assert be3.close_session("/tmp/s.sock", "x") is Liveness.CONFIRMED_GONE


# ---- pass 10: an ALLOWLIST, so an unenumerated operational message cannot mean "gone" ----

@pytest.mark.parametrize("stderr,stdout", [
    ("Permission denied", "no sessions"),                 # pass-9 case: mixed streams
    ("Permission denied\nno sessions", ""),               # pass-9 case: mixed multi-line
    ("failed to send command", "can't find session: x"),  # PASS-10 case: real tmux diagnostic
    ("protocol version mismatch", "no sessions"),         # ...not on any denylist
    ("some tmux error nobody enumerated", "no sessions"),
])
def test_unrecognised_message_beside_an_absence_phrase_is_indeterminate(stderr, stdout):
    """The pass-9 fix used a DENYLIST of operational phrases, which could never be airtight:
    `failed to send command` is a real tmux diagnostic that was not on it, so pairing it with an
    absence phrase still classified CONFIRMED_GONE. Pass 10 inverts it — every non-empty stream
    must match a known absence diagnostic IN FULL — so an unenumerated message can only ever
    make the result indeterminate, never "dead"."""
    res = subprocess.CompletedProcess(["tmux"], 1, stdout, stderr)
    assert classify_tmux_result(res) is Liveness.INDETERMINATE


def test_nonzero_with_no_message_at_all_is_indeterminate():
    assert classify_tmux_result(_cp(1, "")) is Liveness.INDETERMINATE


def test_absence_in_every_nonempty_stream_is_confirmed_gone():
    res = subprocess.CompletedProcess(["tmux"], 1, "no sessions", "can't find session: x")
    assert classify_tmux_result(res) is Liveness.CONFIRMED_GONE


# ---- pass-10 self-audit: absence messages may contain SPACES -----------------

@pytest.mark.parametrize("stderr", [
    # every one of these was produced by the real pinned binary during a live probe
    "can't find session: has space",
    "can't find session: a b c",
    "can't find session: we'ird",
    "can't find session",                                   # no name at all
    "no server running on /tmp/tm x.8CCa/s p.sock",         # socket PATH with spaces
    "error connecting to /tmp/tm x.8CCa/s p.sock (No such file or directory)",
])
def test_absence_messages_with_spaces_still_classify_gone(stderr):
    """Found by self-audit before the reviewer's report landed: the first allowlist used `\\S+`
    for the variable parts, so a tmux SESSION NAME or SOCKET PATH containing spaces made an
    ORDINARY absence message classify INDETERMINATE. That direction of miss is not harmless --
    it holds quota permits and excludes records from the sweep indefinitely."""
    assert classify_tmux_result(_cp(1, stderr)) is Liveness.CONFIRMED_GONE


def test_multiline_body_still_cannot_whole_match_despite_dot():
    # `.` excludes newlines (no DOTALL), so widening to `.*` did not reopen the mixed-body hole
    assert classify_tmux_result(
        _cp(1, "can't find session: x\nPermission denied")) is Liveness.INDETERMINATE
