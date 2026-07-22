---
name: sync-security-patterns
description: Sync security patterns from the official security-guidance plugin into rawgentic's security-patterns.json. Use when the official plugin has been updated and you want to pull in new patterns.
---

# Sync Security Patterns

Merge the latest patterns from Anthropic's `security-guidance` plugin into rawgentic's `hooks/security-patterns.json`.

## Steps

1. Read the official plugin's pattern definitions from:

   ```
   ~/.claude/plugins/marketplaces/claude-plugins-official/plugins/security-guidance/hooks/patterns.py
   ```

   Look for the `SECURITY_PATTERNS` list (it lives in `patterns.py`; the sibling `security_reminder_hook.py` only imports it). Extract each entry's `ruleName`, `substrings`, `reminder`, and `regex`. Three matcher shapes appear — handle each explicitly, never silently drop:
   - **content rules** carry `substrings` and/or `regex` (often alongside a `path_filter`, which only *scopes* which files the content match applies to — it is NOT a standalone path matcher). Keep these as `"type": "substring"` and copy the `substrings`. The `regex` field (on 18 entries) holds real match logic — see the **Consumer caveat** in Step 3 for how to carry it.
   - **path-only rules** match on the file path via a `path_check` callable (a non-serializable lambda; only `github_actions_workflow` today). Do NOT try to serialize the lambda — map it to `"type": "path"` with the equivalent glob in `"pathPattern"` (the field the `hooks/security-guard.py` consumer actually matches on; `suggestedGlobs` is an exception-hint list, NOT a matcher, so a glob placed there never fires), matching the existing `github_actions_workflow` entries. If the glob is not reconstructable, list the rule under **Flagged** as skipped rather than dropping it.
   - Do NOT confuse `path_filter` (a scope on a content rule → keep `"type": "substring"`) with `path_check` (a standalone path matcher → `"type": "path"`).

2. Read the current `hooks/security-patterns.json` from the rawgentic plugin at `${CLAUDE_PLUGIN_ROOT}/hooks/security-patterns.json`.

3. For each pattern from the official plugin:
   - If a pattern with the same `ruleName` exists AND has `"source": "upstream"`: update its `substrings` and `reminder` fields (preserve `wordBoundary`, `suggestedGlobs`, and `type`)
   - If a pattern with the same `ruleName` exists AND has `"source": "custom"`: **skip it** (user owns this pattern)
   - If a pattern with the same `ruleName` exists AND has `"source": "upstream"`: also update its `regex` field when the upstream entry carries one.
   - If no pattern with that `ruleName` exists: add it with `"source": "upstream"`, `"wordBoundary": false`, empty `"suggestedGlobs"`, and the `regex` field when present. Set `"type": "substring"` for a content rule (copy its `substrings`), or `"type": "path"` with `"pathPattern"` for a `path_check` (path-only) rule per Step 1 — never a `"type": "substring"` entry with no `substrings` (it would match nothing).
   - **Consumer caveat:** the rawgentic security-guard consumer (`hooks/security-guard.py`) currently matches on `substrings` and path type only — it does not yet honor `regex`. Preserve the `regex` field so the logic is not lost, but it is NOT enforced until consumer regex support lands (a separate follow-up). Two consequences to surface, never hide: a **regex-ONLY** rule (no `substrings`, e.g. `python_subprocess_shell`, `yaml_unsafe_load_variants`) matches NOTHING if added as-is — list it under **Flagged**; a **mixed** rule (`substrings` + `regex`, e.g. `os_system_injection`) keeps its substring matching but loses the regex-only cases — note the reduced coverage in the report rather than reporting it as fully synced.

4. Check for upstream patterns in `security-patterns.json` that are no longer in the official plugin. Flag these for user review but do NOT auto-delete them.

5. Write the updated `hooks/security-patterns.json`.

6. Report:
   - **Added:** new patterns from upstream
   - **Updated:** existing upstream patterns with changed reminders/substrings
   - **Unchanged:** patterns that were already in sync
   - **Preserved:** custom patterns that were not touched
   - **Flagged:** upstream patterns no longer in the official plugin (ask user whether to keep or remove)

7. **Update staleness marker:** Compute the sha256 hash of the official plugin's `patterns.py` file and write it to `${CLAUDE_PLUGIN_ROOT}/hooks/.last-security-sync-hash`. This prevents the session-start hook from showing a stale-patterns warning until the official plugin changes again.

   ```bash
   sha256sum ~/.claude/plugins/marketplaces/claude-plugins-official/plugins/security-guidance/hooks/patterns.py | cut -d' ' -f1 > ${CLAUDE_PLUGIN_ROOT}/hooks/.last-security-sync-hash
   ```

8. Commit the change with message: `chore(security-guard): sync patterns from security-guidance`
