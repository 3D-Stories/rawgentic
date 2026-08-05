"""skill_corpus(): the drift-guard text surface for a skill (#157).

The M2 restructure (#158) moves SKILL.md prose into per-skill references/
files. Guards that pin prose must keep matching wherever the prose lives, so
they assert over the CORPUS — SKILL.md plus every references/*.md — instead of
SKILL.md alone. Guards that pin *location* (e.g. "the <headless-mode> pointer
must be in the SKILL.md body") deliberately keep reading SKILL.md directly.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = REPO_ROOT / "skills"


def skill_corpus(skill_name: str) -> str:
    """SKILL.md + sorted references/*.md, newline-joined. Raises if no SKILL.md."""
    skill_dir = SKILLS_DIR / skill_name
    parts = [(skill_dir / "SKILL.md").read_text(encoding="utf-8")]
    refs = skill_dir / "references"
    if refs.is_dir():
        parts.extend(p.read_text(encoding="utf-8") for p in sorted(refs.glob("*.md")))
    return "\n".join(parts)


def skill_files(skill_name: str) -> dict:
    """{relative path -> text} for SKILL.md and every references/*.md (#909).

    The provenance-preserving counterpart to skill_corpus(). Use this — never the
    joined corpus string — for any guard that must know WHICH file a match came
    from: ORDERING pins especially. skill_corpus() discards file boundaries, so
    "these two commands are in the same file, in this order" is not expressible
    over it, and a guard written that way passes vacuously the moment an
    unrelated earlier file happens to contain the first string.

    Keys are posix-relative to the skill dir ("SKILL.md",
    "references/phase-a-stabilize.md") so failure messages name a real path.
    """
    skill_dir = SKILLS_DIR / skill_name
    files = {"SKILL.md": (skill_dir / "SKILL.md").read_text(encoding="utf-8")}
    refs = skill_dir / "references"
    if refs.is_dir():
        for p in sorted(refs.glob("*.md")):
            files[p.relative_to(skill_dir).as_posix()] = p.read_text(encoding="utf-8")
    return files


def assert_ordered_in_one_file(files: dict, first: str, second: str, why: str) -> None:
    """`first` must precede `second`, exactly once each, within ONE file (#909).

    Three assertions, because the weak version of this guard is what #909 had to
    repair: each string occurs exactly once across the whole mapping (a duplicate
    makes "which occurrence?" ambiguous and lets a reference file clone a
    canonical command pair), both land in the same file, and the offsets order
    correctly inside that file.
    """
    hits = {
        path: (text.find(first), text.find(second))
        for path, text in files.items()
    }
    n_first = sum(text.count(first) for text in files.values())
    n_second = sum(text.count(second) for text in files.values())
    assert n_first == 1, f"expected exactly 1 occurrence of {first!r}, found {n_first} — {why}"
    assert n_second == 1, f"expected exactly 1 occurrence of {second!r}, found {n_second} — {why}"

    owners = [p for p, (a, b) in hits.items() if a != -1 and b != -1]
    assert owners, (
        f"{first!r} and {second!r} are not in the SAME file — "
        f"{first!r} in {[p for p, (a, _) in hits.items() if a != -1]}, "
        f"{second!r} in {[p for p, (_, b) in hits.items() if b != -1]}. {why}"
    )
    path = owners[0]
    a, b = hits[path]
    assert a < b, f"in {path}, {first!r} must come BEFORE {second!r}. {why}"
