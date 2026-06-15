#!/usr/bin/env python3
"""Headless interaction helpers for rawgentic workflow skills.

Provides testable functions for:
- Structured GitHub issue comment generation
- Comment metadata parsing
- Suspend state file management (atomic write/read)

Used by workflow skills (via Bash tool) and tested via pytest.
"""
import argparse
import json
import os
import re
import sys
import uuid
from datetime import datetime, timezone


# --- HTML comment injection prevention ---

def _sanitize_for_html_comment(value: str) -> str:
    """Escape --> sequences in dynamic values to prevent HTML comment injection."""
    return value.replace("--", "&#45;&#45;")


def _sanitize_markdown(value: str) -> str:
    """Escape markdown special chars in user-sourced content.

    Prevents link injection ([text](url)), image injection (![alt](url)),
    and HTML tag injection (<script>). Covers OWASP markdown injection vectors.
    """
    for ch in ["<", ">", "[", "]", "(", ")", "!"]:
        value = value.replace(ch, f"\\{ch}")
    return value


# --- Comment formatting ---

def format_comment(
    step: int | str,
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
    step : int | str
        The WF step number that triggered the interaction. Strings allowed for
        sub-steps like "8a" (P15 tiered review).
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

    # Sanitize metadata string values against --> injection inside HTML comment
    safe_metadata = {
        k: (_sanitize_for_html_comment(v) if isinstance(v, str) else v)
        for k, v in clean_metadata.items()
    }

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
    meta_json = json.dumps(safe_metadata, separators=(",", ":"))
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
    step: int | str,
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


# --- CLI ---
#
# The workflow skill (SKILL.md) drives the headless QUESTION-suspend protocol
# from Bash. Doing that via `python3 -c "..."` forces Claude to reconstruct a
# multi-line Python snippet (with nested quotes and string interpolation) on
# every suspend — fragile and a frequent source of escaping bugs. This CLI gives
# each step a stable flag-based contract so the skill calls a command instead of
# rebuilding code.

# A WF step is an integer in 1.._MAX_WF_STEP optionally followed by one
# lowercase sub-step letter (e.g. "8a"). Validating at the CLI boundary stops a
# malformed step ("", "0", "-1", whitespace, "abc") OR a well-formed but
# unroutable step ("17", "99") from silently producing a suspend file that the
# resume path can't dispatch. The numeric bound is the ASCII-only [0-9] class +
# an int comparison, so leading zeros ("08") and unicode digits are also rejected.
# Keep _MAX_WF_STEP in sync if WF2 gains steps.
_MAX_WF_STEP = 16
# No ``$`` anchor: it matches before a trailing newline, so ``re.match`` would
# accept "5\n". Used with ``fullmatch`` (whole string must match) the trailing
# "\n" is left unconsumed and correctly rejected.
_STEP_RE = re.compile(r"([1-9][0-9]*)([a-z]?)")


def _parse_step(step: str) -> int | str:
    """Validate and coerce a WF step token. A bare number (``"5"``) becomes an
    int so it matches int step values used in resume routing; a sub-step
    (``"8a"``) stays a string. Anything malformed or out of range raises
    ``ValueError``."""
    m = _STEP_RE.fullmatch(step)
    if not m or int(m.group(1)) > _MAX_WF_STEP:
        raise ValueError(
            f"invalid --step {step!r} (expected 1-{_MAX_WF_STEP}, "
            f"optionally with a sub-step letter like '8a')"
        )
    # No sub-step letter → a pure integer step; otherwise keep the string form.
    return int(m.group(1)) if not m.group(2) else step


def main(argv=None) -> int:
    """CLI entry point for headless interaction helpers.

    Subcommands (the QUESTION-suspend path the workflow skill drives from Bash):
      new-id          print a fresh question_id (uuid4)
      format-comment  render the structured GitHub comment body to stdout
      write-suspend   write the suspend state file (atomic)

    (The resume side — reading suspend state and parsing a reply — is handled by
    the read_suspend_state / parse_metadata library functions; those gain CLI
    subcommands when the resume protocol is wired to use them.)

    Exit codes:
      0  success
      1  invalid input (empty/malformed identifier or step) OR a fail-closed
         write error — surfaced as non-zero so a caller never mistakes a failed
         or unmatchable write for success
    """
    parser = argparse.ArgumentParser(prog="headless_interaction")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("new-id", help="print a fresh question_id (uuid4)")

    p_fmt = sub.add_parser("format-comment", help="render the comment body")
    p_fmt.add_argument("--step", required=True)
    p_fmt.add_argument("--title", required=True)
    p_fmt.add_argument("--context", required=True)
    p_fmt.add_argument("--question", required=True)
    p_fmt.add_argument("--option", action="append", help="repeatable")
    p_fmt.add_argument("--type", required=True, dest="type")
    p_fmt.add_argument("--question-id", required=True, dest="question_id")

    p_sus = sub.add_parser("write-suspend", help="write the suspend state file")
    p_sus.add_argument("--path", required=True)
    p_sus.add_argument("--issue", required=True, type=int)
    p_sus.add_argument("--step", required=True)
    p_sus.add_argument("--question-id", required=True, dest="question_id")
    p_sus.add_argument("--comment-url", required=True, dest="comment_url")
    p_sus.add_argument("--session-id", default="N/A", dest="session_id")
    p_sus.add_argument("--clarification-round", default=0, type=int,
                       dest="clarification_round")

    args = parser.parse_args(argv)

    if args.cmd == "new-id":
        print(uuid.uuid4())
        return 0

    if args.cmd == "format-comment":
        if not args.question_id.strip():
            print("--question-id must be a non-empty value", file=sys.stderr)
            return 1
        try:
            step = _parse_step(args.step)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        comment = format_comment(
            step=step,
            title=args.title,
            context=args.context,
            question=args.question,
            options=args.option or [],
            metadata={"question_id": args.question_id, "step": step, "type": args.type},
        )
        print(comment)
        return 0

    if args.cmd == "write-suspend":
        # argparse required=True only proves the flag was PRESENT — an empty
        # value (e.g. a lost/empty shell variable upstream) would otherwise
        # write a suspend file that can never be matched to a real comment.
        # Fail closed so an upstream shell failure can't masquerade as success.
        for flag, value in (("--question-id", args.question_id),
                            ("--comment-url", args.comment_url)):
            if not value.strip():
                print(f"{flag} must be a non-empty value", file=sys.stderr)
                return 1
        if args.issue <= 0:
            print("--issue must be a positive integer", file=sys.stderr)
            return 1
        try:
            step = _parse_step(args.step)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        state = format_suspend_state(
            session_id=args.session_id,
            issue=args.issue,
            step=step,
            question_id=args.question_id,
            comment_url=args.comment_url,
            clarification_round=args.clarification_round,
        )
        try:
            write_suspend_state(args.path, state)
        except OSError as exc:
            print(f"failed to write suspend state: {exc}", file=sys.stderr)
            return 1
        print(args.path)
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
