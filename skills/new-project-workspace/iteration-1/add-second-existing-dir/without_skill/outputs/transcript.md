# Transcript: Add Second Project to Existing Workspace

## Scenario
- Working directory: `/tmp/rawgentic-newproj-test/add-second`
- Existing workspace has `first-app` (active: true, configured: true)
- Target: add `./projects/second-app` which already exists as a directory with a git repo and `package.json`

## Steps Performed

### 1. Read existing workspace
Read `.rawgentic_workspace.json` and found a single project entry:
- `first-app` at `./projects/first-app` (active: true, configured: true, lastUsed: 2026-03-06T10:00:00Z)

### 2. Verify second-app directory
Confirmed `./projects/second-app` exists and contains a `.git` directory and `package.json`.

### 3. Update workspace JSON
- Deactivated `first-app` by setting `active: false` (kept all other fields unchanged)
- Added `second-app` entry with:
  - `name`: "second-app"
  - `path`: "./projects/second-app"
  - `active`: true
  - `lastUsed`: "2026-03-06T13:12:00Z" (current timestamp)
  - `configured`: false (new project, not yet configured)

### 4. Delegation note
The newly added `second-app` has `configured: false`. The next step would be to delegate to `/rawgentic:setup` to run project configuration (detect framework, install dependencies, set up dev environment, etc.) for `second-app`.

## Decisions Made
- Set `active: false` on `first-app` to enforce the "only one active project at a time" constraint
- Set `configured: false` on `second-app` since it has not been through the rawgentic setup flow yet
- Used current timestamp for `lastUsed` on the new project since it is now the active project
- Did not modify `first-app`'s `lastUsed` timestamp since it was not interacted with, only deactivated

## Output
- Workspace JSON saved to: `outputs/workspace.json`
