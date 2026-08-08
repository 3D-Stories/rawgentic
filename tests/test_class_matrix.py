"""The per-class gate matrix (#1002) — its typed rows, its renderer, and its drift guard.

The matrix says, per task class, which WF2 steps run FULL and which run COLLAPSED. The whole
point of the design is what it CANNOT express: there is no `SKIP` value in the step-row enum, so
no class can declare that a mandatory step does not run. That guarantee is asserted here
structurally, against the enum, rather than by reading a sentence out of prose.

Two facts about the row kinds, because conflating them produced a Critical finding at the design
gate (2026-08-08). A STEP row governs ceremony — whether Step 3 runs a multi-approach brainstorm
or a brief note. An ARTIFACT row governs whether a separate file is committed. They are strictly
orthogonal: the earlier design defined COLLAPSED as "no separate committed artifact", which made
`internal` unimplementable because its Step-3 row read COLLAPSED while its artifact row read KEEP.

`internal` keeping its committed design doc while collapsing Step 3's ceremony is therefore the
regression test for that Critical, and it is the first test below.
"""
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "hooks"))

import plan_lib  # noqa: E402

CLASSES = ("disposable", "internal", "production")


# ---------------------------------------------------------------------------
# The source of truth and its typed rows


def test_every_class_has_a_value_in_every_row() -> None:
    """'Every cell is filled' is AC1's demand, so a hole must fail rather than render blank."""
    for row_id, (kind, label, values) in plan_lib.CLASS_MATRIX.items():
        assert set(values) == set(CLASSES), f"{row_id} ({label}) is missing a class: {values}"
        for cls, value in values.items():
            assert isinstance(value, str) and value, f"{row_id}/{cls} is empty"
        assert kind in plan_lib.CLASS_MATRIX_ROW_KINDS, f"{row_id} has unknown kind {kind!r}"


def test_the_step_row_enum_cannot_express_skipping() -> None:
    """THE guarantee. Not 'the doc says no class skips a step' — the vocabulary has no way to say
    it, so a future editor cannot add one by writing prose."""
    step_enum = plan_lib.CLASS_MATRIX_ROW_KINDS["step"]
    assert step_enum == ("FULL", "COLLAPSED", "n/a"), step_enum
    for word in ("SKIP", "skip", "DROP", "off", "NONE"):
        assert word not in step_enum


def test_every_cell_is_legal_for_its_row_kind() -> None:
    """DROP and off are legal in their OWN kinds. What must never happen is a step row taking
    one, because that is skipping a step by another name."""
    for row_id, (kind, label, values) in plan_lib.CLASS_MATRIX.items():
        if kind == "count":
            for cls, value in values.items():
                assert value == "ALL 4" or value.isdigit(), f"{row_id}/{cls} = {value!r}"
            continue
        allowed = plan_lib.CLASS_MATRIX_ROW_KINDS[kind]
        for cls, value in values.items():
            assert value in allowed, f"{row_id}/{cls} = {value!r} is not in {kind} enum {allowed}"


def test_internal_collapses_step_three_and_still_keeps_its_artifact() -> None:
    """Regression for the design gate's Critical (2026-08-08): the two rows are orthogonal, so
    this combination must be expressible. It previously was not."""
    step_kind, _, step_values = plan_lib.CLASS_MATRIX["3"]
    art_kind, _, art_values = plan_lib.CLASS_MATRIX["3.artifact"]
    assert step_kind == "step" and art_kind == "artifact"
    assert step_values["internal"] == "COLLAPSED"
    assert art_values["internal"] == "KEEP"


def test_the_never_reducible_rows_are_full_for_every_class() -> None:
    """Step 8 red-before-green, Step 8a, Step 11, Step 11.5. A class scales demands, never these."""
    for row_id in plan_lib.CLASS_MATRIX_NEVER_REDUCIBLE:
        kind, label, values = plan_lib.CLASS_MATRIX[row_id]
        assert kind == "step", f"{row_id} must be a step row to be never-reducible"
        for cls in CLASSES:
            assert values[cls] == "FULL", f"{label} is {values[cls]} for {cls}"


def test_reviewer_count_scales_but_lens_coverage_does_not() -> None:
    """The one place a class scales a demand, and the row directly beneath it that makes that
    safe. Coverage is over the UNION of the wave (owner decision D305), so ALL 4 is true in every
    column: production covers four across two briefs, and the single-reviewer classes have a
    one-brief wave."""
    _, _, counts = plan_lib.CLASS_MATRIX["11.count"]
    assert counts == {"disposable": "1", "internal": "1", "production": "2"}
    _, label, coverage = plan_lib.CLASS_MATRIX["11.lenses"]
    assert all(v == "ALL 4" for v in coverage.values()), coverage
    assert "union" in label.lower(), "the row must name the union reading, not 'per reviewer'"


def test_excluded_steps_are_declared_with_a_reason() -> None:
    """Steps 10, 14 and 15 carry no row. Silence would read as 'unclassified'; the exclusion list
    makes it a declared decision the drift test can check."""
    assert set(plan_lib.CLASS_MATRIX_EXCLUDED_STEPS) == {"10", "14", "15"}
    for step, why in plan_lib.CLASS_MATRIX_EXCLUDED_STEPS.items():
        assert isinstance(why, str) and why.strip(), f"step {step} excluded with no reason"
    for step in plan_lib.CLASS_MATRIX_EXCLUDED_STEPS:
        assert step not in plan_lib.CLASS_MATRIX


# ---------------------------------------------------------------------------
# The renderer


def test_render_emits_a_table_and_the_exclusion_list() -> None:
    out = plan_lib.render_class_matrix()
    assert out.lstrip().startswith("| Row |"), out[:120]
    for cls in CLASSES:
        assert cls in out
    for step in plan_lib.CLASS_MATRIX_EXCLUDED_STEPS:
        assert f"Step {step}" in out


def test_render_refuses_an_illegal_cell_rather_than_emitting_it() -> None:
    """Fail at render time, not in review. A bad cell must never reach prose in the first place."""
    broken = dict(plan_lib.CLASS_MATRIX)
    kind, label, values = broken["3"]
    broken["3"] = (kind, label, dict(values, disposable="SKIP"))
    with pytest.raises(plan_lib.PlanFormatError, match="SKIP"):
        plan_lib.render_class_matrix(matrix=broken)


def test_the_cli_prints_the_same_table() -> None:
    """Black-box via subprocess, per docs/testing.md."""
    proc = subprocess.run(
        [sys.executable, "hooks/plan_lib.py", "render-class-matrix", "--project-root", "."],
        cwd=REPO_ROOT, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == plan_lib.render_class_matrix().strip()


# ---------------------------------------------------------------------------
# The drift guard — the table in prose must equal the table in code


MATRIX_CALL_SITES = (
    Path("docs/planning/2026-08-08-923-lite-lane-and-reservations-design.md"),
    Path("skills/implement-feature/references/class-gate-matrix.md"),
)


def _table_after(text: str, header: str) -> str:
    """Slice the markdown table that follows a canonical header.

    Anchored to ONE header in ONE file rather than a whole-corpus regex, per this repo's
    drift-guard convention (CLAUDE.md §4 mistake 6): a corpus-wide pattern false-positives on any
    stray pipe character and breaks on every new occurrence.
    """
    idx = text.index(header)
    lines = text[idx:].splitlines()
    table = [ln for ln in lines if ln.lstrip().startswith("|")]
    assert table, f"no table found after {header!r}"
    # stop at the first blank-line break after the table starts
    out, started = [], False
    for ln in lines:
        if ln.lstrip().startswith("|"):
            started, _ = True, out.append(ln)
        elif started:
            break
    return "\n".join(_norm(ln) for ln in out)


def _norm(line: str) -> str:
    return re.sub(r"\s+", " ", line.replace("&nbsp;", " ")).strip()


@pytest.mark.parametrize("rel", MATRIX_CALL_SITES, ids=lambda p: p.name)
def test_the_documented_matrix_equals_the_rendered_one(rel: Path) -> None:
    text = (REPO_ROOT / rel).read_text(encoding="utf-8")
    documented = _table_after(text, plan_lib.CLASS_MATRIX_DOC_HEADER)
    rendered = "\n".join(_norm(ln) for ln in plan_lib.render_class_matrix().splitlines()
                         if ln.lstrip().startswith("|"))
    assert documented == rendered, f"{rel} has drifted from render_class_matrix()"


@pytest.mark.parametrize("rel", MATRIX_CALL_SITES, ids=lambda p: p.name)
def test_no_call_site_states_a_per_reviewer_lens_requirement(rel: Path) -> None:
    """B6's ambiguity must not creep back in prose (owner decision D305). The design doc records
    the REJECTED reading inside one clearly-marked decision block, which is quoted history — so
    the check is scoped to the normative text before it."""
    text = (REPO_ROOT / rel).read_text(encoding="utf-8")
    marker = "**B6, decided"
    normative = text.split(marker)[0] if marker in text else text
    assert "lenses per reviewer" not in normative, f"{rel} still states the per-reviewer reading"
