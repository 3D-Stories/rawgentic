#!/usr/bin/env bash
# Probe 9 (#687) — PostToolUse: dump the real hook payload, and emit a canary
# additionalContext so delivery can be observed end to end.
#
# Result on this host 2026-07-28: the canary came back quoted VERBATIM in the
# model's reply, named as arriving in a `PostToolUse:Bash` hook system-reminder.
# Only the NESTED hookSpecificOutput shape works on this event — the top-level
# {"additionalContext": ...} form that hooks/wal-context:43 uses on
# UserPromptSubmit is not the right shape here.
#
# Not wired into the plugin. See README.md in this directory to re-run.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
cat > "$HERE/payload-posttooluse.json"
printf '%s\n' '{"hookSpecificOutput":{"hookEventName":"PostToolUse","additionalContext":"CANARY-QX7HJ2"}}'
exit 0
