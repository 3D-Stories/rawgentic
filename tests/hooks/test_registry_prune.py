"""Tests for hooks/registry_prune.py — session-registry TTL pruning (#7)."""
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

HOOKS_DIR = Path(__file__).resolve().parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

import registry_prune as rp  # noqa: E402

CLI = HOOKS_DIR / "registry_prune.py"
NOW = datetime(2026, 7, 6, tzinfo=timezone.utc)


def _entry(sid, started):
    return json.dumps({"session_id": sid, "project": "p", "project_path": "./p",
                       "started": started, "cwd": "/w"})


# --- ttl_days env resolution ------------------------------------------------

def test_ttl_default():
    assert rp.ttl_days({}) == 30

def test_ttl_env_override():
    assert rp.ttl_days({"RAWGENTIC_REGISTRY_TTL_DAYS": "7"}) == 7

def test_ttl_env_invalid_falls_back():
    assert rp.ttl_days({"RAWGENTIC_REGISTRY_TTL_DAYS": "abc"}) == 30
    assert rp.ttl_days({"RAWGENTIC_REGISTRY_TTL_DAYS": "0"}) == 30
    assert rp.ttl_days({"RAWGENTIC_REGISTRY_TTL_DAYS": "-5"}) == 30


# --- prune_registry (pure) --------------------------------------------------

def test_removes_old_keeps_recent():
    text = "\n".join([
        _entry("old", "2026-05-01T00:00:00Z"),   # 66 days before NOW → removed
        _entry("recent", "2026-07-01T00:00:00Z"), # 5 days → kept
    ]) + "\n"
    out, stats = rp.prune_registry(text, NOW, 30)
    assert stats == {"kept": 1, "removed": 1, "undatable": 0}
    assert "recent" in out and "old" not in out

def test_boundary_just_inside_ttl_kept():
    text = _entry("edge", "2026-06-10T00:00:00Z") + "\n"  # 26 days → within 30 → kept
    out, stats = rp.prune_registry(text, NOW, 30)
    assert stats["removed"] == 0 and stats["kept"] == 1

def test_undatable_lines_kept_failsafe():
    text = "\n".join([
        "{ not json",                                  # malformed
        json.dumps({"session_id": "x"}),               # no started
        json.dumps({"session_id": "y", "started": 42}),# non-str started
        _entry("z", "2020-01-01T00:00:00Z"),           # ancient → removed
    ]) + "\n"
    out, stats = rp.prune_registry(text, NOW, 30)
    assert stats["removed"] == 1
    assert stats["undatable"] == 3
    assert stats["kept"] == 3  # the 3 undatable survive
    assert "z" not in out

def test_blank_lines_dropped_not_counted():
    text = _entry("a", "2026-07-05T00:00:00Z") + "\n\n\n"
    out, stats = rp.prune_registry(text, NOW, 30)
    assert stats["kept"] == 1
    assert out == _entry("a", "2026-07-05T00:00:00Z") + "\n"

def test_empty_text():
    out, stats = rp.prune_registry("", NOW, 30)
    assert out == "" and stats == {"kept": 0, "removed": 0, "undatable": 0}

def test_naive_timestamp_treated_as_utc():
    text = _entry("naive", "2026-05-01T00:00:00") + "\n"  # no Z → UTC → 66d → removed
    _out, stats = rp.prune_registry(text, NOW, 30)
    assert stats["removed"] == 1


# --- CLI --------------------------------------------------------------------

def _run(*args):
    return subprocess.run([sys.executable, str(CLI), *args], capture_output=True, text=True)

def test_cli_prunes_and_rewrites(tmp_path):
    reg = tmp_path / "session_registry.jsonl"
    reg.write_text(_entry("old", "2026-01-01T00:00:00Z") + "\n"
                   + _entry("new", "2026-07-05T00:00:00Z") + "\n", encoding="utf-8")
    r = _run("--registry", str(reg), "--now", "2026-07-06T00:00:00Z")
    assert r.returncode == 0, r.stderr
    lines = [l for l in reg.read_text().splitlines() if l.strip()]
    assert len(lines) == 1 and "new" in lines[0]

def test_cli_dry_run_does_not_write(tmp_path):
    reg = tmp_path / "r.jsonl"
    body = _entry("old", "2026-01-01T00:00:00Z") + "\n"
    reg.write_text(body, encoding="utf-8")
    r = _run("--registry", str(reg), "--now", "2026-07-06T00:00:00Z", "--dry-run")
    assert r.returncode == 0 and "dry-run" in r.stdout
    assert reg.read_text() == body  # unchanged

def test_cli_missing_registry_is_noop(tmp_path):
    r = _run("--registry", str(tmp_path / "nope.jsonl"))
    assert r.returncode == 0 and "nothing to prune" in r.stdout

def test_cli_rejects_bad_ttl(tmp_path):
    reg = tmp_path / "r.jsonl"; reg.write_text("", encoding="utf-8")
    r = _run("--registry", str(reg), "--ttl-days", "0")
    assert r.returncode == 2
