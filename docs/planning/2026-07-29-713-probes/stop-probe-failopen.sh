#!/usr/bin/env bash
# Probe 13 (#713) — does a BROKEN Stop hook block the turn?
#
# context_meter.py is fail-OPEN by contract: any error means "emit nothing,
# exit 0". Registering it on a blocking-capable event (Stop CAN block, unlike
# PostToolUse) makes that contract load-bearing in a new way, so the failure
# mode is measured rather than assumed.
#
# Emits garbage on stdout AND exits non-zero — the two ways a hook can be
# broken that are NOT exit 2. Per the docs, exit 1 is a non-blocking error and
# only exit 2 blocks; this probe checks that claim on Stop specifically.
#
# Not wired into the plugin. See README.md in this directory to re-run.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="${PROBE_OUT:-$HERE}"
mkdir -p "$OUT" 2>/dev/null || true
cat > "$OUT/stop-failopen-payload.json"
printf 'this is not JSON at all\n'
echo "probe 13: deliberate stderr" >&2
exit 1
