"""Drift guards for the pylint lint workflow (#40).

The lint job runs on standard actions (no OAuth) and gates the production Python
(hooks + tests) on the ERROR class so a hook bug can't ship silently. These assert
the workflow's shape; the gate itself running green on the current tree is verified
in the PR (pylint --errors-only is clean at authoring time).
"""
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
LINT_YML = REPO_ROOT / ".github" / "workflows" / "lint.yml"


def _load():
    return yaml.safe_load(LINT_YML.read_text())


def test_lint_workflow_exists_and_parses():
    assert LINT_YML.exists(), "AC: .github/workflows/lint.yml must exist"
    assert _load() is not None


def test_triggers_on_push_and_pr_to_main():
    wf = _load()
    # PyYAML parses the bare `on:` key as the boolean True
    on = wf.get("on") if "on" in wf else wf.get(True)
    assert "main" in on["push"]["branches"]
    assert "main" in on["pull_request"]["branches"]


def test_single_python_312_no_matrix():
    wf = _load()
    steps = wf["jobs"]["lint"]["steps"]
    setup = [s for s in steps if "setup-python" in str(s.get("uses", ""))]
    assert setup, "must set up Python"
    assert str(setup[0]["with"]["python-version"]) == "3.12"
    # no matrix strategy
    assert "strategy" not in wf["jobs"]["lint"]


def test_lints_hooks_and_tests():
    run_steps = " ".join(
        s.get("run", "") for s in _load()["jobs"]["lint"]["steps"]
    )
    assert "pylint hooks/*.py" in run_steps
    assert "pylint tests/" in run_steps


def test_disables_import_error_and_scopes_to_error_class():
    # import-error disabled (hook deps not installed in CI) AND scoped to the ERROR
    # class so the gate passes on the current tree instead of drowning in style noise.
    run_steps = " ".join(
        s.get("run", "") for s in _load()["jobs"]["lint"]["steps"]
    )
    assert "--disable=import-error" in run_steps
    assert "--errors-only" in run_steps


def test_pip_installs_pylint():
    run_steps = " ".join(
        s.get("run", "") for s in _load()["jobs"]["lint"]["steps"]
    )
    assert "pip install pylint" in run_steps
