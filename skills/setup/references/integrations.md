# Setup integrations — Steps 2c, 2d, 2f, 2g detail

Read this file before executing Steps 2c, 2d, 2f, and 2g. Step 2e (Security
Scan Tooling) and the "New features are ON by default" policy live in the spine
(`SKILL.md`), not here.

## Step 2c: Headless Mode Access

This step runs on **every** setup invocation (including Sub-flow A re-runs).

Check the active project's entry in `.rawgentic_workspace.json` for the `headlessEnabled` field.

`headlessEnabled` accepts two shapes (#165):

- **bool** (legacy): `true` allows headless via ANY trigger; `false` denies.
- **object**: `{"enabled": true, "triggers": ["issue-label"], "auth": "subscription-oauth"}` —
  `triggers` is a per-trigger allowlist matched against the orchestrator's
  `RAWGENTIC_HEADLESS_TRIGGER` env (absent list = any trigger; the
  session-start gate fails CLOSED on a non-member or unset trigger).
  `auth` records the repo's Action auth-mode decision:
  `"subscription-oauth"` (default — `claude setup-token` →
  `CLAUDE_CODE_OAUTH_TOKEN` repo secret, shares the owner's plan bucket) or
  `"api-key"` (isolated dollar budget via `ANTHROPIC_API_KEY`).

- **If `headlessEnabled` is not set** (first-time configuration): prompt the user:

  ```
  Allow autonomous AI agent (headless mode) to work on [project-name]?

  When enabled, an external orchestrator can run rawgentic workflow skills
  on this project without interactive terminal access. The agent posts
  questions to GitHub issues and waits for replies.

  Enable headless mode for [project-name]? (y/n) [default: n]
  ```

  On **n**: write `headlessEnabled: false`. On **y**, follow up with the
  trigger allowlist and auth mode:

  ```
  Restrict which triggers may start a headless run? (recommended)
  - issue-label   — GitHub Actions run from a rawgentic:auto label
  - (blank)       — allow any trigger
  Triggers [issue-label]:

  Auth mode for Action runs:
  - subscription-oauth — claude setup-token → CLAUDE_CODE_OAUTH_TOKEN secret (default)
  - api-key            — ANTHROPIC_API_KEY secret, isolated budget
  Auth [subscription-oauth]:
  ```

  Write the object shape with the chosen values (or bare `true` if the user
  explicitly wants no restrictions).

- **If `headlessEnabled` is already set** (re-configuration): show current
  status — including triggers/auth when it is the object shape — and allow
  toggling or editing:

  ```
  Headless mode: [ENABLED / DISABLED] (triggers: [...], auth: [...])
  Change? (y/n) [default: keep current]
  ```

---

## Step 2d: Adversarial Review (WF5) Integration

This step runs on **every** setup invocation (including Sub-flow A re-runs).

The `/rawgentic:adversarial-review` skill (WF5) runs a cross-model review of a
text artifact via the Codex CLI. It can also be wired into the WF1, WF2, WF3, and
WF4 quality gates so they automatically run a cross-model second opinion on the
issue spec (WF1), design / implementation plan (WF2), root-cause analysis (WF3),
design artifacts. (WF4 refactoring removed at v3.0.0, #161.) WF5 is **on by default for the applicable workflows**
— the only thing it needs is an OpenAI account for the Codex CLI, so setup ASKS
about that account rather than asking you to opt in. The setting lives in the
active project's entry in `.rawgentic_workspace.json` (sibling to `headlessEnabled`),
NOT in `.rawgentic.json` — it is workspace-scoped, not
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
    because WF1 is a lean drafting workflow (no multi-agent critique), so most
    projects don't need a cross-model pass on issue specs — offer it as an opt-in
    add ("also enable for create-issue? (y/n) [default: n]"). Remind them the Codex CLI must be installed
    and authenticated (`curl -fsSL https://codex.openai.com/install.sh | bash`
    then `codex login`); if Codex is absent at run time the gate fails closed and
    is skipped (no error, just no cross-model pass). WF4 (refactor) was removed at v3.0.0 (#161); a configured refactor entry is inert and only fires on
    the Extract/Restructure path (Rename/Simplify skips it).
  - **If no →** disable it:
    `"adversarialReview": { "enabled": false, "workflows": [] }`
    The standalone `/rawgentic:adversarial-review` skill still works on demand;
    this only controls the workflow-embedded gates.

  Write the result to the project's entry using **bare skill names** in `workflows`
  (valid names: `implement-feature`, `fix-bug`, `create-issue`; `refactor` accepted for back-compat but inert — WF4 removed at v3.0.0, #161).

  - **Backend question (#405, asked whenever the block is enabled):**

    ```
    Which review backend? (gpt / glm / both) [default: gpt]
      gpt  — Codex CLI (OpenAI). The default; Enter keeps it.
      glm  — Zhipu GLM via the zhipuai SDK. Prereqs: pip install "zhipuai>=2.1.5"
             and ZHIPUAI_API_KEY (a z.ai Coding Plan subscription key works).
      both — two independent reviews; if one backend is unready the run degrades
             to the ready one (PARTIAL, exit 5), never aborting the other.
    ```

    Stage the answer into the block's `backend` field. Choosing the default `gpt`
    MAY omit the field entirely (absent → gpt is the documented contract, #403).
    **Prereq-aware nudge, never a block (AC4):** when the pick is `glm` or `both`,
    run `python3 hooks/adversarial_review_lib.py prereq --backend <pick>`; on a
    non-zero exit print the engine's install/credential guidance verbatim and
    STILL stage the choice — config is intent; the runtime prereq gate owns
    enforcement. Setup completion is never blocked on a backend prereq.

- **If `adversarialReview` is already set** (re-configuration): show current
  status and allow changing:

  ```
  Adversarial review (WF5): [DISABLED / enabled for: <bare skill names>]
  Backend: <current backend, or "gpt (default, field absent)">
  Change? Enter numbers (1=implement-feature, 2=fix-bug, 3=create-issue),
  "none", or "all" [default: keep current]
  Change backend? (gpt / glm / both) [default: keep current]
  ```

  Re-configuration offers the **current backend** as the default rather than
  silently resetting it (AC3, the read-modify-write convention); a `true`
  bool-shorthand block being reconfigured is normalized to the object shape
  before a backend can be staged. The same prereq nudge applies on change.

  (refactor removed at v3.0.0, #161)

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
- If already set: show status (including the current backend) and allow changing.
- **Backend question (#405):** when the block is enabled, ask the SAME
  backend question as Step 2d (same vocabulary: gpt / glm / both, default gpt,
  Enter keeps it, same prereq nudge via `prereq --backend <pick>`) — but as an
  **independent answer**: a project may want `both` reviews and a single-peer
  consult, so never copy Step 2d's answer. Stage into `peerConsult.backend`;
  the default `gpt` MAY omit the field.

## Step 2h: HTML Design-Artifact Lifecycle (#174) Integration

This step runs on **every** setup invocation (including Sub-flow A re-runs).

Mirror Step 2d. Check the project entry's `designArtifact` field.

The design-artifact lifecycle gives each issue a browsable HTML design doc: WF1
renders + publishes the issue spec and comments the URL; WF2/WF3 create-or-update
the `.md`+`.html` (with this run's telemetry embedded) inside the feature PR before
`gh pr create`. Rendering is `hooks/render_artifact.py` — self-contained, CSP-safe,
escape-first, with a mountain-time datetime stamp. The renderer ships seven
design-language templates (plain, roadmap, report, design, dashboard, review, spec);
see `docs/design-language.md`. Default OFF (byte-identical when declined).

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

## Step 2i: Phase-Executor Seat Table (#446) Integration

This step runs on **every** setup invocation (including Sub-flow A re-runs). It COLLECTS
only — the staged pointer is merged into the `.rawgentic.json` draft at Step 3 and written
at Step 6; the table file materializes after the Step-5 confirm. It never touches the
workspace file (Step 8).

1. **Show the resolved table** (read-only):
   ```bash
   python3 hooks/executor_routing_lib.py show-table --workspace <ws> --project <name>
   ```
   Displays one line per seat (primary, chain, role), the informational build bake-off
   set (`bakeoff_policy.BUILD_MODELS` — NOT table-editable; a follow-up issue tracks
   making it configurable), `table_source`, and `config_digest`. If the project already
   declares `phaseExecutorTable`, this IS the current override — change-or-keep applies,
   never rewrite silently.
2. **Ask**: "Keep the current resolved seat models? (Enter = keep)". Declining or keeping
   **stages nothing and touches nothing** — the current resolution (package default, or the
   project's existing override when one is declared) stands, byte-identical to not running
   this step (diff-DF4: for an overridden project Enter keeps the OVERRIDE — never imply a
   reset happened). `show-table` is read-only.
3. **On tweak**: collect a sparse per-seat patch — `primary` and/or `chain` model names
   only (a supplied chain REPLACES the whole chain; models must already have a lane in
   the base table). Write it to a temp patch file, then validate WITHOUT writing:
   ```bash
   python3 hooks/executor_routing_lib.py apply-table --workspace <ws> --project <name> \
     --patch-json <patch> --dest <dest> --expected-digest <digest-from-show-table> \
     --validate-only
   ```
   - Fresh create: `<dest>` is the constant `claude_docs/routing/phase-executor-table.json`.
   - Re-seed (override exists): `<dest>` is the EXISTING `phaseExecutorTable.file`.
   - Add `--reset-to-default` (combinable with `--validate-only`) to start from the
     package table instead of the current override — confirm that choice separately.
   - Success prints `{config_digest, pointer}`; stage the printed pointer literal
     `{"version": 1, "file": "<dest>"}` for the Step-3 draft merge and KEEP the printed
     candidate `config_digest` for materialization. Failure (exit 2) prints the
     validator's legible message (bad seat/field, unknown-lane model, statically-dead
     seat, drifted base) — offer edit-answers / use-defaults / cancel.
4. **Materialize (post-Step-5 confirm, immediately before Step 6)**: re-run the SAME
   `apply-table` invocation without `--validate-only`, adding
   `--expected-candidate-digest <the digest kept from step 3>`. Fresh create is atomic
   no-clobber; re-seed atomically replaces only the pointed-to file and only while its
   content still matches `--expected-digest`. On any later abort (Step-6 failure,
   cancel): a fresh-created file is RETAINED and named in a warning to the user (never
   auto-deleted); a re-seed needs no cleanup (the pointer pre-exists unchanged — the
   replace was the commit).
