# WF2 Transcript: Issue #10 — Add search feature

## Test Scenario: config-version-mismatch

### Config-Loading Protocol Execution

**Step 1: Read `.rawgentic_workspace.json`**

- Path: `/tmp/rawgentic-implement-feature-test/config-version-mismatch/.rawgentic_workspace.json`
- Result: Valid JSON. Parsed successfully.
- Active project found: `version-app` at `/tmp/rawgentic-implement-feature-test/config-version-mismatch/projects/version-app`

**Step 2: Read `<activeProject.path>/.rawgentic.json`**

- Path: `/tmp/rawgentic-implement-feature-test/config-version-mismatch/projects/version-app/.rawgentic.json`
- Result: Valid JSON. Parsed successfully.
- Contents:
  ```json
  {
    "version": 99,
    "project": {
      "name": "version-app",
      "type": "application",
      "description": "App with future version number"
    },
    "repo": {
      "provider": "github",
      "fullName": "eval-org/version-test",
      "defaultBranch": "main"
    }
  }
  ```

**Version Check:**

- `config.version` = 99
- Expected version: 1
- Condition: `version > 1` => **TRUE**
- Action per skill: **Warn user about version mismatch**

### Workflow Outcome

**WARNING: Config version mismatch detected.**

The project configuration file `.rawgentic.json` for project `version-app` has `"version": 99`, but this workflow expects version 1. This may indicate:

- The config was created by a newer version of rawgentic than this workflow supports
- The config version was manually modified
- There is a compatibility gap between the workflow and the project config

The user should verify the config version is correct. The workflow may not behave correctly with an unsupported config version. Consider:

- Updating the rawgentic skill/workflow to support version 99
- Running `/rawgentic:setup` to regenerate the config at the expected version
- Manually setting `"version": 1` if the config content is actually v1-compatible

**The workflow proceeded with parsing the config as-is (per the skill, a version mismatch is a warning, not a hard stop).** The config was parsed into the `config` object despite the version mismatch.

### Step 3 (Capabilities Build) — Attempted

From the config, the following capabilities were derived:

- `has_tests`: false (no `testing` section in config)
- `test_commands`: [] (no testing frameworks)
- `has_ci`: false (no `ci` section in config)
- `has_deploy`: false (no `deploy` section in config)
- `has_database`: false (no `database` section in config)
- `has_docker`: false (no `infrastructure` section in config)
- `project_type`: "application"
- `repo`: "eval-org/version-test"
- `default_branch`: "main"

### Step 1 (Issue Fetch) — Not Executed

The workflow would next attempt to fetch issue #10 via `gh issue view 10 --repo eval-org/version-test`. However, since this is a test scenario with a synthetic repo (`eval-org/version-test`), the issue fetch would fail. The version mismatch warning was the primary behavior under test.

### Summary

| Check | Result |
|-------|--------|
| Workspace file found | Yes |
| Workspace file valid JSON | Yes |
| Active project identified | Yes (`version-app`) |
| Project config found | Yes |
| Project config valid JSON | Yes |
| Version check (`config.version`) | 99 (mismatch — expected 1) |
| Version mismatch warning issued | **Yes** |
| Workflow hard-stopped | No (version mismatch is a warning, not a stop condition) |
| Config parsed despite mismatch | Yes |
| Capabilities built | Yes (minimal — sparse config) |
