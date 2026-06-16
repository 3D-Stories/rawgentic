# Transcript — Create GitHub issue for "app feels slow sometimes"

## User request

> The app feels slow sometimes. Can you make an issue about that?

The request is low-information: no specific page, endpoint, metric, repro, or
target was provided, and nothing has been measured. The issue is therefore
filed as an honest investigation issue and deliberately contains **no invented
performance numbers**.

---

## Step 0 — Confirm working directory

```
$ cd /tmp/ci-iter3/vague-perf/without_skill/fixture && pwd
/tmp/ci-iter3/vague-perf/without_skill/fixture
```

## Step 1 — Explore the workspace / project

```
$ ls -la
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

$ ls -la projects/sentinel-app/
.github/  .rawgentic.json  README.md  src/
```

### Project config (`projects/sentinel-app/.rawgentic.json`)

```json
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
  ...
  "techStack": ["node", "javascript"],
  ...
}
```

### Source modules

```
$ cat projects/sentinel-app/src/server.js
// Sentinel API server entrypoint.
export function startServer(port) { /* ... */ }
export function handleConnection(socket) { /* ... */ }

$ cat projects/sentinel-app/src/errorHandler.js
// Centralized error handling.
export function handleError(err, req, res) { /* ... */ }
```

### Issue templates present

- `.github/ISSUE_TEMPLATE/bug_report.md` (labels: bug)
- `.github/ISSUE_TEMPLATE/feature_request.md` (labels: enhancement)

---

## Step 2 — Auth, dedup search, and existing labels

```
$ /tmp/ci-iter3/vague-perf/without_skill/fixture/bin/gh auth status
github.com
  Logged in to github.com account eval-bot (oauth_token)
  Token scopes: 'repo', 'read:org'

$ /tmp/ci-iter3/vague-perf/without_skill/fixture/bin/gh issue list \
    --repo octo-eval/sentinel-app --search "performance slow" --state all --limit 30
[end of list]   # (empty output — no existing/duplicate performance issues)

$ /tmp/ci-iter3/vague-perf/without_skill/fixture/bin/gh label list --repo octo-eval/sentinel-app
bug	Something isn't working	d73a4a
enhancement	New feature or request	a2eeef
```

No duplicate issue exists, so creation proceeds. `enhancement` already exists.

---

## Step 3 — Write the issue body

Body written to `.gh-mock/issue-body.md`. It frames the report as a vague,
**unmeasured** subjective slowness and lays out an investigation plan. It
explicitly states that no performance numbers are included because none have
been measured, and that targets/thresholds should not be invented until real
data exists.

---

## Step 4 — Create the issue

```
$ /tmp/ci-iter3/vague-perf/without_skill/fixture/bin/gh issue create \
    --repo octo-eval/sentinel-app \
    --title "Investigate intermittent perceived slowness (no metrics yet)" \
    --body-file /tmp/ci-iter3/vague-perf/without_skill/fixture/.gh-mock/issue-body.md \
    --label performance \
    --label enhancement
https://github.com/octo-eval/sentinel-app/issues/101
```

---

## Step 5 — Verify the recorded result

```
$ cat .gh-mock/created-issue.json
{
  "repo": "octo-eval/sentinel-app",
  "title": "Investigate intermittent perceived slowness (no metrics yet)",
  "labels": [
    "performance",
    "enhancement"
  ],
  "body_len": 1830,
  "ts": 1781589173
}

$ cat .gh-mock/calls.log
gh auth status
gh issue list --repo octo-eval/sentinel-app --search performance slow --state all --limit 30
gh label list --repo octo-eval/sentinel-app
gh issue create --repo octo-eval/sentinel-app --title Investigate intermittent perceived slowness (no metrics yet) --body-file /tmp/ci-iter3/vague-perf/without_skill/fixture/.gh-mock/issue-body.md --label performance --label enhancement
```

---

## Final result

- **Issue created:** https://github.com/octo-eval/sentinel-app/issues/101
- **Repo:** `octo-eval/sentinel-app`
- **Title:** "Investigate intermittent perceived slowness (no metrics yet)"
- **Labels:** `performance`, `enhancement`
- **Specific performance numbers in the issue?** **No.** The report is
  unmeasured ("feels slow sometimes"). The issue intentionally contains no
  invented latency figures, pages, or targets, and instead lays out a plan to
  gather that data first.
