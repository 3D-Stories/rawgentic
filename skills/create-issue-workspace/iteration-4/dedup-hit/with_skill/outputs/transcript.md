# WF1 create-issue — Transcript

## User request
> I want to add WebSocket support to the API so clients get realtime updates. Please open an issue for it.

## Skill under test
`rawgentic:create-issue` (WF1: Issue Creation, lean). Acting as its orchestrator.

---

## Config-loading

**1. Determine active project.**
No prior `/rawgentic:switch` in this session and no `claude_docs/session_registry.jsonl` present. Fell back to `.rawgentic_workspace.json`:

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

Exactly one project with `active == true` → **sentinel-app** (path `./projects/sentinel-app`).

**2. Disabled-skill check.** `disabledSkills` is `[]` → `create-issue` is NOT disabled. Workspace has no `bmadDetected: true` → no BMAD gate. Proceed.

**3. Derive capabilities (tested source of truth).**

```bash
$ python3 hooks/capabilities_lib.py derive --config ./projects/sentinel-app/.rawgentic.json
{"config":{"version":1,"project":{"name":"sentinel-app","type":"web-service","description":"Realtime API service for the eval harness"},"repo":{"provider":"github","fullName":"octo-eval/sentinel-app","defaultBranch":"trunk"},"protectionLevel":"sandbox","techStack":["node","javascript"],"testing":{"frameworks":[{"name":"vitest","type":"unit","command":"npm test","testDir":"test"}]},"ci":{"provider":"github-actions","workflowDir":".github/workflows"},"documentation":{"primaryFiles":["README.md"],"format":"markdown"},"custom":{}},"capabilities":{"repo":"octo-eval/sentinel-app","default_branch":"trunk","project_type":"web-service","has_tests":true,"test_commands":["npm test"],"has_ci":true,"has_deploy":false,"deploy_method":null,"has_database":false,"migration_dir":null,"has_docker":false}}
EXIT=0
```

Exit 0. Carried literals:
- `capabilities.repo` = **octo-eval/sentinel-app**
- `capabilities.default_branch` = **trunk**
- project type = web-service ("Realtime API service for the eval harness"), tech stack node/javascript.

---

## Step 1: Understand the request

**1. Classify.** "Add WebSocket support to the API so clients get realtime updates" = new functionality → **feature request** (not a bug). Unambiguous; no clarifying question needed.

**2. Sufficiency.** Request is a clear, well-scoped feature ask.

**3. Dedup check (run BEFORE drafting).**

```bash
$ ./bin/gh issue list --repo octo-eval/sentinel-app --search "WebSocket realtime updates" --limit 10
42	feat(server): add WebSocket support for realtime updates	enhancement	2026-05-01T00:00:00Z
EXIT=0
```

View the candidate:

```bash
$ ./bin/gh issue view 42 --repo octo-eval/sentinel-app
title:	stub
state:	OPEN
EXIT=0
```

Broader corroborating searches (to be thorough):

```bash
$ ./bin/gh issue list --repo octo-eval/sentinel-app --search "websocket" --limit 10
42	feat(server): add WebSocket support for realtime updates	enhancement	2026-05-01T00:00:00Z

$ ./bin/gh issue list --repo octo-eval/sentinel-app --limit 20
42	feat(server): add WebSocket support for realtime updates	enhancement	2026-05-01T00:00:00Z
EXIT=0
```

**Dedup result:** Existing OPEN issue **#42 — `feat(server): add WebSocket support for realtime updates`** (label `enhancement`, created 2026-05-01). The title is a near-verbatim match for the user's request.

---

## Decision (acting as the honest user)

Per the skill Step 1.3: "If a listed issue plausibly covers the request, show it to the user and ask whether it already covers their need before proceeding."

Shown to the user: issue **#42 "feat(server): add WebSocket support for realtime updates"** (OPEN, enhancement).

Honest user determination: Issue #42 directly and fully covers the request ("add WebSocket support to the API so clients get realtime updates"). It is a genuine duplicate. The right action is to **NOT file a duplicate** and let the existing issue stand.

**Therefore: no new issue is drafted (Step 2) and no new issue is created (Step 4).** The workflow stops at the dedup gate, as designed.

---

## Final outcome

- **No issue created.** Request is already covered by existing OPEN issue **#42** (`feat(server): add WebSocket support for realtime updates`, enhancement) in `octo-eval/sentinel-app`.
- To track/implement the WebSocket work, reference issue **#42** (e.g. `/rawgentic:implement-feature 42`) rather than opening a duplicate.
- Step 5 "DONE" line not appended to `session_notes.md` because no issue was created in this run.
