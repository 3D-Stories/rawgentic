# Rawgentic — LOW Issues

**Source:** First-run retrospective 2026-03-22

---

## 1. Brainstorm additional run guardrails for future iteration (A4)

### Context

Cost is not a concern (Claude Max subscription, not API billing). The orchestrator's 90-min timeout is the hard kill and is sufficient for v1.

### Recommendation

Future consideration — not urgent for v1. Ideas to brainstorm:
- Turn-count based progress checks (e.g., if >80 turns with no new commit, trigger self-diagnosis)
- "Are you stuck?" self-diagnosis at checkpoints
- Intermediate checkpoints where the bot evaluates whether it's making progress vs. going in circles
- Automatic escalation to `rawgentic:ai-waiting` if the bot detects it can't proceed

---

## 2. "Implementation started" comment should include step plan (A7)

### What Happened

The initial bot comment just says "Implementation started on branch..." with no detail about what the bot plans to do.

### Recommendation

This comment should come from the WF2 skill (not just the orchestrator's claim comment) and include the step plan so the user knows what's coming. Example:

> Starting implementation on branch `feature/309-eslint-phase2-tighten`:
> 1. Fix existing lint warnings (no-unused-vars, no-empty, consistent-return)
> 2. Remove debug console.log statements
> 3. Promote warnings to errors + add --max-warnings=0
> 4. Run code review

This sets expectations for the run duration and scope. Related to HIGH "Progress comments at step boundaries" (A3) — this is the first of those comments.

---

## 3. Improve headlessEnabled setup UX in /rawgentic:setup (B3)

### What Happened

The `headlessEnabled` flag in `.rawgentic_workspace.json` works correctly but there's no guided setup for it. Users have to manually add it or know to look for it.

### Recommendation

When running `/rawgentic:setup`, explicitly ask: "Should headless AI agents be allowed to work on this project?" and set the `headlessEnabled` flag with a clear explanation of what it enables (autonomous Claude Code sessions can claim and implement issues in this repo).
