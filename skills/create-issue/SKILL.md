---
name: rawgentic:create-issue
description: Open and file a NEW GitHub issue — a feature request or bug report — for the active rawgentic project. Use whenever the user wants to capture a desired feature/enhancement or an observed/reproducible bug as a tracked issue, however phrased ("open/log/raise/file an issue", "write up a bug report", "file a feature request", "put it on github", "track this", "users keep asking for X"), even when no repo is named. It targets the repo from the project config, checks for duplicates, conforms to the issue template, and verifies referenced code exists. Do NOT use to implement/fix/code the change itself, to list/search/read existing issues, to comment on or review a PR against an issue, or to edit issue-template files. Invoke with /create-issue followed by a description of the desired feature or observed bug.
argument-hint: Description of the feature to request or bug to report
---

# WF1: Issue Creation (lean)

<role>
You turn a raw feature/bug request into a clean GitHub issue for the active
rawgentic project: correct repo, no duplicate, template-conformant, and every
referenced component verified against the real code. You do the drafting yourself —
no judge panel, no loop-back. You NEVER auto-start implementation (WF2); issue
creation is where this workflow ends.
</role>

<why-this-is-lean>
An earlier version of this skill ran a 3-judge critique, an ambiguity circuit
breaker, loop-back iterations, and per-run memorization. Head-to-head evals showed
a current model already produces an equivalent issue without that machinery — at
~⅓ the time and tokens. So this version keeps only what adds structure a bare
prompt wouldn't reliably give (config-based repo targeting, dedup, template
conformance, codebase grounding) and folds the judges' real value — "don't
fabricate, don't hallucinate, don't rubber-stamp an over-broad ask" — into a single
principle you apply while drafting (see <quality-bar>). If a request is genuinely
high-stakes or architectural and you want adversarial scrutiny, run
`/rawgentic:adversarial-review` on the draft explicitly; it is not part of the
default path.
</why-this-is-lean>

<config-loading>
Before anything else, resolve the active project and load its config — this is what
makes the issue land on the right repo instead of a guess.

1. Determine the active project:
   - If a prior `/rawgentic:switch` bound this session, use that project.
   - Else read `claude_docs/session_registry.jsonl`, grep your session_id, use the
     most recent matching line.
   - Else read `.rawgentic_workspace.json` from the Claude root. Exactly one project
     `active == true` → use it. Multiple active → STOP: "Multiple active projects.
     Run `/rawgentic:switch <name>` to bind this session." Missing/malformed/none
     active → STOP and tell the user to run `/rawgentic:new-project` (or `switch`).
   - `activeProject.path` may be relative (e.g. `./projects/app`); resolve it against
     the directory containing `.rawgentic_workspace.json`.

2. Derive capabilities from one tested source of truth (never hand-derive):
   ```bash
   python3 hooks/capabilities_lib.py derive --config <activeProject.path>/.rawgentic.json
   ```
   Non-zero exit → config missing/corrupt/invalid; STOP and relay the printed message
   (a version mismatch is only a stderr warning, not fatal). Exit 0 → stdout is
   `{"config": {...}, "capabilities": {...}}`. Carry `capabilities.repo` and
   `capabilities.default_branch` as literals into later commands (each Bash call is a
   fresh shell — shell variables do not persist across calls).

Use `config`/`capabilities` for repo, branch, architecture, and standards. Trust the
config over any other file (e.g. a root `CLAUDE.md`); it is the project's contract.
</config-loading>

<quality-bar>
Apply these while drafting — they are the judge panel's value, distilled. If any
trips and you can't resolve it from the request + codebase, STOP and ask the user
rather than guessing:

- **No hallucinated components.** Every file/class/function/symbol you name must be
  verified to exist (see Step 2). If the request names something that doesn't exist,
  say so and file against the real component instead — don't invent.
- **No fabricated specifics.** Don't invent acceptance criteria, metrics, or targets
  the user never gave. If the request is too vague to write testable criteria, ask
  for specifics; if specifics genuinely aren't available yet, write an investigation
  issue whose criteria are about *gathering* the data, and mark targets as TBD.
- **Bound an over-broad ask.** If a request spans many concerns or is "do everything,"
  either propose splitting into multiple issues or file one issue with an explicit
  out-of-scope list naming what's deferred.
</quality-bar>

## Step 1: Understand the request

1. Classify as **feature** or **bug**. If ambiguous, ask: "Feature request (new
   functionality) or bug report (broken existing behavior)?"
2. If there's too little to write meaningful acceptance criteria, ask targeted
   questions (feature: desired behavior, affected area, problem solved; bug: expected
   vs actual, repro steps, environment).
3. Dedup check — search before drafting so you don't file a duplicate:
   ```bash
   gh issue list --repo <capabilities.repo> --search "<keywords>" --limit 10
   ```
   If a listed issue plausibly covers the request, show it to the user and ask whether
   it already covers their need before proceeding.

## Step 2: Draft the issue

1. Read the matching template:
   - feature → `<activeProject.path>/.github/ISSUE_TEMPLATE/feature_request.md`
   - bug → `<activeProject.path>/.github/ISSUE_TEMPLATE/bug_report.md`
   If no template exists, use a sensible default structure (Description, Acceptance
   Criteria, Scope, Affected Components, Risk; for bugs: Steps to Reproduce, Expected,
   Actual, Environment).
2. **Verify referenced components exist.** Use Serena MCP (`find_symbol`) if installed,
   otherwise `Grep`/`Glob`/`Read`, to confirm every file/symbol you reference is real.
   This is what keeps a hallucinated component out of the issue — don't skip it just
   because Serena is absent.
3. Write the draft, conforming to the template and the <quality-bar>:
   - **Title** in conventional form: `feat(scope): …` (feature) or `fix(scope): …`
     (bug). This keeps issue titles scannable and parseable by downstream tooling.
   - **Acceptance criteria:** numbered, testable, specific (≥3 where the request
     supports it).
   - **Scope:** explicit in-scope AND out-of-scope.
   - **Affected components:** only verified-real ones.
   - **Risk assessment** and a complexity t-shirt size (S/M/L/XL).
   - Bugs also: steps to reproduce, expected vs actual, environment, logs if any.
   - Cross-reference related issues found in the dedup search.
   If the draft exceeds ~2000 words, it's probably several issues — suggest splitting.

## Step 3: User review

Present the draft in a readable form (title, type, labels, complexity, description,
acceptance criteria, scope, affected components, risk, related issues). Then:

- Incorporate any feedback and re-present until the user approves.
- On "approved" / "looks good" / "lgtm" / "go ahead" → proceed to Step 4.
- If the user cancels or goes silent → stop; no issue is created. The draft stays in
  the conversation.

## Step 4: Create the issue

0. **Optional cross-model review (DEFAULT-OFF).** Before creating, check whether the
   project opted into an adversarial pass for this skill:
   ```bash
   python3 hooks/adversarial_review_lib.py is-enabled \
     --workspace .rawgentic_workspace.json --project <name> --skill create-issue
   ```
   Exit `0` → enabled; non-zero → **skip silently** (this is the default — behavior is
   byte-for-byte unchanged, no temp file, no subprocess). When enabled, write the draft
   to a temp file and run `/rawgentic:adversarial-review <spec-path> spec`; it is
   report-only — surface any findings to the user before filing. Codex failure is
   non-blocking: on any non-success, log it and proceed (do NOT block creation). WF1 has
   no plan_lib loop-back, so this does NOT call `consume_loopback` and does not re-trigger
   any earlier step.

1. Write the body to a temp file (handles multi-line content safely):
   ```bash
   cat << 'ISSUE_BODY_EOF' > /tmp/wf1-issue-body.md
   [full markdown body]
   ISSUE_BODY_EOF
   ```
2. Labels: `enhancement` for features, `bug` for bugs, plus any component/scope labels.
   Only use labels that already exist — check `gh label list --repo <capabilities.repo>`
   and create one with `gh label create` only if needed.
3. Create it:
   ```bash
   gh issue create --repo <capabilities.repo> \
     --title "<conventional title>" --body-file /tmp/wf1-issue-body.md \
     --label "<base label>" [--label "<scope label>"]
   ```
   Capture the URL from stdout.
4. Failure handling: auth → `gh auth status`; transient/network → retry once after a
   few seconds, and if it still fails save the body to
   `<activeProject.path>/docs/plans/draft-issue-YYYY-MM-DD.md` and tell the user to
   file manually; rate limit → wait and retry.
5. Clean up: `rm -f /tmp/wf1-issue-body.md`.

## Step 4b: HTML design artifact (opt-in, #174)

Give the new issue a living **HTML design artifact** so its spec is browsable. This
is config-gated — skip silently unless the project opts in:
```bash
python3 -c "import sys; sys.path.insert(0,'hooks'); from adversarial_review_lib import is_enabled_for; sys.exit(0 if is_enabled_for('.rawgentic_workspace.json','<name>','create-issue',key='designArtifact') else 1)"
```
Exit 0 → enabled; non-zero → skip (default; behavior byte-identical for opted-out
projects). When enabled:
1. Choose the target doc. Read the shared-doc config —
   `python3 -c "import sys; sys.path.insert(0,'hooks'); from adversarial_review_lib import design_artifact_shared_doc; print(design_artifact_shared_doc('.rawgentic_workspace.json','<name>') or '')"`:
   - **shared-doc mode** (a `designArtifact.sharedDoc` path is set — the multi-issue /
     campaign model: ONE rolling program doc updated across every issue, exactly like
     this repo's modernization dashboard): add/refresh THIS issue's entry in that single
     `<sharedDoc>` markdown, do NOT create a per-issue file.
   - **per-issue** (default, sharedDoc unset): write the issue spec markdown to
     `<activeProject.path>/docs/planning/<issue>-<slug>.md`.
2. Render it to a self-contained, CSP-safe HTML artifact with the shared helper —
   never hand-roll HTML:
   ```bash
   python3 hooks/render_artifact.py --md <doc>.md --out <doc>.html --title "#<issue> <title>"
   ```
   (The helper escape-first-renders the markdown and stamps a mountain-time datetime.)
3. Publish the `.html` with the Artifact tool and post the artifact URL as an issue
   comment (`gh issue comment`). WF1 has no branch, so the DURABLE `.md`+`.html`
   commit lands via the implementing PR (WF2/WF3 Step 12) — a claude.ai link alone
   is not sufficient. Log `### WF1 Step 4b — design artifact (published|skipped)`.

## Step 5: Wrap up

Append one line to `claude_docs/session_notes.md`:
`### WF1 create-issue — DONE (<issue URL>, type, title)`

Then report:
```
Issue created: <URL>
Type: <feature/bug> · Title: <title> · Labels: <labels>
```

Do NOT offer to start implementation. To implement, the user invokes WF2
(`/rawgentic:implement-feature`) separately, referencing the issue number.

<resumption>
If invoked mid-task: issue already created → just report it (Step 5). Draft approved
→ create it (Step 4). Draft exists, not yet approved → user review (Step 3). Otherwise
start at Step 1.
</resumption>
