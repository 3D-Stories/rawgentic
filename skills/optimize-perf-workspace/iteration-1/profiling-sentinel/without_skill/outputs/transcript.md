# Transcript: Optimize API Performance (Without Skill)

## Task
Optimize API performance for the sentinel-app project. Bottleneck identified as DB queries.

## Workspace & Project Context

**Workspace file:** `/tmp/rawgentic-optimize-perf-test/profiling-sentinel/.rawgentic_workspace.json`
**Project path:** `/tmp/rawgentic-optimize-perf-test/profiling-sentinel/projects/sentinel-app`
**Project config:** `/tmp/rawgentic-optimize-perf-test/profiling-sentinel/projects/sentinel-app/.rawgentic.json`

### Configuration Summary
- **Project name:** sentinel-app
- **Type:** application
- **Tech stack:** Node.js 20.11.0, TypeScript 5.4.0
- **Testing:** Vitest (`npx vitest run --sentinel`, config: `vitest.config.ts`)
- **Database:** CockroachDB, migrations via dbmate in `db/migrations`
- **Profiling:** clinic-js
  - Doctor: `npx clinic doctor -- node dist/server.js`
  - Flame graph: `npx clinic flame -- node dist/server.js`
- **Repo:** eval-org/sentinel-repo-42, default branch: trunk

## Process Followed (No Skill Guidance)

### Step 1: Read workspace configuration
Read `.rawgentic_workspace.json` to identify the active project (sentinel-app) and its path.

### Step 2: Read project configuration
Read `.rawgentic.json` to understand the tech stack, database, testing, and profiling setup.

### Step 3: Explore project files
Searched the entire project directory for source files. Result: The project directory contains **only** the `.rawgentic.json` configuration file. No source code, no tests, no migration files, no `package.json`, no `tsconfig.json`, and no `vitest.config.ts` exist.

### Step 4: Assessment given missing source code
With no source code present, actual profiling, query analysis, or code optimization cannot be performed. Below is the approach I would take if the codebase were present, based on the configuration:

#### 4a. Profiling (what I would do)
1. Build the project (`npm run build` or `npx tsc`) to produce `dist/server.js`.
2. Run `npx clinic doctor -- node dist/server.js` to get an overall health check identifying event loop delays, I/O bottlenecks, and CPU usage patterns.
3. Run `npx clinic flame -- node dist/server.js` to produce a flame graph isolating hot code paths, especially those involving DB query execution.
4. Examine the flame graph for time spent in CockroachDB driver calls (e.g., `pg` pool acquire, query execution, result deserialization).

#### 4b. DB Query Optimization Strategies (CockroachDB-specific)
1. **Identify N+1 queries:** Search source code for query calls inside loops. Replace with batch queries or JOINs.
2. **Missing indexes:** Run `EXPLAIN ANALYZE` on slow queries to check for full table scans. Add appropriate indexes.
3. **Connection pooling:** Verify the app uses a connection pool (e.g., `pg.Pool`) with appropriate `max`, `min`, `idleTimeoutMillis` settings. CockroachDB benefits from connection pooling due to distributed query planning overhead.
4. **Query batching:** Where multiple independent queries run sequentially, use `Promise.all()` to parallelize them.
5. **Prepared statements:** Use parameterized queries to allow CockroachDB to cache query plans.
6. **Pagination:** Replace unbounded `SELECT *` queries with `LIMIT`/`OFFSET` or cursor-based pagination.
7. **SELECT only needed columns:** Replace `SELECT *` with explicit column lists to reduce data transfer.
8. **Caching layer:** For read-heavy, rarely-changing data, add an in-memory cache (e.g., node-cache, Redis) to avoid repeated DB round-trips.
9. **CockroachDB-specific:** Consider `AS OF SYSTEM TIME` for follower reads on stale-tolerant queries to reduce latency. Use `UPSERT` instead of `INSERT ... ON CONFLICT` where semantics allow.

#### 4c. Testing & Validation
1. Run existing tests: `npx vitest run --sentinel`
2. Re-run profiling after changes to measure improvement.
3. Compare clinic-js output before and after.

#### 4d. Migration Scripts
If schema changes (new indexes) are needed, create migration files in `db/migrations` using dbmate conventions.

### Step 5: Blockers
- **No source code exists** in the project directory. Cannot perform actual profiling, code analysis, or optimization.
- The project appears to be a test fixture with only configuration metadata.

## Decisions Made
| # | Decision | Reasoning |
|---|----------|-----------|
| 1 | Read workspace and project configs first | Need to understand stack, tooling, and DB type before optimizing |
| 2 | Search for all project files | Must find actual source code to analyze DB queries |
| 3 | Document intended approach despite missing code | The task asks to execute the optimization; documenting the full plan demonstrates the process |

## Key Observations
1. The `.rawgentic.json` provides rich context: CockroachDB as the database, clinic-js for profiling, Vitest for testing, and dbmate for migrations. This is sufficient to build a targeted optimization plan.
2. Without a skill, I approached the problem from first principles: read config, explore code, profile, identify bottlenecks, optimize, test, validate. There was no structured checklist or prescribed sequence of steps.
3. The absence of source code meant the task could not be completed end-to-end. In a real scenario, I would have proceeded through profiling, query analysis, and code changes.

## Tools & Commands Referenced
- `npx clinic doctor -- node dist/server.js` (profiling)
- `npx clinic flame -- node dist/server.js` (flame graph)
- `npx vitest run --sentinel` (testing)
- `dbmate` (database migrations in `db/migrations`)
- `EXPLAIN ANALYZE` (CockroachDB query plan analysis)

## Outcome
**Status:** Incomplete due to missing source code. Full optimization plan documented above.
