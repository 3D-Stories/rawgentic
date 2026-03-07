#!/bin/bash
# Security Guard Check -- SessionStart hook that detects conflict
# with the official security-guidance plugin.
# Hook: SessionStart (matcher: startup|resume)
# Uses additionalContext (matching existing session-start hook format).
# Silently skips on any error.

set -euo pipefail

SETTINGS_FILE="$HOME/.claude/settings.json"

# Skip if settings file doesn't exist
if [ ! -f "$SETTINGS_FILE" ]; then
  exit 0
fi

# Check if security-guidance plugin is enabled
CONFLICT=$(python3 -c "
import json, sys
try:
    with open('$SETTINGS_FILE') as f:
        s = json.load(f)
    enabled = s.get('enabledPlugins', {})
    if enabled.get('security-guidance@claude-plugins-official', False):
        print('yes')
except Exception:
    pass
" 2>/dev/null || true)

if [ "$CONFLICT" = "yes" ]; then
  cat <<'JSONEOF'
{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":"The official security-guidance plugin must be disabled for rawgentic's security guard to work correctly. Both plugins check the same patterns, but the official one uses a broken blocking mechanism that auto-retries, and running both causes a confusing double-block loop. Run: claude plugin disable security-guidance@claude-plugins-official"}}
JSONEOF
fi

exit 0
