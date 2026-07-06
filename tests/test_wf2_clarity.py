"""Drift guards for the WF2 (implement-feature) clarity additions from the
2026-06-16 assessment (Tier 3):

- a single ordered happy-path "spine" so an orchestrator under context pressure
  has one anchor for "what always runs, in order";
- Step 11.5 (security scan) and Step 16 (completion summary + run-record) added to
  <mandatory-steps> so the must-not-skip set matches the <completion-gate> set;
- the missing 4th loop-back source (review_design, consumed by Step 8a) listed in
  <loop-back-budget>;
- a single authoritative "Breaker decision" table in Step 4 so the run-exactly-once
  invariant can't drift across the disabled/enabled/non-success/loop-back branches.

These assert content that should EXIST in SKILL.md; they fail before the edits land.
"""
import re
from pathlib import Path

from tests.corpus import skill_corpus

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILL = REPO_ROOT / "skills" / "implement-feature" / "SKILL.md"
REFERENCES = REPO_ROOT / "skills" / "implement-feature" / "references"


def _text() -> str:
    # Corpus, not SKILL.md alone: the #158 restructure may move pinned prose
    # into references/ — these guards pin CONTENT, wherever it lives.
    return skill_corpus("implement-feature")


def _block(text: str, tag: str) -> str:
    m = re.search(rf"<{tag}>(.*?)</{tag}>", text, re.DOTALL)
    assert m, f"<{tag}> block not found in skill corpus"
    return m.group(1)


def test_happy_path_spine_present_and_ordered():
    text = _text()
    spine = _block(text, "happy-path")
    # The always-run security gate and completion step must be in the spine,
    # in order, around the PR step.
    assert "11 → 11.5 → 12" in spine, "spine must show 11 -> 11.5 -> 12 in order"
    assert "16" in spine and "(8a)" in spine


def test_mandatory_steps_includes_security_scan_and_completion():
    block = _block(_text(), "mandatory-steps")
    assert "| 11.5 |" in block, "Step 11.5 (security scan) must be in the mandatory table"
    assert "| 16 |" in block, "Step 16 (completion summary + run-record) must be in the mandatory table"


def test_loopback_budget_lists_review_design_source():
    block = _block(_text(), "loop-back-budget")
    assert "review_design" in block, "the 4th loop-back source (review_design) must be listed"
    assert "review_design_loopback_used" in block, "review_design needs an in-context mirror counter"


def test_step4_has_single_breaker_decision_table():
    text = _text()
    # Scope to Step 4.
    start = text.index("## Step 4: Quality Gate")
    end = text.index("## Step 5:")
    step4 = text[start:end]
    assert "Breaker decision" in step4, "Step 4 must carry a single authoritative breaker-decision table"
    assert "Breaker runs over" in step4, "the table must state which findings the one breaker runs over"
    # The volume-loopback row skips the breaker; the non-success row still runs it.
    assert "SKIP" in step4


def test_step4_is_quality_bar_no_reflexion():
    """#190 retired the 3-judge panel; #205 removed the external reflexion
    dependency entirely. WF2 Step 4 now applies the in-repo quality-bar rubric —
    no /reflexion:* invocation, no 3-judge panel. The opt-in cross-model
    adversarial-on-design (WF5) stays (AC2)."""
    text = _text()
    start = text.index("## Step 4: Quality Gate")
    end = text.index("## Step 5:")
    step4 = text[start:end]
    # No reflexion dependency at all (#205), no panel (#190).
    assert "/reflexion:" not in step4, \
        "Step 4 must not invoke any /reflexion:* skill (#205)"
    assert "three judge" not in step4.lower() and "3-judge" not in step4, \
        "Step 4 must not describe a 3-judge panel (#190)"
    # The in-repo quality-bar rubric is the gate for all lanes.
    assert "quality-bar" in step4, "Step 4 must apply the in-repo quality-bar rubric (#205)"
    # AC2: the opt-in cross-model adversarial-on-design review stays available.
    assert "adversarial" in step4.lower(), \
        "Step 4 must keep the opt-in adversarial-on-design sub-step (AC2)"
    assert "critiqueMethod" not in step4, \
        "the critiqueMethod preamble is removed from WF2 Step 4 (#190/#205)"


def test_readme_wf2_gate_row_is_quality_bar_not_reflexion():
    """#205 leftover guard: the README feature-section tables must not advertise
    WF2's design gate as any /reflexion:* skill."""
    readme = (REPO_ROOT / "README.md").read_text()
    for line in readme.splitlines():
        if "WF2 Feature Implementation" in line and "|" in line:
            assert "/reflexion:" not in line, \
                "README WF2 gate row still names a /reflexion:* skill (#205)"
            assert "quality-bar" in line.lower(), \
                "README WF2 gate row should name the in-repo quality-bar self-review (#205)"


def test_active_skills_are_reflexion_free():
    """#205: no active skill may invoke a /reflexion:* skill or read the
    critiqueMethod preference. Historical docs under docs/ may still mention
    reflexion; only the active skill corpus is held clean."""
    import re
    skills_dir = REPO_ROOT / "skills"
    pat = re.compile(r"/reflexion:\w+|reflexion:(?:reflect|critique|memorize)|\bcritiqueMethod\b")
    offenders = []
    for md in skills_dir.rglob("*.md"):
        # eval workspaces are fixtures, not the active corpus
        if "-workspace" in md.parts[len(skills_dir.parts)]:
            continue
        hits = sorted(set(pat.findall(md.read_text(encoding="utf-8", errors="replace"))))
        if hits:
            offenders.append((str(md.relative_to(REPO_ROOT)), hits))
    assert not offenders, f"active skills still reference reflexion: {offenders}"


def test_fast_path_table_step4_full_and_lane_both_reflect():
    """#190: the keep/collapse table's Step-4 row no longer contrasts a panel
    (full) against reflect (lane) — both use reflect; the full spine's only extra
    is the opt-in adversarial-on-design."""
    text = _text()
    # The Step 4 row of the keep/collapse table.
    for marker in ("4 Design critique",):
        assert marker in text
    # No table cell may still advertise the retired panel.
    table_region = text[text.index("Keep / collapse table"):text.index("Exact retained vs. removed gates")]
    assert "3-judge panel" not in table_region, \
        "keep/collapse table still advertises the retired 3-judge panel (#190)"


# --- Tier 1 (progressive disclosure): run-record schema extracted to references/ ---

class TestRunRecordReference:
    def test_run_record_reference_exists_with_schema(self):
        ref = REFERENCES / "run-record.md"
        assert ref.exists(), "the run-record schema should live in references/run-record.md"
        text = ref.read_text()
        for key in ("workflow_version", '"gates"', "security_scan", "loop_backs", "follow_ups"):
            assert key in text, f"run-record reference is missing the {key} field"

    def test_step16_points_to_reference_and_keeps_invocation(self):
        # LOCATION pin: the pointer + invocation must be in the SKILL.md BASE
        # (not merely somewhere in the corpus) — reads SKILL.md directly.
        text = SKILL.read_text()
        step16 = text[text.index("## Step 16:"):]
        assert "references/run-record.md" in step16, "Step 16 must point at the extracted schema reference"
        # The load-bearing CLI invocation + rc handling stay in the base (test_work_summary pins it too).
        assert "work_summary.py summarize" in step16

    def test_full_schema_not_reinlined_in_base(self):
        # LOCATION pin: asserts the schema is ABSENT from the base — over the
        # corpus this would be meaningless (references/ legitimately has it).
        text = SKILL.read_text()
        step16 = text[text.index("## Step 16:"):text.index("<completion-gate>")]
        assert step16.count('"workflow_version"') == 0, (
            "the full run-record JSON schema should be in references/run-record.md, "
            "not re-inlined in the base Step 16"
        )


# --- Task 5 (#131): WF2 Step 11 opt-in adversarial diff-review sub-step ---

GITIGNORE = REPO_ROOT / ".gitignore"


def _step11() -> str:
    """Text of Step 11 only (up to Step 11.5), where the sub-step lives."""
    text = _text()
    start = text.index("## Step 11: Pre-PR Code Review")
    end = text.index("## Step 11.5:")
    return text[start:end]


class TestStep11DiffReview:
    def test_marker_template_four_states(self):
        s11 = _step11()
        assert "### WF2 Step 11 — Adversarial Diff Review:" in s11
        for state in ("findings_present", "no_findings", "failed (", "skipped ("):
            assert state in s11, f"Step 11 diff-review marker missing state {state!r}"

    def test_gate_probe_is_enabled_for_this_skill(self):
        s11 = _step11()
        assert "is-enabled" in s11, "Step 11 must reuse the is-enabled enablement probe"
        assert "--skill implement-feature" in s11

    def test_should_run_diff_review_referenced(self):
        assert "plan_lib.should_run_diff_review" in _step11()

    def test_dispatch_command_flags(self):
        s11 = _step11()
        for flag in ("--type diff", "--findings-json", "--headless"):
            assert flag in s11, f"Step 11 dispatch command missing {flag!r}"

    def test_patch_construction_and_failure_strings(self):
        s11 = _step11()
        assert "high-risk-first" in s11, "patch must be built high-risk-first"
        assert "truncated" in s11 and "failed (truncated)" in s11
        assert "base ref unavailable" in s11

    def test_stale_sweep_and_confidence_mapping(self):
        s11 = _step11()
        assert ".rawgentic-diff-review-" in s11, "stale-temp sweep must name the patch glob prefix"
        assert re.search(r"stale|cleanup|leftover", s11, re.IGNORECASE), (
            "the sweep must carry cleanup/stale language (crash recovery)"
        )
        assert "ADV_CONFIDENCE_TO_FLOAT" in s11, "confidence enum must map via ADV_CONFIDENCE_TO_FLOAT"

    def test_secrets_surfacing_in_marker(self):
        assert "secrets detected" in _step11()

    def test_completion_gate_conditional_marker(self):
        gate = _block(_text(), "completion-gate")
        assert "Adversarial Diff Review" in gate, (
            "completion-gate must require the 4-state diff-review marker when opted in"
        )

    def test_gitignore_has_diff_review_globs(self):
        gi = GITIGNORE.read_text()
        assert ".rawgentic-diff-review-*.patch" in gi
        assert ".rawgentic-diff-findings-*.json" in gi


# --- Task 2 (#135): WF2 small-standard lane — a middle gear between the ---
# --- trivial-work exit and the full 16-step spine. ---


def _section(text: str, header: str, next_header: str) -> str:
    start = text.index(header)
    end = text.index(next_header, start)
    return text[start:end]


class TestSmallStandardLane:
    """Drift guards for the WF2 small-standard lane (#135). The lane is a semantic
    replacement of the old Step-4-only fast path, generalized to the whole spine:
    it collapses design ceremony (Steps 3/4/5/9) and skips Step 6, but keeps TDD,
    code review, and the security scan. These assert the block, its canonical
    predicate + deprecated alias, the mechanical decision call, the keep/collapse
    contract, the Step-9 input-source cross-check, the Step 6 lane skip, the
    suggested-never-silent surfacing, and the non-negotiable review/security gates
    all stay present so a later edit can't silently drop them.
    """

    def test_lane_block_present_and_replaces_fast_path(self):
        text = _text()
        assert "<small-standard-lane>" in text and "</small-standard-lane>" in text
        # The old Step-4-only block name is REPLACED, not kept alongside.
        assert "<fast-path-detection>" not in text

    def test_canonical_predicate_and_deprecated_alias(self):
        block = _block(_text(), "small-standard-lane")
        assert "small_standard_lane_eligible" in block
        # fast_path_eligible kept ONLY as a deprecated alias so Step-4 readers work.
        assert "fast_path_eligible = small_standard_lane_eligible" in block
        assert "deprecated alias" in block.lower()

    def test_lane_decision_and_count_helper_wired(self):
        block = _block(_text(), "small-standard-lane")
        assert "lane_decision" in block
        assert "count_impl_files" in block

    def test_keep_collapse_contract_wording(self):
        block = _block(_text(), "small-standard-lane")
        for token in ("RETAINED", "COLLAPSED", "REMOVED"):
            assert token in block, f"keep/collapse contract missing {token!r}"
        # The one removed gate is Step 6 (plan drift).
        assert "Step 6" in block

    def test_input_source_honesty_and_step9_lane_widened_crosscheck(self):
        text = _text()
        block = _block(text, "small-standard-lane")
        # Step-2 file_count is an ESTIMATE (input-source honesty).
        assert "estimate" in block.lower()
        # Step 9 cross-checks the REAL diff count and records lane-widened, never fails.
        step9 = _section(text, "## Step 9:", "## Step 10:")
        assert "lane-widened" in step9
        assert "count_impl_files" in step9
        assert "git diff --name-only" in step9

    def test_surfacing_block_recommends_lane(self):
        block = _block(_text(), "small-standard-lane")
        assert "(a) Small-standard lane" in block
        assert "[recommended]" in block
        # Headless auto-resolves the lane-vs-full choice (no interactive user).
        assert "AUTO-RESOLVE" in block

    def test_step6_skip_mentions_lane(self):
        step6 = _section(_text(), "## Step 6:", "## Step 7:")
        assert "small-standard lane" in step6.lower()

    def test_step5_checklist_variant_keeps_risklevel(self):
        step5 = _section(_text(), "## Step 5:", "## Step 6:")
        assert "checklist plan" in step5.lower()
        # riskLevel tagging is RETAINED in the lane (Step 8a still needs it).
        assert "riskLevel" in step5

    def test_step9_evidence_only_variant(self):
        step9 = _section(_text(), "## Step 9:", "## Step 10:")
        low = step9.lower()
        assert "evidence-only" in low or "evidence only" in low

    def test_mandatory_steps_lane_note_keeps_review_and_security(self):
        block = _block(_text(), "mandatory-steps")
        assert "small-standard lane" in block.lower()
        # Steps 4/5/9 COLLAPSE (not skipped) so the mandatory invariant holds.
        assert "COLLAPSED" in block
        # Step 11 (review) + 11.5 (security) stay non-negotiable in the lane.
        assert "NON-NEGOTIABLE in the lane" in block
        assert "Step 11" in block
        assert "11.5" in block


# --- #140: Step 7 branches from fresh origin/<default>, mutates nothing ---

def _step7() -> str:
    """The Step 7 section text (## Step 7 ... up to the next ## Step)."""
    text = _text()
    m = re.search(r"## Step 7:.*?(?=\n## Step 8:)", text, re.DOTALL)
    assert m, "Step 7 section not found"
    return m.group(0)


class TestStep7BranchBase:
    def test_branches_from_fresh_origin_default(self):
        s7 = _step7()
        assert "git fetch origin" in s7, "Step 7 must fetch before branching"
        # stance-independent create: checkout -b <branch> origin/<default>
        assert re.search(
            r"git checkout -b <branch_name> origin/\$\{capabilities\.default_branch\}", s7
        ), "Step 7 must create the branch from origin/<default>, not the current HEAD"

    def test_no_pull_into_current_branch(self):
        s7 = _step7()
        # the buggy form merged origin/main INTO whatever branch was checked out
        assert "git pull origin ${capabilities.default_branch} && git checkout -b" not in s7, \
            "Step 7 must not pull into the current branch before creating the new one"

    def test_has_base_assertion(self):
        s7 = _step7()
        assert "merge-base" in s7, "Step 7 must assert the new branch's base == origin/<default>"


def test_incident_hotfix_branch_fetches_first():
    """#140 AC4: incident is the one sibling whose branch step lacked an explicit
    fetch (the other 7 already fetch). Its hotfix branch must fetch first so a
    stale origin/<default> ref can't reintroduce the bug."""
    incident = (REPO_ROOT / "skills" / "incident" / "SKILL.md").read_text()
    idx = incident.find("git checkout -b hotfix/")
    assert idx != -1, "incident hotfix checkout not found"
    preceding = incident[:idx]
    assert "git fetch origin" in preceding, "incident must fetch origin before the hotfix checkout"


# --- #138: deferred-to-target verification surfaced across the spine ---

class TestDeferredVerification:
    def test_step5_documents_deferral_marker(self):
        text = _text()
        assert "deferred-to-target" in text, "Step 5 must document the deferral marker"
        assert "best local proxy" in text, "deferral must require the best local proxy (anti-abuse)"

    def test_step9_lists_deferred_never_verified(self):
        s9 = re.search(r"## Step 9:.*?(?=\n## Step 10:)", _text(), re.DOTALL)
        assert s9, "Step 9 section not found"
        body = s9.group(0)
        assert "deferred_tasks" in body
        assert "never counts as verified" in body

    def test_step12_has_canonical_deferred_heading(self):
        s12 = re.search(r"## Step 12:.*?(?=\n## Step 13:)", _text(), re.DOTALL)
        assert s12, "Step 12 section not found"
        assert "## Deferred verification" in s12.group(0), "canonical PR heading required"

    def test_completion_gate_calls_assert_deferrals_recorded(self):
        gate = _block(_text(), "completion-gate")
        assert "assert_deferrals_recorded" in gate
        assert "unrecorded" in gate.lower()


# --- #137: CI quarantine handled as a visible non-gate ---

class TestCiQuarantine:
    def test_step13_handles_quarantine_as_non_gate(self):
        s13 = re.search(r"## Step 13:.*?(?=\n## Step 14:)", _text(), re.DOTALL)
        assert s13, "Step 13 not found"
        body = s13.group(0)
        assert "ci_quarantined" in body
        assert "not gating" in body
        assert "never claim green" in body or "never report" in body.lower() or "not report" in body.lower()
        # trust guard: a PR must not disable its own CI gate (#137 Step-11 F1)
        assert "ci_quarantine_change" in body

    def test_completion_gate_item6_allows_quarantine(self):
        gate = _block(_text(), "completion-gate")
        assert "ci_quarantined" in gate or "quarantine recorded" in gate.lower()

    def test_step1_has_quarantine_staleness_nag(self):
        s1 = re.search(r"## Step 1:.*?(?=\n## Step 2:)", _text(), re.DOTALL)
        assert s1, "Step 1 not found"
        assert "ci_quarantined" in s1.group(0) and "30 calendar days" in s1.group(0)


# --- #139: branch-protection probe + gate-layer honesty ---

class TestBranchProtectionProbe:
    def test_step1_probes_protection(self):
        s1 = re.search(r"## Step 1:.*?(?=\n## Step 2:)", _text(), re.DOTALL)
        assert s1, "Step 1 not found"
        body = s1.group(0)
        assert "classify_branch_protection" in body
        assert "/protection" in body
        assert "@uri" in body or "URL-encode" in body  # branch URL-encoded (#139 F2)
        assert "fail-open" in body.lower() or "never fail the run" in body.lower()

    def test_step12_pr_body_has_protection_line(self):
        s12 = re.search(r"## Step 12:.*?(?=\n## Step 13:)", _text(), re.DOTALL)
        assert s12, "Step 12 not found"
        assert "branch_protection_line" in s12.group(0)

    def test_step14_checks_quarantine_protection_contradiction(self):
        s14 = re.search(r"## Step 14:.*?(?=\n## Step 15:)", _text(), re.DOTALL)
        assert s14, "Step 14 not found"
        assert "quarantine_protection_contradiction" in s14.group(0)


# --- #223: tiered design loop-back (spec-tightening vs design-flaw) ---

class TestTieredLoopback:
    """Drift guards for the #223 two-class Step-4 loop-back. Pin the canonical
    contract sentences, the fail-closed fold, the volume-never-folds rule, the
    Loopback-class field in both finding shapes, and the SKILL.md budget-block
    mirror of the new plan_lib source."""

    def _step4(self) -> str:
        text = _text()
        return text[text.index("## Step 4: Quality Gate"):text.index("## Step 5:")]

    def test_canonical_cheap_path_sentence(self):
        # AC1's contract: one verifier, changed sections only, no Step-3 return.
        s4 = self._step4()
        assert ("dispatches exactly one verifier over only the changed design "
                "sections and never returns to Step 3") in s4

    def test_canonical_one_entry_per_finding_sentence(self):
        # Whitespace-normalized: prose may hard-wrap mid-phrase.
        s4 = " ".join(self._step4().split())
        assert "contributes exactly one Loopback-class entry" in s4
        assert "untagged" in s4, "absent field must contribute the 'untagged' entry"

    def test_volume_loopback_never_folds(self):
        s4 = self._step4()
        assert "NEVER folds" in s4, "the item-5 volume loop-back must stay on the full design path"

    def test_fold_helper_named(self):
        assert "classify_loopback_source" in self._step4()

    def test_loopback_class_field_in_wf2_finding_shape(self):
        # steps.md §4 is WF2's gate-owned finding shape (quality-bar.md is the
        # 3-gate shared DEFAULT — fix-bug/setup don't tier, so the field lives
        # only in the WF2 override, per quality-bar.md's own override clause).
        s4 = self._step4()
        assert "Loopback-class: spec-tightening | design-flaw" in s4

    def test_budget_block_lists_spec_tighten(self):
        block = _block(_text(), "loop-back-budget")
        assert "spec_tighten" in block
        assert "spec_tighten_loopback_count" in block, "spec_tighten needs an in-context mirror counter"
        assert "**five** sources" in block

    def test_constants_mirror_plan_lib_cap(self):
        import sys
        sys.path.insert(0, str(REPO_ROOT / "hooks"))
        import plan_lib
        constants = _block(_text(), "constants")
        m = re.search(r"MAX_SPEC_TIGHTEN_LOOPBACK = (\d+)", constants)
        assert m, "SKILL.md constants must declare MAX_SPEC_TIGHTEN_LOOPBACK"
        assert int(m.group(1)) == plan_lib._LOOPBACK_SOURCE_MAX["spec_tighten"]

    def test_escalation_never_silent_passes_ambiguity(self):
        # F6: an ambiguous/conflicting verifier finding escalates, never PASSes.
        s4 = self._step4()
        assert "never silent-PASS" in s4 or "never silently PASS" in s4


# --- #224: Step-2 upfront agent-count / est-time estimate ---

class TestStep2PathEstimate:
    """Drift guards for the #224 Step-2 path-cost estimate."""

    def _step2(self) -> str:
        text = _text()
        return text[text.index("## Step 2:"):text.index("## Step 3:")]

    def test_step2_emits_derived_estimate(self):
        s2 = " ".join(self._step2().split())
        assert "plan_lib.estimate_agents" in s2
        assert "Path estimate:" in s2
        # AC1: both paths in one line.
        assert "small-standard lane ≈" in s2

    def test_not_a_contract_canonical_sentence(self):
        s2 = " ".join(self._step2().split())
        assert ("derived via plan_lib.estimate_agents — never hard-coded — "
                "and is an estimate, not a contract") in s2

    def test_projection_is_labeled_lower_bound(self):
        s2 = " ".join(self._step2().split())
        assert "any_high_risk_path" in s2
        assert "lower bound" in s2
        # semantic criteria invisible pre-decomposition must be named
        assert "invisible" in s2 or "cannot be seen" in s2

    def test_unconditional_estimate_marker(self):
        # AC3: a dedicated session-note marker, not a suspend-only checkpoint.
        s2 = self._step2()
        assert "### WF2 Step 2 — path estimate:" in s2

    def test_step5_refreshes_estimate(self):
        text = _text()
        s5 = _section(text, "## Step 5:", "## Step 6:")
        assert "estimate_agents" in s5, "Step 5 must refresh the estimate with the real high-risk count"

    def test_step11_axis_reconciled_in_skill_base(self):
        # The stale complexity-keyed row contradicted steps.md's unconditional
        # 3-agent dispatch; the mandatory table is lane-keyed now.
        base = SKILL.read_text()
        assert "Full 3-agent review for complex_feature. Minimum 1-agent for simple/standard." not in base
        assert "Full 3-agent review; ≥1 in the small-standard lane." in base

    def test_constants_mirror_step11_counts(self):
        import sys
        sys.path.insert(0, str(REPO_ROOT / "hooks"))
        import plan_lib
        assert plan_lib.STEP11_REVIEW_AGENT_COUNT_FULL == 3
        assert plan_lib.STEP11_REVIEW_AGENT_COUNT_LANE == 1
        # steps.md §11 still documents the 3-agent dispatch the constant mirrors.
        text = _text()
        s11 = _step11()
        assert "3-agent parallel review" in s11


# --- #136: worktree-isolation parallelism probe ---

class TestParallelismProbe:
    def test_step2_probes_parallelism(self):
        s2 = re.search(r"## Step 2:.*?(?=\n## Step 3:)", _text(), re.DOTALL)
        assert s2, "Step 2 not found"
        body = s2.group(0)
        assert "probe-parallelism" in body
        assert "capabilities.parallelism" in body

    def test_step8_consults_parallelism(self):
        # the Step 8 parallel-execution block references the probe result
        text = _text()
        s8 = re.search(r"## Step 8:.*?(?=\n## Step 9:)", text, re.DOTALL)
        assert s8, "Step 8 not found"
        body = s8.group(0)
        assert "capabilities.parallelism" in body
        assert "serial-only" in body
