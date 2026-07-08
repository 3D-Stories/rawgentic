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

from atomic_write_lib import atomic_write_text


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


def format_status_comment(step: int | str, title: str, context: str) -> str:
    """Format a NON-BLOCKING progress (STATUS) comment (#48/#165).

    Posted at step boundaries as the headless run's progress surface. Unlike
    format_comment it carries no question, no options, and no reply
    instruction — and its metadata deliberately has NO question_id, so the
    resume path can never mistake it for a pending question.
    """
    safe_title = _sanitize_for_html_comment(_sanitize_markdown(title))
    safe_context = _sanitize_for_html_comment(_sanitize_markdown(context))
    meta_json = json.dumps({"step": step, "type": "status"},
                           separators=(",", ":"))
    return "\n".join([
        f"## [WF2 Step {step}] {safe_title}",
        "",
        f"**Status:** {safe_context}",
        "",
        f"<!-- rawgentic-headless: {meta_json} -->",
    ])


# --- Metadata parsing ---

# (parse_metadata was removed in #275: the metadata block is emitted into
# every headless comment for driver/human visibility, but resume reads the
# suspend-state file — nothing ever parsed the comment back. The emitted
# format is still round-trip-validated by a test-local helper.)


# --- User reply parsing (headless resume) ---

# A confident, unambiguous option token: an optional "option" prefix, an optional
# wrapping paren, a single letter OR a 1-99 number (NO leading zero, NO bare "0" —
# options are 1-based, so "0"/"00"/"012" are not valid choices), and an optional
# trailing ")" or ".". Used with ``fullmatch`` on the trimmed/lowercased reply so
# the WHOLE reply must be just this token — a sentence ("go with the first one",
# "I'll take a, thanks") does NOT match and is deferred to the skill's
# natural-language judgement. No ``$`` anchor (it matches before a trailing
# newline); the strip() handles surrounding whitespace/newlines instead.
_REPLY_CHOICE_RE = re.compile(r"(?:option\s*:?\s*)?\(?([a-z]|[1-9][0-9]?)\)?[.)]?")


def parse_reply_choice(reply: str | None) -> str | None:
    """Extract an unambiguous option choice from a user's free-text reply.

    Returns the normalized token (a lowercase letter like ``"a"`` or a number
    like ``"2"``) only when the entire reply IS that token. Returns None for
    empty input, natural language, or anything with more than one candidate — the
    caller then falls back to prose interpretation / a clarification round.
    Conservative by design: a wrong guess here would silently apply the wrong
    decision, so "not sure" must mean "re-ask," never "pick something."
    """
    if not reply:
        return None
    m = _REPLY_CHOICE_RE.fullmatch(reply.strip().lower())
    return m.group(1) if m else None


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

    Shared helper (#264) with fsync=True — suspend state must survive a crash
    right after the write (a headless orchestrator resumes from it).
    """
    atomic_write_text(path, json.dumps(state, indent=2), fsync=True)


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


def _validate_suspend_state(state) -> tuple[bool, str | None]:
    """Check a loaded suspend state is not just JSON-valid but USABLE for resume.

    Returns ``(True, None)`` or ``(False, reason)``. A file that parses but is
    missing or has an empty/ill-typed identifier can never be matched to the
    user's reply, so the resume path must treat it as a hard error rather than
    silently behaving as "no pending question" (which would drop the user's
    decision). This is the validation the original deferred CLI lacked.
    """
    if not isinstance(state, dict):
        return False, "suspend state must be a JSON object"
    for key in ("question_id", "comment_url", "suspended_at"):
        val = state.get(key)
        if not isinstance(val, str) or not val.strip():
            return False, f"suspend state field {key!r} must be a non-empty string"
    issue = state.get("issue")
    # bool is an int subclass — exclude it explicitly so True/False can't pass.
    if isinstance(issue, bool) or not isinstance(issue, int) or issue <= 0:
        return False, "suspend state field 'issue' must be a positive integer"
    if "step" not in state:
        return False, "suspend state is missing 'step'"
    try:
        _parse_step(str(state["step"]))
    except ValueError:
        return False, f"suspend state field 'step' is invalid: {state['step']!r}"
    # clarification_round may be absent in an old/hand-written file — that's not
    # corruption, it defaults to 0; but if present it must be a sane count.
    cr = state.get("clarification_round", 0)
    if isinstance(cr, bool) or not isinstance(cr, int) or cr < 0:
        return False, (
            "suspend state field 'clarification_round' must be a "
            "non-negative integer"
        )
    return True, None


def main(argv=None) -> int:
    """CLI entry point for headless interaction helpers.

    Subcommands:
      QUESTION-suspend path (drives the suspend side from Bash):
        new-id          print a fresh question_id (uuid4)
        format-comment  render the structured GitHub comment body to stdout
        write-suspend   write the suspend state file (atomic)
      Resume path (drives the resume side from Bash):
        read-suspend    read + validate the suspend state file
        parse-reply     extract an unambiguous option choice from a reply on stdin
                        (optionally validated against --options)

    Exit codes:
      0  success
      1  invalid input (empty/malformed identifier or step), a fail-closed write
         error, a suspend file that parses but is unusable, or a reply with no
         unambiguous choice — surfaced as non-zero so a caller never mistakes a
         failed/unmatchable result for success
      3  read-suspend only: no suspend file present (benign "no pending question"
         signal, kept distinct from the corrupt-file error so the caller can
         proceed with normal resumption instead of escalating)
    """
    parser = argparse.ArgumentParser(prog="headless_interaction")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("new-id", help="print a fresh question_id (uuid4)")

    p_fmt = sub.add_parser("format-comment", help="render the comment body")
    p_fmt.add_argument("--step", required=True)
    p_fmt.add_argument("--title", required=True)
    p_fmt.add_argument("--context", required=True)
    # --question/--question-id are required for every BLOCKING type but not for
    # "--type status" (#48: non-blocking progress comment) — enforced at runtime
    # below so the blocking contract still fails closed when they are absent.
    p_fmt.add_argument("--question", default=None)
    p_fmt.add_argument("--option", action="append", help="repeatable")
    p_fmt.add_argument("--type", required=True, dest="type")
    p_fmt.add_argument("--question-id", default=None, dest="question_id")

    p_sus = sub.add_parser("write-suspend", help="write the suspend state file")
    p_sus.add_argument("--path", required=True)
    p_sus.add_argument("--issue", required=True, type=int)
    p_sus.add_argument("--step", required=True)
    p_sus.add_argument("--question-id", required=True, dest="question_id")
    p_sus.add_argument("--comment-url", required=True, dest="comment_url")
    p_sus.add_argument("--session-id", default="N/A", dest="session_id")
    p_sus.add_argument("--clarification-round", default=0, type=int,
                       dest="clarification_round")

    p_read = sub.add_parser("read-suspend",
                            help="read + validate the suspend state file")
    p_read.add_argument("--path", required=True)

    p_reply = sub.add_parser(
        "parse-reply",
        help="extract an unambiguous option choice from a user reply (read on "
             "stdin so an arbitrary reply is never spliced into a command)",
    )
    p_reply.add_argument(
        "--options", default=None,
        help="comma-separated valid option tokens (e.g. 'a,b,c'); when given, the "
             "matched choice MUST be one of them or the command fails closed, so "
             "an out-of-range answer routes to clarification instead of resuming",
    )

    args = parser.parse_args(argv)

    if args.cmd == "new-id":
        print(uuid.uuid4())
        return 0

    if args.cmd == "format-comment":
        try:
            step = _parse_step(args.step)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        if args.type == "status":
            print(format_status_comment(step=step, title=args.title,
                                         context=args.context))
            return 0
        if args.question is None or not args.question.strip():
            print("--question is required for non-status comment types",
                  file=sys.stderr)
            return 1
        if args.question_id is None or not args.question_id.strip():
            print("--question-id must be a non-empty value", file=sys.stderr)
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

    if args.cmd == "read-suspend":
        if not os.path.exists(args.path):
            # Missing file is benign — no pending question. A DISTINCT exit code
            # (3) lets the caller proceed to normal resumption rather than
            # escalating, while a parse/validation failure below stays exit 1.
            print("no suspend file", file=sys.stderr)
            return 3
        state = read_suspend_state(args.path)
        if state is None:
            print("suspend file is unreadable or not valid JSON", file=sys.stderr)
            return 1
        ok, reason = _validate_suspend_state(state)
        if not ok:
            print(reason, file=sys.stderr)
            return 1
        # Materialize the clarification_round default so the printed JSON always
        # carries it — a caller running `jq -r .clarification_round` must get 0,
        # not the JSON null an absent key would yield.
        state.setdefault("clarification_round", 0)
        print(json.dumps(state, separators=(",", ":")))
        return 0

    if args.cmd == "parse-reply":
        # Read the reply from stdin: an arbitrary GitHub comment (which may
        # contain quotes, newlines, shell metacharacters) is never passed as a
        # command-line literal the caller would have to escape.
        choice = parse_reply_choice(sys.stdin.read())
        if choice is None:
            print("no unambiguous option choice in reply", file=sys.stderr)
            return 1
        if args.options is not None:
            valid = {t.strip().lower() for t in args.options.split(",") if t.strip()}
            if choice not in valid:
                print(
                    f"choice {choice!r} is not one of the offered options "
                    f"{sorted(valid)}",
                    file=sys.stderr,
                )
                return 1
        print(choice)
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
