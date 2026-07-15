# Adversarial Review — .rawgentic-diff-review-403-f5fa1deb.patch

- Date: 2026-07-14
- Artifact type: diff
- Reviewer: Codex (model config-default, reasoning effort high)
- Findings: 5 (Critical 0, High 4, Medium 1, Low 0)
- **[WARNING]** Possible secrets detected: API key, password.

## Summary

The change adds selectable GPT/GLM review backends and dual-backend execution. The diff contains backend-resolution bypasses that can route artifacts to the wrong provider, incomplete prerequisite CLI wiring, and an output collision capable of overwriting the reviewed artifact.

## Findings

### 1. [High] completeness · high confidence — hooks/adversarial_review_lib.py CLI parser; skills/adversarial-review Step 2

> +   python3 hooks/adversarial_review_lib.py prereq --backend <resolved backend> [--headless]

The workflows now invoke `prereq --backend`, but the CLI parser changes add `--backend` only to the review and consult subcommands; no corresponding prerequisite-parser argument or dispatch wiring appears in the diff. Following the documented GLM or both workflow will therefore be rejected by argparse, or the prerequisite branch will retain its default GPT check, stopping a valid GLM run before review.

**Recommendation:** In `main()`, add `--backend` with `choices=BACKENDS` to the `prereq` subparser and pass `args.backend` to `prereq_status`; add subprocess-level tests for `prereq --backend glm` and `prereq --backend both`.

### 2. [High] correctness · high confidence — hooks/adversarial_review_lib.py `main()` consult dispatch

> +        out_by_backend = ({"gpt": args.out, "glm": _sidecar_sibling(args.out)}
> +                          if backend == "both" else {backend: args.out})

Consult outputs receive no collision validation against the artifact. In both mode, an artifact such as `foo-glm.md` combined with `--out foo.md` makes the generated GLM sibling equal the artifact path; `run_glm_consult` then opens that path for writing and replaces the reviewed artifact with proposal JSON. This violates the report-only invariant and causes source data loss.

**Recommendation:** Before reading or invoking either backend in the consult branch, resolve every `out_by_backend` path and reject collisions among outputs, the artifact, and computed report paths. Apply the same validation to single-backend consults and add artifact/sibling collision tests.

### 3. [High] security · high confidence — hooks/adversarial_review_lib.py `_coerce_backend` / `_coerce_config`

> +    if raw is None:
> +        return "gpt", None

`None` is treated as an absent backend, while the caller uses `raw.get("backend")`, which returns `None` for both an absent field and an explicitly present JSON `null`. Thus `{ "backend": null }` bypasses the promised present-but-invalid refusal and silently routes the artifact to OpenAI/GPT, potentially crossing a provider or jurisdiction boundary the operator did not select.

**Recommendation:** Change `_coerce_config` to distinguish key absence from a present null value, for example by testing `"backend" in raw`; pass an absence sentinel to `_coerce_backend`, and classify explicit `None` as `invalid`. Add a test for JSON `"backend": null`.

### 4. [High] security · high confidence — hooks/adversarial_review_lib.py `_resolve_cli_backend`

> +    if ws or proj:
> +        # Half-given resolution info (e.g. --project "$NAME" with $NAME unset) is
> +        # a malformed invocation, not "no config": refusing beats silently
> +        # skipping the config a caller clearly meant to consult (8a T5).
> +        print(
> +            "backend resolution needs BOTH --workspace and --project (got only "
> +            "one, or an empty value) — refusing to default the backend (no egress)",
> +            file=sys.stderr,
> +        )
> +        return None, 2
> +    return "gpt", 0

The empty-value refusal is bypassed when both supplied values are empty strings: `ws or proj` is false, so resolution returns GPT. A command using two unset shell variables for `--workspace` and `--project` therefore silently egresses to OpenAI instead of refusing as the comment and data-handling contract require.

**Recommendation:** In `_resolve_cli_backend`, distinguish `None` from an explicitly supplied empty string. Default to GPT only when both values are `None`; refuse if either supplied value is empty or if exactly one value is supplied. Add a test where both arguments are empty strings.

### 5. [Medium] completeness · high confidence — hooks/adversarial_review_lib.py `GLM_MODEL` declaration

> +# concrete default: glm-5.2 is the live-verified slug on the subscription endpoint.

The claim that `glm-5.2` was verified against the live subscription endpoint is unverifiable from the provided diff: the added invocation tests use injected fake clients and establish only that the hard-coded value is passed through. If the slug or endpoint entitlement is wrong, every real default GLM invocation fails despite the release documentation declaring it supported.

**Recommendation:** Attach a reproducible, sanitized live-capture record or checked fixture supporting the model/endpoint compatibility claim, including date and returned model identifier; otherwise change the comments and release notes to label the slug as an operator-overridable assumption rather than live-verified.
**Ambiguity:** The diff provides no live endpoint result, so the model slug may be correct but cannot be confirmed from the supplied artifact.

---
_Report-only: this review does not edit the artifact. Findings are advisory; incorporate them at your discretion._