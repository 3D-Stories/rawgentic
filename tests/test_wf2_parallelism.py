"""Drift guards for WF2 latency optimizations (parallel design-phase gates).

These assert that the parallelism instructions added to implement-feature's
Step 2 (parallel analysis fan-out) and Step 4 (adversarial review running
concurrently with the reflexion judges) stay present, so a later edit can't
silently re-serialize them. Companion to test_skill_helpers.py and
tests/hooks/test_adversarial_review_registration.py.

Issue: WF2 phase-latency optimization (A = Step 2 fan-out, B = Step 4
concurrent adversarial review).
"""
import re

from tests.corpus import skill_corpus


def _text() -> str:
    # Corpus, not SKILL.md alone — #158 may move these instructions into
    # references/; the parallelism pins follow the prose.
    return skill_corpus("implement-feature")


def _section(text: str, header: str, next_header: str) -> str:
    start = text.index(header)
    end = text.index(next_header, start)
    return text[start:end]


def test_step2_fans_out_independent_analyses():
    """Step 2's independent, read-only analyses are the dominant wall-clock
    cost; they must be dispatched as concurrent subagents, with the
    authoritative complexity classification described as a synthesis step
    after the gather barrier (not interleaved with the reads)."""
    step2 = _section(_text(), "## Step 2:", "## Step 3:")
    assert re.search(r"concurrent|in parallel|fan.?out", step2, re.I), (
        "Step 2 must describe parallel/concurrent dispatch of its analyses"
    )
    assert re.search(r"subagent|Agent tool", step2), (
        "Step 2 must dispatch the independent analyses via subagents (Agent tool)"
    )
    assert re.search(r"synthesi", step2, re.I), (
        "Step 2 must run complexity classification as a synthesis step after the gather"
    )
    # Data-dependency invariant: component mapping (item 1) is NOT independent of
    # the others (items 2 and 5 need the mapped artifact list), so it must run
    # first and be passed as shared input to the fan-out. Guards against the
    # "all 6 are independent" mistake.
    assert "must run first" in step2, (
        "Step 2 must run component mapping first (items 2/5 depend on the map)"
    )
    assert "shared input" in step2, (
        "Step 2 must pass the component map as shared input to the fanned-out analyses"
    )
    assert "gather barrier" in step2, (
        "Step 2 classification/fast-path must run after an explicit gather barrier"
    )
    # item 5 (test inventory) needs item 2's blast radius, so 2->5 is a chain
    # inside the fan-out, not a fully-independent parallel leaf.
    assert "sequential subagent" in step2, (
        "Step 2 must run items 2 -> 5 as a sequential subagent (test inventory needs blast radius)"
    )


def test_step4_adversarial_runs_concurrent_with_judges():
    """The cross-model adversarial review reviews the same design doc as the
    three reflexion judges, so it must run concurrently with them rather than
    serially after, while preserving the existing opt-in gate."""
    step4 = _section(_text(), "## Step 4:", "## Step 5:")
    adv = step4[step4.index("Adversarial review sub-step"):]
    assert re.search(r"concurrent|alongside|in parallel with", adv, re.I), (
        "Step 4 adversarial review must run concurrently with the judges"
    )
    assert "AFTER the critique above completes" not in adv, (
        "adversarial review should no longer be gated to run strictly after the judges"
    )
    # Single-breaker invariant: with the review concurrent, the ambiguity circuit
    # breaker must run EXACTLY ONCE over merged findings at a join barrier, with the
    # reflexion-only breaker (item 6) explicitly deferred. Guards against the
    # double-breaker / breaker-before-adversarial-arrives contradiction.
    assert "exactly once" in step4, (
        "Step 4 must apply the ambiguity circuit breaker exactly once over merged findings"
    )
    assert "join barrier" in step4, (
        "Step 4 must join judges + adversarial results at an explicit barrier before the breaker"
    )
    assert re.search(r"defer", step4, re.I), (
        "Step 4 item 6 must defer its breaker to the merged join when the review is enabled"
    )
    # Failure path must NOT leave the breaker unrun: if the review is deferred-then-fails,
    # the deferred breaker still runs over reflexion-only findings (no zero-breaker path).
    assert "must not skip the breaker" in step4, (
        "Step 4 must run the deferred breaker over reflexion-only findings when the review fails"
    )
    # Concurrency edge: a volume loop-back (items 4-5) firing while the adversarial
    # call is in flight must discard the in-flight result as stale (no wait, no breaker).
    assert "in flight" in step4 and "stale" in step4, (
        "Step 4 must discard an in-flight adversarial result as stale on a volume loop-back"
    )
    # Existing invariants preserved (mirror of test_adversarial_review_registration).
    assert "adversarial-review" in step4.lower()
    assert "is-enabled" in step4
    assert "fast_path_eligible == false" in step4
    assert '"design"' in step4
    assert '"adversarial"' not in step4
