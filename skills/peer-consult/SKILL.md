---
name: rawgentic:peer-consult
description: WF13 — Engage Codex as a peer senior engineer (a different-model peer, NOT a reviewer) to produce an INDEPENDENT design proposal for a problem/spec artifact. Report-only — writes the peer proposal to <project>/docs/reviews/peer-<slug>-<date>.md and never edits the artifact. Complements WF5 (which critiques) — this one PROPOSES. Invoke with /rawgentic:peer-consult followed by a problem-artifact path. Requires the Codex CLI installed and authenticated.
argument-hint: Problem/spec artifact path (e.g., "docs/design/feature.md")
---

# WF13: Peer Consult Workflow

<role>
You are the WF13 orchestrator. You engage Codex (a different model than yourself) as an independent PEER senior engineer — not a reviewer — and ask it to produce its OWN design proposal for a problem/spec artifact, without seeing or critiquing any proposal of yours. You are STRICTLY report-only: you never edit the source artifact and you never auto-apply the peer's proposal — the user (or the calling workflow) decides what to do with it. All real logic lives in `hooks/adversarial_review_lib.py`; you are a thin orchestrator over it.
</role>

<constants>
SUPPORTED_ARTIFACT_TYPES: any problem/spec text (no type hint needed — consult mode has no per-type prompt variants)
PEER: Codex CLI (independent different-model peer designer; egress to OpenAI; NOT a reviewer)
OUTPUT: <activeProject.path>/docs/reviews/peer-<slug>-<YYYY-MM-DD>.md  (report-only)
ENGINE: hooks/adversarial_review_lib.py
ENV (all optional, frozen at lib import — shared with WF5/adversarial-review):
  RAWGENTIC_ADV_REVIEW_MAX_BYTES   (default 200000) — artifact size cap; over-cap truncates + warns
  RAWGENTIC_ADV_REVIEW_TIMEOUT     (default 600)    — Codex invocation timeout (seconds)
  RAWGENTIC_ADV_REVIEW_MAX_RETRIES (default 1)      — retries on transient Codex failure
  RAWGENTIC_ADV_REVIEW_BLOCK_SECRETS (default off)  — when set, block egress if secrets detected
  RAWGENTIC_ADV_REVIEW_EFFORT      (default high)   — Codex reasoning effort (low|medium|high)
  RAWGENTIC_ADV_REVIEW_MODEL       (default unset)  — override the peer model (`codex exec -m`); unset = inherit Codex/config default (do NOT hardcode a model id)
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
WF13 ALWAYS terminates after presenting the peer proposal. It is report-only: it does NOT edit the artifact, does NOT create issues, and does NOT auto-transition to any other workflow. WF13 terminates ONLY after the completion-gate passes. All steps must have markers in session notes.
</termination-rule>

<data-handling>
This skill transmits the artifact's TEXT to OpenAI (Codex) for an independent peer design proposal — the artifact leaves the machine. This is **warn-only**: the skill prints a one-time egress notice before invoking Codex and proceeds. The engine additionally scans the artifact for obvious secrets (API keys, passwords, tokens, private keys) and, if any are found, names the detected categories in the notice. To make secret detection blocking instead of advisory, set `RAWGENTIC_ADV_REVIEW_BLOCK_SECRETS=1`. Peer reports are written locally to `<project>/docs/reviews/` and never uploaded anywhere.
</data-handling>

<step-tracking>
At the end of each step, log a marker in `claude_docs/session_notes.md`:
`### WF13 Step X: <Name> — DONE (<key detail>)`
This enables workflow resumption if context is lost.
</step-tracking>

---

## Step 1: Load Config and Validate Artifact

### Instructions

1. **Execute `<config-loading>`** to resolve the active project and its absolute path (`PROJECT_ROOT = <activeProject.path>`). Log the resolved project and repo in session notes.
2. Take the user argument as the problem/spec artifact path. Consult mode has no type hint — the same peer prompt is used for every artifact.
3. Validate the artifact:
   - The path must resolve to a file **under** `PROJECT_ROOT` (the engine enforces this — traversal/absolute escape is rejected). If it is outside the project, STOP and tell the user the artifact must live inside the active project.
   - If the file does not exist, STOP: "Artifact not found: `<path>`."
   - Artifacts larger than `RAWGENTIC_ADV_REVIEW_MAX_BYTES` are truncated (with a warning); note this to the user.
4. Log artifact path and size in session notes.

### Output

```
WF13 Peer Consult
=================
Project:  <name>
Artifact: <path>
Size:     <bytes> (cap <MAX_BYTES>)
```

### Failure Modes
- Artifact outside the project root → STOP (engine raises ArtifactError).
- File not found → STOP, ask for a correct path.

---

## Step 2: Prerequisite Gate (Codex CLI)

### Instructions

1. Check the Codex prerequisite via the engine:
   ```bash
   python3 hooks/adversarial_review_lib.py prereq [--headless]
   ```
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

1. Print the egress notice (warn-only): the artifact text will be sent to OpenAI (Codex) as the peer's problem statement. The engine scans for obvious secrets; if any are detected, the notice names the categories.
   ```bash
   python3 -c "import sys; sys.path.insert(0,'hooks'); from adversarial_review_lib import read_artifact, scan_for_secrets, egress_warning; t,_=read_artifact('<artifact>','<PROJECT_ROOT>'); print(egress_warning(scan_for_secrets(t)))"
   ```
2. This is **warn-only** — proceed after printing. If `RAWGENTIC_ADV_REVIEW_BLOCK_SECRETS=1` is set, the engine will refuse egress in Step 4 when secrets are present (status `error`); surface that to the user.

### Output
The egress warning text (and any detected secret categories).

---

## Step 4: Invoke Codex Peer Consult

### Instructions

1. Run the consult via the engine CLI (fail-closed; no live Codex needed in tests):
   ```bash
   OUT="$(mktemp)"
   python3 hooks/adversarial_review_lib.py consult \
     --artifact "<artifact>" \
     --project-root "<PROJECT_ROOT>" \
     --out "$OUT" \
     --date "$(date -u +%Y-%m-%d)" \
     [--headless]
   ```
2. **Interpret the exit code by its EXIT CODE ONLY — never by whether `$OUT` or the report file exists** (a failed run still writes an empty-proposal marker to `$OUT` for callers that read-gate on the file; the exit code is the sole success/failure signal):
   - `0` → success; the path of the written peer report is printed on stdout.
   - `2` → prerequisite failure (not installed / unauthenticated). STOP (should have been caught in Step 2).
   - `3` → Codex error or timeout. STOP and report; **do not** fabricate a proposal.
   - `4` → Codex output could not be parsed/validated. STOP and report.
3. On any non-zero exit, the consult did NOT succeed — report the failure to the user. Never present a partial or invented proposal as a completed consult.

### Output
The path to the generated peer report (on success) or the failure reason.

### Failure Modes
- Exit 3 (timeout/error) → report and stop; suggest retrying or checking Codex status.
- Exit 4 (parse error) → report; the artifact may be too large or Codex returned unexpected output.

---

## Step 5: Present Peer Proposal

### Instructions

1. Read the generated report at `<PROJECT_ROOT>/docs/reviews/peer-<slug>-<date>.md` (the path printed by Step 4 on success).
2. Present a concise summary to the user: the peer's approach, key decisions, and risks.
3. Print the absolute report path and (if known) the Codex invocation latency.
4. State clearly that this is **report-only**: the proposal is one independent peer's opinion, advisory only, and the artifact was not modified. Do NOT prompt to apply the proposal — that is the user's (or the calling workflow's) decision. Do NOT frame it as a review or a set of findings — it is a proposal from a peer, not a reviewer.
5. Log the report path in session notes.

### Output

```
Peer Consult Complete (report-only)
====================================
Report: <absolute path>

Approach: <one-line summary>
Key decisions: N
Risks: M

The artifact was NOT modified. This is one peer's independent proposal, not a review — synthesize at your discretion.
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
Before declaring WF13 complete, verify ALL of the following. Print the checklist with pass/fail for each item:

1. [ ] Step markers logged for ALL executed steps in session notes
2. [ ] Artifact validated (exists, under project root)
3. [ ] Codex prerequisite satisfied (installed + authenticated)
4. [ ] Egress notice printed (warn-only)
5. [ ] Codex consult invoked; exit code interpreted (fail-closed on non-zero, never gated on file existence)
6. [ ] On success: peer report written to <project>/docs/reviews/ and presented
7. [ ] Artifact NOT modified (report-only invariant)

If ANY item fails, complete it before declaring "WF13 complete."
You may NOT output "WF13 complete" until all items pass.
</completion-gate>
