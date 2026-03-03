---
name: rawgentic:update-docs
description: Update, create, or restructure project documentation using the WF7 10-step workflow with accuracy verification, user review, and conventional commit PR. Invoke with /update-docs followed by a description of the documentation change or an issue number.
argument-hint: Description of docs to update (e.g., "update CLAUDE.md testing section") or issue number
---

# WF7: Documentation Update Workflow

<role>
You are the WF7 orchestrator implementing a 10-step documentation update workflow. You guide the user through scope validation, documentation audit, drafting, accuracy verification, user review, and PR creation. You enforce the accuracy-first principle: inaccurate docs are worse than no docs. Every factual claim must be verified against actual code.
</role>

<constants>
REPO = "<inferred from `git remote -v` at workflow start>"
PROJECT_ROOT = "<inferred from `git rev-parse --show-toplevel`>"
SMB_USER = "<from CLAUDE.md documentation section>"
COMPOSE_INFRA = "<from docker compose config>"
DOC_CATEGORIES:
  correction: Fix inaccurate docs (lightweight reflect)
  addition: Add missing documentation (full user review)
  restructure: Reorganize doc structure (full user review + reflect)
  generation: Create new doc from code (full user review + reflect)
BRANCH_PREFIX = "docs/"
</constants>

<environment-setup>
Constants are populated at workflow start (Step 1) by running:
- `REPO`: `git remote get-url origin | sed 's|.*github.com[:/]||;s|\.git$||'`
- `PROJECT_ROOT`: `git rev-parse --show-toplevel`
- `SMB_USER`: Read from CLAUDE.md documentation section
- `COMPOSE_INFRA`: Read from CLAUDE.md and docker compose config

If any constant cannot be resolved, STOP and ask the user. Do not assume values.
</environment-setup>

<accuracy-first-principle>
Documentation changes are unique because inaccurate docs are worse than no docs. WF7 enforces:
1. Every factual claim must be verified against actual code
2. Port numbers, file paths, command syntax, and config values must be grep-verified
3. Architecture descriptions must match current codebase (not aspirational state)
4. Example commands must be tested (run them and verify output)
Code is authoritative. If docs and code disagree, fix the docs, NOT the code (unless the code is buggy — then escalate to WF3).
</accuracy-first-principle>

<termination-rule>
WF7 terminates after merge and deployment. No auto-transition to other workflows.
</termination-rule>

<context-compaction>
Per CLAUDE.md shared invariant #9: before context compaction, document in `claude_docs/session_notes.md`: current step number, branch name, last commit SHA, documentation category, which files have been updated vs pending, and user review feedback state.
</context-compaction>

<ambiguity-circuit-breaker>
Per CLAUDE.md shared invariant #1: STOP and ask the user when documentation requirements are ambiguous, accuracy verification yields conflicting results (code vs existing docs disagree on behavior), or the scope of changes expands beyond the original request. The accuracy-first principle makes this especially critical — incorrect documentation is worse than missing documentation, so when in doubt, STOP and ask rather than guess.
</ambiguity-circuit-breaker>

## Step 1: Receive Documentation Scope

### Instructions

1. If argument is an issue number: fetch via `gh issue view <number> --repo ${REPO}` and display.
2. Clarify scope with the user:
   - Which files need updating?
   - What kind of change? Classify as: **correction** / **addition** / **restructure** / **generation**
   - For SMB-shared docs: ensure `${SMB_USER}` has r/w access (per CLAUDE.md rule)
3. Confirm scope with user before proceeding.
4. Update `claude_docs/session_notes.md` with: documentation scope, target files, accuracy audit plan.

### Output Format

Present to user:

```
Documentation Scope:
- Category: [correction / addition / restructure / generation]
- Files affected: [list]
- Summary: [one-sentence description of the change]
- Issue: [#NNN or "none"]

Proceeding to audit current documentation. Confirm or correct this scope.
```

Wait for user confirmation before proceeding to Step 2.

### Failure Modes

- Scope is actually a code change disguised as docs → redirect to WF2 or WF3
- User cannot specify which files need updating → suggest auditing CLAUDE.md and MEMORY.md as a starting point
- Issue references stale documentation without specifying what is wrong → ask user for specifics before proceeding

---

## Step 2: Audit Current Documentation

### Instructions

1. Read all affected documentation files.
2. Cross-reference factual claims against codebase:
   - File paths → Glob / `ls`
   - Port numbers → grep .env files and docker-compose YAML
   - Command syntax → grep scripts and package.json
   - Architecture claims → Serena MCP (`get_symbols_overview`, `find_symbol`)
   - Config values → grep source files for defaults
3. Identify inaccuracies, gaps, and outdated information.
4. For **generation** category: map code structure to document outline using Serena symbol overview.

### Output

Audit report (internal working artifact):

- Inaccuracies found (with correct values from code)
- Gaps identified (missing documentation)
- Outdated sections (stale information)
- Proposed structure (for generation/restructure)

Do NOT present the full audit to the user — it feeds into Step 3.

### Failure Modes

- Code and docs disagree on behavior → code is authoritative; fix the docs, not the code (unless code is buggy, then escalate to WF3)
- Cannot determine correct value from code → ask user for clarification
- Affected documentation files do not exist yet → classify as "generation" category and proceed to Step 3

---

## Step 3: Draft Documentation Changes

### Instructions

1. Draft all documentation changes based on audit report and scope.
2. For each factual claim, include verification source:
   - File path verified: `ls /path/to/file` or Glob match
   - Port number verified: `.env.dev:LINE_N` or `${COMPOSE_INFRA}:LINE_N`
   - Command verified: tested and output matches
3. For code examples, test them (run commands, verify output matches).
4. Follow existing documentation style:
   - CLAUDE.md conventions (heading levels, section ordering)
   - Markdown formatting (no unnecessary emojis)
   - Match existing indentation and list styles
5. For SMB-shared docs: note access requirements.

### Output

Draft documentation (in-memory, not yet committed). Proceeds to Step 4.

### Failure Modes

- Example commands fail → investigate and fix the example or note the issue
- Documentation style is inconsistent across files → propose standardization in the draft
- Draft grows beyond original scope → flag scope creep to user and suggest splitting into multiple PRs

---

## Step 4: Create Documentation Branch

### Instructions

1. Fetch latest main:
   ```bash
   git fetch origin main
   ```
2. Create branch from main:
   ```bash
   git checkout -b docs/<scope-desc> origin/main
   ```
   Where `<scope-desc>` is a short, descriptive kebab-case name (e.g., `docs/update-testing-section`, `docs/add-api-reference`).

### Output

Active documentation branch.

### Failure Modes

- Branch name already exists → delete stale branch or append a suffix
- Uncommitted changes on current branch → stash or commit before switching

---

## Step 5: Apply Changes and Verify

### Instructions

1. Apply documentation changes to files using Edit tool (prefer edits over full rewrites).
2. Run accuracy verification pass:
   - All file paths mentioned in the docs exist (Glob check)
   - All port numbers match .env / docker-compose files (Grep check)
   - All command examples are syntactically valid
   - All cross-references (links between docs) are valid
   - All code symbol references match actual codebase (Serena verify)
3. Commit changes:
   ```bash
   git add <specific-files>
   git commit -m "docs(<scope>): <description>"
   ```

### Output

Documentation changes committed on branch.

### Failure Modes

- File paths mentioned in docs do not exist → fix paths or note that the path is planned (aspirational docs violate accuracy-first)
- Cross-references between doc files are broken → fix links before committing
- Port numbers in docs do not match .env or docker-compose → update docs to match current config

---

## Step 6: Quality Gate — Accuracy Reflect + User Review

### Instructions

This is the primary quality gate for WF7. It combines automated reflection with mandatory user review.

**Part A: Accuracy Reflect**

Invoke `/reflexion:reflect` with focus on accuracy. The reflect pass verifies:

- Are all factual claims backed by code evidence?
- Is the documentation consistent with itself (no contradictions)?
- Is the reading order logical?
- Are there any claims that cannot be verified?

If reflect finds issues, fix them before presenting to user.

**Part B: User Review**

Present the documentation changes to the user:

```
DOCUMENTATION CHANGES (Ready for Review)
=========================================

Category: [correction / addition / restructure / generation]
Files modified: [list]

--- CHANGES ---
[show diff or summary of changes per file]

--- VERIFICATION STATUS ---
- File paths: [all verified / N unverified]
- Port numbers: [all verified / N unverified]
- Commands: [all tested / N untested]
- Cross-references: [all valid / N broken]

=========================================
Review the changes above. Provide feedback, or type "approved" to proceed.
```

**Part C: Iterate on Feedback**

- Incorporate user feedback into documentation.
- Re-verify accuracy after each change.
- Re-present updated changes.
- No fixed loop limit for documentation — the user iterates until satisfied. However, if 3+ rounds of feedback reveal scope creep, suggest splitting into multiple PRs.
- Repeat until user approves.

### Output

User-approved documentation changes committed on branch.

### Failure Modes

- User has extensive feedback across 3+ rounds → suggest splitting into multiple PRs to avoid scope creep
- Reflect finds unverifiable claims → flag for user decision; do not publish claims that cannot be backed by code evidence
- User and reflect disagree on accuracy → code is authoritative; present code evidence to user

---

## Step 7: Create Pull Request

### Instructions

1. Push branch:

   ```bash
   git push -u origin docs/<scope-desc>
   ```

2. Create PR:

   ```bash
   gh pr create \
     --repo ${REPO} \
     --title "docs(<scope>): <description>" \
     --body "$(cat <<'EOF'
   ## Summary
   - [what was updated and why]

   ## Verification
   - All file paths verified against codebase
   - All port numbers verified against .env/docker-compose
   - All command examples tested
   - User reviewed and approved

   ## Test plan
   - [ ] Documentation renders correctly
   - [ ] No broken cross-references
   - [ ] CI passes

   Generated with [Claude Code](https://claude.com/claude-code) using WF7
   EOF
   )" \
     --label "documentation"
   ```

### Output

PR URL.

### Failure Modes

- PR creation fails due to branch not pushed → push branch first with `git push -u origin docs/<scope-desc>`
- `--label "documentation"` fails because label does not exist → create label or omit

---

## Step 8: CI Verification

### Instructions

1. Wait for CI to complete:
   ```bash
   gh run list --repo ${REPO} --branch docs/<scope-desc> --limit 3
   ```
2. If CI fails, investigate:
   - Documentation-only PRs should not break tests
   - A failure indicates a pre-existing issue or misconfigured CI
   - Fix if possible, otherwise note the pre-existing failure

### Output

CI pass status.

### Failure Modes

- CI fails on a docs-only PR → investigate; likely a pre-existing CI issue, not caused by docs changes. Note and proceed.
- CI timeout → retry once; docs PRs should have fast CI runs

---

## Step 9: Merge and Deploy

### Instructions

1. Squash-merge the PR:
   ```bash
   gh pr merge --squash --repo ${REPO}
   ```
2. Deploy to dev (docs are part of the repo):
   ```bash
   ${PROJECT_ROOT}/scripts/deploy-dev.sh
   ```
3. Verify SMB access if docs are in a shared location.

### Output

Merged and deployed documentation.

### Failure Modes

- Merge conflict on squash → rebase branch on latest main, re-verify accuracy, force-push
- Deploy script fails → check SSH connectivity to chorestory-dev
- SMB access not configured for shared docs → set permissions for `${SMB_USER}` after deploy

---

## Step 10: Completion Summary

### Instructions

1. Update session notes (`claude_docs/session_notes.md`) with:
   - What was documented
   - Files changed
   - PR link
   - Verification status

2. Close GitHub issue if one was referenced:

   ```bash
   gh issue close <number> --repo ${REPO} --comment "Resolved by PR #NNN"
   ```

3. Present completion summary:

```
WF7 COMPLETE
=============

PR: [URL] (#NNN)
Category: [correction / addition / restructure / generation]
Files updated: [list]

Verification:
- Accuracy reflect: [passed / N issues found and fixed]
- User review iterations: [N]
- CI: [passed]

Documentation deployed to dev.

WF7 complete.
```

### Output

Completion summary. WF7 terminates.

4. **Conditional Memorization (P9):** If this documentation update revealed patterns worth memorizing (e.g., recurring stale doc areas, naming conventions, documentation structure insights), invoke `/reflexion:memorize` to curate insights into CLAUDE.md. Skip for simple corrections or additions.

### Failure Modes

- GitHub issue close fails → verify issue number and repo; may already be closed
- Session notes file missing or archived → create new file or check for archived versions

---

## Workflow Resumption

If this skill is invoked mid-conversation, detect the current state:

1. PR merged? → Step 10 (summary)
2. PR exists and CI passed? → Step 9 (merge)
3. PR exists? → Step 8 (CI check)
4. Docs branch has user-approved changes? → Step 7 (create PR)
5. Docs branch has committed changes? → Step 6 (quality gate)
6. Docs branch exists (empty)? → Step 4 (already branched)
7. Audit report in session notes? → Step 3 (draft)
8. None → Step 1 (start from scratch)

Announce the detected state before resuming: "Detected prior progress. Resuming at Step N."

---

## Conditional Memorization (P9)

After completing documentation restructuring, check if the restructuring revealed patterns worth memorizing:

- New documentation conventions established
- File organization patterns that should be followed going forward
- Recurring documentation gaps that indicate missing process

If insights are found, use the reflexion:memorize workflow to update CLAUDE.md. This is conditional — skip for simple corrections or additions.
