"""AskUserQuestion registration guard (#947 Part B AC8, §10).

Static, repo-wide, test-only — guard logic lives HERE, not a separate hooks module,
because nothing else consumes it (mirrors `tests/test_retirement_tripwire.py`'s own
convention). Every literal `AskUserQuestion` occurrence under `skills/**/*.md` must be
EITHER in the explicit ALLOWLIST (one pre-existing, non-supervision-relevant mention:
`skills/pane-handoff/SKILL.md`), OR accompanied by an `AskUserQuestion route:
<owner-only|supervision-gated>` marker line within 6 lines before it — self-documenting,
so a NEW skill adding a question without ever having read this guard fails loudly,
naming exactly what to add, rather than silently bypassing the routing protocol the M4
design's departure preflight depends on.

Interception of the harness tool itself is impossible (a model-invoked harness tool, not
a Python function this guard could wrap) — discovery is not, which is the whole point
(Part A §12's own refutation, carried forward here).
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILLS_DIR = REPO_ROOT / "skills"

#: File paths (repo-relative, POSIX separators) exempt entirely — a pre-existing,
#: non-supervision-relevant mention that predates this guard.
ALLOWLIST = frozenset({"skills/pane-handoff/SKILL.md"})

_MENTION_RE = re.compile(r"AskUserQuestion")
_MARKER_RE = re.compile(r"AskUserQuestion route:\s*(owner-only|supervision-gated)\b")
_MARKER_WINDOW = 6


def find_askuserquestion_sites(text: str, *, relpath: str) -> list:
    """Every unmarked, non-allowlisted `AskUserQuestion` mention in `text`.

    Returns a list of `(line_number, line_text)` for each mention that is NEITHER
    allowlisted by file path NOR preceded (within `_MARKER_WINDOW` lines, inclusive of
    its own line) by a marker line. A line that IS itself a marker line never needs a
    marker of its own — it names its own route.
    """
    if relpath in ALLOWLIST:
        return []
    lines = text.splitlines()
    marker_lines = {i for i, line in enumerate(lines) if _MARKER_RE.search(line)}
    violations = []
    for i, line in enumerate(lines):
        if not _MENTION_RE.search(line):
            continue
        if i in marker_lines:
            continue  # the marker line itself names its own route
        window = range(max(0, i - _MARKER_WINDOW), i + 1)
        if any(j in marker_lines for j in window):
            continue
        violations.append((i + 1, line.strip()))
    return violations


def _all_skill_markdown():
    return sorted(SKILLS_DIR.rglob("*.md"))


def test_synthetic_unmarked_mention_fails():
    text = "Some prose.\nAsk via AskUserQuestion for the decision.\nMore prose.\n"
    sites = find_askuserquestion_sites(text, relpath="skills/synthetic/SKILL.md")
    assert sites == [(2, "Ask via AskUserQuestion for the decision.")]


def test_synthetic_marked_mention_passes():
    text = (
        "Some prose.\n"
        "AskUserQuestion route: owner-only\n"
        "Ask via AskUserQuestion for the decision.\n"
    )
    assert find_askuserquestion_sites(text, relpath="skills/synthetic/SKILL.md") == []


def test_synthetic_marker_on_the_same_line_passes():
    text = "Ask via AskUserQuestion (AskUserQuestion route: owner-only) for the decision.\n"
    assert find_askuserquestion_sites(text, relpath="skills/synthetic/SKILL.md") == []


def test_synthetic_marker_more_than_6_lines_before_does_not_count():
    text = "AskUserQuestion route: owner-only\n" + "filler line\n" * 7 + \
        "Ask via AskUserQuestion for the decision.\n"
    sites = find_askuserquestion_sites(text, relpath="skills/synthetic/SKILL.md")
    assert len(sites) == 1


def test_synthetic_unrecognized_route_value_does_not_count_as_a_marker():
    """An off-vocabulary route value is not a marker for the NEXT mention, and it is
    also itself an unmarked mention (it contains the literal string too) -- both lines
    are flagged, not silently accepted as "close enough"."""
    text = "AskUserQuestion route: sometimes\nAsk via AskUserQuestion for the decision.\n"
    sites = find_askuserquestion_sites(text, relpath="skills/synthetic/SKILL.md")
    assert len(sites) == 2


def test_allowlisted_file_is_exempt_even_when_unmarked():
    text = "Ask via AskUserQuestion for the decision.\n"
    assert find_askuserquestion_sites(text, relpath="skills/pane-handoff/SKILL.md") == []


def test_the_real_repo_tree_passes_today():
    """Given #947 already added markers at every new site (away/sleeping SKILL.md),
    the real tree must pass with no violations."""
    offenders = {}
    for path in _all_skill_markdown():
        relpath = str(path.relative_to(REPO_ROOT))
        text = path.read_text(encoding="utf-8")
        sites = find_askuserquestion_sites(text, relpath=relpath)
        if sites:
            offenders[relpath] = sites
    assert offenders == {}, f"unmarked AskUserQuestion site(s): {offenders}"


def test_the_allowlist_still_names_a_real_file_with_a_real_mention():
    """An allowlist entry that no longer matches anything is dead weight that would
    silently widen scope the next time someone edits that file."""
    for relpath in ALLOWLIST:
        path = REPO_ROOT / relpath
        assert path.exists(), f"allowlisted file does not exist: {relpath}"
        assert _MENTION_RE.search(path.read_text(encoding="utf-8")), \
            f"allowlisted file no longer mentions AskUserQuestion: {relpath}"
