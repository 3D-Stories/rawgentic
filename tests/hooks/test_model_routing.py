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
    assert mr.resolve(ws, "app", "review") == ("inherit", None)


def test_configured_role_returns_model(tmp_path):
    ws = _ws(tmp_path, {"name": "app", "path": "./p",
                        "modelRouting": {"review": "opus", "analysis": "sonnet"}})
    assert mr.resolve(ws, "app", "review") == ("opus", None)
    assert mr.resolve(ws, "app", "analysis") == ("sonnet", None)


def test_partial_config_absent_role_inherits(tmp_path):
    ws = _ws(tmp_path, {"name": "app", "path": "./p",
                        "modelRouting": {"review": "opus"}})
    assert mr.resolve(ws, "app", "analysis") == ("inherit", None)


def test_explicit_inherit_value(tmp_path):
    ws = _ws(tmp_path, {"name": "app", "path": "./p",
                        "modelRouting": {"review": "inherit"}})
    assert mr.resolve(ws, "app", "review") == ("inherit", None)


def test_invalid_model_value_falls_back_to_inherit_with_warning(tmp_path, capsys):
    ws = _ws(tmp_path, {"name": "app", "path": "./p",
                        "modelRouting": {"review": "gpt-9"}})
    assert mr.resolve(ws, "app", "review") == ("inherit", None)
    assert "gpt-9" in capsys.readouterr().err


def test_invalid_value_type_list_falls_back_to_inherit_with_warning(tmp_path, capsys):
    # fail-open: a non-str, non-dict value (list/int/etc) also degrades to inherit.
    ws = _ws(tmp_path, {"name": "app", "path": "./p",
                        "modelRouting": {"review": ["opus"]}})
    assert mr.resolve(ws, "app", "review") == ("inherit", None)
    assert capsys.readouterr().err


def test_invalid_value_type_int_falls_back_to_inherit_with_warning(tmp_path, capsys):
    ws = _ws(tmp_path, {"name": "app", "path": "./p",
                        "modelRouting": {"review": 5}})
    assert mr.resolve(ws, "app", "review") == ("inherit", None)
    assert capsys.readouterr().err


def test_malformed_block_falls_back_to_inherit(tmp_path, capsys):
    ws = _ws(tmp_path, {"name": "app", "path": "./p",
                        "modelRouting": "not-an-object"})
    assert mr.resolve(ws, "app", "review") == ("inherit", None)
    assert capsys.readouterr().err  # warned


def test_missing_project_returns_inherit(tmp_path):
    ws = _ws(tmp_path, {"name": "app", "path": "./p",
                        "modelRouting": {"review": "opus"}})
    assert mr.resolve(ws, "other", "review") == ("inherit", None)


def test_missing_workspace_file_returns_inherit(tmp_path):
    assert mr.resolve(str(tmp_path / "nope.json"), "app", "review") == ("inherit", None)


def test_malformed_workspace_json_returns_inherit(tmp_path, capsys):
    p = tmp_path / ".rawgentic_workspace.json"
    p.write_text("{ not json")
    assert mr.resolve(str(p), "app", "review") == ("inherit", None)
    assert capsys.readouterr().err


def test_invalid_utf8_workspace_file_returns_inherit(tmp_path, capsys):
    p = tmp_path / ".rawgentic_workspace.json"
    p.write_bytes(b'{"projects":[]}\xff')
    assert mr.resolve(str(p), "app", "review") == ("inherit", None)
    assert capsys.readouterr().err  # warned


def test_review_below_opus_floor_warns_sonnet(tmp_path, capsys):
    ws = _ws(tmp_path, {"name": "app", "path": "./p",
                        "modelRouting": {"review": "sonnet"}})
    assert mr.resolve(ws, "app", "review") == ("sonnet", None)  # resolves as configured
    assert "opus floor" in capsys.readouterr().err


def test_review_haiku_bumped_to_sonnet(tmp_path, capsys):
    # rawgentic never uses Haiku for routed work: a haiku config bumps to sonnet.
    ws = _ws(tmp_path, {"name": "app", "path": "./p",
                        "modelRouting": {"review": "haiku"}})
    assert mr.resolve(ws, "app", "review") == ("sonnet", None)
    assert "never uses Haiku" in capsys.readouterr().err


def test_review_inherit_and_fable_do_not_warn_floor(tmp_path, capsys):
    ws = _ws(tmp_path, {"name": "app", "path": "./p",
                        "modelRouting": {"review": "fable"}})
    assert mr.resolve(ws, "app", "review") == ("fable", None)
    assert "opus floor" not in capsys.readouterr().err


def test_analysis_haiku_bumped_to_sonnet(tmp_path, capsys):
    # never-Haiku is global (all roles), not just review; and it is NOT the
    # review-only opus floor.
    ws = _ws(tmp_path, {"name": "app", "path": "./p",
                        "modelRouting": {"analysis": "haiku"}})
    assert mr.resolve(ws, "app", "analysis") == ("sonnet", None)
    err = capsys.readouterr().err
    assert "never uses Haiku" in err
    assert "opus floor" not in err


def test_analysis_sonnet_no_floor_warn(tmp_path, capsys):
    # a non-haiku sub-opus model on a non-review role warns nothing (floor is review-only).
    ws = _ws(tmp_path, {"name": "app", "path": "./p",
                        "modelRouting": {"analysis": "sonnet"}})
    assert mr.resolve(ws, "app", "analysis") == ("sonnet", None)
    assert "opus floor" not in capsys.readouterr().err


class TestEffortDict:
    """modelRouting.<role> may be {model, effort} in addition to a plain string."""

    def test_dict_model_and_effort(self, tmp_path):
        ws = _ws(tmp_path, {"name": "app", "path": "./p",
                            "modelRouting": {"review": {"model": "opus", "effort": "high"}}})
        assert mr.resolve(ws, "app", "review") == ("opus", "high")

    def test_dict_haiku_model_bumped_with_effort_kept(self, tmp_path, capsys):
        ws = _ws(tmp_path, {"name": "app", "path": "./p",
                            "modelRouting": {"analysis": {"model": "haiku", "effort": "low"}}})
        assert mr.resolve(ws, "app", "analysis") == ("sonnet", "low")
        assert "never uses Haiku" in capsys.readouterr().err

    def test_dict_missing_model_defaults_to_inherit(self, tmp_path):
        ws = _ws(tmp_path, {"name": "app", "path": "./p",
                            "modelRouting": {"review": {"effort": "high"}}})
        assert mr.resolve(ws, "app", "review") == ("inherit", "high")

    def test_dict_invalid_effort_value_ignored_with_warning(self, tmp_path, capsys):
        ws = _ws(tmp_path, {"name": "app", "path": "./p",
                            "modelRouting": {"review": {"model": "opus", "effort": "turbo"}}})
        assert mr.resolve(ws, "app", "review") == ("opus", None)
        err = capsys.readouterr().err
        assert "effort" in err
        assert "turbo" in err

    def test_dict_non_string_effort_ignored_with_warning(self, tmp_path, capsys):
        ws = _ws(tmp_path, {"name": "app", "path": "./p",
                            "modelRouting": {"review": {"model": "opus", "effort": 3}}})
        assert mr.resolve(ws, "app", "review") == ("opus", None)
        assert "effort" in capsys.readouterr().err

    def test_dict_invalid_model_falls_back_to_inherit_effort_kept(self, tmp_path, capsys):
        ws = _ws(tmp_path, {"name": "app", "path": "./p",
                            "modelRouting": {"review": {"model": "gpt", "effort": "high"}}})
        assert mr.resolve(ws, "app", "review") == ("inherit", "high")
        assert "gpt" in capsys.readouterr().err

    def test_dict_review_below_opus_floor_still_warns(self, tmp_path, capsys):
        ws = _ws(tmp_path, {"name": "app", "path": "./p",
                            "modelRouting": {"review": {"model": "sonnet"}}})
        assert mr.resolve(ws, "app", "review") == ("sonnet", None)
        assert "opus floor" in capsys.readouterr().err

    def test_dict_unknown_keys_ignored(self, tmp_path):
        ws = _ws(tmp_path, {"name": "app", "path": "./p",
                            "modelRouting": {"review": {"model": "opus", "effort": "high",
                                                        "bogus": "x"}}})
        assert mr.resolve(ws, "app", "review") == ("opus", "high")

    def test_dict_effort_none_stays_none(self, tmp_path):
        ws = _ws(tmp_path, {"name": "app", "path": "./p",
                            "modelRouting": {"review": {"model": "opus", "effort": None}}})
        assert mr.resolve(ws, "app", "review") == ("opus", None)

    def test_string_value_equivalent_to_effort_null(self, tmp_path):
        ws = _ws(tmp_path, {"name": "app", "path": "./p",
                            "modelRouting": {"review": "opus"}})
        assert mr.resolve(ws, "app", "review") == ("opus", None)


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


def test_cli_resolve_default_omits_effort_even_when_configured(tmp_path, capsys):
    # back-compat is critical: default stdout is the bare model, never a second
    # line/value, even when the role resolves an effort.
    ws = _ws(tmp_path, {"name": "app", "path": "./p",
                        "modelRouting": {"review": {"model": "opus", "effort": "high"}}})
    rc = mr.main(["resolve", "--workspace", ws, "--project", "app", "--role", "review"])
    assert rc == 0
    out = capsys.readouterr().out
    assert out.strip() == "opus"
    assert "\n" not in out.strip()
    assert "high" not in out


def test_cli_resolve_effort_flag_prints_effort_for_dict_role(tmp_path, capsys):
    ws = _ws(tmp_path, {"name": "app", "path": "./p",
                        "modelRouting": {"review": {"model": "opus", "effort": "high"}}})
    rc = mr.main(["resolve", "--workspace", ws, "--project", "app", "--role", "review",
                  "--effort"])
    assert rc == 0
    assert capsys.readouterr().out.strip() == "high"


def test_cli_resolve_effort_flag_prints_none_for_string_role(tmp_path, capsys):
    ws = _ws(tmp_path, {"name": "app", "path": "./p",
                        "modelRouting": {"review": "opus"}})
    rc = mr.main(["resolve", "--workspace", ws, "--project", "app", "--role", "review",
                  "--effort"])
    assert rc == 0
    assert capsys.readouterr().out.strip() == "none"


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
            # never-Haiku: a haiku or unknown ceiling floors to sonnet (never
            # inherit, which could resolve to a Haiku session model).
            ("haiku", "high", "complex_feature", "sonnet"),
            ("bogus", "standard", "simple_change", "sonnet"),
        ],
    )
    def test_select_impl_model(self, ceiling, risk_level, complexity, expected):
        model, reason = mr.select_impl_model(ceiling, risk_level, complexity)
        assert model == expected
        assert isinstance(reason, str) and reason
        assert model != "haiku"  # rawgentic never routes coding to Haiku

    def test_reason_reflects_branch_not_ceiling_coincidence(self):
        # a standard/simple task under a sonnet ceiling is a down-route, not
        # "high-risk/complex", even though desired==ceiling==sonnet.
        model, reason = mr.select_impl_model("sonnet", "standard", "simple_change")
        assert model == "sonnet"
        assert "down-routed" in reason
        assert "high-risk" not in reason
        # and a genuine high-risk task IS labelled as such
        _, hi_reason = mr.select_impl_model("sonnet", "high", "standard_feature")
        assert "high-risk/complex" in hi_reason


class TestLoadBlockKeyAndMissing:
    """#427: _load_block gains a key= and a missing sentinel so a caller can tell an ABSENT config
    key from a present-but-non-dict one; resolve() stays fail-open regardless."""

    def _ws(self, tmp_path, entry):
        p = tmp_path / "w.json"
        p.write_text(json.dumps({"projects": [entry]}), encoding="utf-8")
        return str(p)

    def test_absent_key_returns_missing_sentinel(self, tmp_path):
        ws = self._ws(tmp_path, {"name": "x", "modelRouting": {"review": "opus"}})
        assert mr._load_block(ws, "x", key="executorRouting") is mr._ABSENT

    def test_present_non_dict_returned_raw(self, tmp_path):
        ws = self._ws(tmp_path, {"name": "x", "executorRouting": "oops"})
        assert mr._load_block(ws, "x", key="executorRouting") == "oops"

    def test_present_dict_returned(self, tmp_path):
        ws = self._ws(tmp_path, {"name": "x", "executorRouting": {"version": 1}})
        assert mr._load_block(ws, "x", key="executorRouting") == {"version": 1}

    def test_load_project_entry(self, tmp_path):
        ws = self._ws(tmp_path, {"name": "x", "path": "./p"})
        assert mr._load_project_entry(ws, "x")["path"] == "./p"
        assert mr._load_project_entry(ws, "missing") is None

    def test_resolve_still_fail_open_on_absent_and_nondict(self, tmp_path):
        assert mr.resolve(self._ws(tmp_path, {"name": "x"}), "x", "review") == ("inherit", None)
        ws = self._ws(tmp_path, {"name": "x", "modelRouting": "bogus"})
        assert mr.resolve(ws, "x", "analysis") == ("inherit", None)

    def test_explicit_null_modelrouting_no_warning(self, tmp_path, capsys):
        # A2-F2: an explicit `modelRouting: null` resolves to inherit WITHOUT the spurious
        # "not an object" warning (parity with the pre-#427 silent block-is-None path).
        ws = self._ws(tmp_path, {"name": "x", "modelRouting": None})
        assert mr.resolve(ws, "x", "review") == ("inherit", None)
        assert "not an object" not in capsys.readouterr().err


class TestSelectReviewLensModel:
    """#491: per-lens review-model selection. Pure, fail-open, never raises.
    Security lens is pinned to the resolved review model (config can never
    downgrade it); non-security lenses default to sonnet; haiku floors to
    sonnet everywhere (never-Haiku)."""

    def test_security_lens_pinned_to_review_model(self):
        model, reason = mr.select_review_lens_model("opus", "security")
        assert model == "opus"
        assert "pinned" in reason

    def test_security_override_ignored(self):
        model, _ = mr.select_review_lens_model(
            "opus", "security", {"security": "sonnet"})
        assert model == "opus"

    def test_mechanical_defaults_to_sonnet(self):
        model, reason = mr.select_review_lens_model("opus", "mechanical")
        assert model == "sonnet"
        assert "default" in reason

    def test_all_nonsecurity_lenses_default_sonnet(self):
        for lens in ("mechanical", "ac_completeness", "test_coverage", "bug_logic"):
            assert mr.select_review_lens_model("opus", lens)[0] == "sonnet"

    def test_override_respected(self):
        model, reason = mr.select_review_lens_model(
            "opus", "bug_logic", {"bug_logic": "opus"})
        assert model == "opus"
        assert "override" in reason

    def test_haiku_override_floors_to_sonnet(self):
        model, _ = mr.select_review_lens_model(
            "opus", "mechanical", {"mechanical": "haiku"})
        assert model == "sonnet"

    def test_invalid_override_falls_back_to_default(self):
        model, _ = mr.select_review_lens_model(
            "opus", "mechanical", {"mechanical": "gpt9"})
        assert model == "sonnet"

    def test_unknown_lens_fails_safe_to_review_model(self):
        model, reason = mr.select_review_lens_model("opus", "vibes")
        assert model == "opus"
        assert "unknown" in reason

    def test_inherit_review_model_security_inherits(self):
        assert mr.select_review_lens_model("inherit", "security")[0] == "inherit"

    def test_inherit_review_model_mechanical_still_sonnet(self):
        assert mr.select_review_lens_model("inherit", "mechanical")[0] == "sonnet"

    def test_lens_vocab_constant(self):
        assert mr.REVIEW_LENSES == {
            "security", "mechanical", "ac_completeness", "test_coverage", "bug_logic"}


class TestResolveLensCLI:
    """#491: `resolve --role review --lens <lens>` round-trip through the CLI."""

    def _run(self, ws, lens):
        import subprocess
        cli = str(HOOKS / "model_routing_lib.py")
        r = subprocess.run(
            [sys.executable, cli, "resolve", "--workspace", ws,
             "--project", "app", "--role", "review", "--lens", lens],
            capture_output=True, text=True)
        return r.returncode, r.stdout.strip()

    def test_cli_security_lens_prints_review_model(self, tmp_path):
        ws = _ws(tmp_path, {"name": "app", "path": "./p",
                            "modelRouting": {"review": "opus"}})
        assert self._run(ws, "security") == (0, "opus")

    def test_cli_mechanical_default_sonnet(self, tmp_path):
        ws = _ws(tmp_path, {"name": "app", "path": "./p",
                            "modelRouting": {"review": "opus"}})
        assert self._run(ws, "mechanical") == (0, "sonnet")

    def test_cli_reviewlenses_override_roundtrip(self, tmp_path):
        ws = _ws(tmp_path, {"name": "app", "path": "./p",
                            "modelRouting": {"review": "opus",
                                             "reviewLenses": {"bug_logic": "opus"}}})
        assert self._run(ws, "bug_logic") == (0, "opus")

    def test_cli_reviewlenses_security_downgrade_ignored(self, tmp_path):
        ws = _ws(tmp_path, {"name": "app", "path": "./p",
                            "modelRouting": {"review": "opus",
                                             "reviewLenses": {"security": "sonnet"}}})
        assert self._run(ws, "security") == (0, "opus")


class TestReviewLensNeverHaikuBoundary:
    """#491 8a R2 finding: the never-Haiku floor must hold at the FUNCTION
    boundary, not only via resolve()'s pre-floor — a future direct caller
    passing raw config must never get haiku back on any path."""

    def test_security_lens_haiku_review_model_floors(self):
        model, _ = mr.select_review_lens_model("haiku", "security")
        assert model == "sonnet"

    def test_unknown_lens_haiku_review_model_floors(self):
        model, _ = mr.select_review_lens_model("haiku", "vibes")
        assert model == "sonnet"
