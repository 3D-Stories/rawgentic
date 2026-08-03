"""M0b negative guard (#866, roadmap 2026-08-03 §4 M0b, D174).

Active WORKFLOW prose names no executor entry point. After the behavioral
cutover, WF2/WF3 run analysis + implementation inline and dispatch reviews
through hooks/review_runner.py via a harness subagent — so any surviving
executor invocation in the active prose is a live instruction pointing at a
machine being deleted (M0d) and would break the first run that follows it.

Scope (deliberate): M0b covered the four cutover workflow skills + the shared
blocks that sync into them; M0c (config contraction) added skills/setup and
agents/, plus the retired config-key names to the vocabulary. The repo-wide
sweep is M0d's retirement tripwire. `*-workspace/` dirs are archival and exempt.
"""
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

# Entry-point vocabulary: commands/identifiers that INVOKE the executor path,
# plus (since M0c) the retired config-key names whose setup/config surfaces
# were removed.
FORBIDDEN = (
    "executor_routing_lib",
    "begin-run",
    "mint-gate",
    "--seat",
    "collect-work-product",
    "land-work-product",
    "rawgentic:rawgentic-implementer",
    "rawgentic:rawgentic-reviewer",
    "model_routing_lib",
    "phaseExecutorTable",
    "executorTerminalBackend",
    "telemetryAlerts",
    # "phase_executor" is deliberately absent: until M0d deletes the package,
    # active prose may legitimately name the DIRECTORY (test-scope exclusions).
    # M0d's repo-wide retirement tripwire adds it.
)

ACTIVE_PROSE_DIRS = (
    "skills/implement-feature",
    "skills/fix-bug",
    "skills/adversarial-review",
    "skills/peer-consult",
    "skills/setup",
    "shared/blocks",
    "agents",
)


def _active_prose_files():
    files = []
    for rel in ACTIVE_PROSE_DIRS:
        base = REPO_ROOT / rel
        assert base.is_dir(), f"expected active prose dir missing: {rel}"
        files.extend(p for p in sorted(base.rglob("*.md")))
    assert files, "no active prose files found — scope glob broke"
    return files


def test_active_workflow_prose_names_no_executor_entry_point():
    offenders = []
    for path in _active_prose_files():
        text = path.read_text(encoding="utf-8")
        for needle in FORBIDDEN:
            if needle in text:
                line_no = next(
                    i for i, line in enumerate(text.splitlines(), 1)
                    if needle in line
                )
                offenders.append(
                    f"{path.relative_to(REPO_ROOT)}:{line_no}: contains {needle!r}")
    assert not offenders, (
        "active workflow prose still names executor entry points "
        "(M0b cutover incomplete):\n" + "\n".join(offenders))
