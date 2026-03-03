# Phase 3 Workflow: Documentation Update (v1.0)

**Date:** 2026-03-02
**Author:** Orchestrator (direct authoring)
**Inputs:** phase2-principles.md, phase2b-official-comparison.md
**Purpose:** Define the workflow for updating, creating, or restructuring project documentation without code changes. WF7 covers CLAUDE.md maintenance, session notes restructuring, API docs, architecture docs, and README updates.

---

## Workflow: Documentation Update

**Invocation:** `/update-docs <scope>` skill (custom Claude Code skill)
**Trigger:** User invokes `/update-docs "update CLAUDE.md testing section"` or `/update-docs <issue-number>`
**Inputs:**

- Documentation scope description (free text) or GitHub issue number
- Existing documentation files (CLAUDE.md, MEMORY.md, README, session notes)
- Codebase context (for accuracy verification)

**Outputs:**

- Merged PR with documentation changes
- Updated/restructured documentation files
- Deployed changes to dev environment

**Tracking:** GitHub issue (if exists) or session notes.

**Principles enforced:**

- P1: Branch Isolation (docs/ branch)
- P2: Code Formatting (Prettier for markdown if configured)
- P3: Frequent Local Commits
- P4: Regular Remote Sync
- P8: Shift-Left Critique (reflect on accuracy)
- P9: Continuous Memorization (if doc restructuring reveals patterns)
- P11: User-in-the-Loop (user reviews doc changes)
- P12: Conventional Commit Discipline
- P14: Documentation-Gated PRs

**Principles NOT applicable:**

- P5 (TDD): No code to test
- P7 (Triple-Gate): No code gates -- documentation correctness is verified by accuracy check and user review
- P10 (Diagram-Driven): Docs may include diagrams, but no mandatory architecture diagrams

**Diagram:** `diagrams/workflow-documentation.excalidraw`

**Termination:** WF7 terminates after merge and deployment. No auto-transition.

---

## Documentation Categories

| Category        | Scope                     | Review Level               | Example                            |
| --------------- | ------------------------- | -------------------------- | ---------------------------------- |
| **Correction**  | Fix inaccurate docs       | Lightweight reflect        | Fix wrong port number in CLAUDE.md |
| **Addition**    | Add missing documentation | Full user review           | Document new API endpoints         |
| **Restructure** | Reorganize doc structure  | Full user review + reflect | Split MEMORY.md into topic files   |
| **Generation**  | Create new doc from code  | Full user review + reflect | Generate API reference from routes |

---

## Accuracy-First Principle

> **Note:** Accuracy-first is a WF7-specific workflow principle, not a framework-level principle. It is applied through P8 (Shift-Left Critique) -- by shifting accuracy verification before implementation (Step 2 audit + Step 6 reflect), WF7 catches inaccuracies early rather than after merge.

Documentation changes are unique because **inaccurate docs are worse than no docs**. WF7 enforces accuracy verification:

1. Every factual claim in new/updated docs must be verified against actual code
2. Port numbers, file paths, command syntax, and config values must be grep-verified
3. Architecture descriptions must match current codebase (not aspirational state)
4. Example commands must be tested (run them and verify output)

---

## Finding Application and Ambiguity Circuit Breaker

**Inherited from WF2:** Apply findings automatically. Circuit breaker on ambiguity (especially important for docs -- ambiguous documentation is worse than none).

---

## Global Loop-Back Budget

- Step 6 (user review) -> Step 3: no fixed limit (user controls iteration on docs)
- **No global cap for user-driven feedback loops.** Documentation quality is subjective -- the user iterates until satisfied. However, if 3+ rounds of feedback reveal scope creep, suggest splitting into multiple PRs.

---

## Workflow Resumption

**Checkpoint artifacts:**

| Artifact      | Location                       | Created at   | Purpose      |
| ------------- | ------------------------------ | ------------ | ------------ |
| GitHub issue  | GitHub (remote)                | Before WF7   | Scope        |
| Docs branch   | Git (remote)                   | Step 4       | Change state |
| Draft docs    | Git (committed)                | Step 5       | Content      |
| PR            | GitHub (remote)                | Step 7       | Review state |
| Session notes | `claude_docs/session_notes.md` | Continuously | Progress log |

**Step detection on resume:**

1. PR merged? -> Step 10
2. PR exists and CI passed? -> Step 9
3. PR exists? -> Step 8
4. Docs branch has user-approved changes? -> Step 7
5. Docs branch has committed changes? -> Step 6
6. Docs branch exists (empty)? -> Step 4
7. Audit report in session notes? -> Step 3
8. None -> Step 1

---

## Steps

### Step 1: Receive Documentation Scope

**Type:** user decision
**Actor:** human
**Command:** `/update-docs <scope>` or `/update-docs <issue-number>`
**Input:** Description of what docs to update, or issue number
**Action:**

1. If issue: fetch via `gh issue view` and display
2. Clarify scope: which files, what kind of change (correction/addition/restructure/generation)
3. Confirm with user

**Output:** Validated scope: { description, files, category, issue_number (optional) }
**Principle alignment:** P11

---

### Step 2: Audit Current Documentation

**Type:** automated
**Actor:** Claude (Read + Grep + Serena)
**Input:** Scope from Step 1, current docs, codebase
**Action:**

1. Read all affected documentation files
2. Cross-reference claims against codebase:
   - File paths -> `ls` / Glob
   - Port numbers -> grep .env files and docker-compose
   - Command syntax -> grep scripts and package.json
   - Architecture claims -> Serena symbol overview
3. Identify inaccuracies, gaps, and outdated information
4. For Generation category: map code structure to document outline

**Output:** Audit report: { inaccuracies, gaps, outdated_sections, proposed_structure }
**Failure mode:** (1) Code and docs disagree -- code is authoritative (fix docs, not code). (2) Cannot determine correct value -- ask user.

---

### Step 3: Draft Documentation Changes

**Type:** automated
**Actor:** Claude
**Input:** Audit report from Step 2, scope from Step 1
**Action:**

1. Draft all documentation changes
2. For each factual claim, include verification source (file:line or command output)
3. For code examples, test them (run commands, verify output matches)
4. Follow existing documentation style (CLAUDE.md conventions, markdown formatting)
5. For SMB-shared docs: ensure the appropriate user has r/w access (per CLAUDE.md rule)

**Output:** Draft documentation (in-memory, not yet committed)
**Failure mode:** (1) Example commands fail -- investigate and fix the example or note the issue. (2) Documentation style is inconsistent across files -- propose standardization.

---

### Step 4: Create Documentation Branch

**Type:** automated
**Actor:** Claude
**Command:** `git checkout -b docs/<scope-desc>`
**Input:** Branch name derived from scope
**Action:**

1. `git fetch origin main`
2. `git checkout -b docs/<scope-desc> origin/main`

**Output:** Active docs branch
**Principle alignment:** P1

---

### Step 5: Apply Changes and Verify

**Type:** automated
**Actor:** Claude
**Input:** Draft from Step 3
**Action:**

1. Apply documentation changes to files
2. Run accuracy verification:
   - All file paths mentioned exist
   - All port numbers match .env/docker-compose
   - All command examples are syntactically valid
   - All cross-references (links between docs) are valid
3. Commit: `docs(scope): description`

**Output:** Documentation changes committed on branch
**Principle alignment:** P3, P12

---

### Step 6: Quality Gate -- Accuracy Reflect + User Review

**Type:** quality gate
**Actor:** Claude + human
**Command:** `/reflexion:reflect` + user review
**Input:** Documentation changes on branch
**Action:**

1. **Reflect:** Single-pass verification:
   - Are all factual claims backed by code evidence?
   - Is the documentation consistent with itself (no contradictions)?
   - Is the reading order logical?
   - Are there any claims that cannot be verified?
2. **User review:** Present changes to user for review. Documentation is subjective -- the user must approve tone, completeness, and accuracy.
3. **Apply feedback:** Iterate on user feedback until approved.

**Output:** User-approved documentation
**Failure mode:** (1) User has extensive feedback -> iterate (no loop limit for docs, user controls). (2) Reflect finds unverifiable claims -> flag for user.
**Principle alignment:** P8 (Shift-Left), P11 (User-in-the-Loop)

---

### Step 7: Create Pull Request

**Type:** automated
**Actor:** Claude
**Command:** `/commit-commands:commit-push-pr`
**Input:** Approved documentation on branch
**Action:**

1. Push: `git push -u origin docs/<scope-desc>`
2. Create PR:
   - Title: `docs(scope): description`
   - Body: What changed, why, verification evidence
   - Labels: documentation

**Output:** PR URL
**Principle alignment:** P12, P14

---

### Step 8: CI Verification

**Type:** automated
**Actor:** GitHub Actions
**Input:** PR from Step 7
**Action:** CI should pass (documentation changes don't break tests). If CI fails, investigate -- a docs-only PR failing CI indicates a pre-existing issue or misconfigured CI.

**Output:** CI pass
**Principle alignment:** P7 (Gate 2 -- even docs PRs run CI)

---

### Step 9: Merge and Deploy

**Type:** automated
**Actor:** Claude
**Command:** `gh pr merge --squash` + deploy script
**Input:** CI-passing PR
**Action:**

1. Squash-merge
2. Deploy (docs are part of the repo, so deploy syncs them)
3. Verify SMB access if applicable

**Output:** Merged and deployed
**Principle alignment:** P6

---

### Step 10: Completion Summary

**Type:** automated
**Actor:** Claude
**Input:** All artifacts
**Action:**

1. Update session notes
2. Close GitHub issue if exists
3. Present summary: what was updated, verification status, PR link

**Output:** Session notes updated
**Principle alignment:** P14

---

## Design Decisions

### D1: 10 Steps (Lightest Workflow)

**Rationale:** Documentation changes don't require TDD, complex design gates, or code review agents. The critical gate is accuracy verification + user review. 10 steps is appropriate.

### D2: User Review is Mandatory

**Rationale:** Unlike code (where tests prove correctness), documentation correctness is partly subjective. The user must review docs for accuracy, tone, and completeness. This is the primary quality gate.

### D3: No Code Review Agents

**Rationale:** Code review agents (type-design, silent-failure, etc.) are irrelevant for documentation. The accuracy reflect + user review replaces them.

### D4: Accuracy Verification Over Style

**Rationale:** An ugly but accurate doc is better than a polished but incorrect one. WF7 prioritizes fact-checking over formatting, though both are addressed.

### D5: CI Still Runs

**Rationale:** Docs-only PRs should still run CI to catch: (a) YAML frontmatter errors, (b) broken markdown that affects tooling, (c) pre-existing CI failures that should be flagged. The CI gate is lightweight but ensures the main branch stays clean.

---

## Principle Coverage Matrix

| Principle                | Enforced    | How                                 |
| ------------------------ | ----------- | ----------------------------------- |
| P1 Branch Isolation      | Yes         | Step 4                              |
| P2 Code Formatting       | Partial     | Prettier for markdown if configured |
| P3 Frequent Commits      | Yes         | Step 5                              |
| P4 Remote Sync           | Yes         | Step 7                              |
| P5 TDD Enforcement       | N/A         | No code changes                     |
| P6 Main-to-Dev Sync      | Yes         | Step 9                              |
| P7 Triple-Gate           | Partial     | CI only (no code gates)             |
| P8 Shift-Left Critique   | Yes         | Step 6 (accuracy reflect)           |
| P9 Memorization          | Conditional | Only for restructuring              |
| P10 Diagram-Driven       | N/A         |                                     |
| P11 User-in-the-Loop     | Yes         | Steps 1, 6                          |
| P12 Conventional Commits | Yes         | Steps 5, 7                          |
| P13 Pre-PR Review        | N/A         | User review replaces                |
| P14 Documentation-Gated  | Yes         | Core purpose                        |
