"""Tests for hooks/charter_lib.py — #113 opt-in operating-instructions charter.

Covers:
- import_line / has_import (line-anchored, not substring)
- inject_import: add-when-absent, idempotent, no-clobber, no-trailing-newline safe,
  pre-existing heading without import line (no duplicate heading)
- GATING drift guard: shipped charter is safe; non-vacuity over REAL gating-language
  families (not the guard's own literals); provenance sentinel present on shipped charter
- install(): project scope writes charter + import; idempotent; foreign same-named file
  never clobbered; **global scope refuses without explicit --confirm-global**
"""
import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

import charter_lib  # noqa: E402


# --- import line / detection ------------------------------------------------

def test_import_line_is_rawgentic_namespaced():
    # H2: must NOT be `@operating-instructions.md` (collides with the user's own charter)
    assert charter_lib.import_line() == "@rawgentic-operating-charter.md"
    assert charter_lib.CHARTER_FILENAME == "rawgentic-operating-charter.md"


def test_has_import_is_line_anchored_not_substring():
    # M1: the filename appearing in prose or a path must NOT count as "already imported"
    assert charter_lib.has_import("@rawgentic-operating-charter.md\n") is True
    assert charter_lib.has_import("see @rawgentic-operating-charter.md for details\n") is False
    assert charter_lib.has_import("# backup: rawgentic-operating-charter.md.bak\n") is False
    assert charter_lib.has_import("") is False


# --- inject_import ----------------------------------------------------------

def test_inject_adds_block_when_absent():
    new, changed = charter_lib.inject_import("# Project\n\nsome rules\n")
    assert changed is True
    assert charter_lib.import_line() in new
    assert "some rules" in new  # no-clobber of existing content
    assert charter_lib.has_import(new) is True


def test_inject_is_idempotent():
    once, c1 = charter_lib.inject_import("# Project\n")
    twice, c2 = charter_lib.inject_import(once)
    assert c1 is True and c2 is False
    assert once == twice
    # exactly one import line
    assert once.count(charter_lib.import_line()) == 1


def test_inject_handles_missing_trailing_newline():
    # M1: appending must not concatenate onto the last line
    new, changed = charter_lib.inject_import("last line no newline")
    assert changed is True
    assert "last line no newline" in new
    assert "last line no newline##" not in new
    assert "last line no newline@" not in new
    assert charter_lib.has_import(new)


def test_inject_no_duplicate_heading_when_heading_preexists():
    # M1: a pre-existing "## Operating Instructions" heading must not be duplicated
    base = "# Project\n\n## Operating Instructions\n\n(my own notes)\n"
    new, changed = charter_lib.inject_import(base)
    assert changed is True
    assert charter_lib.has_import(new)
    assert new.count("## Operating Instructions") == 1
    assert "(my own notes)" in new


def test_inject_empty_string():
    new, changed = charter_lib.inject_import("")
    assert changed is True
    assert charter_lib.has_import(new)


# --- gating-language drift guard --------------------------------------------

# Paraphrased REAL gating-language families (NOT the guard's own literals) — proves the
# guard catches the dangerous class, not just that a regex compiles (H1 non-vacuity).
REAL_GATING_SAMPLES = [
    "Always confirm first before taking any outward action.",
    "Get the call before acting on anything irreversible.",
    "Stop and ask the user before you proceed.",
    "You must wait for explicit confirmation from the user.",
    "Wait for approval before merging.",
    "A hold persists until the user clears it.",
    "Commit and push only when the user asks.",
    "Do not proceed without asking first.",
]


@pytest.mark.parametrize("sample", REAL_GATING_SAMPLES)
def test_find_gating_language_catches_real_families(sample):
    hits = charter_lib.find_gating_language(sample)
    assert hits, f"guard missed real gating language: {sample!r}"


def test_assert_charter_safe_raises_on_gating():
    bad = "# Charter\n\n" + "\n".join(REAL_GATING_SAMPLES) + "\n"
    with pytest.raises(ValueError):
        charter_lib.assert_charter_safe(bad)


def test_clean_quality_text_is_not_flagged():
    # a real quality/verification/honesty line must NOT trip the guard (no false positive)
    clean = (
        "Mark every load-bearing claim as confirmed or inferred.\n"
        "Reproduce the reported symptom before you fix it.\n"
        "Get the baseline before you can claim you broke nothing.\n"
    )
    assert charter_lib.find_gating_language(clean) == []


# --- shipped charter --------------------------------------------------------

def test_bundled_charter_exists():
    p = charter_lib.bundled_charter_path()
    assert p.exists(), f"bundled charter missing at {p}"


def test_shipped_charter_is_autonomy_safe():
    # AC3 drift guard: the actual shipped charter must contain NO gating language
    text = charter_lib.bundled_charter_path().read_text(encoding="utf-8")
    hits = charter_lib.find_gating_language(text)
    assert hits == [], f"shipped charter contains gating language: {hits}"


def test_shipped_charter_has_provenance_sentinel():
    # M2: sentinel lets install distinguish our file from a user's same-named file
    text = charter_lib.bundled_charter_path().read_text(encoding="utf-8")
    assert charter_lib.has_provenance_sentinel(text), "shipped charter missing provenance sentinel"


# --- install() --------------------------------------------------------------

def test_install_project_scope_writes_charter_and_import(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("# Proj\n\nrules\n", encoding="utf-8")
    res = charter_lib.install(scope="project", project_root=str(tmp_path))
    charter = tmp_path / charter_lib.CHARTER_FILENAME
    claude = tmp_path / "CLAUDE.md"
    assert charter.exists()
    assert charter_lib.has_import(claude.read_text(encoding="utf-8"))
    assert "rules" in claude.read_text(encoding="utf-8")
    assert res["import_action"] in ("added",)


def test_install_project_scope_idempotent(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("# Proj\n", encoding="utf-8")
    charter_lib.install(scope="project", project_root=str(tmp_path))
    body_after_first = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
    res2 = charter_lib.install(scope="project", project_root=str(tmp_path))
    assert (tmp_path / "CLAUDE.md").read_text(encoding="utf-8") == body_after_first
    assert res2["import_action"] == "present"


def test_install_never_clobbers_foreign_same_named_file(tmp_path):
    # M2: a user's own file named rawgentic-operating-charter.md (no sentinel) is untouched
    (tmp_path / "CLAUDE.md").write_text("# Proj\n", encoding="utf-8")
    foreign = tmp_path / charter_lib.CHARTER_FILENAME
    foreign.write_text("MY OWN CONTENT, not rawgentic's\n", encoding="utf-8")
    res = charter_lib.install(scope="project", project_root=str(tmp_path))
    assert foreign.read_text(encoding="utf-8") == "MY OWN CONTENT, not rawgentic's\n"
    assert res["charter_action"] == "kept-foreign"


def test_install_does_not_wire_import_to_foreign_charter(tmp_path):
    # Step-11 Finding 1: never point CLAUDE.md at an unvalidated foreign file. A foreign
    # charter carrying gating language must NOT get wired into CLAUDE.md.
    (tmp_path / "CLAUDE.md").write_text("# Proj\n", encoding="utf-8")
    foreign = tmp_path / charter_lib.CHARTER_FILENAME
    foreign.write_text("Always stop and ask the user before you proceed.\n", encoding="utf-8")
    res = charter_lib.install(scope="project", project_root=str(tmp_path))
    assert res["import_action"] == "skipped-foreign-charter"
    assert "warning" in res
    assert not charter_lib.has_import((tmp_path / "CLAUDE.md").read_text(encoding="utf-8"))


def test_install_global_refuses_without_confirm(tmp_path):
    # M4: "never silent global" — global scope MUST require explicit confirmation
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / "CLAUDE.md").write_text("# global\n", encoding="utf-8")
    with pytest.raises(charter_lib.GlobalScopeNotConfirmed):
        charter_lib.install(scope="global", project_root=str(tmp_path), home=str(home))
    # nothing written
    assert not (home / ".claude" / charter_lib.CHARTER_FILENAME).exists()


def test_install_global_with_confirm_writes(tmp_path):
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / "CLAUDE.md").write_text("# global\n", encoding="utf-8")
    res = charter_lib.install(
        scope="global", project_root=str(tmp_path), home=str(home), confirm_global=True
    )
    assert (home / ".claude" / charter_lib.CHARTER_FILENAME).exists()
    assert charter_lib.has_import((home / ".claude" / "CLAUDE.md").read_text(encoding="utf-8"))
    assert res["scope"] == "global"


def test_install_rejects_unknown_scope(tmp_path):
    with pytest.raises(ValueError):
        charter_lib.install(scope="banana", project_root=str(tmp_path))
