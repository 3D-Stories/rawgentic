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


# --- #320: port the #314 mechanical-projection read discipline into WF3. ---
# Section-sliced, ONE canonical sentence per guard (repo mistake #6: no
# whole-corpus regex, no substring counts). Companion to
# test_wf2_clarity.py::TestDelegatedReads (the WF2 sibling this ports from).


def _norm(s: str) -> str:
    """Whitespace-collapse so wrapped prose compares equal (WF2 sibling does
    the same `" ".join(text.split())` before pinning a canonical sentence)."""
    return " ".join(s.split())


class TestDelegatedReadsWF3:
    """The #314 contract in WF3: the RED reproduction run (Step 7 item 1), the
    full-suite regression run (Step 7 item 5 / Step 8 item 4), and the CI
    `--log-failed` read (Step 11 item 3) consume runner/CI output as bounded
    mechanical projections, never full-log dumps into the orchestrator's
    context. Prose + drift guards only (#320): the byte-threshold constants are
    skill-agnostic and shipped in #319; no hook changes here."""

    def _step7(self) -> str:
        return _section(_text(), "## Step 7:", "## Step 8:")

    def _step8(self) -> str:
        return _section(_text(), "## Step 8:", "## Step 9:")

    def _step11(self) -> str:
        return _section(_text(), "## Step 11:", "## Step 12:")

    def test_step7_projection_final_summary_tail(self):
        # AC1: the RED run + full suite consume the runner's own bounded summary.
        s7 = _norm(self._step7())
        assert ("consume the runner's own final summary (pass/fail counts + "
                "failing test ids + first assertion lines") in s7, (
            "Step 7 must wire the #314 test-output projection (final-summary tail)")

    def test_step7_projection_fail_closed(self):
        # AC1: the fail-closed rule — a failing-run projection that is empty /
        # malformed / command-failed falls back to the inline raw read.
        s7 = _norm(self._step7())
        assert ("an empty, malformed, or command-failed projection on a failing "
                "run falls back to the inline raw read") in s7, (
            "Step 7 must carry the #314 fail-closed inline-fallback rule")

    def test_step8_verify_all_tests_pass_is_a_projection(self):
        # AC1: Step 8's "verify all tests pass" is projection-consumed too.
        # Pinned to the item-4 canonical sentence (not the bare word "projection")
        # so a reword that drops the discipline while leaving "projection" in a
        # cross-reference elsewhere in the section still trips this guard.
        s8 = _norm(self._step8())
        assert ("consume the run as a **test-output projection** (#314, the same "
                "discipline as Step 7)") in s8, (
            "Step 8 item 4 (verify all tests pass) must consume the run as a "
            "#314 projection")

    def test_step11_log_failed_bounded_grep(self):
        # AC2: CI --log-failed consumed as a bounded grep over the threshold,
        # measured with a piped wc -c, same fail-closed fallback.
        s11 = self._step11()
        assert "WF2_READ_DELEGATE_BYTES_LOG" in s11, (
            "Step 11 must name the skill-agnostic byte threshold constant (#320)")
        assert "| wc -c" in s11, (
            "Step 11 must measure --log-failed with a piped wc -c (#314)")
        assert _norm(
            "grep it to the failing job/step + assertion/traceback first lines "
            "instead of reading the full log") in _norm(s11), (
            "Step 11 must bound the --log-failed read to failing job/step + "
            "assertion/traceback first lines (#314 AC2)")

    def test_option3_no_llm_reader_surface(self):
        # AC4: option-3 scope — WF3 ports the mechanical projections ONLY; the
        # validated-index LLM reader path (step11-diff/step2-map) is NOT wired.
        c = _text()
        assert "validate_index" not in c, (
            "WF3 must not wire the validated-index reader (option-3 scope, #320 AC4)")
        assert ".rawgentic-read-" not in c, (
            "WF3 must not wire the delegated-read temp-artifact surface (#320 AC4)")


# --- #330: canonical DISPATCH completion-time audit-line grammar (review-only) ---

class TestDispatchGrammar:
    """Drift guard for the #330 canonical DISPATCH audit line, WF3 review-only
    variant. WF3 dispatches only the `review` role, so its grammar line pins
    role=review literally. Whitespace-normalized per the repo convention."""

    def test_wf3_canonical_grammar_sentence_present(self):
        corpus = " ".join(skill_corpus("fix-bug").split())
        grammar = (
            "DISPATCH issue=<n> role=review type=<subagent_type> "
            "model=<model|null> effort=<effort|null> "
            "outcome=<ok|error|retried|dead> resolution=<primary|fallback|generic>"
        )
        assert grammar in corpus, (
            "the WF3 review-only canonical DISPATCH grammar line must be present "
            "in the fix-bug corpus")

    def test_wf3_per_invocation_emission_rule_present(self):
        """#330 8a hardening: two review agents must mean two DISPATCH lines.
        #331 (Step 11 refinement) splits descent emission by trigger — a
        runtime-error descent adds the abandoned tier's terminal line; a
        resolve-failure descent adds none (an unresolvable tier never ran)."""
        corpus = " ".join(skill_corpus("fix-bug").split())
        rule = ("One line per SUBAGENT INVOCATION dispatched (not per attempt) "
                "— WF3 Step 9's two review agents = two lines at a single "
                "tier; a slot that descends on a RUNTIME ERROR adds the "
                "abandoned tier's terminal line, while a resolve-failure "
                "descent adds none (an unresolvable tier never ran).")
        assert rule in corpus, (
            "the WF3 per-invocation DISPATCH emission rule must be present in "
            "the fix-bug corpus, split by descent trigger per #331")

    def test_wf3_descent_trigger_split_present(self):
        """#331 Step 11 refinement: a resolve-failure descent must never
        fabricate an 'attempted and errored' audit line for a tier that never
        ran; the runtime-error descent carries the abandoned tier's OWN
        resolution value. Pins the load-bearing clauses of the split rule."""
        corpus = " ".join(skill_corpus("fix-bug").split())
        assert ("a RESOLVE-FAILURE descent (the tier's agent type is not "
                "installed / does not resolve) emits NO line for the "
                "unresolved tier") in corpus, (
            "the resolve-failure no-line clause must be present (#331)")
        assert ("the abandoned tier's terminal line with `outcome=error` and "
                "THAT TIER's own resolution value (tier 1 → "
                "`resolution=primary`, tier 2 → `resolution=fallback`)") in corpus, (
            "the runtime-error two-line clause with per-tier resolution "
            "values must be present (#331)")


# --- #330: dispatches[] assembly instruction at WF3 Step 14 ---

class TestDispatchesAssembly:
    """Header-index-sliced guard (repo convention: test_wf2_clarity.py's
    TestTieredLoopback pattern, :444-454) pinning the Step 14 dispatches[]
    assembly instruction — the WF3 mirror of the WF2 Step 16 guard. Location pin
    (reads steps.md directly, not the corpus) since this is a specific-file,
    specific-section contract."""

    def _step14(self) -> str:
        text = (REPO_ROOT / "skills" / "fix-bug" / "references" / "steps.md").read_text()
        return _section(text, "## Step 14:", "## Workflow Resumption")

    def test_canonical_assembly_sentence_present(self):
        s14 = " ".join(self._step14().split())
        sentence = (
            "Assemble `dispatches[]` by grepping claude_docs/session_notes.md "
            "for lines matching `^DISPATCH issue=<n> ` where `<n>` is this "
            "run's issue number.")
        assert sentence in s14, (
            "Step 14 must contain the canonical #330 dispatches[] assembly "
            "sentence")


# --- #331: per-slot fallback chain + dead-return detection at the Step 9 gate ---

class TestPerSlotFallbackChain:
    """Header-index-sliced guard (repo convention: TestDispatchesAssembly /
    test_wf2_clarity.py's TestTieredLoopback, :444-454). Pins the two #331
    canonical sentences the Step 9 review gate must carry: the per-slot
    three-tier fallback chain (a fallback in one slot never collapses the gate
    from two reviews to one) and the dead-return relaunch rule (a vacuous
    reviewer return is a DEAD dispatch, not a clean pass). Location pin (reads
    steps.md directly), whitespace-normalized so wrapped prose compares equal."""

    def _step9(self) -> str:
        text = (REPO_ROOT / "skills" / "fix-bug" / "references" / "steps.md").read_text()
        return " ".join(_section(text, "## Step 9:", "## Step 10:").split())

    def test_per_slot_fallback_chain_sentence_present(self):
        s9 = self._step9()
        sentence = (
            "For EACH reviewer slot independently: dispatch the named "
            "`pr-review-toolkit` agent; if THAT slot's agent type fails to "
            "resolve, dispatch `rawgentic:rawgentic-reviewer` with that slot's "
            "brief; if that also fails, use a generic inline-prompt dispatch "
            "with that slot's brief — fallback in one slot never collapses the "
            "gate from two reviews to one.")
        assert sentence in s9, (
            "Step 9 must carry the #331 canonical per-slot three-tier fallback "
            "chain sentence")

    def test_dead_return_detection_sentence_present(self):
        s9 = self._step9()
        sentence = (
            "A reviewer return that is vacuous (no findings AND no substantive "
            "content) is a DEAD dispatch, not a clean pass — relaunch that slot "
            "once at the same tier; on a second death, record the slot as "
            "REVIEW_DISPATCH_FAILED in session notes and invoke the workflow's "
            "ERROR protocol — the gate never proceeds with fewer than two live "
            "reviews.")
        assert sentence in s9, (
            "Step 9 must carry the #331 canonical dead-return detection sentence")

    def test_step9_failure_mode_bullets_present(self):
        """8a hardening (#331): the failure-mode bullets carry the terminal
        action — deleting them must fail a test, not just the bold blocks."""
        s9 = self._step9()
        assert ("Named agent type does not resolve → per-slot fallback chain" in s9)
        assert ("Reviewer returns vacuous success → dead-return relaunch once, "
                "then REVIEW_DISPATCH_FAILED + the workflow's ERROR protocol") in s9


# --- #341: issue-keyed step markers (WF3) — contract + prescribed literals ---

class TestIssueKeyedMarkersWF3:
    """Drift guards for #341 in WF3: the <step-tracking> contract sentence plus
    each prescribed keyed WF3 marker literal. Corpus `in` (the `#<issue>` token
    makes each literal distinctive). Companion to
    test_wf2_clarity.py::TestIssueKeyedMarkers."""

    CONTRACT = (
        "On every marker line the run key is read from the marker type's "
        "canonical slot — concurrent runs share one notes file and un-keyed "
        "markers are mechanically un-attributable (#341)."
    )

    KEYED_LITERALS = (
        "### WF3 Step 1b — Goal guard (set|deferred|skipped): #<issue> — <first 80 chars of text | epic #N | decline reason>",
        "### WF3 Step 4 — Adversarial Review (#<issue>, invoked|skipped): <report path or skip reason>",
        "### WF3 Step 10 — design artifact #<issue> (updated|skipped)",
        "### WF3 Step 14: Completion summary + run-record — DONE (#<issue>: persisted: yes/no)",
    )

    def test_contract_sentence_present(self):
        # Whitespace-normalized: the sentence hard-wraps in <step-tracking>.
        corpus = " ".join(_text().split())
        assert self.CONTRACT in corpus, (
            "WF3 <step-tracking> must carry the #341 canonical attribution sentence")

    def test_step_tracking_marker_template_keyed(self):
        block = _block(_text(), "step-tracking")
        assert "### WF3 Step X: <Name> — DONE (#<issue>: <key detail>)" in block, (
            "the <step-tracking> marker template must carry the #<issue> key")

    def test_all_prescribed_literals_keyed(self):
        corpus = _text()
        for lit in self.KEYED_LITERALS:
            assert lit in corpus, f"missing keyed WF3 marker literal: {lit!r}"

    def test_markers_complete_is_run_scoped(self):
        """#341 Task 3: WF3's own §Workflow Resumption prose must state the
        same run-scoped counting rule as WF2's state-and-resume.md — WF3
        cannot inherit WF2's reference file (cache blocks cross-skill reads),
        so the rule is restated here verbatim."""
        norm = " ".join(_text().split())
        assert (
            "MARKERS_COMPLETE counts only markers whose canonical-slot key names "
            "the resuming issue; legacy un-keyed markers count only when the "
            "containing run-section header names the issue."
        ) in norm, (
            "fix-bug/references/steps.md §Workflow Resumption must state the "
            "run-scoped MARKERS_COMPLETE counting rule verbatim")


# --- #340: multi-pass gate counting pointer at WF3 Step 14 ---

class TestMultiPassGatePointerWF3:
    """#340: WF3 duplicates the gates JSON inline (only dispatches[] is by
    pointer), so its Step 14 gates block must carry a one-line pointer to the
    canonical #340 counting rule in WF2's run-record.md. Header-index-sliced
    (Step 14 section), location pin (reads steps.md directly), whitespace-
    normalized per the repo convention."""

    def _step14(self) -> str:
        text = (REPO_ROOT / "skills" / "fix-bug" / "references" / "steps.md").read_text()
        return " ".join(_section(text, "## Step 14:", "## Workflow Resumption").split())

    def test_pointer_line_present(self):
        s14 = self._step14()
        pointer = (
            "Multi-pass gates count per the #340 rule in "
            "`skills/implement-feature/references/run-record.md` "
            "(unique-across-passes / final-disposition-at-close).")
        assert pointer in s14, (
            "Step 14 must carry the #340 multi-pass counting pointer to "
            "run-record.md")
