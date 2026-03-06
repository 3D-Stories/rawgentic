# Phase 1: Foundation Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build the foundation layer for the rawgentic plugin overhaul: schema template, session-start hook, and three new skills (setup, new-project, switch).

**Architecture:** Two-layer system. Workspace layer (`.rawgentic_workspace.json` + new-project + switch) manages which project is active. Config layer (`.rawgentic.json` + setup) manages how each project is configured. Session-start hook provides context injection. All details in `docs/plans/2026-03-06-plugin-overhaul-design.md`.

**Tech Stack:** Bash (hook), Markdown/prompt engineering (SKILL.md files), JSON (schema template). Using `/skill-creator` for skills 3-5.

**Repo:** `./projects/rawgentic` (https://github.com/3D-Stories/rawgentic)

---

## Task 1: Create Schema Template

The schema template is the reference document that `setup` uses when generating `.rawgentic.json`. It replaces the old `templates/claude-md-sdlc-section.md`.

**Files:**
- Create: `templates/rawgentic-json-schema.json`
- Delete: `templates/claude-md-sdlc-section.md`

### Step 1: Create the schema template file

Write `templates/rawgentic-json-schema.json` with the full annotated schema from the design doc. This is a concrete example (not just type annotations) so setup can use it as a reference:

```json
{
  "$comment": "Rawgentic project configuration schema v1. Used by /rawgentic:setup as reference when generating .rawgentic.json for a project.",
  "version": 1,
  "project": {
    "name": "my-app",
    "type": "application",
    "description": "Description of the project"
  },
  "repo": {
    "provider": "github",
    "fullName": "org/repo-name",
    "defaultBranch": "main"
  },
  "techStack": ["typescript", "react", "postgres", "docker"],
  "testing": {
    "frameworks": [
      {
        "name": "vitest",
        "type": "unit",
        "command": "npx vitest run",
        "configFile": "vitest.config.ts",
        "testDir": "src/__tests__"
      },
      {
        "name": "playwright",
        "type": "e2e",
        "command": "npx playwright test",
        "configFile": "playwright.config.ts",
        "testDir": "e2e"
      }
    ]
  },
  "database": {
    "type": "postgres",
    "cli": "psql",
    "name": "myapp_dev",
    "user": "myapp_dev",
    "container": "myapp-postgres-dev",
    "migrationsDir": "postgres-migrations",
    "migrationPattern": "0XX-name.sql"
  },
  "services": [
    {
      "name": "dashboard",
      "type": "frontend",
      "framework": "react",
      "port": 3000,
      "entryPoint": "src/main.tsx",
      "healthCheck": "/health"
    },
    {
      "name": "engine",
      "type": "backend",
      "framework": "fastapi",
      "port": 8100,
      "entryPoint": "engine/main.py",
      "healthCheck": "/status"
    }
  ],
  "infrastructure": {
    "hosts": [
      {
        "name": "dev",
        "address": "192.168.1.100",
        "sshUser": "deploy",
        "description": "Development server"
      }
    ],
    "docker": {
      "composeFiles": [
        { "name": "infra", "path": "docker-compose.infra.yml" },
        { "name": "engine", "path": "docker-compose.engine.yml" }
      ]
    }
  },
  "deploy": {
    "method": "compose",
    "command": "docker compose -f docker-compose.infra.yml up -d",
    "targetHost": "dev"
  },
  "security": {
    "authMechanisms": [
      {
        "type": "JWT",
        "appliesTo": ["dashboard", "socketio"],
        "middlewarePath": "src/middleware/auth.ts"
      },
      {
        "type": "api-key",
        "appliesTo": ["engine"],
        "headerName": "X-API-Key"
      }
    ],
    "validationLibrary": "zod",
    "dataChannels": ["rest", "socketio", "redis-pubsub", "engine-http"]
  },
  "ci": {
    "provider": "github-actions",
    "workflowDir": ".github/workflows"
  },
  "formatting": {
    "tool": "prettier",
    "configFile": ".prettierrc",
    "command": "npx prettier --write"
  },
  "documentation": {
    "primaryFiles": ["README.md", "docs/"],
    "format": "markdown"
  },
  "custom": {}
}
```

### Step 2: Verify the schema template is valid JSON

Run: `cd ./projects/rawgentic && python3 -c "import json; json.load(open('templates/rawgentic-json-schema.json')); print('Valid JSON')"`
Expected: `Valid JSON`

### Step 3: Delete the old template

Run: `rm ./projects/rawgentic/templates/claude-md-sdlc-section.md`
Expected: File removed, no errors.

### Step 4: Verify old template is gone and new one exists

Run: `ls ./projects/rawgentic/templates/`
Expected: Only `rawgentic-json-schema.json` listed.

### Step 5: Commit

```bash
cd ./projects/rawgentic && git add templates/rawgentic-json-schema.json && git rm templates/claude-md-sdlc-section.md && git commit -m "feat(schema): replace CLAUDE.md template with .rawgentic.json schema template

Replace templates/claude-md-sdlc-section.md (CLAUDE.md constant injection)
with templates/rawgentic-json-schema.json (structured project config schema).
Schema includes version field, custom section, and all optional sections
per the plugin overhaul design.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Task 2: Rewrite Session-start Hook

Replace the current CLAUDE.md-marker-checking hook with the new 3-state workspace-aware hook.

**Files:**
- Rewrite: `hooks/session-start`
- No changes needed to: `hooks/hooks.json` (matcher pattern and command path are unchanged)

### Step 1: Read the current hook for reference

Read `hooks/session-start` to understand the existing JSON output format. The output structure (`hookSpecificOutput.hookEventName` + `additionalContext`) must be preserved.

### Step 2: Write the new session-start hook

Overwrite `hooks/session-start` with the new 3-state flow:

```bash
#!/usr/bin/env bash
# SessionStart hook for rawgentic plugin — workspace-aware context injection
# Design: docs/plans/2026-03-06-plugin-overhaul-design.md

set -euo pipefail

# Escape string for JSON embedding using bash parameter substitution.
escape_for_json() {
    local s="$1"
    s="${s//\\/\\\\}"
    s="${s//\"/\\\"}"
    s="${s//$'\n'/\\n}"
    s="${s//$'\r'/\\r}"
    s="${s//$'\t'/\\t}"
    printf '%s' "$s"
}

# Find workspace file — check current dir, then parent (for subdirectory launches)
WORKSPACE_FILE=""
if [ -f ".rawgentic_workspace.json" ]; then
    WORKSPACE_FILE=".rawgentic_workspace.json"
elif [ -f "../.rawgentic_workspace.json" ]; then
    WORKSPACE_FILE="../.rawgentic_workspace.json"
fi

# State 1: No workspace file
if [ -z "$WORKSPACE_FILE" ]; then
    context=$(escape_for_json "No rawgentic workspace found. Run /rawgentic:new-project to get started.")
    cat <<EOF
{
  "hookSpecificOutput": {
    "hookEventName": "SessionStart",
    "additionalContext": "${context}"
  }
}
EOF
    exit 0
fi

# Validate JSON before parsing
if ! python3 -c "import json; json.load(open('${WORKSPACE_FILE}'))" 2>/dev/null; then
    context=$(escape_for_json "Rawgentic workspace file is corrupted. Run /rawgentic:new-project to regenerate, or fix .rawgentic_workspace.json manually.")
    cat <<EOF
{
  "hookSpecificOutput": {
    "hookEventName": "SessionStart",
    "additionalContext": "${context}"
  }
}
EOF
    exit 0
fi

# Parse workspace file
PROJECTS_COUNT=$(python3 -c "import json; d=json.load(open('${WORKSPACE_FILE}')); print(len(d.get('projects',[])))")

# State 2: No projects registered
if [ "$PROJECTS_COUNT" -eq 0 ]; then
    context=$(escape_for_json "Rawgentic workspace exists but no projects registered. Run /rawgentic:new-project.")
    cat <<EOF
{
  "hookSpecificOutput": {
    "hookEventName": "SessionStart",
    "additionalContext": "${context}"
  }
}
EOF
    exit 0
fi

# State 3: Active project found
ACTIVE_INFO=$(python3 -c "
import json
d = json.load(open('${WORKSPACE_FILE}'))
for p in d.get('projects', []):
    if p.get('active'):
        print(f\"{p['name']}|{p['path']}|{p.get('configured', False)}\")
        break
")

if [ -n "$ACTIVE_INFO" ]; then
    IFS='|' read -r ACTIVE_NAME ACTIVE_PATH CONFIGURED <<< "$ACTIVE_INFO"

    if [ "$CONFIGURED" = "True" ]; then
        context=$(escape_for_json "Active project: ${ACTIVE_NAME} (${ACTIVE_PATH}).")
    else
        context=$(escape_for_json "Active project: ${ACTIVE_NAME} (${ACTIVE_PATH}). Config missing -- run /rawgentic:setup.")
    fi
else
    context=$(escape_for_json "Rawgentic workspace has projects but none is active. Run /rawgentic:switch to select one.")
fi

cat <<EOF
{
  "hookSpecificOutput": {
    "hookEventName": "SessionStart",
    "additionalContext": "${context}"
  }
}
EOF

exit 0
```

### Step 3: Verify the hook is executable and valid bash

Run: `bash -n ./projects/rawgentic/hooks/session-start && echo "Syntax OK"`
Expected: `Syntax OK`

### Step 4: Test the hook with no workspace file (State 1)

Run: `cd /tmp && bash ./home/candrosoff/claude/projects/rawgentic/hooks/session-start`
Expected: JSON output containing `"No rawgentic workspace found. Run /rawgentic:new-project to get started."`

### Step 5: Test the hook with an empty workspace (State 2)

```bash
cd /tmp && echo '{"version":1,"projectsDir":"./projects","projects":[]}' > .rawgentic_workspace.json && bash /home/candrosoff/claude/projects/rawgentic/hooks/session-start && rm .rawgentic_workspace.json
```
Expected: JSON output containing `"Rawgentic workspace exists but no projects registered."`

### Step 6: Test the hook with an active configured project (State 3)

```bash
cd /tmp && echo '{"version":1,"projectsDir":"./projects","projects":[{"name":"test","path":"./projects/test","active":true,"lastUsed":"2026-03-06T00:00:00Z","configured":true}]}' > .rawgentic_workspace.json && bash /home/candrosoff/claude/projects/rawgentic/hooks/session-start && rm .rawgentic_workspace.json
```
Expected: JSON output containing `"Active project: test (./projects/test)."`

### Step 7: Test the hook with an active unconfigured project (State 3, not configured)

```bash
cd /tmp && echo '{"version":1,"projectsDir":"./projects","projects":[{"name":"test","path":"./projects/test","active":true,"lastUsed":"2026-03-06T00:00:00Z","configured":false}]}' > .rawgentic_workspace.json && bash /home/candrosoff/claude/projects/rawgentic/hooks/session-start && rm .rawgentic_workspace.json
```
Expected: JSON output containing `"Config missing -- run /rawgentic:setup."`

### Step 8: Test corruption handling

```bash
cd /tmp && echo 'not json{{{' > .rawgentic_workspace.json && bash /home/candrosoff/claude/projects/rawgentic/hooks/session-start && rm .rawgentic_workspace.json
```
Expected: JSON output containing `"Rawgentic workspace file is corrupted."`

### Step 9: Commit

```bash
cd ./projects/rawgentic && git add hooks/session-start && git commit -m "feat(hook): rewrite session-start for workspace-aware 3-state flow

Replace CLAUDE.md marker check with .rawgentic_workspace.json reading.
Three states: no workspace, no projects, active project (configured/not).
Adds JSON corruption detection and parent-directory fallback.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Task 3: Create Setup Skill with /skill-creator

The setup skill is the most complex Phase 1 artifact. Use `/skill-creator` to build it.

**Files:**
- Rewrite: `skills/setup/SKILL.md`

### Step 1: Prepare the skill-creator brief

Before invoking `/skill-creator`, prepare the full specification. The brief should include:
- The skill name: `rawgentic:setup`
- The design from `docs/plans/2026-03-06-plugin-overhaul-design.md` sections:
  - "Schema: `.rawgentic.json`" (the full schema)
  - "Config-loading Protocol"
  - "Learning Config Merge Policy"
  - "CLAUDE.md Content"
  - "Skill Designs > rawgentic:setup" (the 9-step flow)
  - "User Journeys > Migrating from Old Rawgentic"
- The schema template at `templates/rawgentic-json-schema.json` (as reference for what setup generates)
- The current skill at `skills/setup/SKILL.md` (for understanding what's being replaced)

Key requirements to emphasize in the brief:
1. Skill is a SKILL.md prompt file (markdown with XML tags), not runtime code
2. Must include `---` YAML frontmatter with name, description, argument-hint
3. Must handle 3 sub-flows: re-run (existing .rawgentic.json), existing code (auto-detect), blank project (brainstorm)
4. Must include CLAUDE.md migration step (detect old sections, parse as seed, clean up)
5. Must write `.rawgentic.json` with `"version": 1`
6. Must update `.rawgentic_workspace.json` to set `configured: true`
7. Must present config section-by-section for user approval
8. Must handle the case where `.rawgentic.json` already exists (merge, don't overwrite learned entries)
9. The skill should reference `templates/rawgentic-json-schema.json` as the schema reference
10. All tech-stack detection should be generic (not hardcoded to React/Node.js)

### Step 2: Invoke /skill-creator

Run: `/skill-creator` with the prepared brief targeting `skills/setup/SKILL.md` in the rawgentic repo.

Follow the skill-creator's interactive process:
- Provide the design spec as context
- Review the generated skill
- Iterate until the skill matches the design doc's 9-step flow
- Ensure the frontmatter, role tag, and step structure are correct

### Step 3: Verify the generated SKILL.md

After skill-creator produces the file, verify:
1. YAML frontmatter has `name: rawgentic:setup`, `description`, `argument-hint`
2. Has a `<role>` tag defining the setup wizard persona
3. Has 9 numbered steps matching the design doc
4. Step 2 includes CLAUDE.md migration detection
5. Step 3 has all three sub-flows (re-run, existing code, blank project)
6. Step 3's auto-detection covers: package.json, pyproject.toml, go.mod, Cargo.toml, Dockerfile, docker-compose, .github/workflows, .env files, git remote
7. Step 6 writes `.rawgentic.json` with `"version": 1`
8. Step 7 handles CLAUDE.md cleanup and pointer insertion
9. References `templates/rawgentic-json-schema.json`
10. No hardcoded React/Node.js/Docker assumptions
11. Blank project path invokes `/superpowers:brainstorm`

### Step 4: Commit

```bash
cd ./projects/rawgentic && git add skills/setup/SKILL.md && git commit -m "feat(setup): rewrite setup skill for .rawgentic.json config generation

Complete rewrite of the setup wizard. Now generates .rawgentic.json
(structured project config) instead of CLAUDE.md constants section.
Supports three flows: re-run (merge with existing), existing code
(auto-detect tech stack), and blank project (brainstorm intent).
Includes CLAUDE.md migration for existing rawgentic users.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Task 4: Create New-Project Skill with /skill-creator

**Files:**
- Create: `skills/new-project/SKILL.md`

### Step 1: Prepare the skill-creator brief

The brief should include:
- Skill name: `rawgentic:new-project`
- Design from `docs/plans/2026-03-06-plugin-overhaul-design.md` section "Skill Designs > rawgentic:new-project"
- The workspace schema (`.rawgentic_workspace.json`)
- User journeys (Cold Start)

Key requirements:
1. SKILL.md with YAML frontmatter: name, description, argument-hint (e.g., "project name or path")
2. 6-step flow from the design doc
3. Parse input — accept bare name (`my-app`) or path (`./projects/my-app`)
4. Check if already registered — offer switch or re-setup
5. Folder check with two paths:
   - Doesn't exist: create folder, ask about GitHub clone vs brand new
   - Clone failure: remove created folder, STOP, do NOT register
   - Exists: proceed
6. Create `.rawgentic_workspace.json` if it doesn't exist (first-time bootstrap)
7. Register project with `active: true`, `configured: false`, `lastUsed: now()`
8. Deactivate any previously active project
9. Delegate to `/rawgentic:setup` at the end
10. Write safety: full read-modify-write for workspace JSON

### Step 2: Invoke /skill-creator

Run: `/skill-creator` with the brief targeting `skills/new-project/SKILL.md`.

Follow skill-creator's process. Ensure the skill:
- Handles the cold-start case (no workspace file yet)
- Has rollback logic for failed git clone
- Deactivates previous active project before activating new one
- Delegates to setup as final step

### Step 3: Verify the generated SKILL.md

Verify:
1. YAML frontmatter correct
2. 6 steps matching design
3. Input parsing handles both name and path
4. Workspace file creation for first-time users
5. Clone failure rollback (rm -rf created folder)
6. Delegation to `/rawgentic:setup` in step 6

### Step 4: Commit

```bash
cd ./projects/rawgentic && git add skills/new-project/SKILL.md && git commit -m "feat(new-project): add workspace registration skill

New skill for adding projects to the rawgentic workspace. Handles
folder creation, GitHub clone, git init, and workspace registration.
Creates .rawgentic_workspace.json on first run. Delegates to
/rawgentic:setup for project configuration.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Task 5: Create Switch Skill with /skill-creator

**Files:**
- Create: `skills/switch/SKILL.md`

### Step 1: Prepare the skill-creator brief

The brief should include:
- Skill name: `rawgentic:switch`
- Design from `docs/plans/2026-03-06-plugin-overhaul-design.md` section "Skill Designs > rawgentic:switch"
- The workspace schema

Key requirements:
1. SKILL.md with YAML frontmatter: name, description, argument-hint
2. 5-step flow from design doc
3. Four invocation styles:
   - `/rawgentic:switch my-app`
   - `/rawgentic:switch ./projects/my-app`
   - "change project to my-app"
   - "change project to ./projects/my-app"
4. Match by name first, then by path
5. If not found, suggest `/rawgentic:new-project`
6. Verify target directory exists on disk before switching
7. If directory deleted, warn user and offer to unregister or re-create
8. Set target `active: true`, previous `active: false`, update `lastUsed`
9. Report confirmation with name, path, configured status
10. If not configured, suggest `/rawgentic:setup`

### Step 2: Invoke /skill-creator

Run: `/skill-creator` with the brief targeting `skills/switch/SKILL.md`.

### Step 3: Verify the generated SKILL.md

Verify:
1. YAML frontmatter correct
2. Description mentions all 4 invocation styles
3. 5 steps matching design
4. Directory existence check in step 3
5. Deactivation of previous + activation of target
6. Confirmation output format matches design

### Step 4: Commit

```bash
cd ./projects/rawgentic && git add skills/switch/SKILL.md && git commit -m "feat(switch): add project switching skill

New skill for changing the active project in the rawgentic workspace.
Supports invocation by name or path, plus natural language variants.
Verifies target directory exists, updates workspace file, and reports
configured status.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Task 6: Update Plugin Metadata

Update the plugin description to reflect the new skill count.

**Files:**
- Modify: `.claude-plugin/plugin.json`
- Modify: `.claude-plugin/marketplace.json`

### Step 1: Update plugin.json description

Change description from "9 SDLC workflow skills" to "12 SDLC workflow skills" and add "workspace management" to the description:

```json
{
  "name": "rawgentic",
  "version": "2.0.0",
  "description": "12 SDLC workflow skills for Claude Code: workspace management, project setup, issue creation, feature implementation, bug fixing, refactoring, documentation, dependency updates, security audits, performance optimization, and incident response.",
  "author": {
    "name": "3D-Stories",
    "url": "https://github.com/3D-Stories"
  },
  "homepage": "https://github.com/3D-Stories/rawgentic",
  "repository": "https://github.com/3D-Stories/rawgentic",
  "license": "MIT",
  "keywords": [
    "sdlc",
    "workflow",
    "tdd",
    "code-review",
    "quality-gates",
    "conventional-commits",
    "security-audit",
    "incident-response",
    "workspace-management",
    "project-config"
  ]
}
```

### Step 2: Update marketplace.json description to match

Update the description field in marketplace.json to match plugin.json. Update version to 2.0.0.

### Step 3: Verify both files are valid JSON

Run: `cd ./projects/rawgentic && python3 -c "import json; json.load(open('.claude-plugin/plugin.json')); json.load(open('.claude-plugin/marketplace.json')); print('Both valid')"`
Expected: `Both valid`

### Step 4: Commit

```bash
cd ./projects/rawgentic && git add .claude-plugin/plugin.json .claude-plugin/marketplace.json && git commit -m "chore: bump version to 2.0.0 and update plugin metadata

Update skill count from 9 to 12 (added setup rewrite, new-project, switch).
Add workspace-management and project-config keywords.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Task 7: Integration Verification

Verify all Phase 1 artifacts work together end-to-end.

**Files:** None (verification only)

### Step 1: Verify directory structure

Run: `find ./projects/rawgentic/skills -name "SKILL.md" | sort`
Expected:
```
./projects/rawgentic/skills/create-issue/SKILL.md
./projects/rawgentic/skills/fix-bug/SKILL.md
./projects/rawgentic/skills/implement-feature/SKILL.md
./projects/rawgentic/skills/incident/SKILL.md
./projects/rawgentic/skills/new-project/SKILL.md
./projects/rawgentic/skills/optimize-perf/SKILL.md
./projects/rawgentic/skills/refactor/SKILL.md
./projects/rawgentic/skills/security-audit/SKILL.md
./projects/rawgentic/skills/setup/SKILL.md
./projects/rawgentic/skills/switch/SKILL.md
./projects/rawgentic/skills/update-deps/SKILL.md
./projects/rawgentic/skills/update-docs/SKILL.md
```
(12 skills total)

### Step 2: Verify all SKILL.md files have valid YAML frontmatter

Run: `for f in ./projects/rawgentic/skills/*/SKILL.md; do echo "=== $f ==="; head -5 "$f"; echo; done`
Expected: Each file starts with `---` and has `name:` on line 2.

### Step 3: Verify schema template is valid and complete

Run: `python3 -c "import json; d=json.load(open('./projects/rawgentic/templates/rawgentic-json-schema.json')); assert 'version' in d; assert 'project' in d; assert 'repo' in d; assert 'custom' in d; print(f'Schema has {len(d)} top-level keys: {list(d.keys())}')"`
Expected: Schema has all expected keys including version and custom.

### Step 4: Verify hook syntax

Run: `bash -n ./projects/rawgentic/hooks/session-start && echo "Hook syntax OK"`
Expected: `Hook syntax OK`

### Step 5: Verify new skills reference the design doc patterns

Check that setup, new-project, and switch skills contain key design elements:
```bash
echo "--- setup ---"
grep -c "rawgentic_workspace.json\|rawgentic.json\|version.*1\|configured.*true\|CLAUDE.md" ./projects/rawgentic/skills/setup/SKILL.md
echo "--- new-project ---"
grep -c "rawgentic_workspace.json\|git clone\|git init\|rawgentic:setup" ./projects/rawgentic/skills/new-project/SKILL.md
echo "--- switch ---"
grep -c "rawgentic_workspace.json\|active.*true\|active.*false\|rawgentic:new-project\|rawgentic:setup" ./projects/rawgentic/skills/switch/SKILL.md
```
Expected: Each skill has multiple matches (non-zero counts).

### Step 6: Review git log for all Phase 1 commits

Run: `cd ./projects/rawgentic && git log --oneline -10`
Expected: See all Phase 1 commits in conventional commit format.

---

## Task Summary

| Task | Artifact | Method | Estimated Steps |
|------|----------|--------|-----------------|
| 1 | Schema template | Manual write | 5 |
| 2 | Session-start hook | Manual rewrite | 9 |
| 3 | Setup skill | /skill-creator | 4 |
| 4 | New-project skill | /skill-creator | 4 |
| 5 | Switch skill | /skill-creator | 4 |
| 6 | Plugin metadata | Manual edit | 4 |
| 7 | Integration verification | Verification only | 6 |
| **Total** | | | **36 steps** |

**Dependency chain:** Task 1 (schema) -> Task 2 (hook) -> Task 3 (setup) -> Task 4 (new-project) -> Task 5 (switch) -> Task 6 (metadata) -> Task 7 (verify)

Tasks 1-2 are independent of each other and could run in parallel. Tasks 3-5 depend on Task 1 (schema reference) but are otherwise independent. Task 6-7 run after all skills are created.
