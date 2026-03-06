# Transcript: /rawgentic:switch (no argument, pick from list)

## Context
- Working directory: /tmp/rawgentic-switch-test/no-arg
- Command invoked: `/rawgentic:switch` (no argument provided)
- Workspace file: `.rawgentic_workspace.json`

## Step 1: Read workspace configuration

Read `/tmp/rawgentic-switch-test/no-arg/.rawgentic_workspace.json` to determine the current state.

Found 3 projects:
| Project | Active | Configured | Last Used |
|---------|--------|------------|-----------|
| alpha   | true   | true       | 2026-03-06T09:00:00Z |
| beta    | false  | true       | 2026-03-05T14:00:00Z |
| gamma   | false  | false      | 2026-03-04T08:00:00Z |

Currently active project: **alpha**

## Step 2: No argument provided -- list projects for user to pick

Since no project name was passed as an argument to `/rawgentic:switch`, the available projects are presented to the user:

```
Available projects:
  1. alpha    (active) [configured]
  2. beta              [configured]
  3. gamma             [not configured]

Which project would you like to switch to?
```

## Step 3: User selects "beta"

The user responds with: **beta**

## Step 4: Validate selection

- Project "beta" exists in the workspace configuration: YES
- Directory `./projects/beta` exists on disk: YES
- Project is configured: YES
- Project is already active: NO

Validation passed. Proceeding with switch.

## Step 5: Perform the switch

Actions taken on the workspace JSON:
1. Set `alpha.active` from `true` to `false` (deactivate current project)
2. Set `beta.active` from `false` to `true` (activate selected project)
3. Updated `beta.lastUsed` to `2026-03-06T12:00:00Z` (current timestamp)

## Step 6: Write updated workspace file

Wrote the updated workspace JSON to the outputs directory.

## Result

Switched active project from **alpha** to **beta** successfully.
