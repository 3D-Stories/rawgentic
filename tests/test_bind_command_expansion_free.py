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

# The WHOLE shell token after `>>`, quotes included. Whitespace-tolerant so
# `>>claude_docs/…` and `>>  claude_docs/…` cannot slip past. Capturing the entire
# token matters: an earlier version stopped at the closing double quote, so
# `>> "<root>/…jsonl".bak` parsed as the expected target and satisfied every
# assertion while appending somewhere else (#885, Step 11 cross-model review).
APPEND_TARGET_RE = re.compile(r">>\s*(\S+)")

# The EXACT token each bind skill must append to, double quotes included so a
# workspace root containing a space stays correct. An allowlist, not a
# reject-one-spelling check: the first version of this guard asserted
# `not target.startswith("claude_docs/")`, which `./claude_docs/…`,
# `../claude_docs/…`, `sub/claude_docs/…` and a single-quoted bare target all
# satisfied — so the defect could be reintroduced with this file green
# (#885, Step 8a cross-model review).
EXPECTED_APPEND_TOKEN = {
    "switch": '"<root>/claude_docs/session_registry.jsonl"',
    "new-project": '"<WORKSPACE_ROOT>/claude_docs/session_registry.jsonl"',
}

# Tokens that must never satisfy the guard. Regression corpus for BOTH fail-open
# holes found in this guard: the cwd-relative spellings, and the adjacent-suffix
# forms that hid behind a quoted segment.
REJECTED_TARGET_FORMS = (
    "claude_docs/session_registry.jsonl",
    "./claude_docs/session_registry.jsonl",
    "../claude_docs/session_registry.jsonl",
    "project/claude_docs/session_registry.jsonl",
    "'claude_docs/session_registry.jsonl'",
    '"<root>/claude_docs/session_registry.jsonl".bak',
    '"<root>/claude_docs/session_registry.jsonl"$SUFFIX',
)


def _target_is_expected(target: str, expected: str) -> bool:
    """The guard's predicate, isolated so it can be pinned in both directions."""
    return target == expected


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
    for the JSON `cwd` field, so the target reuses that same literal. What that buys
    is bounded, and `why.md` says so: it removes the cwd-DRIFT failure mode. A
    nonexistent root, or one with no `claude_docs/`, now fails loudly because `>>`
    cannot create a missing parent; an existing but WRONG absolute root containing
    `claude_docs/` can still misfile silently, so correctness still depends on
    substituting the resolved directory that holds `.rawgentic_workspace.json`.

    Editing note: `_registry_blocks` reads the skill CORPUS, so a FENCED
    counter-example anywhere in `references/*.md` would be selected and fail this
    guard on its own documentation. `why.md` therefore keeps the bad form in prose,
    unfenced, on purpose. Scope: `>>` only — a truncating single `>` is a different
    defect and is not an append, so it is deliberately out of this guard's name and
    remit.
    """
    for skill in BIND_SKILLS:
        expected = EXPECTED_APPEND_TOKEN[skill]
        # Self-contained non-vacuity. `test_registry_block_exists_for_each_bind_skill`
        # already fails if a skill loses its block, but without this assert THIS test
        # would pass by iterating zero times if the template were removed or
        # mis-fenced — a guard that stops looking looks exactly like a guard that
        # passes, so it does not lean on a sibling test.
        blocks = list(_registry_blocks(skill))
        assert blocks, (
            f"{skill} corpus has no fenced registry-append block, so this guard "
            "would pass without checking anything (#885)"
        )
        for block in blocks:
            tokens = APPEND_TARGET_RE.findall(block)
            assert tokens, (
                f"{skill} registry-append block has a `>>` redirect the guard could "
                f"not parse (guard would be vacuous):\n{block}"
            )
            for token in tokens:
                assert _target_is_expected(token, expected), (
                    f"{skill} registry-append token {token!r} is not the expected "
                    f"{expected!r} -> a bind run from a drifted cwd can silently CREATE "
                    "a registry in the wrong tree and report success (#885). Use the "
                    "absolute workspace-root placeholder the same command already "
                    f"passes for the `cwd` field, double-quoted:\n{block}"
                )


def test_guard_rejects_every_bad_target_form():
    """The guard must reject bad target tokens, not just the bare relative one (#885).

    Regression corpus for the two fail-open holes this guard actually had:

    - it asserted `not target.startswith("claude_docs/")`, which `./claude_docs/…`,
      `../claude_docs/…`, `sub/claude_docs/…` and a single-quoted bare target all
      satisfied. `./` matters most — it is exactly what a reader substituting the cwd
      for the root placeholder produces;
    - its regex stopped at the closing double quote, so `"<root>/…jsonl".bak` and
      `"<root>/…jsonl"$SUFFIX` parsed as the expected target and passed every check
      while the shell appended somewhere else.

    Both were found by cross-model review, not by the author.
    """
    for skill, expected in EXPECTED_APPEND_TOKEN.items():
        for form in REJECTED_TARGET_FORMS:
            tokens = APPEND_TARGET_RE.findall(f'printf \'{{}}\' >> {form}\n')
            assert tokens, f"regex failed to parse the redirect in {form!r}"
            for token in tokens:
                assert not _target_is_expected(token, expected), (
                    f"target token {form!r} would satisfy the {skill} guard -> the "
                    "guard is fail-open and the defect could be reintroduced without "
                    "turning this file red"
                )


def test_bind_command_has_no_backtick_substitution():
    """The registry-append command must contain no backtick command substitution."""
    for skill in BIND_SKILLS:
        for block in _registry_blocks(skill):
            assert "`" not in block, (
                f"{skill} registry-append block contains backtick command "
                f"substitution -> permission prompt:\n{block}"
            )
