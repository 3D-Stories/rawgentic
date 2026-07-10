"""Tests for hooks/session_index.py — FTS5 session index (#375).

Pure core tested by direct import; CLI tested black-box via subprocess with
tmp_path corpora, per docs/testing.md. No sqlite3 CLI dependency (absent on
some hosts) — all DB assertions use Python's sqlite3.
"""
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parents[2] / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

import session_index as si  # noqa: E402

CLI = HOOKS_DIR / "session_index.py"

# The exact real-corpus timestamp shape (self-review finding: a hand-made shape
# would let a systematic Z-parse failure stay green).
REAL_TS = "2026-06-18T03:48:27.784Z"
REAL_TS_US = 1781754507784000


# ---------------------------------------------------------------- pure core

class TestParseTsUs:
    def test_real_corpus_shape(self):
        assert si.parse_ts_us(REAL_TS) == REAL_TS_US

    def test_offset_form(self):
        assert si.parse_ts_us("2026-06-18T03:48:27.784+00:00") == REAL_TS_US

    def test_invalid_returns_none(self):
        assert si.parse_ts_us("not-a-date") is None

    def test_empty_and_nonstring(self):
        assert si.parse_ts_us("") is None
        assert si.parse_ts_us(None) is None
        assert si.parse_ts_us(12345) is None


class TestExtractMessage:
    def _line(self, **over):
        base = {
            "type": "user",
            "sessionId": "s-1",
            "timestamp": REAL_TS,
            "uuid": "u-1",
            "message": {"role": "user", "content": "hello world"},
        }
        base.update(over)
        return base

    def test_user_string_content(self):
        m = si.extract_message(self._line())
        assert m is not None
        assert m.role == "user"
        assert m.text == "hello world"
        assert m.session_id == "s-1"
        assert m.ts == REAL_TS
        assert m.ts_us == REAL_TS_US
        assert m.uuid == "u-1"

    def test_assistant_block_list_joins_text_blocks_only(self):
        m = si.extract_message(self._line(
            type="assistant",
            message={"role": "assistant", "content": [
                {"type": "thinking", "thinking": "secret reasoning"},
                {"type": "text", "text": "first"},
                {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}},
                {"type": "text", "text": "second"},
            ]},
        ))
        assert m is not None
        assert m.text == "first\n\nsecond"
        assert "secret reasoning" not in m.text
        assert "ls" not in m.text

    def test_non_message_types_ignored(self):
        for t in ("mode", "file-history-snapshot", "attachment", "system",
                  "queue-operation", "pr-link"):
            assert si.extract_message({"type": t}) is si.IGNORED

    def test_missing_session_id_rejected(self):
        line = self._line()
        del line["sessionId"]
        assert si.extract_message(line) is si.REJECTED

    def test_missing_timestamp_rejected(self):
        line = self._line()
        del line["timestamp"]
        assert si.extract_message(line) is si.REJECTED

    def test_unparseable_timestamp_rejected(self):
        assert si.extract_message(self._line(timestamp="garbage")) is si.REJECTED

    def test_textless_with_good_provenance_ignored(self):
        # tool_result/tool_use/thinking-only messages are EXPECTED shapes on
        # the real corpus (77% of message lines) — ignored, never "rejected",
        # so the format-drift guard measures real shape failures only.
        assert si.extract_message(self._line(
            message={"role": "user", "content": ""})) is si.IGNORED
        assert si.extract_message(self._line(
            type="assistant",
            message={"role": "assistant", "content": [
                {"type": "tool_use", "name": "X", "input": {}}]})) is si.IGNORED
        assert si.extract_message(self._line(
            message={"role": "user", "content": [
                {"type": "tool_result", "content": "big blob"}]})) is si.IGNORED

    def test_weird_content_shapes_rejected_never_raise(self):
        for content in (None, 42, {"a": 1}):
            assert si.extract_message(self._line(
                message={"role": "user", "content": content})) is si.REJECTED
        assert si.extract_message(self._line(message=None)) is si.REJECTED
        assert si.extract_message(self._line(message="odd")) is si.REJECTED
        # a block list with junk entries but one good text block still extracts
        m = si.extract_message(self._line(
            message={"role": "user",
                     "content": [None, 42, {"type": "text", "text": "ok"}]}))
        assert m.text == "ok"


class TestLiteralQuote:
    def test_plain(self):
        assert si.literal_quote("fox jumps") == '"fox jumps"'

    def test_embedded_double_quote_doubled(self):
        assert si.literal_quote('say "hi" now') == '"say ""hi"" now"'


class TestResolveWorkspaceRoot:
    def test_found(self, tmp_path):
        (tmp_path / ".rawgentic_workspace.json").write_text("{}")
        sub = tmp_path / "projects" / "x"
        sub.mkdir(parents=True)
        assert si.resolve_workspace_root(sub) == tmp_path

    def test_not_found(self, tmp_path):
        assert si.resolve_workspace_root(tmp_path) is None


class TestPlanChanges:
    def test_new_changed_unchanged_vanished(self):
        stored = {
            "/a.jsonl": (100, 10),
            "/b.jsonl": (200, 20),
            "/gone.jsonl": (300, 30),
        }
        on_disk = {
            "/a.jsonl": (100, 10),   # unchanged
            "/b.jsonl": (201, 20),   # mtime changed
            "/new.jsonl": (400, 40),  # new
        }
        plan = si.plan_changes(stored, on_disk)
        assert plan.unchanged == ["/a.jsonl"]
        assert plan.dirty == ["/b.jsonl", "/new.jsonl"]
        assert plan.vanished == ["/gone.jsonl"]

    def test_size_change_is_dirty(self):
        plan = si.plan_changes({"/a": (1, 1)}, {"/a": (1, 2)})
        assert plan.dirty == ["/a"]


class TestFormatDrift:
    def test_below_threshold_or_few_lines_no_warn(self):
        assert not si.format_drift_warning(indexed=99, rejected=1)
        assert not si.format_drift_warning(indexed=1, rejected=50)  # <100 seen

    def test_above_threshold_warns(self):
        assert si.format_drift_warning(indexed=10, rejected=90)


# ------------------------------------------------------------- CLI fixtures

def make_corpus(root: Path, project: str, name: str, lines) -> Path:
    d = root / project
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"{name}.jsonl"
    f.write_text("\n".join(
        line if isinstance(line, str) else json.dumps(line)
        for line in lines) + "\n")
    return f


def msg(text, sid="s-1", ts=REAL_TS, typ="user"):
    content = text if typ == "user" else [{"type": "text", "text": text}]
    return {"type": typ, "sessionId": sid, "timestamp": ts, "uuid": f"u-{sid}",
            "message": {"role": typ, "content": content}}


def run_cli(*args, cwd=None):
    return subprocess.run(
        [sys.executable, str(CLI), *args],
        capture_output=True, text=True, cwd=cwd)


@pytest.fixture()
def env(tmp_path):
    corpus = tmp_path / "projects"
    corpus.mkdir()
    db = tmp_path / "idx" / "sessions.db"
    make_corpus(corpus, "-proj-alpha", "sess1", [
        msg("the quick brown fox"),
        msg("assistant answer about databases", typ="assistant"),
        {"type": "mode", "mode": "normal"},                 # ignored (non-message)
        "{ this is not json",                                # malformed
        {"type": "user", "sessionId": "s-1", "timestamp": REAL_TS,
         "message": {"role": "user", "content": 42}},        # rejected (shape)
    ])
    make_corpus(corpus, "-proj-beta", "sess2", [
        msg("lazy dog sleeps", sid="s-2", ts="2026-07-01T10:00:00.000Z"),
    ])
    return corpus, db


# ---------------------------------------------------------------- CLI: index

class TestIndexCLI:
    def test_first_run_indexes_and_reports(self, env):
        corpus, db = env
        r = run_cli("index", "--projects-dir", str(corpus), "--db", str(db))
        assert r.returncode == 0, r.stderr
        assert db.exists()
        con = sqlite3.connect(db)
        assert con.execute("SELECT count(*) FROM messages").fetchone()[0] == 3
        files = con.execute(
            "SELECT malformed_count, ignored_count, rejected_count FROM files "
            "WHERE path LIKE '%sess1%'").fetchone()
        assert files == (1, 1, 1)

    def test_incremental_skips_unchanged(self, env):
        corpus, db = env
        run_cli("index", "--projects-dir", str(corpus), "--db", str(db))
        r = run_cli("index", "--projects-dir", str(corpus), "--db", str(db))
        assert r.returncode == 0
        assert "0 files indexed" in r.stdout or "unchanged" in r.stdout

    def test_incremental_equals_rebuild_after_modify(self, env, tmp_path):
        corpus, db = env
        run_cli("index", "--projects-dir", str(corpus), "--db", str(db))
        # modify one file (append) + add a new file
        f = corpus / "-proj-alpha" / "sess1.jsonl"
        with f.open("a") as fh:
            fh.write(json.dumps(msg("appended zebra insight")) + "\n")
        make_corpus(corpus, "-proj-alpha", "sess3", [msg("gamma ray", sid="s-3")])
        run_cli("index", "--projects-dir", str(corpus), "--db", str(db))
        # fresh rebuild into a second db
        db2 = tmp_path / "idx2" / "sessions.db"
        run_cli("index", "--projects-dir", str(corpus), "--db", str(db2))

        def results(d):
            r = run_cli("search", "zebra OR fox OR gamma OR dog OR databases",
                        "--db", str(d), "--json")
            assert r.returncode == 0, r.stderr
            rows = json.loads(r.stdout)["results"]
            return [(x["session_id"], x["ts"], x["project"], x["role"],
                     x["line_no"]) for x in rows]

        assert results(db) == results(db2)
        assert len(results(db)) == 5

    def test_vanished_file_rows_leave_fts(self, env):
        corpus, db = env
        run_cli("index", "--projects-dir", str(corpus), "--db", str(db))
        (corpus / "-proj-beta" / "sess2.jsonl").unlink()
        run_cli("index", "--projects-dir", str(corpus), "--db", str(db))
        r = run_cli("search", "lazy dog", "--db", str(db), "--json")
        assert json.loads(r.stdout)["results"] == []

    def test_version_mismatch_exits_2_and_rebuild_recovers(self, env):
        corpus, db = env
        run_cli("index", "--projects-dir", str(corpus), "--db", str(db))
        con = sqlite3.connect(db)
        con.execute("UPDATE meta SET value='0' WHERE key='parser_version'")
        con.commit(); con.close()
        r = run_cli("index", "--projects-dir", str(corpus), "--db", str(db))
        assert r.returncode == 2
        assert "--rebuild" in r.stderr
        r2 = run_cli("index", "--projects-dir", str(corpus), "--db", str(db),
                     "--rebuild")
        assert r2.returncode == 0

    def test_writer_lock_excludes_second_indexer(self, env):
        corpus, db = env
        run_cli("index", "--projects-dir", str(corpus), "--db", str(db))
        import fcntl
        lock_path = db.parent / (db.name + ".lock")
        with open(lock_path, "w") as lk:
            fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
            r = run_cli("index", "--projects-dir", str(corpus), "--db", str(db))
            assert r.returncode == 3
            assert "in progress" in r.stderr

    def test_permissions(self, env):
        corpus, db = env
        run_cli("index", "--projects-dir", str(corpus), "--db", str(db))
        assert oct(db.parent.stat().st_mode & 0o777) == "0o700"
        assert oct(db.stat().st_mode & 0o777) == "0o600"

    def test_db_symlink_destination_refused(self, env, tmp_path):
        corpus, _ = env
        real = tmp_path / "real.db"
        real.touch()
        link = tmp_path / "link.db"
        link.symlink_to(real)
        r = run_cli("index", "--projects-dir", str(corpus), "--db", str(link))
        assert r.returncode == 2
        assert "symlink" in r.stderr.lower()

    def test_wal_journal_mode_asserted(self, env):
        corpus, db = env
        r = run_cli("index", "--projects-dir", str(corpus), "--db", str(db))
        assert r.returncode == 0
        con = sqlite3.connect(db)
        assert con.execute("PRAGMA journal_mode").fetchone()[0] == "wal"

    def test_format_drift_warning_on_stderr(self, env, tmp_path):
        corpus = tmp_path / "drift-corpus"
        # 120 message-typed lines, all missing timestamps -> all rejected
        make_corpus(corpus, "-proj-d", "bad", [
            {"type": "user", "sessionId": f"s{i}",
             "message": {"role": "user", "content": f"text {i}"}}
            for i in range(120)])
        db = tmp_path / "drift-idx" / "sessions.db"
        r = run_cli("index", "--projects-dir", str(corpus), "--db", str(db))
        assert r.returncode == 0  # fail-soft (AC3)
        assert "format drift" in r.stderr.lower()

    def test_concurrent_reader_consistent_during_write(self, env):
        """AC4: a reader sees a consistent snapshot while a write txn is open."""
        corpus, db = env
        run_cli("index", "--projects-dir", str(corpus), "--db", str(db))
        w = sqlite3.connect(db)
        w.execute("BEGIN IMMEDIATE")
        w.execute(
            "INSERT INTO messages(file_id, line_no, session_id, ts, ts_us, "
            "project, role, uuid, text) SELECT file_id, 999, 'sx', ts, ts_us, "
            "project, role, 'ux', 'uncommitted phantom' FROM messages LIMIT 1")
        try:
            r = run_cli("search", "phantom", "--db", str(db), "--json")
            assert r.returncode == 0
            assert json.loads(r.stdout)["results"] == []
            r2 = run_cli("search", "fox", "--db", str(db), "--json")
            assert len(json.loads(r2.stdout)["results"]) == 1
        finally:
            w.rollback(); w.close()


# --------------------------------------------------------------- CLI: search

class TestSearchCLI:
    def test_provenance_fields(self, env):
        corpus, db = env
        run_cli("index", "--projects-dir", str(corpus), "--db", str(db))
        r = run_cli("search", "fox", "--db", str(db), "--json")
        row = json.loads(r.stdout)["results"][0]
        assert row["session_id"] == "s-1"
        assert row["ts"] == REAL_TS
        assert row["project"] == "-proj-alpha"
        assert row["role"] == "user"
        assert "fox" in row["snippet"]
        assert row["line_no"] == 1

    def test_project_filter(self, env):
        corpus, db = env
        run_cli("index", "--projects-dir", str(corpus), "--db", str(db))
        r = run_cli("search", "fox OR dog", "--db", str(db), "--json",
                    "--project", "-proj-beta")
        rows = json.loads(r.stdout)["results"]
        assert [x["project"] for x in rows] == ["-proj-beta"]

    def test_date_filters_inclusive(self, env):
        corpus, db = env
        run_cli("index", "--projects-dir", str(corpus), "--db", str(db))
        r = run_cli("search", "fox OR dog", "--db", str(db), "--json",
                    "--since", "2026-07-01", "--until", "2026-07-01")
        rows = json.loads(r.stdout)["results"]
        assert len(rows) == 1 and rows[0]["session_id"] == "s-2"

    def test_literal_handles_embedded_quote(self, env):
        corpus, db = env
        run_cli("index", "--projects-dir", str(corpus), "--db", str(db))
        r = run_cli("search", 'fox "quoted', "--db", str(db), "--literal",
                    "--json")
        assert r.returncode == 0  # no FTS syntax error
        assert json.loads(r.stdout)["results"] == []

    def test_fts_syntax_error_exit_2_no_traceback(self, env):
        corpus, db = env
        run_cli("index", "--projects-dir", str(corpus), "--db", str(db))
        r = run_cli("search", 'AND NOT (', "--db", str(db))
        assert r.returncode == 2
        assert "Traceback" not in r.stderr
        assert "syntax" in r.stderr.lower()

    def test_missing_db_exit_2_hint(self, tmp_path):
        r = run_cli("search", "x", "--db", str(tmp_path / "nope.db"))
        assert r.returncode == 2
        assert "index" in r.stderr.lower()

    def test_human_output_default(self, env):
        corpus, db = env
        run_cli("index", "--projects-dir", str(corpus), "--db", str(db))
        r = run_cli("search", "fox", "--db", str(db))
        assert r.returncode == 0
        assert "s-1" in r.stdout and "-proj-alpha" in r.stdout


# --------------------------------------------------------------- CLI: status

class TestStatusCLI:
    def test_status_fields(self, env):
        corpus, db = env
        run_cli("index", "--projects-dir", str(corpus), "--db", str(db))
        r = run_cli("status", "--db", str(db), "--projects-dir", str(corpus))
        assert r.returncode == 0
        out = r.stdout
        for token in ("schema_version", "parser_version", "files: 2",
                      "messages: 3", "malformed: 1", "ignored: 1",
                      "rejected: 1", "stale"):
            assert token in out, f"missing {token!r} in:\n{out}"

    def test_status_detects_staleness(self, env):
        corpus, db = env
        run_cli("index", "--projects-dir", str(corpus), "--db", str(db))
        make_corpus(corpus, "-proj-alpha", "sessNEW", [msg("new stuff")])
        r = run_cli("status", "--db", str(db), "--projects-dir", str(corpus))
        assert "new: 1" in r.stdout


class TestNestedCorpus:
    def test_subagent_files_indexed_with_top_project(self, env):
        """Real corpus nests: project/session-dir/subagents/agent-X.jsonl."""
        corpus, db = env
        nested = corpus / "-proj-alpha" / "sess-dir" / "subagents"
        nested.mkdir(parents=True)
        (nested / "agent-1.jsonl").write_text(
            json.dumps(msg("nested subagent wisdom", sid="s-sub")) + "\n")
        r = run_cli("index", "--projects-dir", str(corpus), "--db", str(db))
        assert r.returncode == 0, r.stderr
        rj = run_cli("search", "nested subagent wisdom", "--db", str(db),
                     "--literal", "--json")
        rows = json.loads(rj.stdout)["results"]
        assert len(rows) == 1
        assert rows[0]["project"] == "-proj-alpha"  # top-level dir, not "subagents"

    def test_status_never_mutates(self, env):
        corpus, db = env
        run_cli("index", "--projects-dir", str(corpus), "--db", str(db))
        before = db.read_bytes()
        make_corpus(corpus, "-proj-alpha", "sessNEW", [msg("new stuff")])
        run_cli("status", "--db", str(db), "--projects-dir", str(corpus))
        assert db.read_bytes() == before


# ----------------------------------------------------- Step 8a finding tests

class TestIndexGuards:
    def test_missing_projects_dir_exit_2_no_prune(self, env, tmp_path):
        corpus, db = env
        run_cli("index", "--projects-dir", str(corpus), "--db", str(db))
        r = run_cli("index", "--projects-dir", str(tmp_path / "typo-nope"),
                    "--db", str(db))
        assert r.returncode == 2
        assert "projects" in r.stderr.lower()
        con = sqlite3.connect(db)
        assert con.execute("SELECT count(*) FROM messages").fetchone()[0] == 3

    def test_mass_vanish_refused(self, env):
        corpus, db = env
        run_cli("index", "--projects-dir", str(corpus), "--db", str(db))
        for f in corpus.glob("*/*.jsonl"):
            f.unlink()
        r = run_cli("index", "--projects-dir", str(corpus), "--db", str(db))
        assert r.returncode == 2
        assert "vanish" in r.stderr.lower()
        con = sqlite3.connect(db)
        assert con.execute("SELECT count(*) FROM messages").fetchone()[0] == 3
        r2 = run_cli("index", "--projects-dir", str(corpus), "--db", str(db),
                     "--rebuild")
        assert r2.returncode == 0
        con2 = sqlite3.connect(db)
        assert con2.execute("SELECT count(*) FROM messages").fetchone()[0] == 0

    def test_lone_surrogate_fails_soft(self, env, tmp_path):
        corpus, db = env
        make_corpus(corpus, "-proj-alpha", "surr", [
            msg("good line before"),
            json.dumps({"type": "user", "sessionId": "s-9",
                        "timestamp": REAL_TS, "uuid": "u-9",
                        "message": {"role": "user",
                                    "content": "bad \ud800 text"}}),
            msg("good line after", sid="s-9"),
        ])
        r = run_cli("index", "--projects-dir", str(corpus), "--db", str(db))
        assert r.returncode == 0, r.stderr
        assert "Traceback" not in r.stderr
        rj = run_cli("search", "good line", "--db", str(db), "--json",
                     "--literal")
        # both good lines indexed; surrogate line stored sanitized or rejected
        assert len(json.loads(rj.stdout)["results"]) == 2

    def test_bad_date_filter_exit_2(self, env):
        corpus, db = env
        run_cli("index", "--projects-dir", str(corpus), "--db", str(db))
        for flag in ("--since", "--until"):
            r = run_cli("search", "fox", "--db", str(db), flag, "2026-13-45")
            assert r.returncode == 2, (flag, r.stdout)
            assert "date" in r.stderr.lower()

    def test_limit_must_be_positive(self, env):
        corpus, db = env
        run_cli("index", "--projects-dir", str(corpus), "--db", str(db))
        r = run_cli("search", "fox", "--db", str(db), "--limit", "-1")
        assert r.returncode == 2

    def test_existing_db_perms_retightened(self, env):
        corpus, db = env
        run_cli("index", "--projects-dir", str(corpus), "--db", str(db))
        os.chmod(db, 0o644)
        run_cli("index", "--projects-dir", str(corpus), "--db", str(db))
        assert oct(db.stat().st_mode & 0o777) == "0o600"


class TestNoIndexDataInRepo:
    def test_repo_tree_contains_no_index_artifacts(self):
        """AC7: the DB is a derived store outside the repo — no index file may
        ever land in the repo tree."""
        repo = Path(__file__).resolve().parents[2]
        hits = [p for p in repo.rglob("*")
                if ".git" not in p.parts
                and (".session-index" in p.name or
                     p.name.startswith("sessions.db"))]
        assert hits == [], f"index artifacts inside repo: {hits}"
