"""Drift guards for docs/multi-issue-driver.md — the multi-issue driver pattern.

The driver is a *documented orchestration pattern* (design #134), so its
acceptance criteria (#148, #163) are largely prose. These guards pin the
canonical anchors an AC keys on, so a future edit can't silently drop the DEFER
taxonomy, the branch-preservation rule, the reconciliation contract, the
dependency-DAG section, or the WF2-non-weakening guarantee. Substring checks
(not counts) — robust to surrounding edits.
"""
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
DOC = REPO / "docs" / "multi-issue-driver.md"


def _doc() -> str:
    assert DOC.exists(), "docs/multi-issue-driver.md is missing"
    return DOC.read_text()


def test_doc_exists_and_titled():
    text = _doc()
    assert "# Multi-issue driver" in text


def test_doc_covers_the_loop_and_policies_148_ac1():
    text = _doc().lower()
    assert "/rawgentic:implement-feature" in text  # fresh per issue
    assert "review budget" in text or "review_budget" in text
    assert "never-haiku" in text or "never_haiku" in text
    # advance on merge, or pr_open when headless
    assert "advance" in text
    assert "pr_open" in text


def test_doc_defer_taxonomy_148_ac3():
    text = _doc()
    for typ in ("owner-decision", "owner-reserved", "cross-repo", "budget"):
        assert typ in text, f"DEFER type {typ!r} missing"
    # #163 AC4 adds the dependency defer reason
    assert "cross-issue-dependency" in text


def test_doc_branch_preservation_rule_148_ac3():
    text = _doc()
    assert "branch_preservation" in text
    for v in ("pushed", "discarded", "none"):
        assert v in text, f"branch_preservation value {v!r} missing"
    assert "deferred_branch" in text


def test_doc_status_machine_and_rollback_anchor_148_ac2():
    text = _doc()
    for st in ("queued", "in_progress", "pr_open", "merged", "deferred", "abandoned"):
        assert st in text, f"status {st!r} missing"
    assert "rollback_anchor" in text
    assert "base_default_branch_sha" in text


def test_doc_resumption_contract_148_ac4():
    text = _doc()
    assert "resume_lib" in text  # intra-WF2 resume delegated, not re-implemented
    lowered = text.lower()
    assert "reconcil" in lowered  # reconciliation table/contract


def test_doc_does_not_weaken_wf2_148_ac5():
    text = _doc()
    lowered = text.lower()
    assert "step 16" in lowered  # each iteration is a full WF2 run terminating at Step 16
    assert "weaken" in lowered   # explicit non-weakening statement


def test_doc_dependency_dag_163_ac1_ac2_ac3():
    text = _doc()
    assert "depends_on" in text
    assert "parse_depends_on" in text
    assert "topo_sort_issues" in text
    assert "next_ready_issue" in text
    assert "deps_satisfied_by" in text
    lowered = text.lower()
    assert "topolog" in lowered or "topo-sort" in lowered or "topo sort" in lowered
    assert "fail-closed" in lowered or "fail closed" in lowered
    assert "cycle" in lowered


def test_doc_epic_anchor_163_ac5():
    text = _doc()
    lowered = text.lower()
    assert "epic" in lowered
    assert "one-way" in lowered or "one way" in lowered  # checkbox mirror direction
    # headless refuses to start without an epic
    assert "refuse" in lowered or "refuses" in lowered


def test_doc_rate_limit_budget_defer_163_ac6():
    text = _doc().lower()
    assert "rate-limit" in text or "rate limit" in text


def test_doc_v1_readable_163_ac7():
    text = _doc()
    assert "validate_driver_state" in text
    lowered = text.lower()
    assert "v1" in lowered
    assert "schema_version" in text


def test_doc_references_committed_schema_and_state_location():
    text = _doc()
    # git-tracked contract
    assert "docs/driver-state/queue.schema.json" in text
    # live runtime state (disk-persisted, gitignored) — the honest "committed" note
    assert "claude_docs/.driver-state/" in text


def test_doc_defines_the_ledger():
    text = _doc()
    assert "### The ledger" in text
    assert "notes" in text
    assert "state file" in text


def test_doc_warns_against_parsing_epic_body():
    text = _doc()
    assert "Never run `parse_depends_on` on the epic body" in text


def test_doc_documents_fresh_session_per_child():
    # #569: the driver doc carries the fresh-session cross-session lifecycle contract.
    text = _doc() if "_doc" in dir() else __import__("pathlib").Path(
        __file__).resolve().parents[2].joinpath("docs/multi-issue-driver.md").read_text()
    assert "Fresh session per child" in text
    assert "session_mode" in text and "fresh-session" in text
    assert "skip the `--resume` attempt" in text or "MUST skip the `--resume`" in text
    assert "handoff_claim" in text and "fail-open" in text.lower()
