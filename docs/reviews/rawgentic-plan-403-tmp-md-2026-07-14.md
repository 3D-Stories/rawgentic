# Adversarial Review — .rawgentic-plan-403-tmp.md

- Date: 2026-07-14
- Artifact type: plan
- Reviewer: Codex (model config-default, reasoning effort high)
- Findings: 6 (Critical 0, High 1, Medium 5, Low 0)

## Summary

The plan adds configurable GPT/GLM execution across the engine, CLI, skills, workflow consumers, and documentation. Its main risk is that production GLM support may ship without proving the external SDK and subscription endpoint work under the project's real configuration; several orchestration contracts are also underspecified.

## Findings

### 1. [High] feasibility · high confidence — Task 9: Pre-merge live GLM smoke

> Fallback if key
>   unreachable: deferred-to-target per design (PR ships explicitly NOT-live-verified,
>   `## Deferred verification` section, run-record entry naming the kwarg-acceptance claim).

The sole real-platform test is optional. Fake-client tests cannot prove that the subscription endpoint accepts `thinking`, `extra_body.reasoning_effort`, streaming, timeout, and structured-output arguments, so the PR can ship a GLM backend that fails on its first real invocation.

**Recommendation:** Change Task 9 into a required pre-merge gate executed with the project's supported GLM configuration. If credentials cannot be provided, keep the GLM backend disabled or defer the feature instead of treating unverified platform compatibility as an acceptable completion state.

### 2. [Medium] ambiguity · high confidence — Task 2: RED criteria

> `glm_api_key` precedence; `glm_base_url` precedence + https/userinfo/query/
>   fragment validation (violation → config error, no egress) + scheme+host redaction helper;

The plan requires precedence tests without listing the competing configuration sources or their order. Implementers cannot derive which credential and endpoint must win, creating a concrete risk of authenticating with the wrong key or sending an artifact to the wrong valid endpoint.

**Recommendation:** In Task 2, enumerate every API-key and base-URL source in exact highest-to-lowest precedence order, state the default endpoint, and add one test for every collision between sources.
**Ambiguity:** The inputs and required ordering for the stated precedence are absent.

### 3. [Medium] ambiguity · high confidence — Task 7: Step 11 join

> join handles exit 5 + dual-sidecar deterministic merge (provenance from file identity,
>   dedupe (evidence,location,category), highest severity, stable sort);

The claimed deterministic merge is incomplete. When duplicate findings have different descriptions, recommendations, confidence values, or backend provenance, the plan specifies only severity selection; it also does not provide the stable-sort keys. Implementations can therefore retain different content or ordering while all claiming conformance.

**Recommendation:** Extend Task 7 with an explicit merge record rule: define which duplicate supplies each field, how backend provenance is combined, the severity and confidence precedence tables, and the complete ordered sort-key tuple. Add golden tests for conflicting duplicates and equal-key records.
**Ambiguity:** Field-level conflict resolution and the stable-sort key are unspecified.

### 4. [Medium] correctness · high confidence — Task 7: Embedded WF2 wiring

> Step 3 synthesis
>   reads both proposals when the sibling exists (per stdout manifest).

File existence is not proof that a sibling was produced by the current invocation. Because a backend writes its sibling only on success, a partial rerun can leave an older sibling in place and cause Step 3 to synthesize stale output; the referenced stdout manifest has no defined format or freshness identifier.

**Recommendation:** Define a machine-readable current-run manifest in Task 5 containing a run ID, backend, outcome, and exact output path. Change Task 7 so consumers read only successful paths from that manifest, and remove or uniquely namespace outputs before each run.
**Ambiguity:** The plan does not define the stdout manifest or how it distinguishes current outputs from stale siblings.

### 5. [Medium] correctness · high confidence — Task 5: RED criteria

> gpt-only/glm-only byte-compatible behavior;

`glm-only` cannot be tested for byte compatibility because this plan introduces GLM and identifies no prior GLM output or golden oracle. The criterion is therefore unverifiable and permits incompatible stdout, report, or exit behavior to pass based on an undefined comparison.

**Recommendation:** Replace this Task 5 criterion with two explicit checks: GPT output remains byte-identical to named existing goldens, while GLM output matches newly specified exact stdout, report, sidecar, and exit-code fixtures.
**Ambiguity:** No comparison baseline for the new GLM-only behavior is identified.

### 6. [Medium] internal-consistency · high confidence — Task 9: files, verification fallback, and commit

> - files: (none — verification task; any fix commits ride the engine files)
> - verification: `pip install "zhipuai>=2.1.5"` (user-level); export ZHIPUAI_API_KEY (owner
>   provides) + ZHIPUAI_BASE_URL=https://api.z.ai/api/coding/paas/v4; run ONE real
>   `adversarial_review_lib.py review --backend glm` on a small artifact; assert exit 0 +
>   report written + Reviewer line names GLM. Confirms layer-1 timeout kwarg acceptance,
>   thinking/extra_body kwargs, streaming completion, json parse. Fallback if key
>   unreachable: deferred-to-target per design (PR ships explicitly NOT-live-verified,
>   `## Deferred verification` section, run-record entry naming the kwarg-acceptance claim).
> - commit: (only if fixes needed) `fix(wf5): live-smoke corrections (#403)`

Task 9 declares no files and no commit unless code fixes are needed, but its fallback requires durable deferred-verification and run-record artifacts. The plan does not assign those artifacts a path or commit, so the required disclosure can be omitted or left outside the reviewed change while Task 9 is still marked complete.

**Recommendation:** Add the exact run-record and deferred-verification locations to Task 9's `files` field and require a documentation commit whenever the fallback is used; state whether `## Deferred verification` belongs in a repository document or the PR body.
**Ambiguity:** The destination and commit mechanism for the required fallback records are not defined.

---
_Report-only: this review does not edit the artifact. Findings are advisory; incorporate them at your discretion._