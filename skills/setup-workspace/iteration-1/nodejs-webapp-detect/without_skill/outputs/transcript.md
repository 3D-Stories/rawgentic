# Detection Transcript: webapp project

## Project Scan Summary

**Project path:** `/tmp/rawgentic-test-fixtures/projects/webapp`
**Date:** 2026-03-06
**Method:** Manual file scanning without skill assistance

---

## Files Examined

| File | Purpose |
|------|---------|
| `package.json` | Dependencies, scripts, project metadata |
| `docker-compose.yml` | Infrastructure services definition |
| `.env` | Environment variables (DB connection, port) |
| `.prettierrc` | Prettier config (empty `{}` = defaults) |
| `vitest.config.ts` | Vitest config (empty `{}` = defaults) |
| `playwright.config.ts` | Playwright config (empty `{}` = defaults) |
| `src/auth.ts` | Authentication module using JWT |
| `.github/workflows/ci.yml` | CI pipeline (name only: "CI") |
| `.github/workflows/deploy.yml` | Deploy pipeline (name only: "Deploy") |
| `README.md` | Project description |
| `.git/config` | Git remote origin URL |
| `.git/HEAD` | Current branch reference |

---

## Detections and Decisions

### 1. Project Identity
- **Name:** `webapp` (from `package.json` name field)
- **Type:** `fullstack-webapp` -- React frontend + Express backend in a single package
- **Description:** Derived from `package.json` description: "ACME Corp internal dashboard" combined with README: "Internal dashboard for monitoring and analytics"

### 2. Repository
- **Provider:** GitHub (detected from remote URL `https://github.com/acme-corp/webapp.git`)
- **Full name:** `acme-corp/webapp` (parsed from remote URL)
- **Default branch:** `master` (from `.git/HEAD` -> `ref: refs/heads/master`)

### 3. Tech Stack
- **Language:** TypeScript v5.3.0 (devDependency)
- **Runtime:** Node.js (inferred from npm ecosystem)
- **Framework:** React 18.2.0 (dependency)
- **Build tool:** Vite (dev script: `vite`, build script: `tsc && vite build`)
- **Server:** Express 4.18.2 (dependency)
- **Package manager:** npm (no yarn.lock or pnpm-lock.yaml found; package.json present)
- **Key libraries:** zod (validation), jsonwebtoken (auth)

### 4. Testing
- **Unit tests:** Vitest v1.0.0 with config at `vitest.config.ts` (empty defaults). Command: `vitest run`
- **E2E tests:** Playwright v1.40.0 with config at `playwright.config.ts` (empty defaults). Command: `playwright test`
- **Decision:** Listed both test frameworks separately since they serve different purposes (unit vs e2e)

### 5. Database
- **Type:** PostgreSQL 16 (from `docker-compose.yml` image: `postgres:16`)
- **Connection string:** From `.env` -> `DATABASE_URL=postgresql://webapp:secret@localhost:5432/webapp_dev`
- **DB name:** `webapp_dev` (from both `.env` DB_NAME and docker-compose POSTGRES_DB)
- **User:** `webapp` (from docker-compose POSTGRES_USER and connection string)
- **Port:** 5432 (standard postgres, confirmed in docker-compose ports mapping)

### 6. Services (Docker Compose)
- **app:** Build from current directory, exposed on port 3000
- **postgres:** Official postgres:16 image, exposed on port 5432

### 7. Infrastructure
- **Containerization:** docker-compose v3.8
- **Env file:** `.env` present with DATABASE_URL, DB_NAME, PORT

### 8. CI/CD
- **Provider:** GitHub Actions
- **CI workflow:** `.github/workflows/ci.yml` (minimal, only contains `name: CI`)
- **Deploy workflow:** `.github/workflows/deploy.yml` (minimal, only contains `name: Deploy`)
- **Decision:** Both workflow files are stubs but their presence signals intent. Listed them in separate ci and deploy sections.

### 9. Security
- **Authentication:** JWT-based, using `jsonwebtoken` library, implementation in `src/auth.ts`
- **Validation:** Zod library present in dependencies for runtime schema validation
- **Decision:** Identified JWT from both the dependency and the import in `src/auth.ts`. Zod is a validation library commonly used for input sanitization/validation.

### 10. Formatting
- **Prettier:** v3.1.0 with empty config (defaults). Config file: `.prettierrc`
- **ESLint:** Referenced in scripts (`eslint src/`) but no eslint config file or devDependency found
- **Decision:** Included both tools. ESLint is referenced as a lint command even though its config/dependency is missing from the fixture.

---

## Observations

1. **Minimal fixture:** Many config files contain empty objects `{}`, indicating this is a test fixture rather than a real project. Detection was based on the presence and naming of files plus dependency declarations.
2. **No lockfile:** No `package-lock.json`, `yarn.lock`, or `pnpm-lock.yaml` was found. Defaulted package manager to npm based on the presence of `package.json` alone.
3. **No Dockerfile:** The docker-compose references `build: .` for the app service but no `Dockerfile` exists in the project root. This would be needed for actual builds.
4. **ESLint gap:** The `lint` script references `eslint src/` but eslint is not listed in devDependencies and no `.eslintrc` config file exists.
5. **Stub CI/CD:** Both GitHub Actions workflow files are one-liners containing only the workflow name. No actual jobs or steps are defined.

---

## Output

Written to: `/tmp/rawgentic-test-fixtures/projects/webapp/.rawgentic-baseline.json`
