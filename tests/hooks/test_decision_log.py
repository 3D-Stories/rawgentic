"""Tests for hooks/decision_log.py — the append-only decision store (#847).

Pure functions are imported; the CLI is exercised via subprocess, matching the
hook testing pattern in docs/testing.md.
"""
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from tests.hooks.conftest import HOOKS_DIR

CLI = HOOKS_DIR / "decision_log.py"

sys.path.insert(0, str(HOOKS_DIR))
import decision_log  # noqa: E402


def _append(state_dir: Path, project: str, did: str, **kw) -> subprocess.CompletedProcess:
    cmd = [sys.executable, str(CLI), "append", "--project", project, "--id", did,
           "--title", kw.pop("title", f"decision {did}"),
           "--overturnable", kw.pop("overturnable", f"revert {did}"),
           "--state-dir", str(state_dir)]
    for k, v in kw.items():
        cmd.extend([f"--{k}", v])
    return subprocess.run(cmd, capture_output=True, text=True, timeout=20)


def _read(state_dir: Path, project: str, *extra) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(CLI), "read", "--project", project,
         "--state-dir", str(state_dir), *extra],
        capture_output=True, text=True, timeout=20,
    )


class TestStoreLocation:
    """AC1-2: the store must be somewhere the trimmer's glob cannot reach."""

    def test_store_is_a_sibling_of_session_notes(self, tmp_path):
        p = decision_log.store_path(tmp_path, "rawgentic")
        assert p == tmp_path / "decisions" / "rawgentic.jsonl"
        assert p.parent.name != "session_notes"

    def test_store_is_not_markdown(self, tmp_path):
        assert decision_log.store_path(tmp_path, "rawgentic").suffix == ".jsonl"

    def test_trimmer_file_discovery_never_yields_a_decisions_path(self, tmp_path):
        """The reporter's entry path is a glob over session_notes/*.md."""
        notes = tmp_path / "session_notes"
        notes.mkdir()
        (notes / "rawgentic.md").write_text("x\n")
        decision_log.append_record(
            tmp_path, "rawgentic",
            decision_log.build_record(
                project="rawgentic", decision_id="D1", title="t",
                body="b", overturnable="u"),
        )
        discovered = sorted(notes.glob("*.md"))
        assert all("decisions" not in str(p) for p in discovered)
        assert decision_log.store_path(tmp_path, "rawgentic").exists()

    def test_invalid_project_name_rejected(self, tmp_path):
        with pytest.raises(ValueError):
            decision_log.store_path(tmp_path, "../etc/passwd")


class TestRecordContract:
    def test_overturnable_is_mandatory(self):
        with pytest.raises(ValueError, match="overturnable"):
            decision_log.build_record(
                project="p", decision_id="D1", title="t", body="b",
                overturnable="")

    def test_title_and_id_are_mandatory(self):
        with pytest.raises(ValueError):
            decision_log.build_record(
                project="p", decision_id="", title="t", body="b", overturnable="u")
        with pytest.raises(ValueError):
            decision_log.build_record(
                project="p", decision_id="D1", title="", body="b", overturnable="u")

    def test_record_carries_every_declared_field(self):
        rec = decision_log.build_record(
            project="p", decision_id="D1", title="t", body="b",
            overturnable="u", run="epic-46", session="s1")
        assert set(rec) == set(decision_log.FIELDS)


class TestAppendIsAppendOnly:
    def test_append_then_read_roundtrip(self, tmp_path):
        assert _append(tmp_path, "proj", "D1").returncode == 0
        assert _append(tmp_path, "proj", "D2").returncode == 0
        out = _read(tmp_path, "proj")
        assert out.returncode == 0
        ids = [json.loads(ln)["id"] for ln in out.stdout.splitlines()]
        assert ids == ["D1", "D2"], "records must read back oldest-first"

    def test_existing_records_are_never_rewritten(self, tmp_path):
        _append(tmp_path, "proj", "D1")
        path = decision_log.store_path(tmp_path, "proj")
        first = path.read_bytes()
        _append(tmp_path, "proj", "D2")
        assert path.read_bytes().startswith(first), "the first record was rewritten"

    def test_concurrent_appends_lose_nothing(self, tmp_path):
        """The failure atomic_write_text would have caused: a lost update."""
        n = 40
        with ThreadPoolExecutor(max_workers=8) as ex:
            list(ex.map(lambda i: _append(tmp_path, "proj", f"D{i}"), range(n)))
        recs = decision_log.read_records(tmp_path, "proj")
        assert len(recs) == n, f"lost {n - len(recs)} concurrent appends"
        assert {r["id"] for r in recs} == {f"D{i}" for i in range(n)}

    def test_append_failure_is_loud(self, tmp_path):
        r = _append(tmp_path, "bad/name", "D1")
        assert r.returncode == 1
        assert "invalid project name" in r.stderr


class TestReadTolerance:
    def test_corrupt_line_is_skipped_and_reported(self, tmp_path):
        _append(tmp_path, "proj", "D1")
        path = decision_log.store_path(tmp_path, "proj")
        with open(path, "a") as f:
            f.write("{not json at all\n")
        _append(tmp_path, "proj", "D3")

        warnings = []
        recs = decision_log.read_records(tmp_path, "proj", warn=warnings.append)
        assert [r["id"] for r in recs] == ["D1", "D3"]
        assert len(warnings) == 1 and ":2:" in warnings[0]

    def test_truncated_final_record_does_not_lose_earlier_ones(self, tmp_path):
        _append(tmp_path, "proj", "D1")
        _append(tmp_path, "proj", "D2")
        path = decision_log.store_path(tmp_path, "proj")
        with open(path, "a") as f:
            f.write('{"id": "D3", "ti')
        recs = decision_log.read_records(tmp_path, "proj")
        assert [r["id"] for r in recs] == ["D1", "D2"]

    def test_missing_file_reads_empty(self, tmp_path):
        assert decision_log.read_records(tmp_path, "neverwritten") == []


class TestLastN:
    def test_last_n_returns_the_newest_n_in_order(self, tmp_path):
        for i in range(20):
            _append(tmp_path, "proj", f"D{i}")
        recs = decision_log.read_records(tmp_path, "proj", last=15)
        assert [r["id"] for r in recs] == [f"D{i}" for i in range(5, 20)]

    def test_last_n_larger_than_store_returns_all(self, tmp_path):
        _append(tmp_path, "proj", "D1")
        assert len(decision_log.read_records(tmp_path, "proj", last=15)) == 1

    def test_last_zero_returns_nothing(self, tmp_path):
        _append(tmp_path, "proj", "D1")
        assert decision_log.read_records(tmp_path, "proj", last=0) == []

    def test_run_filter_applies_before_the_last_slice(self, tmp_path):
        for i in range(10):
            _append(tmp_path, "proj", f"A{i}", run="epic-1")
        for i in range(10):
            _append(tmp_path, "proj", f"B{i}", run="epic-2")
        recs = decision_log.read_records(tmp_path, "proj", last=3, run="epic-1")
        assert [r["id"] for r in recs] == ["A7", "A8", "A9"], (
            "the run filter must be applied before slicing, or an older run "
            "silently returns nothing"
        )
