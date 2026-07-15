# Adversarial Review — .rawgentic-diff-review-407-1784104031.patch

- Date: 2026-07-15
- Artifact type: diff
- Reviewer: Codex (model config-default, reasoning effort high)
- Findings: 2 (Critical 0, High 1, Medium 1, Low 0)

## Summary

The change lets adversarial-review metadata select a cheaper WF2 loop-back path. The security override is bypassable through a second model-controlled field, and the promised resolution gate is not executable or verifiable in the provided diff.

## Findings

### 1. [High] security · high confidence — hooks/adversarial_review_lib.py — loopback_class_entries

> +        if str(f.get("category", "")).lower() == "security":
> +            entries.append("untagged")
> +            continue
> +        val = f.get("loopback_class")
> +        if isinstance(val, str) and val.strip() in _LOOPBACK_CLASS_VOCAB:
> +            entries.append(val.strip())

The poisoned-tag defense trusts the same reviewer to supply both `category` and `loopback_class`. A security defect labeled with another valid category, such as `correctness`, and `loopback_class: "spec-tightening"` passes validation and selects the cheap path, skipping the intended full-design security route. Artifact steering or ordinary model misclassification therefore bypasses the security override without requiring an invalid value.

**Recommendation:** Change `loopback_class_entries` so adversarial Critical/High findings cannot enter `spec_tighten` solely from model-supplied metadata. Route every adversarial Critical/High to `untagged` unless a separate trusted gate independently establishes that the finding is non-security and text-only; otherwise retain the pre-#407 full-design routing.

### 2. [Medium] completeness · high confidence — skills/implement-feature/references/steps.md — Step 4 verifier-brief hardening

> +     (`classify_loopback_source(loopback_class_entries(adversarial) + self_review_classes)`)
> +     is invoked by the orchestrator via `python3 -c`, the established gate-helper pattern.
> +     **Verifier-brief hardening (#407):** when a spec_tighten cheap pass was reached via
> +     ANY adversarial-sourced tag, the incremental verifier's brief must include the
> +     originating Critical/High findings — read from the review's `--findings-json` sidecar
> +     (the canonical normalized report), never a re-derivation — and the verifier must
> +     confirm EACH is resolved by the applied amendment; any unresolved, omitted, or
> +     recategorized originating finding escalates to the full `design` path exactly like a
> +     new Critical/High finding.

The load-bearing mitigation is specified only as workflow prose. The provided executable change merely returns class strings; it does not bind those entries to originating findings, reject a missing/corrupt/empty sidecar, prove every finding was presented to the verifier, or enforce escalation. The tests shown only assert that the prose mentions the sidecar. Consequently, the claim that omitted findings fail closed is unverifiable from this artifact and can become a vacuous manual check.

**Recommendation:** Add an executable Step-4 gate helper that accepts the normalized sidecar and verifier result, rejects missing/corrupt sidecars, assigns stable identities to all originating Critical/High findings, requires one explicit resolved result per identity, and returns `design` on any missing, recategorized, unresolved, or extra Critical/High result. Add behavioral tests for each failure case instead of testing only documentation substrings.
**Ambiguity:** The actual orchestrator implementation may exist outside the supplied diff, but the artifact provides no executable call site or enforcement evidence.

---
_Report-only: this review does not edit the artifact. Findings are advisory; incorporate them at your discretion._