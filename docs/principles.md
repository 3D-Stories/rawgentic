# Phase 2: First Principles -- SDLC Workflow Framework

**Date:** 2026-03-01
**Author:** Principles Agent
**Inputs:** phase1-discovery.md, phase1-tooling-catalog.md
**Purpose:** Define, validate, and rank first principles governing how a developer and Claude Code CLI collaborate through the entire SDLC.

---

## Critique Strategy Reference

<!-- NOTE: This matrix is intentionally duplicated here (also in orchestrator-prompt.md) because sub-agents
     run in isolated context windows and cannot see the orchestrator prompt. Downstream agents read
     phase2-principles.md and need the strategy matrix in that file. -->

| Gate Type           | Command               | When to Use                                       | Cost                                                       |
| ------------------- | --------------------- | ------------------------------------------------- | ---------------------------------------------------------- |
| Full critique       | `/reflexion:critique` | Ideation gates, pre-execution gates               | High (full analysis -- 3 judge sub-agents + debate rounds) |
| Lightweight reflect | `/reflexion:reflect`  | Translation validation, post-execution gates      | Low (drift check -- single pass with complexity triage)    |
| Memorize            | `/reflexion:memorize` | After any critique/reflect with reusable insights | Minimal (curates insights into CLAUDE.md via ACE)          |

**Gate Placement in Workflows:**

- **WF1 (Issue Creation):** brainstorm --> `/reflexion:critique` --> user selection --> design --> `/reflexion:reflect` --> finalize
- **WF2 (Feature Implementation):** design --> `/reflexion:critique` --> plan --> `/reflexion:reflect` --> TDD --> `/reflexion:reflect` --> PR --> deploy

---

## Principle 1: Branch Isolation

**Statement:** Every workflow gets its own feature branch; parallel workflows use git worktrees for full filesystem isolation.

**Industry basis:** Trunk-based development with short-lived feature branches. Git worktrees are the emerging standard for AI-agent parallel development, preventing cross-contamination between concurrent tasks. Atlassian and Google both advocate for short-lived feature branches that merge within 1-2 days.

**Discovery validation:** Aligns with the existing pattern. Phase 1 discovery confirms trunk-based strategy with `feature/` and `fix/` branch naming conventions. A `block-main-push.sh` PreToolUse hook already prevents direct pushes to main. No conflicts detected. However, no worktree usage was observed -- this is a net-new capability.

**Enforcement:** Hook (PreToolUse) + Plugin command

**Tooling:**

- `git:create-worktree` / `git:compare-worktrees` / `git:merge-worktree` -- NeoLabHQ/context-engineering-kit (git plugin v1.2.0) for worktree lifecycle management
- `git:worktrees` skill -- always-loaded reference for worktree operations
- Existing `block-main-push.sh` hook -- already enforces the "no direct main push" rule

**Source:** NeoLabHQ/context-engineering-kit

**Trade-offs:**

- Worktrees share the same `.git` directory -- concurrent rebases on different worktrees can cause lock contention (`index.lock` errors).
- Each worktree requires its own `node_modules` install and Docker container setup, which adds disk and time cost for this project (dashboard + engine).
- Worktree cleanup is manual -- stale worktrees accumulate if not pruned after merge.
- For a solo developer + Claude Code workflow, worktrees are only necessary when running multiple Claude Code sessions in parallel on different features. For sequential work, simple branching suffices.

**Priority:** 1

**Dependencies:** None (foundational)

---

## Principle 2: Code Formatting

**Statement:** All changed files pass through automated formatting (Prettier for JS/JSX, Ruff for Python) before every commit.

**Industry basis:** Shift-left code quality. Google, Meta, and Stripe all enforce automated formatting as a pre-commit gate. Eliminates style debates, reduces diff noise, and prevents formatting-only PRs. The "format on save" pattern is industry standard.

**Discovery validation:** Phase 1 discovery explicitly flags this as a gap: "Ruff is configured for Python but not enforced. No JS linter (ESLint/Prettier) exists at all." Code style rules are documented in CLAUDE.md (no semicolons, single quotes, 2-space indent for JS) but not machine-enforced. This principle directly addresses a documented inconsistency.

**Enforcement:** PostToolUse hook (after file write) + PreToolUse hook (before commit)

**Tooling:**

- `ddd:setup-code-formatting` -- NeoLabHQ/context-engineering-kit (ddd plugin v1.0.0) for initial setup of formatting rules in CLAUDE.md
- Custom PostToolUse hook: after any `Write` or `Edit` tool call, run `prettier --write` on changed JS/JSX files and `ruff format` on changed Python files
- Alternatively: a PreToolUse hook on `git commit` that checks formatting and blocks if files are not formatted

**Source:** NeoLabHQ/context-engineering-kit (setup), custom hook (enforcement)

**Implementation notes:**

- Prettier needs to be installed as a devDependency in `dashboard/package.json` (currently absent)
- A `.prettierrc` config file must be created matching the existing CLAUDE.md style rules: `{ "semi": false, "singleQuote": true, "tabWidth": 2 }`
- Ruff is already configured in `engine/pyproject.toml` (line-length=100, target=py312) but needs `ruff format` enforcement
- The PostToolUse hook should format on file write (so Claude sees formatted output immediately) rather than only at commit time

**Trade-offs:**

- PostToolUse hooks run after every tool call -- formatting after every file write adds latency. The 60-second default timeout is more than sufficient, but accumulated time across many writes could be noticeable.
- Formatting conflicts: if Claude writes code in one style and the formatter rewrites it, Claude may "fight" the formatter in subsequent edits. The hook must output the formatted result back to Claude's context.
- Ruff format and Prettier occasionally disagree with each other on edge cases (string quotes in Python f-strings, etc.) -- keep formatters scoped to their respective languages only.

**Priority:** 2

**Dependencies:** None (can be implemented independently, but benefits from Principle 1 for testing in isolation)

---

## Principle 3: Frequent Local Commits

**Statement:** Commit to the feature branch at minimum every 5 minutes of active work, providing a continuous rollback safety net.

**Industry basis:** Continuous integration fundamentals. Martin Fowler's CI principles advocate "commit frequently" (at least daily for teams; for AI-agent workflows, much more frequently). GitButler's Claude Code integration auto-commits after every tool action. The 5-minute interval balances rollback granularity against commit noise.

**Discovery validation:** No existing commit frequency enforcement exists. The current pattern relies on Claude Code making commits at natural breakpoints (feature complete, test passing). Phase 1 shows conventional commit messages are consistently used, which must be preserved even with frequent commits.

**Enforcement:** Custom background timer + PostToolUse hook hybrid

**Tooling:** Custom build required (no existing plugin provides time-based commit automation)

**Source:** N/A -- custom implementation

### Special Research: Time-Based Commit Automation in Claude Code CLI

**Finding:** Claude Code CLI has NO native timer or cron-like capability. Hooks are event-driven (PreToolUse, PostToolUse, Stop, Notification, SessionStart) -- none fire on a time interval. The `idle_prompt` Notification fires after 60 seconds of inactivity, but this is the opposite of what we need (we want commits during active work, not idle periods).

**Proposed Implementation: Background Watchdog + PostToolUse Commit Check**

The design uses two components working together:

#### Component A: SessionStart Hook -- Launch Background Watchdog

When a Claude Code session starts, a SessionStart hook spawns a background process that creates a "commit needed" marker file every 5 minutes.

```bash
#!/bin/bash
# .claude/hooks/start-commit-timer.sh
# SessionStart hook: spawns background commit watchdog

INPUT=$(cat)
CWD=$(echo "$INPUT" | jq -r '.cwd // empty')
MARKER_DIR="/tmp/claude-commit-timer"
MARKER_FILE="$MARKER_DIR/commit-needed"
PID_FILE="$MARKER_DIR/watchdog.pid"

# Create marker directory
mkdir -p "$MARKER_DIR"

# Kill any existing watchdog from a prior session
if [ -f "$PID_FILE" ]; then
  OLD_PID=$(cat "$PID_FILE")
  kill "$OLD_PID" 2>/dev/null
  rm -f "$PID_FILE"
fi

# Spawn background watchdog (runs independently of Claude Code process)
(
  while true; do
    sleep 300  # 5 minutes
    # Only create marker if there are uncommitted changes on a feature branch
    BRANCH=$(cd "$CWD" && git branch --show-current 2>/dev/null)
    if [ "$BRANCH" != "main" ] && [ "$BRANCH" != "master" ]; then
      DIRTY=$(cd "$CWD" && git diff --name-only 2>/dev/null)
      STAGED=$(cd "$CWD" && git diff --cached --name-only 2>/dev/null)
      if [ -n "$DIRTY" ] || [ -n "$STAGED" ]; then
        touch "$MARKER_FILE"
      fi
    fi
  done
) &
echo $! > "$PID_FILE"

# Inject context for Claude
echo "Commit timer started. Auto-commit reminders will appear every 5 minutes when uncommitted changes exist on a feature branch."
exit 0
```

#### Component B: PostToolUse Hook -- Check Marker and Prompt Commit

After every tool use, check if the marker file exists. If it does, inject an advisory message into Claude's context suggesting a commit.

```bash
#!/bin/bash
# .claude/hooks/check-commit-timer.sh
# PostToolUse hook: checks if 5-minute commit timer has fired

MARKER_FILE="/tmp/claude-commit-timer/commit-needed"

# Only fire if marker exists
if [ ! -f "$MARKER_FILE" ]; then
  exit 0
fi

# Remove marker (reset timer)
rm -f "$MARKER_FILE"

# Get current branch and change summary
INPUT=$(cat)
CWD=$(echo "$INPUT" | jq -r '.cwd // empty')
BRANCH=$(cd "$CWD" && git branch --show-current 2>/dev/null)
CHANGES=$(cd "$CWD" && git diff --stat 2>/dev/null | tail -1)

echo "{
  \"hookSpecificOutput\": {
    \"hookEventName\": \"PostToolUse\",
    \"additionalContext\": \"COMMIT REMINDER: 5+ minutes since last commit on branch '$BRANCH'. Changes: $CHANGES. Please commit current progress with a descriptive conventional commit message before continuing. This is a rollback safety net -- commit even if work is incomplete (use 'wip:' prefix for work-in-progress).\"
  }
}"

exit 0
```

#### Component C: Stop Hook Addition -- Kill Watchdog on Session End

```bash
#!/bin/bash
# Addition to existing stop hook or separate hook
# Cleanup: kill the background watchdog when session ends
PID_FILE="/tmp/claude-commit-timer/watchdog.pid"
if [ -f "$PID_FILE" ]; then
  kill "$(cat "$PID_FILE")" 2>/dev/null
  rm -f "$PID_FILE" "/tmp/claude-commit-timer/commit-needed"
fi
```

#### Settings Configuration

```json
{
  "hooks": {
    "SessionStart": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "bash /home/rocky00717/millions/.claude/hooks/start-commit-timer.sh"
          }
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "bash /home/rocky00717/millions/.claude/hooks/check-commit-timer.sh"
          }
        ]
      }
    ]
  }
}
```

#### Design Trade-offs

- **Advisory, not blocking:** The hook injects a reminder into Claude's context but does not block tool execution. Claude may choose to defer the commit if it is mid-operation. This is intentional -- a blocking commit mid-file-edit would corrupt state.
- **Background process lifecycle:** The watchdog process runs independently of Claude Code. If Claude Code crashes, the watchdog persists until the PID file is cleaned up. The SessionStart hook kills prior watchdogs to prevent accumulation.
- **WIP commits:** Frequent commits will include incomplete work. The `wip:` prefix convention distinguishes these from meaningful commits. Before PR creation, an interactive rebase (`git rebase -i`) should squash WIP commits.
- **Marker file race condition:** If Claude checks the marker between the `touch` and the next tool use, the reminder fires immediately. This is acceptable -- early is better than late for rollback safety.
- **PostToolUse matcher:** Using `""` (empty matcher) means this fires on ALL tool uses, not just Bash. This is correct -- we want the check after any action, including file writes.

**Alternative approaches considered:**

1. **Pure PostToolUse with timestamp file:** Check `last-commit-time` file on every PostToolUse, compare to current time. Simpler (no background process) but less precise -- commit reminder only fires when Claude uses a tool, which could be long after the 5-minute mark during extended thinking.
2. **Notification hook on idle_prompt:** Fire a commit reminder when Claude goes idle. Wrong trigger -- we want commits during active work, not when idle.
3. **Agent-level prompt instruction:** Simply tell Claude in CLAUDE.md to commit every 5 minutes. Unreliable -- Claude does not track wall-clock time and has no internal timer.

**Recommended approach:** Component A+B+C (background watchdog + PostToolUse check). The background process ensures the 5-minute interval is real wall-clock time, and the PostToolUse check ensures the reminder reaches Claude at its next available moment.

**Simpler alternative (recommended for initial implementation):** Pure PostToolUse with timestamp file. While less precise, it avoids the complexity of background process management and still achieves the goal of frequent commits during active work. The timestamp approach only checks on tool use, but during active Claude Code sessions, tool use is frequent enough that the effective interval will be close to 5 minutes.

```bash
#!/bin/bash
# .claude/hooks/check-commit-interval.sh
# PostToolUse hook: simpler timestamp-based commit reminder (no background process)

INPUT=$(cat)
CWD=$(echo "$INPUT" | jq -r '.cwd // empty')
TIMESTAMP_FILE="/tmp/claude-last-commit-time"
INTERVAL=300  # 5 minutes in seconds

# Initialize timestamp file if missing
if [ ! -f "$TIMESTAMP_FILE" ]; then
  date +%s > "$TIMESTAMP_FILE"
  exit 0
fi

LAST_COMMIT=$(cat "$TIMESTAMP_FILE")
NOW=$(date +%s)
ELAPSED=$((NOW - LAST_COMMIT))

if [ $ELAPSED -lt $INTERVAL ]; then
  exit 0
fi

# Check if on feature branch with uncommitted changes
BRANCH=$(cd "$CWD" && git branch --show-current 2>/dev/null)
if [ "$BRANCH" = "main" ] || [ "$BRANCH" = "master" ]; then
  exit 0
fi

DIRTY=$(cd "$CWD" && git diff --name-only 2>/dev/null)
STAGED=$(cd "$CWD" && git diff --cached --name-only 2>/dev/null)
if [ -z "$DIRTY" ] && [ -z "$STAGED" ]; then
  # No uncommitted changes -- reset timer
  date +%s > "$TIMESTAMP_FILE"
  exit 0
fi

CHANGES=$(cd "$CWD" && git diff --stat 2>/dev/null | tail -1)

# Reset timer
date +%s > "$TIMESTAMP_FILE"

echo "{
  \"hookSpecificOutput\": {
    \"hookEventName\": \"PostToolUse\",
    \"additionalContext\": \"COMMIT REMINDER: ${ELAPSED}s since last commit check on branch '$BRANCH'. Changes: $CHANGES. Please commit current progress with a conventional commit message before continuing. Use 'wip(scope): checkpoint' for work-in-progress.\"
  }
}"

exit 0
```

**Priority:** 5

**Dependencies:** Principle 1 (must be on a feature branch for commits to be meaningful)

---

## Principle 4: Regular Remote Sync

**Statement:** Push to origin at minimum every 30 minutes after feature branch creation, ensuring remote backup and visibility.

**Industry basis:** Continuous integration. The "integrate early, integrate often" principle. Remote pushes serve as off-machine backup (critical for a solo developer), enable CI to run on the latest code, and provide visibility into work-in-progress. Google's trunk-based development recommends pushing at least daily; 30 minutes is appropriate for an AI-assisted rapid development workflow.

**Discovery validation:** The existing PostToolUse `deploy-on-push.sh` hook already triggers deployment after every push, which validates that pushes are meaningful events in this workflow. No push frequency enforcement exists. Phase 1 shows the deploy hook has a 300s timeout, indicating pushes are expected to trigger substantial operations.

**Enforcement:** PostToolUse hook (same timestamp-file pattern as Principle 3, with 1800-second interval and checking time since last push)

**Tooling:**

- Custom PostToolUse hook (same architecture as Principle 3's simpler timestamp approach, checking last push time)
- `git:commit` from NeoLabHQ/context-engineering-kit for well-formatted commit messages before push

**Source:** Custom build + NeoLabHQ/context-engineering-kit

**Trade-offs:**

- Every push triggers `deploy-on-push.sh` (300s timeout), which deploys to chorestory-dev and runs E2E tests. Pushing every 30 minutes means up to 16 deploy+E2E cycles per 8-hour session. This is expensive but acceptable if E2E tests are fast (approximately 2 minutes currently).
- Pushing WIP code to origin means incomplete features are visible on the remote. Feature branches mitigate this -- the code is not on main.
- If the push triggers CI on GitHub, frequent pushes consume GitHub Actions minutes. The current CI runs on push to main and PRs targeting main only, so feature branch pushes do NOT trigger CI. No conflict.
- Network outage: if push fails, the reminder will fire again on the next cycle. The hook should handle push failures gracefully (log and retry next cycle, not block).

**Priority:** 7

**Dependencies:** Principle 1 (feature branch), Principle 3 (local commits must exist before push)

---

## Principle 5: TDD Enforcement

**Statement:** Unit, integration, and E2E tests must be updated and passing before any PR is created.

**Industry basis:** Test-Driven Development (TDD), the "Iron Law" -- no production code without a failing test first. Kent Beck's Red-Green-Refactor cycle. Shift-left testing: catch defects at the earliest possible stage. The "test pyramid" (many unit tests, fewer integration tests, fewest E2E tests) guides test distribution.

**Discovery validation:** Phase 1 confirms a substantial test suite exists: 569 Python tests, 156 Node.js tests, 89 E2E tests. CI runs unit/integration tests on PR creation. E2E tests run via deploy script but are NOT in CI. The gap is enforcement -- nothing prevents PR creation with failing tests. The `check-e2e-coverage.sh` PostToolUse hook warns about missing E2E specs but does not block commits or PRs.

**Enforcement:** PreToolUse hook (block `gh pr create` if tests fail) + sub-agent verification

**Tooling:**

- `tdd:test-driven-development` skill -- NeoLabHQ/context-engineering-kit (tdd plugin v1.1.0), always-loaded TDD methodology reference
- `tdd:write-tests` / `tdd:fix-tests` -- NeoLabHQ/context-engineering-kit for test generation and repair
- `tdd-workflows:tdd-cycle` -- wshobson/agents, complete 12-step TDD orchestrator
- `tdd-workflows:tdd-red` / `tdd-green` / `tdd-refactor` -- wshobson/agents, individual phase commands
- `unit-testing:test-generate` -- wshobson/agents, test generation for pytest and vitest
- Custom PreToolUse hook: intercept `gh pr create` commands, run `pytest` and `vitest` first, block if failures

**Source:** NeoLabHQ/context-engineering-kit + wshobson/agents + custom hook

**Implementation note for PreToolUse hook:**

```bash
#!/bin/bash
# .claude/hooks/enforce-tests-before-pr.sh
# PreToolUse hook: blocks PR creation if tests are failing

INPUT=$(cat)
COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // empty')

# Only trigger on gh pr create
if [[ ! "$COMMAND" =~ gh\ pr\ create ]]; then
  exit 0
fi

CWD=$(echo "$INPUT" | jq -r '.cwd')

# Run Python tests
echo "Running Python tests before PR creation..." >&2
PYTEST_RESULT=$(ssh root@10.0.17.204 \
  "cd /opt/millions && docker compose -f docker-compose.engine.dev.yml exec -T engine python -m pytest tests/ -v --tb=short" 2>&1)
PYTEST_EXIT=$?

# Run Node.js tests
echo "Running Node.js tests before PR creation..." >&2
VITEST_RESULT=$(ssh root@10.0.17.203 \
  "cd /opt/millions && docker compose -f docker-compose.infra.dev.yml exec -T dashboard npx vitest run" 2>&1)
VITEST_EXIT=$?

if [ $PYTEST_EXIT -ne 0 ] || [ $VITEST_EXIT -ne 0 ]; then
  REASON="Tests must pass before creating a PR."
  if [ $PYTEST_EXIT -ne 0 ]; then
    REASON="$REASON Python tests FAILED (exit $PYTEST_EXIT)."
  fi
  if [ $VITEST_EXIT -ne 0 ]; then
    REASON="$REASON Node.js tests FAILED (exit $VITEST_EXIT)."
  fi
  echo "{
    \"decision\": \"block\",
    \"reason\": \"$REASON\"
  }"
  exit 0
fi

exit 0
```

**Trade-offs:**

- Running full test suites before PR creation adds significant time (Python tests: approximately 30s, Node.js tests: approximately 15s, E2E: approximately 3 minutes). The hook should run unit+integration tests (fast) and defer E2E to the deploy step.
- SSH to remote VMs (darwin, chorestory-dev) to run tests adds network latency and introduces a point of failure -- if the VM is unreachable, the hook will fail and block PR creation. Need a fallback or timeout.
- The 60-second default hook timeout may be insufficient for full test suite execution. Must set a custom timeout (120-180s).
- Does not enforce TDD order (red-green-refactor) -- only enforces that tests pass at PR creation time. True TDD enforcement requires workflow-level discipline, not just a gate.

**Priority:** 3

**Dependencies:** Principle 1 (feature branch for PR targeting)

---

## Principle 6: Main-to-Dev Sync

**Statement:** After every PR merge to main, immediately pull main into the dev environment and verify the deployment.

**Industry basis:** Continuous Deployment (CD). The deploy-on-merge pattern ensures the development environment always reflects the latest main branch. This is a simplified version of GitOps where the desired state (main branch) is automatically reconciled with the actual state (dev servers).

**Discovery validation:** Phase 1 confirms the `deploy-on-push.sh` PostToolUse hook already triggers `deploy-dev.sh` after pushes, but this only fires within Claude Code sessions and only deploys to chorestory-dev (not darwin). The discovery doc flags: "deploy script only covers one of two VMs" and "engine deployment on darwin is fully manual." This principle formalizes and extends the existing pattern.

**Enforcement:** PostToolUse hook (extend existing deploy-on-push.sh) + GitHub Actions workflow (webhook on merge)

**Tooling:**

- Existing `deploy-on-push.sh` hook -- extend to also deploy engine on darwin
- `cicd-automation:workflow-automate` -- wshobson/agents for GitHub Actions CD pipeline setup
- `deployment-validation:config-validate` -- wshobson/agents for pre-deploy validation
- Custom: extend `scripts/deploy-dev.sh` to SSH to darwin and pull+rebuild engine

**Source:** wshobson/agents + custom extension of existing scripts

**Trade-offs:**

- Adding darwin deployment to the hook increases the timeout required (currently 300s for chorestory-dev only; darwin rebuild could add 120-180s more).
- Auto-deploying after merge assumes the merged code is safe -- but CI should have already validated it. The E2E step in deploy-dev.sh provides a post-deploy safety net.
- If darwin deployment fails, the dev environment is in a split state (new dashboard, old engine). The hook must handle partial failure gracefully -- deploy dashboard, then engine, and report the status of each independently.
- GitHub Actions-based CD would run outside Claude Code sessions, which is more reliable but requires additional infrastructure setup (self-hosted runner or SSH action).

**Priority:** 8

**Dependencies:** Principle 1 (feature branch merged to main), Principle 5 (tests pass before merge), Principle 7 (triple-gate ensures post-merge verification)

---

## Principle 7: Triple-Gate Testing

**Statement:** Run tests at three gates: locally before PR creation, in CI after PR creation, and on the dev server after merge-triggered deployment.

**Industry basis:** Progressive testing / defense in depth. Synopsys "Triple Shift Left" methodology. Google's "submit queue" runs tests pre-merge, post-merge, and in canary. The three gates catch different categories of failures: local catches logic errors, CI catches environment-specific failures, dev server catches integration and deployment failures.

**Discovery validation:** Phase 1 shows partial implementation:

- Gate 1 (local pre-PR): Not enforced -- tests exist but no gate prevents PR creation with failures.
- Gate 2 (CI post-PR): Exists via `.github/workflows/test.yml` -- runs Python and Node.js tests. But E2E and lint are missing from CI.
- Gate 3 (dev server post-merge): Partially exists via `deploy-dev.sh` which runs E2E after deploy. But only covers chorestory-dev, not darwin.

This principle formalizes and completes the existing partial pattern.

**Enforcement:** Hook (Gate 1) + CI workflow (Gate 2) + Deploy script (Gate 3)

**Tooling:**

- Gate 1: Custom PreToolUse hook on `gh pr create` (see Principle 5)
- Gate 2: Existing `.github/workflows/test.yml` -- extend with lint checks (ruff, prettier), E2E tests (requires Playwright in CI or on a self-hosted runner), and coverage reporting
- Gate 3: Existing `deploy-dev.sh` -- extend to cover darwin and add post-deploy health checks for both VMs
- `cicd-automation:workflow-automate` -- wshobson/agents for CI pipeline enhancement
- `code-review:review-pr` -- NeoLabHQ/context-engineering-kit for automated PR review in CI (6-agent review)

**Source:** wshobson/agents + NeoLabHQ/context-engineering-kit + custom CI and script extensions

**Trade-offs:**

- E2E tests in CI require either: (a) spinning up Docker containers in GitHub Actions (complex, slow), (b) a self-hosted runner on the LAN with access to the dev stack, or (c) running E2E on chorestory-dev triggered by CI via SSH. Option (c) is most pragmatic for this project.
- Three gates add latency to the merge pipeline. Local tests (approximately 45s) + CI (approximately 2 min) + E2E deploy (approximately 5 min) = approximately 8 minutes total. Acceptable for this project's pace.
- Gate 2 (CI) and Gate 3 (dev server) overlap in test coverage. This is intentional -- CI runs in an isolated environment (catches dependency issues), while dev server runs against real infrastructure (catches deployment issues).
- Adding lint/format checks to CI (Gate 2) may fail on existing code that was never formatted. Must do a one-time format pass on the entire codebase before enabling the CI gate.

**Priority:** 4

**Dependencies:** Principle 2 (formatting must be set up before lint CI gate), Principle 5 (local test gate), Principle 6 (dev server deployment)

---

## Principle 8: Shift-Left Critique

**Statement:** Apply full `/reflexion:critique` (3-judge debate) at ideation and pre-execution gates; lightweight `/reflexion:reflect` (single-pass drift check) at translation and post-execution gates.

**Industry basis:** Shift-left quality. Multi-perspective review catches architectural flaws before implementation begins. The "cost of defects" curve shows that bugs caught in design are 10-100x cheaper to fix than bugs caught in production. LLM-as-a-Judge patterns (from NeurIPS 2023) provide scalable review without human bottleneck.

**Discovery validation:** Phase 1 confirms active usage of reflexion plugin. Commit messages show: "docs: apply 15 critique amendments to Issue #132 E2E design", "docs: apply critique amendments to settings E2E design." The existing pattern is: design doc --> critique --> amendments --> plan --> implementation. This principle formalizes the pattern and extends it to post-execution reflection.

**Enforcement:** Workflow-level (manual invocation at defined gates) + Agent prompt instructions

**Tooling:**

- `reflexion:critique` -- NeoLabHQ/context-engineering-kit (reflexion plugin v1.1.4), 3-judge multi-agent debate with CoVe
- `reflexion:reflect` -- NeoLabHQ/context-engineering-kit (reflexion plugin v1.1.4), single-pass self-refinement with complexity triage
- `sadd:judge-with-debate` -- NeoLabHQ/context-engineering-kit (sadd plugin v1.2.0), alternative multi-judge pattern for high-stakes gates
- `sadd:do-and-judge` -- NeoLabHQ/context-engineering-kit (sadd plugin v1.2.0), execute+verify loop for implementation steps
- Workflow agent prompts should explicitly invoke critique/reflect at defined gates

**Source:** NeoLabHQ/context-engineering-kit

**Gate placement matrix:**

| Workflow Stage             | Gate Type           | Command               | Rationale                                                      |
| -------------------------- | ------------------- | --------------------- | -------------------------------------------------------------- |
| WF1: Post-brainstorm       | Full critique       | `/reflexion:critique` | Catch flawed ideas before they consume design effort           |
| WF1: Post-design, pre-plan | Full critique       | `/reflexion:critique` | Validate architecture before committing to implementation plan |
| WF2: Post-design           | Full critique       | `/reflexion:critique` | Same as WF1 design gate                                        |
| WF2: Post-plan, pre-TDD    | Lightweight reflect | `/reflexion:reflect`  | Check plan aligns with critiqued design (drift check)          |
| WF2: Post-TDD, pre-PR      | Lightweight reflect | `/reflexion:reflect`  | Check implementation matches plan (drift check)                |
| WF2: Post-deploy           | Lightweight reflect | `/reflexion:reflect`  | Verify deployment matches expectations (smoke check)           |

**Trade-offs:**

- Full critique is expensive -- 3 parallel sub-agents + debate rounds. Using it at every gate would consume excessive tokens and time. Reserving it for ideation and pre-execution gates is the right trade-off.
- Lightweight reflect may miss issues that a full critique would catch. The risk is acceptable at translation and post-execution gates because the architecture was already validated.
- Critique fatigue: if every gate produces 15+ findings, the developer may start ignoring them. The Must Do / Should Do / Could Do prioritization in reflexion:critique helps, but finding counts should be tracked over time.
- No automated enforcement -- this depends on workflow agents invoking the commands at the right times. A workflow orchestrator could automate this.

**Priority:** 6

**Dependencies:** Principle 11 (user-in-the-loop selection of critique findings)

---

## Principle 9: Continuous Memorization

**Statement:** Invoke `/reflexion:memorize` after every critique or reflect that surfaces reusable insights, curating them into CLAUDE.md for future sessions.

**Industry basis:** Organizational learning / knowledge management. The "lessons learned" practice from PMBOK, adapted for AI-agent workflows. Prevents the same mistakes from recurring across sessions. The ACE (Agentic Context Engineering) pattern ensures knowledge is structured and retrievable.

**Discovery validation:** Phase 1 confirms the reflexion:memorize command is available and enabled. CLAUDE.md and MEMORY.md are extensively maintained (357 lines and 200+ lines respectively). The existing pattern of documenting discoveries in session notes and MEMORY.md validates the principle. However, no evidence of systematic `/reflexion:memorize` usage was found in commit history -- memorization appears to be manual.

**Enforcement:** Workflow-level (manual invocation after critique/reflect) + Agent prompt instruction

**Tooling:**

- `reflexion:memorize` -- NeoLabHQ/context-engineering-kit (reflexion plugin v1.1.4), curates insights into CLAUDE.md using ACE
- mem0 MCP -- for cross-project knowledge that does not belong in CLAUDE.md
- Serena memories (`.serena/memories/`) -- for code-navigation-specific context

**Source:** NeoLabHQ/context-engineering-kit + mem0 MCP

**Trade-offs:**

- CLAUDE.md is already 357 lines and MEMORY.md hit its 200-line limit. Continuous memorization without pruning will cause context overflow. Need a periodic review cycle to archive stale knowledge.
- Memorization after every critique could add redundant entries -- the same pattern discovered in different contexts. The memorize command should deduplicate against existing CLAUDE.md content.
- The three-system knowledge architecture (CLAUDE.md, mem0, Serena) creates ambiguity about where to store insights. Rule: CLAUDE.md for project-scoped patterns, mem0 for cross-project knowledge, Serena for code-navigation context.
- Over-memorization risk: every minor finding stored as a principle dilutes the signal. Only findings that change behavior or prevent recurring errors should be memorized.

**Priority:** 9

**Dependencies:** Principle 8 (critique/reflect must run first to generate insights worth memorizing)

---

## Principle 10: Diagram-Driven Design

**Statement:** Every workflow and architecture change must be reflected in Excalidraw diagrams via MCP, reviewed collaboratively before implementation.

**Industry basis:** Architecture Decision Records (ADRs) + visual modeling. C4 architecture model advocates diagrams at multiple abstraction levels. The "diagram first" approach from domain-driven design ensures shared understanding before code. Simon Brown's "diagrams as code" movement emphasizes keeping diagrams in sync with implementation.

**Discovery validation:** Phase 1 confirms Excalidraw MCP is configured and operational (canvas sync at chorestory-dev:3100). However, "No `.excalidraw` files exist anywhere in the repository" and the `diagrams/` directory is empty. This is entirely aspirational -- no diagramming practice exists today. The existing design documentation (24 plan/design docs) is text-only.

**Enforcement:** Manual (workflow discipline) -- no automated enforcement mechanism exists for "diagram before implement"

**Tooling:**

- Excalidraw MCP -- already configured in `/root/.claude/mcp.json` with canvas sync
- `c4-architecture:c4-architecture` -- wshobson/agents, bottom-up C4 architecture documentation with Mermaid
- `mermaid-expert` agent -- wshobson/agents, diagram creation (flowcharts, sequence diagrams, ERDs)
- `documentation-generation:doc-generate` -- wshobson/agents, includes Mermaid diagram generation

**Source:** wshobson/agents (C4/Mermaid) + Excalidraw MCP (visual canvas)

**Trade-offs:**

- Excalidraw diagrams are binary blobs -- they do not diff well in git. Changes are not easily reviewable in PRs. Mermaid diagrams (text-based) are more git-friendly but less visually flexible.
- Maintaining diagram-code parity is a manual discipline that degrades over time. Without automated sync (which does not exist), diagrams become stale.
- For a solo developer workflow, the overhead of maintaining diagrams for every change may not justify the benefit. Consider limiting the requirement to: (a) new feature architecture, (b) cross-service interaction changes, and (c) database schema changes.
- The Excalidraw MCP canvas sync is pointed at chorestory-dev:3100 -- this requires the dev VM to be running for diagramming to work. If the VM is down, diagramming is blocked.

**Priority:** 11

**Dependencies:** None (independent), but benefits from Principle 8 (diagrams reviewed during critique)

---

## Principle 11: User-in-the-Loop Quality Gates

**Statement:** All critique/reflect outputs are presented as selectable lists -- the user chooses which findings to incorporate, never auto-applied.

**Industry basis:** Human-in-the-loop AI systems. The "appropriate reliance" principle from AI safety research. GitHub Copilot, Cursor, and other AI coding tools all present suggestions for human approval rather than auto-applying. NIST AI Risk Management Framework recommends human oversight at consequential decision points.

**Discovery validation:** Phase 1 shows existing design doc patterns where critique findings are applied: "docs: apply 15 critique amendments." The "15" suggests the user reviewed and selected findings (not all findings auto-applied). The CLAUDE.md verification checklist already warns: "verify at least 50% of inherited claims by reading actual source code." This principle is consistent with the existing trust-but-verify culture.

**Enforcement:** Workflow-level (built into critique/reflect output format) + Agent prompt instructions

**Tooling:**

- `reflexion:critique` -- already outputs findings in Must Do / Should Do / Could Do prioritization, which is a natural selection interface
- `reflexion:reflect` -- outputs improvement suggestions with confidence scores, enabling selection
- `fpf:propose-hypotheses` -- NeoLabHQ/context-engineering-kit (fpf plugin), provides competing hypotheses with trust scores for human decision
- Custom workflow agent prompt: "Present findings as a numbered list. Ask the user which items to incorporate. Do not auto-apply any finding."

**Source:** NeoLabHQ/context-engineering-kit (reflexion + fpf plugins)

**Trade-offs:**

- User selection adds a synchronous blocking step -- Claude must wait for user input before proceeding. This breaks the "fire and forget" pattern of automated workflows. For long-running sessions, consider allowing Claude to continue with "Must Do" items auto-applied while presenting "Should Do" and "Could Do" for selection.
- Selection fatigue: if every critique produces 10-20 findings across multiple categories, the user may rubber-stamp selections. The Must/Should/Could prioritization helps, but a "top 5" summary view would reduce cognitive load.
- This principle conflicts with fully autonomous workflows (e.g., running overnight). For unattended sessions, a fallback policy is needed: auto-apply "Must Do" items, log "Should Do" for review, skip "Could Do."
- The user may not have sufficient technical depth to evaluate all findings. The confidence score from reflexion:reflect and trust scores from fpf:propose-hypotheses help calibrate user decision-making.

**Priority:** 10

**Dependencies:** Principle 8 (critique/reflect must produce the outputs that the user selects from)

---

## Additional Recommended Principles

Based on patterns observed in Phase 1 discovery, the tooling catalog, and industry best practices, the following additional principles are recommended:

---

## Principle 12: Conventional Commit Discipline

**Statement:** Every commit message must follow Conventional Commits format (`type(scope): description`) and reference the relevant GitHub issue number.

**Industry basis:** Conventional Commits specification (conventionalcommits.org). Enables automated changelog generation, semantic versioning, and commit history searchability. Adopted by Angular, Vue.js, and thousands of open-source projects.

**Discovery validation:** Phase 1 confirms consistent adherence to Conventional Commits: `fix(api):`, `test(e2e):`, `feat(e2e):`, `docs:`, etc. Issue numbers are referenced via `(#NNN)` suffix. This principle formalizes and enforces an already-observed pattern.

**Enforcement:** PreToolUse hook (validate commit message format before `git commit`)

**Tooling:**

- `git:commit` -- NeoLabHQ/context-engineering-kit (git plugin v1.2.0), creates well-formatted conventional commit messages
- Custom PreToolUse hook: intercept `git commit -m "..."` commands, validate message format with regex
- `changelog-automation` skill -- wshobson/agents, generates changelog from conventional commits

**Source:** NeoLabHQ/context-engineering-kit + custom hook

**Trade-offs:**

- Strict format enforcement may conflict with the "frequent commits" principle (Principle 3) -- writing a proper conventional commit message for every 5-minute WIP commit is overhead. Allow a `wip(scope):` type for progress commits that will be squashed before PR.
- The regex validation in a hook cannot enforce semantic accuracy (e.g., using `fix` when it should be `feat`). This remains a judgment call.

**Priority:** 12

**Dependencies:** Principle 3 (frequent commits need a WIP format exception)

---

## Principle 13: Pre-PR Code Review

**Statement:** Run automated multi-agent code review on local changes before creating a PR, supplementing TDD with architectural and security analysis.

**Industry basis:** Shift-left code review. Google's Critique system provides automated pre-review feedback. The "review before review" pattern reduces human reviewer burden and catches mechanical issues before they reach human eyes.

**Discovery validation:** Phase 1 tooling catalog identifies `code-review:review-local-changes` (6-agent review) and `code-review:review-pr` (CI-integrated review) from NeoLabHQ/context-engineering-kit. Neither is currently used in the workflow. The `comprehensive-review:full-review` from wshobson/agents provides 8-dimension parallel review. Adding automated review before PR creation fills a gap identified in Phase 1.

**Enforcement:** Workflow-level (invoked before `gh pr create`) or PreToolUse hook

**Tooling:**

- `code-review:review-local-changes` -- NeoLabHQ/context-engineering-kit (code-review plugin v1.0.8), 6-agent review of uncommitted changes
- `comprehensive-review:full-review` -- wshobson/agents, 8-dimension parallel review
- `comprehensive-review:pr-enhance` -- wshobson/agents, enhance PR description with risk assessment

**Source:** NeoLabHQ/context-engineering-kit + wshobson/agents

**Trade-offs:**

- 6-agent parallel review is token-expensive (high footprint per the catalog). Running it before every PR may be excessive -- consider limiting to PRs that touch critical paths (order execution, risk manager, authentication).
- Automated review cannot replace human judgment on business logic. It excels at catching mechanical issues (security, performance, test coverage, naming conventions).
- Review findings must go through user-in-the-loop selection (Principle 11) -- auto-fixing review findings risks introducing regressions.

**Priority:** 13

**Dependencies:** Principle 5 (tests must pass before review is meaningful), Principle 11 (user selects which findings to address)

---

## Principle 14: Documentation-Gated PRs

**Statement:** All documentation (CLAUDE.md, design docs, session notes) must be updated before a PR is created.

**Industry basis:** "Documentation as code" movement. Stripe's approach of treating documentation as a first-class deliverable. The existing Stop hook (blocks session end if session notes not updated) validates this as an established practice in this project.

**Discovery validation:** Phase 1 confirms the pattern: the Stop hook enforces session notes updates, CLAUDE.md is extensively maintained, and design docs precede implementation. However, there is no gate ensuring documentation is updated before PR creation -- only before session end.

**Enforcement:** PreToolUse hook (check documentation freshness before `gh pr create`) or workflow-level

**Tooling:**

- `docs:update-docs` -- NeoLabHQ/context-engineering-kit (docs plugin v1.2.0), updates implementation documentation
- `docs:write-concisely` -- NeoLabHQ/context-engineering-kit for documentation polish
- Existing `check_session_notes.sh` Stop hook -- extend pattern to PR creation

**Source:** NeoLabHQ/context-engineering-kit + custom hook

**Trade-offs:**

- "Updated documentation" is hard to validate mechanically -- the hook can check file modification timestamps but not content quality.
- Documentation overhead per PR may slow down small fixes (e.g., a one-line bug fix should not require a design doc update). Define thresholds: only require doc updates for PRs touching architecture, APIs, or configuration.

**Priority:** 14

**Dependencies:** Principle 5 (tests), Principle 13 (code review)

---

## Implementation Priority Ranking

Principles are ranked considering: (1) foundational dependencies, (2) gap severity from Phase 1 discovery, (3) effort-to-value ratio, and (4) implementation dependencies.

| Rank | Principle                    | Rationale                                                             |
| ---- | ---------------------------- | --------------------------------------------------------------------- |
| 1    | P1: Branch Isolation         | Foundational -- all other principles depend on feature branches       |
| 2    | P2: Code Formatting          | Low effort, high value -- addresses a documented gap, no dependencies |
| 3    | P5: TDD Enforcement          | Core quality gate -- directly addresses the "no test enforcement" gap |
| 4    | P7: Triple-Gate Testing      | Extends P5 to full CI pipeline -- addresses the "no E2E in CI" gap    |
| 5    | P3: Frequent Local Commits   | Safety net -- requires custom build but high rollback value           |
| 6    | P8: Shift-Left Critique      | Formalizes existing practice -- reflexion plugin already active       |
| 7    | P4: Regular Remote Sync      | Backup and CI trigger -- depends on P1 and P3                         |
| 8    | P6: Main-to-Dev Sync         | Extends existing deploy hook -- depends on P1, P5, P7                 |
| 9    | P9: Continuous Memorization  | Knowledge management -- depends on P8                                 |
| 10   | P11: User-in-the-Loop Gates  | Quality control -- depends on P8                                      |
| 11   | P10: Diagram-Driven Design   | Aspirational -- no existing practice, highest setup effort            |
| 12   | P12: Conventional Commits    | Formalizes existing pattern -- low effort but low urgency             |
| 13   | P13: Pre-PR Code Review      | High value but high token cost -- depends on P5, P11                  |
| 14   | P14: Documentation-Gated PRs | Nice-to-have -- partial enforcement already exists via Stop hook      |

---

## Summary Table

| #   | Principle               | Industry Basis                           | Enforcement                              | Tool                                                                  | Priority |
| --- | ----------------------- | ---------------------------------------- | ---------------------------------------- | --------------------------------------------------------------------- | -------- |
| 1   | Branch Isolation        | Trunk-based dev + worktrees              | PreToolUse hook (existing) + plugin      | git:create-worktree (context-engineering-kit)                         | 1        |
| 2   | Code Formatting         | Shift-left code quality                  | PostToolUse hook (format on write)       | ddd:setup-code-formatting (context-engineering-kit) + Prettier + Ruff | 2        |
| 3   | Frequent Local Commits  | Continuous integration / rollback safety | Custom background timer + PostToolUse    | Custom build (watchdog + marker file)                                 | 5        |
| 4   | Regular Remote Sync     | CI / remote backup                       | Custom background timer + PostToolUse    | Custom build (same pattern as P3)                                     | 7        |
| 5   | TDD Enforcement         | Test-Driven Development                  | PreToolUse hook (block PR if tests fail) | tdd:write-tests (context-engineering-kit) + tdd-workflows (wshobson)  | 3        |
| 6   | Main-to-Dev Sync        | Continuous Deployment                    | PostToolUse hook + GitHub Actions        | cicd-automation (wshobson) + deploy-dev.sh extension                  | 8        |
| 7   | Triple-Gate Testing     | Progressive testing / defense in depth   | Hook + CI + deploy script                | code-review:review-pr (context-engineering-kit) + CI extension        | 4        |
| 8   | Shift-Left Critique     | Shift-left quality / LLM-as-Judge        | Workflow-level (manual at gates)         | reflexion:critique + reflexion:reflect (context-engineering-kit)      | 6        |
| 9   | Continuous Memorization | Organizational learning                  | Workflow-level (post-critique)           | reflexion:memorize (context-engineering-kit) + mem0                   | 9        |
| 10  | Diagram-Driven Design   | ADRs + visual modeling                   | Manual (workflow discipline)             | Excalidraw MCP + c4-architecture (wshobson)                           | 11       |
| 11  | User-in-the-Loop Gates  | Human-in-the-loop AI                     | Workflow-level (selection prompt)        | reflexion:critique output format (context-engineering-kit)            | 10       |
| 12  | Conventional Commits    | Conventional Commits spec                | PreToolUse hook (commit msg validation)  | git:commit (context-engineering-kit) + custom hook                    | 12       |
| 13  | Pre-PR Code Review      | Shift-left code review                   | Workflow-level or PreToolUse hook        | code-review:review-local-changes (context-engineering-kit)            | 13       |
| 14  | Documentation-Gated PRs | Documentation as code                    | PreToolUse hook (doc freshness check)    | docs:update-docs (context-engineering-kit)                            | 14       |

---

## Cross-Cutting Concerns

### Token Budget Management

Several principles involve high-token-cost operations:

- `/reflexion:critique` (3 judge sub-agents + debate) -- estimated high cost per invocation
- `code-review:review-local-changes` (6 parallel sub-agents) -- estimated high cost
- `sadd:do-competitively` (6-7+ sub-agents) -- estimated very high cost

**Recommendation:** Track token usage per gate invocation. If cumulative critique + review costs exceed a session budget, degrade gracefully: skip "Could Do" items, reduce debate rounds, use `reflexion:reflect` instead of `reflexion:critique` for lower-risk gates.

### Conflict: Autonomous Speed vs. Human-in-the-Loop

Principles 3 (frequent commits) and 4 (remote sync) optimize for speed and safety through automation. Principle 11 (user-in-the-loop) introduces synchronous blocking. These create a tension:

**Resolution:** Define two operating modes:

1. **Attended mode:** Full user-in-the-loop at all quality gates. Default mode.
2. **Unattended mode:** Auto-apply "Must Do" critique findings, auto-commit, auto-push. For overnight or batch runs. Requires explicit opt-in.

### Conflict: 5-Minute Commits vs. Clean History

Principle 3 (frequent commits) creates noisy commit history. Principle 12 (conventional commits) expects meaningful commit messages.

**Resolution:** Two-tier commit protocol:

1. **Progress commits:** `wip(scope): checkpoint` every 5 minutes. These provide rollback safety.
2. **Meaningful commits:** Conventional format with full description at logical completion points.
3. **Pre-PR squash:** Interactive rebase to squash WIP commits into meaningful commits before PR creation.

### Tool Source Prioritization

When multiple tools from different repositories serve the same purpose, prioritize:

1. **NeoLabHQ/context-engineering-kit** -- already installed as plugins (reflexion, sdd, fpf), lowest integration cost
2. **Custom hooks** -- for enforcement that no existing tool provides
3. **wshobson/agents** -- large catalog but requires new plugin installation; use for capabilities not in context-engineering-kit
4. **VoltAgent/awesome-claude-code-subagents** -- prompt-only definitions, easy to install as agents but no runtime framework
5. **ruvnet/ruflo** -- NOT recommended for production use (alpha quality, stub implementations, aspirational docs)
