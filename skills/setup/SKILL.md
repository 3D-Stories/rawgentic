---
name: rawgentic:setup
description: Configure a project's .rawgentic.json — the structured config that all rawgentic workflow skills depend on. Auto-detects tech stack, testing, CI, database, services, and more for existing codebases; brainstorms intent for blank projects. Handles migration from old CLAUDE.md-based rawgentic setups. Use this whenever a project needs initial configuration, reconfiguration, or when the session-start hook says "Config missing -- run /rawgentic:setup."
argument-hint: (no arguments needed — operates on the active project)
---

<role>
You are the rawgentic setup wizard. Your job is to generate a `.rawgentic.json` configuration file for the active project by detecting its environment, asking the user to confirm your findings, and writing a structured config that all rawgentic workflow skills will consume.

You are technology-agnostic — you detect whatever stack the project uses rather than assuming any particular language, framework, or infrastructure. You present findings section-by-section and only write files after explicit user approval.
</role>

# Setup Wizard — `/rawgentic:setup`

Run through all steps below **sequentially** (Steps 1–9, with optional Step 4b). Present results at each step and wait for user acknowledgment before proceeding.

<schema-reference>
The full annotated schema lives at `templates/rawgentic-json-schema.json` in the rawgentic plugin directory. Read it at the start of Step 3 to understand every field and section available. That file is the single source of truth for the `.rawgentic.json` structure.
</schema-reference>

---

## Step 1: Verify Context

Determine the active project using this fallback chain:
1. **Conversation context:** If a previous `/rawgentic:switch` in this session set the active project, use that.
2. **Session registry:** Read `claude_docs/session_registry.jsonl`. Grep for your session_id. If found, use the project from the most recent matching line.
3. **Workspace default:** Read `.rawgentic_workspace.json` from the Claude root directory (the directory Claude was launched from). If exactly one project has `active == true`, use it. If multiple projects are active, STOP and tell user: "Multiple active projects. Run `/rawgentic:switch <name>` to bind this session."

At any level:
- `.rawgentic_workspace.json` **missing** → STOP. Tell the user: "No rawgentic workspace found. Run `/rawgentic:new-project` first to register a project."
- `.rawgentic_workspace.json` **malformed** → STOP. Tell the user: "Workspace file is corrupted. Run `/rawgentic:new-project` to regenerate, or fix `.rawgentic_workspace.json` manually."
- **No active project** found at any level → STOP. Tell the user: "No active project. Run `/rawgentic:new-project` to set one up, or `/rawgentic:switch` to bind this session."

Extract the active project's `name` and `path`. Confirm to the user:

> Setting up project: **<name>** at `<path>`

### Step 1b: Ensure Layer 2 CLAUDE.md Exists

Check if `{WORKSPACE_ROOT}/CLAUDE.md` exists (where WORKSPACE_ROOT is the directory containing `.rawgentic_workspace.json`).

**If missing:** Scaffold it using the Layer 2 scaffolding flow:

1. **Prompt for GitHub Org name.** Check `~/.claude/CLAUDE.md` — if org info is found there (look for patterns like `**Org:**` or `GitHub` section with org name), suggest it as the default. If not found, ask the user: "What GitHub org does this workspace belong to?"

2. **Prompt for GitHub PAT.** Check `~/.claude/CLAUDE.md` — if a PAT is found there (look for `github_pat_` pattern), tell the user: "I found a GitHub PAT in your personal CLAUDE.md (`~/.claude/CLAUDE.md`). Since this workspace is org-scoped, would you like me to move it to the workspace CLAUDE.md instead?" If not found, ask: "Please provide your GitHub PAT for this org, or type 'placeholder' to add it later."

3. **Write `{WORKSPACE_ROOT}/CLAUDE.md`** with this template:

   ```markdown
   # Workspace Instructions

   ## GitHub
   - **Org:** {org-name}

   ### GitHub PAT (fine-grained)
   - Token: `{pat-or-placeholder}`
   - Scopes: Contents (r/w), Issues (r/w), Pull Requests (r/w), Workflows (r/w), Metadata (r)

   ## Rawgentic
   Workspace config: .rawgentic_workspace.json

   ## Workspace Structure
   - Projects live in `./projects/` as individual git repos
   - Each project has its own CLAUDE.md with project-specific instructions
   - Project configuration: `projects/{name}/.rawgentic.json`

   ## Team Process
   [Added as team conventions solidify]
   ```

4. **Ask about team process:** "Do you have any team-wide conventions to add? (You can add these later.)" If yes, add them. If no, leave the placeholder.

Confirm to the user:
> Created workspace CLAUDE.md at `{WORKSPACE_ROOT}/CLAUDE.md`

**If exists:** Read it and verify it has the `## Rawgentic` section. If the section is missing, offer to add it. Continue to Step 2.

---

## Step 2: Migration Check

Check if `CLAUDE.md` (in the Claude root) contains either of these markers from the old rawgentic setup:
- `## Project Constants (generated by /rawgentic:setup)`
- `## SDLC Workflow Principles`

**If found:**
1. Parse the existing constants section — extract values like REPO, PROJECT_ROOT, test commands, deploy commands, DB_NAME, ports, etc.
2. Store these as **seed values** that will pre-populate detection in Step 3.
3. Flag this file for cleanup in Step 7.
4. Tell the user: "Found old rawgentic configuration in CLAUDE.md. I'll use these values as a starting point and migrate to `.rawgentic.json`."

**If not found:** Continue silently.

---

## Step 3: Detect or Brainstorm

Read `templates/rawgentic-json-schema.json` from the rawgentic plugin directory to understand the full schema structure.

Then determine which of the three sub-flows applies:

### Sub-flow A: Existing `.rawgentic.json` (re-run)

**Condition:** `<activeProject.path>/.rawgentic.json` exists.

1. Read and parse the existing config.
2. Present the current configuration to the user, section by section.
3. Ask: "What would you like to update? Or say 'full re-detect' to re-scan the project."
4. If user wants updates: apply changes following the **merge policy** below.
5. If user wants full re-detect: proceed to Sub-flow B but preserve any values from the existing config that the user hasn't asked to change (these are "learned entries" from previous skill runs).

<merge-policy>
When merging with an existing .rawgentic.json:
- **Append** to arrays — add newly detected items, never remove existing entries
- **Set** fields that are null, missing, or empty — never overwrite existing non-null values
- **On conflict** (detected value differs from existing value) — present both to the user and ask which to keep
- Always read the full file, modify in memory, write the full file back
</merge-policy>

### Sub-flow B: Existing Code, No Config (auto-detect)

**Condition:** The project directory has files (not empty) but no `.rawgentic.json`.

Run the detection sequence below. If migration seed values exist from Step 2, use them to pre-fill fields — but still verify them against the actual project files.

<detection-sequence>
Scan the project root (`<activeProject.path>`) for each of the following. Only include a section in the config if you actually find evidence for it.

**1. Required: Project metadata**
- `project.name`: Use the active project name from workspace
- `project.type`: Infer from what you find — `application` (has runnable services), `library` (has package publishing config), `infrastructure` (primarily IaC/config), `scripts` (utility scripts), `docs` (documentation-focused), `research` (notebooks/data analysis). Ask user to confirm.
- `project.description`: Draft from README.md first line, or package.json description, or ask user.

**2. Required: Repository**
- Run `git remote get-url origin` from the project directory.
- Parse `owner/repo` from HTTPS (`https://github.com/owner/repo.git`) or SSH (`git@github.com:owner/repo.git`) formats.
- Detect default branch: `git symbolic-ref refs/remotes/origin/HEAD 2>/dev/null` or fall back to `main`.

**3. Optional: Tech stack (informational)**
Build a list of technologies detected. This is informational only — workflow skills use the structured sections below, not this list.

**4. Optional: Testing**
Search for test framework configuration:

| Indicator | Framework | Type |
|-----------|-----------|------|
| `vitest.config.*` or vitest in package.json | vitest | unit |
| `jest.config.*` or jest in package.json | jest | unit |
| `playwright.config.*` | playwright | e2e |
| `cypress.config.*` or `cypress/` dir | cypress | e2e |
| `pytest.ini`, `pyproject.toml [tool.pytest]`, `setup.cfg [tool:pytest]` | pytest | unit |
| `*_test.go` files | go test | unit |
| `Cargo.toml` with `[dev-dependencies]` test entries | cargo test | unit |
| `.rspec` or `spec/` dir with Gemfile containing rspec | rspec | unit |
| `phpunit.xml` | phpunit | unit |

For each found: determine the run command (check package.json scripts, Makefile targets, or use framework defaults), config file path, and test directory.

**5. Optional: Database**
- Check `.env*` files for patterns: `DATABASE_URL`, `DB_HOST`, `DB_NAME`, `POSTGRES_*`, `MYSQL_*`, `MONGO_*` (report presence only — **never display secret values**)
- Check Docker Compose files for database service images (postgres, mysql, mongo, redis, etc.)
- If found: determine type, CLI tool, and container name if dockerized.

**6. Optional: Services**
- Check Docker Compose files for service definitions with port mappings
- Check package.json for dev server scripts (e.g., `vite`, `next dev`, `express`)
- Check for Python entry points (`main.py`, `app.py`, `manage.py`)
- Check for Go entry points (`cmd/*/main.go`)
- For each service: infer name, type (frontend/backend/worker/api/proxy), framework, port, and entry point.

**7. Optional: Infrastructure**
- Find all `docker-compose*.yml` and `compose*.yml` files
- Check for host references in `.env*` files or config
- Check for Terraform, Pulumi, CloudFormation, or Ansible files

**8. Optional: Deploy**
- Check for deploy scripts, Makefile deploy targets, or CI deployment steps
- Infer method: `compose` (Docker Compose up), `script` (custom script), `ssh` (remote deploy), `manual` (nothing automated)

**9. Optional: Security**
- Search source files for auth patterns: JWT token handling, API key middleware, OAuth config
- Detect validation libraries: search imports/requires for zod, joi, yup, ajv, pydantic, marshmallow
- Identify data channels: REST endpoints, WebSocket/Socket.IO, gRPC, message queues

**10. Optional: CI**
- Check `.github/workflows/` for GitHub Actions
- Check `.gitlab-ci.yml` for GitLab CI
- Check `Jenkinsfile`, `.circleci/`, `bitbucket-pipelines.yml`

**11. Optional: Formatting**
- Check for `.prettierrc*`, `.eslintrc*`, `biome.json`
- Check `pyproject.toml` for `[tool.black]`, `[tool.ruff]`
- Check for `.editorconfig`, `rustfmt.toml`

**12. Optional: Documentation**
- Check for `README.md`, `docs/` directory, `CHANGELOG.md`
- Detect format (markdown, rst, asciidoc)
</detection-sequence>

### Sub-flow C: Empty/New Project (brainstorm)

**Condition:** The project directory is empty or contains only git initialization files (`.git/`, `.gitignore`).

1. Tell the user: "This looks like a new project. Let's figure out what you're building."
2. Invoke `/superpowers:brainstorm` to explore:
   - What is this project for? (application, library, infrastructure, scripts, docs, research)
   - What technologies are planned?
   - What's the core purpose / one-sentence description?
3. Use the brainstorm results to populate the required fields (`project`, `repo`).
4. Add any planned technologies to `techStack` (informational).
5. Leave optional sections empty — they'll be populated by the learning config pattern as workflow skills discover capabilities.

---

## Step 4: Present Detected Config

Show the user the assembled `.rawgentic.json` as formatted JSON. For each section, show where the values came from:

```
## Detected Configuration

### project (required)
Source: README.md + git remote
{
  "name": "my-app",
  "type": "application",
  "description": "A real-time monitoring dashboard"
}

### repo (required)
Source: git remote get-url origin
{
  "provider": "github",
  "fullName": "org/my-app",
  "defaultBranch": "main"
}

### testing
Source: vitest.config.ts, playwright.config.ts
{
  "frameworks": [...]
}

[... only sections where something was detected ...]
```

---

## Step 4b: Critique Detected Config (Optional)

After presenting the detected config in Step 4, compute a **complexity score** to determine whether to offer a multi-agent critique.

### Complexity Heuristic

Count these signals from the detected config:

| Signal | Condition | +1 if |
|--------|-----------|-------|
| Compose files | `infrastructure.docker.composeFiles` | length ≥ 3 |
| Infrastructure hosts | `infrastructure.hosts` | length ≥ 2 |
| Test frameworks | `testing.frameworks` | length ≥ 2 |
| Multi-env database | `database` exists AND multiple environments detected (e.g., dev/prod/test values in `.env*` files) | true |
| Deploy complexity | Multiple deploy methods detected (e.g., script + CI/CD, or ssh + compose) | true |

**Score interpretation:**
- **Score 0:** Skip critique entirely — proceed directly to Step 5.
- **Score 1:** Offer passively: *"Would you like me to run a critique on the detected config? (Optional — can catch missing capabilities)"*
- **Score ≥ 2:** Auto-suggest: *"This is a complex project (N complexity signals detected). I recommend running a multi-agent critique to validate completeness before you review. Run critique?"*

If the user declines (or score is 0), proceed to Step 5 with the config unchanged.

### Critique Execution

If the user accepts, invoke `/reflexion:critique` with the detected `.rawgentic.json` as the work product.

**Three judges evaluate in parallel:**

**Judge 1: Requirements Validator**
- For each schema section, check whether the detected config missed capabilities that exist in the actual project files
- Look for: test frameworks with config files but not detected, services with port mappings not captured, database references in `.env*` not reflected in config
- Check: are all Docker Compose services represented? Are all CI workflows captured?

**Judge 2: Solution Architect**
- Evaluate structural decisions: should services be split (e.g., frontend + backend vs monolith)?
- Check environment awareness: does the database config cover all environments?
- Validate infrastructure topology: do host assignments match actual deployment targets?
- Check service dependencies and port consistency across compose files

**Judge 3: Code Quality Reviewer**
- Verify every concrete value against source files: ports in config match ports in compose/code, paths exist on disk, container names match compose service names
- Check test commands actually work (correct binary, correct config file reference)
- Validate framework detection: does the detected framework version/type match the actual config file?

Each judge produces findings:
```
Finding #N:
- Severity: Critical | High | Medium | Low
- Category: missing_capability | wrong_value | structural | environment | completeness
- Description: [what was missed or incorrect]
- Recommendation: [specific config change]
- Ambiguity flag: clear | ambiguous
```

### Applying Findings

1. **Auto-apply** all findings with `ambiguity_flag == "clear"` — amend the detected config in memory.
2. **Present ambiguous findings** to the user for resolution before proceeding.
3. **Re-present** the amended config with a summary of changes: *"Critique found N issues. Applied M automatically. K need your input:"*
4. Proceed to Step 5 with the amended config.

---

## Step 5: User Confirms/Edits

Walk through each section and ask the user to confirm or edit:

> "Does the **project** section look right?"
> "Does the **testing** section look right? Any frameworks I missed?"
> "I didn't detect a **database** — does this project use one?"

Key behaviors:
- Only present sections that have content (detected or seeded)
- After all detected sections, ask: "Any sections I missed? (database, deploy, services, security, etc.)"
- Accept corrections inline — don't make the user rewrite JSON
- For `project.type`, always ask for explicit confirmation since inference can be wrong

---

## Step 6: Write `.rawgentic.json`

After user approval, write the final config to `<activeProject.path>/.rawgentic.json`.

Requirements:
- Must include `"version": 1` as the first field
- Must include the three required sections: `project`, `repo`, and at minimum an empty `custom: {}`
- Omit optional sections that have no content (don't write empty objects/arrays for undetected capabilities)
- Format as pretty-printed JSON (2-space indent)
- Show the user the exact content before writing and get a final "go ahead"

---

## Step 7: Update CLAUDE.md

Check `CLAUDE.md` in the Claude root directory.

**If it contains old rawgentic sections** (flagged in Step 2):
1. Remove the `## Project Constants (generated by /rawgentic:setup)` section and everything under it until the next `##` heading or end of file
2. Remove the `## SDLC Workflow Principles` section similarly if present
3. Remove the `## Test Commands` and `## Deploy Commands` sections if present
4. Tell the user: "Migrated project constants from CLAUDE.md to .rawgentic.json"

**Ensure the static pointer block is present** (add if missing, leave alone if already there):
```markdown
## Rawgentic
Workspace config: .rawgentic_workspace.json
```

This pointer never changes — it tells Claude where to find the workspace config.

**Layer 3 guardrail:** If the project's CLAUDE.md (`<activeProject.path>/CLAUDE.md`) contains a `## Rawgentic` section or `Workspace config:` pattern, remove it and tell the user: "Removed Rawgentic pointer from project CLAUDE.md — it belongs in the workspace CLAUDE.md, not in project files."

---

### Step 7b: Layer 1 Advisory

Read `~/.claude/CLAUDE.md` and check for content that the three-layer architecture suggests moving. Present all suggestions as a single checklist — do not ask one at a time.

**Check for these patterns:**

- **GitHub PAT** (pattern: `github_pat_`): Suggest: "Your GitHub PAT is in `~/.claude/CLAUDE.md` (personal/machine scope). Since this workspace is org-scoped, it would be better placed in the workspace CLAUDE.md. Would you like me to move it?"

- **GitHub Org** (pattern: `**Org:**` or similar): Same suggestion — offer to move to workspace CLAUDE.md.

- **Team process sections** (patterns: `SDLC`, `Workflow Principles`, `Conventional Commit`, `TDD`): Suggest: "You have team process sections in your personal CLAUDE.md. These could be shared via the workspace CLAUDE.md's Team Process section. Would you like to keep them personal, or move them to the workspace level?"

- **Empty placeholder sections** (sections with only `---` or whitespace as content): Suggest: "You have empty sections in `~/.claude/CLAUDE.md` (e.g., Infrastructure, Servers). Would you like me to remove them to reduce clutter?"

All suggestions require explicit user approval. If the user declines, leave Layer 1 unchanged. If the user approves a move:
1. Remove the content from `~/.claude/CLAUDE.md`
2. Add it to `{WORKSPACE_ROOT}/CLAUDE.md` in the appropriate section

**Interaction between Step 1b and Step 7b:** Step 1b may have already moved PAT/Org content during Layer 2 scaffolding. Step 7b should check what's actually present — if content was already moved, it won't be found and no suggestion is generated. This makes the two steps idempotent together.

---

## Step 8: Update Workspace

Read `.rawgentic_workspace.json`, find the active project entry, and set `"configured": true`. Write the file back.

### Step 8b: Ensure Session Notes Infrastructure

The `wal-stop` hook requires `claude_docs/session_notes/<project>.md` to exist. Ensure this infrastructure is in place:

1. Create `{WORKSPACE_ROOT}/claude_docs/session_notes/` directory if it doesn't exist.
2. If `{WORKSPACE_ROOT}/claude_docs/session_notes/<project-name>.md` does not exist, create it with:
   ```markdown
   # Session Notes -- <project-name>
   ```
3. If `{WORKSPACE_ROOT}/claude_docs/session_registry.jsonl` does not exist, create it as an empty file.

This is idempotent — if `/rawgentic:new-project` already created these, this step is a no-op.

---

## Step 9: Verify

Run these checks and present a summary:

1. **File exists** — Confirm `.rawgentic.json` was written at the expected path
2. **Valid JSON** — Parse the file and confirm no syntax errors
3. **Required fields** — Confirm `version`, `project.name`, `project.type`, `project.description`, `repo.provider`, `repo.fullName`, `repo.defaultBranch` are all present and non-empty
4. **No template placeholders** — Confirm no `${...}` or placeholder strings remain
5. **Repo accessible** — Run `gh repo view <repo.fullName> --json name` to confirm GitHub access (warn but don't fail if gh is not authenticated)
6. **Workspace updated** — Confirm the active project shows `configured: true`

```
Setup Complete!

| Check                    | Status |
|--------------------------|--------|
| .rawgentic.json written  | OK     |
| Valid JSON               | OK     |
| Required fields present  | OK     |
| No placeholders          | OK     |
| GitHub repo accessible   | OK/WARN|
| Workspace updated        | OK     |

Project "<name>" is now configured.
All rawgentic workflow skills will use <path>/.rawgentic.json for project constants.

Next steps:
- Review your .rawgentic.json if you want to add more detail
- Try a workflow: /rawgentic:implement-feature, /rawgentic:fix-bug, etc.
- Skills will update .rawgentic.json as they discover new project capabilities
```
