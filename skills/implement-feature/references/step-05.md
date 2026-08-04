## Step 5: Create Implementation Plan

### Instructions

**Small-standard lane variant (`<small-standard-lane>`).** In the lane, produce a
**checklist plan** instead of the full decomposition: an ordered list of tasks where each carries a
`- riskLevel: high|standard` line and a one-line verification. `parallel_group`/`files` are
OPTIONAL in the lane. **riskLevel tagging is RETAINED** — the fail-closed `plan_lib.parse_tasks`
contract and the Step 3a risk stratification still apply, because Step 8a fires on any
`riskLevel: high` task. The branch-naming (item 1) and commit-message (item 8) items still apply;
the full task decomposition, drift-ready fields, and multi-PR machinery below are the FULL-spine
form (run them when not in the lane).

1. **Branch naming:**
   - Features: `feat/<issue-number>-<kebab-case-summary>` (`feat` — the conventional-commit type; `feature` is not one, #880)
   - Bug fixes: `fix/<issue-number>-<kebab-case-summary>`

2. **Task decomposition:** Break the design into ordered tasks, each appropriately sized (aim for 2-10 minutes each). Adapt the task style to the project:

   **If `capabilities.has_tests == true`:** Follow Red-Green-Refactor per task:
   - RED: Write failing test(s), confirm they fail
   - GREEN: Write minimum code to pass
   - REFACTOR: Clean up

   **If `capabilities.has_tests == false`:** Follow Implement-Verify per task:
   - IMPLEMENT: Write the code/config changes
   - VERIFY: Run a verification command (health check, syntax check, dry-run, or manual inspection)
   - Document what "verified" means for this task

3. **Task ordering:** Make dependencies explicit. Genuinely-independent tasks MAY share a `parallel_group` AND each declares the files it touches via a `- files: <comma-separated paths>` line, so disjointness is reviewable:
   - `- parallel_group: <group-id>` — tasks with the same id are candidates to run concurrently (via the optional Agent-tool worktree subagents of Step 8).
   - `- files: <comma-separated paths>` — the exact files the task creates/modifies (concrete paths only; globs and directories cannot be judged disjoint).

   A group is parallel-eligible ONLY when every member declares concrete `files` and the members' file sets are pairwise disjoint — judge this from the declarations; when in doubt the group runs sequentially (an un-provable group degrades to serial execution, never to a concurrent collision). The default remains serial, inline execution.

3a. **Risk stratification (P15):** Tag every task with a `riskLevel: high|standard` field. Use **`high`** if ANY of the 8 criteria apply; otherwise `standard`. The 8 criteria (canonical list lives in `hooks/plan_lib.py::RISK_CRITERIA`):

   1. **Security surface** — auth, secrets, sanitization, input validation, crypto, access control
   2. **Module boundary** — introduces or changes a service/module API that other code will import
   3. **Non-trivial error/exception flow** — state machines, retry, fallback branches, discriminated outcomes
   4. **Infra/persistence** — infrastructure, deployment, migrations, schema
   5. **Security middleware** — rate limiting, circuit breakers, request validation
   6. **Deserialization of external data** — JSON/YAML/TOML/binary formats from untrusted sources
   7. **Subprocess construction** — shells out to external commands with dynamic args
   8. **Regex on untrusted input** — ReDoS risk, lookahead in user-controlled input

   **Plan format contract** (enforced by `plan_lib.parse_tasks`):
   - Each task begins with `### Task <id>: <title>` heading; the id matches `[A-Za-z0-9][A-Za-z0-9._-]*` (`T1`, `1a`, `2.3` — shell-safe, #880). An unparseable `### Task ` heading fails closed (parse error naming the line → STOP), never silently skipped.
   - Each task body MUST contain a line `- riskLevel: high|standard`; high-risk tasks include a parenthesized reason: `- riskLevel: high (security surface)` — the reason stays on ONE line (a wrapped reason is forbidden and fails closed, #880).
   - Tasks lacking a `riskLevel` line **fail closed** (parse error → STOP); so does a malformed riskLevel attempt (`- riskLevel high`, an off-vocab value).
   - OPTIONAL: `- parallel_group: <id>` and `- files: <comma-separated paths>` (see Task ordering above). These are purely additive — absent fields just mean the task is not parallel-eligible; they never affect the `riskLevel` fail-closed contract or the pre-P15 migration.

   **Calibration sanity (judgment, not a helper call):** the 15–30% high-risk ratio is the
   documented calibration target. Materially above it signals the criteria are being
   over-applied (dilution returns) — reconsider the tags before proceeding; 0% high-risk on
   a complex feature is implausible — confirm it deliberately. (`plan_lib.parse_tasks(md)`
   returns `list[Task]` **objects** — access attributes `t.id`, `t.title`, `t.risk_level`,
   `t.reason`, `t.parallel_group`, `t.files`, `t.deferral_reason`, NOT dict
   `t.get("risk_level")`.)

   **High-risk path allowlist:** A task touching any file whose path matches the regex allowlist in `plan_lib.DEFAULT_HIGH_RISK_PATH_PATTERNS` (auth, secret, .env, migration, crypto, jwt, session, oauth, csrf, token, credential, passport, middleware, lib/server/auth, security-, hooks/security) is auto-tagged `high` regardless of the agent's manual classification.

4. **Verification strategy per task:** Specify how each task is verified:
   - Test file + test cases (if test framework exists)
   - Shell command that confirms correct behavior
   - Manual inspection criteria
   - Health check URL
   - **Deferred-to-target (#138):** when the dev environment *fundamentally cannot* exercise the artifact (e.g. an NSIS uninstaller with no `makensis`; native Win32 code built from WSL for a Windows target; an OS-native tray menu that cannot render headless), declare a `- verification: deferred-to-target (<reason>)` line on that task. This is NOT a skip: the task still requires its **best local proxy** (compile, typecheck, unit tests of any extractable logic); deferral covers only the *unexercisable remainder*, which will be checked on the target box. `plan_lib.parse_tasks` records the reason (a deferral line with no `(reason)` fails closed). A proxy you can run is never the path you can't — so the gap is named here, carried through Step 9 (listed, never counted as verified), Step 12 (the `## Deferred verification` PR section), Step 16 (the run-record `verification_deferred` list), and enforced by `<completion-gate>`.

5. **Migrations / config changes (if applicable):** Specify files, content, and rollback approach. Use `capabilities.migration_dir` if it exists, otherwise specify where migration files should live.

6. **Documentation tasks:** Identify docs that need updating (CLAUDE.md, README, Confluence pages, inline comments).

7. **Multi-PR decomposition (if applicable):** If design exceeds 500 lines, decompose by logical phase. Each sub-PR follows Steps 8-14 independently.

8. **Commit messages:** Pre-specify conventional commit messages for each task.

### Output
Implementation plan with ordered tasks, verification strategy, branch name, optional multi-PR decomposition.

### Failure Modes
- Too many tasks (>30) -> suggest scope narrowing or multi-PR
- Circular dependencies -> re-order to break cycles
- Plan references nonexistent files -> verify against Step 2 analysis

---

