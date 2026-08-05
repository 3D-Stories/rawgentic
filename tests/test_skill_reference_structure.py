"""Repo-wide structural guards for skill `references/` directories (#909).

Two invariants that the docs state and nothing enforced until now.

**1. references/ is FLAT.** `tests/corpus.py::skill_corpus` (and `skill_files`)
discover reference prose with a FLAT `references/*.md` glob. A file placed in a
subdirectory is therefore invisible to every content pin that reads the corpus —
prose silently loses its guard while CI stays green. That is epic #875 lesson 2,
learned the hard way during the #874 split, and it is why
`tests/test_wf2_prose_budget.py` globs RECURSIVELY.

This guard must make the same choice, for the same reason: written with the flat
glob it is meant to police, it would inspect only direct children and never see
the nested file — passing on exactly the layout it exists to reject. So the walk
here is recursive and the assertion is that every discovered descendant sits
directly under `references/`.

**2. `${CLAUDE_PLUGIN_ROOT}` never appears in a reference file.** Claude Code
substitutes it in a SKILL.md *body* loaded as a skill, but NOT in any file opened
with the Read tool — and `references/*.md` are always Read (repo CLAUDE.md §1,
measured on 2.1.220). A command carrying it inside a reference would ship the
literal `${CLAUDE_PLUGIN_ROOT}` to a shell. Measured at the time of writing: zero
occurrences across all `skills/*/references/*.md`, while three SKILL.md bodies use
it legitimately and are out of scope here.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = REPO_ROOT / "skills"

PLUGIN_ROOT_TOKEN = "${CLAUDE_PLUGIN_ROOT}"


def nested_reference_violations(refs_dir: Path) -> list:
    """Reference files that are not DIRECT .md children of references/.

    Recursive by construction — see the module docstring. Returns one message per
    offender, naming the path relative to references/. Non-markdown assets at the
    top level are NOT offenders: the invariant is flatness, not file type.
    """
    if not refs_dir.is_dir():
        return []
    violations = []
    for path in sorted(refs_dir.rglob("*")):
        if path.is_dir():
            continue
        rel = path.relative_to(refs_dir)
        if len(rel.parts) > 1:
            violations.append(
                f"NESTED REFERENCE: {refs_dir.parent.name}/references/{rel.as_posix()} "
                f"is not a direct child — tests/corpus.py discovers reference prose "
                f"with a FLAT references/*.md glob, so a nested file silently "
                f"un-pins every content guard over it (#874 lesson 2)."
            )
    return violations


# --- unit tests over the collector (synthetic trees) ---------------------


def test_flat_references_yield_no_violations(tmp_path):
    refs = tmp_path / "demo" / "references"
    refs.mkdir(parents=True)
    (refs / "a.md").write_text("x", encoding="utf-8")
    (refs / "b.md").write_text("y", encoding="utf-8")
    assert nested_reference_violations(refs) == []


def test_nested_reference_is_caught(tmp_path):
    """The fixture a FLAT-glob implementation would silently pass."""
    refs = tmp_path / "demo" / "references"
    (refs / "sub").mkdir(parents=True)
    (refs / "ok.md").write_text("x", encoding="utf-8")
    (refs / "sub" / "hidden.md").write_text("y", encoding="utf-8")
    v = nested_reference_violations(refs)
    assert len(v) == 1 and v[0].startswith("NESTED REFERENCE:"), v
    assert "sub/hidden.md" in v[0]


def test_a_non_markdown_asset_is_allowed_at_the_top_level():
    """Deliberately NOT a violation, and the live tree is why.

    `skills/epic-post-mortem/references/artifact-template.html` is a real,
    legitimate template asset. The documented invariant is FLATNESS — a
    subdirectory defeats the corpus glob — not "markdown only". An earlier draft
    of this guard also rejected non-.md files and immediately failed on that
    template, which is the guard over-reaching past the constraint it enforces.
    """
    refs = SKILLS_DIR / "epic-post-mortem" / "references"
    assert (refs / "artifact-template.html").is_file(), (
        "the file this test documents has moved; re-check the reasoning above"
    )
    assert nested_reference_violations(refs) == []


def test_absent_references_dir_is_not_a_violation(tmp_path):
    assert nested_reference_violations(tmp_path / "demo" / "references") == []


# --- the live tree ------------------------------------------------------


def test_every_skill_references_dir_is_flat():
    violations = []
    for skill_md in sorted(SKILLS_DIR.glob("*/SKILL.md")):
        violations.extend(nested_reference_violations(skill_md.parent / "references"))
    assert not violations, "\n".join(violations)


def test_no_reference_file_uses_the_plugin_root_token():
    """EVERY regular file below references/, not just direct .md children.

    An earlier form globbed `*/references/*.md`, while this same change explicitly
    permits top-level non-markdown assets — so an .html, .sh, .txt or template
    reference carrying the token passed CI, and if its content were used as a
    command the unsubstituted literal would reach a shell (#909 review F4).
    Read as BYTES so a non-UTF-8 asset cannot raise instead of being checked.
    """
    needle = PLUGIN_ROOT_TOKEN.encode()
    offenders = []
    for skill_md in sorted(SKILLS_DIR.glob("*/SKILL.md")):
        refs = skill_md.parent / "references"
        if not refs.is_dir():
            continue
        for p in sorted(refs.rglob("*")):
            if p.is_file() and needle in p.read_bytes():
                offenders.append(p.relative_to(SKILLS_DIR).as_posix())
    assert not offenders, (
        "these reference files contain ${CLAUDE_PLUGIN_ROOT}, which is NOT "
        "substituted in a file opened with the Read tool — the literal token would "
        "reach a shell. Keep such commands in the SKILL.md body (repo CLAUDE.md §1): "
        + ", ".join(offenders)
    )


def test_the_plugin_root_scan_covers_non_markdown_assets(tmp_path, monkeypatch):
    """Proves the scan is not .md-only, using the permitted-asset shape."""
    skill = tmp_path / "demo"
    (skill / "references").mkdir(parents=True)
    (skill / "SKILL.md").write_text("x", encoding="utf-8")
    (skill / "references" / "template.html").write_text(
        "<pre>" + PLUGIN_ROOT_TOKEN + "/scripts/x.py</pre>", encoding="utf-8"
    )
    monkeypatch.setattr("tests.test_skill_reference_structure.SKILLS_DIR", tmp_path)
    import pytest

    with pytest.raises(AssertionError, match=r"template\.html"):
        test_no_reference_file_uses_the_plugin_root_token()


def test_the_reference_tree_was_actually_walked():
    """Guards the guard: a glob finding nothing must not read as clean."""
    total = len(list(SKILLS_DIR.glob("*/references/*.md")))
    assert total >= 20, f"only {total} reference files discovered — check the path"
