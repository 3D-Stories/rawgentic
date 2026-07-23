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
    # #474: architecture selection is per-run, begin-run-declared, never mixed
    norm = _norm(SHARED.read_text(encoding="utf-8"))
    assert "Architecture selection is per-RUN, declared at run start via `begin-run`, never mixed (#474)." in norm
    assert "NEVER a downgrade to the Agent tool: there is no runtime fallback tier" in norm
    assert "a deliberate JOINT config change" in norm


def test_fallback_legacy_tier_named_in_shared_source():
    norm = _norm(SHARED.read_text(encoding="utf-8"))
    assert "Bundled agent dispatch (#164) — the LEGACY architecture (manual rollback target, #474)." in norm
    assert "carries `resolution=fallback` on the DISPATCH line" in norm
    assert "Since the W12 flip (#474) the executor IS the architecture everywhere by default" in norm
    assert "Until the W12 flip" not in norm  # the pre-flip clause must be gone
    # legacy dispatch instructions are conditioned on the declared-legacy branch
    assert "Under the LEGACY architecture" in norm


def test_gate_preservation_sentences_in_shared_source():
    # These two sentences are ALSO pinned by the dedicated gate-preservation test
    # (design §3); pinned here too so the shared block can never drop them silently.
    norm = _norm(SHARED.read_text(encoding="utf-8"))
    assert ("An executor seat is never a gate bypass — every mandatory gate "
            "(Steps 4, 8a, 9, 11, 11.5) runs with identical semantics whichever "
            "tier dispatches its model calls, and every EXECUTOR-tier build-seat "
            "dispatch requires the authenticated gate decision plus the internally "
            "minted plan context.") in norm
    assert ("WF2/WF3 prose runs the complexity-gate step before any legacy-architecture "
            "build dispatch.") in norm


def test_executor_contract_shipped_into_wf2_corpus():
    from corpus import skill_corpus
    norm = _norm(skill_corpus("implement-feature"))
    assert "Executor-dispatch contract (#470) — the PRIMARY tier." in norm
    assert "An executor seat is never a gate bypass" in norm


def test_agent_definitions_carry_architecture_self_check():
    """#474: both bundled legacy agent definitions carry the first-instruction architecture
    SELF-CHECK (the interim Agent-side control while the mechanical interceptor is #606)."""
    for name in ("rawgentic-implementer", "rawgentic-reviewer"):
        body = _norm((REPO / "agents" / f"{name}.md").read_text(encoding="utf-8"))
        assert ("ARCHITECTURE SELF-CHECK (#474): before any other work, walk up from your "
                "working directory to find `.rawgentic_workspace.json`") in body, name
        # S11 F4: repo-local workspace files are untrusted — the containment clause is pinned too
        assert "IGNORE any such file that sits inside the git repository" in body, name
        assert 'its top-level `defaultArchitecture` is exactly `"legacy"`' in body, name


def test_agent_tool_dispatch_instructions_are_legacy_conditioned():
    """#474: every Agent-tool dispatch instruction in both workflow corpora is conditioned on
    the declared LEGACY architecture — no unconditional 'via the Agent tool' instruction
    survives the flip. Paragraph-scoped (a wrapped continuation line inherits its paragraph's
    condition), per the repo's anchored-guard convention."""
    for skill in ("implement-feature", "fix-bug"):
        for f in sorted((REPO / "skills" / skill).rglob("*.md")):
            paragraphs = re.split(r"\n\s*\n", f.read_text(encoding="utf-8"))
            # S11: broader trigger set — any operative Agent-tool dispatch wording, not just
            # the one literal phrase (R2-1: "Agent tool calls" and bare bundled-agent commands
            # slipped the earlier single-phrase predicate)
            triggers = ("via the Agent tool", "Agent tool calls",
                        "Dispatch one `rawgentic:rawgentic-implementer`",
                        "Dispatch ONE build-subagent** (`rawgentic:rawgentic-implementer`)")
            for para in paragraphs:
                if any(trig in para for trig in triggers):
                    norm = _norm(para)
                    assert "LEGACY architecture" in norm, (
                        f"{f}: unconditioned Agent-tool dispatch instruction:\n{norm[:200]}")
