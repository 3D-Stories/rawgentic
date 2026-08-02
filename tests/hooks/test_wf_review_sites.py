"""#826: every WF review-dispatch site must DECLARE its context requirement.

Owner decision 2026-08-01: the review-context requirement is carried out of band as a typed
`--requires-context` flag, not inferred from a brief's prose. Three review rounds refuted three
prose heuristics (over-inclusive, under-inclusive, forgeable), and the counter-argument to a typed
flag is real: *a caller who forgets `--context-file` can equally forget `--requires-context`.*

**This file is the answer to that counter-argument.** A forgotten flag is a STATIC property of the
call sites, so a test can read them. A wrong prose guess never was — it could only be discovered in
production. That asymmetry is the entire reason the typed design is better, so this test is the
mechanism that makes the claim true, not a nice-to-have.

DISCOVERY, not a hard-coded list (Step-11 F3). An earlier version pinned four hand-written prose
sentences. That version could pass while a review caller was unwired two ways: a newly added FIFTH
site was simply not looked at, and an anchor sentence left behind after the real dispatch moved
still satisfied it. So the sites are now DISCOVERED from the canonical
``<!-- model-routing: role=review -->`` marker — the same marker the skill contract uses to mean
"a review-role dispatch happens here" — and the expected count is pinned, so adding a review site
forces a deliberate decision instead of silently widening the gap.
"""
import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parents[2]
SKILLS = REPO / "skills"

MARKER = "model-routing: role=review"

# Pinned so a NEW review-dispatch site cannot appear unnoticed. Raising this number is a deliberate
# act that should come with wiring the new site's flags in the same commit.
EXPECTED_SITE_COUNT = 4

# How far past the marker the dispatch mandate must appear. Generous enough to survive rewrapping
# and the intervening legacy-architecture prose, tight enough that the NEXT site's mandate (these
# are hundreds of lines apart) cannot satisfy this one.
_WINDOW = 2500


def _norm(text: str) -> str:
    """Whitespace-normalize so a wrapped sentence still matches (CLAUDE.md §5 prose-pin rule)."""
    return re.sub(r"\s+", " ", text)


def _discover_sites():
    """Every review-dispatch site in the skill corpus, as (relpath, marker_index, window)."""
    found = []
    for md in sorted(SKILLS.rglob("*.md")):
        body = _norm(md.read_text(encoding="utf-8"))
        for m in re.finditer(re.escape(MARKER), body):
            found.append((str(md.relative_to(REPO)), m.start(), body[m.start():m.start() + _WINDOW]))
    return found


def test_the_set_of_review_dispatch_sites_is_what_we_think_it_is():
    """A fifth site appearing must FAIL here rather than be silently unchecked."""
    sites = _discover_sites()
    where = ", ".join(f"{rel}@{idx}" for rel, idx, _ in sites)
    assert len(sites) == EXPECTED_SITE_COUNT, (
        f"expected {EXPECTED_SITE_COUNT} review-dispatch sites, discovered {len(sites)}: {where}. "
        f"A NEW review site must mandate --requires-context and --context-file, and this count "
        f"must be raised in the same commit; a REMOVED one must be deliberate.")


def test_every_discovered_review_site_declares_the_context_requirement():
    """Each site must mandate BOTH flags: declaring without carrying, or carrying without
    declaring, is a half-wired site."""
    missing = []
    for rel, idx, window in _discover_sites():
        if "--requires-context" not in window:
            missing.append(f"{rel}@{idx}: does not mandate --requires-context")
        if "--context-file" not in window:
            missing.append(f"{rel}@{idx}: does not mandate --context-file")
    assert not missing, (
        "#826: a review site that does not declare its context requirement can dispatch a review "
        "of nothing and have the gate read the verdict as a pass:\n  " + "\n  ".join(missing))


def test_the_refuted_prose_marker_is_absent_from_every_skill():
    """The forgeable HTML-comment marker was round 3's refuted mechanism. If it reappears in skill
    prose, a brief is emitting a control signal that artifact data can also emit."""
    offenders = [str(md.relative_to(REPO)) for md in sorted(SKILLS.rglob("*.md"))
                 if "rawgentic:requires-context" in md.read_text(encoding="utf-8")]
    assert not offenders, (
        "the refuted `<!-- rawgentic:requires-context -->` marker is back in: "
        + ", ".join(offenders))
