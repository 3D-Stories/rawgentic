# Design — Platform / external-dependency feasibility gate (issue #226)

<!-- Visual artifact (hosted): https://claude.ai/code/artifact/f616b97a-c4bf-47e8-b1ff-7081356624d2 · committed standalone render: 2026-07-06-platform-feasibility-gate.html -->


**Workflow:** WF2 Step 3 · Status: design (Step 4 pending) · Complexity: standard_feature · Blast: low code, prose-heavy

## Problem

A design can commit to a platform/framework API the project's own configuration does not
permit and still pass **every** current gate, because the gates are test-centric and the
failure only manifests at runtime on a surface CI never exercises. Motivating real run: a
Tauri 2 overlay used `window.setSize`; that window's capability file lacked `allow-set-size`
(the main window had it — a visible asymmetry in the repo). The call was silently denied
(error only in the webview console); `cargo` compile, `tsc`, and unit tests were all green.
Three build-and-UAT cycles to catch.

**Unifying principle:** an unproven external/platform dependency is a design-gate blocker,
verified against the project's real config — not assumed from the API's existence.

## Approaches weighed

- **A. Evidence-cited feasibility note + review lens + tiny validator (CHOSEN).** A design
  declares a `feasibility:` note per unproven platform API, cited to real evidence; the
  design gate (Step 4 self-review + the WF5 adversarial lens) checks it; a small `plan_lib`
  helper mechanically rejects an "assumed" dep; drift guards pin the prose. Generic and
  framework-agnostic.
- **B. Automated capability-model parser** (parse Tauri capability files, iOS entitlements,
  browser permission policies, …). **Rejected** — explicitly out-of-scope in the issue: it
  is per-framework, unbounded to maintain, and the issue wants a *generic, evidence-cited*
  mechanism, not an auto-parser. Evidence is a cited file/doc/call-site/spike, not a parse.

## Mechanism

### The canonical feasibility contract (peer-refined + adversarial-hardened)

**A `platform_apis:` declaration is MANDATORY on every design doc / RCA** — the key decision
resolving the adversarial High findings (Adv#1/#2): an *omitted* note must not pass silently,
which is the exact silent-gap class #226 targets. It is exactly one line when there are no
platform APIs — symmetric with the already-required "Security implications" section, so it is
not the "feasibility proof for APIs that are fine" over-gating the issue warns against:

```md
## Platform / external dependencies
platform_apis: none        # <- the whole declaration when no material platform/external API is used
```
or, when material platform/framework/external APIs (not already proven in-repo the same way)
are used, one block per API:
```md
platform_apis:
- api: <exact API> on <exact object/runtime surface>
  feasibility: verified via <capabilities-file|existing-call-site|spike> — <citation>
  failure: fail-loud | fail-silent
  surface: <assertion|log|observable check> — <where>   # REQUIRED when failure: fail-silent
```

Rules:
- `feasibility: assumed` may appear as an **interim Step-3 drafting marker**, but is explicitly
  **Step-4-blocking** (peer decision — draft, then resolve before the gate).
- **`docs` is NOT an accepted evidence kind (adversarial-diff review, hardened from Adv#5).**
  Docs prove an API *exists*, not that this project's config *permits* it — accepting
  `verified via docs` for a permission/capability-gated API is the exact silent gap #226
  targets (docs say `setSize` exists; the capability file denies it). `assert_feasibility_declared`
  rejects `docs`; cite a `capabilities-file`, an exact `existing-call-site`, or a `spike`. (The
  first draft accepted docs and deferred credibility to review; a cross-model diff review flagged
  that as a fail-open — closed mechanically instead.)
- **Working-precedent (AC3):** an `existing-call-site` counts only for the **exact** API on the
  **exact** object kind + target surface (the main-window-has-it/overlay-doesn't asymmetry);
  otherwise spike or cite config.
- The full contract lives **once** in WF2 §3; WF3 and the lens carry a short checklist +
  pointer, not a duplicate (peer DRY guidance).

### 1. `plan_lib` feasibility-note validator (the only new code)
Mirror the existing `parse_tasks` style. Grammar (Adv#3): a `platform_apis:` line starts the
declaration; `none` on the same line = no APIs; otherwise each subsequent `- api:` opens a
block whose `feasibility:`/`failure:`/`surface:` fields belong to that block until the next
`- api:` or a non-indented line. Helpers:

- `parse_feasibility_block(text) -> FeasibilityDecl | None` — returns `None` when NO
  `platform_apis:` declaration is present (so the caller can distinguish "no declaration" from
  "declared none"); code-fence aware (both ` ``` ` and `~~~`, so a doc quoting the contract is
  not mis-parsed); collects ALL non-fenced declarations — **>1 sets `ambiguous=True`** so an
  early stray `platform_apis: none` can't shadow a later real one (review finding).
  - `ApiFeasibility(api, status, kind, citation, failure, surface)`; `FeasibilityDecl(present, none, apis, ambiguous)`.
- `assert_feasibility_declared(decl) -> (ok, errors)` — the mechanical Step-4 gate, **fail-closed**:
  - `decl is None` (**declaration absent**) → error (Adv#1/#2 — omission caught).
  - `decl.ambiguous` (>1 declaration) → error.
  - `decl.none` → ok.
  - else per api block: `assumed` → error; `verified` with kind ∉ {capabilities-file, existing-call-site, spike} (docs excluded) or empty citation → error; `failure: fail-silent` with empty `surface` → error (AC4 mechanical); missing `failure` → error.

Separator tolerance: the `verified via <kind> <sep> <citation>` split accepts em-dash, en-dash,
minus, hyphen, and colon (editor smart-punctuation); dashless-indented fields only (a `- field`
line is a new item, not a bleeding sibling); unit tests cover each.

No new dataclass field is needed for AC5 — the existing `deferral_reason` (free text) and the
run-record's `verification_deferred[].target_check` already carry "the claim most likely to
be wrong." AC5 is prose that *uses* that field (Step 9 makes it explicit that, for a runtime
deferral, `target_check` NAMES the single most-likely-wrong claim). A mechanical "is this the
*most* likely wrong claim?" check is impossible — it is prose judgment — so the drift guard
pins the requirement rather than a validator.

### 2. Prose edits (the bulk)

| AC | Where | What |
|---|---|---|
| 1,3,4 | WF2 `steps.md` §3 (design-doc structure, "for all project types") | New **required** "Platform / external dependencies" section carrying the mandatory `platform_apis: none \| <api-blocks>` declaration (contract above). Working-precedent rule (exact API on exact object kind; the main-window/overlay asymmetry). Silent-failure rule (`failure: fail-loud\|fail-silent` per API; `fail-silent` requires `surface:`). |
| 2 | WF2 `steps.md` §4 (design critique dimensions) | New "Platform / external-dependency feasibility" dimension; runs `plan_lib.assert_feasibility_declared(parse_feasibility_block(<design>))`; a non-ok result (absent declaration, `assumed`, weak evidence, or `fail-silent` w/o `surface`) is a **blocking** finding. Lens also judges `docs` credibility for config-gated APIs (Adv#5) and flags a used-but-undeclared API. |
| 2 | `quality-bar.md` (×3, kept byte-identical) | Add `platform_feasibility` to the Category enum + a stance bullet ("platform feasibility is evidence, not assumption"). |
| 2 | `adversarial_review_lib.py` `_TYPE_LENS["design"]` + `["plan"]` | Append the platform-constraints lens so the WF5 cross-model review asks the same question. |
| 5 | WF2 `steps.md` §9 (deferred-to-target block) | Runtime-surface rule: for behavior that only manifests at runtime (UI, permission-gated, GPU/audio), require a real-surface spike OR a `deferred-to-target` whose reason NAMES the single feasibility claim most likely to be wrong (carried into `target_check`). "Deferred, unspecified" does not satisfy it. |
| 6 | WF2 `steps.md` §8 (implementation/mid-flight) | Mid-flight feasibility check: a fix/iteration that introduces a NEW platform API bypassing the design gate applies AC1/3/4 in miniature before committing, note recorded in session notes. |
| 6 | WF3 `fix-bug/references/steps.md` §3 (RCA) + §4 (reflect) + SKILL.md spine one-liners | Mirror: fix approach carries a feasibility note when it relies on a platform API; the reflect dimensions + shared quality-bar lens apply. This is where the real failure lived (a mid-UAT fix). |
| 7 | `tests/test_feasibility_gate.py` (new) | Unit tests for the two helpers (verified ok / assumed fails / verified-no-evidence fails / empty ok) + corpus drift guards asserting the §3 note requirement, §4 + `_TYPE_LENS` lens, §9 runtime-surface rule, quality-bar `platform_feasibility`, and the WF3 mirror are all present. |

## Failure modes / over-gating traps (the main risk)
- **Over-gating** (issue's stated risk): feasibility *proof* is scoped to APIs **not already
  proven in-repo the same way** — a precedented exact call site is sufficient, so well-worn
  APIs never trip it. The mandatory declaration is one line (`platform_apis: none`) when no
  platform API is used — symmetric with the always-required "Security implications" section,
  not proof-for-everything.
- **False "verified"**: `assert_feasibility_declared` requires an allowed evidence **kind**
  (`docs` excluded) AND a non-empty citation, so `verified via docs`, an unknown kind, or a
  citation-less `verified` all fail closed.
- **Silent skip of the mirror**: the drift guard pins the WF3 §3/§4 text so a later prose
  edit can't quietly drop it.

## Platform / external dependencies
platform_apis: none
<!-- Dogfood: this feature is pure Python stdlib (re/dataclasses) + workflow prose — no
     platform/framework/external API. It satisfies its own new Step-4 gate. -->

## Security / backward-compat
- Additive: the validator returns ok on the empty-note case, so every existing design/plan
  with no `feasibility:` lines is unaffected. No change to `parse_tasks`, the deferral
  machinery, or any existing contract.
- No new dependency. No migration. Pure-function helpers, unit-tested.

## Peer consult (provenance)
A blind cross-model peer consult (Codex, via `adversarial_review_lib.py consult`, per the
Step-3 opt-in) ran against the issue and converged on Approach A. Grafted contributions:
the **structured contract fields** (`api`/`feasibility`/`failure`/`surface`), `assumed` as an
interim-but-Step-4-blocking marker, **evidence-kind + citation** validation in the helper,
the **single-canonical-block + pointer** (DRY) placement for WF3/lens, and **stable-phrase**
(not whole-paragraph) drift anchors. Divergence resolved in scope: AC2 mandates a
`quality-bar.md` edit, so the `platform_feasibility` category + one stance bullet are added to
all three identical copies (inert in setup) to preserve the byte-identical invariant, kept to
2 lines to honor the peer's blast-radius caution.

## Step-11 review hardening (provenance)
The pre-PR review round (a cross-model adversarial diff review + two `rawgentic:rawgentic-reviewer`
agents, each confirming findings by executing the parser) closed five issues the first cut left
open — all corroborating the "close it mechanically, don't defer to review" direction:
- **`docs` fail-open (High):** the first cut accepted `verified via docs` and deferred credibility
  to the review lens; the diff review flagged that a permission-gated API could pass on docs alone
  (the exact #226 failure). Fix: `docs` removed from the accepted evidence kinds.
- **first-`none`-wins fail-open (Med):** an early stray `platform_apis: none` shadowed a later real
  declaration. Fix: >1 non-fenced declaration → `ambiguous`, fails closed.
- **`~~~` fences (Med):** only ` ``` ` was skipped. Fix: skip `~~~` too (4-space-indent noted as a
  residual, fail-*closed* gap).
- **en-dash separator (Low):** the "en/em-dash" comment omitted U+2013/U+2212. Fix: added.
- **`-?` sibling-field bleed (Low):** a stray `- feasibility:` bled into the prior block. Fix:
  dashless-indented fields only.
- **vacuous lens guard (Low):** the WF5-lens drift guard was file-scoped (phrase also in the plan
  lens). Fix: assert on `_TYPE_LENS["design"]` specifically.

## Pre-PR
Version minor 3.11.5 → 3.12.0; README + Changelog; this design md + html; **workflow-diagram
REV** (spine change: Steps 3/4/8/9 gain the feasibility gate); `pytest tests/ -v` green.
