# Transcript — Add WebSocket support issue request

## User request
> I want to add WebSocket support to the API so clients get realtime updates. Please open an issue for it.

Instruction: inspect the project, check whether a similar issue already exists, and handle it sensibly before creating anything. Use the mock `gh` (absolute path).

---

## 1. Explore the project

### `cat .rawgentic_workspace.json`
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

### `find projects/sentinel-app -type f`
```
projects/sentinel-app/.github/ISSUE_TEMPLATE/bug_report.md
projects/sentinel-app/.github/ISSUE_TEMPLATE/feature_request.md
projects/sentinel-app/.rawgentic.json
projects/sentinel-app/README.md
projects/sentinel-app/src/errorHandler.js
projects/sentinel-app/src/server.js
```

### `cat projects/sentinel-app/.rawgentic.json` (key fields)
```json
{
  "project": { "name": "sentinel-app", "type": "web-service", "description": "Realtime API service for the eval harness" },
  "repo": { "provider": "github", "fullName": "octo-eval/sentinel-app", "defaultBranch": "trunk" },
  "techStack": ["node", "javascript"]
}
```
- Repo for `gh`: **octo-eval/sentinel-app**

### `cat projects/sentinel-app/README.md`
```
# sentinel-app
Realtime API service. Modules: src/server.js, src/errorHandler.js.
```

### Source
`src/server.js`
```js
// Sentinel API server entrypoint.
export function startServer(port) { /* ... */ }
export function handleConnection(socket) { /* ... */ }
```
`src/errorHandler.js`
```js
// Centralized error handling.
export function handleError(err, req, res) { /* ... */ }
```

### Issue template that would apply (`feature_request.md`)
```
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
```
(`bug_report.md` also present but not relevant — this is a feature.)

---

## 2. Verify auth (mock gh)

Command:
```
/tmp/ci-iter2/dedup-hit/without_skill/fixture/bin/gh auth status
```
Output (stderr):
```
github.com
  Logged in to github.com account eval-bot (oauth_token)
  Token scopes: 'repo', 'read:org'
```

---

## 3. Duplicate check (BEFORE creating anything)

### Search 1 — "websocket"
Command:
```
/tmp/ci-iter2/dedup-hit/without_skill/fixture/bin/gh issue list --repo octo-eval/sentinel-app --search "websocket" --state all --limit 20
```
Output:
```
42	feat(server): add WebSocket support for realtime updates	enhancement	2026-05-01T00:00:00Z
```

### Search 2 — "realtime updates"
Command:
```
/tmp/ci-iter2/dedup-hit/without_skill/fixture/bin/gh issue list --repo octo-eval/sentinel-app --search "realtime updates" --state all --limit 20
```
Output:
```
42	feat(server): add WebSocket support for realtime updates	enhancement	2026-05-01T00:00:00Z
```

### Inspect the match
Command:
```
/tmp/ci-iter2/dedup-hit/without_skill/fixture/bin/gh issue view 42 --repo octo-eval/sentinel-app
```
Output:
```
title:	stub
state:	OPEN
```

### Dedup result
**A near-identical issue already exists and is OPEN.**

| # | Title | Labels | State | Updated |
|---|-------|--------|-------|---------|
| 42 | feat(server): add WebSocket support for realtime updates | enhancement | OPEN | 2026-05-01 |

The user's request ("add WebSocket support to the API so clients get realtime updates") maps directly onto issue #42, both in subject (WebSocket support) and in motivation (realtime updates to clients). It is the same feature.

---

## 4. Decision — handle sensibly

Creating a second issue would be a duplicate. The sensible action is **NOT to create a new issue**. Instead, point the user at the existing open issue #42 so the discussion/work stays consolidated there.

- No `gh issue create` was run.
- No `gh label create` was run (the existing `enhancement` label already covers it, and no new issue is being filed).

---

## 5. Final outcome

No new issue created. An existing open issue already tracks this exact request:

- **#42 — feat(server): add WebSocket support for realtime updates** (label: `enhancement`, state: OPEN)
- URL: https://github.com/octo-eval/sentinel-app/issues/42

Recommendation to the user: add any additional detail (acceptance criteria, affected components — `src/server.js#handleConnection`, `src/server.js#startServer`) as a comment on #42 rather than opening a duplicate. If #42 turns out to be stale or scoped differently than intended, reopen the conversation there or explicitly request a fresh issue.
