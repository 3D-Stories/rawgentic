# Rawgentic — CRITICAL Issues

**Source:** First-run retrospective 2026-03-22

---

## 1. Headless mode must run all tests locally, never deploy to remote (A1, B1)

### What Happened

During the first autonomous run on chorestory issue #309, the bot SSHed to .203 (the dev VM) and ran ESLint on a completely different branch (`fix/T1-01-parameterize-tenant-context`), wasting 15-20 minutes on wrong-environment verification. It also disrupted the live dev environment — 504 errors on all API requests, Google SSO disappeared, and login was broken.

### Root Cause Chain

1. `.rawgentic.json` has `deploy.command` pointing to .203 via SSH
2. WF2 uses this config to verify changes on the "dev" target
3. Headless mode didn't distinguish between "run tests locally" and "deploy and test remotely"
4. The bot ran commands on .203 that corrupted its state

### Recommendation

Headless mode must run all tests locally, never deploy to remote. CI should handle deploy-to-dev when the PR is created — same pattern as deploy-to-prod (GitHub Actions deploys on PR merge). The bot's job ends at creating the PR. Human verifies on dev after CI deploys.

The `.rawgentic.json` deploy section is for human workflow and CI, not for the bot. WF2 headless mode should:
- Skip any step that references `deploy.command` or `deploy.targets`
- Run all test/lint/build commands locally within the project directory
- Treat PR creation as the final deliverable

### Cross-References

- Arc critical issue: "Remove SSH from container bind mounts" (same root cause, infrastructure layer)
- Top 5 action item #1
