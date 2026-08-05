"""Supervision declaration writer — locked, validated, fail-LOUD (#943 Part A).

Fail mode is deliberately the OPPOSITE of `supervision_lib`'s read path. A read that
quietly degrades is correct, because it rides a hook on every tool call. A WRITE that
quietly fails is not: it would leave a session believing the owner is recorded as away
when nothing was written, which is precisely the false belief the state file exists to
prevent. So every refusal here is loud, and nothing is written on a refusal.
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOKS = REPO_ROOT / "hooks"
sys.path.insert(0, str(HOOKS))

import supervision_admin as sa  # noqa: E402
import supervision_lib as sl  # noqa: E402

NOW = datetime(2026, 8, 5, 21, 0, 0, tzinfo=timezone.utc)
LATER = NOW + timedelta(hours=2)
EARLIER = NOW - timedelta(hours=2)


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _read(root):
    return json.loads(Path(sl.supervision_path(str(root))).read_text())


def _declare(root, **kw):
    kw.setdefault("state", "away")
    kw.setdefault("until", None)
    kw.setdefault("session_id", "sess-1")
    kw.setdefault("campaign_ids", [])
    kw.setdefault("consult_providers", ["gpt"])
    kw.setdefault("consult_granted", True)
    kw.setdefault("now", NOW)
    return sa.declare(str(root), **kw)


# ------------------------------------------------------------------ happy path

def test_declare_writes_the_file_at_revision_one(tmp_path):
    rec = _declare(tmp_path, state="away", until=_iso(LATER))
    assert rec["revision"] == 1
    on_disk = _read(tmp_path)
    assert on_disk["state"] == "away"
    assert on_disk["until"] == _iso(LATER)
    assert on_disk["declared_by_session"] == "sess-1"
    assert on_disk["consult_grant"] == {"providers": ["gpt"], "granted": True}
    assert on_disk["schema_version"] == 1
    assert on_disk["declared_at"]


def test_the_written_file_reads_back_as_valid(tmp_path):
    """The writer and the reader must agree on the schema — they share one validator."""
    _declare(tmp_path, state="sleeping", until=_iso(LATER))
    loaded = sl.read_state(str(tmp_path))
    assert loaded.load_status == "valid"
    view = sl.evaluate_workspace(loaded, now=NOW)
    assert view.state == "sleeping"
    assert sl.installs_forbidden(view) is True


def test_revision_increments_on_every_write(tmp_path):
    assert _declare(tmp_path)["revision"] == 1
    assert _declare(tmp_path)["revision"] == 2
    assert _declare(tmp_path, state="attended")["revision"] == 3


def test_mark_attended_clears_the_absence_and_the_grant(tmp_path):
    _declare(tmp_path, state="sleeping", until=_iso(LATER))
    rec = sa.mark_attended(str(tmp_path), session_id="sess-2",
                           reason="owner replied", expected_revision=1, now=NOW)
    assert rec["state"] == "attended"
    assert rec["until"] is None
    assert rec["revision"] == 2
    assert rec["consult_grant"]["granted"] is False, (
        "a consult grant must not outlive the absence it was given for")
    view = sl.evaluate_workspace(sl.read_state(str(tmp_path)), now=NOW)
    assert sl.installs_forbidden(view) is False


def test_declare_creates_the_parent_directory(tmp_path):
    root = tmp_path / "fresh-workspace"
    root.mkdir()
    _declare(root)
    assert Path(sl.supervision_path(str(root))).is_file()


# -------------------------------------------------------------- refusals (loud)

@pytest.mark.parametrize("kw, needle", [
    ({"state": "sleeping", "until": None}, "until"),
    ({"state": "attended", "until": _iso(LATER)}, "attended"),
    ({"state": "away", "until": _iso(EARLIER)}, "past"),
    ({"state": "away", "until": "tomorrow"}, "ISO"),
    ({"state": "napping"}, "state"),
])
def test_invalid_declarations_are_refused_before_any_write(tmp_path, kw, needle):
    with pytest.raises(sa.DeclarationRefused) as exc:
        _declare(tmp_path, **kw)
    assert needle.lower() in str(exc.value).lower()
    assert not Path(sl.supervision_path(str(tmp_path))).exists(), (
        "a refused declaration must write nothing at all")


def test_unknown_consult_provider_is_refused(tmp_path):
    with pytest.raises(sa.DeclarationRefused) as exc:
        _declare(tmp_path, consult_providers=["gpt", "hal9000"])
    assert "hal9000" in str(exc.value)
    assert not Path(sl.supervision_path(str(tmp_path))).exists()


@pytest.mark.parametrize("bad", ["../escape", "a/b", "", "x" * 200])
def test_unsafe_campaign_id_is_refused(tmp_path, bad):
    with pytest.raises(sa.DeclarationRefused):
        _declare(tmp_path, campaign_ids=[bad])
    assert not Path(sl.supervision_path(str(tmp_path))).exists()


def test_declare_refuses_an_unresolvable_workspace_root(tmp_path):
    with pytest.raises(sa.DeclarationRefused):
        _declare(tmp_path / "nope")


# ------------------------------------------------------ revision fencing

def test_stale_expected_revision_aborts_rather_than_clobbering(tmp_path):
    """The optimistic-concurrency fence.

    A write computed against a stale read must not overwrite a fresher declaration —
    otherwise a long-running caller silently reverts someone else's `/back`.
    """
    _declare(tmp_path)                      # revision 1
    _declare(tmp_path)                      # revision 2
    with pytest.raises(sa.RevisionMismatch) as exc:
        _declare(tmp_path, expected_revision=1)
    assert "2" in str(exc.value), "the error must report the revision actually found"
    assert _read(tmp_path)["revision"] == 2, "the fresher record must survive intact"


def test_matching_expected_revision_is_accepted(tmp_path):
    _declare(tmp_path)
    rec = _declare(tmp_path, expected_revision=1)
    assert rec["revision"] == 2


def test_expected_revision_zero_means_the_file_must_not_exist_yet(tmp_path):
    rec = _declare(tmp_path, expected_revision=0)
    assert rec["revision"] == 1
    with pytest.raises(sa.RevisionMismatch):
        _declare(tmp_path, expected_revision=0)


def test_mark_attended_fences_on_revision_too(tmp_path):
    """A correlated owner reply that arrives late must not clear a NEWER absence."""
    _declare(tmp_path, state="away")                     # revision 1
    _declare(tmp_path, state="sleeping", until=_iso(LATER))   # revision 2
    with pytest.raises(sa.RevisionMismatch):
        sa.mark_attended(str(tmp_path), session_id="s", reason="late reply",
                         expected_revision=1, now=NOW)
    assert _read(tmp_path)["state"] == "sleeping"


# ----------------------------------------------------------------- atomicity

def test_no_stray_temp_files_are_left_behind(tmp_path):
    _declare(tmp_path)
    _declare(tmp_path)
    names = os.listdir(os.path.dirname(sl.supervision_path(str(tmp_path))))
    strays = [n for n in names
              if n != ".supervision.json" and not n.endswith(".lock")]
    assert strays == [], f"stray temp file(s): {strays}"


def test_a_write_failure_is_loud_and_not_silent(tmp_path):
    """An unwritable target must raise, never return as if it had written."""
    _declare(tmp_path)
    docs = Path(os.path.dirname(sl.supervision_path(str(tmp_path))))
    os.chmod(docs, 0o500)
    try:
        if os.geteuid() == 0:
            pytest.skip("root ignores directory permissions")
        with pytest.raises(Exception):
            _declare(tmp_path)
    finally:
        os.chmod(docs, 0o700)


def test_concurrent_writers_keep_revisions_monotonic(tmp_path):
    """The lock is held across the WHOLE read-modify-write cycle, so N writers land N
    increments and never a torn file."""
    n = 6
    procs = [
        subprocess.Popen(
            [sys.executable, str(HOOKS / "supervision_admin.py"), "declare",
             "--workspace", str(tmp_path), "--state", "away",
             "--session-id", f"sess-{i}"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        for i in range(n)
    ]
    for p in procs:
        p.communicate()
        assert p.returncode == 0

    record = _read(tmp_path)                     # must still be valid JSON
    assert record["revision"] == n
    assert sl.read_state(str(tmp_path)).load_status == "valid"


# ----------------------------------------------------------------------- CLI

def _cli(*args):
    return subprocess.run(
        [sys.executable, str(HOOKS / "supervision_admin.py"), *args],
        capture_output=True, text=True)


def test_cli_declare_prints_json_on_stdout_and_a_human_line_on_stderr(tmp_path):
    r = _cli("declare", "--workspace", str(tmp_path), "--state", "sleeping",
             "--until", _iso(LATER), "--session-id", "sess-cli",
             "--campaign", "epic-871-m4-wave", "--provider", "gpt", "--granted")
    assert r.returncode == 0
    payload = json.loads(r.stdout)
    assert payload["state"] == "sleeping"
    assert payload["revision"] == 1
    assert r.stderr.strip(), "the operator-visible confirmation goes to stderr"
    assert _read(tmp_path)["governed_campaign_ids"] == ["epic-871-m4-wave"]


def test_cli_refusal_exits_1_loudly(tmp_path):
    r = _cli("declare", "--workspace", str(tmp_path), "--state", "sleeping",
             "--session-id", "s")           # sleeping with no --until
    assert r.returncode == 1
    assert "until" in r.stderr.lower()
    assert not Path(sl.supervision_path(str(tmp_path))).exists()


def test_cli_mark_attended_round_trip(tmp_path):
    _cli("declare", "--workspace", str(tmp_path), "--state", "away",
         "--session-id", "s")
    r = _cli("mark-attended", "--workspace", str(tmp_path), "--session-id", "s2",
             "--reason", "owner texted back", "--expected-revision", "1")
    assert r.returncode == 0
    assert json.loads(r.stdout)["state"] == "attended"


def test_cli_stale_revision_exits_1(tmp_path):
    _cli("declare", "--workspace", str(tmp_path), "--state", "away",
         "--session-id", "s")
    _cli("declare", "--workspace", str(tmp_path), "--state", "away",
         "--session-id", "s")
    r = _cli("declare", "--workspace", str(tmp_path), "--state", "away",
             "--session-id", "s", "--expected-revision", "1")
    assert r.returncode == 1
    assert "revision" in r.stderr.lower()


def test_cli_missing_required_argument_is_a_usage_error(tmp_path):
    assert _cli("declare", "--workspace", str(tmp_path)).returncode == 2
