# Phase 3 Workflow: Feature/Bug Issue Creation (v2.3)

**Date:** 2026-03-01 (revised)
**Author:** Workflow Design Agent
**Revision:** v2.3 -- critique fixes (loop-guard, threshold wording, Step 5 type contradiction, circuit breaker dual-path, template needs doc) + new Step 7 User Review & Refinement
**Inputs:** phase2-principles.md, phase3-workflow-issue-creation.md.v1, user feedback (5 decisions)
**Purpose:** Define the end-to-end workflow for creating feature requests and bug reports, with quality gates, user selection points, and principle alignment.

---

## Workflow: Feature/Bug Issue Creation

**Invocation:** `/create-issue` skill (custom Claude Code skill)
**Trigger:** User invokes `/create-issue` with a description of the desired feature or observed bug
**Inputs:**

- User's verbal or textual description of the desired feature or observed bug
- Existing codebase context (CLAUDE.md, MEMORY.md, session notes)
- Phase 2 principles (for quality gate placement)
- GitHub issue templates in `.github/ISSUE_TEMPLATE/` (for output conformance)

**Outputs:**

- GitHub issue with complete details (title, description, acceptance criteria, scope, labels), conforming to the repository's issue templates
- Memorized insights (if critique surfaces reusable patterns)
- Excalidraw diagram of this workflow

**Tracking:** The GitHub issue is the SOLE tracker of work. No README TODO, no ROADMAP, no `docs/plans/` entries are created by this workflow. The issue IS the tracking artifact.

**Principles enforced:**

- P8: Shift-Left Critique (full critique at ideation gate -- cheapest place to catch bad ideas)
- P9: Continuous Memorization (memorize reusable patterns after critique)
- P10: Diagram-Driven Design (workflow diagrammed in Excalidraw)
- P11: User-in-the-Loop Quality Gates (user selects which critique findings to incorporate)
- P12: Conventional Commit Discipline (issue title follows conventional format)

**Diagram:** `diagrams/workflow-issue-creation.excalidraw`

**Termination:** WF1 ALWAYS terminates after issue creation. WF2 (Feature Implementation) requires explicit, separate invocation. There is no auto-transition from WF1 to WF2 under any circumstance.

---

## Issue Template Requirement

This workflow assumes that GitHub issue templates exist at `.github/ISSUE_TEMPLATE/`. Two templates should be created:

1. **Feature Request** (`.github/ISSUE_TEMPLATE/feature_request.md` or `.yml`) -- structured sections for description, acceptance criteria, scope, affected components, risk assessment, complexity estimate
2. **Bug Report** (`.github/ISSUE_TEMPLATE/bug_report.md` or `.yml`) -- structured sections for description, steps to reproduce, expected vs. actual behavior, environment details, error logs

The brainstorm output (Step 2) and final issue creation (Step 8) MUST conform to whichever template matches the issue type. If templates do not yet exist in the repository, WF1 should create them as a prerequisite on first run.

---

## Finding Application & Ambiguity Circuit Breaker

**Default behavior:** Apply ALL findings (Critical + High + Medium + Low). All findings are applied automatically.

**Ambiguity circuit breaker (ALWAYS active):** Before applying findings, the workflow checks for ambiguity. Relying on "the user is watching" is not a safety mechanism — the circuit breaker fires regardless of whether a human is actively monitoring. It activates when:

- Any finding has ambiguous interpretation (multiple valid ways to apply it)
- Two or more findings conflict with each other (applying both would be contradictory)
- A finding requires a judgment call that depends on user intent not captured in the original description

When the circuit breaker triggers, the workflow STOPS at Step 4 and waits for user input. It does NOT auto-apply the ambiguous/conflicting findings and does NOT skip them. The workflow resumes only after the user resolves the ambiguity.

**Rationale for removing attended/unattended distinction:** The previous design exempted "attended mode" from the circuit breaker on the theory that human oversight would catch issues in real time. This is insufficient — ambiguous findings should always be flagged explicitly rather than hoping the user notices. One mode, one behavior, one safety mechanism.

---

## Steps

### Step 1: Receive User Intent

**Type:** user decision
**Actor:** human
**Command:** `/create-issue` (user invokes this skill to start WF1)
**Input:** Raw user description of a feature request or bug report, provided as argument to `/create-issue` or in the follow-up prompt. This can be a verbal description, a pasted error log, a screenshot, or a reference to existing behavior that needs changing.
**Action:** The user invokes `/create-issue` with their intent. Claude Code acknowledges the request and confirms whether this is a feature request or a bug report. If ambiguous, Claude asks clarifying questions before proceeding.
**Output:** Classified intent: { type: "feature" | "bug", raw_description: string, clarifications: string[] }
**Failure mode:** User provides insufficient information. Recovery: Claude asks targeted clarifying questions (what is the expected behavior? what is the actual behavior? what area of the system is affected?). Do not proceed to brainstorm until the intent is clear enough to generate meaningful acceptance criteria.
**Principle alignment:** P11 (User-in-the-Loop) -- the user is the sole decision-maker on what gets built.
**Critique level:** N/A (not a quality gate)
**User selection:** yes -- user confirms the classification (feature vs. bug) and provides any missing details

---

### Step 2: Brainstorm Feature/Bug Details

**Type:** automated
**Actor:** sub-agent
**Command:** `/superpowers:brainstorm`
**Input:** Classified intent from Step 1, plus codebase context (CLAUDE.md architecture, relevant code files, existing issues for dedup check), plus the matching GitHub issue template from `.github/ISSUE_TEMPLATE/`
**Action:** The brainstorm sub-agent generates a comprehensive feature/bug specification. The output MUST conform to the structure defined in the matching GitHub issue template (feature_request or bug_report). Within that template structure, the brainstorm generates:

- Title (following conventional format: `feat(scope): description` or `fix(scope): description`)
- Detailed description with context and motivation
- Acceptance criteria (numbered, testable, specific)
- Scope definition (what is in scope and what is explicitly out of scope)
- Affected components (which files, services, DB tables are likely impacted)
- Risk assessment (what could go wrong, dependencies, blockers)
- Estimated complexity (T-shirt size: S/M/L/XL)
- Related issues (cross-reference existing issues if applicable)

For bug reports, the brainstorm additionally includes:

- Steps to reproduce
- Expected behavior vs. actual behavior
- Error logs or stack traces (if available)
- Environment details (which VM, which container, which branch)

**Output:** Draft issue specification document (structured markdown conforming to the issue template)
**Failure mode:** (1) Brainstorm produces overly vague acceptance criteria. Recovery: the critique step (Step 3) will catch this. (2) Brainstorm hallucinates non-existent components or files. Recovery: the critique step verifies claims against actual codebase. (3) Brainstorm duplicates an existing issue. Recovery: brainstorm should include a dedup check via `gh issue list --search`. (4) No issue template exists. Recovery: create the template first, then proceed with brainstorm.
**Principle alignment:** P8 (Shift-Left Critique) -- brainstorm generates the artifact that will be critiqued; P10 (Diagram-Driven Design) -- brainstorm may suggest architectural diagrams needed.
**Critique level:** N/A (not a quality gate -- this is the artifact being gated)
**User selection:** no -- automated generation, user reviews in the next steps

---

### Step 3: Full Critique of Brainstorm Output

**Type:** quality gate
**Actor:** sub-agent (3-judge multi-agent debate)
**Command:** `/reflexion:critique`
**Input:** Draft issue specification from Step 2, plus codebase context for claim verification
**Action:** Three judge sub-agents independently analyze the brainstorm output, then engage in debate rounds. Each judge evaluates:

- **Completeness:** Are all acceptance criteria testable? Is scope well-defined? Are edge cases covered?
- **Accuracy:** Do referenced components/files actually exist? Are architecture claims correct? (Addresses the known pitfall of subagent hallucination documented in CLAUDE.md verification checklist)
- **Feasibility:** Is the estimated complexity realistic? Are there hidden dependencies or blockers?
- **Consistency:** Does this feature/bug align with existing architecture decisions? Does it conflict with any known security standards or code quality rules?
- **Deduplication:** Is this truly new, or does it overlap with an existing issue or recent work?
- **Template conformance:** Does the output match the expected GitHub issue template structure?

The critique produces findings in four severity tiers:

- **Critical:** Issues that would make the issue fundamentally misleading, incomplete, or harmful (e.g., wrong component referenced, missing acceptance criteria for a critical path, security implications missed)
- **High:** Important issues that significantly degrade issue quality (e.g., vague acceptance criteria that can't be tested, scope that conflicts with existing architecture)
- **Medium:** Moderate improvements that strengthen the specification (e.g., adding edge case criteria, clarifying scope boundaries, better risk assessment)
- **Low:** Minor refinements and polish (e.g., adding links to related design docs, suggesting labels, improving wording)

**Output:** Prioritized findings list (Critical / High / Medium / Low) with specific, actionable recommendations. Each finding is also tagged with an ambiguity flag (clear | ambiguous) for use by the circuit breaker in Step 4.
**Failure mode:** (1) Critique finds zero issues (suspicious -- may indicate insufficient analysis). Recovery: verify the critique sub-agents actually read the codebase context and that the debate rounds actually occurred. (2) Finding volume exceeds thresholds: more than 5 Critical findings OR more than 5 High findings, OR more than 10 Medium findings OR more than 10 Low findings. These thresholds are evaluated independently per tier (not combined). This volume indicates a requirements gathering problem, not a critique problem -- the brainstorm input was too vague, ambiguous, or poorly scoped. Recovery: loop back to Step 2 (Brainstorm) — present ALL findings to the user as targeted clarifying questions, work through each finding to tighten requirements, then re-run brainstorm with improved inputs. Do NOT proceed to Step 4 with this many findings. **Maximum 2 loop-back iterations.** After 2 loops without threshold improvement, escalate to user with the full finding list instead of looping again. Configurable: `MAX_LOOPBACK_ITERATIONS = 2` in the `/create-issue` skill. (3) Critique sub-agents disagree fundamentally on a finding. Recovery: the debate rounds should resolve disagreements; if not, flag the contested finding as ambiguous for user attention.
**Principle alignment:** P8 (Shift-Left Critique) -- this IS the ideation gate, the cheapest point to catch bad ideas (changes are free before implementation); P11 (User-in-the-Loop) -- findings are presented for selection, not auto-applied.
**Critique level:** Full critique (`/reflexion:critique`) -- RATIONALE: This is an ideation gate. The cost of change at this stage is effectively zero (no code has been written, no branch created, no tests exist). A full 3-judge debate is justified because: (a) catching a flawed feature description here prevents wasted implementation effort downstream, (b) the cost-of-defect curve shows ideation-stage fixes are 10-100x cheaper than post-implementation fixes, and (c) the brainstorm output may contain hallucinated component references that only multi-agent verification can reliably catch.
**User selection:** no -- findings are produced here but presented to the user in Step 4

---

### Step 4: Apply Critique Findings (with Ambiguity Circuit Breaker)

**Type:** automated with circuit breaker
**Actor:** automated (circuit breaker may escalate to human)
**Command:** N/A
**Input:** Prioritized findings list from Step 3 (Critical / High / Medium / Low), each tagged with an ambiguity flag. Note: Step 4 is only reached if Step 3's volume thresholds passed (i.e., finding counts are within acceptable limits -- if they exceeded thresholds, the workflow already looped back to Step 1).
**Action:**

ALL findings are applied (Critical + High + Medium + Low). Before application, the ambiguity circuit breaker scans all findings:

1. Check each finding's ambiguity flag
2. Check for pairwise conflicts between findings (finding A contradicts finding B)
3. Check for findings requiring judgment calls beyond the original user description

If ALL findings are clear (no ambiguity, no conflicts): apply all findings and continue to Step 5.

If ANY finding triggers the circuit breaker: STOP the workflow. Present the ambiguous/conflicting findings to the user with an explanation of why automatic application was blocked. Wait for user input before proceeding. Do NOT apply the unambiguous findings separately -- the full set is applied together after resolution.

After circuit breaker resolution (if triggered), the user may also add their own amendments not raised by the critique.

**Output (two paths):**

- **Clear path (no ambiguity):** All findings are clear → amendment list produced immediately → continue to Step 5.
- **Blocked path (circuit breaker triggered):** Ambiguous/conflicting findings detected → workflow STOPS → present ambiguous findings to user → user resolves ambiguity → resume → amendment list produced (all findings + any user-added amendments) → continue to Step 5.

**Failure mode:** (1) User rejects a Critical finding during circuit breaker resolution. Recovery: warn the user that Critical items address fundamental issues (e.g., wrong component references, security gaps) and ask for confirmation. Do not block -- the user has final authority. (2) The circuit breaker triggers on most runs. Recovery: this indicates the critique step is flagging too many ambiguous findings -- tune the ambiguity detection threshold or improve the brainstorm step's specificity. (3) User adds amendments that contradict the original brainstorm intent. Recovery: flag the contradiction and ask for clarification.
**Principle alignment:** P11 (User-in-the-Loop Quality Gates) -- the circuit breaker escalates to the user only when needed; the default is fully automated application.
**Critique level:** N/A (not a quality gate -- this is the application step that follows the gate)
**User selection:** only on circuit breaker trigger -- otherwise fully automated

---

### Step 5: Incorporate Amendments into Issue Specification

**Type:** automated
**Actor:** sub-agent
**Command:** N/A (standard Claude Code editing)
**Input:** Original draft issue specification from Step 2 + amendment list from Step 4
**Action:** Claude Code revises the draft issue specification to incorporate all selected amendments. For each amendment:

- If it adds an acceptance criterion, insert it in the appropriate section with a note: "[Added per critique finding #N]"
- If it corrects a factual error (wrong component, wrong file path), fix it silently
- If it adjusts scope, update both the in-scope and out-of-scope sections
- If it adds risk assessment items, append to the risk section

The output must still conform to the GitHub issue template structure.

**Output:** Refined issue specification (draft, ready for user review in Step 7)
**Failure mode:** (1) Amendment incorporation introduces internal contradictions (e.g., one amendment narrows scope while another adds acceptance criteria outside the narrowed scope). Recovery: flag the contradiction to the user before proceeding. (2) The refined specification exceeds a reasonable issue length (over 2000 words for a single issue). Recovery: suggest splitting into multiple issues with cross-references.
**Principle alignment:** P8 (Shift-Left Critique) -- the critique findings are now incorporated into the artifact.
**Critique level:** N/A (not a quality gate)
**User selection:** no -- automated incorporation only; user reviews the output in Step 7

---

### Step 6: Conditional Memorization

**Type:** quality gate
**Actor:** sub-agent
**Command:** `/reflexion:memorize`
**Input:** Critique findings from Step 3 (including both selected and rejected findings), plus the pattern of what was learned
**Action:** If the critique in Step 3 surfaced reusable insights -- patterns that would apply to future issue creation or feature development -- invoke `/reflexion:memorize` to curate them into CLAUDE.md. Examples of memorizable insights:

- A recurring architecture constraint that future features must respect (e.g., "all new API endpoints require rate limiting" -- if the critique caught a missing rate limit consideration)
- A new anti-pattern discovered during the critique (e.g., "features touching the ML pipeline must account for the 120s startup delay")
- A codebase convention not yet documented (e.g., "all new tables need a corresponding migration script in postgres-migrations/")

The memorize command uses the ACE (Agentic Context Engineering) pattern to:

1. Extract the insight from the critique context
2. Check for duplication against existing CLAUDE.md and MEMORY.md content
3. If novel, append to the appropriate section of CLAUDE.md
4. If CLAUDE.md/MEMORY.md is at capacity, suggest archiving stale entries

If no reusable patterns were surfaced by the critique, this step is skipped entirely.

**Output:** Updated CLAUDE.md (if insights were memorized) or no output (if skipped)
**Failure mode:** (1) Over-memorization: storing trivial or context-specific findings as general principles. Recovery: the memorize command should filter for novelty and generalizability. (2) CLAUDE.md/MEMORY.md at capacity. Recovery: suggest archiving stale entries or moving detailed content to topic-specific files (as already recommended in MEMORY.md header). (3) Duplicate memorization. Recovery: the memorize command should deduplicate against existing content.
**Principle alignment:** P9 (Continuous Memorization) -- curate reusable insights from critique into persistent knowledge.
**Critique level:** Memorize (`/reflexion:memorize`) -- RATIONALE: This is not a scrutiny gate but a knowledge capture gate. The cost is minimal (single pass to extract and deduplicate insights). It runs conditionally -- only when the critique surfaces patterns that are both novel and generalizable. The cost of skipping is knowledge loss; the cost of running is minimal.
**User selection:** no -- memorization is automated but the user can review CLAUDE.md changes in a subsequent session

---

### Step 7: User Review & Refinement

**Type:** user decision
**Actor:** human
**Command:** N/A (interactive review loop)
**Input:** Refined issue specification from Step 5
**Action:** The user reviews the draft issue specification produced by Step 5. This step runs in **parallel** with Step 6 (Conditional Memorization) -- both start as soon as Step 5 completes.

The review is an iterative loop:

1. Claude presents the refined specification to the user
2. User reviews and provides feedback (additions, corrections, scope changes, wording improvements)
3. Claude incorporates the feedback and presents the updated specification
4. Repeat until the user approves the specification

The user may approve immediately (zero iterations) or request multiple rounds of refinement. There is no limit on iterations -- this is a user decision point, not an automated process.

**Output:** Approved issue specification (final, ready for GitHub issue creation)
**Failure mode:** (1) User requests changes that contradict the critique findings from Step 3. Recovery: flag the contradiction, explain which critique finding is affected, and ask for confirmation. The user has final authority. (2) User abandons the review. Recovery: the workflow cannot proceed to Step 8 without user approval -- the specification remains in draft state.
**Principle alignment:** P11 (User-in-the-Loop Quality Gates) -- explicit user approval before creating the GitHub issue.
**Critique level:** N/A (not a quality gate -- this is a user decision point)
**User selection:** yes -- user reviews, provides feedback, and explicitly approves the final specification

---

### Step 8: Create GitHub Issue

**Type:** automated
**Actor:** sub-agent (via `gh` CLI)
**Command:** `gh issue create`
**Input:** Approved issue specification from Step 7, plus the matching GitHub issue template name
**Action:** Create the GitHub issue using the `gh` CLI with the following structure:

- **Title:** Conventional format (`feat(scope): description` or `fix(scope): description`)
- **Body:** Full specification conforming to the GitHub issue template, including description, acceptance criteria, scope, affected components, risk assessment, and complexity estimate
- **Labels:** Applied based on issue type (`enhancement` for features, `bug` for bugs) plus scope-based labels (e.g., `engine`, `dashboard`, `ml`, `infrastructure`)
- **Template:** Use `--template` flag if the `gh` CLI supports it, otherwise ensure the body structure matches the template
- **Assignees:** None (solo developer project)
- **Milestone:** If applicable, assign to the current development milestone

The `gh issue create` command is executed against the `${REPO}` repository.

This is the FINAL step that produces a persistent artifact. The GitHub issue is the sole tracker of the planned work -- no README, ROADMAP, or docs/plans updates are made.

**Output:** GitHub issue URL (e.g., `https://github.com/${REPO}/issues/NNN`)
**Failure mode:** (1) `gh` CLI authentication failure. Recovery: verify the GitHub PAT is valid and has Issues (r/w) scope. The current fine-grained PAT has this scope. (2) Network failure. Recovery: retry once after 5 seconds; if still failing, save the issue specification locally and instruct the user to create it manually. (3) Rate limiting by GitHub API. Recovery: wait and retry with exponential backoff.
**Principle alignment:** P12 (Conventional Commit Discipline) -- issue title follows conventional format, enabling future automation.
**Critique level:** N/A (not a quality gate)
**User selection:** no -- automated, but the user already approved the specification in Step 7

---

### Step 9: Workflow Completion Summary

**Type:** automated
**Actor:** sub-agent
**Command:** N/A
**Input:** All outputs from previous steps: issue URL, memorized insights (if any), selected/rejected critique findings
**Action:** Present a completion summary to the user including:

- GitHub issue URL with clickable link
- Summary of critique findings: N applied, M rejected (if any), K memorized
- Any open questions or follow-up actions identified during the workflow
- Explicit termination notice: "WF1 complete. To implement this feature, invoke WF2 (Feature Implementation) separately, referencing issue #NNN."

The completion summary does NOT suggest auto-transitioning to WF2. The workflow terminates here.

**Output:** Completion summary message
**Failure mode:** None -- this is an informational step. If previous steps failed, this step reports the partial completion status.
**Principle alignment:** P11 (User-in-the-Loop) -- clear termination gives the user control over when (and whether) to start implementation.
**Critique level:** N/A (not a quality gate)
**User selection:** no -- informational output only. WF1 terminates.

---

## Quality Gate Summary

| Gate                   | Step   | Command                             | Rationale                                                                                                                                                                                                                                                                                                                                                                         | User Selection                                                             |
| ---------------------- | ------ | ----------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------- |
| Ideation Gate          | Step 3 | `/reflexion:critique` (full)        | This is the cheapest point to catch bad ideas. No code, no branch, no tests exist yet. The cost of change is zero. A full 3-judge debate is justified because brainstorm output may contain hallucinated component references, incomplete acceptance criteria, or scope that conflicts with existing architecture. Catching these here saves hours of wasted implementation time. | Findings auto-applied in Step 4; circuit breaker ALWAYS stops on ambiguity |
| Knowledge Capture Gate | Step 6 | `/reflexion:memorize` (conditional) | After the critique debate, reusable insights may have surfaced (new anti-patterns, undocumented conventions, recurring architecture constraints). Memorizing these is cheap (single pass, deduplicated) and prevents re-discovery in future sessions. Conditional -- only fires if novel, generalizable insights exist.                                                           | No -- automated knowledge capture                                          |

**Note on `/reflexion:reflect`:** The user explicitly decided that `/reflexion:critique` is the ONLY quality gate needed for issue creation. A reflect step was considered (see v1 Open Question #1) but rejected. The rationale: issue creation is a low-stakes ideation activity where full critique at Step 3 provides sufficient scrutiny, and adding a reflect step between Steps 5 and 8 would add latency without proportional benefit. The reflect gate is reserved for higher-stakes workflows (WF2) where code has been written and drift detection matters.

---

## Principle Violation Analysis

The workflow was validated against all 14 Phase 2 principles. Findings:

### Principles Fully Enforced

- **P8 (Shift-Left Critique):** Full critique at ideation gate (Step 3) -- correct level per the gate placement matrix.
- **P9 (Continuous Memorization):** Conditional memorize after critique (Step 6) -- per the principle's requirement.
- **P11 (User-in-the-Loop):** Circuit breaker in Step 4 escalates to user on ambiguity; user invokes `/create-issue` to start workflow; user provides intent in Step 1.
- **P12 (Conventional Commit Discipline):** Issue titles follow conventional format -- enables downstream automation in WF2.

### Principles Partially Applicable

- **P10 (Diagram-Driven Design):** This workflow itself is diagrammed. However, the issue being created may describe a feature that requires architectural diagrams -- the brainstorm step (Step 2) should flag when the feature warrants a diagram as part of its design phase.
- **P1 (Branch Isolation):** Not directly applicable -- no branch is created in this workflow. Branch creation happens in WF2 (Feature Implementation) when the issue is picked up for implementation.

### Principles Not Applicable to This Workflow

- **P2 (Code Formatting):** No code is written.
- **P3 (Frequent Local Commits):** No code changes to commit.
- **P4 (Regular Remote Sync):** No branch to push.
- **P5 (TDD Enforcement):** No tests to run.
- **P6 (Main-to-Dev Sync):** No merge to main.
- **P7 (Triple-Gate Testing):** No PR to gate.
- **P13 (Pre-PR Code Review):** No PR.
- **P14 (Documentation-Gated PRs):** No PR, and the GitHub issue IS the documentation artifact. README/ROADMAP updates are explicitly excluded by user decision.

### Gaps Identified and Resolved

1. **No `/reflexion:reflect` gate:** RESOLVED -- user decision: critique is sufficient for issue creation. Reflect is reserved for WF2.
2. **No explicit Excalidraw diagram review point:** Acceptable for this process-oriented workflow. Architecture-oriented workflows (WF2) should include diagram review points.

---

## Data Flow Diagram (Text Representation)

```
[User Intent]
     |
     v
Step 1: Receive User Intent ---- type: feature|bug, raw_description
     |
     v
Step 2: /superpowers:brainstorm ---- Draft Issue Specification (template-conformant)
     |
     v
Step 3: /reflexion:critique ---- Prioritized Findings (Critical/High/Medium/Low + ambiguity flags)
     |                              |
     |  [VOLUME CHECK: >5 Critical OR >5 High OR >10 Medium OR >10 Low?]
     |                              |
     |                     YES: loop back to Step 1 (max 2 loops, then escalate)
     |                              |
     v                             NO: proceed
Step 4: Apply Findings ---- Amendment List (default: apply all)
     |                       [CIRCUIT BREAKER: stop if ambiguous (ALWAYS active)]
     |                       Clear path: proceed immediately
     |                       Blocked path: STOP → user resolves → RESUME → proceed
     v
Step 5: Incorporate Amendments ---- Refined Issue Specification (draft)
     |                                    |
     v                                    v
Step 6: /reflexion:memorize        Step 7: User Review & Refinement
  (conditional, parallel)            (parallel, iterative loop)
     |                                    |
     v                                    v
Updated CLAUDE.md                  Approved Issue Specification
  (if insights found)                    |
                                         v
                                   Step 8: gh issue create
                                         |
                                         v
                                   Step 9: Completion Summary
                                         |
                                         v
                                   [WF1 TERMINATES]
                                   (WF2 requires explicit invocation)
```

---

## Excalidraw Diagram

The Excalidraw diagram is saved to `diagrams/workflow-issue-creation.excalidraw`.

**Color coding applied:**

- **Blue (#4A90D9):** Automated steps -- Steps 2, 4, 5, 8, 9
- **Green (#7BC67E):** User decision points -- Steps 1, 7
- **Yellow (#F5A623):** Quality gates -- Steps 3, 6
- **Red (#D0021B):** Failure/rollback paths -- annotated on Steps 3 and 8
- **Purple (#9B59B6):** Diagram review points -- none in this workflow (acceptable for process-oriented workflow)

**Data flow annotations:**

- Arrows between nodes show what artifact moves between steps
- Each node annotated with the principle(s) it enforces
- Step 4 includes the ambiguity circuit breaker annotation
- Terminal node explicitly shows WF1 termination (no WF2 auto-transition)

---

## Open Questions (All Resolved)

### Q1: Missing `/reflexion:reflect` gate -- RESOLVED

**Decision:** Keep `/reflexion:critique` as the only quality gate. Do NOT add a `/reflexion:reflect` step. Critique is sufficient for issue creation -- it is a low-stakes ideation activity. Reflect is reserved for WF2 where code drift matters.

### Q2: README vs. ROADMAP vs. GitHub Issue as tracker -- RESOLVED

**Decision:** GitHub issue is the SOLE tracker of work. No README TODO, no ROADMAP.md, no docs/plans entries. Step 8 (Update README TODO) from v1 has been removed entirely.

### Q3: Issue templates -- RESOLVED

**Decision:** YES, create GitHub issue templates. `.github/ISSUE_TEMPLATE/` should contain templates for both feature requests and bug reports. The brainstorm output (Step 2) and final issue (Step 8) must conform to these templates. Template sections are defined in `.claude/framework-build/templates/needed-templates.md`.

### Q4: Attended/unattended mode -- RESOLVED (then SUPERSEDED by Q7)

**Original decision (v2):** Two modes with circuit breaker only in unattended. **Superseded by Q7** — single mode, circuit breaker always active.

### Q5: WF1 to WF2 transition -- RESOLVED

**Decision:** WF1 ALWAYS terminates after issue creation. WF2 requires explicit invocation. No auto-transition, no "and implement it" shortcut. Clean separation of concerns.

### Q6: Critique finding volume thresholds -- RESOLVED

**Decision:** The original design had an arbitrary hard cap of 20+ findings with a 5/5/5 per-tier max. These numbers were invented by the workflow-design-agent with no empirical basis. Replaced with a volume-triggered loop-back: if critique produces >5 Critical findings OR >5 High findings, OR >10 Medium findings OR >10 Low findings (independent per tier, not combined), the workflow loops back to Step 1 for requirements clarification. The rationale: this many findings signals that the initial requirements gathering was insufficient -- the problem is upstream (vague/ambiguous intent), not in the critique. The loop-back has Claude explicitly work through ALL findings as clarifying questions before re-running brainstorm. **v2.3 addition:** Loop-guard added — maximum 2 loop-back iterations before escalating to user.

### Q7: Remove attended/unattended distinction -- RESOLVED

**Decision:** The attended/unattended mode distinction is removed entirely. There is one mode: apply all findings, with the ambiguity circuit breaker ALWAYS active. The previous design exempted "attended mode" from the circuit breaker on the theory that human oversight would catch ambiguity in real time. This is insufficient — "the user is watching" is not a safety mechanism. Ambiguous findings must always be explicitly flagged and resolved, regardless of whether a human is monitoring.

### Q8: WF1 invocation mechanism -- RESOLVED

**Decision:** WF1 is invoked via the `/create-issue` custom skill. This is a Claude Code skill that orchestrates the full 9-step flow. No natural language detection, no auto-trigger. The user explicitly invokes `/create-issue` to start the workflow.

### Q9: Step 5 type contradiction (v2.3) -- RESOLVED

**Decision:** Step 5 was typed as "automated" but had "User selection: yes" — a contradiction flagged by all 3 critique judges. Resolution: Step 5 is now purely automated (incorporate amendments only). User review is extracted into a new Step 7 (User Review & Refinement), which runs in parallel with Step 6 (Memorize). This adds 1 step (8 → 9 total) but eliminates the type contradiction and gives user review its own explicit step.

### Q10: Loop-guard for volume-triggered loop-back (v2.3) -- RESOLVED

**Decision:** The volume-triggered loop-back in Step 3 had no upper bound — infinite loops were theoretically possible. Resolution: maximum 2 loop-back iterations. After 2 loops without threshold improvement, escalate to user with the full finding list. The constant `MAX_LOOPBACK_ITERATIONS = 2` will be configurable in the `/create-issue` skill file.

### Q11: Volume threshold wording ambiguity (v2.3) -- RESOLVED

**Decision:** The original wording ">5 findings in Critical or High" was ambiguous — could mean >5 combined or >5 in each. Resolution: explicitly ">5 Critical findings OR >5 High findings" — independent per tier, not combined. Same for Medium/Low: ">10 Medium findings OR >10 Low findings".

---

## Plugin Interface Contracts

**Deferred to implementation-spec-agent.** The exact interface contracts between WF1 steps (function signatures, data structures, error handling protocols) will be defined during the implementation specification phase, not in this design document. This design focuses on _what_ each step does and _what data flows between them_, while the implementation spec will define _how_ the data is structured and passed.

---

## Revision History

| Version | Date       | Changes                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| ------- | ---------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| v1      | 2026-03-01 | Initial workflow design with 9 steps                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| v2      | 2026-03-01 | Removed Step 8 (README TODO) per user decision. Updated Step 4 to default-apply-all with ambiguity circuit breaker. Added issue template conformance to Steps 2 and 7. Resolved all 5 open questions. Renumbered to 8 steps. Updated diagram. Explicit WF1 termination.                                                                                                                                                                                                                                                                     |
| v2.1    | 2026-03-01 | Changed finding tiers from 3 (Must Do/Should Do/Could Do) to 4 severity levels (Critical/High/Medium/Low). Replaced arbitrary hard cap (20+ / 5-5-5) with volume-triggered loop-back: >5 Critical or High OR >10 Medium or Low triggers return to Step 1 for requirements clarification. Rationale: high finding volume indicates a requirements gathering problem, not a critique problem.                                                                                                                                                 |
| v2.2    | 2026-03-01 | Added `/create-issue` as invocation mechanism. Removed attended/unattended mode distinction — single mode with circuit breaker ALWAYS active. Rationale: "user is watching" is not a safety mechanism; ambiguity must always be flagged explicitly. Step 4 simplified from dual-mode to single automated step with circuit breaker escalation.                                                                                                                                                                                              |
| v2.3    | 2026-03-01 | Critique fixes from 3-judge review: (1) New Step 7 User Review & Refinement — extracted user review from Step 5, runs parallel with Step 6, 9 steps total. (2) Loop-guard: max 2 loop-backs in Step 3 before escalating to user. (3) Volume threshold wording clarified: >5 Critical OR >5 High (independent per tier, not combined). (4) Template needs document created. (5) Step 4 circuit breaker dual-path output documented. (6) Diagram title updated to v2.3. (7) Plugin interface contracts deferred to implementation-spec-agent. |
