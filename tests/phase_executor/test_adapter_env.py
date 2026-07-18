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


# --- #465 T4: codex launch profiles ---
import pytest

from phase_executor.adapters import codex_cli as _cx

TODAY_CODEX = ["codex", "exec", "--json", "-m", "gpt-5.6-terra",
               "-c", "model_reasoning_effort=high",
               "--ephemeral", "--color", "never", "-c", "project_doc_max_bytes=0",
               "-s", "read-only", "-C", "/tmp/x", "--skip-git-repo-check", "-"]


class TestCodexProfiles:
    def test_readonly_argv_unchanged(self):
        assert _cx.build_command("gpt-5.6-terra", "/tmp/x", effort="high") == TODAY_CODEX

    def test_none_effort_omits_flag(self):
        cmd = _cx.build_command("gpt-5.6-terra", "/tmp/x", effort=None)
        assert "model_reasoning_effort=high" not in " ".join(cmd)
        assert not any(a.startswith("model_reasoning_effort") for a in cmd)

    def test_mutating_composition_exact(self, tmp_path):
        root = tmp_path / "root"; wt = root / "wt"; wt.mkdir(parents=True)
        cmd = _cx.build_mutating_command("gpt-5.6-terra", str(wt), effort="high",
                                         containment_root=str(root))
        j = " ".join(cmd)
        assert "-s workspace-write" in j
        assert "-c sandbox_workspace_write.exclude_slash_tmp=true" in j
        assert "-c sandbox_workspace_write.exclude_tmpdir_env_var=true" in j
        import os as _os
        canon = _os.path.realpath(str(wt))
        assert f'sandbox_workspace_write.writable_roots=["{canon}"]' in j
        assert "-c approval_policy=never" in j
        assert cmd[cmd.index("-C") + 1] == canon

    @pytest.mark.parametrize("drop", [
        "sandbox_workspace_write.exclude_slash_tmp=true",
        "sandbox_workspace_write.exclude_tmpdir_env_var=true",
        "sandbox_workspace_write.writable_roots",
        "workspace-write",
    ])
    def test_validator_refuses_each_missing_override(self, tmp_path, drop):
        from phase_executor import contract
        root = tmp_path / "root"; wt = root / "wt"; wt.mkdir(parents=True)
        import os as _os
        canon = _os.path.realpath(str(wt))
        cmd = _cx.build_mutating_command("gpt-5.6-terra", str(wt), effort="high",
                                         containment_root=str(root))
        mutated = [a for a in cmd if drop not in a]
        with pytest.raises(contract.CompositionError):
            _cx.validate_mutating_composition(mutated, canon)

    def test_worktree_escaping_root_refuses(self, tmp_path):
        from phase_executor import contract
        root = tmp_path / "root"; root.mkdir()
        outside = tmp_path / "outside"; outside.mkdir()
        with pytest.raises(contract.CompositionError, match="containment"):
            _cx.build_mutating_command("gpt-5.6-terra", str(outside), effort="high",
                                       containment_root=str(root))

    def test_worktree_equal_to_root_refuses(self, tmp_path):
        from phase_executor import contract
        root = tmp_path / "root"; root.mkdir()
        with pytest.raises(contract.CompositionError, match="containment"):
            _cx.build_mutating_command("gpt-5.6-terra", str(root), effort="high",
                                       containment_root=str(root))

    def test_run_requires_containment_root_when_mutating(self, tmp_path):
        from phase_executor import contract
        from phase_executor.adapters.base import AdapterRequest
        m = {"session_policy": "fresh", "tool_grants": ["edit"], "effort": "high",
             "confinement": {"openai": "worktree"}, "bounds": {"timeout_s": 60}}
        profile = contract.profile_from_manifest(m, engine="codex", worktree=str(tmp_path / "wt"))
        req = AdapterRequest(seat="build", requested_model="gpt-5.6-terra", prompt="hi",
                             profile=profile)  # containment_root ABSENT
        with pytest.raises(contract.CompositionError, match="containment_root"):
            _cx.run(req, run_id="r", attempt_id="0-a", capture_root=tmp_path,
                    routing_config_digest="sha256:d")

    def test_mutating_grant_mismatch_refuses_codex(self, tmp_path):
        from phase_executor import contract
        p = contract.profile_from_manifest(
            {"session_policy": "fresh", "tool_grants": ["read"], "effort": "high",
             "confinement": {"openai": "worktree"}, "bounds": {"timeout_s": 60}}, engine="codex")
        object.__setattr__(p, "effective_grants", ("edit",))
        from phase_executor.adapters.base import AdapterRequest
        req = AdapterRequest(seat="build", requested_model="gpt-5.6-terra", prompt="hi",
                             profile=p, containment_root=str(tmp_path))
        with pytest.raises(contract.CompositionError, match="mutating"):
            _cx.run(req, run_id="r", attempt_id="0-a", capture_root=tmp_path,
                    routing_config_digest="sha256:d")
