# Adversarial Review — .rawgentic-diff-review-226.patch

- Date: 2026-07-06
- Artifact type: diff
- Reviewer: Codex (model config-default, reasoning effort high)
- Findings: 2 (Critical 0, High 1, Medium 1, Low 0)

## Summary

The change adds a `platform_apis` feasibility declaration, parser, gate, and prose drift guards. The main risk is that the new gate still has a fail-open path around `docs` evidence despite the prose saying docs alone do not prove permission-gated feasibility.

## Findings

### 1. [High] internal-consistency · high confidence — hooks/plan_lib.py feasibility evidence kinds

> +    "capabilities-file", "existing-call-site", "spike", "docs",

The mechanical gate treats `docs` as an allowed verification kind, but the new quality-bar text says docs alone never suffices for permission-gated APIs. This creates a fail-open path: a permission-gated platform API can be marked `feasibility: verified via docs — <citation>` and pass `assert_feasibility_declared`, even though the change's own stated goal is to require proof under this project's real config.

**Recommendation:** Change `FEASIBILITY_EVIDENCE_KINDS` or `assert_feasibility_declared` in `hooks/plan_lib.py` so `docs` is either not accepted as verified evidence, or is accepted only with an explicit non-permission-gated classification. Align the canonical contract and tests with that rule.

### 2. [Medium] consistency · high confidence — quality-bar.md / implement-feature Step 3 contract

> +  runs under *this project's* config (a capabilities/manifest file, an exact existing call
> +  site, a spike; `docs` alone never suffices for a permission-gated API), not the mere

The quality-bar rule says `docs` alone never suffices for permission-gated APIs, while the canonical contract added to Step 3 still lists `docs` as a normal `verified via` option. A reasonable implementer could follow the Step 3 template, cite docs for a permission-gated API, and believe the declaration is complete.

**Recommendation:** In `skills/implement-feature/references/steps.md` and the WF3 mirror text, change the `feasibility: verified via <capabilities-file|existing-call-site|spike|docs>` template to state that `docs` is valid only for APIs that are not permission/capability gated, or remove `docs` from the template entirely.

---
_Report-only: this review does not edit the artifact. Findings are advisory; incorporate them at your discretion._