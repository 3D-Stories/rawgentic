#!/usr/bin/env bash
# Probe 11 (#713) — Stop: dump the real payload, and emit a canary
# additionalContext ONCE so its effect on continuation can be observed.
#
# Emits at most once per run, guarded by an O_EXCL-ish marker file, because the
# docs say additionalContext at Stop "keeps the conversation going" through the
# same 8-consecutive-continuation cap as `decision: block`. An unguarded probe
# would loop until that cap, which measures the cap rather than the delivery.
#
# Dumps EVERY firing (numbered), so `stop_hook_active` can be read across a
# multi-turn /goal loop rather than only on the first Stop.
#
# Not wired into the plugin. See README.md in this directory to re-run.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="${PROBE_OUT:-$HERE}"
mkdir -p "$OUT" 2>/dev/null || true

N=1
while [ -e "$OUT/stop-payload-$N.json" ]; do N=$((N + 1)); done
cat > "$OUT/stop-payload-$N.json"

# Fire the canary only on the first firing of this run.
if [ "${PROBE_SILENT:-0}" = "1" ]; then
  exit 0
fi
CANARY="${PROBE_CANARY:-CANARY-STOP-VT9KQ4: the rawgentic context meter would speak here. Quote this token verbatim in your next reply so delivery can be observed, then stop.}"
if mkdir "$OUT/.canary-fired" 2>/dev/null; then
  printf '{"hookSpecificOutput":{"hookEventName":"Stop","additionalContext":%s}}\n' \
    "$(printf '%s' "$CANARY" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))')"
fi
exit 0
