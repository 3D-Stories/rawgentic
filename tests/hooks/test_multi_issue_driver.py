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


def test_doc_documents_queue_revalidation_840():
    # #840: the driver doc carries the revalidation state shape AND the fact that PR 1 is
    # inert. Anchored to one canonical sentence per claim rather than to counts, per the
    # repo's drift-guard convention (§4 mistake 6).
    text = _doc()
    assert "## Queue revalidation (#840)" in text
    assert "validated_against" in text and "queue_revalidation" in text
    # The owner ruling is the load-bearing prose — a future edit that reinstates auto-clear
    # must trip here, because the whole gate collapses to theatre without it.
    assert "The intersection sets DEPTH, never whether to look" in text
    assert "Nothing is auto-cleared." in text
    # PR 2 INVERTS this pair. PR 1's pins required the doc to say the machinery was inert and that
    # `QueueRevalidationRequired` is never raised; both are now false, so keeping them passing
    # would make the doc lie in the one place a reader checks whether the guard is real. The
    # replacement pins the live contract, and specifically the three facts a reader could get
    # wrong: that selection RAISES rather than returning None, that the head must be freshly
    # observed, and that only one skill clears the gate.
    assert "### The gate is LIVE (#840 PR 2)" in text
    assert "It **raises** rather than returning `None`" in text
    assert "must be FRESHLY OBSERVED" in text
    # INVERTED at round-5 finding 4. This pinned the sentence "**What clears it:**
    # `/rawgentic:revalidate-children`, and nothing else." — which is FALSE for a pending
    # disposition: re-running the skill rediscovers the same marker, and only the owner's
    # `record-child-outcome --status deferred|abandoned` clears it. A drift guard that pins a
    # false sentence actively defends the error, which is worse than having no guard.
    assert "**What clears it depends on WHY it refused**" in text
    # INVERTED with the owner-gate cut (#848). The doc used to pin an owner-only remedy for a
    # `pending_disposition`; that clause is gone, so the doc must now say so rather than
    # keep prescribing a remedy for a refusal the gate can no longer make.
    assert "That clause was CUT (#848)" in text, "the driver doc must record the cut"
    assert "record-child-outcome" in text, "the command is still named for when #848 lands"
    assert "and nothing else" in text, "the stale-provenance half is still absolute"
    # The review round INVERTED this pin. It previously required the doc to state the
    # per-campaign activation limit; the Step-11 review called that limit opt-in theatre and the
    # owner closed it, so the doc must now state that the gate is universal AND name the migration
    # consequence, which is the thing an operator will actually hit.
    assert "The gate is UNIVERSAL" in text
    assert "refuses until\n`/rawgentic:revalidate-children` has run against it once" in text
    # The in-session loop's gated selection path must be documented, or the default mode silently
    # keeps selecting unguarded (Step-11 finding 2).
    assert "`launcher_lib next-child`" in text
    # An obsolete child must not read as a stampable outcome.
    assert "`issue_obsolete` is not an `outcome`" in text
    # Corrections are annotations, not rewrites.
    assert "Corrections are COMMENTS, never body edits" in text


def _section(text: str, header: str) -> str:
    """The body under ``header``, up to the next heading of the same or higher level.

    Header-index slicing per the repo's drift-guard convention (§4 mistake 6): a whole-document
    substring check cannot tell an INSTRUCTION to call the bypass from a mention of it.
    """
    start = text.index(header) + len(header)
    depth = len(header) - len(header.lstrip("#"))
    rest = text[start:]
    ends = [rest.index(f"\n{'#' * lvl} ") for lvl in range(1, depth + 1)
            if f"\n{'#' * lvl} " in rest]
    return rest[:min(ends)] if ends else rest


def test_the_selection_sections_route_through_next_child_not_the_pure_function():
    """#840 Step-11 round 3, High 2. The doc grew a correct `next-child` section while its OWN
    primary loop and advance rule still told operators to call `next_ready_issue(state,
    deps_satisfied_by)` directly. That call observes no head, so it bypasses the gate entirely on
    a receipt-less campaign and RAISES on an armed one — an operator following the main loop
    selected a child without ever fetching `origin/main`.

    Mutation-sensitive on purpose: the two sections that carried the bypass are sliced out by
    header and checked individually. A whole-document check would stay green on exactly the
    defect that shipped, because the corrective section elsewhere already names `next-child`.
    """
    text = _doc()
    for header in ("## The loop", "## Dependency ordering (schema v2)"):
        body = _section(text, header)
        assert "next-child" in body, (
            f"{header} must route selection through `launcher_lib.py next-child`")
        if "next_ready_issue" in body:
            assert "never" in body.lower() and "directly" in body.lower(), (
                f"{header} names the pure selector without saying not to call it directly — "
                "that is the round-3 High 2 bypass verbatim")


def test_the_next_child_exit_contract_documents_rc_2():
    """Round-3 finding 6. rc 2 covers three situations, one of which is a SUCCESSFUL selection
    missing only `project`. Neither driver document nor the CLI help mentioned it, so an
    automated caller could only guess — and guessing 'error' stops a campaign that is fine."""
    text = _doc()
    body = _section(text, "### The gate is LIVE (#840 PR 2)")
    assert "| 2 |" in body, "rc 2 is missing from the next-child exit-code table"
    assert "parse stdout" in body.lower(), "rc 2 is useless to a caller without this instruction"
    assert "`next_issue`" in body


def test_doc_documents_the_probed_transport_and_the_closed_fence():
    """#927. Two contracts this doc must not drift from, both of which it once stated INVERTED.

    The doc said the boundary was opt-in and that it had no exactly-one-successor fence. Both were
    true when written and both are now false, so shipping the code without rewriting them would
    have left the authoritative contract document telling an operator to expect a double launch.
    """
    text = __import__("pathlib").Path(__file__).resolve().parents[2].joinpath(
        "docs/multi-issue-driver.md").read_text(encoding="utf-8")
    assert "preferred_transport" in text and "transport resolve-creation" in text
    assert "PROBED at campaign creation" in text, "the answer is probed, never asked at setup"
    assert "write-only compatibility projection" in text, (
        "`session_mode` survives only as a projection; the canonical field wins")
    assert "fence is HERE now" in text
    assert "rc 7" in text, "the losing contender's distinct exit code belongs in the contract"
    assert "no generation counter and no exactly-one-successor fence at the child boundary" \
        not in text, "the pre-#927 absence claim must be gone"


# --------------------------------------------------------------------------- #
# #769 — the boundary-sweep schema declaration
# --------------------------------------------------------------------------- #
def _queue_schema():
    import json as _json
    from pathlib import Path as _Path
    root = _Path(__file__).resolve().parent.parent.parent
    return _json.loads((root / "docs" / "driver-state" / "queue.schema.json").read_text())


def test_boundary_sweeps_is_declared_in_the_committed_schema():
    """The live state file is gitignored, so the schema IS the contract-of-record."""
    props = _queue_schema()["properties"]
    assert "boundary_sweeps" in props, "the sweep record must be part of the tracked contract"
    assert props["boundary_sweeps"]["type"] == "array"


def test_boundary_sweeps_did_not_bump_the_schema_version():
    """Additive top-level field; the precedent is campaign_wait / advisory_deliveries /
    transport_audit / transitions, none of which bumped it either."""
    assert _queue_schema()["properties"]["schema_version"]["enum"] == [1, 2]


def test_the_declared_sweep_outcomes_match_the_code():
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent / "hooks"))
    import driver_lib as _dl
    declared = _queue_schema()["properties"]["boundary_sweeps"]["items"]["properties"][
        "assessments"]["items"]["properties"]["outcome"]["enum"]
    assert set(declared) == set(_dl.SWEEP_OUTCOMES)
    assert "blocked" not in declared, "deleted at the Step-4 gate; nothing consumed it"


def test_the_doc_carries_the_boundary_sweep_contract():
    """#769 AC 2. The doc is the durable contract; the skill is the procedure. Both, or a
    fresh-session successor reading only the doc never learns the step exists."""
    from pathlib import Path as _Path
    root = _Path(__file__).resolve().parent.parent.parent
    text = " ".join((root / "docs" / "multi-issue-driver.md").read_text().split())
    assert "After every merged, deferred, or abandoned child" in text
    assert "without a completion" in text
    assert "before selecting or handing off the next child" in text
    assert "boundary_sweeps" in text
    assert "D181" in text


def test_the_doc_states_the_gate_checks_coverage_not_judgment():
    from pathlib import Path as _Path
    root = _Path(__file__).resolve().parent.parent.parent
    text = " ".join((root / "docs" / "multi-issue-driver.md").read_text().split()).lower()
    assert "coverage and record integrity" in text
