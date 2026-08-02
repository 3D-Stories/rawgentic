"""#826: every WF review-dispatch site must DECLARE its context requirement.

Owner decision 2026-08-01: the review-context requirement is carried out of band as a typed
`--requires-context` flag, not inferred from a brief's prose. Three review rounds refuted three
prose heuristics (over-inclusive, under-inclusive, forgeable), and the counter-argument to a typed
flag is real: *a caller who forgets `--context-file` can equally forget `--requires-context`.*

**This file is the answer to that counter-argument.** A forgotten flag is a STATIC property of the
call sites, so a test can read them. A wrong prose guess never was — it could only be discovered in
production. That asymmetry is the entire reason the typed design is better, so this test is the
mechanism that makes the claim true, not a nice-to-have.

DISCOVERY, in two layers, and the second layer exists because the first was not enough.

An early version pinned four hand-written prose sentences — a fifth site would simply not be looked
at. Replacing that with marker discovery was still not enough: the re-review found a REAL fifth
executor `review` seat dispatch (Step 4's spec-tightening incremental verifier) carrying no marker,
so marker discovery returned four and passed while an unannotated review dispatch existed.

So there are two assertions:

1. **Every review-seat DISPATCH is accounted for.** Discovered from the dispatch instruction itself
   (``executor `review` seat``), not from an annotation that can simply be missing. Each must be
   either marker-annotated (and therefore flag-checked) or on ``_INLINE_CONTEXT_SITES`` with a
   stated reason. A new dispatch is neither, so it FAILS.
2. **Every marker-annotated site mandates both flags.**

The distinction the re-review drew and this file now encodes: **`role=review` and "needs an external
artifact" are different properties.** The incremental verifier is a genuine review dispatch that
deliberately INLINES its before/after sections, so mandating `--context-file` there would be wrong.
It is exempt by name, not by being invisible.
"""
import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parents[2]
SKILLS = REPO / "skills"

MARKER = "model-routing: role=review"

# Pinned so a NEW review-dispatch site cannot appear unnoticed. Raising this number is a deliberate
# act that should come with wiring the new site's flags in the same commit.
EXPECTED_SITE_COUNT = 4

# The literal dispatch instruction. Discovering on THIS rather than on the marker is what catches a
# review dispatch someone forgot to annotate.
DISPATCH_PHRASE = "executor `review` seat"

# Review dispatches that legitimately need NO external artifact, each with the reason it is exempt.
# Being on this list is a DECLARATION; being absent from both this list and the marker set is a
# failure, which is the point.
_INLINE_CONTEXT_SITES = {
    ("skills/implement-feature/references/steps.md",
     "incremental verifier"):
        "Step 4 spec-tightening: the verifier reviews ONLY the changed design sections and quotes "
        "them before/after INLINE, so there is no external artifact to attach.",
    # Continuations: prose that re-describes a dispatch already declared a line or two above, at
    # the marker-annotated site. Named explicitly rather than absorbed by widening the search
    # window — a wider window would also let an unrelated marker vouch for a genuinely new,
    # unannotated dispatch, which is exactly what this test exists to catch.
    ("skills/implement-feature/references/steps.md",
     "Dispatch 2 reviewers in parallel"):
        "Step 8a continuation of the marker-annotated dispatch two lines above; same dispatch, "
        "described again for the per-reviewer lens split.",
    ("skills/fix-bug/references/steps.md",
     "Launch a focused 2-agent code review in parallel"):
        "WF3 continuation of the marker-annotated dispatch two lines above; same dispatch, "
        "described again for the per-slot architecture split.",
}

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


def _discover_dispatches():
    """Every place the corpus tells the orchestrator to dispatch a review seat."""
    out = []
    for md in sorted(SKILLS.rglob("*.md")):
        raw = md.read_text(encoding="utf-8")
        for ln_no, line in enumerate(raw.splitlines(), 1):
            if DISPATCH_PHRASE in line:
                out.append((str(md.relative_to(REPO)), ln_no, line))
    return out


def test_every_review_dispatch_is_either_flag_checked_or_declared_inline():
    """Re-review F2: marker discovery alone passed while an UNANNOTATED review dispatch existed.

    A review dispatch must be one of two things, explicitly:
      * marker-annotated -> its flags are checked by the test above, or
      * named in `_INLINE_CONTEXT_SITES` with a reason -> it needs no external artifact.

    Being neither is how a review of nothing gets dispatched, so it fails here.
    """
    unaccounted = []
    for rel, ln_no, line in _discover_dispatches():
        exempt = any(rel == r and key in line for (r, key) in _INLINE_CONTEXT_SITES)
        if exempt:
            continue
        body = _norm(pathlib.Path(REPO / rel).read_text(encoding="utf-8"))
        idx = body.find(_norm(line)[:80])
        # a marker within the preceding window means this dispatch is one of the annotated sites
        preceding = body[max(0, idx - 400):idx] if idx >= 0 else ""
        if MARKER in preceding or "--requires-context" in body[idx:idx + _WINDOW]:
            continue
        unaccounted.append(f"{rel}:{ln_no}: review dispatch is neither flag-checked nor declared inline")
    assert not unaccounted, (
        "#826: every review dispatch must declare whether it needs an external artifact:\n  "
        + "\n  ".join(unaccounted))
