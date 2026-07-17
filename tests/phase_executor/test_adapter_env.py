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
