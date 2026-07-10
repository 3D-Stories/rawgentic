"""Tests for hooks/session_mining_lib.py — WF17 session mining (#376).

Pure core by direct import; CLI black-box via subprocess (added with the CLI
task). Queue fixtures use tmp_path; no real corpus/queue is ever touched.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parents[2] / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

import session_mining_lib as sm  # noqa: E402

CLI = HOOKS_DIR / "session_mining_lib.py"

SID_A = "aaaaaaaa-1111-2222-3333-444444444444"
SID_B = "bbbbbbbb-1111-2222-3333-444444444444"
SID_C = "cccccccc-1111-2222-3333-444444444444"


def row(sid, text_snippet="x [permission denied] y", **over):
    base = {"session_id": sid, "ts": "2026-07-01T10:00:00.000Z",
            "project": "-proj", "role": "user", "snippet": text_snippet,
            "path": f"/corpus/-proj/{sid}.jsonl", "line_no": 1, "score": -1.0}
    base.update(over)
    return base


# ---------------------------------------------------------------- normalize

class TestNormalize:
    def test_pattern_lowercase_ws_punct(self):
        assert sm.normalize_pattern("  Permission   DENIED! ") == "permission denied"

    def test_command_first_two_tokens(self):
        assert sm.normalize_command("git push -u origin feature/x") == "git push"
        assert sm.normalize_command("pytest") == "pytest"


class TestCandidateKey:
    def test_deterministic(self):
        k1 = sm.candidate_key("friction", "permission denied")
        k2 = sm.candidate_key("friction", "permission denied")
        assert k1 == k2 and len(k1) == 64

    def test_detector_and_pattern_vary_key(self):
        base = sm.candidate_key("friction", "permission denied")
        assert sm.candidate_key("note_commands", "permission denied") != base
        assert sm.candidate_key("friction", "command not found") != base

    def test_identity_version_in_key(self):
        # bumping IDENTITY_VERSION must change every key (deliberate reset)
        k = sm.candidate_key("friction", "x")
        old = sm.IDENTITY_VERSION
        try:
            sm.IDENTITY_VERSION = old + 1
            assert sm.candidate_key("friction", "x") != k
        finally:
            sm.IDENTITY_VERSION = old


# ---------------------------------------------------------------- detectors

class TestDetectFriction:
    def test_signals_carry_provenance(self):
        sigs = sm.detect_friction("permission denied", [row(SID_A), row(SID_B)])
        assert len(sigs) == 2
        s = sigs[0]
        assert s.detector == "friction"
        assert s.canonical_pattern == "permission denied"
        assert s.session_id == SID_A
        assert s.source == "index"

    def test_rejects_rows_missing_session(self):
        sigs = sm.detect_friction("x", [row(None)])
        assert sigs == []


class TestDetectNoteCommands:
    NOTES = f"""
## Section one ({SID_A})
Ran `git push -u origin feat` twice.
```bash
pytest tests/ -q
```
## Section two — no session id here
Also ran `docker compose up` again.
"""

    def test_same_section_uuid_resolves(self):
        sigs = sm.detect_note_commands(self.NOTES)
        by_pattern = {s.canonical_pattern: s for s in sigs}
        assert by_pattern["git push"].session_id == SID_A
        assert by_pattern["pytest tests/"].session_id == SID_A

    def test_no_uuid_is_evidence_only(self):
        sigs = sm.detect_note_commands(self.NOTES)
        docker = [s for s in sigs if s.canonical_pattern == "docker compose"]
        assert docker and docker[0].session_id is None


# --------------------------------------------------------------- recurrence

class TestRecurrence:
    def _sig(self, sid, pattern="permission denied"):
        return sm.Signal(detector="friction", canonical_pattern=pattern,
                         session_id=sid, ts="2026-07-01T00:00:00Z",
                         quote="q", source="index")

    def test_distinct_sessions_one_vote_each(self):
        sigs = [self._sig(SID_A), self._sig(SID_A), self._sig(SID_B),
                self._sig(SID_C)]
        rec = sm.recurrence(sigs)
        assert rec["permission denied"].distinct_sessions == 3

    def test_evidence_only_signals_never_count(self):
        sigs = [self._sig(SID_A), self._sig(None), self._sig(None)]
        rec = sm.recurrence(sigs)
        assert rec["permission denied"].distinct_sessions == 1


# ---------------------------------------------------------------- redaction

class TestRedactEvidence:
    def test_long_hex_masked(self):
        out = sm.redact_evidence("token deadbeefdeadbeefdeadbeef1234 end")
        assert "deadbeef" not in out and "[redacted:" in out

    def test_key_value_masked(self):
        out = sm.redact_evidence("export API_TOKEN=supersecretvalue123")
        assert "supersecretvalue123" not in out

    def test_bearer_masked(self):
        out = sm.redact_evidence("Authorization: Bearer abc.def.ghi123456")
        assert "abc.def.ghi123456" not in out

    def test_plain_text_untouched(self):
        assert sm.redact_evidence("the quick brown fox") == "the quick brown fox"


# ------------------------------------------------------------------- dedupe

class TestDedupe:
    def _cand(self, pattern="mine sessions", title="session miner"):
        key = sm.candidate_key("friction", pattern)
        return sm.Candidate(candidate_key=key, detector="friction",
                            canonical_pattern=pattern, title=title,
                            evidence=[], distinct_sessions=3,
                            coverage={"returned_rows": 3,
                                      "requested_limit": 500,
                                      "limit_hit": False})

    def test_terminal_state_suppresses(self):
        c = self._cand()
        reduced = {c.candidate_key: {"event": "declined"}}
        fresh, suppressed, borderline = sm.dedupe_candidates([c], reduced, [])
        assert fresh == [] and len(suppressed) == 1

    def test_strong_skill_match_suppresses(self):
        c = self._cand(pattern="recall past sessions full text search",
                       title="session history search")
        desc = [("rawgentic:session-recall",
                 "Full-text search over past Claude Code session history "
                 "via the local FTS5 session index search")]
        fresh, suppressed, borderline = sm.dedupe_candidates([c], {}, desc)
        assert fresh == []
        assert suppressed or borderline

    def test_unrelated_candidate_fresh(self):
        c = self._cand(pattern="rotate kubernetes certificates",
                       title="k8s cert rotation helper")
        desc = [("rawgentic:scan", "Run the security scanners over the tree")]
        fresh, _, _ = sm.dedupe_candidates([c], {}, desc)
        assert len(fresh) == 1


class TestParseFrontmatter:
    def test_extracts_name_description(self):
        text = "---\nname: rawgentic:x\ndescription: does X when Y\n---\nbody"
        name, desc = sm.parse_frontmatter(text)
        assert name == "rawgentic:x" and desc == "does X when Y"


# -------------------------------------------------------------------- queue

def ev(key, event, **over):
    base = {"schema_version": 1, "ts": "2026-07-10T00:00:00Z",
            "run_id": "r1", "event": event, "candidate_key": key,
            "detector": "friction", "canonical_pattern": "p", "title": "t",
            "evidence": [], "distinct_sessions": 3,
            "coverage": {"returned_rows": 1, "requested_limit": 500,
                         "limit_hit": False}}
    base.update(over)
    return base


class TestQueue:
    def test_append_and_reduce(self, tmp_path):
        q = tmp_path / "m" / "candidates.jsonl"
        sm.queue_append(q, ev("k1", "detected"))
        sm.queue_append(q, ev("k1", "proposed"))
        state, torn = sm.reduce_queue(q)
        assert state["k1"]["event"] == "proposed" and torn is False

    def test_human_over_machine(self, tmp_path):
        q = tmp_path / "candidates.jsonl"
        sm.queue_append(q, ev("k1", "detected"))
        sm.queue_append(q, ev("k1", "declined"))
        sm.queue_append(q, ev("k1", "detected"))       # machine after human
        sm.queue_append(q, ev("k1", "evidence_updated"))
        state, _ = sm.reduce_queue(q)
        assert state["k1"]["event"] == "declined"       # human state locked

    def test_later_human_event_wins(self, tmp_path):
        q = tmp_path / "candidates.jsonl"
        sm.queue_append(q, ev("k1", "accepted"))
        sm.queue_append(q, ev("k1", "filed"))
        state, _ = sm.reduce_queue(q)
        assert state["k1"]["event"] == "filed"

    def test_torn_tail_truncated_on_append(self, tmp_path):
        q = tmp_path / "candidates.jsonl"
        sm.queue_append(q, ev("k1", "detected"))
        with q.open("a") as fh:
            fh.write('{"torn": "fragm')            # crash mid-append, no \n
        sm.queue_append(q, ev("k2", "detected"))
        state, torn = sm.reduce_queue(q)            # file is all-parseable
        assert set(state) == {"k1", "k2"} and torn is False

    def test_torn_tail_read_is_benign(self, tmp_path):
        q = tmp_path / "candidates.jsonl"
        sm.queue_append(q, ev("k1", "detected"))
        with q.open("a") as fh:
            fh.write('{"torn": "fragm')
        state, torn = sm.reduce_queue(q)
        assert set(state) == {"k1"} and torn is True

    def test_midfile_corruption_raises(self, tmp_path):
        q = tmp_path / "candidates.jsonl"
        sm.queue_append(q, ev("k1", "declined"))
        raw = q.read_text()
        q.write_text("GARBAGE NOT JSON\n" + raw)
        with pytest.raises(sm.QueueCorruption):
            sm.reduce_queue(q)

    def test_evidence_bounded(self, tmp_path):
        q = tmp_path / "candidates.jsonl"
        e = ev("k1", "detected",
               evidence=[{"session_id": SID_A, "quote": "x" * 2000,
                          "source": "index"}])
        sm.queue_append(q, e)
        state, _ = sm.reduce_queue(q)
        assert len(state["k1"]["evidence"][0]["quote"]) <= 500


# --------------------------------------------------------------- WF1 draft

class TestBuildWf1Draft:
    def test_template_sections_and_title(self):
        c = sm.Candidate(candidate_key="k", detector="friction",
                         canonical_pattern="permission denied",
                         title="permission-denied helper",
                         evidence=[{"session_id": SID_A,
                                    "quote": "permission denied on deploy",
                                    "source": "index"}],
                         distinct_sessions=3,
                         coverage={"returned_rows": 3, "requested_limit": 500,
                                   "limit_hit": False})
        draft = sm.build_wf1_draft(c)
        assert draft.splitlines()[0].startswith("feat(")
        for section in ("## Description", "## Acceptance Criteria", "## Scope",
                        "## Affected Components", "## Risk"):
            assert section in draft
        assert SID_A in draft and "recurrence" in draft.lower()


# ---------------------------------------------------------------- CLI tests

SI_CLI = HOOKS_DIR / "session_index.py"


def mk_corpus(root: Path, sid, texts):
    d = root / "-proj-m"
    d.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps({
        "type": "user", "sessionId": sid,
        "timestamp": "2026-07-01T10:00:00.000Z", "uuid": f"u-{sid}",
        "message": {"role": "user", "content": t}}) for t in texts]
    (d / f"{sid}.jsonl").write_text("\n".join(lines) + "\n")


def run_mine(*args, cwd=None):
    return subprocess.run([sys.executable, str(CLI), *args],
                         capture_output=True, text=True, cwd=cwd)


@pytest.fixture()
def mining_env(tmp_path):
    corpus = tmp_path / "projects"
    for sid in (SID_A, SID_B, SID_C):
        mk_corpus(corpus, sid, [f"hit a wall: permission denied again ({sid})",
                                "unrelated chatter"])
    db = tmp_path / "idx" / "sessions.db"
    r = subprocess.run([sys.executable, str(SI_CLI), "index",
                        "--projects-dir", str(corpus), "--db", str(db)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    queue = tmp_path / "mining" / "candidates.jsonl"
    ws = tmp_path / "ws"
    (ws / "claude_docs").mkdir(parents=True)
    return db, queue, ws


class TestDetectCLI:
    def test_detect_appends_events_with_verbatim_quotes(self, mining_env):
        db, queue, ws = mining_env
        r = run_mine("detect", "--queue", str(queue), "--db", str(db),
                     "--workspace-root", str(ws))
        assert r.returncode == 0, r.stderr
        import session_mining_lib as sm2
        state, _ = sm2.reduce_queue(queue)
        pd = [e for e in state.values()
              if e["canonical_pattern"] == "permission denied"]
        assert len(pd) == 1
        assert pd[0]["distinct_sessions"] == 3
        # verbatim quote resolved from the DB, not the 12-token snippet
        assert any("hit a wall" in ev_["quote"] for ev_ in pd[0]["evidence"])

    def test_rerun_without_change_appends_nothing(self, mining_env):
        db, queue, ws = mining_env
        run_mine("detect", "--queue", str(queue), "--db", str(db),
                 "--workspace-root", str(ws))
        n1 = len(queue.read_text().splitlines())
        run_mine("detect", "--queue", str(queue), "--db", str(db),
                 "--workspace-root", str(ws))
        assert len(queue.read_text().splitlines()) == n1


class TestProposeDispositionCLI:
    def _detect(self, db, queue, ws):
        run_mine("detect", "--queue", str(queue), "--db", str(db),
                 "--workspace-root", str(ws))

    def test_propose_threshold_and_evidence(self, mining_env):
        db, queue, ws = mining_env
        self._detect(db, queue, ws)
        r = run_mine("propose", "--queue", str(queue), "--json")
        assert r.returncode == 0, r.stderr
        out = json.loads(r.stdout)
        pd = [e for e in out["proposed"]
              if e["canonical_pattern"] == "permission denied"]
        assert len(pd) == 1
        assert pd[0]["recurrence"] == 3
        assert pd[0]["coverage"]["requested_limit"] == 500
        assert pd[0]["evidence"][0]["session_id"]

    def test_declined_never_reproposed(self, mining_env):
        db, queue, ws = mining_env
        self._detect(db, queue, ws)
        r = run_mine("propose", "--queue", str(queue), "--json")
        key = [e for e in json.loads(r.stdout)["proposed"]
               if e["canonical_pattern"] == "permission denied"][0]["candidate_key"]
        rd = run_mine("disposition", key, "declined", "--queue", str(queue))
        assert rd.returncode == 0
        r2 = run_mine("propose", "--queue", str(queue), "--json")
        keys2 = [e["candidate_key"]
                 for e in json.loads(r2.stdout)["proposed"]]
        assert key not in keys2
        # and a fresh detect must not resurrect it
        run_mine("detect", "--queue", str(queue), "--db", str(db))
        r3 = run_mine("propose", "--queue", str(queue), "--json")
        assert key not in [e["candidate_key"]
                           for e in json.loads(r3.stdout)["proposed"]]

    def test_accepted_listed_pending_wf1(self, mining_env):
        db, queue, ws = mining_env
        self._detect(db, queue, ws)
        r = run_mine("propose", "--queue", str(queue), "--json")
        key = json.loads(r.stdout)["proposed"][0]["candidate_key"]
        run_mine("disposition", key, "accepted", "--queue", str(queue))
        r2 = run_mine("propose", "--queue", str(queue), "--json")
        out = json.loads(r2.stdout)
        assert key in [e["candidate_key"] for e in out["pending_wf1_action"]]
        assert key not in [e["candidate_key"] for e in out["proposed"]]

    def test_corrupt_queue_refuses_propose_and_disposition(self, mining_env, tmp_path):
        db, queue, ws = mining_env
        self._detect(db, queue, ws)
        raw = queue.read_text()
        queue.write_text("MIDFILE GARBAGE\n" + raw)
        r = run_mine("propose", "--queue", str(queue))
        assert r.returncode == 2 and "corruption" in r.stderr.lower()
        r2 = run_mine("disposition", "deadbeef", "declined",
                      "--queue", str(queue))
        assert r2.returncode == 2

    def test_unknown_key_exit_2(self, mining_env):
        _, queue, _ws = mining_env
        queue.parent.mkdir(parents=True, exist_ok=True)
        queue.touch()
        r = run_mine("disposition", "nope", "declined", "--queue", str(queue))
        assert r.returncode == 2

    def test_no_workspace_root_requires_queue_flag(self, tmp_path):
        r = run_mine("propose", cwd=str(tmp_path))
        assert r.returncode == 2
        assert "--queue" in r.stderr
