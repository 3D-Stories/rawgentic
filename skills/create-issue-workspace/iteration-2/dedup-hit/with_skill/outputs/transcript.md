# WF1 Issue-Creation Transcript

**User request:** "I want to add WebSocket support to the API so clients get realtime updates. Please open an issue for it."

**Skill under test:** `rawgentic:create-issue` (WF1, 9-step issue-creation workflow).

---

## Step 0 — Working directory

```
$ cd /tmp/ci-iter2/dedup-hit/with_skill/fixture && pwd
/tmp/ci-iter2/dedup-hit/with_skill/fixture
```

Confirmed: a self-contained rawgentic workspace with one active project (`sentinel-app`).

---

## Config-loading (runs before any workflow step)

### Active-project fallback chain
- **Level 1 (conversation context):** No `/rawgentic:switch` ran in this session → not bound here.
- **Level 2 (session registry):** `claude_docs/session_registry.jsonl` does not exist → no binding.

```
$ cat claude_docs/session_registry.jsonl
(no session_registry.jsonl)
```

- **Level 3 (workspace default):** Read `.rawgentic_workspace.json` from the workspace root.

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
      "configured": true,
      "disabledSkills": [],
      "headlessEnabled": true,
      "lastUsed": "2026-06-15T00:00:00Z"
    }
  ]
}
```

Exactly one project has `active == true` → **bind to `sentinel-app`** (path `./projects/sentinel-app`, resolved against the workspace root).

### Step 1b — disabled-skill check
- Project entry has `disabledSkills: []` (field present, empty). `create-issue` is **not** in it.
- Workspace has **no** `bmadDetected` field (so the "BMAD detected but no skill prefs" STOP does not apply).
- → Proceed.

### Capabilities derivation (single source of truth)

```
$ python3 hooks/capabilities_lib.py derive --config ./projects/sentinel-app/.rawgentic.json
{"config":{"version":1,"project":{"name":"sentinel-app","type":"web-service","description":"Realtime API service for the eval harness"},"repo":{"provider":"github","fullName":"octo-eval/sentinel-app","defaultBranch":"trunk"},"protectionLevel":"sandbox","techStack":["node","javascript"],"testing":{"frameworks":[{"name":"vitest","type":"unit","command":"npm test","testDir":"test"}]},"ci":{"provider":"github-actions","workflowDir":".github/workflows"},"documentation":{"primaryFiles":["README.md"],"format":"markdown"},"custom":{}},"capabilities":{"repo":"octo-eval/sentinel-app","default_branch":"trunk","project_type":"web-service","has_tests":true,"test_commands":["npm test"],"has_ci":true,"has_deploy":false,"deploy_method":null,"has_database":false,"migration_dir":null,"has_docker":false}}
EXIT=0
```

**Exit 0 — config valid.** Resolved values carried as literals:

| field | value |
|---|---|
| repo | `octo-eval/sentinel-app` |
| default_branch | `trunk` |
| project_type | `web-service` |
| has_tests | `true` (`npm test`) |
| has_ci | `true` (github-actions) |
| has_deploy | `false` |
| has_database | `false` |
| has_docker | `false` |

---

## Step 1 — Receive User Intent

**Acknowledge:** Request received — add WebSocket support to the API for realtime client updates.

**Classification:** **FEATURE** — new functionality (a WebSocket transport that pushes realtime updates to clients). Unambiguously a feature request, not a bug; no classification clarifying question needed.

**Scope hints:** server/API layer (`projects/sentinel-app/src/server.js`), realtime transport, client push updates.

**Information sufficiency:** Enough to draft acceptance criteria; but before any drafting, the dedup gate runs (Step 1.6).

### Deduplication check (mocked `gh`, invoked via absolute path)

```
$ /tmp/ci-iter2/dedup-hit/with_skill/fixture/bin/gh issue list --repo octo-eval/sentinel-app --search "websocket realtime" --limit 10
42	feat(server): add WebSocket support for realtime updates	enhancement	2026-05-01T00:00:00Z
EXIT=0
```

Confirming with additional keyword variations:

```
$ .../bin/gh issue list --repo octo-eval/sentinel-app --search "websocket" --limit 10
42	feat(server): add WebSocket support for realtime updates	enhancement	2026-05-01T00:00:00Z

$ .../bin/gh issue list --repo octo-eval/sentinel-app --search "WebSocket API realtime updates clients" --limit 10
42	feat(server): add WebSocket support for realtime updates	enhancement	2026-05-01T00:00:00Z

$ .../bin/gh issue view 42 --repo octo-eval/sentinel-app
title:	stub
state:	OPEN
```

### Dedup search results
- **Issue #42 — `feat(server): add WebSocket support for realtime updates`** — label `enhancement`, created 2026-05-01, **state OPEN**.
- Every keyword variation returns this same single issue.

### Duplicate decision (answering honestly as the user)
Per Step 1.7, presenting #42 and asking "Does this cover your request?":

> My request is "add WebSocket support to the API so clients get realtime updates." Issue **#42** is titled "add WebSocket support for realtime updates" and is **OPEN**. That is the same feature, near-verbatim. **Yes — #42 fully covers my request.**

This is a **genuine duplicate**, so per the test's honesty instruction and the skill's Step 1 failure-mode ("Dedup check finds a matching issue → present to user and ask if it covers their request before proceeding"), the workflow must **not** create a duplicate.

**Decision: Do NOT create a new issue.** WF1 stops at the dedup gate and points the user at the existing open issue #42. Steps 2–9 (brainstorm, critique, circuit breaker, review, create) are intentionally not run, because creating an issue would duplicate #42.

---

## Issue Classification (Step 1 output)

```
Issue Classification:
- Type: feature
- Summary: Add WebSocket support to the API so clients receive realtime updates.
- Scope hints: server/API (src/server.js), realtime transport, client push
- Existing issues found: #42 "feat(server): add WebSocket support for realtime updates" (enhancement, OPEN, 2026-05-01)

Dedup result: #42 is an OPEN, near-verbatim duplicate of this request. No new issue created.
```

---

## Step 6 — Conditional memorization
No novel cross-cutting insight was produced (no brainstorm/critique ran), so the memorize step is correctly **skipped** — no output.

---

## Final outcome

**WF1 terminated at the dedup gate — no new GitHub issue created.** The request is already tracked by existing **OPEN issue #42 ("feat(server): add WebSocket support for realtime updates")**. The user was shown the duplicate and confirmed it covers the request, so the workflow correctly avoided creating a duplicate and directed the user to follow #42.
