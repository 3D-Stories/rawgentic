"""#552: skill `name:` frontmatter must be bare (no `rawgentic:` prefix, no colon).

Current Claude Code builds construct the public command as `<plugin>:<skill-name>`
and forbid a colon inside the `name:` field. An embedded prefix gets sanitized
(`rawgentic:switch` -> `rawgentic-switch`) and then namespaced, doubling the
command to `/rawgentic:rawgentic-switch` and unregistering every old-style
`/rawgentic:<name>` invocation. The bare name IS the contract: the harness adds
the namespace itself (proof: `sync-security-patterns` registers clean).
"""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SKILLS = REPO / "skills"

NAME_RE = re.compile(r"^name:\s*(.+?)\s*$", re.M)


def _skill_dirs():
    return sorted(p.parent for p in SKILLS.glob("*/SKILL.md"))


def test_skill_names_are_bare_directory_names():
    violations = []
    for d in _skill_dirs():
        text = (d / "SKILL.md").read_text(encoding="utf-8")
        m = NAME_RE.search(text)
        if m is None:
            violations.append(f"{d.name}: no name: field")
            continue
        name = m.group(1)
        if ":" in name:
            violations.append(f"{d.name}: name contains a colon ({name!r})")
        elif name != d.name:
            violations.append(f"{d.name}: name {name!r} != directory name")
    assert not violations, (
        "skill name: fields must be bare directory names (harness namespaces "
        "them as /rawgentic:<name>; a colon in the field doubles the command "
        "— #552):\n" + "\n".join(violations)
    )
