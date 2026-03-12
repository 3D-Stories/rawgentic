"""Tests for query-archive.py — JSONL session archive querying.

Tests the standalone Python script that searches structured JSONL session
archives. Uses subprocess invocation to match the hook testing pattern.
"""
import json
import subprocess
from pathlib import Path

from tests.hooks.conftest import HOOKS_DIR

# --- Synthetic test data ---

UNENRICHED_ENTRY = {
    "schema_version": 1,
    "archived_at": "2026-03-01T12:00:00Z",
    "source_file": "testproj.md",
    "line_count": 700,
    "note": "# Session Notes\n## Task: Fix auth middleware\nDecided to use JWT tokens.\nPattern: retry with backoff.\nEncountered CORS issue.",
    "insights": None,
}

ENRICHED_ENTRY = {
    "schema_version": 1,
    "archived_at": "2026-03-10T18:00:00Z",
    "source_file": "testproj.md",
    "line_count": 850,
    "note": "# Session Notes\n## Task: Refactor database layer\nMigrated from raw SQL to ORM.",
    "insights": {
        "summary": "Database layer refactoring with ORM migration",
        "sessions": [
            {
                "task": "WF4: Refactor database layer",
                "status": "COMPLETE",
                "patterns": ["retry with backoff", "connection pooling"],
                "decisions": ["chose SQLAlchemy over raw SQL", "kept migrations in alembic"],
                "artifacts": ["src/db/models.py", "migrations/001_initial.py"],
                "issues_encountered": ["N+1 query in user listing"],
            }
        ],
    },
}

OLD_ENTRY = {
    "schema_version": 1,
    "archived_at": "2026-01-15T08:00:00Z",
    "source_file": "testproj.md",
    "line_count": 500,
    "note": "# Old session\nEarly prototype work.",
    "insights": None,
}


def _make_archive(tmp_path, project="testproj", entries=None):
    """Create an archive directory with JSONL data."""
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    if entries:
        jsonl_file = archive_dir / f"{project}.jsonl"
        jsonl_file.write_text(
            "\n".join(json.dumps(e) for e in entries) + "\n"
        )
    return archive_dir


def _run_query(archive_dir, *args, timeout=10):
    """Run query-archive.py as a subprocess."""
    cmd = ["python3", str(HOOKS_DIR / "query-archive.py"), str(archive_dir)] + list(args)
    result = subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout
    )
    return result.stdout, result.stderr, result.returncode


class TestKeywordSearch:
    def test_keyword_matches_note_text(self, tmp_path):
        archive_dir = _make_archive(tmp_path, entries=[UNENRICHED_ENTRY])
        stdout, _, rc = _run_query(archive_dir, "--keyword", "JWT")
        assert rc == 0
        results = json.loads(stdout)
        assert len(results) == 1
        assert "JWT" in results[0]["match_context"]

    def test_keyword_matches_enriched_summary(self, tmp_path):
        archive_dir = _make_archive(tmp_path, entries=[ENRICHED_ENTRY])
        stdout, _, rc = _run_query(archive_dir, "--keyword", "ORM migration")
        assert rc == 0
        results = json.loads(stdout)
        assert len(results) == 1

    def test_keyword_matches_enriched_patterns(self, tmp_path):
        archive_dir = _make_archive(tmp_path, entries=[ENRICHED_ENTRY])
        stdout, _, rc = _run_query(archive_dir, "--keyword", "connection pooling")
        assert rc == 0
        results = json.loads(stdout)
        assert len(results) == 1

    def test_keyword_no_match_returns_empty(self, tmp_path):
        archive_dir = _make_archive(tmp_path, entries=[UNENRICHED_ENTRY])
        stdout, _, rc = _run_query(archive_dir, "--keyword", "nonexistent_xyz")
        assert rc == 0
        assert json.loads(stdout) == []

    def test_keyword_regex_support(self, tmp_path):
        archive_dir = _make_archive(tmp_path, entries=[UNENRICHED_ENTRY])
        stdout, _, rc = _run_query(archive_dir, "--keyword", "JWT|CORS")
        assert rc == 0
        results = json.loads(stdout)
        assert len(results) == 1  # both match same entry


class TestStructuredSearch:
    def test_pattern_searches_insights_only(self, tmp_path):
        archive_dir = _make_archive(tmp_path, entries=[ENRICHED_ENTRY])
        stdout, _, rc = _run_query(archive_dir, "--pattern", "connection pooling")
        assert rc == 0
        results = json.loads(stdout)
        assert len(results) == 1

    def test_pattern_skips_unenriched(self, tmp_path):
        archive_dir = _make_archive(tmp_path, entries=[UNENRICHED_ENTRY])
        stdout, _, rc = _run_query(archive_dir, "--pattern", "retry")
        assert rc == 0
        assert json.loads(stdout) == []

    def test_decision_searches_insights_only(self, tmp_path):
        archive_dir = _make_archive(tmp_path, entries=[ENRICHED_ENTRY])
        stdout, _, rc = _run_query(archive_dir, "--decision", "SQLAlchemy")
        assert rc == 0
        results = json.loads(stdout)
        assert len(results) == 1

    def test_decision_skips_unenriched(self, tmp_path):
        archive_dir = _make_archive(tmp_path, entries=[UNENRICHED_ENTRY])
        stdout, _, rc = _run_query(archive_dir, "--decision", "JWT")
        assert rc == 0
        assert json.loads(stdout) == []

    def test_artifact_searches_insights_and_note(self, tmp_path):
        archive_dir = _make_archive(tmp_path, entries=[ENRICHED_ENTRY])
        stdout, _, rc = _run_query(archive_dir, "--artifact", "models.py")
        assert rc == 0
        results = json.loads(stdout)
        assert len(results) == 1

    def test_artifact_falls_back_to_note_for_unenriched(self, tmp_path):
        archive_dir = _make_archive(tmp_path, entries=[UNENRICHED_ENTRY])
        stdout, _, rc = _run_query(archive_dir, "--artifact", "auth middleware")
        assert rc == 0
        results = json.loads(stdout)
        assert len(results) == 1


class TestFiltering:
    def test_project_filter(self, tmp_path):
        archive_dir = tmp_path / "archive"
        archive_dir.mkdir()
        # Two project JSONL files
        (archive_dir / "projA.jsonl").write_text(json.dumps(UNENRICHED_ENTRY) + "\n")
        entry_b = dict(ENRICHED_ENTRY, source_file="projB.md")
        (archive_dir / "projB.jsonl").write_text(json.dumps(entry_b) + "\n")

        stdout, _, rc = _run_query(archive_dir, "--keyword", ".", "--project", "projA")
        assert rc == 0
        results = json.loads(stdout)
        assert len(results) == 1
        assert results[0]["source_file"] == "testproj.md"

    def test_since_filter(self, tmp_path):
        archive_dir = _make_archive(
            tmp_path, entries=[OLD_ENTRY, UNENRICHED_ENTRY, ENRICHED_ENTRY]
        )
        stdout, _, rc = _run_query(
            archive_dir, "--keyword", ".", "--since", "2026-02-01"
        )
        assert rc == 0
        results = json.loads(stdout)
        # OLD_ENTRY is 2026-01-15, should be filtered out
        assert len(results) == 2

    def test_limit(self, tmp_path):
        archive_dir = _make_archive(
            tmp_path, entries=[OLD_ENTRY, UNENRICHED_ENTRY, ENRICHED_ENTRY]
        )
        stdout, _, rc = _run_query(
            archive_dir, "--keyword", ".", "--limit", "1"
        )
        assert rc == 0
        results = json.loads(stdout)
        assert len(results) == 1


class TestOutputFormats:
    def test_brief_format_has_summary(self, tmp_path):
        archive_dir = _make_archive(tmp_path, entries=[ENRICHED_ENTRY])
        stdout, _, rc = _run_query(
            archive_dir, "--keyword", ".", "--format", "brief"
        )
        assert rc == 0
        results = json.loads(stdout)
        assert len(results) == 1
        result = results[0]
        assert "archived_at" in result
        assert "source_file" in result
        assert "summary" in result
        assert "match_context" in result
        # Brief should NOT include full note
        assert "note" not in result

    def test_brief_uses_insights_summary_when_enriched(self, tmp_path):
        archive_dir = _make_archive(tmp_path, entries=[ENRICHED_ENTRY])
        stdout, _, rc = _run_query(
            archive_dir, "--keyword", ".", "--format", "brief"
        )
        assert rc == 0
        results = json.loads(stdout)
        assert results[0]["summary"] == "Database layer refactoring with ORM migration"

    def test_brief_uses_note_prefix_when_unenriched(self, tmp_path):
        archive_dir = _make_archive(tmp_path, entries=[UNENRICHED_ENTRY])
        stdout, _, rc = _run_query(
            archive_dir, "--keyword", ".", "--format", "brief"
        )
        assert rc == 0
        results = json.loads(stdout)
        assert len(results[0]["summary"]) <= 100

    def test_full_format_includes_note(self, tmp_path):
        archive_dir = _make_archive(tmp_path, entries=[ENRICHED_ENTRY])
        stdout, _, rc = _run_query(
            archive_dir, "--keyword", ".", "--format", "full"
        )
        assert rc == 0
        results = json.loads(stdout)
        result = results[0]
        assert "note" in result
        assert "insights" in result

    def test_default_format_is_brief(self, tmp_path):
        archive_dir = _make_archive(tmp_path, entries=[UNENRICHED_ENTRY])
        stdout, _, rc = _run_query(archive_dir, "--keyword", ".")
        assert rc == 0
        results = json.loads(stdout)
        # Default brief: no 'note' key
        assert "note" not in results[0]


class TestErrorHandling:
    def test_invalid_regex_exits_1(self, tmp_path):
        archive_dir = _make_archive(tmp_path, entries=[UNENRICHED_ENTRY])
        stdout, _, rc = _run_query(archive_dir, "--keyword", "[invalid")
        assert rc == 1
        error = json.loads(stdout)
        assert "error" in error

    def test_empty_archive_returns_empty(self, tmp_path):
        archive_dir = tmp_path / "archive"
        archive_dir.mkdir()
        stdout, _, rc = _run_query(archive_dir, "--keyword", "anything")
        assert rc == 0
        assert json.loads(stdout) == []

    def test_malformed_jsonl_lines_skipped(self, tmp_path):
        archive_dir = tmp_path / "archive"
        archive_dir.mkdir()
        jsonl = archive_dir / "testproj.jsonl"
        jsonl.write_text(
            "not valid json\n"
            + json.dumps(UNENRICHED_ENTRY) + "\n"
            + "{broken\n"
        )
        stdout, _, rc = _run_query(archive_dir, "--keyword", "JWT")
        assert rc == 0
        results = json.loads(stdout)
        assert len(results) == 1

    def test_no_search_flags_exits_1(self, tmp_path):
        archive_dir = _make_archive(tmp_path, entries=[UNENRICHED_ENTRY])
        stdout, _, rc = _run_query(archive_dir)
        assert rc == 1

    def test_nonexistent_archive_dir(self, tmp_path):
        stdout, _, rc = _run_query(tmp_path / "nonexistent", "--keyword", "x")
        assert rc == 0
        assert json.loads(stdout) == []

    def test_invalid_project_name_rejected(self, tmp_path):
        archive_dir = _make_archive(tmp_path, entries=[UNENRICHED_ENTRY])
        stdout, _, rc = _run_query(archive_dir, "--keyword", "x", "--project", "../../etc")
        assert rc == 1
        error = json.loads(stdout)
        assert "error" in error


class TestMultiProject:
    def test_searches_all_projects_without_filter(self, tmp_path):
        archive_dir = tmp_path / "archive"
        archive_dir.mkdir()
        (archive_dir / "projA.jsonl").write_text(json.dumps(UNENRICHED_ENTRY) + "\n")
        entry_b = dict(ENRICHED_ENTRY, source_file="projB.md")
        (archive_dir / "projB.jsonl").write_text(json.dumps(entry_b) + "\n")

        stdout, _, rc = _run_query(archive_dir, "--keyword", ".")
        assert rc == 0
        results = json.loads(stdout)
        assert len(results) == 2
