"""Post-retreat hook smoke test (#866, M0d).

After deleting the executor modules, every SURVIVING hooks/*.py must still
import cleanly (a survivor importing a deleted sibling dies here), and every
argparse-bearing CLI hook must answer --help without a traceback.

Imports run in one subprocess with __name__ != "__main__", so ``main()``
guards never fire; stdin is /dev/null so a hook that reads stdin cannot hang.
"""

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
HOOKS = sorted((REPO_ROOT / "hooks").glob("*.py"))

_IMPORT_PROG = r"""
import importlib.util, os.path, sys, traceback
# Hooks import siblings bare (`from atomic_write_lib import ...`,
# plan_lib.py:28), so the hooks dir itself must be importable — exactly what
# the plugin runtime provides. Without this the loop only passes when pytest's
# environment happens to leak a suitable PYTHONPATH (review finding, #866).
sys.path.insert(0, os.path.dirname(os.path.abspath(sys.argv[1])))
failures = []
for i, path in enumerate(sys.argv[1:]):
    name = f"_smoke_{i}"
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    except BaseException:
        failures.append(path + "\n" + traceback.format_exc())
sys.stdout.write("\n---\n".join(failures))
sys.exit(1 if failures else 0)
"""


def test_hooks_present():
    assert len(HOOKS) > 5, "hooks/*.py glob came back implausibly empty"


def test_every_surviving_hook_imports():
    # Deliberately CLEAN environment (no inherited PYTHONPATH): the subprocess
    # must succeed on _IMPORT_PROG's own sys.path setup alone.
    result = subprocess.run(
        [sys.executable, "-c", _IMPORT_PROG, *map(str, HOOKS)],
        capture_output=True, text=True, stdin=subprocess.DEVNULL,
        timeout=120, cwd=REPO_ROOT, env={"PATH": os.environ.get("PATH", "")},
    )
    assert result.returncode == 0, (
        f"hook import failures:\n{result.stdout}\n{result.stderr}"
    )


def test_cli_hooks_answer_help():
    cli_hooks = [p for p in HOOKS if "argparse" in p.read_text(errors="ignore")]
    assert cli_hooks, "no argparse CLI hooks found — glob or convention drifted"
    for hook in cli_hooks:
        result = subprocess.run(
            [sys.executable, str(hook), "--help"],
            capture_output=True, text=True, stdin=subprocess.DEVNULL,
            timeout=30, cwd=REPO_ROOT,
        )
        assert "Traceback" not in result.stderr, (
            f"{hook.name} --help raised:\n{result.stderr}"
        )
        assert result.returncode == 0, (
            f"{hook.name} --help rc={result.returncode}\n{result.stderr}"
        )
