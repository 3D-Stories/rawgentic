# Adversarial Review — 2026-07-06-platform-feasibility-gate.md

- Date: 2026-07-06
- Artifact type: design
- Reviewer: Codex (model config-default, reasoning effort high)
- Findings: 5 (Critical 0, High 2, Medium 3, Low 0)

## Summary

The artifact proposes a prose-first feasibility gate with a small validator for `feasibility:` notes. The main risks are that the mechanical gate only validates notes that already exist, and some accepted evidence does not necessarily prove the project's actual runtime configuration permits the API.

## Findings

### 1. [High] completeness · high confidence — Problem; Mechanism §1

> A design can commit to a platform/framework API the project's own configuration does not
> permit and still pass **every** current gate

The proposed validator cannot detect the core failure case when a design omits the `feasibility:` note entirely. The artifact later states `Empty list → ok`, so a design that uses an unproven platform API but provides no feasibility line still passes the mechanical check, leaving the blocker dependent on human review noticing the omission.

**Recommendation:** In `plan_lib feasibility-note validator`, change the contract so Step 4 requires either an explicit `platform_apis: none` declaration or a list of API-scoped feasibility notes, and make the validator fail when the declaration is absent.

### 2. [High] correctness · high confidence — Mechanism §1

> Empty list → ok (a design with no
>   platform APIs needs no notes).

This assumes the validator can distinguish 'no platform APIs' from 'platform APIs present but no notes', but the described parser only extracts feasibility lines. Implemented as written, the mechanical gate treats both cases identically, so missing feasibility coverage is silently accepted.

**Recommendation:** In `assert_feasibility_proven`, replace `Empty list → ok` with validation against an explicit no-platform-API marker or a caller-provided count/list of detected material external APIs.

### 3. [Medium] ambiguity · medium confidence — Mechanism §1

> Two pure, tested helpers:

The design does not specify how `parse_feasibility_notes` associates an unscoped `- feasibility:` line with the surrounding `api`, `failure`, and `surface` fields. This makes the structured contract hard to validate consistently and can produce ambiguous results when a design contains multiple platform APIs.

**Recommendation:** In `plan_lib feasibility-note validator`, define the exact block grammar and scoping rules for multiple API entries, including whether unscoped `- feasibility:` is allowed when more than one `- api:` appears.
**Ambiguity:** The artifact mentions optional API-scoped feasibility notes but does not define block boundaries or multi-API behavior.

### 4. [Medium] completeness · high confidence — Mechanism §1

> - `parse_feasibility_notes(text) -> list[FeasibilityNote]` — extract `- feasibility: <value>`
>   lines

The canonical contract requires `failure` and conditionally requires `surface`, but the only validator parses feasibility lines. A fail-silent API can therefore have verified feasibility but omit the surfacing assertion/log, despite the artifact's own silent-failure rule.

**Recommendation:** Extend `plan_lib` with parsing and validation for the full API block: `api`, `feasibility`, `failure`, and `surface`; make `failure: fail-silent` without a non-empty `surface` a Step-4 error.

### 5. [Medium] internal-consistency · high confidence — Mechanism § The canonical feasibility contract

> verified via <capabilities-file|docs|existing-call-site|spike> — <citation>

The artifact's stated principle is verification against the project's real config, but `docs` alone can prove an API exists without proving the repository configuration permits it. That reopens the motivating failure mode where the API exists but a capability file denies it.

**Recommendation:** In `The canonical feasibility contract`, split evidence kinds into `project-config` and `external-reference`, and require project-specific evidence for permission/capability-gated APIs; allow `docs` only for APIs that are not project-config-gated.

---
_Report-only: this review does not edit the artifact. Findings are advisory; incorporate them at your discretion._