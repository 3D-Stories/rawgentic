"""#417 drift guard: the routing-resolve prose (seat fallback chain + circuit breaker, the ≤3-Claude
concurrency ceiling, the driver-seat guidance) is present in the single-source shared block AND ships
into the WF2 corpus. Anchors the ONE canonical fallback-contract sentence to ONE file (the shared
source), per the repo drift-guard pattern."""
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SHARED = REPO / "shared" / "blocks" / "model-routing-resolve.md"
sys.path.insert(0, str(REPO / "tests"))


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text)


# The ONE canonical, drift-guardable fallback-contract sentence (shared source = single source).
CANONICAL = "a chain that exhausts its eligible entries is a handled hard failure, never a silent downgrade"


def test_canonical_fallback_sentence_in_shared_source():
    norm = _norm(SHARED.read_text(encoding="utf-8")).lower()
    assert CANONICAL in norm, "shared/blocks/model-routing-resolve.md must carry the canonical fallback sentence"


def test_concurrency_and_driver_prose_in_shared_source():
    norm = _norm(SHARED.read_text(encoding="utf-8"))
    assert "≤ 3 concurrent Claude subagents" in norm
    assert "effective working ceiling of 2" in norm
    assert "strong-model-on-top reliability floor" in norm
    assert "GUIDANCE only" in norm or "guidance, not enforcement" in norm.lower() or "guidance only" in norm.lower()


def test_prose_shipped_into_wf2_corpus():
    # the sync must have propagated the shared block into implement-feature's SKILL.md corpus
    from corpus import skill_corpus
    norm = _norm(skill_corpus("implement-feature")).lower()
    assert CANONICAL in norm, "implement-feature corpus must carry the synced canonical fallback sentence"


def test_wf3_fix_bug_carries_concurrency_and_fallback():
    # fix-bug's bespoke WF3 block (edited directly, not synced) gets the corresponding note
    norm = _norm((REPO / "skills" / "fix-bug" / "SKILL.md").read_text(encoding="utf-8"))
    assert "effective working ceiling of 2" in norm
    assert "handled hard failure, never a silent downgrade" in norm


# --- #470: the executor-dispatch contract replaced the Agent-tool-only prose. ---
# New canonical sentences in the single-source shared block. Anchored to the
# shared source (single source of truth), whitespace-normalized per the repo
# convention. The Agent-tool path survives, demoted to the FALLBACK (legacy) tier.


def test_executor_dispatch_contract_present_in_shared_source():
    norm = _norm(SHARED.read_text(encoding="utf-8"))
    # single entry point, --plan-file (never --plan-context)
    assert "Executor-dispatch contract (#470) — the PRIMARY tier." in norm
    assert "python3 hooks/executor_routing_lib.py dispatch" in norm
    assert "the input is `--plan-file`, NEVER `--plan-context`" in norm
    # exit taxonomy: 6 additive (EXIT_REFUSED), 3 availability
    assert "`6` refused (`EXIT_REFUSED`" in norm
    assert "`3` availability (chain exhausted / quota timeout)" in norm


def test_mutating_engine_allowlist_fact_in_shared_source():
    norm = _norm(SHARED.read_text(encoding="utf-8"))
    assert "Mutating dispatch is codex-only until an FS-sandbox child ships" in norm
    assert "`MUTATING_FS_SANDBOXED` allowlist in `hooks/executor_routing_lib.py`" in norm
    assert "a mutating-claude composition — is refused at the supervised STEP 0 with exit 6" in norm


def test_per_run_tier_selection_in_shared_source():
    norm = _norm(SHARED.read_text(encoding="utf-8"))
    assert "Tier selection is per-RUN, at run start, never mixed." in norm
    # tier-switch terminates the run and starts a new linked run_id — never a silent mid-run downgrade
    assert "never an automatic, silent per-dispatch downgrade to the Agent tool" in norm
    assert "TERMINATES the current run" in norm and "starts a NEW run_id on the other tier" in norm


def test_fallback_legacy_tier_named_in_shared_source():
    norm = _norm(SHARED.read_text(encoding="utf-8"))
    assert "Bundled agent dispatch (#164) — the FALLBACK (legacy) tier." in norm
    assert "carries `resolution=fallback` on the DISPATCH line" in norm
    assert "Until the W12 flip (#474) this tier remains a working, declared fallback" in norm


def test_gate_preservation_sentences_in_shared_source():
    # These two sentences are ALSO pinned by the dedicated gate-preservation test
    # (design §3); pinned here too so the shared block can never drop them silently.
    norm = _norm(SHARED.read_text(encoding="utf-8"))
    assert ("An executor seat is never a gate bypass — every mandatory gate "
            "(Steps 4, 8a, 9, 11, 11.5) runs with identical semantics whichever "
            "tier dispatches its model calls, and every EXECUTOR-tier build-seat "
            "dispatch requires the authenticated gate decision plus the internally "
            "minted plan context.") in norm
    assert ("WF2/WF3 prose runs the complexity-gate step before any fallback-tier "
            "build dispatch.") in norm


def test_executor_contract_shipped_into_wf2_corpus():
    from corpus import skill_corpus
    norm = _norm(skill_corpus("implement-feature"))
    assert "Executor-dispatch contract (#470) — the PRIMARY tier." in norm
    assert "An executor seat is never a gate bypass" in norm
