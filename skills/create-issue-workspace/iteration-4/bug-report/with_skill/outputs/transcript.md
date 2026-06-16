# WF1 create-issue — Transcript

Skill under test: `rawgentic:create-issue` (WF1, lean). Orchestrated end-to-end.
GitHub CLI is mocked; invoked only via absolute path
`/tmp/ci-iter4/bug-report/with_skill/fixture/bin/gh`.

## User request

> Report a bug: the server drops WebSocket connections after about 30 seconds of
> inactivity instead of keeping them alive. It happens reliably on the staging box.

Classification: **BUG** (broken existing behavior) — confirmed cooperatively by the user.

---

## Config-loading

### 1. Active project resolution
- No prior `/rawgentic:switch` binding in this session.
- `claude_docs/session_registry.jsonl` does not exist → fall back to workspace file.
- `.rawgentic_workspace.json`: exactly one project with `active == true` → `sentinel-app`. Use it.

```
$ cat .rawgentic_workspace.json
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

### 2. Disabled-skill check
- `sentinel-app` entry `disabledSkills: []` → `create-issue` NOT disabled.
- No `bmadDetected` key in workspace → no BMAD gate triggered. Proceed.

### 3. Derive capabilities (tested source of truth, run from workspace root)

```
$ python3 hooks/capabilities_lib.py derive --config ./projects/sentinel-app/.rawgentic.json
{"config":{"version":1,"project":{"name":"sentinel-app","type":"web-service","description":"Realtime API service for the eval harness"},"repo":{"provider":"github","fullName":"octo-eval/sentinel-app","defaultBranch":"trunk"},"protectionLevel":"sandbox","techStack":["node","javascript"],"testing":{"frameworks":[{"name":"vitest","type":"unit","command":"npm test","testDir":"test"}]},"ci":{"provider":"github-actions","workflowDir":".github/workflows"},"documentation":{"primaryFiles":["README.md"],"format":"markdown"},"custom":{}},"capabilities":{"repo":"octo-eval/sentinel-app","default_branch":"trunk","project_type":"web-service","has_tests":true,"test_commands":["npm test"],"has_ci":true,"has_deploy":false,"deploy_method":null,"has_database":false,"migration_dir":null,"has_docker":false}}
EXIT=0
```

**Resolved literals carried forward:**
- `capabilities.repo` = `octo-eval/sentinel-app`
- `capabilities.default_branch` = `trunk`
- `project_type` = `web-service`; `has_tests` = true (`npm test`, vitest)

---

## Step 1: Understand the request

- Type: **bug** (server drops WebSocket connections that should stay alive).
- Enough detail to write testable criteria (expected vs actual, repro, environment).

### Dedup check

```
$ ./bin/gh issue list --repo octo-eval/sentinel-app --search "websocket connection drop timeout keepalive" --limit 10
(no output)
EXIT=0

$ ./bin/gh issue list --repo octo-eval/sentinel-app --search "websocket" --limit 10
(no output)

$ ./bin/gh issue list --repo octo-eval/sentinel-app --limit 10
(no output)
EXIT=0
```

No existing issues → no duplicate. Safe to file.

---

## Step 2: Draft the issue

### Template read

```
$ cat projects/sentinel-app/.github/ISSUE_TEMPLATE/bug_report.md
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
```

### Component verification (Grep/Glob/Read — no Serena)

```
$ find projects/sentinel-app/src -type f
projects/sentinel-app/src/errorHandler.js
projects/sentinel-app/src/server.js

$ cat projects/sentinel-app/src/server.js
// Sentinel API server entrypoint.
export function startServer(port) { /* ... */ }
export function handleConnection(socket) { /* ... */ }

$ cat projects/sentinel-app/src/errorHandler.js
// Centralized error handling.
export function handleError(err, req, res) { /* ... */ }

$ grep -rniE "websocket|keepalive|keep-alive|heartbeat|ping|pong|timeout|setInterval|30000|idle" projects/sentinel-app/src
(no matches)
EXIT=1
```

Findings (grounding per <quality-bar>):
- VERIFIED REAL: `src/server.js` exporting `startServer(port)` and `handleConnection(socket)`.
- `handleConnection(socket)` is the connection handler → the right place to reference.
- No keepalive/heartbeat/idle-timeout code exists in `src/` (grep empty) → consistent
  with the reported drop. Did NOT invent any heartbeat symbol or config value.

### Labels available

```
$ ./bin/gh label list --repo octo-eval/sentinel-app
bug          Something isn't working    d73a4a
enhancement  New feature or request     a2eeef
```

Only `bug` and `enhancement` exist. Bug → `bug`. No real component/scope label exists,
so none invented.

### Draft presented to user

- **Title:** `fix(server): WebSocket connections dropped after ~30s of inactivity instead of being kept alive`
- **Type:** bug · **Labels:** `bug` · **Complexity:** M
- **Affected components:** `src/server.js` — `handleConnection(socket)` (no keepalive logic currently in `src/`)
- Description / Steps to Reproduce / Expected / Actual / Environment / Error Logs (per template)
- 3 testable acceptance criteria (idle connection stays open; explicit keepalive in `handleConnection`; vitest regression test)
- Explicit in-scope / out-of-scope; Low-to-moderate risk; related issues: none.

---

## Step 3: User review

User reply: **approved**. Proceed to create.

---

## Step 4: Create the issue

Body written to `/tmp/wf1-issue-body.md` (heredoc, 47 lines), then:

```
$ /tmp/ci-iter4/bug-report/with_skill/fixture/bin/gh issue create --repo octo-eval/sentinel-app \
    --title "fix(server): WebSocket connections dropped after ~30s of inactivity instead of being kept alive" \
    --body-file /tmp/wf1-issue-body.md \
    --label "bug"
https://github.com/octo-eval/sentinel-app/issues/101
EXIT=0
```

### Issue body (as filed)

```markdown
## Description

The Sentinel realtime API server closes WebSocket connections after roughly 30 seconds with no traffic. Idle connections should be held open (the service is a realtime API). The drop is reproducible on the staging environment. No application-level keepalive/heartbeat (ping/pong) or idle-timeout handling is present in `src/server.js`.

## Steps to Reproduce
1. Open a WebSocket connection to the server running on the staging box.
2. Leave the connection idle — send no frames.
3. Wait ~30 seconds.
4. Observe the connection is closed by the server.

## Expected Behavior

The connection stays open across idle periods; the server keeps it alive (e.g., via ping/pong heartbeat) rather than closing it after a short idle window.

## Actual Behavior

After about 30 seconds of inactivity the server drops the connection. Reproduces reliably on staging.

## Environment

- Environment: staging
- Service: sentinel-app realtime API (`src/server.js`)
- Reproducibility: reliable / consistent

## Error Logs

None provided. Capturing server-side close logs / WebSocket close codes at the time of the drop would help confirm the cause (TBD).

## Acceptance Criteria
1. An idle WebSocket connection to the staging server remains open for at least several minutes (well beyond 30s) without being closed by the server.
2. The server keeps idle connections alive via an explicit mechanism (e.g., ping/pong heartbeat) implemented in `src/server.js`'s connection handling (`handleConnection`).
3. A regression test (vitest, `npm test`) covers the idle-connection-stays-open behavior, simulating an idle period beyond the previous ~30s threshold.

## Scope
- In scope: idle WebSocket connections being dropped by the server; keepalive/heartbeat or idle-timeout handling in `src/server.js`.
- Out of scope: connection drops caused by client-side or network-infrastructure timeouts (load balancer / proxy idle timeouts) unless investigation shows they originate there; client-side reconnection logic.

## Affected Components
- `src/server.js` — `handleConnection(socket)` (connection handler; no keepalive/heartbeat logic currently exists in `src/`).

## Risk Assessment

Low-to-moderate. The fix is localized to connection handling in `src/server.js`. Risks: an overly aggressive keepalive could increase traffic, and an incorrect idle-timeout could mask a legitimate infrastructure-level timeout. The exact close source (app vs. proxy/LB) should be confirmed during investigation.

**Complexity:** M

**Related issues:** none found (dedup search returned no existing issues).
```

### Cleanup

```
$ rm -f /tmp/wf1-issue-body.md
CLEANUP=0
```

---

## Step 5: Wrap up

Appended to `claude_docs/session_notes.md`:

```
### WF1 create-issue — DONE (https://github.com/octo-eval/sentinel-app/issues/101, bug, fix(server): WebSocket connections dropped after ~30s of inactivity instead of being kept alive)
```

### Final result

```
Issue created: https://github.com/octo-eval/sentinel-app/issues/101
Type: bug · Title: fix(server): WebSocket connections dropped after ~30s of inactivity instead of being kept alive · Labels: bug
```

Implementation NOT auto-started (WF2 is separate, per skill contract).
