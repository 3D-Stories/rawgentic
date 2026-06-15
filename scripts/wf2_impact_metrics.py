#!/usr/bin/env python3
"""Compute deterministic Tier-1 impact metrics for a rawgentic skill-extraction
effort.

These are the cheap, run-free metrics that prove RELIABILITY, CONSISTENCY, and
MAINTAINABILITY impact (test growth, fail-closed coverage, dedup, diff volume) —
as opposed to SPEED/QUALITY, which require expensive end-to-end workflow A/B runs
(a separate, Tier-2 harness). Reusable across efforts: pass --baseline and --head
git refs.

Usage:
  python3 scripts/wf2_impact_metrics.py [--baseline REF] [--head REF] [--json]

Defaults target the WF2 script-extraction effort (#83..#89).
"""
import argparse
import json
import re
import subprocess
import sys

# Effort default range: parent-of-#83 .. #89 merge.
DEFAULT_BASELINE = "fcd22b2"
DEFAULT_HEAD = "86fbbf7"

# Test files that received the new fail-closed CLI coverage in this effort.
FAILCLOSED_TEST_FILES = [
    "tests/hooks/test_headless.py",
    "tests/hooks/test_resume_lib.py",
    "tests/hooks/test_capabilities_lib.py",
]
# Regex (alternation) marking an adversarial / fail-closed test line. NOTE: this
# counts matching LINES (test names + assertions) across the adversarial test
# surface — a proxy/lower-bound indicator, NOT a literal `assert`-statement count.
_FAILCLOSED_RE = (
    r"pytest\.raises|assert rc != 0|assert rc == 1|fails_closed|rejects|"
    r"_invalid|_raises|malformed|present_null|out_of_range|unusable|corrupt"
)

_SHORTSTAT_RE = re.compile(
    r"(?:(\d+) files? changed)?"
    r"(?:, (\d+) insertions?\(\+\))?"
    r"(?:, (\d+) deletions?\(-\))?"
)


def parse_shortstat(line: str) -> dict:
    """Parse a `git diff --shortstat` line into {files, insertions, deletions}.

    Any clause may be absent (git omits the insertions/deletions clause when that
    side is zero) — an absent clause counts as 0 rather than crashing.
    """
    m = _SHORTSTAT_RE.match(line.strip())
    if not m:
        return {"files": 0, "insertions": 0, "deletions": 0}
    files, ins, dels = m.groups()
    return {
        "files": int(files or 0),
        "insertions": int(ins or 0),
        "deletions": int(dels or 0),
    }


def _git(*args) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], capture_output=True, text=True)


def _grep_count(ref: str, pattern: str, pathspec: str, extended: bool = False) -> int:
    """Count matching lines via `git grep` at a ref. git grep exits 1 on no
    matches (not an error); anything else is surfaced."""
    flags = ["-h"] + (["-E"] if extended else [])
    r = _git("grep", *flags, pattern, ref, "--", pathspec)
    if r.returncode not in (0, 1):
        raise RuntimeError(f"git grep failed: {r.stderr.strip()}")
    return len([ln for ln in r.stdout.splitlines() if ln])


def _shortstat(base: str, head: str, pathspec: str) -> dict:
    r = _git("diff", "--shortstat", f"{base}..{head}", "--", pathspec)
    if r.returncode != 0:
        raise RuntimeError(f"git diff failed: {r.stderr.strip()}")
    return parse_shortstat(r.stdout.strip().splitlines()[0] if r.stdout.strip() else "")


def collect_metrics(baseline: str, head: str) -> dict:
    """Collect the deterministic Tier-1 metrics over baseline..head."""
    metrics = {
        "baseline": baseline,
        "head": head,
        "test_defs": {
            "baseline": _grep_count(baseline, "def test_", "tests/"),
            "head": _grep_count(head, "def test_", "tests/"),
        },
        "parametrize_blocks": {
            "baseline": _grep_count(baseline, "parametrize", "tests/"),
            "head": _grep_count(head, "parametrize", "tests/"),
        },
        "diff": {
            "skills": _shortstat(baseline, head, "skills/"),
            "hooks_py": _shortstat(baseline, head, "hooks/*.py"),
            "tests": _shortstat(baseline, head, "tests/"),
        },
        "failclosed_markers": {},
        "new_hooks_libs": [],
    }
    for f in FAILCLOSED_TEST_FILES:
        metrics["failclosed_markers"][f] = _grep_count(
            head, _FAILCLOSED_RE, f, extended=True)
    # New hooks/*.py libraries added in the range.
    r = _git("diff", "--name-status", f"{baseline}..{head}", "--", "hooks/*.py")
    metrics["new_hooks_libs"] = [
        ln.split("\t", 1)[1] for ln in r.stdout.splitlines()
        if ln.startswith("A")
    ]
    return metrics


def render_markdown(m: dict) -> str:
    td, pb = m["test_defs"], m["parametrize_blocks"]
    fc_total = sum(m["failclosed_markers"].values())
    lines = [
        f"# WF2 impact metrics ({m['baseline']}..{m['head']})",
        "",
        f"- Test functions: **{td['baseline']} -> {td['head']}** "
        f"(+{td['head'] - td['baseline']})",
        f"- Parametrize blocks: {pb['baseline']} -> {pb['head']}",
        f"- Fail-closed/adversarial test markers (grep matches, new CLI test files): **{fc_total}**",
        f"- New hooks libraries: {', '.join(m['new_hooks_libs']) or '(none)'}",
        "- Diff volume:",
        f"    - skills/ (prose): +{m['diff']['skills']['insertions']} "
        f"-{m['diff']['skills']['deletions']} ({m['diff']['skills']['files']} files)",
        f"    - hooks/*.py (code): +{m['diff']['hooks_py']['insertions']} "
        f"-{m['diff']['hooks_py']['deletions']}",
        f"    - tests/: +{m['diff']['tests']['insertions']} "
        f"-{m['diff']['tests']['deletions']}",
    ]
    return "\n".join(lines)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="wf2_impact_metrics")
    p.add_argument("--baseline", default=DEFAULT_BASELINE)
    p.add_argument("--head", default=DEFAULT_HEAD)
    p.add_argument("--json", action="store_true", help="emit JSON instead of markdown")
    args = p.parse_args(argv)
    try:
        m = collect_metrics(args.baseline, args.head)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(m, indent=2) if args.json else render_markdown(m))
    return 0


if __name__ == "__main__":
    sys.exit(main())
