"""#196 — the post-PR code-review CI lane (the #162 candidate `builtin_code_review`
arm). Runs the built-in `/code-review` through claude-code-action@v1, OAuth-first,
draft-gated, additive to WF2's hand-rolled Step 11 (coverage never drops)."""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WF = REPO_ROOT / ".github" / "workflows" / "claude-code-review.yml"


def _text():
    return WF.read_text()


def test_lane_exists_and_triggers_on_pull_request():
    assert WF.exists(), "#196: the post-PR code-review lane must exist"
    assert re.search(r"^on:\n  pull_request:", _text(), re.M)


def test_draft_gated():
    """AC1: skip draft PRs — review code proposed for merge, not every WIP push."""
    assert "github.event.pull_request.draft == false" in _text()


def test_ready_for_review_is_a_trigger_type():
    """Step-11 F1: the `draft == false` job gate needs `ready_for_review` in the
    trigger types, else the draft→ready transition never fires the lane (WF2
    drafts/un-drafts PRs on suspend/resume)."""
    assert "ready_for_review" in _text()
    assert "types:" in _text()


def test_runs_builtin_code_review_via_sha_pinned_action():
    """AC1: /code-review through claude-code-action@v1, SHA-pinned."""
    uses = re.findall(r"uses:\s*anthropics/claude-code-action@(\S+)", _text())
    assert uses, "must run through claude-code-action"
    for u in uses:
        assert re.fullmatch(r"[0-9a-f]{40}", u), f"must be SHA-pinned, got @{u}"
    assert _text().count('prompt: "/code-review"') == 2  # oauth + apikey


def test_oauth_first_auth_and_visible_skip():
    """Shares the #195 OAuth-first resolver: OAuth → API key → visible skip."""
    text = _text()
    assert text.index("OAUTH") < text.index("APIKEY")
    assert "claude_code_oauth_token: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}" in text
    assert "anthropic_api_key: ${{ secrets.ANTHROPIC_API_KEY }}" in text
    assert "executed=false" in text and "::warning::" in text


def test_records_reviewer_kind_builtin_code_review():
    """AC2: the lane's review is the builtin_code_review arm for the #162 A/B."""
    assert "builtin_code_review" in _text()


def test_non_blocking_least_privilege_and_idtoken():
    text = _text()
    # #233: advisory is now a NON-required check + honest-RED, NOT a
    # continue-on-error mask (which hid a failed/errored review as green).
    assert "continue-on-error: true" not in text
    assert re.search(r"^\s+exit 1$", text, re.M), "no-auth must fail RED, not exit 0 green"
    blocks = re.findall(r"^permissions:\n((?:^[ \t]+\S.*\n)+)", text, re.M)
    assert len(blocks) == 1
    perms = {ln.strip() for ln in blocks[0].splitlines()}
    assert perms == {"pull-requests: write", "contents: read", "id-token: write"}


def test_secrets_by_name_never_inline():
    text = _text()
    assert "sk-ant-" not in text and "oat01" not in text


def test_162_decision_reopened_with_computable_status():
    """AC3: the #162 decision record is reopened — mechanism in place, comparison
    computable, decision deferred pending owner-gated data (not a fake decision)."""
    doc = (REPO_ROOT / "docs" / "measurements"
           / "2026-07-05-issue-162-data-gate-decision.md").read_text()
    assert "REOPENED at #196" in doc
    assert "computable" in doc.lower()
    # the original ABANDONED record is preserved (not overwritten)
    assert "abandon" in doc.lower() and "not a rejection" in doc.lower()
    # the built-in arm runs ADDITIVE to hand-rolled (coverage never drops)
    low = doc.lower()
    assert "additional" in low and "never drops" in low
