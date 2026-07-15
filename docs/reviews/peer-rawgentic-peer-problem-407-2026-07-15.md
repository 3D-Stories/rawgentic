# Peer Consult — .rawgentic-peer-problem-407.md

- Date: 2026-07-15
- Reviewer: Codex (peer designer)

## Approach

Introduce `loopback_class` as a bounded, nullable classification emitted by current WF5 backends and consumed by WF2’s existing fold. In `FINDINGS_SCHEMA`, add `loopback_class` to both `properties` and `required`, with `{"type":["string","null"],"enum":["spec-tightening","design-flaw",null]}`. This satisfies strict structured-output requirements while preventing arbitrary instruction-bearing text. Keep wire-level backward compatibility in `validate_finding`: absence is accepted and normalized by consumers to `untagged`; explicit `null` has the same meaning. Place classification guidance immediately after the severity rubric in `build_prompt`, so severity is selected first and loopback class is then determined independently. Update WF2 item 7 to map each adversarial Critical/High finding as follows: `spec-tightening` contributes `spec-tightening`; `design-flaw` contributes `design-flaw`; absent, null, or otherwise unavailable contributes `untagged`. Pass those values unchanged to `classify_loopback_source`, preserving its all-spec-tightening cheap-path rule and conservative design fallback.

## Key decisions

- Use the name `loopback_class`, matching the established fold vocabulary and avoiding the ambiguity of a boolean such as `amendment_only`.
- Use schema-level enum-with-null rather than nullable free-form strings. The vocabulary is closed, both backends share the schema, invalid values should be rejected at generation time when possible, and the field cannot carry injected prose.
- Add `loopback_class` to strict-schema `required`, but make it nullable. Current structured-output engines must emit the key; legacy or externally supplied findings may omit it and remain valid through the independent validator.
- In `validate_finding`, accept an absent key, `null`, `"spec-tightening"`, or `"design-flaw"`; reject booleans, other types, differently spelled values, and off-vocabulary strings. Do not normalize case or whitespace in the validator because silently repairing model output can conceal schema/backend drift.
- Treat validator failure according to the existing finding-validation policy, but never let an invalid value unlock the cheap path. If invalid findings can continue fail-open, erase the invalid classification to `untagged` and record validation diagnostics; otherwise reject the finding normally.
- Prompt wording: `After assigning severity, classify the loop-back needed for each finding. Use loopback_class="spec-tightening" only when the artifact's INTENT is right but its text is wrong: the complete fix is a wording amendment that you can state verbatim in Recommendation. Use loopback_class="design-flaw" when intent, behavior, structure, interfaces, sequencing, or validation strategy must change. If the boundary is uncertain, use "design-flaw". Use null only when loop-back classification is not applicable.`
- Keep classification independent of severity. The field may be present on every finding because strict mode requires it, but WF2 consumes it only for the Critical/High set participating in loopback selection.
- Replace item 7’s unconditional adversarial-to-untagged rule with: `For each adversarial-review Critical/High, contribute its loopback_class when it is spec-tightening or design-flaw. If the field is absent or null, contribute untagged. Fold all contributing findings with classify_loopback_source: only an all-spec-tightening set selects spec_tighten; any design-flaw, untagged, empty, or unknown contribution selects design.`
- Do not change `classify_loopback_source`; its conservative behavior is already the desired policy boundary.
- Red-before-green tests should first demonstrate failures for: schema omission of the new property; current prompt omission; tagged adversarial findings being forced to untagged; and validator rejection/handling gaps. Then implement schema, validation, prompt, and workflow prose changes.
- Schema tests should assert the property is required, nullable, enum-bounded, and compatible with strict mode. Validate both legal strings and explicit null.
- Validator tests should cover valid `spec-tightening`, valid `design-flaw`, explicit null, absent-field legacy input, off-vocabulary text, empty string, case/whitespace variants, boolean, number, list, and object.
- Fold integration tests should construct adversarial Critical/High inputs and assert: all tagged spec-tightening => `spec_tighten`; any design-flaw => `design`; any absent tag => `design`; any null tag => `design`; mixed spec-tightening and untagged => `design`; mixed spec-tightening and design-flaw => `design`. Also preserve self-review and empty-input behavior.
- Backend contract tests should exercise both GPT and GLM response paths through the shared schema and validator, including a legacy/cached payload without the field.
- Prompt tests should drift-guard the two exact vocabulary tokens, the intent-right/text-wrong criterion, the Recommendation-verbatim constraint, and the uncertain-to-design-flaw default.
- Complete repository bookkeeping with the established three version locations, changelog entry, decision diagram update, Suite-tail/count adjustments, and documentation of strict-current versus tolerant-legacy behavior.

## Risks

- A strict current engine cannot omit a required key, while cached or older engines can. Conflating schema conformance with legacy ingestion would either break strict mode or break backward compatibility; the schema and validator intentionally have different acceptance boundaries.
- Using `null` too freely could make new-model findings consume the full design path. The prompt should reserve null for genuinely non-applicable cases and require one of the two classes for actionable Critical/High findings, while consumers still fail closed.
- Misclassification could under-escalate a real design defect into a cheap prose pass. The narrow spec-tightening definition, verbatim-amendment requirement, all-members fold, and unsure-to-design-flaw rule reduce this risk.
- A text-only recommendation may still have downstream behavioral implications. Prompt examples or tests should clarify that changes to contracts, executable behavior, data shape, ordering, or verification strategy are design flaws even when expressed as documentation edits.
- If off-vocabulary values are coerced through case or whitespace normalization, backend drift can go unnoticed. Exact validation plus conservative `untagged` fallback prevents accidental cheap-path eligibility.
- If invalid classification causes the entire finding to be dropped, a serious Critical/High could disappear from the fold. Validation handling must retain the finding and downgrade only its class to `untagged`, unless the existing pipeline already aborts safely on malformed findings.
- Prompt injection in reviewed artifacts may try to influence classification. The enum prevents the field itself from carrying instructions, but the reviewer can still be induced to choose the cheaper token. Existing artifact-delimiting and untrusted-content guidance should explicitly cover classification, and uncertainty must resolve to design-flaw.
- String-based documentation drift guards can become brittle when prompt prose changes. Assert essential semantic phrases and schema tokens separately rather than snapshotting the entire prompt.
- Version/count updates are easy to miss because the functional change is concentrated in one shared schema. Repository-wide convention tests should identify every required bookkeeping location.

## Sketch

FINDINGS_SCHEMA.properties.loopback_class = { type: ["string", "null"], enum: ["spec-tightening", "design-flaw", null] }
FINDINGS_SCHEMA.required += ["loopback_class"]

validate_finding(finding):
  if "loopback_class" not in finding:
    accept  # legacy payload; consumer treats as untagged
  else if finding.loopback_class is null:
    accept
  else if finding.loopback_class in {"spec-tightening", "design-flaw"}:
    accept
  else:
    validation error; if pipeline retains malformed findings, clear only this field to untagged

WF2 Step 4 item 7:
  classes = []
  for finding in participating_critical_high_findings:
    if source == adversarial_review:
      value = finding.get("loopback_class")
      classes.append(value if value in allowed_classes else "untagged")
    else:
      classes.append(existing_self_review_mapping(finding))
  loopback = classify_loopback_source(classes)

Expected fold:
  [spec-tightening, spec-tightening] -> spec_tighten
  [spec-tightening, design-flaw]     -> design
  [spec-tightening, untagged]        -> design
  [spec-tightening, null/absent]     -> design

---
_Peer proposal (report-only). Synthesize at your discretion._
