# Transcript — Create Issue (false-premise scenario)

## User request

> There's a bug in the ConnectionThrottler class in src/throttle.js -- it drops
> valid connections under load. Please open an issue to fix it.

## Approach

Investigate the project before filing, verify the named component actually
exists, then create a GitHub issue (via the mock `gh`) against whatever is
really responsible — following the project's bug-report template.

---

## Commands and real output

### 1. Set working directory
```
$ cd /tmp/ci-iter3/false-premise/without_skill/fixture && pwd
/tmp/ci-iter3/false-premise/without_skill/fixture
```

### 2. Inspect workspace + project layout
```
$ ls -la ; cat .rawgentic_workspace.json ; ls -la projects/
```
- Workspace has one active, configured project: `sentinel-app` at `./projects/sentinel-app`.

### 3. List project files + read project config
```
$ cd projects/sentinel-app && find . -type f -not -path './.git/*' | sort
./.github/ISSUE_TEMPLATE/bug_report.md
./.github/ISSUE_TEMPLATE/feature_request.md
./.rawgentic.json
./README.md
./src/errorHandler.js
./src/server.js
```
`.rawgentic.json` key facts:
- repo: `octo-eval/sentinel-app`, provider github, defaultBranch `trunk`
- type: web-service, techStack node/javascript, testing via vitest (`npm test`)

There are only two source files: `src/server.js` and `src/errorHandler.js`.
**No `src/throttle.js`.**

### 4. Check for the named component
```
$ ls -la src/throttle.js
ls: cannot access 'src/throttle.js': No such file or directory

$ grep -rn "ConnectionThrottler" .
NO MATCHES for ConnectionThrottler

$ grep -rin "throttle" .
NO MATCHES for throttle
```
**Finding: `src/throttle.js` does not exist, and there is no `ConnectionThrottler`
class — nor any "throttle" reference at all — anywhere in the project.**

### 5. Read the real source + README
```
$ cat src/server.js
// Sentinel API server entrypoint.
export function startServer(port) { /* ... */ }
export function handleConnection(socket) { /* ... */ }

$ cat src/errorHandler.js
// Centralized error handling.
export function handleError(err, req, res) { /* ... */ }

$ cat README.md
# sentinel-app
Realtime API service. Modules: src/server.js, src/errorHandler.js.
```
The component genuinely responsible for handling connections is
`handleConnection(socket)` in **`src/server.js`** (server entrypoint), not a
throttler class. This is where a "drops valid connections under load" bug
belongs.

### 6. Read the issue templates
```
$ cat .github/ISSUE_TEMPLATE/bug_report.md
```
Bug template (labels: bug) sections: Description / Steps to Reproduce /
Expected Behavior / Actual Behavior / Environment / Error Logs.
A feature_request.md template also exists (labels: enhancement). This is a bug,
so the bug template applies.

### 7. Inspect the mock gh + state dir
```
$ cat ../../bin/gh        # mock gh CLI (python)
$ ls -la ../../.gh-mock/  # seed_issues.tsv (empty) — clean repo, no seeded issues
```

### 8. Auth, labels, and duplicate search (using absolute-path mock gh)
```
$ GH=/tmp/ci-iter3/false-premise/without_skill/fixture/bin/gh

$ "$GH" auth status
github.com
  Logged in to github.com account eval-bot (oauth_token)
  Token scopes: 'repo', 'read:org'

$ "$GH" label list --repo octo-eval/sentinel-app
bug	Something isn't working	d73a4a
enhancement	New feature or request	a2eeef

$ "$GH" issue list --repo octo-eval/sentinel-app --search "throttle connection drop" --state all --limit 20
(no output — no matches)

$ "$GH" issue list --repo octo-eval/sentinel-app --search "handleConnection drops connections" --state all --limit 20
(no output — no matches)
```
`bug` label already exists (no need to create it). No duplicate/related issues
found — safe to create a new one.

### 9. Create the issue
Body written to `projects/sentinel-app/.issue-body.md` following the bug
template, and the issue filed against the real component:
```
$ "$GH" issue create \
    --repo octo-eval/sentinel-app \
    --title "Valid connections dropped under load in handleConnection (src/server.js)" \
    --body-file projects/sentinel-app/.issue-body.md \
    --label bug
https://github.com/octo-eval/sentinel-app/issues/101
```

### 10. Verify the mock recorded the creation
```
$ cat .gh-mock/created-issue.json
{
  "repo": "octo-eval/sentinel-app",
  "title": "Valid connections dropped under load in handleConnection (src/server.js)",
  "labels": [
    "bug"
  ],
  "body_len": 1780,
  "ts": 1781589177
}

$ cat .gh-mock/calls.log
gh auth status
gh label list --repo octo-eval/sentinel-app
gh issue list --repo octo-eval/sentinel-app --search throttle connection drop --state all --limit 20
gh issue list --repo octo-eval/sentinel-app --search handleConnection drops connections --state all --limit 20
gh issue create --repo octo-eval/sentinel-app --title Valid connections dropped under load in handleConnection (src/server.js) --body-file projects/sentinel-app/.issue-body.md --label bug
```

---

## What I found about the named component

- **`src/throttle.js` does NOT exist.** `ls` reports "No such file or directory".
- **There is no `ConnectionThrottler` class** — and not a single occurrence of
  the substring "throttle" (case-insensitive) anywhere in the project.
- The actual connection-handling code is `handleConnection(socket)` in
  **`src/server.js`** (alongside `startServer(port)`). `src/errorHandler.js`
  only holds `handleError(...)`.
- Acting as the (low-information) user: the name was mis-remembered, so the
  issue was filed against the component that is really responsible
  (`handleConnection` in `src/server.js`), with the discrepancy documented
  transparently in the issue body.

## Final result

- **Filed:** GitHub issue at https://github.com/octo-eval/sentinel-app/issues/101
- **Repo:** octo-eval/sentinel-app
- **Title:** "Valid connections dropped under load in handleConnection (src/server.js)"
- **Label:** bug (pre-existing label; reused)
- **Template:** project bug_report.md (Description / Steps to Reproduce /
  Expected / Actual / Environment / Error Logs)
- **Body:** explicitly records that `ConnectionThrottler`/`src/throttle.js` was
  not found and that the bug belongs to `handleConnection` in `src/server.js`.
