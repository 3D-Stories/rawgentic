# Rawgentic — Project Instructions

## Git Workflow

- **Never push directly to `main`.** All changes must go through a pull request.
- Create a feature branch (`feat/`, `fix/`, `docs/`, etc.), push it, and open a PR via `gh pr create`.
- Wait for the user to merge — do not auto-merge PRs.

## Pre-PR Checklist (mandatory before `gh pr create`)

Every PR must include these updates. Do NOT create the PR until all are done:

1. **Bump version** in `.claude-plugin/plugin.json` — patch for fixes/docs, minor for features
2. **Update `README.md`** — reflect any new features, changed behavior, or new files in relevant sections, **including the Changelog section** (feature-section edits without a matching Changelog entry are the most common miss)
3. **Update `docs/`** — update the relevant doc file(s) for the area changed (e.g., `docs/testing.md` for test changes, `docs/wal-guide.md` for WAL changes)
4. **Run tests** — `pytest tests/ -v` must pass with 0 failures
   - Drift-guard tests over a doc corpus: anchor to one canonical source sentence, not a whole-corpus regex — the latter false-positives on stray matches in unrelated reference files

## Updating the Plugin

After a PR is merged to main, the plugin cache must be updated separately. See the workspace CLAUDE.md for the full update workflow. Do NOT reinstall the plugin during an active session.
To Reinstall:

1. Exit all sessions using rawgentic hooks
2. Run:
   claude plugin remove rawgentic@rawgentic && claude plugin install rawgentic@rawgentic
3. Start a new session
