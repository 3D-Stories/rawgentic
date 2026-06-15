"""Tests for hooks/resume_lib.py — the WF2 resumption step-detection cascade.

PR 4b extracts the priority-ordered resume cascade (previously hand-applied in
SKILL.md prose) into a single tested decision function + CLI. The orchestrator
gathers the facts (git/gh for PR + branch, session notes for design/issue/test
status) and `detect-step` applies the canonical precedence, so the resume target
can no longer drift from the order the prose intended.
"""
import sys
from pathlib import Path

import pytest

# Import Python helper from hooks/
HOOKS_DIR = Path(__file__).resolve().parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

RESUME_CLI = HOOKS_DIR / "resume_lib.py"


def _run_cli(*args, timeout=10):
    """Invoke resume_lib.py as a CLI subprocess (exercises argparse + exit codes
    exactly as the SKILL.md Bash block does)."""
    import subprocess
    result = subprocess.run(
        ["python3", str(RESUME_CLI), *args],
        capture_output=True, text=True, timeout=timeout,
    )
    return result.stdout, result.stderr, result.returncode


class TestDetectResumeStepFunction:
    """The pure decision function. Each (pr_state, branch_state, notes_state)
    maps to exactly one resume step; higher dimensions take precedence (a merged
    PR resumes at post-deploy even if a stale design doc also exists)."""

    # --- PR cascade (highest precedence) ---

    def test_pr_merged_resumes_post_deploy(self):
        from resume_lib import detect_resume_step
        assert detect_resume_step("merged", "none", "none") == 15

    def test_pr_ready_to_merge_resumes_merge_deploy(self):
        from resume_lib import detect_resume_step
        assert detect_resume_step("ready-to-merge", "none", "none") == 14

    def test_pr_open_resumes_ci_verification(self):
        from resume_lib import detect_resume_step
        assert detect_resume_step("open", "none", "none") == 13

    # --- branch cascade (only when no PR) ---

    def test_branch_verified_resumes_code_review(self):
        from resume_lib import detect_resume_step
        assert detect_resume_step("none", "verified", "none") == 11

    def test_branch_changes_resumes_drift_check(self):
        from resume_lib import detect_resume_step
        assert detect_resume_step("none", "changes", "none") == 9

    def test_branch_empty_resumes_implementation(self):
        from resume_lib import detect_resume_step
        assert detect_resume_step("none", "empty", "none") == 8

    # --- notes cascade (only when no PR, no branch) ---

    def test_design_doc_resumes_create_plan(self):
        from resume_lib import detect_resume_step
        assert detect_resume_step("none", "none", "design-doc") == 5

    def test_issue_validated_resumes_analyze(self):
        from resume_lib import detect_resume_step
        assert detect_resume_step("none", "none", "issue-validated") == 2

    def test_nothing_starts_from_step_1(self):
        from resume_lib import detect_resume_step
        assert detect_resume_step("none", "none", "none") == 1

    # --- precedence: higher dimension wins over lower ---

    def test_pr_wins_over_branch_and_notes(self):
        from resume_lib import detect_resume_step
        # A merged PR with a still-present branch + stale design doc resumes at
        # post-deploy, NOT at code review or create-plan.
        assert detect_resume_step("merged", "verified", "design-doc") == 15

    def test_branch_wins_over_notes(self):
        from resume_lib import detect_resume_step
        assert detect_resume_step("none", "changes", "design-doc") == 9

    # --- completion-gate pre-check (rule 0) ---

    def test_markers_complete_gate_not_printed_runs_gate(self):
        from resume_lib import detect_resume_step
        # All step markers present but the completion gate never printed: run the
        # gate, do not redo Step 15.
        assert detect_resume_step(
            "merged", "none", "none",
            markers_complete=True, completion_gate_printed=False
        ) == "completion-gate"

    def test_markers_complete_gate_already_printed_falls_through(self):
        from resume_lib import detect_resume_step
        # Gate already printed → the workflow is genuinely done; fall through to
        # the normal cascade (no special completion-gate signal).
        assert detect_resume_step(
            "merged", "none", "none",
            markers_complete=True, completion_gate_printed=True
        ) == 15

    def test_markers_incomplete_ignores_gate_flag(self):
        from resume_lib import detect_resume_step
        assert detect_resume_step(
            "open", "none", "none",
            markers_complete=False, completion_gate_printed=False
        ) == 13

    # --- fail-closed on unknown state (importable function must not silently
    #     fall through to Step 1, which would restart in-flight work) ---

    @pytest.mark.parametrize("bad", ["", "bogus", "MERGED", "step15", None])
    def test_invalid_pr_state_raises(self, bad):
        from resume_lib import detect_resume_step
        with pytest.raises(ValueError):
            detect_resume_step(bad, "none", "none")

    @pytest.mark.parametrize("bad", ["", "bogus", "Verified", None])
    def test_invalid_branch_state_raises(self, bad):
        from resume_lib import detect_resume_step
        with pytest.raises(ValueError):
            detect_resume_step("none", bad, "none")

    @pytest.mark.parametrize("bad", ["", "bogus", "DesignDoc", None])
    def test_invalid_notes_state_raises(self, bad):
        from resume_lib import detect_resume_step
        with pytest.raises(ValueError):
            detect_resume_step("none", "none", bad)


class TestDetectStepCLI:
    """The `detect-step` subcommand the SKILL.md resumption block drives."""

    def test_prints_step_number(self):
        out, err, rc = _run_cli(
            "detect-step", "--pr-state", "merged",
            "--branch-state", "none", "--notes-state", "none",
        )
        assert rc == 0, err
        assert out.strip() == "15"

    def test_prints_completion_gate_sentinel(self):
        out, _, rc = _run_cli(
            "detect-step", "--pr-state", "merged",
            "--branch-state", "none", "--notes-state", "none",
            "--markers-complete", "true",
        )
        assert rc == 0
        assert out.strip() == "completion-gate"

    def test_completion_gate_suppressed_when_already_printed(self):
        out, _, rc = _run_cli(
            "detect-step", "--pr-state", "merged",
            "--branch-state", "none", "--notes-state", "none",
            "--markers-complete", "true", "--completion-gate-printed", "true",
        )
        assert rc == 0
        assert out.strip() == "15"

    @pytest.mark.parametrize("flag", ["--markers-complete", "--completion-gate-printed"])
    def test_marker_flags_reject_non_boolean_value(self, flag):
        """The marker flags take explicit true/false; a typo'd value must fail
        closed (argparse choices), not be silently coerced to false."""
        _, _, rc = _run_cli(
            "detect-step", "--pr-state", "merged",
            "--branch-state", "none", "--notes-state", "none",
            flag, "maybe",
        )
        assert rc != 0

    def test_default_no_state_is_step_1(self):
        out, _, rc = _run_cli(
            "detect-step", "--pr-state", "none",
            "--branch-state", "none", "--notes-state", "none",
        )
        assert rc == 0
        assert out.strip() == "1"

    @pytest.mark.parametrize("dim,flag", [
        ("--pr-state", "bogus"),
        ("--branch-state", "bogus"),
        ("--notes-state", "bogus"),
    ])
    def test_invalid_enum_fails_closed(self, dim, flag):
        """argparse `choices=` rejects an unrecognized state with a non-zero exit
        rather than silently mapping it to Step 1 (which would restart work)."""
        valid = {"--pr-state": "merged", "--branch-state": "none",
                 "--notes-state": "none"}
        valid[dim] = flag
        _, _, rc = _run_cli("detect-step",
                            "--pr-state", valid["--pr-state"],
                            "--branch-state", valid["--branch-state"],
                            "--notes-state", valid["--notes-state"])
        assert rc != 0

    def test_missing_required_flag_fails(self):
        _, _, rc = _run_cli("detect-step", "--pr-state", "merged")
        assert rc != 0

    def test_no_subcommand_fails(self):
        _, _, rc = _run_cli()
        assert rc != 0


class TestResumeSkillWiring:
    """Drift guard: the WF2 skill must drive resume step-detection through the
    `detect-step` CLI, not by hand-applying the numbered cascade in prose (which
    is exactly the ordering bug this extraction removes)."""

    SKILL_MD = (Path(__file__).resolve().parent.parent.parent
                / "skills" / "implement-feature" / "SKILL.md")

    def _resumption_block(self):
        content = self.SKILL_MD.read_text()
        start = content.index("<resumption-protocol>")
        end = content.index("</resumption-protocol>")
        return content[start:end]

    def test_skill_invokes_detect_step(self):
        block = self._resumption_block()
        assert "resume_lib.py detect-step" in block, (
            "the resumption protocol must call `resume_lib.py detect-step`; if you "
            "renamed the subcommand, update SKILL.md and this guard."
        )

    @pytest.mark.parametrize("flag", [
        "--pr-state", "--branch-state", "--notes-state",
        "--markers-complete", "--completion-gate-printed",
    ])
    def test_skill_passes_all_detect_step_flags(self, flag):
        """All five flags must appear in the block — in particular the two marker
        flags, so the completion-gate rule is part of the deterministic
        invocation rather than left to prose (review finding)."""
        assert flag in self._resumption_block(), (
            f"detect-step invocation in SKILL.md must pass {flag}"
        )

    def test_skill_does_not_duplicate_the_numbered_cascade(self):
        """The old hand-applied cascade ("PR exists and is merged? -> Resume at
        Step 15") must not coexist with the CLI — two sources of the ordering is
        how they drift apart."""
        block = self._resumption_block()
        assert "PR exists and is merged" not in block, (
            "the prose cascade was re-introduced alongside detect-step; the CLI "
            "is the single source of truth for the resume ordering."
        )
