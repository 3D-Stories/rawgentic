"""`switch` SKILL.md stays trimmed, and the rationale stays reachable (#720).

`skills/switch/SKILL.md` is injected into the session as a user message on EVERY
bind, so its size is a per-invocation cost paid by every project switch. #720 cut
it from 12,369 to 4,844 bytes by relocating rationale — not deleting it — into
`skills/switch/references/why.md`, which is loaded only when a step fails or the
reader wants the reasoning.

Nothing else in the suite pins that outcome. The 16 constraints the trim had to
respect are all PRESENCE pins (the canonical load sentence, the expansion-free
registry append, the fail-closed headless wording, the staleness advisory): every
one of them still passes if a maintainer re-inlines the rationale and the file
grows back to its old size. These two guards pin the SIZE and the RELOCATION, so
the regression this issue exists to prevent cannot land silently.

Deliberately a LOCATION pin — reads the two files directly rather than through
`tests.corpus.skill_corpus`, which concatenates SKILL.md with `references/*.md`
and would therefore be blind to prose moving back across that boundary.

On the unmet target: the issue asked for <=3,000 chars and the trim reached 4,796
chars / 4,844 bytes. The ceiling below is a REGROWTH ceiling, not the target — it
is set just above what was achieved so a small future operative step fits, while
still failing loudly on a re-inlined rationale block. The gap to 3,000 is argued
from per-section arithmetic in the PR body, not asserted here.
"""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILL_MD = REPO_ROOT / "skills" / "switch" / "SKILL.md"
WHY_MD = REPO_ROOT / "skills" / "switch" / "references" / "why.md"

# Pre-trim size, for the record: 12,369 bytes. Achieved: 4,844.
REGROWTH_CEILING_BYTES = 5_200

# why.md must carry real relocated rationale, not a stub that satisfies exists().
# Relocated content measured 9,699 bytes; a floor well under that catches deletion
# without pinning the exact prose.
WHY_FLOOR_BYTES = 5_000


def test_switch_skill_md_stays_trimmed():
    """A re-inlined rationale block fails here, and only here."""
    size = SKILL_MD.stat().st_size
    assert size <= REGROWTH_CEILING_BYTES, (
        f"skills/switch/SKILL.md is {size} bytes, over the {REGROWTH_CEILING_BYTES}-byte "
        "regrowth ceiling (#720 trimmed it from 12,369). Its body is injected as a user "
        "message on every bind, so growth is paid per switch. If the addition is an "
        "OPERATIVE step, raise the ceiling in the same commit and say why; if it is "
        "rationale, it belongs in skills/switch/references/why.md."
    )


def test_switch_rationale_is_relocated_not_deleted():
    """The trim's contract is relocation. An empty or missing why.md means deletion."""
    assert WHY_MD.exists(), (
        "skills/switch/references/why.md is missing — #720's contract is that the "
        "rationale MOVED there, so the reasoning stays reachable when a step fails"
    )
    size = WHY_MD.stat().st_size
    assert size >= WHY_FLOOR_BYTES, (
        f"skills/switch/references/why.md is only {size} bytes, under the "
        f"{WHY_FLOOR_BYTES}-byte floor. The rationale trimmed out of SKILL.md was "
        "9,699 bytes; a stub here means it was deleted rather than relocated."
    )
