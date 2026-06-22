#!/usr/bin/env python3
"""Reliable, detectable, self-healing security-scanner bootstrap.

Invoked by hooks/session-start on startup|resume. Replaces the old in-bash
fire-once bootstrap, which had two defects:

  1. It never fired in production — its `[ "$EVENT_TYPE" = "startup" ]` guard
     read the wrong field (hook_event_name, always "SessionStart", instead of
     `source`), so the whole function returned immediately. (Fixed at the parse
     site in session-start.)
  2. Even once it DID fire, it wrote a permanent 0-byte marker
     (~/.rawgentic/scanners-bootstrapped) BEFORE the install finished. After
     that, the function returned on every startup forever — so a scanner that
     was later removed was never reinstalled, a plugin update that added a
     scanner never re-triggered, and a silent no-fire/failed-install looked
     identical to "all clean".

This module is idempotent and SELF-HEALING instead:
  - re-checks scanner presence every eligible session (cheap `install-scanners.sh
    --check`); because it re-checks, a newly-added scanner after a plugin update
    is installed on the next session with no version bookkeeping,
  - installs ONLY what's missing, in the BACKGROUND (the hook has a ~10s budget),
  - THROTTLES repeat install attempts (RAWGENTIC_SCANNER_RETRY_SECONDS, default
    6h) so a persistently-failing install doesn't respawn every session,
  - ALWAYS writes a timestamped status file (~/.rawgentic/scanner-status.json) —
    even when it skips — so a stale/absent status is a visible no-fire signal
    rather than masquerading as success.

Opt-outs (unchanged contract): RAWGENTIC_SKIP_SCANNER_INSTALL=1, or
"installScanners": false in .rawgentic_workspace.json. Skipped in headless
(RAWGENTIC_HEADLESS=1). Every skip is recorded in the status file with its reason.
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

STATUS_SCHEMA = 1
ALL_TOOLS = ["gitleaks", "semgrep", "osv-scanner", "trivy", "pip-audit"]
DEFAULT_THROTTLE_S = 21600  # 6h


# --------------------------------------------------------------------------
# status file I/O
# --------------------------------------------------------------------------

def read_status(path):
    """Return the parsed status dict, or None if absent/corrupt."""
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError, ValueError):
        return None


def write_status(path, status):
    """Atomically write the status dict (tmp file + os.replace)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".scanner-status-")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(status, f, indent=2)
        os.replace(tmp, str(p))
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# --------------------------------------------------------------------------
# presence check
# --------------------------------------------------------------------------

def check_presence(installer):
    """Run `bash <installer> --check` and parse which tools are present/missing.

    Returns (present, missing). Raises on a subprocess-level failure so the
    caller can record an 'error' outcome rather than guess.
    """
    r = subprocess.run(
        ["bash", installer, "--check"],
        capture_output=True, text=True, timeout=60,
    )
    present, missing = [], []
    for line in r.stdout.splitlines():
        s = line.strip()
        low = s.lower()
        if low.startswith("present:"):
            present.append(s.split(":", 1)[1].strip())
        elif low.startswith("missing:"):
            missing.append(s.split(":", 1)[1].strip())
    return present, missing


# --------------------------------------------------------------------------
# decision (pure)
# --------------------------------------------------------------------------

def _parse_ts(s):
    """Parse an ISO8601 timestamp to epoch seconds, or None if unparseable."""
    if not s:
        return None
    try:
        t = str(s).strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(t)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except (ValueError, TypeError):
        return None


def decide(*, optout_env, optout_ws, headless, missing, status, now, throttle_s):
    """Pure decision. Returns one of:
    skip-headless | skip-optout-env | skip-optout-ws | ok | throttled | install.

    Precedence mirrors the original bootstrap: headless first, then the env
    opt-out, then the workspace opt-out, then presence, then the throttle.
    """
    if headless:
        return "skip-headless"
    if optout_env:
        return "skip-optout-env"
    if optout_ws:
        return "skip-optout-ws"
    if not missing:
        return "ok"
    last = (status or {}).get("last_install_attempt")
    prev = _parse_ts(last)
    cur = _parse_ts(now)
    # A corrupt/missing last-attempt must NOT silently throttle forever — fall
    # through to install (fail toward doing the work).
    if prev is not None and cur is not None and (cur - prev) < throttle_s:
        return "throttled"
    return "install"


# --------------------------------------------------------------------------
# background install
# --------------------------------------------------------------------------

def launch_install(installer, log_path):
    """Launch the installer detached so it survives the hook process exiting.

    The log file is opened immediately (so its existence is itself a signal that
    an install was launched). start_new_session detaches the child from the
    hook's process group.
    """
    Path(log_path).parent.mkdir(parents=True, exist_ok=True)
    logf = open(log_path, "w")
    try:
        subprocess.Popen(
            ["bash", installer],
            stdout=logf, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    finally:
        # The child has its own dup'd fd; closing the parent handle is safe.
        logf.close()


# --------------------------------------------------------------------------
# helpers + main
# --------------------------------------------------------------------------

def _now_iso():
    override = os.environ.get("RAWGENTIC_NOW")
    if override:
        return override
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _int_env(name, default):
    v = os.environ.get(name)
    if v is None:
        return default
    try:
        return int(v)
    except ValueError:
        return default


def _ws_optout(workspace_path):
    """True iff the workspace file explicitly sets installScanners: false."""
    if not workspace_path:
        return False
    try:
        with open(workspace_path) as f:
            d = json.load(f)
        return d.get("installScanners") is False
    except (FileNotFoundError, json.JSONDecodeError, OSError, ValueError):
        return False


def _build_status(event, outcome, present, missing, now, log_path,
                  last_attempt=None, error=None):
    st = {
        "schema": STATUS_SCHEMA,
        "checked_at": now,
        "event": event,
        "outcome": outcome,
        "present": present,
        "missing": missing,
        "log": log_path,
    }
    if last_attempt:
        st["last_install_attempt"] = last_attempt
    if error:
        st["error"] = error
    return st


_OUTCOME = {
    "skip-headless": "skipped-headless",
    "skip-optout-env": "skipped-optout-env",
    "skip-optout-ws": "skipped-optout-ws",
    "ok": "ok",
    "throttled": "throttled",
    "install": "installing",
}


def main(argv=None):
    parser = argparse.ArgumentParser(description="rawgentic security-scanner bootstrap")
    parser.add_argument("--event", default="startup",
                        help="SessionStart subtype (startup/resume/...)")
    parser.add_argument("--workspace", default=None,
                        help="path to .rawgentic_workspace.json (for installScanners opt-out)")
    args = parser.parse_args(argv)

    home = os.environ.get("HOME") or os.path.expanduser("~")
    rawgentic_dir = os.path.join(home, ".rawgentic")
    status_path = (os.environ.get("RAWGENTIC_SCANNER_STATUS_PATH")
                   or os.path.join(rawgentic_dir, "scanner-status.json"))
    log_path = os.path.join(rawgentic_dir, "scanner-install.log")
    # Decommission the old fire-once marker (superseded by the status file). The
    # previous bootstrap wrote a permanent 0-byte ~/.rawgentic/scanners-bootstrapped
    # that disabled all re-checks once present; remove it so it can't mislead.
    try:
        _old = os.path.join(rawgentic_dir, "scanners-bootstrapped")
        if os.path.exists(_old):
            os.remove(_old)
    except OSError:
        pass
    installer = (os.environ.get("RAWGENTIC_SCANNER_INSTALLER")
                 or str(Path(__file__).resolve().parent.parent / "scripts" / "install-scanners.sh"))
    throttle_s = _int_env("RAWGENTIC_SCANNER_RETRY_SECONDS", DEFAULT_THROTTLE_S)
    now = _now_iso()

    headless = os.environ.get("RAWGENTIC_HEADLESS") == "1"
    optout_env = os.environ.get("RAWGENTIC_SKIP_SCANNER_INSTALL") == "1"
    optout_ws = _ws_optout(args.workspace)
    prev = read_status(status_path) or {}

    present, missing = [], []
    # Only spend the (cheap) --check subprocess when we might act on it.
    if not (headless or optout_env or optout_ws):
        try:
            present, missing = check_presence(installer)
        except Exception as e:  # subprocess failed — record, don't guess
            write_status(status_path, _build_status(
                args.event, "error", present, missing, now, log_path, error=str(e)))
            print(f"rawgentic: could not check security scanner presence ({e}). "
                  f"See {status_path}.")
            return 0

    action = decide(optout_env=optout_env, optout_ws=optout_ws, headless=headless,
                    missing=missing, status=prev, now=now, throttle_s=throttle_s)
    outcome = _OUTCOME[action]
    last_attempt = now if action == "install" else prev.get("last_install_attempt")

    # Write the status BEFORE launching anything: it records the install attempt so
    # the throttle/concurrent-session window starts immediately (narrowing the
    # chance two simultaneous startups both spawn an install), and it guarantees a
    # detectable status even if the background launch raises.
    write_status(status_path, _build_status(
        args.event, outcome, present, missing, now, log_path, last_attempt=last_attempt))

    if action == "install":
        try:
            launch_install(installer, log_path)
        except Exception as e:
            write_status(status_path, _build_status(
                args.event, "error", present, missing, now, log_path,
                last_attempt=last_attempt, error="launch failed: " + str(e)))
            return 0
        print(
            f"rawgentic is installing the missing WF2/WF9 security scanners "
            f"({', '.join(missing)}) in the BACKGROUND — log at {log_path}. It "
            f"re-checks every session and self-heals if a scanner goes missing. "
            f"Opt out with RAWGENTIC_SKIP_SCANNER_INSTALL=1 or "
            f'"installScanners": false in .rawgentic_workspace.json.'
        )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        # Fail-open: a bootstrap error must never break session start.
        sys.exit(0)
