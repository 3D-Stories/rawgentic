#!/usr/bin/env bash
# Probe 14 (#713, adjacent) — which additionalContext shape does UserPromptSubmit
# actually honour?
#
# WHY this probe exists: the official hooks guide says of UserPromptSubmit "Nest
# `additionalContext` inside `hookSpecificOutput`; if you place it at the top
# level of the JSON, Claude Code silently ignores it." But
# `hooks/context_meter.py:905-915` and `hooks/wal-context:43` both emit the
# TOP-LEVEL form on that event, recorded as verified live 2026-07-28. Those two
# claims cannot both be true, and if the docs are right the meter's
# UserPromptSubmit arm is silently dead. Settled by measurement, not by deciding
# which source to trust.
#
# Usage: ups-shape-probe.sh top|nested   (registered twice, once per shape, so
# one run distinguishes them by token.)
#
# Not wired into the plugin. See README.md in this directory to re-run.
set -u
SHAPE="${1:-top}"
case "$SHAPE" in
  top)    printf '%s\n' '{"additionalContext":"CANARY-UPS-TOPLEVEL-9WZ2"}' ;;
  nested) printf '%s\n' '{"hookSpecificOutput":{"hookEventName":"UserPromptSubmit","additionalContext":"CANARY-UPS-NESTED-4KQ7"}}' ;;
esac
exit 0
