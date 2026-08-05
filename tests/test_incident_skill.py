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
    assert "For destructive actions (rollback, DB operations): Always get user approval first." in body


def test_every_reference_is_linked_with_a_read_condition():
    """Both directions, plus the condition.

    A one-way "every link resolves" check passes an ORPHAN reference that nothing
    links to, and passes a bare link carrying no read instruction — either way the
    prose is unreachable in practice while the guard stays green.
    """
    body = SKILL.read_text(encoding="utf-8")
    refs = sorted(p.name for p in (SKILL_DIR / "references").glob("*.md"))
    assert refs, "the split must leave at least one reference file"
    for name in refs:
        rel = f"references/{name}"
        assert rel in body, f"{rel} exists but SKILL.md never links it — an orphan reference (#909)"
    # every linked reference resolves, and a read condition is stated
    for phase_ref in ("references/phase-a-stabilize.md", "references/phase-b-analyze.md"):
        assert (SKILL_DIR / phase_ref).is_file(), f"{phase_ref} is linked but missing"
    assert "Read before executing Steps 1–6." in body
    assert "Read before executing Steps 7–14." in body


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
