"""#701 — make the contextMeter knobs reachable from `/rawgentic:setup`, and validate them there.

#687 shipped the block, the thresholds and the conservative 200k window fallback. Nothing shipped a
way to SET them short of hand-editing `.rawgentic.json` after reading the config reference, and the
cost showed up live on 2026-07-29: a 1M-context session was told it was at 88% of an assumed
200,000-token window and directed to hand off mid-task. The real figure was about 18%. The advisory
was working exactly as designed against a window nobody had been asked to declare.

The validator lives in `context_meter.py` beside the constants it enforces, exposed as a
`validate-config` subcommand — the same shape setup's retired telemetry-alerts block
used. That placement is the whole point rather than a
convenience: MIN_TIER_GAP_PCT and the 1..99 range are the hook's, so a validator anywhere else would
have to copy them, and a copy is exactly the drift this issue exists to remove. It adds nothing to
the reading, thresholds or nag behaviour #687 settled and its tests pin.

The load-bearing test here is `test_anything_the_validator_accepts_is_actually_honoured`: a setup
that writes a block the hook then silently discards would leave the user's tuned values inert and the
meter back on 60/70, which is indistinguishable from the bug being fixed.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
HOOKS = REPO_ROOT / "hooks"
CLI = HOOKS / "context_meter.py"
SKILL = REPO_ROOT / "skills" / "setup" / "SKILL.md"
CONFIG_REF = REPO_ROOT / "docs" / "config-reference.md"

if str(HOOKS) not in sys.path:
    sys.path.insert(0, str(HOOKS))

import context_meter as cm  # noqa: E402


def _cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(CLI), *args], capture_output=True,
                          text=True, check=False)


# ---------------------------------------------------------------------------
# the validator — pure
# ---------------------------------------------------------------------------

class TestValidateSetupBlock:
    def test_a_well_formed_block_passes(self) -> None:
        assert cm.validate_setup_block(
            {"windowSize": 1_000_000, "checkInPercent": 60, "actPercent": 70}) == []

    def test_an_empty_block_passes_because_absence_means_defaults(self) -> None:
        assert cm.validate_setup_block({}) == []

    def test_an_inverted_pair_is_refused_with_the_reason(self) -> None:
        """AC3. It silently makes the advisory tier unreachable, so the session goes straight from
        'fine' to 'hand off now' with no search band."""
        errors = cm.validate_setup_block({"checkInPercent": 70, "actPercent": 60})
        assert errors
        assert any("actPercent" in e and "checkInPercent" in e for e in errors)

    def test_an_equal_pair_is_refused(self) -> None:
        assert cm.validate_setup_block({"checkInPercent": 65, "actPercent": 65})

    def test_the_gap_rule_is_the_hooks_own_constant_not_a_copy(self) -> None:
        """The issue's AC says 'strictly less than'; the HOOK requires a gap of at least
        MIN_TIER_GAP_PCT and falls back to 60/70 otherwise. Validating the weaker rule would accept
        a pair the hook then discards — a setup that writes inert values. Exactly at the gap passes,
        one below it does not, and both are computed from the constant so they cannot drift."""
        gap = cm.MIN_TIER_GAP_PCT
        assert cm.validate_setup_block({"checkInPercent": 50, "actPercent": 50 + gap}) == []
        assert cm.validate_setup_block({"checkInPercent": 50, "actPercent": 50 + gap - 1})

    @pytest.mark.parametrize("pct", [0, 100, -5, 250])
    def test_a_percentage_out_of_range_is_refused(self, pct) -> None:
        assert cm.validate_setup_block({"checkInPercent": pct, "actPercent": 99})

    @pytest.mark.parametrize("value", ["60", 60.5, True, None, [], {}])
    def test_a_non_integer_percentage_is_refused(self, value) -> None:
        """`True` is in here deliberately: it is an int subclass in Python, so a bare isinstance
        check would accept `checkInPercent: true` and quietly mean 1%."""
        errors = cm.validate_setup_block({"checkInPercent": value, "actPercent": 70})
        assert errors, f"{value!r} was accepted as a percentage"

    @pytest.mark.parametrize("value", [0, -1, "1000000", 1.5, True, None])
    def test_a_bad_window_size_is_refused(self, value) -> None:
        assert cm.validate_setup_block({"windowSize": value})

    def test_a_positive_window_passes(self) -> None:
        assert cm.validate_setup_block({"windowSize": 200_000}) == []

    def test_the_other_documented_keys_are_not_rejected(self) -> None:
        """The block legitimately carries five keys (#687). This issue only makes three of them
        reachable from setup; refusing the other two would make the validator unusable on a block a
        user had already tuned by hand."""
        assert cm.validate_setup_block(
            {"checkInPercent": 60, "actPercent": 70, "everyTurns": 5,
             "everySeconds": 300}) == []

    def test_a_non_dict_block_is_refused_rather_than_crashing(self) -> None:
        for value in ("nope", 5, None, []):
            assert cm.validate_setup_block(value)

    def test_one_percentage_alone_is_checked_against_the_shipped_default_for_the_other(self) -> None:
        """A user setting only `actPercent` still has to clear the gap against the default
        check-in, because that is the pair the hook will actually evaluate."""
        assert cm.validate_setup_block({"actPercent": cm.DEFAULT_CHECK_IN_PCT + 1})
        assert cm.validate_setup_block(
            {"actPercent": cm.DEFAULT_CHECK_IN_PCT + cm.MIN_TIER_GAP_PCT}) == []


def test_anything_the_validator_accepts_is_actually_honoured() -> None:
    """The anti-drift test, and the reason the validator lives beside the constants.

    A block that passes setup validation must survive `thresholds()` unchanged. If it fell back to
    the shipped defaults instead, the user's tuned values would be inert and the meter would behave
    exactly as it did before this feature — the bug, wearing the fix's clothes.
    """
    for check_in, act in ((60, 70), (50, 60), (30, 90), (1, 99),
                          (40, 40 + cm.MIN_TIER_GAP_PCT)):
        block = {"checkInPercent": check_in, "actPercent": act}
        assert cm.validate_setup_block(block) == [], block
        assert cm.thresholds(block, {}) == (check_in, act), block


def test_anything_the_validator_refuses_would_have_fallen_back() -> None:
    """The converse, so the two sides are proven to agree rather than merely assumed to: a refused
    pair is one the hook would have discarded anyway."""
    for check_in, act in ((70, 60), (65, 65), (60, 65)):
        block = {"checkInPercent": check_in, "actPercent": act}
        assert cm.validate_setup_block(block)
        assert cm.thresholds(block, {}) == (cm.DEFAULT_CHECK_IN_PCT, cm.DEFAULT_ACT_PCT)


def test_an_accepted_window_is_the_window_the_hook_resolves() -> None:
    block = {"windowSize": 1_000_000}
    assert cm.validate_setup_block(block) == []
    window, provenance = cm.resolve_window(block["windowSize"], None, 0)
    assert (window, provenance) == (1_000_000, "config")


# ---------------------------------------------------------------------------
# the CLI setup shells out to
# ---------------------------------------------------------------------------

class TestValidateConfigCLI:
    def test_a_valid_block_exits_zero(self) -> None:
        proc = _cli("validate-config", "--json",
                    json.dumps({"windowSize": 1_000_000, "checkInPercent": 60,
                                "actPercent": 70}))
        assert proc.returncode == 0, proc.stderr
        assert "ok" in proc.stdout

    def test_an_invalid_block_exits_nonzero_and_names_the_problem(self) -> None:
        proc = _cli("validate-config", "--json",
                    json.dumps({"checkInPercent": 70, "actPercent": 60}))
        assert proc.returncode == 2, proc.stdout
        assert "actPercent" in proc.stderr

    def test_bad_json_exits_nonzero(self) -> None:
        proc = _cli("validate-config", "--json", "{not json")
        assert proc.returncode == 2
        assert "JSON" in proc.stderr or "json" in proc.stderr

    def test_the_validator_does_not_inherit_the_hooks_fail_open_exit(self) -> None:
        """`context_meter.py`'s `__main__` deliberately swallows exceptions and exits 0, because a
        PostToolUse hook must never block a turn. A VALIDATION gate with that behaviour would let
        setup stage an invalid block, so the refusal path must return a real non-zero code."""
        proc = _cli("validate-config", "--json", json.dumps({"windowSize": -1}))
        assert proc.returncode != 0

    def test_the_default_subcommand_is_still_the_hook(self) -> None:
        """Adding a subcommand must not change what a bare invocation does — the hook is wired to
        run with no argv at all."""
        proc = subprocess.run([sys.executable, str(CLI)], input="{}", capture_output=True,
                              text=True, check=False)
        assert proc.returncode == 0


# ---------------------------------------------------------------------------
# the prompt and the docs (drift guards, anchored to ONE file each)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def setup_body() -> str:
    return SKILL.read_text(encoding="utf-8")


def test_setup_prompts_for_all_three_keys(setup_body) -> None:
    """AC1."""
    for key in ("windowSize", "checkInPercent", "actPercent"):
        assert key in setup_body, f"setup never mentions {key}"


def test_setup_states_the_units_and_the_denominator(setup_body) -> None:
    """AC2 — the live failure was a window nobody had been asked to declare, so 'percent of WHAT'
    and 'tokens, not a percentage' are the two things the prompt cannot leave implicit."""
    lowered = setup_body.lower()
    assert "tokens" in lowered
    assert "percentages are of" in lowered or "percent of" in lowered


def test_setup_shells_out_to_the_shared_validator(setup_body) -> None:
    """AC3, and the retired telemetry-alerts block's discipline: never re-implement the rules in prose."""
    assert "context_meter.py validate-config" in setup_body


def test_setup_declines_by_writing_nothing(setup_body) -> None:
    """AC5. Deliberately UNLIKE the retired telemetry-alerts block, which staged an answered-defaults sentinel: a
    contextMeter block that restates 60/70 is indistinguishable from a deliberate choice on the next
    read, so a later change to the shipped defaults would silently never reach that project."""
    assert "absent" in setup_body.lower()


def test_setup_preserves_an_existing_block(setup_body) -> None:
    """AC4 — read-modify-write, the rule the rest of setup already follows."""
    window = setup_body[setup_body.index("contextMeter"):]
    assert "preserve" in window.lower() or "verbatim" in window.lower()


def test_the_config_reference_says_setup_can_configure_it() -> None:
    """AC6 — the doc and the skill stopped disagreeing about how the block gets set."""
    body = CONFIG_REF.read_text(encoding="utf-8")
    section = body[body.index("### `contextMeter`"):]
    section = section[:section.index("\n### ")] if "\n### " in section else section
    assert "setup" in section.lower()

# ---------------------------------------------------------------------------
# #701 Step-11 diff review — two ways the validator said "ok" to a block the hook ignores
# ---------------------------------------------------------------------------

class TestUnknownKeysAreRefused:
    """The sharper of the two findings: a validator that only checks the keys it KNOWS about says
    "ok" to a typo, setup stages it, the hook ignores the misspelled field and keeps using the
    200,000-token fallback — which is the exact failure #701 exists to prevent, reproduced by the
    fix for it.
    """

    def test_a_typod_key_is_refused_and_named(self) -> None:
        errors = cm.validate_setup_block({"windowSzie": 1_000_000})
        assert errors, "a misspelled key silently means the default"
        assert any("windowSzie" in e for e in errors)

    def test_an_unknown_key_is_refused_even_beside_valid_ones(self) -> None:
        assert cm.validate_setup_block(
            {"checkInPercent": 60, "actPercent": 70, "windwoSize": 1_000_000})

    # Per-key valid values, because a lone percentage is still checked against the DEFAULT for the
    # other half: `checkInPercent: 50` against the default act of 50 is a zero-gap pair, not a
    # valid single key.
    @pytest.mark.parametrize("key,value", [("windowSize", 200_000), ("checkInPercent", 30),
                                           ("actPercent", 80), ("everyTurns", 5),
                                           ("everySeconds", 300)])
    def test_every_documented_key_is_still_accepted(self, key, value) -> None:
        """The allowlist is the five keys `docs/config-reference.md` documents — not the three this
        issue made reachable from setup. Refusing the cadence pair would make the validator unusable
        on a block a user had already tuned by hand."""
        assert cm.validate_setup_block({key: value}) == []

    def test_the_allowlist_is_derived_from_one_place(self) -> None:
        """A hand-copied list in the validator would drift from the documented block the moment a
        sixth key is added, so the names live in a module constant the docs guard can also read."""
        assert set(cm.SETUP_BLOCK_KEYS) == {"windowSize", "checkInPercent", "actPercent",
                                            "everyTurns", "everySeconds",
                                            # #718 — the auto-typing kill switch
                                            "insertPrompt"}

    def test_the_cli_refuses_an_unknown_key(self) -> None:
        proc = _cli("validate-config", "--json", json.dumps({"windowSzie": 1_000_000}))
        assert proc.returncode == 2, proc.stdout
        assert "windowSzie" in proc.stderr


class TestTheValidatorCannotFailOpen:
    """The second finding, and it is specific to THIS module: `__main__` deliberately swallows every
    exception and exits 0, because a PostToolUse hook must never block a turn. So any exception that
    escapes `cmd_validate_config` is reported to setup as SUCCESS — a validation gate that passes on
    error. Catching only `ValueError` around `json.loads` left that open.
    """

    def test_deeply_nested_json_does_not_report_success(self) -> None:
        """`json.loads` raises RecursionError on this — a RuntimeError, NOT a ValueError, so it
        escaped the narrow except and reached the fail-open wrapper. Verified live: 100k nested
        arrays raise RecursionError, which is an Exception but not a ValueError."""
        # 10_000 is the measured threshold on this interpreter (5_000 still parses) and keeps the
        # argument under Linux's 128 KB per-argument cap — 100_000 exceeded it and the test died with
        # `Argument list too long` instead of exercising the path.
        bomb = "[" * 10_000 + "]" * 10_000
        proc = _cli("validate-config", "--json", bomb)
        assert proc.returncode == 2, (
            f"rc={proc.returncode}: a validator that cannot parse its input must NOT report ok")
        assert "ok" not in proc.stdout

    def test_an_unexpected_exception_inside_validation_still_refuses(self,
                                                                     monkeypatch) -> None:
        """Belt and braces on the same hazard: whatever goes wrong, the answer is a refusal."""
        def boom(_block):
            raise RuntimeError("unexpected")

        monkeypatch.setattr(cm, "validate_setup_block", boom)

        class Args:
            json_block = "{}"

        assert cm.cmd_validate_config(Args()) == 2
