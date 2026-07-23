#!/usr/bin/env bash
# #535: regenerate docs/assets/workflow-diagram-{light,dark}.png (fullPage, dual
# theme) and gate them with tests/test_workflow_diagram.py. See
# docs/workflow-diagram.md "Regenerating the README snapshots" for context.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
DOCS_DIR="$REPO_ROOT/docs"
PW="playwright@1.61.1"

SERVER_OUT="$(mktemp)"
CAPTURE_ERR="$(mktemp)"
HTTP_PID=""

cleanup() {
  if [[ -n "$HTTP_PID" ]]; then
    kill "$HTTP_PID" 2>/dev/null || true
    wait "$HTTP_PID" 2>/dev/null || true
  fi
  rm -f "$SERVER_OUT" "$CAPTURE_ERR"
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

capture() {
  local theme="$1" out_rel="$2"
  echo "==> capturing $theme theme -> $out_rel"
  if ! npx "$PW" screenshot --full-page --viewport-size=1440,900 \
      "$BASE_URL/workflow-diagram.html?theme=${theme}" \
      "$REPO_ROOT/$out_rel" 2>"$CAPTURE_ERR"; then
    if grep -q "Executable doesn't exist" "$CAPTURE_ERR"; then
      echo "ERROR: Playwright browser binary missing. Run: npx $PW install chromium" >&2
    else
      cat "$CAPTURE_ERR" >&2
    fi
    exit 1
  fi
}

capture light docs/assets/workflow-diagram-light.png
capture dark docs/assets/workflow-diagram-dark.png

echo "==> running diagram test gate"
cd "$REPO_ROOT"
set +e
pytest tests/test_workflow_diagram.py -q
GATE_RC=$?
set -e
if [[ $GATE_RC -ne 0 ]]; then
  echo "ERROR: tests/test_workflow_diagram.py failed (exit $GATE_RC) — capture rejected" >&2
fi
exit $GATE_RC
