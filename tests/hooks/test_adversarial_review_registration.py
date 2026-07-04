"""Drift guards for WF5 adversarial-review registration + integration (issue #77).

Covers Task 11 (registration + count strings) and Task 12 (WF2/WF3 config-gated
invocation present in the expected steps; consolidation WF5 text).
"""
import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SKILLS_DIR = REPO_ROOT / "skills"


# --- registration ---

def test_skill_dir_and_frontmatter_exist():
    skill = SKILLS_DIR / "adversarial-review" / "SKILL.md"
    assert skill.exists()
    text = skill.read_text()
    assert "name: rawgentic:adversarial-review" in text
    assert "<config-loading>" in text
    assert "<completion-gate>" in text


def test_marketplace_registers_skill():
    mp = json.loads((REPO_ROOT / ".claude-plugin" / "marketplace.json").read_text())
    skills = mp["plugins"][0]["skills"]
    assert "./skills/adversarial-review" in skills
    # alphabetical placement right after add-exception
    assert skills.index("./skills/adversarial-review") == skills.index("./skills/add-exception") + 1


def test_plugin_version_bumped():
    plugin = json.loads((REPO_ROOT / ".claude-plugin" / "plugin.json").read_text())
    assert plugin["version"] == "2.53.1"


def test_descriptions_consistent_count():
    """plugin.json + marketplace.json descriptions both claim 12 SDLC skills."""
    plugin = json.loads((REPO_ROOT / ".claude-plugin" / "plugin.json").read_text())
    mp = json.loads((REPO_ROOT / ".claude-plugin" / "marketplace.json").read_text())
    for desc in (plugin["description"], mp["plugins"][0]["description"]):
        assert "12 SDLC workflow skills" in desc
        assert "11 SDLC workflow skills" not in desc


def test_readme_count_strings_updated():
    readme = (REPO_ROOT / "README.md").read_text()
    assert "12 SDLC workflow skills" in readme
    assert "11 SDLC workflow skills" not in readme
    assert "provides 18 skills" in readme
    assert "All 12 workflow skills share" in readme
    assert "15/18 skills have evals.json" in readme


def test_marketplace_skill_dirs_all_exist():
    """Every registered skill path must resolve to a real SKILL.md (no dangling entry)."""
    mp = json.loads((REPO_ROOT / ".claude-plugin" / "marketplace.json").read_text())
    for rel in mp["plugins"][0]["skills"]:
        assert (REPO_ROOT / rel / "SKILL.md").exists(), f"missing {rel}/SKILL.md"


# --- diff artifact type support in SKILL.md (issue #131, Task 4) ---

def test_description_mentions_diff_review_and_drops_not_for_clause():
    text = (SKILLS_DIR / "adversarial-review" / "SKILL.md").read_text()
    frontmatter = text.split("---")[1]
    assert "diff" in frontmatter.lower()
    assert "NOT for reviewing code diffs" not in text


def test_constants_supported_artifact_types_includes_diff():
    text = (SKILLS_DIR / "adversarial-review" / "SKILL.md").read_text()
    constants = _section(text, "<constants>", "</constants>")
    line = next(l for l in constants.splitlines() if l.startswith("SUPPORTED_ARTIFACT_TYPES:"))
    types = [t.strip() for t in line.split(":", 1)[1].split(",")]
    assert "diff" in types


def test_body_documents_findings_json_sidecar_flag():
    text = (SKILLS_DIR / "adversarial-review" / "SKILL.md").read_text()
    assert "--findings-json" in text


def test_step1_autodetect_mentions_patch_and_diff_globs():
    text = (SKILLS_DIR / "adversarial-review" / "SKILL.md").read_text()
    step1 = _section(text, "## Step 1:", "## Step 2:")
    assert "*.patch" in step1
    assert "*.diff" in step1
    assert "diff" in step1.lower()


def test_data_handling_mentions_diff_secret_density_and_egress_classifier():
    text = (SKILLS_DIR / "adversarial-review" / "SKILL.md").read_text()
    dh = _section(text, "<data-handling>", "</data-handling>")
    low = dh.lower()
    assert "raw source code" in low
    assert "egress classifier" in low
    assert "non-blocking" in low


# --- consolidation doc ---

def test_consolidation_lists_wf5_adversarial_review():
    doc = (REPO_ROOT / "docs" / "consolidation.md").read_text()
    assert "Adversarial Review" in doc
    # WF5 should no longer be described purely as a reserved "Code Review" gap
    assert "WF5 is **Adversarial Review**" in doc or "| WF5" in doc


def test_design_doc_exists():
    assert (REPO_ROOT / "docs" / "design" / "workflow-adversarial-review.md").exists()


# --- WF2 / WF3 integration (config-gated invocation present in expected steps) ---

def _section(text: str, header: str, next_header: str | None) -> str:
    start = text.index(header)
    end = text.index(next_header, start) if next_header else len(text)
    return text[start:end]


def test_wf2_invokes_in_step4_and_step6():
    text = (SKILLS_DIR / "implement-feature" / "SKILL.md").read_text()
    step4 = _section(text, "## Step 4:", "## Step 5:")
    step6 = _section(text, "## Step 6:", "## Step 7:")
    for section, name in ((step4, "Step 4"), (step6, "Step 6")):
        assert "adversarial-review" in section.lower(), f"WF2 {name} missing adversarial-review invocation"
        assert "is-enabled" in section, f"WF2 {name} missing config gate (is-enabled)"


def test_wf2_step4_is_fast_path_gated():
    text = (SKILLS_DIR / "implement-feature" / "SKILL.md").read_text()
    step4 = _section(text, "## Step 4:", "## Step 5:")
    assert "fast_path_eligible == false" in step4


def test_wf2_reuses_existing_design_loopback_not_new_source():
    """Decision A: adversarial design flaws consume the existing 'design' counter."""
    text = (SKILLS_DIR / "implement-feature" / "SKILL.md").read_text()
    step4 = _section(text, "## Step 4:", "## Step 5:")
    assert '"design"' in step4 or "design` loop-back" in step4
    # must NOT introduce a new 'adversarial' loopback source
    assert '"adversarial"' not in step4


def test_wf3_invokes_in_step4_default_off():
    text = (SKILLS_DIR / "fix-bug" / "SKILL.md").read_text()
    step4 = _section(text, "## Step 4:", "## Step 5:")
    assert "adversarial-review" in step4.lower()
    assert "is-enabled" in step4
    assert "DEFAULT-OFF" in step4 or "default-off" in step4.lower()
    # lightweight rationale preserved
    assert "Lightweight reflect ONLY" in step4


def test_plan_lib_has_no_adversarial_loopback_source():
    """Decision A: no new plan_lib loopback source was added."""
    plan_lib = (REPO_ROOT / "hooks" / "plan_lib.py").read_text()
    assert '"adversarial"' not in plan_lib


def test_setup_has_step_2d():
    text = (SKILLS_DIR / "setup" / "SKILL.md").read_text()
    assert "Step 2d" in text
    assert "adversarialReview" in text


# --- WF1 / WF4 integration (issue #79) ---

def test_wf1_invokes_in_step4_default_off():
    text = (SKILLS_DIR / "create-issue" / "SKILL.md").read_text()
    step4 = _section(text, "## Step 4:", "## Step 5:")
    assert "adversarial-review" in step4.lower(), "WF1 Step 4 missing adversarial-review invocation"
    assert "is-enabled" in step4, "WF1 Step 4 missing config gate (is-enabled)"
    assert "create-issue" in step4, "WF1 hook must gate on the 'create-issue' skill name"
    assert "default-off" in step4.lower() or "DEFAULT-OFF" in step4


def test_wf1_uses_no_plan_lib_loopback():
    """WF1 has no plan_lib loopback — its hook must NOT *invoke* consume_loopback.

    (The prose may mention `consume_loopback` to say it is NOT used; we assert there
    is no actual call, i.e. no `consume_loopback(` invocation.)
    """
    text = (SKILLS_DIR / "create-issue" / "SKILL.md").read_text()
    step4 = _section(text, "## Step 4:", "## Step 5:")
    assert "consume_loopback(" not in step4


def test_wf4_invokes_in_step4_extract_restructure_only():
    text = (SKILLS_DIR / "refactor" / "SKILL.md").read_text()
    step4 = _section(text, "## Step 4:", "## Step 5:")
    assert "adversarial-review" in step4.lower(), "WF4 Step 4 missing adversarial-review invocation"
    assert "is-enabled" in step4, "WF4 Step 4 missing config gate (is-enabled)"
    assert "refactor" in step4
    # gated to the full-critique path only (extract/restructure), not rename/simplify
    low = step4.lower()
    assert "extract" in low and "restructure" in low


def test_wf4_uses_textual_budget_not_plan_lib():
    """WF4 manages loop-back via its own textual LOOPBACK_BUDGET, not plan_lib.

    Assert no actual `consume_loopback(` invocation, and that the textual budget
    is referenced.
    """
    text = (SKILLS_DIR / "refactor" / "SKILL.md").read_text()
    step4 = _section(text, "## Step 4:", "## Step 5:")
    assert "consume_loopback(" not in step4  # WF4 does not use plan_lib counters
    assert "LOOPBACK_BUDGET" in step4


def test_setup_offers_all_four_workflows():
    text = (SKILLS_DIR / "setup" / "SKILL.md").read_text()
    step2d = _section(text, "## Step 2d:", "## Step 3:")
    for name in ("implement-feature", "fix-bug", "create-issue", "refactor"):
        assert name in step2d, f"setup Step 2d must offer {name}"


# --- adversarial-review evals workspace (issue #79) ---

def test_adversarial_review_evals_exist_and_valid():
    evals_path = SKILLS_DIR / "adversarial-review-workspace" / "evals" / "evals.json"
    assert evals_path.exists(), "missing skills/adversarial-review-workspace/evals/evals.json"
    data = json.loads(evals_path.read_text())
    assert data["skill_name"] == "rawgentic:adversarial-review"
    assert isinstance(data["evals"], list) and len(data["evals"]) >= 3
    for ev in data["evals"]:
        assert isinstance(ev.get("id"), int)
        assert ev.get("prompt") and isinstance(ev["prompt"], str)
        assert ev.get("expected_output") and isinstance(ev["expected_output"], str)


def test_adversarial_review_workspace_has_no_skill_md():
    """Workspace dirs are eval artifacts — must NOT contain a SKILL.md (validator rejects)."""
    ws = SKILLS_DIR / "adversarial-review-workspace"
    assert not (ws / "SKILL.md").exists()


# --- whole-issue delegation (#133) drift guards ---

def test_whole_issue_delegation_reference_exists():
    ref = SKILLS_DIR / "implement-feature" / "references" / "whole-issue-delegation.md"
    assert ref.exists(), "references/whole-issue-delegation.md must exist"
    body = ref.read_text()
    # the receipt schema keys + the trust-boundary contract must be documented
    for token in ("task_shas", "files_per_task", "exit_code", "promotions",
                  "validate_build_receipt", "never self-certif", "fall"):
        assert token in body, f"reference missing {token!r}"


def test_wf2_step8_documents_whole_issue_delegation_submode():
    skill = (SKILLS_DIR / "implement-feature" / "SKILL.md").read_text()
    # the opt-in block, its gate invocation, the validator, and the reference pointer
    assert "whole-issue-delegation: #133" in skill
    assert "--key wholeIssueDelegation" in skill
    assert "validate_build_receipt" in skill
    assert "references/whole-issue-delegation.md" in skill
    # the reject path must NOT prescribe a blanket clean against the operator tree
    assert "never" in skill.lower() and "git clean -fd" in skill  # named only to forbid it


def test_wf2_step8_delegation_is_opt_in_default_off():
    skill = (SKILLS_DIR / "implement-feature" / "SKILL.md").read_text()
    # default-off: a non-zero is-enabled exit skips silently
    assert "default-off" in skill
    assert "skip silently" in skill
