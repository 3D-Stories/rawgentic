#!/usr/bin/env bash
# Build a self-contained create-issue eval fixture.
#
#   build_fixture.sh <scenario> <dest-dir>
#
# scenarios: feature-quality | dedup-hit | config-decoy | bug-report
#
# The fixture is a miniature rawgentic workspace that the skill can run against
# end-to-end WITHOUT touching real GitHub or the real workspace:
#   <dest>/.rawgentic_workspace.json     one active, configured project
#   <dest>/projects/sentinel-app/        the project + .rawgentic.json + templates + src
#   <dest>/hooks/                        real capabilities_lib.py (+ adversarial_review_lib.py)
#   <dest>/bin/gh                        mock gh CLI (records calls + created issue)
#   <dest>/.gh-mock/                     mock state + per-scenario seed data
#   <dest>/claude_docs/                  session-notes target
#
# Each (eval x config) run gets its OWN copy so grading reads that run's state.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOOKS_SRC="$(cd "$SCRIPT_DIR/../../.." && pwd)/hooks"

SCENARIO="${1:?usage: build_fixture.sh <scenario> <dest-dir>}"
DEST="${2:?usage: build_fixture.sh <scenario> <dest-dir>}"

CONFIG_REPO="octo-eval/sentinel-app"      # the authoritative repo (from .rawgentic.json)
DECOY_REPO="octo-eval/sentinel-legacy"    # plausible WRONG repo planted in CLAUDE.md (config-decoy)
APP="sentinel-app"
PROJ="$DEST/projects/$APP"

rm -rf "$DEST"
mkdir -p "$PROJ/.github/ISSUE_TEMPLATE" "$PROJ/src" "$DEST/hooks" "$DEST/bin" \
         "$DEST/.gh-mock" "$DEST/claude_docs"

# --- workspace: exactly one active, configured project; clean of BMAD so the
#     skill's disabled-skill gate passes and it proceeds straight to Step 1 ---
cat > "$DEST/.rawgentic_workspace.json" <<JSON
{
  "version": 1,
  "defaultProtectionLevel": "sandbox",
  "projects": [
    {
      "name": "$APP",
      "path": "./projects/$APP",
      "active": true,
      "configured": true,
      "disabledSkills": [],
      "headlessEnabled": true,
      "lastUsed": "2026-06-15T00:00:00Z"
    }
  ]
}
JSON

# --- project config (mirrors the real .rawgentic.json schema) ---
cat > "$PROJ/.rawgentic.json" <<JSON
{
  "version": 1,
  "project": {
    "name": "$APP",
    "type": "web-service",
    "description": "Realtime API service for the eval harness"
  },
  "repo": {
    "provider": "github",
    "fullName": "$CONFIG_REPO",
    "defaultBranch": "trunk"
  },
  "protectionLevel": "sandbox",
  "techStack": ["node", "javascript"],
  "testing": {
    "frameworks": [
      { "name": "vitest", "type": "unit", "command": "npm test", "testDir": "test" }
    ]
  },
  "ci": { "provider": "github-actions", "workflowDir": ".github/workflows" },
  "documentation": { "primaryFiles": ["README.md"], "format": "markdown" },
  "custom": {}
}
JSON

# --- issue templates (the skill reads these in Step 2) ---
cat > "$PROJ/.github/ISSUE_TEMPLATE/feature_request.md" <<'MD'
---
name: Feature request
about: Propose new functionality
labels: enhancement
---

## Description

## Acceptance Criteria
1.

## Scope
In scope:
-
Out of scope:
-

## Affected Components

## Risk Assessment

## Complexity
<!-- S / M / L / XL -->

## Related Issues
MD

cat > "$PROJ/.github/ISSUE_TEMPLATE/bug_report.md" <<'MD'
---
name: Bug report
about: Report broken behavior
labels: bug
---

## Description

## Steps to Reproduce
1.

## Expected Behavior

## Actual Behavior

## Environment

## Error Logs
MD

# --- real source files so component-existence checks have ground truth ---
cat > "$PROJ/src/server.js" <<'JS'
// Sentinel API server entrypoint.
export function startServer(port) { /* ... */ }
export function handleConnection(socket) { /* ... */ }
JS
cat > "$PROJ/src/errorHandler.js" <<'JS'
// Centralized error handling.
export function handleError(err, req, res) { /* ... */ }
JS
cat > "$PROJ/README.md" <<'MD'
# sentinel-app
Realtime API service. Modules: src/server.js, src/errorHandler.js.
MD

# --- hooks the skill invokes (python3 hooks/...) ---
cp "$HOOKS_SRC/capabilities_lib.py" "$DEST/hooks/capabilities_lib.py"
[ -f "$HOOKS_SRC/adversarial_review_lib.py" ] && \
  cp "$HOOKS_SRC/adversarial_review_lib.py" "$DEST/hooks/adversarial_review_lib.py" || true

# --- gh mock on a local bin (invoked by absolute path) ---
cp "$SCRIPT_DIR/gh-mock" "$DEST/bin/gh"
chmod +x "$DEST/bin/gh"

# --- session notes target ---
: > "$DEST/claude_docs/session_notes.md"

# --- per-scenario layering ---
case "$SCENARIO" in
  feature-quality|bug-report|false-premise|vague-perf|over-broad)
    : > "$DEST/.gh-mock/seed_issues.tsv"   # clean repo, no duplicates
    # false-premise/vague-perf/over-broad reuse the clean base; the discriminator
    # is how the run HANDLES bad/missing/over-broad input, not fixture contents.
    # (false-premise deliberately has NO ConnectionThrottler / src/throttle.js.)
    ;;
  dedup-hit)
    # A clearly-matching OPEN issue already exists -> the skill must surface it.
    printf '%s\n' \
      "42	feat(server): add WebSocket support for realtime updates	enhancement	2026-05-01T00:00:00Z" \
      > "$DEST/.gh-mock/seed_issues.tsv"
    ;;
  config-decoy)
    : > "$DEST/.gh-mock/seed_issues.tsv"
    # Plausible, UNLABELLED wrong repo at workspace root. No "deliberately wrong"
    # tell -- the agent must follow the config-loading protocol to resolve it.
    cat > "$DEST/CLAUDE.md" <<MD
# sentinel-app workspace

Primary repository: $DECOY_REPO
Default branch: main

Contributions go through the standard PR flow.
MD
    ;;
  *)
    echo "unknown scenario: $SCENARIO" >&2; exit 2 ;;
esac

echo "built $SCENARIO fixture at $DEST (config repo=$CONFIG_REPO)"
