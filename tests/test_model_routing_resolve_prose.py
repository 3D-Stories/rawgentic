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


def test_wf3_fix_bug_carries_build_seat_clause():
    # #762: WF3's bespoke block now dispatches fix-plan implementation work through
    # the build seat; whitespace-normalized single-file anchor.
    norm = _norm((REPO / "skills" / "fix-bug" / "SKILL.md").read_text(encoding="utf-8"))
    assert ("WF3 fix-plan tasks dispatch through the executor `build` seat: mint "
            "the gate from the WF3 fix plan, dispatch with `--gate-file` and "
            "`--plan-file`, then collect and land the work product audited.") in norm


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
    # exit taxonomy: 6 additive (EXIT_REFUSED), 3 availability (#733 widened the gloss to the
    # process-failure results — killed dispatches are exit 3 with the partial flagged)
    assert "`6` refused (`EXIT_REFUSED`" in norm
    assert ("`3` availability (chain exhausted / quota timeout / a timed-out, signalled, "
            "or otherwise process-failed dispatch") in norm


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


# --- #765: the Step-3 bake-off is WIRED (drift guard; supersedes the #735 carve-out pin) ---
# Anchors ONE canonical sentence in ONE file (the shared source) per the repo
# drift-guard pattern; the corpus check proves the sync shipped it.

_WIRED_TRUTH = ("full-spine step 3 design generation dispatches one competitive design round "
                "via `python3 hooks/bakeoff_policy.py design-round")
_GATE_TRUTH = ("gated by `python3 hooks/bakeoff_policy.py design-round-enabled --workspace "
               "<workspace-file> --project <name>` (exit 0 = opted in via `designbakeoff: "
               '{"enabled": true}` on the project\'s workspace entry; default off')
_CARVEOUT_DECIDED = "whether to wire or retire the bake-off is decided in #765"
_CARVEOUT_STALE = "proven in #472"


def test_step3_row_states_wired_truth():
    from corpus import skill_corpus
    shared = _norm(SHARED.read_text(encoding="utf-8")).lower()
    corpus = _norm(skill_corpus("implement-feature")).lower()
    assert _WIRED_TRUTH in shared, \
        "shared block Step-3 row must state the WIRED truth naming the design-round CLI (#765)"
    assert _WIRED_TRUTH in corpus, \
        "WF2 corpus must carry the synced wired truth (run sync_shared_blocks.py)"
    assert _GATE_TRUTH in shared, \
        "the Step-3 row must carry the designBakeoff opt-in gate, default OFF (#765 owner decision)"
    assert _GATE_TRUTH in corpus, \
        "the synced corpus must carry the designBakeoff gate (run sync_shared_blocks.py)"
    for stale, label in ((_CARVEOUT_DECIDED, "'decided in #765' pending text"),
                         (_CARVEOUT_STALE, "'#472 proves it' text"),
                         ("#472", "any #472 deferral pointer")):
        assert stale not in shared, f"stale {label} must be gone from the shared block (#765)"
        assert stale not in corpus, f"stale {label} must be gone from the corpus (#765)"


def test_run_records_reconciliation_states_wired_join_truth():
    """#765: the run-records reconciliation section must not defer to the CLOSED #472,
    and must state #762's shipped landing-audit join while retaining the uncalled,
    prospective build-bake-off follow-up (#779)."""
    text = (REPO / "docs" / "run-records.md").read_text(encoding="utf-8")
    start = text.index("### Audit-stream reconciliation")
    end = text.index("### Capture", start)
    section = _norm(text[start:end])
    assert "#472" not in section, \
        "run-records reconciliation must not defer to the closed #472 (#765)"
    assert "landed_work_product" in section
    assert "receipt_nonce" in section
    assert "pre_cutover_unverifiable" in section
    assert "#779" in section, \
        "the uncalled build bake-off follow-up must point at #779, not #762"
    assert "no current reconciliation join is performed on `correlation_id`" in section


# --- #735: Step-8 executor-primary sentence + legacy-conditioned delegation --

_STEP8_EXECUTOR_PRIMARY = (
    "full receipts-asserted adoption across every wf2/wf3 run is #762's acceptance surface"
)
_STEP8_OLD_UNSCOPED_INLINE = (
    "when the `implementation` role is `inherit` (default), step 8 runs inline exactly as today"
)


def _step8_section() -> str:
    steps = REPO / "skills" / "implement-feature" / "references" / "steps.md"
    text = steps.read_text(encoding="utf-8")
    # Line-anchored heading boundaries (Step-11 R1-F2: a bare .index() could
    # match a "## Step 8/9" substring inside prose).
    start = text.index("\n## Step 8: Implementation")
    end = text.index("\n## Step 9:", start)
    return _norm(text[start:end]).lower()


def test_step8_executor_primary_sentence():
    s8 = _step8_section()
    assert _STEP8_EXECUTOR_PRIMARY in s8, \
        "steps.md §8 must carry the executor-primary sentence (#735 AC2)"
    assert "legacy architecture only" in s8, \
        "steps.md §8 delegation block must be legacy-conditioned (#735 AC2)"
    assert _STEP8_OLD_UNSCOPED_INLINE not in s8, \
        "the unscoped 'inherit → Step 8 runs inline' sentence must be legacy-scoped (#735 AC2)"


def test_step8_legacy_conditioning_reaches_every_delegation_directive():
    """Step-11 findings R1-F1/R2-F1 (#735): the conditioning must cover the whole
    delegation MECHANISM — the clean-state retry and never-blocks items by name,
    the same-model inline paragraph, and the executor branch's own no-inline +
    proven-death retry rules."""
    s8 = _step8_section()
    assert "including the clean-state restore-and-retry (item 4) and the never-blocks rule (item 5)" in s8, \
        "items 4/5 of the delegation procedure must be named as legacy-only (#735 R2-F1)"
    assert ("under the legacy architecture, when the resolved `implementation` model "
            "equals the session/orchestrator model") in s8, \
        "the same-model inline paragraph must be legacy-scoped (#735 R1-F1)"
    assert "inline execution is not a sanctioned implementation path under the executor architecture" in s8, \
        "the executor branch must reject inline implementation (#735 R1-F1)"
    assert "retry once only on proven death" in s8, \
        "the executor branch must carry the ratified proven-death retry rule (#735 R2-F1)"


def test_review_seat_row_names_no_review_fast_seat():
    """Step-11 R2-F4 (#735): review_fast is a lens/model tier, not a wired seat —
    no dispatch prose may instruct dispatching it as a seat."""
    from corpus import skill_corpus
    shared = _norm(SHARED.read_text(encoding="utf-8"))
    assert "`review` / `review_fast`" not in shared, \
        "the seat-mapping review row must not present review_fast as a seat"
    assert "NOT a wired seat" in shared, \
        "the review row must state review_fast is a lens/model tier, not a seat"
    for skill in ("implement-feature", "fix-bug"):
        corpus = _norm(skill_corpus(skill))
        assert "`review`/`review_fast` seats" not in corpus, \
            f"{skill}: dispatch prose must not name review_fast as a seat"
    assert "--seat <review|review_fast>" not in _norm(skill_corpus("fix-bug")), \
        "fix-bug: the dispatch CLI template must use --seat review"


# --- #762 Step-11 r1-2/r2-3: retune-pinned prose tracks the EXECUTABLE policy ---------------

_MODEL_SHORT = {"gpt-5.6-sol": "sol", "claude-fable-5": "fable", "claude-opus-5": "opus",
                "claude-sonnet-5": "sonnet", "gpt-5.6-terra": "terra"}


def _short(model_id: str) -> str:
    assert model_id in _MODEL_SHORT, (
        f"model id {model_id!r} has no short prose name — extend _MODEL_SHORT so the "
        f"retune-prose guards keep tracking the executable policy")
    return _MODEL_SHORT[model_id]


def _design_pairing() -> str:
    sys.path.insert(0, str(REPO / "hooks"))
    import bakeoff_policy  # noqa: E402  pylint: disable=import-outside-toplevel
    a, b = bakeoff_policy.DESIGN_MODELS
    return f"{_short(a)} vs {_short(b)}"


def _review_chain() -> str:
    import json  # pylint: disable=import-outside-toplevel
    table = json.loads((REPO / "phase_executor" / "src" / "phase_executor" / "routing"
                        / "rawgentic.routing-table.json").read_text(encoding="utf-8"))
    seat = table["seats"]["review"]
    order = [seat["primary"]["model"]] + [c["model"] for c in seat["chain"]
                                          if c["model"] != seat["primary"]["model"]]
    return " → ".join(_short(m) for m in order)


def test_design_pairing_prose_tracks_design_models():
    # A future retune of DESIGN_MODELS fails these until the prose moves with it (r1-2/r2-3).
    pairing = _design_pairing()
    for surface in (SHARED, REPO / "skills" / "implement-feature" / "SKILL.md"):
        text = _norm(surface.read_text(encoding="utf-8"))
        assert f"({pairing} concurrent, glm-5.2 judge)" in text, (
            f"{surface.name}: the design-round pairing prose must state the executable "
            f"DESIGN_MODELS pairing {pairing!r}")


def test_design_pairing_current_diagram_rev_tracks_design_models():
    # Only the CURRENT (non-superseded) diagram rev must track the live pairing; historical
    # revs legitimately keep the models of their day.
    pairing = _design_pairing()
    html = (REPO / "docs" / "workflow-diagram.html").read_text(encoding="utf-8")
    m = re.search(r'"([0-9.]+)": \{ superseded:false', html)
    assert m, "no non-superseded wf2 rev found in the diagram data"
    start = m.start()
    nxt = re.search(r'"[0-9.]+": \{ superseded:"', html[start:])
    block = html[start:start + nxt.start()] if nxt else html[start:]
    assert f"· {pairing} ·" in block, (
        f"the current diagram rev {m.group(1)} must state the executable pairing {pairing!r}")
    others = set(re.findall(r"· (\w+ vs \w+) ·", block)) - {pairing}
    assert not others, f"current diagram rev carries a non-executable pairing: {others}"


def test_review_chain_prose_tracks_routing_table():
    chain = _review_chain()
    for surface, needle in (
            (SHARED, f"the `review` chain {chain}"),
            (REPO / "skills" / "implement-feature" / "SKILL.md", f"the `review` chain {chain}"),
            (REPO / "skills" / "fix-bug" / "SKILL.md", f"the routing table, {chain}")):
        text = _norm(surface.read_text(encoding="utf-8"))
        assert needle in text, (
            f"{surface}: the review-chain prose must state the executable order {chain!r}")
