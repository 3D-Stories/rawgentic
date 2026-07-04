# Setup detect / brainstorm flows — Step 3 detail

Read this file before executing Step 3. It holds the three sub-flows (A: re-run,
B: auto-detect, C: brainstorm) that Step 3 dispatches to.

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
- Write `ci.provider` when found (drives `has_ci`).
- **CI quarantine (#137):** if the user reports the suite is chronically red for reasons unrelated to any diff (an incomplete port, a stale artifact check) and should NOT gate, offer to set `ci.status: "quarantined"` with a required `ci.quarantineReason` (a one-line why) and an optional `ci.quarantinedSince` (ISO date, so a staleness nag can fire after 30 days). Never set this automatically — quarantine is a human declaration (a chronically-red suite and a genuinely broken diff are mechanically indistinguishable). Omit `ci.status` (or set `"active"`) for a healthy suite. Example: `"ci": { "provider": "github-actions", "status": "quarantined", "quarantineReason": "incomplete Tauri port; build-path check stale", "quarantinedSince": "2026-07-01" }`.

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
