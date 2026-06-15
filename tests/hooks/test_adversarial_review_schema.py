"""Tests for adversarial_review_lib findings schema + normalization (issue #77, Task 3)."""
import json
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
    # schema the strict jsonschema test enforces.
    base = {"severity": "High", "category": "security",
            "description": "d", "recommendation": "r", "location": "S1",
            "ambiguity_flag": False, "ambiguity_reason": None}
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

def test_schema_is_draft07_and_writable(tmp_path):
    assert arl.FINDINGS_SCHEMA["$schema"].endswith("draft-07/schema#")
    p = tmp_path / "schema.json"
    arl.write_schema(str(p))
    loaded = json.loads(p.read_text())
    assert loaded["properties"]["findings"]["items"]["properties"]["severity"]["enum"] == list(arl.SEVERITIES)


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
    for field in ("ambiguity_flag", "ambiguity_reason", "location"):
        t = item_props[field]["type"]
        assert "null" in t, f"{field} must allow null (strict mode requires it in `required`)"


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
        {"severity": "High", "category": "security", "description": "d",
         "recommendation": "r", "ambiguity_flag": None, "ambiguity_reason": None, "location": None},
    ]}
    jsonschema.validate(doc, arl.FINDINGS_SCHEMA)  # must not raise
