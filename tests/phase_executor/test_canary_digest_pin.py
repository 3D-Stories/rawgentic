"""#468 W5 — guardrail-canary drift guard (the #271 pin==live idiom).

Two HARD pins, re-pinned per release:
- EXPECTED_REGISTRATION_DIGEST == compute_registration_digest(live) over hooks.json + every
  referenced enforcer script (adversarial H3). A mutated hooks.json OR any referenced script
  flips the framed digest and fails this test — forcing a conscious re-pin.
- EXPECTED_PLUGIN_VERSION == the live .claude-plugin/plugin.json version.

These are HARD assertions on purpose: a self-lifting skip/xfail would make the guard inert (a
check that can never fire), which is exactly the failure mode a drift guard exists to prevent.
"""
import json
import pathlib

from phase_executor import canary

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


def test_registration_digest_pin_matches_live():
    live = canary.compute_registration_digest(REPO_ROOT)
    assert canary.EXPECTED_REGISTRATION_DIGEST == live, (
        "hooks.json or a referenced enforcer script changed — re-pin "
        f"canary.EXPECTED_REGISTRATION_DIGEST to {live!r}")


def test_plugin_version_pin_matches_manifest():
    manifest = json.loads((REPO_ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert canary.EXPECTED_PLUGIN_VERSION == manifest["version"], (
        f"EXPECTED_PLUGIN_VERSION ({canary.EXPECTED_PLUGIN_VERSION}) != live plugin.json "
        f"({manifest['version']}) — re-pin both to the version this PR ships")


def test_pretooluse_guard_set_is_classified():
    """Security-review M1: positive_deny coverage is bounded by the CURATED _GUARD_DENY_MARKERS,
    not auto-derived from the matcher set. This guard fails if hooks.json gains a PreToolUse guard
    absent from BOTH _GUARD_DENY_MARKERS and _KNOWN_NONENFORCING_PRETOOL_GUARDS — forcing a
    conscious classify so a NEW enforcing-deny guard can never be silently left un-probed."""
    hooks_obj = json.loads((REPO_ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    live = canary.pretooluse_guard_basenames(hooks_obj)
    classified = set(canary._GUARD_DENY_MARKERS) | set(canary._KNOWN_NONENFORCING_PRETOOL_GUARDS)
    unclassified = live - classified
    assert not unclassified, (
        f"unclassified PreToolUse guard(s) {sorted(unclassified)} — add each to "
        "canary._GUARD_DENY_MARKERS (if it enforces a deny for the un-granted-mutating-tool "
        "threat) or canary._KNOWN_NONENFORCING_PRETOOL_GUARDS (if genuinely non-enforcing)")
