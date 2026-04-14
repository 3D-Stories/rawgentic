# Skill Development

## Skill Structure

Each skill is a single Markdown file at `skills/<name>/SKILL.md`. Claude Code
auto-discovers every `SKILL.md` under the `skills/` directory when the plugin
is installed -- there is no explicit skills array in `plugin.json`.

### Frontmatter

The YAML frontmatter has three fields (`skills/create-issue/SKILL.md`):

```yaml
---
name: rawgentic:create-issue
description: Create a GitHub issue (feature request or bug report) using the WF1 9-step workflow...
argument-hint: Description of the feature to request or bug to report
---
```

### Body

Everything below the frontmatter is the full prompt injected when a user
invokes `/rawgentic:<name>`. This typically includes role definitions,
constants, numbered workflow steps, quality gates, and config-loading
instructions.

### Invocation

Users invoke a skill with `/rawgentic:<name>` followed by optional arguments.
For example: `/rawgentic:create-issue Add retry logic to the API client`.

## Workspace Directories

Each skill may have a corresponding workspace directory at
`skills/<name>-workspace/`. These contain **evaluation artifacts**, not runtime
data. They are created during skill quality evaluation and are not used when
the skill is invoked in normal operation.

### Directory Layout

```
skills/<name>-workspace/
  iteration-<N>/
    benchmark.md              # Per-skill results table
    benchmark.json            # Machine-readable results
    review.html               # Human-readable evaluation report
    <scenario-name>/
      eval_metadata.json      # Prompt, assertions, scenario config
      with_skill/
        grading.json          # Pass/fail per assertion with evidence
        timing.json           # Token count and duration
        outputs/
          transcript.md       # Full agent transcript
      without_skill/
        grading.json
        timing.json
        outputs/
          transcript.md
```

## Evaluation Methodology

Each skill is tested against scenarios (edge cases, error paths, happy paths):

1. **Two runs per scenario** -- `with_skill` vs `without_skill`.
2. Each run produces a transcript in `outputs/transcript.md`.
3. A grading pass checks the transcript against assertions from
   `eval_metadata.json`, writing `grading.json` with per-assertion verdicts
   and evidence.
4. Per-skill results go into `benchmark.md` (pass rate, timing, token deltas).
5. Cross-skill summary: `skills/phase2-eval-summary.md` (requirement coverage
   matrix across all skills).

Phase 2 results: 25 scenarios, 9 skills, 52 agent runs -- 100% with-skill
pass rate vs 72% without (+28% delta).

## Adding a New Skill

1. **Create the SKILL.md.** Add `skills/<name>/SKILL.md` with the three
   frontmatter fields (`name`, `description`, `argument-hint`) and the full
   prompt body. The `name` field must use the `rawgentic:<name>` prefix.

2. **Add to the marketplace skills whitelist.** Edit
   `.claude-plugin/marketplace.json` and add `"./skills/<name>"` to the
   `skills` array in the plugin entry. The marketplace uses this list to
   control which skills are available for org installs. **Skills not in this
   list are invisible to the marketplace but still discoverable by local
   installs.**

3. **Reinstall the plugin** after adding the file (see the update workflow in
   the workspace `CLAUDE.md`). Existing sessions will not pick up new skills
   until they restart.

4. **Evaluation workspace (optional).** Create
   `skills/<name>-workspace/iteration-1/` with scenario directories, each
   containing `eval_metadata.json` with a prompt and assertions. Running the
   evaluation harness will populate `with_skill/` and `without_skill/`
   subdirectories. This is recommended for SDLC workflow skills but not
   required for workspace-management skills like `setup` or `switch`.

## Marketplace Manifest Validation

When the plugin is installed via a Claude org marketplace (syncing from a
private GitHub repo), the marketplace validator applies strict rules:

1. **No duplicate skill names.** The validator walks ALL `skills/**/SKILL.md`
   files recursively, regardless of the `skills` whitelist. Two files declaring
   the same `name:` in frontmatter cause `sync_status: failed_content`. Names
   are compared after normalization (stripping colons and hyphens), so
   `rawgentic:setup` and `rawgentic-setup` would collide.

2. **Workspace directories must not contain `SKILL.md`.** Dev snapshots in
   `*-workspace/skill-snapshot/` should use a different filename like
   `SKILL.snapshot.md`. The validator only searches for files named exactly
   `SKILL.md`.

3. **No version field on plugin entries.** If `marketplace.json` includes a
   `version` on the plugin entry and it differs from `plugin.json`, the
   validator rejects the install. Keep version only in `plugin.json` (the
   single source-of-truth) and optionally in `metadata.version` at the
   marketplace top level.

4. **Use `strict: true`.** Required for org marketplace installs. Without it,
   validation errors may be silently swallowed, producing confusing failures.
