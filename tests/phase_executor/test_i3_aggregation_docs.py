"""#469 W6 Task 4 (I3, adversarial H3) — the I3 contribution is DOCUMENTED join/reference
semantics only. This drift-guard pins the README section that documents the join keys + work_product
refs, and asserts #469 introduces NO seat-outcomes sidecar/schema/file/append helper (that is #473).
Section-sliced anchor (not a whole-corpus regex), per the repo drift-guard convention."""
from __future__ import annotations

import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
PKG_SRC = REPO_ROOT / "phase_executor" / "src" / "phase_executor"
README = REPO_ROOT / "phase_executor" / "README.md"
SCHEMA_DIR = PKG_SRC / "schemas"


def _i3_section() -> str:
    """The README's I3 aggregation section (from its header to the next `## ` header)."""
    text = README.read_text(encoding="utf-8")
    start = text.index("## Observation telemetry aggregation")
    rest = text[start + len("## Observation telemetry aggregation"):]
    nxt = rest.find("\n## ")
    return text[start:] if nxt == -1 else text[start:start + len("## Observation telemetry aggregation") + nxt]


def test_i3_join_and_reference_fields_documented():
    """The documented join keys (run_id, seat, model, issue) + work_product refs are present."""
    section = _i3_section()
    for token in ("run_id", "seat", "model", "issue", "work_product"):
        assert token in section, f"I3 join/reference field {token!r} not documented"


def test_i3_sidecar_deferred_to_473():
    """The seat-outcomes sidecar is documented as DEFERRED to #473 — the H3 scope boundary."""
    section = _i3_section()
    assert "seat-outcomes" in section
    assert "#473" in section
    assert "DEFERRED" in section


def test_469_introduces_no_seat_outcomes_sidecar_schema():
    """No seat-outcomes schema artifact ships in #469 (the sidecar's row schema is #473's)."""
    assert not list(SCHEMA_DIR.glob("*seat-outcome*")), "unexpected seat-outcomes schema in #469"
    assert not list(SCHEMA_DIR.glob("*seat_outcome*"))


def test_469_introduces_no_seat_outcomes_writer_or_file():
    """No append helper / writer for a seat-outcomes sidecar, and no committed sidecar file — the
    aggregation code is #473's. Scoped to package SOURCE (a `def` naming seat_outcome), so the
    README's documentation of the concept does not false-positive."""
    writer = re.compile(r"def\s+\w*seat[_-]?outcome", re.IGNORECASE)
    for py in PKG_SRC.rglob("*.py"):
        assert not writer.search(py.read_text(encoding="utf-8")), f"seat-outcomes writer in {py.name}"
    for pat in ("*seat-outcomes*", "*seat_outcomes*"):
        assert not list(PKG_SRC.rglob(pat)), f"unexpected seat-outcomes artifact matching {pat}"
