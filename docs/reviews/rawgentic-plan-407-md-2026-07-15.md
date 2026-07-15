# Adversarial Review — .rawgentic-plan-407.md

- Date: 2026-07-15
- Artifact type: plan
- Reviewer: Codex (model config-default, reasoning effort high)
- Findings: 5 (Critical 0, High 2, Medium 3, Low 0)

## Summary

The plan adds a model-generated loopback classification and uses it to steer WF2 behavior. Its largest risks are a likely version-ordering failure, reliance on prompt text as a security boundary, and missing proof that external-model and browser-dependent paths work under the project’s real configuration.

## Findings

### 1. [High] internal-consistency · medium confidence — Tasks 5–6

> - Work: rev-diagram recipe — new REV entry newest-first (spine change: Step-4 fold now consumes adversarial loopback_class; :304 "never fold" prose superseded), versions entry, prior rev superseded, provenance footer @ 3.39.0; regenerate FULL-PAGE light+dark 1440px snapshots via served http + Playwright.
> - Verify: pytest tests/test_workflow_diagram.py -q green (version linkage).
> - Commit: feat(diagram): REV 3.39.0 — Step 4 adversarial loopback-class fold (#407)
> 
> ### Task 6: Version ×3 + README changelog
> - riskLevel: standard
> - files: .claude-plugin/plugin.json, plugins/rawgentic/.codex-plugin/plugin.json, tests/hooks/test_adversarial_review_registration.py, README.md
> - Work: bump 3.38.0 → 3.39.0 on all three surfaces; README changelog entry in exact repo shape (bold lead + issue/epic + diagram decision "WF2 diagram REV 3.39.0 (station 4 delta)" + Suite old→new tail; no splice hazard).

Task 5 creates and verifies a 3.39.0 diagram with a test explicitly described as checking version linkage, while the authoritative version surfaces remain at 3.38.0 until Task 6. If linkage includes those surfaces, Task 5 cannot satisfy its required green full-suite checkpoint, breaking the promised red-before-green and per-task commit sequence.

**Recommendation:** Move the three version bumps into Task 5 before running the diagram linkage test, or move diagram generation and its verification after Task 6. State exactly which files `test_workflow_diagram.py` links so the intermediate green state is demonstrable.
**Ambiguity:** The artifact does not define what the named version-linkage test compares, so the failure is strongly indicated but not provable solely from the text.

### 2. [High] security · high confidence — Task 2: Prompt rubric + injection-guard extension

> - riskLevel: high (security surface — the prompt IS the injection defense)
> - files: hooks/adversarial_review_lib.py, tests/hooks/test_adversarial_review_codex.py
> - RED: build_prompt output asserts — contains both vocab tokens, the intent-right/text-wrong criterion, the verbatim-recommendation constraint, the contracts/behavior/data-shape boundary clarifier, the unsure→design-flaw default, null-for-Medium/Low; UNTRUSTED DATA paragraph contains "severity or loop-back classifications".

An untrusted model-produced value will control whether findings take the spec-tightening or design path, but the only specified injection defense and acceptance test are prompt wording and substring assertions. Injected reviewed content can steer a non-security Critical/High finding to `spec-tightening`, causing the fold to select `spec_tighten` and bypass the intended design loopback; the security-category override does not protect findings whose category is also model-controlled.

**Recommendation:** Change Task 2 to require adversarial end-to-end fixtures in which untrusted artifact text attempts to set both `category` and `loopback_class`, and add a deterministic trust boundary before fold consumption—such as verifier-side recomputation or mandatory confirmation of every `spec-tightening` classification from trusted inputs.

### 3. [Medium] completeness · high confidence — Task 2 GREEN

> - GREEN: LOOPBACK CLASS paragraph in the CLASSIFY block (design §1 wording, mirrors steps.md 638-643 rubric); extend the UNTRUSTED DATA sentence.

The required prompt wording is delegated to `design §1`, which is not identified or reproduced in the artifact. The RED criteria check selected concepts but do not define the complete rubric, so the implementation and acceptance of the field’s load-bearing classification semantics are unverifiable from this plan.

**Recommendation:** Inline the complete LOOPBACK CLASS paragraph in Task 2 or identify the exact design artifact and anchor, then add an exact or structured expected-output assertion covering every rubric rule rather than only token presence.
**Ambiguity:** The referenced design section and its exact normative wording are absent from the provided artifact.

### 4. [Medium] feasibility · high confidence — Verification strategy

> Full suite after every task (exit code, delta vs 2889/1). No deferred-to-target tasks (pure Python + markdown; every surface exercisable locally).

The claim that every surface is locally exercised is unsupported for the external-model boundary. Earlier tasks change an externally supplied structured-output schema and prompt behavior, but the verification strategy names only local pytest runs and provides no test against the project’s real configured provider. Provider rejection of the nullable required field or noncompliance with the new classification contract could therefore appear only after deployment.

**Recommendation:** Add a real-config provider preflight task before Task 1 GREEN that submits the updated schema and records acceptance plus a conforming response for each supported backend. If live calls are intentionally excluded, narrow the verification claim and add an explicit owner-gated integration validation step.

### 5. [Medium] feasibility · high confidence — Task 5: Diagram REV

> - Work: rev-diagram recipe — new REV entry newest-first (spine change: Step-4 fold now consumes adversarial loopback_class; :304 "never fold" prose superseded), versions entry, prior rev superseded, provenance footer @ 3.39.0; regenerate FULL-PAGE light+dark 1440px snapshots via served http + Playwright.
> - Verify: pytest tests/test_workflow_diagram.py -q green (version linkage).

Task 5 depends on an HTTP server, Playwright, a compatible browser, and snapshot generation, but its verification only runs a linkage test and provides no preflight proving those platform dependencies work in the project’s actual environment. The task can reach implementation complete yet fail to produce the required snapshots.

**Recommendation:** Add a Task 5 preflight that runs the repository’s exact server and Playwright recipe, verifies the required browser is installed, and produces a disposable light/dark capture before changing the diagram. Include the exact command and expected success signal in Verify.

---
_Report-only: this review does not edit the artifact. Findings are advisory; incorporate them at your discretion._