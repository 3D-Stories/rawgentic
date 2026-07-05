# Setup integrations — Steps 2c, 2d, 2f, 2g detail

Read this file before executing Steps 2c, 2d, 2f, and 2g. Step 2e (Security
Scan Tooling) and the "New features are ON by default" policy live in the spine
(`SKILL.md`), not here.

## Step 2c: Headless Mode Access

This step runs on **every** setup invocation (including Sub-flow A re-runs).

Check the active project's entry in `.rawgentic_workspace.json` for the `headlessEnabled` field.

- **If `headlessEnabled` is not set** (first-time configuration): prompt the user:

  ```
  Allow autonomous AI agent (headless mode) to work on [project-name]?

  When enabled, an external orchestrator can run rawgentic workflow skills
  on this project without interactive terminal access. The agent posts
  questions to GitHub issues and waits for replies.

  Enable headless mode for [project-name]? (y/n) [default: n]
  ```

  Write `headlessEnabled: true` or `headlessEnabled: false` to the project's
  entry in `.rawgentic_workspace.json` based on the user's choice.

- **If `headlessEnabled` is already set** (re-configuration): show current
  status and allow toggling:

  ```
  Headless mode: [ENABLED / DISABLED]
  Change? (y/n) [default: keep current]
  ```

---

## Step 2d: Adversarial Review (WF5) Integration

This step runs on **every** setup invocation (including Sub-flow A re-runs).

The `/rawgentic:adversarial-review` skill (WF5) runs a cross-model review of a
text artifact via the Codex CLI. It can also be wired into the WF1, WF2, WF3, and
WF4 quality gates so they automatically run a cross-model second opinion on the
issue spec (WF1), design / implementation plan (WF2), root-cause analysis (WF3),
design artifacts. (WF4 refactoring removed — deprecated to a stub, #160.) WF5 is **on by default for the applicable workflows**
— the only thing it needs is an OpenAI account for the Codex CLI, so setup ASKS
about that account rather than asking you to opt in. The setting lives in the
active project's entry in `.rawgentic_workspace.json` (sibling to `headlessEnabled`
/ `critiqueMethod`), NOT in `.rawgentic.json` — it is workspace-scoped, not
committed to the project repo. (It does send artifact text to OpenAI; declining
the account question keeps it fully off.)

Check the active project's entry for the `adversarialReview` field.

- **If `adversarialReview` is not set** (first-time configuration): ask the
  OpenAI-account question and default WF5 **on** when the answer is yes:

  ```
  Cross-model adversarial review (WF5) gives your workflows an independent,
  different-model second opinion at their quality gates (WF2 design + plan, WF3
  root-cause). It runs through the Codex CLI, which needs
  an OpenAI account, and it sends the artifact text to OpenAI.

  Do you have an OpenAI account you can use for Codex? (y/n) [default: n]
  ```

  - **If yes →** enable WF5 for all applicable workflows by default:
    `"adversarialReview": { "enabled": true, "workflows": ["implement-feature", "fix-bug"] }`
    Tell the user it's now on for implement-feature (WF2), fix-bug (WF3), and
    `create-issue` (WF1) is intentionally **left off** by default
    because WF1 already runs a full same-model 3-judge critique, so a cross-model
    pass there is redundant — offer it as an opt-in add ("also enable for
    create-issue? (y/n) [default: n]"). Remind them the Codex CLI must be installed
    and authenticated (`curl -fsSL https://codex.openai.com/install.sh | bash`
    then `codex login`); if Codex is absent at run time the gate fails closed and
    is skipped (no error, just no cross-model pass). WF4 (refactor) is deprecated (#160); a configured refactor entry is inert and only fires on
    the Extract/Restructure path (Rename/Simplify skips it).
  - **If no →** disable it:
    `"adversarialReview": { "enabled": false, "workflows": [] }`
    The standalone `/rawgentic:adversarial-review` skill still works on demand;
    this only controls the workflow-embedded gates.

  Write the result to the project's entry using **bare skill names** in `workflows`
  (valid names: `implement-feature`, `fix-bug`, `create-issue`; `refactor` accepted for back-compat but inert — WF4 deprecated, #160).

- **If `adversarialReview` is already set** (re-configuration): show current
  status and allow changing:

  ```
  Adversarial review (WF5): [DISABLED / enabled for: <bare skill names>]
  Change? Enter numbers (1=implement-feature, 2=fix-bug, 3=create-issue),
  "none", or "all" [default: keep current]
  ```

  (refactor removed — WF4 deprecated to a stub, #160)

---

## Step 2f: Model Routing (optional)

This step runs on **every** setup invocation (including Sub-flow A re-runs).

Offer per-project subagent model routing. Ask whether to route the three dispatch roles to specific models (skip any role = inherit the session model). Suggested defaults: `review: opus`, `analysis: sonnet`, `implementation: opus`.

Check the active project's entry for the `modelRouting` field.

- **If `modelRouting` is not set** (first-time configuration): ask the
  per-role question below.
- **If `modelRouting` is already set** (re-configuration): show the current
  per-role values first, then ask change-or-keep — never rewrite silently:

  ```
  Model routing: review=<value>, analysis=<value>, implementation=<value> (unset roles show as "inherit")
  Change? (y/n) [default: keep current]
  ```

  If keeping, write nothing for this section.

Per-role question (asked when opting in, or on "change"):

- Collect a model (`opus`/`sonnet`/`haiku`/`fable` — a `haiku` choice is bumped
  to `sonnet` at resolve time, since rawgentic never routes work to Haiku),
  "skip", or a model plus an optional effort tier (`low`/`medium`/`high`/`xhigh`/`max`),
  per role.
- A plain model choice stays staged as the string: `"<role>": "<model>"`. Do
  NOT convert an existing string value to a dict unless the user picks an
  effort for that role. A model + effort choice stages
  `"<role>": { "model": "<model>", "effort": "<effort>" }` instead. Omit
  skipped roles.
- If the user picks an effort tier for any role, tell them the `{model, effort}`
  shape needs the updated plugin (v2.55.0+) loaded in the running session's
  cache — an older cached lib fail-opens that role to `inherit` with a stderr
  warning, silently turning routing off for it until the plugin is reloaded.
- If the user declines routing entirely, stage nothing (absent block = inherit everywhere; byte-identical default).
- Note the soft opus floor: routing `review` to `sonnet` warns at run time but still
  applies (`haiku` is bumped to `sonnet` instead, per the never-Haiku rule).
- Note that `implementation` acts as a per-task ceiling, not a blanket assignment:
  WF2 Step 8 down-routes standard/simple tasks to `sonnet` and reserves the
  configured model for high-risk or complex tasks.

## Step 2g: Peer Consult (WF13) Integration

This step runs on **every** setup invocation (including Sub-flow A re-runs).

Mirror Step 2d (Adversarial Review). Check the project entry's `peerConsult` field.

- If not set: ask whether to enable the cross-model peer designer at the WF2 design step. On yes, stage `"peerConsult": { "enabled": true, "workflows": ["implement-feature"] }`; on no, `"peerConsult": { "enabled": false, "workflows": [] }`. The standalone `/rawgentic:peer-consult` works regardless.
- If already set: show status and allow changing.

## Step 2h: HTML Design-Artifact Lifecycle (#174) Integration

This step runs on **every** setup invocation (including Sub-flow A re-runs).

Mirror Step 2d. Check the project entry's `designArtifact` field.

The design-artifact lifecycle gives each issue a browsable HTML design doc: WF1
renders + publishes the issue spec and comments the URL; WF2/WF3 create-or-update
the `.md`+`.html` (with this run's telemetry embedded) inside the feature PR before
`gh pr create`. Rendering is `hooks/render_artifact.py` — self-contained, CSP-safe,
escape-first, with a mountain-time datetime stamp. Default OFF (byte-identical when
declined).

- **If `designArtifact` is not set** (first-time configuration): ask two questions:
  1. "Give each issue a living HTML design artifact (rendered spec + run telemetry,
     committed under `docs/planning/`)? (y/n) [default: n]"
     - **no →** stage `"designArtifact": { "enabled": false, "workflows": [] }`.
     - **yes →** ask the second question:
  2. "One artifact **per issue** (default), or **shared-doc mode** — a single rolling
     `docs/*.md` program doc updated across every issue (best for multi-issue
     campaigns; one dashboard-style doc, not N files)?"
     - **per-issue →** stage
       `"designArtifact": { "enabled": true, "workflows": ["create-issue", "implement-feature", "fix-bug"] }`.
     - **shared-doc →** ask for the doc path (must be a project-relative `docs/*.md`;
       an absolute path or `..` traversal or a non-`docs/*.md` value falls back to
       per-issue) and stage it as `"sharedDoc": "<docs/…․md>"` alongside the above.
- **If `designArtifact` is already set** (re-configuration): show current status
  (enabled + per-issue vs `sharedDoc: <path>`) and allow changing. The standalone
  `hooks/render_artifact.py` works regardless of this setting.
