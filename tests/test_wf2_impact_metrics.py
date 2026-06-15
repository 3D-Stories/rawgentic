"""Tests for scripts/wf2_impact_metrics.py.

The metrics script computes the cheap, deterministic Tier-1 impact metrics for a
skill-extraction effort (test growth, fail-closed coverage, dedup, diff volume).
The pure shortstat parser is unit-tested here; the git-backed collectors get a
smoke test against the real (now-permanent) effort commit range.
"""
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))


def _ref_exists(ref: str) -> bool:
    """True if the git ref resolves to a commit. CI shallow checkouts
    (actions/checkout defaults to fetch-depth 1) may not have old SHAs."""
    return subprocess.run(
        ["git", "cat-file", "-e", f"{ref}^{{commit}}"],
        capture_output=True,
    ).returncode == 0


class TestParseShortstat:
    """`git diff --shortstat` output omits the insertions/deletions clause when
    that side is zero, and uses singular nouns for a count of 1 — the parser must
    handle every shape, returning 0 for an absent clause (not crash)."""

    @pytest.mark.parametrize("line,expected", [
        ("", {"files": 0, "insertions": 0, "deletions": 0}),
        (" 11 files changed, 267 insertions(+), 274 deletions(-)",
         {"files": 11, "insertions": 267, "deletions": 274}),
        (" 1 file changed, 2 insertions(+)",
         {"files": 1, "insertions": 2, "deletions": 0}),
        (" 1 file changed, 3 deletions(-)",
         {"files": 1, "insertions": 0, "deletions": 3}),
        (" 1 file changed, 1 insertion(+), 1 deletion(-)",
         {"files": 1, "insertions": 1, "deletions": 1}),
    ])
    def test_parses_all_shapes(self, line, expected):
        from wf2_impact_metrics import parse_shortstat
        assert parse_shortstat(line) == expected


class TestCollectSmoke:
    """Smoke test the git-backed collection over the real effort range. Asserts
    structural sanity (keys present, test count grew) rather than exact numbers,
    so it stays robust."""

    BASE = "fcd22b2"   # pre-#83 baseline
    HEAD = "86fbbf7"   # #89 merge

    def test_collect_returns_expected_shape(self):
        if not (_ref_exists(self.BASE) and _ref_exists(self.HEAD)):
            pytest.skip("effort commit range not present (shallow checkout)")
        from wf2_impact_metrics import collect_metrics
        m = collect_metrics(self.BASE, self.HEAD)
        for key in ("test_defs", "parametrize_blocks", "diff", "new_hooks_libs"):
            assert key in m, f"missing metric key {key}"
        # the effort added tests
        assert m["test_defs"]["head"] >= m["test_defs"]["baseline"]
        assert m["test_defs"]["head"] > 0
