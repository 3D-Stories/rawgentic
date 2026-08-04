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

It also guards the append TARGET (#885). The redirect was a bare, cwd-relative
`>> claude_docs/session_registry.jsonl`, while the same command already carried
the absolute workspace root as a printf argument for the JSON `cwd` field. With
cwd drifted into a project directory, `>>` CREATES a registry in the wrong tree,
exits 0, and a `tail -1` echoes the line back — indistinguishable from the
documented first-bind case. Reads fail loudly; that one write did not.
"""
import re

from tests.corpus import skill_corpus

# Skills whose bind step appends to the session registry from a fenced code block.
BIND_SKILLS = ["switch", "new-project"]

# `$(` opens command substitution; a backtick inside a code fence is backtick substitution.
CMD_SUBST_RE = re.compile(r"\$\(")

# Every `>>` redirect target in the block, quoted or bare. Whitespace-tolerant so
# `>>claude_docs/…` and `>>  claude_docs/…` cannot slip past (#885).
APPEND_TARGET_RE = re.compile(r">>\s*\"?([^\"\s]+)")


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
    """Return fenced blocks in the skill's corpus that append to the session registry.

    Selects by content (`session_registry.jsonl` only appears in the bash append
    command, not in the adjacent JSON example), so it is robust to the fence label.
    Reads the corpus (SKILL.md + references/) so a #158 prose move keeps the guard live.
    """
    return [b for b in _fenced_blocks(skill_corpus(skill)) if "session_registry.jsonl" in b]


def test_registry_block_exists_for_each_bind_skill():
    """Sanity: the guard is non-vacuous — each bind skill really has a registry-append block."""
    for skill in BIND_SKILLS:
        assert _registry_blocks(skill), (
            f"{skill} corpus has no fenced block appending to session_registry.jsonl "
            "(guard would be vacuous)"
        )


def test_bind_command_has_no_command_substitution():
    """The registry-append command must contain no $(...) command substitution."""
    for skill in BIND_SKILLS:
        for block in _registry_blocks(skill):
            assert not CMD_SUBST_RE.search(block), (
                f"{skill} registry-append block contains $(...) command "
                "substitution -> Claude Code flags it 'Contains expansion' and always "
                f"prompts (cannot be allowlisted):\n{block}"
            )


def test_bind_append_target_is_absolute():
    """The registry-append target must not be cwd-relative (#885).

    The bind step assumes cwd is the workspace root, but nothing enforces that at
    the write, and the Bash tool's cwd persists across calls within a session. A
    relative `>>` therefore lands — silently, exit 0 — in whatever tree cwd happens
    to be, and because `>>` CREATES the file, a wrong-repo creation looks exactly
    like the documented "create it if absent" first bind.

    Both skills already interpolate the absolute workspace root as a printf argument
    for the JSON `cwd` field, so the target reuses that same literal. A wrong root is
    then visible in the registry line itself instead of misfiling in silence.
    """
    for skill in BIND_SKILLS:
        for block in _registry_blocks(skill):
            targets = APPEND_TARGET_RE.findall(block)
            assert targets, (
                f"{skill} registry-append block has a `>>` redirect the guard could "
                f"not parse (guard would be vacuous):\n{block}"
            )
            for target in targets:
                assert not target.startswith("claude_docs/"), (
                    f"{skill} registry-append target {target!r} is cwd-relative -> a "
                    "bind run from a drifted cwd silently CREATES a registry in the "
                    "wrong tree and reports success (#885). Prefix it with the "
                    "absolute workspace-root placeholder the same command already "
                    f"passes for the `cwd` field:\n{block}"
                )


def test_bind_command_has_no_backtick_substitution():
    """The registry-append command must contain no backtick command substitution."""
    for skill in BIND_SKILLS:
        for block in _registry_blocks(skill):
            assert "`" not in block, (
                f"{skill} registry-append block contains backtick command "
                f"substitution -> permission prompt:\n{block}"
            )
