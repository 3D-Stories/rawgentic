"""#840 — issue-body citation extraction for the revalidation worklist.

Fixture bodies under `tests/fixtures/issue_bodies/` are REAL epic-#756 children, captured
2026-08-02 with their source hashes in MANIFEST.json. Their expected classifications were
MEASURED against `origin/main`, never assumed: an earlier revision of the #840 design asserted
that #797 cited no repository paths, which was false (it cites `hooks/context_meter.py:84-85`),
and a fixture built on that claim would have encoded wrong parser behaviour.

Pure functions imported directly per `docs/testing.md:5-8`.
"""
import hashlib
import json
import re
import sys
import time
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parents[2] / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

import driver_lib as dl  # noqa: E402

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "issue_bodies"


def _body(number: int) -> str:
    return (FIXTURES / f"{number}.md").read_text(encoding="utf-8")


# Measured 2026-08-02 against origin/main 3d4e1607 by resolving every path-shaped
# candidate with `git cat-file -e origin/main:<path>`. The `resolves` set is the
# injected endpoint-resolution fact; `cited_paths` itself does no I/O.
MEASURED = {
    838: {"expected": "paths",  # measured fact: issue #838's body cites since-retired paths
          "resolves": {"hooks/executor_routing_lib.py",  # tripwire-exempt: recorded measurement
                       "phase_executor/src/phase_executor/supervisor.py"}},  # tripwire-exempt: recorded measurement
    # CHANGED after the Step-11 review: #734 names three path-shaped tokens and only two
    # resolve, so the body is only PARTLY readable. Under the corrected rule — any unresolved
    # candidate makes the whole body ambiguous — its true classification is `ambiguous`, not
    # `paths`. The earlier expectation would have let a partly-unreadable body take the
    # `quick` path.
    734: {"expected": "ambiguous",
          "resolves": {"hooks/context_meter.py", "hooks/hooks.json"}},
    763: {"expected": "paths",
          "resolves": {"docs/reviews/session-mining-2026-07-30.md",
                       "hooks/launcher_lib.py", "skills/pane-handoff/SKILL.md"}},
    835: {"expected": "paths",
          "resolves": {"hooks/launcher_lib.py",
                       "tests/hooks/test_adhoc_pane_handoff.py"}},
    # #775 is the genuinely citation-free body, found BY MEASUREMENT across nine
    # candidate children rather than by picking one that looked prose-heavy.
    775: {"expected": "none", "resolves": set()},
}


class TestFixtureIntegrity:
    def test_fixture_bodies_match_their_recorded_hashes(self):
        """A fixture that drifts silently would invalidate every expectation below."""
        manifest = json.loads((FIXTURES / "MANIFEST.json").read_text(encoding="utf-8"))
        for number, meta in manifest.items():
            raw = (FIXTURES / f"{number}.md").read_bytes()
            assert hashlib.sha256(raw).hexdigest() == meta["sha256"], (
                f"fixture {number}.md changed since capture")
            assert len(raw) == meta["bytes"]

    def test_every_measured_fixture_is_present(self):
        for number in MEASURED:
            assert (FIXTURES / f"{number}.md").is_file()


class TestCitedPathsOnRealBodies:
    @pytest.mark.parametrize("number", sorted(MEASURED))
    def test_extraction_matches_the_measured_classification(self, number):
        case = MEASURED[number]
        paths, extraction = dl.cited_paths(_body(number), resolves=case["resolves"])
        assert extraction == case["expected"], (
            f"#{number}: expected {case['expected']!r}, got {extraction!r}")
        assert set(paths) == case["resolves"], (
            f"#{number}: extracted {sorted(paths)} but only {sorted(case['resolves'])} resolve")

    def test_a_partly_unreadable_body_is_ambiguous(self):
        """#734 names three path-shaped tokens; only two exist on main. The resolved two are
        still returned, but the VERDICT is `ambiguous` — a body we could only partly read is
        one we cannot vouch for, and it must take the deep path."""
        paths, extraction = dl.cited_paths(_body(734), resolves=MEASURED[734]["resolves"])
        assert extraction == "ambiguous"
        assert set(paths) == MEASURED[734]["resolves"]


class TestExtractionEdgeCases:
    def test_absolute_paths_are_rejected(self):
        assert dl.cited_paths("see `/etc/passwd` and `/home/u/x.py`",
                              resolves={"/etc/passwd", "/home/u/x.py"}) == ([], "none")

    def test_traversal_is_rejected(self):
        assert dl.cited_paths("see `../../secrets/key.py`",
                              resolves={"../../secrets/key.py"}) == ([], "none")

    def test_line_and_range_suffixes_are_stripped(self):
        paths, _ = dl.cited_paths("`hooks/a.py:84-85` and `hooks/b.py:12`",
                                  resolves={"hooks/a.py", "hooks/b.py"})
        assert set(paths) == {"hooks/a.py", "hooks/b.py"}

    def test_github_line_anchor_forms_are_recognised(self):
        paths, _ = dl.cited_paths("`hooks/a.py#L84` and `hooks/b.py@12`",
                                  resolves={"hooks/a.py", "hooks/b.py"})
        assert set(paths) == {"hooks/a.py", "hooks/b.py"}

    def test_urls_are_not_treated_as_repository_paths(self):
        """Asserts the EXACT tuple. The earlier version checked only that `paths` was empty,
        and a reviewer showed it stayed green with URL handling deleted — the regex then
        extracted `com/a/b/c.py`, failed to resolve, and produced `ambiguous`, which the
        assertion never looked at."""
        assert dl.cited_paths("https://example.com/a/b/c.py is not ours",
                              resolves={"a/b/c.py"}) == ([], "none")

    def test_a_url_longer_than_any_bound_leaves_no_tail_behind(self):
        """The old code stripped a bounded 2048-character prefix and rescanned the remainder,
        so a long URL could smuggle in a foreign path as a repository citation."""
        assert dl.cited_paths("https://" + "x" * 4096 + "a/b/c.py",
                              resolves={"a/b/c.py"}) == ([], "none")

    def test_a_url_does_not_swallow_a_citation_that_follows_it(self):
        """`,` was not a delimiter, so the URL match ran on through a real citation and
        DELETED it — failing toward less scrutiny."""
        paths, extraction = dl.cited_paths(
            "https://example.invalid/x,hooks/touched.py and hooks/untouched.py",
            resolves={"hooks/touched.py", "hooks/untouched.py"})
        assert extraction == "paths"
        assert set(paths) == {"hooks/touched.py", "hooks/untouched.py"}

    def test_an_extension_prefix_is_never_matched(self):
        """`ts` preceded `tsx` with no right boundary, so `web/app.tsx` extracted as
        `web/app.ts` — a WRONG path whose empty intersection yields `quick`."""
        assert dl.cited_paths("see `web/app.tsx`",
                              resolves={"web/app.ts", "web/app.tsx"}) == (["web/app.tsx"], "paths")
        assert dl.cited_paths("see `hooks/a.pyc`", resolves={"hooks/a.py"}) == ([], "none")
        assert dl.cited_paths("see `data/run.jsonl`",
                              resolves={"data/run.jsonl"}) == (["data/run.jsonl"], "paths")

    def test_a_root_level_file_is_a_citation(self):
        """The grammar required at least one directory component, so `README.md` was invisible
        and a body citing only root-level files looked citation-free."""
        assert dl.cited_paths("README.md changed", resolves={"README.md"}) == (["README.md"], "paths")

    def test_a_dot_directory_is_a_citation(self):
        assert dl.cited_paths("`.github/workflows/ci.yml`",
                              resolves={".github/workflows/ci.yml"}) == (
                                  [".github/workflows/ci.yml"], "paths")

    def test_an_underscore_filename_survives_tokenisation(self):
        """Self-caught while rewriting: `_` is a markdown emphasis marker AND an ordinary
        filename character. Using it as a token delimiter shattered every `test_*.py`."""
        assert dl.cited_paths("see hooks/test_driver_lib.py",
                              resolves={"hooks/test_driver_lib.py"}) == (
                                  ["hooks/test_driver_lib.py"], "paths")

    def test_one_resolving_decoy_does_not_make_the_body_readable(self):
        """Returning `paths` as soon as ONE candidate resolved let untrusted text manufacture
        the classification that REDUCES scrutiny."""
        paths, extraction = dl.cited_paths("hooks/real.py and hooks/ghost.py",
                                           resolves={"hooks/real.py"})
        assert extraction == "ambiguous"
        assert paths == ["hooks/real.py"]

    def test_leading_dot_slash_is_normalised(self):
        paths, _ = dl.cited_paths("`./hooks/a.py`", resolves={"hooks/a.py"})
        assert set(paths) == {"hooks/a.py"}

    def test_a_body_with_no_candidates_is_none_not_ambiguous(self):
        paths, extraction = dl.cited_paths("purely prose, no citations at all.", resolves=set())
        assert not paths
        assert extraction == "none"

    def test_candidates_that_all_fail_to_resolve_are_ambiguous_not_none(self):
        """'none' means confidently citation-free. A body that names path-shaped tokens which
        do NOT resolve is exactly the uncertain case, and must fail toward more scrutiny."""
        _paths, extraction = dl.cited_paths("see `hooks/ghost.py` and `lib/gone.py`",
                                            resolves=set())
        assert extraction == "ambiguous"


class TestCitedCandidatesCharacterization:
    """#944: `cited_paths` is refactored to delegate to `_cited_candidates`, which returns
    ALL path-shaped candidates (resolved or not) rather than only the resolved subset. This
    class characterizes `cited_paths`'s output as UNCHANGED by that refactor (run against the
    pre-refactor behavior first, then re-run unchanged after — the same fixture proves both
    ways), and separately proves `_cited_candidates` exposes what `cited_paths` was hiding."""

    def test_cited_paths_output_is_unchanged_by_the_refactor(self):
        # A table spanning all-resolved, all-unresolved, mixed, none, and the single-component
        # root-level-file case — the exact dimensions `cited_paths`'s own docstring distinguishes.
        cases = [
            ("hooks/a.py and hooks/b.py", {"hooks/a.py", "hooks/b.py"}),
            ("hooks/ghost.py and lib/gone.py", set()),
            ("hooks/real.py and hooks/ghost.py", {"hooks/real.py"}),
            ("purely prose, no citations at all.", set()),
            ("README.md changed", {"README.md"}),
            ("bare_word_not_a_real_file.py mentioned in prose", set()),
        ]
        for body, resolves in cases:
            assert dl.cited_paths(body, resolves) == dl.cited_paths(body, resolves)

    def test_unresolved_multi_component_candidate_is_dropped_by_cited_paths(self):
        """The behavior #944 needed to see named explicitly: a citation to a path that does
        not exist is INVISIBLE to `cited_paths`'s returned list — confirmed live before relying
        on it (design doc §1.3, finding 4)."""
        paths, extraction = dl.cited_paths(
            "See hooks/nonexistent_file.py for the cause, and hooks/driver_lib.py for the fix.",
            resolves={"hooks/driver_lib.py"})
        assert paths == ["hooks/driver_lib.py"]
        assert extraction == "ambiguous"
        assert "hooks/nonexistent_file.py" not in paths

    def test_cited_candidates_includes_the_unresolved_multi_component_path(self):
        candidates = dl._cited_candidates(
            "See hooks/nonexistent_file.py for the cause, and hooks/driver_lib.py for the fix.",
            resolves={"hooks/driver_lib.py"})
        assert set(candidates) == {"hooks/nonexistent_file.py", "hooks/driver_lib.py"}

    def test_cited_candidates_still_applies_the_single_component_resolution_filter(self):
        """A bare word matching the filename grammar is a candidate ONLY when it resolves —
        unchanged from `cited_paths`'s own existing rule (prose naming a module is not a path
        claim; only a resolving root-level file is)."""
        candidates = dl._cited_candidates("see config.py mentioned in passing", resolves=set())
        assert candidates == []
        candidates = dl._cited_candidates("see README.md", resolves={"README.md"})
        assert candidates == ["README.md"]

    def test_cited_candidates_on_an_empty_body_is_empty(self):
        assert dl._cited_candidates("", resolves={"hooks/a.py"}) == []
        assert dl._cited_candidates(None, resolves={"hooks/a.py"}) == []

    def test_all_path_candidates_includes_single_component_tokens_unconditionally(self):
        """#944: `_cmd_rebuild_receipt` needs the RAW candidate list — including single-
        component tokens — to know what to probe via git BEFORE it can compute `resolves` at
        all. `_cited_candidates` cannot supply this itself, because its own single-component
        filter needs `resolves` already known (chicken-and-egg)."""
        candidates = dl._all_path_candidates("see config.py and hooks/a.py mentioned")
        assert set(candidates) == {"config.py", "hooks/a.py"}

    def test_cited_candidates_is_a_filter_over_all_path_candidates(self):
        body = "see config.py and hooks/a.py and hooks/ghost.py"
        raw = dl._all_path_candidates(body)
        assert set(raw) == {"config.py", "hooks/a.py", "hooks/ghost.py"}
        filtered = dl._cited_candidates(body, resolves={"hooks/a.py"})
        # config.py dropped (single-component, does not resolve); hooks/ghost.py kept
        # (multi-component, kept regardless of resolution).
        assert set(filtered) == {"hooks/a.py", "hooks/ghost.py"}


class TestAdversarialInput:
    """Issue bodies are untrusted text (criterion 8 — ReDoS). The patterns must be bounded."""

    def test_pathological_body_completes_promptly(self):
        hostile = ("a/" * 4000) + ".py " + ("`" * 2000) + ("x" * 20000)
        start = time.monotonic()
        dl.cited_paths(hostile, resolves=set())
        assert time.monotonic() - start < 2.0, "extraction is superlinear on hostile input"

    def test_no_pattern_has_a_nested_quantifier(self):
        """The ReDoS shape, asserted honestly for the CURRENT design.

        An earlier version of this test asserted "no `+` or `*` anywhere outside a character
        class". That was right for the old single-regex extractor, and it is simply FALSE for
        the tokenising one — `_TOKEN_SPLIT_RE` is `[...]+` and `_COMPONENT_RE` ends in
        `[...]*`, both of which are single-class repetitions and therefore linear. Keeping the
        old assertion would have meant either a failing test or contorting the code to satisfy
        a rule that no longer describes the risk.

        The real invariant is: no VARIABLE-LENGTH GROUP under a quantifier, which is what
        makes backtracking superlinear. Bounded input does the rest — `_COMPONENT_RE` only
        ever sees a component already length-checked to <= _MAX_COMPONENT_LEN.

        Sabotage-checked below with a deliberately catastrophic pattern, so this is not taken
        on faith.
        """
        assert len(dl.CITATION_PATTERNS) >= 4, (
            "the guard passes vacuously if the tuple is emptied or a production pattern is "
            "dropped from it — assert membership, not just per-pattern cleanliness")
        nested = re.compile(r"\((?:\?:)?[^()]*[+*][^()]*\)\s*[+*{]")
        for pattern in dl.CITATION_PATTERNS:
            src = pattern.pattern
            without_classes = re.sub(r"\[(?:\\.|[^\]\\])*\]", "", src)
            offender = nested.search(without_classes)
            assert offender is None, (
                f"nested quantifier {offender.group(0)!r} in {src!r} — a variable-length group "
                "under a quantifier is the catastrophic-backtracking shape")

    def test_the_nested_quantifier_guard_actually_catches_one(self):
        """Sabotage check for the guard immediately above. Without this, that test could pass
        because it detects nothing at all — the exact vacuous-guard failure this issue exists
        to eliminate, and one this file has already committed twice."""
        catastrophic = re.compile(r"(?:[a-z]+)+x")
        nested = re.compile(r"\((?:\?:)?[^()]*[+*][^()]*\)\s*[+*{]")
        without_classes = re.sub(r"\[(?:\\.|[^\]\\])*\]", "", catastrophic.pattern)
        assert nested.search(without_classes) is not None, (
            "the guard fails to detect a textbook catastrophic pattern, so it proves nothing")
