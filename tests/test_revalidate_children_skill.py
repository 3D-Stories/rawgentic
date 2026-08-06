"""Drift guard for the revalidate-children skill's `--bodies` contract (#944 Task 14).

`rebuild-receipt --bodies` is the ACTUAL production enforcement point for claim-inventory
coverage (design doc `2026-08-06-944-revalidate-hardening-design.md` §1.5) — the constructor
alone has zero production callers. A skill that only DESCRIBES the flag, without a real worked
example of the JSON shape it takes, leaves an agent to reverse-engineer the contract from the
Python (the exact failure mode #944's own design doc names for the `--audited` flag before this
issue fixed it). This guard fails loudly if a future edit drops the worked example back to prose
only, or stops naming when `--bodies` is required.
"""
from tests.corpus import skill_corpus

CORPUS = skill_corpus("revalidate-children")


def test_documents_when_bodies_is_required():
    assert "--bodies` is required whenever any `--audited` entry is NOT `pending_disposition`" \
        in CORPUS


def test_the_rebuild_receipt_example_command_carries_both_flags_together():
    assert "--audited audited.json --bodies bodies.json" in CORPUS


def test_a_real_worked_bodies_json_example_exists_not_only_a_description():
    """A real issue number and a real body string, not the bare placeholder shape
    `{"<issue>": "<body>"}` — #944 Task 12's own requirement was a FULLY WORKED example so the
    step is executable, not merely described."""
    assert '{"612": "## Problem' in CORPUS


def test_bodies_carries_only_the_raw_body_no_resolves_field():
    """The security property design doc §1.5/§4 finding 3 fixed: `--bodies` must never carry a
    caller-suppliable `resolves`, or citation coverage could be fabricated."""
    assert "no `resolves` field, nothing for a caller to" in CORPUS
