"""#826: the four WF review-dispatch sites must DECLARE their context requirement.

Owner decision 2026-08-01: the review-context requirement is carried out of band as a typed
`--requires-context` flag, not inferred from a brief's prose. Three review rounds refuted three
prose heuristics (over-inclusive, under-inclusive, forgeable), and the counter-argument to a typed
flag is real: *a caller who forgets `--context-file` can equally forget `--requires-context`.*

**This file is the answer to that counter-argument.** A forgotten flag is a STATIC property of the
call sites, so a test can read them. A wrong prose guess never was — it could only be discovered in
production. That asymmetry is the entire reason the typed design is better, so this test is not a
nice-to-have: it is the mechanism that makes the claim true.

Deliberately a PROSE-CONTRACT test, not a behavioural one. These sites are markdown instructions to
a model, so there is no code path to exercise — the contract is that each site tells the orchestrator
to pass both flags. Guard shape follows the repo convention for prose pins (CLAUDE.md §5): anchor to
ONE canonical sentence per site, slice by a stable nearby anchor, whitespace-normalize so wrapping
cannot break it.
"""
import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parents[2]

# (path, a stable anchor identifying the site, human name for the failure message)
# The anchor is the artifact each site is required to attach — stable across rewordings of the
# surrounding prose, and distinct per site.
SITES = [
    ("skills/implement-feature/references/steps.md",
     "The design document is a REQUIRED input",
     "WF2 Step 4 (design critique)"),
    ("skills/implement-feature/references/steps.md",
     "The concatenated high-risk diff is a REQUIRED input",
     "WF2 Step 8a (per-task review)"),
    ("skills/implement-feature/references/steps.md",
     "The PR diff is a REQUIRED input",
     "WF2 Step 11 (pre-PR review)"),
    ("skills/fix-bug/references/steps.md",
     "The fix diff is a REQUIRED input",
     "WF3 (fix review)"),
]

# How far past the anchor the mandate must appear. Generous enough to survive rewrapping, tight
# enough that a mandate belonging to a DIFFERENT site cannot satisfy this one (the sites are
# hundreds of lines apart).
_WINDOW = 600


def _norm(text: str) -> str:
    """Whitespace-normalize so a wrapped sentence still matches (CLAUDE.md §5 prose-pin rule)."""
    return re.sub(r"\s+", " ", text)


def test_all_four_review_sites_declare_the_context_requirement():
    """Each site must mandate BOTH flags: declaring without carrying, or carrying without
    declaring, is a half-wired site."""
    missing = []
    for rel, anchor, name in SITES:
        body = _norm((REPO / rel).read_text(encoding="utf-8"))
        assert anchor in body, f"{name}: anchor {anchor!r} not found in {rel} — site moved or renamed"
        window = body[body.index(anchor): body.index(anchor) + _WINDOW]
        if "--requires-context" not in window:
            missing.append(f"{name} ({rel}): does not mandate --requires-context")
        if "--context-file" not in window:
            missing.append(f"{name} ({rel}): does not mandate --context-file")
    assert not missing, (
        "#826: a review site that does not declare its context requirement can dispatch a review "
        "of nothing and have the gate read the verdict as a pass:\n  " + "\n  ".join(missing))


def test_every_site_is_a_distinct_location():
    """Three of the four live in one file; a copy-paste that collapsed two sites onto the same
    anchor would silently halve the coverage this test claims."""
    seen = {}
    for rel, anchor, name in SITES:
        body = _norm((REPO / rel).read_text(encoding="utf-8"))
        assert body.count(anchor) == 1, (
            f"{name}: anchor {anchor!r} appears {body.count(anchor)} times in {rel} — an anchor "
            f"must identify exactly one site")
        seen.setdefault(rel, []).append(body.index(anchor))
    for rel, offsets in seen.items():
        assert len(set(offsets)) == len(offsets), f"{rel}: two sites resolved to the same offset"


def test_the_refuted_prose_marker_is_absent_from_every_skill():
    """The forgeable HTML-comment marker was round 3's refuted mechanism. If it reappears in skill
    prose, a brief is emitting a control signal that artifact data can also emit."""
    offenders = []
    for md in sorted((REPO / "skills").rglob("*.md")):
        if "rawgentic:requires-context" in md.read_text(encoding="utf-8"):
            offenders.append(str(md.relative_to(REPO)))
    assert not offenders, (
        "the refuted `<!-- rawgentic:requires-context -->` marker is back in: "
        + ", ".join(offenders))
