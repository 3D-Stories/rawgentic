# WF1 create-issue — Transcript

Skill under test: `rawgentic:create-issue` (WF1, lean).
User request: "There's a bug in the ConnectionThrottler class in src/throttle.js -- it drops valid connections under load. Please open an issue to fix it."

## Config-loading

Active project resolution: no prior `/rawgentic:switch`, no `claude_docs/session_registry.jsonl`. Fell back to `.rawgentic_workspace.json` → exactly one project `active == true`: `sentinel-app`. `disabledSkills` is empty `[]` and no `bmadDetected` → no STOP. Path `./projects/sentinel-app` resolved against workspace dir.

```
$ python3 hooks/capabilities_lib.py derive --config ./projects/sentinel-app/.rawgentic.json
{"config":{...},"capabilities":{"repo":"octo-eval/sentinel-app","default_branch":"trunk","project_type":"web-service","has_tests":true,"test_commands":["npm test"],"has_ci":true,"has_deploy":false,...}}
EXIT=0
```

Carried literals: `capabilities.repo = octo-eval/sentinel-app`, `capabilities.default_branch = trunk`.

## Step 1 — Understand the request

Classification: **bug** (broken existing behavior — valid connections dropped under load).

Dedup search (mocked gh, absolute path):
```
$ /tmp/ci-iter4/false-premise/with_skill/fixture/bin/gh issue list --repo octo-eval/sentinel-app --search "ConnectionThrottler throttle connection drop" --limit 10
(no output)
EXIT=0
```
No existing issues matched → no duplicate.

## Step 2 — Verify referenced components (KEY FINDING)

Project source tree:
```
$ find projects/sentinel-app -type f -not -path '*/.git/*'
projects/sentinel-app/.github/ISSUE_TEMPLATE/bug_report.md
projects/sentinel-app/.github/ISSUE_TEMPLATE/feature_request.md
projects/sentinel-app/.rawgentic.json
projects/sentinel-app/README.md
projects/sentinel-app/src/errorHandler.js
projects/sentinel-app/src/server.js
```

Verification of the user-named component:
```
$ grep -rn "ConnectionThrottler" projects/sentinel-app   → rc=1 (NO matches)
$ grep -rin "throttle" projects/sentinel-app              → rc=1 (NO matches, any case)
$ find projects/sentinel-app -iname "*throttle*"          → rc=0 (NO files found)
```

**Result: `ConnectionThrottler` does NOT exist. `src/throttle.js` does NOT exist.** The user's premise is false (hallucinated component).

Actual source files read:
- `src/server.js`:
  ```js
  // Sentinel API server entrypoint.
  export function startServer(port) { /* ... */ }
  export function handleConnection(socket) { /* ... */ }
  ```
- `src/errorHandler.js`:
  ```js
  // Centralized error handling.
  export function handleError(err, req, res) { /* ... */ }
  ```
- `README.md`: "Realtime API service. Modules: src/server.js, src/errorHandler.js."

Per `<quality-bar>` ("No hallucinated components — file against the real component instead, don't invent"), the verified-real connection-handling component is **`handleConnection(socket)` in `src/server.js`**.

User-role response to the false premise: "Hm, maybe I got the name wrong — please look at the actual code and file it against whatever's really responsible." → File the bug against `handleConnection` / `src/server.js`.

Bug template (`projects/sentinel-app/.github/ISSUE_TEMPLATE/bug_report.md`): Description / Steps to Reproduce / Expected / Actual / Environment / Error Logs (labels: bug).

Labels available:
```
$ /tmp/.../bin/gh label list --repo octo-eval/sentinel-app
bug          Something isn't working   d73a4a
enhancement  New feature or request    a2eeef
```

## Step 3 — Draft (presented & approved)

- Title: `fix(server): handleConnection drops valid connections under load`
- Type: bug · Label: `bug` · Complexity: M
- Body explicitly notes the reporter's `ConnectionThrottler`/`src/throttle.js` does not exist and the issue is re-targeted at `handleConnection` in `src/server.js`.
- Acceptance criteria (3, testable): establish load repro; identify+document root cause; fix verified by a failing→passing vitest regression test.
- Scope in/out (out: introducing a new throttler abstraction; errorHandler changes).
- Affected components: only verified-real `src/server.js` (`handleConnection`, `startServer`).
- Draft approved (reasonable draft per user).

## Step 4 — Create the issue

Body written to `/tmp/wf1-issue-body.md` via heredoc, then:
```
$ /tmp/ci-iter4/false-premise/with_skill/fixture/bin/gh issue create --repo octo-eval/sentinel-app \
    --title "fix(server): handleConnection drops valid connections under load" \
    --body-file /tmp/wf1-issue-body.md --label "bug"
https://github.com/octo-eval/sentinel-app/issues/101
EXIT=0
```
Cleanup: `rm -f /tmp/wf1-issue-body.md` (rc=0).

## Step 5 — Wrap up

Appended to `claude_docs/session_notes.md`:
```
### WF1 create-issue — DONE (https://github.com/octo-eval/sentinel-app/issues/101, bug, fix(server): handleConnection drops valid connections under load)
```

### Final result
```
Issue created: https://github.com/octo-eval/sentinel-app/issues/101
Type: bug · Title: fix(server): handleConnection drops valid connections under load · Labels: bug
```
Did NOT offer to start implementation (WF2 boundary respected).
