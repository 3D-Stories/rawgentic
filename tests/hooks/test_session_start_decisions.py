"""Integration tests for session-start's decision injection (#847).

Drives the REAL entry path — the session-start hook with a SessionStart payload
on stdin — rather than the helper in isolation. The adversarial review of the
#847 plan called this out: unit tests on `decision_log.py` would not catch wrong
project selection, full-history leakage, or a broken interaction between the
notes tail and the decision injection.
"""
import json
import subprocess
import sys
from pathlib import Path

from tests.hooks.conftest import HOOKS_DIR, run_hook, parse_hook_output

sys.path.insert(0, str(HOOKS_DIR))
import decision_log  # noqa: E402


def _run_session_start(cwd, session_id="test-sess", event_type="startup"):
    fake_home = Path(str(cwd)) / ".test_home"
    fake_home.mkdir(exist_ok=True)
    stdin = {
        "session_id": session_id,
        "cwd": str(cwd),
        "hook_event_name": "SessionStart",
        "source": event_type,
    }
    return run_hook("session-start", stdin, cwd=cwd,
                    env_override={"HOME": str(fake_home)})


def _seed(claude_docs: Path, project: str, n: int, prefix: str = "D"):
    for i in range(1, n + 1):
        decision_log.append_record(
            claude_docs, project,
            decision_log.build_record(
                project=project, decision_id=f"{prefix}{i}",
                title=f"decision {prefix}{i}", body="why",
                overturnable=f"revert {prefix}{i}"),
        )


def _context(result) -> str:
    """run_hook returns (stdout, stderr, rc); the context lives in the JSON."""
    stdout, _, _ = result
    out = parse_hook_output(stdout)
    return json.dumps(out) if out is not None else ""


class TestDecisionInjection:
    def test_injects_exactly_the_newest_fifteen(self, make_workspace):
        ws = make_workspace(registry_entries=[
            {"session_id": "test-sess", "project": "testproj",
             "project_path": "./projects/testproj"}])
        _seed(ws.root / "claude_docs", "testproj", 40)

        ctx = _context(_run_session_start(ws.root))

        assert "RAWGENTIC DECISIONS" in ctx
        for i in range(26, 41):
            assert f"decision D{i}" in ctx, f"D{i} should be in the newest 15"
        for i in range(1, 26):
            assert f'"id": "D{i}"' not in ctx, f"D{i} leaked — history is unbounded"

    def test_does_not_leak_full_history(self, make_workspace):
        ws = make_workspace(registry_entries=[
            {"session_id": "test-sess", "project": "testproj",
             "project_path": "./projects/testproj"}])
        _seed(ws.root / "claude_docs", "testproj", 300)
        ctx = _context(_run_session_start(ws.root))
        injected = ctx.count('\\"id\\": \\"D') + ctx.count('"id": "D')
        assert injected <= 15, f"{injected} decisions injected, expected at most 15"

    def test_other_projects_decisions_are_not_injected(self, make_workspace):
        ws = make_workspace(registry_entries=[
            {"session_id": "test-sess", "project": "testproj",
             "project_path": "./projects/testproj"}])
        _seed(ws.root / "claude_docs", "testproj", 3, prefix="MINE")
        _seed(ws.root / "claude_docs", "otherproj", 3, prefix="THEIRS")

        ctx = _context(_run_session_start(ws.root))

        assert "decision MINE3" in ctx
        assert "THEIRS" not in ctx, "another project's decisions were injected"

    def test_absent_store_is_silent_and_harmless(self, make_workspace):
        """Fail-OPEN: no decisions yet must not break session start."""
        ws = make_workspace(registry_entries=[
            {"session_id": "test-sess", "project": "testproj",
             "project_path": "./projects/testproj"}])
        result = _run_session_start(ws.root)
        assert result[2] == 0
        assert "RAWGENTIC DECISIONS" not in _context(result)

    def test_corrupt_store_does_not_break_session_start(self, make_workspace):
        ws = make_workspace(registry_entries=[
            {"session_id": "test-sess", "project": "testproj",
             "project_path": "./projects/testproj"}])
        _seed(ws.root / "claude_docs", "testproj", 2)
        store = decision_log.store_path(ws.root / "claude_docs", "testproj")
        with open(store, "a") as f:
            f.write("{ broken\n")
        result = _run_session_start(ws.root)
        assert result[2] == 0
        assert "decision D2" in _context(result)


class TestDecisionStoreSurvivesTheTrimmer:
    """AC3: write a decision, force a trim cycle, it is still readable."""

    def test_decision_survives_a_full_trim_cycle_byte_for_byte(self, make_workspace):
        ws = make_workspace(registry_entries=[
            {"session_id": "test-sess", "project": "testproj",
             "project_path": "./projects/testproj"}])
        claude_docs = ws.root / "claude_docs"
        _seed(claude_docs, "testproj", 5)
        store = decision_log.store_path(claude_docs, "testproj")
        before = store.read_bytes()

        # An oversized notes file guarantees the size handler actually fires.
        notes_dir = claude_docs / "session_notes"
        notes_dir.mkdir(parents=True, exist_ok=True)
        (notes_dir / "testproj.md").write_text("".join(
            f"routine line {i}\n" for i in range(12_000)))

        _run_session_start(ws.root)

        assert store.read_bytes() == before, "the trimmer touched the decision store"
        assert len(decision_log.read_records(claude_docs, "testproj")) == 5
        # Assert the trim REALLY ran, or this test would stay green even if the
        # size-handler call were removed from session-start entirely.
        archives = list((notes_dir / ".notes-archive").glob("testproj.md.*.archive.md"))
        assert len(archives) == 1, "the notes file was never trimmed"
        assert len((notes_dir / "testproj.md").read_text()) < 64_000

    def test_trimmer_discovery_never_yields_a_decisions_path(self, make_workspace):
        """The trimmer's own glob, run exactly as session-start runs it."""
        ws = make_workspace(registry_entries=[
            {"session_id": "test-sess", "project": "testproj",
             "project_path": "./projects/testproj"}])
        claude_docs = ws.root / "claude_docs"
        _seed(claude_docs, "testproj", 3)
        notes_dir = claude_docs / "session_notes"
        notes_dir.mkdir(parents=True, exist_ok=True)
        (notes_dir / "testproj.md").write_text("x\n")

        discovered = list(notes_dir.glob("*.md"))
        assert discovered, "sanity: the glob should find the notes file"
        for p in discovered:
            assert "decisions" not in p.parts, f"{p} is inside the decisions store"

    def test_handler_refuses_a_decisions_file_even_if_handed_one(self, tmp_path):
        """Belt and braces: pointed straight at the store, it must not trim."""
        store = tmp_path / "decisions" / "testproj.jsonl"
        store.parent.mkdir(parents=True)
        store.write_text('{"id":"D1"}\n' * 12_000)
        before = store.read_bytes()
        r = subprocess.run(
            [sys.executable, str(HOOKS_DIR / "notes-size-handler.py"), str(store),
             "--session-id", "s"],
            capture_output=True, text=True, timeout=20)
        assert r.returncode == 0
        assert store.read_bytes() == before


class TestInjectionBoundIsEnforced:
    """Round-1 review: a digits-only check accepted 999999999, and
    `out[-999999999:]` is the entire store."""

    def test_absurd_override_falls_back_to_the_default(self, make_workspace):
        ws = make_workspace(registry_entries=[
            {"session_id": "test-sess", "project": "testproj",
             "project_path": "./projects/testproj"}])
        _seed(ws.root / "claude_docs", "testproj", 200)
        fake_home = ws.root / ".test_home"
        fake_home.mkdir(exist_ok=True)
        stdout, _, rc = run_hook(
            "session-start",
            {"session_id": "test-sess", "cwd": str(ws.root),
             "hook_event_name": "SessionStart", "source": "startup"},
            cwd=ws.root,
            env_override={"HOME": str(fake_home),
                          "RAWGENTIC_DECISION_INJECT_COUNT": "999999999"},
        )
        assert rc == 0
        ctx = json.dumps(parse_hook_output(stdout) or "")
        injected = ctx.count('\\"id\\": \\"D') + ctx.count('"id": "D')
        assert injected <= 15, f"{injected} injected — the bound was bypassed"


class TestRoundTwoReviewGuards:
    def test_oversized_integer_override_cannot_bypass_the_clamp(self, make_workspace):
        """A 40-digit value makes bash's own `[ -gt ]` fail with "integer
        expression expected", so a purely numeric clamp silently did not clamp."""
        ws = make_workspace(registry_entries=[
            {"session_id": "test-sess", "project": "testproj",
             "project_path": "./projects/testproj"}])
        _seed(ws.root / "claude_docs", "testproj", 200)
        fake_home = ws.root / ".test_home"
        fake_home.mkdir(exist_ok=True)
        stdout, _, rc = run_hook(
            "session-start",
            {"session_id": "test-sess", "cwd": str(ws.root),
             "hook_event_name": "SessionStart", "source": "startup"},
            cwd=ws.root,
            env_override={"HOME": str(fake_home),
                          "RAWGENTIC_DECISION_INJECT_COUNT": "9" * 40},
        )
        assert rc == 0
        ctx = json.dumps(parse_hook_output(stdout) or "")
        injected = ctx.count('\\"id\\": \\"D') + ctx.count('"id": "D')
        assert injected <= 15, f"{injected} injected — the clamp was bypassed"
