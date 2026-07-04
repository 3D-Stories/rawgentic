"""Drift guards for the claude-code-security-review CI lane (#166).

The Action is a CI-side semantic security review on PRs — a complement to the
local Step 11.5 scanners, never a replacement. These tests pin the properties
that make the lane safe to run non-blocking with a third-party action:
SHA-pinning (supply chain), least-privilege permissions, and the
non-blocking contract (AC1: advisory until promoted after 10 clean PRs).

Text-based pins, not YAML parsing: the CI env installs only pytest+jsonschema,
and the workflow file is small and authored in this repo, so exact-line pins
are the cheaper drift guard.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "claude-security-review.yml"


def _text():
    return WORKFLOW.read_text()


def test_workflow_triggers_on_pull_request():
    assert re.search(r"^on:\n  pull_request:", _text(), re.M)


def test_action_is_sha_pinned_not_ref_pinned():
    uses = re.findall(r"uses:\s*anthropics/claude-code-security-review@(\S+)", _text())
    assert len(uses) == 1, "exactly one review-action step"
    assert re.fullmatch(r"[0-9a-f]{40}", uses[0]), (
        f"third-party action with API-key access must be SHA-pinned, got @{uses[0]}"
    )


def test_permissions_are_least_privilege():
    text = _text()
    assert "pull-requests: write" in text
    assert "contents: read" in text
    # nothing broader
    for excessive in ("contents: write", "id-token", "actions: write", "packages:"):
        assert excessive not in text, f"least-privilege violated: {excessive}"


def test_lane_is_non_blocking_initially():
    """AC1: advisory lane — a red review (or missing secret) must not gate the PR.

    Pinned at JOB scope (4-space indent): step-level continue-on-error would
    change the semantics (a failed later step could still fail the job).
    """
    assert re.search(r"^    continue-on-error: true$", _text(), re.M)


def test_missing_secret_is_legible_never_a_fake_clean_signal():
    """A skipped review must be visibly distinct from a clean one — the 10-PR
    promotion tally is measured off this signal."""
    text = _text()
    assert "::warning::" in text
    assert text.count("if: steps.key.outputs.present == 'true'") == 2, (
        "checkout AND review step must both gate on the key being present"
    )


def test_api_key_comes_from_secrets_never_inline():
    assert "claude-api-key: ${{ secrets.CLAUDE_API_KEY }}" in _text()
    assert not re.search(r"sk-ant-", _text()), "no inline key material"


def test_local_step_11_5_scanners_untouched():
    """AC2: the lane complements hooks/security_scan.py; it must not touch it."""
    scan = (REPO_ROOT / "hooks" / "security_scan.py").read_text()
    assert "claude-code-security-review" not in scan
    ci = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text()
    assert "claude-code-security-review" not in ci, "lane lives in its own workflow file"


def test_promotion_contract_documented_in_workflow():
    text = _text()
    assert "non-blocking" in text
    assert "10 clean" in text, "promotion-to-required contract must be stated in the file"
