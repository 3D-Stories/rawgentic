"""M0b (#866) drift guard: the post-retreat `<model-routing-resolve>` contract — inline
work (D174), the ONE review-runner entry point (D179), the pinned reviewer default, the
reopen-token choke point (#855), and the vacuous-result gate (#766) — is present in the
single-source shared block AND ships into the WF2 corpus; WF3's bespoke block carries the
same load-bearing clauses. Anchors each canonical sentence to ONE file per the repo
drift-guard pattern (mistake #6)."""
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SHARED = REPO / "shared" / "blocks" / "model-routing-resolve.md"
WF3_SKILL = REPO / "skills" / "fix-bug" / "SKILL.md"
sys.path.insert(0, str(REPO / "tests"))


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def _shared() -> str:
    return _norm(SHARED.read_text(encoding="utf-8"))


def _wf3_block() -> str:
    text = WF3_SKILL.read_text(encoding="utf-8")
    start = text.index("<model-routing-resolve>")
    end = text.index("</model-routing-resolve>", start)
    return _norm(text[start:end])


# --- D174: inline work, subagents-by-breadth ---------------------------------

def test_inline_contract_in_shared_source():
    shared = _shared()
    assert "Analysis and implementation run INLINE in the orchestrating session." in shared
    assert "What is retired is the executor seat, not subagents." in shared
    assert "the default is inline TDD" in shared


def test_inline_contract_shipped_into_wf2_corpus():
    from corpus import skill_corpus
    corpus = _norm(skill_corpus("implement-feature"))
    assert "Analysis and implementation run INLINE in the orchestrating session." in corpus, \
        "implement-feature corpus must carry the synced D174 contract (run sync_shared_blocks.py)"
    assert "What is retired is the executor seat, not subagents." in corpus


# --- D179: ONE runner entry point, three verbs -------------------------------

def test_runner_single_entry_point_in_shared_source():
    shared = _shared()
    assert "Cross-model review runs through ONE entry point" in shared
    assert "hooks/review_runner.py" in shared
    for verb in ("review-artifact --artifact", "review-code --base", "consult --artifact"):
        assert f"python3 hooks/review_runner.py {verb}" in shared, verb
    assert "dispatched from a read-only harness subagent" in shared
    assert ("it must not modify project files — its only permitted write is the runner's "
            "declared `--out` result file") in shared


def test_runner_exit_codes_and_transport_policy_in_shared_source():
    shared = _shared()
    assert "`2` refused" in shared
    assert "`3` terminal backend failure" in shared
    assert "`4` empty/invalid backend output" in shared
    assert "callers NEVER add their own retry loop around it" in shared


def test_wf3_block_carries_runner_contract():
    block = _wf3_block()
    assert "hooks/review_runner.py" in block
    assert "python3 hooks/review_runner.py review-code --base" in block
    assert "read-only harness subagent" in block


# --- pinned reviewer identity: the default is single-sourced -----------------

def _shared_reviewer_default() -> str:
    m = re.search(r"current default reviewer id is\s*\*\*`([^`]+)`\*\*", _shared())
    assert m, "the shared block must state the current default reviewer id"
    return m.group(1)


def test_reviewer_default_pinned_in_shared_source():
    default = _shared_reviewer_default()
    assert default == "gpt-5.6-sol"
    shared = _shared()
    assert "single-sourced HERE" in shared
    assert "REFUSES author==reviewer" in shared


def test_wf3_reviewer_default_matches_shared_source():
    # WF3's bespoke block restates the id but defers authority to the shared block —
    # the two copies may never diverge.
    default = _shared_reviewer_default()
    block = _wf3_block()
    assert f"`{default}`" in block, \
        "fix-bug's bespoke block must state the SAME default reviewer id as the shared block"
    assert "single-sourced in `shared/blocks/model-routing-resolve.md`" in block


# --- #855: the reopen-token choke point --------------------------------------

def test_reopen_choke_point_in_shared_source():
    shared = _shared()
    assert "python3 hooks/plan_lib.py review-reopen --state-file" in shared
    assert "The mint itself debits the atomic loop-back budget" in shared
    assert "`diagnostic: true`" in shared
    assert "MUST refuse to open a fix round on a diagnostic result" in shared
    assert "Transport retries inside one runner invocation never re-debit" in shared


def test_wf3_block_carries_reopen_choke_point():
    block = _wf3_block()
    assert "review-reopen" in block
    assert "the mint debits the loop-back budget" in block
    assert "`diagnostic: true`" in block


# --- #766: the vacuous-result gate --------------------------------------------

VACUOUS = ('A dead subagent, an empty file, or a missing status is a FAILED dispatch — '
           'never a pass, never "still running" (#766).')


def test_vacuous_result_gate_in_shared_source():
    shared = _shared()
    assert VACUOUS in shared
    assert "head_sha" in shared and "input_sha256" in shared, \
        "the freshness clause must name the result's binding fields"
    assert "≤ 3 concurrent Claude subagents" in shared


def test_wf3_block_carries_vacuous_result_gate():
    block = _wf3_block()
    assert "FAILED dispatch" in block
    assert "#766" in block


# --- gate preservation: the shared source can never drop the sentence silently ---
# (the corpus-level pin lives in tests/test_gate_preservation.py)

def test_gate_preservation_sentence_in_shared_source():
    assert ("A subagent or runner dispatch is never a gate bypass — every mandatory review "
            "gate runs with identical semantics whether a pass ran inline or through "
            "`hooks/review_runner.py`, and a review that may open a fix round carries a "
            "reopen token minted first.") in _shared()


# --- the retreat holds: no executor vocabulary re-enters the shared source ----

def test_no_executor_vocabulary_in_shared_source():
    shared = _shared()
    for needle in ("executor_routing_lib", "begin-run", "mint-gate", "--seat",  # tripwire-exempt: negative guard
                   "rawgentic:rawgentic-implementer", "rawgentic:rawgentic-reviewer",  # tripwire-exempt: negative guard
                   "model_routing_lib"):
        assert needle not in shared, f"executor vocabulary {needle!r} re-entered the shared block"
