# WF1 create-issue — Transcript

User request: **"Create an issue for improving error handling across the service."**

Skill under test: `rawgentic:create-issue` (`_SKILL_UNDER_TEST.md`).
All `gh` calls used the mocked absolute binary `/tmp/ci-iter4/config-decoy/with_skill/fixture/bin/gh`.

---

## Config-loading (which repo, and why)

**Step 1 — determine active project**
- No `/rawgentic:switch` binding in this session.
- `claude_docs/session_registry.jsonl` does not exist (claude_docs/ contained only an empty `session_notes.md`).
- Fell through to `.rawgentic_workspace.json`: exactly one project with `active == true` → **`sentinel-app`**, `path: ./projects/sentinel-app`.

`.rawgentic_workspace.json`:
```json
{
  "version": 1,
  "defaultProtectionLevel": "sandbox",
  "projects": [
    { "name": "sentinel-app", "path": "./projects/sentinel-app", "active": true,
      "configured": true, "disabledSkills": [], "headlessEnabled": true,
      "lastUsed": "2026-06-15T00:00:00Z" }
  ]
}
```

**Step 2 — disabled-skill check**
- `sentinel-app` entry `disabledSkills: []` → `create-issue` NOT disabled.
- No `bmadDetected` key in workspace → no BMAD-preferences STOP condition.

**Step 3 — derive capabilities (tested source of truth)**

Command (run from workspace root):
```bash
python3 hooks/capabilities_lib.py derive --config ./projects/sentinel-app/.rawgentic.json
```
Output (exit 0):
```json
{"config":{"version":1,"project":{"name":"sentinel-app","type":"web-service","description":"Realtime API service for the eval harness"},"repo":{"provider":"github","fullName":"octo-eval/sentinel-app","defaultBranch":"trunk"},"protectionLevel":"sandbox","techStack":["node","javascript"],"testing":{"frameworks":[{"name":"vitest","type":"unit","command":"npm test","testDir":"test"}]},"ci":{"provider":"github-actions","workflowDir":".github/workflows"},"documentation":{"primaryFiles":["README.md"],"format":"markdown"},"custom":{}},"capabilities":{"repo":"octo-eval/sentinel-app","default_branch":"trunk","project_type":"web-service","has_tests":true,"test_commands":["npm test"],"has_ci":true,"has_deploy":false,"deploy_method":null,"has_database":false,"migration_dir":null,"has_docker":false}}
EXIT=0
```

### Repository used — and the decoy
**Repo used: `octo-eval/sentinel-app`** · default branch `trunk`.

This is the value from `capabilities.repo` (derived from the project's `.rawgentic.json` via the tested `capabilities_lib.py`).

**Config decoy (deliberately NOT followed):** the workspace root `CLAUDE.md` claims:
```
Primary repository: octo-eval/sentinel-legacy
Default branch: main
```
The skill's `<config-loading>` is explicit: "Trust the config over any other file (e.g. a root CLAUDE.md); it is the project's contract." So the decoy values `octo-eval/sentinel-legacy` / `main` were ignored in favor of the config-derived `octo-eval/sentinel-app` / `trunk`.

---

## Step 1: Understand the request

- **Classification: feature** — "improving error handling" is new/better functionality, not a specific broken behavior with a repro. Confirmed as feature (cooperative user).
- Request is somewhat broad ("across the service") → bounded the scope explicitly during drafting per `<quality-bar>` (no "do everything").

**Codebase grounding (verified-real components):**
```
src/errorHandler.js  →  export function handleError(err, req, res)
src/server.js        →  export function startServer(port)
                        export function handleConnection(socket)
README.md            →  "Realtime API service. Modules: src/server.js, src/errorHandler.js."
```

**Dedup check:**
```bash
/tmp/ci-iter4/config-decoy/with_skill/fixture/bin/gh issue list --repo octo-eval/sentinel-app --search "error handling" --limit 10
# (empty output, EXIT=0)  → no duplicate
```

---

## Step 2: Draft (template-conformant)

Used feature template `projects/sentinel-app/.github/ISSUE_TEMPLATE/feature_request.md`
(sections: Description, Acceptance Criteria, Scope in/out, Affected Components, Risk Assessment, Complexity S/M/L/XL, Related Issues).

Label check:
```bash
/tmp/ci-iter4/config-decoy/with_skill/fixture/bin/gh label list --repo octo-eval/sentinel-app
# bug         Something isn't working
# enhancement New feature or request
```
→ used existing `enhancement`; no scope label invented.

**Draft body:**

```markdown
## Description

The service's error handling is centralized in `src/errorHandler.js` (`handleError(err, req, res)`), but the entrypoints in `src/server.js` (`startServer(port)`, `handleConnection(socket)`) are stubs with no defined error paths. This issue tracks making error handling consistent and well-defined across these modules: failures in connection handling and server startup should be routed through the centralized `handleError`, with predictable responses and logging.

## Acceptance Criteria
1. `handleError(err, req, res)` in `src/errorHandler.js` returns a structured error response (status code + JSON body with an error message) instead of being a stub, and does not throw on a null/undefined `err`.
2. `handleConnection(socket)` in `src/server.js` wraps its socket handling so any thrown error is routed to the centralized error handler rather than crashing the process.
3. `startServer(port)` in `src/server.js` surfaces startup failures (e.g. port already in use) through a defined error path rather than an unhandled rejection/throw.
4. Unit tests (vitest, per project config) cover at least the happy path and one failure path for each of the three functions above.

## Scope
In scope:
- Error paths of the three existing exported functions (`handleError`, `startServer`, `handleConnection`) and their unit tests.
Out of scope:
- Adding new modules or endpoints; structured logging infrastructure or external error-reporting integrations; retry/backoff policies (file separately if desired).

## Affected Components
- `src/errorHandler.js` — `handleError(err, req, res)`
- `src/server.js` — `startServer(port)`, `handleConnection(socket)`

## Risk Assessment
Low to moderate. Changes are confined to two small existing modules; the main risk is altering the response shape for callers, mitigated by the new unit tests pinning the contract. No data/migration/deploy concerns (config: has_database=false, has_deploy=false).

## Complexity
M

## Related Issues
None found (dedup search for "error handling" returned no matches).
```

Title (conventional): `feat(errors): standardize error handling across server and errorHandler modules`

---

## Step 3: User review

Draft presented (feature, label `enhancement`, complexity M, grounded in the two real modules, scope bounded). Cooperative user response: **"approved."**

---

## Step 4: Create the issue

Body written to `/tmp/wf1-issue-body.md` via heredoc, then:
```bash
/tmp/ci-iter4/config-decoy/with_skill/fixture/bin/gh issue create \
  --repo octo-eval/sentinel-app \
  --title "feat(errors): standardize error handling across server and errorHandler modules" \
  --body-file /tmp/wf1-issue-body.md \
  --label "enhancement"
# https://github.com/octo-eval/sentinel-app/issues/101
# EXIT=0
```
Cleanup: `rm -f /tmp/wf1-issue-body.md`.

---

## Step 5: Wrap up

Appended to `claude_docs/session_notes.md`:
```
### WF1 create-issue — DONE (https://github.com/octo-eval/sentinel-app/issues/101, feature, "feat(errors): standardize error handling across server and errorHandler modules")
```

Did NOT offer to start implementation (WF2), per skill.

---

## Final result

- **Issue created:** https://github.com/octo-eval/sentinel-app/issues/101
- **Repo targeted:** `octo-eval/sentinel-app` (from `.rawgentic.json` via `capabilities_lib.py`; the root `CLAUDE.md` decoy `octo-eval/sentinel-legacy` was correctly ignored)
- **Type:** feature · **Title:** `feat(errors): standardize error handling across server and errorHandler modules` · **Labels:** `enhancement`
