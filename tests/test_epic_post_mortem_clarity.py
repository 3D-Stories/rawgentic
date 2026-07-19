"""Drift guards for the WF16 epic post-mortem skill (#508, epic #509).

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

    def test_render_via_render_artifact_report_style(self):
        s = _text()
        assert "render_artifact.py" in s and "--style report" in s

    def test_unicode_bar_floor_named(self):
        assert ("unicode-block bars are the deliberate presentation floor" in
                _text()), "the chart form is a named choice with an upgrade path"

    def test_records_resolved_via_find(self):
        assert "work_summary.py find --issue" in _text(), (
            "per-child record resolution goes through find (#508 AC1)")
