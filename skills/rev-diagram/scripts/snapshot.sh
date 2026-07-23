#!/usr/bin/env bash
# #535: regenerate docs/assets/workflow-diagram-{light,dark}.png (fullPage, dual
# theme) and gate them with tests/test_workflow_diagram.py. See
# docs/workflow-diagram.md "Regenerating the README snapshots" for context.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
DOCS_DIR="$REPO_ROOT/docs"
PW="playwright@1.61.1"
LIGHT_OUT="$REPO_ROOT/docs/assets/workflow-diagram-light.png"
DARK_OUT="$REPO_ROOT/docs/assets/workflow-diagram-dark.png"

SERVER_OUT="$(mktemp)"
CAPTURE_ERR="$(mktemp)"
LIGHT_TMP="$(mktemp "${LIGHT_OUT%.png}.XXXXXX.png")"
DARK_TMP="$(mktemp "${DARK_OUT%.png}.XXXXXX.png")"
LIGHT_BACKUP="$(mktemp)"
DARK_BACKUP="$(mktemp)"
HTTP_PID=""

cleanup() {
  if [[ -n "$HTTP_PID" ]]; then
    kill "$HTTP_PID" 2>/dev/null || true
    wait "$HTTP_PID" 2>/dev/null || true
  fi
  rm -f "$SERVER_OUT" "$CAPTURE_ERR" "$LIGHT_TMP" "$DARK_TMP" "$LIGHT_BACKUP" "$DARK_BACKUP"
}
trap cleanup EXIT

echo "==> starting local server for docs/ (unbuffered, OS-assigned port)"
python3 -u -m http.server 0 --bind 127.0.0.1 --directory "$DOCS_DIR" >"$SERVER_OUT" 2>&1 &
HTTP_PID=$!

PORT=""
for _ in $(seq 1 20); do
  if [[ -s "$SERVER_OUT" ]]; then
    PORT="$(grep -oE 'port [0-9]+' "$SERVER_OUT" | head -1 | grep -oE '[0-9]+' || true)"
    [[ -n "$PORT" ]] && break
  fi
  sleep 0.25
done
if [[ -z "$PORT" ]]; then
  echo "ERROR: could not determine the local server's bound port" >&2
  cat "$SERVER_OUT" >&2
  exit 1
fi
BASE_URL="http://127.0.0.1:$PORT"
echo "==> server up at $BASE_URL"

echo "==> waiting for server readiness"
READY=0
for _ in $(seq 1 20); do
  if curl -sf -o /dev/null "$BASE_URL/workflow-diagram.html"; then
    READY=1
    break
  fi
  sleep 0.25
done
if [[ "$READY" -ne 1 ]]; then
  echo "ERROR: local server never became ready at $BASE_URL/workflow-diagram.html" >&2
  exit 1
fi

echo "==> preflight: Playwright CLI"
if ! npx "$PW" --version >/dev/null 2>&1; then
  echo "ERROR: Playwright CLI unavailable (network/npx issue)." >&2
  exit 1
fi

# #535 Step-11 review Finding #1: capture into TEMP files first — never write
# the real committed paths until both captures AND the pytest gate succeed.
# A mid-run failure (second capture, or the gate) must leave the PREVIOUSLY
# committed pair untouched, not a half-overwritten one.
capture() {
  local theme="$1" dest_tmp="$2"
  echo "==> capturing $theme theme -> $dest_tmp" >&2
  if ! npx "$PW" screenshot --full-page --viewport-size=1440,900 \
      "$BASE_URL/workflow-diagram.html?theme=${theme}" \
      "$dest_tmp" 2>"$CAPTURE_ERR"; then
    if grep -q "Executable doesn't exist" "$CAPTURE_ERR"; then
      echo "ERROR: Playwright browser binary missing. Run: npx $PW install chromium" >&2
    else
      cat "$CAPTURE_ERR" >&2
    fi
    exit 1
  fi
}

capture light "$LIGHT_TMP"
capture dark "$DARK_TMP"

echo "==> both captures succeeded; backing up the currently-committed pair"
HAD_LIGHT=0
HAD_DARK=0
if [[ -f "$LIGHT_OUT" ]]; then cp "$LIGHT_OUT" "$LIGHT_BACKUP"; HAD_LIGHT=1; fi
if [[ -f "$DARK_OUT" ]]; then cp "$DARK_OUT" "$DARK_BACKUP"; HAD_DARK=1; fi

echo "==> promoting new captures (atomic rename)"
mv "$LIGHT_TMP" "$LIGHT_OUT"
mv "$DARK_TMP" "$DARK_OUT"

echo "==> running diagram test gate"
cd "$REPO_ROOT"
set +e
pytest tests/test_workflow_diagram.py -q
GATE_RC=$?
set -e

if [[ $GATE_RC -ne 0 ]]; then
  echo "ERROR: tests/test_workflow_diagram.py failed (exit $GATE_RC) — restoring the prior snapshot pair" >&2
  if [[ "$HAD_LIGHT" -eq 1 ]]; then cp "$LIGHT_BACKUP" "$LIGHT_OUT"; else rm -f "$LIGHT_OUT"; fi
  if [[ "$HAD_DARK" -eq 1 ]]; then cp "$DARK_BACKUP" "$DARK_OUT"; else rm -f "$DARK_OUT"; fi
fi
exit $GATE_RC
