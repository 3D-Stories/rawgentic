---
name: rawgentic:adversarial-review
description: WF5 — Adversarially review a TEXT artifact (design, spec, implementation plan, PRD, ADR, RFC, README) using an independent DIFFERENT-MODEL reviewer via the Codex CLI. Report-only — writes a severity-ranked findings report to <project>/docs/reviews/ and NEVER edits the artifact. Also reviews code DIFFS via the `diff` artifact type (refutation lens, report-only) — this complements same-model critique (reflexion:critique) with a cross-model second opinion on planning artifacts. Invoke with /rawgentic:adversarial-review followed by an artifact path. Requires the Codex CLI to be installed and authenticated.
argument-hint: Artifact path (e.g., "docs/design/feature.md") with optional type hint (design|spec|plan|prd|adr|rfc|readme|diff)
---

# WF5: Adversarial Review Workflow

<role>
You are the WF5 orchestrator. You run an independent, cross-model adversarial review of a single TEXT artifact using the Codex CLI (a different model than yourself), then write a severity-ranked findings report. You are STRICTLY report-only: you never edit the reviewed artifact and you never auto-apply findings — the user (or the calling workflow) decides what to do with them. All real logic lives in `hooks/adversarial_review_lib.py`; you are a thin orchestrator over it.
</role>

<constants>
SUPPORTED_ARTIFACT_TYPES: design, spec, plan, prd, adr, rfc, readme, generic, diff
FINDING_SEVERITIES: Critical, High, Medium, Low
REVIEWER: Codex CLI (independent different-model reviewer; egress to OpenAI)
OUTPUT: <activeProject.path>/docs/reviews/<slug>-<YYYY-MM-DD>.md  (report-only)
ENGINE: hooks/adversarial_review_lib.py
CLI: `review --findings-json <path>` (optional; embedded-consumer sidecar — written only on success, after the report; path must resolve under the project root)
ENV (all optional, frozen at lib import):
  RAWGENTIC_ADV_REVIEW_MAX_BYTES   (default 200000) — artifact size cap; over-cap truncates + warns
  RAWGENTIC_ADV_REVIEW_TIMEOUT     (default 600)    — Codex invocation timeout (seconds); 600 gives high-effort reviews of large artifacts headroom
  RAWGENTIC_ADV_REVIEW_MAX_RETRIES (default 1)      — retries on transient Codex failure
  RAWGENTIC_ADV_REVIEW_BLOCK_SECRETS (default off)  — when set, block egress if secrets detected
  RAWGENTIC_ADV_REVIEW_EFFORT      (default high)   — Codex reasoning effort (low|medium|high); pinned explicitly so a fresh ~/.codex/config.toml that defaults to medium does not silently degrade the review
  RAWGENTIC_ADV_REVIEW_MODEL       (default unset)  — override the reviewer model (`codex exec -m`); unset = inherit Codex/config default (do NOT hardcode a model id — OpenAI retires them)
</constants>

<reviewer-invocation>
The engine invokes Codex as a one-shot, tools-OFF, structured-JSON reviewer (NOT
`codex review`, which is git-diff-only with no `--output-schema`). The argv is:
`codex exec [-m <model>] --output-schema <schema> -o <out> -c model_reasoning_effort=<effort> --ephemeral --color never -c project_doc_max_bytes=0 -s read-only -C <root> --skip-git-repo-check -`
- **effort pinned** (high): gpt-5.5 defaults to medium; deep critique benefits from high.
- **--ephemeral**: the prompt inlines the full (possibly proprietary) artifact; this keeps it out of CODEX_HOME session history.
- **project_doc_max_bytes=0**: suppresses the reviewed project's AGENTS.md so the cross-model reviewer stays independent of the project's own conventions.
- **--color never**: keeps the parsed output byte-clean.
- The prompt itself FORBIDS the model from running any shell/tool/file/network op (review purely from inlined text) — required where the Codex bubblewrap sandbox is unavailable, and a defense against artifact-embedded prompt injection. Each finding must carry a verbatim `evidence` quote (grounding) and a `confidence`; severity is governed by an explicit rubric to curb inflation.
</reviewer-invocation>

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
   source of truth — never hand-derive the `capabilities` object, so all 12
   workflow skills and the docs table cannot drift apart):
   ```bash
   python3 hooks/capabilities_lib.py derive \
     --config <activeProject.path>/.rawgentic.json
   ```
   - **Non-zero exit** -> the config is missing, corrupt, or invalid. **STOP** and relay the printed message (it directs the user to `/rawgentic:setup`). A `config.version` mismatch is only a stderr warning and does NOT stop the workflow.
   - **Exit 0** -> stdout is `{"config": {...}, "capabilities": {...}}`. Use the parsed `config` object and the derived `capabilities` object for all subsequent steps. The `capabilities` fields are: `has_tests`, `test_commands`, `has_ci`, `has_deploy`, `deploy_method`, `has_database`, `has_docker`, `project_type`, `repo`, `default_branch`, `migration_dir`. Carry these values as literals into later commands (each step is its own Bash call, so shell variables do not persist across them).

All subsequent steps use `config` and `capabilities` — never probe the filesystem for information that should be in the config.
</config-loading>

<termination-rule>
WF5 ALWAYS terminates after presenting the report. It is report-only: it does NOT edit the artifact, does NOT create issues, and does NOT auto-transition to any other workflow. WF5 terminates ONLY after the completion-gate passes. All steps must have markers in session notes.
</termination-rule>

<ambiguity-circuit-breaker>
Per the shared invariant: STOP and ask the user when findings are ambiguous, conflicting, or require judgment not present in the artifact. This skill is report-only, so the circuit breaker manifests as: if Codex returns findings whose severity or applicability is genuinely unclear, surface them to the user with the ambiguity flagged rather than silently ranking them. The user (or, in embedded mode, the calling workflow) has final authority over every finding (P11).
</ambiguity-circuit-breaker>

<data-handling>
This skill transmits the artifact's TEXT to OpenAI (Codex) for an independent model review — the artifact leaves the machine. This is **warn-only**: the skill prints a one-time egress notice before invoking Codex and proceeds. The engine additionally scans the artifact for obvious secrets (API keys, passwords, tokens, private keys) and, if any are found, names the detected categories in the notice. To make secret detection blocking instead of advisory, set `RAWGENTIC_ADV_REVIEW_BLOCK_SECRETS=1`. Findings reports are written locally to `<project>/docs/reviews/` and never uploaded anywhere. A `diff` artifact is raw source code — the highest secret density of any supported type, so the egress warning above and the `RAWGENTIC_ADV_REVIEW_BLOCK_SECRETS=1` hard-block matter most here. An agent-harness egress classifier may also block the Codex invocation entirely, independent of this skill's own warn-only policy — embedded callers (e.g. WF2 Step 11) must treat that as a failed review and continue non-blocking, while standalone runs surface the block to the user.
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
   - If a type hint (one of SUPPORTED_ARTIFACT_TYPES) is given, record it; otherwise auto-detect from the filename (e.g. `*spec*` → spec, `*plan*` → plan, `*adr*` → adr, `README*` → readme, `*.patch`/`*.diff` → diff) and fall back to `generic`.
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
     [--headless] \
     [--findings-json <path>]
   ```
   Embedded callers (e.g. WF2 Step 11) may append `--findings-json <path>` to also receive a machine-readable sidecar of the findings; it is written only on success, after the report.
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
