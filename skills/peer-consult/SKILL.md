---
name: rawgentic:peer-consult
description: WF13 — Engage a different-model peer senior engineer (NOT a reviewer) to produce an INDEPENDENT design proposal for a problem/spec artifact. Selectable backend (#403) — `gpt` (Codex CLI, default), `glm` (Zhipu GLM via the zhipuai SDK), or `both` (two independent peer proposals). Report-only — writes the peer proposal to <project>/docs/reviews/peer-<slug>-<date>.md (glm: peer-<slug>-<date>-glm.md) and never edits the artifact. Complements WF5 (which critiques) — this one PROPOSES. Invoke with /rawgentic:peer-consult followed by a problem-artifact path. The gpt backend requires the Codex CLI installed and authenticated; glm requires `pip install "zhipuai>=2.1.5"` and ZHIPUAI_API_KEY.
argument-hint: Problem/spec artifact path (e.g., "docs/design/feature.md") with optional --backend (gpt|glm|both)
---

# WF13: Peer Consult Workflow

<role>
You are the WF13 orchestrator. You engage the selected backend — Codex (gpt, default), Zhipu GLM (glm), or both — always a different model than yourself, as an independent PEER senior engineer — not a reviewer — and ask it to produce its OWN design proposal for a problem/spec artifact, without seeing or critiquing any proposal of yours. You are STRICTLY report-only: you never edit the source artifact and you never auto-apply the peer's proposal — the user (or the calling workflow) decides what to do with it. All real logic lives in `hooks/adversarial_review_lib.py`; you are a thin orchestrator over it.
</role>

<constants>
SUPPORTED_ARTIFACT_TYPES: any problem/spec text (no type hint needed — consult mode has no per-type prompt variants)
PEER BACKENDS (#403): gpt (Codex CLI; egress to OpenAI — the default), glm (Zhipu
  GLM via the zhipuai SDK, sync-streaming; egress to z.ai/Zhipu — a distinct
  provider and jurisdiction), both (two INDEPENDENT peer proposals). NOT a reviewer.
BACKEND RESOLUTION: an explicit `--backend` in the invocation argument wins;
  otherwise the project's `peerConsult.backend` config field (engine `backend`
  subcommand with --key peerConsult); absent → gpt. A present-but-INVALID config
  value refuses (exit 2) — never silently laundered into gpt.
OUTPUT: <activeProject.path>/docs/reviews/peer-<slug>-<YYYY-MM-DD>.md  (report-only)
  glm proposal report: peer-<slug>-<YYYY-MM-DD>-glm.md (suffix AFTER the date; both
  mode writes both). Under `both` the structured --out gets a -glm sibling too.
EXIT CODES: 0 success (ALL selected backends) · 2 prereq/config · 3 error/timeout ·
  4 parse · 5 PARTIAL (both mode only: present the successful proposal, name the
  failed backend, do NOT stop)
ENGINE: hooks/adversarial_review_lib.py
ENV (all optional, frozen at lib import — shared with WF5/adversarial-review):
  RAWGENTIC_ADV_REVIEW_MAX_BYTES   (default 200000) — artifact size cap; over-cap truncates + warns
  RAWGENTIC_ADV_REVIEW_TIMEOUT     (default 600)    — Codex invocation timeout (seconds)
  RAWGENTIC_ADV_REVIEW_MAX_RETRIES (default 1)      — retries on transient Codex failure
  RAWGENTIC_ADV_REVIEW_BLOCK_SECRETS (default off)  — when set, block egress if secrets detected
  RAWGENTIC_ADV_REVIEW_EFFORT      (default high)   — Codex reasoning effort (low|medium|high)
  RAWGENTIC_ADV_REVIEW_MODEL       (default unset)  — override the gpt peer model (`codex exec -m`); unset = inherit Codex/config default (do NOT hardcode a model id)
  RAWGENTIC_ADV_REVIEW_GLM_MODEL   (default glm-5.2) — glm model slug
  ZHIPUAI_API_KEY / ZHIPU_API_KEY / GLM_API_KEY (read at call time) — glm credential; a Coding Plan subscription key works
  ZHIPUAI_BASE_URL / GLM_JUDGE_BASE_URL (default https://api.z.ai/api/coding/paas/v4) — glm endpoint; https only, no userinfo/query/fragment
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

<termination-rule>
WF13 ALWAYS terminates after presenting the peer proposal. It is report-only: it does NOT edit the artifact, does NOT create issues, and does NOT auto-transition to any other workflow. WF13 terminates ONLY after the completion-gate passes. All steps must have markers in session notes.
</termination-rule>

<data-handling>
This skill transmits the artifact's TEXT to the selected backend's provider for an independent peer design proposal — the artifact leaves the machine. Destination by backend (#403): gpt → OpenAI (Codex); glm → z.ai/Zhipu at the effective resolved endpoint (named, sanitized, in the notice); both → both destinations. This is **warn-only**: the skill prints a one-time egress notice before invoking the backend(s) and proceeds. The engine additionally scans the artifact for obvious secrets (API keys, passwords, tokens, private keys) and, if any are found, names the detected categories in the notice. To make secret detection blocking instead of advisory, set `RAWGENTIC_ADV_REVIEW_BLOCK_SECRETS=1`. Peer reports are written locally to `<project>/docs/reviews/` and never uploaded anywhere.
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
2. Take the user argument as the problem/spec artifact path and an optional `--backend`. Consult mode has no type hint — the same peer prompt is used for every artifact.
2b. **Resolve the backend (#403).** An explicit `--backend gpt|glm|both` wins. Otherwise:
   ```bash
   python3 hooks/adversarial_review_lib.py backend \
     --workspace .rawgentic_workspace.json --project <name> --key peerConsult
   ```
   Exit 0 → stdout is the backend (absent/disabled → `gpt`). **Exit 2 → present-but-invalid config value: STOP and relay stderr — NEVER fall back to gpt, never default an empty stdout capture.** Carry the resolved backend as a literal into Steps 2–4.
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

## Step 2: Prerequisite Gate (selected backend)

### Instructions

1. Check the SELECTED backend's prerequisite via the engine:
   ```bash
   python3 hooks/adversarial_review_lib.py prereq --backend <resolved backend> [--headless]
   ```
2. If the prerequisite check fails (exit 2), **STOP** and print the message verbatim:
   - gpt — install: `curl -fsSL https://codex.openai.com/install.sh | bash`; authenticate: `codex login` (headless/CI: `printenv OPENAI_API_KEY | codex login --with-api-key`)
   - glm — install: `pip install "zhipuai>=2.1.5"`; credential: export `ZHIPUAI_API_KEY` (a z.ai Coding Plan subscription key works)
3. **`both` is DEGRADE-AND-WARN (#403):** passes when ≥1 backend is ready; the message names both results; an unready backend degrades the run (exit 5), it does not block. Zero-ready fails.
4. **Headless note:** ChatGPT OAuth login is interactive-only — headless gpt auth failure is a terminal ERROR. The glm credential is an env var; same export instruction headless.

### Output
The prereq message (per-backend detail under `both`) or the verbatim instructions on failure.

### Failure Modes
- Selected backend not ready → STOP with instructions (headless: ERROR).
- `both` with zero ready → STOP with both messages.

---

## Step 3: Egress Notice (Warn-Only)

### Instructions

1. Print the egress notice (warn-only): the artifact text will be sent to the selected backend's provider as the peer's problem statement. The engine scans for obvious secrets; if any are detected, the notice names the categories.
   ```bash
   python3 -c "import sys; sys.path.insert(0,'hooks'); from adversarial_review_lib import read_artifact, scan_for_secrets, egress_warning; t,_=read_artifact('<artifact>','<PROJECT_ROOT>'); print(egress_warning(scan_for_secrets(t), backend='<resolved backend>'))"
   ```
2. This is **warn-only** — proceed after printing. If `RAWGENTIC_ADV_REVIEW_BLOCK_SECRETS=1` is set, the engine will refuse egress in Step 4 when secrets are present (status `error`); surface that to the user.

### Output
The egress warning text (and any detected secret categories).

---

## Step 4: Invoke Peer Consult (selected backend)

### Instructions

1. Run the consult via the engine CLI (fail-closed; no live provider needed in tests):
   ```bash
   OUT="$(mktemp)"
   python3 hooks/adversarial_review_lib.py consult \
     --artifact "<artifact>" \
     --project-root "<PROJECT_ROOT>" \
     --out "$OUT" \
     --date "$(date -u +%Y-%m-%d)" \
     --backend <resolved backend> \
     [--headless]
   ```
   Under `both`, gpt writes the exact `$OUT` and glm writes a `-glm` sibling of it; both proposal reports land in docs/reviews/.
2. **Interpret the run by its EXIT CODE and (under `both`) the per-backend stdout status lines — never by whether `$OUT` or the report file exists** (a failed run still writes an empty-proposal marker to its out file for callers that read-gate on the file):
   - `0` → success (ALL selected backends); single mode prints the report path; `both` prints `gpt: <path>` / `glm: <path>` — the authoritative manifest.
   - `2` → prerequisite/config failure. STOP (should have been caught in Steps 1/2).
   - `3` → backend error or timeout. STOP and report; **do not** fabricate a proposal.
   - `4` → backend output could not be parsed/validated. STOP and report.
   - **`5` → PARTIAL (both mode only): present the successful backend's proposal from the stdout manifest, name the failed backend from the stderr FAILED line, do NOT stop.** Never occurs in single mode.
3. On exit 2/3/4, the consult did NOT succeed — report the failure. Never present a partial or invented proposal as a completed consult. (Exit 5: the successful peer's proposal IS complete; the degradation is named.)

### Output
The path(s) to the generated peer report(s) or the failure reason.

### Failure Modes
- Exit 3 (timeout/error) → report and stop; suggest retrying or checking the backend's status.
- Exit 4 (parse error) → report; the artifact may be too large or the backend returned unexpected output.
- Exit 5 (partial, both mode) → present the successful proposal, name the failure — not a stop.

---

## Step 5: Present Peer Proposal

### Instructions

1. Read the generated report(s) — single mode: the path printed by Step 4; `both`: BOTH paths from the stdout manifest. Two proposals stay INDEPENDENT — present them side by side; synthesis is the caller's job, never a merge here.
2. Present a concise summary to the user: each peer's approach, key decisions, and risks (backend named under `both`).
3. Print the absolute report path(s) and (if known) the invocation latency.
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
3. [ ] Selected backend's prerequisite satisfied (gpt: codex; glm: zhipuai>=2.1.5 + key; both: >=1 ready, degradation warned)
4. [ ] Egress notice printed (warn-only)
5. [ ] Consult invoked with the resolved --backend; exit code interpreted (fail-closed on 2/3/4; exit 5 = both-mode partial presented with failure named; never gated on file existence)
6. [ ] On success: peer report(s) written to <project>/docs/reviews/ and presented (both under `both`)
7. [ ] Artifact NOT modified (report-only invariant)

If ANY item fails, complete it before declaring "WF13 complete."
You may NOT output "WF13 complete" until all items pass.
</completion-gate>
