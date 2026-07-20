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


def test_station_drilldown_is_a_modal_not_a_full_page_swap():
    """#227: drilling into a station opens a modal dialog over the overview, NOT a
    full-page swap. Pins the modal mechanism so a later edit can't silently revert."""
    text = _html()
    # a native <dialog> modal (built via the DOM builder) with the accessible contract
    assert "h('dialog'" in text
    assert "role:'dialog'" in text.replace(" ", "")
    assert "aria-modal" in text
    assert ".showModal(" in text
    # the open/close plumbing + the router opening the modal on a station hash
    assert "function openModal(" in text and "function closeModal(" in text
    assert "openModal(r)" in text
    # the overview stays rendered behind the modal (router no longer swaps the page
    # to a detail view): the old full-page detail renderer is gone
    assert "renderDetail(r)" not in text
    # dismissal wiring: Esc (dialog 'cancel') + a close button
    assert "'cancel'" in text or '"cancel"' in text
    assert "modal-close" in text


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


def test_wf14_registry_entry_present():
    """#337: WF14 run-feedback documented as a diagram workflow (skeletal)."""
    text = _html()
    assert "WF14" in text and "Run Feedback" in text
    assert "skills/run-feedback" in text
    # real WF14 phases, not invented ones (mirrors the WF1/WF3/WF5 sourcing check)
    assert "Telemetry Audit" in text
    assert "Gather Run Facts" in text


def test_every_ordered_workflow_has_nonempty_default_steps():
    """#337 guard: every DATA.order key carries versions[revs[0]] with a
    non-empty steps array — an empty/steps:null-only 'skeletal' entry crashes
    the SPA on tab click (the renderer dereferences versions[V].steps
    unconditionally; the steps:null rebuild loop reads revs[0]'s steps as its
    source, so revs[0] itself must be real)."""
    text = _html()
    m = re.search(r'order:\s*\[([^\]]*)\]', text)
    assert m, "DATA.order not found"
    keys = re.findall(r'"(wf\d+)"', m.group(1))
    assert keys, "DATA.order empty"
    for key in keys:
        wf = re.search(rf'\b{key}:\s*{{', text)
        assert wf, f"workflows.{key} entry missing"
        block = text[wf.start():]
        revs = re.search(r'revs:\["([0-9.]+)"', block)
        assert revs, f"{key}: revs array missing or empty"
        newest = revs.group(1)
        ver = re.search(rf'"{re.escape(newest)}":\s*{{[^{{]*steps:\s*\[', block)
        assert ver, (
            f"{key}: versions[{newest}] must carry a real steps ARRAY "
            f"(steps:null or a missing versions entry for revs[0] crashes the SPA)"
        )


# --- #447: executor-seat routing block (generated from the #445 source-of-truth) ---
import json  # noqa: E402
import sys  # noqa: E402

sys.path.insert(0, str(REPO_ROOT / "hooks"))
import diagram_seat_data as _dsd  # noqa: E402

_START = "/*SEAT-ROUTING-START*/"
_END = "/*SEAT-ROUTING-END*/"


def _seat_block():
    text = _html()
    i = text.find(_START) + len(_START)
    j = text.find(_END)
    assert 0 < i < j, "seat-routing sentinels missing or out of order"
    return text[i:j].strip()


def test_seat_routing_block_matches_source_of_truth():
    """AC2 drift guard: the committed DATA.seatRouting equals what the generator
    derives from the #445 routing-table source-of-truth. Config drift fails CI —
    the 'not hand-hardcoded' enforcement."""
    committed = json.loads(_seat_block())
    expected = _dsd.build_seat_dataset(_dsd.load_projection(), _dsd.PHASE_SEAT_MAP)
    assert committed == expected, (
        "DATA.seatRouting is stale vs the routing table; run "
        "`python3 hooks/diagram_seat_data.py write`")


def test_seat_routing_block_parses_and_has_valid_schema():
    """The between-sentinels slice is PURE JSON (not the `DATA.seatRouting =`
    assignment, not an executable fragment that could crash the tab), with a
    valid record schema."""
    block = _seat_block()
    assert not block.startswith("DATA.seatRouting"), "assignment leaked between the sentinels"
    data = json.loads(block)  # raises if the slice is not pure JSON
    assert set(data) >= {"provenance", "records", "mappedStationIds"}
    assert set(data["mappedStationIds"]) == set(data["records"])
    valid_cls = {"executor-wired", "competitive", "bake-off"}
    for sid, rec in data["records"].items():
        assert rec["stationId"] == sid
        assert isinstance(rec["primary"], str) and rec["primary"]
        assert isinstance(rec["chain"], list)
        assert all(isinstance(x, str) and x.strip() for x in rec["chain"])
        assert rec["classification"] in valid_cls


def test_render_routing_panel_is_defensive():
    """The renderer's tab-crash mitigation (the F3 fallback) cannot be silently
    deleted: the source normalizes inputs, guards the chain with Array.isArray,
    and carries the 'Routing metadata unavailable' fallback. (Runtime behaviour is
    proven by the Step-9 Playwright malformed-input smoke — the repo has no CI
    JS-execution harness.)"""
    text = _html()
    i = text.find("function renderRoutingPanel(")
    assert i > 0, "renderRoutingPanel missing"
    body = text[i:i + 1400]
    assert "Routing metadata unavailable" in body, "defensive fallback removed"
    assert "Array.isArray(rec.chain)" in body, "chain no longer array-guarded"
    assert "typeof rec" in body, "input normalization removed"


def test_seat_panel_absent_on_old_rev():
    """Seat routing is a current-state overlay: the panel + badge render only for
    the newest rev, so a historical-rev view carries no routing annotation."""
    text = _html()
    i = text.find("function seatMapped(")
    assert i > 0, "seatMapped gate missing"
    body = text[i:i + 500]
    assert "r.ver !== w.revs[0]" in body, "seat panel not gated to the newest rev"


def _wf2_newest_rev_steps_slice(text):
    """The exact substring of WF2's newest-rev steps array (NOT the rest of the file —
    a station id from an old rev or another workflow must not satisfy the join guard)."""
    wf2 = text[re.search(r'\bwf2:\s*\{', text).start():]
    newest = re.search(r'revs:\["([0-9.]+)"', wf2).group(1)
    v = wf2.find('"%s": {' % newest)
    assert v > 0, f"wf2 versions[{newest}] not found"
    rest = wf2[v + len(newest) + 4:]
    nxt = re.search(r'"\d+\.\d+\.\d+": \{', rest)  # the next version entry
    return wf2[v: v + len(newest) + 4 + nxt.start()] if nxt else wf2[v:]


def test_manifest_station_ids_match_diagram():
    """The join is DATA.seatRouting.records[s.id]; a manifest id that diverges from a
    real NEWEST-WF2-REV station id silently renders nothing while every other test stays
    green. Pin the manifest ids against ONLY the newest WF2 rev's steps — not the whole
    file (which would let an old-rev or other-workflow id falsely satisfy this)."""
    slice_ = _wf2_newest_rev_steps_slice(_html())
    station_ids = set(re.findall(r'\{id:"([^"]+)",', slice_))
    assert station_ids, "no WF2 newest-rev station ids parsed"
    manifest_ids = {sid for sid, *_ in _dsd.PHASE_SEAT_MAP}
    assert manifest_ids <= station_ids, (
        f"manifest station ids not in the newest WF2 rev: {manifest_ids - station_ids}")


def test_seat_panel_scoped_to_wf2():
    """Station ids are reused across workflows; the seat panel/badge must be scoped to
    WF2 or WF2's seat data leaks onto WF1/WF3/WF5/WF14/WF17 stations sharing an id."""
    text = _html()
    i = text.find("function seatMapped(")
    assert i > 0, "seatMapped gate missing"
    body = text[i:i + 500]
    assert "r.wf !== 'wf2'" in body, "seat routing not scoped to WF2"
    assert "mappedStationIds" in body, "mapped-set membership not enforced"
