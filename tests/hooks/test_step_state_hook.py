"""#499: hook-level step-state emission — PostToolUse hybrid detector.

Unit tests import the module; flow tests run it black-box via subprocess with
JSON on stdin exactly as Claude Code invokes hooks (docs/testing.md). Fail-open
everywhere: garbage input exits 0 silently.
"""
import json
import subprocess
import sys
from pathlib import Path

HOOKS = Path(__file__).resolve().parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS))
import step_state_post as ssp  # noqa: E402

CLI = str(HOOKS / "step_state_post.py")

MARKER_CMD = """cat >> /w/claude_docs/session_notes.md <<'EOF'
### WF2 Step 11: Pre-PR Code Review — DONE (#492: 5 findings)
EOF
echo ok"""

MARKER_8A_CMD = """cat >> claude_docs/session_notes.md <<'EOF'
### WF2 Step 8a [task 3, sha 3c418ad]: DONE (#492: 4 findings)
EOF"""

TWO_MARKERS_CMD = """cat >> claude_docs/session_notes.md <<'EOF'
### WF3 Step 4: Root Cause — DONE (#77: found)
### WF2 Step 12: Create PR — DONE (#492: PR #500)
EOF"""


class TestDetectMarker:
    def test_done_marker_detected(self):
        hit = ssp.detect_marker(MARKER_CMD)
        assert hit == {"workflow": "wf2", "step": "11",
                       "step_title": "Pre-PR Code Review ✓done", "issue": 492}

    def test_8a_bracket_shape(self):
        hit = ssp.detect_marker(MARKER_8A_CMD)
        assert hit["workflow"] == "wf2"
        assert hit["step"] == "8a"
        assert hit["issue"] == 492

    def test_last_marker_wins(self):
        hit = ssp.detect_marker(TWO_MARKERS_CMD)
        assert hit["workflow"] == "wf2" and hit["step"] == "12" and hit["issue"] == 492

    def test_plain_command_is_none(self):
        assert ssp.detect_marker("git status --porcelain") is None

    def test_pathological_whitespace_is_fast(self):
        # 8a R2 #499: the v1 regex blew up super-quadratically on a marker
        # prefix + long whitespace run (measured >10s @5000 spaces) — on a
        # hook that runs for EVERY Bash call. Must stay linear.
        import time
        evil = "### WF2 Step 1:" + " " * 8000
        start = time.monotonic()
        assert ssp.detect_marker(evil) is None
        assert time.monotonic() - start < 0.1, "detect_marker must not backtrack"

    def test_overlong_marker_line_skipped(self):
        long_line = "### WF2 Step 11: " + "x" * 5000 + " — DONE (#492: y)"
        assert ssp.detect_marker(long_line) is None, (
            "lines beyond the cap are skipped — real markers are short")

    def test_unkeyed_legacy_marker_still_detects_without_issue(self):
        cmd = "cat >> claude_docs/session_notes.md <<'EOF'\n### WF2 Step 7: Create Branch — DONE (feature/x cut)\nEOF"
        hit = ssp.detect_marker(cmd)
        assert hit is not None and hit["issue"] is None and hit["step"] == "7"


class TestDetectSignature:
    def test_wf2_signature_table(self):
        assert ssp.detect_signature("python3 hooks/security_scan.py scan --json", "wf2")[0] == "11.5"
        assert ssp.detect_signature("gh pr create --repo x --title t", "wf2")[0] == "12"
        assert ssp.detect_signature("gh pr merge 500 --squash", "wf2")[0] == "14"
        assert ssp.detect_signature("python3 hooks/work_summary.py summarize --record-file f", "wf2")[0] == "16"

    def test_wf3_signature_table(self):
        # 8a R1 #499: the same commands land on DIFFERENT step numbers in WF3
        # (fix-bug steps.md: PR=10, merge=12, summary=14; no scan step).
        assert ssp.detect_signature("gh pr create --repo x -t t", "wf3") == ("10", "Create Pull Request")
        assert ssp.detect_signature("gh pr merge 7 --squash", "wf3") == ("12", "Merge and Deploy")
        assert ssp.detect_signature("python3 hooks/work_summary.py summarize -r f", "wf3")[0] == "14"
        assert ssp.detect_signature("python3 hooks/security_scan.py scan", "wf3") is None

    def test_unknown_workflow_never_stamps(self):
        assert ssp.detect_signature("gh pr create --repo x -t t", "wf5") is None
        assert ssp.detect_signature("gh pr create --repo x -t t", None) is None

    def test_derive_row_dropped(self):
        # Inert on a true first entry (no prior state) and corrupting later —
        # removed rather than per-workflow-titled.
        assert ssp.detect_signature("python3 hooks/capabilities_lib.py derive -c c", "wf2") is None

    def test_no_signature_is_none(self):
        assert ssp.detect_signature("ls -la && git log", "wf2") is None


def _mk_workspace(tmp_path, session_id="sess-1", project="rawgentic"):
    (tmp_path / ".rawgentic_workspace.json").write_text('{"version": 1, "projects": []}')
    cd = tmp_path / "claude_docs"
    cd.mkdir()
    (cd / "session_registry.jsonl").write_text(json.dumps(
        {"session_id": session_id, "project": project,
         "project_path": f"./projects/{project}", "started": "2026-07-19T00:00:00Z",
         "cwd": str(tmp_path)}) + "\n")
    return tmp_path


def _run_hook(cwd, payload):
    return subprocess.run([sys.executable, CLI], input=json.dumps(payload),
                          capture_output=True, text=True, cwd=str(cwd), timeout=30)


class TestHookFlow:
    def test_marker_event_writes_state(self, tmp_path):
        ws = _mk_workspace(tmp_path)
        r = _run_hook(ws, {"session_id": "sess-1", "tool_name": "Bash",
                           "tool_input": {"command": MARKER_CMD}})
        assert r.returncode == 0
        assert r.stdout == "", "PostToolUse stdout would inject context — must be empty"
        rec = json.loads((ws / "claude_docs" / "wal" / "rawgentic.state.json").read_text())
        assert rec["step"] == "11" and rec["workflow"] == "wf2"
        assert rec["issue"] == 492 and rec["session_id"] == "sess-1"

    def test_non_bash_tool_no_write(self, tmp_path):
        ws = _mk_workspace(tmp_path)
        r = _run_hook(ws, {"session_id": "sess-1", "tool_name": "Read",
                           "tool_input": {"file_path": "/x"}})
        assert r.returncode == 0
        assert not (ws / "claude_docs" / "wal" / "rawgentic.state.json").exists()

    def test_unregistered_session_no_write(self, tmp_path):
        ws = _mk_workspace(tmp_path, session_id="other-sess")
        r = _run_hook(ws, {"session_id": "sess-1", "tool_name": "Bash",
                           "tool_input": {"command": MARKER_CMD}})
        assert r.returncode == 0
        assert not (ws / "claude_docs" / "wal" / "rawgentic.state.json").exists()

    def test_signature_reuses_matching_session_context(self, tmp_path):
        ws = _mk_workspace(tmp_path)
        _run_hook(ws, {"session_id": "sess-1", "tool_name": "Bash",
                       "tool_input": {"command": MARKER_CMD}})
        r = _run_hook(ws, {"session_id": "sess-1", "tool_name": "Bash",
                           "tool_input": {"command": "gh pr create --repo x -t t"}})
        assert r.returncode == 0
        rec = json.loads((ws / "claude_docs" / "wal" / "rawgentic.state.json").read_text())
        assert rec["step"] == "12" and rec["workflow"] == "wf2" and rec["issue"] == 492

    def test_wf3_session_gets_wf3_numbering(self, tmp_path):
        ws = _mk_workspace(tmp_path)
        wf3_marker = "cat >> claude_docs/session_notes.md <<'EOF'\n### WF3 Step 9: Code Review — DONE (#77: clean)\nEOF"
        _run_hook(ws, {"session_id": "sess-1", "tool_name": "Bash",
                       "tool_input": {"command": wf3_marker}})
        r = _run_hook(ws, {"session_id": "sess-1", "tool_name": "Bash",
                           "tool_input": {"command": "gh pr create --repo x -t t"}})
        assert r.returncode == 0
        rec = json.loads((ws / "claude_docs" / "wal" / "rawgentic.state.json").read_text())
        assert rec["workflow"] == "wf3" and rec["step"] == "10", (
            "a WF3 session must get WF3's step numbering, never WF2's")

    def test_signature_skipped_on_foreign_session_record(self, tmp_path):
        ws = _mk_workspace(tmp_path)
        _run_hook(ws, {"session_id": "sess-1", "tool_name": "Bash",
                       "tool_input": {"command": MARKER_CMD}})
        (ws / "claude_docs" / "session_registry.jsonl").write_text(
            json.dumps({"session_id": "sess-2", "project": "rawgentic",
                        "project_path": "./projects/rawgentic",
                        "started": "2026-07-19T01:00:00Z", "cwd": str(ws)}) + "\n")
        r = _run_hook(ws, {"session_id": "sess-2", "tool_name": "Bash",
                           "tool_input": {"command": "gh pr merge 1 --squash"}})
        assert r.returncode == 0
        rec = json.loads((ws / "claude_docs" / "wal" / "rawgentic.state.json").read_text())
        assert rec["step"] == "11" and rec["session_id"] == "sess-1", (
            "a signature hit must not stamp over another session's context")

    def test_garbage_stdin_fails_open(self, tmp_path):
        ws = _mk_workspace(tmp_path)
        r = subprocess.run([sys.executable, CLI], input="{not json",
                           capture_output=True, text=True, cwd=str(ws), timeout=30)
        assert r.returncode == 0
        assert r.stdout == ""

    def test_empty_stdin_fails_open(self, tmp_path):
        ws = _mk_workspace(tmp_path)
        r = subprocess.run([sys.executable, CLI], input="",
                           capture_output=True, text=True, cwd=str(ws), timeout=30)
        assert r.returncode == 0


class TestMarkerRequiresNotesAppend:
    """#499 Step-11 adversarial F3: a command merely DISPLAYING marker text
    (echo/grep/cat of some other file) is not a step completion — the marker
    path requires the command to name the session-notes file."""

    def test_echoed_marker_without_notes_path_ignored(self):
        assert ssp.detect_marker(
            'echo "### WF2 Step 14: Merge — DONE (#999: fake)"') is None

    def test_grep_of_marker_text_ignored(self):
        assert ssp.detect_marker(
            "grep '### WF2 Step 11' /some/other/file.md") is None
