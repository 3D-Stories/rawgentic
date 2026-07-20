# Activating the CI review lanes (#233)

rawgentic ships two **GitHub Action review lanes** that run on every PR to `main`,
as a *bonus* second opinion on top of WF2's in-workflow Step 11 (code review) and
Step 11.5 (security scan) — most valuable on **human-opened or non-Claude PRs** that
never went through WF2:

| Lane | Workflow | What it runs |
|------|----------|--------------|
| `security-review` | `.github/workflows/claude-security-review.yml` | Claude's built-in `/security-review` over the PR diff, posting findings as inline PR comments. |
| `code-review` | `.github/workflows/claude-code-review.yml` | Claude's built-in `/code-review` (draft PRs skipped). |

They are **advisory** — neither is a *required* check, so a red result never blocks a
merge. Their in-workflow counterparts (Step 11 / 11.5) remain the primary gate.

## The signal is honest (green = actually reviewed) — #233 AC1

Earlier these lanes showed a **green ✓ even when nothing was reviewed** (no auth
secret → a `::warning::` inside a green check). That is fixed:

- **Green** — the review actually ran and succeeded.
- **Red** — the review did **not** run (no auth secret / bad token / plan lockout /
  action outage). Red here means *"not reviewed,"* not *"found problems"* — and
  because the check is advisory it does **not** block the merge.

So a green review check now means what you'd expect. (The lanes drop the old
`continue-on-error` mask; a `::warning::` inside a green check was not enough.)

**Docs-only PRs skip both lanes entirely (#478).** A diff confined to `docs/**` does
not trigger them (`paths-ignore` in each workflow's `on:` block) — an ABSENT check on
such a PR means *path-skipped by design*, not "not reviewed"-red. Scope is deliberately
narrow: `skills/**` markdown (product source), `README.md`, and `CLAUDE.md` still
trigger review. The `test` and `lint` lanes stay unconditional on every PR — docs are
load-bearing here (drift-guard tests pin doc sentences and README counts), so a
docs-only diff can legitimately fail them.

## Activate (one-time, owner)

The lanes need an auth secret. **Two options — OAuth is preferred:**

### 1. Subscription OAuth token (Pro/Max) — recommended

Mint a token on a machine already logged in to Claude:

```bash
claude setup-token          # prints a CLAUDE_CODE_OAUTH_TOKEN
```

Store it as a secret. **Org-wide covers every repo in one shot** (recommended):

```bash
# org-wide — all current + future repos in the org
gh secret set CLAUDE_CODE_OAUTH_TOKEN --org <your-org> --visibility all

# or per-repo, if you don't own the org
gh secret set CLAUDE_CODE_OAUTH_TOKEN --repo <owner>/<repo>
```

`gh` will prompt for the value (paste the token) — it is never echoed. For this
project the org is `3D-Stories`:

```bash
gh secret set CLAUDE_CODE_OAUTH_TOKEN --org 3D-Stories --visibility all
```

### 2. API key fallback (`ANTHROPIC_API_KEY`)

If you'd rather bill an isolated API budget than a subscription:

```bash
gh secret set ANTHROPIC_API_KEY --org <your-org> --visibility all
```

The lanes resolve **OAuth first, then API key**. Either one activates both lanes.

### 3. Zero-secret alternative

Run the lanes on a **self-hosted runner already logged in to Claude** — no repo
secret needed. See `docs/config-reference.md#ci-review-auth`.

> One caveat: activating the token secret alone is not enough — the **Claude Code
> GitHub App** (github.com/apps/claude) must also be installed on the repo/org, or
> the action fails with `401 … not installed on this repository`.

## Verify it ran

On your next PR, open the lane's Action run → **Summary**. A working lane prints:

```
Claude <security|code> review: executed=true (auth=oauth)
```

and the PR check is **green**. If you still see red with `executed=false (no auth
secret)`, the secret isn't visible to the workflow (wrong org/repo scope, or the App
isn't installed).

## Gate on a lane (opt-in)

To make a lane *block* merges (turn advisory-red into a hard gate), mark it a
**required status check** in branch protection — recommended only after ~10 clean
(`executed=true`) PRs, per the lanes' own promotion note. A required red then blocks,
exactly as intended.

## Runner routing (hosted-first, fleet fallback)

All lanes route via a repo variable: `runs-on: ${{ fromJSON(vars.RAWGENTIC_RUNS_ON || '"ubuntu-latest"') }}`.
Default (variable unset) is GitHub-hosted `ubuntu-latest` (parallel lanes). During a
hosted-runner outage, flip every new run to the org self-hosted fleet with one command:

```bash
gh variable set RAWGENTIC_RUNS_ON --repo 3D-Stories/rawgentic \
  --body '{"group":"3ds-fleet","labels":["self-hosted","linux"]}'
```

and back with `gh variable delete RAWGENTIC_RUNS_ON --repo 3D-Stories/rawgentic`.
Caveats: the fleet's single linux runner runs lanes SERIALLY (~10 min/PR vs ~4 hosted),
and re-runs of already-created workflow runs keep their original routing — flip, then
re-kick queued runs. The repo is a member of runner group `3ds-fleet`
(`allows_public_repositories: true`, fork-PR approval `all_external_contributors` — the
approval gate is what makes a public repo on self-hosted runners acceptable; do not
loosen it). An agent driver detecting a stall (ci-wait `TIMEOUT`) may auto-flip,
notify the owner, and flip back once GitHub Actions reports operational (owner grant
2026-07-20, epic #529 run D-8).
