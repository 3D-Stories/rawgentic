"""Retirement tripwire (#866, M0d of the executor retreat).

Retired executor/headless vocabulary must never reappear on an ACTIVE surface.
Fail-mode: fail-closed — any hit in a non-archival git-tracked file fails, and
`tests/` is deliberately NOT archival, so an orphaned test referencing retired
machinery fails here too.

Archival surfaces keep their history (deleting evidence is not retirement):
planning docs, review reports, measurements, *-workspace assessments, test
fixtures, claude_docs working memory, the README Changelog section, and the
versioned workflow diagram (historical REVs pin the prose of their era).

Deliberately NOT in the vocabulary:
- bare ``RAWGENTIC_HEADLESS`` — D184 keeps it as the unattended-session signal
  (context_meter routing, scanner_bootstrap skip, setup Step 2e guard); only
  the ``RAWGENTIC_HEADLESS_TRIGGER`` orchestration variant is retired.
- bare "executor" / "seat" — retirement notes legitimately use the words; the
  tripwire pins the concrete module/command/config tokens instead.
"""

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

RETIRED_VOCABULARY = [
    "executor_routing_lib",
    "seat_outcomes_lib",
    "complexity_gate",
    "bakeoff_policy",
    "driver_bench_lib",
    "diagram_seat_data",
    "phase_executor",
    "begin-run",
    "mint-gate",
    "collect-work-product",
    "land-work-product",
    "headless_interaction",
    "headless_ssh_guard",
    "headless_suspend",
    "RAWGENTIC_HEADLESS_TRIGGER",
    "phaseExecutorTable",
    "executorTerminalBackend",
    "telemetryAlerts",
    "executorRouting",
    "rawgentic-implementer",
]

EXEMPT_PREFIXES = (
    "docs/planning/",
    "docs/reviews/",
    "docs/measurements/",
    "tests/fixtures/",
    "claude_docs/",
)

EXEMPT_FILES = {
    # Versioned document: historical REVs pin the prose of their plugin era.
    "docs/workflow-diagram.html",
    # This file names the vocabulary by definition.
    "tests/test_retirement_tripwire.py",
    # Sibling guard (M0b/M0c): richer prose-scoped vocabulary (--seat, bare
    # model_routing_lib, the prefixed agent names) over the workflow skills.
    "tests/test_no_executor_prose.py",
}

# A line carrying this marker is a deliberate, visible exception — used ONLY
# for executable negative assertions whose subject IS the retired token
# (e.g. `assert "phaseExecutorTable" not in text`). Prose never gets a pragma;
# it gets reworded. Hardened per the M0d adversarial review: the pragma is
# honored ONLY inside tests/ and ONLY with a stated reason ("tripwire-exempt:
# <why>") — on a production surface it is inert and the line still counts.
PRAGMA = "tripwire-exempt"


def _hits(rel_path: str, text: str) -> list[str]:
    """Retired-vocabulary terms present in this file's ACTIVE lines."""
    lines = text.splitlines()
    if rel_path.startswith("tests/"):
        lines = [ln for ln in lines if (PRAGMA + ":") not in ln]
    body = "\n".join(lines)
    return [term for term in RETIRED_VOCABULARY if term in body]

README = "README.md"
CHANGELOG_HEADING = "## Changelog"


def _is_exempt(rel_path: str) -> bool:
    if rel_path in EXEMPT_FILES:
        return True
    if rel_path.startswith(EXEMPT_PREFIXES):
        return True
    # Workspace assessment dirs (skills/<name>-workspace/, evals) are archival.
    if "-workspace/" in rel_path:
        return True
    return False


def _tracked_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    ).stdout
    return [p for p in out.split("\0") if p]


def _scannable_text(rel_path: str) -> str | None:
    path = REPO_ROOT / rel_path
    # Codex-mirror skill entries are symlinks to directories; their targets
    # are tracked (and scanned) under skills/ already.
    if path.is_symlink() or not path.is_file():
        return None
    data = path.read_bytes()
    if b"\0" in data:  # binary (png, woff2, ...)
        return None
    text = data.decode("utf-8", errors="ignore")
    if rel_path == README:
        # Only the prose ABOVE the Changelog is active; entries are history.
        idx = text.find(CHANGELOG_HEADING)
        if idx != -1:
            text = text[:idx]
    return text


def test_no_retired_vocabulary_on_active_surfaces():
    hits: dict[str, list[str]] = {}
    for rel_path in _tracked_files():
        if _is_exempt(rel_path):
            continue
        text = _scannable_text(rel_path)
        if text is None:
            continue
        found = _hits(rel_path, text)
        if found:
            hits[rel_path] = found
    assert not hits, (
        "Retired executor/headless vocabulary found on active surfaces "
        "(#866 — delete, rewrite, or move the content to an archival dir):\n"
        + "\n".join(f"  {p}: {', '.join(terms)}" for p, terms in sorted(hits.items()))
    )


def test_tests_dir_is_not_archival():
    """Guard the guard: nobody quietly exempts tests/ wholesale."""
    assert not any(p == "tests/" or p == "tests" for p in EXEMPT_PREFIXES)
    assert not any(p.startswith("tests/") and p != "tests/fixtures/"
                   for p in EXEMPT_PREFIXES)


def test_pragma_is_inert_outside_tests_and_without_a_reason():
    """The pragma cannot hide a resurrection on a production surface (M0d
    adversarial-review finding), and a bare marker with no reason is inert
    even inside tests/."""
    line = "import executor_routing_lib  # " + PRAGMA + ": negative guard"
    assert _hits("hooks/foo.py", line) == ["executor_routing_lib"]
    assert _hits("skills/x/SKILL.md", line) == ["executor_routing_lib"]
    assert _hits("tests/test_x.py", line) == []
    bare = "import executor_routing_lib  # " + PRAGMA
    assert _hits("tests/test_x.py", bare) == ["executor_routing_lib"]


# D184 (owner decision, #866/#871): bare RAWGENTIC_HEADLESS survives as the
# unattended-session signal in EXACTLY these active-surface files — the three
# code/prose reads plus the two documents that describe them. Both directions
# guarded: a NEW mention is a resurrection; a MISSING one means a D184
# survivor was deleted.
D184_SIGNAL = "RAWGENTIC" + "_HEADLESS"  # split so this file self-exempts
D184_ALLOWED_FILES = {
    # hooks/context_meter.py, hooks/scanner_bootstrap.py — cut over (#943)
    "skills/setup/SKILL.md",        # Step 2e install guard
    "README.md",                    # the Headless Mode (retired) note
    "docs/context-meter.md",        # documents the handoff routing
}


def test_d184_unattended_signal_reads_are_exactly_the_allowed_set():
    carriers = set()
    for rel_path in _tracked_files():
        if _is_exempt(rel_path) or rel_path.startswith("tests/"):
            continue
        text = _scannable_text(rel_path)
        if text is None:
            continue
        if D184_SIGNAL in text.replace(D184_SIGNAL + "_TRIGGER", ""):
            carriers.add(rel_path)
    assert carriers == D184_ALLOWED_FILES, (
        f"D184 drift — new: {sorted(carriers - D184_ALLOWED_FILES)}, "
        f"missing: {sorted(D184_ALLOWED_FILES - carriers)}"
    )
