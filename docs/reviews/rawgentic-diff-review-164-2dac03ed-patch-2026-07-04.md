# Adversarial Review — .rawgentic-diff-review-164-2dac03ed.patch

- Date: 2026-07-04
- Artifact type: diff
- Reviewer: Codex (model config-default, reasoning effort high)
- Findings: 3 (Critical 0, High 1, Medium 2, Low 0)

## Summary

The diff adds bundled implementer/reviewer agent definitions and updates WF2 dispatch prose to use them. The main risk is that the claimed reviewer safety boundary is advisory rather than enforced because Bash remains available without a tool-level read-only constraint.

## Findings

### 1. [High] security · high confidence — agents/rawgentic-reviewer.md frontmatter

> +tools: Read, Grep, Glob, Bash

The change claims the reviewer carries no file-editing capability, but the actual granted tool set includes unrestricted Bash. The surrounding prose asks Bash to be read-only, but that is not an enforceable boundary; a reviewer subagent can still mutate files, stage changes, or run destructive commands through Bash, so the read-only quality-gate agent can fail open.

**Recommendation:** Change `agents/rawgentic-reviewer.md` frontmatter to remove `Bash`, or replace it with an explicitly read-only command wrapper if the platform supports one. Update README and WF2 prose to stop describing the reviewer as read-only unless the tool boundary actually enforces it.

### 2. [Medium] completeness · high confidence — tests/test_bundled_agents.py test_reviewer_tools_are_read_heavy

> +    for required in ("Read", "Grep", "Glob"):

The drift guard does not require Bash even though the reviewer definition and README both rely on Bash for git inspection and running suites. A future edit can remove Bash from the reviewer tools while this test still passes, silently breaking the documented review workflow that says Bash is available.

**Recommendation:** In `test_reviewer_tools_are_read_heavy`, either add `"Bash"` to the required tools list or update the reviewer definition/docs to remove Bash from the contract entirely.

### 3. [Medium] consistency · high confidence — README.md Bundled Subagents

> +| `rawgentic:rawgentic-reviewer` | Quality-gate reviews (Steps 4/8a/11) | `model: inherit`, read-heavy tools only (Read/Grep/Glob/Bash, no Write/Edit) |

The README presents the reviewer as having read-heavy tools only, but the listed Bash capability is not inherently read-only. This creates a documentation/behavior mismatch for users relying on the bundled reviewer as a safe quality gate.

**Recommendation:** In README `Bundled Subagents`, replace `read-heavy tools only (Read/Grep/Glob/Bash, no Write/Edit)` with wording that either omits Bash or explicitly says Bash is available and must be constrained by runtime policy, not by the definition itself.

---
_Report-only: this review does not edit the artifact. Findings are advisory; incorporate them at your discretion._