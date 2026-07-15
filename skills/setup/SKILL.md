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

<references>
This spine carries every step heading in order with a short summary; the detailed
prose for the heavier steps lives in `references/`. Read the pointed-to reference
file **before executing** that step:
- **Steps 2c, 2d, 2f, 2g, 2h** → `references/integrations.md` (Step 2e stays in this spine)
- **Step 3** → `references/detect-flows.md`
- **Steps 4, 4b** → `references/config-reference.md`
</references>

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

## Step 2c: Headless Mode Access

Runs on **every** setup invocation (including Sub-flow A re-runs). Checks the
active project's `headlessEnabled` field in `.rawgentic_workspace.json`: prompts
(default n) on first-time configuration, or shows status and allows toggling on
re-configuration. Headless mode grants an external orchestrator autonomous
access, so it stays opt-in.

**Read `references/integrations.md` before executing Step 2c.**

---

## Step 2d: Adversarial Review (WF5) Integration

Runs on **every** setup invocation (including Sub-flow A re-runs). Asks whether an
OpenAI account is available for the Codex CLI and, on yes, turns WF5 cross-model
review **on by default** for the applicable workflows — implement-feature (WF2)
and fix-bug (WF3), with create-issue (WF1) offered as an opt-in add. Also asks
which review backend (`gpt` | `glm` | `both`, #405) and stages it into the
block's `backend` field (default `gpt` may omit the field). Stores the
`adversarialReview` field in the project's `.rawgentic_workspace.json` entry
(workspace-scoped, not committed to the repo). The standalone
`/rawgentic:adversarial-review` skill works regardless of this setting.

**Read `references/integrations.md` before executing Step 2d.**

---

## Step 2e: Security Scan Tooling

This step runs on **every** setup invocation (including Sub-flow A re-runs).

WF2 Step 11.5 (pre-PR gate) and the `/rawgentic:scan` utility both run
`hooks/security_scan.py`, which shells out to real scanners (gitleaks, semgrep,
osv-scanner, and — for Docker projects — trivy). The scanner degrades gracefully
(a tool that isn't installed is a *visible skip*, never a silent "clean"), but a
skipped scanner is a real coverage gap, so setup installs whatever is missing.

**Installs are opt-OUT, not opt-in** — install by default; let the user decline.

1. **Check the workspace opt-out.** Read `installScanners` from
   `.rawgentic_workspace.json`. If it is `false`, the user previously opted out:
   print "Security scanner install is opted out (`installScanners: false`)." and
   skip the rest of this step.

2. **Install missing scanners (default).** Unless opted out, run the idempotent
   installer (best-effort; a tool already present is left alone, and one that
   can't be auto-installed is reported, never fatal):
   ```bash
   bash <plugin-root>/scripts/install-scanners.sh
   ```
   In an interactive setup, tell the user this is happening and that they can
   decline. If they decline, persist it: read `.rawgentic_workspace.json`, set
   top-level `"installScanners": false`, write it back (and skip the install).
   In **headless** mode do NOT install — just record the gap.

3. **Report** which scanners are now present and which remain missing (so the
   user knows the WF2/WF9 scan will skip those). The installer's `--check` mode
   prints this:
   ```bash
   bash <plugin-root>/scripts/install-scanners.sh --check
   ```
   No `.rawgentic.json` field is written for *presence* — the scanner probes tool
   presence at run time (exactly like WF5 probes for the Codex CLI). Only the
   opt-out decision is persisted, and only to the workspace file.

Note: the session-start hook (`hooks/scanner_bootstrap.py`) also re-checks the
scanners every startup/resume and installs any that are missing in the background,
honoring the same `RAWGENTIC_SKIP_SCANNER_INSTALL=1` / `installScanners: false`
opt-outs and writing a status file at `~/.rawgentic/scanner-status.json` — so most
projects already have the scanners by the time setup runs (and a scanner that goes
missing, or one added by a plugin update, is reinstalled automatically). This step
is the explicit, user-visible confirmation.

### New features are ON by default (opt-OUT)

The feature steps above (2c headless, 2d adversarial review, 2e scanners, 2f model
routing, 2g peer consult) run on **every** setup invocation, including Sub-flow A
re-runs against an existing `.rawgentic.json`. When the plugin gains a capability,
re-running setup therefore **enriches an older config and turns the new feature on
by default** — features are opt-OUT, not opt-in. Four deliberate exceptions, which
always require an explicit answer and are never force-enabled:

- **Headless mode (2c)** stays opt-in — it grants an external orchestrator
  autonomous access to the project, so it must be a conscious choice (default n).
- **Adversarial review / WF5 (2d)** depends on an OpenAI account for the Codex CLI,
  so setup asks the account question; "yes" turns it on for the applicable
  workflows, "no" leaves it off.
- **Model routing (2f)** stays opt-in — it has no dependency to gate on, but routing
  is a deliberate per-project choice; declining stages nothing (absent = inherit
  everywhere, byte-identical to today).
- **Peer consult / WF13 (2g)** mirrors 2d's answer-required pattern (same Codex CLI
  dependency) but, unlike WF5, has no default-on recommendation — it always asks,
  and "no" leaves the WF2 integration off (the standalone skill still works).

Everything else (e.g. the security scanners) installs/enables by default unless the
user has an opt-out on record. The SessionStart post-update reconcile
(`hooks/post_update_reconcile.py`) applies this same policy without a setup re-run:
on a version change it enables any new opt-OUT feature whose flag is absent (honoring
recorded opt-outs), leaves headless and WF5 alone, and nudges the user to run
`/rawgentic:setup` for the answer-required ones.

---

## Step 2f: Model Routing (optional)

Runs on **every** setup invocation (including Sub-flow A re-runs). Offers
per-project subagent model routing for the three dispatch roles (review, analysis,
implementation); stages the `modelRouting` field in the project's
`.rawgentic_workspace.json` entry. Stays opt-in — declining stages nothing (absent
block = inherit everywhere).

**Read `references/integrations.md` before executing Step 2f.**

## Step 2g: Peer Consult (WF13) Integration

Runs on **every** setup invocation (including Sub-flow A re-runs). Mirrors Step 2d:
asks whether to enable the cross-model peer designer at the WF2 design step and
stages the `peerConsult` field in the project's `.rawgentic_workspace.json` entry —
including its own independent `backend` answer (#405; same vocabulary as Step 2d).
The standalone `/rawgentic:peer-consult` works regardless.

**Read `references/integrations.md` before executing Step 2g.**

## Step 2h: HTML Design-Artifact Lifecycle (#174)

Runs on **every** setup invocation (including Sub-flow A re-runs). Mirrors Step 2d:
asks whether to enable the opt-in HTML design-artifact lifecycle (WF1 publishes the
issue artifact; WF2/WF3 create-or-update it in the feature PR with run telemetry),
and — when enabled — whether to use **per-issue** artifacts (default) or **shared-doc
mode** (one rolling `docs/*.md` program doc updated per slot, like a campaign
dashboard). Stages the `designArtifact` field (with optional `sharedDoc`) in the
project's `.rawgentic_workspace.json` entry.

**Read `references/integrations.md` before executing Step 2h.**

---

## Step 3: Detect or Brainstorm

Read `templates/rawgentic-json-schema.json` from the rawgentic plugin directory to
understand the full schema structure, then dispatch to one of three sub-flows:
**A** (existing `.rawgentic.json` — re-run with the merge policy), **B** (existing
code, no config — run the auto-detection sequence), or **C** (empty/new project —
brainstorm intent).

**Read `references/detect-flows.md` before executing Step 3.**

---

## Step 4: Present Detected Config

Show the user the assembled `.rawgentic.json` as formatted JSON, annotating each
section with the source the values came from. Only present sections that were
actually detected.

**Read `references/config-reference.md` before executing Step 4.**

---

## Step 4b: Critique Detected Config (Optional)

Compute a complexity score from the detected config and, above threshold, offer an
in-repo **quality-bar review** (`references/quality-bar.md`) to validate completeness
before the user reviews. Skip when the score is 0 or the user declines.

**Read `references/config-reference.md` before executing Step 4b.**

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
- **Protection level prompt:** After confirming all sections, ask the user to choose a protection level:
  > "What protection level should this project use?"
  > - **sandbox** — No guards active. Good for POC / playground projects.
  > - **standard** — Blocks destroy + mutate ops on production, 6 common security patterns. Read commands stay open for troubleshooting.
  > - **strict** — All guards active. Full production projects. *(This is the default if not set.)*

  Set `protectionLevel` in the config based on the user's choice. If the user wants fine-grained control, explain that `guards.wal` and `guards.security` arrays can override the preset (see `docs/config-reference.md`).

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

Read `.rawgentic_workspace.json`, find the active project entry, and set `"configured": true`. Apply any pending per-project field changes collected earlier in this run — `headlessEnabled` (Step 2c), `adversarialReview` (Step 2d), `modelRouting` (Step 2f), `peerConsult` (Step 2g), and `designArtifact` (Step 2h) — in a single read-modify-write so no step clobbers another's field. Write the file back once.

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

**Review-lane activation nudge (#233 AC2).** If the project ships the GitHub Action
review lanes (`.github/workflows/claude-{security,code}-review.yml`) but **no
`CLAUDE_CODE_OAUTH_TOKEN` / `ANTHROPIC_API_KEY` secret is configured**, those lanes
now go **RED ("not reviewed")** on every PR instead of a misleading green — they are
advisory (non-blocking), but they won't do anything until activated. Tell the user
how to turn them on and point them at the guide:

```
The Claude review lanes are present but inactive (no auth secret) — every PR's
security-review / code-review will show RED until you activate them:
  claude setup-token
  gh secret set CLAUDE_CODE_OAUTH_TOKEN --org <org> --visibility all   # org-wide
(or ANTHROPIC_API_KEY as a fallback). The Claude Code GitHub App must also be
installed on the repo/org. Full guide: docs/ci-review-lanes.md
```

Advisory only — never block setup on this. Skip silently if the lane workflows are
absent or a secret is already set.
