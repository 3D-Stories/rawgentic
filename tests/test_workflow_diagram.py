"""#197 — the official versioned workflow diagram (docs/workflow-diagram.html).

Structural guards over the committed artifact: the diagram is a hand-maintained
single-file SPA, so these tests pin the invariants that make it the OFFICIAL
reference — full WF2 station coverage, both revision snapshots, self-containment
(CSP-safe, no external requests), no innerHTML (DOM-builder rendering only),
theme completeness, and the README embed that renders it on the repo main page.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
HTML = REPO_ROOT / "docs" / "workflow-diagram.html"
MD = REPO_ROOT / "docs" / "workflow-diagram.md"
README = REPO_ROOT / "README.md"

WF2_STATIONS_3_10 = [
    "Receive Issue", "Goal Guard", "Analyze & Classify", "Design Solution",
    "Gate · Design Critique", "Implementation Plan", "Gate · Plan Drift",
    "Create Branch", "Implementation", "Per-Task Review", "Gate · Impl Drift",
    "Memorize", "Pre-PR Code Review", "Security Scan", "Create PR",
    "CI Verification", "Merge & Deploy", "Post-Deploy Verify",
    "Summary + Run-Record",
]


def _html():
    return HTML.read_text()


def test_diagram_files_exist_in_docs_base():
    """AC4 + owner override: artifact lives in docs/ base (also the GitHub
    Pages-servable folder), not docs/planning/."""
    assert HTML.exists(), "docs/workflow-diagram.html must exist"
    assert MD.exists(), "docs/workflow-diagram.md (companion doc) must exist"


def test_all_wf2_stations_present():
    """AC1: the canonical spine, every station a node."""
    text = _html()
    for name in WF2_STATIONS_3_10:
        assert name in text, f"WF2 station missing: {name}"


def test_versioned_with_both_snapshots():
    """AC3: version selector ships 3.1.0 (pre-campaign) + 3.10.0 snapshots."""
    text = _html()
    assert '"3.10.0"' in text and '"3.1.0"' in text
    assert "superseded" in text  # old revs render a superseded stamp


def test_rev_selector_is_a_dropdown():
    """The REV selector is a <select> dropdown (scales to many revisions), not
    one button per revision — built in renderChrome from the workflow's revs."""
    text = _html()
    assert "rev-select" in text
    assert "h('select'," in text
    assert "h('option'," in text
    # charset declared so em-dashes / × / → render when served standalone
    assert '<meta charset="utf-8">' in text


def test_extensible_registry_covers_other_workflows():
    """AC5: WF1/WF3/WF5 registry entries exist (skeletal phase sheets)."""
    text = _html()
    for code, name in [("WF1", "Create Issue"), ("WF3", "Fix Bug"),
                       ("WF5", "Adversarial Review")]:
        assert code in text and name in text
    # real WF3 phases, not invented ones
    assert "Root Cause Analysis" in text
    assert "Reproduce-First" in text
    # WF1's decompose + WF5's prerequisite gate prove real sourcing
    assert "Decompose" in text
    assert "Prerequisite Gate" in text


def test_hash_routed_self_contained_spa():
    """AC2: hash-routed single-file SPA, CSP-safe — zero external requests."""
    text = _html()
    assert "hashchange" in text
    assert "data:font/woff2;base64," in text  # fonts embedded, not linked
    for banned in ("https://fonts.googleapis", "https://fonts.gstatic",
                   '<script src=', '<link rel="stylesheet" href="http'):
        assert banned not in text, f"external resource reference: {banned}"


def test_no_innerhtml_rendering():
    """The artifact renders via a DOM builder — no innerHTML/outerHTML/
    insertAdjacentHTML sinks (repo security-hook contract + showcase-safe)."""
    text = _html()
    for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML",
                 "document.write"):
        assert sink not in text, f"forbidden sink: {sink}"


def test_both_themes_and_reduced_motion():
    text = _html()
    assert "prefers-color-scheme:dark" in text.replace(" ", "") or \
           "prefers-color-scheme: dark" in text
    assert 'data-theme="dark"' in text and 'data-theme="light"' in text
    assert "prefers-reduced-motion" in text


def test_font_license_attribution_present():
    """Vendored OFL fonts (IBM Plex, Big Shoulders) must carry attribution."""
    text = _html()
    assert "SIL Open Font License" in text
    assert "IBM Plex" in text and "Big Shoulders" in text


def test_mockup_stamp_removed():
    """The FOR REVIEW / NOT FOR CONSTRUCTION stamp was mockup-only."""
    low = _html().lower()
    assert "not for construction" not in low
    assert "mockup" not in low


def test_loopback_budgets_encoded():
    """Gate facts: the four loop-back sources + the global budget of 3."""
    text = _html()
    assert "Global loop-back budget: 3" in text
    for lbl in ("×2", "×1 tdd"):
        assert lbl in text


def test_companion_doc_carries_update_recipe():
    """AC3 longevity: the md documents how to append the next WF2 revision
    and how to regenerate the README snapshots."""
    doc = MD.read_text()
    low = doc.lower()
    assert "versions" in low and "append" in low
    assert "snapshot" in low
    assert "sil open font license" in low
    assert "github pages" in low


def test_readme_embeds_theme_aware_snapshot():
    """AC6/owner ask: the diagram renders on the GitHub main page via a
    theme-aware <picture> snapshot linking to the interactive page."""
    text = README.read_text()
    assert "docs/assets/workflow-diagram-dark.png" in text
    assert "docs/assets/workflow-diagram-light.png" in text
    assert 'media="(prefers-color-scheme: dark)"' in text
    assert "docs/workflow-diagram.html" in text


def test_snapshot_assets_committed():
    for p in ("workflow-diagram-light.png", "workflow-diagram-dark.png"):
        f = REPO_ROOT / "docs" / "assets" / p
        assert f.exists(), f"missing snapshot asset {p}"
        assert f.stat().st_size > 20_000, f"{p} suspiciously small"


def test_diagram_newest_rev_matches_plugin_version():
    """The diagram's newest WF2 rev must exist and not exceed the shipped
    plugin version (it documents the pinned spine, never a future one)."""
    import json
    plugin = json.loads(
        (REPO_ROOT / ".claude-plugin" / "plugin.json").read_text())
    revs = re.findall(r'revs:\["([0-9.]+)"', _html())
    assert revs, "wf2 revs array not found"
    newest = tuple(int(x) for x in revs[0].split("."))
    shipped = tuple(int(x) for x in plugin["version"].split("."))
    assert newest <= shipped, (
        f"diagram documents rev {revs[0]} > shipped plugin {plugin['version']}")
