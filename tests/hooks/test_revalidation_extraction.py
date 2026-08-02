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
    838: {"expected": "paths",
          "resolves": {"hooks/executor_routing_lib.py",
                       "phase_executor/src/phase_executor/supervisor.py"}},
    734: {"expected": "paths",
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

    def test_a_candidate_that_does_not_resolve_is_not_a_citation(self):
        """#734 names three path-shaped tokens; only two exist on main. The third must be
        dropped, because an unresolvable token is prose that merely looks like a path."""
        paths, extraction = dl.cited_paths(_body(734), resolves=MEASURED[734]["resolves"])
        assert extraction == "paths"
        assert set(paths) == MEASURED[734]["resolves"]
        assert len(paths) == 2


class TestExtractionEdgeCases:
    def test_absolute_paths_are_rejected(self):
        paths, _ = dl.cited_paths("see `/etc/passwd` and `/home/u/x.py`",
                                  resolves={"/etc/passwd", "/home/u/x.py"})
        assert not paths

    def test_traversal_is_rejected(self):
        paths, _ = dl.cited_paths("see `../../secrets/key.py`",
                                  resolves={"../../secrets/key.py"})
        assert not paths

    def test_line_and_range_suffixes_are_stripped(self):
        paths, _ = dl.cited_paths("`hooks/a.py:84-85` and `hooks/b.py:12`",
                                  resolves={"hooks/a.py", "hooks/b.py"})
        assert set(paths) == {"hooks/a.py", "hooks/b.py"}

    def test_github_line_anchor_forms_are_recognised(self):
        paths, _ = dl.cited_paths("`hooks/a.py#L84` and `hooks/b.py@12`",
                                  resolves={"hooks/a.py", "hooks/b.py"})
        assert set(paths) == {"hooks/a.py", "hooks/b.py"}

    def test_urls_are_not_treated_as_repository_paths(self):
        paths, _ = dl.cited_paths("https://example.com/a/b/c.py is not ours",
                                  resolves={"a/b/c.py"})
        assert not paths

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


class TestAdversarialInput:
    """Issue bodies are untrusted text (criterion 8 — ReDoS). The patterns must be bounded."""

    def test_pathological_body_completes_promptly(self):
        hostile = ("a/" * 4000) + ".py " + ("`" * 2000) + ("x" * 20000)
        start = time.monotonic()
        dl.cited_paths(hostile, resolves=set())
        assert time.monotonic() - start < 2.0, "extraction is superlinear on hostile input"

    def test_the_patterns_contain_no_unbounded_quantifier_at_all(self):
        """Structural guard for the property the module's own comment claims.

        A weaker version of this test (looking only for a nested quantifier over a group)
        was written first and would have passed VACUOUSLY against bounded quantifiers, so
        it proved nothing about a future edit. The real invariant is stronger and is what
        makes the ReDoS argument sound: no `+` or `*` outside a character class anywhere,
        so every match attempt does O(1) work per starting position.

        Character classes are stripped first — `[A-Za-z0-9_.-]` contains a literal `-` and
        `[^\\s)\\]>`"']` contains an escaped `]`, and neither is a quantifier.
        """
        for pattern in dl.CITATION_PATTERNS:
            src = pattern.pattern
            without_classes = re.sub(r"\[(?:\\.|[^\]\\])*\]", "", src)
            offender = re.search(r"(?<!\\)[+*]", without_classes)
            assert offender is None, (
                f"unbounded quantifier {offender.group(0)!r} at index {offender.start()} "
                f"of {without_classes!r} (from {src!r}) — bound it, or the ReDoS claim in "
                "driver_lib's citation-extraction comment is false")
