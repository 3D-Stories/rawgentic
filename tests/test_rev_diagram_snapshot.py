"""#535 — skills/rev-diagram/scripts/snapshot.sh drift guard.

The script needs a real browser + network to actually run (CI installs neither
per this repo's CI lanes), so this is a static, mechanical guard over the
committed script's hard-coded invariants — mirrors this repo's established
pattern for a recipe that can't execute live in CI. It does NOT prove the
script works (that's a required LIVE run at Step 8/9, per the design's own
error-handling section); it proves the invariants that make a future edit
safe to land: the pinned Playwright invocation, the light/dark URL-to-output
mapping, and that the diagram pytest gate runs last and controls exit status.
"""

import stat
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "skills" / "rev-diagram" / "scripts" / "snapshot.sh"


def _source():
    return SCRIPT.read_text()


def test_script_exists_and_executable():
    assert SCRIPT.exists(), "skills/rev-diagram/scripts/snapshot.sh must exist"
    mode = SCRIPT.stat().st_mode
    assert mode & stat.S_IXUSR, "snapshot.sh must be executable (chmod +x)"


def test_script_is_syntax_valid_bash():
    result = subprocess.run(
        ["bash", "-n", str(SCRIPT)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, f"bash -n failed: {result.stderr}"


def test_pinned_playwright_version_used_everywhere():
    """#535 Step-4 review Finding #1: an unversioned `npx playwright` could
    silently resolve a different version than the one actually spiked."""
    text = _source()
    assert "playwright@1.61.1" in text
    # the bare unversioned form must never appear as an invocation prefix
    assert "npx playwright screenshot" not in text
    assert "npx playwright install" not in text


def test_no_unverified_device_scale_flag():
    """#535 Step-4 review Finding #1 (confirmed live): 1.61.1's screenshot
    CLI has no --device-scale-factor option at all."""
    assert "device-scale-factor" not in _source()


def test_full_page_and_viewport_hardcoded():
    text = _source()
    assert "--full-page" in text
    assert "1440,900" in text or "1440" in text


def test_light_dark_url_and_output_mapping():
    """Not bare token presence — each theme's call site must map ITS OWN theme
    value to ITS OWN exact output path (#535 Step-4 review Finding #2). The
    script uses a shared `capture()` function (theme + output as call-site
    args, not string-templated per theme), so the mapping evidence is the two
    call-site lines, not a literal `?theme=light` substring."""
    text = _source()
    assert "?theme=${theme}" in text or "?theme=$theme" in text, (
        "expected a parameterized theme query build in the capture helper"
    )
    assert "docs/assets/workflow-diagram-light.png" in text
    assert "docs/assets/workflow-diagram-dark.png" in text
    light_call = "capture light docs/assets/workflow-diagram-light.png"
    dark_call = "capture dark docs/assets/workflow-diagram-dark.png"
    assert light_call in text, f"expected exact call site: {light_call!r}"
    assert dark_call in text, f"expected exact call site: {dark_call!r}"


def test_diagram_pytest_gate_runs_last_and_controls_exit():
    text = _source()
    assert "pytest tests/test_workflow_diagram.py -q" in text
    gate_idx = text.index("pytest tests/test_workflow_diagram.py -q")
    # both captures must precede the gate (gate runs LAST)
    assert text.index("workflow-diagram-light.png") < gate_idx
    assert text.index("workflow-diagram-dark.png") < gate_idx
    # the script must reference the gate's own exit code, not swallow it
    assert "GATE_RC" in text or "exit $?" in text


def test_preflight_reports_missing_playwright_clearly():
    text = _source()
    assert "install chromium" in text


def test_exit_trap_cleans_up_server_and_temp_state():
    text = _source()
    assert "trap" in text and "EXIT" in text
