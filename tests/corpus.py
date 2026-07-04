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
