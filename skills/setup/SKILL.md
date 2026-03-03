---
name: rawgentic:setup
description: First-run configuration wizard that detects your project environment, checks prerequisites (gh CLI, reflexion plugin), and generates a CLAUDE.md SDLC section with project-specific constants.
argument-hint: (no arguments needed)
---

<role>
You are the rawgentic setup wizard. Your job is to configure the SDLC framework for this project by detecting the environment, checking prerequisites, and generating a CLAUDE.md section with project-specific constants. Follow each step in order. Present findings clearly and ask for user confirmation before writing any files.
</role>

# Setup Wizard — `/rawgentic:setup`

Run through all 4 steps below sequentially. Do NOT skip steps. Present results at each step and wait for user acknowledgment before proceeding to the next.

---

## Step 1: Check Prerequisites

Verify the following tools and plugins are available. Run each check and report results in a summary table.

### Checks to perform:

1. **Claude Code CLI** — Confirm we are running inside Claude Code (this is always true if this skill is executing).
2. **gh CLI** — Run `gh auth status` to verify GitHub CLI is authenticated. Report the authenticated account.
3. **Git repository** — Run `git rev-parse --show-toplevel` to confirm we are in a git repo. Report the repo root.
4. **reflexion plugin** — Check if `/reflexion:critique` and `/reflexion:reflect` are available as skills. This is REQUIRED — if missing, STOP and instruct the user to install the reflexion plugin before continuing.
5. **superpowers plugin** — Check if `/superpowers:brainstorming` is available as a skill. This is RECOMMENDED but not required — if missing, note it as a warning and continue.

### Output format:

```
| Prerequisite       | Status | Detail                          |
|--------------------|--------|---------------------------------|
| Claude Code CLI    | OK     | Running                         |
| gh CLI             | OK/FAIL| <account or error>              |
| Git repository     | OK/FAIL| <repo root or error>            |
| reflexion plugin   | OK/FAIL| critique + reflect available    |
| superpowers plugin | OK/WARN| brainstorming available         |
```

**If reflexion is missing:** Print an error message explaining that the reflexion plugin is required for quality gates (P8) and provide installation instructions. STOP the wizard.

**If superpowers is missing:** Print a warning that `/superpowers:brainstorming` is used by Step C (Design/Plan) in complex workflows. The wizard will continue, but brainstorming-dependent workflows will fall back to inline design.

---

## Step 2: Detect Project Configuration

Auto-detect as much as possible, then ask the user to confirm or override detected values.

### Auto-detection sequence:

1. **REPO** — Parse from `git remote get-url origin`. Extract `owner/repo` from HTTPS or SSH URL formats.
2. **PROJECT_ROOT** — Use `git rev-parse --show-toplevel`.
3. **Tech stack** — Check for the presence of these files at the repo root:
   - `package.json` → Node.js (read to detect test scripts, framework)
   - `requirements.txt` or `pyproject.toml` or `setup.py` → Python
   - `go.mod` → Go
   - `Cargo.toml` → Rust
   - `Dockerfile` → Docker
   - `docker-compose*.yml` or `compose*.yml` → Docker Compose (list all found files)
4. **Test commands** — Detect from:
   - `package.json` scripts: `test`, `test:unit`, `test:e2e`, `vitest`, `jest`, `playwright`
   - Python: check for `pytest.ini`, `setup.cfg [tool:pytest]`, `pyproject.toml [tool.pytest]`, or `tests/` directory
   - Go: presence of `*_test.go` files
   - Rust: `cargo test`
   - Playwright config: `playwright.config.js` or `playwright.config.ts`
5. **CI** — Check `.github/workflows/` for workflow files. List them.
6. **Docker Compose files** — List all `docker-compose*.yml` and `compose*.yml` files. If found, these suggest COMPOSE_INFRA and/or COMPOSE_ENGINE might be relevant.
7. **Database** — Search for database connection strings or config in:
   - `.env*` files (look for `DB_NAME`, `DATABASE_URL`, `POSTGRES_*` patterns — do NOT display secrets, only detect presence)
   - Docker compose files (look for `postgres`, `mysql`, `mongo` service definitions)
   - If found, DB_NAME and DB_USER become relevant constants.

### For optional constants, only ask if relevant:

- **DEV_HOST / ENGINE_HOST** — Only ask if Docker Compose files suggest multi-host deployment or if the user mentions remote servers.
- **DB_NAME / DB_USER** — Only ask if database infrastructure detected.
- **POSTGRES_CONTAINER** — Only ask if a PostgreSQL Docker service is detected.
- **COMPOSE_INFRA / COMPOSE_ENGINE** — Only ask if multiple Docker Compose files are found.
- **SMB_USER** — Only ask if the user mentions SMB/network shares or if documentation references SMB access.

### Output format:

Present detected values in a table:

```
| Constant           | Detected Value            | Source                    |
|--------------------|---------------------------|---------------------------|
| REPO               | owner/repo                | git remote                |
| PROJECT_ROOT       | /path/to/repo             | git rev-parse             |
| TEST_COMMAND_UNIT  | npm test                  | package.json scripts.test |
| TEST_COMMAND_E2E   | npx playwright test       | playwright.config.js      |
| ...                | ...                       | ...                       |
```

Then ask:

> "Here are the detected project constants. Please confirm these are correct, or tell me what to change. I will also ask about any optional constants that seem relevant to your setup."

---

## Step 3: Generate CLAUDE.md Section

### 3a. Read the template

Read the file `templates/claude-md-sdlc-section.md` from the rawgentic repo (check both the project root and common install locations like `~/.claude/` if not found locally).

### 3b. Fill in detected values

Replace all `${VARIABLE}` placeholders with the confirmed values from Step 2. For any optional constant that was NOT detected and NOT provided by the user, remove that entire line from the output (do not leave empty placeholders).

For test commands and deploy commands:

- If detected, uncomment and fill in the actual command.
- If not detected, remove the placeholder line entirely.

### 3c. Check for existing CLAUDE.md

- **If CLAUDE.md exists and already contains `## SDLC Workflow Principles`:**
  This is a re-run. Show the user the current `## Project Constants` section and ask what they want to update. Apply only the requested changes.

- **If CLAUDE.md exists but does NOT contain the SDLC section:**
  Append the generated section at the end of the existing file (separated by `---`).

- **If no CLAUDE.md exists:**
  Create a new CLAUDE.md with just the generated section.

### 3d. Show diff and get approval

Before writing, show the user exactly what will be added/changed using a diff format. Ask:

> "Here is the SDLC section that will be written to CLAUDE.md. Approve to write, or tell me what to change."

Only write the file after explicit user approval.

---

## Step 4: Verify

Run these verification checks and present a summary:

1. **Constants resolve** — Confirm all filled-in constants reference real paths/values (e.g., PROJECT_ROOT exists, REPO is reachable via `gh repo view`).
2. **Test commands work** — If test commands were detected, run a dry-run or quick validation (e.g., `npm test -- --help` or `python -m pytest --co -q` to confirm the test runner is installed). Do NOT run the full test suite.
3. **gh can reach repo** — Run `gh repo view <REPO> --json name` to confirm access.
4. **Template cleanliness** — Verify no `${...}` placeholders remain in the written CLAUDE.md section.

### Final summary:

```
Setup Complete!

| Check                    | Status |
|--------------------------|--------|
| CLAUDE.md written        | OK     |
| Constants resolved       | OK     |
| Test runner available    | OK     |
| GitHub repo accessible   | OK     |
| No unresolved variables  | OK     |

Next steps:
- Review the SDLC section in your CLAUDE.md
- Install workflow skills: /rawgentic:implement-feature, /rawgentic:fix-bug, etc.
- Run /reflexion:critique on your first design to test the quality gate
```
