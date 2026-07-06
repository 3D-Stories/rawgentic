"""Drift guards for WF3 (fix-bug) merge/close conditionality reconciliation (#182).

The <mandatory-rule> block was a legacy shared template ("Steps 12-14 ... NEVER
optional") copied into WF3 verbatim (2026-03-03 cross-skill correction "C1") and never
reconciled with WF3's own <mandatory-steps>, which correctly marks Steps 11-13
(CI / merge / post-deploy) conditional — only Step 14 always runs. Compounded: Step 14
closed the GitHub issue UNCONDITIONALLY, so a run where the user never requested merge
closed a bug whose fix never merged (Step 10 already commits `(closes #N)`, which
auto-closes the issue on the owner's merge). These guards pin the reconciled contract
so the two blocks can't re-diverge.

Companion to tests/test_wf2_clarity.py (the WF2 sibling).
"""
import re
from pathlib import Path

from tests.corpus import skill_corpus

REPO_ROOT = Path(__file__).resolve().parent.parent


def _text() -> str:
    # Corpus (SKILL.md + references/) so the #158 prose restructure can move a
    # block without weakening the guard — same convention as test_wf2_clarity.
    return skill_corpus("fix-bug")


def _block(text: str, tag: str) -> str:
    m = re.search(rf"<{tag}>(.*?)</{tag}>", text, re.DOTALL)
    assert m, f"<{tag}> block not found in fix-bug corpus"
    return m.group(1)


def _section(text: str, header: str, next_header: str) -> str:
    start = text.index(header)
    end = text.index(next_header, start)
    return text[start:end]


def _normalize_ranges(s: str) -> str:
    """Canonicalize step ranges so rewordings compare equal: en/em dash -> '-',
    'X through Y' / 'X to Y' -> 'X-Y'. Defeats the en-dash bypass (12-14 vs 12–14)
    the first version of this guard missed."""
    s = s.replace("–", "-").replace("—", "-")
    s = re.sub(r"(\d+)\s*(?:through|to)\s*(\d+)", r"\1-\2", s)
    return s


# A "non-optional" assertion must never be glued to a CONDITIONAL step/subject in one
# clause — that is the #182 contradiction, in ANY phrasing. Kept as data so the guard
# tests the semantics, not one verbatim string.
_NONOPTIONAL_CLAIMS = ("never optional", "never skipped", "not optional", "always run", "must run")
_CONDITIONAL_SUBJECTS = ("11-14", "12-14", "13-14", "11-13", "12-13",
                         # natural comma/"and" list rephrasings of the same lumping
                         "12, 13 and 14", "12, 13, 14", "11, 12 and 13", "12 and 13", "13 and 14",
                         "merge", "deploy", "post-deploy", "ci verification")


# --- Canonical conditional source: <mandatory-steps> marks 11-13 conditional. ---
# This is what the <mandatory-rule> must not contradict; pinning it makes the
# divergence guard below meaningful (drift on either side trips a test).

def test_mandatory_steps_marks_ci_merge_postdeploy_conditional():
    block = _block(_text(), "mandatory-steps")
    assert "Step 11 (CI): skip only if has_ci == false" in block
    assert "Step 12 (Merge/Deploy): skip only if" in block
    assert "Step 13 (Post-Deploy): skip only if" in block


# --- The bug: <mandatory-rule> must NOT lump conditional 12-13 as "never optional". ---

def test_mandatory_rule_consistent_with_conditional_steps():
    rule = _block(_text(), "mandatory-rule")
    low = _normalize_ranges(rule.lower())
    # Semantic guard (not a single verbatim string): within any one clause the rule must
    # never assert a conditional step/subject is non-optional, however reworded. Splitting
    # per sentence/line lets the rule legitimately say "Step 14 always runs" elsewhere.
    for clause in re.split(r"(?<=[.\n])", low):
        for claim in _NONOPTIONAL_CLAIMS:
            if claim not in clause:
                continue
            for subj in _CONDITIONAL_SUBJECTS:
                assert subj not in clause, (
                    f"<mandatory-rule> re-diverged (#182): clause asserts '{claim}' about "
                    f"conditional subject '{subj}' — contradicts <mandatory-steps>:\n{clause.strip()!r}"
                )
    # Positive expectations for the reconciled rule (adversarial finding #3):
    # Step 14 is the always-run completion closure, and 11-13 stay conditional.
    assert "step 14" in low, "the reconciled rule must name Step 14 as the always-run closure"
    assert "conditional" in low, "the reconciled rule must state Steps 11-13 are conditional"


# --- Step 14: issue closure gated on a VERIFIED successful merge, not unconditional. ---

def test_step14_issue_close_gated_on_verified_merge():
    s14 = _section(_text(), "## Step 14:", "## Workflow Resumption")
    low = s14.lower()
    # Closure relies on the (closes #N) linkage on the owner's merge...
    assert "closes #" in s14, (
        "Step 14 must rely on the PR's (closes #N) linkage to close the issue on merge (#182)"
    )
    # ...and the DIRECT `gh issue close <number>` command must sit INSIDE a verified-merge
    # conditional. Structural check, not a bare "merged" substring — "merged" also appears
    # in the run-record schema, so it would pass even if the gate were reverted (finding #3).
    gate = 'if [ "$merged" = "true" ]'
    cmd = "gh issue close <number>"
    assert gate in s14, (
        "Step 14's direct issue-close must be gated behind a verified-merge check (#182): " + gate
    )
    assert cmd in s14, f"Step 14 must issue the {cmd!r} command"
    assert s14.index(cmd) > s14.index(gate), (
        f"the {cmd!r} command must appear AFTER the verified-merge gate, i.e. inside the "
        "conditional — not reachable unconditionally (#182)"
    )
    # ...and the not-merged path must explicitly forbid closing here.
    assert re.search(r"do not close|not.*close.*issue", low), (
        "Step 14 must state an unmerged PR's issue is NOT closed here (#182)"
    )


# --- Companion Medium: Step 11 detail documents the has_ci == false skip. ---

def test_step11_documents_has_ci_false_skip():
    s11 = _section(_text(), "## Step 11:", "## Step 12:")
    assert "has_ci" in s11, (
        "Step 11 detail must state it is skipped when has_ci == false, matching "
        "<mandatory-steps> (#182 companion Medium)"
    )
