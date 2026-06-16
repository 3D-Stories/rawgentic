Before executing any workflow steps, load the project configuration:

1. Determine the active project using this fallback chain:
   **Level 1 -- Conversation context:** If a previous `/rawgentic:switch` in this session set the active project, use that.
   **Level 2 -- Session registry:** Read `claude_docs/session_registry.jsonl`. Grep for your session_id. If found, use the project from the most recent matching line.
   **Level 3 -- Workspace default:** Read `.rawgentic_workspace.json` from the Claude root directory. If exactly one project has `active == true`, use it. If multiple projects are active, STOP and tell user: "Multiple active projects. Run `/rawgentic:switch <name>` to bind this session."

   At any level:
   - `.rawgentic_workspace.json` missing -> STOP. Tell user: "No rawgentic workspace found. Run /rawgentic:new-project."
   - `.rawgentic_workspace.json` malformed -> STOP. Tell user: "Workspace file is corrupted. Run /rawgentic:new-project to regenerate, or fix manually."
   - No active project found at any level -> STOP. Tell user: "No active project. Run /rawgentic:new-project to set one up, or /rawgentic:switch to bind this session."
   - **Path resolution:** The `activeProject.path` may be relative (e.g., `./projects/my-app`). Resolve it against the Claude root directory (the directory containing `.rawgentic_workspace.json`) to get the absolute path for file operations.

1b. **Disabled skill check:** After resolving the active project, read `.rawgentic_workspace.json` (if not already read in step 1) and find the active project's entry.
   - If the project entry has a `disabledSkills` array and this skill's bare name appears in it:
     **[Headless cleanup]:** Before stopping, check if `claude_docs/headless_suspend.json` exists. If it does, delete it, remove `rawgentic:ai-waiting` label from the issue (read issue number from suspend file), and add `rawgentic:ai-error` with a comment: "This skill was disabled after a headless session was suspended. The pending question can no longer be processed." Then **STOP.**
     - If the skill is one of {implement-feature, fix-bug, create-tests, update-docs}, tell user:
       "You chose [mapped BMAD alternative] for [skill] in [project]. To change, re-run `/rawgentic:setup` or edit `disabledSkills` in `.rawgentic_workspace.json`."
       Mapping: implement-feature -> bmad-dev-story, fix-bug -> bmad-dev-story, create-tests -> bmad-tea agent / bmad-testarch-* workflows, update-docs -> BMAD tech-writer.
     - Otherwise, tell user:
       "Skill [name] is disabled in [project]. Remove it from `disabledSkills` in `.rawgentic_workspace.json` to re-enable."
   - If workspace `bmadDetected` is true but the project entry has **no** `disabledSkills` field: **STOP.** Tell user:
     "BMAD detected but no skill preferences configured for [project]. Run `/rawgentic:switch` or `/rawgentic:setup` to configure."
   - Otherwise: proceed to step 2.

2. Load the config and derive capabilities with the helper CLI (one tested
   source of truth — never hand-derive the `capabilities` object, so all 11
   workflow skills and the docs table cannot drift apart):
   ```bash
   python3 hooks/capabilities_lib.py derive \
     --config <activeProject.path>/.rawgentic.json
   ```
   - **Non-zero exit** -> the config is missing, corrupt, or invalid. **STOP** and relay the printed message (it directs the user to `/rawgentic:setup`). A `config.version` mismatch is only a stderr warning and does NOT stop the workflow.
   - **Exit 0** -> stdout is `{"config": {...}, "capabilities": {...}}`. Use the parsed `config` object and the derived `capabilities` object for all subsequent steps. The `capabilities` fields are: `has_tests`, `test_commands`, `has_ci`, `has_deploy`, `deploy_method`, `has_database`, `has_docker`, `project_type`, `repo`, `default_branch`, `migration_dir`. Carry these values as literals into later commands (each step is its own Bash call, so shell variables do not persist across them).

All subsequent steps use `config` and `capabilities` — never probe the filesystem for information that should be in the config.
