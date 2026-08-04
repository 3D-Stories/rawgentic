"""#807 AC2-AC5: execute the SHIPPED `<render-addon>` block, not a copy of it.

The whole point of AC5 is that the add-on-absent branch is the one path this machine
never exercises, so it rots silently. A test that re-implements the logic would rot with
it. So every case here EXTRACTS the block from a real skill file on disk and runs it —
if the shipped text and these expectations diverge, these tests fail.

The block writes its own fallback page (it is not model-authored), which is what makes
"absent" a deterministic, assertable outcome rather than a hope.
"""
import ast
import json
import os
import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SHARED_BLOCK = REPO_ROOT / "shared" / "blocks" / "render-addon.md"
SHIPPED_SITES = (
    "create-issue/SKILL.md",
    "run-feedback/SKILL.md",
    "session-mining/SKILL.md",
    # #874: WF2's render-addon call site lives in Step 12's section, which is now its own file.
    "implement-feature/references/step-12.md",
    "fix-bug/references/steps.md",
)

PRESENT, ABSENT, BROKEN, CONFIG_ERROR = 0, 20, 70, 71


def _block_from(path: Path) -> str:
    """The bash program shipped in `path`.

    A generated call site delimits it with the `<render-addon>` markers; the canonical
    source under shared/blocks/ IS the inner content and carries no markers.
    """
    text = path.read_text()
    if "<render-addon>" in text:
        text = text.split("<render-addon>", 1)[1].split("</render-addon>", 1)[0]
    m = re.search(r"```bash\n(.*?)\n```", text, re.S)
    assert m, f"no fenced bash block in {path}"
    return m.group(1)


def _shell_quote(value: str) -> str:
    """The block's stated substitution contract: single-quoted, ' -> '\\''."""
    return "'" + value.replace("'", "'\\''") + "'"


def _run(block: str, md, out, title="Doc Title", style="design", telemetry="",
         env=None, cwd="/"):
    script = block
    for placeholder, value in (("'<MD>'", str(md)), ("'<OUT>'", str(out)),
                               ("'<TITLE>'", title), ("'<STYLE>'", style),
                               ("'<TELEMETRY>'", telemetry)):
        script = script.replace(placeholder, _shell_quote(value), 1)
    full_env = {"PATH": os.environ.get("PATH", "")}
    full_env.update(env or {})
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                          cwd=cwd, env=full_env)


def _fake_addon(root: Path, *, launcher_body=None, executable=True, launcher=True):
    """Build a config root containing a fake add-on, so CI never depends on a real install."""
    scripts = root / "skills" / "design-doc-publish" / "scripts"
    scripts.mkdir(parents=True)
    if not launcher:
        return root
    target = scripts / "render-doc"
    target.write_text(launcher_body or (
        "#!/usr/bin/env python3\n"
        "import argparse, html, sys\n"
        "p = argparse.ArgumentParser()\n"
        "for f in ('--md', '--out', '--title', '--style', '--telemetry'):\n"
        "    p.add_argument(f)\n"
        "a = p.parse_args()\n"
        "import os\n"
        "open(a.out, 'w').write('<!doctype html>\\n<title>' + html.escape(a.title)"
        " + '</title>\\n<p>' + html.escape(open(a.md).read()) + '</p>\\n')\n"
        # argv goes beside the INPUT, because --out is a temp path the block renames.
        "open(os.path.join(os.path.dirname(a.md), 'launcher.argv'), 'w')"
        ".write(repr(sys.argv))\n"
    ))
    if executable:
        target.chmod(0o755)
    return root


def _md(tmp_path, text="# Doc\n\nBody.\n"):
    p = tmp_path / "in.md"
    p.write_text(text)
    return p


# --- the shipped text is what runs, and it is identical everywhere ----------------

def test_all_five_sites_ship_the_same_block():
    """AC1: every call site carries the block, byte-identical to its one source."""
    canonical = _block_from(SHARED_BLOCK)
    for rel in SHIPPED_SITES:
        assert _block_from(REPO_ROOT / "skills" / rel) == canonical, f"{rel} drifted"


def test_no_relative_or_placeholder_invocation_survives():
    """AC1: the relative path and the never-implemented placeholder are both gone."""
    for path in (REPO_ROOT / "skills").rglob("*.md"):
        text = path.read_text()
        assert "python3 hooks/render_artifact.py" not in text, path
        assert "<plugin-hooks>/render_artifact.py" not in text, path


# --- AC5: the absent branch, which is the one that rots ---------------------------

def test_absent_writes_a_real_fallback_page_and_says_so(tmp_path):
    """AC3/AC5: no config dir at all -> exit 20, announced, and a VALID page exists."""
    out = tmp_path / "out.html"
    r = _run(_block_from(REPO_ROOT / "skills" / "create-issue" / "SKILL.md"),
             _md(tmp_path), out, title="Absent Case",
             env={"HOME": str(tmp_path / "nohome")})
    assert r.returncode == ABSENT, r.stderr
    assert "not installed" in r.stderr
    page = out.read_text()
    assert page.lstrip().lower().startswith("<!doctype html>")
    assert "Absent Case" in page
    assert "Body." in page, "the fallback must not silently lose the source"
    assert "http://" not in page and "https://" not in page, "must be self-contained"


def test_absent_when_config_root_exists_but_holds_no_addon(tmp_path):
    """The commoner shape: ~/.claude exists, the add-on simply is not installed."""
    (tmp_path / "home" / ".claude").mkdir(parents=True)
    out = tmp_path / "out.html"
    r = _run(_block_from(SHARED_BLOCK), _md(tmp_path), out,
             env={"HOME": str(tmp_path / "home")})
    assert r.returncode == ABSENT, r.stderr
    assert out.exists()


def test_absent_runs_from_a_non_repository_cwd(tmp_path):
    """AC2: the bug was cwd-dependence. Everything here already runs from `/`."""
    out = tmp_path / "out.html"
    r = _run(_block_from(SHARED_BLOCK), _md(tmp_path), out,
             env={"HOME": str(tmp_path / "nohome")}, cwd="/")
    assert r.returncode == ABSENT, r.stderr


# --- present, against a fake launcher so CI never needs a real install ------------

def test_present_renders_via_the_launcher(tmp_path):
    """AC2: with the add-on installed the launcher produces the artifact."""
    root = _fake_addon(tmp_path / "cfg")
    out = tmp_path / "out.html"
    r = _run(_block_from(SHARED_BLOCK), _md(tmp_path), out, title="Present Case",
             env={"CLAUDE_CONFIG_DIR": str(root)})
    assert r.returncode == PRESENT, r.stderr
    assert "Present Case" in out.read_text()


def test_present_passes_telemetry_only_when_given(tmp_path):
    root = _fake_addon(tmp_path / "cfg")
    tel = tmp_path / "t.json"
    tel.write_text(json.dumps({"tests": {"passed": 1}}))
    out = tmp_path / "out.html"
    r = _run(_block_from(SHARED_BLOCK), _md(tmp_path), out, telemetry=str(tel),
             env={"CLAUDE_CONFIG_DIR": str(root)})
    assert r.returncode == PRESENT, r.stderr
    assert "--telemetry" in (tmp_path / "launcher.argv").read_text()

    out2 = tmp_path / "out2.html"
    r2 = _run(_block_from(SHARED_BLOCK), _md(tmp_path), out2,
              env={"CLAUDE_CONFIG_DIR": str(root)})
    assert r2.returncode == PRESENT, r2.stderr
    assert "--telemetry" not in (tmp_path / "launcher.argv").read_text()


def test_a_failed_render_never_clobbers_a_good_artifact(tmp_path):
    """The temp-then-rename rule: a broken run must leave the previous page intact."""
    root = _fake_addon(tmp_path / "cfg", launcher_body="#!/bin/sh\nexit 3\n")
    out = tmp_path / "out.html"
    out.write_text("<!doctype html>\nPREVIOUS GOOD\n")
    r = _run(_block_from(SHARED_BLOCK), _md(tmp_path), out,
             env={"CLAUDE_CONFIG_DIR": str(root)})
    assert r.returncode == BROKEN, r.stderr
    assert "PREVIOUS GOOD" in out.read_text()
    assert not list(tmp_path.glob("*.html.tmp")), "temp file leaked"


# --- broken: every variant must fail loudly and write NO fallback -----------------

def _assert_broken_without_fallback(tmp_path, root):
    out = tmp_path / "out.html"
    r = _run(_block_from(SHARED_BLOCK), _md(tmp_path), out,
             env={"CLAUDE_CONFIG_DIR": str(root)})
    assert r.returncode == BROKEN, f"expected broken, got {r.returncode}: {r.stderr}"
    assert "broken" in r.stderr
    assert not out.exists(), "a broken add-on must NEVER produce a fallback page"


def test_broken_when_launcher_missing(tmp_path):
    _assert_broken_without_fallback(tmp_path, _fake_addon(tmp_path / "c", launcher=False))


def test_broken_when_launcher_not_executable(tmp_path):
    _assert_broken_without_fallback(
        tmp_path, _fake_addon(tmp_path / "c", executable=False))


def test_broken_when_launcher_exits_nonzero(tmp_path):
    _assert_broken_without_fallback(
        tmp_path, _fake_addon(tmp_path / "c", launcher_body="#!/bin/sh\nexit 9\n"))


def test_broken_when_output_is_not_html(tmp_path):
    _assert_broken_without_fallback(tmp_path, _fake_addon(
        tmp_path / "c",
        launcher_body="#!/usr/bin/env python3\nimport sys\n"
                      "open(sys.argv[sys.argv.index('--out') + 1], 'w').write('nope')\n"))


def test_broken_when_output_is_empty(tmp_path):
    _assert_broken_without_fallback(tmp_path, _fake_addon(
        tmp_path / "c",
        launcher_body="#!/usr/bin/env python3\nimport sys\n"
                      "open(sys.argv[sys.argv.index('--out') + 1], 'w').write('')\n"))


def test_dangling_addon_symlink_is_broken_not_absent(tmp_path):
    """The trap: a half-installed add-on must never masquerade as a clean fallback."""
    root = tmp_path / "cfg"
    (root / "skills").mkdir(parents=True)
    (root / "skills" / "design-doc-publish").symlink_to(tmp_path / "gone")
    _assert_broken_without_fallback(tmp_path, root)


# --- config-error: cannot tell, so never "absent" ---------------------------------

def _assert_config_error(tmp_path, env):
    out = tmp_path / "out.html"
    r = _run(_block_from(SHARED_BLOCK), _md(tmp_path), out, env=env)
    assert r.returncode == CONFIG_ERROR, f"got {r.returncode}: {r.stderr}"
    assert "config-error" in r.stderr
    assert not out.exists()


def test_config_error_when_override_is_relative(tmp_path):
    _assert_config_error(tmp_path, {"CLAUDE_CONFIG_DIR": "relative/path"})


def test_config_error_when_explicit_override_does_not_exist(tmp_path):
    _assert_config_error(tmp_path, {"CLAUDE_CONFIG_DIR": str(tmp_path / "nosuch")})


def test_config_error_when_override_is_a_file(tmp_path):
    f = tmp_path / "afile"
    f.write_text("x")
    _assert_config_error(tmp_path, {"CLAUDE_CONFIG_DIR": str(f)})


def test_config_error_when_home_unset(tmp_path):
    _assert_config_error(tmp_path, {})


def test_config_error_when_home_is_relative(tmp_path):
    _assert_config_error(tmp_path, {"HOME": "not/absolute"})


def test_unreadable_config_root_is_config_error_not_absent(tmp_path):
    """EACCES must never be reported as a clean absence — that is the whole rule."""
    if os.geteuid() == 0:
        import pytest
        pytest.skip("root ignores directory permissions")
    root = tmp_path / "cfg"
    (root / "skills").mkdir(parents=True)
    root.chmod(0o000)
    try:
        out = tmp_path / "out.html"
        r = _run(_block_from(SHARED_BLOCK), _md(tmp_path), out,
                 env={"CLAUDE_CONFIG_DIR": str(root)})
        assert r.returncode in (CONFIG_ERROR, BROKEN), r.stderr
        assert r.returncode != ABSENT
        assert not out.exists()
    finally:
        root.chmod(0o755)


# --- the security contract: placeholders are data, never shell ---------------------

HOSTILE = """It's $(touch {pwned}) `touch {pwned}` "quoted" -x --md=/etc/passwd
second line"""


def test_hostile_title_executes_nothing_and_arrives_intact(tmp_path):
    """S1: the values reach the program as argv, so no substitution can execute."""
    pwned = tmp_path / "PWNED"
    root = _fake_addon(tmp_path / "cfg")
    out = tmp_path / "out.html"
    title = HOSTILE.format(pwned=pwned)
    r = _run(_block_from(SHARED_BLOCK), _md(tmp_path), out, title=title,
             env={"CLAUDE_CONFIG_DIR": str(root)})
    assert r.returncode == PRESENT, r.stderr
    assert not pwned.exists(), "shell injection executed"
    argv = ast.literal_eval((tmp_path / "launcher.argv").read_text())
    assert argv[argv.index("--title") + 1] == title, (
        "the title must reach the launcher byte-for-byte, as a single argv element"
    )


def test_hostile_title_on_the_absent_path_is_escaped_into_the_fallback(tmp_path):
    """The fallback page is ours to write, so it must escape what it embeds."""
    pwned = tmp_path / "PWNED"
    out = tmp_path / "out.html"
    r = _run(_block_from(SHARED_BLOCK), _md(tmp_path, "<script>alert(1)</script>\n"),
             out, title=HOSTILE.format(pwned=pwned),
             env={"HOME": str(tmp_path / "nohome")})
    assert r.returncode == ABSENT, r.stderr
    assert not pwned.exists()
    page = out.read_text()
    assert "<script>alert(1)</script>" not in page, "source must be escaped"
    assert "&lt;script&gt;" in page


def test_paths_with_spaces_and_quotes_round_trip(tmp_path):
    root = _fake_addon(tmp_path / "cfg")
    weird = tmp_path / "a dir with 'quotes'"
    weird.mkdir()
    out = weird / "o ut.html"
    r = _run(_block_from(SHARED_BLOCK), _md(tmp_path), out, title="T",
             env={"CLAUDE_CONFIG_DIR": str(root)})
    assert r.returncode == PRESENT, r.stderr
    assert out.exists()
