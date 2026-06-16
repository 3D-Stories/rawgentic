# Transcript: Create issue for improving error handling across the service

## Task
Create a GitHub issue for improving error handling across the sentinel-app service.
Determine the correct repository from project configuration, inspect issue templates
and source files, check for a similar existing issue first, then create the issue
using the mocked `gh` CLI (invoked by absolute path
`/tmp/ci-iter2/config-decoy/without_skill/fixture/bin/gh`).

---

## 1. Establish working directory

```
$ cd /tmp/ci-iter2/config-decoy/without_skill/fixture && pwd
/tmp/ci-iter2/config-decoy/without_skill/fixture
```

## 2. Explore workspace structure and config

```
$ ls -la
total 40
drwxrwxr-x 8 ... .gh-mock
-rw-rw-r-- 1 ... .rawgentic_workspace.json
-rw-rw-r-- 1 ... CLAUDE.md
drwxrwxr-x 2 ... bin
drwxrwxr-x 2 ... claude_docs
drwxrwxr-x 2 ... hooks
drwxrwxr-x 2 ... outputs
drwxrwxr-x 3 ... projects

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

$ ls -la projects/
drwxrwxr-x ... sentinel-app
```

The active project is `sentinel-app` at `./projects/sentinel-app`.

## 3. Read root CLAUDE.md and list project files

```
$ cat CLAUDE.md
# sentinel-app workspace

Primary repository: octo-eval/sentinel-legacy
Default branch: main

Contributions go through the standard PR flow.

$ find projects/sentinel-app -type f -not -path '*/.git/*' | sort
projects/sentinel-app/.github/ISSUE_TEMPLATE/bug_report.md
projects/sentinel-app/.github/ISSUE_TEMPLATE/feature_request.md
projects/sentinel-app/.rawgentic.json
projects/sentinel-app/README.md
projects/sentinel-app/src/errorHandler.js
projects/sentinel-app/src/server.js
```

**DISCREPANCY NOTED:** The root `CLAUDE.md` says "Primary repository: octo-eval/sentinel-legacy"
with default branch `main`. This is a decoy/stale value. The authoritative,
structured per-project config (`projects/sentinel-app/.rawgentic.json`) is the
source of truth for the repo and must be checked (see next step).

## 4. Read project config and README

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

$ cat projects/sentinel-app/README.md
# sentinel-app
Realtime API service. Modules: src/server.js, src/errorHandler.js.
```

**REPO DECISION:** Target repo is **`octo-eval/sentinel-app`**, taken from
`projects/sentinel-app/.rawgentic.json` -> `repo.fullName`. This is the active
project's own structured configuration and is the authoritative source for where
issues are filed. The root `CLAUDE.md` value (`octo-eval/sentinel-legacy`) is a
mismatched decoy — "legacy" naming, a conflicting default branch (`main` vs the
project config's `trunk`), and no corroboration anywhere else (no git remote, README
names only `sentinel-app`). The per-project config wins.

## 5. Check git remote (none) and read issue templates

```
$ (cd projects/sentinel-app && git remote -v; git config --get remote.origin.url; echo "rc: $?")
git status rc: 1        # not a git repo / no remote — no remote signal to corroborate

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

**TEMPLATE DECISION:** "Improving error handling across the service" is an
enhancement / new-functionality effort, not a report of broken behavior, so the
**feature_request.md** template applies (label: `enhancement`).

## 6. Read source files (to write a substantive, accurate issue)

```
$ cat projects/sentinel-app/src/server.js
// Sentinel API server entrypoint.
export function startServer(port) { /* ... */ }
export function handleConnection(socket) { /* ... */ }

$ cat projects/sentinel-app/src/errorHandler.js
// Centralized error handling.
export function handleError(err, req, res) { /* ... */ }
```

Modules referenced in the issue: `src/errorHandler.js` (`handleError`) and
`src/server.js` (`startServer`, `handleConnection`).

## 7. Inspect the mock gh interface

Read `bin/gh`. Supported subcommands: `auth status`, `issue list`, `issue view`,
`label list`, `label create`, `issue create`. `issue create` accepts `--repo`,
`--title`, `--body-file`/`--body`, repeated `--label`. Seed data lives in
`.gh-mock/` (`seed_issues.tsv` was 0 bytes -> clean repo, no seeded duplicates).

## 8. Verify auth and check for a similar/duplicate issue BEFORE creating

```
$ GH=/tmp/ci-iter2/config-decoy/without_skill/fixture/bin/gh

$ "$GH" auth status
github.com
  Logged in to github.com account eval-bot (oauth_token)
  Token scopes: 'repo', 'read:org'

$ "$GH" issue list --repo octo-eval/sentinel-app --search "error handling" --state all --limit 30
            # (no output) -> exit 0, no matching issues

$ "$GH" issue list --repo octo-eval/sentinel-app --search "error handler" --state all --limit 30
            # (no output) -> exit 0, no matching issues

$ "$GH" label list --repo octo-eval/sentinel-app
bug         Something isn't working   d73a4a
enhancement New feature or request    a2eeef
```

**DEDUP RESULT:** Two searches (all states) returned nothing — no existing or
duplicate issue. Safe to create. The `enhancement` label already exists (matches
the feature_request template), so no label creation needed.

## 9. Create the issue

Body written to `.gh-mock/new-issue-body.md` following the feature_request template
(Description, Acceptance Criteria, Scope, Affected Components, Risk Assessment,
Complexity, Related Issues), grounded in the actual source modules.

```
$ "$GH" issue create \
    --repo octo-eval/sentinel-app \
    --title "Improve error handling across the service" \
    --body-file /tmp/ci-iter2/config-decoy/without_skill/fixture/.gh-mock/new-issue-body.md \
    --label enhancement
https://github.com/octo-eval/sentinel-app/issues/101
[exit 0]
```

## 10. Verify the mock recorded the creation

```
$ cat .gh-mock/created-issue.json
{
  "repo": "octo-eval/sentinel-app",
  "title": "Improve error handling across the service",
  "labels": [
    "enhancement"
  ],
  "body_len": 2415,
  "ts": 1781586991
}

$ cat .gh-mock/calls.log
gh auth status
gh issue list --repo octo-eval/sentinel-app --search error handling --state all --limit 30
gh issue list --repo octo-eval/sentinel-app --search error handler --state all --limit 30
gh label list --repo octo-eval/sentinel-app
gh issue create --repo octo-eval/sentinel-app --title Improve error handling across the service --body-file /tmp/ci-iter2/config-decoy/without_skill/fixture/.gh-mock/new-issue-body.md --label enhancement
```

---

## Final result

- **Repository targeted:** `octo-eval/sentinel-app`
  - **Why:** Source of truth is the active project's own structured config,
    `projects/sentinel-app/.rawgentic.json` (`repo.fullName = octo-eval/sentinel-app`).
    The root `CLAUDE.md` "octo-eval/sentinel-legacy" is a stale/decoy value
    (conflicting default branch, "legacy" naming, uncorroborated by README or any
    git remote) and was correctly overridden by the per-project config.
- **Template used:** `feature_request.md` (enhancement), since improving error
  handling is new/enhanced functionality, not a bug report.
- **Dedup check:** Performed two `issue list` searches (`error handling`,
  `error handler`, `--state all`) before creating; no similar issue existed.
- **Issue title:** "Improve error handling across the service"
- **Label:** `enhancement` (pre-existing)
- **Issue URL:** https://github.com/octo-eval/sentinel-app/issues/101
