"""hooks/org_runners_lib.py — tested core of `rawgentic:admit-to-org-runners` (#397).

Migrating a repo's CI workflow from GitHub-hosted runners to an org self-hosted
runner fleet is easy to get wrong in ways that silently kill CI: targeting a label
that no ONLINE runner carries strands every future run (queued forever); a partial
edit leaves a GitHub-hosted fallback that, on an org whose Actions minutes are
exhausted, means CI is simply dead.

This module is the fail-closed core the skill shells out to. The SKILL.md drives the
`gh api` calls (runner-group discovery, admit); THIS decides what is safe to rewrite:

  - classify each workflow `runs-on:` (hosted / already-fleet / expression / list),
  - map a hosted OS -> the minimal labels a fleet job needs,
  - confirm an ONLINE runner in the group carries those labels before editing,
  - plan the migration fail-closed: ANY un-migratable hosted lane refuses the WHOLE
    file (never a partial migration that leaves a hosted fallback),
  - rewrite a hosted scalar into a `{group, labels}` block, touching nothing else.

No new dependency: workflow YAML is scanned line-by-line (a targeted `runs-on:`
rewrite that preserves every other byte), never round-tripped through a YAML dumper
that would reflow comments/quoting.
"""
from __future__ import annotations

import argparse
import json
import re
import sys

from atomic_write_lib import atomic_write_text

# Minimal labels a job needs to land on a fleet runner of each OS. GitHub matches a
# job to a runner iff the runner carries ALL the job's labels (case-insensitive), so
# these are a SUBSET the online runner must satisfy — never the runner's full set.
DEFAULT_OS_LABELS = {
    "linux": ["self-hosted", "linux"],
    "windows": ["self-hosted", "windows"],
    "macos": ["self-hosted", "macos"],
}

# GitHub-HOSTED runner image label prefix -> OS. `ubuntu-latest`, `windows-2022`,
# `macos-14` all split on the first '-'. A bare self-hosted label ('linux',
# 'self-hosted', a custom name) has no hosted prefix and maps to None.
_HOSTED_PREFIX = {"ubuntu": "linux", "windows": "windows", "macos": "macos"}

# Tolerates spec-valid variants that still parse to a `runs-on` key: whitespace
# before the colon and an optionally-quoted key. Missing these silently drops a
# hosted lane (it reads as "clean"), which is the exact silent-CI-death to avoid.
_RUNS_ON_RE = re.compile(r"""^(\s*)["']?runs-on["']?\s*:(.*)$""")


def hosted_os(label: str) -> str | None:
    """OS for a GitHub-HOSTED runner label (ubuntu*/windows*/macos*), else None.
    Quotes are stripped first so a quoted scalar (`"ubuntu-latest"`) is still seen
    as hosted — matching what `_list_items` already does for list members."""
    if not label:
        return None
    head = label.strip().strip("'\"").lower().split("-", 1)[0]
    return _HOSTED_PREFIX.get(head)


def _strip_comment(s: str) -> str:
    """Drop a trailing YAML inline comment (` # ...`) — a comment requires a space
    before the `#`, so a `#` inside a value is preserved."""
    return s.split(" #", 1)[0].strip()


def _list_items(s: str) -> list[str]:
    """Items of an inline `[a, b]` or a block `- a\\n- b` labels list."""
    s = s.strip()
    if s.startswith("["):
        inner = s[1:s.rindex("]")] if "]" in s else s[1:]
        return [x.strip().strip("'\"") for x in inner.split(",") if x.strip()]
    out = []
    for ln in s.splitlines():
        ln = ln.strip()
        if ln.startswith("- "):
            out.append(ln[2:].strip().strip("'\""))
    return out


def classify_runs_on(raw: str) -> str:
    """Classify the text after `runs-on:` (inline scalar OR a reconstructed block).

    Returns one of:
      - 'hosted'      a bare GitHub-hosted image label -> safely migratable
      - 'fleet'       already a {group,labels} mapping or a self-hosted labels list
      - 'expression'  contains `${{ }}` -> never rewritten (fails closed)
      - 'manual'      any other shape (incl. a hosted label buried in a list)
    """
    s = _strip_comment(raw.strip())
    if not s:
        return "manual"
    if "${{" in s:
        return "expression"
    if s.startswith("[") or s.lstrip().startswith("- "):
        items = _list_items(s)
        if any(hosted_os(i) for i in items):
            return "manual"  # a hosted label inside a list — never mangle
        if any(i.lower() == "self-hosted" for i in items):
            return "fleet"
        return "manual"
    if "group:" in s:  # a {group, labels} mapping block -> already on the fleet
        return "fleet"
    if hosted_os(s):
        return "hosted"
    return "manual"  # an unknown bare scalar (a custom single self-hosted label, …)


def labels_satisfied_by(required, runner_labels) -> bool:
    """True iff every `required` label is present in `runner_labels` (GitHub label
    matching is case-insensitive, so 'linux' is satisfied by a runner's 'Linux')."""
    have = {str(x).lower() for x in (runner_labels or [])}
    return all(str(x).lower() in have for x in (required or []))


def online_runner_for(required, runners):
    """First ONLINE runner whose labels satisfy `required`, else None. An offline
    runner that would match is never selected — migrating to it strands the job."""
    for r in runners or []:
        if str(r.get("status", "")).lower() == "online" \
                and labels_satisfied_by(required, r.get("labels", [])):
            return r
    return None


def find_runs_on(text: str) -> list[dict]:
    """Every `runs-on:` in the workflow, with its classification. Handles the inline
    scalar form and the block-mapping form (`runs-on:` then indented `group:`/`labels:`)."""
    lines = text.splitlines()
    occ = []
    i = 0
    n = len(lines)
    while i < n:
        m = _RUNS_ON_RE.match(lines[i])
        if not m:
            i += 1
            continue
        indent, rest = m.group(1), _strip_comment(m.group(2).strip())
        if rest:
            value_repr, end = rest, i
        else:  # block form — gather following lines indented deeper than runs-on
            block, j = [], i + 1
            while j < n:
                ln = lines[j]
                if ln.strip() == "":
                    j += 1
                    continue
                if len(ln) - len(ln.lstrip()) <= len(indent):
                    break
                block.append(ln)
                j += 1
            value_repr, end = "\n".join(block), j - 1
        kind = classify_runs_on(value_repr)
        occ.append({
            "line": i, "indent": indent, "kind": kind, "raw": value_repr,
            "os": hosted_os(value_repr) if kind == "hosted" else None,
        })
        i = end + 1
    return occ


def has_hosted_remnant(text: str) -> bool:
    """True if any `runs-on:` still resolves (directly) to a GitHub-hosted image —
    a bare hosted scalar, or a hosted label buried in a list. Expression/matrix
    forms are NOT decidable here; the migration planner refuses those as 'manual'."""
    for o in find_runs_on(text):
        if o["kind"] == "hosted":
            return True
        if any(hosted_os(item) for item in _list_items(o["raw"])):
            return True
    return False


def is_migrated(text: str) -> bool:
    """No directly-hosted `runs-on:` remains (the post-migration / idempotency target)."""
    return not has_hosted_remnant(text)


def _verdict(jobs: list[dict]) -> str:
    """Overall verdict, fail-closed: any blocked lane blocks the whole file; any
    manual lane needs a human; otherwise ready if there's work, else noop."""
    actions = {j["action"] for j in jobs}
    if "blocked" in actions:
        return "blocked"
    if "manual" in actions:
        return "manual"
    if "migrate" in actions:
        return "ready"
    return "noop"


def plan_migration(text: str, group: str, runners, os_labels=None) -> dict:
    """Per-`runs-on` migration plan + an overall fail-closed verdict."""
    os_labels = os_labels or DEFAULT_OS_LABELS
    jobs = []
    for o in find_runs_on(text):
        base = {"line": o["line"], "indent": o["indent"], "kind": o["kind"], "os": o["os"]}
        if o["kind"] == "fleet":
            jobs.append({**base, "action": "skip", "reason": "already on the fleet"})
        elif o["kind"] == "hosted":
            required = os_labels.get(o["os"])
            if required and online_runner_for(required, runners):
                jobs.append({**base, "action": "migrate", "target_labels": required,
                             "reason": f"hosted {o['os']} -> group '{group}' {required}"})
            else:
                jobs.append({**base, "action": "blocked",
                             "reason": f"no ONLINE runner in group '{group}' "
                                       f"carrying labels {required}"})
        elif o["kind"] == "expression":
            jobs.append({**base, "action": "manual",
                         "reason": "runs-on is an expression (${{ }}); migrate by hand"})
        else:
            jobs.append({**base, "action": "manual",
                         "reason": "runs-on shape not safely rewritable; migrate by hand"})
    return {"group": group, "verdict": _verdict(jobs), "jobs": jobs}


def rewrite_migration(text: str, plan: dict, group: str) -> str:
    """Apply the plan's 'migrate' jobs (hosted scalar -> {group,labels} block),
    preserving every other line. Refuses unless the verdict is ready/noop."""
    if plan["verdict"] not in ("ready", "noop"):
        raise ValueError(
            f"refusing to rewrite: verdict '{plan['verdict']}' — resolve the "
            f"blocked/manual lanes first (never leave a hosted fallback)")
    if plan["verdict"] == "noop":
        return text
    lines = text.splitlines(keepends=True)
    # bottom-to-top so earlier edits don't shift later line indices
    for j in sorted((j for j in plan["jobs"] if j["action"] == "migrate"),
                    key=lambda j: j["line"], reverse=True):
        idx, indent = j["line"], j["indent"]
        orig = lines[idx]
        # Never "" — an unterminated last line would otherwise collapse the block
        # onto one line (invalid YAML the post-check would wrongly pass).
        eol = "\r\n" if orig.endswith("\r\n") else "\n"
        labels = ", ".join(j["target_labels"])
        lines[idx] = (f"{indent}runs-on:{eol}"
                      f"{indent}  group: {group}{eol}"
                      f"{indent}  labels: [{labels}]{eol}")
    return "".join(lines)


# ---------------------------------------------------------------------------
# CLI — the SKILL.md shells out to these; runner data arrives as JSON on stdin
# (from `gh api …/runner-groups/<id>/runners`), never fetched here.
# ---------------------------------------------------------------------------

def _load_runners(arg: str):
    data = sys.stdin.read() if arg == "-" else open(arg, encoding="utf-8").read()
    return json.loads(data)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="org_runners_lib")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("plan", "rewrite"):
        sp = sub.add_parser(name)
        sp.add_argument("--workflow", required=True)
        sp.add_argument("--group", required=True)
        sp.add_argument("--runners", required=True,
                        help="path to runner JSON, or '-' for stdin")
        if name == "rewrite":
            sp.add_argument("--in-place", action="store_true")
    cp = sub.add_parser("check-hosted")
    cp.add_argument("--workflow", required=True)
    args = p.parse_args(argv)

    text = open(args.workflow, encoding="utf-8").read()

    if args.cmd == "check-hosted":
        remnant = has_hosted_remnant(text)
        print(json.dumps({"hosted_remnant": remnant}))
        return 1 if remnant else 0

    runners = _load_runners(args.runners)
    plan = plan_migration(text, args.group, runners)

    if args.cmd == "plan":
        print(json.dumps(plan, indent=2))
        return 0 if plan["verdict"] in ("ready", "noop") else 1

    # rewrite
    try:
        out = rewrite_migration(text, plan, args.group)
    except ValueError as e:
        print(json.dumps({"error": str(e), "verdict": plan["verdict"]}), file=sys.stderr)
        return 1
    if has_hosted_remnant(out):  # fail-closed: never write a hosted fallback back out
        print(json.dumps({"error": "post-rewrite hosted remnant detected"}), file=sys.stderr)
        return 1
    if args.in_place:
        atomic_write_text(args.workflow, out)
    else:
        sys.stdout.write(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
