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

MARKER_8A_CMD = """cat >> notes.md <<'EOF'
### WF2 Step 8a [task 3, sha 3c418ad]: DONE (#492: 4 findings)
EOF"""

TWO_MARKERS_CMD = """cat >> notes.md <<'EOF'
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

    def test_unkeyed_legacy_marker_still_detects_without_issue(self):
        cmd = "cat >> n.md <<'EOF'\n### WF2 Step 7: Create Branch — DONE (feature/x cut)\nEOF"
        hit = ssp.detect_marker(cmd)
        assert hit is not None and hit["issue"] is None and hit["step"] == "7"


class TestDetectSignature:
    def test_signature_table(self):
        assert ssp.detect_signature("python3 hooks/security_scan.py scan --json")[0] == "11.5"
        assert ssp.detect_signature("gh pr create --repo x --title t")[0] == "12"
        assert ssp.detect_signature("gh pr merge 500 --squash")[0] == "14"
        assert ssp.detect_signature("python3 hooks/work_summary.py summarize --record-file f")[0] == "16"
        assert ssp.detect_signature("python3 hooks/capabilities_lib.py derive --config c")[0] == "1"

    def test_no_signature_is_none(self):
        assert ssp.detect_signature("ls -la && git log") is None


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
