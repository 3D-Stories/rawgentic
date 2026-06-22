#!/usr/bin/env python3
"""SessionStart post-update reconcile — new features ON by default after an update.

Invoked by hooks/session-start on startup|resume. When the plugin gains a
capability, existing projects should not stay on the old defaults until the user
happens to re-run setup. On a plugin VERSION CHANGE this reconciles each project's
workspace config against the current feature manifest:

  - auto-on (opt-OUT) features whose flag is ABSENT are turned on by default;
    an explicit opt-out already on record (the flag present with any value) is
    HONORED and left alone,
  - opt-in features (headlessEnabled — grants external-orchestrator access) are
    never force-enabled,
  - answer-required features (adversarialReview/WF5 — needs an OpenAI account for
    Codex) are never silently enabled; instead the user is NUDGED to run
    /rawgentic:setup,
  - the reconciled version is recorded so this runs exactly ONCE per version.

The version is read from the plugin's .claude-plugin/plugin.json (so it tracks
the installed version automatically). The reconciled-version marker is stored
per-workspace (in --state-dir, normally the workspace's claude_docs) so each
workspace reconciles independently.

Fail-open: any error leaves the workspace untouched and emits nothing.
"""
import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

STATE_FILENAME = "rawgentic-reconciled-version"

# The feature policy table. Add a new opt-OUT capability here as a
# {"key": ..., "policy": "auto-on", "default_value": ...} entry and it will flip
# on automatically after the next plugin update (honoring recorded opt-outs).
# Today there are no auto-on entries: the security scanners are install-managed by
# scanner_bootstrap.py rather than a per-project workspace flag, headless is opt-in,
# and WF5 is answer-required. The framework exists so future features get this for free.
FEATURE_MANIFEST = [
    {"key": "adversarialReview", "policy": "needs-question",
     "nudge": "adversarial review (WF5)"},
    {"key": "headlessEnabled", "policy": "opt-in"},
]


def reconcile_projects(projects, manifest):
    """Pure: apply the manifest to a list of project entries (mutates + returns).

    Returns (projects, changes, needs_question):
      changes        = [(project_name, key, value), ...]  auto-on flags newly set
      needs_question = [(project_name, key, nudge_label), ...]  active projects
                       missing an answer-required feature
    """
    changes = []
    needs_question = []
    for p in projects:
        if not isinstance(p, dict):
            continue
        name = p.get("name", "?")
        for feat in manifest:
            key = feat.get("key")
            if not key:
                continue
            policy = feat.get("policy")
            present = key in p
            if policy == "auto-on":
                # Absent -> set the default ON. Present (even a falsey value) is a
                # recorded opt-out and is left untouched.
                if not present:
                    val = feat.get("default_value", True)
                    p[key] = val
                    changes.append((name, key, val))
            elif policy == "needs-question":
                if not present and p.get("active"):
                    needs_question.append((name, key, feat.get("nudge", key)))
            # opt-in: never touched
    return projects, changes, needs_question


# --------------------------------------------------------------------------
# I/O helpers
# --------------------------------------------------------------------------

def _load_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError, ValueError):
        return None


def _write_json_atomic(path, data):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".rawgentic-tmp-")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, str(p))
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _read_version_file(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except (FileNotFoundError, OSError):
        return None


def _write_version_file(path, version):
    _write_json_text(path, version + "\n")


def _write_json_text(path, text):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".reconciled-")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.replace(tmp, str(p))
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _resolve_version(args):
    if args.version:
        return args.version
    root = args.plugin_root or str(Path(__file__).resolve().parent.parent)
    pj = _load_json(os.path.join(root, ".claude-plugin", "plugin.json"))
    if isinstance(pj, dict):
        return pj.get("version")
    return None


def _load_manifest():
    override = os.environ.get("RAWGENTIC_RECONCILE_MANIFEST")
    if override:
        m = _load_json(override)
        if isinstance(m, list):
            return m
    return FEATURE_MANIFEST


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(description="rawgentic post-update reconcile")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--version", default=None, help="override the detected version (tests)")
    parser.add_argument("--plugin-root", default=None)
    args = parser.parse_args(argv)

    current = _resolve_version(args)
    if not current:
        return 0  # can't determine the version -> do nothing

    state_path = os.path.join(args.state_dir, STATE_FILENAME)
    last = _read_version_file(state_path)
    if last == current:
        return 0  # no version change -> silent (record-once already satisfied)

    ws = _load_json(args.workspace)
    if not isinstance(ws, dict):
        # Workspace missing/corrupt: we haven't reconciled it, so DON'T record the
        # version — retry next session once it's readable. Stay silent.
        return 0

    projects = ws.get("projects")
    if not isinstance(projects, list):
        projects = []

    manifest = _load_manifest()
    _, changes, needs_q = reconcile_projects(projects, manifest)

    persisted = True
    if changes:
        ws["projects"] = projects
        try:
            _write_json_atomic(args.workspace, ws)
        except Exception:
            # Could not persist the auto-on changes (fail-open: never break session
            # start). Crucially, do NOT record the version below — otherwise next
            # session short-circuits on (last == current) and the unpersisted
            # changes are lost forever. Leaving the version unrecorded retries the
            # whole reconcile next session.
            persisted = False

    if persisted:
        try:
            _write_version_file(state_path, current)
        except Exception:
            pass

    parts = []
    if changes and persisted:
        feats = ", ".join(sorted({c[1] for c in changes}))
        parts.append(
            f"rawgentic updated to {current} and enabled new default feature(s): "
            f"{feats} (opt-OUT — edit .rawgentic_workspace.json to disable)."
        )
    if needs_q:
        labels = ", ".join(sorted({n[2] for n in needs_q}))
        projs = ", ".join(sorted({n[0] for n in needs_q}))
        parts.append(
            f"rawgentic updated to {current}. Run /rawgentic:setup to configure new "
            f"answer-required feature(s) ({labels}) for: {projs}."
        )
    if parts:
        print("\n\n".join(parts))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
