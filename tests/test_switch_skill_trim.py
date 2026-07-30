"""`switch` SKILL.md stays trimmed, and the rationale stays reachable (#720).

`skills/switch/SKILL.md` is injected into the session as a user message on EVERY
bind, so its size is a per-invocation cost paid by every project switch. #720 cut
it from 12,369 to 5,651 bytes by relocating rationale — not deleting it — into
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

On the unmet target: the issue asked for <=3,000 chars and the trim reached 5,591
chars / 5,651 bytes. The ceiling below is a REGROWTH ceiling, not the target — it
is set just above what was achieved so a small future operative step fits, while
still failing loudly on a re-inlined rationale block. The gap to 3,000 is argued
from per-section arithmetic in the PR body, not asserted here.
"""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILL_MD = REPO_ROOT / "skills" / "switch" / "SKILL.md"
WHY_MD = REPO_ROOT / "skills" / "switch" / "references" / "why.md"

# Pre-trim size, for the record: 12,369 bytes. Achieved: 5,651.
#
# Raised 5,200 -> 6,000 in the same commit that restored three operative items the
# trim had wrongly demoted to rationale (both Step 11 reviewers found them): Step 1's
# free-form target extraction, Step 5b item 1's protection-level descriptions plus its
# top-level storage contract, and item 2b's "non-zero exit OR empty output both mean
# nothing to nudge". That is the raise this guard's own failure message prescribes for
# an OPERATIVE addition — recorded here rather than silently widened.
REGROWTH_CEILING_BYTES = 6_000

# why.md must carry real relocated rationale, not a stub that satisfies exists().
# Relocated content measured 9,699 bytes; a floor well under that catches wholesale
# deletion without pinning the exact prose.
#
# The byte floor ALONE was fake-green and both Step 11 reviewers said so: deleting
# the entire item-3b/rebinding rationale (why.md:103-158, 3,105 bytes) leaves 6,594
# bytes and still clears it. So the floor is now a supplemental check, and the real
# guard is the section list below.
WHY_FLOOR_BYTES = 5_000

# One short, stable anchor per rationale AREA the trim relocated. Deliberately topic
# keys, not sentences: pinning why.md's prose verbatim would recreate the very
# over-specific pin this issue had to fix in test_corpus.py:33. Each anchor dies if
# its section is deleted, and none of them constrains how the section is worded.
REQUIRED_RATIONALE_ANCHORS = (
    # Step 5 — session id: the source and the forbidden alternative.
    "CLAUDE_CODE_SESSION_ID",
    ".current_session_id",
    # Step 5 — why the registry append is split and literal.
    "expansion-free",
    # Step 5b item 2 — why the universal-field check must not use the Read tool.
    "universal-field check",
    # Step 5b item 1 — the protection levels.
    "protection level",
    # Step 5b item 2b — the staleness nudge's failure mode.
    "fail-open",
    # Step 5b item 3 — the headless verdict's failure mode.
    "fails CLOSED",
    # Step 5b item 3b — the bind-time load, which is the whole point of #721.
    "bind-time load",
)


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


def test_every_relocated_rationale_area_survives():
    """Each area the trim moved out of SKILL.md must still be discussed in why.md.

    This is the guard the byte floor only pretended to be. A maintainer deleting one
    whole rationale section — the bind-time load reasoning, say — passes the floor and
    fails here, which is the regression AC2 ("nothing silently deleted") describes.
    """
    text = WHY_MD.read_text(encoding="utf-8")
    missing = [a for a in REQUIRED_RATIONALE_ANCHORS if a not in text]
    assert not missing, (
        "skills/switch/references/why.md no longer covers: "
        f"{missing}. #720's contract is that rationale MOVED out of SKILL.md rather "
        "than being deleted, so every relocated area must still be explained here. "
        "If an area is genuinely obsolete, drop its anchor in the same commit and say "
        "why in the message."
    )
