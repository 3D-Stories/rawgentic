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
# act that should come with wiring the new site's artifact carrier in the same commit.
EXPECTED_SITE_COUNT = 4

# The literal dispatch instruction. Since M0b (#866) every review dispatch goes through the
# runner — discovering on the runner invocation rather than on the marker is what catches a
# review dispatch someone forgot to annotate.
DISPATCH_PHRASE = "hooks/review_runner.py review-"

# Runner invocations that are legitimately NOT a marker-annotated WF gate site, each with the
# reason. Being on this list is a DECLARATION; being absent from both this list and the marker
# set is a failure, which is the point.
_INLINE_CONTEXT_SITES = {
    ("skills/implement-feature/SKILL.md",
     "review-artifact --artifact <file> --type <design|plan|diff|…>"):
        "the <model-routing-resolve> contract's command-shape listing, not a dispatch site.",
    ("skills/implement-feature/SKILL.md",
     "review-code --base <base ref> --brief <brief.md>"):
        "the <model-routing-resolve> contract's command-shape listing, not a dispatch site.",
    ("skills/fix-bug/SKILL.md",
     "review-code --base <default branch> --brief <brief.md>"):
        "the bespoke WF3 contract's command shape, not a dispatch site.",
    ("skills/implement-feature/references/steps.md",
     "review-artifact --artifact <design-doc> --type design --author-model"):
        "Step 4 item 7: the opt-in adversarial-on-design layer — a continuation of the "
        "Step-4 marker-annotated site; the artifact is carried by the mandatory --artifact.",
    ("skills/implement-feature/references/steps.md",
     "review-code --base origin/<default> --brief <brief.md> \\"):
        "Step 11 item 1a: the opt-in diff-review layer, tokenless/report-only; the diff is "
        "composed by the runner itself from --base (structural carrier).",
    ("skills/adversarial-review/SKILL.md",
     "hooks/review_runner.py review-artifact \\"):
        "WF5's standalone invocation — the artifact IS the skill's own input, carried by the "
        "mandatory --artifact.",
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


def test_every_discovered_review_site_names_the_artifact_carrier():
    """M0b (#866): `--requires-context`/`--context-file` retired with the executor — artifact
    delivery is now STRUCTURAL in the runner (a route that cannot carry the bytes cannot be
    called, #826's intent). Each marker site's window must dispatch through the runner and name
    the artifact-carrying parameter (`--brief` for review-code, `--artifact` for
    review-artifact)."""
    missing = []
    for rel, idx, window in _discover_sites():
        if "review_runner.py" not in window:
            missing.append(f"{rel}@{idx}: does not dispatch through hooks/review_runner.py")
        if "--brief" not in window and "--artifact" not in window:
            missing.append(f"{rel}@{idx}: does not name the artifact-carrying parameter")
    assert not missing, (
        "#826: a review site that does not carry its artifact can dispatch a review "
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
        if MARKER in preceding:
            continue
        unaccounted.append(f"{rel}:{ln_no}: review dispatch is neither flag-checked nor declared inline")
    assert not unaccounted, (
        "#826: every review dispatch must declare whether it needs an external artifact:\n  "
        + "\n  ".join(unaccounted))
