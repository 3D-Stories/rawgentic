"""Tests for WF2 Step 8 parallel_group support (PR 3a, optimization C).

Covers the two additive plan_lib pieces that make parallel_group a real,
validated concept (execution stays serial until the worktree layer in #85):
- parse_tasks captures optional `parallel_group` + `files` per task
  (purely additive; the riskLevel fail-closed contract is unchanged)
- validate_parallel_groups(tasks) -> (all_eligible, conflicts): proves
  same-group tasks are file-disjoint, so a group can NEVER be parallelized
  into a collision. Cannot-prove-disjointness (missing files, glob, dir,
  overlap) degrades to NOT-eligible (caller runs it sequentially).
"""
import sys
from pathlib import Path

HOOKS_DIR = Path(__file__).resolve().parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

import plan_lib  # noqa: E402


# --- parse_tasks: optional parallel_group + files ---

def test_parse_tasks_captures_parallel_group_and_files():
    plan = """### Task 1: do thing
- riskLevel: standard
- parallel_group: g1
- files: src/a.py, src/b.py
"""
    (task,) = plan_lib.parse_tasks(plan)
    assert task.parallel_group == "g1"
    assert task.files == ("src/a.py", "src/b.py")


def test_parse_tasks_back_compat_no_parallel_fields():
    plan = """### Task 1: do thing
- riskLevel: high (security surface)
"""
    (task,) = plan_lib.parse_tasks(plan)
    assert task.parallel_group is None
    assert task.files == ()
    # everything else unchanged
    assert task.risk_level == "high"
    assert task.reason == "security surface"


def test_parse_tasks_risklevel_contract_unchanged_with_parallel_fields():
    """Partial annotation still fail-closes even when the untagged task carries
    parallel_group/files — the additive fields must not weaken the contract.
    (A *fully* untagged plan is the pre-P15 migration, not a partial bug.)"""
    plan = """### Task 1: tagged
- riskLevel: standard

### Task 2: untagged but has parallel fields
- parallel_group: g1
- files: a.py
"""
    import pytest
    with pytest.raises(plan_lib.PlanFormatError):
        plan_lib.parse_tasks(plan)


# --- validate_parallel_groups ---

def _t(id_, group=None, files=()):
    return plan_lib.Task(id=id_, title=f"task {id_}", risk_level="standard",
                         reason=None, parallel_group=group, files=tuple(files))


def test_validate_parallel_groups_disjoint_ok():
    tasks = [_t("1", "g1", ["a.py"]), _t("2", "g1", ["b.py"])]
    ok, conflicts = plan_lib.validate_parallel_groups(tasks)
    assert ok is True
    assert conflicts == []


def test_validate_parallel_groups_overlap_fails():
    tasks = [_t("1", "g1", ["a.py", "shared.py"]), _t("2", "g1", ["shared.py"])]
    ok, conflicts = plan_lib.validate_parallel_groups(tasks)
    assert ok is False
    assert any("shared.py" in c for c in conflicts)


def test_validate_parallel_groups_missing_files_not_eligible():
    tasks = [_t("1", "g1", ["a.py"]), _t("2", "g1", [])]
    ok, conflicts = plan_lib.validate_parallel_groups(tasks)
    assert ok is False
    assert any("2" in c for c in conflicts)


def test_validate_parallel_groups_ignores_ungrouped():
    """Ungrouped tasks (parallel_group=None) are never compared, even if their
    declared files collide — they run sequentially anyway."""
    tasks = [_t("1", None, ["same.py"]), _t("2", None, ["same.py"])]
    ok, conflicts = plan_lib.validate_parallel_groups(tasks)
    assert ok is True
    assert conflicts == []


def test_validate_parallel_groups_normalizes_paths():
    """`./a.py` and `a.py` are the same file — overlap must still be caught."""
    tasks = [_t("1", "g1", ["./a.py"]), _t("2", "g1", ["a.py"])]
    ok, conflicts = plan_lib.validate_parallel_groups(tasks)
    assert ok is False
    assert any("a.py" in c for c in conflicts)


def test_validate_parallel_groups_rejects_glob():
    """A glob declaration can't be proven disjoint statically -> not eligible."""
    tasks = [_t("1", "g1", ["src/*.py"]), _t("2", "g1", ["other.py"])]
    ok, conflicts = plan_lib.validate_parallel_groups(tasks)
    assert ok is False
    assert any("glob" in c.lower() for c in conflicts)


def test_validate_parallel_groups_rejects_directory():
    """A directory declaration (trailing slash) can't be proven disjoint -> not eligible."""
    tasks = [_t("1", "g1", ["src/"]), _t("2", "g1", ["other.py"])]
    ok, conflicts = plan_lib.validate_parallel_groups(tasks)
    assert ok is False
    assert any("director" in c.lower() for c in conflicts)


def test_validate_parallel_groups_rejects_absolute_path():
    """An absolute path can't be proven disjoint from a repo-relative one -> not eligible."""
    tasks = [_t("1", "g1", ["/etc/thing.py"]), _t("2", "g1", ["thing.py"])]
    ok, conflicts = plan_lib.validate_parallel_groups(tasks)
    assert ok is False
    assert any("absolute" in c.lower() for c in conflicts)


def test_validate_parallel_groups_case_insensitive_overlap():
    """Same file via case variants must be flagged (safe under a case-insensitive FS)."""
    tasks = [_t("1", "g1", ["A.py"]), _t("2", "g1", ["a.py"])]
    ok, conflicts = plan_lib.validate_parallel_groups(tasks)
    assert ok is False
    assert any("a.py" in c.lower() for c in conflicts)


def test_validate_parallel_groups_directory_containment_overlap():
    """A bare directory token containing another task's file is an overlap."""
    tasks = [_t("1", "g1", ["src"]), _t("2", "g1", ["src/a.py"])]
    ok, conflicts = plan_lib.validate_parallel_groups(tasks)
    assert ok is False
    assert any("src" in c for c in conflicts)


def test_validate_parallel_groups_rejects_parent_escape():
    """`../x` can re-enter the repo and alias another declared file -> reject as unprovable."""
    tasks = [_t("1", "g1", ["../other/a.py"]), _t("2", "g1", ["a.py"])]
    ok, conflicts = plan_lib.validate_parallel_groups(tasks)
    assert ok is False
    assert any("repo-relative" in c.lower() for c in conflicts)


def test_validate_parallel_groups_rejects_dot_root():
    """`.` (or anything normalizing to it) is the repo root — ancestor of everything -> reject."""
    tasks = [_t("1", "g1", ["."]), _t("2", "g1", ["src/a.py"])]
    ok, conflicts = plan_lib.validate_parallel_groups(tasks)
    assert ok is False


def test_validate_parallel_groups_singleton_and_empty_ok():
    """No parallel groups, or a one-member group, is trivially fine."""
    assert plan_lib.validate_parallel_groups([]) == (True, [])
    assert plan_lib.validate_parallel_groups([_t("1", "g1", ["a.py"])]) == (True, [])
    assert plan_lib.validate_parallel_groups([_t("1"), _t("2")]) == (True, [])
