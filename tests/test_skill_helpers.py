"""Drift guard: SKILL.md must reference each plan_lib helper in its expected
step section (forward test), and every public helper must be referenced
somewhere in SKILL.md (reverse test).

Catches:
- A helper is renamed in plan_lib but the SKILL.md still references the old name.
- A helper is added to plan_lib but never wired into the workflow.
- A helper reference moves to the wrong step section.

Issue #73 — WF2 tiered code review (P15).
"""
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILL_PATH = REPO_ROOT / "skills" / "implement-feature" / "SKILL.md"
PLAN_LIB_PATH = REPO_ROOT / "hooks" / "plan_lib.py"


def _read_skill() -> str:
    return SKILL_PATH.read_text(encoding="utf-8")


def _step_section(skill_text: str, step_number: str) -> str:
    """Extract the text of `## Step <N>: ...` up to the next top-level step heading.

    Step IDs are strings to allow "8a".
    """
    # Match start of the requested step heading
    pattern = re.compile(rf"^## Step {re.escape(step_number)}[:\s]", re.MULTILINE)
    m = pattern.search(skill_text)
    if not m:
        return ""
    start = m.start()
    # Find next ## Step or ## (top-level section) after start
    next_step = re.compile(r"^## (?:Step\s+\S+|[A-Z])", re.MULTILINE)
    m2 = next_step.search(skill_text, m.end())
    end = m2.start() if m2 else len(skill_text)
    return skill_text[start:end]


# Expected (helper_name -> list of step numbers where it must appear)
EXPECTED_REFERENCES = {
    "parse_tasks": ["5"],
    "compute_risk_ratio": ["5"],
    "check_ratio_band": ["5"],
    "should_promote": ["8"],
    "format_promotion_note": ["8"],
    "scan_prior_commits_for_trigger": ["8"],
    "append_review_log": ["8"],
    "assert_review_coverage": ["9"],
    "get_deferred_findings": ["11"],
    "assert_no_unresolved_high_deferrals": ["11"],
    "consume_loopback": ["8"],
    "write_review_state": ["8", "11"],   # written at Step 8a (suspend states) and Step 11 ("applied")
    "read_review_state": ["12", "14"],   # read by the PR-creation and merge gates
    "review_state_path": ["8", "11"],    # path resolver for the state file
}


# Match `helper_name`, `plan_lib.helper_name`, or `something.helper_name` inside
# backticks. The backtick requirement (vs plain substring) filters out prose
# mentions like "we call parse_tasks later" and forces actual invocation syntax.
def _backticked_reference_re(helper: str) -> re.Pattern:
    return re.compile(r"`[A-Za-z0-9_.]*\b" + re.escape(helper) + r"\b[^`]*`")


@pytest.mark.parametrize("helper,steps", list(EXPECTED_REFERENCES.items()))
def test_helper_referenced_in_expected_step(helper, steps):
    """Each helper must appear in a backticked code reference in the section
    of one of its expected steps.

    The backtick requirement is the tightening from review finding R3 F4:
    plain substring matching would accept prose mentions ("we call parse_tasks
    later"), but the helper is only actually wired in if it appears as
    invocation syntax (`plan_lib.parse_tasks(...)`)."""
    skill = _read_skill()
    pattern = _backticked_reference_re(helper)
    for step in steps:
        section = _step_section(skill, step)
        if pattern.search(section):
            return
    pytest.fail(
        f"Helper {helper!r} expected in Step section(s) {steps} of SKILL.md "
        f"as a backticked code reference (e.g., `plan_lib.{helper}` or "
        f"`{helper}(...)`) but not found. "
        f"This is the WF2/plan_lib drift guard — either rename in SKILL.md, "
        f"add the actual invocation, or update this test."
    )


def _public_plan_lib_symbols() -> list[str]:
    """Extract names of public functions defined in plan_lib.py (no leading _)."""
    text = PLAN_LIB_PATH.read_text(encoding="utf-8")
    names = []
    for m in re.finditer(r"^def\s+([a-zA-Z][a-zA-Z0-9_]*)\s*\(", text, re.MULTILINE):
        name = m.group(1)
        if name.startswith("_"):
            continue
        names.append(name)
    return names


def test_risk_criteria_canonical_strings_appear_in_docs():
    """The 8 P15 risk criteria strings live in hooks/plan_lib.py::RISK_CRITERIA.
    SKILL.md and docs/principles.md restate them in prose. This test asserts
    each canonical string appears (case-insensitive substring) in both docs,
    catching wording drift.

    The match is intentionally loose (substring, case-insensitive) so prose
    can use natural language ("**Security surface** — auth, secrets, ...")
    around the canonical phrase. Reviewer 3 F1.
    """
    import sys
    HOOKS_DIR = REPO_ROOT / "hooks"
    sys.path.insert(0, str(HOOKS_DIR))
    if "plan_lib" in sys.modules:
        import importlib
        plan_lib = importlib.reload(sys.modules["plan_lib"])
    else:
        import plan_lib

    skill = _read_skill().lower()
    principles = (REPO_ROOT / "docs" / "principles.md").read_text(encoding="utf-8").lower()
    missing = []
    for criterion in plan_lib.RISK_CRITERIA:
        c = criterion.lower()
        if c not in skill:
            missing.append(f"SKILL.md missing canonical criterion: {criterion!r}")
        if c not in principles:
            missing.append(f"docs/principles.md missing canonical criterion: {criterion!r}")
    assert missing == [], "\n".join(missing)


def test_every_public_helper_is_referenced_in_skill():
    """Reverse test: catch dead helpers — a plan_lib export with no SKILL.md
    reference is either unused or never wired in."""
    skill = _read_skill()
    public_symbols = _public_plan_lib_symbols()
    unreferenced = [s for s in public_symbols if s not in skill]
    assert unreferenced == [], (
        f"Public plan_lib symbols not referenced in SKILL.md: {unreferenced}. "
        f"Either wire them into a workflow step or make them private."
    )
