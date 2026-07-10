# Adversarial Review — .rawgentic-plan-344.md

- Date: 2026-07-10
- Artifact type: plan
- Reviewer: Codex (model config-default, reasoning effort high)
- Findings: 6 (Critical 0, High 0, Medium 6, Low 0)

## Summary

The plan sequences renderer, configuration, documentation, and release work, but several load-bearing contracts are either delegated to an unavailable design artifact or left without deterministic acceptance criteria. The main risks are ambiguous parser behavior, placeholder-induced content corruption, and unverified workspace-side follow-through.

## Findings

### 1. [Medium] ambiguity · high confidence — Task 1 — RED acceptance cases

> RED: multi-line bold `**a\nb**` → one `<p>` with `<strong>a b</strong>`; hard break `**a  \nb**` → `<strong>a<br>b</strong>`; consecutive hard breaks; buffer-final hard break dropped; paragraph join (2 plain lines → one <p>); flush before heading/list/table/fence/blockquote; `<script>` in joined paragraph escaped; code span across hard break.

Several named cases lack expected input/output, most importantly “code span across hard break” and “consecutive hard breaks.” The placeholder algorithm could therefore be accepted while emitting `<br>` inside `<code>`, preserving the two spaces incorrectly, or producing a different number of breaks.

**Recommendation:** Expand Task 1’s RED criteria with exact source and expected HTML for consecutive breaks, a final break, and a code span crossing a two-space newline; explicitly state whether the code span receives a space, literal spaces, or `<br>`.
**Ambiguity:** The plan names the tests but does not define the behavior they must assert.

### 2. [Medium] ambiguity · high confidence — Task 2 — RED decorator criteria

> RED: per-template — tpl-<name> body class present for 6 non-plain templates, absent for plain; template CSS layer markers present per template, absent from plain; dashboard has _DASHBOARD_STYLE marker + roadmap markup; report decorates `3/5` → span.score; review decorates `Severity: High` → span.sev.sev-high; spec decorates MUST/MUST NOT/SHOULD/SHOULD NOT/MAY → 5 req classes (negative forms styled); decorator patterns inside backtick code spans stay undecorated; injection attempt in decorated line stays escaped; _inline closed-grammar guard (emits only attribute-free code/strong); CLI accepts all 7 --style values, rejects junk.

Decorator recognition boundaries and precedence are undefined. For example, matching MUST before MUST NOT can partially or doubly decorate the negative form, while an unrestricted `3/5` matcher can decorate fractions embedded in larger values or unrelated prose. The listed happy-path fixtures cannot distinguish these implementations.

**Recommendation:** Add a decorator grammar to Task 2 defining longest-match precedence, word/token boundaries, permitted contexts, and non-matches. Add fixtures for MUST NOT and SHOULD NOT overlap, substrings such as MUSTARD, larger fractions such as 13/50, existing emphasis, and adjacent punctuation.
**Ambiguity:** The examples establish desired positive matches but not the matching grammar.

### 3. [Medium] completeness · high confidence — Plan header / Design reference

> Design: claude_docs/.epic-333-scratch/design-344.md (v2 + pass-2 amendments — amendments authoritative where they supersede body text: F1 req slugs, F2 placeholder hard-breaks, F3/F4 config semantics, F5 dashboard accent w/o summary-row).

Core requirements are delegated to an external design whose body and amendments are not present. Consequently, the seven template names, exact CSS/markup contracts, configuration semantics, and amendment precedence cannot be verified from the provided text; an implementer cannot determine whether the listed fixtures cover the authoritative requirements.

**Recommendation:** Make the plan self-contained by adding a “Normative contracts” section that enumerates all seven style names and reproduces the final amended behavior for F1–F5 and design items 1 and 8, including exact expected outputs.

### 4. [Medium] completeness · high confidence — Task 6 — verification

> verification: registration test green at 3.32.0; splice guard; whole suite green; changelog tail tokens (no-spine-change diagram decision + Suite 2556→N)

`N` and “splice guard” are unresolved acceptance criteria. The plan neither defines the final suite count nor says how it is derived and checked, so the changelog can retain a placeholder or report a count that differs across test selections.

**Recommendation:** In Task 6, replace `N` with a deterministic rule and command for obtaining the count, require a check that README contains the resulting numeric value and no `→N` placeholder, and define the exact splice-guard assertion.
**Ambiguity:** Neither the expected suite count nor the meaning of the splice guard is stated.

### 5. [Medium] feasibility · high confidence — POST-MERGE follow-through

> POST-MERGE (outside PR, workspace-side follow-through): edit /home/rocky00717/rawgentic/.claude/skills/design-doc-publish/SKILL.md to `--style design`; record follow-up re other checkouts.

A required behavior change is deferred outside the PR to a machine-specific absolute path, with no owner, tracked work item, existence/configuration proof, or verification step. The release can therefore merge successfully while the actual workspace continues invoking the old style, and other checkouts may remain inconsistent.

**Recommendation:** Replace the POST-MERGE note with a tracked deployment task that names an owner and issue, discovers the configured workspace path rather than assuming this absolute path, verifies the surface invokes `--style design`, and records completion for every supported checkout.

### 6. [Medium] security · medium confidence — Task 1 — GREEN implementation

> GREEN: buffer + placeholder-token algorithm (design item 1 as amended by v2-F2). Update module docstring paragraph contract.

The placeholder-token algorithm has no collision or provenance rule. If artifact text can contain the chosen token, replacement can convert user content into generated markup or otherwise corrupt it; this is especially material because the task explicitly restructures the escaping path.

**Recommendation:** In Task 1, specify a collision-safe token scheme or structural sentinel representation, and add fixtures containing literal and escaped forms of every placeholder token to prove they round-trip without becoming `<br>` or other markup.
**Ambiguity:** The token format and replacement stage are not specified, so collision safety cannot be verified.

---
_Report-only: this review does not edit the artifact. Findings are advisory; incorporate them at your discretion._