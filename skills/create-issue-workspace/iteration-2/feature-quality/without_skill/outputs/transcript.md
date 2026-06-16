# Transcript — Create issue: WebSocket support for realtime updates (sentinel-app)

User request: Create an issue requesting WebSocket support for realtime updates in the sentinel-app API.

All `gh` calls use the mock at the absolute path
`/tmp/ci-iter2/feature-quality/without_skill/fixture/bin/gh`.

---

## 1. Set working directory

```
$ cd /tmp/ci-iter2/feature-quality/without_skill/fixture && pwd
/tmp/ci-iter2/feature-quality/without_skill/fixture
```

## 2. Explore workspace + project

```
$ ls -la
.gh-mock/  .rawgentic_workspace.json  bin/  claude_docs/  hooks/  outputs/  projects/

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

$ ls -la projects/sentinel-app/
.github/  .rawgentic.json  README.md  src/
```

### Project config

```
$ cat projects/sentinel-app/.rawgentic.json
{
  "version": 1,
  "project": {
    "name": "sentinel-app",
    "type": "web-service",
    "description": "Realtime API service for the eval harness"
  },
  "repo": {
    "provider": "github",
    "fullName": "octo-eval/sentinel-app",
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
```

Key facts: repo = `octo-eval/sentinel-app`, default branch `trunk`, stack node/javascript, tests via vitest (`npm test`).

### README

```
$ cat projects/sentinel-app/README.md
# sentinel-app
Realtime API service. Modules: src/server.js, src/errorHandler.js.
```

### Source files

```
$ cat projects/sentinel-app/src/server.js
// Sentinel API server entrypoint.
export function startServer(port) { /* ... */ }
export function handleConnection(socket) { /* ... */ }

$ cat projects/sentinel-app/src/errorHandler.js
// Centralized error handling.
export function handleError(err, req, res) { /* ... */ }
```

### Issue templates

```
$ ls projects/sentinel-app/.github/ISSUE_TEMPLATE/
bug_report.md  feature_request.md

$ cat projects/sentinel-app/.github/ISSUE_TEMPLATE/feature_request.md
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

This is a feature request, so the `feature_request.md` template applies. Its sections were followed exactly, and its declared `labels: enhancement` was used.

## 3. Verify auth

```
$ /tmp/.../bin/gh auth status
github.com
  Logged in to github.com account eval-bot (oauth_token)
  Token scopes: 'repo', 'read:org'
```

## 4. Check for an existing similar issue (dedup)

```
$ /tmp/.../bin/gh issue list --repo octo-eval/sentinel-app --search "websocket" --state all --limit 50
(no output — no matching issues)

$ /tmp/.../bin/gh issue list --repo octo-eval/sentinel-app --search "realtime updates" --state all --limit 50
(no output — no matching issues)
```

No existing open or closed issue matches "websocket" or "realtime updates". Safe to create a new one.

## 5. Confirm labels exist

```
$ /tmp/.../bin/gh label list --repo octo-eval/sentinel-app
bug	Something isn't working	d73a4a
enhancement	New feature or request	a2eeef
```

`enhancement` exists already (matches the template), so no label creation needed.

## 6. Write the issue body (template-conformant)

Body written to `outputs/issue-body.md`, following every section of `feature_request.md`
(Description, Acceptance Criteria, Scope, Affected Components, Risk Assessment, Complexity,
Related Issues) and grounded in the actual source modules `src/server.js` (`handleConnection`)
and `src/errorHandler.js` (`handleError`).

## 7. Create the issue with the mock gh

```
$ /tmp/.../bin/gh issue create \
    --repo octo-eval/sentinel-app \
    --title "Add WebSocket support for realtime updates in the API" \
    --body-file outputs/issue-body.md \
    --label enhancement
https://github.com/octo-eval/sentinel-app/issues/101
```

## 8. Verify mock recorded the creation

```
$ cat .gh-mock/created-issue.json
{
  "repo": "octo-eval/sentinel-app",
  "title": "Add WebSocket support for realtime updates in the API",
  "labels": [
    "enhancement"
  ],
  "body_len": 2228,
  "ts": 1781586946
}

$ cat .gh-mock/calls.log
gh auth status
gh issue list --repo octo-eval/sentinel-app --search websocket --state all --limit 50
gh issue list --repo octo-eval/sentinel-app --search realtime updates --state all --limit 50
gh label list --repo octo-eval/sentinel-app
gh issue create --repo octo-eval/sentinel-app --title Add WebSocket support for realtime updates in the API --body-file outputs/issue-body.md --label enhancement

$ ls -la .gh-mock/created-issue-body.md
-rw-rw-r-- 1 rocky00717 rocky00717 2232 .gh-mock/created-issue-body.md
```

---

## Final result

Issue created successfully against `octo-eval/sentinel-app` with the `enhancement` label,
following the project's `feature_request.md` template. No duplicate existed.

**Issue URL: https://github.com/octo-eval/sentinel-app/issues/101**
