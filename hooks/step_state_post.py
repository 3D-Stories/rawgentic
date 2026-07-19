#!/usr/bin/env python3
"""PostToolUse hook: hook-level step-state emission (#499).

Derives the workflow now-pointer from artifacts the orchestrator already
produces reliably, so the statusline never depends on the model remembering
the manual `step_state.py write` call (#480's prose contract, observed
under-complied twice under batching on 2026-07-19):

1. MARKER detector (primary): a session-notes append's heredoc body rides in
   tool_input.command, so the newest ``### WF<n> Step <X>…DONE (#<issue>``
   marker is parsed straight from the command string (completion-time state).
2. SIGNATURE detector (entry-time): unmistakable per-step commands
   (security_scan.py scan, gh pr create, …). Signature hits carry no
   workflow/issue of their own — they reuse the existing state record ONLY
   when its session_id matches this event's (never stamp a foreign context).

Both write through the existing ``step_state.py write`` CLI (one helper, one
home — no second write path). Fail-OPEN everywhere: this is observational
telemetry, so any failure exits 0 with no stdout (PostToolUse stdout would
inject context into the conversation).
"""
import json
import os
import re
import subprocess
import sys

_HOOKS_DIR = os.path.dirname(os.path.abspath(__file__))
_STEP_STATE_CLI = os.path.join(_HOOKS_DIR, "step_state.py")
_REGISTRY_REL = os.path.join("claude_docs", "session_registry.jsonl")
_WORKSPACE_MARKER = ".rawgentic_workspace.json"

# ### WF2 Step 11: Pre-PR Code Review — DONE (#492: …)
# ### WF2 Step 8a [task 3, sha abc]: DONE (#492: …)
# ### WF2 Step 7: Create Branch — DONE (unkeyed legacy)
# Matched PER LINE (anchored, [ \t]-only whitespace, [^—\n] title class) after
# a startswith prefilter + length cap — the v1 MULTILINE `\s*(.*?)\s*—` form
# backtracked catastrophically on a marker prefix + long whitespace run
# (8a R2 #499, measured >10s), on a hook that runs for every Bash call.
_MARKER_RE = re.compile(
    r"### WF(\d+) Step ([0-9.]+a?b?)(?:[ \t]*\[[^\]]*\])?"
    r"(?::[ \t]*([^—\n]*?)[ \t]*—[ \t]*DONE|:[ \t]*DONE)[ \t]*(?:\(#(\d+))?",
)
_MARKER_LINE_CAP = 1024  # real markers are short single lines

# Ordered, KEYED BY WORKFLOW (8a R1 #499: the same commands land on different
# step numbers per workflow — WF3's PR/merge/summary are Steps 10/12/14, and it
# has no scan step). First match wins within a workflow's table; a workflow with
# no table (wf1/wf5/epic-run) is marker-only. The capabilities-derive row was
# dropped: a true first entry has no prior state record to key off, so it could
# only ever fire late and stomp a further-along position.
_SIGNATURES = {
    "wf2": (
        ("security_scan.py scan", ("11.5", "Security Scan")),
        ("gh pr create", ("12", "Create PR")),
        ("gh pr merge", ("14", "Merge")),
        ("work_summary.py summarize", ("16", "Completion Summary")),
    ),
    "wf3": (
        ("gh pr create", ("10", "Create Pull Request")),
        ("gh pr merge", ("12", "Merge and Deploy")),
        ("work_summary.py summarize", ("14", "Completion Summary")),
    ),
}


def detect_marker(command: str) -> "dict | None":
    """Parse the LAST step-DONE marker out of a Bash command string.

    Per-line matching with a length cap: structurally immune to the
    whitespace-run backtracking the one-shot MULTILINE scan allowed."""
    if not isinstance(command, str) or "### WF" not in command:
        return None
    m = None
    for line in command.splitlines():
        if len(line) > _MARKER_LINE_CAP or "### WF" not in line:
            continue
        hit = _MARKER_RE.match(line.strip())
        if hit:
            m = hit  # last matching line wins (append-only notes: newest last)
    if m is None:
        return None
    title = (m.group(3) or "").strip() or f"Step {m.group(2)}"
    issue = int(m.group(4)) if m.group(4) else None
    return {"workflow": f"wf{m.group(1)}", "step": m.group(2),
            "step_title": f"{title} ✓done", "issue": issue}


def detect_signature(command: str, workflow) -> "tuple[str, str] | None":
    """Match a per-step command AGAINST THE WORKFLOW'S OWN table; None when the
    workflow is unknown or carries no table (marker-only workflows)."""
    if not isinstance(command, str) or not isinstance(workflow, str):
        return None
    for needle, hit in _SIGNATURES.get(workflow, ()):
        if needle in command:
            return hit
    return None


def _may_have_signature(command) -> bool:
    """Cheap prefilter: any needle from any workflow's table (the workflow is
    only knowable after the state read, which this gate avoids on misses)."""
    if not isinstance(command, str):
        return False
    return any(needle in command
               for table in _SIGNATURES.values() for needle, _ in table)


def _find_workspace_root(cwd: str) -> "str | None":
    cur = os.path.abspath(cwd)
    while True:
        if os.path.exists(os.path.join(cur, _WORKSPACE_MARKER)):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            return None
        cur = parent


def resolve_project(registry_path: str, session_id: str) -> "str | None":
    """Last registry entry for this session wins; None when unregistered."""
    project = None
    try:
        with open(registry_path, encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                if entry.get("session_id") == session_id and entry.get("project"):
                    project = entry["project"]
    except OSError:
        return None
    return project


def _read_state(root: str, project: str) -> "dict | None":
    path = os.path.join(root, "claude_docs", "wal", f"{project}.state.json")
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _write(root, project, workflow, step, title, issue, session_id) -> None:
    argv = [sys.executable, _STEP_STATE_CLI, "write", "--project", project,
            "--workflow", workflow, "--step", str(step), "--step-title", title,
            "--session-id", session_id]
    if issue is not None:
        argv += ["--issue", str(issue)]
    subprocess.run(argv, cwd=root, capture_output=True, timeout=5, check=False)


def main() -> int:
    payload = json.load(sys.stdin)
    if payload.get("tool_name") != "Bash":
        return 0
    command = (payload.get("tool_input") or {}).get("command", "")
    marker = detect_marker(command)
    if not marker and not _may_have_signature(command):
        return 0
    session_id = payload.get("session_id") or os.environ.get("CLAUDE_CODE_SESSION_ID")
    if not session_id:
        return 0
    root = _find_workspace_root(os.getcwd())
    if root is None:
        return 0
    project = resolve_project(os.path.join(root, _REGISTRY_REL), session_id)
    if project is None:
        return 0
    if marker:
        _write(root, project, marker["workflow"], marker["step"],
               marker["step_title"], marker["issue"], session_id)
        return 0
    state = _read_state(root, project)
    if not state or state.get("session_id") != session_id:
        return 0  # never stamp over a foreign or unknown context
    signature = detect_signature(command, state.get("workflow"))
    if signature is None:
        return 0
    step, title = signature
    _write(root, project, state["workflow"], step, title,
           state.get("issue"), session_id)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BaseException:  # noqa: BLE001 — observational hook: fail open, always
        sys.exit(0)
