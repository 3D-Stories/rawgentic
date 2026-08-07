---
name: adversarial-review
description: 'WF5 — Adversarially review a TEXT artifact (design, spec, implementation plan, PRD, ADR, RFC, README) using an independent DIFFERENT-MODEL reviewer. Selectable backend (#403) — `gpt` (Codex CLI, the default), `glm` (Zhipu GLM via the zhipuai SDK), or `both` (two independent reviews, two reports). Report-only — writes a severity-ranked findings report to <project>/docs/reviews/ and NEVER edits the artifact. Also reviews code DIFFS via the `diff` artifact type (refutation lens, report-only) — this complements same-model self-review (the in-repo quality-bar rubric) with a cross-model second opinion on planning artifacts. Invoke with /rawgentic:adversarial-review followed by an artifact path.'
argument-hint: Artifact path (e.g., "docs/design/feature.md") with optional type hint (design|spec|plan|prd|adr|rfc|readme|diff) and optional --backend (gpt|glm|both)
---

# WF5: Adversarial Review Workflow

<role>
You are the WF5 orchestrator. You run an independent, cross-model adversarial review of a single TEXT artifact using the selected backend — the Codex CLI (gpt, default), Zhipu GLM via the zhipuai SDK (glm), or both — always a different model than yourself, then write a severity-ranked findings report (one per backend under `both`). You are STRICTLY report-only: you never edit the reviewed artifact and you never auto-apply findings — the user (or the calling workflow) decides what to do with them. The model invocation runs through the ONE review entry point, `hooks/review_runner.py` (D179); config resolution, prerequisite checks, and report rendering use `hooks/adversarial_review_lib.py`'s kept pieces. You are a thin orchestrator over both.
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
ENGINE: hooks/review_runner.py (invocation, D179) + hooks/adversarial_review_lib.py
  (config/prereq/render — the kept pieces)
CLI: `review-artifact --artifact <f> --type <t> --author-model <id> --reviewer <m>
  [--backend gpt|glm] --out <result.json> --project-root <root>` — the result JSON
  IS the machine-readable findings sidecar for embedded callers. `both` = two
  independent runner invocations (one per backend), two results, two reports.
EXIT CODES (per runner invocation): 0 success (result JSON carries `diagnostic`;
  standalone WF5 runs are tokenless and always diagnostic — report-only anyway) ·
  2 refused (validation/identity/config — no egress) · 3 terminal backend failure ·
  4 empty/invalid backend output. Under `both`, one success + one failure = PARTIAL:
  present the successful report, name the failed backend, do NOT stop.
ENV (all optional):
  RAWGENTIC_ADV_REVIEW_MAX_BYTES   (default 200000) — artifact size cap; over-cap REFUSES (never truncate-and-continue)
  RAWGENTIC_ADV_REVIEW_TIMEOUT     (default 600)    — per-attempt invocation timeout (seconds), both backends
  RAWGENTIC_ADV_REVIEW_BLOCK_SECRETS (default off)  — when set, block egress if secrets detected (both backends)
  RAWGENTIC_ADV_REVIEW_EFFORT      (default high)   — reasoning effort (low|medium|high|xhigh), both backends
  RAWGENTIC_ADV_REVIEW_MODEL      (default unset)  — gpt reviewer model override (`codex exec -m`); unset = Codex/config default
  RAWGENTIC_ADV_REVIEW_GLM_MODEL   (default glm-5.2) — glm model slug
  ZHIPUAI_API_KEY / ZHIPU_API_KEY / GLM_API_KEY (read at call time) — glm credential; a Coding Plan subscription key works
  ZHIPUAI_BASE_URL / GLM_JUDGE_BASE_URL (default https://api.z.ai/api/coding/paas/v4) — glm endpoint; must be https with no userinfo/query/fragment

BACKEND PREREQUISITES (#909 — moved here from the frontmatter description, which
  is for triggering symptoms, not install instructions):
  gpt — the Codex CLI installed and authenticated
  glm — install: `pip install "zhipuai>=2.1.5"`; credential: export ZHIPUAI_API_KEY
        (a z.ai Coding Plan subscription key works)
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
`codex review`, which is git-diff-only with no `--output-schema`). The runner composes:
`codex exec -m <reviewer> --sandbox read-only --output-schema <schema> -o <out> -c model_reasoning_effort=<effort> --ephemeral --color never -c project_doc_max_bytes=0 -C <root> --skip-git-repo-check -`
- **`-m` is ALWAYS explicit** — reviewer identity is pinned, never inherited from the codex config; author==reviewer or an unresolvable reviewer REFUSES before any egress.
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
Step-entry state (#480, hook-emitted since #499): the PostToolUse hook (`hooks/step_state_post.py`) stamps later steps from step DONE markers and signature commands — but ONLY once the step-state pointer already names this session, which a DONE marker or an explicit write creates. It does NOT stamp unaided: a run that creates no pointer contributes no timing at all (#976 measured exactly that). The manual `python3 hooks/step_state.py write --project <project> --workflow wf5 --step <N> --step-title "<step name>" --session-id "$CLAUDE_CODE_SESSION_ID"` call is OPTIONAL here: this workflow cuts no branch and carries no issue number, so it produces no per-run timing to protect. Fail-open either way (never gates; any failure is ignored and the step proceeds).
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
2c. **Resolve identities (the runner refuses without them).** AUTHOR_MODEL = your own model id, verbatim (the model running this session). REVIEWER = the current default in `shared/blocks/model-routing-resolve.md` (`gpt-5.6-sol` for gpt; glm resolves `glm-5.2` automatically) unless the user named one. The runner refuses author==reviewer — an artifact you authored in this session cannot be reviewed by your own model.
3. Validate the artifact:
   - The path must resolve to a file **under** `PROJECT_ROOT` (the engine enforces this — traversal/absolute escape is rejected). If it is outside the project, STOP and tell the user the artifact must live inside the active project.
   - If the file does not exist, STOP: "Artifact not found: `<path>`."
   - Artifacts larger than `RAWGENTIC_ADV_REVIEW_MAX_BYTES` are REFUSED by the runner (oversize is never truncated-and-continued); tell the user to split the artifact or raise the env cap deliberately.
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
   python3 hooks/adversarial_review_lib.py prereq --backend <resolved backend>
   ```
2. If the prerequisite check fails (exit 2), **STOP** and print the message verbatim. It tells the user how to install and authenticate:
   - gpt — install: `npm install -g @openai/codex`; authenticate: `codex login` (headless/CI: `printenv OPENAI_API_KEY | codex login --with-api-key`)
   - glm — install: `pip install "zhipuai>=2.1.5"`; credential: export `ZHIPUAI_API_KEY` (a z.ai Coding Plan subscription key works with the default endpoint)
3. **`both` is DEGRADE-AND-WARN (#403):** the check passes when AT LEAST ONE backend is ready; the message names BOTH backends' results, and an unready backend is a loud warning (the run will degrade to the ready backend, exit 5). Only zero-ready fails. Surface the warning to the user, then proceed.
4. **Non-interactive note:** ChatGPT OAuth login is interactive-only. If the gpt prereq fails on authentication and no user is present to log in, report it as a terminal failure — do not wait for an interactive login. The glm credential is an env var (no interactive step).

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

1. **Resolve the task class FIRST (#761) — required on this path, never guessed and never hand-defaulted.** Read it from the snapshot the run already committed to:
   ```bash
   # an issue IS in scope (the WF2/WF3 embedded case) — read THAT issue's snapshot
   python3 hooks/task_class_lib.py read --issue "<ISSUE>" --project-root "<PROJECT_ROOT>"
   # standalone artifact review, NO issue in scope — omit --issue; returns the project default
   python3 hooks/task_class_lib.py read --project-root "<PROJECT_ROOT>"
   ```
   Take `task_class` from the JSON on stdout (rc 1 = unreadable/invalid snapshot: STOP and relay it — never re-resolve and never substitute a default). **When an issue is in scope you MUST pass BOTH `--task-class` and `--issue` below.** Passing `--issue` without `--task-class` is REFUSED (exit 2, `invalid_input`) by design: an issue-scoped review that quietly fell back to the project default would show the reviewer a class the issue never set, with no failure and no diagnostic.
2. Run the review through the runner (fail-closed; the codex binary is PATH-stubbed in tests):
   ```bash
   python3 hooks/review_runner.py review-artifact \
     --artifact "<artifact>" \
     --type "<resolved type>" \
     --author-model "<AUTHOR_MODEL>" \
     --reviewer "<REVIEWER>" \
     --backend <gpt|glm> \
     --task-class "<TASK_CLASS>" \
     --issue "<ISSUE>" \
     --out "<PROJECT_ROOT>/.rawgentic-wf5-result.json" \
     --project-root "<PROJECT_ROOT>"
   ```
   **`--task-class` is ALWAYS passed** — with an issue in scope or without one. When no issue is in scope, drop ONLY the `--issue` line and still pass the class `task_class_lib.py read` returned (the project's `defaultTaskClass`, else `production`). Dropping both would throw away the value you just resolved and render `production` even in a project that configured `internal` or `disposable`, making the documented standalone config-default path unreachable. Passing `--issue` WITHOUT `--task-class` is refused (exit 2).
   `--reviewer` is backend-specific: pass the pinned id for `gpt` (`gpt-5.6-sol` per the `<model-routing-resolve>` contract); for `glm`, OMIT the flag — the runner resolves `glm-5.2` itself, and that omission is the sanctioned form, not an oversight. Under `both`, run TWO independent invocations — one `--backend gpt` (with `--reviewer gpt-5.6-sol`) and one `--backend glm` (no `--reviewer`) — with distinct `--out` paths. The result JSON is the machine-readable findings sidecar for embedded callers: `{status, diagnostic, reviewer_model, backend, input_sha256, head_sha, timing, findings, summary, error_class}`. Standalone WF5 runs are tokenless, so `diagnostic` is `true` — irrelevant here, because WF5 is report-only and authorizes nothing.
3. Interpret the exit code per invocation:
   - `0` → success; the result file holds validated findings + summary.
   - `2` → refused (identity/validation/oversize/config — no egress happened). STOP and relay `error_detail`.
   - `3` → terminal backend failure (`error_class`: org_quota | account_quota | transport | unclassified). STOP and report; **do not** fabricate findings, and do not add your own retry loop — the runner already applied the #857 policy.
   - `4` → empty/invalid backend output after the runner's bounded retry. STOP and report.
   - **`both` PARTIAL: one invocation succeeded, one failed → do NOT stop — render and present the successful backend's report and name the failed backend with its `error_class`.**
4. On a single-backend 2/3/4, the review did NOT succeed — report the failure to the user. Never present partial or invented findings as a completed review.

### Output
The result JSON path(s) (one per backend) or the failure reason.

### Failure Modes
- Exit 3 (backend failure) → report and stop; the result's `error_class` says whether retrying later can help (quota vs transport vs unclassified).
- Exit 4 (empty/invalid output) → report; the backend returned unusable output after one bounded retry.
- `both` with one failure → present the successful report, name the failure — not a stop.

---

## Step 5: Render and Present Report(s)

### Instructions

0. Render each successful result JSON into the standard report file (one per backend):
   ```bash
   python3 - <<'EOF'
   import json, sys
   sys.path.insert(0, 'hooks')
   from adversarial_review_lib import render_report_md, review_report_path
   from atomic_write_lib import atomic_write_text
   res = json.load(open("<result.json>"))
   meta = {"artifact": "<artifact>", "artifact_type": "<resolved type>",
           "date": "<YYYY-MM-DD>", "model": res.get("reviewer_model") or "",
           "backend": res.get("backend", "gpt"), "summary": res.get("summary", ""),
           "secrets": res.get("secrets_detected") or []}
   path = review_report_path("<PROJECT_ROOT>", "<artifact>", "<YYYY-MM-DD>",
                             backend=res.get("backend", "gpt"))
   atomic_write_text(path, render_report_md(list(res.get("findings") or []), meta))
   print(path)
   EOF
   ```
1. Read the rendered report(s) — single mode: `<PROJECT_ROOT>/docs/reviews/<slug>-<date>.md` (glm: `<slug>-<date>-glm.md`); `both`: BOTH files. Under both mode the two reviews are INDEPENDENT — present them side by side (per-backend finding counts), never merged (attribution is the point of a cross-model pass).
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
5. [ ] Review invoked through `hooks/review_runner.py` with the resolved backend, the backend-appropriate identity (`--reviewer gpt-5.6-sol` for gpt; flag omitted for glm — the runner resolves `glm-5.2`), `--author-model`, and the task class resolved via `task_class_lib.py read` (`--task-class` is ALWAYS passed; `--issue` is additionally passed whenever an issue is in scope, and ONLY `--issue` is omitted when none is); exit code interpreted (fail-closed on 2/3/4; both-mode partial presented with the failure named)
6. [ ] On success: report(s) written to <project>/docs/reviews/ and presented (both files under `both`)
7. [ ] Artifact NOT modified (report-only invariant)

If ANY item fails, complete it before declaring "WF5 complete."
You may NOT output "WF5 complete" until all items pass.
</completion-gate>
