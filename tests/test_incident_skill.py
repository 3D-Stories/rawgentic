"""Drift guards for the incident skill (#581, restructured #909).

The label bootstrap (#581): the incident workflow opens with
`gh issue create --repo ... --label incident`, and `gh issue create --label X`
FAILS when the repo has no label X — the `incident` label does not exist by
default. So the skill MUST create the label before using it, mirroring the
create-issue / run-feedback bootstrap pattern.

#909 split the 537-line SKILL.md into a contract+spine body plus per-phase
`references/`, which moved that command pair out of SKILL.md. The ordering pin is
therefore rebuilt on `corpus.skill_files()` — the provenance-preserving mapping —
NOT on `skill_corpus()`, whose joined string cannot express "same file" and would
have let an unrelated earlier `gh label create` satisfy the assertion vacuously.

Also guards the split's own structural invariants: both safety gates stay in the
always-loaded body, and every reference is reachable with a read condition.
"""
import re
from pathlib import Path

from tests.corpus import assert_ordered_in_one_file, skill_files

SKILL_DIR = Path(__file__).resolve().parent.parent / "skills" / "incident"
SKILL = SKILL_DIR / "SKILL.md"

CREATE = 'gh issue create --repo ${capabilities.repo} --title "incident(SEV-X)'
BOOTSTRAP = "gh label create incident"


def test_incident_bootstraps_label_before_creating_issue():
    assert_ordered_in_one_file(
        skill_files("incident"),
        BOOTSTRAP,
        CREATE,
        why=(
            "the incident skill creates an issue with --label incident, and "
            "`gh issue create --label` fails on a repo lacking it, so the "
            "bootstrap must come first (#581)"
        ),
    )


# --- #909: what the split must not break -------------------------------


def test_safety_gates_stay_in_the_always_loaded_body():
    """A safety gate must not depend on a references/ file having been read.

    References are read on a prose instruction, which is compliance rather than
    enforcement — fine for procedure detail, not for a gate whose omission ends
    with an unverified SEV-1 restoration or an unapproved destructive rollback.
    """
    body = SKILL.read_text(encoding="utf-8")
    assert "<mandatory-verification>" in body, (
        "the SEV-1/SEV-2 verification gate must stay in SKILL.md, not move to a "
        "lazily-read reference (#909)"
    )
    assert "For SEV-1 and SEV-2: Step 5 is MANDATORY and non-skippable." in body
    assert "**For destructive actions (rollback, DB operations):** Always get user approval first." in body, (
        "the destructive-action rule must stay in SKILL.md, and its ORIGINAL scope "
        "must be preserved — broadening it to every config fix and every DB "
        "operation would stall a SEV-1 on a read-only health check (#909 review F1)"
    )
    # whitespace-normalised: the carve-out wraps across lines, and an exact
    # substring over wrapped prose is the repo's documented drift-guard trap
    flat = " ".join(body.split())
    assert "read-only diagnostic" in flat, "the scope carve-out must be explicit"
    assert "destructive** actions only" in flat


LINK_RE = re.compile(r"references/([A-Za-z0-9._-]+\.md)")


def test_references_and_links_are_in_bijection_each_with_a_read_condition():
    """Set EQUALITY both ways, plus a read condition bound to each link.

    An earlier form of this guard hard-coded reverse resolution to the two phase
    files, so deleting `quick-diagnostic-playbook.md` while leaving its link behind
    passed; and it asserted the read-condition strings as unbound globals, so they
    could sit anywhere in the file while a link had none (#909 review F2).
    """
    body = SKILL.read_text(encoding="utf-8")
    linked = set(LINK_RE.findall(body))
    on_disk = {p.name for p in (SKILL_DIR / "references").glob("*.md")}

    assert on_disk, "the split must leave at least one reference file"
    dangling = sorted(linked - on_disk)
    orphans = sorted(on_disk - linked)
    assert not dangling, f"SKILL.md links references that do not exist: {dangling}"
    assert not orphans, (
        f"these reference files exist but nothing in SKILL.md links them — orphans, "
        f"unreachable in practice: {orphans}"
    )

    # Each link's read condition must sit in the SAME <references> entry as the link,
    # not merely somewhere in the file.
    block = body[body.index("<references>"):body.index("</references>")]
    for name in sorted(on_disk):
        idx = block.find(f"references/{name}")
        assert idx != -1, f"references/{name} is not listed in the <references> block"
        entry = block[idx:idx + 400]
        nxt = entry.find("- `references/", 1)
        if nxt != -1:
            entry = entry[:nxt]
        assert "Read before executing" in entry, (
            f"references/{name} is linked without a read condition in its own entry — "
            f"references load lazily, so a link with no read instruction is prose "
            f"that may never be read (#909)"
        )


def test_skill_body_stays_under_the_line_guidance():
    """Anthropic's guidance is <500 lines for the SKILL.md body.

    Asserted with headroom rather than at the limit: a file sitting at 499 lines
    re-crosses on the next operative edit, which is how the 537-line state arrived.
    """
    lines = len(SKILL.read_text(encoding="utf-8").splitlines())
    assert lines <= 400, f"incident/SKILL.md is {lines} lines — over the 400-line working ceiling (#909)"


def test_the_step_spine_covers_all_fourteen_steps():
    """The spine is the map; a missing step would silently vanish from the body."""
    body = SKILL.read_text(encoding="utf-8")
    for n in range(1, 15):
        assert f"**Step {n} —" in body, f"Step {n} is missing from the SKILL.md spine (#909)"
