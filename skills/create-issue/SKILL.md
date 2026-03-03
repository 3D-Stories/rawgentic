---
name: rawgentic:create-issue
description: Create a GitHub issue (feature request or bug report) using the WF1 9-step workflow with multi-agent critique, ambiguity circuit breaker, and user review. Invoke with /create-issue followed by a description of the desired feature or observed bug.
argument-hint: Description of the feature to request or bug to report
---

# WF1: Issue Creation Workflow (v1.0)

<role>
You are the WF1 orchestrator implementing a 9-step issue creation workflow. You guide the user from raw intent through brainstorming, multi-agent critique, ambiguity resolution, user review, and GitHub issue creation. You enforce quality gates at each step and NEVER auto-transition to WF2 (Feature Implementation).
</role>

<constants>
MAX_LOOPBACK_ITERATIONS = 2
VOLUME_THRESHOLDS:
  Critical: 5
  High: 5
  Medium: 10
  Low: 10
REPO = "<inferred from `git remote -v` at workflow start>"
PROJECT_ROOT = "<inferred from `git rev-parse --show-toplevel`>"
TEMPLATE_DIR = ".github/ISSUE_TEMPLATE"
</constants>

<environment-setup>
Constants are populated at workflow start (Step 1) by running:
- `REPO`: `git remote get-url origin | sed 's|.*github.com[:/]||;s|\.git$||'`
- `PROJECT_ROOT`: `git rev-parse --show-toplevel`

If any constant cannot be resolved, STOP and ask the user. Do not assume values.
</environment-setup>

<termination-rule>
WF1 ALWAYS terminates after issue creation. WF2 (Feature Implementation) requires explicit, separate invocation. There is NO auto-transition from WF1 to WF2 under ANY circumstance. Do not suggest "shall I implement this?" or "would you like to start WF2?"
</termination-rule>

<context-compaction>
Per CLAUDE.md shared invariant #9: before context compaction, document in `claude_docs/session_notes.md`: current step number, loop-back iteration count, circuit breaker state (clear/blocked), issue type (feature/bug), and critique findings summary if past Step 3.
</context-compaction>

<ambiguity-circuit-breaker>
Per CLAUDE.md shared invariant #1: STOP and ask the user when critique findings are ambiguous, conflicting, or require judgment calls not covered by the original description. This circuit breaker fires in Step 4 but the principle applies throughout the workflow — if ANY step encounters ambiguity that could lead to a wrong outcome, STOP and ask rather than guess.
</ambiguity-circuit-breaker>

## Step 1: Receive User Intent

### Instructions

1. Acknowledge the user's request.
2. Classify the intent as "feature" or "bug" based on the description provided.
3. If classification is ambiguous, ask: "Is this a feature request (new functionality) or a bug report (existing behavior that is broken)?"
4. Check for sufficient information to generate meaningful acceptance criteria. If insufficient, ask targeted clarifying questions:
   - For features: "What is the desired behavior? Which part of the system is affected? What problem does this solve?"
   - For bugs: "What is the expected behavior? What is the actual behavior? Can you reproduce it? Which VM/container is affected?"
5. Run a deduplication check:
   ```bash
   gh issue list --repo ${REPO} --search "<keywords from description>" --limit 10
   ```
6. If potential duplicates found, present them to the user and ask: "Any of these existing issues cover your request?"
7. Update `claude_docs/session_notes.md` with: issue description, classification (feature/bug), initial scope hints, duplicate check results.
8. Confirm classification with the user before proceeding.

### Output Format

Present to user:

```
Issue Classification:
- Type: [feature / bug]
- Summary: [one-sentence summary of the intent]
- Scope hints: [components/systems mentioned]
- Existing issues found: [list or "none"]

Proceeding to brainstorm. Confirm or correct this classification.
```

Wait for user confirmation before proceeding to Step 2.

#### Failure Modes

- User provides insufficient information → ask targeted clarifying questions (expected behavior, actual behavior, affected system area). Do not proceed until intent is clear enough to generate meaningful acceptance criteria.
- Classification ambiguous (could be feature or bug) → ask explicitly: "Is this a feature request or a bug report?"
- Dedup check finds a matching issue → present to user and ask if it covers their request before proceeding

---

## Step 2: Brainstorm Feature/Bug Details

### Instructions

**Brainstorming approach:** For complex features (multi-component, architectural changes), invoke `superpowers:brainstorming` to run the full design pipeline (context exploration → clarifying questions → approach proposals → design validation). For simpler features or bug reports, proceed with inline brainstorming using the instructions below. The tooling audit recommends `superpowers:brainstorming` as the primary tool for Step 2.

1. Read the matching GitHub issue template:
   - Feature: `${PROJECT_ROOT}/.github/ISSUE_TEMPLATE/feature_request.md`
   - Bug: `${PROJECT_ROOT}/.github/ISSUE_TEMPLATE/bug_report.md`

2. Read codebase context:
   - CLAUDE.md (architecture, code quality standards, known pitfalls)
   - MEMORY.md (project memory, infrastructure context)
   - Relevant source files identified from scope hints

3. Use Serena MCP (`find_symbol`, `get_symbols_overview`) to verify that any components, files, or symbols referenced in the brainstorm actually exist in the codebase.

4. Generate a comprehensive draft issue specification that MUST conform to the template structure. Include:
   - **Title:** Conventional format (`feat(scope): description` or `fix(scope): description`)
   - **Description:** Detailed with context and motivation
   - **Acceptance criteria:** Numbered, testable, specific (minimum 3 criteria)
   - **Scope:** Explicit in-scope AND out-of-scope lists
   - **Affected components:** Verified against actual codebase (use Serena)
   - **Risk assessment:** Dependencies, blockers, security implications
   - **Complexity:** T-shirt size (S/M/L/XL) with justification
   - **Related issues:** Cross-reference from dedup search

   For bugs, additionally include:
   - Steps to reproduce
   - Expected vs. actual behavior
   - Environment details
   - Error logs (if available)

5. Optionally, if the feature is complex and would benefit from divergent thinking, use the sdd:create-ideas technique: generate 6 approaches (3 high-probability, 3 tail-of-distribution) before selecting the best approach for the specification.

6. If the feature involves a library or framework API, use Context7 MCP (`resolve-library-id` → `query-docs`) to fetch up-to-date documentation before finalizing the specification.

### Output

The draft specification is an internal working artifact. Do NOT present it to the user yet -- it goes directly to Step 3 for critique.

#### Failure Modes

- Brainstorm produces overly vague acceptance criteria → the critique step (Step 3) will catch this
- Brainstorm hallucinates non-existent components or files → the critique step verifies claims against actual codebase via Serena
- Brainstorm duplicates an existing issue → brainstorm should include a dedup check via `gh issue list --search`
- No issue template exists in `.github/ISSUE_TEMPLATE/` → create the template first, then proceed with brainstorm

---

## Step 3: Full Critique of Brainstorm Output

### Instructions

1. Launch all three judges in a single message with three parallel Agent tool calls (subagent_type="general-purpose"), each operating independently:

   **Judge 1: Requirements Validator**
   - Evaluate: completeness of acceptance criteria, scope clarity, edge case coverage, template conformance
   - Verify: all referenced components/files exist (use Serena MCP via `find_symbol`)
   - Check: deduplication against existing issues

   **Judge 2: Solution Architect**
   - Evaluate: feasibility, complexity estimate accuracy, hidden dependencies
   - Check: consistency with existing architecture decisions (documented in CLAUDE.md)
   - Assess: risk assessment completeness

   **Judge 3: Code Quality Reviewer**
   - Evaluate: acceptance criteria testability, scope boundaries, wording clarity
   - Check: alignment with code quality standards and security standards (documented in CLAUDE.md)
   - Verify: no hallucinated claims about codebase capabilities

2. Each judge produces findings with the following structure per finding:

   ```
   Finding #N:
   - Severity: Critical | High | Medium | Low
   - Category: completeness | accuracy | feasibility | consistency | deduplication | template_conformance
   - Description: [what the issue is]
   - Recommendation: [specific action to take]
   - Ambiguity flag: clear | ambiguous
   - Ambiguity reason: [why, if ambiguous]
   ```

3. Synthesize findings across all three judges. Conduct a debate round if judges disagree on a finding. Unresolved disagreements are flagged as ambiguous.

4. **Volume threshold check** (independent per tier, not combined):
   - More than 5 Critical findings: TRIGGER LOOP-BACK
   - More than 5 High findings: TRIGGER LOOP-BACK
   - More than 10 Medium findings: TRIGGER LOOP-BACK
   - More than 10 Low findings: TRIGGER LOOP-BACK

5. **If loop-back triggered:**
   - Check `loop_iteration` counter.
   - If `loop_iteration < MAX_LOOPBACK_ITERATIONS` (i.e., < 2):
     - Increment `loop_iteration`.
     - Present ALL findings to the user as targeted clarifying questions.
     - Work through each finding to tighten requirements.
     - Return to Step 2 (re-brainstorm with improved requirements).
   - If `loop_iteration >= MAX_LOOPBACK_ITERATIONS`:
     - STOP looping. Escalate to user:
       ```
       After 2 requirement refinement iterations, the critique still produces findings
       above volume thresholds. Here is the full finding list:
       [all findings]
       Please review and decide how to proceed:
       (a) Continue with current findings (proceed to Step 4)
       (b) Abandon this issue and start over
       (c) Provide additional clarification and retry
       ```
     - Wait for user decision before proceeding.

6. **If volume thresholds pass:** Proceed to Step 4 with the full findings list.

### Output

Prioritized findings list with severity tiers and ambiguity flags. This is NOT presented to the user directly -- it feeds into Step 4.

#### Failure Modes

- Critique finds zero issues (suspicious) → verify the critique sub-agents actually read the codebase context and that debate rounds occurred
- Finding volume exceeds thresholds → loop back to Step 2 for requirements clarification (max 2 iterations)
- Critique sub-agents disagree fundamentally → unresolved disagreements are flagged as ambiguous for user attention in Step 4

---

## Step 4: Apply Critique Findings (with Ambiguity Circuit Breaker)

### Instructions

This step implements the circuit breaker logic. It is prompt-level logic, not a separate tool.

1. **Scan all findings for ambiguity:**

   ```
   ambiguous_findings = [f for f in findings if f.ambiguity_flag == "ambiguous"]
   ```

2. **Check for pairwise conflicts:**
   For each pair of findings (finding_i, finding_j):
   - Do their recommendations contradict each other? (e.g., "narrow scope to X" vs. "add acceptance criteria for Y which is outside X")
   - If yes, add both to `conflicting_findings` list.

3. **Check for judgment-call findings:**
   For each finding:
   - Does applying it require information not present in the original user description or codebase context?
   - If yes, add to `judgment_findings` list.

4. **Circuit breaker evaluation:**

   **CLEAR PATH** (no ambiguity, no conflicts, no judgment calls):
   - Produce amendment list from ALL findings (Critical + High + Medium + Low).
   - Proceed directly to Step 5.
   - Brief notification to user: "Critique complete. N findings applied (X Critical, Y High, Z Medium, W Low). All clear -- no ambiguity detected."

   **BLOCKED PATH** (any ambiguity, conflict, or judgment call detected):
   - STOP the workflow.
   - Present the problematic findings to the user:

     ```
     CIRCUIT BREAKER TRIGGERED

     The following findings cannot be applied automatically:

     AMBIGUOUS FINDINGS:
     [list with ambiguity reasons]

     CONFLICTING FINDINGS:
     [list with conflict explanations]

     JUDGMENT-CALL FINDINGS:
     [list with explanations of what information is missing]

     Please resolve each item:
     - For ambiguous findings: clarify the intended interpretation
     - For conflicting findings: decide which finding takes priority
     - For judgment-call findings: provide the missing information

     You may also add your own amendments not raised by the critique.
     ```

   - Wait for user resolution.
   - If user rejects a Critical finding: warn that Critical findings address fundamental issues and ask for confirmation. User has final authority (P11).
   - After resolution: produce amendment list (all findings + user-added amendments).
   - Proceed to Step 5.

### Output

Amendment list with workflow_state ("clear" or "blocked_resolved").

#### Failure Modes

- User rejects a Critical finding → warn that Critical items address fundamental issues (wrong component references, security gaps) and ask for confirmation. User has final authority.
- Circuit breaker triggers on most runs → indicates the critique step is flagging too many ambiguous findings; tune ambiguity detection or improve brainstorm specificity
- User adds amendments that contradict original brainstorm intent → flag the contradiction and ask for clarification

---

## Step 5: Incorporate Amendments into Issue Specification

### Instructions

1. Take the original draft specification from Step 2 and the amendment list from Step 4.
2. For each amendment, apply the change:
   - `add_criterion`: Insert acceptance criterion in the appropriate section. Annotate: "[Added per critique finding #N]"
   - `fix_error`: Correct the error silently (wrong component name, wrong file path).
   - `adjust_scope`: Update both in-scope and out-of-scope sections.
   - `add_risk`: Append to risk assessment section.
   - `improve_wording`: Revise wording in the specified section.
   - `add_detail`: Add detail to the specified section.
3. After applying all amendments, verify:
   - No internal contradictions introduced (e.g., scope narrowed but new criteria added outside the narrowed scope).
   - Specification still conforms to the GitHub issue template structure.
   - Total length is reasonable (under 2000 words for a single issue). If over 2000 words, suggest splitting into multiple issues.
4. Produce the refined specification.

### Output

Refined issue specification (internal working artifact). Immediately proceeds to Steps 6 and 7 in parallel: Step 6 (memorization) runs as a background operation via the Agent tool with run_in_background=true, while Step 7 (user review) proceeds interactively in the foreground.

#### Failure Modes

- Amendment incorporation introduces internal contradictions (scope narrowed but new criteria added outside narrowed scope) → flag the contradiction to the user before proceeding
- Refined specification exceeds 2000 words → suggest splitting into multiple issues with cross-references

---

## Step 6: Conditional Memorization (runs in parallel with Step 7)

### Instructions

This step runs concurrently with Step 7 (User Review). It does NOT block Step 7.

1. Review the critique findings from Step 3. Identify findings that surface reusable insights -- patterns applicable beyond this specific issue:
   - Architecture constraints that future features must respect
   - Anti-patterns discovered during critique
   - Codebase conventions not yet documented in CLAUDE.md
   - Recurring pitfalls that should be added to verification checklists

2. If memorizable insights exist:
   - Follow the reflexion:memorize workflow (ACE pattern):
     a. Extract the insight from critique context.
     b. Check for duplication against existing CLAUDE.md and MEMORY.md content (read both files).
     c. If novel, append to the appropriate section of CLAUDE.md.
     d. If CLAUDE.md or MEMORY.md is at capacity, suggest archiving stale entries or moving detailed content to topic-specific files.
   - Do NOT store in mem0 unless the insight is cross-project (per memory system rules in CLAUDE.md). If the insight IS cross-project (infrastructure, deployment, API quirks), store in mem0 via `search_memory` (check for duplicates) then `add_memory`.

3. If no memorizable insights exist: skip this step entirely. No output.

4. Periodically (suggested: every 10 `/create-issue` invocations), consider running `claude-md-management:claude-md-improver` to optimize CLAUDE.md structure. This is NOT a per-run action.

### Output

Updated CLAUDE.md (if insights memorized) or no output (if skipped). Does not affect Step 7.

#### Failure Modes

- Over-memorization: storing trivial or context-specific findings as general principles → the memorize command should filter for novelty and generalizability
- CLAUDE.md/MEMORY.md at capacity → suggest archiving stale entries or moving detailed content to topic-specific files
- Duplicate memorization → the memorize command should deduplicate against existing content before appending

---

## Step 7: User Review & Refinement (runs in parallel with Step 6)

### Instructions

This step runs concurrently with Step 6 (Memorization). It does NOT wait for Step 6 to complete.

1. Present the refined specification to the user in a readable format:

   ```
   DRAFT ISSUE SPECIFICATION (Ready for Review)
   =============================================

   Title: [conventional format title]
   Type: [feature / bug]
   Labels: [label list]
   Complexity: [T-shirt size]

   --- DESCRIPTION ---
   [description text]

   --- ACCEPTANCE CRITERIA ---
   1. [criterion 1]
   2. [criterion 2]
   ...

   --- SCOPE ---
   In scope:
   - [item]
   Out of scope:
   - [item]

   --- AFFECTED COMPONENTS ---
   - [component]

   --- RISK ASSESSMENT ---
   - [risk]

   --- RELATED ISSUES ---
   - [issue ref]

   [Bug-only sections if applicable]

   =============================================
   Critique summary: N findings applied (X Critical, Y High, Z Medium, W Low)

   Review the specification above. Provide any changes, or type "approved" to proceed with issue creation.
   ```

2. If user provides feedback:
   - Incorporate the feedback into the specification.
   - Re-present the updated specification.
   - If user's feedback contradicts a critique finding from Step 3: flag the contradiction, explain which finding is affected, ask for confirmation. User has final authority.
   - Repeat until user approves.

3. If user types "approved" (or equivalent affirmation such as "looks good", "go ahead", "lgtm", "create it"):
   - Mark specification as approved.
   - Proceed to Step 8.

4. If user abandons (stops responding or says "cancel"):
   - The specification remains in draft state.
   - WF1 terminates without creating an issue.
   - Summary: "WF1 cancelled. No issue created. Draft specification is available in this conversation for future reference."

### Output

Approved specification (or cancellation). Step 8 only runs after explicit approval.

#### Failure Modes

- User requests changes that contradict critique findings from Step 3 → flag the contradiction, explain which critique finding is affected, and ask for confirmation. User has final authority.
- User abandons the review (stops responding or says "cancel") → workflow cannot proceed to Step 8 without approval; specification remains in draft state, WF1 terminates without creating an issue.

---

## Step 8: Create GitHub Issue

### Instructions

1. Render the approved specification into a markdown body string that conforms to the GitHub issue template structure.

2. Write the body to a temporary file to handle multi-line content and special characters:

   ```bash
   cat << 'ISSUE_BODY_EOF' > /tmp/wf1-issue-body.md
   [full markdown body]
   ISSUE_BODY_EOF
   ```

3. Determine labels:
   - Base label: `enhancement` for features, `bug` for bugs.
   - Scope labels: based on affected components (e.g., `engine`, `dashboard`, `ml`, `infrastructure`, `api`).
   - Only use labels that already exist in the repository. Check with:
     ```bash
     gh label list --repo ${REPO}
     ```
   - If a needed label does not exist, create it:
     ```bash
     gh label create "engine" --repo ${REPO} --description "Engine (Python/darwin)" --color "0E8A16"
     ```

4. Create the issue:

   ```bash
   gh issue create \
     --repo ${REPO} \
     --title "feat(engine): implement two-stage entry validation" \
     --body-file /tmp/wf1-issue-body.md \
     --label "enhancement" \
     --label "engine"
   ```

5. Capture the output URL (gh issue create prints the URL to stdout).

6. **Failure handling:**
   - Authentication failure: verify PAT with `gh auth status`. The fine-grained PAT has Issues (r/w) scope.
   - Network failure: retry once after 5 seconds. If still failing, save the specification to `${PROJECT_ROOT}/docs/plans/draft-issue-YYYY-MM-DD.md` and instruct the user to create manually.
   - Rate limiting: wait 60 seconds and retry.

7. Clean up temp file:
   ```bash
   rm -f /tmp/wf1-issue-body.md
   ```

### Output

GitHub issue URL (e.g., `https://github.com/${REPO}/issues/NNN`).

#### Failure Modes

- `gh` CLI authentication failure → verify PAT with `gh auth status`; the fine-grained PAT has Issues (r/w) scope
- Network failure → retry once after 5 seconds; if still failing, save the issue specification locally to `${PROJECT_ROOT}/docs/plans/draft-issue-YYYY-MM-DD.md` and instruct the user to create manually
- Rate limiting by GitHub API → wait 60 seconds and retry with exponential backoff

---

## Step 9: Workflow Completion Summary

### Instructions

1. Update `claude_docs/session_notes.md` with WF1 results: issue URL, type, critique findings summary, loop-back count, memorized insights.

2. Present a completion summary:

```
WF1 COMPLETE
=============

GitHub Issue: [URL] (Issue #NNN)
Type: [feature / bug]
Title: [title]

Critique Summary:
- Total findings: N
- Applied: N (X Critical, Y High, Z Medium, W Low)
- Ambiguity circuit breaker: [triggered / not triggered]
- Loop-backs: [0 / 1 / 2]
- Memorized insights: [N insights saved to CLAUDE.md / none]

User Review: [N iterations / approved immediately]

WF1 complete. To implement this feature, invoke WF2 (Feature Implementation) separately, referencing issue #NNN.
```

Do NOT suggest auto-transitioning to WF2. Do NOT ask "shall I start implementing?" The workflow terminates here.

### Output

Completion summary message. WF1 terminates.

#### Failure Modes

- None — this is an informational step. If previous steps failed, this step reports the partial completion status.

---

## Workflow Resumption

If this skill is invoked mid-conversation, detect the current state:

1. Was a GitHub issue just created? → Step 9 (summary only)
2. Was the spec approved by user? → Step 8 (create issue)
3. Is there a refined spec with critique findings applied? → Step 7 (user review)
4. Is there a critique findings list? → Step 4 (apply findings)
5. Is there a draft specification? → Step 3 (critique)
6. None of the above → Step 1 (start from scratch)

Announce the detected state before resuming: "Detected prior progress. Resuming at Step N."
