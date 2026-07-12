---
name: rawgentic:admit-to-org-runners
description: Admit the BOUND project's repo to an org self-hosted runner group and migrate its CI workflow from GitHub-hosted runners to the fleet. Use when a project's `ci.yml` runs on `ubuntu-latest`/`windows-latest`/`macos-*` and you want it on a shared org fleet instead — e.g. org Actions minutes are exhausted (an account-wide pool that blocks hosted jobs in every repo), or the workspace directive is "default self-hosted, never GitHub-hosted." Dry-run by default; fail-closed (never strands CI on a label no online runner has, never leaves a hosted fallback); idempotent. Do NOT use to CREATE runners/groups, to add NEW OS jobs to a workflow (it only migrates existing `runs-on`), or for repo-level (non-org) runners. Invoke with /rawgentic:admit-to-org-runners.
argument-hint: "[--group <name>] [--admin-token-file <path>] [--workflow <path>] [--apply]  (dry-run unless --apply)"
---

# Admit to Org Runners — fleet admission + CI migration

<role>
You admit the BOUND project's repo to an organization self-hosted runner group and
migrate its CI workflow off GitHub-hosted runners onto that fleet. You are fail-closed
by construction: you verify an ONLINE runner carries the exact labels a job will target
BEFORE editing anything, you NEVER leave (or instate) a GitHub-hosted fallback, and you
are idempotent — a repo already admitted and a workflow already on the fleet are a clean
no-op. Dry-run is the default; you write nothing and open nothing until `--apply`.

Credential discipline is absolute: the org runner API needs a SEPARATE admin credential
from ordinary repo ops. The admin token is used ONLY on `orgs/<org>/actions/runner-*`
endpoints; the default `gh` token is used for everything repo/workflow/PR. You never
cross them, and a missing admin token is a STOP — never a fall back to the default token,
never a fall back to hosted runners.
</role>

<config-loading>
Before executing any workflow steps, load the project configuration:

1. Determine the active project using this fallback chain:
   **Level 1 -- Conversation context:** If a previous `/rawgentic:switch` in this session set the active project, use that.
   **Level 2 -- Session registry:** Read `claude_docs/session_registry.jsonl`. Grep for your session_id. If found, use the project from the most recent matching line.
   **Level 3 -- Workspace default:** Read `.rawgentic_workspace.json` from the Claude root directory. If exactly one project has `active == true`, use it. If multiple projects are active, STOP and tell user: "Multiple active projects. Run `/rawgentic:switch <name>` to bind this session."

   At any level:
   - `.rawgentic_workspace.json` missing -> STOP. Tell user: "No rawgentic workspace found. Run /rawgentic:new-project."
   - `.rawgentic_workspace.json` malformed -> STOP. Tell user: "Workspace file is corrupted. Run /rawgentic:new-project to regenerate, or fix manually."
   - No active project found at any level -> STOP. Tell user: "No active project. Run /rawgentic:new-project to set one up, or /rawgentic:switch to bind this session."
   - **Path resolution:** The `activeProject.path` may be relative (e.g., `./projects/my-app`). Resolve it against the Claude root directory (the directory containing `.rawgentic_workspace.json`) to get the absolute path for file operations.

2. Load the config and derive capabilities with the helper CLI (one tested
   source of truth — never hand-derive the `capabilities` object, so every
   config-driven skill and the docs table cannot drift apart):
   ```bash
   python3 hooks/capabilities_lib.py derive \
     --config <activeProject.path>/.rawgentic.json
   ```
   - **Non-zero exit** -> the config is missing, corrupt, or invalid. **STOP** and relay the printed message (it directs the user to `/rawgentic:setup`). A `config.version` mismatch is only a stderr warning and does NOT stop the workflow.
   - **Exit 0** -> stdout is `{"config": {...}, "capabilities": {...}}`. Use the parsed `config` object and the derived `capabilities` object for all subsequent steps. The `capabilities` fields are: `has_tests`, `test_commands`, `has_ci`, `ci_quarantined`, `ci_quarantine_reason`, `ci_quarantined_since`, `has_deploy`, `deploy_method`, `has_database`, `has_docker`, `project_type`, `repo`, `default_branch`, `migration_dir`. Carry these values as literals into later commands (each step is its own Bash call, so shell variables do not persist across them).

All subsequent steps use `config` and `capabilities` — never probe the filesystem for information that should be in the config.
</config-loading>

## Step 1: Resolve the bound repo and org

From the loaded `capabilities`:
- `repo` = `owner/name` (e.g. `3D-Stories/kukakuka`). **`org` = the `owner` segment.**
- `default_branch` (for the migration PR base).

If `capabilities.repo` is empty, STOP: the project config has no repo — run `/rawgentic:setup`.

## Step 2: Resolve the admin credential (fail-closed)

The org runner API requires a credential with **org "Self-hosted runners: Read/Write"**.
Resolve it in this order; **do not** fall back to the default `gh` token:

1. `--admin-token-file <path>` → the token is the file's contents.
2. `$RAWGENTIC_RUNNER_ADMIN_TOKEN` → the token is the env value directly.
3. Neither present → **STOP**: "No runner-admin credential. Pass `--admin-token-file
   <path>` (a file holding a fine-grained PAT with org Self-hosted-runners R/W) or set
   `$RAWGENTIC_RUNNER_ADMIN_TOKEN`. The default gh token is NOT used for the org runner
   API." Do not proceed.

Reference the token by its **path/name only** — never echo its value into chat, commits,
or issues. In every org-API call below, supply it as `GH_TOKEN=$(cat <admin-token-file>)`
(or `GH_TOKEN="$RAWGENTIC_RUNNER_ADMIN_TOKEN"`) on that single command — nowhere else.

## Step 3: Discover the runner group and its ONLINE runners

List the org's runner groups (admin token):

```bash
GH_TOKEN=$(cat <admin-token-file>) gh api --paginate \
  orgs/<org>/actions/runner-groups --jq '.runner_groups[] | {id, name, visibility}'
```

Choose the group:
- `--group <name>` given → select the group with that name; if none matches, STOP and
  list the available group names.
- omitted and exactly one group exists → use it.
- omitted and several exist → present the list and ask which group.

Then fetch the group's runners with their status + labels (admin token):

```bash
GH_TOKEN=$(cat <admin-token-file>) gh api --paginate \
  orgs/<org>/actions/runner-groups/<group_id>/runners \
  --jq '[.runners[] | {name, status, labels: [.labels[].name]}]'
```

Keep this JSON array — it is the `--runners` input to the migration planner. If the
group has **zero online runners**, STOP: migrating now would strand every job.

## Step 4: Admit status (idempotency check — read only here)

Is the bound repo already in the group?

```bash
GH_TOKEN=$(cat <admin-token-file>) gh api --paginate \
  orgs/<org>/actions/runner-groups/<group_id>/repositories --jq '.repositories[].full_name'
```

If `<owner>/<name>` is present → **already admitted**; the admit step is a no-op. Record
this; do not re-admit.

## Step 5: Plan the CI migration (dry-run — the default)

Pick the workflow file(s):
- `--workflow <path>` → just that file.
- omitted → every `.github/workflows/*.yml` / `*.yaml` in the repo.

For each workflow, feed the Step-3 runner JSON to the planner (it decides what is safe —
you do not hand-parse YAML):

```bash
printf '%s' '<runners-json>' | python3 hooks/org_runners_lib.py \
  plan --workflow <path> --group <group_name> --runners -
```

Read the JSON `verdict`:
- **`ready`** — one or more hosted `runs-on` map to an online runner in the group; each
  `migrate` job names its `target_labels`.
- **`noop`** — already on the fleet (or nothing hosted). Nothing to do for this file.
- **`blocked`** — a hosted job has **no online runner** in the group with the needed
  labels. Migrating would strand it → this file is NOT migrated; report the blocked job.
- **`manual`** — a `runs-on` shape the planner will not rewrite (a `${{ }}` expression /
  matrix, or an unrecognized form). It is NOT touched; report it for hand-migration.

**Fail-closed rule:** only `ready`/`noop` files are eligible to apply. A `blocked` or
`manual` file is left exactly as-is — never a partial edit that leaves a hosted fallback.

## Step 6: Present the plan and stop (unless `--apply`)

Show, together (a quality-gate presentation — never compressed away):
- admit status (already-in-group vs will-admit),
- per-workflow verdict + each job's action/reason,
- the exact diff each `ready` file would get (hosted scalar → `{group, labels}` block),
- any `blocked`/`manual` lanes, called out as the reason the migration is incomplete.

**Without `--apply`: stop here.** This is a dry run — nothing was admitted, nothing edited.

## Step 7: Apply (`--apply` only)

Perform, in order. If everything is already admitted + on the fleet, report a clean
**no-op** and stop.

**7a — Admit the repo (admin token), only if Step 4 said it isn't a member:**

```bash
REPO_ID=$(gh api repos/<owner>/<name> --jq '.id')          # default token — repo read
GH_TOKEN=$(cat <admin-token-file>) gh api --method PUT \    # admin token — org runner API
  orgs/<org>/actions/runner-groups/<group_id>/repositories/$REPO_ID
```

(PUT is idempotent — re-admitting a member is harmless.)

**7b — Migrate each `ready` workflow as a PR (default token — repo ops):**

1. Branch from fresh `origin/<default_branch>`: `git fetch origin` then
   `git checkout -b ci/migrate-<group_name>-fleet origin/<default_branch>`.
2. Rewrite in place (the planner re-verifies and refuses anything not `ready`, and
   fails closed if a hosted remnant would survive):
   ```bash
   printf '%s' '<runners-json>' | python3 hooks/org_runners_lib.py \
     rewrite --workflow <path> --group <group_name> --runners - --in-place
   ```
3. Confirm no hosted fallback survived (exit 0 required):
   ```bash
   python3 hooks/org_runners_lib.py check-hosted --workflow <path>
   ```
   Non-zero → STOP; do not commit a workflow that still has a hosted `runs-on`.
4. Stage **only** the workflow file(s) by name, commit (conventional
   `ci: migrate <repo> CI to org self-hosted fleet <group_name>`), push, and open a PR
   against `<default_branch>`. Body: which jobs moved to which `{group, labels}`, and the
   admit status. A repo that cannot take a PR should not be on the fleet — the migration
   ships as a reviewable PR, never a direct push to the default branch.

Report the PR URL. Do NOT merge it (no standing merge grant).

<completion-gate>
Before declaring done: the admit status was read (Step 4), every targeted workflow was
planned (Step 5) with its verdict surfaced, and — on `--apply` — the repo is a group
member, each migrated file passes `check-hosted` (no hosted remnant), and a PR is open
(or a clean no-op was reported). The admin token was used ONLY on `orgs/<org>/actions/…`
calls and never echoed by value. On a dry run, confirm nothing was admitted or edited.
</completion-gate>
