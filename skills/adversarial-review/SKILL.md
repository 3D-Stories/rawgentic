---
name: rawgentic:adversarial-review
description: WF5 — Adversarially review a TEXT artifact (design, spec, implementation plan, PRD, ADR, RFC, README) using an independent DIFFERENT-MODEL reviewer via the Codex CLI. Report-only — writes a severity-ranked findings report to <project>/docs/reviews/ and NEVER edits the artifact. NOT for reviewing code diffs (use /code-review or /rawgentic:security-audit) — this complements same-model critique (reflexion:critique) with a cross-model second opinion on planning artifacts. Invoke with /rawgentic:adversarial-review followed by an artifact path. Requires the Codex CLI to be installed and authenticated.
argument-hint: Artifact path (e.g., "docs/design/feature.md") with optional type hint (design|spec|plan|prd|adr|rfc|readme)
---

# WF5: Adversarial Review Workflow

<role>
You are the WF5 orchestrator. You run an independent, cross-model adversarial review of a single TEXT artifact using the Codex CLI (a different model than yourself), then write a severity-ranked findings report. You are STRICTLY report-only: you never edit the reviewed artifact and you never auto-apply findings — the user (or the calling workflow) decides what to do with them. All real logic lives in `hooks/adversarial_review_lib.py`; you are a thin orchestrator over it.
</role>

<constants>
SUPPORTED_ARTIFACT_TYPES: design, spec, plan, prd, adr, rfc, readme, generic
FINDING_SEVERITIES: Critical, High, Medium, Low
REVIEWER: Codex CLI (independent different-model reviewer; egress to OpenAI)
OUTPUT: <activeProject.path>/docs/reviews/<slug>-<YYYY-MM-DD>.md  (report-only)
ENGINE: hooks/adversarial_review_lib.py
ENV (all optional, frozen at lib import):
  RAWGENTIC_ADV_REVIEW_MAX_BYTES   (default 200000) — artifact size cap; over-cap truncates + warns
  RAWGENTIC_ADV_REVIEW_TIMEOUT     (default 300)    — Codex invocation timeout (seconds)
  RAWGENTIC_ADV_REVIEW_MAX_RETRIES (default 1)      — retries on transient Codex failure
  RAWGENTIC_ADV_REVIEW_BLOCK_SECRETS (default off)  — when set, block egress if secrets detected
</constants>

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

1b. **Disabled skill check:** After resolving the active project, read `.rawgentic_workspace.json` (if not already read in step 1) and find the active project's entry.
   - If the project entry has a `disabledSkills` array and this skill's bare name appears in it: **STOP.**
     - If the skill is one of {implement-feature, fix-bug, create-tests, update-docs}, tell user:
       "You chose [mapped BMAD alternative] for [skill] in [project]. To change, re-run `/rawgentic:setup` or edit `disabledSkills` in `.rawgentic_workspace.json`."
       Mapping: implement-feature -> bmad-dev-story, fix-bug -> bmad-dev-story, create-tests -> bmad-tea agent / bmad-testarch-* workflows, update-docs -> BMAD tech-writer.
     - Otherwise, tell user:
       "Skill [name] is disabled in [project]. Remove it from `disabledSkills` in `.rawgentic_workspace.json` to re-enable."
   - If workspace `bmadDetected` is true but the project entry has **no** `disabledSkills` field: **STOP.** Tell user:
     "BMAD detected but no skill preferences configured for [project]. Run `/rawgentic:switch` or `/rawgentic:setup` to configure."
   - Otherwise: proceed to step 2.

2. Read `<activeProject.path>/.rawgentic.json`.
   - Missing -> STOP. Tell user: "Active project <name> has no config. Run /rawgentic:setup."
   - Malformed JSON -> STOP. Tell user: "Project config is corrupted. Run /rawgentic:setup to regenerate."
   - Check `config.version`. If version > 1 (or missing), warn user about version mismatch.
   - Parse full JSON into `config` object.

3. Build the `capabilities` object from config:
   - has_tests: config.testing exists AND config.testing.frameworks.length > 0
   - test_commands: config.testing.frameworks[].command
   - has_ci: config.ci exists AND config.ci.provider exists
   - has_deploy: config.deploy exists AND config.deploy.method exists and != "manual"
   - has_database: config.database exists AND config.database.type exists
   - has_docker: config.infrastructure exists AND config.infrastructure.docker.composeFiles.length > 0
   - project_type: config.project.type
   - repo: config.repo.fullName
   - default_branch: config.repo.defaultBranch

All subsequent steps use `config` and `capabilities` — never probe the filesystem for information that should be in the config.
</config-loading>

<termination-rule>
WF5 ALWAYS terminates after presenting the report. It is report-only: it does NOT edit the artifact, does NOT create issues, and does NOT auto-transition to any other workflow. WF5 terminates ONLY after the completion-gate passes. All steps must have markers in session notes.
</termination-rule>

<ambiguity-circuit-breaker>
Per the shared invariant: STOP and ask the user when findings are ambiguous, conflicting, or require judgment not present in the artifact. This skill is report-only, so the circuit breaker manifests as: if Codex returns findings whose severity or applicability is genuinely unclear, surface them to the user with the ambiguity flagged rather than silently ranking them. The user (or, in embedded mode, the calling workflow) has final authority over every finding (P11).
</ambiguity-circuit-breaker>

<data-handling>
This skill transmits the artifact's TEXT to OpenAI (Codex) for an independent model review — the artifact leaves the machine. This is **warn-only**: the skill prints a one-time egress notice before invoking Codex and proceeds. The engine additionally scans the artifact for obvious secrets (API keys, passwords, tokens, private keys) and, if any are found, names the detected categories in the notice. To make secret detection blocking instead of advisory, set `RAWGENTIC_ADV_REVIEW_BLOCK_SECRETS=1`. Findings reports are written locally to `<project>/docs/reviews/` and never uploaded anywhere.
</data-handling>

<step-tracking>
At the end of each step, log a marker in `claude_docs/session_notes.md`:
`### WF5 Step X: <Name> — DONE (<key detail>)`
This enables workflow resumption if context is lost.
</step-tracking>

---

## Step 1: Load Config and Validate Artifact

### Instructions

1. **Execute `<config-loading>`** to resolve the active project and its absolute path (`PROJECT_ROOT = <activeProject.path>`). Log the resolved project and repo in session notes.
2. Parse the user argument into an artifact path and an optional type hint:
   - If the argument is a path to an existing file, use it.
   - If a type hint (one of SUPPORTED_ARTIFACT_TYPES) is given, record it; otherwise auto-detect from the filename (e.g. `*spec*` → spec, `*plan*` → plan, `*adr*` → adr, `README*` → readme) and fall back to `generic`.
3. Validate the artifact:
   - The path must resolve to a file **under** `PROJECT_ROOT` (the engine enforces this — traversal/absolute escape is rejected). If it is outside the project, STOP and tell the user the artifact must live inside the active project.
   - If the file does not exist, STOP: "Artifact not found: `<path>`."
   - Artifacts larger than `RAWGENTIC_ADV_REVIEW_MAX_BYTES` are truncated (with a warning in the report); note this to the user.
4. Log artifact path, resolved type, and size in session notes.

### Output

```
WF5 Adversarial Review
======================
Project:  <name>
Artifact: <path>
Type:     <resolved type>
Size:     <bytes> (cap <MAX_BYTES>)
```

### Failure Modes
- Artifact outside the project root → STOP (engine raises ArtifactError).
- File not found → STOP, ask for a correct path.
- Unrecognized type hint → fall back to `generic`.

---

## Step 2: Prerequisite Gate (Codex CLI)

### Instructions

1. Check the Codex prerequisite via the engine:
   ```bash
   python3 -c "import sys; sys.path.insert(0,'hooks'); from adversarial_review_lib import prereq_status; ok,msg=prereq_status(headless=__import__('os').environ.get('RAWGENTIC_HEADLESS')=='1'); print(msg); sys.exit(0 if ok else 2)"
   ```
   (Equivalently: `python3 hooks/adversarial_review_lib.py prereq [--headless]`.)
2. If the prerequisite check fails (exit 2), **STOP** and print the message verbatim. It tells the user how to install and authenticate:
   - Install (standalone binary): `curl -fsSL https://codex.openai.com/install.sh | bash`
   - Authenticate (interactive): `codex login`
   - Headless/CI (API key): `printenv OPENAI_API_KEY | codex login --with-api-key`
3. **Headless note:** ChatGPT OAuth login is interactive-only. If the session is headless (`RAWGENTIC_HEADLESS=1`) and Codex is unauthenticated, this is a terminal ERROR — do not wait for an interactive login. Post an error and exit.

### Output
```
Codex CLI: installed and authenticated [OK]
```
or the verbatim install/login instructions on failure.

### Failure Modes
- Codex not installed → STOP with install instructions.
- Codex not authenticated → STOP with login instructions (headless: ERROR).

---

## Step 3: Egress Notice (Warn-Only)

### Instructions

1. Print the egress notice (warn-only): the artifact text will be sent to OpenAI (Codex). The engine scans for obvious secrets; if any are detected, the notice names the categories.
   ```bash
   python3 -c "import sys; sys.path.insert(0,'hooks'); from adversarial_review_lib import read_artifact, scan_for_secrets, egress_warning; t,_=read_artifact('<artifact>','<PROJECT_ROOT>'); print(egress_warning(scan_for_secrets(t)))"
   ```
2. This is **warn-only** — proceed after printing. If `RAWGENTIC_ADV_REVIEW_BLOCK_SECRETS=1` is set, the engine will refuse egress in Step 4 when secrets are present (status `error`); surface that to the user.

### Output
The egress warning text (and any detected secret categories).

---

## Step 4: Invoke Codex Adversarial Review

### Instructions

1. Run the review via the engine CLI (fail-closed; no live Codex needed in tests):
   ```bash
   python3 hooks/adversarial_review_lib.py review \
     --artifact "<artifact>" \
     --type "<resolved type>" \
     --project-root "<PROJECT_ROOT>" \
     --date "$(date -u +%Y-%m-%d)" \
     [--headless]
   ```
2. Interpret the exit code (the contract is fail-closed):
   - `0` → success; the path of the written report is printed on stdout.
   - `2` → prerequisite failure (not installed / unauthenticated). STOP (should have been caught in Step 2).
   - `3` → Codex error or timeout. STOP and report; **do not** fabricate findings.
   - `4` → Codex output could not be parsed/validated. STOP and report.
3. On any non-zero exit, the review did NOT succeed — report the failure to the user. Never present partial or invented findings as a completed review.

### Output
The path to the generated report (on success) or the failure reason.

### Failure Modes
- Exit 3 (timeout/error) → report and stop; suggest retrying or checking Codex status.
- Exit 4 (parse error) → report; the artifact may be too large or Codex returned unexpected output.

---

## Step 5: Present Report

### Instructions

1. Read the generated report at `<PROJECT_ROOT>/docs/reviews/<slug>-<date>.md`.
2. Present a concise summary to the user: total findings, per-severity counts, and the top Critical/High findings.
3. Print the absolute report path and (if known) the Codex invocation latency.
4. State clearly that this is **report-only**: findings are advisory and the artifact was not modified. Do NOT prompt to apply findings — that is the user's (or the calling workflow's) decision.
5. Log the report path and finding counts in session notes.

### Output

```
Adversarial Review Complete (report-only)
=========================================
Report: <absolute path>
Findings: N (Critical X, High Y, Medium Z, Low W)

Top findings:
- [Critical] ...
- [High] ...

The artifact was NOT modified. Incorporate findings at your discretion.
```

---

## Workflow Resumption

If invoked mid-conversation, detect state:
1. A report file already exists for this artifact+date in `docs/reviews/`? → Step 5 (present it).
2. Config loaded and artifact validated (in session notes)? → Step 2 (prereq) / Step 4 (invoke).
3. None of the above → Step 1.

Announce the detected state before resuming: "Detected prior progress. Resuming at Step N."

---

<completion-gate>
Before declaring WF5 complete, verify ALL of the following. Print the checklist with pass/fail for each item:

1. [ ] Step markers logged for ALL executed steps in session notes
2. [ ] Artifact validated (exists, under project root, type resolved)
3. [ ] Codex prerequisite satisfied (installed + authenticated)
4. [ ] Egress notice printed (warn-only)
5. [ ] Codex review invoked; exit code interpreted (fail-closed on non-zero)
6. [ ] On success: report written to <project>/docs/reviews/ and presented
7. [ ] Artifact NOT modified (report-only invariant)

If ANY item fails, complete it before declaring "WF5 complete."
You may NOT output "WF5 complete" until all items pass.
</completion-gate>
