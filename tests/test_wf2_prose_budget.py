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
# #874: references/steps.md was SPLIT into per-step files, and these ceilings are
# now RECALIBRATED to actual + allowed_headroom() — no longer the provisional
# actual + max(1 KiB, 10%) that shipped with the split. AC7's STALE CEILING check
# is what forced the recalibration: it condemned all 22 provisional rows, which is
# the guard doing its job rather than a regression.
# #761 raised `references/step-01.md` (and the total with it) across three rounds, all
# operative prose rather than commentary: the task-class resolve-and-snapshot instruction
# (the step had only 256 bytes of headroom); Step 8a's F2 fix, which made the fetch a
# single captured call with an exit-status gate because an unguarded second fetch could
# snapshot an EMPTY body permanently; and Step 11's R2-1/R2-6 fixes, which gate the `jq`
# extraction through a temp-file rename and clean up the captured body.
#
# Measured at the time of writing (Step 11 DIFF-4 asked for the numbers to be recorded
# beside the constants — the finding's premise that they are unverifiable is declined,
# since the STALE CEILING check below MECHANICALLY enforces
# `ceiling <= actual + allowed_headroom()` on every run, which is stronger than a
# comment; the numbers are recorded anyway because they help a human reader):
#   references/step-01.md  actual 7_074 + headroom   354 -> ceiling   7_428
#   corpus total           actual 247_398 + headroom 4_948 -> ceiling 252_346
TOTAL_CEILING_BYTES = 252_346
PER_FILE_CEILING_BYTES = {
    "SKILL.md": 46_099,
    "references/quality-bar.md": 3_616,
    "references/run-record.md": 23_202,
    "references/state-and-resume.md": 6_476,
    "references/step-00-preamble.md": 18_925,
    "references/step-01.md": 7_428,
    "references/step-01b.md": 3_444,
    "references/step-02.md": 9_676,
    "references/step-03.md": 8_831,
    "references/step-04.md": 23_727,
    "references/step-05.md": 7_818,
    "references/step-06.md": 2_995,
    "references/step-07.md": 2_124,
    "references/step-08.md": 23_557,
    "references/step-09.md": 6_416,
    "references/step-10.md": 1_201,
    "references/step-11.md": 15_023,
    "references/step-11_5.md": 4_543,
    "references/step-12.md": 14_999,
    "references/step-13.md": 3_621,
    "references/step-14.md": 3_614,
    "references/step-15.md": 1_520,
    "references/step-16.md": 11_545,
    "references/whole-issue-delegation.md": 8_236,
}


# AC7 (#874): allowed headroom above actual before a ceiling reads as STALE.
# Hybrid on purpose. A percentage alone punishes small files (5% of 400 bytes is
# 20, which no real ceiling fits inside); an absolute alone becomes
# disproportionately strict on large ones. The aggregate margin is deliberately
# TIGHTER than per-file so many individually-legal gaps cannot accumulate into
# paste-sized corpus slack.
STALE_CEILING_PCT = 0.05
STALE_CEILING_MIN_BYTES = 256
STALE_TOTAL_PCT = 0.02
STALE_TOTAL_MIN_BYTES = 1_024


def allowed_headroom(actual: int, pct: float, floor: int) -> int:
    """Bytes a ceiling may sit above actual before it reads as stale."""
    return max(floor, -(-int(actual) * int(pct * 10_000) // 10_000))  # ceil, no float drift


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
        else:
            # AC7: the SYMMETRIC direction. A ceiling far above actual is
            # unguarded slack — a paste-class regression would fit under it
            # while this guard stayed green. Only reachable when NOT over
            # ceiling, so the two directions are mutually exclusive by
            # construction.
            allowed = allowed_headroom(size, STALE_CEILING_PCT, STALE_CEILING_MIN_BYTES)
            excess = ceiling - size - allowed
            if excess > 0:
                violations.append(
                    f"STALE CEILING: {path} is {size} bytes but its ceiling is "
                    f"{ceiling} — {ceiling - size} bytes of headroom against an "
                    f"allowed {allowed} ({excess} bytes too much). Lower the "
                    f"ceiling to actual + allowed in the same commit as the "
                    f"shrink (#874 AC7)."
                )
    if not sizes:
        # Review F4: `elif sizes:` previously SKIPPED the aggregate check on an
        # empty mapping, and the live-corpus test filters for "STALE" messages —
        # so an absent, renamed, unreadable or mis-resolved directory measured as
        # zero files and passed as "no stale ceilings". Empty measurement is a
        # broken measurement, not a clean corpus.
        violations.append(
            "EMPTY CORPUS: no markdown files were discovered under the skill "
            "directory — the glob resolved to nothing, which is a broken "
            "measurement and never a clean result. Check the path."
        )
        return violations
    total = sum(sizes.values())
    if total > total_ceiling:
        violations.append(
            f"OVER TOTAL: the corpus is {total} bytes — {total - total_ceiling} bytes "
            f"over the {total_ceiling}-byte total ceiling. Trim prose, or raise the "
            f"ceiling in the same commit and say why in the PR."
        )
    else:
        allowed = allowed_headroom(total, STALE_TOTAL_PCT, STALE_TOTAL_MIN_BYTES)
        excess = total_ceiling - total - allowed
        if excess > 0:
            violations.append(
                f"STALE TOTAL: the corpus is {total} bytes but the total ceiling is "
                f"{total_ceiling} — {total_ceiling - total} bytes of headroom against "
                f"an allowed {allowed} ({excess} bytes too much). Lower the total "
                f"ceiling in the same commit as the shrink (#874 AC7)."
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


# --- AC7 (#874): STALE CEILING — a ceiling far above actual is paste-sized slack ---
# Red-before-green: these were written and observed FAILING before the
# allowed-headroom logic existed in budget_violations.


def test_stale_ceiling_flags_a_ceiling_far_above_actual():
    """The #874 split shrinks these files; a ceiling left at the old size would
    leave ~30 KB of unguarded slack while every other class stays green."""
    sizes = {"a.md": 1_000}
    budgets = {"a.md": 40_000}
    v = budget_violations(sizes, budgets, 100_000)
    assert any("STALE CEILING" in m for m in v), v
    msg = next(m for m in v if "STALE CEILING" in m)
    for token in ("a.md", "1000", "40000"):
        assert token in msg.replace("_", ""), f"{token!r} missing from: {msg}"


def test_within_margin_ceiling_is_not_flagged():
    """Modest headroom must stay legal or every ordinary edit trips the guard.
    5% of 100_000 is 5_000, so a 4_000-byte gap is inside the margin."""
    sizes = {"a.md": 100_000}
    budgets = {"a.md": 104_000}
    assert [m for m in budget_violations(sizes, budgets, 200_000)
            if "STALE CEILING" in m] == []


def test_small_file_gets_an_absolute_floor_not_a_percentage():
    """A percentage alone punishes small files: 5% of 400 is 20 bytes, which no
    real ceiling can sit inside. The floor is 256 bytes."""
    sizes = {"tiny.md": 400}
    budgets = {"tiny.md": 640}          # 240-byte gap, under the 256 floor
    assert [m for m in budget_violations(sizes, budgets, 10_000)
            if "STALE CEILING" in m] == []
    budgets_over = {"tiny.md": 900}     # 500-byte gap, over the floor
    assert any("STALE CEILING" in m
               for m in budget_violations(sizes, budgets_over, 10_000))


def test_total_ceiling_has_the_same_symmetric_check_with_a_tighter_margin():
    """The aggregate analog (AC7). Deliberately tighter (2%) than per-file (5%)
    so many individually-legal gaps cannot accumulate into corpus-wide slack."""
    sizes = {"a.md": 1_000, "b.md": 1_000}       # total 2_000
    v = budget_violations(sizes, {"a.md": 1_100, "b.md": 1_100}, 50_000)
    assert any("STALE TOTAL" in m for m in v), v
    # 2% of 2_000 is 40, so the 1_024 floor governs: a 1_000-byte gap is legal.
    assert [m for m in budget_violations(sizes, {"a.md": 1_100, "b.md": 1_100}, 3_000)
            if "STALE TOTAL" in m] == []


def test_over_and_stale_are_mutually_exclusive_per_file():
    """A file cannot be both over its ceiling and far under it — the two
    directions must never both fire for one path."""
    sizes = {"a.md": 100}
    v = budget_violations(sizes, {"a.md": 50}, 10_000)      # over
    assert any("OVER CEILING" in m for m in v)
    assert not any("STALE CEILING" in m for m in v)


def test_empty_corpus_is_a_violation_not_a_pass():
    """Review F4: zero discovered files must fail loudly."""
    v = budget_violations({}, {}, 100)
    assert any("EMPTY CORPUS" in m for m in v), v


def test_live_corpus_has_no_stale_ceilings():
    """The real budgets must satisfy the margin — this is what forces the #874
    split's shrunken files to carry recalibrated ceilings rather than the
    monolith's old slack."""
    sizes = measured_sizes()
    # Review F4: assert the measurement is real before filtering it, so an empty
    # glob cannot read as "no stale ceilings".
    assert len(sizes) >= 20, f"corpus measurement looks broken: {sorted(sizes)}"
    v = [m for m in budget_violations(sizes, PER_FILE_CEILING_BYTES,
                                      TOTAL_CEILING_BYTES)
         if "STALE" in m]
    assert v == [], "stale ceilings in the live budget:\n" + "\n".join(v)
