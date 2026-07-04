"""Drift guards for the WF4/WF7/WF8/WF9/WF10/WF12 deprecation stubs (#160).

Evidence (AC12): run-records 12/12 = WF2 only, zero session-note traces,
design-doc mtimes frozen — the six workflows are unused. Each SKILL.md becomes
a short stub: frontmatter preserved (still invocable), body = deprecation
notice + redirect to the replacement + a STUB-FIRED telemetry line + exit.
Removal is the v3.0.0 release issue (#161); a stub firing during the cycle is
data for a keep re-verdict, so the telemetry line is load-bearing.
"""
import re
from pathlib import Path

import pytest

from tests.corpus import SKILLS_DIR, skill_corpus

REPO_ROOT = Path(__file__).resolve().parent.parent

# skill -> the replacement its redirect must name
DEPRECATED = {
    "refactor": "implement-feature",           # WF4  -> WF2 refactor-typed issue
    "update-docs": "implement-feature",        # WF7  -> WF2 docs-typed issue
    "update-deps": "implement-feature",        # WF8  -> WF2 deps-typed issue
    "security-audit": "/security-review",      # WF9  -> built-in review (+ /rawgentic:scan tooling)
    "optimize-perf": "implement-feature",      # WF10 -> WF2 perf-typed issue
    "create-tests": "superpowers",             # WF12 -> superpowers TDD skills
}

MAX_STUB_LINES = 60  # a stub is a redirect, not a workflow


@pytest.mark.parametrize("skill", sorted(DEPRECATED))
def test_stub_keeps_frontmatter(skill):
    """Still invocable: name + description survive; description says deprecated."""
    text = (SKILLS_DIR / skill / "SKILL.md").read_text(encoding="utf-8")
    assert f"name: rawgentic:{skill}" in text
    m = re.match(r"\A---\n(.*?)\n---\n", text, re.DOTALL)
    assert m, f"{skill} stub lost its frontmatter"
    assert "DEPRECATED" in m.group(1), f"{skill} description must announce deprecation"


@pytest.mark.parametrize("skill", sorted(DEPRECATED))
def test_stub_is_short_and_redirects(skill):
    text = (SKILLS_DIR / skill / "SKILL.md").read_text(encoding="utf-8")
    assert len(text.splitlines()) <= MAX_STUB_LINES, (
        f"{skill}/SKILL.md must be a stub (<= {MAX_STUB_LINES} lines), "
        f"got {len(text.splitlines())}"
    )
    assert DEPRECATED[skill] in text, f"{skill} stub must redirect to {DEPRECATED[skill]!r}"
    assert "v3.0.0" in text, f"{skill} stub must name the removal release"


@pytest.mark.parametrize("skill", sorted(DEPRECATED))
def test_stub_logs_a_firing(skill):
    """Success metric: 0 stub-redirect firings over the cycle. Unmeasurable
    unless every stub logs its own firing before redirecting."""
    text = (SKILLS_DIR / skill / "SKILL.md").read_text(encoding="utf-8")
    assert "STUB FIRED" in text, f"{skill} stub must log a STUB FIRED line"
    assert "session_notes.md" in text


def test_security_audit_stub_names_scan_tooling():
    """WF9's tooling survives its skill: the stub must point at /rawgentic:scan."""
    text = (SKILLS_DIR / "security-audit" / "SKILL.md").read_text(encoding="utf-8")
    assert "rawgentic:scan" in text


def test_scan_skill_exists_and_wraps_full_scan():
    """AC2: thin utility skill wrapping security_scan.py --full."""
    corpus = skill_corpus("scan")
    assert "name: rawgentic:scan" in corpus
    assert "security_scan.py" in corpus
    assert "--full" in corpus


def test_step_11_5_scan_invocation_unchanged():
    """AC2: no fail-closed gate weakened — WF2 Step 11.5 still invokes the
    scanner lib exactly as before; the scan skill is additive tooling."""
    corpus = skill_corpus("implement-feature")
    assert "hooks/security_scan.py scan" in corpus
    assert "--base-ref origin/" in corpus
    # the deprecation must not have touched the shared lib's gate semantics
    scan = (REPO_ROOT / "hooks" / "security_scan.py").read_text(encoding="utf-8")
    assert "--full" in scan  # the flag the scan skill wraps


@pytest.mark.parametrize("skill", sorted(DEPRECATED))
def test_stub_still_registered_in_marketplace(skill):
    """Stubs stay installable for the whole cycle — removal is #161, not now."""
    import json
    mp = json.loads((REPO_ROOT / ".claude-plugin" / "marketplace.json").read_text())
    assert f"./skills/{skill}" in mp["plugins"][0]["skills"]


def test_scan_registered_in_marketplace():
    import json
    mp = json.loads((REPO_ROOT / ".claude-plugin" / "marketplace.json").read_text())
    skills = mp["plugins"][0]["skills"]
    assert "./skills/scan" in skills
    # alphabetical placement: refactor < scan < security-audit
    assert skills.index("./skills/scan") == skills.index("./skills/refactor") + 1
