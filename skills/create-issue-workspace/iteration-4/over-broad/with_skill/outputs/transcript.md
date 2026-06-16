# WF1 create-issue — Transcript

Skill under test: `rawgentic:create-issue` (WF1, lean). Orchestrator followed it precisely.
GitHub CLI is mocked at `/tmp/ci-iter4/over-broad/with_skill/fixture/bin/gh` (invoked by absolute/relative path under the fixture, never bare `gh`).

## User request
> I want to completely overhaul error handling, logging, monitoring, retries, and alerting across the entire service. Open an issue for it.

This is the canonical **over-broad ask** the skill's `<quality-bar>` warns about: five distinct cross-cutting concerns ("do everything") across the whole service.

---

## Config loading

Command (from workspace root):
```bash
python3 hooks/capabilities_lib.py derive --config ./projects/sentinel-app/.rawgentic.json
```
Output (EXIT=0):
```json
{"config":{"version":1,"project":{"name":"sentinel-app","type":"web-service","description":"Realtime API service for the eval harness"},"repo":{"provider":"github","fullName":"octo-eval/sentinel-app","defaultBranch":"trunk"},"protectionLevel":"sandbox","techStack":["node","javascript"],"testing":{"frameworks":[{"name":"vitest","type":"unit","command":"npm test","testDir":"test"}]},"ci":{"provider":"github-actions","workflowDir":".github/workflows"},"documentation":{"primaryFiles":["README.md"],"format":"markdown"},"custom":{}},"capabilities":{"repo":"octo-eval/sentinel-app","default_branch":"trunk","project_type":"web-service","has_tests":true,"test_commands":["npm test"],"has_ci":true,"has_deploy":false,"deploy_method":null,"has_database":false,"migration_dir":null,"has_docker":false}}
```

Active-project resolution:
- `.rawgentic_workspace.json` has exactly one project `active: true` → `sentinel-app`. (Session registry `claude_docs/session_registry.jsonl` was absent; fell back to workspace file, single active.)
- `disabledSkills: []` → create-issue not disabled.
- `bmadDetected` not present → no BMAD gate.

Carried literals: **repo = `octo-eval/sentinel-app`**, **default_branch = `trunk`**.

---

## Step 1: Understand the request

**Classification: feature request.** Grounding the codebase first:

```
projects/sentinel-app/src/server.js        -> startServer(port), handleConnection(socket)   [stubs]
projects/sentinel-app/src/errorHandler.js  -> handleError(err, req, res)                     [stub]
projects/sentinel-app/README.md            -> "Realtime API service. Modules: src/server.js, src/errorHandler.js."
```

```bash
grep -rn "retr|logg|monitor|alert|metric" projects/sentinel-app/src/   # EXIT=1, no matches
```
None of the five concerns exist in source yet → this is greenfield resilience/observability work, **feature**, not a bug.

### Dedup check (Step 1.3)
```bash
./bin/gh issue list --repo octo-eval/sentinel-app --search "error handling logging monitoring retries alerting" --limit 10   # (empty)
./bin/gh issue list --repo octo-eval/sentinel-app --search "observability" --limit 10                                       # (empty)
./bin/gh issue list --repo octo-eval/sentinel-app --search "error" --limit 10                                               # (empty)
./bin/gh issue list --repo octo-eval/sentinel-app --limit 20                                                                # (empty)
```
Clean repo, no duplicates.

### Labels (Step 4.2, checked early)
```bash
./bin/gh label list --repo octo-eval/sentinel-app
# bug          Something isn't working   d73a4a
# enhancement  New feature or request    a2eeef
```
Only `enhancement`/`bug` exist. No fabricated component labels created (skill: create one "only if needed"). All issues are features → `enhancement`.

---

## How the breadth was handled (`<quality-bar>`: "Bound an over-broad ask")

The ask bundles five separable workstreams with different acceptance criteria. The skill permits either splitting OR one issue with an explicit out-of-scope list. I (orchestrator) chose a **split**, because each concern is independently implementable and lumping them would produce one unreviewable mega-issue (and would exceed the ~2000-word "probably several issues" heuristic).

Acting as the user, the answer to the structuring decision was: *"whatever you think is best — you decide how to structure it."* So the orchestrator made the call and the user approved the resulting reasonable draft.

**Chosen structure: 1 umbrella tracking issue + 5 scoped child issues.** Sequencing baked in (errors → logging → monitoring → retries → alerting) since later concerns consume earlier ones. Shared conventions (one log format, one error contract, env-var thresholds) pinned in the umbrella so the children compose instead of conflicting. Every issue's Affected Components lists only the two real files/three real symbols; thresholds are env-configurable per AC; no fabricated metrics/targets.

---

## Step 2-3: Drafts (presented and approved)

Template used: `projects/sentinel-app/.github/ISSUE_TEMPLATE/feature_request.md`
(Description / Acceptance Criteria / Scope in+out / Affected Components / Risk Assessment / Complexity / Related Issues).

### Draft proposal shown to user
- **Umbrella** `feat(reliability): resilience & observability overhaul (tracking)` — links all five, conventions + sequencing, complexity XL.
- **#errors** `feat(errors): standardize error handling and error contract` — `handleError` contract, route `startServer`/`handleConnection` errors through it, operational vs programmer errors. M.
- **#logging** `feat(logging): structured request and connection logging` — JSON logger, correlation ids, `LOG_LEVEL`. M.
- **#monitoring** `feat(monitoring): health and metrics endpoints` — `/healthz`, `/metrics` counters wired to real lifecycle points. M.
- **#retries** `feat(reliability): connection retry with backoff in handleConnection` — bounded exp backoff, retryable-only gating. M.
- **#alerting** `feat(alerting): error-rate and health alerting` — threshold rules over metrics, pluggable notifier, cooldown/de-dup. M.

Each draft carries explicit **out-of-scope** lines pushing the other four concerns out, so the boundaries don't overlap. User reviewed and approved ("whatever you think is best").

---

## Step 4: Create the issues

Bodies written to temp files via `cat << 'EOF'` heredocs, then:

```bash
./bin/gh issue create --repo octo-eval/sentinel-app --title "feat(reliability): resilience & observability overhaul (tracking)" --body-file /tmp/wf1-issue-umbrella.md   --label "enhancement"
./bin/gh issue create --repo octo-eval/sentinel-app --title "feat(errors): standardize error handling and error contract"      --body-file /tmp/wf1-issue-errors.md     --label "enhancement"
./bin/gh issue create --repo octo-eval/sentinel-app --title "feat(logging): structured request and connection logging"         --body-file /tmp/wf1-issue-logging.md    --label "enhancement"
./bin/gh issue create --repo octo-eval/sentinel-app --title "feat(monitoring): health and metrics endpoints"                   --body-file /tmp/wf1-issue-monitoring.md --label "enhancement"
./bin/gh issue create --repo octo-eval/sentinel-app --title "feat(reliability): connection retry with backoff in handleConnection" --body-file /tmp/wf1-issue-retries.md --label "enhancement"
./bin/gh issue create --repo octo-eval/sentinel-app --title "feat(alerting): error-rate and health alerting"                   --body-file /tmp/wf1-issue-alerting.md   --label "enhancement"
```

Each returned: `https://github.com/octo-eval/sentinel-app/issues/101`
(The mock always returns #101 and overwrites its single `created-issue.json`/`created-issue-body.md` record; each creation was snapshotted into `outputs/created/` so all six survive — see below.)

### Recorded creations (from `.gh-mock/created-issue.json` snapshots)
| # | title | repo | labels | body_len |
|---|-------|------|--------|----------|
| 01 umbrella | feat(reliability): resilience & observability overhaul (tracking) | octo-eval/sentinel-app | [enhancement] | 2281 |
| 02 errors | feat(errors): standardize error handling and error contract | octo-eval/sentinel-app | [enhancement] | 1941 |
| 03 logging | feat(logging): structured request and connection logging | octo-eval/sentinel-app | [enhancement] | 1634 |
| 04 monitoring | feat(monitoring): health and metrics endpoints | octo-eval/sentinel-app | [enhancement] | 1818 |
| 05 retries | feat(reliability): connection retry with backoff in handleConnection | octo-eval/sentinel-app | [enhancement] | 1676 |
| 06 alerting | feat(alerting): error-rate and health alerting | octo-eval/sentinel-app | [enhancement] | 1898 |

Cleanup: `rm -f /tmp/wf1-issue-*.md` (all removed).

---

## Step 5: Wrap up

Appended to `claude_docs/session_notes.md`:
```
### WF1 create-issue — DONE (split: over-broad ask bounded into 1 umbrella + 5 scoped issues)
- feature, octo-eval/sentinel-app
- https://github.com/octo-eval/sentinel-app/issues/101 — feat(reliability): resilience & observability overhaul (tracking) [umbrella]
- feat(errors): ... / feat(logging): ... / feat(monitoring): ... / feat(reliability) retry ... / feat(alerting): ...
```

Did NOT offer to start implementation (WF2 is invoked separately).

### Full `.gh-mock/calls.log`
```
gh issue list --repo octo-eval/sentinel-app --search error handling logging monitoring retries alerting --limit 10
gh issue list --repo octo-eval/sentinel-app --search observability --limit 10
gh issue list --repo octo-eval/sentinel-app --search error --limit 10
gh issue list --repo octo-eval/sentinel-app --limit 20
gh label list --repo octo-eval/sentinel-app
gh issue create --repo octo-eval/sentinel-app --title feat(reliability): resilience & observability overhaul (tracking) --body-file /tmp/wf1-issue-umbrella.md --label enhancement
gh issue create --repo octo-eval/sentinel-app --title feat(errors): standardize error handling and error contract --body-file /tmp/wf1-issue-errors.md --label enhancement
gh issue create --repo octo-eval/sentinel-app --title feat(logging): structured request and connection logging --body-file /tmp/wf1-issue-logging.md --label enhancement
gh issue create --repo octo-eval/sentinel-app --title feat(monitoring): health and metrics endpoints --body-file /tmp/wf1-issue-monitoring.md --label enhancement
gh issue create --repo octo-eval/sentinel-app --title feat(reliability): connection retry with backoff in handleConnection --body-file /tmp/wf1-issue-retries.md --label enhancement
gh issue create --repo octo-eval/sentinel-app --title feat(alerting): error-rate and health alerting --body-file /tmp/wf1-issue-alerting.md --label enhancement
```

---

## Result

**Split, not one issue.** The over-broad "overhaul everything" ask was bounded into **1 umbrella tracking issue + 5 scoped child issues** on `octo-eval/sentinel-app`, all `enhancement`, conventional `feat(scope):` titles.

**Scope boundaries each issue carried:** every child has an explicit in-scope line and an out-of-scope list that pushes the other four concerns away (e.g. feat(logging) declares the error-contract, metrics, and alerting all out-of-scope), plus per-concern dependencies and sequencing. The umbrella owns the shared conventions (one log format, one error contract, env-var thresholds) and closes only after all five children. All Affected Components reference only the verified-real `src/server.js` (`startServer`, `handleConnection`) and `src/errorHandler.js` (`handleError`); no fabricated symbols, metrics, or targets.
