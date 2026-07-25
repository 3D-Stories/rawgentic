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


# ---- pass 9: operational evidence must WIN over a co-occurring absence phrase ----

@pytest.mark.parametrize("stderr", [
    "Permission denied\nno sessions",          # THE pass-9 bug: re.M matched line 2 -> "gone"
    "no sessions\nPermission denied",          # order must not matter
    "protocol version mismatch\nno sessions",
])
def test_operational_message_beside_an_absence_phrase_is_indeterminate(stderr):
    # `no sessions` is a short generic phrase, so it gets a WHOLE-MESSAGE match, and any
    # operational indicator takes PRECEDENCE by construction -- not by ordering luck. Without
    # this, a live server behind a permission error classified as dead: exactly the
    # operational-failure-as-death class the tri-state exists to eliminate.
    assert classify_tmux_result(_cp(1, stderr)) is Liveness.INDETERMINATE


def test_absence_in_one_stream_beside_an_operational_error_in_the_other_is_indeterminate():
    # the classifier used to CONCATENATE stderr+stdout, so a mixed pair could match
    res = subprocess.CompletedProcess(["tmux"], 1, "can't find session: x", "Permission denied")
    assert classify_tmux_result(res) is Liveness.INDETERMINATE


def test_no_sessions_on_stdout_only_still_classifies():
    res = subprocess.CompletedProcess(["tmux"], 1, "no sessions", "")
    assert classify_tmux_result(res) is Liveness.CONFIRMED_GONE
