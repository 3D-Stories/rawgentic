"""Drift guards for docs/runbooks/herdr.md (#696).

The #696 defect was a guidance gap whose only visible symptom argued for the wrong
response. A ~1,400-char prompt sent with `herdr pane run` did not submit, the pane
showed `paste again to expand`, and that string was read as evidence the content had
been mangled — inviting a retry (double-submission) or a truncation (silent
corruption). It is in fact Claude Code's normal collapsed-display affordance and
appears on SUCCESSFUL submissions.

No runtime code changed: `launcher_lib.build_send_text_argv` already emits a separate
Enter and cites #654 for why. These guards pin the guidance instead, each anchoring ONE
canonical sentence in the ONE doc (location pin, direct file read) per the repo's
drift-guard rules.

The `herdr wait` guard is deliberately SCOPED to instructional surfaces. `herdr wait`
appears across ~12 `docs/planning/` files and a README changelog entry, all correctly
recording that it does not exist — a corpus-wide regex would fail on contact, which is
workspace mistake #11. What matters is that no doc a reader COPIES A COMMAND FROM ever
presents it as runnable.
"""

import os
import re

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOC_PATH = os.path.join(REPO_ROOT, "docs", "runbooks", "herdr.md")

# A TOP-LEVEL `herdr wait`. Does not match `herdr agent wait`, which exists in 0.7.5 and
# is the real readiness primitive — the whole point of the correction.
_TOP_LEVEL_WAIT = re.compile(r"herdr\s+wait\b")

# A mention is acceptable only when the same line says the command is not real.
_NEGATED = ("does not exist", "did not exist", "absent", "not a command",
            "no top-level", "does NOT exist")


def _doc_text() -> str:
    with open(DOC_PATH, encoding="utf-8") as f:
        return f.read()


def _doc_normalized() -> str:
    return " ".join(_doc_text().split())


def _instructional_docs() -> list:
    """Docs a reader copies commands out of: the runbooks and the skill library.

    Deliberately excludes `docs/planning/` and the README changelog, which are
    historical records and legitimately quote the broken command while saying so.
    """
    paths = []
    runbooks = os.path.join(REPO_ROOT, "docs", "runbooks")
    for name in sorted(os.listdir(runbooks)):
        if name.endswith(".md"):
            paths.append(os.path.join(runbooks, name))
    skills = os.path.join(REPO_ROOT, "skills")
    for dirpath, _dirnames, filenames in os.walk(skills):
        for name in sorted(filenames):
            if name.endswith(".md"):
                paths.append(os.path.join(dirpath, name))
    return paths


def test_doc_exists():
    assert os.path.isfile(DOC_PATH)


# --------------------------------------------------------------------------
# AC6 — the drift guard proper
# --------------------------------------------------------------------------

def test_no_top_level_herdr_wait_is_presented_as_runnable():
    """It does not exist in 0.7.5, so no instructional doc may offer it as a command.

    Two shapes count as offering it: inside a fenced code block (which is what a reader
    copies), or a bare command line. A prose mention is allowed ONLY alongside its own
    negation — that mention is load-bearing, because it is what stops the command being
    reintroduced by someone who read the upstream skill.
    """
    offenders = []
    for path in _instructional_docs():
        fenced = False
        with open(path, encoding="utf-8") as f:
            for lineno, line in enumerate(f.read().split("\n"), 1):
                if line.lstrip().startswith("```"):
                    fenced = not fenced
                    continue
                if not _TOP_LEVEL_WAIT.search(line):
                    continue
                rel = os.path.relpath(path, REPO_ROOT)
                if fenced:
                    offenders.append(f"{rel}:{lineno} inside a code block")
                elif line.lstrip().startswith("herdr wait"):
                    offenders.append(f"{rel}:{lineno} bare command line")
                elif not any(n.lower() in line.lower() for n in _NEGATED):
                    offenders.append(f"{rel}:{lineno} unqualified prose mention")
    assert not offenders, (
        "a top-level `herdr wait` is presented as runnable in instructional docs — it "
        f"does not exist in herdr 0.7.5 (#696): {offenders}")


def test_the_real_readiness_primitive_is_still_documented():
    """The guard above must not be satisfiable by deleting the correction outright."""
    doc = _doc_normalized()
    assert "herdr agent wait" in doc
    assert _TOP_LEVEL_WAIT.search(doc), (
        "the note that the top-level `herdr wait` does not exist was removed — the guard "
        "then passes vacuously and nothing stops it being reintroduced")


# --------------------------------------------------------------------------
# AC4 — `agent wait` keys on an agent NAME
# --------------------------------------------------------------------------

def test_agent_wait_is_documented_as_taking_an_agent_name_not_a_pane_id():
    """Measured live: passing a pane id returns `agent_not_found`, so "wait on the pane"
    is wrong for any pane whose agent was not registered via `herdr agent start`."""
    doc = _doc_normalized()
    assert "agent NAME, not a pane id" in doc
    assert "agent_not_found" in doc


# --------------------------------------------------------------------------
# AC2 — `paste again to expand` is not an error
# --------------------------------------------------------------------------

def test_the_collapsed_paste_affordance_is_documented_as_success():
    doc = _doc_normalized()
    assert "paste again to expand" in doc
    assert "[Pasted text +N lines]" in doc
    assert "the buffer is INTACT and must be submitted as-is" in doc


def test_neither_retry_nor_truncation_is_permitted():
    """Both wrong responses have a named cost, because "don't do that" without the cost
    is what got ignored the first time."""
    doc = _doc_normalized()
    assert "never retried" in doc and "never truncated" in doc
    assert "double-submission" in doc
    assert "silently corrupts the handoff" in doc


# --------------------------------------------------------------------------
# AC3 — the send pattern, and what `pane run` is for
# --------------------------------------------------------------------------

def test_prompt_text_uses_send_text_then_a_separate_enter():
    doc = _doc_normalized()
    assert "`pane send-text` then a SEPARATE `pane send-keys Enter`" in doc


def test_pane_run_is_documented_as_a_shell_command_runner():
    """The sharper diagnosis: `pane run` is the wrong tool, not a tool used wrongly."""
    doc = _doc_normalized()
    assert "`pane run` is a shell-command runner" in doc
    assert "never used for prompt text" in doc


def test_submission_is_verified_from_the_transcript_never_from_agent_status():
    doc = _doc_normalized()
    assert "verified from the TRANSCRIPT" in doc
    assert "never from `agent_status`" in doc


# --------------------------------------------------------------------------
# AC5 — the #659 mis-citation is corrected
# --------------------------------------------------------------------------

# Bans ATTRIBUTION to #659, not every mention of it. Naming the old mis-citation while
# correcting it is worth keeping — it is what stops the wrong number being restored — so a
# blanket "#659 must not appear" rule would forbid the very sentence that fixes the defect.
_ATTRIBUTED_TO_659 = re.compile(r"(?:tracked as|see|per)\s+#659|\(#659\)", re.IGNORECASE)


def test_the_missing_wait_is_not_attributed_to_659():
    """#659 is herdr version-drift detection (GAP-4), a different concern. Three lines
    cited it as tracking the stale `wait` docs, so nothing actually tracked them."""
    offenders = []
    with open(DOC_PATH, encoding="utf-8") as f:
        for lineno, line in enumerate(f.read().split("\n"), 1):
            if _TOP_LEVEL_WAIT.search(line) and _ATTRIBUTED_TO_659.search(line):
                offenders.append(f"{lineno}: {line.strip()[:80]}")
    assert not offenders, (
        "the missing top-level `herdr wait` is still attributed to #659, which is "
        f"version-drift detection, not this: {offenders}")


def test_the_missing_wait_is_attributed_to_696():
    doc = _doc_normalized()
    assert "#696" in doc


# --------------------------------------------------------------------------
# AC7 — the out-of-git skill limitation is stated
# --------------------------------------------------------------------------

def test_the_user_level_skill_gap_is_recorded():
    """Its five stale invocations cannot be fixed or CI-tested from this repo, so the
    next reader has to be told that is a known gap rather than an oversight."""
    doc = _doc_normalized()
    assert "~/.claude/skills/herdr/SKILL.md" in doc
    assert "outside any git repository" in doc
