"""Post-retreat survivors of the old headless-infrastructure test file (#43 → #866 M0d).

The headless ORCHESTRATION (access gate, suspend/QUESTION protocol,
headless_interaction.py, references/headless.md, the Action pilot) was deleted in
M0d. What remains here:
- the <config-loading> skill-count canary (hooks/skill_registration_check.py:229
  reads EXPECTED_CONFIG_LOADING_COUNT out of THIS file by path — do not move it
  without updating that reader),
- the D178 negative guard (no skill body re-grows a <headless-mode> pointer),
- the two setup pins (setup stages no headless config; Step 2e keeps the
  no-unattended-installs guard — D184: RAWGENTIC_HEADLESS survives only as the
  bare "nobody is watching" signal until epic #871),
- the Step 1b goal-guard and mandatory-steps lints (never headless-specific;
  they live here for historical reasons).
"""
import re
from pathlib import Path

import pytest

from tests.corpus import skill_corpus

SKILLS_DIR = Path(__file__).resolve().parent.parent.parent / "skills"


class TestSkillCountCanary:
    """Canary: assert the number of SKILL.md files with <config-loading> matches expected."""

    SKILLS_DIR = Path(__file__).resolve().parent.parent.parent / "skills"
    EXPECTED_CONFIG_LOADING_COUNT = 9  # +epic-post-mortem (#508); -6 deprecated stubs (#160: WF4/7/8/9/10/12), +scan (#160), +run-feedback (#337), +admit-to-org-runners (#397), -admit-to-org-runners (#788: extracted to the claude-skills repo); was 12

    def test_config_loading_skill_count(self):
        """If a new workflow skill is added, this test reminds you to wire in the config-loading preamble."""
        count = 0
        for skill_dir in self.SKILLS_DIR.iterdir():
            skill_file = skill_dir / "SKILL.md"
            # corpus, not SKILL.md alone: #158 may move the preamble into references/.
            # Line-anchored opening tag, not a bare substring: a backtick MENTION of
            # `<config-loading>` in a reference doc must not count as the real block.
            if skill_file.exists() and re.search(r"^<config-loading>", skill_corpus(skill_dir.name), re.M):
                count += 1
        assert count == self.EXPECTED_CONFIG_LOADING_COUNT, (
            f"Expected {self.EXPECTED_CONFIG_LOADING_COUNT} skills with <config-loading>, "
            f"found {count}. If you added a new workflow skill, add the <config-loading> "
            f"preamble and bump this count."
        )


# NOTE (#205): the external reflexion plugin dependency was removed entirely — no
# active skill invokes /reflexion:critique or carries a critiqueMethod preference
# check anymore (WF2 #190 → reflect-only → #205 in-repo quality-bar rubric; setup's
# config critique is now the same in-repo rubric). The former TestCritiqueMethodPreamble
# guard and its CRITIQUE_SKILLS list are gone; reflexion-freedom of the active skill
# corpus is now asserted by tests/test_wf2_clarity.py::test_active_skills_are_reflexion_free.



class TestHeadlessRetirement:
    """M0d (#866) negative guards over the retired headless orchestration."""

    @pytest.mark.parametrize("skill_name", ["implement-feature", "fix-bug"])
    def test_headless_reference_files_are_gone(self, skill_name: str):
        assert not (SKILLS_DIR / skill_name / "references" / "headless.md").exists()

    @pytest.mark.parametrize("skill_name", ["implement-feature", "fix-bug"])
    def test_body_no_longer_points_to_headless_reference(self, skill_name: str):
        """D178 (M0b, #866): the <headless-mode> pointer must never re-grow."""
        content = (SKILLS_DIR / skill_name / "SKILL.md").read_text()
        assert "<headless-mode>" not in content

    def test_setup_no_longer_configures_headless(self):
        """M0c (#866): setup stages no headless config."""
        content = (SKILLS_DIR / "setup" / "references" / "integrations.md").read_text()
        assert "headlessEnabled" not in content
        spine = (SKILLS_DIR / "setup" / "SKILL.md").read_text()
        assert "headlessEnabled" not in spine and "Step 2c" not in spine

    def test_setup_step_2e_keeps_unattended_install_guard(self):
        """D184 (#866/#871): RAWGENTIC_HEADLESS survives ONLY as the bare
        "nobody is watching this session" signal — and unattended package
        installs stay forbidden. The guard leaves when epic #871 replaces the
        signal, never before."""
        spine = (SKILLS_DIR / "setup" / "SKILL.md").read_text()
        assert "RAWGENTIC_HEADLESS=1" in spine
        norm = " ".join(spine.split())
        assert "do NOT install — just record the gap" in norm


class TestGoalGuardStep1b:
    """Drift guard: Step 1b (AC-derived /goal guard, #156) must be present and wired
    to plan_lib.build_goal_text in both WF2 and WF3."""

    def test_implement_feature_has_step_1b_and_build_goal_text(self):
        content = skill_corpus("implement-feature")
        assert "## Step 1b" in content, "implement-feature/SKILL.md missing '## Step 1b'"
        assert "build_goal_text" in content, (
            "implement-feature/SKILL.md Step 1b must reference plan_lib.build_goal_text"
        )
        assert "or a blocker is posted to the issue via the ERROR protocol" in content, (
            "implement-feature/SKILL.md Step 1b missing the escape-disjunct phrasing"
        )

    def test_fix_bug_has_step_1b_and_build_goal_text(self):
        content = skill_corpus("fix-bug")
        assert "## Step 1b" in content, "fix-bug/SKILL.md missing '## Step 1b'"
        assert "build_goal_text" in content, (
            "fix-bug/SKILL.md Step 1b must reference plan_lib.build_goal_text"
        )

    @pytest.mark.parametrize("skill_name", ["implement-feature", "fix-bug"])
    def test_step_1b_always_emits_with_epic_defer(self, skill_name):
        """#191 (WF2) + #192 (WF3 parity): Step 1b ALWAYS emits the /goal prompt (a
        prior goal it can't observe must not suppress emission), except under an
        epic campaign (RAWGENTIC_EPIC_GOAL set) where it DEFERS to the epic-level
        goal — logged, never silent."""
        content = skill_corpus(skill_name)
        assert "ALWAYS emit" in content, \
            f"{skill_name} Step 1b must state it ALWAYS emits the /goal prompt"
        assert "RAWGENTIC_EPIC_GOAL" in content, \
            f"{skill_name} Step 1b must key the epic-campaign defer on RAWGENTIC_EPIC_GOAL"
        assert "deferred" in content.lower(), \
            f"{skill_name} Step 1b must record a deferred marker under an epic campaign"


class TestMandatoryStepsEnforcement:
    """Lint: workflow skills with code review steps must have <mandatory-steps> block."""

    # Skills that have multi-step workflows with code review
    ENFORCED_SKILLS = ["implement-feature", "fix-bug"]

    @pytest.mark.parametrize("skill_name", ENFORCED_SKILLS)
    def test_skill_has_mandatory_steps_block(self, skill_name: str):
        content = skill_corpus(skill_name)
        assert "<mandatory-steps>" in content and "</mandatory-steps>" in content, (
            f"{skill_name}/SKILL.md is missing the <mandatory-steps> enforcement block"
        )

    @pytest.mark.parametrize("skill_name", ENFORCED_SKILLS)
    def test_mandatory_steps_marks_code_review_non_negotiable(self, skill_name: str):
        """Code review must be explicitly marked NON-NEGOTIABLE."""
        content = skill_corpus(skill_name)
        start = content.find("<mandatory-steps>")
        end = content.find("</mandatory-steps>")
        block = content[start:end]
        assert "NON-NEGOTIABLE" in block, (
            f"{skill_name}/SKILL.md <mandatory-steps> must mark code review as NON-NEGOTIABLE"
        )

    @pytest.mark.parametrize("skill_name", ENFORCED_SKILLS)
    def test_mandatory_steps_lists_invalid_justifications(self, skill_name: str):
        """Block must include common invalid justifications to counter-program the LLM."""
        content = skill_corpus(skill_name)
        start = content.find("<mandatory-steps>")
        end = content.find("</mandatory-steps>")
        block = content[start:end]
        assert "session is" in block.lower() or "context window" in block.lower(), (
            f"{skill_name}/SKILL.md <mandatory-steps> must address common skip justifications"
        )


# TestCritiqueMethodPreamble removed at #205 — no active skill carries a
# critiqueMethod preference check now that the reflexion dependency is gone.
# See tests/test_wf2_clarity.py::test_active_skills_are_reflexion_free.
