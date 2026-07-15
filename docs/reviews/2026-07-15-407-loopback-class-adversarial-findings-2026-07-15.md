# Adversarial Review — 2026-07-15-407-loopback-class-adversarial-findings.md

- Date: 2026-07-15
- Artifact type: design
- Reviewer: Codex (model config-default, reasoning effort high)
- Findings: 5 (Critical 0, High 1, Medium 4, Low 0)

## Summary

The artifact adds reviewer-controlled routing metadata so some adversarial Critical/High findings can use the cheaper spec-tightening loopback. Its main risks are incomplete verifier provenance, an unspecified production integration point, and compatibility/security claims that are not fully established by the provided text.

## Findings

### 1. [High] completeness · high confidence — §2 Verifier-brief hardening; §5 Tests

> **Verifier-brief hardening (pass-2 A2, partial adopt):** when a spec_tighten cheap
> pass was reached via ANY adversarial-sourced tag, the item-7 incremental verifier's
> brief must include the originating Critical/High findings list, and the verifier
> must confirm EACH is resolved by the applied amendment — any unresolved, omitted,
> or recategorized originating finding escalates to the full `design` path exactly
> like a new Critical/High.

The verifier cannot detect that an originating finding was omitted before its brief was constructed unless it also receives an independent canonical source, count, or identity manifest. The proposed helper returns only classification strings, and §5 contains no test for complete brief propagation or intentional omission. An unresolved design or security finding omitted during brief construction can therefore escape verification and let the cheap path pass.

**Recommendation:** In §2, define the verifier input as the canonical normalized adversarial report rather than a caller-assembled findings list. Give findings stable indexes or IDs, require the brief to carry the expected ID set or count/digest, and add §5 end-to-end tests that intentionally omit, duplicate, and recategorize an originating finding and assert escalation to `design`.

### 2. [Medium] completeness · medium confidence — §1 loopback_class_entries; §2 item 7; §5 Fold integration

> Makes the WF2 item-7 fold mechanical:
>   `classify_loopback_source(loopback_class_entries(adversarial) + self_review_classes)`.

This is a composition formula, but the artifact identifies no executable WF2 production call site that imports and invokes the new helper, retains the original findings for the verifier, or surfaces invocation failure. The proposed integration test calls the two pure functions directly, so it can pass even if the real item-7 workflow never consumes `loopback_class`; the cheap path would then remain unreachable in production.

**Recommendation:** In §2, name the exact WF2 runtime call site or command that executes this composition and specify its error handling. Add an end-to-end workflow test that feeds a tagged adversarial report through that real call site and observes the resulting `spec_tighten` decision and verifier brief.
**Ambiguity:** The text calls the consumer “orchestrator prose,” but does not establish whether that prose is itself the production execution mechanism.

### 3. [Medium] feasibility · medium confidence — Error handling / failure modes — old cached engine

> - Old cached engine (≤3.38.0) emits no field → validator passes, entries yield
>   "untagged", fold → design. Byte-for-byte pre-#407 behavior.

The compatibility claim assumes that a new `loopback_class_entries` helper can process output from an old cached engine, but the helper is introduced in the same engine module for 3.39.0. The provided text does not prove that producer and consumer code are independently versioned or that WF2 can import the new helper while invoking a ≤3.38.0 cache. If the cached module owns both operations, item 7 can fail because the helper does not exist rather than safely returning `untagged`.

**Recommendation:** In Backward compatibility and Platform dependencies, document the producer/consumer loading boundary and cite the exact mixed-version call site or add a version-skew spike. If the old cache can own the helper module, feature-detect `loopback_class_entries` and provide an explicit local fallback that maps every old adversarial Critical/High finding to `untagged`.
**Ambiguity:** “Engine” and “cached engine” are not defined well enough to determine which version supplies the newly added helper.

### 4. [Medium] internal-consistency · high confidence — §5 Tests — validate_finding and existing-fixture updates

> `tests/hooks/test_adversarial_review_schema.py:14-26` `_finding()` helper;
>   `:199-203` nullable-fields tuple (+ `loopback_class`); `:258-269` wrong-type
>   parametrize (+ new field case); `:278-285` literal full-finding dict;

Adding `loopback_class` to the existing wrong-type parameterization conflicts with the earlier explicit requirement that `validate_finding` treat `loopback_class: 123` as valid. Unless that parameterization has different expectations for this field, the planned tests simultaneously require the same non-string value to be accepted and rejected, producing an unavoidable test or implementation disagreement.

**Recommendation:** Rewrite the §5 fixture-update entry to exclude `loopback_class` from the invalid-type parameterization. Add a separate named test asserting that non-string `loopback_class` values remain valid in application validation but map to `untagged` in `loopback_class_entries`; keep JSON-schema shape validation as a distinct test.

### 5. [Medium] security · high confidence — Error handling / failure modes — prompt-injection defense

> (2) security-category Critical/High findings fold design UNCONDITIONALLY in
>   `loopback_class_entries` — a poisoned tag on a security finding is mechanically
>   ignored; (3) for non-security findings, worst case a poisoned tag downgrades a
>   full loop-back to amend+verifier

The mechanical security override depends on `category`, which is controlled by the same untrusted reviewer output as `loopback_class`. A poisoned response can label a security defect as a non-security category and `spec-tightening`, bypassing the unconditional override and downgrading it to the cheap path. Prompt instructions are not an independent enforcement layer; only the verifier remains, and its stated task is resolution checking rather than independent security/category classification.

**Recommendation:** Revise §2 and Security implications so the incremental verifier receives the complete canonical report and artifact and must independently reassess whether any originating finding is security-relevant or a design flaw before approving the amendment. Any such reassessment, disagreement, missing provenance, or injection indication must force the full `design` path.

---
_Report-only: this review does not edit the artifact. Findings are advisory; incorporate them at your discretion._