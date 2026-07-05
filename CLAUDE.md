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
4. **Update the official workflow diagram** (`docs/workflow-diagram.html`) — a mandatory *decision* every PR, exactly like the README. If the change alters any documented workflow spine (WF1/WF2/WF3/WF5 steps, gates, loop-backs, lane behavior), append a new REV entry per the recipe in `docs/workflow-diagram.md` (mark changed stations with `rev`, set the prior rev `superseded`, regenerate the `docs/assets/` snapshots). If the change does NOT touch any workflow spine, no diagram edit is needed — but confirm that deliberately rather than skipping silently. `tests/test_workflow_diagram.py::test_diagram_newest_rev_matches_plugin_version` guards that the diagram never documents a rev ahead of the shipped plugin.
5. **Run tests** — `pytest tests/ -v` must pass with 0 failures
   - Drift-guard tests over a doc corpus: anchor to one canonical source sentence, not a whole-corpus regex — the latter false-positives on stray matches in unrelated reference files

## Gotchas

- `plugins/rawgentic/skills/<name>` is a **symlink** to `skills/<name>/` — content edits auto-sync, but adding/removing a skill still means creating/deleting that symlink entry *and* the whitelist entry in `.claude-plugin/marketplace.json` (`tests/test_codex_plugin_packaging.py` asserts `is_symlink()`, catching a missed symlink but not a missed whitelist entry).
- `git rm -r <dir>` leaves untracked files behind (e.g. eval outputs) — a "dir no longer exists" removal check can pass in CI but fail locally until you also `rm -rf` the leftovers.
- To find which PR shipped a given version, walk `git log -- .claude-plugin/plugin.json` and read the `version` field at each commit — no separate version→PR map is kept.

## Updating the Plugin

After a PR is merged to main, the plugin cache must be updated separately. See the workspace CLAUDE.md for the full update workflow. Do NOT reinstall the plugin during an active session.
To Reinstall:

1. Exit all sessions using rawgentic hooks
2. Run:
   claude plugin remove rawgentic@rawgentic && claude plugin install rawgentic@rawgentic
3. Start a new session
