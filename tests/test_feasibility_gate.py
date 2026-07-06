"""Platform / external-dependency feasibility gate (#226).

Two layers:
1. Unit tests for the `plan_lib` feasibility-note validator — the mechanical Step-4
   gate that makes an *omitted* declaration fail closed (the silent-gap class #226
   exists to kill), rejects `assumed`, weak evidence, and a `fail-silent` API with
   no surfacing.
2. Corpus drift guards (added in Task 4) pin the prose requirement + the WF5 lens so
   a later edit can't silently drop them.

Design: docs/design/2026-07-06-platform-feasibility-gate.md
"""
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "hooks"))

import plan_lib  # noqa: E402


# --------------------------------------------------------------------------
# parse_feasibility_block — grammar
# --------------------------------------------------------------------------

def test_no_declaration_returns_none():
    """No `platform_apis:` line at all -> None, so the caller can distinguish
    'declaration absent' (an omission -> Step-4 error) from 'declared none'."""
    decl = plan_lib.parse_feasibility_block("## Design\nSome prose, no declaration.\n")
    assert decl is None


def test_declared_none():
    decl = plan_lib.parse_feasibility_block("## Platform / external dependencies\nplatform_apis: none\n")
    assert decl is not None
    assert decl.present is True
    assert decl.none is True
    assert decl.apis == ()  # frozen dataclass -> tuple, like Task.files


def test_declared_none_case_and_spacing_insensitive():
    for line in ("platform_apis:none", "platform_apis:   NONE  ", "  platform_apis: None"):
        decl = plan_lib.parse_feasibility_block(line + "\n")
        assert decl is not None and decl.none is True, line


def test_single_verified_block_emdash():
    text = (
        "platform_apis:\n"
        "- api: window.setSize on overlay window\n"
        "  feasibility: verified via existing-call-site — src/main.ts:42\n"
        "  failure: fail-loud\n"
    )
    decl = plan_lib.parse_feasibility_block(text)
    assert decl is not None and decl.none is False
    assert len(decl.apis) == 1
    a = decl.apis[0]
    assert a.api == "window.setSize on overlay window"
    assert a.status == "verified"
    assert a.kind == "existing-call-site"
    assert a.citation == "src/main.ts:42"
    assert a.failure == "fail-loud"
    assert a.surface is None


def test_separator_tolerance_hyphen_and_colon():
    """The `via <kind> <sep> <citation>` split must accept em-dash, hyphen, and colon
    (the WF3 drift guard hit exactly this en/em-dash bug)."""
    for sep in ("—", "-", ":"):
        text = (
            "platform_apis:\n"
            f"- api: X\n  feasibility: verified via docs {sep} https://d/ok\n  failure: fail-loud\n"
        )
        decl = plan_lib.parse_feasibility_block(text)
        a = decl.apis[0]
        assert a.kind == "docs", sep
        assert a.citation == "https://d/ok", sep


def test_multi_api_block_scoping():
    """Each `- api:` opens a block; fields belong to the block they follow."""
    text = (
        "platform_apis:\n"
        "- api: A\n  feasibility: verified via spike — result-1\n  failure: fail-silent\n  surface: assert log — a.ts\n"
        "- api: B\n  feasibility: verified via capabilities-file — cap.json\n  failure: fail-loud\n"
        "\n## Next section\nunrelated\n"
    )
    decl = plan_lib.parse_feasibility_block(text)
    assert len(decl.apis) == 2
    assert decl.apis[0].api == "A" and decl.apis[0].failure == "fail-silent" and decl.apis[0].surface == "assert log — a.ts"
    assert decl.apis[1].api == "B" and decl.apis[1].kind == "capabilities-file"


def test_fenced_contract_example_is_skipped():
    """A design doc that QUOTES the contract in a ``` code fence (this feature's own
    design doc does) must not have the example parsed as a real declaration — the
    real prose declaration below the fence is what counts. Regression: dogfooding."""
    text = (
        "## Platform / external dependencies\n"
        "```md\n"
        "platform_apis:\n"
        "- api: <exact API> on <exact object/runtime surface>\n"
        "  feasibility: verified via <capabilities-file|docs> — <citation>\n"
        "  failure: fail-loud | fail-silent\n"
        "```\n"
        "platform_apis: none\n"
    )
    decl = plan_lib.parse_feasibility_block(text)
    assert decl is not None and decl.none is True, "fenced example must be skipped"
    ok, errors = plan_lib.assert_feasibility_declared(decl)
    assert ok is True, errors


def test_assumed_status_parsed():
    text = "platform_apis:\n- api: Z\n  feasibility: assumed\n  failure: fail-loud\n"
    decl = plan_lib.parse_feasibility_block(text)
    assert decl.apis[0].status == "assumed"


# --------------------------------------------------------------------------
# assert_feasibility_declared — the mechanical gate
# --------------------------------------------------------------------------

def test_absent_declaration_is_error():
    """The Adv#1/#2 hole: an omitted declaration must FAIL closed, not pass."""
    ok, errors = plan_lib.assert_feasibility_declared(None)
    assert ok is False
    assert any("platform_apis" in e.lower() for e in errors)


def test_declared_none_passes():
    decl = plan_lib.parse_feasibility_block("platform_apis: none\n")
    ok, errors = plan_lib.assert_feasibility_declared(decl)
    assert ok is True and errors == []


def test_verified_block_passes():
    decl = plan_lib.parse_feasibility_block(
        "platform_apis:\n- api: A\n  feasibility: verified via spike — ran it, works\n  failure: fail-loud\n"
    )
    ok, errors = plan_lib.assert_feasibility_declared(decl)
    assert ok is True, errors


def test_assumed_blocks():
    decl = plan_lib.parse_feasibility_block(
        "platform_apis:\n- api: A\n  feasibility: assumed\n  failure: fail-loud\n"
    )
    ok, errors = plan_lib.assert_feasibility_declared(decl)
    assert ok is False
    assert any("assumed" in e.lower() for e in errors)


def test_bad_evidence_kind_blocks():
    decl = plan_lib.parse_feasibility_block(
        "platform_apis:\n- api: A\n  feasibility: verified via vibes — trust me\n  failure: fail-loud\n"
    )
    ok, errors = plan_lib.assert_feasibility_declared(decl)
    assert ok is False
    assert any("vibes" in e or "evidence" in e.lower() or "kind" in e.lower() for e in errors)


def test_empty_citation_blocks():
    """`verified via docs` with no citation must fail (peer graft)."""
    decl = plan_lib.parse_feasibility_block(
        "platform_apis:\n- api: A\n  feasibility: verified via docs\n  failure: fail-loud\n"
    )
    ok, errors = plan_lib.assert_feasibility_declared(decl)
    assert ok is False
    assert any("citation" in e.lower() or "evidence" in e.lower() for e in errors)


def test_fail_silent_without_surface_blocks():
    """AC4 made mechanical: a fail-silent API with no surfacing is an error."""
    decl = plan_lib.parse_feasibility_block(
        "platform_apis:\n- api: A\n  feasibility: verified via spike — ok\n  failure: fail-silent\n"
    )
    ok, errors = plan_lib.assert_feasibility_declared(decl)
    assert ok is False
    assert any("surface" in e.lower() for e in errors)


def test_fail_silent_with_surface_passes():
    decl = plan_lib.parse_feasibility_block(
        "platform_apis:\n- api: A\n  feasibility: verified via spike — ok\n  failure: fail-silent\n  surface: assert size changed — overlay.ts\n"
    )
    ok, errors = plan_lib.assert_feasibility_declared(decl)
    assert ok is True, errors


def test_missing_failure_classification_blocks():
    decl = plan_lib.parse_feasibility_block(
        "platform_apis:\n- api: A\n  feasibility: verified via spike — ok\n"
    )
    ok, errors = plan_lib.assert_feasibility_declared(decl)
    assert ok is False
    assert any("failure" in e.lower() for e in errors)


def test_present_but_empty_declaration_blocks():
    """`platform_apis:` present, not `none`, but no api blocks -> malformed, error."""
    decl = plan_lib.parse_feasibility_block("platform_apis:\n\n## Next\n")
    ok, errors = plan_lib.assert_feasibility_declared(decl)
    assert ok is False
