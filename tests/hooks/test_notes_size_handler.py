"""Tests for notes-size-handler.py — session notes size handler.

Tests the standalone Python script that trims oversized session notes files
and optionally ingests content to the memorypalace server before trimming.
Uses subprocess invocation to match the hook testing pattern.
"""
import json
import subprocess
from pathlib import Path

import pytest

from tests.hooks.conftest import HOOKS_DIR

HANDLER_SCRIPT = HOOKS_DIR / "notes-size-handler.py"


def _run_handler(
    notes_file: Path,
    *,
    session_id: str = "test-session",
    timeout: int = 10,
) -> tuple[str, str, int]:
    """Run notes-size-handler.py as a subprocess."""
    cmd = ["python3", str(HANDLER_SCRIPT), str(notes_file), "--session-id", session_id]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return result.stdout, result.stderr, result.returncode


def _make_notes(path: Path, project: str, num_lines: int) -> Path:
    """Create a notes file with the given number of lines."""
    notes_file = path / f"{project}.md"
    header = f"# Session Notes -- {project}\n"
    content = header + "".join(f"line {i}\n" for i in range(1, num_lines))
    notes_file.write_text(content)
    return notes_file


class TestNoOp:
    """Tests for cases where no trimming should occur."""

    def test_under_threshold_no_trim(self, tmp_path):
        """Notes under 800 lines should not be trimmed."""
        notes_file = _make_notes(tmp_path, "testproj", 500)
        original = notes_file.read_text()

        stdout, stderr, rc = _run_handler(notes_file)
        assert rc == 0

        result = json.loads(stdout.strip())
        assert result["trimmed"] is False

        # File unchanged
        assert notes_file.read_text() == original

    def test_nonexistent_file_exits_zero(self, tmp_path):
        """Missing file should exit 0 with trimmed=false."""
        fake_file = tmp_path / "nonexistent.md"

        stdout, stderr, rc = _run_handler(fake_file)
        assert rc == 0

        result = json.loads(stdout.strip())
        assert result["trimmed"] is False

    def test_empty_file_no_trim(self, tmp_path):
        """Empty file should not be trimmed."""
        notes_file = tmp_path / "testproj.md"
        notes_file.write_text("")

        stdout, stderr, rc = _run_handler(notes_file)
        assert rc == 0

        result = json.loads(stdout.strip())
        assert result["trimmed"] is False


class TestTrimming:
    """Tests for the trimming behavior."""

    def test_trim_keeps_last_200_lines(self, tmp_path):
        """Notes exceeding 800 lines should keep the last 200 lines."""
        notes_file = _make_notes(tmp_path, "testproj", 9000)

        stdout, stderr, rc = _run_handler(notes_file)
        assert rc == 0

        result = json.loads(stdout.strip())
        assert result["trimmed"] is True
        assert result["char_count"] > 64_000
        assert result["kept_lines"] == 200
        assert result["project"] == "testproj"

        # Verify content: last 200 lines preserved
        content = notes_file.read_text()
        lines = content.split("\n")
        # Should contain the header and trim marker, then the last 200 original lines
        assert "# Session Notes -- testproj" in content
        assert "Trimmed from" in content and "chars at" in content
        # The last line of original was "line 8999"
        assert "line 8999" in content
        # The first lines (like "line 1") should be gone
        assert "line 1\n" not in content

    def test_trim_header_format(self, tmp_path):
        """Trimmed file should have proper header with project name and timestamp."""
        notes_file = _make_notes(tmp_path, "myproject", 9000)

        stdout, _, _ = _run_handler(notes_file)
        result = json.loads(stdout.strip())
        assert result["trimmed"] is True

        content = notes_file.read_text()
        assert content.startswith("# Session Notes -- myproject\n")
        assert "<!-- Trimmed from " in content and " chars at " in content
        # Timestamp should be ISO format
        assert "T" in content.split("Trimmed from")[1]

    def test_atomic_write_no_partial(self, tmp_path):
        """Verify the original file is intact if we read it immediately."""
        notes_file = _make_notes(tmp_path, "testproj", 9000)

        _run_handler(notes_file)

        # File should be valid (not empty, not partial)
        content = notes_file.read_text()
        assert len(content) > 0
        assert content.startswith("# Session Notes")
        # No .tmp files left behind
        tmp_files = list(tmp_path.glob("*.tmp"))
        assert len(tmp_files) == 0


class TestValidation:
    """Tests for path and project name validation."""

    def test_invalid_project_name_rejected(self, tmp_path):
        """A stem that fails PROJECT_NAME_RE must be refused, not just survive.

        This previously used `../../etc/passwd.md`, whose stem is `passwd` — a
        perfectly VALID project name — and asserted only rc == 0. The handler
        trimmed it and the test passed, so it pinned nothing.
        """
        bad = tmp_path / "bad name!.md"
        bad.write_text("x\n" * 40_000)
        before = bad.read_bytes()

        stdout, stderr, rc = _run_handler(bad)
        assert rc == 0
        assert json.loads(stdout.strip())["reason"] == "invalid_project_name"
        assert bad.read_bytes() == before

    def test_valid_project_names_accepted(self, tmp_path):
        """Standard project names should be accepted."""
        for name in ["testproj", "my-project", "project_123", "CamelCase"]:
            notes_file = _make_notes(tmp_path, name, 9000)
            stdout, _, rc = _run_handler(notes_file)
            assert rc == 0
            result = json.loads(stdout.strip())
            assert result["trimmed"] is True, f"Failed for project name: {name}"


class TestExitBehavior:
    """Tests that the script always exits 0 on non-fatal errors."""

    def test_permission_error_exits_zero(self, tmp_path):
        """If the file can't be read, exit 0 gracefully."""
        notes_file = tmp_path / "testproj.md"
        notes_file.write_text("x\n" * 40_000)
        notes_file.chmod(0o000)

        try:
            stdout, stderr, rc = _run_handler(notes_file)
            assert rc == 0
        finally:
            notes_file.chmod(0o644)


class TestMultiFile:
    """#269: one invocation handles many files; a failing file is isolated."""

    def test_multiple_files_one_invocation(self, tmp_path):
        import subprocess
        big = "x\n" * 40_000
        small = "y\n" * 10
        f1 = tmp_path / "alpha.md"
        f2 = tmp_path / "beta.md"
        f3 = tmp_path / "gamma.md"
        f1.write_text(big)
        f2.write_text(small)
        f3.write_text(big)
        r = subprocess.run(
            ["python3", str(HANDLER_SCRIPT), str(f1), str(f2), str(f3),
             "--session-id", "s1"],
            capture_output=True, text=True, timeout=30,
        )
        assert r.returncode == 0, r.stderr
        results = [json.loads(line) for line in r.stdout.splitlines() if line]
        assert len(results) == 3
        assert results[0]["trimmed"] is True
        assert results[1]["trimmed"] is False
        assert results[2]["trimmed"] is True
        assert len(f1.read_text().splitlines()) < 40_000
        assert len(f3.read_text().splitlines()) < 40_000

    def test_failing_file_does_not_block_rest(self, tmp_path):
        import subprocess
        f_bad = tmp_path / "bad.md"
        f_bad.write_text("x\n" * 40_000)
        f_bad.chmod(0o000)
        f_good = tmp_path / "good.md"
        f_good.write_text("x\n" * 40_000)
        try:
            r = subprocess.run(
                ["python3", str(HANDLER_SCRIPT), str(f_bad), str(f_good),
                 "--session-id", "s1"],
                capture_output=True, text=True, timeout=30,
            )
            assert r.returncode == 0
            results = [json.loads(line) for line in r.stdout.splitlines() if line]
            assert len(results) == 2
            assert results[0]["trimmed"] is False
            assert results[1]["trimmed"] is True, (
                "a failing earlier file must not block later files"
            )
        finally:
            f_bad.chmod(0o644)


# ---------------------------------------------------------------------------
# #847 — the trimmer destroyed six epic decision logs.
# Reproduce-first: every test below FAILS against the pre-fix handler.
# ---------------------------------------------------------------------------

THRESHOLD_CHARS = 64_000


def _decision_log(path: Path, name: str, n_decisions: int, n_churn: int) -> Path:
    """A decision log: valuable OLD entries, high-volume routine churn after them."""
    f = path / name
    lines = [f"# Session Notes -- {name}", ""]
    for i in range(1, n_decisions + 1):
        lines.append(f"### D{i} — decision: irreplaceable reasoning {i}")
        lines.append(f"Undo: revert commit abc{i}")
        lines.append("")
    # Padded so the fixture clears THRESHOLD_CHARS. Without this the file is
    # ~850 lines but well under 64,000 chars, so deleting the exclusion logic
    # would leave it untrimmed anyway and these tests could never fail.
    lines.extend(f"- routine step marker {i} " + "p" * 80 for i in range(n_churn))
    f.write_text("\n".join(lines) + "\n")
    return f


class TestDecisionLogsAreNeverTruncated:
    """AC1-3: a decision log is never truncated, by anything."""

    def test_autorun_log_is_never_trimmed(self, tmp_path):
        f = _decision_log(tmp_path, "epic-999-autorun-log.md", 140, 430)
        before = f.read_bytes()
        stdout, _, rc = _run_handler(f)
        assert rc == 0
        assert json.loads(stdout.strip())["trimmed"] is False
        assert f.read_bytes() == before, "decision log was modified"

    def test_handoff_log_is_never_trimmed(self, tmp_path):
        f = _decision_log(tmp_path, "sysop.handoff.md", 140, 430)
        before = f.read_bytes()
        _run_handler(f)
        assert f.read_bytes() == before

    def test_archive_files_are_never_re_trimmed(self, tmp_path):
        f = _decision_log(tmp_path, "notes.2026-08-03T00:00:00Z.archive.md", 140, 430)
        before = f.read_bytes()
        _run_handler(f)
        assert f.read_bytes() == before


class TestTrimIsNonDestructive:
    """AC4-5: cut content is archived first, and a failed archive blocks the trim."""

    def test_every_cut_line_is_recoverable_from_the_archive(self, tmp_path):
        f = tmp_path / "proj.md"
        original = "".join(f"line {i}\n" for i in range(12_000))
        f.write_text(original)
        _run_handler(f)
        archives = list((tmp_path / ".notes-archive").glob("proj.md.*.archive.md"))
        assert len(archives) == 1, f"expected one archive, got {archives}"
        recovered = archives[0].read_text() + f.read_text()
        for i in range(12_000):
            assert f"line {i}\n" in recovered, f"line {i} was destroyed"

    def test_archive_write_failure_leaves_original_byte_identical(self, tmp_path):
        """Only the ARCHIVE write fails; the notes write would have succeeded.

        The notes directory stays writable on purpose. An earlier version of
        this test made the whole directory read-only, which also blocked the
        notes write — so it passed even with the fail-closed guard removed.
        A sabotage run caught that.
        """
        f = tmp_path / "proj.md"
        f.write_text("".join(f"line {i}\n" for i in range(12_000)))
        before = f.read_bytes()
        adir = tmp_path / ".notes-archive"
        adir.mkdir()
        adir.chmod(0o500)  # exists, not writable -> only the archive can't land
        try:
            stdout, _, rc = _run_handler(f)
        finally:
            adir.chmod(0o700)
        assert rc == 0
        result = json.loads(stdout.strip())
        assert result["trimmed"] is False
        assert result["reason"] == "archive_failed"
        assert f.read_bytes() == before, "trim proceeded despite a failed archive"
        assert tmp_path.exists() and (tmp_path / "proj.md").stat().st_size > 64_000

    def test_same_second_archives_do_not_clobber(self, tmp_path):
        """Two trims in one second must not have the second destroy the first archive.

        The collision is FORCED rather than raced for (#761 drive-by). The original
        version ran two trims back to back and asserted that the second took a `-1`
        suffix — true only when both landed inside the same wall-clock second, since the
        stamp is `%Y-%m-%dT%H:%M:%SZ`. When they straddled a boundary both archives got
        distinct names, no suffix was needed, and the assertion failed on correct
        behaviour. It failed a CI run on PR #926 and 1 of 3 local runs.

        Fix: pre-create a decoy archive for BOTH seconds the second trim can possibly
        land in, so a collision is certain either way and the `-1` path is exercised
        deterministically. The non-destructiveness assertion — the property that actually
        matters — is unchanged and still unconditional.
        """
        from datetime import datetime, timezone, timedelta

        f = tmp_path / "proj.md"
        f.write_text("".join(f"first {i}\n" for i in range(12_000)))
        _run_handler(f)
        adir = tmp_path / ".notes-archive"
        real = sorted(adir.glob("proj.md.*.archive.md"))
        assert len(real) == 1, f"first trim should make exactly one archive: {real}"

        # Occupy the un-suffixed name for this second and the next, so whichever second
        # the second trim reads, its first candidate is already taken.
        now = datetime.now(timezone.utc)
        decoys = []
        for delta in (0, 1):
            ts = (now + timedelta(seconds=delta)).strftime("%Y-%m-%dT%H:%M:%SZ")
            decoy = adir / f"proj.md.{ts}.archive.md"
            if not decoy.exists():
                decoy.write_text("decoy\n")
                decoys.append(decoy)

        f.write_text("".join(f"second {i}\n" for i in range(12_000)))
        _run_handler(f)

        archives = sorted(adir.glob("proj.md.*.archive.md"))
        assert any(a.name.endswith("-1.archive.md") for a in archives), (
            f"the second trim did not take a collision-suffixed name: {archives}"
        )
        assert real[0].read_text(), "the first trim's archive was clobbered"
        for d in decoys:
            assert d.read_text() == "decoy\n", f"a pre-existing archive was clobbered: {d}"
        archives = [a for a in archives if a not in decoys]
        blob = "".join(a.read_text() for a in archives)
        assert "first 0\n" in blob and "second 0\n" in blob


class TestThresholdIsMeasuredInCharacters:
    """AC7: characters, not lines — the line metric was inverted 833x."""

    def test_many_short_lines_are_spared(self, tmp_path):
        f = tmp_path / "tiny.md"
        f.write_text("ok\n" * 801)          # 801 lines, ~2.4 KB
        before = f.read_bytes()
        stdout, _, _ = _run_handler(f)
        assert json.loads(stdout.strip())["trimmed"] is False
        assert f.read_bytes() == before

    def test_few_very_long_lines_are_trimmed(self, tmp_path):
        f = tmp_path / "huge.md"
        f.write_text(("x" * 20_000 + "\n") * 100)   # 100 lines, ~2.0 MB
        stdout, _, _ = _run_handler(f)
        assert json.loads(stdout.strip())["trimmed"] is True
        assert len(f.read_text()) < THRESHOLD_CHARS


class TestBoundaryAndEncodingGuards:
    """Round-1 review findings: boundaries that were asserted but never exercised."""

    def test_exactly_at_char_threshold_is_not_trimmed(self, tmp_path):
        """Genuinely 64,000 characters. The old test used a ~7 KB fixture, so a
        threshold regression from <= to < would not have failed anything."""
        f = tmp_path / "proj.md"
        f.write_text("a" * 63_999 + "\n")
        assert len(f.read_text()) == 64_000
        before = f.read_bytes()
        stdout, _, _ = _run_handler(f)
        assert json.loads(stdout.strip())["trimmed"] is False
        assert f.read_bytes() == before

    def test_one_char_over_threshold_is_considered(self, tmp_path):
        f = tmp_path / "proj.md"
        f.write_text("ab\n" * 21_334)          # 64,002 chars, many lines
        assert len(f.read_text()) == 64_002
        stdout, _, _ = _run_handler(f)
        assert json.loads(stdout.strip())["trimmed"] is True

    def test_single_oversized_line_is_not_grown(self, tmp_path):
        """A file that is one huge line has nothing to cut. Trimming it would
        prepend a header, make the file BIGGER, write an empty archive, and
        report success — on every run, forever."""
        f = tmp_path / "proj.md"
        f.write_text("a" * 64_001)
        before = f.read_bytes()
        stdout, _, _ = _run_handler(f)
        result = json.loads(stdout.strip())
        assert result["trimmed"] is False
        # "irreducible" — the single line IS the tail and is itself over the
        # threshold, so it is caught by the no-progress guard before the
        # empty-cut guard. Either refusal is correct; what matters is that the
        # file is untouched and no empty archive is written.
        assert result["reason"] in ("irreducible", "nothing_to_cut")
        assert f.read_bytes() == before
        assert not (tmp_path / ".notes-archive").exists(), "wrote an empty archive"

    def test_crlf_content_is_preserved_byte_for_byte(self, tmp_path):
        """Universal-newline translation silently ate every \\r, so the archive
        did not actually preserve what it archived."""
        f = tmp_path / "proj.md"
        original = ("x" * 40 + "\r\n") * 2_000        # ~84 KB, CRLF throughout
        f.write_bytes(original.encode("utf-8"))
        _run_handler(f)
        archives = list((tmp_path / ".notes-archive").glob("proj.md.*.archive.md"))
        assert len(archives) == 1
        recovered = archives[0].read_bytes() + f.read_bytes()
        assert b"\r\n" in archives[0].read_bytes(), "CRLF was normalised away"
        assert recovered.count(b"\r\n") == 2_000, (
            f"expected 2000 CRLF pairs across archive+file, got "
            f"{recovered.count(chr(13).encode() + chr(10).encode())}"
        )

    def test_crlf_file_is_measured_at_its_real_size(self, tmp_path):
        """A CRLF file at 64,002 stored chars must not read as 42,668."""
        f = tmp_path / "proj.md"
        f.write_bytes(("a\r\n" * 21_334).encode("utf-8"))
        stdout, _, _ = _run_handler(f)
        result = json.loads(stdout.strip())
        assert result["trimmed"] is True
        assert result["char_count"] == 64_002, result


class TestSelectTailBoundary:
    """select_tail is pure — test it directly at the exact cap."""

    def test_keeps_lines_that_fit_exactly_at_keep_chars(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("nsh", HANDLER_SCRIPT)
        nsh = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(nsh)
        lines = ["x" * 7_999 + "\n", "y" * 7_999 + "\n"]   # exactly 16,000 chars
        assert sum(len(l) for l in lines) == nsh.KEEP_CHARS
        assert nsh.select_tail(lines) == lines, (
            "a tail that fits exactly at the cap must not lose its oldest line"
        )


class TestRoundTwoReviewGuards:
    """Round-2 review: defenses that were asserted but passed for other reasons."""

    def _mod(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("nsh", HANDLER_SCRIPT)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        return m

    def test_is_excluded_pins_each_suffix_directly(self):
        """The file-level tests pass via PROJECT_NAME_RE's dotted-stem rejection
        even with a suffix removed from EXCLUDED_SUFFIXES, so pin the predicate."""
        nsh = self._mod()
        for name in ("epic-756-autorun-log.md", "sysop.handoff.md",
                     "notes.2026-08-03T00:00:00Z.archive.md"):
            assert nsh.is_excluded(Path("/x") / name), name
        for name in ("rawgentic.md", "testproj.md"):
            assert not nsh.is_excluded(Path("/x") / name), name

    def test_markdown_inside_a_decisions_dir_is_refused(self, tmp_path):
        """The earlier test used a .jsonl fixture, so it passed on the suffix
        check alone and could not detect losing the parent-directory guard."""
        d = tmp_path / "decisions"
        d.mkdir()
        f = d / "testproj.md"
        f.write_text("".join(f"line {i}\n" for i in range(12_000)))
        before = f.read_bytes()
        stdout, _, rc = _run_handler(f)
        assert rc == 0
        assert json.loads(stdout.strip())["reason"] == "not_a_notes_file"
        assert f.read_bytes() == before

    def test_huge_terminal_line_does_not_churn_archives(self, tmp_path):
        """`old\\n` + a 70,000-char line: the old code archived the tiny prefix,
        left the file oversized, and did it again every single session start."""
        f = tmp_path / "proj.md"
        f.write_text("old\n" + "z" * 70_000)
        stdout, _, _ = _run_handler(f)
        result = json.loads(stdout.strip())
        assert result["trimmed"] is False
        assert result["reason"] == "irreducible"

        # And it must still refuse on every subsequent run, creating no archives.
        for _ in range(3):
            _run_handler(f)
        adir = tmp_path / ".notes-archive"
        archives = list(adir.glob("*.archive.md")) if adir.exists() else []
        assert archives == [], f"archive churn: {archives}"

    def test_tail_plus_header_over_threshold_is_refused(self, tmp_path):
        """The guard must measure the RENDERED result. A tail that fits under
        the threshold but crosses back over once the header is prepended would
        otherwise 'trim' successfully and churn a new archive every session."""
        f = tmp_path / "proj.md"
        # Tail is exactly 64,000 chars: NOT > THRESHOLD_CHARS, so a tail-only
        # check passes it through. Only measuring the rendered result catches it.
        f.write_text("prefix\n" + "x" * 64_000)
        stdout, _, _ = _run_handler(f)
        result = json.loads(stdout.strip())
        assert result["trimmed"] is False
        assert result["reason"] == "irreducible"
        for _ in range(3):
            _run_handler(f)
        adir = tmp_path / ".notes-archive"
        assert (list(adir.glob("*.archive.md")) if adir.exists() else []) == []
