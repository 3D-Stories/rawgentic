#!/usr/bin/env python3
"""Single-source shared SKILL.md prose blocks across skills.

WHY: Claude Code marketplace plugins copy each plugin to a cache and block any path
outside a skill's own directory — `../shared/...` won't resolve at runtime. So a block
that several skills share (e.g. <config-loading>) cannot be a single file they all read
at runtime; the only way to single-source it is to keep the canonical copy in
shared/blocks/ and GENERATE each skill's inline copy here, guarded against drift by
tests/test_shared_block_drift.py. (Before this, the copies silently diverged — an
em-dash in two skills.)

A NOTE ON `${CLAUDE_PLUGIN_ROOT}`, because an earlier version of this docstring got it
wrong and the error propagated into a design (#807). Measured on Claude Code 2.1.220:
it IS substituted into a SKILL.md *body* when Claude Code loads that file as a skill;
it is NOT substituted when any file is opened with the Read tool (so `references/*.md`
never sees it, since those are always Read); and it is NOT a shell environment variable.
That makes it useless as a uniform locator across a skill's own files — which is a real
constraint, just not the one this docstring used to claim. The cache path-blocking above
is the actual reason this generator exists.

The block in each target file is delimited by its XML-ish tags (e.g. `<config-loading>`
... `</config-loading>`), each on its own line; those tags ARE the sync sentinels, so
no extra markers are introduced. Only the text BETWEEN the tags is replaced.

Usage:
  python3 scripts/sync_shared_blocks.py            # rewrite each copy from its source
  python3 scripts/sync_shared_blocks.py --check    # verify only; exit 1 on any drift
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILLS = ROOT / "skills"
SHARED = ROOT / "shared" / "blocks"

# tag -> { source filename in shared/blocks/ : [skills that use that variant] }.
# Skills NOT listed for a tag are intentionally bespoke (e.g. create-issue's slim WF1
# config-loading from PR #104) and are never touched.
MANIFEST = {
    "config-loading": {
        "config-loading.md": [
            # WF4/7/8/9/10/12 removed 2026-07-04 (#160): deprecation stubs carry
            # no config-loading block; removal at v3.0.0 (#161).
            "adversarial-review", "epic-post-mortem",
            "incident",
            "implement-feature", "fix-bug", "peer-consult", "run-feedback", "scan",
        ],
    },
    # #158 AC6: WF2's dispatch-routing and loop-back contracts single-sourced.
    # Single-carrier today; WF3 joins via its own restructure issue (#159) —
    # its current block text DIFFERS from WF2's, so no forced unification here.
    "model-routing-resolve": {
        "model-routing-resolve.md": ["implement-feature"],
    },
    "loop-back-budget": {
        "loop-back-budget.md": ["implement-feature"],
    },
}

# Whole-FILE targets (#276): source filename in shared/blocks/ -> list of
# skill-relative destination paths. Unlike MANIFEST's tag-delimited SKILL.md
# blocks, these are standalone references/ files copied verbatim (the
# quality-bar rubric was a hand-synced byte-identical triple before this).
FILE_MANIFEST = {
    "quality-bar.md": [
        "fix-bug/references/quality-bar.md",
        "implement-feature/references/quality-bar.md",
        "setup/references/quality-bar.md",
    ],
}


def _skill_md(skill: str) -> Path:
    """Resolve a MANIFEST target to the file the block is generated into.

    A bare skill name means that skill's SKILL.md (the original and still the
    common case). A value containing "/" is a skill-relative PATH, e.g.
    "fix-bug/references/steps.md" — needed because not every shared block lives
    in a SKILL.md (#807: two of the five render call sites are in references/).
    """
    return SKILLS / skill if "/" in skill else SKILLS / skill / "SKILL.md"


def _marker_span(lines: list[str], tag: str):
    """(start, end) indices of the `<tag>` and `</tag>` marker lines, or None."""
    try:
        s = next(i for i, ln in enumerate(lines) if ln.strip() == f"<{tag}>")
        e = next(i for i in range(s + 1, len(lines)) if lines[i].strip() == f"</{tag}>")
        return s, e
    except StopIteration:
        return None


def _source_inner(src_file: str) -> list[str]:
    return SHARED.joinpath(src_file).read_text().rstrip("\n").split("\n")


def _targets():
    for tag, sources in MANIFEST.items():
        for src_file, skills in sources.items():
            for skill in skills:
                yield tag, src_file, skill


def check() -> list[str]:
    drift = []
    for tag, src_file, skill in _targets():
        lines = _skill_md(skill).read_text().splitlines()
        span = _marker_span(lines, tag)
        if span is None:
            drift.append(f"{skill}: missing <{tag}> block")
            continue
        s, e = span
        if lines[s + 1:e] != _source_inner(src_file):
            drift.append(f"{skill}: <{tag}> differs from shared/blocks/{src_file}")
    for src_file, dests in FILE_MANIFEST.items():
        src_text = SHARED.joinpath(src_file).read_text()
        for dest in dests:
            p = SKILLS / dest
            if not p.exists():
                drift.append(f"{dest}: missing (source shared/blocks/{src_file})")
            elif p.read_text() != src_text:
                drift.append(f"{dest}: differs from shared/blocks/{src_file}")
    return drift


def sync() -> list[str]:
    changed = []
    for tag, src_file, skill in _targets():
        p = _skill_md(skill)
        lines = p.read_text().splitlines()
        span = _marker_span(lines, tag)
        if span is None:
            raise SystemExit(f"sync: {skill} has no <{tag}> block to sync into")
        s, e = span
        new_inner = _source_inner(src_file)
        if lines[s + 1:e] == new_inner:
            continue
        p.write_text("\n".join(lines[:s + 1] + new_inner + lines[e:]) + "\n")
        changed.append(f"{skill} <{tag}>")
    for src_file, dests in FILE_MANIFEST.items():
        src_text = SHARED.joinpath(src_file).read_text()
        for dest in dests:
            p = SKILLS / dest
            if p.exists() and p.read_text() == src_text:
                continue
            p.write_text(src_text)
            changed.append(dest)
    return changed


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true",
                    help="verify copies match sources; exit 1 on drift (for CI/tests)")
    args = ap.parse_args()
    if args.check:
        drift = check()
        if drift:
            print("shared-block DRIFT detected (run scripts/sync_shared_blocks.py):", file=sys.stderr)
            for d in drift:
                print(f"  - {d}", file=sys.stderr)
            sys.exit(1)
        print("shared blocks in sync.")
        return
    changed = sync()
    print("synced: " + ", ".join(changed) if changed else "nothing to sync (already in sync).")


if __name__ == "__main__":
    main()
