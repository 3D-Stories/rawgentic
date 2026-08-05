"""Supervision state model — pure evaluation + fail-open read (#943 Part A).

Two safety properties carry this module, and each has its own test below:

1. The two predicates fail SAFE in OPPOSITE directions. `nobody_to_ask` (which only
   changes the wording of advice) treats an expired declaration as "a human is back";
   `installs_forbidden` (which authorizes a real, outward package install) keeps
   refusing until an explicit `/rawgentic:back`. Collapsing them into one boolean is
   the defect `test_expired_away_is_asymmetric_across_the_two_predicates` exists to
   catch.
2. Present-but-invalid is NOT absent. `ENOENT` under a valid root is the only *file*
   failure treated as absence; a supplied-but-unresolvable root is invalid too. Any
   other outcome would let a corrupt file or a caller misconfiguration silently unlock
   installs while the real workspace held an active away declaration.
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

import supervision_lib as sl  # noqa: E402

NOW = datetime(2026, 8, 5, 21, 0, 0, tzinfo=timezone.utc)
LATER = NOW + timedelta(hours=2)
EARLIER = NOW - timedelta(hours=2)


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _real_future(hours=2):
    """An `until` the REAL clock has not reached yet.

    `NOW` above is frozen, and every in-process call takes it as an injected clock, so
    those tests stay deterministic. A CLI subprocess cannot be handed that clock — it
    reads the real one — so a frozen future constant crossing into `_cli` expires for
    good the moment wall-clock time passes it. `LATER` is 2026-08-05T23:00:00Z, and
    three CLI tests here began failing permanently at that instant (#948). Anything
    time-sensitive that crosses into `_cli` is therefore built from the real clock.
    """
    return _iso(datetime.now(timezone.utc) + timedelta(hours=hours))


def _real_past(hours=2):
    """A deadline the REAL clock has already passed — the `_real_future` mirror."""
    return _iso(datetime.now(timezone.utc) - timedelta(hours=hours))


def _record(state="away", until=None, **kw):
    rec = {
        "schema_version": 1,
        "revision": 3,
        "state": state,
        "until": until,
        "declared_at": _iso(EARLIER),
        "declared_by_session": "sess-1",
        "governed_campaign_ids": [],
        "consult_grant": {"providers": ["gpt"], "granted": True},
    }
    rec.update(kw)
    return rec


def _write(root, record):
    p = Path(sl.supervision_path(str(root)))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(record))
    return p


def _view(root, now=NOW):
    return sl.evaluate_workspace(sl.read_state(str(root)), now=now)


# ---------------------------------------------------------------- path + read

def test_supervision_path_is_under_claude_docs(tmp_path):
    assert sl.supervision_path(str(tmp_path)) == os.path.join(
        str(tmp_path), "claude_docs", ".supervision.json")


def test_no_workspace_supplied_is_absent():
    for empty in (None, ""):
        loaded = sl.read_state(empty)
        assert loaded.load_status == "absent"
        assert loaded.record == {}


def test_supplied_but_unresolvable_root_is_invalid_not_absent(tmp_path):
    """A caller-configuration failure must NOT read as 'nobody declared anything'.

    Classing it as absence would let a path-resolution bug ALLOW installs while the
    real workspace held an active away declaration — a fail-safe property inverted by
    a config bug.
    """
    missing = tmp_path / "does-not-exist"
    assert sl.read_state(str(missing)).load_status == "invalid"


def test_root_that_is_a_file_not_a_directory_is_invalid(tmp_path):
    f = tmp_path / "afile"
    f.write_text("x")
    assert sl.read_state(str(f)).load_status == "invalid"


def test_missing_file_under_a_valid_root_is_absent(tmp_path):
    assert sl.read_state(str(tmp_path)).load_status == "absent"


def test_malformed_json_is_invalid(tmp_path):
    p = Path(sl.supervision_path(str(tmp_path)))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{not json")
    assert sl.read_state(str(tmp_path)).load_status == "invalid"


def test_non_object_json_is_invalid(tmp_path):
    _write(tmp_path, ["a", "list"])
    assert sl.read_state(str(tmp_path)).load_status == "invalid"


def test_off_vocabulary_state_is_invalid(tmp_path):
    _write(tmp_path, _record(state="wandering"))
    assert sl.read_state(str(tmp_path)).load_status == "invalid"


def test_oversized_file_is_invalid(tmp_path):
    rec = _record()
    rec["padding"] = "x" * (sl.READ_CAP_BYTES + 1)
    _write(tmp_path, rec)
    assert sl.read_state(str(tmp_path)).load_status == "invalid"


def test_a_fifo_in_place_of_the_state_file_is_invalid_and_does_not_block(tmp_path):
    """Found by this change's own pre-PR self-review.

    `open()` on a FIFO with no writer BLOCKS. This read rides a hook that runs on every
    tool call, so a FIFO at that path would hang the session rather than degrade. Refuse
    non-regular files before opening — the same guard `context_meter._read_capped` has.
    """
    p = Path(sl.supervision_path(str(tmp_path)))
    p.parent.mkdir(parents=True, exist_ok=True)
    os.mkfifo(p)
    try:
        loaded = sl.read_state(str(tmp_path))     # must return, not hang
    finally:
        p.unlink()
    assert loaded.load_status == "invalid"


def test_a_directory_in_place_of_the_state_file_is_invalid(tmp_path):
    p = Path(sl.supervision_path(str(tmp_path)))
    p.mkdir(parents=True)
    assert sl.read_state(str(tmp_path)).load_status == "invalid"


def test_invalid_utf8_is_invalid_and_does_not_raise(tmp_path):
    """UnicodeDecodeError is a ValueError, NOT an OSError — it escaped the never-raises
    contract and would have aborted a per-tool-call hook (pre-PR review finding,
    reproduced live before the fix)."""
    p = Path(sl.supervision_path(str(tmp_path)))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b'{"state": "\xff\xfe not utf-8"}')
    assert sl.read_state(str(tmp_path)).load_status == "invalid"


@pytest.mark.parametrize("bad_root", [["a", "list"], {"a": 1}, 7, object()])
def test_a_non_string_workspace_root_is_invalid_and_does_not_raise(bad_root):
    """`os.path.isdir([])` raises TypeError, which would escape read_state."""
    assert sl.read_state(bad_root).load_status == "invalid"


def test_a_dangling_symlink_is_invalid_not_absent(tmp_path):
    """Something was put there deliberately and no longer resolves. Reading that as
    absence would permit installs while a declaration was in force."""
    p = Path(sl.supervision_path(str(tmp_path)))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.symlink_to(tmp_path / "gone.json")
    assert sl.read_state(str(tmp_path)).load_status == "invalid"


@pytest.mark.parametrize("partial", [
    {"state": "attended"},
    {"state": "away"},
    {"schema_version": 1, "state": "attended"},
])
def test_a_schema_incomplete_record_is_invalid_not_permissive(tmp_path, partial):
    """A record missing declared fields must fail SAFE (invalid forbids installs), never
    read as a permissive `attended` (pre-PR review finding)."""
    _write(tmp_path, partial)
    view = _view(tmp_path)
    assert view.load_status == "invalid"
    assert sl.installs_forbidden(view) is True


def test_a_valid_file_reads_as_valid(tmp_path):
    _write(tmp_path, _record(state="away"))
    loaded = sl.read_state(str(tmp_path))
    assert loaded.load_status == "valid"
    assert loaded.record["state"] == "away"


def test_read_never_raises_on_an_unreadable_file(tmp_path):
    """A per-tool-call hook must never be wedged by this read."""
    p = _write(tmp_path, _record())
    os.chmod(p, 0o000)
    try:
        loaded = sl.read_state(str(tmp_path))
    finally:
        os.chmod(p, 0o600)
    if os.geteuid() != 0:  # root ignores the mode
        assert loaded.load_status == "invalid"


# --------------------------------------------------------------- evaluation

def test_absent_state_is_attended_and_permits_everything(tmp_path):
    view = _view(tmp_path)
    assert view.state == "attended"
    assert view.load_status == "absent"
    assert sl.nobody_to_ask(view) is False
    assert sl.installs_forbidden(view) is False


def test_declared_attended_permits_everything(tmp_path):
    _write(tmp_path, _record(state="attended"))
    view = _view(tmp_path)
    assert view.state == "attended"
    assert sl.nobody_to_ask(view) is False
    assert sl.installs_forbidden(view) is False


@pytest.mark.parametrize("declared", ["away", "sleeping"])
def test_live_absence_sets_both_predicates(tmp_path, declared):
    _write(tmp_path, _record(state=declared, until=_iso(LATER)))
    view = _view(tmp_path)
    assert view.state == declared
    assert view.expired is False
    assert sl.nobody_to_ask(view) is True
    assert sl.installs_forbidden(view) is True


def test_away_with_no_until_never_expires(tmp_path):
    _write(tmp_path, _record(state="away", until=None))
    view = _view(tmp_path)
    assert view.state == "away"
    assert view.expired is False
    assert sl.nobody_to_ask(view) is True


def test_expiry_boundary_is_exactly_at_until(tmp_path):
    """AT `until` the declaration is still live; one second past it is overdue."""
    _write(tmp_path, _record(state="sleeping", until=_iso(NOW)))
    assert _view(tmp_path, now=NOW).state == "sleeping"
    assert _view(tmp_path, now=NOW + timedelta(seconds=1)).state == "attended-overdue"


def test_expired_absence_retains_its_reporting_context(tmp_path):
    _write(tmp_path, _record(state="away", until=_iso(EARLIER), revision=9))
    view = _view(tmp_path)
    assert view.state == "attended-overdue"
    assert view.expired is True
    assert view.declared == "away"
    assert view.revision == 9
    assert view.declared_at == _iso(EARLIER)


def test_expired_away_is_asymmetric_across_the_two_predicates(tmp_path):
    """THE test. A single `is_watched` boolean cannot express this.

    A clock passing a timestamp is not evidence a human came back, so the outward
    action (installing packages) stays refused while the advice-only predicate
    relaxes. If a future refactor collapses these into one flag, this fails.
    """
    _write(tmp_path, _record(state="away", until=_iso(EARLIER)))
    view = _view(tmp_path)
    assert sl.nobody_to_ask(view) is False
    assert sl.installs_forbidden(view) is True


def test_invalid_state_forbids_installs_but_does_not_claim_nobody_is_there(tmp_path):
    p = Path(sl.supervision_path(str(tmp_path)))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{corrupt")
    view = _view(tmp_path)
    assert view.load_status == "invalid"
    assert sl.nobody_to_ask(view) is False
    assert sl.installs_forbidden(view) is True


def test_unresolvable_root_forbids_installs(tmp_path):
    view = _view(tmp_path / "nope")
    assert view.load_status == "invalid"
    assert sl.installs_forbidden(view) is True


def test_a_bare_away_declaration_still_guards_the_hook_sites(tmp_path):
    """The inert-feature regression.

    `evaluate_workspace` answers "is the human watching, at all?" — it must NOT be
    narrowed by `governed_campaign_ids`, because the hook sites belong to no campaign.
    An earlier draft overloaded one evaluator with both questions, which would have
    shipped a declaration that silently did nothing at exactly the sites it exists for.
    """
    _write(tmp_path, _record(state="away", governed_campaign_ids=[]))
    view = _view(tmp_path)
    assert sl.installs_forbidden(view) is True
    assert sl.nobody_to_ask(view) is True

    _write(tmp_path, _record(state="away", governed_campaign_ids=["some-other-campaign"]))
    view = _view(tmp_path)
    assert sl.installs_forbidden(view) is True, (
        "naming campaigns must not narrow the workspace-global question")


def test_unparseable_until_degrades_to_attended_not_to_permanent_absence(tmp_path):
    _write(tmp_path, _record(state="away", until="not-a-timestamp"))
    view = _view(tmp_path)
    assert view.load_status == "invalid"
    assert sl.nobody_to_ask(view) is False
    assert sl.installs_forbidden(view) is True


def test_consult_grant_is_surfaced(tmp_path):
    _write(tmp_path, _record(state="sleeping", until=_iso(LATER)))
    view = _view(tmp_path)
    assert view.consult_providers == ("gpt",)
    assert view.granted is True


def test_view_is_immutable(tmp_path):
    view = _view(tmp_path)
    with pytest.raises(Exception):
        view.state = "away"


# --------------------------------------------------------------- validators

def test_validate_declaration_accepts_the_three_states():
    assert sl.validate_declaration("attended", None, NOW)[0] is True
    assert sl.validate_declaration("away", None, NOW)[0] is True
    assert sl.validate_declaration("away", _iso(LATER), NOW)[0] is True
    assert sl.validate_declaration("sleeping", _iso(LATER), NOW)[0] is True


def test_validate_declaration_refuses_off_vocabulary_state():
    ok, err = sl.validate_declaration("napping", None, NOW)
    assert ok is False and "napping" in err


def test_validate_declaration_requires_a_wake_time_for_sleeping():
    ok, err = sl.validate_declaration("sleeping", None, NOW)
    assert ok is False and "until" in err


def test_validate_declaration_refuses_until_on_attended():
    ok, err = sl.validate_declaration("attended", _iso(LATER), NOW)
    assert ok is False


def test_validate_declaration_refuses_a_past_until():
    """A wake time behind `now` would declare an instantly-expired absence."""
    ok, err = sl.validate_declaration("away", _iso(EARLIER), NOW)
    assert ok is False and "past" in err.lower()


def test_validate_declaration_refuses_an_unparseable_until():
    ok, err = sl.validate_declaration("away", "tomorrow-ish", NOW)
    assert ok is False


@pytest.mark.parametrize("bad", [
    "../escape", "a/b", "a\\b", "", "   ", "x" * 200, None, 7,
    # `.` and `..` MATCH the charset but are path navigation — the regex alone let them
    # through (pre-PR review finding, confirmed live).
    ".", "..", "...",
])
def test_validate_campaign_id_rejects_unsafe_values(bad):
    assert sl.validate_campaign_id(bad) is False


@pytest.mark.parametrize("good", ["epic-871-m4-wave", "a", "A_b.c-1"])
def test_validate_campaign_id_accepts_safe_values(good):
    assert sl.validate_campaign_id(good) is True


def test_validate_providers_accepts_the_runner_vocabulary():
    assert sl.validate_providers(["gpt"])[0] is True
    assert sl.validate_providers(["gpt", "glm"])[0] is True
    assert sl.validate_providers([])[0] is True


def test_validate_providers_rejects_an_unknown_provider():
    ok, err = sl.validate_providers(["gpt", "hal9000"])
    assert ok is False and "hal9000" in err


# ------------------------------------------------------- import-graph guard

def test_supervision_lib_imports_stdlib_only():
    """The whole reason this module is separate from supervision_admin.

    `hooks/context_meter.py` runs on every tool call and imports stdlib only. If this
    module ever imports `plan_lib` (or anything else heavy) for convenience, that
    property dies silently — so the guard names the import list rather than trusting
    review to notice.
    """
    import ast
    tree = ast.parse((HOOKS / "supervision_lib.py").read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imported.add(node.module.split(".")[0])
    allowed = {
        "argparse", "collections", "dataclasses", "datetime", "json", "os", "re",
        "stat", "sys", "typing", "__future__",
    }
    assert imported <= allowed, f"non-stdlib or heavy import(s): {sorted(imported - allowed)}"


# ------------------------------------------------------------------- the CLI

def _cli(*args, cwd=None):
    return subprocess.run(
        [sys.executable, str(HOOKS / "supervision_lib.py"), *args],
        capture_output=True, text=True, cwd=cwd or str(REPO_ROOT))


def test_cli_installs_forbidden_exit_codes(tmp_path):
    """Exit 0 = FORBIDDEN, 1 = allowed. A bare state word would be ambiguous here:
    `attended-overdue` reads as attended but must still forbid."""
    assert _cli("installs-forbidden", "--workspace", str(tmp_path)).returncode == 1

    _write(tmp_path, _record(state="away", until=None))
    assert _cli("installs-forbidden", "--workspace", str(tmp_path)).returncode == 0

    _write(tmp_path, _record(state="away", until=_real_past()))
    assert _cli("installs-forbidden", "--workspace", str(tmp_path)).returncode == 0, (
        "an expired declaration must still forbid installs")


def test_cli_nobody_to_ask_exit_codes(tmp_path):
    assert _cli("nobody-to-ask", "--workspace", str(tmp_path)).returncode == 1
    _write(tmp_path, _record(state="sleeping", until=_real_future()))
    assert _cli("nobody-to-ask", "--workspace", str(tmp_path)).returncode == 0


def test_cli_missing_required_workspace_is_a_usage_error():
    assert _cli("installs-forbidden").returncode == 2


def test_cli_effective_prints_parseable_json(tmp_path):
    _write(tmp_path, _record(state="away", until=_real_future()))
    r = _cli("effective", "--workspace", str(tmp_path))
    assert r.returncode == 0
    payload = json.loads(r.stdout)
    assert payload["state"] == "away"
    assert payload["installs_forbidden"] is True
    assert payload["nobody_to_ask"] is True
    assert payload["load_status"] == "valid"


def test_cli_diagnoses_an_invalid_file_on_stderr(tmp_path):
    p = Path(sl.supervision_path(str(tmp_path)))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{corrupt")
    r = _cli("installs-forbidden", "--workspace", str(tmp_path))
    assert r.returncode == 0            # forbidden
    assert r.stderr.strip(), "an invalid state file must be visible, never silent"


# ------------------------------------------------- the #948 time bomb, pinned

def test_no_cli_test_hands_a_frozen_future_timestamp_to_a_subprocess():
    """A frozen future constant must never cross into a real-clock subprocess.

    `LATER` is `NOW + 2h` off a FROZEN `NOW`, which is right for the in-process tests
    because each one injects that clock. A CLI subprocess cannot take it and reads the
    real clock instead, so formatting `LATER` below the `_cli` definition is a dated
    bomb: it passes until wall-clock reaches the constant, then fails forever. That is
    exactly what happened at 2026-08-05T23:00:00Z, when three tests across these two
    files broke on `main` an hour after merging green (#948). Use `_real_future()`.

    Deliberately narrow (CLAUDE.md §4 mistake #6): ONE exact pattern, in the CLI
    section of two named files — not a corpus-wide regex.
    """
    needle = "_iso(" + "LATER)"          # split, so this guard is not its own match
    for name in ("test_supervision_lib.py", "test_supervision_admin.py"):
        src = (Path(__file__).parent / name).read_text()
        cli_section = src[src.index("def _cli("):]
        assert needle not in cli_section, (
            f"{name}: a CLI test builds a timestamp from the frozen `LATER`, which "
            f"expires against the real clock — use `_real_future()` instead")
