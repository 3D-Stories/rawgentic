# Transcript — Create issue for reliability overhaul

User request: "I want to completely overhaul error handling, logging, monitoring,
retries, and alerting across the entire service. Open an issue for it."

All `gh` calls use the absolute path to the mocked CLI:
`/tmp/ci-iter3/over-broad/without_skill/fixture/bin/gh`

---

## 1. Enter working directory

```
$ cd /tmp/ci-iter3/over-broad/without_skill/fixture && pwd && ls -la
/tmp/ci-iter3/over-broad/without_skill/fixture
total 36
drwxrwxr-x 8 rocky00717 rocky00717 4096 Jun 15 22:51 .
drwxrwxr-x 3 rocky00717 rocky00717 4096 Jun 15 22:51 ..
drwxrwxr-x 2 rocky00717 rocky00717 4096 Jun 15 22:51 .gh-mock
-rw-rw-r-- 1 rocky00717 rocky00717  310 Jun 15 22:51 .rawgentic_workspace.json
drwxrwxr-x 2 rocky00717 rocky00717 4096 Jun 15 22:51 bin
drwxrwxr-x 2 rocky00717 rocky00717 4096 Jun 15 22:51 claude_docs
drwxrwxr-x 2 rocky00717 rocky00717 4096 Jun 15 22:51 hooks
drwxrwxr-x 2 rocky00717 rocky00717 4096 Jun 15 22:51 outputs
drwxrwxr-x 3 rocky00717 rocky00717 4096 Jun 15 22:51 projects
```

## 2. Inspect workspace + project

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
      ...
    }
  ]
}

$ cat projects/sentinel-app/.rawgentic.json
{
  "version": 1,
  "project": { "name": "sentinel-app", "type": "web-service",
               "description": "Realtime API service for the eval harness" },
  "repo": { "provider": "github", "fullName": "octo-eval/sentinel-app",
            "defaultBranch": "trunk" },
  "techStack": ["node", "javascript"],
  "testing": { "frameworks": [ { "name": "vitest", "command": "npm test", "testDir": "test" } ] },
  ...
}

$ cat projects/sentinel-app/README.md
# sentinel-app
Realtime API service. Modules: src/server.js, src/errorHandler.js.
```

Source (stubs):

```
$ cat projects/sentinel-app/src/server.js
// Sentinel API server entrypoint.
export function startServer(port) { /* ... */ }
export function handleConnection(socket) { /* ... */ }

$ cat projects/sentinel-app/src/errorHandler.js
// Centralized error handling.
export function handleError(err, req, res) { /* ... */ }
```

Issue templates available: `.github/ISSUE_TEMPLATE/bug_report.md` and
`feature_request.md` (the feature template has Description / Acceptance Criteria /
Scope [In/Out] / Affected Components / Risk Assessment / Complexity / Related
Issues — I followed this structure).

## 3. Check auth, dedup existing issues, list labels (mock gh)

```
$ /tmp/ci-iter3/over-broad/without_skill/fixture/bin/gh auth status
github.com
  Logged in to github.com account eval-bot (oauth_token)
  Token scopes: 'repo', 'read:org'

$ /tmp/ci-iter3/over-broad/without_skill/fixture/bin/gh issue list \
    --repo octo-eval/sentinel-app --search "error handling logging monitoring" \
    --state all --limit 50
(no output — no matching issues)

$ /tmp/ci-iter3/over-broad/without_skill/fixture/bin/gh issue list \
    --repo octo-eval/sentinel-app --state all --limit 100
(no output — clean repo, no dedup hit)

$ /tmp/ci-iter3/over-broad/without_skill/fixture/bin/gh label list \
    --repo octo-eval/sentinel-app
bug	Something isn't working	d73a4a
enhancement	New feature or request	a2eeef
```

Decision: no duplicate exists. Available labels are `bug` and `enhancement`;
this is an enhancement, so I used the `enhancement` label (no new label needed).

## 4. Scoping decision

The request names five distinct cross-cutting concerns (error handling, logging,
monitoring, retries, alerting) "across the entire service." Filing five separate
issues for a two-stub-file service would be over-fragmented; filing one vague
"do everything" blob would be unactionable. As the implementer I chose **one
tracking/epic issue** that explicitly carves the work into the five separable
deliverables with hard In-scope / Out-of-scope boundaries, acceptance criteria,
a suggested implementation order, and Complexity = XL. This honors the user's
"open an issue" (singular) while keeping each concern independently shippable.

Out-of-scope boundaries carried on the issue:
- No unrelated business-logic / product features.
- No provisioning of external infra (hosted Prometheus/Grafana, PagerDuty) —
  only the in-app surface (metrics endpoint + alert rules).
- No distributed tracing (deferred; follow-up built on correlation IDs added here).
- No auth / rate limiting / other reliability concerns not named in the request.

## 5. Create the issue

Body written to `outputs/issue-body.md`, then:

```
$ /tmp/ci-iter3/over-broad/without_skill/fixture/bin/gh issue create \
    --repo octo-eval/sentinel-app \
    --title "Reliability overhaul: error handling, logging, monitoring, retries & alerting (epic)" \
    --body-file outputs/issue-body.md \
    --label enhancement
https://github.com/octo-eval/sentinel-app/issues/101
```

## 6. Verify the mock recorded it

```
$ cat .gh-mock/created-issue.json
{
  "repo": "octo-eval/sentinel-app",
  "title": "Reliability overhaul: error handling, logging, monitoring, retries & alerting (epic)",
  "labels": [
    "enhancement"
  ],
  "body_len": 4141,
  "ts": 1781589198
}

$ cat .gh-mock/calls.log
gh auth status
gh issue list --repo octo-eval/sentinel-app --search error handling logging monitoring --state all --limit 50
gh issue list --repo octo-eval/sentinel-app --state all --limit 100
gh label list --repo octo-eval/sentinel-app
gh issue create --repo octo-eval/sentinel-app --title Reliability overhaul: ... --body-file outputs/issue-body.md --label enhancement

$ wc -l .gh-mock/created-issue-body.md
88 .gh-mock/created-issue-body.md
```

---

## Final result

- **Issues filed:** 1 (a tracking/epic issue)
- **Issue URL:** https://github.com/octo-eval/sentinel-app/issues/101
- **Title:** Reliability overhaul: error handling, logging, monitoring, retries & alerting (epic)
- **Label:** enhancement
- **Scope boundaries:** Covers exactly the five named concerns applied to the
  existing `src/server.js` + `src/errorHandler.js` paths, broken into five
  separable per-PR deliverables with a suggested order. Explicitly excludes:
  unrelated business/product features; provisioning external infra (Prometheus/
  Grafana/PagerDuty) beyond the in-app metrics endpoint + alert rules; distributed
  tracing (deferred follow-up); and any reliability concern not named (auth, rate
  limiting). All thresholds required to be env-configurable.
