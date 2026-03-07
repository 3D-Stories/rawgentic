---
name: sync-security-patterns
description: Sync security patterns from the official security-guidance plugin into rawgentic's security-patterns.json. Use when the official plugin has been updated and you want to pull in new patterns.
---

# Sync Security Patterns

Merge the latest patterns from Anthropic's `security-guidance` plugin into rawgentic's `hooks/security-patterns.json`.

## Steps

1. Read the official plugin's pattern definitions from:

   ```
   ~/.claude/plugins/marketplaces/claude-plugins-official/plugins/security-guidance/hooks/security_reminder_hook.py
   ```

   Look for the `SECURITY_PATTERNS` list. Extract each entry's `ruleName`, `substrings`, and `reminder`.

2. Read the current `hooks/security-patterns.json` from the rawgentic plugin at `${CLAUDE_PLUGIN_ROOT}/hooks/security-patterns.json`.

3. For each pattern from the official plugin:
   - If a pattern with the same `ruleName` exists AND has `"source": "upstream"`: update its `substrings` and `reminder` fields (preserve `wordBoundary`, `suggestedGlobs`, and `type`)
   - If a pattern with the same `ruleName` exists AND has `"source": "custom"`: **skip it** (user owns this pattern)
   - If no pattern with that `ruleName` exists: add it with `"source": "upstream"`, `"wordBoundary": false`, `"type": "substring"`, and empty `"suggestedGlobs"`

4. Check for upstream patterns in `security-patterns.json` that are no longer in the official plugin. Flag these for user review but do NOT auto-delete them.

5. Write the updated `hooks/security-patterns.json`.

6. Report:
   - **Added:** new patterns from upstream
   - **Updated:** existing upstream patterns with changed reminders/substrings
   - **Unchanged:** patterns that were already in sync
   - **Preserved:** custom patterns that were not touched
   - **Flagged:** upstream patterns no longer in the official plugin (ask user whether to keep or remove)

7. Commit the change with message: `chore(security-guard): sync patterns from security-guidance`
