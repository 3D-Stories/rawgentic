"""Tests for hooks/step_state.py — step-entry state record (#480).

Purely observational "now" pointer: black-box CLI tests via subprocess (per
docs/testing.md) plus direct unit tests of the pure core. Every scenario here
that names a runtime failure (bad --issue, unwritable dir, traversal attempt,
unresolved state dir) asserts exit 0 — this module is never a gate.
"""
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HOOKS_DIR = Path(__file__).resolve().parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

import step_state as ss  # noqa: E402

CLI = HOOKS_DIR / "step_state.py"


def _run(args, cwd=None):
    return subprocess.run([sys.executable, str(CLI), *args],
                          capture_output=True, text=True, cwd=cwd)


# --- pure core ---------------------------------------------------------------

def test_sanitize_project_happy_path():
    assert ss.sanitize_project("rawgentic") == "rawgentic"


def test_sanitize_project_neutralizes_traversal():
    out = ss.sanitize_project("../evil/x")
    assert out is not None
    assert "/" not in out and "\\" not in out


def test_sanitize_project_rejects_all_dots():
    assert ss.sanitize_project("..") is None
    assert ss.sanitize_project(".") is None
    assert ss.sanitize_project("...") is None


def test_sanitize_project_rejects_empty():
    assert ss.sanitize_project("") is None
    assert ss.sanitize_project("   ") is None


def test_find_state_dir_uses_workspace_marker(tmp_path):
    root = tmp_path / "ws"
    sub = root / "projects" / "p"
    sub.mkdir(parents=True)
    (root / ".rawgentic_workspace.json").write_text("{}", encoding="utf-8")
    got = ss.find_state_dir(str(sub))
    assert got == str(root / "claude_docs" / "wal")


def test_find_state_dir_falls_back_only_if_exists(tmp_path):
    # no workspace marker anywhere above tmp_path/lone; fallback dir absent -> None
    lone = tmp_path / "lone"
    lone.mkdir()
    assert ss.find_state_dir(str(lone)) is None
    # now create the fallback dir -> resolves
    fallback = lone / "claude_docs" / "wal"
    fallback.mkdir(parents=True)
    assert ss.find_state_dir(str(lone)) == str(fallback)


def test_build_record_shape():
    now = datetime(2026, 7, 18, 11, 22, 33, tzinfo=timezone.utc)
    rec = ss.build_record("proj", "wf2", "8a", "Title", "sess-1", 42, now)
    assert rec == {
        "schema_version": 1,
        "project": "proj",
        "workflow": "wf2",
        "step": "8a",
        "step_title": "Title",
        "issue": 42,
        "session_id": "sess-1",
        "entered_at": "2026-07-18T11:22:33Z",
    }


# --- CLI: write ---------------------------------------------------------------

def test_write_creates_file_with_all_fields(tmp_path):
    r = _run(["write", "--project", "rawgentic", "--workflow", "wf2",
              "--step", "8a", "--step-title", "Code review",
              "--session-id", "sess-123", "--issue", "480",
              "--state-dir", str(tmp_path)])
    assert r.returncode == 0, r.stderr
    f = tmp_path / "rawgentic.state.json"
    assert f.is_file()
    rec = json.loads(f.read_text(encoding="utf-8"))
    assert set(rec.keys()) == {"schema_version", "project", "workflow", "step",
                               "step_title", "issue", "session_id", "entered_at"}
    assert rec["project"] == "rawgentic"
    assert rec["workflow"] == "wf2"
    assert rec["step"] == "8a"
    assert rec["step_title"] == "Code review"
    assert rec["session_id"] == "sess-123"
    assert rec["issue"] == 480
    # entered_at parses as a UTC ISO-8601 timestamp
    dt = datetime.fromisoformat(rec["entered_at"].replace("Z", "+00:00"))
    assert dt.tzinfo is not None


def test_write_overwrites_single_file(tmp_path):
    r1 = _run(["write", "--project", "p", "--workflow", "wf2", "--step", "3",
               "--step-title", "First", "--session-id", "s1",
               "--state-dir", str(tmp_path)])
    assert r1.returncode == 0, r1.stderr
    r2 = _run(["write", "--project", "p", "--workflow", "wf3", "--step", "11.5",
               "--step-title", "Second", "--session-id", "s2",
               "--state-dir", str(tmp_path)])
    assert r2.returncode == 0, r2.stderr
    files = list(tmp_path.glob("p.state.json"))
    assert len(files) == 1
    rec = json.loads(files[0].read_text(encoding="utf-8"))
    assert rec["workflow"] == "wf3"
    assert rec["step"] == "11.5"
    assert rec["step_title"] == "Second"
    assert rec["session_id"] == "s2"
    # no stray temp file left behind
    assert not list(tmp_path.glob(".step_state.*"))


def test_write_issue_absent_is_null(tmp_path):
    r = _run(["write", "--project", "p", "--workflow", "wf2", "--step", "1",
              "--step-title", "T", "--session-id", "s",
              "--state-dir", str(tmp_path)])
    assert r.returncode == 0, r.stderr
    rec = json.loads((tmp_path / "p.state.json").read_text(encoding="utf-8"))
    assert rec["issue"] is None


def test_write_issue_not_an_int_is_null_and_exits_0(tmp_path):
    r = _run(["write", "--project", "p", "--workflow", "wf2", "--step", "1",
              "--step-title", "T", "--session-id", "s", "--issue", "notanint",
              "--state-dir", str(tmp_path)])
    assert r.returncode == 0, r.stderr
    assert r.stderr.strip() != ""
    rec = json.loads((tmp_path / "p.state.json").read_text(encoding="utf-8"))
    assert rec["issue"] is None


def test_write_unwritable_state_dir_fails_open(tmp_path):
    state_dir = tmp_path / "locked"
    state_dir.mkdir()
    state_dir.chmod(0o000)
    try:
        r = _run(["write", "--project", "p", "--workflow", "wf2", "--step", "1",
                  "--step-title", "T", "--session-id", "s",
                  "--state-dir", str(state_dir)])
        assert r.returncode == 0, r.stderr
        assert r.stderr.strip() != ""
    finally:
        state_dir.chmod(0o755)


def test_write_project_traversal_stays_inside_dir(tmp_path):
    r = _run(["write", "--project", "../evil/x", "--workflow", "wf2",
              "--step", "1", "--step-title", "T", "--session-id", "s",
              "--state-dir", str(tmp_path)])
    assert r.returncode == 0, r.stderr
    # nothing escaped the target directory
    assert not (tmp_path.parent / "evil").exists()
    for f in tmp_path.iterdir():
        assert "/" not in f.name


def test_write_default_resolution_finds_workspace(tmp_path):
    root = tmp_path / "ws"
    proj_dir = root / "projects" / "myproj"
    proj_dir.mkdir(parents=True)
    (root / ".rawgentic_workspace.json").write_text("{}", encoding="utf-8")
    r = _run(["write", "--project", "myproj", "--workflow", "wf2", "--step", "1",
              "--step-title", "T", "--session-id", "s"], cwd=str(proj_dir))
    assert r.returncode == 0, r.stderr
    f = root / "claude_docs" / "wal" / "myproj.state.json"
    assert f.is_file()


# --- CLI: read ------------------------------------------------------------

def _write_state(state_dir, project, entered_at, extra=None):
    rec = {"schema_version": 1, "project": project, "workflow": "wf2",
           "step": "1", "step_title": "T", "issue": None,
           "session_id": "s", "entered_at": entered_at}
    if extra:
        rec.update(extra)
    (Path(state_dir) / f"{project}.state.json").write_text(
        json.dumps(rec), encoding="utf-8")


def test_read_fresh_prints_json(tmp_path):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    _write_state(tmp_path, "p", now)
    r = _run(["read", "--project", "p", "--state-dir", str(tmp_path)])
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["project"] == "p"


def test_read_stale_prints_nothing(tmp_path):
    old = (datetime.now(timezone.utc) - timedelta(hours=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _write_state(tmp_path, "p", old)
    r = _run(["read", "--project", "p", "--state-dir", str(tmp_path),
              "--max-age-min", "240"])
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == ""


def test_read_corrupt_json_prints_nothing(tmp_path):
    (tmp_path / "p.state.json").write_text("{ not json", encoding="utf-8")
    r = _run(["read", "--project", "p", "--state-dir", str(tmp_path)])
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == ""


def test_read_absent_prints_nothing(tmp_path):
    r = _run(["read", "--project", "nope", "--state-dir", str(tmp_path)])
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == ""


# --- drift guard: no gating hook reads this file (AC4; #499 carve-out) ------

def test_no_gating_hook_references_step_state():
    # GATING hooks must never read the observational state (#480 AC4).
    for name in ("wal-guard", "wal-bind-guard", "security-guard.py"):
        text = (HOOKS_DIR / name).read_text(encoding="utf-8")
        assert "step_state" not in text, f"{name} references step_state"
        assert "state.json" not in text, f"{name} references state.json"


def test_hooks_json_step_state_is_posttooluse_only():
    # #499: the ONE sanctioned hooks.json reference is the observational
    # PostToolUse emitter — never a Pre* or Stop (gating-capable) event.
    cfg = json.loads((HOOKS_DIR / "hooks.json").read_text(encoding="utf-8"))
    for event, entries in cfg.get("hooks", {}).items():
        blob = json.dumps(entries)
        if event == "PostToolUse":
            assert "step_state_post" in blob, (
                "the #499 emitter must be registered on PostToolUse")
        else:
            assert "step_state" not in blob, (
                f"step_state referenced on {event} — only PostToolUse is sanctioned")


# --- per-workflow prose pins (AC6): the entry-call line exists in each skill --------------

def test_step_entry_prose_pin_all_five_skills():
    """#480 AC6: each of the five workflow skills carries the one canonical step-entry
    call sentence (anchored to the invocation literal in ONE file each — content pin)."""
    repo = HOOKS_DIR.parent
    expected = {
        "create-issue": "--workflow wf1",
        "implement-feature": "--workflow wf2",
        "fix-bug": "--workflow wf3",
        "adversarial-review": "--workflow wf5",
        "epic-run": "--workflow epic-run",
    }
    for skill, token in expected.items():
        text = (repo / "skills" / skill / "SKILL.md").read_text(encoding="utf-8")
        assert "step_state.py write --project" in text, f"{skill}: entry-call line missing"
        assert token in text, f"{skill}: wrong workflow token"
        assert "fail-open" in text.lower(), f"{skill}: fail-open clause missing"


# --- Step-11 join fixes (#480): reader honesty + import fail-open ------------------

def test_read_works_without_writer_dependency(tmp_path):
    """Step-11 adversarial M2: a missing atomic_write_lib must not break `read` —
    the writer dep imports lazily so ImportError stays inside the fail-open boundary.
    Proved by running a LONE copy of step_state.py with no hooks siblings."""
    import os as _os
    import shutil
    lone = tmp_path / "lone"
    lone.mkdir()
    shutil.copy(HOOKS_DIR / "step_state.py", lone / "step_state.py")
    # strip PYTHONPATH: the pytest invocation exports PYTHONPATH=hooks, which would
    # leak the writer dep back into the "lone" subprocess and vacuously pass this cell
    env = {k: v for k, v in _os.environ.items() if k != "PYTHONPATH"}
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    _write_state(tmp_path, "p", now)
    r = subprocess.run([sys.executable, str(lone / "step_state.py"), "read",
                        "--project", "p", "--state-dir", str(tmp_path)],
                       capture_output=True, text=True, env=env, cwd=str(lone))
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["project"] == "p"
    # and a WRITE without the dep fails OPEN (stderr note, exit 0, no crash)
    w = subprocess.run([sys.executable, str(lone / "step_state.py"), "write",
                        "--project", "p", "--workflow", "wf2", "--step", "1",
                        "--step-title", "T", "--session-id", "s",
                        "--state-dir", str(tmp_path)],
                       capture_output=True, text=True, env=env, cwd=str(lone))
    assert w.returncode == 0
    assert "fail-open" in w.stderr


def test_read_rejects_future_timestamp(tmp_path):
    """Step-11 adversarial M4: a corrupt FUTURE entered_at must not be immortal-fresh."""
    future = (datetime.now(timezone.utc) + timedelta(hours=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _write_state(tmp_path, "p", future)
    r = _run(["read", "--project", "p", "--state-dir", str(tmp_path)])
    assert r.returncode == 0 and r.stdout.strip() == ""


def test_read_rejects_structurally_corrupt_record(tmp_path):
    """Step-11 adversarial M3: a record with only entered_at must not render as
    placeholder state (required fields validated)."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    (tmp_path / "p.state.json").write_text(
        json.dumps({"entered_at": now, "session_id": "s"}), encoding="utf-8")
    r = _run(["read", "--project", "p", "--state-dir", str(tmp_path)])
    assert r.returncode == 0 and r.stdout.strip() == ""


def test_read_rejects_project_mismatch(tmp_path):
    """Step-11 adversarial M5 (partial adopt): a sanitize-collision or mislabeled file
    must not present another project's state — stored project must equal the request."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    rec = {"schema_version": 1, "project": "OTHER", "workflow": "wf2", "step": "1",
           "step_title": "T", "issue": None, "session_id": "s", "entered_at": now}
    (tmp_path / "p.state.json").write_text(json.dumps(rec), encoding="utf-8")
    r = _run(["read", "--project", "p", "--state-dir", str(tmp_path)])
    assert r.returncode == 0 and r.stdout.strip() == ""


def test_read_negative_max_age_prints_nothing(tmp_path):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    _write_state(tmp_path, "p", now)
    r = _run(["read", "--project", "p", "--state-dir", str(tmp_path),
              "--max-age-min", "-5"])
    assert r.returncode == 0 and r.stdout.strip() == ""
