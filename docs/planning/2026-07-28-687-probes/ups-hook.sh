#!/usr/bin/env bash
# Probe 9 (#687) — UserPromptSubmit: dump the real hook payload.
#
# Result on this host 2026-07-28: keys delivered were
#   cwd, hook_event_name, permission_mode, prompt, prompt_id, session_id,
#   transcript_path
# so `transcript_path` IS present, and its basename is exactly
# <session_id>.jsonl — which is what makes context_meter's basename-binding
# hardening rule satisfiable.
#
# Not wired into the plugin. See README.md in this directory to re-run.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
cat > "$HERE/payload-userpromptsubmit.json"
exit 0
