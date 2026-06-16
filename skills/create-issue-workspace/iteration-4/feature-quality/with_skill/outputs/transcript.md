# WF1 create-issue — Transcript

**User request:** Create an issue requesting WebSocket support for realtime updates in the sentinel-app API.

---

## Config-loading

No prior `/rawgentic:switch` binding. `claude_docs/session_registry.jsonl` does not exist. Fell back to `.rawgentic_workspace.json` at the workspace root (the fixture dir).

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

Exactly one project `active == true` → **sentinel-app**. `disabledSkills` is empty (create-issue not disabled). `bmadDetected` not present. `activeProject.path = ./projects/sentinel-app` resolved against the workspace root.

### Derive capabilities (tested source of truth)

Command (run from workspace root):
```bash
python3 hooks/capabilities_lib.py derive --config ./projects/sentinel-app/.rawgentic.json
```

Output (EXIT=0):
```json
{"config":{"version":1,"project":{"name":"sentinel-app","type":"web-service","description":"Realtime API service for the eval harness"},"repo":{"provider":"github","fullName":"octo-eval/sentinel-app","defaultBranch":"trunk"},"protectionLevel":"sandbox","techStack":["node","javascript"],"testing":{"frameworks":[{"name":"vitest","type":"unit","command":"npm test","testDir":"test"}]},"ci":{"provider":"github-actions","workflowDir":".github/workflows"},"documentation":{"primaryFiles":["README.md"],"format":"markdown"},"custom":{}},"capabilities":{"repo":"octo-eval/sentinel-app","default_branch":"trunk","project_type":"web-service","has_tests":true,"test_commands":["npm test"],"has_ci":true,"has_deploy":false,"deploy_method":null,"has_database":false,"migration_dir":null,"has_docker":false}}
```

**Resolved capabilities carried forward as literals:**
- `repo = octo-eval/sentinel-app`
- `default_branch = trunk`
- `project_type = web-service`, tests = vitest (`npm test`), CI = github-actions, no deploy/db/docker.

---

## Step 1: Understand the request

**Classification:** Feature request (new functionality — WebSocket realtime push). Unambiguous.

**Dedup check:**
```bash
/tmp/ci-iter4/feature-quality/with_skill/fixture/bin/gh issue list --repo octo-eval/sentinel-app --search "websocket realtime updates" --limit 10
```
Output: *(empty)* — EXIT=0. No existing/duplicate issues. Clean repo.

---

## Step 2: Draft (codebase grounding)

Read the matching template `projects/sentinel-app/.github/ISSUE_TEMPLATE/feature_request.md` (Description, Acceptance Criteria, Scope, Affected Components, Risk Assessment, Complexity, Related Issues).

**Verified components against real code (Grep/Read, no Serena):**
- `src/server.js` — exports `startServer(port)` and `handleConnection(socket)`. (`handleConnection(socket)` is the natural WebSocket integration point.)
- `src/errorHandler.js` — exports `handleError(err, req, res)`.
- `README.md` — "Realtime API service. Modules: src/server.js, src/errorHandler.js."

No hallucinated components. No fabricated metrics (the request gave none; criteria are grounded in existing components and the vitest harness). Request is appropriately scoped, not over-broad; out-of-scope items listed explicitly.

Labels available (`gh label list`):
```
bug         Something isn't working   d73a4a
enhancement New feature or request    a2eeef
```
No `websocket`/`api` scope label exists; used only the existing `enhancement` label (did not invent new labels).

---

## Step 3: User review

Presented the draft below. User (cooperative) confirmed classification (feature) and replied **approved**.

### Draft

**Title:** `feat(server): add WebSocket support for realtime updates`
**Type:** Feature · **Labels:** `enhancement` · **Complexity:** M

> ## Description
>
> The sentinel-app API is described as a "Realtime API service" (README.md and the `.rawgentic.json` project description), but the current server (`src/server.js`) exposes only `startServer(port)` and `handleConnection(socket)` with no realtime push mechanism. Clients have no way to receive server-initiated updates and must poll. This issue requests first-class WebSocket support so the API can push realtime updates to connected clients.
>
> ## Acceptance Criteria
> 1. The server accepts WebSocket upgrade requests on the existing HTTP listener started by `startServer(port)` (no separate port required).
> 2. A new connection is routed through `handleConnection(socket)` in `src/server.js`, which establishes and tracks the WebSocket session.
> 3. The server can broadcast a realtime update message to all connected WebSocket clients, and a unit test (vitest, `npm test`) asserts that a subscribed client receives a broadcast payload.
> 4. WebSocket errors (failed upgrade, mid-session socket error, abrupt disconnect) are routed through the centralized error path in `src/errorHandler.js` (`handleError`) rather than crashing the process, with a regression test covering a forced socket error.
> 5. Connections are cleaned up on close/disconnect so closed sockets are removed from the tracked-client set (verified by test).
>
> ## Scope
> In scope: WebSocket upgrade handling on the existing listener, connection lifecycle tracking, server-to-client broadcast, error routing through `errorHandler`, unit tests.
> Out of scope: auth/authz of WS clients, message schema/protocol versioning, horizontal-scaling fan-out (multi-instance pub/sub), client-side SDK changes (deferred to follow-ups).
>
> ## Affected Components (verified)
> - `src/server.js` — `startServer(port)`, `handleConnection(socket)`
> - `src/errorHandler.js` — `handleError(err, req, res)`
> - `test/` — new vitest unit tests (`npm test`)
>
> ## Risk Assessment
> Medium. Adds a long-lived connection type and shared client-tracking state to a currently stateless path; risks are resource leaks from untracked/unclosed sockets and unhandled socket errors crashing the process — mitigated by AC4 and AC5. Library choice (e.g. `ws`) left to implementation.
>
> ## Complexity: M
>
> ## Related Issues: None (dedup returned no results).

---

## Step 4: Create the issue

Wrote body to `/tmp/wf1-issue-body.md` via heredoc (2689 bytes written).

```bash
/tmp/ci-iter4/feature-quality/with_skill/fixture/bin/gh issue create --repo octo-eval/sentinel-app \
  --title "feat(server): add WebSocket support for realtime updates" \
  --body-file /tmp/wf1-issue-body.md \
  --label "enhancement"
```
Output (EXIT=0):
```
https://github.com/octo-eval/sentinel-app/issues/101
```

**Liveness verification — `.gh-mock/created-issue.json`:**
```json
{
  "repo": "octo-eval/sentinel-app",
  "title": "feat(server): add WebSocket support for realtime updates",
  "labels": [
    "enhancement"
  ],
  "body_len": 2683,
  "ts": 1781590762
}
```

**`.gh-mock/calls.log`:**
```
gh issue list --repo octo-eval/sentinel-app --search websocket realtime updates --limit 10
gh label list --repo octo-eval/sentinel-app
gh issue create --repo octo-eval/sentinel-app --title feat(server): add WebSocket support for realtime updates --body-file /tmp/wf1-issue-body.md --label enhancement
```

Cleanup: `rm -f /tmp/wf1-issue-body.md` (confirmed removed).

---

## Step 5: Wrap up

Appended to `claude_docs/session_notes.md`:
```
### WF1 create-issue — DONE (https://github.com/octo-eval/sentinel-app/issues/101, feature, feat(server): add WebSocket support for realtime updates)
```

### Final result
```
Issue created: https://github.com/octo-eval/sentinel-app/issues/101
Type: feature · Title: feat(server): add WebSocket support for realtime updates · Labels: enhancement
```

Did not offer to start implementation (WF2 is invoked separately by the user).
