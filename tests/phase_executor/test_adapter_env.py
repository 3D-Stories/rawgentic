"""#431 — multi-account Claude lanes: run_subprocess env MERGE (additions onto os.environ, not
replace) + the claude adapter's CLAUDE_CONFIG_DIR-from-credential_ref helper."""
import sys

from phase_executor.adapters.base import run_subprocess
from phase_executor.adapters.claude_cli import _claude_env

# A tiny, dependency-free probe: print an injected var + whether PATH survived the merge.
_PROBE = "import os;print(os.environ.get('PE_TEST_VAR'), os.environ.get('PATH') is not None)"


def test_env_addition_present_and_path_inherited():
    # env is a dict of ADDITIONS merged onto os.environ — the child gets PE_TEST_VAR AND keeps PATH.
    out = run_subprocess([sys.executable, "-c", _PROBE], "", 30.0, env={"PE_TEST_VAR": "hello"})
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "hello True"


def test_env_none_inherits_unchanged():
    # env=None (default) inherits the parent environment; the injected var is absent, PATH present.
    out = run_subprocess([sys.executable, "-c", _PROBE], "", 30.0)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "None True"


def test_env_does_not_replace_environment():
    # Belt: a merge (not a replace) means a large parent environ is still visible to the child.
    out = run_subprocess([sys.executable, "-c", "import os;print(len(os.environ) > 0)"], "", 30.0,
                         env={"PE_TEST_VAR": "x"})
    assert out.stdout.strip() == "True"


def test_claude_env_sets_config_dir_from_credential_ref():
    assert _claude_env("/home/u/.claude-acct2") == {"CLAUDE_CONFIG_DIR": "/home/u/.claude-acct2"}


def test_claude_env_none_or_empty_is_none():
    assert _claude_env(None) is None
    assert _claude_env("") is None


def test_claude_env_non_string_is_none():
    # defensive: a non-string credential_ref (schema drift) must not build a bogus env
    assert _claude_env(123) is None


# --- #465 T3: claude launch profile composition ---

from phase_executor import contract as _c
from phase_executor.adapters import claude_cli as _cl


def _profile(policy="fresh", grants=(), budget=None, worktree=None, engine="claude"):
    m = {"session_policy": policy, "tool_grants": list(grants), "effort": "high",
         "confinement": {"anthropic": "hooks"}, "bounds": {"timeout_s": 60}}
    if budget is not None:
        m["bounds"]["max_budget_usd"] = budget
    return _c.profile_from_manifest(m, engine=engine, worktree=worktree)


TODAY_CMD = ["claude", "--print", "--model", "claude-sonnet-5", "--output-format", "json",
             "--no-session-persistence"]


class TestClaudeProfileComposition:
    def test_no_profile_is_todays_exact_argv(self):
        assert _cl.build_command("claude-sonnet-5") == TODAY_CMD

    def test_fresh_profile_pins_persistence_flag(self):
        assert "--no-session-persistence" in _cl.build_command("claude-sonnet-5", profile=_profile("fresh"))

    def test_resume_profile_omits_persistence_flag(self):
        cmd = _cl.build_command("claude-sonnet-5", profile=_profile("resume"))
        assert "--no-session-persistence" not in cmd

    def test_unvalidated_session_value_refuses_at_spawn(self):
        import dataclasses, pytest as _pt
        p = _profile("fresh")
        p2 = dataclasses.replace(p, session_policy="Fresh")  # hand-built inconsistent profile
        with _pt.raises(_c.CompositionError, match="session_policy"):
            _cl.build_command("claude-sonnet-5", profile=p2)

    def test_grants_map_to_allowed_tools_wire_equals_record(self):
        p = _profile("fresh", grants=("read", "edit", "bash"), budget=5.0, worktree="/tmp/wt")
        cmd = _cl.build_command("claude-sonnet-5", profile=p)
        i = cmd.index("--allowedTools")
        assert cmd[i + 1] == "Read,Grep,Glob,Edit,Write,Bash,WebFetch,WebSearch"
        j = cmd.index("--max-budget-usd")
        assert cmd[j + 1] == "5.0"

    def test_no_grants_no_flags(self):
        cmd = _cl.build_command("claude-sonnet-5", profile=_profile("fresh"))
        assert "--allowedTools" not in cmd and "--max-budget-usd" not in cmd

    def test_mutating_grant_mismatch_refuses(self):
        # The only real inconsistency vector: effective_grants injected past init=False
        # (object.__setattr__) while mutating stays False — the pre-spawn assert catches it.
        # (A dataclasses.replace()-laundered profile ZEROES effective_grants — nothing lands
        # on the wire, fail-safe, deliberately NOT refused.)
        import pytest as _pt
        p = _profile("fresh")
        object.__setattr__(p, "effective_grants", ("edit", "bash"))
        with _pt.raises(_c.CompositionError, match="mutating"):
            _cl.build_command("claude-sonnet-5", profile=p)

    def test_replace_laundered_profile_grants_nothing(self):
        import dataclasses
        p = _profile("fresh", grants=("read", "edit"), budget=5.0, worktree="/tmp/wt")
        p2 = dataclasses.replace(p, mutating=False)  # init=False field resets to ()
        cmd = _cl.build_command("claude-sonnet-5", profile=p2)
        assert "--allowedTools" not in cmd  # wire maps from effective_grants: nothing granted
