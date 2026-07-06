"""Drift guards for the GitHub Action review lanes' honest-signal contract +
activation UX (issue #233).

The lanes (#195 security, #196 code) previously showed a green check even when no
auth secret was present and nothing was reviewed — a misleading "reviewed" signal.
AC1: a skipped/failed review must NOT read as a green check. This repo's chosen
mechanism is advisory-RED: drop the job-level `continue-on-error` mask, and make the
no-auth path exit non-zero so the (non-required, advisory) check goes red instead of
green. AC2/AC3: setup + a docs guide tell users how to activate the lanes.
"""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WF = REPO_ROOT / ".github" / "workflows"
LANES = ["claude-security-review.yml", "claude-code-review.yml"]


def test_lanes_drop_continue_on_error_mask():
    # AC1: continue-on-error:true made the job green even when the review step
    # failed — an action error/rate-limit must not read as "reviewed".
    for f in LANES:
        y = (WF / f).read_text()
        assert "continue-on-error: true" not in y, (
            f"{f}: advisory-red means no continue-on-error masking (#233 AC1)")


def test_lanes_no_auth_goes_red_not_green():
    # AC1: the no-auth branch must fail the job (red), not exit 0 (green ✓).
    for f in LANES:
        y = (WF / f).read_text()
        assert "::error::" in y, f"{f}: no-auth must emit ::error::, not a green ::warning::"
        assert "exit 1" in y, f"{f}: no-auth branch must exit non-zero so the check is red"


def test_lanes_document_advisory_nonblocking():
    # AC1: red must stay ADVISORY (non-required) — the contract must say so, so a
    # red lane is understood as "not reviewed", never as a merge blocker by default.
    for f in LANES:
        y = (WF / f).read_text().lower()
        # parenthesized: BOTH "advisory" AND some non-blocking phrasing must be present
        # (the un-parenthesized form let a future edit drop "advisory" and still pass).
        assert ("advisory" in y) and ("non-blocking" in y or "does not block" in y), \
            f"{f}: contract must say advisory + non-blocking"


def test_ci_review_lanes_doc_exists_and_covers_activation():
    # AC3: an activation guide (token -> org secret -> verify).
    doc = REPO_ROOT / "docs" / "ci-review-lanes.md"
    assert doc.exists(), "docs/ci-review-lanes.md (activation guide) missing"
    t = doc.read_text().lower()
    assert "claude setup-token" in t
    assert "gh secret set" in t and "--org" in t          # org-wide activation
    assert "anthropic_api_key" in t                        # fallback documented
    assert "verify" in t or "executed=true" in t           # how to confirm it ran


def test_setup_points_to_review_lane_activation():
    # AC2: setup tells the user the lanes exist + how to activate them.
    import sys
    sys.path.insert(0, str(REPO_ROOT / "tests"))
    from corpus import skill_corpus
    corpus = skill_corpus("setup").lower()
    assert "ci-review-lanes" in corpus or (
        "review lane" in corpus and "setup-token" in corpus), \
        "setup must point to the review-lane activation guide (#233 AC2)"
