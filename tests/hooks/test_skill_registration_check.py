"""Tests for hooks/skill_registration_check.py — skill-registration surface checker (#528).

The checker COMPUTES every registration expectation from the tree the way the
#271 guards do (whitelist == disk glob, README strings rendered from computed
values, canary from the line-anchored corpus regex) and grep-discovers the
hand-pinned count copies (the test_interview_skill.py-class stragglers), so a
new-skill registration walk is one command instead of a prose checklist.

Fail-mode: fail-CLOSED — an unreadable/malformed surface is reported STALE
(the checker's whole job is to fail loudly; a silent pass on a parse error
would hide exactly the drift it exists to find).
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

import skill_registration_check as src  # noqa: E402

CLI = HOOKS_DIR / "skill_registration_check.py"
REPO_ROOT = Path(__file__).resolve().parent.parent.parent


# --- fixture tree ------------------------------------------------------------

def d(s):
    """Decode underscored fixture pin-strings. The checker's sweep scans THIS
    repo's tests/**/*.py — fixture literals like "2_SDLC_workflow_skills" (decoded) in
    this file's source would be swept as hand-pins, so every fixture pin is
    written underscored (sweep-invisible) and decoded to spaces at runtime."""
    return s.replace("_", " ")


FRONTMATTER = """---
name: {name}
description: test skill
argument-hint: none
---

body
"""

SYNC_SCRIPT = '''"""fixture sync script."""
MANIFEST = {
    "config-loading": {
        "config-loading.md": ["alpha"],
    },
}
'''

HEADLESS_TEST = """class TestSkillCountCanary:
    EXPECTED_CONFIG_LOADING_COUNT = 1  # fixture
"""

README = d("""# Fixture

**2_SDLC_workflow_skills + 0_workspace_management + 0_planning_skill + 0_security_skills**

provides_2_skills

All_2_config-driven_skills share a block.

0/2_skills_have_evals.json

## Changelog

- old entry: provides_99_skills, 7/9_skills_have_evals.json, 1_SDLC_workflow_skills
""")

DESCRIPTION = d("2_SDLC_workflow_skills + 0_workspace_management + "
                "0_planning_skill + 0_security_skills for testing")


def make_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    for name, config_loading in (("alpha", True), ("beta", False)):
        sd = root / "skills" / name
        sd.mkdir(parents=True)
        body = FRONTMATTER.format(name=name)
        if config_loading:
            body += "\n<config-loading>\nstuff\n</config-loading>\n"
        (sd / "SKILL.md").write_text(body)
        mirror = root / "plugins" / "rawgentic" / "skills"
        mirror.mkdir(parents=True, exist_ok=True)
        (mirror / name).symlink_to(Path("..") / ".." / ".." / "skills" / name)
    cp = root / ".claude-plugin"
    cp.mkdir()
    (cp / "marketplace.json").write_text(json.dumps(
        {"plugins": [{"skills": ["./skills/alpha", "./skills/beta"],
                      "description": DESCRIPTION}]}))
    (cp / "plugin.json").write_text(json.dumps({"description": DESCRIPTION}))
    codex = root / "plugins" / "rawgentic" / ".codex-plugin"
    codex.mkdir(parents=True)
    (codex / "plugin.json").write_text(json.dumps({"description": DESCRIPTION}))
    (root / "README.md").write_text(README)
    th = root / "tests" / "hooks"
    th.mkdir(parents=True)
    (th / "test_headless.py").write_text(HEADLESS_TEST)
    scripts = root / "scripts"
    scripts.mkdir()
    (scripts / "sync_shared_blocks.py").write_text(SYNC_SCRIPT)
    (root / ".rawgentic.json").write_text(json.dumps(
        {"project": {"description": DESCRIPTION}}))
    return root


def stale(findings):
    return [f for f in findings if not f.ok]


def surfaces(findings):
    return {f.surface for f in findings}


# --- happy path ---------------------------------------------------------------

def test_clean_fixture_all_ok(tmp_path):
    root = make_repo(tmp_path)
    findings = src.run_checks(root, "alpha")
    assert findings and not stale(findings)


def test_clean_fixture_covers_all_surfaces(tmp_path):
    root = make_repo(tmp_path)
    got = surfaces(src.run_checks(root, "alpha"))
    for expected in ("frontmatter", "whitelist", "whitelist-vs-disk",
                     "codex-symlink", "manifest", "canary", "readme-provides",
                     "readme-evals", "breakdown-sum"):
        assert expected in got, f"missing surface {expected}"


# --- per-skill surfaces -------------------------------------------------------

def test_missing_skill_md_is_stale(tmp_path):
    root = make_repo(tmp_path)
    findings = src.run_checks(root, "gamma")
    bad = stale(findings)
    assert any(f.surface == "frontmatter" for f in bad)


def test_frontmatter_missing_keys_stale(tmp_path):
    root = make_repo(tmp_path)
    (root / "skills" / "alpha" / "SKILL.md").write_text(
        "---\nname: alpha\n---\nbody\n")
    bad = stale(src.check_skill(root, "alpha"))
    assert any(f.surface == "frontmatter" for f in bad)


def test_frontmatter_bare_name_passes(tmp_path):
    # #552: bare name is the canonical form — the harness namespaces it
    root = make_repo(tmp_path)
    (root / "skills" / "beta" / "SKILL.md").write_text(
        "---\nname: beta\ndescription: d\nargument-hint: h\n---\nbody\n")
    assert not [f for f in stale(src.check_skill(root, "beta"))
                if f.surface == "frontmatter"]


def test_frontmatter_prefixed_name_stale(tmp_path):
    # #552: an embedded rawgentic: prefix is the defect the checker must flag —
    # the loader colon-sanitizes it and doubles the command name
    root = make_repo(tmp_path)
    (root / "skills" / "beta" / "SKILL.md").write_text(
        "---\nname: rawgentic:beta\ndescription: d\nargument-hint: h\n---\nbody\n")
    bad = stale(src.check_skill(root, "beta"))
    assert any(f.surface == "frontmatter" for f in bad)


def test_frontmatter_empty_name_with_stray_line_stale(tmp_path):
    # #552 review: `\s*` matches newlines, so an EMPTY name: value used to
    # bleed onto a stray next line and pass. Must be flagged stale.
    root = make_repo(tmp_path)
    (root / "skills" / "beta" / "SKILL.md").write_text(
        "---\nname:\nbeta\ndescription: d\nargument-hint: h\n---\nbody\n")
    bad = stale(src.check_skill(root, "beta"))
    assert any(f.surface == "frontmatter" for f in bad)


def test_frontmatter_name_in_body_only_stale(tmp_path):
    # #552 review: a flush-left `name: beta` in the BODY must not satisfy the
    # frontmatter requirement.
    root = make_repo(tmp_path)
    (root / "skills" / "beta" / "SKILL.md").write_text(
        "---\ndescription: d\nargument-hint: h\n---\nbody\nname: beta\n")
    bad = stale(src.check_skill(root, "beta"))
    assert any(f.surface == "frontmatter" for f in bad)


def test_whitelist_missing_entry_stale(tmp_path):
    root = make_repo(tmp_path)
    mp = root / ".claude-plugin" / "marketplace.json"
    data = json.loads(mp.read_text())
    data["plugins"][0]["skills"] = ["./skills/beta"]
    mp.write_text(json.dumps(data))
    bad = stale(src.check_skill(root, "alpha"))
    assert any(f.surface == "whitelist" for f in bad)
    # and the computed disk cross-check names the drift too
    assert any(f.surface == "whitelist-vs-disk" for f in bad)


def test_whitelist_unsorted_stale(tmp_path):
    root = make_repo(tmp_path)
    mp = root / ".claude-plugin" / "marketplace.json"
    data = json.loads(mp.read_text())
    data["plugins"][0]["skills"] = ["./skills/beta", "./skills/alpha"]
    mp.write_text(json.dumps(data))
    bad = stale(src.check_skill(root, "alpha"))
    assert any(f.surface == "whitelist" and "alphabetical" in f.detail for f in bad)


def test_missing_symlink_stale(tmp_path):
    root = make_repo(tmp_path)
    (root / "plugins" / "rawgentic" / "skills" / "alpha").unlink()
    bad = stale(src.check_skill(root, "alpha"))
    assert any(f.surface == "codex-symlink" for f in bad)


def test_symlink_wrong_target_stale(tmp_path):
    root = make_repo(tmp_path)
    link = root / "plugins" / "rawgentic" / "skills" / "alpha"
    link.unlink()
    link.symlink_to(Path("..") / ".." / ".." / "skills" / "beta")
    bad = stale(src.check_skill(root, "alpha"))
    assert any(f.surface == "codex-symlink" for f in bad)


def test_real_dir_instead_of_symlink_stale(tmp_path):
    root = make_repo(tmp_path)
    link = root / "plugins" / "rawgentic" / "skills" / "alpha"
    link.unlink()
    link.mkdir()
    bad = stale(src.check_skill(root, "alpha"))
    assert any(f.surface == "codex-symlink" for f in bad)


def test_config_loading_skill_missing_from_manifest_stale(tmp_path):
    root = make_repo(tmp_path)
    (root / "scripts" / "sync_shared_blocks.py").write_text(
        SYNC_SCRIPT.replace('["alpha"]', "[]"))
    bad = stale(src.check_skill(root, "alpha"))
    assert any(f.surface == "manifest" for f in bad)


def test_no_config_loading_skill_needs_no_manifest(tmp_path):
    root = make_repo(tmp_path)
    assert not [f for f in stale(src.check_skill(root, "beta"))
                if f.surface == "manifest"]


def test_config_loading_in_references_counts(tmp_path):
    # canary rule is corpus-wide (#158): the block may live in references/
    root = make_repo(tmp_path)
    refs = root / "skills" / "beta" / "references"
    refs.mkdir()
    (refs / "extra.md").write_text("<config-loading>\nx\n</config-loading>\n")
    bad = stale(src.check_skill(root, "beta"))
    assert any(f.surface == "manifest" for f in bad)  # beta not in MANIFEST


def test_backtick_mention_does_not_count(tmp_path):
    # a `<config-loading>` mention mid-line is not the block (line-anchored)
    root = make_repo(tmp_path)
    refs = root / "skills" / "beta" / "references"
    refs.mkdir()
    (refs / "extra.md").write_text("see the `<config-loading>` block\n")
    assert not [f for f in stale(src.check_skill(root, "beta"))
                if f.surface == "manifest"]


# --- computed global counts ---------------------------------------------------

def test_canary_mismatch_stale(tmp_path):
    root = make_repo(tmp_path)
    (root / "tests" / "hooks" / "test_headless.py").write_text(
        HEADLESS_TEST.replace("= 1", "= 7"))
    bad = stale(src.check_counts(root))
    assert any(f.surface == "canary" and "7" in f.detail for f in bad)


def test_canary_pin_missing_is_stale(tmp_path):
    root = make_repo(tmp_path)
    (root / "tests" / "hooks" / "test_headless.py").write_text("nothing here\n")
    bad = stale(src.check_counts(root))
    assert any(f.surface == "canary" for f in bad)


def test_readme_provides_stale(tmp_path):
    root = make_repo(tmp_path)
    (root / "README.md").write_text(README.replace(
        d("provides_2_skills"), d("provides_3_skills")))
    bad = stale(src.check_counts(root))
    assert any(f.surface == "readme-provides" for f in bad)


def test_readme_evals_fraction_stale(tmp_path):
    root = make_repo(tmp_path)
    (root / "README.md").write_text(README.replace(
        d("0/2_skills"), d("1/2_skills")))
    bad = stale(src.check_counts(root))
    assert any(f.surface == "readme-evals" for f in bad)


def test_evals_fraction_computed_from_both_locations(tmp_path):
    # a skill "has evals" iff evals.json in its own evals/ or -workspace evals/
    root = make_repo(tmp_path)
    ev = root / "skills" / "alpha-workspace" / "evals"
    ev.mkdir(parents=True)
    (ev / "evals.json").write_text("{}")
    bad = stale(src.check_counts(root))
    assert any(f.surface == "readme-evals" and "1/2" in f.detail for f in bad)


def test_breakdown_sum_mismatch_stale(tmp_path):
    root = make_repo(tmp_path)
    pj = root / ".claude-plugin" / "plugin.json"
    pj.write_text(json.dumps({"description": DESCRIPTION.replace(
        d("2_SDLC_workflow_skills"), d("5_SDLC_workflow_skills"))}))
    bad = stale(src.check_counts(root))
    assert any(f.surface == "breakdown-sum" for f in bad)


def test_malformed_marketplace_json_fails_closed(tmp_path):
    root = make_repo(tmp_path)
    (root / ".claude-plugin" / "marketplace.json").write_text("{not json")
    bad = stale(src.run_checks(root, "alpha"))
    assert bad, "malformed surface must be reported STALE, never silently OK"


# --- hand-pin sweep -----------------------------------------------------------

def test_sweep_finds_test_file_straggler(tmp_path):
    # the AC2 case: a hand-pinned count in a tests/ file disagrees
    root = make_repo(tmp_path)
    (root / "tests" / "test_straggler.py").write_text(
        d('assert "1_SDLC_workflow_skills" in desc\n'))
    bad = stale(src.sweep_hand_pins(root))
    assert any("test_straggler.py" in f.detail for f in bad)


def test_sweep_skips_negative_pins(tmp_path):
    root = make_repo(tmp_path)
    (root / "tests" / "test_neg.py").write_text(
        'assert "12 SDLC workflow skills" not in readme\n')
    assert not stale(src.sweep_hand_pins(root))


def test_sweep_counts_prose_containing_not_in_words(tmp_path):
    # R1-F1 (#528 review): "cannot install ..." must NOT be treated as a
    # negative pin — only `not in` directly after the occurrence is skipped
    root = make_repo(tmp_path)
    (root / "tests" / "test_prose.py").write_text(
        d('# cannot install 1_SDLC_workflow_skills tooling here\n'))
    bad = stale(src.sweep_hand_pins(root))
    assert any("test_prose.py" in f.detail for f in bad)


def test_sweep_skips_readme_changelog(tmp_path):
    # README's Changelog section legitimately holds historical counts
    root = make_repo(tmp_path)
    assert not [f for f in stale(src.sweep_hand_pins(root))
                if "README" in f.detail and "99" in f.detail]


def test_sweep_covers_rawgentic_json(tmp_path):
    # the live straggler class this run found: .rawgentic.json's description
    root = make_repo(tmp_path)
    (root / ".rawgentic.json").write_text(json.dumps(
        {"project": {"description": DESCRIPTION.replace(
            d("2_SDLC_workflow_skills"), d("8_SDLC_workflow_skills"))}}))
    bad = stale(src.sweep_hand_pins(root))
    assert any(".rawgentic.json" in f.detail for f in bad)


def test_sweep_consensus_names_both_locations(tmp_path):
    root = make_repo(tmp_path)
    (root / "tests" / "test_straggler.py").write_text(
        d('assert "1_SDLC_workflow_skills" in desc\n'))
    bad = [f for f in stale(src.sweep_hand_pins(root))
           if f.surface == "pin:sdlc"]
    assert bad
    joined = " ".join(f.detail for f in bad)
    assert "test_straggler.py" in joined


def test_bad_encoding_skill_md_fails_closed(tmp_path):
    # R1-F2 (#528 review): a non-UTF-8 SKILL.md anywhere must yield STALE
    # findings, never a traceback (UnicodeDecodeError is a ValueError)
    root = make_repo(tmp_path)
    (root / "skills" / "alpha" / "SKILL.md").write_bytes(b"\xff\xfe broken")
    per_skill = src.check_skill(root, "alpha")
    assert any(f.surface == "frontmatter" and not f.ok for f in per_skill)
    counts = src.check_counts(root)
    assert any(not f.ok for f in counts), "corpus-wide canary walk must fail closed"


# --- skill-name hardening -----------------------------------------------------

@pytest.mark.parametrize("name", ["../evil", "a/b", "UPPER", "", "a b", "scan\n"])
def test_invalid_skill_name_rejected(name):
    with pytest.raises(ValueError):
        src.validate_skill_name(name)


def test_valid_skill_names_accepted():
    for name in ("scan", "add-skill", "implement-feature", "a1"):
        assert src.validate_skill_name(name) == name


# --- CLI ----------------------------------------------------------------------

def _run_cli(*args):
    proc = subprocess.run([sys.executable, str(CLI), *args],
                          capture_output=True, text=True, timeout=60)
    return proc.stdout, proc.stderr, proc.returncode


def test_cli_clean_fixture_exit_0(tmp_path):
    root = make_repo(tmp_path)
    out, _, rc = _run_cli("check", "--skill", "alpha", "--project-root", str(root))
    assert rc == 0, out
    assert "OK" in out
    assert "STALE" not in out


def test_cli_stale_exit_1(tmp_path):
    root = make_repo(tmp_path)
    (root / "plugins" / "rawgentic" / "skills" / "alpha").unlink()
    out, _, rc = _run_cli("check", "--skill", "alpha", "--project-root", str(root))
    assert rc == 1
    assert "STALE codex-symlink" in out


def test_cli_invalid_name_exit_2(tmp_path):
    root = make_repo(tmp_path)
    _, err, rc = _run_cli("check", "--skill", "../evil", "--project-root", str(root))
    assert rc == 2


def test_cli_usage_error_exit_2():
    _, _, rc = _run_cli("bogus")
    assert rc == 2


def test_measured_basis_cited():
    """AC3: the module cites its measured basis (epic #509 lever 3)."""
    doc = src.__doc__
    assert "epic #509" in doc and "lever 3" in doc
    assert "full-suite round-trip" in doc


# --- the real repo ------------------------------------------------------------

def test_real_repo_is_clean():
    """The shipped tree passes its own checker — this is the new coverage no
    existing guard has (it swept .rawgentic.json's stale '8 SDLC' straggler)."""
    out, _, rc = _run_cli("check", "--skill", "implement-feature",
                          "--project-root", str(REPO_ROOT))
    assert rc == 0, f"checker found stale surfaces in the live repo:\n{out}"
