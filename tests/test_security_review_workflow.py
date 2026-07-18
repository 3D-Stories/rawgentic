"""Drift guards for the security-review CI lane (#166, migrated to
anthropics/claude-code-action@v1 with OAuth-first auth at #195).

The lane is a CI-side semantic security review on PRs — a complement to the local
Step 11.5 scanners, never a replacement. These tests pin the properties that keep
it safe to run non-blocking with a third-party action that receives an auth token:
SHA-pinning (supply chain), least-privilege permissions, the non-blocking
contract (advisory until promoted after 10 clean PRs), the OAuth-first auth
priority (#195), and the visible-skip signal (a missing secret is never a clean
pass — the #166 pattern).

Text-based pins, not YAML parsing: the workflow is small and authored here, so
exact-line pins are the cheaper drift guard.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "claude-security-review.yml"


def _text():
    return WORKFLOW.read_text()


def test_workflow_triggers_on_pull_request():
    assert re.search(r"^on:\n  pull_request:", _text(), re.M)


def test_migrated_off_api_key_only_action():
    """#195 AC3: no `uses:` step invokes the API-key-only
    claude-code-security-review action (a comment may still name it to explain
    the migration)."""
    assert not re.search(r"uses:\s*anthropics/claude-code-security-review", _text())


def test_review_action_is_sha_pinned():
    """#195 AC2: runs through claude-code-action, SHA-pinned (it receives an auth
    token + write GITHUB_TOKEN — a mutable @v1 tag is a supply-chain vector)."""
    uses = re.findall(r"uses:\s*anthropics/claude-code-action@(\S+)", _text())
    assert uses, "the review must run through claude-code-action"
    for u in uses:
        assert re.fullmatch(r"[0-9a-f]{40}", u), f"must be SHA-pinned, got @{u}"
    assert "# v1" in _text()


def test_oauth_first_then_api_key_priority():
    """#195 AC1: CLAUDE_CODE_OAUTH_TOKEN is resolved before ANTHROPIC_API_KEY."""
    text = _text()
    assert text.index("OAUTH") < text.index("APIKEY")
    # the two action steps gate on the resolved mode
    assert "mode == 'oauth'" in text and "mode == 'apikey'" in text
    assert "claude_code_oauth_token: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}" in text
    assert "anthropic_api_key: ${{ secrets.ANTHROPIC_API_KEY }}" in text


def test_prompt_runs_security_review():
    assert _text().count('prompt: "/security-review"') == 2  # oauth + apikey steps


def test_permissions_are_least_privilege():
    text = _text()
    blocks = re.findall(r"^permissions:\n((?:^[ \t]+(?:\S.*|#.*)\n)+)", text, re.M)
    assert len(blocks) == 1, "exactly one permissions block (workflow level)"
    perms = {ln.strip() for ln in blocks[0].splitlines() if not ln.strip().startswith("#")}
    # id-token: write is required for claude-code-action's default App-token OIDC path
    assert perms == {"pull-requests: write", "contents: read", "id-token: write"}
    assert "write-all" not in text


def test_execution_status_is_durable_for_the_promotion_tally():
    text = _text()
    assert "executed=false" in text and "executed=true" in text
    assert "GITHUB_STEP_SUMMARY" in text


def test_lane_is_non_blocking_initially():
    # #233: advisory via a NON-required check + honest-RED skip, not a
    # continue-on-error mask. Green = actually reviewed; no-auth/error = red.
    text = _text()
    assert "continue-on-error: true" not in text
    assert re.search(r"^\s+exit 1$", text, re.M), "no-auth must fail RED, not exit 0 green"
    assert "advisory" in text.lower()


def test_no_auth_is_a_visible_skip():
    """A skipped review must be visibly distinct from a clean one (the tally
    is measured off this). All three action-adjacent steps gate on present=true."""
    text = _text()
    assert "::warning::" in text
    assert "present=false" in text
    assert text.count("steps.auth.outputs.present == 'true'") == 4, (
        "checkout + both auth-mode review steps + the status step must gate on "
        "an auth secret present"
    )


def test_executed_true_gated_on_review_success_not_secret_presence():
    """#195 Step-11 F2: the tally signal executed=true is written only when a
    review step actually SUCCEEDED, not merely because an auth secret was set
    (a plan lockout / outage / bad token must not read as a completed review)."""
    text = _text()
    assert "steps.review_oauth.outcome" in text and "steps.review_apikey.outcome" in text
    # executed=true lives in the outcome-gated status step, not the auth pre-flight
    auth_block = text.split("id: auth")[1].split("- name: Checkout")[0]
    assert "executed=true" not in auth_block, \
        "executed=true must NOT be emitted from the auth pre-flight (F2)"


def test_secrets_by_name_never_inline():
    text = _text()
    assert "sk-ant-" not in text and "oat01" not in text
    # no run: step echoes a secret
    for m in re.findall(r"run:\s*\|(.*?)(?=\n      -|\Z)", text, re.S):
        assert "secrets." not in m


def test_local_step_11_5_scanners_untouched():
    scan = (REPO_ROOT / "hooks" / "security_scan.py").read_text()
    assert "claude-code-action" not in scan and "claude-code-security-review" not in scan
    ci = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text()
    assert "claude-code-security-review" not in ci


def test_promotion_and_auth_setup_documented_in_workflow():
    text = _text()
    assert "non-blocking" in text
    assert "10 clean" in text
    assert "setup-token" in text  # owner-gated OAuth setup named
    assert "self-hosted" in text  # zero-secret alternative named (AC4)


def test_docs_only_prs_skipped_via_paths_ignore():
    """#478: same paths-ignore as the code-review lane — docs-only diffs skip
    this advisory lane; anything touching non-docs paths still runs it."""
    assert re.search(r"pull_request:\n(?:.*\n)*?    paths-ignore:\n      - \"docs/\*\*\"", _text())
