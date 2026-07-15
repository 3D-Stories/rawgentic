---
name: rawgentic:adversarial-review
description: WF5 — Adversarially review a TEXT artifact (design, spec, implementation plan, PRD, ADR, RFC, README) using an independent DIFFERENT-MODEL reviewer. Selectable backend (#403) — `gpt` (Codex CLI, the default), `glm` (Zhipu GLM via the zhipuai SDK), or `both` (two independent reviews, two reports). Report-only — writes a severity-ranked findings report to <project>/docs/reviews/ and NEVER edits the artifact. Also reviews code DIFFS via the `diff` artifact type (refutation lens, report-only) — this complements same-model self-review (the in-repo quality-bar rubric) with a cross-model second opinion on planning artifacts. Invoke with /rawgentic:adversarial-review followed by an artifact path. The gpt backend requires the Codex CLI installed and authenticated; the glm backend requires `pip install "zhipuai>=2.1.5"` and ZHIPUAI_API_KEY.
argument-hint: Artifact path (e.g., "docs/design/feature.md") with optional type hint (design|spec|plan|prd|adr|rfc|readme|diff) and optional --backend (gpt|glm|both)
---

# WF5: Adversarial Review Workflow

<role>
You are the WF5 orchestrator. You run an independent, cross-model adversarial review of a single TEXT artifact using the selected backend — the Codex CLI (gpt, default), Zhipu GLM via the zhipuai SDK (glm), or both — always a different model than yourself, then write a severity-ranked findings report (one per backend under `both`). You are STRICTLY report-only: you never edit the reviewed artifact and you never auto-apply findings — the user (or the calling workflow) decides what to do with them. All real logic lives in `hooks/adversarial_review_lib.py`; you are a thin orchestrator over it.
</role>

<constants>
SUPPORTED_ARTIFACT_TYPES: design, spec, plan, prd, adr, rfc, readme, generic, diff
FINDING_SEVERITIES: Critical, High, Medium, Low
BACKENDS (#403): gpt (Codex CLI; egress to OpenAI — the default), glm (Zhipu GLM
  via the zhipuai SDK, sync-streaming; egress to z.ai/Zhipu — a distinct provider
  and jurisdiction), both (run each independently; two reports)
BACKEND RESOLUTION: an explicit `--backend` in the invocation argument wins;
  otherwise the project's `adversarialReview.backend` config field (read via the
  engine's `backend` subcommand); absent → gpt. A present-but-INVALID config value
  refuses (exit 2) — it is never silently laundered into gpt.
OUTPUT: <activeProject.path>/docs/reviews/<slug>-<YYYY-MM-DD>.md  (report-only)
  glm report: <slug>-<YYYY-MM-DD>-glm.md (suffix AFTER the date; both mode writes both files)
ENGINE: hooks/adversarial_review_lib.py
CLI: `review --backend {gpt,glm,both} --findings-json <path>` (both optional; the
  sidecar is written only on success, after the report; path must resolve under the
  project root. Under `both`, gpt writes the exact sidecar path — byte-compatible —
  and glm writes a `-glm` sibling with per-finding `backend` tags.)
EXIT CODES: 0 success (ALL selected backends) · 2 prereq/config · 3 error/timeout ·
  4 parse · 5 PARTIAL (both mode only: ≥1 backend succeeded, ≥1 failed — present
  the successful report(s), name the failed backend, do NOT stop)
ENV (all optional, frozen at lib import unless noted):
  RAWGENTIC_ADV_REVIEW_MAX_BYTES   (default 200000) — artifact size cap; over-cap truncates + warns
  RAWGENTIC_ADV_REVIEW_TIMEOUT     (default 600)    — per-attempt invocation timeout (seconds), both backends
  RAWGENTIC_ADV_REVIEW_MAX_RETRIES (default 1)      — retries on transient failure, both backends
  RAWGENTIC_ADV_REVIEW_BLOCK_SECRETS (default off)  — when set, block egress if secrets detected (both backends)
  RAWGENTIC_ADV_REVIEW_EFFORT      (default high)   — reasoning effort (low|medium|high), both backends
  RAWGENTIC_ADV_REVIEW_MODEL      (default unset)  — gpt reviewer model override (`codex exec -m`); unset = Codex/config default
  RAWGENTIC_ADV_REVIEW_GLM_MODEL   (default glm-5.2) — glm model slug
  ZHIPUAI_API_KEY / ZHIPU_API_KEY / GLM_API_KEY (read at call time) — glm credential; a Coding Plan subscription key works
  ZHIPUAI_BASE_URL / GLM_JUDGE_BASE_URL (default https://api.z.ai/api/coding/paas/v4) — glm endpoint; must be https with no userinfo/query/fragment
</constants>

<reviewer-invocation>
GLM backend (#403): the engine calls the zhipuai SDK's sync chat completion —
`chat.completions.create(model=glm-5.2, response_format=json_object,
thinking=enabled, extra_body.reasoning_effort, stream=True)` — STREAMED (a
non-streamed thinking call stalls and dies; measured live) with a two-layer
timeout (SDK read timeout + per-chunk wall-clock deadline). GLM json_object has
no strict-schema enforcement, so the findings schema rides in the prompt and the
engine's tolerant validators are the gate; the same nonce-fenced prompt-injection
defense applies. No shell, no subprocess — the SDK talks https directly.

The gpt backend invokes Codex as a one-shot, tools-OFF, structured-JSON reviewer (NOT
`codex review`, which is git-diff-only with no `--output-schema`). The argv is:
`codex exec [-m <model>] --output-schema <schema> -o <out> -c model_reasoning_effort=<effort> --ephemeral --color never -c project_doc_max_bytes=0 -s read-only -C <root> --skip-git-repo-check -`
- **effort pinned** (high): gpt-5.5 defaults to medium; deep critique benefits from high.
- **--ephemeral**: the prompt inlines the full (possibly proprietary) artifact; this keeps it out of CODEX_HOME session history.
- **project_doc_max_bytes=0**: suppresses the reviewed project's AGENTS.md so the cross-model reviewer stays independent of the project's own conventions.
- **--color never**: keeps the parsed output byte-clean.
- The prompt itself FORBIDS the model from running any shell/tool/file/network op (review purely from inlined text) — required where the Codex bubblewrap sandbox is unavailable, and a defense against artifact-embedded prompt injection. Each finding must carry a verbatim `evidence` quote (grounding) and a `confidence`; severity is governed by an explicit rubric to curb inflation. Critical/High findings additionally carry a `loopback_class` tag (`spec-tightening` | `design-flaw`, unsure→design-flaw — #407) that WF2's Step-4 fold consumes; absent/off-vocab values fail closed to the full design path.
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
WF5 ALWAYS terminates after presenting the report. It is report-only: it does NOT edit the artifact, does NOT create issues, and does NOT auto-transition to any other workflow. WF5 terminates ONLY after the completion-gate passes. All steps must have markers in session notes.
</termination-rule>

<ambiguity-circuit-breaker>
Per the shared invariant: STOP and ask the user when findings are ambiguous, conflicting, or require judgment not present in the artifact. This skill is report-only, so the circuit breaker manifests as: if Codex returns findings whose severity or applicability is genuinely unclear, surface them to the user with the ambiguity flagged rather than silently ranking them. The user (or, in embedded mode, the calling workflow) has final authority over every finding (P11).
</ambiguity-circuit-breaker>

<data-handling>
This skill transmits the artifact's TEXT to the selected backend's provider for an independent model review — the artifact leaves the machine. Destination by backend (#403): gpt → OpenAI (Codex); glm → z.ai / Zhipu at the EFFECTIVE resolved endpoint (named, sanitized scheme+host, in the notice) — a distinct provider and jurisdiction; both → both destinations. This is **warn-only**: the skill prints a one-time egress notice before invoking the backend(s) and proceeds. The engine additionally scans the artifact for obvious secrets (API keys, passwords, tokens, private keys) and, if any are found, names the detected categories in the notice. To make secret detection blocking instead of advisory, set `RAWGENTIC_ADV_REVIEW_BLOCK_SECRETS=1`. Findings reports are written locally to `<project>/docs/reviews/` and never uploaded anywhere. A `diff` artifact is raw source code — the highest secret density of any supported type, so the egress warning above and the `RAWGENTIC_ADV_REVIEW_BLOCK_SECRETS=1` hard-block matter most here. An agent-harness egress classifier may also block the Codex invocation entirely, independent of this skill's own warn-only policy — embedded callers (e.g. WF2 Step 11) must treat that as a failed review and continue non-blocking, while standalone runs surface the block to the user.
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
2. Parse the user argument into an artifact path, an optional type hint, and an optional `--backend`:
   - If the argument is a path to an existing file, use it.
   - If a type hint (one of SUPPORTED_ARTIFACT_TYPES) is given, record it; otherwise auto-detect from the filename (e.g. `*spec*` → spec, `*plan*` → plan, `*adr*` → adr, `README*` → readme, `*.patch`/`*.diff` → diff) and fall back to `generic`.
2b. **Resolve the backend (#403).** An explicit `--backend gpt|glm|both` in the argument wins. Otherwise read the project's config default:
   ```bash
   python3 hooks/adversarial_review_lib.py backend \
     --workspace .rawgentic_workspace.json --project <name> --key adversarialReview
   ```
   Exit 0 → stdout is the backend (absent/disabled config → `gpt`). **Exit 2 → the config carries a present-but-INVALID backend value: STOP and relay the stderr message — NEVER fall back to gpt** (a typo'd backend must not silently reroute the artifact to a different provider). Never default an empty stdout capture to gpt — branch on the exit code. Carry the resolved backend as a literal into Steps 2–4.
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

## Step 2: Prerequisite Gate (selected backend)

### Instructions

1. Check the SELECTED backend's prerequisite via the engine:
   ```bash
   python3 hooks/adversarial_review_lib.py prereq --backend <resolved backend> [--headless]
   ```
2. If the prerequisite check fails (exit 2), **STOP** and print the message verbatim. It tells the user how to install and authenticate:
   - gpt — install (standalone binary): `curl -fsSL https://codex.openai.com/install.sh | bash`; authenticate: `codex login` (headless/CI: `printenv OPENAI_API_KEY | codex login --with-api-key`)
   - glm — install: `pip install "zhipuai>=2.1.5"`; credential: export `ZHIPUAI_API_KEY` (a z.ai Coding Plan subscription key works with the default endpoint)
3. **`both` is DEGRADE-AND-WARN (#403):** the check passes when AT LEAST ONE backend is ready; the message names BOTH backends' results, and an unready backend is a loud warning (the run will degrade to the ready backend, exit 5). Only zero-ready fails. Surface the warning to the user, then proceed.
4. **Headless note:** ChatGPT OAuth login is interactive-only. If the session is headless (`RAWGENTIC_HEADLESS=1`) and the gpt prereq fails on authentication, this is a terminal ERROR — do not wait for an interactive login. The glm credential is an env var (no interactive step), so its headless message is the same export instruction.

### Output
The prereq message (per-backend detail under `both`), or the verbatim install/credential instructions on failure.

### Failure Modes
- Selected backend not ready (gpt/glm single mode) → STOP with instructions (headless: ERROR).
- `both` with zero backends ready → STOP with both messages.

---

## Step 3: Egress Notice (Warn-Only)

### Instructions

1. Print the egress notice (warn-only): the artifact text will be sent to the selected backend's provider — gpt → OpenAI (Codex); glm → z.ai/Zhipu at the effective endpoint; both → both. The engine scans for obvious secrets; if any are detected, the notice names the categories.
   ```bash
   python3 -c "import sys; sys.path.insert(0,'hooks'); from adversarial_review_lib import read_artifact, scan_for_secrets, egress_warning; t,_=read_artifact('<artifact>','<PROJECT_ROOT>'); print(egress_warning(scan_for_secrets(t), backend='<resolved backend>'))"
   ```
2. This is **warn-only** — proceed after printing. If `RAWGENTIC_ADV_REVIEW_BLOCK_SECRETS=1` is set, the engine will refuse egress in Step 4 when secrets are present (status `error`, on every backend); surface that to the user.

### Output
The egress warning text (destination(s) named, and any detected secret categories).

---

## Step 4: Invoke Adversarial Review (selected backend)

### Instructions

1. Run the review via the engine CLI (fail-closed; no live provider needed in tests):
   ```bash
   python3 hooks/adversarial_review_lib.py review \
     --artifact "<artifact>" \
     --type "<resolved type>" \
     --project-root "<PROJECT_ROOT>" \
     --date "$(date -u +%Y-%m-%d)" \
     --backend <resolved backend> \
     [--headless] \
     [--findings-json <path>] \
     [--dispositions <path> --issue <n>]
   ```
   Embedded callers (e.g. WF2 Step 11) may append `--findings-json <path>` to also receive a machine-readable sidecar of the findings; it is written only on success, after the report. Under `both`, gpt writes the exact sidecar path (byte-compatible) and glm writes a `-glm` sibling.
   Embedded pass-N callers (#393) may additionally append `--dispositions <path> --issue <n>` — a folded settled-dispositions JSONL (written by the WF2 orchestrator under the project root) rendered into a second nonce fence so the reviewer does not re-litigate settled findings. `--dispositions` REQUIRES `--issue` (exit `2` without it); every valid entry's `issue` field is cross-checked and a mismatch fails CLOSED with exit `6` BEFORE any backend dispatch (cross-issue contamination). Benign ledger failures (missing/unreadable file, corrupt lines) fail OPEN: the review still runs and stderr carries `ledger: degraded (<reason>, N lines skipped)` — embedded callers record that phrase in the gate marker; exit `6` maps to the loud-abort marker `failed (ledger integrity)`.
2. Interpret the exit code (the contract is fail-closed, with ONE both-mode carve-out):
   - `0` → success (ALL selected backends); single mode prints the report path on stdout; `both` prints per-backend status lines (`gpt: <path>` / `glm: <path>`) — the authoritative manifest.
   - `2` → prerequisite/config failure. STOP (should have been caught in Steps 1b/2).
   - `3` → backend error or timeout. STOP and report; **do not** fabricate findings.
   - `4` → backend output could not be parsed/validated. STOP and report.
   - **`5` → PARTIAL (both mode only): ≥1 backend succeeded, ≥1 failed. Do NOT stop — present the successful report(s) from the stdout manifest, name the failed backend from the stderr `FAILED` line, and continue.** Exit 5 never occurs in single-backend mode.
3. On exit 2/3/4, the review did NOT succeed — report the failure to the user. Never present partial or invented findings as a completed review. (Exit 5 is not that case: the successful backend's review DID complete and is presented as such, with the degradation named.)

### Output
The path(s) to the generated report(s) (from the stdout manifest under `both`) or the failure reason.

### Failure Modes
- Exit 3 (timeout/error) → report and stop; suggest retrying or checking the backend's status.
- Exit 4 (parse error) → report; the artifact may be too large or the backend returned unexpected output.
- Exit 5 (partial, both mode) → present the successful report, name the failure — not a stop.

---

## Step 5: Present Report(s)

### Instructions

1. Read the generated report(s) — single mode: `<PROJECT_ROOT>/docs/reviews/<slug>-<date>.md` (glm: `<slug>-<date>-glm.md`); `both`: BOTH files from the Step 4 stdout manifest. Under both mode the two reviews are INDEPENDENT — present them side by side (per-backend finding counts), never merged (attribution is the point of a cross-model pass).
2. Present a concise summary to the user: total findings per backend, per-severity counts, and the top Critical/High findings (with their backend named under both).
3. Print the absolute report path(s) and (if known) the invocation latency.
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
3. [ ] Selected backend's prerequisite satisfied (gpt: codex installed+authenticated; glm: zhipuai>=2.1.5 + key; both: >=1 ready, degradation warned)
4. [ ] Egress notice printed (warn-only)
5. [ ] Review invoked with the resolved --backend; exit code interpreted (fail-closed on 2/3/4; exit 5 = both-mode partial, presented with the failure named)
6. [ ] On success: report(s) written to <project>/docs/reviews/ and presented (both files under `both`)
7. [ ] Artifact NOT modified (report-only invariant)

If ANY item fails, complete it before declaring "WF5 complete."
You may NOT output "WF5 complete" until all items pass.
</completion-gate>
