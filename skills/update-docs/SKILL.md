---
name: rawgentic:update-docs
description: Update, create, or restructure project documentation using the WF7 10-step workflow with accuracy verification, user review, and conventional commit PR. Invoke with /update-docs followed by a description of the documentation change or an issue number. DO NOT use this skill if the user is working within a BMAD workflow — use the BMAD tech-writer agent instead. Only trigger when the user explicitly invokes /update-docs or /rawgentic:update-docs, or is working in a rawgentic-only project without BMAD.
argument-hint: Description of docs to update (e.g., "update API reference section") or issue number
---


# WF7: Documentation Update Workflow

<role>
You are the WF7 orchestrator implementing a 10-step documentation update workflow. You guide the user through scope validation, documentation audit, drafting, accuracy verification, user review, and PR creation. You enforce the accuracy-first principle: inaccurate docs are worse than no docs. Every factual claim must be verified against actual code.
</role>

<constants>
DOC_CATEGORIES:
  correction: Fix inaccurate docs (lightweight reflect)
  addition: Add missing documentation (full user review)
  restructure: Reorganize doc structure (full user review + reflect)
  generation: Create new doc from code (full user review + reflect)
BRANCH_PREFIX = "docs/"
</constants>

<config-loading>
Before executing any workflow steps, load the project configuration:

1. Determine the active project using this fallback chain:
   **Level 1 -- Conversation context:** If a previous `/rawgentic:switch` in this session set the active project, use that.
   **Level 2 -- Session registry:** Read `claude_docs/session_registry.jsonl`. Grep for your session_id. If found, use the project from the most recent matching line.
   **Level 3 -- Workspace default:** Read `.rawgentic_workspace.json` from the Claude root directory. If exactly one project has `active == true`, use it. If multiple projects are active, STOP and tell user: "Multiple active projects. Run `/rawgentic:switch <name>` to bind this session."

   At any level:
   - `.rawgentic_workspace.json` missing -> STOP. Tell user: "No rawgentic workspace found. Run /rawgentic:new-project."
   - `.rawgentic_workspace.json` malformed -> STOP. Tell user: "Workspace file is corrupted. Run /rawgentic:new-project to regenerate, or fix manually."
   - No active project found at any level -> STOP. Tell user: "No active project. Run /rawgentic:new-project to set one up, or /rawgentic:switch to bind this session."
   - **Path resolution:** The `activeProject.path` may be relative (e.g., `./projects/my-app`). Resolve it against the Claude root directory (the directory containing `.rawgentic_workspace.json`) to get the absolute path for file operations.

2. Read `<activeProject.path>/.rawgentic.json`.
   - Missing -> STOP. Tell user: "Active project <name> has no config. Run /rawgentic:setup."
   - Malformed JSON -> STOP. Tell user: "Project config is corrupted. Run /rawgentic:setup to regenerate."
   - Check `config.version`. If version > 1 (or missing), warn user about version mismatch.
   - Parse full JSON into `config` object.

3. Build the `capabilities` object from config:
   - has_tests: config.testing exists AND config.testing.frameworks.length > 0
   - test_commands: config.testing.frameworks[].command
   - has_ci: config.ci exists AND config.ci.provider exists
   - has_deploy: config.deploy exists AND config.deploy.method exists and != "manual"
   - has_database: config.database exists AND config.database.type exists
   - has_docker: config.infrastructure exists AND config.infrastructure.docker.composeFiles.length > 0
   - project_type: config.project.type
   - repo: config.repo.fullName
   - default_branch: config.repo.defaultBranch

All subsequent steps use `config` and `capabilities` — never probe the filesystem for information that should be in the config.
</config-loading>

<learning-config>
If this workflow discovers new documentation files or conventions, update `.rawgentic.json` before completing:
- Append to config.documentation.primaryFiles[]
- Set config.documentation.format if currently missing
- Do NOT overwrite existing non-null values without asking the user
- Always read full file, modify in memory, write full file back
</learning-config>

<environment-setup>
Configuration is loaded at workflow start (Step 1) via `<config-loading>`.
All project-specific values (repo, paths, services, ports) come from `.rawgentic.json`.

If config loading fails, STOP and follow the error guidance in `<config-loading>`.
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
WF7 terminates after merge and deployment. No auto-transition to other workflows. WF7 terminates ONLY after the completion-gate (after Step 10) passes. All steps must have markers in session notes, and the completion-gate checklist must be printed with all items passing.
</termination-rule>

<context-compaction>
Before context compaction, document in `claude_docs/session_notes.md`: current step number, branch name, last commit SHA, documentation category, which files have been updated vs pending, and user review feedback state.
</context-compaction>

<ambiguity-circuit-breaker>
STOP and ask the user when documentation requirements are ambiguous, accuracy verification yields conflicting results (code vs existing docs disagree on behavior), or the scope of changes expands beyond the original request. The accuracy-first principle makes this especially critical — incorrect documentation is worse than missing documentation, so when in doubt, STOP and ask rather than guess.
</ambiguity-circuit-breaker>

<step-tracking>
At the end of each step, log a marker in `claude_docs/session_notes.md`:
`### WF7 Step X: <Name> — DONE (<key detail>)`
This enables workflow resumption if context is lost.
</step-tracking>

## Step 1: Receive Documentation Scope

### Instructions

1. **Execute `<config-loading>`** to load `.rawgentic_workspace.json` and `.rawgentic.json`. Build `config` and `capabilities` objects. Log resolved values in session notes. If config loading fails, STOP and follow the error guidance.
2. If argument is an issue number: fetch via `gh issue view <number> --repo ${capabilities.repo}` and display.
3. Clarify scope with the user:
   - Which files need updating?
   - What kind of change? Classify as: **correction** / **addition** / **restructure** / **generation**
4. Confirm scope with user before proceeding.
5. Update `claude_docs/session_notes.md` with: documentation scope, target files, accuracy audit plan.

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
- User cannot specify which files need updating → suggest auditing files listed in `config.documentation.primaryFiles[]` as a starting point
- Issue references stale documentation without specifying what is wrong → ask user for specifics before proceeding

---

## Step 2: Audit Current Documentation

### Instructions

1. Read all affected documentation files.
2. Cross-reference factual claims against codebase:
   - File paths → Glob / `ls`
   - Port numbers → derive from `config.services[].port` and verify against .env files
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
   - Port number verified: `config.services[].port` or `.env:LINE_N`
   - Command verified: tested and output matches
3. For code examples, test them (run commands, verify output matches).
4. Follow existing documentation style:
   - Match `config.documentation.format` conventions if specified
   - Markdown formatting (no unnecessary emojis)
   - Match existing indentation and list styles

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
   - All port numbers match `config.services[].port` (Grep check)
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
- Port numbers in docs do not match `config.services[].port` → update docs to match current config

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
     --repo ${capabilities.repo} \
     --title "docs(<scope>): <description>" \
     --body "$(cat <<'EOF'
   ## Summary
   - [what was updated and why]

   ## Verification
   - All file paths verified against codebase
   - All port numbers verified against config.services
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
   gh run list --repo ${capabilities.repo} --branch docs/<scope-desc> --limit 3
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
   gh pr merge --squash --repo ${capabilities.repo}
   ```
2. If `capabilities.has_deploy`: run the project's deploy method per `config.deploy`.
   Otherwise: skip deploy (docs are merged to main).

### Output

Merged documentation (and deployed if applicable).

### Failure Modes

- Merge conflict on squash → rebase branch on latest main, re-verify accuracy, force-push
- Deploy fails → check deploy config in `.rawgentic.json` and connectivity

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
   gh issue close <number> --repo ${capabilities.repo} --comment "Resolved by PR #NNN"
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

Documentation merged (and deployed if applicable).

WF7 complete.
```

### Output

Completion summary. WF7 terminates.

4. **Conditional Memorization (P9):** If this documentation update revealed patterns worth memorizing (e.g., recurring stale doc areas, naming conventions, documentation structure insights), update `.rawgentic.json` or session notes as appropriate. Skip for simple corrections or additions.

### Failure Modes

- GitHub issue close fails → verify issue number and repo; may already be closed
- Session notes file missing or archived → create new file or check for archived versions

---

<completion-gate>
Before declaring WF7 complete, verify ALL of the following. Print the checklist with pass/fail for each item:

1. [ ] Step markers logged for ALL executed steps in session notes
2. [ ] Final step output (completion summary) presented to user
3. [ ] Session notes updated with completion summary
4. [ ] Documentation committed
5. [ ] PR URL documented (if branch)
6. [ ] Cross-references verified

If ANY item fails, go back and complete it before declaring "WF7 complete."
You may NOT output "WF7 complete" until all items pass.
</completion-gate>

---

## Workflow Resumption

If this skill is invoked mid-conversation, detect the current state:

0. All step markers present but completion-gate not printed? → Run completion-gate, then terminate.
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

If insights are found, update `.rawgentic.json` or session notes as appropriate. This is conditional — skip for simple corrections or additions.
