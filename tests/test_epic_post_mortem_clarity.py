"""Drift guards for the WF19 epic post-mortem skill (#508, epic #509).

Pins the honesty contract: timing consumed from #506's telemetry when usable,
visible degradation (never fabricated splits), idle bucketed separately, the
WF14 batch assessment linked — never duplicated — and the unicode-bar chart
floor named as a deliberate presentation choice. Section/file-sliced, one
canonical sentence per guard, whitespace-normalized (repo mistake #6).
"""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILL = REPO_ROOT / "skills" / "epic-post-mortem" / "SKILL.md"


def _text() -> str:
    return " ".join(SKILL.read_text(encoding="utf-8").split())


class TestEpicPostMortemContract:
    def test_children_derived_from_task_list_never_deps(self):
        s = _text()
        assert "BOTH `- [ ] #N` and `- [x] #N`" in s
        assert "never dependency parsing" in s

    def test_timing_degrades_visibly_never_fabricated(self):
        s = _text()
        assert ("degrade VISIBLY to the per-child total (`usage.wall_clock_s`)"
                in s), "records without usable timing must fall back visibly (#508 AC2)"
        assert "never fabricated splits" in s

    def test_idle_bucketed_separately(self):
        assert ("`phases.idle` is bucketed separately — stalled time never "
                "inflates a phase bar") in _text(), "#508 AC2 idle handling"

    def test_wf14_linked_never_duplicated(self):
        s = _text()
        assert "--epic" in s and "never duplicates the rubric" in s, (
            "the machinery assessment is WF14 batch's job — link it (#508 AC4)")

    def test_html_built_on_vendored_template(self):
        """Owner decision 2026-07-19 (epic #509): the generic report render was
        rejected — the html is a designed timing page on the vendored template,
        and the template ships with the skill."""
        s = _text()
        assert "designed timing page built on the vendored template" in s
        assert "references/artifact-template.html" in s
        assert (SKILL.parent / "references" / "artifact-template.html").is_file(), (
            "the vendored template must ship with the skill")
        assert "supersedes `render_artifact.py` for THIS skill's html only" in s, (
            "the house-renderer exception is scoped to this skill, never general")

    def test_page_script_is_dom_builder_only(self):
        s = _text()
        assert "no `innerHTML`" in s, (
            "the repo security-hook contract applies to the designed page")

    def test_stall_flagged_beside_row_not_inside_segment(self):
        assert "flag a stall beside its row, never inside a phase segment" in _text(), (
            "idle honesty extends to the visual: a stall is never drawn as phase time")

    def test_records_resolved_via_find(self):
        assert "work_summary.py find --issue" in _text(), (
            "per-child record resolution goes through find (#508 AC1)")
