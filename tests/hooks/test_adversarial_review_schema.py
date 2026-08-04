"""Tests for adversarial_review_lib findings schema + normalization (issue #77, Task 3)."""
import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

import adversarial_review_lib as arl  # noqa: E402


def _finding(**over):
    # ambiguity_flag/ambiguity_reason are "required-but-nullable" in
    # FINDINGS_SCHEMA (OpenAI strict structured-output requires every property in
    # `required`, recursively — see #80). A realistic finding always carries them
    # (null when not flagged), so the helper must too, or it diverges from the
    # schema the strict jsonschema test enforces. evidence (grounding quote) and
    # confidence are required + non-nullable.
    base = {"evidence": "a quoted span", "severity": "High", "category": "security",
            "confidence": "high",
            "description": "d", "recommendation": "r", "location": "S1",
            "ambiguity_flag": False, "ambiguity_reason": None,
            "loopback_class": None}
    base.update(over)
    return base


# --- validate_finding ---

def test_validate_finding_valid():
    ok, errs = arl.validate_finding(_finding())
    assert ok and errs == []


def test_validate_finding_bad_severity():
    ok, errs = arl.validate_finding(_finding(severity="Urgent"))
    assert not ok and any("severity" in e for e in errs)


def test_validate_finding_missing_description():
    ok, errs = arl.validate_finding(_finding(description=""))
    assert not ok and any("description" in e for e in errs)


def test_validate_finding_non_dict():
    ok, errs = arl.validate_finding("nope")
    assert not ok


def test_validate_finding_bad_ambiguity_type():
    ok, errs = arl.validate_finding(_finding(ambiguity_flag="yes"))
    assert not ok and any("ambiguity_flag" in e for e in errs)


def test_validate_finding_missing_evidence():
    # Grounding gate: a finding with no quotable evidence is exactly the generic
    # hallucinated nitpick the rule exists to drop.
    ok, errs = arl.validate_finding(_finding(evidence=""))
    assert not ok and any("evidence" in e for e in errs)


def test_validate_finding_evidence_non_string():
    ok, errs = arl.validate_finding(_finding(evidence=123))
    assert not ok and any("evidence" in e for e in errs)


def test_validate_finding_category_off_vocab():
    # category is now an enum; an off-vocab value (valid in old free-string schema)
    # must be rejected so it can't break report grouping.
    ok, errs = arl.validate_finding(_finding(category="design"))
    assert not ok and any("category" in e for e in errs)


def test_validate_finding_all_known_categories_accepted():
    for cat in arl.CATEGORIES:
        ok, errs = arl.validate_finding(_finding(category=cat))
        assert ok, (cat, errs)


@pytest.mark.parametrize("bad", [
    "sure", "", None, 1.5, -0.1, True, False, "1.5", "certain",
    float("nan"), float("inf"), [], {"v": 0.9},
])
def test_validate_finding_bad_confidence(bad):
    # Garbage refuses (#902 AC3): out-of-range, non-finite, bool (an int
    # subclass — never a confidence), unmappable strings, wrong types.
    ok, errs = arl.validate_finding(_finding(confidence=bad))
    assert not ok and any("confidence" in e for e in errs)


@pytest.mark.parametrize("good", [
    "high", "medium", "low",     # legacy words — tolerant GLM path (mapped)
    "HIGH", " high ",            # case/whitespace variants (mapped)
    0.9, 0.0, 1.0, 0, 1, 0.42,   # native numerics in [0, 1]
    "0.9",                       # numeric string — classic GLM slip (mapped)
])
def test_validate_finding_good_confidence(good):
    ok, errs = arl.validate_finding(_finding(confidence=good))
    assert ok, errs


# --- coerce_confidence (#902): numeric-or-word with provenance ---

@pytest.mark.parametrize("value,expected", [
    (0.9, (0.9, "native")),
    (0.0, (0.0, "native")),
    (1.0, (1.0, "native")),
    (0, (0.0, "native")),
    (1, (1.0, "native")),
    (0.42, (0.42, "native")),
])
def test_coerce_confidence_native_numbers_pass_through(value, expected):
    assert arl.coerce_confidence(value) == expected


@pytest.mark.parametrize("value,expected_float", [
    ("high", 0.9), ("medium", 0.7), ("low", 0.4),
    ("HIGH", 0.9), (" high ", 0.9), ("Medium", 0.7),
])
def test_coerce_confidence_words_map_with_mapped_flag(value, expected_float):
    # Word forms map through the ONE existing map (ADV_CONFIDENCE_TO_FLOAT —
    # AC1's 0.8/0.6/0.3 literals deliberately dropped, see the issue's
    # authority comment) and are flagged mapped, never silently native.
    assert arl.coerce_confidence(value) == (expected_float, "mapped")


def test_coerce_confidence_word_values_are_the_single_map():
    for word, val in arl.ADV_CONFIDENCE_TO_FLOAT.items():
        assert arl.coerce_confidence(word) == (val, "mapped")


@pytest.mark.parametrize("value,expected_float", [
    ("0.9", 0.9), ("0", 0.0), ("1", 1.0), (" 0.65 ", 0.65),
])
def test_coerce_confidence_numeric_strings_map_with_mapped_flag(value, expected_float):
    assert arl.coerce_confidence(value) == (expected_float, "mapped")


@pytest.mark.parametrize("value", [
    True, False,                       # bool is an int subclass — refused
    None, "", "sure", "very high",     # garbage
    1.5, -0.1, "1.5", "-3",            # out of range
    float("nan"), float("inf"), "nan", "inf", "1e300",  # non-finite / overflow
    [], {}, ("high",),
])
def test_coerce_confidence_garbage_refuses(value):
    assert arl.coerce_confidence(value) is None


# --- normalize_findings (#902): numeric confidence + confidence_source ---

def test_normalize_word_confidence_becomes_numeric_with_mapped_source():
    out = arl.normalize_findings([_finding(confidence="high")])
    assert len(out) == 1
    assert out[0]["confidence"] == 0.9
    assert out[0]["confidence_source"] == "mapped"


def test_normalize_native_confidence_keeps_value_with_native_source():
    out = arl.normalize_findings([_finding(confidence=0.85)])
    assert len(out) == 1
    assert out[0]["confidence"] == 0.85
    assert out[0]["confidence_source"] == "native"


def test_normalize_mixed_confidences_flag_per_finding():
    out = arl.normalize_findings([
        _finding(confidence=0.85, description="native one"),
        _finding(confidence="low", description="worded one"),
    ])
    by_desc = {f["description"]: f for f in out}
    assert by_desc["native one"]["confidence_source"] == "native"
    assert by_desc["worded one"]["confidence"] == 0.4
    assert by_desc["worded one"]["confidence_source"] == "mapped"


def test_normalize_does_not_mutate_caller_findings():
    f = _finding(confidence="high")
    arl.normalize_findings([f])
    assert f["confidence"] == "high" and "confidence_source" not in f


# --- validate_findings ---

def test_validate_findings_non_list():
    ok, errs = arl.validate_findings({"not": "a list"})
    assert not ok


def test_validate_findings_reports_index():
    ok, errs = arl.validate_findings([_finding(), _finding(severity="X")])
    assert not ok and any("finding[1]" in e for e in errs)


# --- normalize_findings: dedupe + rank ---

def test_normalize_ranks_by_severity():
    raw = [_finding(severity="Low", description="a"),
           _finding(severity="Critical", description="b"),
           _finding(severity="Medium", description="c")]
    out = arl.normalize_findings(raw)
    assert [f["severity"] for f in out] == ["Critical", "Medium", "Low"]


def test_normalize_dedupes_identical():
    raw = [_finding(description="same"), _finding(description="same")]
    out = arl.normalize_findings(raw)
    assert len(out) == 1


def test_normalize_keeps_findings_sharing_80char_prefix():
    # F2 regression: same first 80 chars, different tails -> must NOT collapse
    prefix = "Parameter validation missing at the request boundary in handler number forty-two zone "
    assert len(prefix) >= 80
    raw = [
        _finding(description=prefix + "(SQL injection vector)"),
        _finding(description=prefix + "(XSS injection vector)"),
    ]
    out = arl.normalize_findings(raw)
    assert len(out) == 2


def test_normalize_drops_invalid():
    raw = [_finding(), {"severity": "Bogus", "category": "x",
                        "description": "y", "recommendation": "z"}]
    out = arl.normalize_findings(raw)
    assert len(out) == 1


def test_normalize_non_list_returns_empty():
    assert arl.normalize_findings("nope") == []


def test_normalize_ranks_same_severity_by_category():
    raw = [_finding(severity="High", category="scope", description="a"),
           _finding(severity="High", category="completeness", description="b")]
    out = arl.normalize_findings(raw)
    assert out[0]["category"] == "completeness"  # alphabetical within severity


# --- schema ---

def test_schema_is_draft07_with_severity_enum():
    # write_schema was deleted with the old engine (#866 M0d) — the runner
    # serializes FINDINGS_SCHEMA itself; pin the schema content directly.
    assert arl.FINDINGS_SCHEMA["$schema"].endswith("draft-07/schema#")
    sev = arl.FINDINGS_SCHEMA["properties"]["findings"]["items"]["properties"]["severity"]
    assert sev["enum"] == list(arl.SEVERITIES)


def test_schema_validates_with_jsonschema(tmp_path):
    jsonschema = pytest.importorskip("jsonschema")
    doc = {"summary": "s", "findings": [_finding()]}
    jsonschema.validate(doc, arl.FINDINGS_SCHEMA)  # should not raise


def test_schema_rejects_bad_severity_with_jsonschema():
    jsonschema = pytest.importorskip("jsonschema")
    doc = {"findings": [_finding(severity="Nope")]}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(doc, arl.FINDINGS_SCHEMA)


# --- #80: OpenAI strict structured-output compliance ---

def _assert_strict(node, path="root"):
    """Recursively assert every object's `properties` keys all appear in `required`
    and additionalProperties is False (OpenAI strict structured-output rule)."""
    if isinstance(node, dict):
        if node.get("type") == "object" and "properties" in node:
            props = set(node["properties"].keys())
            required = set(node.get("required", []))
            missing = props - required
            assert not missing, f"{path}: properties not in required: {missing}"
            assert node.get("additionalProperties") is False, \
                f"{path}: additionalProperties must be False for strict mode"
        for k, v in node.items():
            _assert_strict(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            _assert_strict(v, f"{path}[{i}]")


def test_findings_schema_is_openai_strict_compliant():
    # Every key in properties must be in required, recursively (OpenAI strict mode).
    _assert_strict(arl.FINDINGS_SCHEMA)


def test_optional_finding_fields_are_nullable_in_schema():
    item_props = arl.FINDINGS_SCHEMA["properties"]["findings"]["items"]["properties"]
    for field in ("ambiguity_flag", "ambiguity_reason", "location", "loopback_class"):
        t = item_props[field]["type"]
        assert "null" in t, f"{field} must allow null (strict mode requires it in `required`)"


def test_category_is_enum_sharing_one_source_of_truth():
    item = arl.FINDINGS_SCHEMA["properties"]["findings"]["items"]
    assert item["properties"]["category"]["enum"] == list(arl.CATEGORIES)


def test_confidence_is_enum_in_schema():
    item = arl.FINDINGS_SCHEMA["properties"]["findings"]["items"]
    assert item["properties"]["confidence"]["enum"] == ["high", "medium", "low"]


def test_evidence_and_confidence_required_non_nullable():
    item = arl.FINDINGS_SCHEMA["properties"]["findings"]["items"]
    assert "evidence" in item["required"] and "confidence" in item["required"]
    # non-nullable: a plain string type, not ["string","null"]
    assert item["properties"]["evidence"]["type"] == "string"


def test_evidence_is_first_property_for_cot_grounding():
    # Emitting the grounding quote BEFORE the conclusion conditions generation on
    # real text — order matters, so lock it.
    props = list(arl.FINDINGS_SCHEMA["properties"]["findings"]["items"]["properties"])
    assert props[0] == "evidence"


def test_schema_has_no_strict_mode_rejected_keywords():
    # minLength/pattern/minItems/minimum/maxItems are rejected by OpenAI strict
    # mode (HTTP 400). Enforce non-emptiness in validate_finding, never the schema.
    banned = {"minLength", "maxLength", "pattern", "minItems", "maxItems",
              "minimum", "maximum", "format"}
    found = []

    def walk(node, path="root"):
        if isinstance(node, dict):
            for k, v in node.items():
                if k in banned:
                    found.append(f"{path}.{k}")
                walk(v, f"{path}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]")

    walk(arl.FINDINGS_SCHEMA)
    assert not found, f"strict-mode-rejected keywords present: {found}"


def test_validate_finding_accepts_null_optionals():
    # Codex now returns ALL fields (optionals as null) under strict mode.
    f = _finding(ambiguity_flag=None, ambiguity_reason=None, location=None)
    ok, errs = arl.validate_finding(f)
    assert ok, errs


@pytest.mark.parametrize("field,bad", [
    ("location", 123),
    ("location", []),
    ("ambiguity_reason", 42),
    ("ambiguity_reason", {"x": "y"}),
])
def test_validate_finding_rejects_wrong_type_optionals(field, bad):
    # Step 11 High: optionals are required-but-nullable in the schema, so a
    # non-null wrong TYPE must be rejected (not just null accepted).
    f = _finding(**{field: bad})
    ok, errs = arl.validate_finding(f)
    assert not ok and any(field in e for e in errs)


def test_normalize_handles_null_location():
    f = _finding(location=None, ambiguity_flag=None)
    out = arl.normalize_findings([f])
    assert len(out) == 1


def test_strict_schema_validates_full_findings_with_jsonschema():
    jsonschema = pytest.importorskip("jsonschema")
    doc = {"summary": "s", "findings": [
        {"evidence": "q", "severity": "High", "category": "security",
         "confidence": "high", "description": "d", "recommendation": "r",
         "ambiguity_flag": None, "ambiguity_reason": None, "location": None,
         "loopback_class": None},
    ]}
    jsonschema.validate(doc, arl.FINDINGS_SCHEMA)  # must not raise


# --- loopback_class (#407) ---

def test_loopback_class_in_schema_required_nullable_string_no_enum():
    item = arl.FINDINGS_SCHEMA["properties"]["findings"]["items"]
    assert "loopback_class" in item["required"]
    prop = item["properties"]["loopback_class"]
    # Plain nullable string — the proven #80 strict-mode shape (ambiguity_reason).
    assert prop["type"] == ["string", "null"]
    # NO enum: a null-member enum has no strict-mode precedent in this repo; the
    # prompt constrains vocab and loopback_class_entries fail-closes off-vocab.
    assert "enum" not in prop


@pytest.mark.parametrize("value", [
    "spec-tightening", "design-flaw", None, "prose-fix", 123, [], {"x": 1}, True,
])
def test_validate_finding_fully_permissive_on_loopback_class(value):
    # Advisory routing metadata must NEVER invalidate a finding: validate_findings
    # is a whole-report gate (one bad tag would parse_error the entire review),
    # and normalize_findings drops invalid findings. Any value is valid here;
    # loopback_class_entries owns the fail-close.
    ok, errs = arl.validate_finding(_finding(loopback_class=value))
    assert ok, errs


def test_validate_finding_accepts_absent_loopback_class():
    f = _finding()
    del f["loopback_class"]
    ok, errs = arl.validate_finding(f)
    assert ok, errs


def _lc_finding(severity="High", category="completeness", **over):
    return _finding(severity=severity, category=category, **over)


def test_entries_vocab_values_pass_through():
    fs = [_lc_finding(loopback_class="spec-tightening"),
          _lc_finding(severity="Critical", loopback_class="design-flaw")]
    assert arl.loopback_class_entries(fs) == ["spec-tightening", "design-flaw"]


def test_entries_strips_surrounding_whitespace_only():
    fs = [_lc_finding(loopback_class=" spec-tightening ")]
    assert arl.loopback_class_entries(fs) == ["spec-tightening"]


def test_entries_case_variant_is_untagged_no_case_repair():
    # Exact case-sensitive match: silent case repair would conceal backend drift.
    fs = [_lc_finding(loopback_class="Spec-Tightening")]
    assert arl.loopback_class_entries(fs) == ["untagged"]


@pytest.mark.parametrize("value", [None, "prose-fix", 123, [], ""])
def test_entries_absent_null_offvocab_nonstring_untagged(value):
    fs = [_lc_finding(loopback_class=value)]
    assert arl.loopback_class_entries(fs) == ["untagged"]


def test_entries_absent_key_untagged():
    f = _lc_finding()
    del f["loopback_class"]
    assert arl.loopback_class_entries([f]) == ["untagged"]


def test_entries_security_category_unconditionally_untagged():
    # Poisoned-tag defense: model metadata alone must never route a security
    # finding onto the cheap path — category: security folds design regardless.
    fs = [_lc_finding(category="security", loopback_class="spec-tightening")]
    assert arl.loopback_class_entries(fs) == ["untagged"]


def test_entries_security_override_case_insensitive_self_contained():
    # The override must hold on RAW (un-normalized) findings too — widening the
    # security net is fail-closed, unlike vocab case-repair which would open
    # the cheap path.
    fs = [_lc_finding(category="Security", loopback_class="spec-tightening")]
    assert arl.loopback_class_entries(fs) == ["untagged"]


def test_entries_medium_low_excluded_and_empty_input():
    fs = [_lc_finding(severity="Medium", loopback_class="spec-tightening"),
          _lc_finding(severity="Low", loopback_class="design-flaw")]
    assert arl.loopback_class_entries(fs) == []
    assert arl.loopback_class_entries([]) == []


def test_offvocab_loopback_class_visible_in_normalized_sidecar_content():
    # Visibility contract: the sidecar (--findings-json) carries normalize_findings
    # output verbatim — an off-vocab value survives and stays observable, while the
    # fold helper fail-closes it. (render_report_md deliberately does NOT emit the
    # field — routing metadata, not report content.)
    f = _lc_finding(loopback_class="prose-fix")
    ok, errs = arl.validate_findings([f])
    assert ok, errs
    out = arl.normalize_findings([f])
    assert len(out) == 1 and out[0]["loopback_class"] == "prose-fix"
    assert arl.loopback_class_entries(out) == ["untagged"]


def test_entries_fold_integration_with_classify_loopback_source():
    import plan_lib
    tight = [_lc_finding(loopback_class="spec-tightening"),
             _lc_finding(severity="Critical", loopback_class="spec-tightening")]
    assert plan_lib.classify_loopback_source(arl.loopback_class_entries(tight)) == "spec_tighten"
    mixed = tight + [_lc_finding(loopback_class="design-flaw")]
    assert plan_lib.classify_loopback_source(arl.loopback_class_entries(mixed)) == "design"
    untagged = tight + [_lc_finding(loopback_class=None)]
    assert plan_lib.classify_loopback_source(arl.loopback_class_entries(untagged)) == "design"
