"""Unit tests for hooks/model_routing_lib.py (modelRouting resolution, fail-open)."""
import json
import sys
from pathlib import Path

import pytest

HOOKS = Path(__file__).resolve().parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS))
import model_routing_lib as mr  # noqa: E402


def _ws(tmp_path, entry: dict) -> str:
    p = tmp_path / ".rawgentic_workspace.json"
    p.write_text(json.dumps({"version": 1, "projects": [entry]}))
    return str(p)


def test_absent_block_returns_inherit(tmp_path):
    ws = _ws(tmp_path, {"name": "app", "path": "./p"})
    assert mr.resolve(ws, "app", "review") == "inherit"


def test_configured_role_returns_model(tmp_path):
    ws = _ws(tmp_path, {"name": "app", "path": "./p",
                        "modelRouting": {"review": "opus", "analysis": "sonnet"}})
    assert mr.resolve(ws, "app", "review") == "opus"
    assert mr.resolve(ws, "app", "analysis") == "sonnet"


def test_partial_config_absent_role_inherits(tmp_path):
    ws = _ws(tmp_path, {"name": "app", "path": "./p",
                        "modelRouting": {"review": "opus"}})
    assert mr.resolve(ws, "app", "analysis") == "inherit"


def test_explicit_inherit_value(tmp_path):
    ws = _ws(tmp_path, {"name": "app", "path": "./p",
                        "modelRouting": {"review": "inherit"}})
    assert mr.resolve(ws, "app", "review") == "inherit"


def test_invalid_model_value_falls_back_to_inherit_with_warning(tmp_path, capsys):
    ws = _ws(tmp_path, {"name": "app", "path": "./p",
                        "modelRouting": {"review": "gpt-9"}})
    assert mr.resolve(ws, "app", "review") == "inherit"
    assert "gpt-9" in capsys.readouterr().err


def test_malformed_block_falls_back_to_inherit(tmp_path, capsys):
    ws = _ws(tmp_path, {"name": "app", "path": "./p",
                        "modelRouting": "not-an-object"})
    assert mr.resolve(ws, "app", "review") == "inherit"
    assert capsys.readouterr().err  # warned


def test_missing_project_returns_inherit(tmp_path):
    ws = _ws(tmp_path, {"name": "app", "path": "./p",
                        "modelRouting": {"review": "opus"}})
    assert mr.resolve(ws, "other", "review") == "inherit"


def test_missing_workspace_file_returns_inherit(tmp_path):
    assert mr.resolve(str(tmp_path / "nope.json"), "app", "review") == "inherit"


def test_malformed_workspace_json_returns_inherit(tmp_path, capsys):
    p = tmp_path / ".rawgentic_workspace.json"
    p.write_text("{ not json")
    assert mr.resolve(str(p), "app", "review") == "inherit"
    assert capsys.readouterr().err


def test_invalid_utf8_workspace_file_returns_inherit(tmp_path, capsys):
    p = tmp_path / ".rawgentic_workspace.json"
    p.write_bytes(b'{"projects":[]}\xff')
    assert mr.resolve(str(p), "app", "review") == "inherit"
    assert capsys.readouterr().err  # warned


def test_review_below_opus_floor_warns_sonnet(tmp_path, capsys):
    ws = _ws(tmp_path, {"name": "app", "path": "./p",
                        "modelRouting": {"review": "sonnet"}})
    assert mr.resolve(ws, "app", "review") == "sonnet"  # resolves as configured
    assert "opus floor" in capsys.readouterr().err


def test_review_haiku_also_warns_floor(tmp_path, capsys):
    ws = _ws(tmp_path, {"name": "app", "path": "./p",
                        "modelRouting": {"review": "haiku"}})
    assert mr.resolve(ws, "app", "review") == "haiku"
    assert "opus floor" in capsys.readouterr().err


def test_review_inherit_and_fable_do_not_warn_floor(tmp_path, capsys):
    ws = _ws(tmp_path, {"name": "app", "path": "./p",
                        "modelRouting": {"review": "fable"}})
    assert mr.resolve(ws, "app", "review") == "fable"
    assert "opus floor" not in capsys.readouterr().err


def test_analysis_below_opus_does_not_warn_floor(tmp_path, capsys):
    ws = _ws(tmp_path, {"name": "app", "path": "./p",
                        "modelRouting": {"analysis": "haiku"}})
    assert mr.resolve(ws, "app", "analysis") == "haiku"
    assert "opus floor" not in capsys.readouterr().err  # floor is review-only


def test_cli_resolve_prints_value_exit_zero(tmp_path, capsys):
    ws = _ws(tmp_path, {"name": "app", "path": "./p",
                        "modelRouting": {"review": "opus"}})
    rc = mr.main(["resolve", "--workspace", ws, "--project", "app", "--role", "review"])
    assert rc == 0
    assert capsys.readouterr().out.strip() == "opus"


def test_cli_resolve_bad_config_still_exit_zero(tmp_path, capsys):
    ws = _ws(tmp_path, {"name": "app", "path": "./p",
                        "modelRouting": {"review": "bogus"}})
    rc = mr.main(["resolve", "--workspace", ws, "--project", "app", "--role", "review"])
    assert rc == 0  # fail-open: never non-zero
    assert capsys.readouterr().out.strip() == "inherit"


class TestSelectImplModel:
    @pytest.mark.parametrize(
        "ceiling, risk_level, complexity, expected",
        [
            ("opus", "high", "standard_feature", "opus"),
            ("opus", "standard", "simple_change", "sonnet"),
            ("opus", "standard", "standard_feature", "sonnet"),
            ("opus", "standard", "complex_feature", "opus"),
            ("opus", "high", "complex_feature", "opus"),
            ("sonnet", "high", "complex_feature", "sonnet"),
            ("sonnet", "standard", "simple_change", "sonnet"),
            ("fable", "high", "standard_feature", "fable"),
            ("fable", "standard", "simple_change", "sonnet"),
            ("inherit", "high", "complex_feature", "inherit"),
            ("haiku", "high", "complex_feature", "inherit"),
            ("bogus", "standard", "simple_change", "inherit"),
        ],
    )
    def test_select_impl_model(self, ceiling, risk_level, complexity, expected):
        model, reason = mr.select_impl_model(ceiling, risk_level, complexity)
        assert model == expected
        assert isinstance(reason, str) and reason
