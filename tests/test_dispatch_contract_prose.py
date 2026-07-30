"""#733 — drift guards for the dispatch-failure contract prose.

Pins ONE canonical sentence per surface (repo CLAUDE.md mistake #6: no whole-corpus regex),
whitespace-normalized, on all THREE normative surfaces — the shared-block source, the
GENERATED implement-feature copy (a failed/no-op sync must fail here, not pass silently),
and the fix-bug bespoke copy — plus the #735 per-workflow-dispatch emission rule on fix-bug
(the harmonization #733 shipped; the two surfaces made mutually exclusive claims before it).
"""
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# The canonical #733 exit-3 sentence (table row tail) — identical on all three surfaces.
PARTIAL_IS_A_LEAD = "a `partial: true` payload is a lead, not a pass"
# The AC5 timeout-contract sentence.
TIMEOUT_DEFAULTS = ("An omitted `--timeout` defaults to the seat's own declared bound "
                    "(`resolve_dispatch_timeout`, #753); `--timeout` only tightens")
# The #735 per-workflow-dispatch emission anchor (fix-bug harmonization).
ONE_DISPATCH_LINE = ("emits exactly ONE canonical `DISPATCH` line once the loop completes")

SURFACES = [
    REPO / "shared" / "blocks" / "model-routing-resolve.md",
    REPO / "skills" / "implement-feature" / "SKILL.md",
    REPO / "skills" / "fix-bug" / "SKILL.md",
]


def _norm(text: str) -> str:
    return " ".join(text.split())


def test_partial_is_a_lead_on_all_three_surfaces():
    for path in SURFACES:
        assert _norm(PARTIAL_IS_A_LEAD) in _norm(path.read_text(encoding="utf-8")), path


def test_timeout_default_contract_on_all_three_surfaces():
    for path in SURFACES:
        assert _norm(TIMEOUT_DEFAULTS) in _norm(path.read_text(encoding="utf-8")), path


def test_fix_bug_carries_the_per_workflow_dispatch_emission_rule():
    text = _norm((REPO / "skills" / "fix-bug" / "SKILL.md").read_text(encoding="utf-8"))
    assert _norm(ONE_DISPATCH_LINE) in text
    # the pre-#733 stale rule must be gone: its heading claimed per-ATTEMPT emission
    assert "**Per-attempt emission rule:**" not in text