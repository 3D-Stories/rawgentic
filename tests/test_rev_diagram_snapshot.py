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
    """Not bare token presence — each theme's OWN value must map through to its
    OWN exact output path, end to end (#535 Step-4 review Finding #2 + #535
    Step-11 review Finding #1's atomic-promote rewrite changed the chain to
    var-definition -> tmp-derivation -> capture call -> promotion rename;
    each link is checked so the mapping can't silently cross-wire)."""
    text = _source()
    assert "?theme=${theme}" in text or "?theme=$theme" in text, (
        "expected a parameterized theme query build in the capture helper"
    )
    assert 'LIGHT_OUT="$REPO_ROOT/docs/assets/workflow-diagram-light.png"' in text
    assert 'DARK_OUT="$REPO_ROOT/docs/assets/workflow-diagram-dark.png"' in text
    # tmp names must PRESERVE the .png extension (Playwright's screenshot CLI
    # infers output mime type from the extension; a bare random suffix broke
    # this live -- "unsupported mime type null" -- caught by the mandatory
    # live run, not a static check)
    assert 'LIGHT_TMP="$(mktemp "${LIGHT_OUT%.png}.XXXXXX.png")"' in text
    assert 'DARK_TMP="$(mktemp "${DARK_OUT%.png}.XXXXXX.png")"' in text
    assert 'capture light "$LIGHT_TMP"' in text
    assert 'capture dark "$DARK_TMP"' in text
    assert 'mv "$LIGHT_TMP" "$LIGHT_OUT"' in text
    assert 'mv "$DARK_TMP" "$DARK_OUT"' in text


def test_promotes_atomically_with_rollback_on_gate_failure():
    """#535 Step-11 review Finding #1 (confirmed live: the original script
    captured directly into the committed paths, so a failure after the first
    successful capture left it partially overwritten despite the script
    reporting rejection). The script must capture to temp files, back up the
    prior committed pair, promote via rename, and restore the backup if the
    post-promotion pytest gate fails."""
    text = _source()
    assert "LIGHT_BACKUP" in text and "DARK_BACKUP" in text
    assert "cp \"$LIGHT_OUT\" \"$LIGHT_BACKUP\"" in text
    assert "cp \"$DARK_OUT\" \"$DARK_BACKUP\"" in text
    gate_idx = text.index("pytest tests/test_workflow_diagram.py -q")
    restore_idx = text.index("cp \"$LIGHT_BACKUP\" \"$LIGHT_OUT\"")
    assert restore_idx > gate_idx, "restore-on-failure must come AFTER the gate runs"


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
