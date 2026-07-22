"""Drift guard for the incident skill's label bootstrap (#581).

The incident workflow opens with `gh issue create --repo ... --label incident`.
`gh issue create --label X` FAILS when the repo has no label X — and the
`incident` label does not exist by default. So the skill MUST create the label
before using it, mirroring the create-issue / run-feedback bootstrap pattern.

Anchored to ONE canonical file (location pin); ordering asserted by index so a
future reorder that puts the create before the bootstrap fails loudly.
"""
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent / "skills" / "incident" / "SKILL.md"


def test_incident_bootstraps_label_before_creating_issue():
    text = SKILL.read_text(encoding="utf-8")

    create_at = text.find('gh issue create --repo ${capabilities.repo} --title "incident(SEV-X)')
    assert create_at != -1, "incident tracking-issue create command not found — skill shape changed"

    bootstrap_at = text.find("gh label create incident")
    assert bootstrap_at != -1, (
        "incident skill creates an issue with --label incident but never bootstraps "
        "the label; `gh issue create --label` fails on a repo lacking it"
    )
    assert bootstrap_at < create_at, (
        "the `gh label create incident` bootstrap must come BEFORE the "
        "`gh issue create ... --label incident` that depends on it"
    )
