# Adversarial Review — .rawgentic-diff-review-159-766b434e.patch

- Date: 2026-07-04
- Artifact type: diff
- Reviewer: Codex (model config-default, reasoning effort high)
- Findings: 2 (Critical 0, High 1, Medium 1, Low 0)

## Summary

The diff restructures WF3 by moving detailed step instructions into references, adds an incident lane, generalizes a verifier script, bumps versions, and adjusts tests to read skill corpora. The main risks are internal workflow contradictions introduced by making merge/deploy conditional while keeping completion and issue closure unconditional, and a copied command that no longer matches the stated conditional CI gate.

## Findings

### 1. [High] internal-consistency · high confidence — skills/fix-bug/SKILL.md Steps overview and skills/fix-bug/references/steps.md Step 14

> +Steps **1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 14 always run** (see <mandatory-steps>).
> +- **Step 12 - Merge & deploy (conditional).** Only on user-requested merge; squash-merge, deploy, migration. (read references/steps.md before executing)
> +2. Close GitHub issue with closing comment:

The new spine makes Step 12 merge/deploy conditional but still makes Step 14 always run, and Step 14 unconditionally closes the GitHub issue. If a user does not request merge, WF3 will still close the bug issue even though the PR may only be open, creating a false completion state and losing the issue as an active tracker.

**Recommendation:** Change `skills/fix-bug/references/steps.md` Step 14 so issue closure is conditional on the PR being merged, or change the Step 14 issue action to post a status comment when `outcome.merged != true`. Also reflect that condition in the Step 14 one-line summary in `SKILL.md`.

### 2. [Medium] completeness · high confidence — skills/fix-bug/SKILL.md Step 11 and skills/fix-bug/references/steps.md Step 11

> +- **Step 11 - CI verification (conditional).** Monitor/fix CI when `has_ci`; quarantine handled as a visible non-gate with a trust guard. (read references/steps.md before executing)
> +1. Wait for CI pipeline to complete:
> +   ```bash
> +   gh run list --repo capabilities.repo --branch fix/<branch-name> --limit 3

The spine says CI verification is conditional on `has_ci`, but the referenced Step 11 detail starts directly by waiting for CI and has no branch for `capabilities.has_ci == false`. Implementers following the required reference section can still run `gh run list` in projects without CI instead of recording `not_configured` and continuing.

**Recommendation:** In `skills/fix-bug/references/steps.md` Step 11, add an initial instruction: `If capabilities.has_ci is false, record outcome.ci = "not_configured", log Step 11 skipped, and proceed to Step 12/14 as applicable; do not run gh run list.`

---
_Report-only: this review does not edit the artifact. Findings are advisory; incorporate them at your discretion._