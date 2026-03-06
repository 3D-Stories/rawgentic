# WF10 Performance Optimization -- Transcript

## Task

Optimize performance. Workspace: `/tmp/rawgentic-optimize-perf-test/no-profiling-config/.rawgentic_workspace.json`, project: `/tmp/rawgentic-optimize-perf-test/no-profiling-config/projects/minimal-app`. The app is slow.

---

## Config-Loading Phase

### Step 1: Read `.rawgentic_workspace.json`

- **Location:** `/tmp/rawgentic-optimize-perf-test/no-profiling-config/.rawgentic_workspace.json`
- **Status:** Found, valid JSON, version 1.
- **Active project:** `minimal-app` at `/tmp/rawgentic-optimize-perf-test/no-profiling-config/projects/minimal-app` (active == true).

### Step 2: Read `<activeProject.path>/.rawgentic.json`

- **Location:** `/tmp/rawgentic-optimize-perf-test/no-profiling-config/projects/minimal-app/.rawgentic.json`
- **Status:** Found, valid JSON.
- **config.version:** 1 (OK, no version mismatch warning needed).
- **Full config parsed:**

```json
{
  "version": 1,
  "project": { "name": "minimal-app", "type": "application", "description": "App with incomplete techStack" },
  "repo": { "provider": "github", "fullName": "eval-org/sentinel-repo-42", "defaultBranch": "trunk" },
  "techStack": []
}
```

### Step 3: Build `capabilities` Object

| Capability       | Value   | Reason                                               |
|------------------|---------|------------------------------------------------------|
| `has_tests`      | false   | `config.testing` does not exist                      |
| `test_commands`  | N/A     | No testing section                                   |
| `has_ci`         | false   | `config.ci` does not exist                           |
| `has_deploy`     | false   | `config.deploy` does not exist                       |
| `has_database`   | false   | `config.database` does not exist                     |
| `has_docker`     | false   | `config.infrastructure` does not exist               |
| `project_type`   | "application" | From `config.project.type`                     |
| `repo`           | "eval-org/sentinel-repo-42" | From `config.repo.fullName`          |
| `default_branch` | "trunk" | From `config.repo.defaultBranch`                     |

### Config-Loading Result

Config loaded successfully. Capabilities are severely limited -- most are false/unavailable due to minimal config.

---

## WF10 Step 1: Receive Performance Scope

### 1.1 Load Project Configuration

- **Resolved values:**
  - `capabilities.repo` = `eval-org/sentinel-repo-42`
  - `PROJECT_ROOT` = `/tmp/rawgentic-optimize-perf-test/no-profiling-config/projects/minimal-app`

### 1.2 Parse Scope

- **User input:** "The app is slow."
- **Affected subsystem:** Unknown -- scope is too vague.

### 1.3 Critical Blocker: Empty `techStack[]`

**STOPPED.** The workflow cannot proceed with profiling or optimization.

Per the skill's `<environment-setup>` directive:

> "If any required config field is missing or null, STOP and ask the user. Do not assume values."

Per `PROFILING_TOOLS` constant:

> "Select profiling tools based on config.techStack and config.services"

Per `OPTIMIZATION_CATEGORIES` constant:

> "compute: CPU-bound operations -> select profiler based on config.techStack"
> "query: Database queries -> use database-appropriate query analysis from config"

The `config.techStack` array is **empty** (`[]`). This means:

1. **Cannot determine the programming language** -- no way to select appropriate profiling tools (cProfile for Python, --prof for Node.js, pprof for Go, JMH for Java, etc.)
2. **Cannot determine the framework** -- no way to identify framework-specific performance patterns or optimization strategies
3. **Cannot determine appropriate profiling tools** -- the entire Step 3 (Profile and Establish Baseline) is blocked
4. **No `config.services` section** -- cannot identify service types for frontend/backend-specific profiling
5. **No `config.database` section** -- cannot determine if database queries are a factor
6. **No `config.infrastructure` section** -- cannot assess Docker resource limits or VM specs
7. **No `config.testing` section** -- cannot run existing tests or create benchmark tests using established patterns

### 1.4 Scope Clarification Needed

Per the skill's failure modes for Step 1:

> "Scope too vague ('it's slow') -> ask for specific metrics, endpoints, or operations"
> "No measurable target defined -> require specific numbers before proceeding"

The user said "The app is slow" without providing:
- Which specific operations are slow
- Current measured latency/performance numbers
- Target performance goals (specific numbers, not "faster")
- Whether this is user-facing or internal

### 1.5 Questions for the User

**The workflow is STOPPED and requires user input before proceeding.** The following information is needed:

#### Required: Tech Stack Configuration

The `techStack` array in `.rawgentic.json` is empty. Without knowing the language, framework, and available tools, WF10 cannot select profiling tools, design benchmarks, or plan optimizations. Please provide:

1. **What programming language(s) does this application use?** (e.g., TypeScript, Python, Go, Java, Rust)
2. **What framework(s) is it built on?** (e.g., Express, Next.js, FastAPI, Spring Boot)
3. **Are there any profiling tools already available in the project?** (e.g., built-in profilers, APM tools, logging with timing)

To fix this, run `/rawgentic:setup` to populate the tech stack, or manually update the `techStack` array in `/tmp/rawgentic-optimize-perf-test/no-profiling-config/projects/minimal-app/.rawgentic.json`.

#### Required: Performance Scope Clarification

The description "the app is slow" is too vague to begin measurement-driven optimization. Please provide:

1. **What specific operation is slow?** (e.g., "the /api/users endpoint", "startup time", "database queries on the orders page")
2. **What is the current measured performance?** (e.g., "API response takes ~3 seconds", "page load is 8 seconds")
3. **What is the acceptable target?** (specific numbers, e.g., "p95 < 200ms", "page load under 2 seconds")
4. **Is this user-facing or internal?**

---

## Workflow Status

**WF10 is STOPPED at Step 1.** Cannot proceed to Step 2 (Analyze Performance Context) or Step 3 (Profile and Establish Baseline) without:

1. A populated `techStack[]` in the project config (needed for profiler selection, benchmark design, and optimization strategy)
2. Specific, measurable performance scope (needed for baseline measurement and success criteria)

**No assumptions were made.** No profiling tools were guessed. No optimizations were attempted.

### WF10 Step 1: Receive Performance Scope -- BLOCKED (empty techStack[], vague scope -- awaiting user clarification)
