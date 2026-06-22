"""Tests for hooks/scanner_bootstrap.py — the reliable, detectable, self-healing
security-scanner bootstrap that session-start invokes on startup|resume.

The old bootstrap (a) never fired in production (the source/hook_event_name bug) and
(b) once it DID fire, wrote a permanent 0-byte marker that disabled all future
re-checks — so a removed scanner was never reinstalled, an update never re-triggered,
and a silent no-fire looked identical to "all clean". This module replaces that with:
  - re-check every eligible session (cheap `--check`),
  - background install of only what's missing,
  - a throttle so a persistently-failing install doesn't respawn every session,
  - an ALWAYS-written timestamped status file so a no-fire/failure is visible.

decide() is pure and unit-tested across every branch; main() is integration-tested
against a FAKE installer (no network, no real scanners touched).
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

import scanner_bootstrap as sb  # noqa: E402


# --------------------------------------------------------------------------
# decide() — pure decision function
# --------------------------------------------------------------------------

T0 = "2026-06-22T12:00:00Z"


def _status(last_attempt=None):
    s = {}
    if last_attempt is not None:
        s["last_install_attempt"] = last_attempt
    return s


class TestDecide:
    def test_headless_skips_first(self):
        # headless wins even with everything else also true
        assert sb.decide(optout_env=True, optout_ws=True, headless=True,
                         missing=["trivy"], status={}, now=T0, throttle_s=3600) == "skip-headless"

    def test_optout_env_skips(self):
        assert sb.decide(optout_env=True, optout_ws=False, headless=False,
                         missing=["trivy"], status={}, now=T0, throttle_s=3600) == "skip-optout-env"

    def test_optout_ws_skips(self):
        assert sb.decide(optout_env=False, optout_ws=True, headless=False,
                         missing=["trivy"], status={}, now=T0, throttle_s=3600) == "skip-optout-ws"

    def test_all_present_is_ok(self):
        assert sb.decide(optout_env=False, optout_ws=False, headless=False,
                         missing=[], status={}, now=T0, throttle_s=3600) == "ok"

    def test_missing_no_prior_attempt_installs(self):
        assert sb.decide(optout_env=False, optout_ws=False, headless=False,
                         missing=["trivy"], status={}, now=T0, throttle_s=3600) == "install"

    def test_missing_recent_attempt_is_throttled(self):
        recent = "2026-06-22T11:30:00Z"  # 30 min before T0
        assert sb.decide(optout_env=False, optout_ws=False, headless=False,
                         missing=["trivy"], status=_status(recent),
                         now=T0, throttle_s=3600) == "throttled"

    def test_missing_old_attempt_reinstalls(self):
        old = "2026-06-22T09:00:00Z"  # 3h before T0
        assert sb.decide(optout_env=False, optout_ws=False, headless=False,
                         missing=["trivy"], status=_status(old),
                         now=T0, throttle_s=3600) == "install"

    def test_unparseable_last_attempt_does_not_block_install(self):
        # A corrupt timestamp must not silently throttle forever (fail toward action).
        assert sb.decide(optout_env=False, optout_ws=False, headless=False,
                         missing=["trivy"], status=_status("not-a-date"),
                         now=T0, throttle_s=3600) == "install"

    def test_optout_beats_presence(self):
        # opt-out short-circuits before the missing check
        assert sb.decide(optout_env=True, optout_ws=False, headless=False,
                         missing=[], status={}, now=T0, throttle_s=3600) == "skip-optout-env"


# --------------------------------------------------------------------------
# status file I/O
# --------------------------------------------------------------------------

class TestStatusIO:
    def test_write_then_read_roundtrip(self, tmp_path):
        p = tmp_path / "nested" / "scanner-status.json"
        sb.write_status(str(p), {"outcome": "ok", "present": ["gitleaks"]})
        got = sb.read_status(str(p))
        assert got["outcome"] == "ok"
        assert got["present"] == ["gitleaks"]

    def test_read_missing_returns_none(self, tmp_path):
        assert sb.read_status(str(tmp_path / "nope.json")) is None

    def test_read_corrupt_returns_none(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("{ not json")
        assert sb.read_status(str(p)) is None


# --------------------------------------------------------------------------
# check_presence() — parses the installer's --check output
# --------------------------------------------------------------------------

def _fake_installer(tmp_path, *, missing=("trivy",), sentinel=None, check_rc=None):
    """Create a fake install-scanners.sh.

    --check: prints 'present: X' / 'MISSING: X' for each tool, exits 1 if any missing.
    full run: touches `sentinel` and prints an install line (no network).
    """
    tools = ["gitleaks", "semgrep", "osv-scanner", "trivy", "pip-audit"]
    miss = set(missing)
    sentinel = sentinel or (tmp_path / "installed.sentinel")
    lines = []
    for t in tools:
        if t in miss:
            lines.append(f'echo "MISSING: {t}"')
        else:
            lines.append(f'echo "present: {t}"')
    check_block = "\n".join(lines)
    rc = check_rc if check_rc is not None else ("1" if miss else "0")
    script = f"""#!/usr/bin/env bash
if [ "${{1:-}}" = "--check" ]; then
{check_block}
  exit {rc}
fi
echo "rawgentic: ensuring security scanners are installed"
touch "{sentinel}"
echo "  installed (fake)"
exit 0
"""
    p = tmp_path / "fake-install-scanners.sh"
    p.write_text(script)
    p.chmod(0o755)
    return p, Path(sentinel)


class TestCheckPresence:
    def test_parses_present_and_missing(self, tmp_path):
        installer, _ = _fake_installer(tmp_path, missing=("trivy", "pip-audit"))
        present, missing = sb.check_presence(str(installer))
        assert "gitleaks" in present and "semgrep" in present
        assert set(missing) == {"trivy", "pip-audit"}

    def test_all_present(self, tmp_path):
        installer, _ = _fake_installer(tmp_path, missing=())
        present, missing = sb.check_presence(str(installer))
        assert missing == []
        assert len(present) == 5


# --------------------------------------------------------------------------
# main() — end-to-end via subprocess, fake installer, isolated HOME
# --------------------------------------------------------------------------

SCRIPT = HOOKS_DIR / "scanner_bootstrap.py"


def _run_main(tmp_path, installer, *, event="startup", workspace=None,
              extra_env=None, now=None):
    env = dict(os.environ)
    env["HOME"] = str(tmp_path)
    env["RAWGENTIC_SCANNER_INSTALLER"] = str(installer)
    if now:
        env["RAWGENTIC_NOW"] = now
    if extra_env:
        env.update(extra_env)
    cmd = ["python3", str(SCRIPT), "--event", event]
    if workspace:
        cmd += ["--workspace", str(workspace)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=30, env=env)
    return r


def _status_path(tmp_path):
    return tmp_path / ".rawgentic" / "scanner-status.json"


def _wait_for(path, timeout=8.0):
    end = time.time() + timeout
    while time.time() < end:
        if Path(path).exists():
            return True
        time.sleep(0.05)
    return False


class TestMainIntegration:
    def test_missing_triggers_background_install_and_status(self, tmp_path):
        installer, sentinel = _fake_installer(tmp_path, missing=("trivy",))
        r = _run_main(tmp_path, installer)
        assert r.returncode == 0, r.stderr
        # status written, outcome installing
        st = json.loads(_status_path(tmp_path).read_text())
        assert st["outcome"] == "installing"
        assert "trivy" in st["missing"]
        assert st.get("last_install_attempt")
        # a context message is emitted for the model
        assert "background" in r.stdout.lower() or "installing" in r.stdout.lower()
        # the detached install actually ran (no network in the fake)
        assert _wait_for(sentinel), "background install did not run"

    def test_all_present_records_ok_and_no_install(self, tmp_path):
        installer, sentinel = _fake_installer(tmp_path, missing=())
        r = _run_main(tmp_path, installer)
        assert r.returncode == 0, r.stderr
        st = json.loads(_status_path(tmp_path).read_text())
        assert st["outcome"] == "ok"
        assert st["missing"] == []
        time.sleep(0.3)
        assert not sentinel.exists(), "must not install when nothing is missing"
        assert r.stdout.strip() == "", "all-present must be quiet (no context noise)"

    def test_env_optout_skips_visibly(self, tmp_path):
        installer, sentinel = _fake_installer(tmp_path, missing=("trivy",))
        r = _run_main(tmp_path, installer,
                      extra_env={"RAWGENTIC_SKIP_SCANNER_INSTALL": "1"})
        assert r.returncode == 0
        st = json.loads(_status_path(tmp_path).read_text())
        assert st["outcome"] == "skipped-optout-env"
        time.sleep(0.3)
        assert not sentinel.exists()

    def test_headless_skips_visibly(self, tmp_path):
        installer, sentinel = _fake_installer(tmp_path, missing=("trivy",))
        r = _run_main(tmp_path, installer, extra_env={"RAWGENTIC_HEADLESS": "1"})
        assert r.returncode == 0
        st = json.loads(_status_path(tmp_path).read_text())
        assert st["outcome"] == "skipped-headless"
        time.sleep(0.3)
        assert not sentinel.exists()

    def test_workspace_optout_skips_visibly(self, tmp_path):
        installer, sentinel = _fake_installer(tmp_path, missing=("trivy",))
        ws = tmp_path / ".rawgentic_workspace.json"
        ws.write_text(json.dumps({"version": 1, "installScanners": False, "projects": []}))
        r = _run_main(tmp_path, installer, workspace=ws)
        assert r.returncode == 0
        st = json.loads(_status_path(tmp_path).read_text())
        assert st["outcome"] == "skipped-optout-ws"
        time.sleep(0.3)
        assert not sentinel.exists()

    def test_throttle_prevents_respawn(self, tmp_path):
        installer, sentinel = _fake_installer(tmp_path, missing=("trivy",))
        # First run at 12:00 launches install + records last_install_attempt.
        r1 = _run_main(tmp_path, installer, now="2026-06-22T12:00:00Z")
        assert json.loads(_status_path(tmp_path).read_text())["outcome"] == "installing"
        assert _wait_for(sentinel)
        sentinel.unlink()  # remove so a re-install would be detectable
        # Second run 10 min later (within default throttle) must NOT relaunch.
        r2 = _run_main(tmp_path, installer, now="2026-06-22T12:10:00Z")
        assert r2.returncode == 0
        st = json.loads(_status_path(tmp_path).read_text())
        assert st["outcome"] == "throttled"
        time.sleep(0.3)
        assert not sentinel.exists(), "throttle must prevent a respawn within the window"

    def test_detectable_no_fire_leaves_status(self, tmp_path):
        """The whole point of Hole 2's detectability: even a skip writes a fresh,
        timestamped status — a stale/absent status is now a visible no-fire signal."""
        installer, _ = _fake_installer(tmp_path, missing=("trivy",))
        _run_main(tmp_path, installer, extra_env={"RAWGENTIC_HEADLESS": "1"},
                  now="2026-06-22T12:00:00Z")
        st = json.loads(_status_path(tmp_path).read_text())
        assert st["checked_at"] == "2026-06-22T12:00:00Z"
        assert st["event"] == "startup"
