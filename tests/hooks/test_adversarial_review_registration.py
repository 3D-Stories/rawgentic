"""Drift guards for WF5 adversarial-review registration + integration (issue #77).

Covers Task 11 (registration + count strings) and Task 12 (WF2/WF3 config-gated
invocation present in the expected steps; consolidation WF5 text).
"""
import json
import re
from pathlib import Path

import pytest

from tests.corpus import skill_corpus

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SKILLS_DIR = REPO_ROOT / "skills"


# --- registration ---

def test_skill_dir_and_frontmatter_exist():
    skill = SKILLS_DIR / "adversarial-review" / "SKILL.md"
    assert skill.exists()
    # LOCATION pin: frontmatter must be in SKILL.md itself (registration);
    # the prose blocks are content pins over the corpus.
    assert "name: adversarial-review" in skill.read_text()
    corpus = skill_corpus("adversarial-review")
    assert "<config-loading>" in corpus
    assert "<completion-gate>" in corpus


def test_marketplace_registers_skill():
    mp = json.loads((REPO_ROOT / ".claude-plugin" / "marketplace.json").read_text())
    skills = mp["plugins"][0]["skills"]
    assert "./skills/admit-to-org-runners" in skills
    # alphabetical placement: add-exception < admit-to-org-runners < adversarial-review
    assert skills.index("./skills/admit-to-org-runners") == skills.index("./skills/add-exception") + 1
    assert skills.index("./skills/adversarial-review") == skills.index("./skills/admit-to-org-runners") + 1


def test_plugin_version_bumped():
    plugin = json.loads((REPO_ROOT / ".claude-plugin" / "plugin.json").read_text())
    assert plugin["version"] == "3.84.0"


def test_descriptions_consistent_count():
    """plugin.json + marketplace.json descriptions reflect v3.0.0 (#161):
    6 active SDLC workflows, stubs removed."""
    plugin = json.loads((REPO_ROOT / ".claude-plugin" / "plugin.json").read_text())
    mp = json.loads((REPO_ROOT / ".claude-plugin" / "marketplace.json").read_text())
    for desc in (plugin["description"], mp["plugins"][0]["description"]):
        assert "9 SDLC workflow skills" in desc
        assert "deprecated stub" not in desc.lower()   # stubs removed at v3.0.0 (#161)
        assert "12 SDLC workflow skills" not in desc


def test_readme_count_strings_updated():
    readme = (REPO_ROOT / "README.md").read_text()
    assert "9 SDLC workflow skills" in readme
    assert "12 SDLC workflow skills" not in readme
    n_skills = len(list((REPO_ROOT / "skills").glob("*/SKILL.md")))
    assert f"provides {n_skills} skills" in readme
    # #271 reviewer note: computed==computed loses the absolute floor a
    # deleted-everywhere skill would have tripped. The plugin description's
    # human-readable breakdown ("6 SDLC + 6 workspace + 1 planning + 2
    # security") is the remaining hand-written tally — assert it sums to the
    # disk count so a silent shrink still fails somewhere.
    import re as _re2
    desc = json.loads(
        (REPO_ROOT / ".claude-plugin" / "plugin.json").read_text()
    )["description"]
    breakdown = [int(n) for n in _re2.findall(
        r"(\d+) (?:SDLC workflow|workspace management|planning|security)", desc)]
    assert len(breakdown) == 4 and sum(breakdown) == n_skills, (
        f"plugin description breakdown {breakdown} must sum to the "
        f"{n_skills} skills on disk"
    )
    assert "All 9 config-driven skills" in readme
    # #271: computed from disk, never a hand-maintained literal. A skill
    # "has evals" iff evals.json exists in its own evals/ dir or its
    # -workspace evals/ dir.
    skills = sorted(p.parent.name for p in (REPO_ROOT / "skills").glob("*/SKILL.md"))
    have = {
        s for s in skills
        if (REPO_ROOT / "skills" / s / "evals" / "evals.json").exists()
        or (REPO_ROOT / "skills" / f"{s}-workspace" / "evals" / "evals.json").exists()
    }
    assert f"{len(have)}/{len(skills)} skills have evals.json" in readme, (
        f"README must render the computed evals fraction "
        f"{len(have)}/{len(skills)}"
    )
    # Membership cross-check: every skill README names as having NO evals
    # must really lack them (C14: the count was right, the membership wrong)
    for name in sorted(set(skills) - have):
        assert f"`{name}`" in readme, (
            f"README's have-none list must name {name} (computed complement)"
        )
    import re as _re
    m = _re.search(
        r"skills have evals\.json[^)]*?the lightweight (.*?) skills have none",
        readme, _re.S)
    assert m, "README must carry the have-none list in its evals sentence"
    listed_none = set(_re.findall(r"`([a-z0-9-]+)`", m.group(1)))
    assert listed_none == (set(skills) - have - {"peer-consult"}), (
        f"README have-none list {sorted(listed_none)} != computed "
        f"{sorted(set(skills) - have - {'peer-consult'})} (peer-consult is "
        f"called out separately as a stub)"
    )
    assert "9 workspace management" in readme  # #113 — README count must match plugin/marketplace descriptions


def test_readme_changelog_has_no_spliced_headings():
    """Guard against the recurring changelog-insertion garble (#192/#193/#194):
    inserting a new entry above the previous heading spliced a `### vX.Y.Z`
    into the middle of a bullet, e.g. `...goal guard### v3.5.0 (2026-07-05)`.
    A lowercase letter immediately followed by `###` never occurs in clean prose."""
    import re
    readme = (REPO_ROOT / "README.md").read_text()
    offenders = re.findall(r"[A-Za-z0-9]###", readme)
    assert not offenders, f"spliced changelog heading(s) detected: {offenders}"


def test_marketplace_skill_dirs_all_exist():
    """Every registered skill path must resolve to a real SKILL.md (no dangling entry)."""
    mp = json.loads((REPO_ROOT / ".claude-plugin" / "marketplace.json").read_text())
    for rel in mp["plugins"][0]["skills"]:
        assert (REPO_ROOT / rel / "SKILL.md").exists(), f"missing {rel}/SKILL.md"


# --- diff artifact type support in SKILL.md (issue #131, Task 4) ---

def test_description_mentions_diff_review_and_drops_not_for_clause():
    # frontmatter is a LOCATION pin (SKILL.md); the dropped clause must be
    # absent from the whole corpus.
    frontmatter = (SKILLS_DIR / "adversarial-review" / "SKILL.md").read_text().split("---")[1]
    assert "diff" in frontmatter.lower()
    assert "NOT for reviewing code diffs" not in skill_corpus("adversarial-review")


def test_constants_supported_artifact_types_includes_diff():
    text = skill_corpus("adversarial-review")
    constants = _section(text, "<constants>", "</constants>")
    line = next(l for l in constants.splitlines() if l.startswith("SUPPORTED_ARTIFACT_TYPES:"))
    types = [t.strip() for t in line.split(":", 1)[1].split(",")]
    assert "diff" in types


def test_body_documents_findings_json_sidecar_flag():
    text = skill_corpus("adversarial-review")
    assert "--findings-json" in text


def test_step1_autodetect_mentions_patch_and_diff_globs():
    text = skill_corpus("adversarial-review")
    step1 = _section(text, "## Step 1:", "## Step 2:")
    assert "*.patch" in step1
    assert "*.diff" in step1
    assert "diff" in step1.lower()


def test_data_handling_mentions_diff_secret_density_and_egress_classifier():
    text = skill_corpus("adversarial-review")
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
    text = skill_corpus("implement-feature")
    step4 = _section(text, "## Step 4:", "## Step 5:")
    step6 = _section(text, "## Step 6:", "## Step 7:")
    for section, name in ((step4, "Step 4"), (step6, "Step 6")):
        assert "adversarial-review" in section.lower(), f"WF2 {name} missing adversarial-review invocation"
        assert "is-enabled" in section, f"WF2 {name} missing config gate (is-enabled)"


def test_wf2_step4_is_fast_path_gated():
    text = skill_corpus("implement-feature")
    step4 = _section(text, "## Step 4:", "## Step 5:")
    assert "fast_path_eligible == false" in step4


def test_wf2_reuses_existing_design_loopback_not_new_source():
    """Decision A: adversarial design flaws consume the existing 'design' counter."""
    text = skill_corpus("implement-feature")
    step4 = _section(text, "## Step 4:", "## Step 5:")
    assert '"design"' in step4 or "design` loop-back" in step4
    # must NOT introduce a new 'adversarial' loopback source
    assert '"adversarial"' not in step4


def test_wf3_invokes_in_step4_default_off():
    text = skill_corpus("fix-bug")
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
    text = skill_corpus("setup")
    assert "Step 2d" in text
    assert "adversarialReview" in text


# --- WF1 / WF4 integration (issue #79) ---

def test_wf1_invokes_in_step4_default_off():
    text = skill_corpus("create-issue")
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
    text = skill_corpus("create-issue")
    step4 = _section(text, "## Step 4:", "## Step 5:")
    assert "consume_loopback(" not in step4


def test_wf4_is_removed():
    """WF4 removed at v3.0.0 (#161): the skill dir is gone entirely — its old
    Step 4 adversarial integration cannot half-survive a resurrection either
    (tests/test_v3_removals.py pins the removal)."""
    assert not (REPO_ROOT / "skills" / "refactor").exists()


def test_setup_offers_surviving_workflows():
    """#160: refactor (WF4) is deprecated — setup's Step 2d offer detail lives in
    references/integrations.md (LOCATION pin: the corpus slice between the spine's
    '## Step 2d:' and '## Step 3:' headings resolves to the spine SUMMARY only, so
    this reads the reference file directly to guard the real offer list)."""
    detail = (SKILLS_DIR / "setup" / "references" / "integrations.md").read_text()
    for name in ("implement-feature", "fix-bug", "create-issue"):
        assert name in detail, f"setup Step 2d detail must offer {name}"
    # the example config must not present refactor as a live workflow
    assert '"workflows": ["implement-feature", "fix-bug"]' in detail
    assert '"refactor"]' not in detail
    # spine summary also names no refactor offer
    spine = (SKILLS_DIR / "setup" / "SKILL.md").read_text()
    step2d = _section(spine, "## Step 2d:", "## Step 3:")
    assert "refactor" not in step2d

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
    skill = skill_corpus("implement-feature")
    # the opt-in block, its gate invocation, the validator, and the reference pointer
    assert "whole-issue-delegation: #133" in skill
    assert "--key wholeIssueDelegation" in skill
    assert "validate_build_receipt" in skill
    assert "references/whole-issue-delegation.md" in skill
    # the reject path must NOT prescribe a blanket clean against the operator tree
    assert "never" in skill.lower() and "git clean -fd" in skill  # named only to forbid it


def test_wf2_step8_delegation_is_opt_in_default_off():
    skill = skill_corpus("implement-feature")
    # default-off: a non-zero is-enabled exit skips silently
    assert "default-off" in skill
    assert "skip silently" in skill


# --- setup collects the backend field (#405) ---

def test_setup_2d_asks_backend():
    """#405 AC1: the Step 2d detail asks which backend and stages it; the
    default-gpt-may-omit contract is stated (LOCATION pin: the offer detail
    lives in references/integrations.md, same as test_setup_offers_surviving_workflows)."""
    detail = (SKILLS_DIR / "setup" / "references" / "integrations.md").read_text()
    assert "Which review backend? (gpt / glm / both) [default: gpt]" in detail
    assert "absent → gpt is the documented contract" in detail
    assert 'pip install "zhipuai>=2.1.5"' in detail
    assert "ZHIPUAI_API_KEY" in detail


def test_setup_2d_prereq_nudge_never_blocks():
    """#405 AC4: glm/both picks with an unready prereq print the engine guidance
    and STILL stage — config is intent, the runtime gate enforces."""
    detail = (SKILLS_DIR / "setup" / "references" / "integrations.md").read_text()
    assert "prereq --backend" in detail
    assert "STILL stage" in detail


def test_setup_2g_mirrors_backend_question():
    """#405 AC2: peerConsult asks the same-vocabulary backend question
    independently of the review answer."""
    detail = (SKILLS_DIR / "setup" / "references" / "integrations.md").read_text()
    twog = detail[detail.index("## Step 2g:"):detail.index("## Step 2h:")]
    assert "backend" in twog
    assert "independent" in twog


def test_setup_2d_reconfig_preserves_backend():
    """#405 AC3: re-running setup offers the current backend as the default,
    never silently resetting it."""
    detail = (SKILLS_DIR / "setup" / "references" / "integrations.md").read_text()
    assert "current backend" in detail


def test_config_reference_scope_out_dropped():
    """#405 AC6: setup now collects backend — the hand-edit scope-out note is gone."""
    doc = (REPO_ROOT / "docs" / "config-reference.md").read_text()
    assert "deliberate #403 scope-out" not in doc
    assert "does not yet collect" not in doc


# --- #446: setup Step 2i — phase-executor seat table ---

def test_setup_has_step_2i():
    text = skill_corpus("setup")
    assert "Step 2i" in text
    assert "phaseExecutorTable" in text
    assert "show-table" in text and "apply-table" in text
    # #531: declining/keeping defaults stages the answered-defaults sentinel so the
    # staleness nudge can record the answer (presence = answered); resolution stays
    # package-default. The old "stages nothing" contract looped the nudge forever.
    # Whitespace-normalized: the sentence wraps across lines in the skill prose.
    flat = " ".join(text.lower().replace("**", "").split())
    assert "stages the answered-defaults sentinel" in flat
    assert '"file": null' in text
    # A2/Step-6 staging: the pointer is applied at the .rawgentic.json write (Step 6), not Step 8.
    assert "phaseExecutorTable" in _section(text, "## Step 6:", "## Step 7:")


def test_manifest_project_config_entries_have_setup_anchor():
    """#446 S2 (second half — moved from the reconcile guard): every source: project_config
    manifest entry must anchor to a real setup step that stages it."""
    import importlib.util
    import sys as _s
    hooks_dir = REPO_ROOT / "hooks"
    _s.path.insert(0, str(hooks_dir))
    spec = importlib.util.spec_from_file_location(
        "pur_anchor", str(hooks_dir / "post_update_reconcile.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    text = skill_corpus("setup")
    for feat in mod.FEATURE_MANIFEST:
        if feat.get("source") == "project_config":
            assert feat["key"] in text, f"{feat['key']}: no setup-step anchor in the setup skill"
