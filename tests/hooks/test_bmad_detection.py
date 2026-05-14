"""Tests for BMAD detection and per-project skill preferences (issue #41).

Covers:
- Lint: all 10 workflow SKILL.md files contain the disabledSkills check
- Lint: 6 critique-invoking SKILL.md files contain the critiqueMethod check
- Schema: workspace JSON validates with bmadDetected and per-project disabledSkills
"""
import json
from pathlib import Path

import pytest

# Path to the skills/ directory, resolved relative to this file.
SKILLS_DIR = Path(__file__).resolve().parent.parent.parent / "skills"

# All 10 workflow skills that have <config-loading> preambles.
WORKFLOW_SKILLS = [
    "implement-feature",
    "fix-bug",
    "create-tests",
    "update-docs",
    "create-issue",
    "refactor",
    "security-audit",
    "incident",
    "update-deps",
    "optimize-perf",
]

# The 6 skills that invoke /reflexion:critique and need critiqueMethod check.
CRITIQUE_SKILLS = [
    "implement-feature",
    "create-issue",
    "refactor",
    "security-audit",
    "optimize-perf",
    "setup",
]

# The 4 BMAD-overlapping skills.
BMAD_OVERLAP_SKILLS = [
    "implement-feature",
    "fix-bug",
    "create-tests",
    "update-docs",
]


class TestDisabledSkillsPreamble:
    """Lint: all 10 workflow skills contain the disabledSkills check."""

    @pytest.mark.parametrize("skill_name", WORKFLOW_SKILLS)
    def test_skill_contains_disabled_skills_check(self, skill_name: str):
        skill_path = SKILLS_DIR / skill_name / "SKILL.md"
        assert skill_path.exists(), f"SKILL.md not found at {skill_path}"

        content = skill_path.read_text()
        assert "disabledSkills" in content, (
            f"{skill_name}/SKILL.md is missing the disabledSkills check "
            f"in its <config-loading> preamble"
        )

    @pytest.mark.parametrize("skill_name", WORKFLOW_SKILLS)
    def test_skill_check_is_in_config_loading(self, skill_name: str):
        """The disabledSkills check must be inside the <config-loading> block."""
        skill_path = SKILLS_DIR / skill_name / "SKILL.md"
        content = skill_path.read_text()

        # Find the config-loading block
        start = content.find("<config-loading>")
        end = content.find("</config-loading>")
        assert start != -1 and end != -1, (
            f"{skill_name}/SKILL.md is missing <config-loading> block"
        )

        config_block = content[start:end]
        assert "disabledSkills" in config_block, (
            f"{skill_name}/SKILL.md has disabledSkills outside <config-loading> "
            f"or is missing it entirely from the preamble"
        )

    @pytest.mark.parametrize("skill_name", BMAD_OVERLAP_SKILLS)
    def test_bmad_overlap_skills_have_alternative_mapping(self, skill_name: str):
        """BMAD-overlapping skills must include the alternative name in config-loading."""
        skill_path = SKILLS_DIR / skill_name / "SKILL.md"
        content = skill_path.read_text()

        # Find the config-loading block
        start = content.find("<config-loading>")
        end = content.find("</config-loading>")
        assert start != -1 and end != -1, (
            f"{skill_name}/SKILL.md is missing <config-loading> block"
        )
        config_block = content[start:end]

        # Each overlapping skill should mention its BMAD alternative within config-loading
        alternatives = {
            "implement-feature": "bmad-dev-story",
            "fix-bug": "bmad-dev-story",
            "create-tests": "bmad-tea",
            "update-docs": "tech-writer",
        }
        alt = alternatives[skill_name]
        assert alt in config_block, (
            f"{skill_name}/SKILL.md should reference BMAD alternative '{alt}' "
            f"within the <config-loading> block"
        )


class TestHeadlessInteractionBlock:
    """Lint: WF2 must have <headless-interaction>, <headless-checkpoint>, <headless-resume> blocks."""

    HEADLESS_SKILLS = ["implement-feature", "fix-bug"]

    @pytest.mark.parametrize("skill_name", HEADLESS_SKILLS)
    def test_skill_has_headless_interaction(self, skill_name: str):
        skill_path = SKILLS_DIR / skill_name / "SKILL.md"
        content = skill_path.read_text()
        assert "<headless-interaction>" in content and "</headless-interaction>" in content, (
            f"{skill_name}/SKILL.md missing <headless-interaction> block"
        )

    @pytest.mark.parametrize("skill_name", HEADLESS_SKILLS)
    def test_skill_has_headless_checkpoint(self, skill_name: str):
        skill_path = SKILLS_DIR / skill_name / "SKILL.md"
        content = skill_path.read_text()
        assert "<headless-checkpoint>" in content and "</headless-checkpoint>" in content, (
            f"{skill_name}/SKILL.md missing <headless-checkpoint> block"
        )

    @pytest.mark.parametrize("skill_name", HEADLESS_SKILLS)
    def test_skill_has_headless_resume(self, skill_name: str):
        skill_path = SKILLS_DIR / skill_name / "SKILL.md"
        content = skill_path.read_text()
        assert "<headless-resume>" in content and "</headless-resume>" in content, (
            f"{skill_name}/SKILL.md missing <headless-resume> block"
        )

    # Expected [Headless annotation counts per skill
    EXPECTED_COUNTS = {
        "implement-feature": 25,  # 17 base + 1 disabled-skill cleanup + 7 P15 (#73): Step 5 warn/halt/decompose + 8a ambiguity/dispatch-failure/design-flaw/headless-suspend + Step 11 deferred-High
        "fix-bug": 11,            # 10 interaction points + 1 disabled-skill cleanup
    }

    @pytest.mark.parametrize("skill_name", HEADLESS_SKILLS)
    def test_skill_has_correct_headless_annotation_count(self, skill_name: str):
        """Annotation count must match expected — catches missing annotations on new steps."""
        skill_path = SKILLS_DIR / skill_name / "SKILL.md"
        content = skill_path.read_text()
        count = content.count("[Headless")  # matches [Headless: and [Headless cleanup]
        expected = self.EXPECTED_COUNTS[skill_name]
        assert count == expected, (
            f"{skill_name}/SKILL.md has {count} [Headless] annotations, "
            f"expected {expected}. If you added a new interaction point, "
            f"add a [Headless:] annotation and bump this count."
        )

    def test_headless_resume_in_resumption_protocol(self):
        """The resumption protocol must reference headless-resume as Step -1."""
        skill_path = SKILLS_DIR / "implement-feature" / "SKILL.md"
        content = skill_path.read_text()
        start = content.find("<resumption-protocol>")
        end = content.find("</resumption-protocol>")
        block = content[start:end]
        assert "headless-resume" in block, (
            "implement-feature/SKILL.md <resumption-protocol> must reference <headless-resume>"
        )


class TestMandatoryStepsEnforcement:
    """Lint: workflow skills with code review steps must have <mandatory-steps> block."""

    # Skills that have multi-step workflows with code review
    ENFORCED_SKILLS = ["implement-feature", "fix-bug"]

    @pytest.mark.parametrize("skill_name", ENFORCED_SKILLS)
    def test_skill_has_mandatory_steps_block(self, skill_name: str):
        skill_path = SKILLS_DIR / skill_name / "SKILL.md"
        content = skill_path.read_text()
        assert "<mandatory-steps>" in content and "</mandatory-steps>" in content, (
            f"{skill_name}/SKILL.md is missing the <mandatory-steps> enforcement block"
        )

    @pytest.mark.parametrize("skill_name", ENFORCED_SKILLS)
    def test_mandatory_steps_marks_code_review_non_negotiable(self, skill_name: str):
        """Code review must be explicitly marked NON-NEGOTIABLE."""
        skill_path = SKILLS_DIR / skill_name / "SKILL.md"
        content = skill_path.read_text()
        start = content.find("<mandatory-steps>")
        end = content.find("</mandatory-steps>")
        block = content[start:end]
        assert "NON-NEGOTIABLE" in block, (
            f"{skill_name}/SKILL.md <mandatory-steps> must mark code review as NON-NEGOTIABLE"
        )

    @pytest.mark.parametrize("skill_name", ENFORCED_SKILLS)
    def test_mandatory_steps_lists_invalid_justifications(self, skill_name: str):
        """Block must include common invalid justifications to counter-program the LLM."""
        skill_path = SKILLS_DIR / skill_name / "SKILL.md"
        content = skill_path.read_text()
        start = content.find("<mandatory-steps>")
        end = content.find("</mandatory-steps>")
        block = content[start:end]
        assert "session is" in block.lower() or "context window" in block.lower(), (
            f"{skill_name}/SKILL.md <mandatory-steps> must address common skip justifications"
        )


class TestCritiqueMethodPreamble:
    """Lint: 6 critique-invoking skills contain the critiqueMethod check."""

    @pytest.mark.parametrize("skill_name", CRITIQUE_SKILLS)
    def test_skill_contains_critique_method_check(self, skill_name: str):
        skill_path = SKILLS_DIR / skill_name / "SKILL.md"
        assert skill_path.exists(), f"SKILL.md not found at {skill_path}"

        content = skill_path.read_text()
        assert "critiqueMethod" in content, (
            f"{skill_name}/SKILL.md is missing the critiqueMethod preference "
            f"check near its /reflexion:critique invocation"
        )


class TestWorkspaceSchemaWithBmad:
    """Schema: workspace JSON validates with BMAD-related fields."""

    def test_workspace_with_bmad_detected(self, make_workspace):
        """bmadDetected at top level is valid."""
        ws = make_workspace(
            projects=[
                {
                    "name": "myproj",
                    "path": "./projects/myproj",
                    "active": True,
                    "lastUsed": "2026-01-01T00:00:00Z",
                    "configured": True,
                    "disabledSkills": ["implement-feature", "fix-bug"],
                }
            ]
        )
        # Read back and add bmadDetected, write, re-read
        data = json.loads(ws.workspace_json.read_text())
        data["bmadDetected"] = True
        ws.workspace_json.write_text(json.dumps(data, indent=2))

        reloaded = json.loads(ws.workspace_json.read_text())
        assert reloaded["bmadDetected"] is True
        assert reloaded["projects"][0]["disabledSkills"] == [
            "implement-feature",
            "fix-bug",
        ]

    def test_workspace_disabled_skills_empty_means_all_enabled(self, make_workspace):
        """Empty disabledSkills array means user chose rawgentic for all tasks."""
        ws = make_workspace(
            projects=[
                {
                    "name": "myproj",
                    "path": "./projects/myproj",
                    "active": True,
                    "lastUsed": "2026-01-01T00:00:00Z",
                    "configured": True,
                    "disabledSkills": [],
                }
            ]
        )
        data = json.loads(ws.workspace_json.read_text())
        assert data["projects"][0]["disabledSkills"] == []

    def test_workspace_missing_disabled_skills_means_unconfigured(
        self, make_workspace
    ):
        """Missing disabledSkills field means not yet configured."""
        ws = make_workspace()  # default project, no disabledSkills
        data = json.loads(ws.workspace_json.read_text())
        assert "disabledSkills" not in data["projects"][0]

    def test_workspace_with_critique_method(self, make_workspace):
        """Per-project critiqueMethod field is valid."""
        ws = make_workspace(
            projects=[
                {
                    "name": "myproj",
                    "path": "./projects/myproj",
                    "active": True,
                    "lastUsed": "2026-01-01T00:00:00Z",
                    "configured": True,
                    "disabledSkills": ["implement-feature"],
                    "critiqueMethod": "bmad-party-mode",
                }
            ]
        )
        data = json.loads(ws.workspace_json.read_text())
        assert data["projects"][0]["critiqueMethod"] == "bmad-party-mode"

    def test_workspace_bmad_detected_false(self, make_workspace):
        """bmadDetected: false is valid (BMAD previously removed)."""
        ws = make_workspace()
        data = json.loads(ws.workspace_json.read_text())
        data["bmadDetected"] = False
        ws.workspace_json.write_text(json.dumps(data, indent=2))

        reloaded = json.loads(ws.workspace_json.read_text())
        assert reloaded["bmadDetected"] is False
