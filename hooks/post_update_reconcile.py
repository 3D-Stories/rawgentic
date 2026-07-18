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
  - answer-required features (adversarialReview/WF5, modelRouting, peerConsult,
    designArtifact — each needs setup answers) are never silently enabled;
    instead the user is NUDGED to run /rawgentic:setup — but ONLY when the
    version jump actually crossed the feature's `since` (the plugin version
    that introduced it, #184). An upgrade that shipped no new setup-requiring
    feature bumps the marker silently, with no output,
  - a workspace-level `"setupPrompt": false` (top level of
    .rawgentic_workspace.json) silences the prompt entirely; the marker is
    still bumped silently so lifting the opt-out only prompts on the NEXT
    upgrade, never retroactively (#184),
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
from pathlib import Path

from atomic_write_lib import atomic_write_text

STATE_FILENAME = "rawgentic-reconciled-version"

# The feature policy table. Add a new opt-OUT capability here as a
# {"key": ..., "policy": "auto-on", "default_value": ...} entry and it will flip
# on automatically after the next plugin update (honoring recorded opt-outs).
# Today there are no auto-on entries: the security scanners are install-managed by
# scanner_bootstrap.py rather than a per-project workspace flag, headless is opt-in,
# and the rest are answer-required. The framework exists so future features get
# this for free.
#
# `since` = the plugin version whose release introduced the feature's setup step
# (from git history of skills/setup/). needs-question nudges fire only when the
# reconciled-version jump crosses `since` (#184). Every entry here must match a
# workspace field staged by skills/setup/SKILL.md — a drift-guard test enforces
# the two sets stay equal, so a new setup opt-in step MUST add an entry here.
FEATURE_MANIFEST = [
    {"key": "headlessEnabled", "policy": "opt-in", "since": "2.18.0"},
    {"key": "adversarialReview", "policy": "needs-question",
     "nudge": "adversarial review (WF5)", "since": "2.24.0"},
    {"key": "modelRouting", "policy": "needs-question",
     "nudge": "model routing", "since": "2.46.0"},
    {"key": "peerConsult", "policy": "needs-question",
     "nudge": "peer consult (WF13)", "since": "2.46.0"},
    {"key": "designArtifact", "policy": "needs-question",
     "nudge": "HTML design-artifact lifecycle", "since": "2.63.0"},
    # #446: source "project_config" = the key lives in the PROJECT's .rawgentic.json, not
    # the workspace entry. Consumed ONLY by the source-aware project_feature_gaps (with a
    # parsed config passed in); reconcile_projects SKIPS these entirely (its needs-question
    # loop reads the workspace entry, where this key never appears — un-skipped it would
    # nudge every project on the version crossing, even configured ones).
    {"key": "phaseExecutorTable", "policy": "needs-question",
     "nudge": "phase-executor seat table (setup Step 2i)", "since": "3.55.0",
     "source": "project_config"},
]


def _ver_tuple(v):
    """Parse "2.66.0" -> (2, 66, 0) for NUMERIC comparison (string compare
    orders "2.9.0" after "2.10.0"). None on anything unparseable."""
    if not v or not isinstance(v, str):
        return None
    try:
        return tuple(int(p) for p in v.strip().split("."))
    except ValueError:
        return None


def _newly_crossed(manifest, last, current):
    """Entries whose `since` lies in (last, current].

    A missing marker (fresh install) counts as version zero, so only features
    that exist at `current` qualify. No `since`, or any unparseable version,
    -> eligible (fail-open toward prompting — a bad version string must never
    permanently silence a nudge)."""
    cur_t = _ver_tuple(current)
    last_t = _ver_tuple(last) if last else (0,)
    out = []
    for feat in manifest:
        since = feat.get("since")
        if since is None:
            out.append(feat)
            continue
        s_t = _ver_tuple(since)
        if cur_t is None or last_t is None or s_t is None:
            out.append(feat)
            continue
        if last_t < s_t <= cur_t:
            out.append(feat)
    return out


def project_feature_gaps(project, manifest, current, project_config=None):
    """#234: answer-required (needs-question) features a project has NOT configured
    that EXIST at `current` (their `since` <= current). This is PER-PROJECT and
    independent of the reconcile marker — it surfaces a project left behind even when
    the plugin already updated (`last == current`), which the version-cross nudge in
    `main()` deliberately does not. Returns [(key, nudge_label), ...] sorted by key.

    A feature with no/unparseable `since`, or an unparseable `current`, counts as
    present (fail-open toward nudging — a bad version string must never silence it).

    #446 source-awareness (PURE — the caller does the I/O): entries without `source`
    check the workspace entry (`key in project`, byte-identical to before); entries with
    `source == "project_config"` check the PARSED `.rawgentic.json` passed as
    `project_config`. `project_config is None` means absent-or-uninspectable -> NO gap
    for those entries (fail-open; the caller owns the uninspectable stderr warning)."""
    cur_t = _ver_tuple(current)
    out = []
    if not isinstance(project, dict):
        return out
    for feat in manifest:
        if feat.get("policy") != "needs-question":
            continue
        key = feat.get("key")
        if not key:
            continue
        if feat.get("source") == "project_config":
            if project_config is None or not isinstance(project_config, dict):
                continue  # fail-open: cannot confirm absence
            if key in project_config:
                continue  # present (any value) = answered
        elif key in project:  # workspace-entry source (the default): present = answered
            continue
        s_t = _ver_tuple(feat.get("since"))
        if cur_t is None or s_t is None or s_t <= cur_t:
            out.append((key, feat.get("nudge", key)))
    return sorted(out)


def _project_config_state(workspace_path, entry):
    """(state, parsed_config) for an entry's `.rawgentic.json` (#446 — the I/O half the
    pure gap function must not do). Three states (A5): 'ok' (parsed dict), 'absent'
    (plain ENOENT — silent, the run-setup nudge owns it), 'uninspectable' (present but
    unreadable/unparseable, a non-dict, or an entry path escaping the workspace root —
    the caller emits ONE stable stderr warning so a regression is observable). Never
    raises; never reuses _load_json (which collapses ENOENT and parse errors)."""
    try:
        root = Path(workspace_path).resolve().parent
        rel = entry.get("path")
        if not isinstance(rel, str) or not rel:
            return "uninspectable", None
        proj = (root / rel).resolve()
        if proj != root and root not in proj.parents:
            return "uninspectable", None  # traversal — never follow outside the workspace
        cfg = proj / ".rawgentic.json"
        try:
            with open(cfg, encoding="utf-8") as f:
                parsed = json.load(f)
        except FileNotFoundError:
            return "absent", None
        except (OSError, ValueError):
            return "uninspectable", None
        if not isinstance(parsed, dict):
            return "uninspectable", None
        return "ok", parsed
    except Exception:  # noqa: BLE001 — advisory pass: any surprise is uninspectable, never a crash
        return "uninspectable", None


def _sanitize_name(name):
    return "".join(c if (c.isalnum() or c in "._-") else "-" for c in str(name)) or "_"


def _staleness_marker_path(state_dir, project_name):
    return os.path.join(state_dir, "rawgentic-staleness-" + _sanitize_name(project_name))


def _staleness_nudge(name, gaps, current):
    labels = ", ".join(lbl for _, lbl in gaps)
    return (
        f"Project '{name}' is behind rawgentic {current}: it has not configured "
        f"available setup feature(s): {labels}. Run /rawgentic:setup to enable them "
        f"for '{name}' (setup preserves your existing config). Silence these notices "
        f'with "setupPrompt": false at the top level of .rawgentic_workspace.json.'
    )


def _run_staleness(args, current, manifest):
    """#234 modes: `--staleness-project <name>` (always show one project's gaps —
    for the explicit /switch action) and `--staleness-active` (each active project,
    once per (project, version) via a per-project marker — for SessionStart, so it
    doesn't nag every session). Both respect the workspace-level setupPrompt opt-out
    and are advisory/non-blocking. Fail-open: any problem emits nothing."""
    ws = _load_json(args.workspace)
    if not isinstance(ws, dict):
        return 0
    if ws.get("setupPrompt", True) is False:
        return 0  # #184 opt-out silences per-project nudges too
    projects = ws.get("projects")
    if not isinstance(projects, list):
        return 0

    parts = []
    if args.staleness_project:
        p = next((x for x in projects
                  if isinstance(x, dict) and x.get("name") == args.staleness_project), None)
        if isinstance(p, dict):
            state, pcfg = _project_config_state(args.workspace, p)
            if state == "uninspectable":
                print(f"post_update_reconcile: cannot inspect project config for "
                      f"{args.staleness_project!r} ({p.get('path')!r}/.rawgentic.json) — "
                      f"project_config staleness checks skipped", file=sys.stderr)
            gaps = project_feature_gaps(p, manifest, current,
                                        project_config=pcfg if state == "ok" else None)
            if gaps:
                parts.append(_staleness_nudge(args.staleness_project, gaps, current))
        # explicit switch: always show, no once-per-version marker
    else:  # --staleness-active
        # Accepted overlap (#234 review L1): on the FIRST session after a release
        # that ships a NEW needs-question feature (since == the just-crossed version),
        # the default reconcile's version-cross nudge AND this per-project nudge both
        # fire once for that feature — two differently-worded notices in one session,
        # self-clearing next session (both markers == current). Steady-state upgrades
        # that ship no new needs-question feature never trigger it. Left as-is rather
        # than coupling this pass to the reconciled-version marker.
        for p in projects:
            if not isinstance(p, dict) or not p.get("active"):
                continue
            name = p.get("name", "?")
            state, pcfg = _project_config_state(args.workspace, p)
            if state == "uninspectable":
                print(f"post_update_reconcile: cannot inspect project config for "
                      f"{name!r} ({p.get('path')!r}/.rawgentic.json) — "
                      f"project_config staleness checks skipped", file=sys.stderr)
            gaps = project_feature_gaps(p, manifest, current,
                                        project_config=pcfg if state == "ok" else None)
            if not gaps:
                continue
            marker = _staleness_marker_path(args.state_dir, name)
            if _read_version_file(marker) == current:
                continue  # already nudged this project for this version
            parts.append(_staleness_nudge(name, gaps, current))
            try:
                _write_version_file(marker, current)
            except Exception:
                pass  # fail-open: a lost marker only risks a repeat nudge, never a crash

    if parts:
        print("\n\n".join(parts))
    return 0


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
            if feat.get("source") == "project_config":
                # #446 P2-G1: this key lives in .rawgentic.json, never the workspace entry —
                # `key in p` is always False here, so an un-skipped entry would nudge EVERY
                # project on its version crossing. The source-aware staleness pass owns it.
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
    atomic_write_text(path, json.dumps(data, indent=2),
                      prefix=".rawgentic-tmp-", mkdir=True)


def _read_version_file(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except (FileNotFoundError, OSError):
        return None


def _write_version_file(path, version):
    _write_json_text(path, version + "\n")


def _write_json_text(path, text):
    atomic_write_text(path, text, prefix=".reconciled-", mkdir=True)


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
    # #234 per-project staleness modes (separate from the default reconcile so the
    # "silent on same version" reconcile contract is preserved):
    parser.add_argument("--staleness-project", default=None,
                        help="#234: always nudge THIS project's unconfigured feature gaps (for /switch)")
    parser.add_argument("--staleness-active", action="store_true",
                        help="#234: nudge each ACTIVE project's gaps once per version (for SessionStart)")
    parser.add_argument("--session-start", action="store_true",
                        help="#269: run the default reconcile AND the "
                             "--staleness-active pass in ONE process (each "
                             "pass isolated, fail-open)")
    args = parser.parse_args(argv)

    current = _resolve_version(args)
    if not current:
        return 0  # can't determine the version -> do nothing

    # #269: session-start combined mode — both passes, one spawn. Each pass is
    # isolated so a reconcile failure cannot suppress the staleness nudge (and
    # vice versa); both are fail-open by contract. Each pass's output is
    # captured and the non-empty parts joined with a BLANK line — the two
    # notices were separate CONTEXT_PARTS before #269 (double-newline join),
    # and the combined output must render identically (R2 parity catch).
    if args.session_start:
        import contextlib
        import io
        parts = []
        for pass_fn in (
            lambda: _run_reconcile(args, current),
            lambda: (setattr(args, "staleness_project", None),
                     setattr(args, "staleness_active", True),
                     _run_staleness(args, current, _load_manifest())),
        ):
            buf = io.StringIO()
            try:
                with contextlib.redirect_stdout(buf):
                    pass_fn()
            except Exception:
                pass
            text = buf.getvalue().strip()
            if text:
                parts.append(text)
        if parts:
            print("\n\n".join(parts))
        return 0

    # #234: staleness modes short-circuit the default reconcile entirely.
    if args.staleness_project or args.staleness_active:
        return _run_staleness(args, current, _load_manifest())

    return _run_reconcile(args, current)


def _run_reconcile(args, current):
    """The default once-per-version reconcile pass (silent on same version)."""
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
    # #184: needs-question nudges fire only for features the version jump
    # actually shipped; auto-on/opt-in policies are ungated (unchanged semantics).
    newly_keys = {f.get("key") for f in _newly_crossed(manifest, last, current)}
    gated = [f for f in manifest
             if f.get("policy") != "needs-question" or f.get("key") in newly_keys]
    _, changes, needs_q = reconcile_projects(projects, gated)

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

    if ws.get("setupPrompt", True) is False:
        return 0  # #184 opt-out: marker already bumped silently above

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
            f"rawgentic updated to {current}, which ships new setup-requiring "
            f"feature(s): {labels}. Run /rawgentic:setup to configure them for: "
            f"{projs} (setup preserves your existing config). If you skip it, run "
            f"/rawgentic:setup any time later — this notice won't repeat for "
            f"{current}. Silence these notices permanently with "
            f'"setupPrompt": false at the top level of .rawgentic_workspace.json.'
        )
    if parts:
        print("\n\n".join(parts))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
