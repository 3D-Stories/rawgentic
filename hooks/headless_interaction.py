"""Headless interaction helpers for rawgentic workflow skills.

Provides testable functions for:
- Structured GitHub issue comment generation
- Comment metadata parsing
- Suspend state file management (atomic write/read)

Used by workflow skills (via Bash tool) and tested via pytest.
"""
import json
import os
import re
from datetime import datetime, timezone


# --- HTML comment injection prevention ---

def _sanitize_for_html_comment(value: str) -> str:
    """Escape --> sequences in dynamic values to prevent HTML comment injection."""
    return value.replace("--", "&#45;&#45;")


def _sanitize_markdown(value: str) -> str:
    """Escape markdown special chars in user-sourced content."""
    # Light sanitization: escape chars that could create links/images/HTML
    for ch in ["<", ">"]:
        value = value.replace(ch, f"\\{ch}")
    return value


# --- Comment formatting ---

def format_comment(
    step: int,
    title: str,
    context: str,
    question: str,
    options: list[str],
    metadata: dict,
) -> str:
    """Format a structured GitHub issue comment for headless interaction.

    The comment has a human-readable section and a hidden JSON metadata block.
    session_id is intentionally excluded from the public comment (security).

    Parameters
    ----------
    step : int
        The WF step number that triggered the interaction.
    title : str
        Short title for the question (e.g., "Circuit Breaker Triggered").
    context : str
        What the workflow is doing.
    question : str
        The decision needed from the user.
    options : list[str]
        Numbered options for the user to choose from.
    metadata : dict
        Machine-readable metadata (question_id, step, type, etc.).
        Must NOT contain session_id.

    Returns
    -------
    str
        Formatted markdown comment body.
    """
    # Sanitize dynamic values
    safe_title = _sanitize_for_html_comment(_sanitize_markdown(title))
    safe_context = _sanitize_for_html_comment(_sanitize_markdown(context))
    safe_question = _sanitize_for_html_comment(_sanitize_markdown(question))
    safe_options = [_sanitize_for_html_comment(_sanitize_markdown(o)) for o in options]

    # Ensure no session_id leaks into public comment
    clean_metadata = {k: v for k, v in metadata.items() if k != "session_id"}

    # Build comment body
    parts = [
        f"## [WF2 Step {step}] {safe_title}",
        "",
        f"**Context:** {safe_context}",
        f"**Question:** {safe_question}",
    ]

    if safe_options:
        parts.append("")
        parts.append("**Options:**")
        for opt in safe_options:
            parts.append(f"- {opt}")

    parts.append("")
    parts.append("Reply to this comment with your choice.")
    parts.append("")

    # Hidden metadata block
    meta_json = json.dumps(clean_metadata, separators=(",", ":"))
    parts.append(f"<!-- rawgentic-headless: {meta_json} -->")

    return "\n".join(parts)


# --- Metadata parsing ---

_METADATA_PATTERN = re.compile(
    r"<!--\s*rawgentic-headless:\s*(.*?)\s*-->",
    re.DOTALL,
)


def parse_metadata(comment_body: str | None) -> dict | None:
    """Extract JSON metadata from a structured headless comment.

    Looks for the last ``<!-- rawgentic-headless: {...} -->`` block.
    Returns None if no valid metadata found (graceful degradation).
    """
    if not comment_body:
        return None

    matches = list(_METADATA_PATTERN.finditer(comment_body))
    if not matches:
        return None

    # Use the last match (in case of multiple blocks)
    last_match = matches[-1]
    json_str = last_match.group(1).strip()

    try:
        return json.loads(json_str)
    except (json.JSONDecodeError, ValueError):
        return None


# --- Suspend state management ---

def format_suspend_state(
    session_id: str,
    issue: int,
    step: int,
    question_id: str,
    comment_url: str,
    clarification_round: int = 0,
) -> dict:
    """Create a suspend state dictionary.

    Returns
    -------
    dict
        Suspend state with all fields populated, including timestamp.
    """
    return {
        "session_id": session_id,
        "issue": issue,
        "step": step,
        "question_id": question_id,
        "comment_url": comment_url,
        "clarification_round": clarification_round,
        "suspended_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def write_suspend_state(path: str, state: dict) -> None:
    """Atomically write suspend state to a JSON file.

    Uses write-to-tmp-then-rename pattern to prevent partial writes.
    """
    tmp_path = path + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(state, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.rename(tmp_path, path)


def read_suspend_state(path: str) -> dict | None:
    """Read suspend state from a JSON file.

    Returns None if file is missing, unreadable, or malformed JSON.
    """
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, ValueError, OSError):
        return None
