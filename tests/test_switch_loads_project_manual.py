"""Bind-time project-rules load guard (#721).

`/rawgentic:switch` binds a session to a project but historically did not put that
project's `CLAUDE.md` into context. The harness auto-loads a `CLAUDE.md` lazily, on the
first use of one of its OWN file tools (`Read`/`Edit`) inside a subtree — shell access
does not count, because the harness cannot see inside a bash one-liner. `switch` reads
all its config through shell one-liners, so binding never triggered the load.

The fix is one `Read` tool call on the bound project's `.rawgentic.json`, placed after
the fail-closed Headless Access Check and before "Confirm Ready". These guards pin the
three things that would silently disable it:

1. the TOOL CLASS — swapping `Read` for `cat`/`head`/`jq` loads nothing, and reads
   perfectly correct;
2. the POSITION — before the registry append `hooks/wal-bind-guard` Gate 1 denies the
   read; before the headless verdict, project-controlled prose could influence its own
   fail-closed authorization;
3. the SILENCE — 14 of 24 active projects have no `CLAUDE.md` and must bind with no
   warning at all.

Reads `skills/switch/SKILL.md` DIRECTLY rather than via `tests.corpus.skill_corpus`.
That is deliberate and is the point of the guard: this is a LOCATION pin (repo CLAUDE.md
§1 — content pins read the corpus, location pins read the file). Sibling issue #720
trims this same SKILL.md by ~75%, moving rationale into `references/why.md`; a corpus
read would still pass after the step was swept in there as prose, which is exactly the
regression these tests exist to catch.
"""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILL_MD = REPO_ROOT / "skills" / "switch" / "SKILL.md"

# Section anchors, in the order they must appear in the file.
REGISTRY_APPEND_ANCHOR = "session_registry.jsonl"
HEADLESS_ANCHOR = "### 3. Headless Access Check"
LOAD_ANCHOR = "### 3b. Load the project's operating rules"
READY_ANCHOR = "### 4. Confirm Ready"

# The one canonical operative sentence. It carries the tool class, the target file and
# the Bash prohibition together, so no amount of scattered prose elsewhere can satisfy
# it by accident.
CANONICAL_SENTENCE = (
    "Use the `Read` tool on `<project path>/.rawgentic.json`. "
    "Never Bash (`cat`/`head`/`jq`)."
)


def _text() -> str:
    return SKILL_MD.read_text(encoding="utf-8")


def _norm(s: str) -> str:
    """Whitespace-normalise so the pin survives prose re-wrapping."""
    return " ".join(s.split())


def _load_section() -> str:
    """The load item only, sliced by header index (test_wf2_clarity.py:440-454 pattern)."""
    text = _text()
    start = text.index(LOAD_ANCHOR)
    end = text.index(READY_ANCHOR, start)
    return text[start:end]


def test_anchors_are_non_vacuous():
    """Every anchor the ordering tests rely on must actually exist.

    Without this, an assertion like "load comes after the append" could pass simply
    because neither string was found.
    """
    text = _text()
    for anchor in (REGISTRY_APPEND_ANCHOR, HEADLESS_ANCHOR, LOAD_ANCHOR, READY_ANCHOR):
        assert anchor in text, f"anchor missing from switch SKILL.md: {anchor!r}"


def test_canonical_load_sentence_present():
    """The operative contract, as ONE sentence, inside the load section.

    Pins tool class + target + Bash prohibition together. A trim that keeps the words
    but loses the instruction fails here.
    """
    section = _norm(_load_section())
    assert _norm(CANONICAL_SENTENCE) in section, (
        "switch SKILL.md's bind-time load section must carry the canonical sentence "
        f"verbatim:\n  {CANONICAL_SENTENCE}\ngot section:\n{section[:400]}"
    )


def test_load_is_after_the_registry_append():
    """The registry append IS the bind; before it, wal-bind-guard Gate 1 denies the Read.

    Anchored explicitly because the append is the platform gate that makes the read
    permissible at all — moving the append below the load would leave every other
    assertion green while the read is denied at runtime.
    """
    text = _text()
    assert text.index(REGISTRY_APPEND_ANCHOR) < text.index(LOAD_ANCHOR), (
        "the bind-time load must come AFTER the session_registry.jsonl append — before "
        "the append the session is unbound and hooks/wal-bind-guard Gate 1 denies a Read "
        "of any active project's files"
    )


def test_load_is_after_the_headless_verdict():
    """Project prose must never influence its own fail-closed authorization check."""
    text = _text()
    assert text.index(HEADLESS_ANCHOR) < text.index(LOAD_ANCHOR), (
        "the bind-time load must come AFTER the Headless Access Check — loading a "
        "project's own rules before the fail-closed verdict lets project-controlled "
        "text influence whether this session may operate on that project"
    )


def test_load_is_before_confirm_ready():
    """'Ready' must not be reported before the project's rules are in context."""
    text = _text()
    assert text.index(LOAD_ANCHOR) < text.index(READY_ANCHOR), (
        "the bind-time load must come BEFORE Confirm Ready"
    )


def test_no_manual_case_is_silent():
    """14 of 24 active projects have no CLAUDE.md; a nag on every one of those is a defect.

    #720 is most likely to trim this line as stating the obvious, which is precisely
    why it is pinned.
    """
    section = _norm(_load_section())
    assert "Never announce a missing manual." in section, (
        "the load section must forbid announcing a missing manual — projects without a "
        "CLAUDE.md bind silently"
    )


def test_failed_read_does_not_report_ready():
    """A failed load must not be reported as a clean bind.

    The registry append has already succeeded, so the session really IS bound — but its
    rules are absent, and saying 'Ready' would hide that.
    """
    section = _norm(_load_section())
    assert "do not report Ready" in section, (
        "the load section must state that a failed Read does not report Ready"
    )
