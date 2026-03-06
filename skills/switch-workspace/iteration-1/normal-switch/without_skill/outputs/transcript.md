# Transcript: /rawgentic:switch backend-api

## Step 1: Read the workspace file

Read `/tmp/rawgentic-switch-test/normal-switch/.rawgentic_workspace.json` to understand the current workspace state.

**Findings:**
- Two projects defined: `frontend-app` and `backend-api`
- `frontend-app` is currently active (`active: true`, `configured: true`)
- `backend-api` is currently inactive (`active: false`, `configured: false`)
- Projects directory is `./projects`

## Step 2: Verify the target project directory exists

Listed contents of `/tmp/rawgentic-switch-test/normal-switch/projects/` and confirmed that both `frontend-app` and `backend-api` directories exist.

## Step 3: Determine what "switching projects" means

The command `/rawgentic:switch backend-api` requests switching the active project context to `backend-api`. This means:

1. **Deactivate the currently active project** -- set `frontend-app.active` from `true` to `false`
2. **Activate the target project** -- set `backend-api.active` from `false` to `true`
3. **Update the `lastUsed` timestamp** on the newly activated project to the current time
4. **Preserve all other fields** -- `configured` status, paths, and version remain unchanged

## Step 4: Write the updated workspace JSON

Wrote the updated `.rawgentic_workspace.json` to the outputs directory with the following changes:

| Field | Before | After |
|---|---|---|
| `frontend-app.active` | `true` | `false` |
| `backend-api.active` | `false` | `true` |
| `backend-api.lastUsed` | `2026-03-05T15:00:00Z` | `2026-03-06T13:26:00Z` |

All other fields were left unchanged. The `configured` field for `backend-api` remains `false` because switching to a project does not automatically configure it -- configuration is a separate concern.
