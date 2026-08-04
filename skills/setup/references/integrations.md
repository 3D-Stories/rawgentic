# Setup integrations — Steps 2d, 2g, 2h detail

Read this file before executing Steps 2d, 2g, and 2h. Step 2e (Security
Scan Tooling) and the "New features are ON by default" policy live in the spine
(`SKILL.md`), not here.

## Step 2d: Adversarial Review (WF5) Integration

This step runs on **every** setup invocation (including Sub-flow A re-runs).

The `/rawgentic:adversarial-review` skill (WF5) runs a cross-model review of a
text artifact via the Codex CLI. It can also be wired into the WF1, WF2, WF3, and
WF4 quality gates so they automatically run a cross-model second opinion on the
issue spec (WF1), design / implementation plan (WF2), root-cause analysis (WF3),
design artifacts. (WF4 refactoring removed at v3.0.0, #161.) WF5 is **on by default for the applicable workflows**
— the only thing it needs is an OpenAI account for the Codex CLI, so setup ASKS
about that account rather than asking you to opt in. The setting lives in the
active project's entry in `.rawgentic_workspace.json`,
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
    and authenticated (`npm install -g @openai/codex`
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
`gh pr create`. Rendering is the design-doc-publish add-on (SUGGESTED, not required;
with it absent rawgentic writes a plain fallback page and says so) — self-contained, CSP-safe,
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
  the add-on renderer works regardless of this setting.
