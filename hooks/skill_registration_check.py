#!/usr/bin/env python3
"""Skill-registration surface checker (#528, epic #509 lever 3).

Given a skill name, prints every registration surface with its current vs
expected state — whitelist position, codex symlink, MANIFEST membership,
config-loading canary, every count string (computed from the tree the way the
#271 guards do) — and grep-discovers ALL hand-pinned count copies, so
test_interview_skill.py-class stragglers can't hide. Exit 1 on any stale
surface (CI-invocable), 2 on usage error.

Measured basis: epic #509 run analysis, lever 3 — the prose registration walk
cost ~4 min plus one full-suite round-trip per new skill (PR #525 profiler;
#508 Step-8 session notes).

Fail-mode: fail-CLOSED — an unreadable or malformed surface is reported STALE.
The checker's whole job is to fail loudly; a silent pass on a parse error would
hide exactly the drift it exists to find.

Pure core (check_skill / check_counts / sweep_hand_pins, all returning
Finding lists) + thin CLI in main(argv).
"""
import argparse
import importlib.util
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

SKILL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
CONFIG_LOADING_RE = re.compile(r"^<config-loading>", re.M)
CANARY_PIN_RE = re.compile(r"EXPECTED_CONFIG_LOADING_COUNT\s*=\s*(\d+)")
BREAKDOWN_RE = re.compile(
    r"(\d+) (?:SDLC workflow|workspace management|planning|security)")

# Hand-pin sweep families. `expected` is resolved per-family in sweep_hand_pins:
# computable families compare against the tree; hand-tally families require
# cross-surface consensus (every occurrence equal).
PIN_FAMILIES = {
    "pin:sdlc": re.compile(r"(\d+) SDLC workflow skills"),
    "pin:workspace": re.compile(r"(\d+) workspace management"),
    "pin:planning": re.compile(r"(\d+) planning skill"),
    "pin:config-driven": re.compile(r"All (\d+) config-driven skills"),
    "pin:provides": re.compile(r"provides (\d+) skills"),
    "pin:evals": re.compile(r"(\d+)/(\d+) skills have evals\.json"),
}
COMPUTED_FAMILIES = ("pin:provides", "pin:evals")
NEGATIVE_PIN_TAIL_RE = re.compile(r"""["']?\s*not\s+in\b""")

# Sweep scope: the CI-pinned surfaces plus the project's own config description.
# docs/*.md are deliberately excluded (known-stale on counts by convention —
# the add-skill skill's rule: tests are the truth, docs defer to them).
SWEEP_GLOBS = ("tests/**/*.py", "README.md", ".claude-plugin/plugin.json",
               ".claude-plugin/marketplace.json",
               "plugins/rawgentic/.codex-plugin/plugin.json", ".rawgentic.json")


@dataclass
class Finding:
    surface: str
    ok: bool
    detail: str


def validate_skill_name(name: str) -> str:
    """Reject anything that could traverse outside skills/ when path-joined."""
    if not SKILL_NAME_RE.fullmatch(name):
        raise ValueError(f"invalid skill name: {name!r} (want ^[a-z0-9][a-z0-9-]*$)")
    return name


def skill_corpus(root: Path, name: str) -> str:
    """SKILL.md + sorted references/*.md — mirrors tests/corpus.py (which lives
    in tests/ and is not importable from hooks/)."""
    skill_dir = root / "skills" / name
    parts = [(skill_dir / "SKILL.md").read_text(encoding="utf-8")]
    refs = skill_dir / "references"
    if refs.is_dir():
        parts.extend(p.read_text(encoding="utf-8") for p in sorted(refs.glob("*.md")))
    return "\n".join(parts)


def _disk_skills(root: Path) -> list:
    return sorted(p.parent.name for p in (root / "skills").glob("*/SKILL.md"))


def _whitelist(root: Path) -> list:
    mp = json.loads((root / ".claude-plugin" / "marketplace.json").read_text())
    return mp["plugins"][0]["skills"]


def _readme_body(root: Path) -> str:
    """README body before ## Changelog — the changelog legitimately holds
    historical count strings."""
    text = (root / "README.md").read_text(encoding="utf-8")
    idx = text.find("\n## Changelog")
    return text[:idx] if idx >= 0 else text


def _skills_with_config_loading(root: Path) -> list:
    """Line-anchored ^<config-loading> over the corpus — mirrors
    TestSkillCountCanary (a backtick mention mid-line must not count)."""
    return [s for s in _disk_skills(root)
            if CONFIG_LOADING_RE.search(skill_corpus(root, s))]


def _manifest_members(root: Path) -> set:
    """Skills registered for the synced config-loading block."""
    # Deliberate exec of repo-internal code: the script path is fixed (never
    # attacker-influenced) and its module level is side-effect-free constants;
    # a hostile checkout already owns the tree, so AST-parsing would add
    # hardening theater, not safety.
    script = root / "scripts" / "sync_shared_blocks.py"
    spec = importlib.util.spec_from_file_location("_sync_shared_blocks_dynamic", str(script))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    members = set()
    for sources in mod.MANIFEST.get("config-loading", {}).values():
        members.update(sources)
    return members


def _evals_have(root: Path, skills: list) -> set:
    return {s for s in skills
            if (root / "skills" / s / "evals" / "evals.json").exists()
            or (root / "skills" / f"{s}-workspace" / "evals" / "evals.json").exists()}


# --- per-skill surfaces -------------------------------------------------------

def check_skill(root: Path, name: str) -> list:
    findings = []
    skill_md = root / "skills" / name / "SKILL.md"
    if not skill_md.exists():
        return [Finding("frontmatter", False, f"{skill_md} does not exist")]
    try:
        text = skill_md.read_text(encoding="utf-8")
    except (OSError, ValueError) as exc:
        return [Finding("frontmatter", False, f"{skill_md} unreadable: {exc}")]
    missing = []
    name_re = re.compile(rf"^name:\s*(rawgentic:)?{re.escape(name)}\s*$", re.M)
    if not name_re.search(text):
        missing.append(f"name: rawgentic:{name}")
    for key in ("description:", "argument-hint:"):
        if not re.search(rf"^{key}", text, re.M):
            missing.append(key)
    if missing:
        findings.append(Finding("frontmatter", False,
                                f"SKILL.md missing {', '.join(missing)}"))
    else:
        findings.append(Finding("frontmatter", True, "name/description/argument-hint present"))

    try:
        wl = _whitelist(root)
        entry = f"./skills/{name}"
        if entry not in wl:
            findings.append(Finding("whitelist", False,
                                    f"{entry} not in marketplace.json plugins[0].skills"))
        elif wl != sorted(wl):
            findings.append(Finding("whitelist", False,
                                    "whitelist not in alphabetical order "
                                    f"(first offender: {next(a for a, b in zip(wl, sorted(wl)) if a != b)})"))
        else:
            findings.append(Finding("whitelist", True,
                                    f"{entry} present, list alphabetical"))
        listed = {Path(rel).name for rel in wl}
        disk = set(_disk_skills(root))
        if listed != disk:
            findings.append(Finding("whitelist-vs-disk", False,
                                    f"whitelist only: {sorted(listed - disk)}; "
                                    f"disk only: {sorted(disk - listed)}"))
        else:
            findings.append(Finding("whitelist-vs-disk", True,
                                    f"whitelist == {len(disk)} skills on disk"))
    except (OSError, ValueError, KeyError, IndexError) as exc:
        findings.append(Finding("whitelist", False,
                                f"marketplace.json unreadable/malformed: {exc}"))

    link = root / "plugins" / "rawgentic" / "skills" / name
    target = root / "skills" / name
    if not link.is_symlink():
        findings.append(Finding("codex-symlink", False,
                                f"{link} missing or not a symlink"))
    elif link.resolve() != target.resolve():
        findings.append(Finding("codex-symlink", False,
                                f"{link} resolves to {link.resolve()}, want {target}"))
    else:
        findings.append(Finding("codex-symlink", True, f"{link} -> skills/{name}"))

    try:
        has_block = bool(CONFIG_LOADING_RE.search(skill_corpus(root, name)))
        if has_block:
            members = _manifest_members(root)
            if name in members:
                findings.append(Finding("manifest", True,
                                        "config-loading block synced via MANIFEST"))
            else:
                findings.append(Finding("manifest", False,
                                        "corpus has ^<config-loading> but skill is not in "
                                        "scripts/sync_shared_blocks.py MANIFEST (never hand-paste the block)"))
        else:
            findings.append(Finding("manifest", True, "no config-loading block — MANIFEST n/a"))
    except (OSError, ValueError, AttributeError, KeyError, SyntaxError) as exc:
        findings.append(Finding("manifest", False,
                                f"sync_shared_blocks.py unreadable/malformed: {exc}"))
    return findings


# --- computed global counts ---------------------------------------------------

def check_counts(root: Path) -> list:
    findings = []
    skills = _disk_skills(root)
    n = len(skills)
    try:
        readme = _readme_body(root)
    except (OSError, ValueError) as exc:
        return [Finding("readme-provides", False, f"README.md unreadable: {exc}")]
    try:
        computed_canary = len(_skills_with_config_loading(root))
    except (OSError, ValueError) as exc:
        return [Finding("canary", False, f"skill corpus unreadable: {exc}")]
    canary_file = root / "tests" / "hooks" / "test_headless.py"
    try:
        m = CANARY_PIN_RE.search(canary_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        m = None
    if not m:
        findings.append(Finding("canary", False,
                                f"EXPECTED_CONFIG_LOADING_COUNT pin not found in {canary_file}"))
    elif int(m.group(1)) != computed_canary:
        findings.append(Finding("canary", False,
                                f"pin says {m.group(1)}, corpus computes {computed_canary}"))
    else:
        findings.append(Finding("canary", True,
                                f"EXPECTED_CONFIG_LOADING_COUNT == {computed_canary} (computed)"))

    if f"provides {n} skills" in readme:
        findings.append(Finding("readme-provides", True, f"provides {n} skills"))
    else:
        found = PIN_FAMILIES["pin:provides"].findall(readme)
        findings.append(Finding("readme-provides", False,
                                f"README body says 'provides {found or '?'} skills', "
                                f"disk has {n}"))

    have = _evals_have(root, skills)
    frac = f"{len(have)}/{n} skills have evals.json"
    if frac in readme:
        findings.append(Finding("readme-evals", True, frac))
    else:
        found = PIN_FAMILIES["pin:evals"].findall(readme)
        findings.append(Finding("readme-evals", False,
                                f"README body says {found or '?'}, computed {len(have)}/{n}"))

    try:
        desc = json.loads((root / ".claude-plugin" / "plugin.json").read_text())["description"]
        breakdown = [int(x) for x in BREAKDOWN_RE.findall(desc)]
        if len(breakdown) == 4 and sum(breakdown) == n:
            findings.append(Finding("breakdown-sum", True,
                                    f"plugin description breakdown {breakdown} sums to {n}"))
        else:
            findings.append(Finding("breakdown-sum", False,
                                    f"plugin description breakdown {breakdown} must be 4 "
                                    f"numbers summing to the {n} skills on disk"))
    except (OSError, ValueError, KeyError) as exc:
        findings.append(Finding("breakdown-sum", False,
                                f"plugin.json unreadable/malformed: {exc}"))
    return findings


# --- hand-pin sweep -------------------------------------------------------------

def _sweep_lines(root: Path):
    """Yield (relpath, lineno, line) over the sweep scope, README changelog
    excluded, negative-pin lines (`not in`) skipped."""
    for pattern in SWEEP_GLOBS:
        for path in sorted(root.glob(pattern)):
            if not path.is_file():
                continue
            rel = path.relative_to(root)
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                yield str(rel), 0, "\x00unreadable"
                continue
            if rel.name == "README.md":
                text = _readme_body(root)
            for lineno, line in enumerate(text.splitlines(), 1):
                yield str(rel), lineno, line


def sweep_hand_pins(root: Path) -> list:
    skills = _disk_skills(root)
    n = len(skills)
    expected = {
        "pin:provides": (str(n),),
        "pin:evals": (str(len(_evals_have(root, skills))), str(n)),
    }
    occurrences = {family: [] for family in PIN_FAMILIES}
    findings = []
    for rel, lineno, line in _sweep_lines(root):
        if line == "\x00unreadable":
            findings.append(Finding("pin:sweep", False, f"{rel} unreadable"))
            continue
        for family, rx in PIN_FAMILIES.items():
            for m in rx.finditer(line):
                # Negative pin (test_v3_removals convention): `assert "<pin>"
                # not in <surface>` — skip only when `not in` directly follows
                # THIS occurrence's closing quote, so prose like "cannot
                # install 3 SDLC workflow skills" still gets swept.
                if NEGATIVE_PIN_TAIL_RE.match(line[m.end():]):
                    continue
                occurrences[family].append((rel, lineno, m.groups()))
    for family, occ in occurrences.items():
        if not occ:
            continue
        if family in COMPUTED_FAMILIES:
            want = expected[family]
            bad = [(rel, ln, g) for rel, ln, g in occ if g != want]
            if bad:
                findings.append(Finding(family, False,
                                        f"expected {'/'.join(want)}, stale at: "
                                        + ", ".join(f"{rel}:{ln} ({'/'.join(g)})"
                                                    for rel, ln, g in bad)))
            else:
                findings.append(Finding(family, True,
                                        f"{len(occ)} occurrence(s) match computed {'/'.join(want)}"))
        else:
            values = {g for _, _, g in occ}
            if len(values) > 1:
                findings.append(Finding(family, False,
                                        "hand-pinned copies disagree: "
                                        + ", ".join(f"{rel}:{ln} ({'/'.join(g)})"
                                                    for rel, ln, g in occ)))
            else:
                findings.append(Finding(family, True,
                                        f"{len(occ)} occurrence(s) agree on "
                                        f"{'/'.join(next(iter(values)))}"))
    return findings


def run_checks(root: Path, name: str) -> list:
    return check_skill(root, name) + check_counts(root) + sweep_hand_pins(root)


# --- CLI ------------------------------------------------------------------------

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Check every skill-registration surface, computed from the tree (#528).")
    sub = parser.add_subparsers(dest="cmd")
    check = sub.add_parser("check", help="check all surfaces for a skill")
    check.add_argument("--skill", required=True, help="skill name (e.g. scan)")
    check.add_argument("--project-root", default=".", help="repo root (default: cwd)")
    args = parser.parse_args(argv)
    if args.cmd != "check":
        parser.print_usage(sys.stderr)
        return 2
    try:
        name = validate_skill_name(args.skill)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    root = Path(args.project_root).resolve()
    if not (root / "skills").is_dir():
        print(f"error: {root} has no skills/ dir — not the plugin repo?", file=sys.stderr)
        return 2
    findings = run_checks(root, name)
    stale = 0
    for f in findings:
        status = "OK" if f.ok else "STALE"
        stale += 0 if f.ok else 1
        print(f"{status} {f.surface}: {f.detail}")
    print(f"{'CLEAN' if not stale else 'STALE'}: {len(findings) - stale}/{len(findings)} surfaces ok")
    return 1 if stale else 0


if __name__ == "__main__":
    sys.exit(main())
