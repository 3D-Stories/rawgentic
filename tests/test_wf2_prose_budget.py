"""Byte-ceiling guard for the implement-feature (WF2) prose corpus (#856, epic #875).

M1 lesson 1 (epic #875): prose defects need mechanical guards, not vigilance — a
606-line accidental README duplication survived two sessions and a human-adjacent
read. This guard catches that class at commit time: every markdown file in the
WF2 skill directory carries an explicit byte budget, and the corpus carries a
total budget, both set at measured-actual plus modest headroom.

Measured before state — 2026-08-04, tree 050cbe8e (post-M0-retreat, #866):

    file (relative to skills/implement-feature/)   bytes    lines
    SKILL.md                                       43,906     549
    references/steps.md                           154,344   1,933
    references/run-record.md                       22,097     286
    references/whole-issue-delegation.md            7,843     157
    references/state-and-resume.md                  6,167      90
    references/quality-bar.md                       3,360      64
    total (6 files)                               237,717   3,079

Approximately 59.4k tokens at the bytes/4 rule of thumb — a LABELLED
APPROXIMATION only, never asserted: a pinned tokenizer would be a new CI
dependency whose version drift would move the metric (issue #856 AC4).
Pre-retreat comparator: 301,635 bytes across 7 files (#856, 2026-08-03).

Glob-exact accounting: the corpus is discovered by RECURSIVE glob, and the
discovered set must equal the budgeted set exactly — a new file with no budget
entry FAILS (it cannot evade the guard), and a budget entry with no file FAILS
(a rename or the #874 split must update the budget in the same commit, which is
deliberate: the restructure's byte redistribution is forced through this guard).
Recursive rather than flat because tests/corpus.py reads a flat references/*.md
— epic #875 lesson 2: a subdirectory split silently un-pins flat-glob guards,
and this guard must not share that blind spot.

Scope is the skill directory's MARKDOWN prose only — non-.md files are outside
this guard's contract by design.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILL_DIR = REPO_ROOT / "skills" / "implement-feature"

# Actual + modest headroom (2.4%–19% per file, ~3.1% total). Sized so a small
# operative edit fits but a paste-class regression (~30 KB) fails loudly.
# #874: references/steps.md was SPLIT into per-step files. These 19 rows are
# PROVISIONAL — sized at actual + max(1 KiB, 10%) — because the follow-up content
# commit moves blocks between SKILL.md and step-03/08/16, which changes their
# actual sizes. They are recalibrated there, and #874's AC7 stale-ceiling check
# lands only after those sizes are final (so it never runs against rows it would
# itself condemn).
TOTAL_CEILING_BYTES = 245_000
PER_FILE_CEILING_BYTES = {
    "SKILL.md": 46_000,
    "references/step-00-preamble.md": 19_825,
    "references/step-01.md": 5_132,
    "references/step-01b.md": 4_212,
    "references/step-02.md": 10_239,
    "references/step-03.md": 9_434,
    "references/step-04.md": 24_856,
    "references/step-05.md": 8_469,
    "references/step-06.md": 3_763,
    "references/step-07.md": 2_892,
    "references/step-08.md": 24_678,
    "references/step-09.md": 7_134,
    "references/step-10.md": 1_969,
    "references/step-11.md": 15_737,
    "references/step-11_5.md": 5_311,
    "references/step-12.md": 13_919,
    "references/step-13.md": 4_389,
    "references/step-14.md": 4_382,
    "references/step-15.md": 2_288,
    "references/step-16.md": 12_094,
    "references/run-record.md": 23_000,
    "references/whole-issue-delegation.md": 8_500,
    "references/state-and-resume.md": 7_000,
    "references/quality-bar.md": 4_000,
}


def measured_sizes() -> dict:
    """Relative path -> byte size for every .md under the skill dir, recursively."""
    return {
        p.relative_to(SKILL_DIR).as_posix(): p.stat().st_size
        for p in SKILL_DIR.rglob("*.md")
    }


def budget_violations(sizes: dict, budgets: dict, total_ceiling: int) -> list:
    """All budget violations, one message per violation.

    Four classes: UNBUDGETED (globbed file with no budget entry), STALE BUDGET
    (budget entry with no file), OVER CEILING (file over its per-file ceiling),
    OVER TOTAL (corpus over the total ceiling). The three file-specific classes
    name the offending path; the two over-classes carry actual, ceiling, and
    the byte delta (#856 AC4) — OVER TOTAL is corpus-wide, so it names no
    single file. Never quotes file content.
    """
    violations = []
    for path in sorted(sizes.keys() - budgets.keys()):
        violations.append(
            f"UNBUDGETED: {path} ({sizes[path]} bytes) matches the corpus glob but has "
            f"no PER_FILE_CEILING_BYTES entry — a new prose file cannot evade the "
            f"guard; add a budget entry for it in the same commit (#856)."
        )
    for path in sorted(budgets.keys() - sizes.keys()):
        violations.append(
            f"STALE BUDGET: {path} has a budget entry but no file on disk — a rename "
            f"or split (e.g. #874) must update PER_FILE_CEILING_BYTES in the same "
            f"commit."
        )
    for path in sorted(sizes.keys() & budgets.keys()):
        size, ceiling = sizes[path], budgets[path]
        if size > ceiling:
            violations.append(
                f"OVER CEILING: {path} is {size} bytes — {size - ceiling} bytes over "
                f"its {ceiling}-byte ceiling. Trim it, or raise the ceiling in the "
                f"same commit and say why in the PR."
            )
    total = sum(sizes.values())
    if total > total_ceiling:
        violations.append(
            f"OVER TOTAL: the corpus is {total} bytes — {total - total_ceiling} bytes "
            f"over the {total_ceiling}-byte total ceiling. Trim prose, or raise the "
            f"ceiling in the same commit and say why in the PR."
        )
    return violations


# --- unit tests for the helper (synthetic data) ---------------------------


def test_clean_budget_yields_no_violations():
    sizes = {"a.md": 100, "sub/b.md": 200}
    budgets = {"a.md": 150, "sub/b.md": 250}
    assert budget_violations(sizes, budgets, 400) == []


def test_unbudgeted_new_file_is_named():
    sizes = {"a.md": 100, "references/new-step.md": 50}
    budgets = {"a.md": 150}
    violations = budget_violations(sizes, budgets, 400)
    assert len(violations) == 1
    assert violations[0].startswith("UNBUDGETED:")
    assert "references/new-step.md" in violations[0]


def test_stale_budget_entry_is_named():
    sizes = {"a.md": 100}
    budgets = {"a.md": 150, "references/steps.md": 1000}
    violations = budget_violations(sizes, budgets, 400)
    assert len(violations) == 1
    assert violations[0].startswith("STALE BUDGET:")
    assert "references/steps.md" in violations[0]


def test_over_ceiling_file_names_path_and_byte_delta():
    sizes = {"a.md": 180}
    budgets = {"a.md": 150}
    violations = budget_violations(sizes, budgets, 400)
    assert len(violations) == 1
    assert violations[0].startswith("OVER CEILING:")
    assert "a.md" in violations[0]
    assert "30 bytes over" in violations[0]  # the AC4 byte delta
    assert "150" in violations[0]  # the ceiling


def test_over_total_names_byte_delta():
    sizes = {"a.md": 100, "b.md": 100}
    budgets = {"a.md": 150, "b.md": 150}
    violations = budget_violations(sizes, budgets, 180)
    assert len(violations) == 1
    assert violations[0].startswith("OVER TOTAL:")
    assert "20 bytes over" in violations[0]
    assert "180" in violations[0]


# --- the guard: real tree vs the pinned budget -----------------------------


def test_every_corpus_file_is_budgeted_and_within_ceiling():
    violations = [
        v
        for v in budget_violations(
            measured_sizes(), PER_FILE_CEILING_BYTES, TOTAL_CEILING_BYTES
        )
        if not v.startswith("OVER TOTAL:")
    ]
    assert not violations, "\n".join(violations)


def test_corpus_total_stays_within_ceiling():
    violations = [
        v
        for v in budget_violations(
            measured_sizes(), PER_FILE_CEILING_BYTES, TOTAL_CEILING_BYTES
        )
        if v.startswith("OVER TOTAL:")
    ]
    assert not violations, "\n".join(violations)
