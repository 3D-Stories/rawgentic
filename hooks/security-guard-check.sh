#!/bin/bash
# Security Guard Check -- SessionStart hook that detects a conflict with the
# official security-guidance plugin and recommends disabling it EXACTLY ONCE.
# Hook: SessionStart (matcher: startup|resume)
# Uses additionalContext (matching existing session-start hook format).
# Silently skips on any error.
#
# Record-once (issue: per-session nag): the recommendation is a decision recorded
# once, not a per-session nag. ~/.rawgentic/security-guidance-decision records it:
#   surfaced  - the recommendation was shown once (written automatically)
#   kept      - the user chose to keep both enabled (write to suppress permanently)
#   disabled  - the user disabled security-guidance
# ANY recorded value suppresses all future recommendations, so a user who decides
# to keep both enabled is never nagged again.

set -euo pipefail

SETTINGS_FILE="$HOME/.claude/settings.json"
DECISION_FILE="$HOME/.rawgentic/security-guidance-decision"

# A decision is already on record -> never surface the recommendation again.
if [ -f "$DECISION_FILE" ]; then
  exit 0
fi

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

# Only the enabled-and-undecided case surfaces (and records) the recommendation.
# When security-guidance is NOT enabled there is nothing to warn about, so we stay
# silent and do NOT pre-record — a user who deliberately enables it later still
# gets the one-time heads-up.
if [ "$CONFLICT" = "yes" ]; then
  # Record the decision BEFORE emitting, and emit ONLY if the record succeeded.
  # If the write fails (e.g. ~/.rawgentic not writable), the [ -f ] guard at the
  # top would never see a decision, so emitting anyway would re-surface the
  # recommendation every session. Failing closed on the nag (stay silent, retry
  # next session once the write can succeed) is better than nagging forever.
  mkdir -p "$(dirname "$DECISION_FILE")" 2>/dev/null || true
  if printf 'surfaced\n' > "$DECISION_FILE" 2>/dev/null && [ -f "$DECISION_FILE" ]; then
    cat <<'JSONEOF'
{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":"The official security-guidance plugin is enabled alongside rawgentic's own security guard. They overlap on pattern coverage but hook DIFFERENT lifecycle events — rawgentic blocks pre-edit (PreToolUse permissionDecision:deny) while security-guidance reviews post-edit (PostToolUse/Stop). Running both is therefore redundant, not a conflict: they cannot both block the same pre-edit decision, because security-guidance registers no PreToolUse hook (verified in #119). Optional: run `claude plugin disable security-guidance@claude-plugins-official` to cut duplicate security warnings, or keep both (harmless). This notice is shown ONCE and will not repeat. If you would rather KEEP both enabled, tell me and I will record that choice (write 'kept' to ~/.rawgentic/security-guidance-decision) so it stays silent."}}
JSONEOF
  fi
fi

exit 0
