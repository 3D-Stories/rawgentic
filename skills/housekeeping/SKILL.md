---
name: rawgentic:housekeeping
description: Prune stale entries from the session registry (claude_docs/session_registry.jsonl), which grows one entry per session forever. Use when the user asks to clean up / prune / trim the session registry or workspace housekeeping. Removes entries older than a configurable TTL (default 30 days) and reports what was cleaned. WAL files are NOT pruned here — session-start already rotates any WAL over 5000 lines.
argument-hint: optional — "dry-run" to preview, or a TTL in days (e.g. "60")
---

<role>
You run workspace housekeeping. Right now that means one thing: pruning the append-only
**session registry** so it doesn't accumulate stale entries forever. You are a thin
orchestrator — the prune logic is tested code in `hooks/registry_prune.py`; you resolve the
registry path, run the CLI, and report. You do NOT touch WAL files: `hooks/session-start`
already rotates any WAL over 5000 lines to a 7-day window, so the WAL is auto-capped (#7).
</role>

# Housekeeping — `/rawgentic:housekeeping`

## What this prunes

`claude_docs/session_registry.jsonl` gains one line per bound session and is never pruned
by age (session-start dedups by `session_id` but keeps every distinct session forever).
This removes entries whose `started` timestamp is older than the TTL. **Fail-safe:** a line
that can't be parsed or has no datable `started` is KEPT — housekeeping never silently
drops data it can't age.

## Step 1: Resolve the registry path

Find the workspace root (the directory containing `.rawgentic_workspace.json`); the registry
is `<workspace-root>/claude_docs/session_registry.jsonl`. Honor an external
`claudeDocsPath` in the workspace file if set (same resolution `hooks/session-start` uses).
If the file doesn't exist, there is nothing to prune — report that and stop.

## Step 2: Parse the argument

- `dry-run` (or "preview") → pass `--dry-run` (report only, write nothing).
- a bare number → pass it as `--ttl-days <n>` (overrides the default/env TTL).
- nothing → default TTL (30 days, or `$RAWGENTIC_REGISTRY_TTL_DAYS`).

## Step 3: Preview, then prune

Show the user what WOULD change first (safe by default), then apply:

```bash
# preview
python3 hooks/registry_prune.py --registry <abs-registry-path> --dry-run
# then, if the user is happy (or the run is non-interactive), apply:
python3 hooks/registry_prune.py --registry <abs-registry-path> [--ttl-days <n>]
```

The CLI reports `kept N, removed N, undatable-kept N` and only rewrites the file when it
actually removed something. TTL is configurable: `--ttl-days` > `$RAWGENTIC_REGISTRY_TTL_DAYS`
> default 30; a TTL below 1 is rejected (exit 2) rather than pruning everything.

Exit codes: `0` success (incl. nothing-to-prune / missing file), `2` usage error (bad TTL
or unreadable registry).

## Step 4: Report

Relay the CLI's summary line plainly: how many entries were kept, removed, and kept-because-
undatable, and whether the file was rewritten. Note that WAL rotation is handled
automatically by session-start and is out of scope for this skill.
