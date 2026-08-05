"""End-to-end task-class integration: snapshot -> resolved class -> real prompt (#761).

This file exists because builder unit tests are VACUOUS on their own. That was
constraint C1/C5 (pass-5 and pass-6 High, terminal ADOPTED dispositions
`d-761-5-1-62ee` and `d-761-6-5-b362`): `build_prompt`/`build_consult_prompt`
gained a `task_class` argument with a default, so every direct builder test can
pass while REAL workflow prompts carry no class at all — nothing named the call
site that reads `task_class.json` and supplies the flag. Two halves are needed,
and both are pinned here:

1. **The machine half** — seed a snapshot, read it back through the SAME CLI the
   prose calls (`task_class_lib.py read --issue N`), drive the runner's own
   prompt-construction path with the result, and assert both generated prompts
   carry the SNAPSHOT's class and NOT the project default. Non-vacuity comes from
   making those two values differ: any wiring that silently fell back to the
   config default would render `disposable` and fail.
2. **The prose half** — the handoff lives in markdown, so no Python test can
   execute it. What CAN be pinned is that the WF5/WF13 skill files actually carry
   the read-then-pass instruction. A missing or reworded handoff leaves real
   prompts class-less, which is exactly the vacuity above.

Design: `docs/planning/2026-08-04-761-proportionality-contract-design.md`
(injection-points table, and "Integration test, not just builder unit tests" —
it stops BELOW egress, so no backend is called: the injected runner captures the
composed prompt and returns a valid body).
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
HOOKS_DIR = REPO_ROOT / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

import adversarial_review_lib as arl  # noqa: E402
import review_runner as rr  # noqa: E402
import task_class_lib as tcl  # noqa: E402

TCL_CLI = str(HOOKS_DIR / "task_class_lib.py")

# The two values are deliberately DIFFERENT so a fallback cannot pass silently.
SNAPSHOT_CLASS = "internal"
CONFIG_DEFAULT = "disposable"
ISSUE = 761

VALID_FINDINGS_BODY = json.dumps({
    "summary": "one real defect",
    "findings": [{
        "evidence": "the guard returns True on error",
        "severity": "High", "category": "correctness", "confidence": 0.85,
        "description": "fail-open guard", "recommendation": "return False",
        "ambiguity_flag": None, "ambiguity_reason": None,
        "location": "hooks/x.py:10", "loopback_class": "design-flaw",
    }],
})
VALID_PROPOSAL_BODY = json.dumps({
    "approach": "small module", "key_decisions": ["k1"],
    "risks": ["r1"], "sketch": "def f(): ...",
})


@pytest.fixture()
def seeded(tmp_path):
    """A project whose config default DISAGREES with its snapshotted class."""
    root = tmp_path / "proj"
    root.mkdir()
    (root / "artifact.md").write_text("# Design\n\nA small design document.\n")
    (root / ".rawgentic.json").write_text(json.dumps({
        "version": 1, "project": {"name": "t"}, tcl.CONFIG_KEY: CONFIG_DEFAULT,
    }))
    snapshot = root / "claude_docs" / ".wf2-state" / str(ISSUE) / "task_class.json"
    tcl.write_snapshot(str(snapshot), {
        "task_class": SNAPSHOT_CLASS, "provenance": "issue_body",
        "issue": ISSUE, "resolved_at": "2026-08-05T00:00:00Z",
    })
    return root


def _read_class_via_cli(project_root, issue=ISSUE):
    """Exactly what the WF5/WF13 prose does to obtain the value it passes."""
    argv = [sys.executable, TCL_CLI, "read", "--project-root", str(project_root)]
    if issue is not None:
        argv += ["--issue", str(issue)]
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


class _PromptCapture:
    """An injected runner: captures the composed prompt, never calls a backend."""

    def __init__(self, body):
        self.body = body
        self.prompts = []

    def __call__(self, cmd, **kwargs):
        self.prompts.append(kwargs.get("input", ""))
        out = cmd[cmd.index("-o") + 1]
        Path(out).write_text(self.body)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    @property
    def prompt(self):
        assert len(self.prompts) == 1, f"expected 1 egress, got {len(self.prompts)}"
        return self.prompts[0]


# ===========================================================================
# 1. The machine half — snapshot wins over the config default, in a REAL prompt
# ===========================================================================

class TestSnapshotReachesTheRealPrompt:
    def test_cli_read_returns_the_snapshot_not_the_config_default(self, seeded):
        got = _read_class_via_cli(seeded)
        assert got["task_class"] == SNAPSHOT_CLASS
        assert got["provenance"] == "issue_body"
        assert got["issue_scoped"] is True

    def test_review_prompt_carries_the_snapshot_class(self, seeded):
        resolved = _read_class_via_cli(seeded)["task_class"]
        capture = _PromptCapture(VALID_FINDINGS_BODY)
        res = rr.run_review(
            verb="review-artifact", artifact=str(seeded / "artifact.md"),
            artifact_type="design", author_model="claude-fable-5",
            reviewer="gpt-5.5-codex", project_root=str(seeded),
            out_path=str(seeded / "r.json"),
            task_class=resolved, issue=ISSUE, runner=capture,
        )
        assert res["status"] == "success", res["error_detail"]
        assert res["task_class"] == SNAPSHOT_CLASS
        assert f"TASK CLASS: {SNAPSHOT_CLASS}" in capture.prompt
        assert CONFIG_DEFAULT not in capture.prompt

    def test_consult_prompt_carries_the_snapshot_class(self, seeded):
        resolved = _read_class_via_cli(seeded)["task_class"]
        capture = _PromptCapture(VALID_PROPOSAL_BODY)
        res = rr.run_review(
            verb="consult", artifact=str(seeded / "artifact.md"),
            author_model=None, reviewer="gpt-5.5-codex",
            project_root=str(seeded), out_path=str(seeded / "r.json"),
            task_class=resolved, issue=ISSUE, runner=capture,
        )
        assert res["status"] == "success", res["error_detail"]
        assert res["task_class"] == SNAPSHOT_CLASS
        assert f"TASK CLASS: {SNAPSHOT_CLASS}" in capture.prompt
        assert CONFIG_DEFAULT not in capture.prompt

    def test_issueless_read_falls_back_to_the_config_default(self, seeded):
        """The legitimate issue-less path — and it must say it is not scoped."""
        got = _read_class_via_cli(seeded, issue=None)
        assert got["task_class"] == CONFIG_DEFAULT
        assert got["issue_scoped"] is False

    def test_a_run_that_forgets_the_class_cannot_reach_a_prompt(self, seeded):
        """C7 end to end: the omission REFUSES instead of rendering a default."""
        capture = _PromptCapture(VALID_FINDINGS_BODY)
        res = rr.run_review(
            verb="review-artifact", artifact=str(seeded / "artifact.md"),
            artifact_type="design", author_model="claude-fable-5",
            reviewer="gpt-5.5-codex", project_root=str(seeded),
            out_path=str(seeded / "r.json"),
            task_class=None, issue=ISSUE, runner=capture,
        )
        assert res["status"] == "refused"
        assert res["error_class"] == "invalid_input"
        assert capture.prompts == [], "refusal must precede egress"

    def test_the_two_enums_have_not_drifted(self):
        """`arl` mirrors the enum rather than importing it (stdlib-only module)."""
        assert arl.TASK_CLASSES == tcl.TASK_CLASSES
        assert arl.DEFAULT_TASK_CLASS == tcl.DEFAULT_CLASS


# ===========================================================================
# 2. The prose half (C1) — the handoff markdown must actually be there
# ===========================================================================

class TestProseHandoffExists:
    """A missing handoff is invisible to every Python test above."""

    @pytest.mark.parametrize("skill", ["adversarial-review", "peer-consult"])
    def test_skill_reads_the_snapshot_and_passes_both_flags(self, skill):
        text = (REPO_ROOT / "skills" / skill / "SKILL.md").read_text()
        assert "task_class_lib.py read" in text, (
            f"{skill} must RESOLVE the class from the snapshot, not guess it")
        assert "--task-class" in text, f"{skill} must pass --task-class"
        assert "--issue" in text, (
            f"{skill} must pass --issue when an issue is in scope, so an "
            f"issue-scoped review cannot silently take the project default")

    def test_wf2_step_01_resolves_and_snapshots(self):
        text = (REPO_ROOT / "skills" / "implement-feature" / "references"
                / "step-01.md").read_text()
        assert "task_class_lib.py resolve" in text
        assert "task_class.json" in text

    def test_wf1_draft_body_carries_the_canonical_line(self):
        text = (REPO_ROOT / "skills" / "create-issue" / "SKILL.md").read_text()
        assert "Task class:" in text
        for value in tcl.TASK_CLASSES:
            assert value in text, f"the draft contract must document {value!r}"

    def test_step_14_cleanup_stays_directory_wide(self):
        """C7: the snapshot's post-merge removal is STRUCTURAL, not a file list.

        `task_class.json` is never named in any cleanup step — it is removed because
        it lives inside `claude_docs/.wf2-state/<issue>/`, which Step 14 deletes
        whole. That is deliberate (nothing to keep in sync), but it means narrowing
        the cleanup to an explicit file list would silently strand snapshots and
        leave a later re-run adopting a class from a merged issue. So pin the path.
        """
        text = (REPO_ROOT / "skills" / "implement-feature" / "references"
                / "step-14.md").read_text()
        assert "claude_docs/.wf2-state/<issue>/" in text, (
            "Step 14 no longer names the whole .wf2-state/<issue>/ directory — if "
            "cleanup became a file list, add task_class.json to it explicitly")
