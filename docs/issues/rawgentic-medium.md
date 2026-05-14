# Rawgentic — MEDIUM Issues

**Source:** First-run retrospective 2026-03-22

---

## 1. Session notes should be cumulative across WF2 steps (A5)

### What Happened

The bot wrote detailed session notes for the `no-console` fix (174 warnings across 41 files), but the notes don't cover the other 6 commits (no-unused-vars, consistent-return, a11y, exhaustive-deps, config changes). Either the notes were overwritten per-step, or only the most recent batch was documented.

### Recommendation

Each WF2 step should append to session notes, not overwrite. Structure as:

```markdown
## Step 2: Analysis
[what was found, what the plan is]

## Step 8: Implementation — Commit 1
[what was changed and why]

## Step 8: Implementation — Commit 2
[what was changed and why]

## Step 11: Code Review
[findings, any reverts or fixes]

## Step 12: PR Created
[PR number, summary]
```

This provides a complete audit trail and is especially important for headless runs where the human reviews after the fact. The session notes file should be the definitive record of what the bot did and why.

---

## 2. Consider chunking large changes into multiple PRs in headless mode (A6)

### What Happened

The issue spec said "Small," but the bot touched 128 files — nearly every file in the codebase. While each change is individually correct, reviewing a 128-file PR is a significant burden. A human might have split this into multiple PRs (one per ESLint rule category).

### Recommendation

If WF2 detects during the planning phase that an implementation will touch >50 files, it should:

- **In interactive mode:** Ask the user whether to split into multiple PRs
- **In headless mode:** Automatically split into multiple PRs by category

For the ESLint issue, separate PRs would have been:
1. "Fix existing lint warnings" (no-unused-vars, consistent-return, no-empty, etc.)
2. "Promote ESLint warnings to errors + add --max-warnings=0"

Each PR is self-contained, reviewable independently, and can be merged or reverted separately.

The threshold (50 files) should be configurable in `.rawgentic.json` under a `headless.maxFilesPerPR` field.
