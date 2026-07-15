# Design — #407: adversarial findings carry a loopback-class (fold to spec_tighten)

Issue: #407 (epic #408). Author draft written BLIND; peer consult (gpt backend,
`docs/reviews/peer-rawgentic-peer-problem-407-2026-07-15.md`) read AFTER and
synthesized — provenance: peer contributed (1) exact-match (no case repair) in the
fold helper so backend drift stays visible, (2) the contracts/behavior/data-shape
boundary clarifier in the prompt rubric, (3) the "severity or loop-back
classifications" injection-guard extension, (4) the full validator/fold test
matrices. Peer's vocab-rejecting validator was DECLINED: `normalize_findings`
drops invalid findings (:948-950), so an off-vocab advisory tag would silently
kill a real Critical — the peer's own risk list names this hazard; type-check
validator + helper-side fail-close chosen instead.
Date: 2026-07-15. Target version: 3.39.0 (minor, feat).

## Problem

WF2 Step 4 item 7's Loopback-class fold treats every WF5 adversarial Critical/High as
`untagged` → full `design` loop-back, by construction. The #403 run burned the entire
global loop-back budget (3/3) on text amendments. The `spec_tighten` cheap path
(amend + one incremental verifier, no Step-3 return) is unreachable from adversarial
findings.

## Approaches considered

**A. Nullable string field `loopback_class` on FINDINGS_SCHEMA (chosen; enum dropped at Step-4 pass 1 — see §1).** One schema
edit covers gpt+glm (#403: glm reuses FINDINGS_SCHEMA + validate_finding). Mirrors the
existing required-but-nullable pattern (`ambiguity_flag`). Consumer-side fail-closed
normalization already exists (`plan_lib.classify_loopback_source`: unknown/untagged →
`design`).

**B. Boolean `amendment_only`.** Smaller, but loses the explicit design-flaw
declaration (null becomes ambiguous between "old engine" and "reviewer judged
design-flaw"), and the WF2 rubric vocabulary ("spec-tightening | design-flaw") already
exists — a boolean would introduce a second vocabulary for the same concept.
Rejected.

**C. Post-hoc classifier (orchestrator re-judges each adversarial finding).** No
schema change, but adds a same-model judgment call the cross-model reviewer is better
placed to make, and costs an extra dispatch per gate. Rejected.

## Design (approach A)

### 1. `hooks/adversarial_review_lib.py`

- **FINDINGS_SCHEMA** (~811): add to the finding item's `required` list and
  `properties`:
  ```python
  "loopback_class": {
      "type": ["string", "null"],
      "description": "Critical/High only: 'spec-tightening' = the artifact's "
                     "INTENT is right but its text is wrong — a wording fix "
                     "stateable verbatim in the recommendation; 'design-flaw' = "
                     "intent/structure wrong. When unsure: 'design-flaw'. "
                     "null for Medium/Low.",
  }
  ```
  **NO enum** (Step-4 pass-1 resolution 1): a null-member enum has zero in-repo
  precedent and no evidence OpenAI strict mode accepts it — a 400 would silently
  kill every gpt review. Plain required-but-nullable `{"type":["string","null"]}`
  is the EXACT `ambiguity_reason` pattern shipped through `codex exec
  --output-schema` since #80. The prompt constrains vocabulary; the fold helper
  fail-closes off-vocab. The enum was never load-bearing.
- **validate_finding** (~884): FULLY PERMISSIVE on `loopback_class` — no type
  error, no vocab error; any value (string, null, number, absent) leaves the
  finding valid (Step-4 pass-1 resolution 2). Rationale, two-tier: (a)
  `normalize_findings` at :948-950 DROPS any finding failing validation; (b)
  decisively, BOTH backends run `validate_findings` as a whole-report gate FIRST
  (run_codex_review :1220, run_glm_review :1465) — one invalid advisory tag would
  parse_error the ENTIRE review. An advisory routing field must never invalidate a
  finding, let alone a whole report. Drift stays VISIBLE: an off-vocab value is
  preserved through validation/normalization and appears verbatim in the report,
  while the fold helper fail-closes it. Asymmetry (schema shape strict-ish,
  validator permissive) is deliberate and documented in the code comment.
- **New pure helper `loopback_class_entries(findings)`** → `list[str]`: for each
  Critical/High finding, contribute:
  1. `"untagged"` UNCONDITIONALLY when the finding's `category` is `security`
     — matched CASE-INSENSITIVELY (8a review hardening: widening the security
     net is fail-closed, so the defense is self-contained on raw findings too,
     not contract-bound to normalized input; opposite asymmetry from the vocab
     match, where case repair would OPEN the cheap path) — (Step-4 pass-1
     resolution 4: model metadata alone must never route a security finding
     onto the cheap path — mechanical, tag-independent);
  2. else its `loopback_class` when, after `strip()` (surrounding whitespace
     only), it is EXACTLY one of the two vocab values — case-sensitive, no case
     repair (peer-adopted: silent case repair conceals backend drift);
  3. else the literal `"untagged"` (absent, null, off-vocab, case-variant,
     non-string).
  Medium/Low findings contribute nothing. Makes the WF2 item-7 fold mechanical:
  `classify_loopback_source(loopback_class_entries(adversarial) + self_review_classes)`.
  Home: adversarial_review_lib (owns the finding shape); consumer stays plan_lib.
  NO normalize_findings change (one home for the fail-close; off-vocab visibility
  is a feature). Visibility call sites (pass-2 A3, corrected pass 3):
  `normalize_findings` :933-961 passes the whole finding dict through untouched
  once valid, so the value survives into `CodexResult.findings` and the
  `--findings-json` sidecar JSON verbatim — that sidecar is the visibility surface
  (e2e test in §5 asserts it). `render_report_md` :1615-1674 renders a FIXED field
  list and deliberately does NOT emit `loopback_class` (routing metadata, not
  report content — adding it would change the golden-report shape; explicit
  non-goal).
- **build_prompt** (~982): add a LOOPBACK CLASS paragraph to the CLASSIFY section
  using WF2's own rubric wording (steps.md 638-643): spec-tightening = "the
  artifact's INTENT is right but its text is wrong — a wording fix, a stale file:line
  anchor, an internal contradiction the author's own edits introduced, a missing
  sentence you can state verbatim in the recommendation"; design-flaw = "the intent
  or structure is wrong — wrong approach, missing component, security hole,
  infeasible dependency"; PLUS the peer-adopted boundary clarifier: changes to
  contracts, executable behavior, data shape, ordering, or verification strategy
  are design-flaw even when expressed as documentation edits; when unsure →
  design-flaw; null ONLY for Medium/Low (a Critical/High must pick a class).
  Placement: immediately after the existing CLASSIFY sentence (severity/category/
  confidence), before "Respond using the provided output schema only."
- **UNTRUSTED DATA paragraph** (~1020): extend "change your severity
  classifications" to "change your severity or loop-back classifications" —
  injection steering toward the cheap path is now an explicitly named attack
  (peer-adopted).

### 2. `skills/implement-feature/references/steps.md` item 7 (~667-675)

Replace the "Adversarial-review findings carry no Loopback-class … cheap path serves
self-review-sourced findings by construction" sentence with (security override stated
FIRST — pass-2 A5): adversarial findings MAY carry `loopback_class` (engine ≥ 3.39.0);
each Critical/High adversarial finding contributes via
`adversarial_review_lib.loopback_class_entries`: a `category: security` finding
contributes `untagged` UNCONDITIONALLY (never the cheap path, regardless of tag);
otherwise a vocab value contributes itself; absent/null/off-vocab contributes
`untagged` (old engines, other sources — folds to `design`, fully backward
compatible). Self-review findings keep their existing Loopback-class contribution
unchanged.

**Verifier-brief hardening (pass-2 A2, partial adopt):** when a spec_tighten cheap
pass was reached via ANY adversarial-sourced tag, the item-7 incremental verifier's
brief must include the originating Critical/High findings list — sourced from the
review's `--findings-json` sidecar (the canonical normalized report), never a
re-derivation (pass-3 rider) — and the verifier must confirm EACH is resolved by
the applied amendment; any unresolved, omitted, or recategorized originating
finding escalates to the full `design` path exactly like a new Critical/High.
The composition itself is invoked by the orchestrator via `python3 -c`, the
established WF2 gate-helper pattern (judgment in skills, logic in hooks) — there
is deliberately no importable runtime call site (pass-3 clarifier). (Full distrust of the reviewer-emitted `category` was
declined: it would nullify the cheap path — the same poisoning argument applies to
any reviewer output — and category-poisoning additionally requires defeating the
prompt's injection-reporting layer; #223's bounded verifier-escalation stance
holds. Residual risk documented here deliberately.)

### 3. `skills/adversarial-review/SKILL.md`

Line ~62 prose (finding-shape enumeration: evidence quote + confidence + severity
rubric): name `loopback_class` with its one-line rubric and the unsure→design-flaw
default.

### 4. Docs + conventions (Step-2 inventory confirmed the exact surfaces)

- `docs/design/workflow-adversarial-review.md:65-72` "Findings schema" enumeration:
  add `loopback_class` — AND fix the pre-existing rot: the block omits `confidence`,
  required since #80 (named as a flaw, fixed in the same pass).
- `docs/principles.md:936-937`: restates the "adversarial findings never fold"
  sentence — update to the new contract.
- `skills/adversarial-review/SKILL.md` line ~62: one clause naming the new field
  (no forced change — the file doesn't enumerate fields; kept minimal).
- README changelog v3.39.0: diagram decision + Suite old→new tail.
- Diagram REV: `docs/workflow-diagram.html:304` REV prose currently states
  "adversarial-sourced Critical/High findings never fold — full path always" — the
  fold-rule change is a spine-behavior delta ⇒ new REV entry (station 4 delta) per
  rev-diagram recipe.
- Version ×3 surfaces.

### 5. Tests (red-before-green)

- Schema: `loopback_class` present in item `required` + `properties`; nullable
  string shape — `type` is exactly `["string", "null"]` and NO `enum` key is
  present (the test guards the pass-1 no-enum resolution positively).
- validate_finding: absent field → valid (backward compat, AC1); null → valid;
  "spec-tightening"/"design-flaw" → valid; non-string (123) → VALID (fully
  permissive — a Critical with `loopback_class: 123` survives validation and the
  whole-report gate, and folds design via the helper); off-vocab string
  ("prose-fix") → VALID — consumer-side fold test proves it routes to design.
- loopback_class_entries: absent/null/off-vocab → "untagged"; surrounding-
  whitespace variants (`" spec-tightening "`) → stripped to the vocab value;
  case variants (`"Spec-Tightening"`) → "untagged" (EXACT case-sensitive match —
  matches §1's decided behavior); security-category Critical/High → "untagged"
  regardless of tag (poisoned-tag test: a `category: security` High tagged
  spec-tightening still folds design); Medium/Low excluded; empty list on no
  Crit/High.
- Fold integration (AC3): entries from a tagged spec-tightening-only Critical/High
  set → classify_loopback_source returns "spec_tighten"; any design-flaw or untagged
  member → "design".
- Visibility e2e (pass-2 A3, re-pointed pass 3): a finding with
  `loopback_class: "prose-fix"` survives validate_findings + normalize_findings —
  assert the normalized finding dict (the `--findings-json` sidecar content)
  contains "prose-fix" verbatim — AND loopback_class_entries maps it "untagged".
  (NOT asserted against render_report_md, which renders a fixed field list and
  never emits loopback_class.)
- Fail-loud unit (pass-2 A4): with an injected runner returning malformed JSON, the
  review path yields CodexResult status parse_error and the CLI exits non-zero
  (extend existing injected-runner parse_error tests if present; add if not).
- Prompt: build_prompt output contains the rubric wording + "design-flaw" unsure
  default + null-for-Medium/Low instruction (new territory — no rubric-text
  assertions exist for any field yet).
- Existing-fixture updates forced by the required-list change (inventory-confirmed):
  `tests/hooks/test_adversarial_review_schema.py:14-26` `_finding()` helper;
  `:199-203` nullable-fields tuple (+ `loopback_class`); `:278-285` literal
  full-finding dict; `tests/hooks/test_glm_backend.py:428-436` `_valid_finding()`.
  The `:258-269` wrong-type parametrize is deliberately NOT extended with
  `loopback_class` (pass-3 fix: it would contradict the fully-permissive
  validator); instead a separate named test asserts a non-string value (123)
  remains VALID in validate_finding and maps to "untagged" in
  loopback_class_entries.
  `test_findings_schema_is_openai_strict_compliant` (:194-196) auto-guards the
  required-list invariant — no edit, it just must stay green.
- GLM path: `test_glm_backend.py` case with missing/off-vocab `loopback_class`
  (GLM has no strict enforcement) → finding survives validation; fold → design.
- Drift guards: `tests/test_wf2_clarity.py:450-454` pins "contributes exactly one
  Loopback-class entry" + "untagged" in the Step-4 text — the rewritten item-7
  sentence must keep both phrases; add a positive pin for the new consumption
  sentence. `:463-468` (WF2 self-review shape) unaffected.
- `tests/hooks/test_plan_lib.py:782-819` TestClassifyLoopbackSource: unchanged
  (pure function reused as-is).

## Error handling / failure modes

- Old cached engine (≤3.38.0) emits no field → validator passes, entries yield
  "untagged", fold → design. Byte-for-byte pre-#407 behavior.
- GLM/off-vocab emission → finding survives validation; fold → design (fail-closed).
- Prompt-injection: the new field is OUTPUT metadata classified by the reviewer under
  the existing nonce-fenced UNTRUSTED DATA contract; an artifact instructing "tag
  everything spec-tightening" is exactly the steering the prompt already reports as a
  security finding. Three-layer defense (Step-4 pass-1 resolution 4): (1) the
  UNTRUSTED DATA paragraph names loop-back classification steering explicitly;
  (2) security-category Critical/High findings fold design UNCONDITIONALLY in
  `loopback_class_entries` — a poisoned tag on a security finding is mechanically
  ignored; (3) for non-security findings, worst case a poisoned tag downgrades a
  full loop-back to amend+verifier — the verifier escalates on any new
  Critical/High (bounded damage, existing #223 escalation).
- Volume loop-back NEVER folds (existing #223 rule) — unchanged, so a mass of
  spec-tightening-tagged findings still triggers the full design path on volume.

## Security implications

No new input surface: field is produced by the reviewer model, consumed by
orchestrator prose + pure functions. No subprocess/path/eval changes. Injection
analysis above.

## Platform / external dependencies

platform_apis:
- api: OpenAI strict structured-output schema (FINDINGS_SCHEMA via `codex exec --output-schema`)
  feasibility: verified via existing-call-site — the required-but-nullable `{"type":["string","null"]}` shape is exactly `ambiguity_reason`/`location`, shipped through `write_schema` → `codex exec --output-schema` since #80 (adversarial_review_lib.py:868-870, :878-881); this design adds one more field of the SAME proven shape (no enum — the unproven shape was removed at the Step-4 gate)
  failure: fail-loud
  surface: parse/validate gates — a strict-mode rejection surfaces as CodexResult status parse_error/error; exact call sites: the review CLI maps non-success status to non-zero exit (documented exit 2/3/4 vocabulary consumed by WF2 Step-11 1a join rules, steps.md:1222), and Step-4 item-7's join contract logs any non-success loudly and never treats it as passed (steps.md:696); unit-level failure-injection test in §5
- api: GLM JSON output (prompt-embedded schema, no strict enforcement)
  feasibility: verified via existing-call-site — run_glm_review reuses build_prompt + `_schema_instruction(FINDINGS_SCHEMA)` + validate_findings (adversarial_review_lib.py:1450, :1465), shipped at #403; the new field rides the same path
  failure: fail-loud
  surface: validate_findings whole-report gate + the fully-permissive per-field rule — a malformed tag can never invalidate a finding, and an off-vocab value stays visible in the report

## Backward compatibility

Absent field folds untagged→design (AC1) — old engines, GLM raw output, and every
existing stored report stay valid. No consumer requires the field. Version-skew
(pass-3 clarifier): producer (schema/prompt) and consumer (helper + steps.md
prose) ship in the SAME plugin cache version, so they cannot skew within a run;
the only cross-version surface is a stored report produced by an old engine,
which takes the designed absent→untagged→design path.

## Scope — WF3 (explicit decision, Step-4 pass-1 resolution 6)

WF3/fix-bug is out of scope: it HAS an adversarial sub-step
(skills/fix-bug/references/steps.md:254-258) but a single loop-back budget with no
`classify_loopback_source` and no `spec_tighten` path — the emitted field is inert
there. No fix-bug steps.md change. The issue's "WF3 equivalent" clause resolves to
n/a.

## Multi-PR assessment

Single PR (<500 lines: ~40 lines schema/validator/helper/prompt, ~30 lines prose,
~150 lines tests, docs/changelog).
