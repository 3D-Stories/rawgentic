"""Concurrent-session binding-race fix.

Root cause: LLM-driven skills (`switch`, `new-project`) and the security-guard
WAL logger identified "my session" by reading the *shared* file
`claude_docs/.current_session_id`, which every session overwrites on every
prompt. With two concurrent sessions, that file holds whichever session most
recently submitted a prompt — so a switch in session B could write a registry
line tagged with session A's id and bind the wrong session. `tail -1`
resolution then made the mis-binding sticky.

Fix: identify the session from the per-process env var `CLAUDE_CODE_SESSION_ID`
(the correct name — `CLAUDE_SESSION_ID` is unset), with the shared file kept
only as a last-resort fallback. The hooks already use the authoritative stdin
`session_id`; the security-guard logger now prefers that too.
"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
HOOKS_DIR = REPO_ROOT / "hooks"
SKILLS_DIR = REPO_ROOT / "skills"
sys.path.insert(0, str(HOOKS_DIR))


def _load_security_guard():
    """Import the hyphenated security-guard.py as a module (it is __main__-guarded)."""
    spec = importlib.util.spec_from_file_location(
        "security_guard_main_mod", HOOKS_DIR / "security-guard.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --- security-guard WAL logger: session-id precedence --------------------------

SID_A = "sess-aaa-1111"
SID_B = "sess-bbb-2222"


def _two_project_ws(make_workspace):
    """Workspace with two active projects and a registry mapping each session id
    to a distinct project. Writes `.current_session_id` = SID_A (the *wrong* one)."""
    ws = make_workspace(
        projects=[
            {"name": "projA", "path": "./projects/projA", "active": True,
             "lastUsed": "2026-01-01T00:00:00Z", "configured": True},
            {"name": "projB", "path": "./projects/projB", "active": True,
             "lastUsed": "2026-01-01T00:00:00Z", "configured": True},
        ],
        registry_entries=[
            {"session_id": SID_A, "project": "projA",
             "project_path": "./projects/projA", "started": "2026-01-01T00:00:00Z",
             "cwd": "/x"},
            {"session_id": SID_B, "project": "projB",
             "project_path": "./projects/projB", "started": "2026-01-01T00:00:01Z",
             "cwd": "/x"},
        ],
    )
    # The shared file points at the WRONG session — the bug's trigger condition.
    (ws.claude_docs / ".current_session_id").write_text(SID_A)
    return ws


def test_log_uses_explicit_stdin_session_id_over_shared_file(make_workspace, monkeypatch):
    """An explicit (stdin) session id must win over the shared .current_session_id file."""
    mod = _load_security_guard()
    ws = _two_project_ws(make_workspace)
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)

    mod._log_headless_guard_block(
        [{"name": "some-rule"}], "foo.py", str(ws.root), session_id=SID_B
    )

    wal_b = ws.claude_docs / "wal" / "projB.jsonl"
    wal_a = ws.claude_docs / "wal" / "projA.jsonl"
    assert wal_b.exists(), "block must be logged under the correct (stdin) project"
    assert not wal_a.exists(), "must NOT log under the shared-file's stale project"
    entry = json.loads(wal_b.read_text().strip())
    assert entry["session"] == SID_B
    assert entry["phase"] == "GUARD_BLOCK"


def test_log_falls_back_to_env_var_when_no_explicit_id(make_workspace, monkeypatch):
    """With no explicit id, the per-session env var beats the shared file."""
    mod = _load_security_guard()
    ws = _two_project_ws(make_workspace)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SID_B)

    mod._log_headless_guard_block([{"name": "r"}], "foo.py", str(ws.root))

    assert (ws.claude_docs / "wal" / "projB.jsonl").exists()
    assert not (ws.claude_docs / "wal" / "projA.jsonl").exists()


def test_log_falls_back_to_shared_file_when_no_id_and_no_env(make_workspace, monkeypatch):
    """Backward compat: with neither explicit id nor env var, the file is still used."""
    mod = _load_security_guard()
    ws = _two_project_ws(make_workspace)
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)

    mod._log_headless_guard_block([{"name": "r"}], "foo.py", str(ws.root))

    # File says SID_A -> projA
    assert (ws.claude_docs / "wal" / "projA.jsonl").exists()
    assert not (ws.claude_docs / "wal" / "projB.jsonl").exists()


# --- SKILL drift guards: switch + new-project must use the env var -------------

def test_switch_skill_uses_env_var_for_session_id():
    text = (SKILLS_DIR / "switch" / "SKILL.md").read_text()
    assert "$CLAUDE_CODE_SESSION_ID" in text, \
        "switch skill must source the session id from $CLAUDE_CODE_SESSION_ID"
    # Must explain WHY the shared file is unsafe (concurrency).
    assert "concurrent" in text.lower()
    # Must NOT keep the old directive that the shared file is the source of truth.
    assert "always read it from this file" not in text


def test_switch_skill_documents_correct_env_var_name():
    """The legacy name CLAUDE_SESSION_ID is unset; the correct one is CLAUDE_CODE_SESSION_ID."""
    text = (SKILLS_DIR / "switch" / "SKILL.md").read_text()
    # The bare wrong name must not appear without the CODE_ infix as the recommended source.
    assert "CLAUDE_CODE_SESSION_ID" in text


def test_new_project_skill_uses_env_var_for_session_id():
    text = (SKILLS_DIR / "new-project" / "SKILL.md").read_text()
    assert "$CLAUDE_CODE_SESSION_ID" in text, \
        "new-project skill must source the session id from $CLAUDE_CODE_SESSION_ID"
