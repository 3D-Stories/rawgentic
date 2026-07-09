"""Drift guards for docs/codex-reliability.md (#334).

The #334 defect was a routing/guidance gap: the timeout-enforced consult path
(WF13 peer-consult + the `consult` CLI) existed and was tested, but nothing in the
repo directed cross-model consult work to it, documented the dead-job protocol, or
recorded the host userns runbook. These guards pin the three load-bearing pieces —
each anchors ONE canonical sentence in the ONE doc (location pin, direct file read).
"""

import os

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOC_PATH = os.path.join(REPO_ROOT, "docs", "codex-reliability.md")


def _doc_normalized() -> str:
    with open(DOC_PATH, encoding="utf-8") as f:
        return " ".join(f.read().split())


def test_doc_exists():
    assert os.path.isfile(DOC_PATH), "docs/codex-reliability.md missing (#334)"


def test_canonical_routing_sentence():
    # Whitespace-normalized: prose may hard-wrap mid-phrase.
    doc = _doc_normalized()
    assert (
        "route it through `/rawgentic:peer-consult` (WF13) or the "
        "`adversarial_review_lib.py consult` CLI — never a bare "
        "`codex:codex-rescue` dispatch" in doc
    ), "the canonical consult-routing rule must stay in codex-reliability.md"


def test_canonical_dead_job_rule():
    doc = _doc_normalized()
    assert (
        "a companion or rescue job silent past its deadline is DEAD: kill it and "
        "substitute — never keep waiting" in doc
    ), "the dead-job protocol sentence must stay in codex-reliability.md"


def test_host_failure_signature_anchored():
    doc = _doc_normalized()
    assert (
        "bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted" in doc
    ), "the userns failure signature must stay in the runbook section"


def test_runbook_recipe_tokens_anchored():
    # The executable fix is the doc's highest-value content: a typo there (e.g. a
    # dropped `userns,`) must not rot green behind signature-only guards.
    doc = _doc_normalized()
    assert (
        "profile bwrap /usr/bin/bwrap flags=(unconfined)" in doc
    ), "the AppArmor profile line must stay in the runbook recipe"
    assert "userns," in doc, "the userns grant must stay in the runbook recipe"
