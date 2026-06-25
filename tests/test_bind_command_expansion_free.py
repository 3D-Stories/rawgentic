"""Bind-command guard: the session_registry append must be expansion-free.

The `/rawgentic:switch` and `/rawgentic:new-project` bind step appends a line to
`claude_docs/session_registry.jsonl`. If that Bash command contains shell command
substitution (`$(...)` or backticks), Claude Code's permission system flags it
"Contains expansion" and ALWAYS prompts — such a command can NOT be auto-approved
via any `permissions.allow` rule (confirmed against Claude Code permission docs).

Keeping the registry-append command expansion-free (a leading allowlisted binary
such as `printf`, with literal values and only `>>` redirection) lets a user's
`Bash(printf:*)` / `Bash(date:*)` allow rules auto-approve it, so the bind step
stops prompting on every `/rawgentic:switch`.

This guard fails if either skill reintroduces command substitution into its
registry-append block.
"""
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILLS = REPO_ROOT / "skills"

# Skills whose bind step appends to the session registry from a fenced code block.
BIND_SKILLS = ["switch", "new-project"]

# `$(` opens command substitution; a backtick inside a code fence is backtick substitution.
CMD_SUBST_RE = re.compile(r"\$\(")


def _fenced_blocks(text: str):
    """Yield the body (str) of each ``` fenced code block, indentation-tolerant.

    A line-based parser (not a regex) so adjacent fences and 3-space-indented
    fences inside numbered list items pair correctly.
    """
    blocks, cur = [], None
    for line in text.splitlines():
        stripped = line.strip()
        if cur is None:
            if stripped.startswith("```"):
                cur = []
        elif stripped == "```":
            blocks.append("\n".join(cur))
            cur = None
        else:
            cur.append(line)
    return blocks


def _registry_blocks(skill: str):
    """Return fenced blocks in <skill>/SKILL.md that append to the session registry.

    Selects by content (`session_registry.jsonl` only appears in the bash append
    command, not in the adjacent JSON example), so it is robust to the fence label.
    """
    text = (SKILLS / skill / "SKILL.md").read_text()
    return [b for b in _fenced_blocks(text) if "session_registry.jsonl" in b]


def test_registry_block_exists_for_each_bind_skill():
    """Sanity: the guard is non-vacuous — each bind skill really has a registry-append block."""
    for skill in BIND_SKILLS:
        assert _registry_blocks(skill), (
            f"{skill}/SKILL.md has no fenced block appending to session_registry.jsonl "
            "(guard would be vacuous)"
        )


def test_bind_command_has_no_command_substitution():
    """The registry-append command must contain no $(...) command substitution."""
    for skill in BIND_SKILLS:
        for block in _registry_blocks(skill):
            assert not CMD_SUBST_RE.search(block), (
                f"{skill}/SKILL.md registry-append block contains $(...) command "
                "substitution -> Claude Code flags it 'Contains expansion' and always "
                f"prompts (cannot be allowlisted):\n{block}"
            )


def test_bind_command_has_no_backtick_substitution():
    """The registry-append command must contain no backtick command substitution."""
    for skill in BIND_SKILLS:
        for block in _registry_blocks(skill):
            assert "`" not in block, (
                f"{skill}/SKILL.md registry-append block contains backtick command "
                f"substitution -> permission prompt:\n{block}"
            )
