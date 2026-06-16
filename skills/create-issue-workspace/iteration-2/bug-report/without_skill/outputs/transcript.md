# Transcript — Report WebSocket idle-disconnect bug

User request: Report a bug — the server drops WebSocket connections after about
30 seconds of inactivity instead of keeping them alive. Reproduces reliably on
the staging box. Inspect the project (config, issue templates, source) and check
for a similar existing issue before creating. Use the mock `gh`.

All `gh` calls use the absolute path to the mocked CLI:
`/tmp/ci-iter2/bug-report/without_skill/fixture/bin/gh`

---

## 1. Set working directory

```
$ cd /tmp/ci-iter2/bug-report/without_skill/fixture && pwd
/tmp/ci-iter2/bug-report/without_skill/fixture
```

## 2. Inspect the project

### Workspace config (`.rawgentic_workspace.json`)
Active project: `sentinel-app` at `./projects/sentinel-app`, protection level `sandbox`.

### Project config (`projects/sentinel-app/.rawgentic.json`)
```json
{
  "project": { "name": "sentinel-app", "type": "web-service",
               "description": "Realtime API service for the eval harness" },
  "repo": { "provider": "github", "fullName": "octo-eval/sentinel-app",
            "defaultBranch": "trunk" },
  "techStack": ["node", "javascript"]
}
```
=> Repo for `gh` operations: **octo-eval/sentinel-app**.

### README (`projects/sentinel-app/README.md`)
```
# sentinel-app
Realtime API service. Modules: src/server.js, src/errorHandler.js.
```

### Source files
`src/server.js`:
```js
// Sentinel API server entrypoint.
export function startServer(port) { /* ... */ }
export function handleConnection(socket) { /* ... */ }
```
`src/errorHandler.js`:
```js
// Centralized error handling.
export function handleError(err, req, res) { /* ... */ }
```
=> `handleConnection(socket)` is the WebSocket entry point; there is no visible
keep-alive/heartbeat or idle-timeout handling — consistent with idle sockets
being dropped at ~30s.

### Issue template (`projects/sentinel-app/.github/ISSUE_TEMPLATE/bug_report.md`)
```
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
=> The bug template carries the `bug` label and defines the section structure the
issue body should follow.

## 3. Verify auth and existing labels

```
$ /tmp/.../bin/gh auth status
github.com
  Logged in to github.com account eval-bot (oauth_token)
  Token scopes: 'repo', 'read:org'
```

```
$ /tmp/.../bin/gh label list --repo octo-eval/sentinel-app
bug	Something isn't working	d73a4a
enhancement	New feature or request	a2eeef
```
=> The `bug` label already exists; no label creation needed.

## 4. Check for a similar existing issue (dedup)

```
$ /tmp/.../bin/gh issue list --repo octo-eval/sentinel-app --search "websocket" --state all --limit 50
(no output)

$ /tmp/.../bin/gh issue list --repo octo-eval/sentinel-app --search "connection drop timeout" --state all --limit 50
(no output)

$ /tmp/.../bin/gh issue list --repo octo-eval/sentinel-app --search "keepalive idle timeout" --state all --limit 50
(no output)

$ /tmp/.../bin/gh issue list --repo octo-eval/sentinel-app --state all --limit 100
(no output)
```
=> No existing or duplicate issue covers this WebSocket idle-disconnect bug.
Safe to create a new one.

## 5. Compose the issue body

Wrote `outputs/issue-body.md` following the project's `bug_report.md` template
(Description / Steps to Reproduce / Expected / Actual / Environment / Error Logs).
Body content:

```markdown
## Description

The server drops WebSocket connections after roughly 30 seconds of inactivity instead of keeping them alive. Idle clients are disconnected even though the connection should persist. This reproduces reliably on the staging box.

The realtime API service (`src/server.js`) accepts WebSocket connections via `handleConnection(socket)`, but there appears to be no keep-alive / heartbeat (ping-pong) mechanism or idle-timeout configuration that holds an idle connection open, so idle sockets are torn down at the ~30s mark.

## Steps to Reproduce
1. Deploy/run the service on the staging box.
2. Open a WebSocket connection to the server.
3. Leave the connection idle (send no frames) for ~30 seconds.
4. Observe that the server closes the connection.

## Expected Behavior

Idle WebSocket connections stay alive. The server should keep idle connections open (e.g. via a periodic ping/pong heartbeat or an appropriately long / disabled idle timeout) rather than dropping them after ~30 seconds of inactivity.

## Actual Behavior

After about 30 seconds of inactivity the server drops the WebSocket connection. The client is disconnected even though it has done nothing wrong and the connection should still be valid.

## Environment

- Environment: staging box (reproduces reliably)
- Service: sentinel-app (realtime API service)
- Stack: Node.js / JavaScript
- Relevant modules: `src/server.js` (`startServer`, `handleConnection`)

## Error Logs

(Not yet captured. To be added once staging WebSocket close-frame / disconnect logs are collected — capture the close code/reason emitted at the ~30s mark.)
```

## 6. Create the issue

```
$ /tmp/.../bin/gh issue create \
    --repo octo-eval/sentinel-app \
    --title "WebSocket connections dropped after ~30s of inactivity instead of staying alive (staging)" \
    --body-file /tmp/ci-iter2/bug-report/without_skill/fixture/outputs/issue-body.md \
    --label bug
https://github.com/octo-eval/sentinel-app/issues/101
```

## 7. Verify recorded state

```
$ cat .gh-mock/created-issue.json
{
  "repo": "octo-eval/sentinel-app",
  "title": "WebSocket connections dropped after ~30s of inactivity instead of staying alive (staging)",
  "labels": [
    "bug"
  ],
  "body_len": 1616,
  "ts": 1781586994
}
```

```
$ cat .gh-mock/calls.log
gh auth status
gh label list --repo octo-eval/sentinel-app
gh issue list --repo octo-eval/sentinel-app --search websocket --state all --limit 50
gh issue list --repo octo-eval/sentinel-app --search connection drop timeout --state all --limit 50
gh issue list --repo octo-eval/sentinel-app --search keepalive idle timeout --state all --limit 50
gh issue list --repo octo-eval/sentinel-app --state all --limit 100
gh issue create --repo octo-eval/sentinel-app --title WebSocket connections dropped after ~30s of inactivity instead of staying alive (staging) --body-file /tmp/ci-iter2/bug-report/without_skill/fixture/outputs/issue-body.md --label bug
```

---

## Final result

- **Issue URL:** https://github.com/octo-eval/sentinel-app/issues/101
- **Title:** WebSocket connections dropped after ~30s of inactivity instead of staying alive (staging)
- **Labels:** `bug`
- **Repo:** octo-eval/sentinel-app
- **Dedup:** Checked (websocket / connection-drop / keepalive searches + full list) — no existing similar issue.
