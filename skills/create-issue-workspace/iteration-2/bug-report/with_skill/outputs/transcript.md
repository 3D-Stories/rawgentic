# WF1 create-issue — Full Transcript (bug report)

Skill under test: `rawgentic:create-issue` (WF1, 9-step issue creation workflow).
Working directory: `/tmp/ci-iter2/bug-report/with_skill/fixture`
Mock gh (absolute path): `/tmp/ci-iter2/bug-report/with_skill/fixture/bin/gh`
Harness substitution: Step 3's 3 parallel judges performed INLINE (no subagents).

## User request
> Report a bug: the server drops WebSocket connections after about 30 seconds of
> inactivity instead of keeping them alive. It happens reliably on the staging box.

---

## Working directory
```
$ cd /tmp/ci-iter2/bug-report/with_skill/fixture && pwd
/tmp/ci-iter2/bug-report/with_skill/fixture
```

---

## <config-loading>

### Level 1 — conversation context
No prior `/rawgentic:switch` in this session.

### Level 2 — session registry
`claude_docs/session_registry.jsonl` does not exist → fall through.

### Level 3 — workspace default
`.rawgentic_workspace.json`:
```json
{
  "version": 1,
  "defaultProtectionLevel": "sandbox",
  "projects": [
    {
      "name": "sentinel-app",
      "path": "./projects/sentinel-app",
      "active": true,
      "configured": true,
      "disabledSkills": [],
      "headlessEnabled": true,
      "lastUsed": "2026-06-15T00:00:00Z"
    }
  ]
}
```
Exactly one active project → **sentinel-app**. Path `./projects/sentinel-app` resolves
against the workspace root.

### Step 1b — disabled-skill check
`disabledSkills` is `[]` and the workspace has no `bmadDetected` field → proceed.

### Capabilities derivation (one source of truth)
```
$ python3 hooks/capabilities_lib.py derive --config ./projects/sentinel-app/.rawgentic.json
{"config":{"version":1,"project":{"name":"sentinel-app","type":"web-service","description":"Realtime API service for the eval harness"},"repo":{"provider":"github","fullName":"octo-eval/sentinel-app","defaultBranch":"trunk"},"protectionLevel":"sandbox","techStack":["node","javascript"],"testing":{"frameworks":[{"name":"vitest","type":"unit","command":"npm test","testDir":"test"}]},"ci":{"provider":"github-actions","workflowDir":".github/workflows"},"documentation":{"primaryFiles":["README.md"],"format":"markdown"},"custom":{}},"capabilities":{"repo":"octo-eval/sentinel-app","default_branch":"trunk","project_type":"web-service","has_tests":true,"test_commands":["npm test"],"has_ci":true,"has_deploy":false,"deploy_method":null,"has_database":false,"migration_dir":null,"has_docker":false}}
EXIT=0
```

### Resolved config / capabilities
| field | value |
|-------|-------|
| repo | `octo-eval/sentinel-app` |
| default_branch | `trunk` |
| project_type | `web-service` |
| techStack | node, javascript |
| has_tests | true (`npm test`, vitest) |
| has_ci | true (github-actions) |
| has_deploy | false |
| has_database | false |
| has_docker | false |

Gate checks:
```
$ grep -o "critiqueMethod" .rawgentic_workspace.json   # -> no match => default reflexion 3-judge
$ python3 hooks/adversarial_review_lib.py is-enabled --workspace .rawgentic_workspace.json --project sentinel-app --skill create-issue
disabled
ADV_EXIT=1   # => Step 4 adversarial sub-step is a silent no-op
```

---

## Step 1 — Receive User Intent

Dedup check:
```
$ /tmp/ci-iter2/bug-report/with_skill/fixture/bin/gh issue list --repo octo-eval/sentinel-app --search "websocket connection drop inactivity timeout keepalive" --limit 10
            # (empty output)
EXIT=0
```
No existing issues → clean repo.

**Classification presented to user:**
```
Issue Classification:
- Type: bug
- Summary: The server drops WebSocket connections after ~30s of inactivity instead of keeping them alive; reproduces reliably on the staging box.
- Scope hints: WebSocket connection handling (src/server.js handleConnection), keepalive/heartbeat/ping-pong, idle timeout.
- Existing issues found: none

Proceeding to brainstorm. Confirm or correct this classification.
```

**User:** "Confirmed — this is a bug. Yes, proceed."
Clarifying details supplied by user: expected = idle connections stay open; actual = drop after ~30s;
reproduce = reliably on staging (open WS, idle ~30s, disconnect); environment = staging box.

---

## Step 2 — Brainstorm (internal artifact)

Read: `bug_report.md` template, `.rawgentic.json`, `README.md`, `src/server.js`, `src/errorHandler.js`.

Bug template structure:
```
## Description / ## Steps to Reproduce / ## Expected Behavior /
## Actual Behavior / ## Environment / ## Error Logs   (labels: bug)
```

Component verification (Grep — Serena absent):
```
$ grep -rn "handleConnection" projects/sentinel-app/src
projects/sentinel-app/src/server.js:3:export function handleConnection(socket) { /* ... */ }
$ grep -rn "startServer" projects/sentinel-app/src
projects/sentinel-app/src/server.js:2:export function startServer(port) { /* ... */ }
$ grep -rniE "websocket|keepalive|keep-alive|heartbeat|ping|pong|timeout|setInterval" projects/sentinel-app/src
NONE FOUND -> do not claim these symbols exist
$ grep -rnE "export (function|const|class)" projects/sentinel-app/src
projects/sentinel-app/src/errorHandler.js:2:export function handleError(err, req, res) { /* ... */ }
projects/sentinel-app/src/server.js:2:export function startServer(port) { /* ... */ }
projects/sentinel-app/src/server.js:3:export function handleConnection(socket) { /* ... */ }
```
Grounding result: `handleConnection` / `startServer` / `handleError` exist; there is **no**
keepalive/heartbeat/ping/timeout logic anywhere in src → the bug is the ABSENCE of keepalive
in `handleConnection`. The draft must describe it as missing, not broken.

Draft title: `fix(server): keep idle WebSocket connections alive past 30s`. Affected component:
`src/server.js` `handleConnection`.

---

## Step 3 — 3-judge critique (INLINE per harness rule)

Judge-side rechecks:
```
$ /tmp/.../bin/gh issue list --repo octo-eval/sentinel-app --search "websocket" --limit 10 --state all
            # (empty) EXIT=0  -> no dupes
$ grep -o "security" projects/sentinel-app/.rawgentic.json
            # no match -> no config.security standards to check
```

Findings (6 total):
- #1 High / completeness — frame as observed behavior; exact close source (app vs proxy on staging)
  to be confirmed during investigation. (clear)
- #2 Medium / template_conformance — include the Error Logs section ("None provided"). (clear)
- #3 Medium / accuracy — keepalive is MISSING in handleConnection; do not reference a non-existent
  heartbeat function; affected component verified. (clear)
- #4 Low / feasibility — 30s may come from app-level idle setting OR upstream proxy/LB; add risk note. (clear)
- #5 High / accuracy — tighten AC1 with a measurable idle bound (remains open >= 5 min). (clear)
- #6 Low / completeness — add AC: no regression in normal teardown / handleError. (clear)

Debate round: no disagreements; findings complementary; no conflicts.
Volume thresholds (independent per tier): Critical 0/5, High 2/5, Medium 2/10, Low 2/10
→ **no loop-back** (loop_iteration = 0). Proceed to Step 4.

---

## Step 4 — Apply Critique Findings (circuit breaker)

Sub-step 0 (adversarial review): `is-enabled` returned `disabled` (exit 1) → **skipped silently**
(no temp file, no subprocess). Marker logged.
`### WF1 Step 4 — Adversarial Review (skipped): is-enabled disabled for create-issue`

Circuit breaker scan: 0 ambiguous, 0 conflicting, 0 judgment-call findings → **CLEAR PATH**.
Notification to user: "Critique complete. 6 findings applied (0 Critical, 2 High, 2 Medium, 2 Low).
All clear — no ambiguity detected."

Amendments: #1 behavior framing · #2 Error Logs="None provided" · #3 keepalive-missing grounding ·
#4 app-vs-infra risk note · #5 AC idle >= 5 min · #6 AC no teardown/handleError regression.

---

## Step 5 — Incorporate Amendments

All 6 amendments applied. Verified: no internal contradictions, conforms to bug_report.md template,
well under 2000 words. Refined spec produced.

## Step 6 — Conditional Memorization (parallel w/ Step 7)

Reviewed findings for reusable cross-project insight. All findings are issue-specific (this app's
missing keepalive, testable idle bound, app-vs-infra timeout = general knowledge). No novel,
generalizable insight → **memorization skipped** (no output), avoiding over-memorization.

## Step 7 — User Review

### DRAFT ISSUE SPECIFICATION (presented to user)
```
Title: fix(server): keep idle WebSocket connections alive past 30s
Type: bug
Labels: bug
Complexity: S

--- DESCRIPTION ---
WebSocket connections handled in src/server.js (handleConnection) are dropped after
roughly 30 seconds of inactivity instead of being kept alive. The connection handler
has no heartbeat / ping-pong / keepalive mechanism, so idle sockets are closed once an
idle timeout elapses. The exact source of the close (application-level idle timeout vs.
an upstream proxy/load balancer on the staging box) is to be confirmed during
investigation. Reproduces reliably on the staging environment.

--- STEPS TO REPRODUCE ---
1. Connect a WebSocket client to the staging server.
2. Leave the connection idle (send no frames) for ~30 seconds.
3. Observe the server close the connection.

--- EXPECTED BEHAVIOR ---
Idle WebSocket connections remain open well beyond 30s (heartbeat/ping-pong, or a
configurable/disabled idle timeout).

--- ACTUAL BEHAVIOR ---
The connection is dropped after ~30 seconds of inactivity.

--- ENVIRONMENT ---
Staging box (octo-eval/sentinel-app; web-service, node/javascript).

--- ERROR LOGS ---
None provided.

--- ACCEPTANCE CRITERIA ---
1. An idle WebSocket connection remains open for at least 5 minutes without client
   traffic. [Added per critique finding #5]
2. The server implements a keepalive mechanism (periodic ping with pong tracking, or
   equivalent) in handleConnection, where none currently exists. [per finding #3]
3. The idle/keepalive timeout is configurable rather than a hardcoded default.
4. An automated test (vitest, `npm test`) verifies an idle connection survives past the
   previous ~30s drop threshold.
5. No regression in normal connection teardown / error handling (src/errorHandler.js
   handleError). [Added per critique finding #6]

--- SCOPE ---
In scope: keepalive / idle-timeout handling in src/server.js handleConnection.
Out of scope: client-side reconnection logic; auth and error-handler behavior (beyond no-regression).

--- AFFECTED COMPONENTS ---
- src/server.js -> handleConnection(socket) (verified present; no keepalive logic currently).

--- RISK ASSESSMENT ---
- ~30s close may be app-side OR upstream proxy/LB on staging; investigation must distinguish. [#4]
- Naive keepalive w/o pong tracking leaves half-open/zombie connections + leaks; track liveness.

--- RELATED ISSUES ---
- None (dedup returned no matches).

=============================================
Critique summary: 6 findings applied (0 Critical, 2 High, 2 Medium, 2 Low)
Review the specification above. Provide any changes, or type "approved" to proceed.
```

**User:** "approved"  → approved on 1st iteration, 0 feedback rounds.

---

## Step 8 — Create GitHub Issue

Body written to `/tmp/wf1-issue-body.md` (2538 bytes).

Label list:
```
$ /tmp/.../bin/gh label list --repo octo-eval/sentinel-app
bug	Something isn't working	d73a4a
enhancement	New feature or request	a2eeef
EXIT=0
```
Base label `bug` exists. Created missing scope label `server`:
```
$ /tmp/.../bin/gh label create "server" --repo octo-eval/sentinel-app --description "src/server.js (WebSocket/connection layer)" --color "1D76DB"
Created label 'server'
EXIT=0
```

Create issue:
```
$ /tmp/.../bin/gh issue create \
    --repo octo-eval/sentinel-app \
    --title "fix(server): keep idle WebSocket connections alive past 30s" \
    --body-file /tmp/wf1-issue-body.md \
    --label "bug" --label "server"
https://github.com/octo-eval/sentinel-app/issues/101
EXIT=0
```

Mock record (`.gh-mock/created-issue.json`):
```json
{
  "repo": "octo-eval/sentinel-app",
  "title": "fix(server): keep idle WebSocket connections alive past 30s",
  "labels": ["bug", "server"],
  "body_len": 2536,
  "ts": 1781587713
}
```

`.gh-mock/calls.log`:
```
gh issue list --repo octo-eval/sentinel-app --search websocket connection drop inactivity timeout keepalive --limit 10
gh issue list --repo octo-eval/sentinel-app --search websocket --limit 10 --state all
gh label list --repo octo-eval/sentinel-app
gh label create server --repo octo-eval/sentinel-app --description src/server.js (WebSocket/connection layer) --color 1D76DB
gh issue create --repo octo-eval/sentinel-app --title fix(server): keep idle WebSocket connections alive past 30s --body-file /tmp/wf1-issue-body.md --label bug --label server
```

Temp body cleaned up: `rm -f /tmp/wf1-issue-body.md` (confirmed absent).

---

## Step 9 — Workflow Completion Summary

```
WF1 COMPLETE
=============
GitHub Issue: https://github.com/octo-eval/sentinel-app/issues/101 (Issue #101)
Type: bug
Title: fix(server): keep idle WebSocket connections alive past 30s
Labels: bug, server

Critique Summary:
- Total findings: 6
- Applied: 6 (0 Critical, 2 High, 2 Medium, 2 Low)
- Ambiguity circuit breaker: not triggered
- Loop-backs: 0
- Memorized insights: none (correctly skipped)

User Review: approved immediately (1 iteration)
```

### <completion-gate> — all PASS
1. Step markers logged for ALL executed steps (1–9) — PASS
2. Final completion summary presented to user — PASS
3. Session notes updated with completion summary — PASS
4. Issue URL documented in session notes (#101) — PASS
5. Memorize step completed (correctly skipped) — PASS
6. Issue body matches critique findings (#2 Error Logs, #3 keepalive-missing, #4 risk, #5 idle≥5min, #6 no-regression) — PASS

WF1 complete. No auto-transition to WF2.

---

## FINAL RESULT
- Issue URL: https://github.com/octo-eval/sentinel-app/issues/101
- Title: fix(server): keep idle WebSocket connections alive past 30s
- Labels: bug, server
