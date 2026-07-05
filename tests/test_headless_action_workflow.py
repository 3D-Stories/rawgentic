"""#165: the label-triggered headless Action workflow (.github/workflows/
rawgentic-auto.yml) — pins the yml against the claude-code-action@v1 contract
verified from the action's own action.yml (plugins/plugin_marketplaces inputs)
and against #165's security ACs (secret by NAME, label gate, timeout).

Parsed with PyYAML: structural assertions beat regex here because the security
properties (no inline secret value, gate expression exact) live at specific
keys, not anywhere-in-file.
"""
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
WF_PATH = REPO_ROOT / ".github" / "workflows" / "rawgentic-auto.yml"


def _wf():
    return yaml.safe_load(WF_PATH.read_text())


def _job(wf):
    jobs = wf["jobs"]
    assert len(jobs) == 1, "one job expected"
    return next(iter(jobs.values()))


def _action_step(job):
    steps = [s for s in job["steps"]
             if "uses" in s and s["uses"].startswith("anthropics/claude-code-action@")]
    assert len(steps) == 1, "exactly one claude-code-action step expected"
    return steps[0]


def test_workflow_file_exists():
    assert WF_PATH.exists(), "AC1: .github/workflows/rawgentic-auto.yml must ship"


def test_triggers_on_issue_labeled_only():
    wf = _wf()
    # yaml parses the `on:` key as boolean True
    on = wf.get("on", wf.get(True))
    assert list(on.keys()) == ["issues"], "only issue events may trigger"
    assert on["issues"]["types"] == ["labeled"]


def test_job_gated_on_rawgentic_auto_label():
    """AC1: the label gate lives at the JOB level so no runner spins up for
    other labels."""
    job = _job(_wf())
    assert "github.event.label.name == 'rawgentic:auto'" in job["if"]


def test_job_timeout_bounded():
    """AC4/#52: GitHub-native timeout is the hard guardrail."""
    job = _job(_wf())
    assert isinstance(job["timeout-minutes"], int)
    assert 0 < job["timeout-minutes"] <= 180


def test_uses_v1_major_tag():
    step = _action_step(_job(_wf()))
    assert step["uses"] == "anthropics/claude-code-action@v1"


def test_oauth_secret_referenced_by_name_never_value():
    """AC7/AC8: subscription OAuth via the CLAUDE_CODE_OAUTH_TOKEN repo
    secret — referenced only as a secrets expression."""
    step = _action_step(_job(_wf()))
    assert step["with"]["claude_code_oauth_token"] == \
        "${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}"
    raw = WF_PATH.read_text()
    # No credential-shaped literal anywhere (OAuth tokens / API keys)
    assert "sk-ant-" not in raw
    assert "oat01" not in raw


def test_prompt_targets_implement_feature_with_issue_number():
    step = _action_step(_job(_wf()))
    prompt = step["with"]["prompt"]
    assert "/rawgentic:implement-feature ${{ github.event.issue.number }}" in prompt


def test_prompt_carries_goal_guard_preamble():
    """AC6 (honest form): the Action cannot type /goal (session-level), so the
    prompt itself must instruct AC-derived goal discipline."""
    step = _action_step(_job(_wf()))
    prompt = step["with"]["prompt"]
    assert "acceptance criteria" in prompt.lower()


def test_plugin_loaded_from_repo_marketplace():
    """Verified contract from action.yml: plugins + plugin_marketplaces."""
    step = _action_step(_job(_wf()))
    assert "rawgentic@rawgentic" in step["with"]["plugins"]
    assert "https://github.com/3D-Stories/rawgentic" in \
        step["with"]["plugin_marketplaces"]


def test_headless_env_reaches_claude_via_settings():
    """Env passing contract: settings JSON `env` (claude_env is deprecated)."""
    import json
    step = _action_step(_job(_wf()))
    settings = json.loads(step["with"]["settings"])
    assert settings["env"]["RAWGENTIC_HEADLESS"] == "1"
    assert settings["env"]["RAWGENTIC_HEADLESS_TRIGGER"] == "issue-label"


def test_bootstrap_step_writes_runner_workspace():
    """Design call 10: WF2 config-loading STOPs without a workspace file at the
    Claude root; the checkout is the PROJECT repo, so a pre-step must write a
    runner-local .rawgentic_workspace.json with the object-shape headless
    grant."""
    job = _job(_wf())
    action_idx = next(i for i, s in enumerate(job["steps"])
                      if "uses" in s and "claude-code-action" in s["uses"])
    run_bodies = [s.get("run", "") for s in job["steps"][:action_idx]]
    bootstrap = "\n".join(run_bodies)
    assert ".rawgentic_workspace.json" in bootstrap
    assert '"triggers": ["issue-label"]' in bootstrap
    assert '"enabled": true' in bootstrap


def test_permissions_least_privilege():
    """Only the three scopes the run needs; anything else is scope creep on an
    autonomous token."""
    wf = _wf()
    perms = wf["permissions"]
    assert perms == {"contents": "write",
                     "pull-requests": "write",
                     "issues": "write"}
