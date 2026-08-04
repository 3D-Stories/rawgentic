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
`session_id`.
"""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SKILLS_DIR = REPO_ROOT / "skills"


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


# --- security-guard claudeDocsPath containment (#262) --------------------------

